import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock

import pandas as pd

import pv_app


START = date(2026, 10, 13)
TODAY = date(2026, 10, 16)


def configure_sources(monkeypatch, days, missing_weather_days=(), today=TODAY):
    index = pd.date_range(
        START.isoformat(),
        periods=(today - START).days * 24,
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
    weather_loader = Mock(return_value=(weather, 0.0, sunshine, sources))
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
    weather_loader.assert_called_once_with(missing[0], missing[-1])


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
    weather_loader.assert_not_called()


def test_missing_weather_skips_only_that_day(monkeypatch, capsys):
    missing = [START + timedelta(days=offset) for offset in range(3)]
    _, weather_loader = configure_sources(monkeypatch, missing, missing_weather_days=[missing[1]])

    records = asyncio.run(pv_app.collect_backfill_records([], today=TODAY))

    assert [record["date"] for record in records] == [missing[0].isoformat(), missing[2].isoformat()]
    weather_loader.assert_called_once_with(missing[0], missing[-1])
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


def test_backfill_limit_logs_and_caps_the_range(monkeypatch, capsys):
    later_today = date(2026, 11, 4)
    yesterday = later_today - timedelta(days=1)
    missing = [START + timedelta(days=offset) for offset in range((yesterday - START).days + 1)]
    actual_loader, _ = configure_sources(monkeypatch, missing, today=later_today)
    monkeypatch.setenv("BACKFILL_MAX_DAYS", "14")

    records = asyncio.run(pv_app.collect_backfill_records([], today=later_today))

    assert len(records) == 14
    actual_loader.assert_awaited_once_with(START, START + timedelta(days=13), include_hourly=True)
    assert "8 ältere(n) fehlende(n) Tag(e) übersprungen" in capsys.readouterr().out


def test_main_passes_refresh_flag_to_collector(monkeypatch, tmp_path):
    data_path = tmp_path / "daily.json"
    report_path = tmp_path / "dashboard.html"
    collector = AsyncMock(return_value=[])
    monkeypatch.setattr(pv_app, "DATA_PATH", data_path)
    monkeypatch.setattr(pv_app, "REPORT_PATH", report_path)
    monkeypatch.setattr(pv_app, "collect_backfill_records", collector)
    monkeypatch.setattr(pv_app, "create_standalone_report", lambda records: report_path)
    monkeypatch.setattr("sys.argv", ["pv_app.py", "--refresh", "--no-open"])

    pv_app.main()

    collector.assert_awaited_once_with([], refresh=True)
    assert not data_path.exists()
