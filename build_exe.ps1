$ErrorActionPreference = "Stop"

$arguments = @(
    "--clean",
    "--noconfirm",
    "--onefile",
    "--console",
    "--name",
    "PV-Tagesbericht",
    "--add-data",
    "site;site",
    "--collect-all",
    "pvlib",
    "--collect-all",
    "alphaess"
)

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python).Source
}

& $python -m PyInstaller @arguments (Join-Path $PSScriptRoot "pv_app.py")