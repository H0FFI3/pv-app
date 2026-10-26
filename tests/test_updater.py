import subprocess
from datetime import datetime, time, timedelta, timezone

import pytest

from pvapp import updater as up
from pvapp.updater import Scheduler, StatusStore, Updater, describe_status, due, next_slot_text


class FakePopen:
    instances = []

    def __init__(self, command, **kwargs):
        self.command, self.kwargs, self.code = command, kwargs, None
        FakePopen.instances.append(self)

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def finish(self, code=0, log_text=""):
        self.kwargs["stdout"].write(log_text.encode("utf-8"))
        self.kwargs["stdout"].flush()
        self.code = code


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def parts(tmp_path):
    FakePopen.instances = []
    clock = Clock(datetime(2026, 10, 18, 6, 0))
    status = StatusStore(tmp_path / "status.json")
    updater = Updater(status, command=lambda: ["fake", "--no-open"], popen=FakePopen, now=clock, log_path=lambda: tmp_path / "logs" / "last.log")
    return updater, status, clock


def test_start_spawns_one_headless_process_and_records_the_state(parts):
    updater, status, _ = parts
    assert updater.start("manual") is True
    process = FakePopen.instances[0]
    assert process.command[:2] == ["fake", "--no-open"] and process.command[2] == "--log-file" and process.command[3].endswith("last.log")
    assert process.kwargs["stdin"] == subprocess.DEVNULL and process.kwargs["stderr"] == subprocess.STDOUT
    assert process.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert updater.running() and status.read()["last_ok"] is None and status.read()["last_trigger"] == "manual"
    assert updater.start("manual") is False and len(FakePopen.instances) == 1  # nie zwei gleichzeitig


def test_success_sets_the_success_date_and_allows_the_next_run(parts):
    updater, status, clock = parts
    updater.start("schedule")
    clock.now = datetime(2026, 10, 18, 6, 2)
    FakePopen.instances[0].finish(0, "Erfasst: 2026-10-17\n")
    assert updater.running() is False
    data = status.read()
    assert data["last_ok"] is True and data["last_success_date"] == "2026-10-18" and data["last_finished"].startswith("2026-10-18T06:02")
    assert updater.start("manual") is True


def test_failure_reports_the_most_telling_log_line(parts):
    updater, status, _ = parts
    updater.start("schedule")
    FakePopen.instances[0].finish(1, "PV-Tagesbericht wird gestartet.\nAktualisierung fehlgeschlagen: Netzwerk weg\nDer Bericht wird mit lokalen Daten geöffnet.\n")
    updater.poll()
    data = status.read()
    assert data["last_ok"] is False and "Netzwerk weg" in data["last_message"] and "last_success_date" not in data


def test_attempts_are_counted_per_day(parts):
    updater, status, clock = parts
    for _ in range(2):
        updater.start("schedule")
        FakePopen.instances[-1].finish(1)
        updater.poll()
    assert status.read()["attempts"] == 2
    clock.now += timedelta(days=1)
    updater.start("schedule")
    assert status.read()["attempts"] == 1


def test_a_failing_start_is_recorded_without_crashing(tmp_path):
    def broken(*args, **kwargs):
        raise OSError("nicht gefunden")

    status = StatusStore(tmp_path / "s.json")
    updater = Updater(status, command=lambda: ["x"], popen=broken, now=Clock(datetime(2026, 10, 18, 6)), log_path=lambda: tmp_path / "l.log")
    assert updater.start("manual") is False
    assert status.read()["last_ok"] is False and "nicht gefunden" in status.read()["last_message"]


def test_stop_terminates_a_running_update(parts):
    updater, _, _ = parts
    updater.start("manual")
    updater.stop()
    assert FakePopen.instances[0].code == -15


def test_update_command_points_at_the_app():
    command = up.update_command()
    assert command[-2:] == ["--no-open", "--no-report"] and command[0]  # der Server liefert den Bericht selbst aus


def test_the_status_headline_is_shortened(parts):
    updater, status, _ = parts
    updater.start("manual")
    FakePopen.instances[0].finish(1, "Aktualisierung fehlgeschlagen: " + "x" * 500 + "\n")
    updater.poll()
    assert len(status.read()["last_message"]) <= 220 and status.read()["last_message"].endswith("...")


