# Baut PV-Tagesbericht.exe mit PyInstaller.
#   .\build_exe.ps1            -> Ordner-Variante (schneller Start): dist\PV-Tagesbericht\PV-Tagesbericht.exe
#   .\build_exe.ps1 -OneFile   -> einzelne EXE: dist\PV-Tagesbericht.exe (entpackt bei jedem Start, startet langsamer)
param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

$mode = if ($OneFile) { "--onefile" } else { "--onedir" }

$arguments = @(
    "--clean",
    "--noconfirm",
    $mode,
    "--console",
    "--name",
    "PV-Tagesbericht",
    "--add-data",
    "site;site",
    "--collect-all",
    "pvlib",
    "--collect-all",
    "alphaess",
    "--collect-data",
    "tzdata"
)

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python).Source
}

& $python -m PyInstaller @arguments (Join-Path $PSScriptRoot "pv_app.py")

if ($OneFile) {
    Write-Host "Fertig: dist\PV-Tagesbericht.exe"
} else {
    Write-Host "Fertig: dist\PV-Tagesbericht\PV-Tagesbericht.exe"
}
