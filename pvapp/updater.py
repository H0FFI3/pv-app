"""Aktualisierung als eigener Prozess, Status-Datei und Zeitplan für den Serve-Modus.

Die Aktualisierung läuft als Kindprozess (``PV-Tagesbericht.exe --no-open``): Der Server bleibt klein
(kein pandas/pvlib im Speicher), ein Absturz der Berechnung reißt ihn nicht mit, und der Speicher wird
nach jedem Lauf wieder frei. Leichtgewichtig: importiert weder pandas noch pvlib.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from . import paths

log = logging.getLogger(__name__)

RETRY_MINUTES = 30
MAX_ATTEMPTS_PER_DAY = 3
LOG_TAIL_LINES = 40


class StatusStore:
    """Kleine JSON-Datei mit dem Zustand der letzten Aktualisierungen (übersteht Neustarts)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.status_path()
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def update(self, **changes) -> dict:
        with self._lock:
            data = {**self.read(), **changes}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
            return data


def update_command() -> list[str]:
    """Befehl für die Aktualisierung: die EXE selbst bzw. ``python pv_app.py``."""
    if paths.is_frozen():
        return [sys.executable, "--no-open", "--no-report"]
    return [sys.executable, str(paths.project_root() / "pv_app.py"), "--no-open", "--no-report"]


def last_update_log_path() -> Path:
    return paths.log_directory() / "last-update.log"


def last_update_output_path() -> Path:
    """Rohausgabe (stdout/stderr) des Kindprozesses: hilft bei Abstürzen noch vor dem Logging."""
    return paths.log_directory() / "last-update.out"


def read_log_tail(path: Path | None = None, lines: int = LOG_TAIL_LINES) -> list[str]:
    path = path or last_update_log_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()][-lines:]


def _headline(tail: list[str]) -> str:
    """Aussagekräftigste Zeile für die Anzeige: letzte Fehler-/Warnzeile, sonst die letzte Zeile."""
    line = ""
    for candidate in reversed(tail):
        if any(marker in candidate for marker in ("fehlgeschlagen", "Fehler", "nicht möglich", "Übersprungen")):
            line = candidate
            break
    else:
        line = tail[-1] if tail else ""
    return line if len(line) <= 220 else line[:217] + "..."


class Updater:
    """Startet höchstens eine Aktualisierung gleichzeitig und hält den Status aktuell."""

    def __init__(
        self,
        status: StatusStore,
        *,
        command: Callable[[], list[str]] = update_command,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        now: Callable[[], datetime] = datetime.now,
        log_path: Callable[[], Path] = last_update_log_path,
        output_path: Callable[[], Path] | None = None,
    ) -> None:
        self.status = status
        self._command = command
        self._popen = popen
        self._now = now
        self._log_path = log_path
        self._output_path = output_path or (lambda: self._log_path().with_suffix(".out"))
        self._process: subprocess.Popen | None = None
        self._log_handle = None
        self._trigger = ""
        self._lock = threading.Lock()

    def running(self) -> bool:
        with self._lock:
            return self._running_locked()

    def _running_locked(self) -> bool:
        if self._process is None:
            return False
        code = self._process.poll()
        if code is None:
            return True
        self._finish(code)
        return False

    def poll(self) -> None:
        self.running()

    def start(self, trigger: str) -> bool:
        """Startet eine Aktualisierung. ``False``, wenn schon eine läuft oder der Start scheitert."""
        with self._lock:
            if self._running_locked():
                return False
            now = self._now()
            log_file = self._log_path()
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = open(self._output_path(), "wb")
                options = {}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NO_WINDOW
                self._process = self._popen(
                    [*self._command(), "--log-file", str(log_file)],
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                    **options,
                )
            except OSError as error:
                log.error("Aktualisierung konnte nicht gestartet werden: %s", error)
                self.status.update(last_started=now.isoformat(timespec="seconds"), last_finished=now.isoformat(timespec="seconds"),
                                   last_ok=False, last_trigger=trigger, last_message=f"Start nicht möglich: {error}")
                self._close_log()
                return False
            self._trigger = trigger
            today = now.date().isoformat()
            previous = self.status.read()
            attempts = previous.get("attempts", 0) if previous.get("attempts_date") == today else 0
            self.status.update(
                last_started=now.isoformat(timespec="seconds"),
                last_trigger=trigger,
                last_ok=None,
                last_message="Aktualisierung läuft …",
                attempts_date=today,
                attempts=attempts + 1,
            )
            log.info("Aktualisierung gestartet (%s).", trigger)
            return True

    def _finish(self, code: int) -> None:
        self._process = None
        self._close_log()
        now = self._now()
        tail = read_log_tail(self._log_path()) or read_log_tail(self._output_path())
        ok = code == 0
        changes = {
            "last_finished": now.isoformat(timespec="seconds"),
            "last_ok": ok,
            "last_message": "Aktualisierung abgeschlossen." if ok else (_headline(tail) or f"Abbruch mit Code {code}"),
        }
        if ok:
            changes["last_success_date"] = now.date().isoformat()
        self.status.update(**changes)
        log.info("Aktualisierung beendet: %s", "erfolgreich" if ok else f"Fehler (Code {code})")

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._close_log()


