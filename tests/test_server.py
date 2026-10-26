import http.client
import json
import os
import re
import sys
import threading
from urllib.parse import urlencode

import pytest

from pvapp import alphaess_client, paths, server, settings, storage
from pvapp.paths import settings_path
from pvapp.updater import StatusStore, Updater
from test_settings import FORM
from test_updater import FakePopen


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for module in ("pvapp.config", "pvapp.settings", "pvapp.server"):
        monkeypatch.setattr(f"{module}.env_file_candidates", lambda: [settings_path()])
    FakePopen.instances = []
    status = StatusStore(tmp_path / "status.json")
    updater = Updater(status, command=lambda: ["fake"], popen=FakePopen, log_path=lambda: tmp_path / "last.log")
    checks = []

    async def fake_check(app_id, secret, serial=""):
        checks.append((app_id, secret, serial))
        return alphaess_client.ConnectionResult(True, "Verbindung erfolgreich: 1 Anlage(n) gefunden.", [{"model": "T6", "pv_kwp": 7.0, "inverter_kw": 6.0, "battery_kwh": 7.68}])

    state = server.AppState(port=0, updater=updater, base_env={}, connection_check=fake_check)
    httpd = server.create_server("127.0.0.1", 0, state)
    state.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    class Web:
        pass

    web = Web()
    web.state, web.checks, web.status, web.updater, web.port = state, checks, status, updater, state.port

    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", state.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def post_form(form, token=None, headers=None, path="/setup"):
        data = {**form, "token": state.token if token is None else token}
        return request("POST", path, urlencode(data), {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})

    def post_api(path, payload=None, token=None, headers=None):
        merged = {"Content-Type": "application/json", "X-PV-Token": state.token if token is None else token, **(headers or {})}
        return request("POST", path, json.dumps(payload or {}), merged)

    web.request, web.post_form, web.post_api = request, post_form, post_api
    web.configure = lambda **over: settings.write_settings(settings.build_env({**FORM, **over}))
    yield web
    httpd.shutdown()
    httpd.server_close()


def saved(name="ALPHAESS_APP_SECRET"):
    return settings.read_settings().get(name)


# ------------------------------------------------------------------------ Einrichtung


def test_unconfigured_dashboard_redirects_to_setup(web):
    status, headers, _ = web.request("GET", "/")
    assert status == 303 and headers["Location"] == "/setup"


def test_setup_page_has_token_and_no_secret(web):
    web.configure(ALPHAESS_APP_SECRET="topsecret")
    status, _, body = web.request("GET", "/setup")
    assert status == 200 and web.state.token in body
    assert "topsecret" not in body and "(gespeichert)" in body
    assert 'value="21.5"' in body and 'value="14"' in body  # Prozentwerte werden als Prozent angezeigt


def test_valid_setup_saves_converted_values_and_starts_the_first_update(web):
    status, headers, _ = web.post_form(FORM)
    assert status == 303 and headers["Location"] == "/"
    stored = settings.read_settings()
    assert stored["MODULE_EFFICIENCY"] == "0.215" and stored["POWER_TEMPERATURE_COEFFICIENT"] == "-0.003"
    assert stored["ALPHAESS_APP_SECRET"] == "geheim" and stored["UPDATE_TIME"] == "06:30"
    assert len(FakePopen.instances) == 1 and web.status.read()["last_trigger"] == "setup"
    assert web.request("GET", "/")[0] == 200  # jetzt eingerichtet


def test_changed_settings_apply_without_restarting_the_server(web):
    web.post_form(FORM)
    assert web.state.config()[0].latitude == 48.78
    web.post_form({**FORM, "LATITUDE": "50.5"})
    assert web.state.config()[0].latitude == 50.5


def test_invalid_setup_shows_labels_keeps_input_and_writes_nothing(web):
    status, _, body = web.post_form({**FORM, "MODULE_EFFICIENCY": "150"})
    assert status == 422 and "Modulwirkungsgrad" in body and "MODULE_EFFICIENCY" not in body.split("<form")[0]
    assert re.search(r'name="MODULE_EFFICIENCY"[^>]*class="invalid"', body) or 'class="invalid"' in body
    assert 'value="150"' in body  # Eingabe bleibt erhalten
    assert "geheim" not in body
    assert not settings_path().exists() and not FakePopen.instances


def test_blank_secret_keeps_the_stored_one_and_custom_keys_survive(web):
    web.post_form(FORM)
    settings.write_settings({**settings.read_settings(), "CUSTOM_KEY": "bleibt"})
    web.post_form({**FORM, "ALPHAESS_APP_SECRET": "", "LATITUDE": "50"})
    assert saved() == "geheim" and saved("CUSTOM_KEY") == "bleibt" and saved("LATITUDE") == "50"


