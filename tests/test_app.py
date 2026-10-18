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


def plan(stored=(), today=D(2026, 10, 16), start=D(2026, 10, 13), limit=14, refresh=False, **options):
    return app.plan_days(set(stored), today, start, limit, refresh, **options)


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


def test_signal_candidates_are_planned_separately_and_limited_to_the_window():
    stored = {D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)}
    result = plan(stored=stored, signal_candidates={D(2026, 10, 14), D(2026, 10, 15)})
    assert result.days == [] and result.signal_days == [D(2026, 10, 14), D(2026, 10, 15)] and not result.up_to_date
    # außerhalb des Backfill-Fensters (3 Tage bis gestern = 13.-15.): kein Nachladen
    assert plan(stored=stored, limit=2, signal_candidates={D(2026, 10, 13), D(2026, 10, 15)}).signal_days == [D(2026, 10, 15)]
    # Tage, die ohnehin vollständig neu erfasst werden, brauchen kein separates Nachladen
    assert plan(stored=stored, refresh=True, signal_candidates={D(2026, 10, 15)}).signal_days == []


def test_refresh_days_refetches_the_last_n_stored_days():
    stored = [D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15)]
    assert plan(stored=stored, refresh_days=2).days == [D(2026, 10, 14), D(2026, 10, 15)]
    assert plan(stored=stored, refresh_days=10).days == stored  # nie vor DATA_START_DATE


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


def test_signals_are_merged_into_the_hourly_profile(config):
    days = [D(2026, 10, 13)]

    async def actuals(cfg, requested, include_hourly, chooser):
        return ActualResult(
            8.0,
            energy={days[0]: 10.0},
            hourly={days[0]: {"12:00": 3.0}},
            signals={days[0]: {"12:00": {"soc_pct": 100.0, "feed_in_w": 4000.0}}},
        )

    result = run(app.collect_records(config, days, load_weather_fn=lambda *a: make_weather(config, days), fetch_actuals_fn=actuals))
    hours = {p["hour"]: p for p in result.records[0]["hourly"]}
    assert hours["12:00"]["soc_pct"] == 100.0 and hours["12:00"]["feed_in_w"] == 4000.0
    assert "soc_pct" not in hours["13:00"]


def test_new_records_are_marked_as_checked_only_when_the_power_data_was_fetched(config):
    days = [D(2026, 10, 13), D(2026, 10, 14)]

    async def actuals(cfg, requested, include_hourly, chooser):
        # 13.: Leistungsdaten abgerufen (ohne Akkufelder -> {}), 14.: Abruf der Leistungsdaten fehlgeschlagen
        return ActualResult(8.0, energy={d: 5.0 for d in requested}, hourly={days[0]: {}, days[1]: {}}, signals={days[0]: {}})

    result = run(app.collect_records(config, days, load_weather_fn=lambda *a: make_weather(config, days), fetch_actuals_fn=actuals))
    flags = {r["date"]: r.get("signals_checked") for r in result.records}
    assert flags == {"2026-10-13": True, "2026-10-14": None}


def test_collect_signals_returns_only_successfully_fetched_days(config):
    days = [D(2026, 10, 14), D(2026, 10, 15)]
    asked = []

    async def actuals(cfg, requested, include_hourly, chooser):
        asked.append((requested, include_hourly))
        return ActualResult(8.0, signals={days[0]: {"12:00": {"soc_pct": 100.0}}})  # 15.: Abruf fehlgeschlagen

    result = run(app.collect_signals(config, days, fetch_actuals_fn=actuals))
    assert result == {days[0]: {"12:00": {"soc_pct": 100.0}}} and asked == [(days, True)]
    assert run(app.collect_signals(config, [], fetch_actuals_fn=actuals)) == {}


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

    Box.signal_calls = []
    Box.signal_reply = {}  # Tag -> Signale; fehlender Tag = Abruf fehlgeschlagen

    async def fake_signals(config, days, *, chooser=None, **kwargs):
        Box.signal_calls.append(list(days))
        return {d: Box.signal_reply[d] for d in days if d in Box.signal_reply}

    monkeypatch.setattr(app, "collect_signals", fake_signals)
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


