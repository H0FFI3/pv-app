"""Interaktive Eingaben (nur in der Kommandozeile, nie in der Bibliothekslogik)."""

from __future__ import annotations

import dataclasses
import sys
from getpass import getpass

from .config import Config


def is_interactive() -> bool:
    return bool(sys.stdin) and sys.stdin.isatty()


def prompt_credentials(config: Config) -> Config:
    """Fragt fehlende AlphaESS-Zugangsdaten ab, aber nur bei interaktivem Start."""
    if (config.alphaess_app_id and config.alphaess_app_secret) or not is_interactive():
        return config
    app_id = config.alphaess_app_id or input("AlphaESS AppID: ").strip()
    app_secret = config.alphaess_app_secret or getpass("AlphaESS AppSecret (verdeckt): ").strip()
    return dataclasses.replace(config, alphaess_app_id=app_id, alphaess_app_secret=app_secret)


def choose_system_interactively(systems: list[dict]) -> dict:
    for index, system in enumerate(systems, start=1):
        print(
            f"{index}: {system.get('minv', 'AlphaESS-Anlage')} "
            f"({system.get('popv', '?')} kWp PV, {system.get('poinv', '?')} kW Wechselrichter)"
        )
    while True:
        try:
            selection = int(input("Anlage auswählen: "))
            if 1 <= selection <= len(systems):
                return systems[selection - 1]
        except ValueError:
            pass
        print(f"Bitte eine Zahl von 1 bis {len(systems)} eingeben.")


def chooser_for_terminal():
    """Auswahlfunktion nur bei interaktivem Start, sonst ``None`` (dann gilt ALPHAESS_SYSTEM_SN)."""
    return choose_system_interactively if is_interactive() else None
