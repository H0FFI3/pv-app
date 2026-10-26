"""Konfiguration aus .env bzw. Umgebungsvariablen.

Es wird nichts mehr beim Import geparst: ``load_config()`` liest und validiert einmal
und liefert ein unveränderliches ``Config``-Objekt. Fehler nennen die Variablennamen.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Mapping, MutableMapping
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .curtailment import CurtailmentSettings
from .paths import is_frozen, project_root, settings_path

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Konfiguration fehlt oder ist ungültig (Meldung ist für Endnutzer gedacht).

    ``fields``: Namen der betroffenen Einstellungen (z. B. für die Hervorhebung in der Einrichtungsseite)."""

    def __init__(self, message: str, fields=()):
        super().__init__(message)
        self.fields = frozenset(fields)


def _field_names(missing, invalid) -> set[str]:
    names = set(missing)
    for item in invalid:
        match = re.match(r"[A-Z][A-Z0-9_]*", item)
        if match:
            names.add(match.group(0))
    return names


# Physikalisch nötige Angaben. MODULE_MODEL, MODULE_BIFACIAL und alle Modell-Optionen
# aus Phase 4 sind optional.
REQUIRED_SETTINGS = (
    "LATITUDE",
    "LONGITUDE",
    "TIMEZONE",
    "SURFACE_TILT_DEG",
    "MODULE_POWER_WP",
    "MODULE_EFFICIENCY",
    "MODULE_NMOT_C",
    "SYSTEM_LOSS_FRACTION",
    "POWER_TEMPERATURE_COEFFICIENT",
    "MODULES_EAST",
    "AZIMUTH_EAST_DEG",
    "MODULES_WEST",
    "AZIMUTH_WEST_DEG",
    "NOMINAL_INVERTER_EFFICIENCY",
    "DATA_START_DATE",
)

WEATHER_SOURCES = ("auto", "archive", "forecast")


@dataclass(frozen=True)
class ArrayConfig:
    modules: int
    azimuth: float


@dataclass(frozen=True)
class Config:
    latitude: float
    longitude: float
    timezone: str
    surface_tilt: float
    module_model: str
    module_power_wp: float
    module_efficiency: float
    module_nmot_c: float
    module_bifacial: bool
    system_loss_fraction: float
    power_temperature_coefficient: float
    arrays: Mapping[str, ArrayConfig]
    nominal_inverter_efficiency: float
    data_start_date: date
    api_request_interval_seconds: int = 10
    backfill_max_days: int = 14
    update_time: time = time(6, 0)  # tägliche Aktualisierung im Serve-Modus (lokale Uhrzeit der Anlage)
    weather_source: str = "auto"
    # Modelloptionen (Standard = unverändertes Verhalten der Vorversion)
    iam_enabled: bool = False
    iam_b: float = 0.05
    albedo: float = 0.25
    horizon_enabled: bool = False
    bifaciality_gain: float = 0.0
    # Ausgabe / Zugang
    report_path: Path | None = None
    alphaess_app_id: str = field(default="", repr=False)
    alphaess_app_secret: str = field(default="", repr=False)
    alphaess_system_sn: str = field(default="", repr=False)
    # Abregelungserkennung und Feldnamen der AlphaESS-Leistungsdaten
    curtailment: CurtailmentSettings = field(default_factory=CurtailmentSettings)
    alphaess_soc_field: str = "cbat"
    alphaess_feed_in_field: str = "feedIn"

    @property
    def installed_kwp(self) -> float:
        return sum(a.modules for a in self.arrays.values()) * self.module_power_wp / 1000

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self.alphaess_app_id, self.alphaess_app_secret, self.alphaess_system_sn) if s)


# --------------------------------------------------------------------------- .env


def env_file_candidates() -> list[Path]:
    """Höchste Priorität zuerst; ``settings.env`` (Einrichtungsseite) steht ganz hinten. Bei der EXE: neben der EXE, dann Ordner darüber
    (onefile: Projektordner), dann zwei Ebenen darüber (onedir: dist/PV-Tagesbericht/ -> Projektordner)."""
    if is_frozen():
        executable_directory = Path(sys.executable).resolve().parent
        return [
            executable_directory / ".env",
            executable_directory.parent / ".env",
            executable_directory.parent.parent / ".env",
            settings_path(),
        ]
    return [project_root() / ".env", settings_path()]


def merged_file_values(candidates: list[Path] | None = None) -> dict[str, str]:
    """Werte aller .env-Dateien (höchste Priorität gewinnt). Leere Werte werden ignoriert, damit sie
    keinen gefüllten Wert einer anderen Datei verdecken."""
    merged: dict[str, str] = {}
    for path in reversed(candidates if candidates is not None else env_file_candidates()):
        for key, value in dotenv_values(path).items():
            if value is not None and value.strip() != "":
                merged[key] = value
    return merged


