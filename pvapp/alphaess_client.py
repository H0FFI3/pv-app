"""AlphaESS-Client und Verbindungsprüfung. Importiert weder pandas noch pvlib (wichtig für den schlanken Server)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable


def create_client(app_id: str, app_secret: str):
    from alphaess.alphaess import alphaess  # lazy: Paket wird nur für echte Abrufe benötigt

    return alphaess(app_id, app_secret, raise_on_error=True)


def summarize_systems(systems: list[dict]) -> list[dict]:
    """Anlagenübersicht ohne Seriennummern (für die Anzeige in der Einrichtungsseite)."""
    return [
        {
            "model": system.get("minv"),
            "pv_kwp": system.get("popv"),
            "inverter_kw": system.get("poinv"),
            "battery_kwh": system.get("cobat"),
        }
        for system in systems
    ]


@dataclass
class ConnectionResult:
    ok: bool
    message: str
    systems: list[dict] = field(default_factory=list)
    multiple: bool = False  # mehrere Anlagen: ALPHAESS_SYSTEM_SN nötig


async def check_connection(
    app_id: str,
    app_secret: str,
    serial: str = "",
    *,
    client_factory: Callable[[str, str], Any] | None = None,
    timeout: float = 25.0,
) -> ConnectionResult:
    if not app_id or not app_secret:
        return ConnectionResult(False, "Bitte App-ID und App-Secret eintragen.")
    client = (client_factory or create_client)(app_id, app_secret)
    try:
        systems = await asyncio.wait_for(client.getESSList(), timeout)
    except asyncio.TimeoutError:
        return ConnectionResult(False, "Zeitüberschreitung: AlphaESS hat nicht rechtzeitig geantwortet.")
    except Exception as error:
        text = str(error)
        for secret in (app_id, app_secret):
            text = text.replace(secret, "***")
        return ConnectionResult(False, f"Anmeldung bei AlphaESS fehlgeschlagen: {text or type(error).__name__}")
    finally:
        try:
            await client.close()
        except Exception:
            pass

    if not systems:
        return ConnectionResult(False, "Die Anmeldung hat funktioniert, aber es ist keine Anlage mit diesem Zugang verknüpft.")
    summary = summarize_systems(systems)
    if serial and not any(system.get("sysSn") == serial for system in systems):
        return ConnectionResult(False, "Die Seriennummer passt zu keiner Anlage dieses Zugangs.", summary, len(systems) > 1)
    multiple = len(systems) > 1
    note = " Bitte die Seriennummer der gewünschten Anlage eintragen." if multiple and not serial else ""
    return ConnectionResult(True, f"Verbindung erfolgreich: {len(systems)} Anlage(n) gefunden.{note}", summary, multiple)
