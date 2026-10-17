from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

import compare_daily


def make_payload(local_day, timezone):
    local_times = pd.date_range(
        local_day.isoformat(), periods=24, freq="h", tz=timezone
    )
    unix_times = [int(timestamp.timestamp()) for timestamp in local_times]
    return {
        "elevation": 12,
        "hourly": {
            "time": unix_times,
            "temperature_2m": [15.0] * 24,
            "wind_speed_10m": [1.0] * 24,
            "shortwave_radiation": [100.0] * 24,
            "direct_normal_irradiance": [120.0] * 24,
            "diffuse_radiation": [20.0] * 24,
        },
        "daily": {
            "time": [local_day.isoformat()],
            "sunshine_duration": [7200.0],
        },
    }


def test_yesterday_uses_forecast_past_days_and_marks_source(monkeypatch):
    timezone = compare_daily.TIMEZONE
    yesterday = pd.Timestamp.now(tz=timezone).date() - timedelta(days=1)
    payload = make_payload(yesterday, timezone)
    requests = []

    def fake_get(url, params, timeout):
        requests.append((url, params, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(compare_daily.requests, "get", fake_get)

    weather, elevation, sunshine, sources = compare_daily.load_weather(yesterday, yesterday)

    assert len(requests) == 1
    assert requests[0][0].endswith("/v1/forecast")
    assert requests[0][1]["past_days"] == 2
    assert requests[0][1]["timezone"] == timezone
    assert elevation == 12
    assert sunshine[yesterday] == 2.0
    assert sources[yesterday] == "forecast"
    expected_first = pd.Timestamp(yesterday.isoformat(), tz=timezone) - pd.Timedelta(minutes=30)
    assert weather.index[0] == expected_first


def test_older_days_use_historical_weather_archive(monkeypatch):
    timezone = compare_daily.TIMEZONE
    old_day = pd.Timestamp.now(tz=timezone).date() - timedelta(days=30)
    payload = make_payload(old_day, timezone)
    requests = []

    def fake_get(url, params, timeout):
        requests.append((url, params))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(compare_daily.requests, "get", fake_get)

    _, _, _, sources = compare_daily.load_weather(old_day, old_day)

    assert len(requests) == 1
    assert requests[0][0].endswith("/v1/archive")
    assert requests[0][1]["start_date"] == (old_day - timedelta(days=1)).isoformat()
    assert sources[old_day] == "archive"
