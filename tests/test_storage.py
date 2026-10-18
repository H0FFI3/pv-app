import json
import os
from datetime import date

import pytest

from pvapp import storage


def record(day, actual=5.0, potential=6.0, sun=4.0, **extra):
    return {"date": day, "actual_kwh": actual, "potential_kwh": potential, "sunshine_hours": sun, **extra}


def test_missing_file_gives_empty_history(tmp_path):
    assert storage.load_payload(tmp_path / "daily.json") == {"records": []}


def test_old_and_invalid_records_survive_a_load_and_save_cycle(tmp_path):
    """Regression: früher wurden Datensätze vor DATA_START_DATE und ungültige beim Start gelöscht."""
    path = tmp_path / "daily.json"
    raw = [record("2026-10-10"), record("2026-10-13"), record("2026-10-14", actual="x")]
    storage.save_payload({"records": raw}, path)

    payload = storage.load_payload(path)
    payload["records"] = storage.upsert_record(payload["records"], record("2026-10-15"))
    storage.save_payload(payload, path)

    saved_dates = [r["date"] for r in json.loads(path.read_text(encoding="utf-8"))["records"]]
    assert saved_dates == ["2026-10-10", "2026-10-13", "2026-10-14", "2026-10-15"]
    # Anzeige filtert, die Datei nicht:
    shown = [r["date"] for r in storage.valid_records(payload["records"], date(2026, 10, 13))]
    assert shown == ["2026-10-13", "2026-10-15"]


def test_extra_top_level_keys_are_preserved(tmp_path):
    path = tmp_path / "daily.json"
    storage.save_payload({"records": [], "note": "bleibt"}, path)
    assert storage.load_payload(path)["note"] == "bleibt"


def test_upsert_replaces_same_day_and_keeps_order():
    records = [record("2026-10-13", actual=1), record("2026-10-15", actual=3)]
    result = storage.upsert_record(records, record("2026-10-14", actual=2))
    result = storage.upsert_record(result, record("2026-10-13", actual=9))
    assert [(r["date"], r["actual_kwh"]) for r in result] == [("2026-10-13", 9), ("2026-10-14", 2), ("2026-10-15", 3)]


def test_stored_dates_ignore_invalid_records():
    raw = [record("2026-10-13"), record("2026-10-14", actual=float("nan")), {"foo": 1}, "kaputt"]
    assert storage.stored_dates(raw) == {date(2026, 10, 13)}


def test_normalize_keeps_weather_source_and_drops_bad_hours():
    raw = record(
        "2026-10-13",
        weather_source="forecast",
        hourly=[
            {"hour": "10:00", "actual_kwh": None, "potential_kwh": 1.5},
            {"hour": "11:00", "actual_kwh": 1.0, "potential_kwh": float("nan")},
            {"hour": "12:00"},
        ],
    )
    normalized = storage.normalize_record(raw)
    assert normalized["weather_source"] == "forecast"
    assert normalized["hourly"] == [{"hour": "10:00", "actual_kwh": None, "potential_kwh": 1.5}]


def test_corrupt_file_is_moved_and_restored_from_backup(tmp_path, caplog):
    path = tmp_path / "daily.json"
    storage.save_payload({"records": [record("2026-10-13")]}, path)
    storage.save_payload({"records": [record("2026-10-13"), record("2026-10-14")]}, path)  # erzeugt .bak (1 Eintrag)
    path.write_text('{"records": [', encoding="utf-8")

    payload = storage.load_payload(path)

    assert [r["date"] for r in payload["records"]] == ["2026-10-13"]
    assert json.loads(path.read_text(encoding="utf-8")) == payload  # wiederhergestellt
    assert list(tmp_path.glob("daily.json.corrupt-*")), "defekte Datei muss zur Analyse erhalten bleiben"


def test_corrupt_file_without_backup_starts_empty_but_keeps_evidence(tmp_path):
    path = tmp_path / "daily.json"
    path.write_text("kein json", encoding="utf-8")
    assert storage.load_payload(path) == {"records": []}
    assert not path.exists()
    assert len(list(tmp_path.glob("daily.json.corrupt-*"))) == 1


def test_wrong_structure_counts_as_corrupt(tmp_path):
    path = tmp_path / "daily.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert storage.load_payload(path) == {"records": []}


