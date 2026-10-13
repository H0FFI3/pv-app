import argparse
import asyncio
import os
import sys
import time
from datetime import date, timedelta
from getpass import getpass
from pathlib import Path

import pandas as pd
import pvlib
import requests
from alphaess.alphaess import alphaess
from dotenv import load_dotenv

from analyze_pv import ARRAYS, LATITUDE, LONGITUDE, TIMEZONE, estimate_array_power


API_REQUEST_INTERVAL_SECONDS = 10
NOMINAL_INVERTER_EFFICIENCY = 0.96
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
OUTPUT_PATH = Path(__file__).with_name("pv_daily_comparison.csv")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Datum bitte als YYYY-MM-DD angeben.") from error


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vergleicht AlphaESS-Tagesertrag mit wetterbasiertem PV-Potenzial."
    )
    parser.add_argument("start", type=parse_date, help="Erster Tag (YYYY-MM-DD)")
    parser.add_argument("end", type=parse_date, help="Letzter Tag (YYYY-MM-DD)")
    result = parser.parse_args()
    if result.start > result.end:
        parser.error("Das Startdatum muss vor oder gleich dem Enddatum liegen.")
    if result.end >= date.today():
        parser.error("Bitte nur abgeschlossene Tage bis einschliesslich gestern angeben.")
    return result


