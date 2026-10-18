# PV-Tagesbericht

Lokale Windows-App zur Erfassung und Auswertung der PV-Tageswerte. Beim Start
fragt sie AlphaESS und Open-Meteo ab, speichert alle fehlenden abgeschlossenen
Tage und öffnet den eigenständigen HTML-Bericht im Standardbrowser. Der Bericht
benötigt weder einen lokalen Webserver noch eine Internetverbindung.

## Einrichtung

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Trage AppID und AppSecret lokal in `.env` ein (bei mehreren AlphaESS-Anlagen
zusätzlich `ALPHAESS_SYSTEM_SN`) sowie die Anlagen- und Modellparameter.
`.env.example` enthält alle Variablennamen mit Erläuterung, aber keine Standort-
oder Zugangsdaten. `.env` wird nicht eingecheckt.

- **Pflicht:** `LATITUDE`, `LONGITUDE`, `TIMEZONE`, `SURFACE_TILT_DEG`, `MODULE_POWER_WP`,
  `MODULE_EFFICIENCY`, `MODULE_NMOT_C`, `POWER_TEMPERATURE_COEFFICIENT`, `SYSTEM_LOSS_FRACTION`,
  `MODULES_EAST`, `AZIMUTH_EAST_DEG`, `MODULES_WEST`, `AZIMUTH_WEST_DEG`,
  `NOMINAL_INVERTER_EFFICIENCY`, `DATA_START_DATE`.
- **Optional:** `MODULE_MODEL`, `MODULE_BIFACIAL`, `API_REQUEST_INTERVAL_SECONDS` (Standard 10),
  `BACKFILL_MAX_DAYS` (Standard 14), `WEATHER_SOURCE`, `REPORT_PATH` und die Modelloptionen
  (siehe unten).
- Fehlende oder ungültige Werte werden beim Start mit Variablennamen gemeldet (z. B. Prozent statt
  Anteil bei `MODULE_EFFICIENCY`). Dezimalkomma wird akzeptiert.
- **Rangfolge:** Windows-Umgebungsvariablen vor `.env` neben der EXE vor der Projekt-`.env`.
  Leere Werte in einer `.env` verdecken keine gefüllten Werte aus einer anderen.

## Starten und Optionen

```powershell
python pv_app.py                 # aktualisieren, Bericht erzeugen und öffnen
python pv_app.py --offline       # nur lokale Daten anzeigen, keine Abrufe
python pv_app.py --refresh       # den Vortag auch dann neu abrufen, wenn er schon gespeichert ist
python pv_app.py --refresh-days 5  # die letzten 5 Tage neu abrufen (1 bis 92)
python pv_app.py --no-open       # Bericht erzeugen, aber nicht öffnen (z. B. Aufgabenplanung)
python pv_app.py --verbose       # ausführlichere Protokollierung
```

Für die EXE gelten dieselben Optionen (`PV-Tagesbericht.exe --offline`).

### Fehlende Tage werden nachgeholt (Backfill)

Bei jedem Start werden **alle** Tage zwischen `DATA_START_DATE` und gestern erfasst, die noch
nicht gespeichert sind, höchstens die letzten `BACKFILL_MAX_DAYS` (Standard 14, maximal 92).
Ist schon alles vorhanden, finden keine Abrufe statt (kein Warten auf die AlphaESS-Schutzpausen).
Tage ohne verfügbare Daten werden einzeln übersprungen und beim nächsten Start erneut versucht.

Die Zeitplanung lässt sich automatisieren, damit keine Tage fehlen: Windows-Aufgabenplanung →
neue Aufgabe → Aktion „Programm starten“ mit `PV-Tagesbericht.exe` und Argument `--no-open`,
Trigger z. B. täglich um 08:00 und „Bei Anmeldung“.

### Wetterquelle

