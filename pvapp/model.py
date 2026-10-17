"""PV-Modell (pvlib). Reine Funktionen ohne Netzwerk- oder Dateizugriff.

Standardverhalten ist identisch zur Vorversion (Haydavies-Transposition, NOCT-SAM-Zelltemperatur,
PVWatts-DC/AC, pauschale Systemverluste). Die Erweiterungen sind per Config abschaltbar und
standardmäßig aus: Einfallswinkel-Modifikator (IAM), Albedo, Horizont, Rückseitengewinn.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib

from .config import ArrayConfig, Config


def apply_horizon(
    weather: pd.DataFrame,
    solar_position: pd.DataFrame,
    horizon: pd.Series,
) -> pd.DataFrame:
    """Setzt die direkte Strahlung auf 0, wenn die Sonne unter dem Horizontprofil steht.

    ``horizon``: Index = Azimut in Grad (pvlib-Konvention, Nord = 0, Süd = 180), Wert = Horizonthöhe in Grad.
    Die globale Strahlung wird in diesen Stunden auf die diffuse reduziert.
    """
    azimuths = horizon.index.to_numpy(dtype=float)
    elevations = horizon.to_numpy(dtype=float)
    order = np.argsort(azimuths)
    horizon_at_sun = np.interp(
        solar_position["azimuth"].to_numpy(dtype=float),
        azimuths[order],
        elevations[order],
        period=360,
    )
    shaded = pd.Series(
        solar_position["apparent_elevation"].to_numpy(dtype=float) <= horizon_at_sun,
        index=weather.index,
    )
    result = weather.copy()
    result.loc[shaded, "dni"] = 0.0
    result.loc[shaded, "ghi"] = result.loc[shaded, "dhi"]
    return result


def estimate_array_power(
    weather: pd.DataFrame,
    solar_position: pd.DataFrame,
    dni_extra: pd.Series,
    array: ArrayConfig,
    config: Config,
) -> pd.Series:
    """DC-Leistung (W) eines Modulfelds."""
    irradiance = pvlib.irradiance.get_total_irradiance(
        surface_tilt=config.surface_tilt,
        surface_azimuth=array.azimuth,
        solar_zenith=solar_position["apparent_zenith"],
        solar_azimuth=solar_position["azimuth"],
        dni=weather["dni"],
        ghi=weather["ghi"],
        dhi=weather["dhi"],
        dni_extra=dni_extra,
        albedo=config.albedo,
        model="haydavies",
    )
    poa_global = irradiance["poa_global"]
    cell_temperature = pvlib.temperature.noct_sam(
        poa_global,
        weather["temp_air"],
        weather["wind_speed"],
        noct=config.module_nmot_c,
        module_efficiency=config.module_efficiency,
    )

    effective = poa_global
    if config.iam_enabled:
        aoi = pvlib.irradiance.aoi(
            config.surface_tilt,
            array.azimuth,
            solar_position["apparent_zenith"],
            solar_position["azimuth"],
        )
        iam = pd.Series(np.asarray(pvlib.iam.ashrae(aoi, b=config.iam_b), dtype=float), index=weather.index)
        iam = iam.fillna(0.0).clip(0.0, 1.0)
        effective = irradiance["poa_direct"].fillna(0.0) * iam + irradiance["poa_diffuse"].fillna(0.0)

    dc_power = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=effective * (1 - config.system_loss_fraction),
        temp_cell=cell_temperature,
        pdc0=array.modules * config.module_power_wp,
        gamma_pdc=config.power_temperature_coefficient,
    ).clip(lower=0)

    if config.module_bifacial and config.bifaciality_gain > 0:
        dc_power = dc_power * (1 + config.bifaciality_gain)
    return dc_power


def estimate_hourly_potential(
    weather: pd.DataFrame,
    elevation: float,
    inverter_power_kw: float,
    config: Config,
    horizon: pd.Series | None = None,
) -> pd.Series:
    """Stündliches AC-Potenzial in kWh. Index = Stundenbeginn (Zeitzone des Wetter-Index).

    ``weather``: Index = Intervall-Mittelpunkte (HH:30); Spalten temp_air, wind_speed, ghi, dni, dhi.
    """
    solar_position = pvlib.solarposition.get_solarposition(
        weather.index,
        config.latitude,
        config.longitude,
        altitude=elevation,
        temperature=weather["temp_air"],
    )
    dni_extra = pvlib.irradiance.get_extra_radiation(weather.index)
    if horizon is not None and len(horizon):
        weather = apply_horizon(weather, solar_position, horizon)

    array_power = [
        estimate_array_power(weather, solar_position, dni_extra, array, config)
        for array in config.arrays.values()
    ]
    total_dc_power = sum(array_power).clip(lower=0)
    ac_power = pvlib.inverter.pvwatts(
        total_dc_power,
        pdc0=inverter_power_kw * 1000 / config.nominal_inverter_efficiency,
        eta_inv_nom=config.nominal_inverter_efficiency,
    ).clip(lower=0)
    return ac_power.resample("h").sum(min_count=1) / 1000


def estimate_daily_potential(
    weather: pd.DataFrame,
    elevation: float,
    inverter_power_kw: float,
    config: Config,
    horizon: pd.Series | None = None,
) -> pd.Series:
    hourly = estimate_hourly_potential(weather, elevation, inverter_power_kw, config, horizon)
    return hourly.resample("D").sum(min_count=1)


def hourly_wall_clock(series: pd.Series) -> pd.Series:
    """Stundenwerte auf lokale Uhrzeit (ohne Zeitzone) umstellen.

    Am Tag der Zeitumstellung im Herbst kommt die Stunde 02:00 zweimal vor: beide Stunden werden zu
    einem Eintrag "02:00" zusammengefasst, damit Labels eindeutig bleiben und zu den AlphaESS-Stunden passen.
    """
    wall = series.index.tz_localize(None).floor("h") if series.index.tz is not None else series.index.floor("h")
    return series.groupby(wall).sum(min_count=1)
