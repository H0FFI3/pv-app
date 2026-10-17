import asyncio
import json
from datetime import date, timedelta

import pytest

from conftest import BASE_ENV
from helpers import clear_sky_weather
from pvapp import app, paths, sources, storage
from pvapp.sources import ActualResult, WeatherData

D = date


# ------------------------------------------------------------------------- plan_days


def plan(stored=(), today=D(2026, 10, 16), start=D(2026, 10, 13), limit=14, refresh=False):
    return app.plan_days(set(stored), today, start, limit, refresh)


def test_before_the_start_date_nothing_is_collected():
    result = plan(today=D(2026, 10, 13))  # gestern = 12.10. < Start 13.10.
    assert result.before_start and result.days == []


def test_first_run_collects_every_day_up_to_yesterday():
    assert plan().days == [D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)]


def test_gaps_are_backfilled_including_holes_in_the_middle():
    assert plan(stored=[D(2026, 10, 13), D(2026, 10, 15)]).days == [D(2026, 10, 14)]


def test_everything_stored_means_no_work():
    result = plan(stored=[D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)])
    assert result.up_to_date and result.days == []


def test_backfill_window_keeps_the_most_recent_days_and_reports_the_rest():
    result = plan(today=D(2026, 11, 1), limit=3)
    assert result.days == [D(2026, 10, 29), D(2026, 10, 30), D(2026, 10, 31)]
    assert result.skipped_older == 16  # 19 fehlende Tage, 3 im Fenster


def test_refresh_adds_yesterday_even_if_stored():
    stored = [D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)]
    assert plan(stored=stored, refresh=True).days == [D(2026, 10, 15)]


# ------------------------------------------------------------------- collect_records


def make_weather(config, complete, source="archive"):
    first, last = min(complete) - timedelta(days=1), max(complete) + timedelta(days=1)
    frame = clear_sky_weather(config.latitude, config.longitude, config.timezone, first, last)
    return WeatherData(frame, 300.0, {d: 6.5 for d in complete}, {d: source for d in complete})


def run(coro):
    return asyncio.run(coro)


def test_records_contain_daily_hourly_and_source_information(config):
    days = [D(2026, 10, 13), D(2026, 10, 14)]
    seen = []

    async def actuals(cfg, requested, include_hourly, chooser):
        seen.append(requested)
        return ActualResult(
            8.0,
            energy={d: 10.0 for d in requested},
            hourly={d: {"11:00": 2.5, "12:00": 3.0} for d in requested},
        )

    result = run(app.collect_records(config, days, load_weather_fn=lambda *a: make_weather(config, days, "forecast"), fetch_actuals_fn=actuals))

    assert seen == [days] and not result.skipped
    first = result.records[0]
    assert first["date"] == "2026-10-13" and first["actual_kwh"] == 10.0 and first["weather_source"] == "forecast"
    assert first["sunshine_hours"] == 6.5
    hours = {p["hour"]: p for p in first["hourly"]}
    assert len(first["hourly"]) == 24 and hours["12:00"]["actual_kwh"] == 3.0 and hours["03:00"]["actual_kwh"] is None
    assert first["potential_kwh"] == pytest.approx(sum(p["potential_kwh"] for p in first["hourly"]), abs=0.05)
    assert first["potential_kwh"] > 10


def test_days_without_weather_are_skipped_before_any_alphaess_call(config):
    days = [D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)]
    asked = []

    async def actuals(cfg, requested, include_hourly, chooser):
        asked.append(requested)
        return ActualResult(8.0, energy={d: 5.0 for d in requested})

    result = run(
        app.collect_records(
            config, days, load_weather_fn=lambda *a: make_weather(config, days[:2]), fetch_actuals_fn=actuals
        )
    )
    assert asked == [days[:2]]  # der 15. wurde gar nicht erst bei AlphaESS angefragt
    assert [r["date"] for r in result.records] == ["2026-10-13", "2026-10-14"]
    assert "Wetterdaten" in result.skipped[D(2026, 10, 15)]


def test_no_weather_at_all_makes_no_alphaess_call(config):
    async def actuals(*a, **k):
        pytest.fail("AlphaESS darf nicht aufgerufen werden")

    empty = WeatherData(make_weather(config, [D(2026, 10, 13)]).frame, 0.0, {}, {})
    result = run(app.collect_records(config, [D(2026, 10, 13)], load_weather_fn=lambda *a: empty, fetch_actuals_fn=actuals))
    assert result.records == [] and D(2026, 10, 13) in result.skipped


def test_alphaess_errors_skip_only_that_day(config):
    days = [D(2026, 10, 13), D(2026, 10, 14)]

    async def actuals(cfg, requested, include_hourly, chooser):
        return ActualResult(8.0, energy={days[0]: 5.0}, errors={days[1]: "kein epv"})

    result = run(app.collect_records(config, days, load_weather_fn=lambda *a: make_weather(config, days), fetch_actuals_fn=actuals))
    assert [r["date"] for r in result.records] == ["2026-10-13"]
    assert result.skipped == {days[1]: "kein epv"}


