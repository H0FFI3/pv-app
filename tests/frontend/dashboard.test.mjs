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
  const fills = [];
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "file:///bericht.html",
    beforeParse(window) {
      const context = new Proxy(
        {},
        {
          get: (target, key) => {
            if (key === "fillRect") return (...args) => fills.push({ style: target.fillStyle, args });
            return key in target ? target[key] : () => {};
          },
          set: (target, key, value) => ((target[key] = value), true),
        },
      );
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
  return { window, document: window.document, $, errors, downloads, fills, data: window.PV_DAILY_DATA.records };
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
  assert.equal(page.$("#history-body").children[0].children.length, 6);
  const rows = [...page.$("#summary-body").children].map((row) => [...row.children].map((cell) => cell.textContent));
  assert.equal(rows.length, 3);
  assert.equal(rows[0].length, 7);
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
  assert.ok(text.startsWith("\ufeffdate;actual_kwh;potential_kwh;sunshine_hours;curtailed_hours;curtailed_kwh\r\n"));
  const lines = text.split("\r\n");
  assert.equal(lines.length, page.data.length + 1);
  const first = lines[1].split(";");
  assert.equal(first.length, 6);
  assert.match(first[0], /^\d{4}-\d{2}-\d{2}$/);
  for (const value of first.slice(1, 4)) assert.match(value, /^\d+(,\d+)?$/, `Dezimalkomma erwartet: ${value}`);
  const flagged = lines.slice(1).map((line) => line.split(";")).find((cells) => cells[4] && cells[4] !== "0");
  assert.ok(flagged, "Tag mit abgeregelten Stunden im Export");
  assert.match(flagged[4], /^\d+$/);
  assert.match(flagged[5], /^\d+(,\d+)?$/);
  assert.ok(!/\d\.\d/.test(lines.slice(1).join("")), "kein Dezimalpunkt in den Zahlen");
});


// ------------------------------------------------------------------------ Abregelung

const BAND = "rgba(220, 123, 82, 0.2)";

function selectDay(page, date) {
  const select = page.$("#hourly-date");
  select.value = date;
  select.dispatchEvent(new page.window.Event("change"));
}

test("Abgeregelte Stunden werden im Stundendiagramm hinterlegt, Tooltip nennt Grund und Signale", () => {
  const page = load("many");
  const day = page.data.filter((item) => item.curtailment?.hours > 0).at(-1);
  assert.ok(day, "Fixture enthält einen abgeregelten Tag");
  assert.equal(day.curtailment.hours, 4);
  selectDay(page, day.date);

  const chart = page.$("#hourly-chart").pvChart;
  const flagged = Array.from(chart.data.filter((item) => item.curtailed).map((item) => item.hour)); // Array.from: jsdom-Realm -> Node-Realm
  assert.deepEqual(flagged, ["11:00", "12:00", "13:00", "14:00"]);

  page.fills.length = 0;
  selectDay(page, day.date); // neu zeichnen, Aufrufe mitschneiden
  const bands = page.fills.filter((fill) => fill.style === BAND);
  assert.equal(bands.length, 4, "ein Band je abgeregelter Stunde");
  const slot = (600 - 42 - 10) / chart.data.length;
  assert.ok(Math.abs(bands[0].args[2] - slot) < 1e-9, "Band füllt genau einen Stundenslot");

  const index = chart.data.findIndex((item) => item.hour === "12:00");
  const canvas = page.$("#hourly-chart");
  pointer(page.window, canvas, 42 + slot * (index + 0.5));
  const text = page.$("#hourly-tooltip").textContent;
  assert.match(text, /Abregelung wahrscheinlich: Einspeiselimit erreicht/);
  assert.match(text, /Akku 100,0 %/);
  assert.match(text, /Netz 4000 W/);

  assert.equal(page.$("#hourly-curtailed-legend").hidden, false);
  assert.match(page.$("#hourly-curtailment-note").textContent, /4 Stunden mit wahrscheinlicher Abregelung, bis zu \d+,\d kWh/);
});

test("Tage ohne Abregelung und Tage ohne Signale werden unterschiedlich beschrieben", () => {
  const page = load("many");
  const clean = page.data.filter((item) => item.curtailment && item.curtailment.hours === 0).at(-1);
  selectDay(page, clean.date);
  assert.match(page.$("#hourly-curtailment-note").textContent, /Keine Abregelung erkannt/);
  assert.equal(page.$("#hourly-curtailed-legend").hidden, true);
  const noSignal = page.data.find((item) => item.hourly && !item.curtailment);
  assert.ok(noSignal, "Fixture enthält einen Tag mit Stundenprofil ohne Signale");
  selectDay(page, noSignal.date);
  assert.match(page.$("#hourly-curtailment-note").textContent, /vor der Abregelungserkennung erfasst.*nachgeladen/);
  assert.deepEqual(page.errors, []);
});

test("abgefragt, aber ohne Akku-/Netzdaten: eigener Hinweistext statt Nachlade-Versprechen", () => {
  const page = load("no_signals");
  const day = page.data.find((item) => item.hourly);
  selectDay(page, day.date);
  const text = page.$("#hourly-curtailment-note").textContent;
  assert.match(text, /AlphaESS liefert für diesen Tag keine Akku- oder Netzdaten/);
  assert.doesNotMatch(text, /nachgeladen/);
});

test("Tabellen: Abregelung je Tag und Summe; unbekannt (-) ist nicht dasselbe wie keine", () => {
  const page = load("many");
  const rows = [...page.$("#history-body").children].map((row) => [...row.children].map((cell) => cell.textContent));
  const last = rows[0]; // jüngster Tag: heute-0, abgeregelt
  assert.match(last[5], /^4 h · \d+,\d kWh$/);
  assert.ok(rows.some((row) => row[5] === "keine"));
  assert.ok(rows.some((row) => row[5] === "-"), "Tage ohne Stundenprofil: nicht beurteilbar");
  const summary = [...page.$("#summary-body").children].map((row) => [...row.children].map((cell) => cell.textContent));
  const totalHours = page.data.reduce((sum, item) => sum + (item.curtailment?.hours || 0), 0);
  assert.match(summary[2][6], new RegExp(`^${totalHours} h · `));
});

test("Tageszeit-Auswertung nimmt abgeregelte Stunden aus (kein vorgetäuschter Schatten)", () => {
  const page = load("many");
  const profile = page.$("#profile-chart").pvChart;
  const noon = profile.data.find((item) => item.hour === "12:00");
  // erwartetes Verhältnis nur aus nicht abgeregelten Tagen
  let actual = 0;
  let potential = 0;
  for (const item of page.data) {
    for (const point of item.hourly || []) {
      if (point.hour !== "12:00" || point.actual_kwh == null || point.curtailed) continue;
      actual += point.actual_kwh;
      potential += point.potential_kwh;
    }
  }
  assert.ok(Math.abs(noon.ratio_pct - (actual / potential) * 100) < 1e-9);
  assert.ok(noon.ratio_pct > 65, "abgeregelte 50-%-Stunden verfälschen den Mittelwert nicht");
  assert.match(page.$("#profile-note").textContent, /^\d+ als abgeregelt erkannte Stundenwerte/);
});

test("ohne Signale: nichts markiert, Tabellen zeigen -, keine Fehler", () => {
  const page = load("no_signals");
  assert.deepEqual(page.errors, []);
  assert.ok(page.data.every((item) => !item.curtailment));
  const cells = [...page.$("#history-body").children].map((row) => row.children[5].textContent);
  assert.ok(cells.every((text) => text === "-"));
  assert.equal(page.$("#profile-note").textContent, "");
});
