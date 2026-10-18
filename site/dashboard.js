"use strict";

const numberFormat = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const shortDateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", timeZone: "UTC" });

const MIN_DAYS_FOR_HOUR_PROFILE = 5;
const MIN_POTENTIAL_FOR_HOUR_PROFILE = 0.05; // kWh je Stunde über alle Tage; darunter ist das Verhältnis nur Rauschen
const GREEN = "#197459";
const SUN = "#e5ad38";
const ORANGE = "#dc7b52";
const CURTAILED_BAND = "rgba(220, 123, 82, 0.2)";
const CURTAILMENT_REASONS = { limit: "Einspeiselimit erreicht", battery: "Akku voll" };

let records = Array.isArray(window.PV_DAILY_DATA?.records) ? window.PV_DAILY_DATA.records : [];
let activeRange = 30;

const elements = {
  connection: document.querySelector("#connection-state"),
  connectionLabel: document.querySelector("#connection-label"),
  latestDate: document.querySelector("#latest-date"),
  actualValue: document.querySelector("#actual-value"),
  actualCaption: document.querySelector("#actual-caption"),
  potentialValue: document.querySelector("#potential-value"),
  potentialCaption: document.querySelector("#potential-caption"),
  sunshineValue: document.querySelector("#sunshine-value"),
  energyCanvas: document.querySelector("#energy-chart"),
  sunshineCanvas: document.querySelector("#sunshine-chart"),
  ratioCanvas: document.querySelector("#ratio-chart"),
  hourlyCanvas: document.querySelector("#hourly-chart"),
  profileCanvas: document.querySelector("#profile-chart"),
  hourlyEmpty: document.querySelector("#hourly-empty"),
  hourlySelect: document.querySelector("#hourly-date"),
  profileEmpty: document.querySelector("#profile-empty"),
  profileEmptyText: document.querySelector("#profile-empty-text"),
  profileNote: document.querySelector("#profile-note"),
  hourlyCurtailmentNote: document.querySelector("#hourly-curtailment-note"),
  hourlyCurtailedLegend: document.querySelector("#hourly-curtailed-legend"),
  energyEmpty: document.querySelector("#energy-empty"),
  sunshineEmpty: document.querySelector("#sunshine-empty"),
  ratioEmpty: document.querySelector("#ratio-empty"),
  historyBody: document.querySelector("#history-body"),
  historyCount: document.querySelector("#history-count"),
  summaryBody: document.querySelector("#summary-body"),
  sourceNote: document.querySelector("#source-note"),
  exportButton: document.querySelector("#export-button"),
};

/* ------------------------------------------------------------------ Hilfsfunktionen */

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "-";
  return Number.isFinite(Number(value)) ? numberFormat.format(Number(value)) : "-";
}

function formatDate(value) {
  return dateFormat.format(new Date(`${value}T12:00:00Z`));
}

function formatShortDate(value) {
  return shortDateFormat.format(new Date(`${value}T12:00:00Z`));
}

