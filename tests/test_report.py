import json

import pytest

from pvapp import report
from pvapp.paths import project_root

TEMPLATE = (
    "<html><head>"
    '<link rel="stylesheet" href="styles.css" data-inline="styles">'
    '<script src="dashboard.js" defer data-inline="script"></script>'
    "</head><body><main id=\"seite\"></main></body></html>"
)


def test_assets_and_data_are_inlined():
    html = report.build_report_html([{"date": "2026-10-13"}], TEMPLATE, "body{}", "console.log(1)")
    assert "<style>\nbody{}\n</style>" in html
    assert 'window.PV_DAILY_DATA = {"records":[{"date":"2026-10-13"}]}' in html
    assert html.index("PV_DAILY_DATA") < html.index("console.log(1)")  # Daten vor dem Skript
    assert 'href="styles.css"' not in html and 'src="dashboard.js"' not in html


def test_script_runs_after_the_page_elements_exist():
    """Regression: im <head> eingebettet lief das Skript vor dem DOM und fand keine Elemente."""
    html = report.build_report_html([], TEMPLATE, "", "console.log(1)")
    assert html.index('id="seite"') < html.index("PV_DAILY_DATA") < html.index("console.log(1)") < html.index("</body>")
    assert html.count("<script") == 2 and html.index("<script") > html.index("</head>")


def test_template_needs_exactly_one_body_end():
    with pytest.raises(report.ReportTemplateError, match="</body>"):
        report.build_report_html([], TEMPLATE.replace("</body>", ""), "", "")


def test_data_cannot_break_out_of_the_script_element():
    html = report.build_report_html([{"date": "</script><script>alert(1)</script><!--"}], TEMPLATE, "", "")
    assert "</script><script>alert" not in html and "<!--" not in html
    payload = html.split("window.PV_DAILY_DATA = ")[1].split(";</script>")[0]
    assert json.loads(payload)["records"][0]["date"].startswith("</script>")  # \u003c wird von JSON/JS korrekt gelesen


@pytest.mark.parametrize(
    "broken",
    [TEMPLATE.replace(' data-inline="styles"', ""), TEMPLATE.replace(' data-inline="script"', "")],
)
def test_missing_placeholder_fails_loudly(broken):
    with pytest.raises(report.ReportTemplateError):
        report.build_report_html([], broken, "", "")


def test_replacement_content_with_backslashes_is_inserted_literally():
    html = report.build_report_html([], TEMPLATE, r"a{content:'\201C'}", r"const re = /\d+\1/;")
    assert r"content:'\201C'" in html and r"/\d+\1/" in html


def test_real_site_template_produces_a_standalone_file(tmp_path):
    target = report.create_standalone_report([], project_root(), tmp_path / "out" / "dashboard.html")
    html = target.read_text(encoding="utf-8")
    assert "<style>" in html and "PV_DAILY_DATA" in html
    assert 'href="styles.css"' not in html and 'src="dashboard.js"' not in html


def test_report_path_falls_back_when_app_directory_is_not_writable(tmp_path):
    blocked = tmp_path / "datei.txt"
    blocked.write_text("x")  # ein Ordner kann hier nicht angelegt werden
    data = tmp_path / "data"
    assert report.resolve_report_path(None, blocked / "app", data) == data / "dashboard.html"
    assert report.resolve_report_path(None, tmp_path / "app", data) == tmp_path / "app" / "dashboard.html"
    assert report.resolve_report_path(tmp_path / "x.html", blocked, data) == tmp_path / "x.html"
