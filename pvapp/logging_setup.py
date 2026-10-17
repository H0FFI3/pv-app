"""Logging: schlanke Konsole + rotierende Logdatei. Zugangsdaten werden aus Meldungen entfernt."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class SecretRedactor(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add(self, *secrets: str) -> None:
        self._secrets.update(s for s in secrets if s and len(s) >= 4)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            record.msg = self.redact(record.getMessage())
            record.args = ()
        return True


REDACTOR = SecretRedactor()


def setup_logging(log_directory: Path | None, *, verbose: bool = False) -> None:
    root = logging.getLogger("pvapp")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    root.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    console.addFilter(REDACTOR)
    root.addHandler(console)

    if log_directory is not None:
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_directory / "pv-tagesbericht.log", maxBytes=500_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
            handler.addFilter(REDACTOR)
            root.addHandler(handler)
        except OSError as error:
            root.warning("Logdatei nicht verfügbar (%s).", error)
