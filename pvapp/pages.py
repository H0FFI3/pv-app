"""HTML der Einrichtungsseite und der Statusleiste im Bericht (Serve-Modus). Keine externen Ressourcen."""

from __future__ import annotations

import html
import json
from typing import Iterable, Mapping

from .settings import GROUPS, Field, fields

SETUP_STYLE = """
:root { color-scheme: light dark; --bg:#f6f8f6; --card:#fff; --text:#16231f; --muted:#5b6b65; --line:#d8e0db; --green:#197459; --red:#b3261e; --orange:#dc7b52; }
@media (prefers-color-scheme: dark) { :root { --bg:#111815; --card:#1a2320; --text:#e6efe9; --muted:#9bada5; --line:#2c3a35; --green:#3aa37f; --red:#f2776f; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 "Trebuchet MS", system-ui, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 28px 18px 60px; }
h1 { font-size: 24px; margin: 0 0 6px; } p.lead { color: var(--muted); margin: 0 0 18px; }
fieldset { border:1px solid var(--line); background:var(--card); border-radius:10px; margin:0 0 16px; padding:14px 16px 6px; }
legend { padding:0 6px; font-weight:700; color:var(--green); }
.field { margin: 0 0 14px; } label { display:block; font-weight:600; margin-bottom:3px; }
.row { display:flex; align-items:center; gap:8px; }
input[type=text], input[type=password], input[type=date], input[type=time] { width:100%; max-width:340px; padding:8px 10px; border:1px solid var(--line); border-radius:7px; background:transparent; color:inherit; font:inherit; }
input.invalid { border-color: var(--red); outline: 1px solid var(--red); }
.unit { color: var(--muted); } .help { color: var(--muted); font-size: 13px; margin-top: 3px; }
.check { display:flex; gap:8px; align-items:flex-start; } .check label { font-weight:400; margin:0; }
.box { border-radius:8px; padding:10px 14px; margin:0 0 16px; border:1px solid var(--line); background:var(--card); }
.box.error { border-color: var(--red); } .box.warn { border-color: var(--orange); } .box.ok { border-color: var(--green); }
button { font:inherit; padding:9px 16px; border-radius:8px; border:1px solid var(--green); cursor:pointer; background:var(--green); color:#fff; }
button.secondary { background:transparent; color:var(--green); } button:disabled { opacity:.6; cursor:wait; }
.actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:6px; }
a { color: var(--green); } small.note { color: var(--muted); }
"""

SETUP_SCRIPT = """
const TOKEN = __TOKEN__;
const out = document.getElementById('test-result');
const field = (name) => document.querySelector('[name="' + name + '"]');
document.getElementById('test-button').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  out.className = 'box'; out.hidden = false; out.textContent = 'Verbindung wird geprüft … (kann bis zu 25 Sekunden dauern)';
  try {
    const response = await fetch('/api/test-alphaess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PV-Token': TOKEN },
      body: JSON.stringify({ app_id: field('ALPHAESS_APP_ID').value, app_secret: field('ALPHAESS_APP_SECRET').value, serial: field('ALPHAESS_SYSTEM_SN').value }),
    });
    const data = await response.json();
    out.className = 'box ' + (data.ok ? 'ok' : 'error');
    const lines = [data.message];
    (data.systems || []).forEach((system) => {
      const parts = [system.model, system.pv_kwp != null ? system.pv_kwp + ' kWp PV' : null, system.inverter_kw != null ? 'Wechselrichter ' + system.inverter_kw + ' kW' : null, system.battery_kwh != null ? 'Akku ' + system.battery_kwh + ' kWh' : null];
      lines.push('• ' + parts.filter(Boolean).join(' · '));
    });
    const modules = (Number(field('MODULES_EAST').value) || 0) + (Number(field('MODULES_WEST').value) || 0);
    const watt = Number(String(field('MODULE_POWER_WP').value).replace(',', '.')) || 0;
    const mine = (modules * watt) / 1000;
    const reported = data.systems && data.systems.length === 1 ? Number(data.systems[0].pv_kwp) : null;
    if (data.ok && mine > 0 && reported) {
      lines.push(Math.abs(mine - reported) / reported > 0.15
        ? 'Hinweis: Deine Modulangaben ergeben ' + mine.toFixed(2).replace('.', ',') + ' kWp, AlphaESS meldet ' + reported + ' kWp. Bitte Modulanzahl und -leistung prüfen.'
        : 'Deine Modulangaben (' + mine.toFixed(2).replace('.', ',') + ' kWp) passen zur Anlage.');
    }
    out.textContent = '';
    lines.forEach((line) => { const p = document.createElement('div'); p.textContent = line; out.append(p); });
  } catch (error) {
    out.className = 'box error'; out.textContent = 'Die Prüfung ist fehlgeschlagen: ' + error;
  } finally { button.disabled = false; }
});
"""


