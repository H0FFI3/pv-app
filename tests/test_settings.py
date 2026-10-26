import os
import stat
import sys

import pytest
from dotenv import dotenv_values

from pvapp import settings
from pvapp.config import ConfigError, config_from_env, effective_env, load_environment, env_file_candidates
from pvapp.paths import settings_path

FORM = {
    "ALPHAESS_APP_ID": "alpha123", "ALPHAESS_APP_SECRET": "geheim", "LATITUDE": "48.78", "LONGITUDE": "9.18",
    "TIMEZONE": "Europe/Berlin", "SURFACE_TILT_DEG": "30", "MODULE_POWER_WP": "430", "MODULE_EFFICIENCY": "21,5",
    "MODULE_NMOT_C": "44", "POWER_TEMPERATURE_COEFFICIENT": "-0,30", "MODULES_EAST": "10", "AZIMUTH_EAST_DEG": "90",
    "MODULES_WEST": "10", "AZIMUTH_WEST_DEG": "270", "SYSTEM_LOSS_FRACTION": "14", "NOMINAL_INVERTER_EFFICIENCY": "97",
    "DATA_START_DATE": "2026-10-13", "UPDATE_TIME": "06:30",
}


@pytest.fixture(autouse=True)
def local_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for module in ("pvapp.config", "pvapp.settings"):
        monkeypatch.setattr(f"{module}.env_file_candidates", lambda: [settings_path()])


def field(name):
    return next(f for f in settings.fields() if f.name == name)


def test_percent_fields_are_converted_in_both_directions():
    efficiency = field("MODULE_EFFICIENCY")
    assert settings.to_env(efficiency, "21,5") == "0.215"
    assert settings.to_env(field("POWER_TEMPERATURE_COEFFICIENT"), "-0,30") == "-0.003"
    assert settings.to_env(field("SYSTEM_LOSS_FRACTION"), "14") == "0.14"
    assert settings.from_env(efficiency, "0.215") == "21.5"
    assert settings.from_env(field("SYSTEM_LOSS_FRACTION"), "0.14") == "14"
    assert settings.to_env(efficiency, "abc") == "abc"  # ungültig bleibt ungültig und wird gemeldet
    assert settings.to_env(efficiency, "  ") == ""


def test_checkbox_values():
    box = field("IAM_ENABLED")
    assert settings.to_env(box, "on") == "true" and settings.to_env(box, "") == "" and settings.to_env(box, "off") == ""


@pytest.mark.parametrize("secret", ["einfach", "mit leerzeichen", 'ein "zitat"', "raute#test", "back\\slash", "gleich=zeichen", "ümläut", "a'b"])
def test_values_survive_a_write_read_roundtrip(tmp_path, secret):
    path = tmp_path / "settings.env"
    settings.write_settings({"ALPHAESS_APP_SECRET": secret, "LATITUDE": "48.78"}, path)
    assert dotenv_values(path)["ALPHAESS_APP_SECRET"] == secret
    assert settings.read_settings(path) == {"ALPHAESS_APP_SECRET": secret, "LATITUDE": "48.78"}


def test_newlines_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        settings.write_settings({"ALPHAESS_APP_ID": "a\nb"}, tmp_path / "s.env")
    assert not list(tmp_path.glob("*.tmp")) and not (tmp_path / "s.env").exists()


def test_write_keeps_foreign_keys_drops_empty_values_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("CUSTOM_KEY=bleibt\nLATITUDE=1\nFEED_IN_LIMIT_W=4000\n", encoding="utf-8")
    settings.write_settings({"LATITUDE": "48.78", "FEED_IN_LIMIT_W": ""}, path)
    assert settings.read_settings(path) == {"CUSTOM_KEY": "bleibt", "LATITUDE": "48.78"}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX-Dateirechte")
