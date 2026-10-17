import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pvapp.config import config_from_env  # noqa: E402

BASE_ENV = {
    "LATITUDE": "48.78",
    "LONGITUDE": "9.18",
    "TIMEZONE": "Europe/Berlin",
    "SURFACE_TILT_DEG": "30",
    "MODULE_MODEL": "Testmodul",
    "MODULE_POWER_WP": "430",
    "MODULE_EFFICIENCY": "0.21",
    "MODULE_NMOT_C": "44",
    "MODULE_BIFACIAL": "false",
    "POWER_TEMPERATURE_COEFFICIENT": "-0.0030",
    "SYSTEM_LOSS_FRACTION": "0.14",
    "MODULES_EAST": "10",
    "AZIMUTH_EAST_DEG": "90",
    "MODULES_WEST": "10",
    "AZIMUTH_WEST_DEG": "270",
    "NOMINAL_INVERTER_EFFICIENCY": "0.97",
    "API_REQUEST_INTERVAL_SECONDS": "0",
    "DATA_START_DATE": "2026-10-13",
    "ALPHAESS_APP_ID": "test-app-id-1234",
    "ALPHAESS_APP_SECRET": "test-secret-5678",
}


@pytest.fixture
def base_env():
    return dict(BASE_ENV)


@pytest.fixture
def make_config():
    def factory(**overrides):
        env = dict(BASE_ENV)
        env.update({key: str(value) for key, value in overrides.items()})
        return config_from_env(env)

    return factory


@pytest.fixture
def config(make_config):
    return make_config()