# -------------------------------------------------------------------------- Sicherheit


def test_post_without_or_with_wrong_token_is_rejected(web):
    assert web.post_form(FORM, token="falsch")[0] == 403
    assert web.request("POST", "/setup", urlencode(FORM), {"Content-Type": "application/x-www-form-urlencoded"})[0] == 403
    assert web.post_api("/api/update", token="falsch")[0] == 403
    assert not settings_path().exists() and not FakePopen.instances


def test_foreign_origin_is_rejected(web):
    assert web.post_form(FORM, headers={"Origin": "https://boese.example"})[0] == 403
    assert web.post_api("/api/update", headers={"Origin": "http://evil.example"})[0] == 403
    assert web.post_form(FORM, headers={"Origin": f"http://127.0.0.1:{web.port}"})[0] == 303


def test_foreign_host_header_is_rejected_dns_rebinding(web):
    host = {"Host": f"boese.example:{web.port}"}
    assert web.request("GET", "/setup", headers=host)[0] == 403
    assert web.post_form(FORM, headers=host)[0] == 403
    assert web.request("GET", "/api/status", headers={**host, "X-PV-Token": web.state.token})[0] == 403
    assert not settings_path().exists()
    assert web.request("GET", "/setup", headers={"Host": f"localhost:{web.port}"})[0] == 200


def test_non_local_clients_cannot_reach_setup_or_api(web, monkeypatch):
    web.configure()
    monkeypatch.setattr(server, "LOOPBACK_ADDRESSES", set())  # als wäre der Client ein anderes Gerät
    assert web.request("GET", "/setup")[0] == 403
    assert web.post_form(FORM)[0] == 403 and web.post_api("/api/update")[0] == 403
    assert web.post_api("/api/test-alphaess", {"app_id": "x"})[0] == 403
    assert web.request("GET", "/api/status", headers={"X-PV-Token": web.state.token})[0] == 403
    status, _, body = web.request("GET", "/")  # der Bericht selbst ist lesbar, aber ohne Bedienleiste
    assert status == 200 and "pv-bar" not in body and web.state.token not in body


def test_security_headers_on_all_pages(web):
    web.configure()
    for path in ("/", "/setup", "/api/ping"):
        _, headers, _ = web.request("GET", path)
        assert headers["X-Content-Type-Options"] == "nosniff" and headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"] and "default-src 'none'" in headers["Content-Security-Policy"]