def test_settings_file_is_only_readable_by_the_owner(tmp_path):
    path = tmp_path / "settings.env"
    settings.write_settings({"ALPHAESS_APP_SECRET": "x"}, path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_build_env_converts_and_keeps_the_stored_secret_when_left_blank():
    settings.write_settings({"ALPHAESS_APP_SECRET": "gespeichert"})
    env = settings.build_env({**FORM, "ALPHAESS_APP_SECRET": ""})
    assert env["ALPHAESS_APP_SECRET"] == "gespeichert" and env["MODULE_EFFICIENCY"] == "0.215"
    assert settings.build_env(FORM)["ALPHAESS_APP_SECRET"] == "geheim"


def test_a_complete_form_validates_and_yields_a_working_config():
    env = settings.build_env(FORM)
    settings.validate_env(env)
    config = config_from_env({k: v for k, v in env.items() if v})
    assert config.module_efficiency == 0.215 and config.power_temperature_coefficient == -0.003
    assert (config.update_time.hour, config.update_time.minute) == (6, 30)


def test_validation_reports_the_affected_fields_with_labels():
    env = settings.build_env({**FORM, "MODULE_EFFICIENCY": "150", "LATITUDE": ""})
    with pytest.raises(ConfigError) as error:
        settings.validate_env(env)
    assert {"MODULE_EFFICIENCY", "LATITUDE"} <= set(error.value.fields)
    message = settings.friendly_message(str(error.value))
    assert "Modulwirkungsgrad" in message and "Breitengrad" in message and "MODULE_EFFICIENCY" not in message


def test_missing_credentials_are_reported():
    env = settings.build_env({**FORM, "ALPHAESS_APP_ID": "", "ALPHAESS_APP_SECRET": ""})
    with pytest.raises(ConfigError) as error:
        settings.validate_env(env)
    assert {"ALPHAESS_APP_ID", "ALPHAESS_APP_SECRET"} <= set(error.value.fields)


def test_invalid_update_time_is_rejected():
    env = settings.build_env({**FORM, "UPDATE_TIME": "25:99"})
    with pytest.raises(ConfigError, match="UPDATE_TIME"):
        settings.validate_env(env)


def test_current_values_show_defaults_and_percent_and_never_the_secret():
    settings.write_settings(settings.build_env(FORM))
    values = settings.current_values()
    assert values["MODULE_EFFICIENCY"] == "21.5" and values["SYSTEM_LOSS_FRACTION"] == "14" and values["TIMEZONE"] == "Europe/Berlin"
    assert "ALPHAESS_APP_SECRET" not in values and settings.has_stored_secret()


def test_unconfigured_state_uses_helpful_defaults():
    values = settings.current_values({})
    assert values["TIMEZONE"] == "Europe/Berlin" and values["AZIMUTH_EAST_DEG"] == "90" and values["UPDATE_TIME"] == "06:00"
    assert values["LATITUDE"] == ""
    assert settings.is_configured({}) is False and not settings.has_stored_secret({})  # nichts gespeichert


def test_settings_env_is_the_lowest_priority_candidate(monkeypatch):
    monkeypatch.undo()  # echte env_file_candidates verwenden
    monkeypatch.setenv("LOCALAPPDATA", "/tmp/x")
    candidates = env_file_candidates()
    assert candidates[-1] == settings_path() and candidates[0] != settings_path()


def test_effective_env_does_not_touch_os_environ_and_real_variables_win(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LATITUDE=1\nLONGITUDE=2\n", encoding="utf-8")
    environ = {"LONGITUDE": "99"}
    merged = effective_env(environ, [env_file])
    assert merged == {"LATITUDE": "1", "LONGITUDE": "99"} and environ == {"LONGITUDE": "99"}
    monkeypatch.delenv("LATITUDE", raising=False)
    load_environment([env_file], {})  # verändert nur das übergebene Mapping
    assert "LATITUDE" not in os.environ


def test_shadowing_env_files_are_reported(tmp_path, monkeypatch):
    other = tmp_path / ".env"
    other.write_text("LATITUDE=1", encoding="utf-8")
    monkeypatch.setattr("pvapp.settings.env_file_candidates", lambda: [other, settings_path()])
    assert settings.shadowing_files() == [other]
