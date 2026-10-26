# Baut den Programmordner mit PyInstaller (zwei Programme, siehe PV-Tagesbericht.spec):
#   dist\PV-Tagesbericht\PV-Tagesbericht.exe          (Konsolenfenster)
#   dist\PV-Tagesbericht\PV-Tagesbericht-Server.exe   (ohne Konsolenfenster, für "serve")
# Den Installer erzeugt anschließend .\build_installer.ps1.
$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python).Source
}

Push-Location $PSScriptRoot
try {
    & $python -m PyInstaller --clean --noconfirm (Join-Path $PSScriptRoot "PV-Tagesbericht.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller ist fehlgeschlagen (Code $LASTEXITCODE)." }
} finally {
    Pop-Location
}

Write-Host "Fertig: dist\PV-Tagesbericht\PV-Tagesbericht.exe und PV-Tagesbericht-Server.exe"
