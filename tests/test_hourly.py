from datetime import date

import pandas as pd

from helpers import samples_for_day
from pvapp.sources import aggregate_hourly_pv_power

TZ = "Europe/Berlin"


def test_constant_power_is_distributed_and_scaled_to_the_daily_value():
    day = date(2026, 10, 14)
    samples = samples_for_day(day, start_hour=8, end_hour=12, watts=1000)  # 4 h * 1 kW = 4 kWh
    hourly = aggregate_hourly_pv_power(samples, day, daily_energy_kwh=5.0, timezone=TZ)
    assert list(hourly) == ["08:00", "09:00", "10:00", "11:00"]
    assert sum(hourly.values()) == pytest_approx(5.0)
    assert all(abs(v - 1.25) < 0.01 for v in hourly.values())


def pytest_approx(value):
    import pytest

    return pytest.approx(value, abs=0.01)


def test_other_days_garbage_and_negative_values_are_ignored():
    day = date(2026, 10, 14)
    samples = samples_for_day(day, 10, 12, 600) + samples_for_day(date(2026, 10, 15), 10, 12, 9999)
    samples += [{"uploadTime": "kaputt", "ppv": 5}, {"uploadTime": "2026-10-14 11:00:00", "ppv": None}]
    hourly = aggregate_hourly_pv_power(samples, day, 1.2, TZ)
    assert set(hourly) == {"10:00", "11:00"}
    assert sum(hourly.values()) == pytest_approx(1.2)


def test_too_few_samples_give_no_profile():
    day = date(2026, 10, 14)
    assert aggregate_hourly_pv_power([], day, 3.0, TZ) == {}
    assert aggregate_hourly_pv_power(samples_for_day(day, 10, 10, 100)[:1], day, 3.0, TZ) == {}


def test_timezone_aware_timestamps_are_converted_to_local_time():
    day = date(2026, 10, 14)
    samples = [
        {"uploadTime": "2026-10-14T08:00:00+00:00", "ppv": 1000},  # 10:00 Ortszeit (MESZ)
        {"uploadTime": "2026-10-14T08:05:00+00:00", "ppv": 1000},
        {"uploadTime": "2026-10-14T08:10:00+00:00", "ppv": 1000},
    ]
    assert list(aggregate_hourly_pv_power(samples, day, 1.0, TZ)) == ["10:00"]


def test_fall_back_day_keeps_unique_hour_labels_and_the_daily_total():
    """25-Stunden-Tag (25.10.2026): früher gingen Proben der doppelten Stunde verloren (ambiguous='NaT')
    und Labels konnten doppelt auftreten. Jetzt: Wanduhr-Zeit, 24 eindeutige Labels, Summe = Tageswert."""
    day = date(2026, 10, 25)
    samples = samples_for_day(day, start_hour=0, end_hour=24, watts=1000)
    samples += [{"uploadTime": f"2026-10-25 02:{m:02d}:30", "ppv": 1000} for m in range(0, 60, 5)]  # zweite 02-Uhr-Stunde
    hourly = aggregate_hourly_pv_power(samples, day, daily_energy_kwh=24.0, timezone=TZ)
    assert len(hourly) == 24 and "02:00" in hourly
    assert sum(hourly.values()) == pytest_approx(24.0)
    # Die zusätzlichen Proben verzerren die Stunde nicht (Zeitintervalle summieren sich weiterhin zu einer Stunde).
    assert hourly["02:00"] == pytest_approx(hourly["03:00"])


def test_all_zero_power_returns_zeros_without_division_error():
    day = date(2026, 10, 14)
    hourly = aggregate_hourly_pv_power(samples_for_day(day, 10, 12, 0), day, 0.0, TZ)
    assert set(hourly.values()) == {0.0}
    assert isinstance(pd.Series(hourly).sum(), float)