def load_weather(
    start: date,
    end: date,
) -> tuple[pd.DataFrame, float, dict[date, float]]:
    response = requests.get(
        WEATHER_URL,
        params={
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "start_date": (start - timedelta(days=1)).isoformat(),
            "end_date": (end + timedelta(days=1)).isoformat(),
            "hourly": (
                "temperature_2m,wind_speed_10m,shortwave_radiation,"
                "direct_normal_irradiance,diffuse_radiation,sunshine_duration"
            ),
            "timeformat": "unixtime",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    hourly = payload["hourly"]
    interval_midpoints = (
        pd.to_datetime(hourly["time"], unit="s", utc=True)
        - pd.Timedelta(minutes=30)
    ).tz_convert(TIMEZONE)
    weather = pd.DataFrame(
        {
            "temp_air": hourly["temperature_2m"],
            "wind_speed": hourly["wind_speed_10m"],
            "ghi": hourly["shortwave_radiation"],
            "dni": hourly["direct_normal_irradiance"],
            "dhi": hourly["diffuse_radiation"],
        },
        index=interval_midpoints,
    ).dropna()
    sunshine = pd.Series(hourly["sunshine_duration"], index=interval_midpoints)
    sunshine_by_day = sunshine.groupby(sunshine.index.date).sum(min_count=1) / 3600
    sunshine_hours = {
        day: float(hours)
        for day, hours in sunshine_by_day.items()
        if start <= day <= end and pd.notna(hours)
    }
    return weather, float(payload.get("elevation") or 0), sunshine_hours


def estimate_daily_potential(
    weather: pd.DataFrame,
    elevation: float,
    inverter_power_kw: float,
) -> pd.Series:
    solar_position = pvlib.solarposition.get_solarposition(
        weather.index,
        LATITUDE,
        LONGITUDE,
        altitude=elevation,
        temperature=weather["temp_air"],
    )
    dni_extra = pvlib.irradiance.get_extra_radiation(weather.index)
    array_power = [
        estimate_array_power(
            weather,
            solar_position,
            dni_extra,
            modules=array["modules"],
            azimuth=array["azimuth"],
        )
        for array in ARRAYS.values()
    ]
    total_dc_power = sum(array_power).clip(lower=0)
    ac_power = pvlib.inverter.pvwatts(
        total_dc_power,
        pdc0=inverter_power_kw * 1000 / NOMINAL_INVERTER_EFFICIENCY,
        eta_inv_nom=NOMINAL_INVERTER_EFFICIENCY,
    ).clip(lower=0)
    return ac_power.resample("D").sum() / 1000


def choose_system(systems: list[dict]) -> dict:
    if not systems:
        raise RuntimeError("AlphaESS hat keine Anlagen für diese AppID zurückgegeben.")

    configured_serial = os.getenv("ALPHAESS_SYSTEM_SN")
    if configured_serial:
        for system in systems:
            if system.get("sysSn") == configured_serial:
                return system
        raise RuntimeError("ALPHAESS_SYSTEM_SN passt zu keiner Anlage dieser AppID.")
    if len(systems) == 1:
        return systems[0]
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Mehrere Anlagen gefunden. Hinterlege ALPHAESS_SYSTEM_SN als Action Secret."
        )

    for index, system in enumerate(systems, start=1):
        print(
            f"{index}: {system.get('minv', 'AlphaESS-Anlage')} "
            f"({system.get('popv', '?')} kWp PV, "
            f"{system.get('poinv', '?')} kW Wechselrichter)"
        )
    while True:
        try:
            selection = int(input("Anlage auswählen: "))
            if 1 <= selection <= len(systems):
                return systems[selection - 1]
        except ValueError:
            pass
        print(f"Bitte eine Zahl von 1 bis {len(systems)} eingeben.")


async def load_actual_energy(
    start: date,
    end: date,
) -> tuple[dict[date, float], float]:
    app_id = os.getenv("ALPHAESS_APP_ID")
    app_secret = os.getenv("ALPHAESS_APP_SECRET")
    if sys.stdin.isatty():
        app_id = app_id or input("AlphaESS AppID: ").strip()
        app_secret = app_secret or getpass("AlphaESS AppSecret (hidden): ").strip()
    if not app_id or not app_secret:
        raise RuntimeError("ALPHAESS_APP_ID und ALPHAESS_APP_SECRET müssen gesetzt sein.")

    client = alphaess(app_id, app_secret, raise_on_error=True)
    try:
        systems = await client.getESSList()
        last_request = time.monotonic()
        system = choose_system(systems or [])
        serial = system.get("sysSn")
        inverter_power_kw = float(system.get("poinv") or 0)
        if not serial:
            raise RuntimeError("Die ausgewählte Anlage enthält keine sysSn.")
        if inverter_power_kw <= 0:
            raise RuntimeError("AlphaESS hat keine gültige Wechselrichterleistung (poinv) geliefert.")

        actual_energy = {}
        total_days = (end - start).days + 1
        for day_offset in range(total_days):
            day = start + timedelta(days=day_offset)
            wait_seconds = API_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            result = await client.getOneDateEnergyBySn(serial, day.isoformat())
            last_request = time.monotonic()
            if not result or result.get("epv") is None:
                raise RuntimeError(f"AlphaESS lieferte für {day.isoformat()} kein Feld epv.")
            actual_energy[day] = float(result["epv"])
            print(f"Istwert abgerufen: {day.isoformat()} ({day_offset + 1}/{total_days})")

        return actual_energy, inverter_power_kw
    finally:
        await client.close()


async def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    options = arguments()
    actual_energy, inverter_power_kw = await load_actual_energy(options.start, options.end)
    weather, elevation, sunshine_hours = load_weather(options.start, options.end)
    potential = estimate_daily_potential(weather, elevation, inverter_power_kw)

    rows = []
    for day, actual_kwh in actual_energy.items():
        day_key = pd.Timestamp(day).tz_localize(TIMEZONE)
        if day_key not in potential.index:
            raise RuntimeError(f"Für {day.isoformat()} fehlen Wetterdaten.")
        if day not in sunshine_hours:
            raise RuntimeError(f"Für {day.isoformat()} fehlen Sonnenscheindaten.")
        potential_kwh = float(potential.loc[day_key])
        rows.append(
            {
                "date": day.isoformat(),
                "sunshine_hours": sunshine_hours[day],
                "actual_kwh": actual_kwh,
                "potential_kwh": potential_kwh,
                "difference_kwh": potential_kwh - actual_kwh,
                "actual_vs_potential_pct": (
                    actual_kwh / potential_kwh * 100 if potential_kwh > 0 else None
                ),
            }
        )

    comparison = pd.DataFrame(rows)
    comparison.to_csv(OUTPUT_PATH, index=False, float_format="%.2f")
    print(comparison.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print(f"Vergleich gespeichert: {OUTPUT_PATH}")
    print(
        "Potenzial = wetterbasiertes PVWatts-Modell mit 14% Systemverlusten; "
        "Dachverschattung und individuelle Moduldaten sind nicht enthalten."
    )


if __name__ == "__main__":
    asyncio.run(main())