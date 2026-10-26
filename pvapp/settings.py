"""Einstellungen der Einrichtungsseite: Felder, Umrechnung (Prozent), Lesen/Schreiben von settings.env.

Leichtgewichtig (kein pandas/pvlib). Die Einstellungen liegen in ``%LOCALAPPDATA%\\PV-Tagesbericht\\settings.env``
im selben Format wie eine .env. Die Datei enthält auch das AlphaESS-Secret im Klartext; sie wird (soweit das
Betriebssystem es zulässt) nur für den eigenen Benutzer lesbar angelegt und nie auf der Seite wieder angezeigt.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from .config import ConfigError, config_from_env, effective_env, env_file_candidates
from .paths import settings_path


@dataclass(frozen=True)
class Field:
    name: str  # Name der Einstellung (wie in der .env)
    label: str
    group: str
    help: str = ""
    kind: str = "text"  # text | password | checkbox | date | time
    percent: bool = False  # Anzeige in Prozent, gespeichert wird der Anteil
    required: bool = True
    default: str = ""
    placeholder: str = ""
    secret: bool = False
    unit: str = ""


def _default_start() -> str:
    return (date.today() - timedelta(days=7)).isoformat()


GROUPS = ("AlphaESS-Zugang", "Standort", "Dach und Module", "Verluste und Wechselrichter", "Betrieb", "Erweitert")


def fields() -> list[Field]:
    """Felderliste (Standardwerte werden bei jedem Aufruf frisch berechnet, z. B. das Startdatum)."""
    return [
        Field("ALPHAESS_APP_ID", "App-ID", GROUPS[0], "Aus dem AlphaESS-OpenAPI-Portal (siehe README).", placeholder="alpha…"),
        Field("ALPHAESS_APP_SECRET", "App-Secret", GROUPS[0], "Wird gespeichert, aber nie wieder angezeigt. Leer lassen, um den gespeicherten Wert zu behalten.", kind="password", secret=True),
        Field("ALPHAESS_SYSTEM_SN", "Seriennummer der Anlage", GROUPS[0], "Nur nötig, wenn mehrere Anlagen mit dem Zugang verknüpft sind.", required=False),
        Field("LATITUDE", "Breitengrad", GROUPS[1], "Dezimalgrad, z. B. 48.78. Tipp: In Google Maps mit Rechtsklick auf das Haus die Koordinaten kopieren.", placeholder="48.78", unit="°"),
        Field("LONGITUDE", "Längengrad", GROUPS[1], "Dezimalgrad, z. B. 9.18.", placeholder="9.18", unit="°"),
        Field("TIMEZONE", "Zeitzone", GROUPS[1], "IANA-Name, z. B. Europe/Berlin.", default="Europe/Berlin"),
        Field("SURFACE_TILT_DEG", "Dachneigung", GROUPS[2], "0° = flach, 90° = senkrecht. Beide Modulfelder teilen sich eine Neigung.", placeholder="30", unit="°"),
        Field("MODULE_MODEL", "Modultyp (optional)", GROUPS[2], "Nur zur Anzeige.", required=False),
        Field("MODULE_POWER_WP", "Leistung je Modul", GROUPS[2], "Nennleistung laut Datenblatt.", placeholder="430", unit="Wp"),
        Field("MODULE_EFFICIENCY", "Modulwirkungsgrad", GROUPS[2], "Laut Datenblatt, z. B. 21.", percent=True, placeholder="21", unit="%"),
        Field("MODULE_NMOT_C", "NMOT/NOCT", GROUPS[2], "Betriebstemperatur laut Datenblatt, meist 42–47.", placeholder="44", unit="°C"),
        Field("POWER_TEMPERATURE_COEFFICIENT", "Temperaturkoeffizient Pmax", GROUPS[2], "Laut Datenblatt, negativ, z. B. -0,30.", percent=True, placeholder="-0,30", unit="%/K"),
        Field("MODULES_EAST", "Module Feld 1 (z. B. Ost)", GROUPS[2], "Anzahl. Hast du nur eine Dachfläche, trage hier alle Module ein und bei Feld 2 eine 0.", placeholder="10"),
        Field("AZIMUTH_EAST_DEG", "Ausrichtung Feld 1", GROUPS[2], "Himmelsrichtung in Grad: 90 = Ost, 180 = Süd, 270 = West.", default="90", unit="°"),
        Field("MODULES_WEST", "Module Feld 2 (z. B. West)", GROUPS[2], "Anzahl (0, wenn es kein zweites Feld gibt).", placeholder="10"),
        Field("AZIMUTH_WEST_DEG", "Ausrichtung Feld 2", GROUPS[2], "Himmelsrichtung in Grad.", default="270", unit="°"),
        Field("SYSTEM_LOSS_FRACTION", "Systemverluste", GROUPS[3], "Pauschal für Kabel, Verschmutzung, Verschattung usw. 14 % ist ein üblicher Richtwert.", percent=True, default="14", unit="%"),
        Field("NOMINAL_INVERTER_EFFICIENCY", "Wechselrichter-Wirkungsgrad", GROUPS[3], "Laut Datenblatt, meist 96–98.", percent=True, default="97", unit="%"),
        Field("DATA_START_DATE", "Erfassung ab", GROUPS[4], "Erster Tag, der erfasst wird. Fehlende Tage werden beim Start nachgeholt (bis 14 Tage zurück).", kind="date", default=_default_start()),
        Field("UPDATE_TIME", "Tägliche Aktualisierung um", GROUPS[4], "Uhrzeit, zu der der Server täglich neue Daten holt (der Rechner muss dann laufen).", kind="time", default="06:00", required=False),
        Field("FEED_IN_LIMIT_W", "Einspeiselimit", GROUPS[5], "Nur wenn dein Wechselrichter auf eine Höchsteinspeisung begrenzt ist (leer lassen bei Nulleinspeisung oder keiner Begrenzung).", required=False, unit="W"),
        Field("BATTERY_FULL_SOC_PCT", "Akku gilt als voll ab", GROUPS[5], "Für die Erkennung abgeregelter Stunden (Standard 97).", required=False, unit="%"),
        Field("CURTAILMENT_MIN_DEFICIT", "Mindestabstand Ist zu Potenzial", GROUPS[5], "Ab diesem Abstand kann eine Stunde als abgeregelt gelten (Standard 20).", percent=True, required=False, unit="%"),
        Field("IAM_ENABLED", "Einfallswinkel-Verluste berücksichtigen", GROUPS[5], "Senkt den berechneten Ertrag bei flachem Sonnenstand.", kind="checkbox", required=False),
        Field("HORIZON_ENABLED", "Horizont (Verschattung durch Umgebung) berücksichtigen", GROUPS[5], "Lädt das Horizontprofil einmalig von PVGIS.", kind="checkbox", required=False),
    ]


FIELD_NAMES = [f.name for f in fields()]

# ---------------------------------------------------------------- Umrechnung


def to_env(field: Field, text: str) -> str:
    """Eingabe in der Oberfläche -> Wert für die Konfiguration (Prozent wird zum Anteil)."""
    text = (text or "").strip()
    if not text:
        return ""
    if field.kind == "checkbox":
        return "true" if text.lower() in {"on", "true", "1", "yes", "ja"} else ""
    if field.percent:
        try:
            return format((Decimal(text.replace(",", ".")) / 100).normalize(), "f")
        except InvalidOperation:
            return text  # die Validierung meldet den ungültigen Wert
    return text


def from_env(field: Field, value: str) -> str:
    """Gespeicherter Wert -> Anzeige (Anteil wird zu Prozent)."""
    value = (value or "").strip()
    if value and field.percent:
        try:
            return format((Decimal(value.replace(",", ".")) * 100).normalize(), "f")
        except InvalidOperation:
            return value
    return value


# ---------------------------------------------------------------- Datei


def _quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Zeilenumbrüche sind in Einstellungen nicht erlaubt.")
    if value == "" or re.fullmatch(r"[A-Za-z0-9_.:/+@,-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_settings(path: Path | None = None) -> dict[str, str]:
    path = path or settings_path()
    if not path.exists():
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value not in (None, "")}


def write_settings(values: Mapping[str, str], path: Path | None = None) -> None:
    """Schreibt settings.env atomar. Schlüssel außerhalb der Formularfelder bleiben erhalten;
    leere Werte werden entfernt (es gelten dann die Standardwerte)."""
    path = path or settings_path()
    merged = {key: value for key, value in read_settings(path).items() if key not in FIELD_NAMES}
    merged.update({key: value for key, value in values.items() if value not in (None, "")})
    lines = ["# Von der Einrichtungsseite geschrieben. Enthält Zugangsdaten: nicht weitergeben.", ""]
    lines += [f"{key}={_quote(value)}" for key, value in merged.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
        try:
            os.chmod(temp_name, 0o600)  # unter Windows ohne Wirkung; dort schützt das Benutzerprofil
        except OSError:
            pass
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- Formular


def current_values(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Anzeigewerte je Feld: Standardwert < settings.env < Umgebung. Das Secret wird nie zurückgegeben."""
    environ = effective_env() if environ is None else environ
    stored = read_settings()
    values = {}
    for field in fields():
        if field.secret:
            continue
        raw = environ.get(field.name) or stored.get(field.name) or ""
        values[field.name] = from_env(field, raw) if raw else field.default
        if field.kind == "checkbox":
            values[field.name] = "true" if raw.lower() in {"1", "true", "yes", "ja", "on"} else ""
    return values


