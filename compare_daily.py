"""Optional: manueller Vergleich Ist/Potenzial für einen Zeitraum -> pv_daily_comparison.json.

Dünnes Kommandozeilenskript; Datenquellen und Modell liegen in pvapp/.
"""

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from pvapp import model, paths, sources
from pvapp.config import ConfigError, load_config
from pvapp.interactive import chooser_for_terminal, prompt_credentials
from pvapp.logging_setup import REDACTOR, setup_logging

OUTPUT_PATH = Path(__file__).with_name("pv_daily_comparison.json")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Datum bitte als YYYY-MM-DD angeben.") from error


async def main() -> None:
    setup_logging(None)
    parser = argparse.ArgumentParser(description="Vergleicht AlphaESS-Tagesertrag mit wetterbasiertem PV-Potenzial.")
    parser.add_argument("start", type=parse_date, help="Erster Tag (YYYY-MM-DD)")
    parser.add_argument("end", type=parse_date, help="Letzter Tag (YYYY-MM-DD)")
    options = parser.parse_args()
    config = load_config()
    REDACTOR.add(*config.secrets)
    today = sources.local_today(config.timezone)
    if options.start > options.end:
        parser.error("Das Startdatum muss vor oder gleich dem Enddatum liegen.")
    if options.end >= today:
        parser.error("Bitte nur abgeschlossene Tage bis einschließlich gestern angeben.")

    config = prompt_credentials(config)
    REDACTOR.add(*config.secrets)
    days = [options.start + timedelta(days=i) for i in range((options.end - options.start).days + 1)]
    actual = await sources.fetch_actuals(config, days, chooser=chooser_for_terminal())
    weather = sources.load_weather(config, options.start, options.end)
    horizon = sources.load_horizon(config, paths.horizon_cache_path()) if config.horizon_enabled else None
    potential = model.estimate_daily_potential(weather.frame, weather.elevation, actual.inverter_power_kw, config, horizon)

    rows = []
    for day, actual_kwh in sorted(actual.energy.items()):
        key = pd.Timestamp(day).tz_localize(config.timezone)
        if key not in potential.index or day not in weather.sunshine_hours:
            raise RuntimeError(f"Für {day.isoformat()} fehlen Wetter- oder Sonnenscheindaten.")
        potential_kwh = float(potential.loc[key])
        rows.append(
            {
                "date": day.isoformat(),
                "sunshine_hours": weather.sunshine_hours[day],
                "actual_kwh": actual_kwh,
                "potential_kwh": potential_kwh,
                "difference_kwh": potential_kwh - actual_kwh,
                "actual_vs_potential_pct": actual_kwh / potential_kwh * 100 if potential_kwh > 0 else None,
            }
        )
    for day, reason in sorted(actual.errors.items()):
        print(f"Übersprungen: {day.isoformat()} ({reason})")

    comparison = pd.DataFrame(rows)
    comparison.to_json(OUTPUT_PATH, orient="records", date_format="iso", double_precision=2, force_ascii=False)
    print(comparison.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print(f"Vergleich gespeichert: {OUTPUT_PATH}")
    print(
        f"Potenzial = wetterbasiertes PVWatts-Modell mit {config.system_loss_fraction:.0%} Systemverlusten; "
        "Dachverschattung und Abregelung sind nicht enthalten."
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as error:
        sys.exit(str(error))