def _escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def _field_html(field: Field, value: str, invalid: bool, has_secret: bool) -> str:
    name = _escape(field.name)
    css = ' class="invalid"' if invalid else ""
    required = " required" if field.required and not field.secret and field.kind != "checkbox" else ""
    help_html = f'<div class="help" id="help-{name}">{_escape(field.help)}</div>' if field.help else ""
    described = f' aria-describedby="help-{name}"' if field.help else ""
    if field.kind == "checkbox":
        checked = " checked" if value else ""
        return (
            f'<div class="field"><div class="check"><input type="checkbox" id="f-{name}" name="{name}" value="on"{checked}{described}>'
            f'<label for="f-{name}">{_escape(field.label)}</label></div>{help_html}</div>'
        )
    placeholder = "(gespeichert)" if field.secret and has_secret else field.placeholder
    shown = "" if field.secret else value
    input_type = {"password": "password", "date": "date", "time": "time"}.get(field.kind, "text")
    inputmode = ' inputmode="decimal"' if field.kind == "text" and (field.percent or field.unit in {"°", "Wp", "W", "°C", "%", "%/K"}) else ""
    unit = f'<span class="unit">{_escape(field.unit)}</span>' if field.unit else ""
    autocomplete = ' autocomplete="off"' if field.secret or field.name.startswith("ALPHAESS") else ""
    return (
        f'<div class="field"><label for="f-{name}">{_escape(field.label)}</label>'
        f'<div class="row"><input type="{input_type}" id="f-{name}" name="{name}" value="{_escape(shown)}" '
        f'placeholder="{_escape(placeholder)}"{inputmode}{autocomplete}{required}{css}{described}>{unit}</div>{help_html}</div>'
    )


def setup_page(
    token: str,
    values: Mapping[str, str],
    *,
    error_text: str = "",
    error_fields: Iterable[str] = (),
    has_secret: bool = False,
    shadowing: Iterable[str] = (),
    configured: bool = False,
) -> str:
    invalid = set(error_fields)
    groups = []
    for group in GROUPS:
        body = "".join(
            _field_html(f, values.get(f.name, f.default), f.name in invalid, has_secret) for f in fields() if f.group == group
        )
        if group == "Erweitert":
            groups.append(f'<details><summary style="cursor:pointer;margin:0 0 12px;font-weight:700;color:var(--green)">Erweitert (Abregelung und Modell)</summary><fieldset><legend>{_escape(group)}</legend>{body}</fieldset></details>')
        else:
            groups.append(f"<fieldset><legend>{_escape(group)}</legend>{body}</fieldset>")

    notices = ""
    if error_text:
        notices += f'<div class="box error" role="alert"><strong>Bitte prüfen:</strong> {_escape(error_text)}</div>'
    if shadowing:
        listed = ", ".join(_escape(path) for path in shadowing)
        notices += (
            f'<div class="box warn">Es gibt zusätzlich eine .env-Datei ({listed}). '
            "Ihre Werte haben Vorrang vor dieser Seite. Änderungen hier wirken nur für Einstellungen, die dort fehlen.</div>"
        )
    back = '<a href="/">Zurück zum Bericht</a>' if configured else ""
    script = SETUP_SCRIPT.replace("__TOKEN__", json.dumps(token).replace("<", "\\u003c"))
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PV-Tagesbericht · Einrichtung</title><style>{SETUP_STYLE}</style></head>
<body><main>
<h1>PV-Tagesbericht · Einrichtung</h1>
<p class="lead">Trage deine Anlagendaten ein. Alles bleibt auf diesem Rechner; die App ruft nur AlphaESS und Open-Meteo ab. {back}</p>
{notices}
<form method="post" action="/setup" autocomplete="off">
<input type="hidden" name="token" value="{_escape(token)}">
{"".join(groups)}
<div id="test-result" class="box" hidden role="status"></div>
<div class="actions">
<button type="button" class="secondary" id="test-button">Verbindung testen</button>
<button type="submit">Speichern und Daten abrufen</button>
</div>
<p><small class="note">Die Zugangsdaten werden in der Datei settings.env in deinem Benutzerordner gespeichert (im Klartext, nur für dein Benutzerkonto lesbar). Gib diese Datei nicht weiter.</small></p>
</form>
</main><script>{script}</script></body></html>"""


def server_bar(token: str, status: dict, schedule_text: str) -> str:
    """Statusleiste für den Bericht im Serve-Modus (nur für lokale Zugriffe)."""
    data = json.dumps({"token": token, "status": status, "schedule": schedule_text}).replace("<", "\\u003c")
    return f"""<div id="pv-bar" role="status" aria-live="polite">
