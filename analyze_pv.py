"""Optional: Jahres-Referenz aus dem PVGIS-Referenzjahr (TMY) -> pv_expected_tmy.json.

Dünnes Kommandozeilenskript; das Modell selbst liegt in pvapp/model.py.
"""

import sys
from pathlib import Path

import pandas as pd
import pvlib

from pvapp.config import ConfigError, load_config
from pvapp.model import estimate_array_power

OUTPUT_PATH = Path(__file__).with_name("pv_expected_tmy.json")


def main() -> None:
    config = load_config()
    weather, metadata = pvlib.iotools.get_pvgis_tmy(config.latitude, config.longitude, timeout=30)
    elevation = metadata.get("inputs", {}).get("location", {}).get("elevation", 0)
    solar_position = pvlib.solarposition.get_solarposition(
        weather.index,
        config.latitude,
        config.longitude,
        altitude=elevation,
        temperature=weather["temp_air"],
    )
    dni_extra = pvlib.irradiance.get_extra_radiation(weather.index)

    expected = pd.DataFrame(index=weather.index)
    for side, array in config.arrays.items():
        expected[f"{side}_dc_w"] = estimate_array_power(weather, solar_position, dni_extra, array, config)
    expected["total_dc_w"] = expected["east_dc_w"] + expected["west_dc_w"]
    expected.index = expected.index.tz_convert(config.timezone)
    expected.index.name = "timestamp"
    expected.to_json(OUTPUT_PATH, orient="table", date_format="iso", double_precision=3)

    installed_kwp = config.installed_kwp
    annual_dc_energy_kwh = expected["total_dc_w"].sum() / 1000
    print(f"Installierte DC-Leistung: {installed_kwp:.2f} kWp")
    print(
        f"Modul: {config.module_model} (NMOT {config.module_nmot_c:g} °C, "
        f"Pmax {config.power_temperature_coefficient * 100:g} %/K)"
    )
    print(f"Geschätzte jährliche DC-Energie: {annual_dc_energy_kwh:.0f} kWh")
    print(f"Spezifischer Ertrag: {annual_dc_energy_kwh / installed_kwp:.0f} kWh/kWp")
    print(f"Stundenwerte gespeichert in: {OUTPUT_PATH}")
    print(
        f"Grundlage: PVGIS-Referenzjahr und {config.system_loss_fraction:.0%} Systemverluste; "
        "Rückseitengewinn und Wechselrichter-Begrenzung sind nicht enthalten."
    )


if __name__ == "__main__":
    try:
        main()
    except ConfigError as error:
        sys.exit(str(error))
