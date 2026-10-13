import asyncio
import argparse
import json
import math
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from analyze_pv import TIMEZONE
from compare_daily import estimate_daily_potential, load_actual_energy, load_weather


APP_NAME = "PV-Tagesbericht"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIRECTORY = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
APP_DATA_DIRECTORY = Path(os.getenv("LOCALAPPDATA", Path.home())) / APP_NAME
DATA_PATH = APP_DATA_DIRECTORY / "daily.json"
REPORT_PATH = APP_DIRECTORY / "dashboard.html"


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    valid_records = []
    for record in records:
        try:
            date.fromisoformat(record["date"])
            values = {
                key: float(record[key])
                for key in ("actual_kwh", "potential_kwh", "sunshine_hours")
            }
            if not all(math.isfinite(value) for value in values.values()):
                continue
            valid_records.append({"date": record["date"], **values})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(valid_records, key=lambda record: record["date"])


def save_records(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def seed_local_history() -> None:
    if DATA_PATH.exists():
        return
    for seed_path in (
        RESOURCE_ROOT / "data" / "daily.json",
        APP_DIRECTORY / "data" / "daily.json",
    ):
        if seed_path.exists():
            save_records(load_records(seed_path), DATA_PATH)
            return


async def collect_yesterday() -> dict:
    yesterday = datetime.now(ZoneInfo(TIMEZONE)).date() - timedelta(days=1)
    actual_energy, inverter_power_kw = await load_actual_energy(yesterday, yesterday)
    weather, elevation, sunshine_hours = load_weather(yesterday, yesterday)
    potential = estimate_daily_potential(weather, elevation, inverter_power_kw)

    day_key = pd.Timestamp(yesterday).tz_localize(TIMEZONE)
    if day_key not in potential.index:
        raise RuntimeError(f"Für {yesterday.isoformat()} fehlen Wetterdaten.")
    if yesterday not in sunshine_hours:
        raise RuntimeError(f"Für {yesterday.isoformat()} fehlen Sonnenscheindaten.")

    return {
        "date": yesterday.isoformat(),
        "actual_kwh": round(actual_energy[yesterday], 2),
        "potential_kwh": round(float(potential.loc[day_key]), 2),
        "sunshine_hours": round(sunshine_hours[yesterday], 2),
    }


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


def credentials_path() -> Path:
    executable_env = APP_DIRECTORY / ".env"
    source_env = Path(__file__).resolve().parent / ".env"
    if getattr(sys, "frozen", False):
        return executable_env
    return source_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Erfasst PV-Tageswerte und öffnet den lokalen HTML-Bericht.")
    parser.add_argument("--offline", action="store_true", help="Nur vorhandene lokale Daten anzeigen.")
    parser.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()

    load_dotenv(credentials_path(), override=False)
    seed_local_history()
    records = load_records(DATA_PATH)

    if not options.offline:
        try:
            record = asyncio.run(collect_yesterday())
            records = upsert_record(records, record)
            save_records(records, DATA_PATH)
            print(
                f"Erfasst: {record['date']} | Ist {record['actual_kwh']:.2f} kWh | "
                f"Potenzial {record['potential_kwh']:.2f} kWh | "
                f"Sonne {record['sunshine_hours']:.2f} h"
            )
        except Exception as error:
            print(f"Aktualisierung nicht möglich: {error}")
            print("Der Bericht wird mit den zuletzt lokal gespeicherten Daten geöffnet.")

    report = create_standalone_report(records)
    print(f"HTML-Bericht: {report}")
    print(f"Lokale Daten: {DATA_PATH}")
    if not options.no_open:
        webbrowser.open(report.resolve().as_uri())
    if sys.stdin.isatty() and not options.no_open:
        input("Eingabetaste zum Schließen drücken ...")


if __name__ == "__main__":
    main()