# ------------------------------------------------------------------------ run() end-to-end


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr("pvapp.config.env_file_candidates", lambda: [])
    monkeypatch.setattr(paths, "app_directory", lambda: tmp_path / "app")
    monkeypatch.setattr(sources, "local_today", lambda tz: D(2026, 10, 16))
    opened = []
    monkeypatch.setattr(app.webbrowser, "open", lambda url: opened.append(url) or True)

    class Box:
        data_file = tmp_path / "local" / "PV-Tagesbericht" / "daily.json"
        report = tmp_path / "app" / "dashboard.html"
        calls = []
        browser = opened

    async def fake_collect(config, days, *, include_hourly=True, chooser=None, **kwargs):
        Box.calls.append(list(days))
        return app.CollectionResult(
            records=[
                {"date": d.isoformat(), "actual_kwh": 5.0, "potential_kwh": 6.0, "sunshine_hours": 4.0, "weather_source": "archive"}
                for d in days
            ]
        )

    monkeypatch.setattr(app, "collect_records", fake_collect)
    Box.calls = []
    return Box


def start(*flags):
    return app.run(app.build_parser().parse_args(list(flags)))


def saved_dates(box):
    return [r["date"] for r in json.loads(box.data_file.read_text(encoding="utf-8"))["records"]]


def test_first_start_backfills_and_writes_history_and_report(sandbox):
    assert start("--no-open") == 0
    assert sandbox.calls == [[D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)]]
    assert saved_dates(sandbox) == ["2026-10-13", "2026-10-14", "2026-10-15"]
    html = sandbox.report.read_text(encoding="utf-8")
    assert "PV_DAILY_DATA" in html and "2026-10-15" in html
    assert sandbox.browser == []  # --no-open


def test_second_start_makes_no_api_calls_and_does_not_touch_the_history_file(sandbox):
    start("--no-open")
    sandbox.calls.clear()
    before = sandbox.data_file.read_bytes()
    assert start("--no-open") == 0
    assert sandbox.calls == []
    assert sandbox.data_file.read_bytes() == before
    assert not storage.backup_path(sandbox.data_file).exists()


def test_refresh_refetches_only_yesterday(sandbox):
    start("--no-open")
    sandbox.calls.clear()
    start("--no-open", "--refresh")
    assert sandbox.calls == [[D(2026, 10, 15)]]


def test_browser_is_opened_by_default(sandbox):
    start()
    assert len(sandbox.browser) == 1 and sandbox.browser[0].startswith("file:")


def test_old_and_invalid_records_survive_a_normal_start_regression(sandbox):
    sandbox.data_file.parent.mkdir(parents=True)
    raw = [
        {"date": "2026-10-10", "actual_kwh": 1, "potential_kwh": 2, "sunshine_hours": 3},  # vor DATA_START_DATE
        {"date": "2026-10-14", "actual_kwh": "kaputt", "potential_kwh": 2, "sunshine_hours": 3},
    ]
    sandbox.data_file.write_text(json.dumps({"records": raw}), encoding="utf-8")
    start("--no-open")
    saved = json.loads(sandbox.data_file.read_text(encoding="utf-8"))["records"]
    assert saved[0] == raw[0]  # unverändert vorhanden
    assert "2026-10-10" in saved_dates(sandbox)
    assert sandbox.calls == [[D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)]]  # ungültiger 14.10. wird neu geholt
    assert '"2026-10-10"' not in sandbox.report.read_text(encoding="utf-8")  # nicht angezeigt


def test_offline_start_leaves_the_file_byte_identical(sandbox):
    sandbox.data_file.parent.mkdir(parents=True)
    content = json.dumps({"records": [{"date": "2026-10-10", "actual_kwh": 1}]})
    sandbox.data_file.write_text(content, encoding="utf-8")
    assert start("--offline", "--no-open") == 0
    assert sandbox.data_file.read_text(encoding="utf-8") == content and sandbox.calls == []


def test_corrupt_history_does_not_crash_the_app(sandbox):
    sandbox.data_file.parent.mkdir(parents=True)
    sandbox.data_file.write_text('{"records": [', encoding="utf-8")
    assert start("--no-open") == 0
    assert saved_dates(sandbox) == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert list(sandbox.data_file.parent.glob("daily.json.corrupt-*"))


def test_failed_update_still_opens_the_report_with_local_data(sandbox, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("Netzwerk weg")

    monkeypatch.setattr(app, "collect_records", boom)
    assert start("--no-open") == 1
    assert sandbox.report.exists() and not sandbox.data_file.exists()


def test_incomplete_configuration_is_reported_but_the_report_is_still_created(sandbox, monkeypatch):
    monkeypatch.delenv("LATITUDE")
    assert start("--no-open") == 1
    assert sandbox.report.exists()
    assert start("--offline", "--no-open") == 0  # offline braucht keine vollständige Konfiguration


def test_before_start_date_prints_the_computed_first_day(sandbox, monkeypatch, caplog):
    monkeypatch.setattr(sources, "local_today", lambda tz: D(2026, 10, 13))
    with caplog.at_level("INFO"):
        start("--no-open")
    assert "14.10.2026" in caplog.text and sandbox.calls == []


def test_secrets_never_reach_the_log(sandbox, caplog):
    from pvapp.logging_setup import REDACTOR

    with caplog.at_level("DEBUG"):
        start("--no-open")
    REDACTOR.add("test-secret-5678")
    assert "test-secret-5678" not in caplog.text and REDACTOR.redact("x test-secret-5678 y") == "x *** y"