def due(
    now: datetime,
    update_time: time,
    status: dict,
    *,
    retry_minutes: int = RETRY_MINUTES,
    max_attempts: int = MAX_ATTEMPTS_PER_DAY,
) -> bool:
    """Soll jetzt eine geplante Aktualisierung starten?

    - Heute schon erfolgreich: nein.
    - Noch nie erfolgreich (erster Start): sofort.
    - Sonst ab ``update_time``; nach einem Fehler frühestens nach ``retry_minutes`` und höchstens
      ``max_attempts`` Versuche pro Tag. Wurde der Rechner zur Uhrzeit nicht betrieben, läuft die
      Aktualisierung beim nächsten Start nach (Nachholen).
    """
    today = now.date().isoformat()
    if status.get("last_success_date") == today:
        return False
    if status.get("last_success_date") is None:
        attempts = status.get("attempts", 0) if status.get("attempts_date") == today else 0
        return _retry_allowed(now, status, attempts, retry_minutes, max_attempts)
    slot = datetime.combine(now.date(), update_time, tzinfo=now.tzinfo)
    if now < slot:
        return False
    attempts = status.get("attempts", 0) if status.get("attempts_date") == today else 0
    return _retry_allowed(now, status, attempts, retry_minutes, max_attempts)


def _retry_allowed(now: datetime, status: dict, attempts: int, retry_minutes: int, max_attempts: int) -> bool:
    if attempts >= max_attempts:
        return False
    started = status.get("last_started")
    if attempts and started:
        try:
            last = datetime.fromisoformat(started)
        except ValueError:
            return True
        if last.tzinfo is None and now.tzinfo is not None:
            last = last.replace(tzinfo=now.tzinfo)
        if now - last < timedelta(minutes=retry_minutes):
            return False
    return True


def describe_status(status: dict, running: bool) -> dict:
    """Anzeige für den Berichtskopf: Text und Zustand."""
    if running:
        return {"running": True, "ok": None, "text": "Aktualisierung läuft …"}
    finished = status.get("last_finished")
    if not finished:
        return {"running": False, "ok": None, "text": "Noch keine Aktualisierung."}
    try:
        when = datetime.fromisoformat(finished).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        when = finished
    if status.get("last_ok"):
        return {"running": False, "ok": True, "text": f"Zuletzt aktualisiert: {when}"}
    return {"running": False, "ok": False, "text": f"Letzte Aktualisierung fehlgeschlagen ({when}): {status.get('last_message', '')}"}


def next_slot_text(now: datetime, update_time: time, status: dict) -> str:
    done_today = status.get("last_success_date") == now.date().isoformat()
    if not done_today and now.time() >= update_time:
        return "Nächste Aktualisierung: sobald möglich"
    day = now.date() + timedelta(days=1) if done_today else now.date()
    return f"Nächste Aktualisierung: {day.strftime('%d.%m.')} um {update_time.strftime('%H:%M')}"


class Scheduler(threading.Thread):
    """Prüft regelmäßig, ob eine geplante Aktualisierung fällig ist. Lädt die Konfiguration nur neu,
    wenn sich Einstellungsdateien geändert haben (kein Log-Spam, keine unnötige Arbeit)."""

    def __init__(
        self,
        updater: Updater,
        get_settings: Callable[[], tuple[bool, time, str | None]],
        *,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="pv-scheduler")
        self.updater = updater
        self._get_settings = get_settings
        self._interval = interval_seconds
        self._now = now
        self._stop_event = threading.Event()

    def tick(self) -> bool:
        """Ein Prüfschritt (für Tests öffentlich). ``True``, wenn eine Aktualisierung gestartet wurde."""
        self.updater.poll()
        configured, update_time, timezone = self._get_settings()
        if not configured or self.updater.running():
            return False
        if self._now is not None:
            now = self._now()
        elif timezone:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo(timezone))
        else:
            now = datetime.now().astimezone()
        if due(now, update_time, self.updater.status.read()):
            return self.updater.start("schedule")
        return False

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self.tick()
            except Exception:  # der Zeitplan darf den Server nie beenden
                log.exception("Fehler im Zeitplan")

    def stop(self) -> None:
        self._stop_event.set()
