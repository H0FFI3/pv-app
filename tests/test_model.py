from datetime import date

import pandas as pd
import pvlib
import pytest

from helpers import clear_sky_weather
from pvapp import model


def legacy_hourly_potential(weather, elevation, inverter_kw, c):
    """Unveränderte Kopie der Rechnung aus der Vorversion (analyze_pv.py / compare_daily.py)."""

    def array_power(solar_position, dni_extra, modules, azimuth):
        irradiance = pvlib.irradiance.get_total_irradiance(
            surface_tilt=c.surface_tilt,
            surface_azimuth=azimuth,
            solar_zenith=solar_position["apparent_zenith"],
            solar_azimuth=solar_position["azimuth"],
            dni=weather["dni"],
            ghi=weather["ghi"],
            dhi=weather["dhi"],
            dni_extra=dni_extra,
            model="haydavies",
        )
        cell_temperature = pvlib.temperature.noct_sam(
            irradiance["poa_global"], weather["temp_air"], weather["wind_speed"],
            noct=c.module_nmot_c, module_efficiency=c.module_efficiency,
        )
        return pvlib.pvsystem.pvwatts_dc(
            effective_irradiance=irradiance["poa_global"] * (1 - c.system_loss_fraction),
            temp_cell=cell_temperature,
            pdc0=modules * c.module_power_wp,
            gamma_pdc=c.power_temperature_coefficient,
        ).clip(lower=0)

    sp = pvlib.solarposition.get_solarposition(
        weather.index, c.latitude, c.longitude, altitude=elevation, temperature=weather["temp_air"]
    )
    extra = pvlib.irradiance.get_extra_radiation(weather.index)
    total = sum(array_power(sp, extra, a.modules, a.azimuth) for a in c.arrays.values()).clip(lower=0)
    ac = pvlib.inverter.pvwatts(
        total, pdc0=inverter_kw * 1000 / c.nominal_inverter_efficiency, eta_inv_nom=c.nominal_inverter_efficiency
    ).clip(lower=0)
    return ac.resample("h").sum(min_count=1) / 1000


def sky(config, start, end=None, **kwargs):
    return clear_sky_weather(config.latitude, config.longitude, config.timezone, start, end or start, **kwargs)


def test_default_model_equals_the_previous_version(config):
    weather = sky(config, date(2026, 6, 15), date(2026, 6, 16), temp=22.0, wind=3.0)
    new = model.estimate_hourly_potential(weather, 300, 8.0, config)
    old = legacy_hourly_potential(weather, 300, 8.0, config)
    pd.testing.assert_series_equal(new, old)


def test_plausible_clear_sky_yields(config):
    summer = model.estimate_hourly_potential(sky(config, date(2026, 6, 15)), 300, 8.0, config).sum()
    autumn = model.estimate_hourly_potential(sky(config, date(2026, 10, 14)), 300, 8.0, config).sum()
    assert 40 < summer < 60  # ca. 8,6 kWp
    assert 12 < autumn < 28
    assert summer > autumn * 1.8


def test_east_west_roof_is_symmetric_around_solar_noon(make_config):
    config = make_config(LONGITUDE=0, TIMEZONE="UTC")  # Sonnenmittag ~12:00 UTC; Zeitgleichung um 15.04. ~0
    hourly = model.estimate_hourly_potential(sky(config, date(2026, 4, 15)), 0, 20.0, config)
    for offset in (1, 2, 3, 4):
        before = hourly.iloc[12 - offset]
        after = hourly.iloc[12 + offset - 1]
        assert before == pytest.approx(after, rel=0.06), offset


def test_inverter_limit_clips_output(make_config):
    config = make_config(MODULES_EAST=20, MODULES_WEST=20)  # 17,2 kWp
    hourly = model.estimate_hourly_potential(sky(config, date(2026, 6, 15)), 300, 4.0, config)
    assert hourly.max() <= 4.0 + 1e-9
    assert (hourly > 3.95).sum() >= 3  # mehrere Stunden am Limit


