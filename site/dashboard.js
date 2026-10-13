const DATA_URL = `data/daily.json?cache=${Date.now()}`;
const numberFormat = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

let records = [];
let activeRange = 30;
let energyChart;
let sunshineChart;

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
  energyEmpty: document.querySelector("#energy-empty"),
  sunshineEmpty: document.querySelector("#sunshine-empty"),
  historyBody: document.querySelector("#history-body"),
  historyCount: document.querySelector("#history-count"),
  exportButton: document.querySelector("#export-button"),
};

function formatNumber(value) {
  return Number.isFinite(Number(value)) ? numberFormat.format(Number(value)) : "-";
}

function formatDate(value) {
  return dateFormat.format(new Date(`${value}T12:00:00Z`));
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

function chartOptions(unit) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#172a25",
        padding: 11,
        callbacks: { label: (context) => `${context.dataset.label}: ${formatNumber(context.parsed.y)} ${unit}` },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: "#687872", maxTicksLimit: 8, maxRotation: 0, font: { size: 10 } }, border: { display: false } },
      y: { beginAtZero: true, suggestedMax: unit === "h" ? 12 : undefined, grid: { color: "#e8eeea" }, ticks: { color: "#687872", font: { size: 10 } }, border: { display: false } },
    },
  };
}

function createEnergyChart(data) {
  if (!window.Chart || !data.length) return;
  const config = {
    type: "bar",
    data: {
      labels: data.map((item) => formatDate(item.date)),
      datasets: [
        { type: "bar", label: "Ist-Ertrag", data: data.map((item) => item.actual_kwh), backgroundColor: "#197459", borderRadius: 3, maxBarThickness: 19, order: 2 },
        { type: "line", label: "Potenzial", data: data.map((item) => item.potential_kwh), borderColor: "#dc7b52", backgroundColor: "#dc7b52", borderWidth: 2, pointRadius: data.length > 45 ? 0 : 2, pointHoverRadius: 4, tension: 0.22, order: 1 },
      ],
    },
    options: chartOptions("kWh"),
  };
  if (energyChart) {
    energyChart.data = config.data;
    energyChart.update();
  } else {
    energyChart = new Chart(elements.energyCanvas, config);
  }
}

function createSunshineChart(data) {
  if (!window.Chart || !data.length) return;
  const config = {
    type: "bar",
    data: {
      labels: data.map((item) => formatDate(item.date)),
      datasets: [{ label: "Sonnenstunden", data: data.map((item) => item.sunshine_hours), backgroundColor: "#e5ad38", borderRadius: 3, maxBarThickness: 19 }],
    },
    options: chartOptions("h"),
  };
  config.options.scales.x.ticks.maxTicksLimit = 6;
  config.options.scales.x.ticks.font.size = 9;
  config.options.scales.y.ticks.stepSize = 3;
  if (sunshineChart) {
    sunshineChart.data = config.data;
    sunshineChart.update();
  } else {
    sunshineChart = new Chart(elements.sunshineCanvas, config);
  }
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
  createEnergyChart(data);
  createSunshineChart(data);
  elements.exportButton.disabled = records.length === 0;
  if (!window.Chart && records.length) {
    elements.connection.dataset.state = "error";
    elements.connectionLabel.textContent = "Diagrammbibliothek nicht erreichbar";
  }
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

async function loadDashboard() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`Datenabruf fehlgeschlagen (${response.status})`);
    const payload = await response.json();
    records = (Array.isArray(payload.records) ? payload.records : [])
      .filter((item) => item.date && Number.isFinite(Number(item.actual_kwh)))
      .sort((left, right) => left.date.localeCompare(right.date));
    elements.connection.dataset.state = records.length ? "ready" : "loading";
    elements.connectionLabel.textContent = records.length ? "Daten aktuell" : "Wartet auf ersten Tageslauf";
    render();
  } catch (error) {
    elements.connection.dataset.state = "error";
    elements.connectionLabel.textContent = "Daten nicht erreichbar";
    elements.energyEmpty.hidden = false;
    elements.sunshineEmpty.hidden = false;
    elements.energyEmpty.querySelector("p").textContent = "Daten konnten nicht geladen werden";
    elements.energyEmpty.querySelector("span:last-child").textContent = error.message;
  }
}

loadDashboard();