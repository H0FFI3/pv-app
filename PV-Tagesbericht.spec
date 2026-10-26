# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spezifikation: ein Ordner mit ZWEI Programmen, die sich alle Bibliotheken teilen.
#   PV-Tagesbericht.exe          mit Konsolenfenster (Aktualisieren, Bericht öffnen, Fehlersuche)
#   PV-Tagesbericht-Server.exe   ohne Konsolenfenster (Startmenü/Autostart: "serve")
# Bauen:  python -m PyInstaller --clean --noconfirm PV-Tagesbericht.spec   (oder .\build_exe.ps1)

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("site", "site")]
binaries = []
hiddenimports = []
for package in ("pvlib", "alphaess"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
datas += collect_data_files("tzdata")

analysis = Analysis(
    ["pv_app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
archive = PYZ(analysis.pure)

console_exe = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PV-Tagesbericht",
    console=True,
    upx=False,
)
server_exe = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PV-Tagesbericht-Server",
    console=False,
    upx=False,
)
COLLECT(
    console_exe,
    server_exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="PV-Tagesbericht",
)
