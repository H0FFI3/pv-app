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

The model assumes 460 Wp modules, 14% system losses, and does not account for
inverter clipping or site-specific shading. It writes the hourly estimate to
`pv_expected_tmy.csv`.

To inspect the AlphaESS API response, run the probe and enter your credentials
in the terminal when prompted, or store them locally in `.env` as
`ALPHAESS_APP_ID` and `ALPHAESS_APP_SECRET`. The AppSecret is hidden when
prompted, and `.env` is excluded from version control:

```powershell
python alphaess_probe.py
```
