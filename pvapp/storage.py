"""Lokale Tageshistorie (daily.json): sicheres Laden, Validieren und atomares Speichern.

Grundregel: Die Datei enthält den *Rohbestand*. Ungültige oder alte Datensätze werden nur für die
Anzeige ausgefiltert (``valid_records``), aber nie aus der Datei gelöscht.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

HOURLY_SIGNAL_KEYS = ("soc_pct", "feed_in_w")


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _read_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records", []), list):
        raise ValueError("Unerwartete Struktur in der Tageshistorie.")
    payload.setdefault("records", [])
    return payload


def load_payload(path: Path) -> dict:
    """Lädt die Historie. Eine defekte Datei wird beiseite verschoben (nie gelöscht) und aus der
    Sicherung (.bak) wiederhergestellt, falls möglich. Die App stürzt dabei nicht ab."""
    if not path.exists():
        return {"records": []}
    try:
        return _read_payload(path)
    except ValueError as error:  # JSONDecodeError und UnicodeDecodeError sind ValueError
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        corrupt = path.with_name(f"{path.name}.corrupt-{stamp}")
        os.replace(path, corrupt)
        log.warning("Tageshistorie ist defekt (%s). Die Datei wurde nach %s verschoben.", error, corrupt.name)

    backup = backup_path(path)
    if backup.exists():
        try:
            payload = _read_payload(backup)
        except ValueError:
            log.warning("Auch die Sicherung %s ist defekt.", backup.name)
        else:
            shutil.copy2(backup, path)
            log.warning("Tageshistorie wurde aus der Sicherung wiederhergestellt (%d Datensätze).", len(payload["records"]))
            return payload
    log.warning("Es wird mit leerer Historie gestartet. Die defekte Datei bleibt zur Analyse erhalten.")
    return {"records": []}


def save_payload(payload: dict, path: Path) -> None:
    """Atomar speichern: temporäre Datei schreiben, vorherigen Stand als .bak sichern, dann ersetzen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copy2(path, backup_path(path))
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def normalize_record(record: dict, data_start: date | None = None) -> dict | None:
    """Gibt einen bereinigten Datensatz für die Anzeige zurück oder ``None`` bei ungültigen Daten."""
    try:
        record_date = date.fromisoformat(record["date"])
        if data_start is not None and record_date < data_start:
            return None
        values = {key: float(record[key]) for key in ("actual_kwh", "potential_kwh", "sunshine_hours")}
        if not all(math.isfinite(value) for value in values.values()):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None

    normalized = {"date": record["date"], **values}
    hourly_profile = []
    for point in record.get("hourly", []) or []:
        try:
            actual = None if point.get("actual_kwh") is None else float(point["actual_kwh"])
            potential = float(point["potential_kwh"])
            if not math.isfinite(potential) or (actual is not None and not math.isfinite(actual)):
                continue
            entry = {"hour": str(point["hour"]), "actual_kwh": actual, "potential_kwh": potential}
            for key in HOURLY_SIGNAL_KEYS:  # Akku-/Netzsignale für die Abregelungserkennung (optional)
                value = point.get(key)
                if value is not None and math.isfinite(float(value)):
                    entry[key] = float(value)
            hourly_profile.append(entry)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if hourly_profile:
        normalized["hourly"] = hourly_profile
    if isinstance(record.get("weather_source"), str):
        normalized["weather_source"] = record["weather_source"]
    if record.get("signals_checked") is True:
        normalized["signals_checked"] = True
    return normalized


def valid_records(raw_records: list, data_start: date | None = None) -> list[dict]:
    """Nur für die Anzeige: gültige Datensätze ab ``data_start``, nach Datum sortiert."""
    cleaned = [normalize_record(record, data_start) for record in raw_records if isinstance(record, dict)]
    return sorted((record for record in cleaned if record), key=lambda record: record["date"])


def stored_dates(raw_records: list) -> set[date]:
    """Tage, für die ein *gültiger* Datensatz vorliegt (ungültige Datensätze gelten als fehlend)."""
    return {date.fromisoformat(record["date"]) for record in valid_records(raw_records)}


def upsert_record(raw_records: list, record: dict) -> list:
    """Ersetzt den Datensatz desselben Tages oder fügt ihn ein. Alle anderen Einträge bleiben unverändert."""
    kept = [item for item in raw_records if not (isinstance(item, dict) and item.get("date") == record["date"])]
    kept.append(record)
    return sorted(kept, key=lambda item: str(item.get("date", "")) if isinstance(item, dict) else "")


def days_missing_signals(raw_records: list) -> set[date]:
    """Gültige Tage mit Stundenprofil, für die Akku-/Netzsignale noch nie abgefragt wurden.

    Gemeint sind Datensätze, die vor der Abregelungserkennung erfasst wurden: kein ``signals_checked``-Vermerk
    und kein Signal in den Stunden. Tage, die einmal abgefragt wurden (auch ohne Ergebnis), tauchen nicht auf,
    damit nicht bei jedem Start erneut abgerufen wird."""
    missing = set()
    for record in valid_records(raw_records):
        hourly = record.get("hourly")
        if not hourly or record.get("signals_checked"):
            continue
        if not any(point.get(key) is not None for point in hourly for key in HOURLY_SIGNAL_KEYS):
            missing.add(date.fromisoformat(record["date"]))
    return missing


def apply_signals(raw_records: list, day: date, signals: dict[str, dict[str, float]]) -> list:
    """Ergänzt den gespeicherten Tag um Signale je Stunde und setzt ``signals_checked``. Alle übrigen Werte
    des Datensatzes (Ist, Potenzial, Sonne, Stundenwerte) bleiben unverändert."""
    updated = []
    for record in raw_records:
        if isinstance(record, dict) and record.get("date") == day.isoformat():
            record = {**record, "signals_checked": True}
            if isinstance(record.get("hourly"), list):
                points = []
                for point in record["hourly"]:
                    point = dict(point) if isinstance(point, dict) else point
                    if isinstance(point, dict):
                        point.update(signals.get(str(point.get("hour")), {}))
                    points.append(point)
                record["hourly"] = points
        updated.append(record)
    return updated
