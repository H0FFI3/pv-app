# pv-app
app to visualize and monitor the efficiency of my pv

## Python environment

Create and activate the virtual environment, then install the dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Check that the required packages are installed and importable:

```powershell
python test_dependencies.py
```

Create an initial east-west yield estimate from PVGIS typical weather data:

```powershell
python analyze_pv.py
```

The model uses AxSun AX M-96 GG 2.0 460 Wp datasheet values (23.0% efficiency,
NMOT 42 C, Pmax temperature coefficient -0.29%/K) and assumes 14% system losses.
Because roof clearance and ground reflectivity are unknown, bifacial rear-side
gain, inverter clipping, and site-specific shading are not included. It writes
the hourly estimate to `pv_expected_tmy.csv`. See the
[manufacturer datasheet](https://www.axsun.de/wp-content/uploads/2025/12/2026_Datenblatt_AXM_96_infinity_BT_GG_RE_460Wp.pdf).

To inspect the AlphaESS API response, run the probe and enter your credentials
in the terminal when prompted, or store them locally in `.env` as
`ALPHAESS_APP_ID` and `ALPHAESS_APP_SECRET`. The AppSecret is hidden when
prompted, and `.env` is excluded from version control:

```powershell
python alphaess_probe.py
```

Compare actual AlphaESS daily energy with modeled potential for a date range:

```powershell
python compare_daily.py 2026-09-01 2026-09-07
```

The script uses historical hourly weather from Open-Meteo and AlphaESS daily
energy (`epv`). It also records daily sunshine hours, defined by Open-Meteo as
direct normal irradiance above 120 W/m2 (WMO definition); this is regional
historical weather data, not a sensor measurement on the roof. AlphaESS requests
are spaced at least 10 seconds apart, so retrieving a week takes about one
minute. The CSV comparison is git-ignored.

## GitHub Pages dashboard

The static dashboard lives in `site/` and displays only daily actual energy,
modeled potential, and sunshine hours. `site/data/daily.json` is the private
repository's historical source; the Pages deployment publishes those three
daily values publicly.

To enable the scheduled update:

1. In repository **Settings → Secrets and variables → Actions**, add
	`ALPHAESS_APP_ID` and `ALPHAESS_APP_SECRET`. If the AppID has multiple
	systems, also add `ALPHAESS_SYSTEM_SN` for the system to monitor.
2. In **Settings → Pages**, set the build and deployment source to **GitHub Actions**.
3. Run **Actions → Update PV dashboard → Run workflow** once to verify the
	connection and publish the first available day.

Afterward the workflow updates the prior day's values every morning and
deploys the dashboard. Credentials remain in GitHub Actions Secrets. The Pages
site and its JSON data are public. Manual comparison remains available with
`python compare_daily.py START_DATE END_DATE`.