def effective_env(base: Mapping[str, str] | None = None, candidates: list[Path] | None = None) -> dict[str, str]:
    """Wirksame Einstellungen als neues Dict, ohne ``os.environ`` zu verändern: .env-Dateien, überstimmt
    von ``base`` (Standard: echte Umgebungsvariablen). Für langlebige Prozesse (Server), die Änderungen
    an settings.env ohne Neustart übernehmen müssen."""
    base = os.environ if base is None else base
    return {**merged_file_values(candidates), **dict(base)}


def load_environment(
    candidates: list[Path] | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Liest .env-Dateien in ``environ`` (Standard ``os.environ``). Vorhandene Umgebungsvariablen haben Vorrang."""
    environ = os.environ if environ is None else environ
    for key, value in merged_file_values(candidates).items():
        environ.setdefault(key, value)


# ------------------------------------------------------------------------ Parsing


class _Reader:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = env
        self.missing: list[str] = []
        self.invalid: list[str] = []
        self.warnings: list[str] = []

    def raw(self, name: str) -> str:
        return (self.env.get(name) or "").strip()

    def _number(self, name, cast, kind, default, required, minimum, maximum, exclusive_min=False):
        text = self.raw(name)
        if not text:
            if required:
                self.missing.append(name)
            return default
        try:
            value = cast(text.replace(",", ".") if cast is float else text)
        except ValueError:
            self.invalid.append(f"{name}='{text}' ({kind} erwartet)")
            return default
        too_small = minimum is not None and (value <= minimum if exclusive_min else value < minimum)
        if too_small or (maximum is not None and value > maximum):
            lo = "" if minimum is None else f"{'>' if exclusive_min else '≥'} {minimum:g}"
            hi = "" if maximum is None else f"≤ {maximum:g}"
            self.invalid.append(f"{name}={text} (erlaubt: {' und '.join(x for x in (lo, hi) if x)})")
            return default
        return value

    def float(self, name, *, default=0.0, required=False, minimum=None, maximum=None, exclusive_min=False):
        return self._number(name, float, "Zahl", default, required, minimum, maximum, exclusive_min)

    def int(self, name, *, default=0, required=False, minimum=None, maximum=None):
        return self._number(name, int, "ganze Zahl", default, required, minimum, maximum)

    def bool(self, name, *, default=False):
        text = self.raw(name).lower()
        if not text:
            return default
        if text in {"1", "true", "yes", "ja", "on", "wahr"}:
            return True
        if text in {"0", "false", "no", "nein", "off", "falsch"}:
            return False
        self.invalid.append(f"{name}='{text}' (true/false erwartet)")
        return default

    def text(self, name, default="") -> str:
        return self.raw(name) or default


def _curtailment_from_reader(r: "_Reader") -> CurtailmentSettings:
    limit = r.float("FEED_IN_LIMIT_W", default=0.0, minimum=0)
    return CurtailmentSettings(
        enabled=r.bool("CURTAILMENT_DETECTION", default=True),
        soc_full_pct=r.float("BATTERY_FULL_SOC_PCT", default=97.0, minimum=50, maximum=100),
        min_deficit=r.float("CURTAILMENT_MIN_DEFICIT", default=0.20, minimum=0, maximum=1),
        min_potential_kwh=r.float("CURTAILMENT_MIN_POTENTIAL_KWH", default=0.3, minimum=0),
        feed_in_limit_w=limit or None,
        feed_in_tolerance=r.float("FEED_IN_LIMIT_TOLERANCE", default=0.95, minimum=0.5, maximum=1),
    )


def curtailment_settings_from_env(env: Mapping[str, str]) -> CurtailmentSettings:
    """Nur die Abregelungs-Einstellungen (für die Anzeige gespeicherter Daten ohne vollständige Konfiguration)."""
    r = _Reader(env)
    settings = _curtailment_from_reader(r)
    if r.invalid:
        raise ConfigError("Ungültige Werte in .env: " + "; ".join(r.invalid), _field_names([], r.invalid))
    return settings


def config_from_env(env: Mapping[str, str]) -> Config:
    """Erzeugt und validiert eine Config aus einem Mapping (z. B. ``os.environ``)."""
    r = _Reader(env)

    latitude = r.float("LATITUDE", required=True, minimum=-90, maximum=90)
    longitude = r.float("LONGITUDE", required=True, minimum=-180, maximum=180)
    timezone = r.text("TIMEZONE")
    if not timezone:
        r.missing.append("TIMEZONE")
    else:
        try:
            ZoneInfo(timezone)
        except Exception:  # ZoneInfoNotFoundError, ValueError, OSError
            r.invalid.append(f"TIMEZONE='{timezone}' (unbekannte Zeitzone, z. B. Europe/Berlin)")

    surface_tilt = r.float("SURFACE_TILT_DEG", required=True, minimum=0, maximum=90)
    module_power = r.float("MODULE_POWER_WP", required=True, minimum=0, exclusive_min=True)
    efficiency = r.float("MODULE_EFFICIENCY", required=True, minimum=0, maximum=1, exclusive_min=True)
    nmot = r.float("MODULE_NMOT_C", required=True, minimum=0, maximum=100)
    loss = r.float("SYSTEM_LOSS_FRACTION", required=True, minimum=0, maximum=0.99)
    gamma = r.float("POWER_TEMPERATURE_COEFFICIENT", required=True, minimum=-0.1, maximum=0.1)
    if gamma and abs(gamma) > 0.02:
        r.warnings.append(
            f"POWER_TEMPERATURE_COEFFICIENT={gamma:g} wirkt ungewöhnlich; erwartet wird ein Anteil pro Kelvin (z. B. -0.0030 für -0,30 %/K)."
        )
    inverter_eff = r.float("NOMINAL_INVERTER_EFFICIENCY", required=True, minimum=0, maximum=1, exclusive_min=True)

    modules_east = r.int("MODULES_EAST", required=True, minimum=0)
    azimuth_east = r.float("AZIMUTH_EAST_DEG", required=True, minimum=0, maximum=360)
    modules_west = r.int("MODULES_WEST", required=True, minimum=0)
    azimuth_west = r.float("AZIMUTH_WEST_DEG", required=True, minimum=0, maximum=360)
    if not r.missing and modules_east + modules_west <= 0:
        r.invalid.append("MODULES_EAST + MODULES_WEST muss größer als 0 sein")

    start_text = r.raw("DATA_START_DATE")
    data_start = date(1970, 1, 1)
    if not start_text:
        r.missing.append("DATA_START_DATE")
    else:
        try:
            data_start = date.fromisoformat(start_text)
        except ValueError:
            r.invalid.append(f"DATA_START_DATE='{start_text}' (Format JJJJ-MM-TT erwartet)")

    interval = r.int("API_REQUEST_INTERVAL_SECONDS", default=10, minimum=0)
    backfill = r.int("BACKFILL_MAX_DAYS", default=14, minimum=1, maximum=92)
    weather_source = r.text("WEATHER_SOURCE", "auto").lower()
    if weather_source not in WEATHER_SOURCES:
        r.invalid.append(f"WEATHER_SOURCE='{weather_source}' (erlaubt: {', '.join(WEATHER_SOURCES)})")
        weather_source = "auto"

    update_time = time(6, 0)
    update_text = r.raw("UPDATE_TIME")
    if update_text:
        try:
            hours, minutes = update_text.split(":")
            update_time = time(int(hours), int(minutes))
        except ValueError:
            r.invalid.append(f"UPDATE_TIME='{update_text}' (Format HH:MM erwartet, z. B. 06:00)")

    iam_enabled = r.bool("IAM_ENABLED")
    iam_b = r.float("IAM_B", default=0.05, minimum=0, maximum=1)
    albedo = r.float("ALBEDO", default=0.25, minimum=0, maximum=1)
    horizon_enabled = r.bool("HORIZON_ENABLED")
    bifaciality_gain = r.float("BIFACIALITY_GAIN", default=0.0, minimum=0, maximum=0.5)
    module_bifacial = r.bool("MODULE_BIFACIAL")
    report_text = r.text("REPORT_PATH")
    curtailment = _curtailment_from_reader(r)

    if r.missing or r.invalid:
        parts = []
        if r.missing:
            parts.append("Bitte folgende PV-Einstellungen in .env ergänzen: " + ", ".join(r.missing))
        if r.invalid:
            parts.append("Ungültige Werte in .env: " + "; ".join(r.invalid))
        raise ConfigError(". ".join(parts), _field_names(r.missing, r.invalid))
    for warning in r.warnings:
        log.warning(warning)

    return Config(
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        surface_tilt=surface_tilt,
        module_model=r.text("MODULE_MODEL", "Nicht angegeben"),
        module_power_wp=module_power,
        module_efficiency=efficiency,
        module_nmot_c=nmot,
        module_bifacial=module_bifacial,
        system_loss_fraction=loss,
        power_temperature_coefficient=gamma,
        arrays={
            "east": ArrayConfig(modules_east, azimuth_east),
            "west": ArrayConfig(modules_west, azimuth_west),
        },
        nominal_inverter_efficiency=inverter_eff,
        data_start_date=data_start,
        api_request_interval_seconds=interval,
        backfill_max_days=backfill,
        update_time=update_time,
        weather_source=weather_source,
        iam_enabled=iam_enabled,
        iam_b=iam_b,
        albedo=albedo,
        horizon_enabled=horizon_enabled,
        bifaciality_gain=bifaciality_gain,
        report_path=Path(report_text) if report_text else None,
        alphaess_app_id=r.text("ALPHAESS_APP_ID"),
        alphaess_app_secret=r.text("ALPHAESS_APP_SECRET"),
        alphaess_system_sn=r.text("ALPHAESS_SYSTEM_SN"),
        curtailment=curtailment,
        alphaess_soc_field=r.text("ALPHAESS_SOC_FIELD", "cbat"),
        alphaess_feed_in_field=r.text("ALPHAESS_FEED_IN_FIELD", "feedIn"),
    )


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Lädt .env-Dateien (nur ohne übergebenes Mapping) und validiert die Konfiguration."""
    if env is None:
        load_environment()
        env = os.environ
    return config_from_env(env)
