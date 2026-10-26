import asyncio
from datetime import date

import pandas as pd
import pytest
import requests

from helpers import FakeAlphaess, FakeResponse, FakeTime, samples_for_day
from pvapp import sources
from pvapp.sources import SourceError

NO_SLEEP = lambda seconds: None  # noqa: E731


def hourly_payload(first_utc, last_utc, temp, sunshine=1800.0, elevation=300):
    times = pd.date_range(first_utc, last_utc, freq="h", tz="UTC")
    n = len(times)
    return {
        "elevation": elevation,
        "hourly": {
            "time": [int(t.timestamp()) for t in times],
            "temperature_2m": [temp] * n,
            "wind_speed_10m": [2.0] * n,
            "shortwave_radiation": [100.0] * n,
            "direct_normal_irradiance": [200.0] * n,
            "diffuse_radiation": [50.0] * n,
            "sunshine_duration": [sunshine] * n,
        },
    }


class FakeHttp:
    def __init__(self, archive=None, forecast=None):
        self.archive, self.forecast, self.calls = archive, forecast, []

    def __call__(self, url, params=None, timeout=None):
        which = "archive" if url == sources.ARCHIVE_URL else "forecast"
        self.calls.append((which, params))
        reply = self.archive if which == "archive" else self.forecast
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, FakeResponse):
            return reply
        return FakeResponse(reply)


# ----------------------------------------------------------------------------- fetch_json


def test_fetch_json_retries_server_errors_then_succeeds():
    replies = iter([FakeResponse({}, 500), FakeResponse({}, 429), FakeResponse({"ok": 1})])
    assert sources.fetch_json("u", {}, http_get=lambda *a, **k: next(replies), sleep=NO_SLEEP) == {"ok": 1}


def test_fetch_json_does_not_retry_client_errors_and_reports_the_reason():
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        return FakeResponse({"reason": "end_date is out of range"}, 400)

    with pytest.raises(SourceError, match="out of range"):
        sources.fetch_json("u", {}, http_get=get, sleep=NO_SLEEP)
    assert len(calls) == 1


def test_fetch_json_gives_up_after_network_errors():
    def get(*args, **kwargs):
        raise requests.ConnectionError("offline")

    with pytest.raises(SourceError, match="3 Versuchen"):
        sources.fetch_json("u", {}, http_get=get, sleep=NO_SLEEP)


def test_network_errors_are_short_and_name_the_host():
    def offline(*args, **kwargs):
        raise requests.ConnectionError("HTTPSConnectionPool(host='api.open-meteo.com', port=443): Max retries exceeded ... NameResolutionError ...")

    with pytest.raises(SourceError) as error:
        sources.fetch_json("https://api.open-meteo.com/v1/forecast", {}, http_get=offline, sleep=NO_SLEEP)
    assert "keine Verbindung zu api.open-meteo.com" in str(error.value) and "NameResolutionError" not in str(error.value)

    def slow(*args, **kwargs):
        raise requests.Timeout("read timed out")

    with pytest.raises(SourceError, match="Zeitüberschreitung bei example.org"):
        sources.fetch_json("https://example.org/x", {}, http_get=slow, sleep=NO_SLEEP)


# -------------------------------------------------------------------------------- Wetter


def test_interval_end_timestamps_become_local_midpoints():
    payload = hourly_payload("2026-10-13 10:00", "2026-10-13 10:00", temp=10)
    frame, sunshine, elevation = sources._weather_from_payload(payload, "Europe/Berlin")
    assert frame.index[0] == pd.Timestamp("2026-10-13 11:30", tz="Europe/Berlin")  # 10:00Z = Intervallende -> 09:30Z Mitte
    assert elevation == 300 and sunshine.iloc[0] == 1800


def weather_for(config, http, **kwargs):
    return sources.load_weather(
        config, date(2026, 10, 12), date(2026, 10, 14), http_get=http, today=date(2026, 10, 15), sleep=NO_SLEEP, **kwargs
    )


def test_auto_uses_archive_and_fills_the_missing_recent_day_from_the_forecast(config):
    http = FakeHttp(
        archive=hourly_payload("2026-10-11 00:00", "2026-10-13 23:00", temp=10),
        forecast=hourly_payload("2026-10-11 00:00", "2026-10-15 23:00", temp=99),
    )
    result = weather_for(config, http)

    assert result.source_by_day == {
        date(2026, 10, 12): "archive",
        date(2026, 10, 13): "archive",
        date(2026, 10, 14): "forecast",
    }
    assert result.sunshine_hours[date(2026, 10, 14)] == pytest.approx(12.0)
    day13 = result.frame[result.frame.index.date == date(2026, 10, 13)]
    day14 = result.frame[result.frame.index.date == date(2026, 10, 14)]
    assert (day13["temp_air"] == 10).all()
    assert day14["temp_air"].iloc[12] == 99
    assert [call[0] for call in http.calls] == ["archive", "forecast"]
    assert http.calls[1][1]["past_days"] == 4 and http.calls[1][1]["forecast_days"] == 2