def test_second_save_creates_backup_of_previous_state(tmp_path):
    path = tmp_path / "daily.json"
    storage.save_payload({"records": [record("2026-10-13")]}, path)
    assert not storage.backup_path(path).exists()
    storage.save_payload({"records": [record("2026-10-13"), record("2026-10-14")]}, path)
    assert len(json.loads(storage.backup_path(path).read_text(encoding="utf-8"))["records"]) == 1


def test_failed_write_leaves_original_intact_and_no_temp_files(tmp_path, monkeypatch):
    path = tmp_path / "daily.json"
    storage.save_payload({"records": [record("2026-10-13")]}, path)
    before = path.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("Platte voll")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        storage.save_payload({"records": [record("2026-10-13"), record("2026-10-14")]}, path)

    assert path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("*.tmp"))


def test_hourly_battery_and_grid_signals_are_kept_and_invalid_ones_dropped():
    raw = record(
        "2026-10-13",
        hourly=[
            {"hour": "12:00", "actual_kwh": 1.0, "potential_kwh": 2.0, "soc_pct": 100.0, "feed_in_w": 4000.0},
            {"hour": "13:00", "actual_kwh": 1.0, "potential_kwh": 2.0, "soc_pct": float("nan")},
        ],
    )
    hours = storage.normalize_record(raw)["hourly"]
    assert hours[0]["soc_pct"] == 100.0 and hours[0]["feed_in_w"] == 4000.0
    assert "soc_pct" not in hours[1]


# ---------------------------------------------------------- Signale nachladen (Altdatensätze)


def legacy_day(day, hourly=None, **extra):
    """Datensatz aus der Zeit vor der Abregelungserkennung: Stundenprofil ohne Signale, ohne Vermerk."""
    hourly = hourly or [{"hour": "11:00", "actual_kwh": 1.0, "potential_kwh": 3.0}, {"hour": "12:00", "actual_kwh": 1.2, "potential_kwh": 3.2}]
    return record(day, hourly=hourly, **extra)


def test_legacy_days_are_candidates_for_signal_backfill():
    raw = [
        legacy_day("2026-10-14"),
        legacy_day("2026-10-15", signals_checked=True),  # schon abgefragt, auch ohne Ergebnis
        record("2026-10-13"),  # kein Stundenprofil: nichts nachzuladen
        legacy_day("2026-10-16", hourly=[{"hour": "12:00", "actual_kwh": 1, "potential_kwh": 2, "soc_pct": 90.0}]),  # hat Signale
        legacy_day("2026-10-17", actual="kaputt"),  # ungültig
    ]
    assert storage.days_missing_signals(raw) == {date(2026, 10, 14)}


def test_apply_signals_adds_only_signals_and_marks_the_day():
    raw = [legacy_day("2026-10-14"), legacy_day("2026-10-15")]
    result = storage.apply_signals(raw, date(2026, 10, 14), {"12:00": {"soc_pct": 100.0, "feed_in_w": 0.0}, "03:00": {"soc_pct": 5.0}})
    day = result[0]
    assert day["signals_checked"] is True
    assert day["hourly"][1] == {"hour": "12:00", "actual_kwh": 1.2, "potential_kwh": 3.2, "soc_pct": 100.0, "feed_in_w": 0.0}
    assert day["hourly"][0] == {"hour": "11:00", "actual_kwh": 1.0, "potential_kwh": 3.0}  # Stunde ohne Signal unverändert
    assert (day["actual_kwh"], day["potential_kwh"]) == (5.0, 6.0)
    assert result[1] == raw[1] and "signals_checked" not in raw[0], "Eingabedaten und andere Tage bleiben unberührt"
    assert storage.days_missing_signals(result) == {date(2026, 10, 15)}


def test_empty_signals_still_mark_the_day_as_checked():
    result = storage.apply_signals([legacy_day("2026-10-14")], date(2026, 10, 14), {})
    assert result[0]["signals_checked"] is True and storage.days_missing_signals(result) == set()
    assert "soc_pct" not in result[0]["hourly"][0]


def test_signals_checked_survives_normalization():
    assert storage.normalize_record(legacy_day("2026-10-14", signals_checked=True))["signals_checked"] is True
    assert "signals_checked" not in storage.normalize_record(legacy_day("2026-10-14"))
