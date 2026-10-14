const numberFormat = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const shortDateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", timeZone: "UTC" });

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
  hourlyCanvas: document.querySelector("#hourly-chart"),
  hourlyTooltip: document.querySelector("#hourly-tooltip"),
  hourlyEmpty: document.querySelector("#hourly-empty"),
  hourlySelect: document.querySelector("#hourly-date"),
  energyEmpty: document.querySelector("#energy-empty"),
  sunshineEmpty: document.querySelector("#sunshine-empty"),
  historyBody: document.querySelector("#history-body"),
  historyCount: document.querySelector("#history-count"),
  exportButton: document.querySelector("#export-button"),
};

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

function bindChartHover(canvas, tooltipId) {
  if (canvas.dataset.hoverBound) return;
  canvas.dataset.hoverBound = "true";
  const tooltip = document.getElementById(tooltipId);
  const hideTooltip = () => { tooltip.hidden = true; };

  canvas.addEventListener("pointermove", (event) => {
    const chart = canvas.pvChart;
    if (!chart?.data.length) return hideTooltip();
    const bounds = canvas.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    if (x < chart.left || x > bounds.width - chart.right) return hideTooltip();

    const slotWidth = (bounds.width - chart.left - chart.right) / chart.data.length;
    const index = Math.min(chart.data.length - 1, Math.floor((x - chart.left) / slotWidth));
    const item = chart.data[index];
    const lines = [chart.title ? chart.title(item) : formatDate(item.date)];
    lines.push(`${chart.label}: ${formatNumber(item[chart.valueKey])} ${chart.unit}`);
    if (chart.comparisonKey) {
      lines.push(`${chart.comparisonLabel}: ${formatNumber(item[chart.comparisonKey])} ${chart.unit}`);
    }
    tooltip.textContent = lines.join("\n");
    tooltip.hidden = false;

    const center = chart.left + slotWidth * (index + 0.5);
    const halfTooltip = tooltip.offsetWidth / 2;
    const left = Math.max(halfTooltip + 6, Math.min(bounds.width - halfTooltip - 6, center));
    const y = Math.max(tooltip.offsetHeight + 8, event.clientY - bounds.top - 10);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${y}px`;
  });
  canvas.addEventListener("pointerleave", hideTooltip);
  canvas.addEventListener("blur", hideTooltip);
}

function visibleRecords() {
  return activeRange === "all" ? records : records.slice(-activeRange);
}

function setEmptyState(hasData) {
  elements.energyEmpty.hidden = hasData;
  elements.sunshineEmpty.hidden = hasData;
  elements.energyCanvas.hidden = !hasData;
  elements.sunshineCanvas.hidden = !hasData;
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
  const achieved = Number(latest.potential_kwh) > 0 ? (Number(latest.actual_kwh) / Number(latest.potential_kwh)) * 100 : null;
  elements.actualValue.textContent = formatNumber(latest.actual_kwh);
  elements.potentialValue.textContent = formatNumber(latest.potential_kwh);
  elements.sunshineValue.textContent = formatNumber(latest.sunshine_hours);
  elements.latestDate.textContent = formatDate(latest.date);
  elements.actualCaption.textContent = achieved === null ? "AlphaESS Tageswert" : `${formatNumber(achieved)} % des Potenzials`;
  elements.potentialCaption.textContent = `Abstand zum Ist: ${formatNumber(difference)} kWh`;
}

function drawChart(canvas, data, valueKey, color, comparisonKey = null, options = {}) {
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
  canvas.pvChart = {
    data,
    valueKey,
    comparisonKey,
    label: valueKey === "sunshine_hours" ? "Sonne" : "Ist-Ertrag",
    unit: options.hourly ? "kWh" : valueKey === "sunshine_hours" ? "h" : "kWh",
    comparisonLabel: options.comparisonLabel || "Potentieller Ertrag",
    title: options.hourly ? (item) => `${formatDate(options.day)} · ${item.hour}` : (item) => formatDate(item.date),
    xLabel: options.hourly ? (item) => item.hour.slice(0, 2) : (item) => formatShortDate(item.date),
    left,
    right,
  };
  bindChartHover(canvas, canvas.id === "energy-chart" ? "energy-tooltip" : "sunshine-tooltip");
  const values = data.flatMap((item) => [Number(item[valueKey]), comparisonKey ? Number(item[comparisonKey]) : 0]);
  const maximum = Math.max(1, ...values.filter(Number.isFinite)) * 1.12;
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

  data.forEach((item, index) => {
    const center = left + slotWidth * (index + 0.5);
    if (item[valueKey] === null || item[valueKey] === undefined) return;
    const value = Number(item[valueKey]);
    const y = yFor(value);
    context.fillStyle = color;
    context.fillRect(center - barWidth / 2, y, barWidth, top + plotHeight - y);
  });

  if (comparisonKey) {
    context.strokeStyle = "#dc7b52";
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

  const labelStride = Math.max(1, Math.ceil(data.length / 6));
  context.fillStyle = "#687872";
  context.textAlign = "center";
  context.textBaseline = "top";
  data.forEach((item, index) => {
    if (index % labelStride !== 0 && index !== data.length - 1) return;
    const x = left + slotWidth * (index + 0.5);
    context.fillText(options.hourly ? item.hour.slice(0, 2) : formatShortDate(item.date), x, top + plotHeight + 8);
  });
}

function renderHourlyChart() {
  const availableDays = records.filter((record) => Array.isArray(record.hourly) && record.hourly.length > 0);
  const previousSelection = elements.hourlySelect.value;
  elements.hourlySelect.replaceChildren();
  if (!availableDays.length) {
    elements.hourlySelect.disabled = true;
    elements.hourlyCanvas.hidden = true;
    elements.hourlyTooltip.hidden = true;
    elements.hourlyEmpty.hidden = false;
    return;
  }

  for (const record of availableDays) {
    const option = document.createElement("option");
    option.value = record.date;
    option.textContent = formatDate(record.date);
    elements.hourlySelect.append(option);
  }
  const selectedDay = availableDays.find((record) => record.date === previousSelection) || availableDays.at(-1);
  elements.hourlySelect.value = selectedDay.date;
  elements.hourlySelect.disabled = false;
  elements.hourlyCanvas.hidden = false;
  elements.hourlyEmpty.hidden = true;
  drawChart(
    elements.hourlyCanvas,
    selectedDay.hourly,
    "actual_kwh",
    "#197459",
    "potential_kwh",
    { hourly: true, day: selectedDay.date, comparisonLabel: "Potentieller Ertrag" },
  );
}

function renderHistory(data) {
  elements.historyBody.replaceChildren();
  elements.historyCount.textContent = `${records.length} ${records.length === 1 ? "Tag" : "Tage"}`;
  if (!data.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "table-empty";
    cell.textContent = "Die Tageshistorie wird nach dem ersten Lauf hier angezeigt.";
    row.append(cell);
    elements.historyBody.append(row);
    return;
  }
  data.slice().reverse().slice(0, 14).forEach((item) => {
    const row = document.createElement("tr");
    [formatDate(item.date), `${formatNumber(item.actual_kwh)} kWh`, `${formatNumber(item.potential_kwh)} kWh`, `${formatNumber(item.sunshine_hours)} h`]
      .forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
    elements.historyBody.append(row);
  });
}

function render() {
  const data = visibleRecords();
  setEmptyState(data.length > 0);
  renderMetrics();
  renderHistory(data);
  drawChart(elements.energyCanvas, data, "actual_kwh", "#197459", "potential_kwh");
  drawChart(elements.sunshineCanvas, data, "sunshine_hours", "#e5ad38");
  renderHourlyChart();
  elements.exportButton.disabled = records.length === 0;
}

function exportCsv() {
  const rows = [
    ["date", "actual_kwh", "potential_kwh", "sunshine_hours"],
    ...records.map((item) => [item.date, item.actual_kwh, item.potential_kwh, item.sunshine_hours]),
  ];
  const csv = rows.map((row) => row.join(";")).join("\r\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "pv-tagesbericht.csv";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    activeRange = button.dataset.range === "all" ? "all" : Number(button.dataset.range);
    document.querySelectorAll("[data-range]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    render();
  });
});
elements.exportButton.addEventListener("click", exportCsv);
elements.hourlySelect.addEventListener("change", renderHourlyChart);

elements.connection.dataset.state = records.length ? "ready" : "loading";
elements.connectionLabel.textContent = records.length ? "Lokal gespeichert" : "Noch keine Tagesdaten";
render();
window.addEventListener("resize", render);