def test_auto_skips_the_forecast_when_the_archive_is_complete(config):
    http = FakeHttp(archive=hourly_payload("2026-10-11 00:00", "2026-10-15 00:00", temp=10))
    result = weather_for(config, http)
    assert set(result.source_by_day.values()) == {"archive"} and len(result.source_by_day) == 3
    assert [call[0] for call in http.calls] == ["archive"]
    assert http.calls[0][1]["end_date"] == "2026-10-15"  # nie nach "heute"


def test_archive_end_date_is_clamped_to_today(config):
    http = FakeHttp(archive=hourly_payload("2026-10-11 00:00", "2026-10-15 00:00", temp=10))
    sources.load_weather(
        config, date(2026, 10, 12), date(2026, 10, 14), http_get=http, today=date(2026, 10, 14), sleep=NO_SLEEP, source="archive"
    )
    assert http.calls[0][1]["end_date"] == "2026-10-14"


def test_auto_falls_back_to_forecast_when_the_archive_fails(config):
    http = FakeHttp(
        archive=FakeResponse({"reason": "boom"}, 400),
        forecast=hourly_payload("2026-10-11 00:00", "2026-10-15 23:00", temp=99),
    )
    result = weather_for(config, http)
    assert set(result.source_by_day.values()) == {"forecast"} and len(result.source_by_day) == 3


def test_archive_mode_raises_instead_of_falling_back(config):
    http = FakeHttp(archive=FakeResponse({"reason": "boom"}, 400), forecast=hourly_payload("2026-10-11 00:00", "2026-10-15 23:00", 1))
    with pytest.raises(SourceError, match="Archiv"):
        weather_for(config, http, source="archive")
    assert all(call[0] == "archive" for call in http.calls)


def test_forecast_mode_never_touches_the_archive(config):
    http = FakeHttp(forecast=hourly_payload("2026-10-11 00:00", "2026-10-15 23:00", temp=5))
    result = weather_for(config, http, source="forecast")
    assert {call[0] for call in http.calls} == {"forecast"} and len(result.source_by_day) == 3


def test_incomplete_days_are_not_reported(config):
    http = FakeHttp(archive=hourly_payload("2026-10-11 00:00", "2026-10-13 12:00", temp=10), forecast=FakeResponse({}, 400))
    result = weather_for(config, http)
    assert date(2026, 10, 13) not in result.source_by_day and date(2026, 10, 14) not in result.source_by_day
    assert date(2026, 10, 12) in result.source_by_day


# ----------------------------------------------------------------------------- Horizont


def test_horizon_is_cached_and_reused(config, tmp_path):
    cache = tmp_path / "horizon.json"
    fetched = []

    def fetch(lat, lon):
        fetched.append((lat, lon))
        return pd.Series([3.0, 10.0, 2.0], index=[0.0, 120.0, 240.0])

    first = sources.load_horizon(config, cache, fetch=fetch)
    again = sources.load_horizon(config, cache, fetch=lambda *a: pytest.fail("Cache wurde nicht genutzt"))
    assert len(fetched) == 1 and list(first) == list(again) == [3.0, 10.0, 2.0]


def test_horizon_failure_falls_back_to_none(config, tmp_path):
    def boom(lat, lon):
        raise RuntimeError("PVGIS offline")

    assert sources.load_horizon(config, tmp_path / "h.json", fetch=boom) is None


def test_cache_for_another_location_is_ignored(make_config, tmp_path):
    cache = tmp_path / "h.json"
    sources.load_horizon(make_config(), cache, fetch=lambda *a: pd.Series([1.0], index=[0.0]))
    other = make_config(LATITUDE=50.0)
    result = sources.load_horizon(other, cache, fetch=lambda *a: pd.Series([7.0], index=[0.0]))
    assert list(result) == [7.0]


# --------------------------------------------------------------------------------- AlphaESS


def run(coro):
    return asyncio.run(coro)


def fetch(config, days, client, clock=None, **kwargs):
    clock = clock or FakeTime()
    return run(
        sources.fetch_actuals(
            config, days, client_factory=lambda *a: client, sleep=clock.sleep, clock=clock.clock, **kwargs
        )
    )


def test_energy_and_hourly_profile_are_collected(config):
    day = date(2026, 10, 13)
    client = FakeAlphaess(energy={"2026-10-13": {"epv": 4.8}}, power={"2026-10-13": samples_for_day(day, 8, 12, 1000)})
    result = fetch(config, [day], client, include_hourly=True)
    assert result.energy == {day: 4.8} and result.inverter_power_kw == 8.0
    assert sum(result.hourly[day].values()) == pytest.approx(4.8, abs=0.01)
    assert client.closed


