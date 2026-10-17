// Frontend-Tests mit jsdom. Die HTML-Berichte werden von tests/test_frontend.py erzeugt (PV_FIXTURE_DIR),
// oder manuell: python tests/frontend/make_fixtures.py <Ordner>
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const dir = process.env.PV_FIXTURE_DIR;
if (!dir) throw new Error("PV_FIXTURE_DIR fehlt (siehe tests/test_frontend.py)");

function load(name, { width = 600, height = 250 } = {}) {
  const html = fs.readFileSync(path.join(dir, `${name}.html`), "utf8");
  const errors = [];
  const downloads = [];
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "file:///bericht.html",
    beforeParse(window) {
      const context = new Proxy({}, { get: (target, key) => (key in target ? target[key] : () => {}), set: (target, key, value) => ((target[key] = value), true) });
      window.HTMLCanvasElement.prototype.getContext = () => context;
      window.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ width, height, left: 0, top: 0, right: width, bottom: height });
      window.URL.createObjectURL = (blob) => (downloads.push({ blob }), "blob:test");
      window.URL.revokeObjectURL = () => {};
      window.HTMLAnchorElement.prototype.click = function () { downloads.at(-1).name = this.download; };
      window.addEventListener("error", (event) => errors.push(event.message));
    },
  });
  const { window } = dom;
  const $ = (selector) => window.document.querySelector(selector);
  return { window, document: window.document, $, errors, downloads, data: window.PV_DAILY_DATA.records };
}

const pointer = (window, canvas, x, y = 40) => canvas.dispatchEvent(new window.MouseEvent("pointermove", { clientX: x, clientY: y, bubbles: true }));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("leere Daten: keine Fehler, Diagramme versteckt, Export gesperrt", () => {
  const page = load("empty");
  assert.deepEqual(page.errors, []);
  assert.equal(page.$("#actual-value").textContent, "-");
  for (const id of ["#energy-chart", "#sunshine-chart", "#ratio-chart", "#hourly-chart", "#profile-chart"]) assert.equal(page.$(id).hidden, true, id);
  assert.equal(page.$("#export-button").disabled, true);
  assert.match(page.$("#summary-body").textContent, /Summen erscheinen/);
  assert.match(page.$("#profile-empty-text").textContent, /ab 5 Tagen/);
});

test("ein einzelner Tag wird ohne Fehler dargestellt", () => {
  const page = load("one");
  assert.deepEqual(page.errors, []);
  assert.equal(page.$("#energy-chart").pvChart.data.length, 1);
  assert.equal(page.$("#summary-body").children.length, 3);
});

test("Tabellen, Summen, Verhältnis und Wetterquelle", () => {
  const page = load("many");
  assert.deepEqual(page.errors, []);
  assert.equal(page.$("#history-body").children.length, 14);
  assert.equal(page.$("#history-body").children[0].children.length, 5);
  const rows = [...page.$("#summary-body").children].map((row) => [...row.children].map((cell) => cell.textContent));
  assert.equal(rows.length, 3);
  const all = page.data;
  assert.equal(rows[2][1], String(all.length));
  const actual = all.reduce((sum, item) => sum + item.actual_kwh, 0);
  const potential = all.reduce((sum, item) => sum + item.potential_kwh, 0);
  const fmt = (value) => new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value);
  assert.equal(rows[2][2], `${fmt(actual)} kWh`);
  assert.equal(rows[2][4], `${fmt((actual / potential) * 100)} %`);
  assert.equal(rows[0][1], "7");
  assert.match(page.$("#source-note").textContent, /Archiv.*Forecast|Forecast.*Archiv/);
  assert.match(page.$("#energy-chart").getAttribute("aria-label"), /30 Tage/);
});

test("Tooltip-Zuordnung: jedes Diagramm nutzt sein eigenes Tooltip (Fehler: Stundendiagramm nutzte das Sonnen-Tooltip)", () => {
  const page = load("many");
  const { window } = page;
  for (const [canvasId, tooltipId] of [["#energy-chart", "#energy-tooltip"], ["#sunshine-chart", "#sunshine-tooltip"], ["#ratio-chart", "#ratio-tooltip"], ["#hourly-chart", "#hourly-tooltip"], ["#profile-chart", "#profile-tooltip"]]) {
    for (const tip of ["#energy-tooltip", "#sunshine-tooltip", "#ratio-tooltip", "#hourly-tooltip", "#profile-tooltip"]) page.$(tip).hidden = true;
    pointer(window, page.$(canvasId), 300);
    assert.equal(page.$(tooltipId).hidden, false, `${canvasId} zeigt ${tooltipId}`);
    for (const other of ["#energy-tooltip", "#sunshine-tooltip", "#ratio-tooltip", "#hourly-tooltip", "#profile-tooltip"].filter((id) => id !== tooltipId)) {
      assert.equal(page.$(other).hidden, true, `${canvasId} darf ${other} nicht öffnen`);
    }
  }
  assert.match(page.$("#hourly-tooltip").textContent, /Ist-Ertrag/);
  pointer(window, page.$("#hourly-chart"), 5); // links außerhalb der Zeichenfläche
  assert.equal(page.$("#hourly-tooltip").hidden, true);
});

