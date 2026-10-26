# Erzeugt den Windows-Installer (Inno Setup 6): dist\installer\PV-Tagesbericht-Setup-<Version>.exe
# Voraussetzungen: Python-Umgebung mit requirements-dev.txt und Inno Setup 6 (https://jrsoftware.org/isinfo.php)
param(
    [switch]$SkipBuild   # den vorhandenen Ordner dist\PV-Tagesbericht verwenden
)
$ErrorActionPreference = "Stop"

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build_exe.ps1")
}
if (-not (Test-Path (Join-Path $PSScriptRoot "dist\PV-Tagesbericht\PV-Tagesbericht-Server.exe"))) {
    throw "dist\PV-Tagesbericht fehlt. Zuerst .\build_exe.ps1 ausführen."
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }
$version = (& $python -c "import sys; sys.path.insert(0, r'$PSScriptRoot'); import pvapp; print(pvapp.__version__)").Trim()

$candidates = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path $_) }
if (-not $candidates) {
    throw "Inno Setup 6 wurde nicht gefunden. Bitte installieren: https://jrsoftware.org/isdl.php"
}

& $candidates[0] "/DAppVersion=$version" (Join-Path $PSScriptRoot "installer\PV-Tagesbericht.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup ist fehlgeschlagen (Code $LASTEXITCODE)." }
Write-Host "Fertig: dist\installer\PV-Tagesbericht-Setup-$version.exe"
