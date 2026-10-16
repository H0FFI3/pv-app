import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pandas as pd

import pv_app


START = date(2026, 10, 13)
TODAY = date(2026, 10, 16)


def configure_sources(monkeypatch, days, missing_weather_days=()):
    index = pd.date_range(
        START.isoformat(),
        periods=(TODAY - START).days * 24,
        freq="h",
        tz=pv_app.TIMEZONE,
    )
    weather = pd.DataFrame(
        {
            "temp_air": 15.0,
            "wind_speed": 1.0,
            "ghi": 100.0,
            "dni": 100.0,
            "dhi": 20.0,
        },
        index=index[~index.normalize().isin(
            pd.DatetimeIndex([day.isoformat() for day in missing_weather_days]).tz_localize(
                pv_app.TIMEZONE
            )
        )],
    )
    sunshine = {day: 3.0 for day in days if day not in missing_weather_days}
    sources = {day: "forecast" for day in days if day not in missing_weather_days}
    actual = {day: 5.0 for day in days}
    hourly_actual = {day: {} for day in days}
    actual_loader = AsyncMock(return_value=(actual, 6.0, hourly_actual))
    weather_loader = AsyncMock(return_value=(weather, 0.0, sunshine, sources))
    monkeypatch.setattr(pv_app, "load_actual_energy", actual_loader)
    monkeypatch.setattr(pv_app, "load_weather", weather_loader)
    monkeypatch.setattr(
        pv_app,
        "estimate_hourly_potential",
        lambda frame, elevation, inverter_power: pd.Series(1000.0, index=frame.index),
    )
    monkeypatch.setattr(pv_app, "DATA_START_DATE", START)
    monkeypatch.setenv("BACKFILL_MAX_DAYS", "14")
    return actual_loader, weather_loader


def test_backfill_collects_three_missing_days(monkeypatch):
    missing = [START + timedelta(days=offset) for offset in range(3)]
    actual_loader, weather_loader = configure_sources(monkeypatch, missing)

    records = asyncio.run(pv_app.collect_backfill_records([], today=TODAY))

    assert [record["date"] for record in records] == [day.isoformat() for day in missing]
    assert all(record["weather_source"] == "forecast" for record in records)
    actual_loader.assert_awaited_once_with(missing[0], missing[-1], include_hourly=True)
    weather_loader.assert_awaited_once_with(missing[0], missing[-1])


def test_stored_yesterday_skips_all_source_calls(monkeypatch):
    yesterday = TODAY - timedelta(days=1)
    actual_loader, weather_loader = configure_sources(monkeypatch, [yesterday])
    existing = [
        {
            "date": yesterday.isoformat(),
            "actual_kwh": 5.0,
            "potential_kwh": 6.0,
            "sunshine_hours": 3.0,
        }
    ]

    records = asyncio.run(pv_app.collect_backfill_records(existing, today=TODAY))

    assert records == []
    actual_loader.assert_not_awaited()
    weather_loader.assert_not_awaited()


def test_missing_weather_skips_only_that_day(monkeypatch, capsys):
    missing = [START + timedelta(days=offset) for offset in range(3)]
    _, weather_loader = configure_sources(monkeypatch, missing, missing_weather_days=[missing[1]])

    records = asyncio.run(pv_app.collect_backfill_records([], today=TODAY))

    assert [record["date"] for record in records] == [missing[0].isoformat(), missing[2].isoformat()]
    assert missing[0] in weather_loader.await_args.args
    assert "übersprungen" in capsys.readouterr().out.lower()


def test_refresh_recollects_stored_yesterday(monkeypatch):
    yesterday = TODAY - timedelta(days=1)
    actual_loader, _ = configure_sources(monkeypatch, [yesterday])
    existing = [
        {
            "date": yesterday.isoformat(),
            "actual_kwh": 5.0,
            "potential_kwh": 6.0,
            "sunshine_hours": 3.0,
        }
    ]

    records = asyncio.run(
        pv_app.collect_backfill_records(existing, refresh=True, today=TODAY)
    )

    assert [record["date"] for record in records] == [yesterday.isoformat()]
    actual_loader.assert_awaited_once_with(yesterday, yesterday, include_hourly=True)