def test_one_failing_day_does_not_abort_the_others(config):
    client = FakeAlphaess(energy={"2026-10-13": {"epv": None}, "2026-10-14": {"epv": 6.1}})
    result = fetch(config, [date(2026, 10, 14), date(2026, 10, 13)], client)
    assert result.energy == {date(2026, 10, 14): 6.1}
    assert "epv" in result.errors[date(2026, 10, 13)]


def test_missing_hourly_data_still_returns_the_daily_value(config):
    client = FakeAlphaess(energy={"2026-10-13": {"epv": 3.0}}, fail_power=True)
    result = fetch(config, [date(2026, 10, 13)], client, include_hourly=True)
    assert result.energy[date(2026, 10, 13)] == 3.0 and result.hourly[date(2026, 10, 13)] == {}


def test_rate_limit_pauses_between_every_call(make_config):
    config = make_config(API_REQUEST_INTERVAL_SECONDS=10)
    energy = {"2026-10-13": {"epv": 1.0}, "2026-10-14": {"epv": 2.0}}
    clock = FakeTime()
    fetch(config, [date(2026, 10, 13), date(2026, 10, 14)], FakeAlphaess(energy=energy), clock, include_hourly=True)
    assert clock.sleeps == [10, 10, 10, 10]


def test_no_pauses_when_interval_is_zero(config):
    clock = FakeTime()
    fetch(config, [date(2026, 10, 13)], FakeAlphaess(energy={"2026-10-13": {"epv": 1.0}}), clock, include_hourly=True)
    assert clock.sleeps == []


def test_missing_credentials_raise_before_any_request(make_config):
    config = make_config(ALPHAESS_APP_ID="", ALPHAESS_APP_SECRET="")
    with pytest.raises(SourceError, match="ALPHAESS_APP_ID"):
        run(sources.fetch_actuals(config, [date(2026, 10, 13)], client_factory=lambda *a: pytest.fail("kein Client")))


def test_invalid_inverter_power_raises_and_client_is_closed(config):
    client = FakeAlphaess(systems=[{"sysSn": "SN1", "poinv": 0}])
    with pytest.raises(SourceError, match="poinv"):
        fetch(config, [date(2026, 10, 13)], client)
    assert client.closed


def test_system_selection():
    one = {"sysSn": "A", "poinv": 5}
    two = {"sysSn": "B", "poinv": 8}
    assert sources.choose_system([one]) is one
    assert sources.choose_system([one, two], "B") is two
    assert sources.choose_system([one, two], chooser=lambda systems: systems[0]) is one
    with pytest.raises(SourceError, match="ALPHAESS_SYSTEM_SN"):
        sources.choose_system([one, two])
    with pytest.raises(SourceError, match="passt zu keiner"):
        sources.choose_system([one, two], "X")
    with pytest.raises(SourceError, match="keine Anlagen"):
        sources.choose_system([])


def test_signals_are_collected_per_hour(config):
    day = date(2026, 10, 13)
    samples = [{**s, "cbat": 100, "feedIn": 4000} for s in samples_for_day(day, 10, 12, 3000)]
    client = FakeAlphaess(energy={"2026-10-13": {"epv": 6.0}}, power={"2026-10-13": samples})
    result = fetch(config, [day], client, include_hourly=True)
    assert result.signals[day]["10:00"] == {"soc_pct": 100.0, "feed_in_w": 4000.0}


def test_missing_fields_are_reported_once(config, caplog):
    days = [date(2026, 10, 13), date(2026, 10, 14)]
    energy = {d.isoformat(): {"epv": 3.0} for d in days}
    power = {d.isoformat(): samples_for_day(d, 10, 12, 500) for d in days}  # ohne cbat/feedIn
    with caplog.at_level("WARNING"):
        fetch(config, days, FakeAlphaess(energy=energy, power=power), include_hourly=True)
    warnings = [r for r in caplog.records if "cbat" in r.getMessage()]
    assert len(warnings) == 1 and "feedIn" in warnings[0].getMessage() and "alphaess_fields" in warnings[0].getMessage()


def test_custom_field_names_from_the_configuration_are_used(make_config):
    config = make_config(ALPHAESS_SOC_FIELD="soc", ALPHAESS_FEED_IN_FIELD="grid")
    day = date(2026, 10, 13)
    samples = [{**s, "soc": 77, "grid": 1500} for s in samples_for_day(day, 10, 12, 500)]
    result = fetch(config, [day], FakeAlphaess(energy={"2026-10-13": {"epv": 1.0}}, power={"2026-10-13": samples}), include_hourly=True)
    assert result.signals[day]["10:00"] == {"soc_pct": 77.0, "feed_in_w": 1500.0}
