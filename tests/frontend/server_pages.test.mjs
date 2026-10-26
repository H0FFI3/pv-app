// Tests der Seiten des Serve-Modus: Statusleiste im Bericht und Einrichtungsseite (jsdom, Netzwerk gestubbt).
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import test, { after } from "node:test";
import assert from "node:assert/strict";

const dir = process.env.PV_FIXTURE_DIR;
if (!dir) throw new Error("PV_FIXTURE_DIR fehlt (siehe tests/test_frontend.py)");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function scriptedFetch(handler) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url, init });
    const data = await handler(url, init, calls.length);
    return { ok: true, status: 200, json: async () => data };
  };
  impl.calls = calls;
  return impl;
}

const windows = [];
// Die Statusleiste fragt dauerhaft per Timer ab: Fenster am Ende schließen, sonst beendet sich Node nie.
after(() => windows.forEach((window) => window.close()));

function load(name, fetchImpl) {
  const navigations = [];
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => navigations.push(error.message));
  const dom = new JSDOM(fs.readFileSync(path.join(dir, `${name}.html`), "utf8"), {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "http://127.0.0.1:8765/",
    virtualConsole,
    beforeParse(window) {
      window.fetch = fetchImpl;
      window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => {}), set: (t, k, v) => ((t[k] = v), true) });
      window.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ width: 600, height: 250, left: 0, top: 0, right: 600, bottom: 250 });
      window.addEventListener("error", (event) => errors.push(event.message));
    },
  });
  windows.push(dom.window);
  return { window: dom.window, $: (selector) => dom.window.document.querySelector(selector), navigations, errors };
}

test("Statusleiste zeigt Stand und nächsten Termin, Bericht läuft unverändert", () => {
  const page = load("served", scriptedFetch(() => ({})));
  assert.deepEqual(page.errors, []);
  assert.equal(page.$("#pv-bar-text").textContent, "Zuletzt aktualisiert: 18.10.2026 06:02 · Nächste Aktualisierung: 19.10. um 06:00");
  assert.equal(page.$("#pv-bar-update").disabled, false);
  assert.equal(page.$("#pv-bar a").getAttribute("href"), "/setup");
  assert.ok(page.$("#energy-chart").pvChart, "der Bericht selbst wird gezeichnet");
});

test("Jetzt aktualisieren: POST mit Token, Fortschritt, danach Neuladen", async () => {
  const fetchImpl = scriptedFetch((url, init, n) => {
    if (url === "/api/update") return { ok: true };
    return n <= 2 ? { status: { running: true, ok: null, text: "Aktualisierung läuft …" }, schedule: "" } : { status: { running: false, ok: true, text: "Zuletzt aktualisiert: jetzt" }, schedule: "" };
  });
  const page = load("served", fetchImpl);
  page.$("#pv-bar-update").click();
  assert.equal(page.$("#pv-bar-update").disabled, true);
  await sleep(150);
  const update = fetchImpl.calls.find((call) => call.url === "/api/update");
  assert.equal(update.init.method, "POST");
  assert.equal(update.init.headers["X-PV-Token"], "TOKEN123");
  assert.equal(page.$("#pv-bar-text").textContent, "Aktualisierung läuft …");
  await sleep(2300); // nächste Abfrage nach 2 s: fertig -> Seite lädt neu
  assert.ok(page.navigations.some((message) => /navigation/i.test(message)), "Seite wurde neu geladen");
  const status = fetchImpl.calls.find((call) => call.url === "/api/status");
  assert.equal(status.init.headers["X-PV-Token"], "TOKEN123");
});

test("fehlgeschlagene Aktualisierung wird hervorgehoben", async () => {
  const fetchImpl = scriptedFetch(() => ({ status: { running: false, ok: false, text: "Letzte Aktualisierung fehlgeschlagen: kein Netz" }, schedule: "" }));
  const page = load("served", fetchImpl);
  page.$("#pv-bar-update").click();
  await sleep(100);
  assert.ok(page.$("#pv-bar").classList.contains("bad"));
  assert.match(page.$("#pv-bar-text").textContent, /kein Netz/);
  assert.equal(page.$("#pv-bar-update").disabled, false);
});

