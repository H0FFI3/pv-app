"""Erzeugt HTML-Berichte für die Frontend-Tests: python tests/frontend/make_fixtures.py <Zielordner>"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pvapp.paths import project_root  # noqa: E402
from pvapp.report import create_standalone_report  # noqa: E402


def hourly(shift, missing_actual=False):
    points = []
    for hour in range(6, 19):
        potential = max(0.0, 3.0 - abs(hour - 12) * 0.5)
        actual = round(potential * (0.7 + shift * 0.02), 3)
        points.append(
            {
                "hour": f"{hour:02d}:00",
                "actual_kwh": None if (missing_actual and hour in (7, 17)) else actual,
                "potential_kwh": round(potential, 3),
            }
        )
    return points


def records(count, with_hourly):
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
            item["hourly"] = hourly(i % 5, missing_actual=(i % 2 == 0))
        result.append(item)
    return result


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    scenarios = {
        "empty": [],
        "one": records(1, 0),
        "many": records(30, 7),
        "few_hourly": records(30, 3),
    }
    for name, data in scenarios.items():
        create_standalone_report(data, project_root(), target / f"{name}.html")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