<span id="pv-bar-text"></span>
<button type="button" id="pv-bar-update">Jetzt aktualisieren</button>
<a href="/setup">Einstellungen</a>
<button type="button" id="pv-bar-quit" class="quiet" title="Beendet den Server; die automatische Aktualisierung pausiert, bis er wieder gestartet wird.">Beenden</button>
</div>
<style>
#pv-bar {{ position: fixed; right: 12px; bottom: 12px; z-index: 50; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; max-width: calc(100vw - 24px);
  padding: 8px 12px; border-radius: 10px; border: 1px solid #c8d3cd; background: #ffffff; color: #16231f; font: 12px/1.4 "Trebuchet MS", system-ui, sans-serif; box-shadow: 0 2px 10px rgb(0 0 0 / 12%); }}
#pv-bar button {{ font: inherit; padding: 4px 10px; border-radius: 7px; border: 1px solid #197459; background: #197459; color: #fff; cursor: pointer; }}
#pv-bar button.quiet {{ background: transparent; color: inherit; border-color: #8a9a93; }}
#pv-bar button:disabled {{ opacity: .6; cursor: wait; }} #pv-bar a {{ color: #197459; }} #pv-bar.bad {{ border-color: #b3261e; }}
@media (prefers-color-scheme: dark) {{ #pv-bar {{ background: #1a2320; color: #e6efe9; border-color: #2c3a35; }} #pv-bar a {{ color: #6fd1ad; }} }}
@media print {{ #pv-bar {{ display: none; }} }}
</style>
<script>
(() => {{
  const init = {data};
  const bar = document.getElementById('pv-bar'), text = document.getElementById('pv-bar-text'), button = document.getElementById('pv-bar-update');
  let wasRunning = init.status.running;
  let polling = true;
  const show = (status, schedule) => {{
    text.textContent = status.text + (schedule && !status.running ? ' · ' + schedule : '');
    bar.classList.toggle('bad', status.ok === false);
    button.disabled = status.running;
  }};
  const poll = async () => {{
    if (!polling) return;
    try {{
      const data = await (await fetch('/api/status', {{ headers: {{ 'X-PV-Token': init.token }} }})).json();
      show(data.status, data.schedule);
      if (wasRunning && !data.status.running) {{ location.reload(); return; }}
      wasRunning = data.status.running;
      setTimeout(poll, data.status.running ? 2000 : 15000);
    }} catch (error) {{ setTimeout(poll, 15000); }}
  }};
  button.addEventListener('click', async () => {{
    button.disabled = true;
    try {{ await fetch('/api/update', {{ method: 'POST', headers: {{ 'X-PV-Token': init.token }} }}); }} catch (error) {{ /* Status-Abfrage zeigt den Zustand */ }}
    wasRunning = true; poll();
  }});
  const quit = document.getElementById('pv-bar-quit');
  let armed = null;
  quit.addEventListener('click', async () => {{
    if (!armed) {{
      quit.textContent = 'Wirklich beenden?';
      armed = setTimeout(() => {{ armed = null; quit.textContent = 'Beenden'; }}, 5000);
      return;
    }}
    clearTimeout(armed);
    quit.disabled = true;
    try {{ await fetch('/api/shutdown', {{ method: 'POST', headers: {{ 'X-PV-Token': init.token }} }}); }} catch (error) {{ /* Server ist schon weg */ }}
    text.textContent = 'Server beendet. Zum Starten PV-Tagesbericht im Startmenü öffnen. Dieses Fenster kann geschlossen werden.';
    button.disabled = true; wasRunning = false; polling = false;
  }});
  show(init.status, init.schedule);
  setTimeout(poll, init.status.running ? 2000 : 15000);
}})();
</script>"""
