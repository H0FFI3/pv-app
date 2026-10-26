"""Erzeugt HTML-Berichte für die Frontend-Tests: python tests/frontend/make_fixtures.py <Zielordner>"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pvapp.curtailment import CurtailmentSettings, annotate_records  # noqa: E402
from pvapp.pages import server_bar, setup_page  # noqa: E402
from pvapp.paths import project_root  # noqa: E402
from pvapp.report import create_standalone_report, render_report  # noqa: E402
from pvapp.settings import current_values  # noqa: E402


def hourly(shift, missing_actual=False, curtail=False, signals=True):
    """Stundenprofil. ``curtail``: 11-14 Uhr Akku voll und Einspeisung am Limit (4000 W), Ist nur 50 % des Potenzials."""
    points = []
    for hour in range(6, 19):
        potential = max(0.0, 3.0 - abs(hour - 12) * 0.5)
        is_curtailed = curtail and 11 <= hour <= 14
        actual = round(potential * (0.5 if is_curtailed else 0.7 + shift * 0.02), 3)
        point = {
            "hour": f"{hour:02d}:00",
            "actual_kwh": None if (missing_actual and hour in (7, 17)) else actual,
            "potential_kwh": round(potential, 3),
        }
        if signals:
            point["soc_pct"] = 100.0 if is_curtailed else 60.0
            point["feed_in_w"] = 4000.0 if is_curtailed else 800.0
        points.append(point)
    return points


def records(count, with_hourly, curtailed_days=(), signals=True):
    start = date(2026, 10, 14) - timedelta(days=count - 1)
    result = []
    for i in range(count):
        item = {
            "date": (start + timedelta(days=i)).isoformat(),
            "actual_kwh": round(10 + (i % 5) * 1.37, 2),
            "potential_kwh": round(14 + (i % 3) * 0.91, 2),
            "sunshine_hours": round(4 + (i % 4) * 1.1, 2),
        }
        if i >= 5:
            item["weather_source"] = "archive" if i < 20 else "forecast"
        if i >= count - with_hourly:
            has_signals = signals and i >= count - with_hourly + 1  # ältester Stundentag ohne Signale: Erkennung nicht möglich
            item["hourly"] = hourly(i % 5, missing_actual=(i % 2 == 0), curtail=(count - 1 - i) in curtailed_days, signals=has_signals)
            if has_signals or not signals:
                item["signals_checked"] = True  # neue Datensätze: Signale wurden abgefragt (ggf. ohne Ergebnis)
        result.append(item)
    return result


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    scenarios = {
        "empty": [],
        "one": records(1, 0),
        "many": records(30, 7, curtailed_days=(0, 2)),  # letzter und vorletzter Tag zuvor sind abgeregelt
        "few_hourly": records(30, 3),
        "no_signals": records(30, 7, signals=False),
    }
    settings = CurtailmentSettings(feed_in_limit_w=4000)
    for name, data in scenarios.items():
        create_standalone_report(annotate_records(data, settings), project_root(), target / f"{name}.html")

    status = {"running": False, "ok": True, "text": "Zuletzt aktualisiert: 18.10.2026 06:02"}
    bar = server_bar("TOKEN123", status, "Nächste Aktualisierung: 19.10. um 06:00")
    (target / "served.html").write_text(render_report(annotate_records(records(5, 2), settings), project_root(), bar), encoding="utf-8")
    (target / "setup.html").write_text(setup_page("TOKEN123", current_values({}), has_secret=True), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