`WEATHER_SOURCE=auto` (Standard) nutzt das Open-Meteo-Archiv (ERA5-Reanalyse). Das Archiv liegt
laut Open-Meteo-Dokumentation einige Tage zurück; fehlende oder unvollständige Tage werden aus
der Forecast-API (`past_days`, Modellanalyse) ergänzt. Pro Datensatz wird die Quelle gespeichert
(`weather_source`) und im Bericht-Footer angezeigt. `archive` bzw. `forecast` erzwingen eine Quelle.
Ein Tag gilt nur als vollständig, wenn mindestens 22 Stundenwerte vorliegen.

## EXE erstellen

```powershell
python -m pip install -r requirements-dev.txt
.\build_exe.ps1            # Ordner-Variante: dist\PV-Tagesbericht\PV-Tagesbericht.exe (schneller Start)
.\build_exe.ps1 -OneFile   # einzelne EXE: dist\PV-Tagesbericht.exe (entpackt bei jedem Start)
```

Beim Start sucht die EXE eine `.env` neben sich, danach im Ordner darüber und zwei Ebenen darüber
(so findet auch `dist\PV-Tagesbericht\` die `.env` im Projektordner). `dashboard.html` wird neben
der EXE erzeugt; ist der Ordner nicht beschreibbar (z. B. `Program Files`), weicht die App nach
`%LOCALAPPDATA%\PV-Tagesbericht` aus. `REPORT_PATH` überschreibt beides.

## Lokale Daten und Sicherheit der Historie

| Datei | Zweck |
|---|---|
| `%LOCALAPPDATA%\PV-Tagesbericht\daily.json` | Tageshistorie (Rohbestand) |
| `daily.json.bak` | Stand vor dem letzten Schreiben |
| `daily.json.corrupt-<zeit>` | defekte Datei, zur Analyse erhalten |
| `logs\pv-tagesbericht.log` | Protokoll (rotierend, ohne Zugangsdaten) |
| `horizon.json` | Cache des PVGIS-Horizontprofils |

- Die Datei wird **atomar** geschrieben (temporäre Datei, dann ersetzen) und nur, wenn sich etwas
  geändert hat. Ein Start ohne neue Daten oder `--offline` fasst sie nicht an.
- Ungültige oder ältere Datensätze werden nur **nicht angezeigt**, aber nie gelöscht. Ändert sich
  `DATA_START_DATE`, geht keine Historie verloren.
- Ist die Datei defekt, wird sie umbenannt und aus `.bak` wiederhergestellt, die App startet normal.
- Ein erneuter Abruf ersetzt denselben Kalendertag, statt Duplikate anzulegen.

Der Stundenverlauf nutzt AlphaESS-Leistungsmessungen (typisch im 5-Minuten-Abstand), die auf
Stunden verteilt und auf den Tageswert skaliert werden. Ältere Tageswerte ohne Leistungsprofil
lassen sich nicht nachträglich in Stundenwerte aufteilen. Am Tag der Zeitumstellung im Herbst
werden die beiden Stunden „02:00“ zu einem Eintrag zusammengefasst (für PV ohne praktische Wirkung).

`analyze_pv.py` kann optional `pv_expected_tmy.json` (PVGIS-Referenzjahr) und `compare_daily.py`
optional `pv_daily_comparison.json` (manueller Tagesvergleich) erzeugen. Beide nutzen dasselbe
Modell wie die App. Der CSV-Button im Dashboard exportiert (Dezimalkomma, Semikolon, UTF-8 mit BOM
für deutsches Excel).

## Modell und Grenzen

Das Potenzial ist ein wetterbasiertes PVWatts-Modell (pvlib: Haydavies-Transposition,
NOCT-SAM-Zelltemperatur, pauschale Systemverluste `SYSTEM_LOSS_FRACTION`, Wechselrichter-
Begrenzung mit der AlphaESS-Leistung). Die Modelloptionen sind **standardmäßig aus**, sodass die
Werte denen der Vorversion entsprechen. Schalte sie einzeln ein und vergleiche die Potenzialwerte:

| Variable | Wirkung |
|---|---|
| `IAM_ENABLED`, `IAM_B` | Einfallswinkel-Modifikator (ASHRAE) für die direkte Strahlung. Reduziert den Ertrag bei flachem Einfall. Die Systemverluste enthalten Reflexion eventuell schon, daher nicht blind zusätzlich aktivieren. |
| `ALBEDO` | Bodenreflexion (Standard 0.25). Bei Schnee deutlich höher. |
| `HORIZON_ENABLED` | Horizontprofil von PVGIS: direkte Strahlung entfällt, wenn die Sonne dahinter steht. Das Profil wird einmal geladen und lokal gecacht; bei Fehlern wird ohne Horizont gerechnet. |
| `MODULE_BIFACIAL` + `BIFACIALITY_GAIN` | Pauschaler Rückseitengewinn bifazialer Module (nur wirksam, wenn beides gesetzt ist). |

**Bekannte Modellgrenzen**

- **Abregelung ist nicht modelliert** (Einspeisebegrenzung, voller Akku), wird aber erkannt und markiert
  (siehe oben). Der AlphaESS-Ist-Ertrag enthält sie, das Potenzial nicht. Abweichungen zwischen Ist und Potenzial sind deshalb **nicht
  automatisch Verschattung oder Defekt**. Die Auswertung „Abweichung nach Tageszeit“ hilft zu
  unterscheiden: Dauerhaft niedrige Morgen-/Abendstunden deuten auf Verschattung, niedrige
  Mittagsstunden eher auf Begrenzung.
- **Strahlungsquelle:** ERA5-Reanalyse hat eine grobe Auflösung (~10 km). Satellitenbasierte
  Strahlungsdaten (z. B. PVGIS-SARAH oder Open-Meteo-Satellitenstrahlung, Verfügbarkeit für den
  Standort prüfen) sind für PV meist genauer. Eine zusätzliche Quelle ist nicht eingebaut.
- Diffuse Strahlung und Bodenreflexion erhalten keinen Einfallswinkel-Modifikator.
- Schneebedeckung, Verschmutzung und Modulalterung sind nicht enthalten.
- Sonnenstunden folgen der WMO-Schwelle von 120 W/m² direkter Normalstrahlung (Open-Meteo).

## Abregelung erkennen und markieren

Hybrid-Wechselrichter drosseln die PV-Leistung, wenn der Akku voll ist und die Einspeisung begrenzt wird.
Das Modell kennt das nicht, der AlphaESS-Ist-Ertrag enthält es. Die App markiert deshalb Stunden, die
**wahrscheinlich abgeregelt** wurden:

1. Der Ist-Ertrag der Stunde liegt mindestens `CURTAILMENT_MIN_DEFICIT` (Standard 20 %) unter dem Potenzial
   und das Potenzial beträgt mindestens `CURTAILMENT_MIN_POTENTIAL_KWH` (Standard 0,3 kWh), und
2. mindestens ein Hinweis aus den AlphaESS-Leistungsdaten (5-Minuten-Werte, ohne zusätzliche Abrufe):
   - **Einspeiselimit erreicht:** die mittlere Netzleistung der Stunde liegt bei mindestens 95 %
     von `FEED_IN_LIMIT_W` (nur wenn das Limit gesetzt ist), oder
   - **Akku voll:** der Ladestand erreichte `BATTERY_FULL_SOC_PCT` (Standard 97 %).

Im Bericht erscheinen markierte Stunden im Stundendiagramm als hinterlegtes Band (Tooltip: Grund, Akkustand,
Netzleistung), pro Tag und in den Summen als „Stunden · kWh“ sowie in den CSV-Spalten `curtailed_hours` und
`curtailed_kwh`. Die Auswertung „Abweichung nach Tageszeit“ **lässt markierte Stunden weg**, damit
Abregelung keine Verschattung vortäuscht. Eine Anzeige „-“ bedeutet: für den Tag liegen keine Signale
vor, die Erkennung ist nicht möglich. „keine“ bedeutet: geprüft, nichts erkannt.

**Wichtig zur Einordnung**

- Es ist eine Heuristik. Ein zu optimistisches Potenzial (z. B. ohne Horizont oder IAM) kann zusammen mit
  vollem Akku fälschlich als Abregelung erscheinen. Das Einspeiselimit (`FEED_IN_LIMIT_W`) ist der stärkere
  Hinweis, „Akku voll“ allein der schwächere. Erhöhe bei zu vielen Markierungen `CURTAILMENT_MIN_DEFICIT`.
- Die angegebene Menge ist eine **Obergrenze** (Potenzial minus Ist in den markierten Stunden, inklusive
  Modellabweichung).
- Gespeichert werden nur die Rohsignale je Stunde (`soc_pct`, `feed_in_w`). Die Markierung wird bei jeder
  Berichtserstellung neu berechnet; geänderte Schwellen gelten damit **rückwirkend** für alle Tage,
  die schon Signale haben.
- **Ältere Tage werden nachgeladen:** Tage, die vor der Abregelungserkennung gespeichert wurden, bekommen ihre
  Akku-/Netzdaten beim nächsten Start nachgeladen (innerhalb des Fensters `BACKFILL_MAX_DAYS`, ein Abruf je
  Tag mit der üblichen Schutzpause). Ist-, Potenzial- und Sonnenwerte bleiben dabei unverändert. Der Tag wird
  danach als geprüft vermerkt (`signals_checked`) und nicht erneut abgefragt, auch wenn AlphaESS für ihn
  keine Daten mehr liefert. Schlägt der Abruf fehl, wird er beim nächsten Start wiederholt.
- **Keine Einspeisung erlaubt (Nulleinspeisung)?** Dann gibt es kein Einspeiselimit zum Vergleichen; lass
  `FEED_IN_LIMIT_W` leer. Es greift die Regel „Akku voll“.
- Die Feldnamen (`cbat` = Akkustand in %, `feedIn` = Netzleistung in W) stammen aus der AlphaESS-Doku und
  können je nach Anlage abweichen. Mit `python tools/alphaess_fields.py` siehst du die tatsächlichen
  Felder (Seriennummern maskiert); passende Namen trägst du in `ALPHAESS_SOC_FIELD` und
  `ALPHAESS_FEED_IN_FIELD` ein. Fehlen die Felder, warnt die App einmal pro Lauf.

## Bericht

`site/` enthält die HTML-, CSS- und JavaScript-Vorlage. Die fertige `dashboard.html` bettet
Daten, Stylesheet und Skript ein (Platzhalter: `data-inline="styles"` und `data-inline="script"`;
fehlen sie, bricht die Erzeugung mit einer Fehlermeldung ab). Das Dashboard zeigt Tagesertrag,
Potenzial, das Verhältnis Ist/Potenzial, Sonnenstunden, den Stundenverlauf, die Abweichung nach
Tageszeit (ab 5 Tagen mit Stundenprofil), Summen für 7 Tage, Monat und Gesamtzeitraum sowie die
Wetterquelle. Die Diagramme sind per Tastatur bedienbar (Pfeiltasten, Pos1/Ende, Esc).

## Entwicklung

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest                      # alle Python-Tests, ohne Netzwerk (Quellen werden gemockt)
```

Optionale Frontend-Tests (jsdom, benötigt Node.js): `cd tests\frontend; npm install`, danach laufen
sie automatisch mit `python -m pytest` oder einzeln mit `npm test` (Umgebungsvariable
`PV_FIXTURE_DIR`, siehe `tests/test_frontend.py`).

Aufbau: `pv_app.py` (Einstieg) → `pvapp/` mit `config.py` (Konfiguration), `sources.py` (AlphaESS,
Open-Meteo, PVGIS), `model.py` (reines PV-Modell), `storage.py` (Historie), `report.py` (HTML),
`curtailment.py` (Abregelungserkennung), `app.py` (Ablauf und CLI), `interactive.py`, `logging_setup.py`, `paths.py`. `tools/alphaess_probe.py` und
`tools/alphaess_fields.py` sind Diagnosewerkzeuge für die AlphaESS-API.
