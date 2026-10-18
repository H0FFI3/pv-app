import pytest

from pvapp.curtailment import CurtailmentSettings, annotate_records, classify_hour

S = CurtailmentSettings(feed_in_limit_w=4000)


def point(actual=2.0, potential=4.0, soc=None, feed=None):
    p = {"hour": "12:00", "actual_kwh": actual, "potential_kwh": potential}
    if soc is not None:
        p["soc_pct"] = soc
    if feed is not None:
        p["feed_in_w"] = feed
    return p


def test_battery_full_with_clear_deficit_is_flagged():
    assert classify_hour(point(soc=100), S) == "battery"
    assert classify_hour(point(soc=97), S) == "battery"  # Grenzwert zählt als voll


def test_feed_in_at_the_limit_is_flagged_and_wins_over_battery():
    assert classify_hour(point(feed=3900), S) == "limit"  # >= 95 % von 4000 W
    assert classify_hour(point(soc=100, feed=4000), S) == "limit"


def test_negative_grid_power_uses_the_absolute_value():
    assert classify_hour(point(feed=-4000), S) == "limit"


@pytest.mark.parametrize(
    "p",
    [
        point(soc=80, feed=1000),  # Akku nicht voll, Einspeisung weit unter Limit: Verschattung/Wolken/Modellfehler
        point(actual=3.5, soc=100),  # Abstand nur 12,5 % < 20 %
        point(actual=4.2, soc=100),  # Ist über Potenzial
        point(potential=0.2, actual=0.0, soc=100),  # zu wenig Potenzial (Morgen/Abend)
        point(actual=None, soc=100),  # kein Ist-Wert
        point(),  # keine Signale
    ],
)
def test_no_flag_without_evidence_or_deficit(p):
    assert classify_hour(p, S) is None


def test_without_a_limit_only_the_battery_rule_applies():
    s = CurtailmentSettings()  # kein Einspeiselimit
    assert classify_hour(point(feed=9999), s) is None
    assert classify_hour(point(soc=99), s) == "battery"


def test_detection_can_be_switched_off():
    off = CurtailmentSettings(enabled=False)
    assert classify_hour(point(soc=100), off) is None
    records = [{"date": "2026-10-14", "hourly": [point(soc=100)]}]
    assert annotate_records(records, off) is records


def test_thresholds_are_applied_retroactively_to_stored_signals():
    records = [{"date": "2026-10-14", "hourly": [point(actual=3.4, soc=100)]}]  # 15 % Abstand
    strict = annotate_records(records, CurtailmentSettings(min_deficit=0.2))[0]
    lenient = annotate_records(records, CurtailmentSettings(min_deficit=0.1))[0]
    assert strict["curtailment"]["hours"] == 0 and lenient["curtailment"]["hours"] == 1


def test_day_summary_counts_hours_and_estimates_the_loss():
    day = {
        "date": "2026-10-14",
        "actual_kwh": 20,
        "hourly": [
            point(actual=1.0, potential=3.0, soc=100),  # 2,0 kWh
            point(actual=1.5, potential=4.0, soc=100),  # 2,5 kWh
            point(actual=3.0, potential=3.2, soc=60),  # nicht abgeregelt
        ],
    }
    result = annotate_records([day], S)[0]
    assert result["curtailment"] == {"hours": 2, "lost_kwh": 4.5}
    assert [p.get("curtailed") for p in result["hourly"]] == ["battery", "battery", None]
    assert "curtailed" not in day["hourly"][0], "Eingabedaten dürfen nicht verändert werden"


def test_days_without_signals_are_not_evaluated():
    day = {"date": "2026-10-13", "hourly": [point(actual=1.0, potential=4.0)]}
    result = annotate_records([day], S)[0]
    assert "curtailment" not in result  # unbekannt, nicht "keine Abregelung"
    assert "curtailed" not in result["hourly"][0]


def test_days_without_hourly_profile_are_passed_through():
    day = {"date": "2026-10-01", "actual_kwh": 5}
    assert annotate_records([day], S) == [day]