def test_iam_lowers_yield_moderately(make_config):
    base = make_config()
    with_iam = make_config(IAM_ENABLED="true", IAM_B=0.05)
    weather = sky(base, date(2026, 10, 14))
    without = model.estimate_hourly_potential(weather, 300, 8.0, base)
    reduced = model.estimate_hourly_potential(weather, 300, 8.0, with_iam)
    assert 0.9 * without.sum() < reduced.sum() < 0.995 * without.sum()
    assert (reduced <= without + 1e-9).all()  # IAM kann den Ertrag nie erhöhen


def test_albedo_increases_yield(make_config):
    low, high = make_config(ALBEDO=0.1), make_config(ALBEDO=0.8)
    weather = sky(low, date(2026, 10, 14))
    assert model.estimate_hourly_potential(weather, 300, 8.0, high).sum() > model.estimate_hourly_potential(weather, 300, 8.0, low).sum()


def test_bifacial_gain_needs_both_switches(make_config):
    weather = sky(make_config(), date(2026, 10, 14))
    plain = model.estimate_hourly_potential(weather, 300, 30.0, make_config()).sum()
    gain_only = model.estimate_hourly_potential(weather, 300, 30.0, make_config(BIFACIALITY_GAIN=0.1)).sum()
    bifacial = model.estimate_hourly_potential(
        weather, 300, 30.0, make_config(MODULE_BIFACIAL="true", BIFACIALITY_GAIN=0.1)
    ).sum()
    assert gain_only == pytest.approx(plain)
    assert bifacial == pytest.approx(plain * 1.1, rel=0.02)  # Wechselrichter-Wirkungsgrad ist nicht exakt linear


def test_horizon_blocks_direct_light(config):
    weather = sky(config, date(2026, 10, 14))
    open_sky = pd.Series([0.0, 0.0], index=[0.0, 180.0])
    wall = pd.Series([90.0, 90.0], index=[0.0, 180.0])  # alles verdeckt -> nur diffus
    base = model.estimate_hourly_potential(weather, 300, 8.0, config)
    assert model.estimate_hourly_potential(weather, 300, 8.0, config, open_sky).sum() == pytest.approx(base.sum(), rel=0.01)
    blocked = model.estimate_hourly_potential(weather, 300, 8.0, config, wall).sum()
    assert 0 < blocked < base.sum() * 0.6


def test_apply_horizon_only_changes_shaded_hours(config):
    weather = sky(config, date(2026, 10, 14))
    position = pvlib.solarposition.get_solarposition(weather.index, config.latitude, config.longitude)
    # Hügel im Osten (Azimut 90) mit 25° Höhe, sonst frei
    horizon = pd.Series([0.0, 25.0, 0.0], index=[0.0, 90.0, 180.0])
    result = model.apply_horizon(weather, position, horizon)
    changed = result["dni"] != weather["dni"]
    assert changed.any() and not changed.all()
    assert (position.loc[changed, "azimuth"] < 160).all()
    assert (result.loc[changed, "ghi"] == result.loc[changed, "dhi"]).all()
    pd.testing.assert_frame_equal(result.loc[~changed], weather.loc[~changed])


def test_wall_clock_hours_merge_the_repeated_autumn_hour():
    index = pd.date_range("2026-10-25 00:00", "2026-10-26 00:00", freq="h", tz="Europe/Berlin", inclusive="left")
    assert len(index) == 25
    series = pd.Series(1.0, index=index)
    merged = model.hourly_wall_clock(series)
    assert len(merged) == 24
    assert merged[pd.Timestamp("2026-10-25 02:00")] == 2.0
    assert merged.index.tz is None


def test_wall_clock_on_spring_forward_day_has_23_hours():
    index = pd.date_range("2026-03-29 00:00", "2026-03-30 00:00", freq="h", tz="Europe/Berlin", inclusive="left")
    merged = model.hourly_wall_clock(pd.Series(1.0, index=index))
    assert len(merged) == 23 and pd.Timestamp("2026-03-29 02:00") not in merged.index
