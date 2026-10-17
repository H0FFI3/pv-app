import argparse
import asyncio
import os
import sys
import time
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pvlib
import requests
from alphaess.alphaess import alphaess

from analyze_pv import estimate_array_power
from pv_config import (
    API_REQUEST_INTERVAL_SECONDS,
    ARRAYS,
    LATITUDE,
    LONGITUDE,
    NOMINAL_INVERTER_EFFICIENCY,
    TIMEZONE,
    validate_configuration,
)


WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OUTPUT_PATH = Path(__file__).with_name("pv_daily_comparison.json")
WEATHER_HOURLY_VARIABLES = (
    "temperature_2m,wind_speed_10m,shortwave_radiation,"
    "direct_normal_irradiance,diffuse_radiation"
)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Datum bitte als YYYY-MM-DD angeben.") from error


def arguments() -> argparse.Namespace:
    validate_configuration()
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
) -> tuple[pd.DataFrame, float, dict[date, float], dict[date, str]]:
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    recent_start = max(start, today - timedelta(days=2))
    archive_end = min(end, recent_start - timedelta(days=1))
    weather_frames = []
    sunshine_hours: dict[date, float] = {}
    weather_sources: dict[date, str] = {}
    elevations = []

    if start <= archive_end:
        archive_params = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "start_date": (start - timedelta(days=1)).isoformat(),
            "end_date": (archive_end + timedelta(days=1)).isoformat(),
            "hourly": WEATHER_HOURLY_VARIABLES + ",sunshine_duration",
            "timeformat": "unixtime",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        try:
            response = requests.get(WEATHER_URL, params=archive_params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            frame, daily_sunshine, sources = _parse_weather_payload(
                payload, start, archive_end, "archive"
            )
            weather_frames.append(frame)
            sunshine_hours.update(daily_sunshine)
            weather_sources.update(sources)
            elevations.append(float(payload.get("elevation") or 0))
        except requests.RequestException as error:
            print(f"Warnung: Historische Wetterdaten nicht verfügbar ({error}).", flush=True)

    if recent_start <= end:
        forecast_params = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "past_days": 2,
            "forecast_days": 1,
            "hourly": WEATHER_HOURLY_VARIABLES,
            "daily": "sunshine_duration",
            "timeformat": "unixtime",
            "wind_speed_unit": "ms",
            "timezone": TIMEZONE,
        }
        try:
            response = requests.get(FORECAST_URL, params=forecast_params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            frame, daily_sunshine, sources = _parse_weather_payload(
                payload, recent_start, end, "forecast"
            )
            weather_frames.append(frame)
            sunshine_hours.update(daily_sunshine)
            weather_sources.update(sources)
            elevations.append(float(payload.get("elevation") or 0))
        except requests.RequestException as error:
            print(f"Warnung: Aktuelle Wetterprognose nicht verfügbar ({error}).", flush=True)

    if not weather_frames:
        raise RuntimeError("Open-Meteo lieferte für den angefragten Zeitraum keine Wetterdaten.")
    weather = pd.concat(weather_frames).sort_index()
    weather = weather[~weather.index.duplicated(keep="last")]
    if not weather_sources:
        raise RuntimeError("Open-Meteo lieferte keine verwertbaren Wetterwerte.")
    return weather, elevations[-1], sunshine_hours, weather_sources


def _parse_weather_payload(
    payload: dict,
    start: date,
    end: date,
    source: str,
) -> tuple[pd.DataFrame, dict[date, float], dict[date, str]]:
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
    if "sunshine_duration" in hourly:
        sunshine = pd.Series(hourly["sunshine_duration"], index=interval_midpoints)
        sunshine_by_day = sunshine.groupby(sunshine.index.date).sum(min_count=1) / 3600
        sunshine_hours = {
            day: float(hours)
            for day, hours in sunshine_by_day.items()
            if start <= day <= end and pd.notna(hours)
        }
    else:
        sunshine_by_day = (weather["dni"] > 120).groupby(weather.index.date).sum()
        sunshine_hours = {
            day: float(hours)
            for day, hours in sunshine_by_day.items()
            if start <= day <= end and pd.notna(hours)
        }
    for day_text, seconds in zip(
        payload.get("daily", {}).get("time", []),
        payload.get("daily", {}).get("sunshine_duration", []),
    ):
        day = date.fromisoformat(str(day_text)[:10])
        if start <= day <= end and seconds is not None:
            sunshine_hours[day] = float(seconds) / 3600

    weather_sources = {
        day: source
        for day in set(weather.index.date)
        if start <= day <= end
    }
    return weather, sunshine_hours, weather_sources


def aggregate_hourly_pv_power(
    samples: list[dict],
    day: date,
    daily_energy_kwh: float,
) -> dict[str, float]:
    frame = pd.DataFrame(
        {
            "upload_time": [sample.get("uploadTime") for sample in samples],
            "power_w": [sample.get("ppv") for sample in samples],
        }
    )
    if frame.empty:
        return {}

    frame["upload_time"] = pd.to_datetime(frame["upload_time"], errors="coerce")
    frame["power_w"] = pd.to_numeric(frame["power_w"], errors="coerce")
    frame = frame.dropna(subset=["upload_time", "power_w"])
    if frame.empty:
        return {}

    timestamps = pd.DatetimeIndex(frame["upload_time"])
    if timestamps.tz is None:
        timestamps = timestamps.tz_localize(TIMEZONE, ambiguous="NaT", nonexistent="shift_forward")
    else:
        timestamps = timestamps.tz_convert(TIMEZONE)
    frame.index = timestamps
    frame = frame[~frame.index.isna()].sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[frame.index.date == day]
    if len(frame) < 2:
        return {}

    local_times = pd.Series(frame.index.tz_localize(None), index=frame.index)
    intervals = (local_times.shift(-1) - local_times).dt.total_seconds()
    normal_intervals = intervals[(intervals > 0) & (intervals <= 600)]
    typical_interval = float(normal_intervals.median()) if not normal_intervals.empty else 300.0
    intervals = intervals.fillna(typical_interval).clip(lower=0, upper=600)
    frame["sample_energy_kwh"] = frame["power_w"].clip(lower=0) * intervals / 3_600_000

    hour_labels = frame.index.tz_localize(None).floor("h")
    hourly_energy = frame.groupby(hour_labels)["sample_energy_kwh"].sum()
    measured_total = float(hourly_energy.sum())
    if measured_total > 0:
        hourly_energy *= max(0.0, daily_energy_kwh) / measured_total
    return {
        timestamp.strftime("%H:00"): round(float(energy), 3)
        for timestamp, energy in hourly_energy.items()
    }


def estimate_hourly_potential(
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
    return ac_power.resample("h").sum(min_count=1) / 1000


def estimate_daily_potential(
    weather: pd.DataFrame,
    elevation: float,
    inverter_power_kw: float,
) -> pd.Series:
    hourly_potential = estimate_hourly_potential(weather, elevation, inverter_power_kw)
    return hourly_potential.resample("D").sum(min_count=1)


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
    include_hourly: bool = False,
) -> tuple[dict[date, float], float, dict[date, dict[str, float]]]:
    app_id = os.getenv("ALPHAESS_APP_ID")
    app_secret = os.getenv("ALPHAESS_APP_SECRET")
    if sys.stdin.isatty():
        app_id = app_id or input("AlphaESS AppID: ").strip()
        app_secret = app_secret or getpass("AlphaESS AppSecret (hidden): ").strip()
    if not app_id or not app_secret:
        raise RuntimeError("ALPHAESS_APP_ID und ALPHAESS_APP_SECRET müssen gesetzt sein.")

    print("AlphaESS: Zugangsdaten vorhanden; melde mich an und rufe die Anlagenliste ab ...", flush=True)
    client = alphaess(app_id, app_secret, raise_on_error=True)
    try:
        systems = await client.getESSList()
        print(f"AlphaESS: Anlagenliste erhalten ({len(systems or [])} Anlage(n)).", flush=True)
        last_request = time.monotonic()
        system = choose_system(systems or [])
        serial = system.get("sysSn")
        inverter_power_kw = float(system.get("poinv") or 0)
        if not serial:
            raise RuntimeError("Die ausgewählte Anlage enthält keine sysSn.")
        if inverter_power_kw <= 0:
            raise RuntimeError("AlphaESS hat keine gültige Wechselrichterleistung (poinv) geliefert.")

        actual_energy = {}
        hourly_actual = {}
        total_days = (end - start).days + 1
        for day_offset in range(total_days):
            day = start + timedelta(days=day_offset)
            wait_seconds = API_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request)
            if wait_seconds > 0:
                print(f"AlphaESS: API-Schutzpause {wait_seconds:.0f} s ...", flush=True)
                await asyncio.sleep(wait_seconds)
            print(f"AlphaESS: rufe Tagesertrag für {day.isoformat()} ab ...", flush=True)
            result = await client.getOneDateEnergyBySn(serial, day.isoformat())
            last_request = time.monotonic()
            if not result or result.get("epv") is None:
                raise RuntimeError(f"AlphaESS lieferte für {day.isoformat()} kein Feld epv.")
            actual_energy[day] = float(result["epv"])
            if include_hourly:
                wait_seconds = API_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request)
                if wait_seconds > 0:
                    print(f"AlphaESS: API-Schutzpause {wait_seconds:.0f} s vor Stundenwerten ...", flush=True)
                    await asyncio.sleep(wait_seconds)
                print(f"AlphaESS: rufe 5-Minuten-Leistungswerte für {day.isoformat()} ab ...", flush=True)
                try:
                    samples = await client.getOneDayPowerBySn(serial, day.isoformat()) or []
                    hourly_actual[day] = aggregate_hourly_pv_power(
                        samples,
                        day,
                        actual_energy[day],
                    )
                    print(f"AlphaESS: {len(samples)} Leistungsproben erhalten.", flush=True)
                except Exception as error:
                    print(f"AlphaESS: Stundenwerte nicht verfügbar ({error}).", flush=True)
                    hourly_actual[day] = {}
                last_request = time.monotonic()
            print(f"Istwert abgerufen: {day.isoformat()} ({day_offset + 1}/{total_days})", flush=True)

        return actual_energy, inverter_power_kw, hourly_actual
    finally:
        await client.close()


async def main() -> None:
    options = arguments()
    validate_configuration()
    actual_energy, inverter_power_kw, _ = await load_actual_energy(options.start, options.end)
    weather, elevation, sunshine_hours, _ = load_weather(options.start, options.end)
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
    comparison.to_json(
        OUTPUT_PATH,
        orient="records",
        date_format="iso",
        double_precision=2,
        force_ascii=False,
    )
    print(comparison.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print(f"Vergleich gespeichert: {OUTPUT_PATH}")
    print(
        "Potenzial = wetterbasiertes PVWatts-Modell mit 14% Systemverlusten; "
        "Dachverschattung und individuelle Moduldaten sind nicht enthalten."
    )


if __name__ == "__main__":
    asyncio.run(main())