def test_oversized_bodies_and_unknown_routes(web):
    status, *_ = web.request("POST", "/setup", "x" * 70_000, {"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 413
    assert web.request("GET", "/gibt-es-nicht")[0] == 404 and web.request("POST", "/gibt-es-nicht")[0] == 404
    assert web.request("GET", "/api/ping")[2] == '{"app": "pv-tagesbericht"}'


def test_ping_identifies_the_app_for_the_second_start(web):
    assert server.already_running(web.port) is True
    assert server.already_running(1) is False


# ---------------------------------------------------------------------------- Bericht


def test_dashboard_shows_data_attribution_and_the_control_bar(web):
    web.configure()
    storage.save_payload({"records": [{"date": "2026-10-14", "actual_kwh": 12.5, "potential_kwh": 15, "sunshine_hours": 6}]}, paths.data_path())
    status, _, body = web.request("GET", "/")
    assert status == 200 and '"date":"2026-10-14"' in body
    assert 'href="https://open-meteo.com/"' in body and "Weather data by Open-Meteo.com" in body
    assert 'id="pv-bar"' in body and web.state.token in body and 'href="/setup"' in body


# ----------------------------------------------------------------------------- API


def test_update_api_runs_one_update_and_reports_the_result(web):
    web.configure()
    status, _, body = web.post_api("/api/update")
    assert status == 202 and json.loads(body)["ok"] is True and len(FakePopen.instances) == 1
    assert web.post_api("/api/update")[0] == 409  # läuft bereits

    token = {"X-PV-Token": web.state.token}
    running = json.loads(web.request("GET", "/api/status", headers=token)[2])
    assert running["status"]["running"] is True and running["status"]["text"] == "Aktualisierung läuft …"
    FakePopen.instances[0].finish(0)
    done = json.loads(web.request("GET", "/api/status", headers=token)[2])
    assert done["status"] == {"running": False, "ok": True, "text": done["status"]["text"]} and "Zuletzt aktualisiert" in done["status"]["text"]
    assert web.request("GET", "/api/status")[0] == 403  # ohne Token


def test_connection_test_uses_the_stored_secret_and_never_returns_it(web):
    web.configure(ALPHAESS_APP_SECRET="gespeichert-123")
    status, _, body = web.post_api("/api/test-alphaess", {"app_id": "alpha123", "app_secret": "", "serial": ""})
    data = json.loads(body)
    assert status == 200 and data["ok"] and data["systems"][0]["pv_kwp"] == 7.0
    assert web.checks == [("alpha123", "gespeichert-123", "")]
    assert "gespeichert-123" not in body
    web.post_api("/api/test-alphaess", {"app_id": "a", "app_secret": "neu", "serial": "SN"})
    assert web.checks[-1] == ("a", "neu", "SN")
    assert web.post_api("/api/test-alphaess", token="falsch")[0] == 403
    assert web.request("POST", "/api/test-alphaess", "kein json", {"Content-Type": "application/json", "X-PV-Token": web.state.token})[0] == 400


def test_secrets_added_by_the_server_are_masked_in_logs(web):
    from pvapp.logging_setup import REDACTOR

    web.configure(ALPHAESS_APP_SECRET="mask-me-please")
    web.state.config()
    assert REDACTOR.redact("fehler mit mask-me-please") == "fehler mit ***"


# ------------------------------------------------------------- AlphaESS-Verbindungsprüfung


class Client:
    def __init__(self, systems=None, error=None):
        self.systems, self.error, self.closed = systems, error, False

    async def getESSList(self):
        if self.error:
            raise self.error
        return self.systems

    async def close(self):
        self.closed = True


def check(client, serial="", app_id="idid1234", secret="secret1234"):
    import asyncio

    return asyncio.run(alphaess_client.check_connection(app_id, secret, serial, client_factory=lambda *a: client))


def test_connection_check_summarizes_systems_without_serials():
    client = Client([{"sysSn": "GEHEIM-SN", "minv": "T6", "popv": 7.0, "poinv": 6.0, "cobat": 7.68}])
    result = check(client)
    assert result.ok and result.systems == [{"model": "T6", "pv_kwp": 7.0, "inverter_kw": 6.0, "battery_kwh": 7.68}]
    assert "GEHEIM-SN" not in json.dumps(result.systems) and client.closed


def test_connection_check_failures_are_friendly_and_mask_credentials():
    result = check(Client(error=RuntimeError("401 für secret1234 und idid1234")))
    assert not result.ok and "secret1234" not in result.message and "idid1234" not in result.message and "fehlgeschlagen" in result.message
    assert not check(Client([])).ok
    assert not alphaess_client.asyncio.run(alphaess_client.check_connection("", "")).ok


def test_multiple_systems_need_a_serial():
    two = [{"sysSn": "A", "minv": "x"}, {"sysSn": "B", "minv": "y"}]
    result = check(Client(two))
    assert result.ok and result.multiple and "Seriennummer" in result.message
    assert check(Client(two), serial="B").ok and not check(Client(two), serial="Z").ok


# ---------------------------------------------------------------------- Leichtgewicht


def test_the_server_does_not_import_the_heavy_libraries():
    import subprocess

    code = "import sys, pvapp.server; print([m for m in ('pandas','pvlib','numpy','alphaess','aiohttp','requests') if m in sys.modules])"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert result.stdout.strip() == "[]", result.stdout + result.stderr


def test_pv_app_dispatches_the_serve_command(monkeypatch):
    import importlib

    called = {}
    monkeypatch.setattr(server, "main", lambda argv: called.setdefault("argv", argv) and 0 or 0)
    monkeypatch.setattr(sys, "argv", ["pv_app.py", "serve", "--port", "9999"])
    module = importlib.import_module("pv_app")
    assert module.entry() == 0 and called["argv"] == ["--port", "9999"]


def test_main_exits_cleanly_if_the_port_is_already_served(web, monkeypatch):
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    assert server.main(["--port", str(web.port)]) == 0
    assert opened == [f"http://127.0.0.1:{web.port}/"]


# ---------------------------------------------------------------------------- Beenden


def test_shutdown_api_needs_token_and_local_access(web, monkeypatch):
    import time

    calls = []
    web.state.shutdown = lambda: calls.append(1)
    assert web.post_api("/api/shutdown", token="falsch")[0] == 403
    assert web.post_api("/api/shutdown", headers={"Origin": "http://evil.example"})[0] == 403
    monkeypatch.setattr(server, "LOOPBACK_ADDRESSES", set())
    assert web.post_api("/api/shutdown")[0] == 403 and calls == []
    monkeypatch.undo()
    status, _, body = web.post_api("/api/shutdown")
    assert status == 200 and json.loads(body)["ok"] is True
    for _ in range(50):
        if calls:
            break
        time.sleep(0.05)
    assert calls == [1]
