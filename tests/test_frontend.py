"""Startet die optionalen jsdom-Tests für site/dashboard.js (benötigt Node.js und `npm install` in tests/frontend)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent / "frontend"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
@pytest.mark.skipif(not (FRONTEND / "node_modules" / "jsdom").exists(), reason="jsdom fehlt: cd tests/frontend && npm install")
def test_dashboard_in_jsdom(tmp_path):
    subprocess.run([sys.executable, str(FRONTEND / "make_fixtures.py"), str(tmp_path)], check=True)
    result = subprocess.run(
        ["node", "--test", "dashboard.test.mjs"],
        cwd=FRONTEND,
        env={**os.environ, "PV_FIXTURE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
