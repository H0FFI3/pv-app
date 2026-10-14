import os
import sys
from datetime import date
from pathlib import Path

from dotenv import dotenv_values


def _load_environment() -> None:
    if getattr(sys, "frozen", False):
        executable_directory = Path(sys.executable).resolve().parent
        candidates = [executable_directory / ".env", executable_directory.parent / ".env"]
    else:
        candidates = [Path(__file__).resolve().parent / ".env"]

    file_values = {}
    for path in reversed(candidates):
        file_values.update(
            {
                key: value
                for key, value in dotenv_values(path).items()
                if value is not None
            }
        )
    for key, value in file_values.items():
        os.environ.setdefault(key, value)


def _float_setting(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _int_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


_load_environment()

REQUIRED_SETTINGS = (
    "LATITUDE",
    "LONGITUDE",
    "TIMEZONE",
    "SURFACE_TILT_DEG",
    "MODULE_MODEL",
    "MODULE_POWER_WP",
    "MODULE_EFFICIENCY",
    "MODULE_NMOT_C",
    "MODULE_BIFACIAL",
    "SYSTEM_LOSS_FRACTION",
    "POWER_TEMPERATURE_COEFFICIENT",
    "MODULES_EAST",
    "AZIMUTH_EAST_DEG",
    "MODULES_WEST",
    "AZIMUTH_WEST_DEG",
    "NOMINAL_INVERTER_EFFICIENCY",
    "API_REQUEST_INTERVAL_SECONDS",
    "DATA_START_DATE",
)


def validate_configuration() -> None:
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Bitte folgende PV-Einstellungen in .env ergänzen: " + ", ".join(missing)
        )


LATITUDE = _float_setting("LATITUDE", 0)
LONGITUDE = _float_setting("LONGITUDE", 0)
TIMEZONE = os.getenv("TIMEZONE", "UTC")
SURFACE_TILT = _float_setting("SURFACE_TILT_DEG", 0)
MODULE_MODEL = os.getenv("MODULE_MODEL", "Nicht konfiguriert")
MODULE_POWER_WP = _float_setting("MODULE_POWER_WP", 0)
MODULE_EFFICIENCY = _float_setting("MODULE_EFFICIENCY", 0)
MODULE_NMOT_C = _float_setting("MODULE_NMOT_C", 0)
MODULE_BIFACIAL = os.getenv("MODULE_BIFACIAL", "false").strip().lower() in {"1", "true", "yes"}
SYSTEM_LOSS_FRACTION = _float_setting("SYSTEM_LOSS_FRACTION", 0)
POWER_TEMPERATURE_COEFFICIENT = _float_setting("POWER_TEMPERATURE_COEFFICIENT", 0)
MODULES_EAST = _int_setting("MODULES_EAST", 0)
AZIMUTH_EAST = _float_setting("AZIMUTH_EAST_DEG", 0)
MODULES_WEST = _int_setting("MODULES_WEST", 0)
AZIMUTH_WEST = _float_setting("AZIMUTH_WEST_DEG", 0)
NOMINAL_INVERTER_EFFICIENCY = _float_setting("NOMINAL_INVERTER_EFFICIENCY", 0)
API_REQUEST_INTERVAL_SECONDS = _int_setting("API_REQUEST_INTERVAL_SECONDS", 10)
DATA_START_DATE = date.fromisoformat(os.getenv("DATA_START_DATE", "1970-01-01"))

ARRAYS = {
    "east": {"modules": MODULES_EAST, "azimuth": AZIMUTH_EAST},
    "west": {"modules": MODULES_WEST, "azimuth": AZIMUTH_WEST},
}