test("Tastatur: Pfeiltasten wechseln den Datenpunkt, Escape schließt", () => {
  const page = load("many");
  const { window } = page;
  const canvas = page.$("#energy-chart");
  const tooltip = page.$("#energy-tooltip");
  const key = (name) => canvas.dispatchEvent(new window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
  const last = canvas.pvChart.data.length - 1;

  key("ArrowLeft"); // erster Druck zeigt den jüngsten Tag
  assert.equal(tooltip.hidden, false);
  assert.equal(canvas.pvChart.activeIndex, last);
  key("ArrowLeft");
  assert.equal(canvas.pvChart.activeIndex, last - 1);
  key("Home");
  assert.equal(canvas.pvChart.activeIndex, 0);
  key("ArrowLeft");
  assert.equal(canvas.pvChart.activeIndex, 0); // nicht unter 0
  key("End");
  key("ArrowRight");
  assert.equal(canvas.pvChart.activeIndex, last); // nicht über das Ende
  key("Escape");
  assert.equal(tooltip.hidden, true);
  assert.equal(canvas.pvChart.activeIndex, null);
});

test("Größenänderung zeichnet nur neu: Auswahl und Tabellen bleiben unverändert", async () => {
  const page = load("many");
  const { window } = page;
  const select = page.$("#hourly-date");
  select.value = select.options[1].value;
  select.dispatchEvent(new window.Event("change"));
  const firstOption = select.options[0];
  const firstRow = page.$("#history-body").firstElementChild;
  const chartBefore = page.$("#energy-chart").pvChart;

  window.dispatchEvent(new window.Event("resize"));
  window.dispatchEvent(new window.Event("resize"));
  await sleep(300);

  assert.equal(select.options[0], firstOption, "Optionen dürfen nicht neu aufgebaut werden");
  assert.equal(select.value, select.options[1].value);
  assert.equal(page.$("#history-body").firstElementChild, firstRow);
  assert.notEqual(page.$("#energy-chart").pvChart, chartBefore, "Diagramm wurde neu gezeichnet");
  assert.deepEqual(page.errors, []);
});

test("Zeitraumwahl begrenzt die Diagramme", () => {
  const page = load("many");
  page.$('[data-range="7"]').click();
  assert.equal(page.$("#energy-chart").pvChart.data.length, 7);
  assert.equal(page.$("#ratio-chart").pvChart.data.length, 7);
  assert.equal(page.$('[data-range="7"]').getAttribute("aria-pressed"), "true");
  page.$('[data-range="all"]').click();
  assert.equal(page.$("#energy-chart").pvChart.data.length, page.data.length);
});

test("Tageszeit-Auswertung erscheint erst ab 5 Tagen mit Stundenprofil", () => {
  const few = load("few_hourly");
  assert.equal(few.$("#profile-chart").hidden, true);
  assert.match(few.$("#profile-empty-text").textContent, /aktuell 3/);
  const many = load("many");
  assert.equal(many.$("#profile-chart").hidden, false);
  const profile = many.$("#profile-chart").pvChart;
  assert.ok(profile.data.length > 0 && profile.data.every((item) => Number.isFinite(item.ratio_pct)));
  const hours = profile.data.map((item) => item.hour);
  assert.ok(hours.every((hour) => hour >= "07:00" && hour <= "17:00"), `Stunden ohne Potenzial werden ausgeblendet: ${hours}`);
  assert.ok(!hours.includes("06:00") && !hours.includes("18:00"));
});

test("Stundendiagramm verträgt fehlende Ist-Werte", () => {
  const page = load("many");
  assert.deepEqual(page.errors, []);
  const select = page.$("#hourly-date");
  for (const option of select.options) {
    select.value = option.value;
    select.dispatchEvent(new page.window.Event("change"));
  }
  assert.deepEqual(page.errors, []);
  assert.ok(page.data.some((item) => item.hourly?.some((point) => point.actual_kwh === null)));
});

test("CSV-Export: Dezimalkomma, Semikolon, BOM", async () => {
  const page = load("many");
  page.$("#export-button").click();
  assert.equal(page.downloads.length, 1);
  assert.equal(page.downloads[0].name, "pv-tagesbericht.csv");
  const bytes = new Uint8Array(
    await new Promise((resolve, reject) => {
      const reader = new page.window.FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsArrayBuffer(page.downloads[0].blob);
    }),
  );
  assert.deepEqual([...bytes.slice(0, 3)], [0xef, 0xbb, 0xbf], "UTF-8-BOM für Excel");
  const text = new TextDecoder("utf-8", { ignoreBOM: true }).decode(bytes);
  assert.ok(text.startsWith("\ufeffdate;actual_kwh;potential_kwh;sunshine_hours\r\n"));
  const lines = text.split("\r\n");
  assert.equal(lines.length, page.data.length + 1);
  const first = lines[1].split(";");
  assert.equal(first.length, 4);
  assert.match(first[0], /^\d{4}-\d{2}-\d{2}$/);
  for (const value of first.slice(1)) assert.match(value, /^\d+(,\d+)?$/, `Dezimalkomma erwartet: ${value}`);
  assert.ok(!/\d\.\d/.test(lines.slice(1).join("")), "kein Dezimalpunkt in den Zahlen");
});
