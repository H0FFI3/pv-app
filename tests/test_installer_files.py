"""Konsistenzprüfungen für Spec, Build-Skripte und Installer (der Installer selbst lässt sich nur unter Windows bauen)."""

import re

import pvapp
from pvapp.paths import APP_NAME, project_root

ROOT = project_root()
SPEC = (ROOT / "PV-Tagesbericht.spec").read_text(encoding="utf-8")
ISS_BYTES = (ROOT / "installer" / "PV-Tagesbericht.iss").read_bytes()
ISS = ISS_BYTES.decode("utf-8-sig")


def test_iss_is_utf8_with_bom_because_inno_would_garble_umlauts_otherwise():
    assert ISS_BYTES.startswith(b"\xef\xbb\xbf")


def test_spec_builds_a_console_and_a_windowless_program_from_one_bundle():
    assert re.search(r'name="PV-Tagesbericht",\s*console=True', SPEC)
    assert re.search(r'name="PV-Tagesbericht-Server",\s*console=False', SPEC)
    assert re.search(r'COLLECT\([^)]*name="PV-Tagesbericht",\s*\)', SPEC, re.S)  # Ausgabeordner dist\\PV-Tagesbericht
    assert '("site", "site")' in SPEC and "collect_all" in SPEC and "tzdata" in SPEC


def test_installer_uses_the_program_names_and_folder_of_the_spec():
    assert '#define ServerExe "PV-Tagesbericht-Server.exe"' in ISS
    assert r'Source: "..\dist\PV-Tagesbericht\*"' in ISS


def test_installer_version_default_matches_the_package():
    assert re.search(r'#define AppVersion "([^"]+)"', ISS).group(1) == pvapp.__version__


def test_installer_data_folder_matches_the_apps_data_folder():
    assert APP_NAME == "PV-Tagesbericht"
    assert r"{localappdata}\PV-Tagesbericht" in ISS


def test_installer_is_per_user_and_starts_the_server_headless_on_login():
    assert "PrivilegesRequired=lowest" in ISS and "PrivilegesRequiredOverridesAllowed" not in ISS
    autostart = next(line for line in ISS.splitlines() if line.startswith("Name: \"{autostartup}"))
    assert 'Parameters: "serve --no-browser"' in autostart and "Tasks: autostart" in autostart
    assert re.search(r'Name: "autostart".*(?<!unchecked)$', next(line for line in ISS.splitlines() if line.startswith('Name: "autostart"')))
    run = next(line for line in ISS.splitlines() if line.startswith("Filename: \"{app}"))
    assert 'Parameters: "serve"' in run and "postinstall" in run


def test_installer_stops_the_server_before_replacing_files_and_asks_before_deleting_data():
    assert "taskkill.exe" in ISS and "PrepareToInstall" in ISS and "usUninstall" in ISS
    assert "UninstallSilent()" in ISS and "MB_DEFBUTTON2" in ISS  # Datenlöschung nie automatisch, Standard "Nein"


def test_app_id_is_a_guid():
    assert re.search(r"AppId=\{\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", ISS)


def test_build_scripts_point_at_existing_files():
    exe = (ROOT / "build_exe.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "build_installer.ps1").read_text(encoding="utf-8")
    assert "PV-Tagesbericht.spec" in exe and (ROOT / "PV-Tagesbericht.spec").exists()
    assert r"installer\PV-Tagesbericht.iss" in installer and (ROOT / "installer" / "PV-Tagesbericht.iss").exists()
    assert "/DAppVersion=" in installer and "pvapp.__version__" in installer


def test_the_spec_file_is_not_swallowed_by_gitignore():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.spec" in ignore and "!PV-Tagesbericht.spec" in ignore and ignore.index("!PV-Tagesbericht.spec") > ignore.index("*.spec")
