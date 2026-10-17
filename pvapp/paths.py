"""Pfade für Quellcode-Betrieb und PyInstaller-EXE. Alles wird zur Laufzeit ermittelt (testbar)."""

import os
import sys
from pathlib import Path

APP_NAME = "PV-Tagesbericht"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Ordner, der die mitgelieferten Vorlagen (site/) enthält."""
    return Path(getattr(sys, "_MEIPASS", project_root()))


def app_directory() -> Path:
    """Ordner der EXE bzw. des Projekts (hier liegt standardmäßig dashboard.html)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def app_data_directory() -> Path:
    return Path(os.getenv("LOCALAPPDATA", Path.home())) / APP_NAME


def data_path() -> Path:
    return app_data_directory() / "daily.json"


def log_directory() -> Path:
    return app_data_directory() / "logs"


def horizon_cache_path() -> Path:
    return app_data_directory() / "horizon.json"
