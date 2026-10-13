import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from analyze_pv import TIMEZONE
from compare_daily import estimate_daily_potential, load_actual_energy, load_weather


DATA_PATH = Path(__file__).parent / "site" / "data" / "daily.json"


def upsert_daily_record(path: Path, record: dict) -> None:
    if path.exists():
        dataset = json.loads(path.read_text(encoding="utf-8"))
        records = dataset.get("records", [])
    else:
        records = []

    by_date = {item["date"]: item for item in records}
    by_date[record["date"]] = record
    dataset = {"records": [by_date[key] for key in sorted(by_date)]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8")


async def update_yesterday() -> dict:
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


async def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    record = await update_yesterday()
    upsert_daily_record(DATA_PATH, record)
    print(
        f"Gespeichert: {record['date']}, Ist {record['actual_kwh']:.2f} kWh, "
        f"Potenzial {record['potential_kwh']:.2f} kWh, "
        f"Sonne {record['sunshine_hours']:.2f} h"
    )


if __name__ == "__main__":
    asyncio.run(main())