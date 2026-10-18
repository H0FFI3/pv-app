"""Zeigt, welche Felder die AlphaESS-API für deine Anlage liefert (Seriennummern und Namen werden maskiert).

Nützlich, um die Feldnamen für die Abregelungserkennung zu prüfen (ALPHAESS_SOC_FIELD / ALPHAESS_FEED_IN_FIELD).

Aufruf aus dem Projektordner:  python tools/alphaess_fields.py [--date JJJJ-MM-TT]
Die Zugangsdaten kommen aus .env bzw. Umgebung. Es werden 4 Abfragen mit der üblichen API-Schutzpause gestellt.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta
from getpass import getpass
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pvapp.config import load_environment  # noqa: E402

SENSITIVE_PARTS = ("sn", "serial", "mail", "phone", "address", "license", "token", "secret", "name")


def mask(key: str, value: Any) -> str:
    if any(part in key.lower() for part in SENSITIVE_PARTS):
        return "***"
    text = repr(value)
    return text if len(text) <= 48 else text[:45] + "..."


def describe_record(record: dict, indent: str = "  ") -> list[str]:
    lines = []
    for key in sorted(record):
        value = record[key]
        if isinstance(value, dict):
            lines.append(f"{indent}{key}: (Objekt) Felder: {', '.join(sorted(value))}")
        elif isinstance(value, list):
            lines.append(f"{indent}{key}: (Liste mit {len(value)} Einträgen)")
        else:
            lines.append(f"{indent}{key}: {mask(key, value)}")
    return lines


def describe_samples(samples: list[dict], soc_field: str, feed_in_field: str) -> list[str]:
    lines = [f"  {len(samples)} Leistungsproben"]
    if not samples:
        return lines
    lines.append("  Felder: " + ", ".join(sorted({key for sample in samples for key in sample})))
    lines.append("  Erste Probe:")
    lines += describe_record(samples[0], "    ")
    for name in (soc_field, feed_in_field):
        values = [float(s[name]) for s in samples if isinstance(s.get(name), (int, float))]
        if values:
            lines.append(f"  {name}: Minimum {min(values):g}, Maximum {max(values):g} ({len(values)} Werte)")
        else:
            lines.append(f"  {name}: NICHT VORHANDEN (Feldname prüfen, ggf. ALPHAESS_*_FIELD setzen)")
    return lines


async def describe_fields(
    client: Any,
    serial: str,
    day: date,
    *,
    soc_field: str = "cbat",
    feed_in_field: str = "feedIn",
    pause_seconds: float = 10,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[str]:
    lines = [f"== getOneDateEnergyBySn ({day.isoformat()}) =="]
    lines += describe_record(await client.getOneDateEnergyBySn(serial, day.isoformat()) or {})
    await sleep(pause_seconds)
    lines += ["", f"== getOneDayPowerBySn ({day.isoformat()}) =="]
    lines += describe_samples(await client.getOneDayPowerBySn(serial, day.isoformat()) or [], soc_field, feed_in_field)
    await sleep(pause_seconds)
    lines += ["", "== getLastPowerData (Momentaufnahme, evtl. mit Werten je MPPT) =="]
    lines += describe_record(await client.getLastPowerData(serial) or {})
    return lines


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    options = parser.parse_args()

    load_environment()
    app_id = os.getenv("ALPHAESS_APP_ID") or input("AlphaESS AppID: ").strip()
    app_secret = os.getenv("ALPHAESS_APP_SECRET") or getpass("AlphaESS AppSecret (verdeckt): ").strip()
    pause = float(os.getenv("API_REQUEST_INTERVAL_SECONDS") or 10)

    from alphaess.alphaess import alphaess

    client = alphaess(app_id, app_secret, raise_on_error=True)
    try:
        systems = await client.getESSList()
        print("== getESSList ==")
        for system in systems:
            print("\n".join(describe_record(system)))
        wanted = os.getenv("ALPHAESS_SYSTEM_SN")
        system = next((s for s in systems if s.get("sysSn") == wanted), systems[0])
        await asyncio.sleep(pause)
        print()
        print(
            "\n".join(
                await describe_fields(
                    client,
                    system["sysSn"],
                    options.date,
                    soc_field=os.getenv("ALPHAESS_SOC_FIELD") or "cbat",
                    feed_in_field=os.getenv("ALPHAESS_FEED_IN_FIELD") or "feedIn",
                    pause_seconds=pause,
                )
            )
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