test("Beenden braucht zwei Klicks, ruft die API mit Token auf und hört auf zu fragen", async () => {
  const fetchImpl = scriptedFetch(() => ({ ok: true, status: { running: false, ok: true, text: "x" }, schedule: "" }));
  const page = load("served", fetchImpl);
  const quit = page.$("#pv-bar-quit");
  quit.click();
  assert.equal(quit.textContent, "Wirklich beenden?");
  assert.ok(!fetchImpl.calls.some((call) => call.url === "/api/shutdown"), "erster Klick beendet noch nichts");
  quit.click();
  await sleep(50);
  const call = fetchImpl.calls.find((c) => c.url === "/api/shutdown");
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.headers["X-PV-Token"], "TOKEN123");
  assert.match(page.$("#pv-bar-text").textContent, /Server beendet/);
  assert.equal(page.$("#pv-bar-update").disabled, true);
  const before = fetchImpl.calls.length;
  await sleep(100);
  assert.equal(fetchImpl.calls.length, before, "keine weiteren Abfragen nach dem Beenden");
});

// ------------------------------------------------------------------------ Einrichtungsseite

function fill(page, values) {
  for (const [name, value] of Object.entries(values)) page.$(`[name="${name}"]`).value = value;
}

test("Einrichtungsseite: Felder, gespeichertes Secret wird nicht angezeigt", () => {
  const page = load("setup", scriptedFetch(() => ({})));
  assert.deepEqual(page.errors, []);
  assert.equal(page.$('[name="token"]').value, "TOKEN123");
  assert.equal(page.$('[name="ALPHAESS_APP_SECRET"]').value, "");
  assert.equal(page.$('[name="ALPHAESS_APP_SECRET"]').placeholder, "(gespeichert)");
  assert.equal(page.$('[name="ALPHAESS_APP_SECRET"]').type, "password");
  assert.equal(page.$('[name="TIMEZONE"]').value, "Europe/Berlin");
  assert.equal(page.$('[name="AZIMUTH_WEST_DEG"]').value, "270");
  assert.equal(page.$("form").getAttribute("method"), "post");
});

test("Verbindung testen: sendet Zugangsdaten mit Token und vergleicht die kWp", async () => {
  const fetchImpl = scriptedFetch(() => ({ ok: true, message: "Verbindung erfolgreich: 1 Anlage(n) gefunden.", systems: [{ model: "T6", pv_kwp: 7.0, inverter_kw: 6.0, battery_kwh: 7.68 }] }));
  const page = load("setup", fetchImpl);
  fill(page, { ALPHAESS_APP_ID: "alpha123", ALPHAESS_APP_SECRET: "geheim", MODULES_EAST: "10", MODULES_WEST: "10", MODULE_POWER_WP: "430" });
  page.$("#test-button").click();
  await sleep(50);
  const call = fetchImpl.calls[0];
  assert.equal(call.url, "/api/test-alphaess");
  assert.equal(call.init.headers["X-PV-Token"], "TOKEN123");
  assert.deepEqual(JSON.parse(call.init.body), { app_id: "alpha123", app_secret: "geheim", serial: "" });
  const text = page.$("#test-result").textContent;
  assert.match(text, /Verbindung erfolgreich/);
  assert.match(text, /T6 · 7 kWp PV · Wechselrichter 6 kW · Akku 7.68 kWh/);
  assert.match(text, /ergeben 8,60 kWp, AlphaESS meldet 7 kWp\. Bitte Modulanzahl/);
  assert.ok(page.$("#test-result").classList.contains("ok"));
  assert.equal(page.$("#test-button").disabled, false);
});

test("Verbindung testen: passende Modulangaben werden bestätigt, Fehler hervorgehoben", async () => {
  const good = load("setup", scriptedFetch(() => ({ ok: true, message: "ok", systems: [{ model: "T6", pv_kwp: 8.6 }] })));
  fill(good, { MODULES_EAST: "10", MODULES_WEST: "10", MODULE_POWER_WP: "430" });
  good.$("#test-button").click();
  await sleep(50);
  assert.match(good.$("#test-result").textContent, /passen zur Anlage/);

  const bad = load("setup", scriptedFetch(() => ({ ok: false, message: "Anmeldung bei AlphaESS fehlgeschlagen: 401" })));
  bad.$("#test-button").click();
  await sleep(50);
  assert.ok(bad.$("#test-result").classList.contains("error"));
  assert.match(bad.$("#test-result").textContent, /401/);
});

test("Verbindung testen: Netzwerkfehler im Browser werden angezeigt", async () => {
  const page = load("setup", async () => { throw new Error("Failed to fetch"); });
  page.$("#test-button").click();
  await sleep(50);
  assert.match(page.$("#test-result").textContent, /Die Prüfung ist fehlgeschlagen: Error: Failed to fetch/);
  assert.equal(page.$("#test-button").disabled, false);
});
