"""Anzeigedaten für den Bericht aus dem gespeicherten Rohbestand. Leichtgewichtig (kein pandas/pvlib)."""

from __future__ import annotations

import logging
import os

from . import curtailment, storage
from .config import Config, ConfigError, curtailment_settings_from_env

log = logging.getLogger(__name__)


def fallback_curtailment() -> curtailment.CurtailmentSettings:
    """Ohne vollständige Konfiguration (z. B. --offline): Einstellungen aus der Umgebung, sonst Standardwerte."""
    try:
        return curtailment_settings_from_env(os.environ)
    except ConfigError as error:
        log.warning("%s Es gelten die Standardwerte der Abregelungserkennung.", error)
        return curtailment.CurtailmentSettings()


def build_display_records(raw_records: list, config: Config | None) -> list[dict]:
    """Gültige Datensätze ab DATA_START_DATE, mit markierten abgeregelten Stunden."""
    records = storage.valid_records(raw_records, config.data_start_date if config else None)
    return curtailment.annotate_records(records, config.curtailment if config else fallback_curtailment())