def test_report_marks_curtailed_hours(sandbox, monkeypatch):
    sandbox.data_file.parent.mkdir(parents=True)
    hours = [
        {"hour": "11:00", "actual_kwh": 1.0, "potential_kwh": 3.0, "soc_pct": 100.0, "feed_in_w": 800.0},
        {"hour": "12:00", "actual_kwh": 2.9, "potential_kwh": 3.0, "soc_pct": 100.0},
    ]
    day = {"date": "2026-10-14", "actual_kwh": 5, "potential_kwh": 8, "sunshine_hours": 4, "hourly": hours}
    sandbox.data_file.write_text(json.dumps({"records": [day]}), encoding="utf-8")
    start("--offline", "--no-open")
    html = sandbox.report.read_text(encoding="utf-8")
    assert '"curtailed":"battery"' in html and '"curtailment":{"hours":1,"lost_kwh":2.0}' in html
    assert sandbox.data_file.read_text(encoding="utf-8") == json.dumps({"records": [day]}), "Markierung wird nicht gespeichert"


def test_curtailment_thresholds_apply_offline_without_a_complete_configuration(sandbox, monkeypatch):
    monkeypatch.delenv("LATITUDE")
    monkeypatch.setenv("FEED_IN_LIMIT_W", "4000")
    sandbox.data_file.parent.mkdir(parents=True)
    hours = [{"hour": "11:00", "actual_kwh": 1.0, "potential_kwh": 3.0, "soc_pct": 50.0, "feed_in_w": 4000.0}]
    day = {"date": "2026-10-14", "actual_kwh": 5, "potential_kwh": 8, "sunshine_hours": 4, "hourly": hours}
    sandbox.data_file.write_text(json.dumps({"records": [day]}), encoding="utf-8")
    assert start("--offline", "--no-open") == 0
    assert '"curtailed":"limit"' in sandbox.report.read_text(encoding="utf-8")


def test_curtailment_chain_from_alphaess_samples_to_marked_hours(config):
    """AlphaESS-Proben (mit Akku/Netz) -> Signale -> Datensatz -> Markierung, ohne Mocks der Zwischenschritte."""
    from helpers import FakeAlphaess, FakeTime
    from pvapp import curtailment, model

    day = D(2026, 10, 14)
    weather = make_weather(config, [day])
    potential = model.hourly_wall_clock(model.estimate_hourly_potential(weather.frame, 300.0, 8.0, config))
    potential = potential[potential.index.date == day]

    samples = []
    for ts, kwh in potential.items():
        capped = 11 <= ts.hour <= 14
        for minute in range(0, 60, 5):
            samples.append(
                {
                    "uploadTime": ts.replace(minute=minute).strftime("%Y-%m-%d %H:%M:%S"),
                    "ppv": kwh * 1000 * (0.5 if capped else 0.9),
                    "cbat": 100 if capped else 60,
                    "feedIn": 4000 if capped else 700,
                }
            )
    total = sum(kwh * (0.5 if 11 <= ts.hour <= 14 else 0.9) for ts, kwh in potential.items())
    client = FakeAlphaess(energy={day.isoformat(): {"epv": total}}, power={day.isoformat(): samples})
    clock = FakeTime()

    async def real_fetch(cfg, days, include_hourly, chooser):
        return await sources.fetch_actuals(cfg, days, include_hourly=include_hourly, client_factory=lambda *a: client, sleep=clock.sleep, clock=clock.clock)

    collected = run(app.collect_records(config, [day], load_weather_fn=lambda *a: weather, fetch_actuals_fn=real_fetch))
    record = collected.records[0]
    assert any("soc_pct" in p for p in record["hourly"])

    for settings in (curtailment.CurtailmentSettings(feed_in_limit_w=4000), curtailment.CurtailmentSettings()):
        marked = curtailment.annotate_records(storage.valid_records([record]), settings)[0]
        flagged = [p["hour"] for p in marked["hourly"] if p.get("curtailed")]
        assert flagged == ["11:00", "12:00", "13:00", "14:00"], flagged
        assert marked["curtailment"]["hours"] == 4 and marked["curtailment"]["lost_kwh"] > 3
    # mit Limit lautet der Grund "limit", ohne Limit "battery"
    with_limit = curtailment.annotate_records([record], curtailment.CurtailmentSettings(feed_in_limit_w=4000))[0]
    assert {p["curtailed"] for p in with_limit["hourly"] if p.get("curtailed")} == {"limit"}
    without_limit = curtailment.annotate_records([record], curtailment.CurtailmentSettings())[0]
    assert {p["curtailed"] for p in without_limit["hourly"] if p.get("curtailed")} == {"battery"}


