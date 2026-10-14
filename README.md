# PV-Tagesbericht

Lokale Windows-App zur Erfassung und Auswertung der PV-Tageswerte. Beim Start
fragt sie AlphaESS und Open-Meteo ab, speichert den abgeschlossenen Vortag und
öffnet den eigenständigen HTML-Bericht im Standardbrowser. Der Bericht benötigt
weder einen lokalen Webserver noch eine Internetverbindung.

## App erstellen

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Trage AppID und AppSecret lokal in `.env` ein. Bei mehreren AlphaESS-Anlagen
zusätzlich `ALPHAESS_SYSTEM_SN`. Alle Anlagen- und Modellparameter werden dort
verwaltet: `LATITUDE`, `LONGITUDE`, `TIMEZONE`, `SURFACE_TILT_DEG`,
`MODULE_MODEL`, `MODULE_POWER_WP`, `MODULE_EFFICIENCY`, `MODULE_NMOT_C`,
`MODULE_BIFACIAL`, `POWER_TEMPERATURE_COEFFICIENT`, `SYSTEM_LOSS_FRACTION`,
`MODULES_EAST`, `AZIMUTH_EAST_DEG`, `MODULES_WEST`, `AZIMUTH_WEST_DEG`,
`NOMINAL_INVERTER_EFFICIENCY`, `API_REQUEST_INTERVAL_SECONDS` und
`DATA_START_DATE`. `.env.example` enthält die Variablennamen, aber keine
Standort- oder Zugangsdaten. `.env` wird nicht eingecheckt. Die EXE
liest zuerst eine `.env` neben sich und verwendet fehlende Werte ergänzend aus
der Projekt-`.env`; vorhandene Windows-Umgebungsvariablen haben Vorrang.

Erzeuge die ausführbare Datei:

```powershell
.\build_exe.ps1
```

Die App liegt anschließend unter `dist\PV-Tagesbericht.exe`. Beim Doppelklick
werden Daten aktualisiert und `dashboard.html` neben der App erzeugt und geöffnet.
Wenn du die EXE separat weitergibst, lege eine `.env` mit deinen Zugangsdaten
neben die EXE. Ohne gültige Verbindung öffnet die App den zuletzt gespeicherten Bericht.

## Lokale Daten

Der Verlauf liegt unter `%LOCALAPPDATA%\PV-Tagesbericht\daily.json`; der HTML-
Bericht wird neben dem gestarteten Skript bzw. der EXE gespeichert. Ein erneuter
Start ersetzt denselben Kalendertag, statt Duplikate anzulegen. Zum Öffnen des
letzten Berichts ohne API-Aufruf:

```powershell
.\dist\PV-Tagesbericht.exe --offline
```

Die App benötigt keine separaten Analyseexporte. `analyze_pv.py` kann optional
`pv_expected_tmy.json` (PVGIS-Referenzjahr) und `compare_daily.py` optional
`pv_daily_comparison.json` (manueller Tagesvergleich) erzeugen. Der Verlauf für
die App bleibt `%LOCALAPPDATA%\PV-Tagesbericht\daily.json`; der CSV-Button im
Dashboard exportiert nur bei Bedarf.

Die Erfassung beginnt am 13.10.2026. Der unvollständige 12.10. wird nicht in
den Verlauf übernommen; der erste Tageswert für den 13.10. ist am Folgetag
verfügbar.

Für den Stundenverlauf ruft die App zusätzlich AlphaESS-Leistungsmessungen
(typischerweise im 5-Minuten-Abstand) ab und verteilt sie auf Stunden. Das
Stundenpotenzial kommt aus dem stündlichen Wettermodell. Ältere Tageswerte ohne
Leistungsprofil lassen sich nicht nachträglich in Stundenwerte aufteilen.

`site/` enthält nur die HTML-, CSS- und JavaScript-Vorlage. Die fertige
`dashboard.html` bettet die Tagesdaten und alle Assets ein. Gehostete Runner und
GitHub Pages werden nicht verwendet.

Das Modell verwendet die Modul- und Anlagendaten aus `.env`, 14 % pauschale
Systemverluste und die AlphaESS-Wechselrichterleistung. Bifazialer Rückseitenertrag,
Verschattung und weitere Anlagendetails sind mangels Messdaten nicht berücksichtigt. Sonnenstunden folgen
der WMO-Schwelle von 120 W/m² direkter Normalstrahlung und stammen aus
historischen Open-Meteo-Wetterdaten.
