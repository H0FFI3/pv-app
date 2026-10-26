"""Lokaler Server: Bericht, Einrichtungsseite und tägliche Aktualisierung (Befehl ``serve``).

Sicherheit (der Server läuft auf dem eigenen Rechner, die Seite verwaltet aber Zugangsdaten):
- Standard: nur ``127.0.0.1``. Einrichtungsseite und API (``/setup``, ``/api/*`` außer ``/api/ping``) sind
  immer nur für lokale Clients erreichbar, auch wenn der Bericht mit ``--host`` im Netz freigegeben wird.
- ``Host``-Header wird geprüft (Schutz gegen DNS-Rebinding), ``Origin`` bei POST ebenso.
- Schreibende Aufrufe benötigen ein Token, das nur in den vom Server ausgelieferten Seiten steht.
- Das AlphaESS-Secret wird nie an den Browser zurückgegeben.

Leichtgewichtig: importiert weder pandas noch pvlib.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import http.client
import json
import logging
import os
import secrets
import sys
import threading
import webbrowser
from datetime import datetime, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import alphaess_client, paths, settings, storage
from .config import Config, ConfigError, config_from_env, effective_env, env_file_candidates
from .display import build_display_records
from .interactive import show_message
from .logging_setup import REDACTOR, setup_logging
from .pages import server_bar, setup_page
from .report import render_report
from .updater import Scheduler, StatusStore, Updater, describe_status, next_slot_text

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


class AppState:
    """Gemeinsamer Zustand des Servers (Token, Aktualisierung, geladene Konfiguration)."""

    def __init__(
        self,
        *,
        port: int,
        updater: Updater,
        base_env: dict[str, str] | None = None,
        connection_check: Callable[..., Any] | None = None,
    ) -> None:
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.updater = updater
        self.base_env = dict(os.environ) if base_env is None else dict(base_env)  # echte Umgebungsvariablen, unverändert
        self.connection_check = connection_check or alphaess_client.check_connection
        self.shutdown: Callable[[], None] = lambda: None  # setzt main(): beendet serve_forever
        self._config_cache: tuple[tuple, Config | None, str] | None = None
        self._lock = threading.Lock()

    # -- Konfiguration (ohne os.environ zu verändern; neu laden, wenn sich Dateien ändern)
    def _signature(self) -> tuple:
        signature = []
        for path in env_file_candidates():
            try:
                stat = path.stat()
                signature.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                signature.append((str(path), None, None))
        return tuple(signature)

    def environment(self) -> dict[str, str]:
        return effective_env(self.base_env)

    def config(self) -> tuple[Config | None, str]:
        """(Konfiguration oder None, Fehlermeldung). Das Ergebnis wird je Dateistand zwischengespeichert."""
        with self._lock:
            signature = self._signature()
            if self._config_cache and self._config_cache[0] == signature:
                return self._config_cache[1], self._config_cache[2]
            try:
                config, message = config_from_env(self.environment()), ""
                REDACTOR.add(*config.secrets)
            except ConfigError as error:
                config, message = None, str(error)
            self._config_cache = (signature, config, message)
            return config, message

    def scheduler_settings(self):
        config, _ = self.config()
        usable = bool(config and config.alphaess_app_id and config.alphaess_app_secret)
        return usable, (config.update_time if config else time(6, 0)), (config.timezone if config else None)


# ------------------------------------------------------------------------------ Handler


def make_handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PV-Tagesbericht"
        protocol_version = "HTTP/1.1"

        # -- Hilfen ------------------------------------------------------------------
        def log_message(self, format: str, *args) -> None:  # Zugriffe nicht auf die Konsole schreiben
            log.debug("%s %s", self.address_string(), format % args)

        def client_is_loopback(self) -> bool:
            return self.client_address[0] in LOOPBACK_ADDRESSES

        def host_is_local(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{state.port}", f"localhost:{state.port}", f"[::1]:{state.port}"}

        def origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin.lower() in {f"http://127.0.0.1:{state.port}", f"http://localhost:{state.port}"}

        def token_ok(self, supplied: str | None) -> bool:
            return bool(supplied) and hmac.compare_digest(str(supplied), state.token)

        def send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(key, value)
            if status >= 400:  # unverarbeiteter Anfragekörper darf die nächste Anfrage nicht stören
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_html(self, text: str, status: int = 200) -> None:
            self.send(status, text.encode("utf-8"), "text/html; charset=utf-8")

        def send_json(self, data: dict, status: int = 200) -> None:
            self.send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def redirect(self, location: str) -> None:
            self.send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": location})

        def forbidden(self, reason: str) -> None:
            log.warning("Anfrage abgewiesen (%s): %s %s", reason, self.command, self.path)
            self.send_json({"ok": False, "message": "Nicht erlaubt."}, HTTPStatus.FORBIDDEN)

        def local_only(self) -> bool:
            """Für Einrichtung und API: nur lokale Clients mit lokalem Host-Header."""
            if not (self.client_is_loopback() and self.host_is_local()):
                self.forbidden("kein lokaler Zugriff")
                return False
            return True

        def read_body(self) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self.send_json({"ok": False, "message": "Anfrage zu groß."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return None
            return self.rfile.read(length)

        # -- GET -----------------------------------------------------------------------
        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/ping":
                return self.send_json({"app": "pv-tagesbericht"})
            if path == "/favicon.ico":
                return self.send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            if path == "/":
                return self.dashboard()
            if path == "/setup":
                if not self.local_only():
                    return
                return self.setup_form()
            if path == "/api/status":
                if not self.local_only():
                    return
                if not self.token_ok(self.headers.get("X-PV-Token")):
                    return self.forbidden("Token fehlt oder falsch")
                return self.send_json(self.status_payload())
            self.send_json({"ok": False, "message": "Nicht gefunden."}, HTTPStatus.NOT_FOUND)

        def status_payload(self) -> dict:
            state.updater.poll()
            status = describe_status(state.updater.status.read(), state.updater.running())
            config, _ = state.config()
            schedule = ""
            if config:
                schedule = next_slot_text(datetime.now(), config.update_time, state.updater.status.read())
            return {"status": status, "schedule": schedule}

        def dashboard(self) -> None:
            config, message = state.config()
            if config is None or not (config.alphaess_app_id and config.alphaess_app_secret):
                if self.client_is_loopback():
                    return self.redirect("/setup")
                return self.send_html(
                    "<!doctype html><meta charset=utf-8><title>PV-Tagesbericht</title>"
                    "<p>Die App ist noch nicht eingerichtet. Bitte die Einrichtung am Rechner selbst öffnen.</p>",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            payload = storage.load_payload(paths.data_path())
            records = build_display_records(payload["records"], config)
            extra = ""
            if self.client_is_loopback() and self.host_is_local():
                state.updater.poll()
                status = describe_status(state.updater.status.read(), state.updater.running())
                schedule = next_slot_text(datetime.now(), config.update_time, state.updater.status.read())
                extra = server_bar(state.token, status, schedule)
            self.send_html(render_report(records, paths.resource_root(), extra))

        def setup_form(self, *, values=None, error_text="", error_fields=()) -> None:
            self.send_html(
                setup_page(
                    state.token,
                    values if values is not None else settings.current_values(state.environment()),
                    error_text=error_text,
                    error_fields=error_fields,
                    has_secret=settings.has_stored_secret(state.environment()),
                    shadowing=[str(path) for path in settings.shadowing_files()],
                    configured=state.config()[0] is not None,
                ),
                HTTPStatus.UNPROCESSABLE_ENTITY if error_text else HTTPStatus.OK,
            )

        # -- POST ----------------------------------------------------------------------
        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in {"/setup", "/api/update", "/api/test-alphaess", "/api/shutdown"}:
                return self.send_json({"ok": False, "message": "Nicht gefunden."}, HTTPStatus.NOT_FOUND)
            if not self.local_only():
                return
            if not self.origin_ok():
                return self.forbidden("fremder Origin")
            body = self.read_body()
            if body is None:
                return

            if path == "/setup":
                form = {key: values[0] for key, values in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}
                if not self.token_ok(form.get("token")):
                    return self.forbidden("Token fehlt oder falsch")
                return self.save_setup(form)

            if not self.token_ok(self.headers.get("X-PV-Token")):
                return self.forbidden("Token fehlt oder falsch")
            if path == "/api/update":
                started = state.updater.start("manual")
                return self.send_json({"ok": started, "message": "Aktualisierung gestartet." if started else "Es läuft bereits eine Aktualisierung."},
                                      HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT)
            if path == "/api/shutdown":
                log.info("Beenden angefordert.")
                self.send_json({"ok": True, "message": "Der Server wird beendet."})
                threading.Thread(target=state.shutdown, daemon=True).start()
                return
            return self.test_alphaess(body)

        def save_setup(self, form: dict[str, str]) -> None:
            env = settings.build_env(form)
            try:
                settings.validate_env(env)
            except ConfigError as error:
                values = {name: form.get(name, "") for name in settings.FIELD_NAMES}
                return self.setup_form(values=values, error_text=settings.friendly_message(str(error)), error_fields=error.fields)
            try:
                settings.write_settings(env)
            except (OSError, ValueError) as error:
                return self.setup_form(values={n: form.get(n, "") for n in settings.FIELD_NAMES}, error_text=f"Einstellungen konnten nicht gespeichert werden: {error}")
            log.info("Einstellungen gespeichert.")
            state.updater.start("setup")
            self.redirect("/")

        def test_alphaess(self, body: bytes) -> None:
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except ValueError:
                return self.send_json({"ok": False, "message": "Ungültige Anfrage."}, HTTPStatus.BAD_REQUEST)
            secret = (data.get("app_secret") or "").strip() or settings.read_settings().get("ALPHAESS_APP_SECRET", "") or state.environment().get("ALPHAESS_APP_SECRET", "")
            result = asyncio.run(
                state.connection_check((data.get("app_id") or "").strip(), secret, (data.get("serial") or "").strip())
            )
            self.send_json({"ok": result.ok, "message": result.message, "systems": result.systems, "multiple": result.multiple})

    return Handler


# ------------------------------------------------------------------------------- Start


def create_server(host: str, port: int, state: AppState) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.daemon_threads = True
    return server


def already_running(port: int) -> bool:
    """Antwortet auf dem Port bereits ein PV-Tagesbericht-Server?"""
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/api/ping")
        return json.loads(connection.getresponse().read().decode("utf-8")).get("app") == "pv-tagesbericht"
    except (OSError, ValueError):
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{paths.APP_NAME} serve",
        description="Lokaler Server: Bericht, Einrichtungsseite und tägliche Aktualisierung.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (Standard {DEFAULT_PORT}).")
    parser.add_argument("--host", default="127.0.0.1", help="Adresse (Standard 127.0.0.1 = nur dieser Rechner). Mit 0.0.0.0 ist nur der Bericht im Netz sichtbar; Einrichtung und Aktualisierung bleiben lokal.")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen.")
    parser.add_argument("--no-schedule", action="store_true", help="Keine automatische Aktualisierung (nur manuell).")
    parser.add_argument("--verbose", action="store_true", help="Ausführlichere Protokollierung.")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    setup_logging(paths.log_directory(), verbose=options.verbose)
    state = AppState(port=options.port, updater=Updater(StatusStore()))
    try:
        server = create_server(options.host, options.port, state)
    except OSError as error:
        if already_running(options.port):
            url = f"http://127.0.0.1:{options.port}/"
            log.info("PV-Tagesbericht läuft bereits: %s", url)
            if not options.no_browser:
                webbrowser.open(url)
            return 0
        message = f"Der Port {options.port} ist nicht verfügbar ({error}). Mit --port einen anderen Port wählen."
        log.error(message)
        show_message("PV-Tagesbericht", message)  # Fenster-EXE hat keine Konsole
        return 1

    state.shutdown = server.shutdown
    url = f"http://127.0.0.1:{options.port}/"
    log.info("PV-Tagesbericht läuft: %s", url)
    log.info("Dieses Fenster geöffnet lassen. Beenden mit Strg+C.")
    if options.host not in {"127.0.0.1", "localhost"}:
        log.warning("Der Bericht ist über %s im Netzwerk sichtbar (ohne Anmeldung). Einrichtung und Aktualisierung bleiben nur lokal erreichbar.", options.host)
    scheduler = None
    if not options.no_schedule:
        scheduler = Scheduler(state.updater, state.scheduler_settings)
        scheduler.start()
        scheduler.tick()  # beim Start sofort prüfen (Nachholen, erste Aktualisierung)
    if not options.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Beendet.")
    finally:
        if scheduler:
            scheduler.stop()
        state.updater.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
