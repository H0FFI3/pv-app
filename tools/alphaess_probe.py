"""Diagnose: zeigt Anlagen und aktuelle Leistungsdaten der AlphaESS-API (Seriennummern werden gekürzt).

Aufruf aus dem Projektordner: python tools/alphaess_probe.py
"""
import asyncio
import json
import os
from getpass import getpass
from pathlib import Path
from typing import Any

from alphaess.alphaess import alphaess
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def redact_serials(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in {"syssn", "serial", "serialnumber", "sn"} and item:
                redacted[key] = f"...{str(item)[-4:]}"
            else:
                redacted[key] = redact_serials(item)
        return redacted
    if isinstance(value, list):
        return [redact_serials(item) for item in value]
    return value


async def probe() -> None:
    app_id = os.getenv("ALPHAESS_APP_ID") or input("AlphaESS AppID: ").strip()
    app_secret = os.getenv("ALPHAESS_APP_SECRET") or getpass(
        "AlphaESS AppSecret (hidden): "
    ).strip()
    if not app_id or not app_secret:
        raise SystemExit("AppID und AppSecret muessen angegeben werden.")

    client = alphaess(app_id, app_secret, raise_on_error=True)
    try:
        systems = await client.getESSList()
        if not systems:
            print("Die API hat keine Anlagen zurueckgegeben.")
            return

        for system in systems:
            print("Anlage:")
            print(json.dumps(redact_serials(system), ensure_ascii=True, indent=2))

            serial = system.get("sysSn")
            if serial:
                power_data = await client.getLastPowerData(serial)
                print("Aktuelle Leistungsdaten:")
                print(json.dumps(redact_serials(power_data), ensure_ascii=True, indent=2))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(probe())