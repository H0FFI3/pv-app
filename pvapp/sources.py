"""Datenquellen: AlphaESS (Ist-Erträge), Open-Meteo (Wetter) und PVGIS (Horizont).

Keine interaktiven Eingaben in diesem Modul: Die Anlagenauswahl bei mehreren Anlagen wird
über den ``chooser``-Parameter von außen (CLI) bereitgestellt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import Config

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = (
    "temperature_2m,wind_speed_10m,shortwave_radiation,"
    "direct_normal_irradiance,diffuse_radiation,sunshine_duration"
)
# Ein Tag gilt nur als vollständig, wenn (fast) alle Stunden vorliegen (23/25 Stunden bei Zeitumstellung).
MIN_WEATHER_HOURS = 22
FORECAST_MAX_PAST_DAYS = 92


# =============================================================================
# Wetter (Open-Meteo)
# =============================================================================


class SourceError(RuntimeError):
    """Eine Datenquelle hat nicht oder fehlerhaft geantwortet."""


@dataclass
class WeatherData:
    frame: pd.DataFrame  # Index: Intervall-Mittelpunkte (lokale Zeitzone); temp_air, wind_speed, ghi, dni, dhi
    elevation: float
    sunshine_hours: dict[date, float]
    source_by_day: dict[date, str]  # nur vollständige Tage: "archive" oder "forecast"


def fetch_json(
    url: str,
    params: dict[str, Any],
    *,
    http_get: Callable[..., Any] | None = None,
    attempts: int = 3,
    backoff_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """GET mit Wiederholung bei Netzwerkfehlern, 5xx und 429. Andere 4xx-Fehler brechen sofort ab."""
    http_get = http_get or requests.get
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = http_get(url, params=params, timeout=30)
            if response.status_code >= 400:
                reason = ""
                try:
                    reason = str(response.json().get("reason", ""))
                except Exception:
                    pass
                message = f"HTTP {response.status_code} von {url}" + (f": {reason}" if reason else "")
                if response.status_code < 500 and response.status_code != 429:
                    raise SourceError(message)
                last_error = SourceError(message)
            else:
                return response.json()
        except SourceError:
            raise
        except requests.RequestException as error:
            last_error = error
        if attempt + 1 < attempts:
            sleep(backoff_seconds * (attempt + 1))
    raise SourceError(f"Abruf fehlgeschlagen nach {attempts} Versuchen: {last_error}")


def _weather_from_payload(payload: dict, timezone: str) -> tuple[pd.DataFrame, pd.Series, float]:
    """Wandelt eine Open-Meteo-Antwort um. Zeitstempel sind das Intervallende (vorangehende Stunde),
    daher wird der Intervall-Mittelpunkt (Zeit - 30 min) in lokaler Zeit verwendet."""
    hourly = payload["hourly"]
    midpoints = (pd.to_datetime(hourly["time"], unit="s", utc=True) - pd.Timedelta(minutes=30)).tz_convert(timezone)
    frame = pd.DataFrame(
        {
            "temp_air": hourly["temperature_2m"],
            "wind_speed": hourly["wind_speed_10m"],
            "ghi": hourly["shortwave_radiation"],
            "dni": hourly["direct_normal_irradiance"],
            "dhi": hourly["diffuse_radiation"],
        },
        index=midpoints,
    ).dropna()
    sunshine = pd.Series(hourly["sunshine_duration"], index=midpoints, dtype=float)
    elevation = float(payload.get("elevation") or 0)
    return frame, sunshine, elevation


def complete_days(index: pd.DatetimeIndex, minimum_hours: int = MIN_WEATHER_HOURS) -> set[date]:
    """Lokale Kalendertage, für die mindestens ``minimum_hours`` Stundenwerte vorliegen."""
    if len(index) == 0:
        return set()
    counts = pd.Series(1, index=index).groupby(index.date).sum()
    return {day for day, count in counts.items() if count >= minimum_hours}


def _request_params(config: Config) -> dict[str, Any]:
    return {
        "latitude": config.latitude,
        "longitude": config.longitude,
        "hourly": HOURLY_VARIABLES,
        "timeformat": "unixtime",
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }


def local_today(timezone: str) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


def load_weather(
    config: Config,
    start: date,
    end: date,
    *,
    source: str | None = None,
    http_get: Callable[..., Any] | None = None,
    today: date | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> WeatherData:
    """Lädt stündliche Wetterdaten für ``start``..``end`` (lokale Tage).

    Quelle ``auto``: Archiv (ERA5-Reanalyse). Das Archiv liegt einige Tage zurück; fehlende oder
    unvollständige Tage werden aus der Forecast-API (``past_days``) ergänzt. Pro Tag wird vermerkt,
    woher die Daten stammen. ``archive``/``forecast`` erzwingen eine Quelle.
    """
    mode = source or config.weather_source
    today = today or local_today(config.timezone)
    wanted = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    first, last = start - timedelta(days=1), end + timedelta(days=1)  # Puffer für UTC-Tagesgrenzen

    archive_frame = pd.DataFrame()
    archive_sunshine = pd.Series(dtype=float)
    elevation = None

    if mode in ("auto", "archive"):
        params = {**_request_params(config), "start_date": first.isoformat(), "end_date": min(last, today).isoformat()}
        try:
            payload = fetch_json(ARCHIVE_URL, params, http_get=http_get, sleep=sleep)
            archive_frame, archive_sunshine, elevation = _weather_from_payload(payload, config.timezone)
        except (SourceError, KeyError, ValueError) as error:
            if mode == "archive":
                raise SourceError(f"Open-Meteo-Archiv nicht verfügbar: {error}") from error
            log.warning("Open-Meteo-Archiv nicht verfügbar (%s); versuche die Forecast-API.", error)

    archive_done = complete_days(archive_frame.index) if mode != "forecast" else set()
    forecast_frame = pd.DataFrame()
    forecast_sunshine = pd.Series(dtype=float)
    if mode == "forecast" or (mode == "auto" and any(day not in archive_done for day in wanted)):
        past_days = min(max((today - first).days, 1), FORECAST_MAX_PAST_DAYS)
        params = {**_request_params(config), "past_days": past_days, "forecast_days": 2}
        try:
            payload = fetch_json(FORECAST_URL, params, http_get=http_get, sleep=sleep)
            forecast_frame, forecast_sunshine, forecast_elevation = _weather_from_payload(payload, config.timezone)
            elevation = forecast_elevation if elevation is None else elevation
        except (SourceError, KeyError, ValueError) as error:
            if mode == "forecast" or archive_frame.empty:
                raise SourceError(f"Open-Meteo-Forecast nicht verfügbar: {error}") from error
            log.warning("Open-Meteo-Forecast nicht verfügbar (%s); es werden nur Archivdaten verwendet.", error)

    frame = archive_frame.combine_first(forecast_frame) if not forecast_frame.empty else archive_frame
    sunshine = archive_sunshine.combine_first(forecast_sunshine) if not forecast_sunshine.empty else archive_sunshine
    if frame.empty:
        raise SourceError("Open-Meteo lieferte keine Wetterdaten.")
    frame = frame.sort_index()

    weather_days = complete_days(frame.index)
    sunshine_valid = sunshine.dropna()
    sunshine_days = complete_days(sunshine_valid.index)
    sunshine_hours_by_day = sunshine_valid.groupby(sunshine_valid.index.date).sum() / 3600
    sunshine_hours = {
        day: float(hours)
        for day, hours in sunshine_hours_by_day.items()
        if day in wanted and day in sunshine_days
    }
    source_by_day = {
        day: ("archive" if day in archive_done else "forecast")
        for day in wanted
        if day in weather_days and day in sunshine_hours
    }
    return WeatherData(frame, float(elevation or 0), sunshine_hours, source_by_day)


# =============================================================================
# Horizont (PVGIS)
# =============================================================================


def load_horizon(
    config: Config,
    cache_path: Path,
    *,
    fetch: Callable[[float, float], pd.Series] | None = None,
) -> pd.Series | None:
    """Horizontprofil (Azimut -> Höhe) aus lokalem Cache oder PVGIS. Fehler führen zu ``None`` (ohne Horizont rechnen)."""
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if abs(cached["latitude"] - config.latitude) < 1e-4 and abs(cached["longitude"] - config.longitude) < 1e-4:
                return pd.Series(cached["elevation"], index=cached["azimuth"], dtype=float)
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("Horizont-Cache unlesbar; wird neu geladen.")

    try:
        if fetch is None:
            import pvlib

            def fetch(latitude: float, longitude: float) -> pd.Series:
                series, _ = pvlib.iotools.get_pvgis_horizon(latitude, longitude, timeout=30)
                return series

        horizon = fetch(config.latitude, config.longitude)
    except Exception as error:
        log.warning("Horizontprofil nicht verfügbar (%s); Berechnung ohne Horizont.", error)
        return None

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "latitude": config.latitude,
                    "longitude": config.longitude,
                    "azimuth": [float(a) for a in horizon.index],
                    "elevation": [float(v) for v in horizon.to_numpy()],
                }
            ),
            encoding="utf-8",
        )
    except OSError as error:
        log.warning("Horizont-Cache konnte nicht gespeichert werden: %s", error)
    return horizon


# =============================================================================
# AlphaESS
# =============================================================================


def aggregate_hourly_pv_power(
    samples: list[dict],
    day: date,
    daily_energy_kwh: float,
    timezone: str,
) -> dict[str, float]:
    """Verdichtet AlphaESS-Leistungsproben (W, typ. 5 min) zu Stunden-kWh und skaliert auf den Tageswert.

    Zeitstempel ohne Zeitzone gelten als lokale Uhrzeit und werden ohne Zeitzonenumrechnung verarbeitet,
    damit an Zeitumstellungstagen keine Proben verloren gehen. Die doppelte Stunde im Herbst
    erscheint als ein Eintrag "02:00".
    """
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
    if timestamps.tz is not None:
        timestamps = timestamps.tz_convert(timezone).tz_localize(None)
    frame.index = timestamps
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[frame.index.date == day]
    if len(frame) < 2:
        return {}

    times = pd.Series(frame.index, index=frame.index)
    intervals = (times.shift(-1) - times).dt.total_seconds()
    normal_intervals = intervals[(intervals > 0) & (intervals <= 600)]
    typical_interval = float(normal_intervals.median()) if not normal_intervals.empty else 300.0
    intervals = intervals.fillna(typical_interval).clip(lower=0, upper=600)
    frame["sample_energy_kwh"] = frame["power_w"].clip(lower=0) * intervals / 3_600_000

    hourly_energy = frame.groupby(frame.index.floor("h"))["sample_energy_kwh"].sum()
    measured_total = float(hourly_energy.sum())
    if measured_total > 0:
        hourly_energy *= max(0.0, daily_energy_kwh) / measured_total
    return {timestamp.strftime("%H:00"): round(float(energy), 3) for timestamp, energy in hourly_energy.items()}


class RateLimiter:
    """Mindestabstand zwischen AlphaESS-Aufrufen (API-Schutzpause)."""

    def __init__(
        self,
        interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.interval = interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._last = clock()

    def mark(self) -> None:
        self._last = self._clock()

    async def wait(self, reason: str) -> None:
        remaining = self.interval - (self._clock() - self._last)
        if remaining > 0:
            log.info("AlphaESS: API-Schutzpause %.0f s %s ...", remaining, reason)
            await self._sleep(remaining)


@dataclass
class ActualResult:
    inverter_power_kw: float
    energy: dict[date, float] = field(default_factory=dict)
    hourly: dict[date, dict[str, float]] = field(default_factory=dict)
    errors: dict[date, str] = field(default_factory=dict)


def choose_system(
    systems: list[dict],
    configured_serial: str = "",
    chooser: Callable[[list[dict]], dict] | None = None,
) -> dict:
    if not systems:
        raise SourceError("AlphaESS hat keine Anlagen für diese AppID zurückgegeben.")
    if configured_serial:
        for system in systems:
            if system.get("sysSn") == configured_serial:
                return system
        raise SourceError("ALPHAESS_SYSTEM_SN passt zu keiner Anlage dieser AppID.")
    if len(systems) == 1:
        return systems[0]
    if chooser is None:
        raise SourceError("Mehrere Anlagen gefunden. Bitte ALPHAESS_SYSTEM_SN in der .env eintragen.")
    return chooser(systems)


def _default_client_factory(app_id: str, app_secret: str):
    from alphaess.alphaess import alphaess  # lazy: Paket wird nur für echte Abrufe benötigt

    return alphaess(app_id, app_secret, raise_on_error=True)


async def fetch_actuals(
    config: Config,
    days: list[date],
    *,
    include_hourly: bool = False,
    chooser: Callable[[list[dict]], dict] | None = None,
    client_factory: Callable[[str, str], Any] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ActualResult:
    """Ruft Tageserträge (und optional Stundenprofile) für ``days`` ab.

    Fehler einzelner Tage landen in ``errors`` und brechen die übrigen Tage nicht ab.
    Fehler bei Anmeldung/Anlagenliste werden als ``SourceError`` ausgelöst.
    """
    if not config.alphaess_app_id or not config.alphaess_app_secret:
        raise SourceError("ALPHAESS_APP_ID und ALPHAESS_APP_SECRET müssen gesetzt sein.")

    client = (client_factory or _default_client_factory)(config.alphaess_app_id, config.alphaess_app_secret)
    try:
        log.info("AlphaESS: melde mich an und rufe die Anlagenliste ab ...")
        try:
            systems = await client.getESSList()
        except Exception as error:
            raise SourceError(f"AlphaESS-Anlagenliste nicht abrufbar: {error}") from error
        log.info("AlphaESS: Anlagenliste erhalten (%d Anlage(n)).", len(systems or []))
        limiter = RateLimiter(config.api_request_interval_seconds, clock=clock, sleep=sleep)

        system = choose_system(systems or [], config.alphaess_system_sn, chooser)
        serial = system.get("sysSn")
        inverter_power_kw = float(system.get("poinv") or 0)
        if not serial:
            raise SourceError("Die ausgewählte Anlage enthält keine sysSn.")
        if inverter_power_kw <= 0:
            raise SourceError("AlphaESS hat keine gültige Wechselrichterleistung (poinv) geliefert.")

        result = ActualResult(inverter_power_kw=inverter_power_kw)
        ordered = sorted(days)
        for position, day in enumerate(ordered, start=1):
            await limiter.wait(f"vor Tagesertrag {day.isoformat()}")
            log.info("AlphaESS: rufe Tagesertrag für %s ab ...", day.isoformat())
            try:
                reply = await client.getOneDateEnergyBySn(serial, day.isoformat())
                limiter.mark()
                if not reply or reply.get("epv") is None:
                    raise SourceError(f"AlphaESS lieferte für {day.isoformat()} kein Feld epv.")
                result.energy[day] = float(reply["epv"])
            except Exception as error:
                limiter.mark()
                result.errors[day] = str(error)
                log.warning("AlphaESS: %s übersprungen (%s).", day.isoformat(), error)
                continue

            if include_hourly:
                await limiter.wait("vor Stundenwerten")
                log.info("AlphaESS: rufe 5-Minuten-Leistungswerte für %s ab ...", day.isoformat())
                try:
                    samples = await client.getOneDayPowerBySn(serial, day.isoformat()) or []
                    result.hourly[day] = aggregate_hourly_pv_power(
                        samples, day, result.energy[day], config.timezone
                    )
                    log.info("AlphaESS: %d Leistungsproben erhalten.", len(samples))
                except Exception as error:
                    log.warning("AlphaESS: Stundenwerte für %s nicht verfügbar (%s).", day.isoformat(), error)
                    result.hourly[day] = {}
                limiter.mark()
            log.info("Istwert abgerufen: %s (%d/%d)", day.isoformat(), position, len(ordered))
        return result
    finally:
        await client.close()
