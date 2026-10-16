import json
from datetime import date
from pathlib import Path

import pv_app
import pytest


def write_records(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"records": records}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_offline(monkeypatch, path):
    monkeypatch.setattr(pv_app, "DATA_PATH", path)
    monkeypatch.setattr(pv_app, "create_standalone_report", lambda records: path)
    monkeypatch.setattr("sys.argv", ["pv_app.py", "--offline", "--no-open"])
    pv_app.main()


def test_offline_start_keeps_records_before_data_start_date(monkeypatch, tmp_path):
    path = tmp_path / "daily.json"
    records = [
        {
            "date": "2026-10-12",
            "actual_kwh": 5.0,
            "potential_kwh": 6.0,
            "sunshine_hours": 2.0,
        }
    ]
    write_records(path, records)
    original = path.read_bytes()
    monkeypatch.setattr(pv_app, "DATA_START_DATE", date(2026, 10, 13))

    run_offline(monkeypatch, path)

    assert json.loads(path.read_text(encoding="utf-8"))["records"] == records
    assert path.read_bytes() == original
    assert not path.with_name("daily.json.bak").exists()


def test_offline_start_keeps_records_with_invalid_values(monkeypatch, tmp_path):
    path = tmp_path / "daily.json"
    records = [
        {
            "date": "2026-10-13",
            "actual_kwh": "unbekannt",
            "potential_kwh": 6.0,
            "sunshine_hours": 2.0,
            "source_note": "muss erhalten bleiben",
        }
    ]
    write_records(path, records)
    original = path.read_bytes()

    run_offline(monkeypatch, path)

    assert json.loads(path.read_text(encoding="utf-8"))["records"] == records
    assert path.read_bytes() == original
    assert not path.with_name("daily.json.bak").exists()


@pytest.mark.parametrize(
    "existing_record",
    [
        {
            "date": "2026-10-12",
            "actual_kwh": 5.0,
            "potential_kwh": 6.0,
            "sunshine_hours": 2.0,
        },
        {
            "date": "2026-10-13",
            "actual_kwh": "unbekannt",
            "potential_kwh": 6.0,
            "sunshine_hours": 2.0,
            "source_note": "muss erhalten bleiben",
        },
    ],
)
def test_saving_new_record_preserves_existing_raw_record(
    monkeypatch,
    tmp_path,
    existing_record,
):
    path = tmp_path / "daily.json"
    monkeypatch.setattr(pv_app, "DATA_START_DATE", date(2026, 10, 13))
    write_records(path, [existing_record])
    new_record = {
        "date": "2026-10-14",
        "actual_kwh": 7.0,
        "potential_kwh": 8.0,
        "sunshine_hours": 3.0,
    }

    raw_records = pv_app.load_raw_records(path)
    pv_app.save_records(pv_app._merge_record(raw_records, new_record), path)

    assert json.loads(path.read_text(encoding="utf-8"))["records"] == [
        existing_record,
        new_record,
    ]


def test_invalid_json_is_preserved_and_recovered_from_backup(tmp_path, capsys):
    path = tmp_path / "daily.json"
    path.write_text('{"records": [', encoding="utf-8")
    backup_records = [
        {
            "date": "2026-10-13",
            "actual_kwh": 4.0,
            "potential_kwh": 5.0,
            "sunshine_hours": 1.0,
        }
    ]
    write_records(path.with_name("daily.json.bak"), backup_records)

    recovered = pv_app.load_raw_records(path)

    assert recovered == backup_records
    assert not path.exists()
    assert list(tmp_path.glob("daily.json.corrupt-*"))
    assert "beschädigt" in capsys.readouterr().out.lower()


def test_save_replaces_file_atomically_in_same_directory(monkeypatch, tmp_path):
    path = tmp_path / "daily.json"
    original = '{"records": []}\n'
    path.write_text(original, encoding="utf-8")
    real_replace = pv_app.os.replace
    observations = []

    def check_replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        observations.append(
            (
                source_path.parent,
                destination_path.parent,
                path.read_text(encoding="utf-8"),
            )
        )
        real_replace(source, destination)

    monkeypatch.setattr(pv_app.os, "replace", check_replace)

    pv_app.save_records(
        [{"date": "2026-10-13", "actual_kwh": 4.0}],
        path,
    )

    assert observations == [(tmp_path, tmp_path, original)]
    assert json.loads(path.read_text(encoding="utf-8"))["records"] == [
        {"date": "2026-10-13", "actual_kwh": 4.0}
    ]
    assert json.loads(path.with_name("daily.json.bak").read_text(encoding="utf-8")) == {
        "records": []
    }