"""Eigenständigen HTML-Bericht aus der Vorlage in site/ erzeugen."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

STYLE_TAG = re.compile(r'<link\b[^>]*data-inline="styles"[^>]*>')
SCRIPT_TAG = re.compile(r'<script\b[^>]*data-inline="script"[^>]*>\s*</script>')


class ReportTemplateError(RuntimeError):
    """Die Vorlage enthält die erwarteten Platzhalter nicht."""


def embed_json(records: list[dict]) -> str:
    """JSON für die Einbettung in ein <script>: '<' wird escaped (kein </script> oder <!-- im Inhalt möglich)."""
    data = json.dumps({"records": records}, ensure_ascii=True, separators=(",", ":"))
    return data.replace("<", "\\u003c")


def build_report_html(records: list[dict], template: str, styles: str, script: str) -> str:
    """Stylesheet und Skript werden eingebettet. Das Skript steht vor </body>: Inline-Skripte ignorieren
    ``defer`` und würden im <head> ausgeführt, bevor die Seitenelemente existieren."""
    html, style_count = STYLE_TAG.subn(lambda _: f"<style>\n{styles}\n</style>", template, count=1)
    if style_count != 1:
        raise ReportTemplateError('Vorlage: <link ... data-inline="styles"> nicht gefunden.')
    html, script_count = SCRIPT_TAG.subn("", html, count=1)
    if script_count != 1:
        raise ReportTemplateError('Vorlage: <script data-inline="script"> nicht gefunden.')
    if html.count("</body>") != 1:
        raise ReportTemplateError("Vorlage: genau ein </body> erwartet.")
    block = f"<script>window.PV_DAILY_DATA = {embed_json(records)};</script>\n<script>\n{script}\n</script>\n"
    # Lambda verhindert, dass Backslashes im Inhalt als Regex-Ersetzung interpretiert werden.
    return re.sub(r"</body>", lambda _: block + "</body>", html, count=1)


def create_standalone_report(records: list[dict], resource_root: Path, report_path: Path) -> Path:
    site = resource_root / "site"
    html = build_report_html(
        records,
        (site / "index.html").read_text(encoding="utf-8"),
        (site / "styles.css").read_text(encoding="utf-8"),
        (site / "dashboard.js").read_text(encoding="utf-8"),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html, encoding="utf-8")
    return report_path


def is_writable_directory(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
        return True
    except OSError:
        return False


def resolve_report_path(configured: Path | None, app_directory: Path, data_directory: Path) -> Path:
    """REPORT_PATH, sonst neben der EXE; ist dieser Ordner nicht beschreibbar (z. B. Program Files),
    wird in den Datenordner ausgewichen."""
    if configured is not None:
        return configured
    if is_writable_directory(app_directory):
        return app_directory / "dashboard.html"
    return data_directory / "dashboard.html"
