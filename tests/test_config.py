import pytest

from pvapp.config import ConfigError, config_from_env, load_environment


def test_valid_configuration(config):
    assert config.latitude == 48.78
    assert config.installed_kwp == pytest.approx(8.6)
    assert set(config.arrays) == {"east", "west"}
    assert config.arrays["west"].azimuth == 270
    assert config.api_request_interval_seconds == 0
    # Optionen haben Standardwerte = bisheriges Verhalten
    assert (config.iam_enabled, config.albedo, config.horizon_enabled, config.bifaciality_gain) == (False, 0.25, False, 0.0)
    assert config.backfill_max_days == 14 and config.weather_source == "auto"


def test_missing_values_are_all_listed(base_env):
    for name in ("LATITUDE", "DATA_START_DATE", "NOMINAL_INVERTER_EFFICIENCY"):
        del base_env[name]
    with pytest.raises(ConfigError) as error:
        config_from_env(base_env)
    for name in ("LATITUDE", "DATA_START_DATE", "NOMINAL_INVERTER_EFFICIENCY"):
        assert name in str(error.value)


def test_invalid_number_names_the_variable_instead_of_crashing_with_valueerror(base_env):
    base_env["MODULE_POWER_WP"] = "abc"
    with pytest.raises(ConfigError, match="MODULE_POWER_WP='abc'"):
        config_from_env(base_env)


@pytest.mark.parametrize(
    "name,value",
    [
        ("LATITUDE", "95"),
        ("NOMINAL_INVERTER_EFFICIENCY", "0"),  # früher Division durch null
        ("MODULE_EFFICIENCY", "21"),  # Prozent statt Anteil
        ("TIMEZONE", "Mars/Olympus"),
        ("DATA_START_DATE", "13.10.2026"),
        ("BACKFILL_MAX_DAYS", "0"),
        ("WEATHER_SOURCE", "wolke"),
        ("IAM_ENABLED", "vielleicht"),
    ],
)
def test_implausible_values_are_rejected(base_env, name, value):
    base_env[name] = value
    with pytest.raises(ConfigError, match=name):
        config_from_env(base_env)


def test_no_modules_at_all_is_rejected(base_env):
    base_env.update(MODULES_EAST="0", MODULES_WEST="0")
    with pytest.raises(ConfigError, match="MODULES_EAST"):
        config_from_env(base_env)


def test_german_decimal_comma_is_accepted(base_env):
    base_env["MODULE_EFFICIENCY"] = "0,21"
    assert config_from_env(base_env).module_efficiency == 0.21


def test_optional_fields_may_be_missing(base_env):
    for name in ("MODULE_MODEL", "MODULE_BIFACIAL", "API_REQUEST_INTERVAL_SECONDS"):
        base_env.pop(name)
    config = config_from_env(base_env)
    assert config.module_model == "Nicht angegeben" and config.api_request_interval_seconds == 10


def test_secrets_are_not_part_of_repr(config):
    assert "test-secret-5678" not in repr(config)
    assert "test-secret-5678" in config.secrets


def test_load_environment_priority_and_empty_values(tmp_path):
    high, low = tmp_path / "high.env", tmp_path / "low.env"
    high.write_text("A=\nB=von_high\n", encoding="utf-8")
    low.write_text("A=von_low\nB=von_low\nC=nur_low\n", encoding="utf-8")
    environ = {"C": "aus_umgebung"}
    load_environment([high, low], environ)
    assert environ == {"A": "von_low", "B": "von_high", "C": "aus_umgebung"}


def test_curtailment_defaults_and_overrides(base_env, config):
    c = config.curtailment
    assert (c.enabled, c.soc_full_pct, c.min_deficit, c.feed_in_limit_w) == (True, 97.0, 0.2, None)
    assert (config.alphaess_soc_field, config.alphaess_feed_in_field) == ("cbat", "feedIn")
    base_env.update(FEED_IN_LIMIT_W="4200", BATTERY_FULL_SOC_PCT="95", CURTAILMENT_MIN_DEFICIT="0.1",
                    ALPHAESS_SOC_FIELD="soc", CURTAILMENT_DETECTION="false")
    custom = config_from_env(base_env)
    assert custom.curtailment.feed_in_limit_w == 4200 and custom.curtailment.soc_full_pct == 95
    assert custom.curtailment.enabled is False and custom.alphaess_soc_field == "soc"


@pytest.mark.parametrize("name,value", [("BATTERY_FULL_SOC_PCT", "20"), ("CURTAILMENT_MIN_DEFICIT", "2"), ("FEED_IN_LIMIT_W", "-5")])
def test_invalid_curtailment_values_are_rejected(base_env, name, value):
    base_env[name] = value
    with pytest.raises(ConfigError, match=name):
        config_from_env(base_env)


def test_curtailment_settings_can_be_read_without_a_complete_configuration():
    from pvapp.config import curtailment_settings_from_env

    assert curtailment_settings_from_env({"FEED_IN_LIMIT_W": "3000"}).feed_in_limit_w == 3000
    with pytest.raises(ConfigError, match="BATTERY_FULL_SOC_PCT"):
        curtailment_settings_from_env({"BATTERY_FULL_SOC_PCT": "x"})