function addDays(isoDate, days) {
  const date = new Date(`${isoDate}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function ratioPercent(actual, potential) {
  const a = Number(actual);
  const p = Number(potential);
  return Number.isFinite(a) && Number.isFinite(p) && p > 0 ? (a / p) * 100 : null;
}

function sumOf(items, key) {
  return items.reduce((total, item) => total + (Number(item[key]) || 0), 0);
}

function visibleRecords() {
  return activeRange === "all" ? records : records.slice(-activeRange);
}

/* ------------------------------------------------------------ Tooltips (Maus, Touch, Tastatur) */

function tooltipFor(canvas) {
  return document.getElementById(canvas.dataset.tooltipId);
}

function hideTooltip(canvas) {
  const tooltip = tooltipFor(canvas);
  if (tooltip) tooltip.hidden = true;
  if (canvas.pvChart) canvas.pvChart.activeIndex = null;
}

function showTooltip(canvas, index, pointerY) {
  const chart = canvas.pvChart;
  const tooltip = tooltipFor(canvas);
  if (!chart || !tooltip || !chart.data.length) return;

  const bounds = canvas.getBoundingClientRect();
  const slotWidth = (bounds.width - chart.left - chart.right) / chart.data.length;
  const safeIndex = Math.max(0, Math.min(chart.data.length - 1, index));
  const item = chart.data[safeIndex];
  chart.activeIndex = safeIndex;

  const lines = [chart.title(item)];
  lines.push(`${chart.label}: ${formatNumber(item[chart.valueKey])} ${chart.unit}`);
  if (chart.comparisonKey) {
    lines.push(`${chart.comparisonLabel}: ${formatNumber(item[chart.comparisonKey])} ${chart.unit}`);
  }
  if (chart.extraLine) {
    const extra = chart.extraLine(item);
    if (extra) lines.push(extra);
  }
  tooltip.textContent = lines.join("\n");
  tooltip.hidden = false;

  const center = chart.left + slotWidth * (safeIndex + 0.5);
  const halfTooltip = tooltip.offsetWidth / 2;
  const left = Math.max(halfTooltip + 6, Math.min(bounds.width - halfTooltip - 6, center));
  const y = Math.max(tooltip.offsetHeight + 8, pointerY ?? bounds.height * 0.45);
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${y}px`;
}

function bindChartInteraction(canvas) {
  if (canvas.dataset.interactionBound) return;
  canvas.dataset.interactionBound = "true";

  canvas.addEventListener("pointermove", (event) => {
    const chart = canvas.pvChart;
    if (!chart?.data.length) return hideTooltip(canvas);
    const bounds = canvas.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    if (x < chart.left || x > bounds.width - chart.right) return hideTooltip(canvas);
    const slotWidth = (bounds.width - chart.left - chart.right) / chart.data.length;
    showTooltip(canvas, Math.floor((x - chart.left) / slotWidth), event.clientY - bounds.top - 10);
  });
  canvas.addEventListener("pointerleave", () => hideTooltip(canvas));
  canvas.addEventListener("blur", () => hideTooltip(canvas));
  canvas.addEventListener("focus", () => {
    const chart = canvas.pvChart;
    if (!chart?.data.length) return;
    let keyboardFocus = true;
    try {
      keyboardFocus = canvas.matches(":focus-visible");
    } catch (error) {
      keyboardFocus = true;
    }
    if (keyboardFocus) showTooltip(canvas, chart.data.length - 1);
  });
  canvas.addEventListener("keydown", (event) => {
    const chart = canvas.pvChart;
    if (!chart?.data.length) return;
    const last = chart.data.length - 1;
    let index = chart.activeIndex ?? last; // erster Tastendruck: zuerst den letzten (jüngsten) Wert zeigen
    if (event.key === "ArrowLeft") index = chart.activeIndex === null ? last : index - 1;
    else if (event.key === "ArrowRight") index = chart.activeIndex === null ? last : index + 1;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = last;
    else if (event.key === "Escape") return hideTooltip(canvas);
    else return;
    event.preventDefault();
    showTooltip(canvas, index);
  });
}

/* ---------------------------------------------------------------------------- Diagramm */

function drawChart(canvas, data, valueKey, color, comparisonKey = null, options = {}) {
  if (!canvas) return;
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height || !data.length) return;

  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(bounds.width * ratio);
  canvas.height = Math.round(bounds.height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, bounds.width, bounds.height);

  const left = 42;
  const right = 10;
  const top = 10;
  const bottom = 28;
  const plotWidth = bounds.width - left - right;
  const plotHeight = bounds.height - top - bottom;

  hideTooltip(canvas); // ein neu gezeichnetes Diagramm hat keinen gültigen Tooltip-Bezug mehr
  canvas.pvChart = {
    data,
    valueKey,
    comparisonKey,
    label: options.label || (valueKey === "sunshine_hours" ? "Sonne" : "Ist-Ertrag"),
    unit: options.unit || (valueKey === "sunshine_hours" ? "h" : "kWh"),
    comparisonLabel: options.comparisonLabel || "Potentieller Ertrag",
    title: options.title || ((item) => formatDate(item.date)),
    extraLine: options.extraLine || null,
    left,
    right,
    activeIndex: null,
  };
  bindChartInteraction(canvas);

  const values = data.flatMap((item) => [Number(item[valueKey]), comparisonKey ? Number(item[comparisonKey]) : 0]);
  const referenceLine = options.referenceLine ?? null;
  const maximum = Math.max(options.minimumMaximum ?? 1, referenceLine ?? 0, ...values.filter(Number.isFinite)) * 1.12;
  const slotWidth = plotWidth / data.length;
  const barWidth = Math.max(2, Math.min(18, slotWidth * (comparisonKey ? 0.5 : 0.62)));
  const yFor = (value) => top + plotHeight - (Math.max(0, Number(value)) / maximum) * plotHeight;

  context.font = "10px Trebuchet MS, sans-serif";
  context.textAlign = "right";
  context.textBaseline = "middle";
  for (let tick = 0; tick <= 4; tick += 1) {
    const y = top + (plotHeight * tick) / 4;
    const value = maximum * (1 - tick / 4);
    context.strokeStyle = "#e8eeea";
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(bounds.width - right, y);
    context.stroke();
    context.fillStyle = "#687872";
    context.fillText(formatNumber(value), left - 7, y);
  }

  if (options.highlight) {
    data.forEach((item, index) => {
      const band = options.highlight(item);
      if (!band) return;
      context.fillStyle = band;
      context.fillRect(left + slotWidth * index, top, slotWidth, plotHeight);
    });
  }

  data.forEach((item, index) => {
    const raw = item[valueKey];
    if (raw === null || raw === undefined || !Number.isFinite(Number(raw))) return;
    const center = left + slotWidth * (index + 0.5);
    const y = yFor(raw);
    context.fillStyle = color;
    context.fillRect(center - barWidth / 2, y, barWidth, top + plotHeight - y);
  });

  if (referenceLine !== null) {
    context.save();
    context.strokeStyle = ORANGE;
    context.lineWidth = 1.5;
    context.setLineDash([5, 4]);
    context.beginPath();
    context.moveTo(left, yFor(referenceLine));
    context.lineTo(bounds.width - right, yFor(referenceLine));
    context.stroke();
    context.restore();
  }

  if (comparisonKey) {
    context.strokeStyle = ORANGE;
    context.lineWidth = 2;
    context.beginPath();
    data.forEach((item, index) => {
      const x = left + slotWidth * (index + 0.5);
      const y = yFor(item[comparisonKey]);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
  }

  const xLabel = options.xLabel || ((item) => formatShortDate(item.date));
  const labelStride = Math.max(1, Math.ceil(data.length / 6));
  context.fillStyle = "#687872";
  context.textAlign = "center";
  context.textBaseline = "top";
  data.forEach((item, index) => {
    if (index % labelStride !== 0 && index !== data.length - 1) return;
    context.fillText(xLabel(item), left + slotWidth * (index + 0.5), top + plotHeight + 8);
  });
}

/* ------------------------------------------------------------------------------ Anzeige */

function setEmptyState(hasData) {
  elements.energyEmpty.hidden = hasData;
  elements.sunshineEmpty.hidden = hasData;
  elements.ratioEmpty.hidden = hasData;
  elements.energyCanvas.hidden = !hasData;
  elements.sunshineCanvas.hidden = !hasData;
  elements.ratioCanvas.hidden = !hasData;
}

function renderMetrics() {
  const latest = records.at(-1);
  if (!latest) {
    elements.actualValue.textContent = "-";
    elements.potentialValue.textContent = "-";
    elements.sunshineValue.textContent = "-";
    elements.latestDate.textContent = "Noch keine Daten";
    elements.actualCaption.textContent = "AlphaESS Tageswert";
    elements.potentialCaption.textContent = "Wetterbasiertes PV-Modell";
    return;
  }
  const difference = Number(latest.potential_kwh) - Number(latest.actual_kwh);
  const achieved = ratioPercent(latest.actual_kwh, latest.potential_kwh);
  elements.actualValue.textContent = formatNumber(latest.actual_kwh);
  elements.potentialValue.textContent = formatNumber(latest.potential_kwh);
  elements.sunshineValue.textContent = formatNumber(latest.sunshine_hours);
  elements.latestDate.textContent = formatDate(latest.date);
  elements.actualCaption.textContent = achieved === null ? "AlphaESS Tageswert" : `${formatNumber(achieved)} % des Potenzials`;
  elements.potentialCaption.textContent = `Abstand zum Ist: ${formatNumber(difference)} kWh`;
}

function appendRow(body, values) {
  const row = document.createElement("tr");
  values.forEach((value) => {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.append(cell);
  });
  body.append(row);
}

function appendEmptyRow(body, columns, text) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = columns;
  cell.className = "table-empty";
  cell.textContent = text;
  row.append(cell);
  body.append(row);
}

function curtailmentText(curtailment) {
  if (!curtailment) return "-";
  if (!curtailment.hours) return "keine";
  return `${curtailment.hours} h · ${formatNumber(curtailment.lost_kwh)} kWh`;
}

function combineCurtailment(items) {
  const checked = items.filter((item) => item.curtailment);
  if (!checked.length) return null;
  return {
    hours: checked.reduce((total, item) => total + item.curtailment.hours, 0),
    lost_kwh: checked.reduce((total, item) => total + item.curtailment.lost_kwh, 0),
  };
}

function hourlyTooltipExtra(item) {
  const lines = [];
  if (item.curtailed) lines.push(`Abregelung wahrscheinlich: ${CURTAILMENT_REASONS[item.curtailed] || "erkannt"}`);
  const signals = [];
  if (Number.isFinite(item.soc_pct)) signals.push(`Akku ${formatNumber(item.soc_pct)} %`);
  if (Number.isFinite(item.feed_in_w)) signals.push(`Netz ${Math.round(item.feed_in_w)} W`);
  if (signals.length) lines.push(signals.join(" · "));
  return lines.join("\n");
}

function percentText(value) {
  return value === null ? "-" : `${formatNumber(value)} %`;
}

function renderHistory(data) {
  elements.historyBody.replaceChildren();
  elements.historyCount.textContent = `${records.length} ${records.length === 1 ? "Tag" : "Tage"}`;
  if (!data.length) {
    appendEmptyRow(elements.historyBody, 6, "Die Tageshistorie wird nach dem ersten Lauf hier angezeigt.");
    return;
  }
  data
    .slice()
    .reverse()
    .slice(0, 14)
    .forEach((item) =>
      appendRow(elements.historyBody, [
        formatDate(item.date),
        `${formatNumber(item.actual_kwh)} kWh`,
        `${formatNumber(item.potential_kwh)} kWh`,
        `${formatNumber(item.sunshine_hours)} h`,
        percentText(ratioPercent(item.actual_kwh, item.potential_kwh)),
        curtailmentText(item.curtailment),
      ]),
    );
}

function summaryRows() {
  const latest = records.at(-1);
  if (!latest) return [];
  const month = latest.date.slice(0, 7);
  const weekStart = addDays(latest.date, -6);
  return [
    { label: "Letzte 7 Tage", items: records.filter((item) => item.date >= weekStart) },
    { label: `Laufender Monat (${latest.date.slice(5, 7)}/${latest.date.slice(0, 4)})`, items: records.filter((item) => item.date.startsWith(month)) },
    { label: "Gesamter Zeitraum", items: records },
  ];
}

function renderSummary() {
  elements.summaryBody.replaceChildren();
  const rows = summaryRows();
  if (!rows.length) {
    appendEmptyRow(elements.summaryBody, 7, "Summen erscheinen nach dem ersten Datensatz.");
    return;
  }
  rows.forEach(({ label, items }) => {
    const actual = sumOf(items, "actual_kwh");
    const potential = sumOf(items, "potential_kwh");
    appendRow(elements.summaryBody, [
      label,
      String(items.length),
      `${formatNumber(actual)} kWh`,
      `${formatNumber(potential)} kWh`,
      percentText(ratioPercent(actual, potential)),
      `${formatNumber(sumOf(items, "sunshine_hours"))} h`,
      curtailmentText(combineCurtailment(items)),
    ]);
  });
}

function renderSourceNote() {
  const labels = { archive: "Open-Meteo-Archiv (ERA5-Reanalyse)", forecast: "Open-Meteo-Forecast (Modellanalyse)" };
  const counts = {};
  records.forEach((item) => {
    const key = labels[item.weather_source] ? item.weather_source : "unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  const parts = Object.entries(counts).map(([key, count]) => `${labels[key] || "Quelle nicht vermerkt (ältere Datensätze)"}: ${count} ${count === 1 ? "Tag" : "Tage"}`);
  elements.sourceNote.textContent = parts.length ? `Wetterquelle – ${parts.join("; ")}.` : "";
}

function describeSeries(name, data, valueText) {
  if (!data.length) return `${name}: keine Daten.`;
  return `${name}, ${data.length} ${data.length === 1 ? "Tag" : "Tage"} von ${formatShortDate(data[0].date)} bis ${formatShortDate(data.at(-1).date)}. ${valueText} Mit Pfeiltasten den Tag wechseln.`;
}

/* --------------------------------------------------------------- Stunden- und Tageszeit-Diagramme */

function daysWithHourly() {
  return records.filter((record) => Array.isArray(record.hourly) && record.hourly.length > 0);
}

function populateHourlySelect() {
  const availableDays = daysWithHourly();
  elements.hourlySelect.replaceChildren();
  if (!availableDays.length) {
    elements.hourlySelect.disabled = true;
    return;
  }
  for (const record of availableDays) {
    const option = document.createElement("option");
    option.value = record.date;
    option.textContent = formatDate(record.date);
    elements.hourlySelect.append(option);
  }
  elements.hourlySelect.value = availableDays.at(-1).date;
  elements.hourlySelect.disabled = false;
}

function renderHourlyCurtailmentNote(day) {
  const info = day.curtailment;
  elements.hourlyCurtailedLegend.hidden = !(info && info.hours > 0);
  if (!info) {
    elements.hourlyCurtailmentNote.textContent = day.signals_checked
      ? "AlphaESS liefert für diesen Tag keine Akku- oder Netzdaten; Abregelung kann nicht beurteilt werden."
      : "Dieser Tag wurde vor der Abregelungserkennung erfasst. Beim nächsten Start werden die Akku- und Netzdaten nachgeladen.";
  } else if (!info.hours) {
    elements.hourlyCurtailmentNote.textContent = "Keine Abregelung erkannt.";
  } else {
    const label = info.hours === 1 ? "Stunde" : "Stunden";
    elements.hourlyCurtailmentNote.textContent = `${info.hours} ${label} mit wahrscheinlicher Abregelung, bis zu ${formatNumber(info.lost_kwh)} kWh nicht erzeugt (Obergrenze, inklusive Modellabweichung).`;
  }
}

function drawHourlyChart() {
  const availableDays = daysWithHourly();
  const selectedDay = availableDays.find((record) => record.date === elements.hourlySelect.value) || availableDays.at(-1);
  if (!selectedDay) {
    elements.hourlyCanvas.hidden = true;
    elements.hourlyEmpty.hidden = false;
    elements.hourlyCurtailedLegend.hidden = true;
    elements.hourlyCurtailmentNote.textContent = "";
    return;
  }
  elements.hourlyCanvas.hidden = false;
  elements.hourlyEmpty.hidden = true;
  renderHourlyCurtailmentNote(selectedDay);
  drawChart(elements.hourlyCanvas, selectedDay.hourly, "actual_kwh", GREEN, "potential_kwh", {
    unit: "kWh",
    comparisonLabel: "Potentieller Ertrag",
    title: (item) => `${formatDate(selectedDay.date)} · ${item.hour}`,
    xLabel: (item) => item.hour.slice(0, 2),
    highlight: (item) => (item.curtailed ? CURTAILED_BAND : null),
    extraLine: hourlyTooltipExtra,
  });
}

function hourProfile() {
  const days = daysWithHourly().filter((record) => record.hourly.some((point) => point.actual_kwh !== null && point.actual_kwh !== undefined));
  const sums = new Map();
  let excluded = 0;
  days.forEach((record) => {
    record.hourly.forEach((point) => {
      if (point.actual_kwh === null || point.actual_kwh === undefined) return;
      if (point.curtailed) {
        excluded += 1; // abgeregelte Stunden würden Verschattung vortäuschen
        return;
      }
      const entry = sums.get(point.hour) || { hour: point.hour, actual: 0, potential: 0, days: 0 };
      entry.actual += Number(point.actual_kwh);
      entry.potential += Number(point.potential_kwh);
      entry.days += 1;
      sums.set(point.hour, entry);
    });
  });
  const data = [...sums.values()]
    .filter((entry) => entry.potential >= MIN_POTENTIAL_FOR_HOUR_PROFILE)
    .sort((a, b) => a.hour.localeCompare(b.hour))
    .map((entry) => ({ hour: entry.hour, ratio_pct: ratioPercent(entry.actual, entry.potential), days: entry.days }));
  return { dayCount: days.length, data, excluded };
}

function drawProfileChart() {
  const { dayCount, data, excluded } = hourProfile();
  elements.profileNote.textContent = excluded ? `${excluded} als abgeregelt erkannte Stundenwerte wurden aus dieser Auswertung ausgenommen.` : "";
  const enough = dayCount >= MIN_DAYS_FOR_HOUR_PROFILE && data.length > 0;
  elements.profileCanvas.hidden = !enough;
  elements.profileEmpty.hidden = enough;
  if (!enough) {
    elements.profileEmptyText.textContent = `Die Auswertung erscheint ab ${MIN_DAYS_FOR_HOUR_PROFILE} Tagen mit Stundenprofil (aktuell ${dayCount}).`;
    return;
  }
  drawChart(elements.profileCanvas, data, "ratio_pct", GREEN, null, {
    label: "Ø Ist/Potenzial",
    unit: "%",
    referenceLine: 100,
    minimumMaximum: 100,
    title: (item) => `${item.hour} Uhr`,
    extraLine: (item) => `Basis: ${item.days} ${item.days === 1 ? "Tag" : "Tage"}`,
    xLabel: (item) => item.hour.slice(0, 2),
  });
}

/* ------------------------------------------------------------------------------- Rendern */

function drawCharts() {
  const data = visibleRecords();
  if (data.length) {
    drawChart(elements.energyCanvas, data, "actual_kwh", GREEN, "potential_kwh");
    drawChart(elements.sunshineCanvas, data, "sunshine_hours", SUN);
    drawChart(
      elements.ratioCanvas,
      data.map((item) => ({ ...item, ratio_pct: ratioPercent(item.actual_kwh, item.potential_kwh) })),
      "ratio_pct",
      GREEN,
      null,
      { label: "Ist/Potenzial", unit: "%", referenceLine: 100, minimumMaximum: 100 },
    );
  }
  drawHourlyChart();
  drawProfileChart();
}

function updateAccessibleLabels(data) {
  elements.energyCanvas.setAttribute(
    "aria-label",
    describeSeries("Täglicher Ist-Ertrag und potentieller Ertrag", data, `Summe Ist ${formatNumber(sumOf(data, "actual_kwh"))} kWh, Summe Potenzial ${formatNumber(sumOf(data, "potential_kwh"))} kWh.`),
  );
  elements.sunshineCanvas.setAttribute(
    "aria-label",
    describeSeries("Tägliche Sonnenstunden", data, `Summe ${formatNumber(sumOf(data, "sunshine_hours"))} Stunden.`),
  );
  elements.ratioCanvas.setAttribute(
    "aria-label",
    describeSeries("Tägliches Verhältnis von Ist-Ertrag zu Potenzial", data, `Gesamtverhältnis ${percentText(ratioPercent(sumOf(data, "actual_kwh"), sumOf(data, "potential_kwh")))}.`),
  );
}

function render() {
  const data = visibleRecords();
  setEmptyState(data.length > 0);
  renderMetrics();
  renderSummary();
  renderHistory(data);
  renderSourceNote();
  updateAccessibleLabels(data);
  drawCharts();
  elements.exportButton.disabled = records.length === 0;
}

/* -------------------------------------------------------------------------------- Export */

function csvNumber(value) {
  if (value === null || value === undefined || value === "") return "";
  // Deutsches Excel erwartet das Dezimalkomma; mit Punkt würde es Werte als Datum oder Text lesen.
  return String(value).replace(".", ",");
}

function buildCsv() {
  const rows = [
    ["date", "actual_kwh", "potential_kwh", "sunshine_hours", "curtailed_hours", "curtailed_kwh"],
    ...records.map((item) => [
      item.date,
      csvNumber(item.actual_kwh),
      csvNumber(item.potential_kwh),
      csvNumber(item.sunshine_hours),
      csvNumber(item.curtailment?.hours),
      csvNumber(item.curtailment?.lost_kwh),
    ]),
  ];
  return rows.map((row) => row.join(";")).join("\r\n");
}

function exportCsv() {
  const blob = new Blob(["\ufeff", buildCsv()], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "pv-tagesbericht.csv";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ------------------------------------------------------------------------------- Start */

function debounce(callback, delay) {
  let timer = null;
  return () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(callback, delay);
  };
}

document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    activeRange = button.dataset.range === "all" ? "all" : Number(button.dataset.range);
    document.querySelectorAll("[data-range]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    render();
  });
});
elements.exportButton.addEventListener("click", exportCsv);
elements.hourlySelect.addEventListener("change", drawHourlyChart);

elements.connection.dataset.state = records.length ? "ready" : "loading";
elements.connectionLabel.textContent = records.length ? "Lokal gespeichert" : "Noch keine Tagesdaten";
populateHourlySelect();
render();
// Bei Größenänderung nur neu zeichnen: Tabellen und die Tag-Auswahl bleiben unangetastet.
window.addEventListener("resize", debounce(drawCharts, 150));