def test_status_store_survives_corrupt_files(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{kaputt", encoding="utf-8")
    store = StatusStore(path)
    assert store.read() == {}
    store.update(a=1)
    assert store.read() == {"a": 1}


# ------------------------------------------------------------------------------ due()

SLOT = time(6, 0)


def at(hour, minute=0, day=18):
    return datetime(2026, 10, day, hour, minute)


@pytest.mark.parametrize(
    "now,status,expected",
    [
        (at(7), {"last_success_date": "2026-10-18"}, False),  # heute schon erledigt
        (at(5), {"last_success_date": "2026-10-17"}, False),  # vor der Uhrzeit
        (at(6), {"last_success_date": "2026-10-17"}, True),  # genau zur Uhrzeit
        (at(21), {"last_success_date": "2026-10-15"}, True),  # Rechner war aus: Nachholen
        (at(5), {}, True),  # erster Start: sofort, auch vor der Uhrzeit
        (at(7), {"last_success_date": "2026-10-17", "attempts_date": "2026-10-18", "attempts": 3, "last_started": "2026-10-18T06:00:00"}, False),  # Versuche aufgebraucht
        (at(6, 20), {"last_success_date": "2026-10-17", "attempts_date": "2026-10-18", "attempts": 1, "last_started": "2026-10-18T06:00:00"}, False),  # Wartezeit
        (at(6, 31), {"last_success_date": "2026-10-17", "attempts_date": "2026-10-18", "attempts": 1, "last_started": "2026-10-18T06:00:00"}, True),
        (at(8), {"last_success_date": "2026-10-17", "attempts_date": "2026-10-17", "attempts": 3, "last_started": "2026-10-17T07:00:00"}, True),  # Versuche zählen je Tag
        (at(5, 10), {"attempts_date": "2026-10-18", "attempts": 1, "last_started": "2026-10-18T05:00:00"}, False),  # erster Start schlug fehl: erst nach der Wartezeit
        (at(7), {"last_success_date": "2026-10-17", "last_started": "kein-datum", "attempts_date": "2026-10-18", "attempts": 1}, True),
    ],
)
def test_due(now, status, expected):
    assert due(now, SLOT, status) is expected


def test_due_works_with_timezone_aware_times():
    now = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)
    status = {"last_success_date": "2026-10-17", "attempts_date": "2026-10-18", "attempts": 1, "last_started": "2026-10-18T06:00:00"}
    assert due(now, SLOT, status) is True


def test_status_texts():
    assert describe_status({}, False)["text"] == "Noch keine Aktualisierung."
    assert describe_status({}, True) == {"running": True, "ok": None, "text": "Aktualisierung läuft …"}
    ok = describe_status({"last_finished": "2026-10-18T06:02:00", "last_ok": True}, False)
    assert ok["ok"] is True and "18.10.2026 06:02" in ok["text"]
    bad = describe_status({"last_finished": "2026-10-18T06:02:00", "last_ok": False, "last_message": "Netzwerk weg"}, False)
    assert bad["ok"] is False and "Netzwerk weg" in bad["text"]
    assert next_slot_text(at(5), SLOT, {}) == "Nächste Aktualisierung: 18.10. um 06:00"
    assert next_slot_text(at(7), SLOT, {}) == "Nächste Aktualisierung: sobald möglich"
    assert next_slot_text(at(7), SLOT, {"last_success_date": "2026-10-18"}) == "Nächste Aktualisierung: 19.10. um 06:00"


# ------------------------------------------------------------------------------ Scheduler


def test_scheduler_starts_only_when_configured_and_due(parts):
    updater, status, clock = parts
    state = {"configured": False}
    scheduler = Scheduler(updater, lambda: (state["configured"], SLOT, None), now=clock)
    assert scheduler.tick() is False and not FakePopen.instances  # nicht eingerichtet
    state["configured"] = True
    assert scheduler.tick() is True and status.read()["last_trigger"] == "schedule"
    assert scheduler.tick() is False and len(FakePopen.instances) == 1  # läuft schon
    FakePopen.instances[0].finish(0)
    assert scheduler.tick() is False  # heute erledigt
    clock.now += timedelta(days=1, hours=1)
    assert scheduler.tick() is True and len(FakePopen.instances) == 2


def test_scheduler_thread_survives_errors():
    class Boom:
        def poll(self):
            raise RuntimeError("kaputt")

    scheduler = Scheduler(Boom(), lambda: (True, SLOT, None), interval_seconds=0.01)
    scheduler.start()
    import time as t

    t.sleep(0.1)
    assert scheduler.is_alive()
    scheduler.stop()
    scheduler.join(1)


def test_the_clean_log_file_is_preferred_for_the_status_line(parts):
    updater, status, _ = parts
    updater.start("schedule")
    process = FakePopen.instances[0]
    log_file = process.command[process.command.index("--log-file") + 1]
    import pathlib

    pathlib.Path(log_file).write_text("Start\nAktualisierung fehlgeschlagen: Passwort falsch\nBericht wird geöffnet\n", encoding="utf-8")
    process.finish(1, "Traceback (most recent call last): ... rohe Ausgabe ...")
    updater.poll()
    assert status.read()["last_message"] == "Aktualisierung fehlgeschlagen: Passwort falsch"


def test_crash_before_logging_falls_back_to_the_raw_output(parts):
    updater, status, _ = parts
    updater.start("schedule")
    FakePopen.instances[0].finish(1, "ModuleNotFoundError: No module named 'pvlib'\n")
    updater.poll()
    assert "ModuleNotFoundError" in status.read()["last_message"]