def has_stored_secret(environ: Mapping[str, str] | None = None) -> bool:
    environ = effective_env() if environ is None else environ
    return bool(environ.get("ALPHAESS_APP_SECRET") or read_settings().get("ALPHAESS_APP_SECRET"))


def shadowing_files() -> list[Path]:
    """.env-Dateien mit höherer Priorität als settings.env: Ihre Werte überstimmen die Einrichtungsseite."""
    return [path for path in env_file_candidates() if path != settings_path() and path.exists()]


def build_env(post: Mapping[str, str]) -> dict[str, str]:
    """Formularwerte -> Einstellungen. Ein leeres Secret behält den gespeicherten Wert."""
    stored = read_settings()
    env = {}
    for field in fields():
        text = post.get(field.name, "")
        if field.secret and not text.strip():
            env[field.name] = stored.get(field.name, "")
        else:
            env[field.name] = to_env(field, text)
    return env


def validate_env(env: Mapping[str, str]) -> None:
    """Wirft ConfigError (mit ``fields``), wenn die Einstellungen unvollständig oder ungültig sind."""
    missing_access = [name for name in ("ALPHAESS_APP_ID", "ALPHAESS_APP_SECRET") if not env.get(name)]
    try:
        config_from_env({key: value for key, value in env.items() if value != ""})
    except ConfigError as error:
        if missing_access:
            raise ConfigError(str(error) + ". Außerdem fehlen die AlphaESS-Zugangsdaten", set(error.fields) | set(missing_access)) from error
        raise
    if missing_access:
        raise ConfigError("Bitte die AlphaESS-Zugangsdaten (App-ID und App-Secret) eintragen", missing_access)


def friendly_message(message: str) -> str:
    """Ersetzt Einstellungsnamen in einer ConfigError-Meldung durch die Feldbezeichnungen."""
    labels = {field.name: field.label for field in fields()}
    return re.sub(r"[A-Z][A-Z0-9_]{3,}", lambda match: labels.get(match.group(0), match.group(0)), message)


def is_configured(environ: Mapping[str, str] | None = None) -> bool:
    """Gibt es eine vollständige, gültige Konfiguration (inklusive AlphaESS-Zugang)?"""
    environ = effective_env() if environ is None else environ
    try:
        config = config_from_env(environ)
    except ConfigError:
        return False
    return bool(config.alphaess_app_id and config.alphaess_app_secret)