# ------------------------------------------------- Nachladen der Akku-/Netzdaten für gespeicherte Tage


def write_history(box, *days):
    box.data_file.parent.mkdir(parents=True, exist_ok=True)
    box.data_file.write_text(json.dumps({"records": list(days)}), encoding="utf-8")


def stored_day(day, checked=False):
    hours = [
        {"hour": "11:00", "actual_kwh": 1.0, "potential_kwh": 3.0},
        {"hour": "12:00", "actual_kwh": 1.2, "potential_kwh": 3.2},
    ]
    item = {"date": day, "actual_kwh": 5, "potential_kwh": 8, "sunshine_hours": 4, "hourly": hours}
    return {**item, "signals_checked": True} if checked else item


def test_stored_legacy_days_get_their_battery_and_grid_data_and_are_then_marked(sandbox):
    """Regression: der 15.10. wurde vor der Abregelungserkennung gespeichert und blieb ohne Signale."""
    write_history(sandbox, stored_day("2026-10-13", checked=True), stored_day("2026-10-14", checked=True), stored_day("2026-10-15"))
    sandbox.signal_reply = {D(2026, 10, 15): {"11:00": {"soc_pct": 100.0, "feed_in_w": 0.0}, "12:00": {"soc_pct": 100.0, "feed_in_w": 0.0}}}

    assert start("--no-open") == 0
    assert sandbox.calls == [] and sandbox.signal_calls == [[D(2026, 10, 15)]]
    saved = {r["date"]: r for r in json.loads(sandbox.data_file.read_text(encoding="utf-8"))["records"]}
    assert saved["2026-10-15"]["signals_checked"] is True
    assert saved["2026-10-15"]["hourly"][0]["soc_pct"] == 100.0
    assert (saved["2026-10-15"]["actual_kwh"], saved["2026-10-15"]["potential_kwh"]) == (5, 8)  # unverändert
    html = sandbox.report.read_text(encoding="utf-8")
    assert '"curtailed":"battery"' in html  # Akku voll + Ist 33 % unter Potenzial -> markiert

    sandbox.signal_calls.clear()
    start("--no-open")
    assert sandbox.signal_calls == [], "kein erneuter Abruf nach erfolgreichem Nachladen"


def test_days_where_alphaess_has_no_signals_are_not_retried(sandbox):
    write_history(sandbox, stored_day("2026-10-13", checked=True), stored_day("2026-10-14", checked=True), stored_day("2026-10-15"))
    sandbox.signal_reply = {D(2026, 10, 15): {}}  # Abruf erfolgreich, aber ohne Akku-/Netzfelder
    start("--no-open")
    saved = {r["date"]: r for r in json.loads(sandbox.data_file.read_text(encoding="utf-8"))["records"]}
    assert saved["2026-10-15"]["signals_checked"] is True and "soc_pct" not in saved["2026-10-15"]["hourly"][0]
    sandbox.signal_calls.clear()
    start("--no-open")
    assert sandbox.signal_calls == []


def test_failed_backfill_is_retried_and_leaves_the_file_unchanged(sandbox):
    write_history(sandbox, stored_day("2026-10-13", checked=True), stored_day("2026-10-14", checked=True), stored_day("2026-10-15"))
    before = sandbox.data_file.read_bytes()
    sandbox.signal_reply = {}  # Abruf für den 15. schlägt fehl
    start("--no-open")
    assert sandbox.data_file.read_bytes() == before and sandbox.signal_calls == [[D(2026, 10, 15)]]
    start("--no-open")
    assert sandbox.signal_calls == [[D(2026, 10, 15)]] * 2


def test_signal_backfill_can_be_disabled_with_the_detection_switch(sandbox, monkeypatch):
    monkeypatch.setenv("CURTAILMENT_DETECTION", "false")
    write_history(sandbox, stored_day("2026-10-13", checked=True), stored_day("2026-10-14", checked=True), stored_day("2026-10-15"))
    start("--no-open")
    assert sandbox.signal_calls == []


def test_refresh_days_option_and_validation(sandbox):
    start("--no-open")
    sandbox.calls.clear()
    start("--no-open", "--refresh-days", "2")
    assert sandbox.calls == [[D(2026, 10, 14), D(2026, 10, 15)]]
    assert start("--no-open", "--refresh-days", "500") == 1  # ungültig: Fehlermeldung, Bericht trotzdem erzeugt
    assert sandbox.report.exists()
