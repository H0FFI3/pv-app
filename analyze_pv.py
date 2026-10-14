from pathlib import Path

import pandas as pd
import pvlib

from pv_config import (
    ARRAYS,
    LATITUDE,
    LONGITUDE,
    MODULE_BIFACIAL,
    MODULE_EFFICIENCY,
    MODULE_MODEL,
    MODULE_NMOT_C,
    MODULE_POWER_WP,
    POWER_TEMPERATURE_COEFFICIENT,
    SURFACE_TILT,
    SYSTEM_LOSS_FRACTION,
    TIMEZONE,
    validate_configuration,
)


def estimate_array_power(
    weather: pd.DataFrame,
    solar_position: pd.DataFrame,
    dni_extra: pd.Series,
    modules: int,
    azimuth: int,
) -> pd.Series:
    irradiance = pvlib.irradiance.get_total_irradiance(
        surface_tilt=SURFACE_TILT,
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
        irradiance["poa_global"],
        weather["temp_air"],
        weather["wind_speed"],
        noct=MODULE_NMOT_C,
        module_efficiency=MODULE_EFFICIENCY,
    )
    return pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=irradiance["poa_global"] * (1 - SYSTEM_LOSS_FRACTION),
        temp_cell=cell_temperature,
        pdc0=modules * MODULE_POWER_WP,
        gamma_pdc=POWER_TEMPERATURE_COEFFICIENT,
    ).clip(lower=0)


def main() -> None:
    validate_configuration()
    weather, metadata = pvlib.iotools.get_pvgis_tmy(
        LATITUDE,
        LONGITUDE,
        timeout=30,
    )
    elevation = metadata.get("inputs", {}).get("location", {}).get("elevation", 0)
    solar_position = pvlib.solarposition.get_solarposition(
        weather.index,
        LATITUDE,
        LONGITUDE,
        altitude=elevation,
        temperature=weather["temp_air"],
    )
    dni_extra = pvlib.irradiance.get_extra_radiation(weather.index)

    expected = pd.DataFrame(index=weather.index)
    for side, array in ARRAYS.items():
        expected[f"{side}_dc_w"] = estimate_array_power(
            weather,
            solar_position,
            dni_extra,
            modules=array["modules"],
            azimuth=array["azimuth"],
        )
    expected["total_dc_w"] = expected["east_dc_w"] + expected["west_dc_w"]
    expected.index = expected.index.tz_convert(TIMEZONE)
    expected.index.name = "timestamp"

    output_path = Path(__file__).with_name("pv_expected_tmy.json")
    expected.to_json(output_path, orient="table", date_format="iso", double_precision=3)

    installed_power_kwp = sum(array["modules"] for array in ARRAYS.values())
    installed_power_kwp *= MODULE_POWER_WP / 1000
    annual_dc_energy_kwh = expected["total_dc_w"].sum() / 1000
    print(f"Installed DC power: {installed_power_kwp:.2f} kWp")
    print(
        f"Module: {MODULE_MODEL} (NMOT {MODULE_NMOT_C:g} C, "
        f"Pmax {POWER_TEMPERATURE_COEFFICIENT * 100:g}%/K)"
    )
    print(f"Estimated annual DC energy: {annual_dc_energy_kwh:.0f} kWh")
    print(f"Specific yield: {annual_dc_energy_kwh / installed_power_kwp:.0f} kWh/kWp")
    print(f"Hourly estimate saved to: {output_path}")
    rear_gain = "bifacial rear gain" if MODULE_BIFACIAL else "rear-side gain"
    print(
        f"Estimate uses PVGIS typical weather and {SYSTEM_LOSS_FRACTION:.0%} "
        f"system losses; {rear_gain} and inverter clipping are excluded."
    )


if __name__ == "__main__":
    main()