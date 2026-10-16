import argparse
import asyncio
import json
import math
import os
import shutil
import sys
import tempfile
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from pv_config import DATA_START_DATE, TIMEZONE, validate_configuration
from compare_daily import estimate_hourly_potential, load_actual_energy, load_weather


APP_NAME = "PV-Tagesbericht"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIRECTORY = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
APP_DATA_DIRECTORY = Path(os.getenv("LOCALAPPDATA", Path.home())) / APP_NAME
DATA_PATH = APP_DATA_DIRECTORY / "daily.json"
REPORT_PATH = APP_DIRECTORY / "dashboard.html"


def _read_raw_records(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Das Datenformat enthält keine Datensatzliste.")
    return payload["records"]


def load_raw_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return _read_raw_records(path)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        corrupt_path = path.with_name(f"{path.name}.corrupt-{timestamp}")
        suffix = 1
        while corrupt_path.exists():
            corrupt_path = path.with_name(f"{path.name}.corrupt-{timestamp}-{suffix}")
            suffix += 1
        try:
            os.replace(path, corrupt_path)
            print(
                f"Warnung: {path.name} ist beschädigt und wurde als "
                f"{corrupt_path.name} gesichert.",
                flush=True,
            )
        except OSError as error:
            print(f"Warnung: {path.name} konnte nicht umbenannt werden: {error}", flush=True)

    backup_path = path.with_name(f"{path.name}.bak")
    if backup_path.exists():
        try:
            records = _read_raw_records(backup_path)
            print(f"Warnung: Daten werden aus {backup_path.name} wiederhergestellt.", flush=True)
            return records
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            print(f"Warnung: Auch {backup_path.name} ist beschädigt.", flush=True)
    return []


def _normalize_records(records: list[dict]) -> list[dict]:
    valid_records = []
    for record in records:
        try:
            record_date = date.fromisoformat(record["date"])
            if record_date < DATA_START_DATE:
                continue
            values = {
                key: float(record[key])
                for key in ("actual_kwh", "potential_kwh", "sunshine_hours")
            }
            if not all(math.isfinite(value) for value in values.values()):
                continue
            normalized = {"date": record["date"], **values}
            hourly_profile = []
            for point in record.get("hourly", []):
                try:
                    actual = None if point.get("actual_kwh") is None else float(point["actual_kwh"])
                    potential = float(point["potential_kwh"])
                    if not math.isfinite(potential) or (actual is not None and not math.isfinite(actual)):
                        continue
                    hourly_profile.append(
                        {
                            "hour": str(point["hour"]),
                            "actual_kwh": actual,
                            "potential_kwh": potential,
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            if hourly_profile:
                normalized["hourly"] = hourly_profile
            valid_records.append(normalized)
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(valid_records, key=lambda record: record["date"])


def load_records(path: Path) -> list[dict]:
    return _normalize_records(load_raw_records(path))


def save_records(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump({"records": records}, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.bak"))
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _merge_record(records: list[dict], updated_record: dict) -> list[dict]:
    merged = list(records)
    updated_date = updated_record["date"]
    for index, existing in enumerate(merged):
        if not isinstance(existing, dict) or existing.get("date") != updated_date:
            continue
        if _normalize_records([existing]):
            merged[index] = updated_record
            return merged
    merged.append(updated_record)
    return merged


async def collect_yesterday() -> dict | None:
    yesterday = datetime.now(ZoneInfo(TIMEZONE)).date() - timedelta(days=1)
    if yesterday < DATA_START_DATE:
        return None
    validate_configuration()
    print(f"Datenabruf für {yesterday.isoformat()} wird gestartet.", flush=True)
    actual_energy, inverter_power_kw, hourly_actual = await load_actual_energy(
        yesterday,
        yesterday,
        include_hourly=True,
    )
    print("Wetter: rufe historische Strahlungs- und Sonnenscheindaten ab ...", flush=True)
    weather, elevation, sunshine_hours = load_weather(yesterday, yesterday)
    print("Modell: berechne das wetterbasierte PV-Potenzial ...", flush=True)
    hourly_potential = estimate_hourly_potential(weather, elevation, inverter_power_kw)
    potential = hourly_potential.resample("D").sum(min_count=1)

    day_key = pd.Timestamp(yesterday).tz_localize(TIMEZONE)
    if day_key not in potential.index:
        raise RuntimeError(f"Für {yesterday.isoformat()} fehlen Wetterdaten.")
    if yesterday not in sunshine_hours:
        raise RuntimeError(f"Für {yesterday.isoformat()} fehlen Sonnenscheindaten.")

    day_potential = hourly_potential[hourly_potential.index.date == yesterday]
    hourly_measurements = hourly_actual.get(yesterday, {})
    hourly_profile = [
        {
            "hour": timestamp.strftime("%H:00"),
            "actual_kwh": hourly_measurements.get(timestamp.strftime("%H:00")),
            "potential_kwh": round(float(hourly_kwh), 3),
        }
        for timestamp, hourly_kwh in day_potential.items()
    ]
    record = {
        "date": yesterday.isoformat(),
        "actual_kwh": round(actual_energy[yesterday], 2),
        "potential_kwh": round(float(potential.loc[day_key]), 2),
        "sunshine_hours": round(sunshine_hours[yesterday], 2),
        "hourly": hourly_profile,
    }
    print("Datenabruf und Berechnung abgeschlossen.", flush=True)
    return record


def upsert_record(records: list[dict], record: dict) -> list[dict]:
    by_date = {item["date"]: item for item in records}
    by_date[record["date"]] = record
    return [by_date[key] for key in sorted(by_date)]


def create_standalone_report(records: list[dict]) -> Path:
    template_path = RESOURCE_ROOT / "site" / "index.html"
    style_path = RESOURCE_ROOT / "site" / "styles.css"
    script_path = RESOURCE_ROOT / "site" / "dashboard.js"
    html = template_path.read_text(encoding="utf-8")
    styles = style_path.read_text(encoding="utf-8")
    script = script_path.read_text(encoding="utf-8")
    data = json.dumps({"records": records}, ensure_ascii=True, separators=(",", ":"))
    html = html.replace(
        '<link rel="stylesheet" href="styles.css">',
        f"<style>\n{styles}\n</style>",
    )
    html = html.replace(
        '<script src="dashboard.js" defer></script>',
        "",
    )
    html = html.replace(
        "</body>",
        f"<script>window.PV_DAILY_DATA = {data};</script>\n<script>\n{script}\n</script>\n</body>",
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    return REPORT_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Erfasst PV-Tageswerte und öffnet den lokalen HTML-Bericht.")
    parser.add_argument("--offline", action="store_true", help="Nur vorhandene lokale Daten anzeigen.")
    parser.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()

    print("PV-Tagesbericht wird gestartet.", flush=True)
    credentials_loaded = bool(os.getenv("ALPHAESS_APP_ID") and os.getenv("ALPHAESS_APP_SECRET"))
    if credentials_loaded:
        print("AlphaESS-Zugangsdaten gefunden; Werte werden nicht angezeigt.", flush=True)
    else:
        print("AlphaESS-Zugangsdaten fehlen oder sind unvollständig.", flush=True)
    raw_records = load_raw_records(DATA_PATH)
    records = _normalize_records(raw_records)
    print(f"{len(records)} lokale Tagesdatensätze geladen.", flush=True)

    if not options.offline:
        try:
            record = asyncio.run(collect_yesterday())
            if record is None:
                print(f"Die Erfassung beginnt am {DATA_START_DATE.isoformat()}.")
                print("Der erste vollständige Tagesdatensatz ist am 14.10. verfügbar.")
            else:
                raw_records = _merge_record(raw_records, record)
                records = _normalize_records(raw_records)
                save_records(raw_records, DATA_PATH)
                print(
                    f"Erfasst: {record['date']} | Ist {record['actual_kwh']:.2f} kWh | "
                    f"Potenzial {record['potential_kwh']:.2f} kWh | "
                    f"Sonne {record['sunshine_hours']:.2f} h"
                )
        except Exception as error:
            print(f"Aktualisierung fehlgeschlagen: {error}", flush=True)
            print("Der Bericht wird mit den zuletzt lokal gespeicherten Daten geöffnet.", flush=True)

    print("Erzeuge eigenständigen HTML-Bericht ...", flush=True)
    report = create_standalone_report(records)
    print(f"HTML-Bericht erstellt: {report}", flush=True)
    print(f"Lokale Daten: {DATA_PATH}", flush=True)
    if not options.no_open:
        print("Öffne Bericht im Standardbrowser ...", flush=True)
        if not webbrowser.open(report.resolve().as_uri()):
            print(f"Browser konnte nicht gestartet werden. Datei manuell öffnen: {report}", flush=True)
    if sys.stdin.isatty() and not options.no_open:
        input("Eingabetaste zum Schließen drücken ...")


if __name__ == "__main__":
    main()