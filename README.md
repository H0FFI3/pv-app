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
zusätzlich `ALPHAESS_SYSTEM_SN`. Die Datei wird nicht eingecheckt.

Erzeuge die ausführbare Datei:

```powershell
.\build_exe.ps1
```

Die App liegt anschließend unter `dist\PV-Tagesbericht.exe`. Lege eine `.env`
mit deinen Zugangsdaten neben die EXE. Beim Doppelklick werden Daten aktualisiert
und `dashboard.html` neben der App erzeugt und geöffnet. Ohne gültige Verbindung
öffnet die App den zuletzt gespeicherten Bericht.

## Lokale Daten

Der Verlauf liegt unter `%LOCALAPPDATA%\PV-Tagesbericht\daily.json`; der HTML-
Bericht wird neben dem gestarteten Skript bzw. der EXE gespeichert. Ein erneuter
Start ersetzt denselben Kalendertag, statt Duplikate anzulegen. Zum Öffnen des
letzten Berichts ohne API-Aufruf:

```powershell
.\dist\PV-Tagesbericht.exe --offline
```

`site/` enthält nur die HTML-, CSS- und JavaScript-Vorlage. Die fertige
`dashboard.html` bettet die Tagesdaten und alle Assets ein. Gehostete Runner und
GitHub Pages werden nicht verwendet.

Das Modell verwendet AxSun AX M-96 GG 2.0 460 Wp-Datenblattwerte (23,0 %,
NMOT 42 °C, Pmax -0,29 %/K), 14 % pauschale Systemverluste und die AlphaESS-
Wechselrichterleistung. Bifazialer Rückseitenertrag, Verschattung und weitere
Anlagendetails sind mangels Messdaten nicht berücksichtigt. Sonnenstunden folgen
der WMO-Schwelle von 120 W/m² direkter Normalstrahlung und stammen aus
historischen Open-Meteo-Wetterdaten.
