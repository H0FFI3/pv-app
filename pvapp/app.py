"""Ablauf der App: Historie laden, fehlende Tage erfassen (Backfill), Bericht erzeugen und öffnen."""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import os
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from . import curtailment, model, paths, sources, storage
from .config import Config, ConfigError, curtailment_settings_from_env, load_config
from .interactive import chooser_for_terminal, is_interactive, prompt_credentials
from .logging_setup import REDACTOR, setup_logging
from .report import create_standalone_report, resolve_report_path

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- Planung


@dataclass
class CollectionPlan:
    days: list[date] = field(default_factory=list)  # neu bzw. vollständig neu zu erfassen
    signal_days: list[date] = field(default_factory=list)  # nur Akku-/Netzsignale nachladen (Tag bleibt sonst unverändert)
    before_start: bool = False  # gestern liegt noch vor DATA_START_DATE
    skipped_older: int = 0  # fehlende Tage außerhalb des Backfill-Fensters
    up_to_date: bool = False


def plan_days(
    stored: set[date],
    today: date,
    data_start: date,
    max_days: int,
    refresh: bool = False,
    *,
    refresh_days: int = 0,
    signal_candidates: set[date] | frozenset[date] = frozenset(),
) -> CollectionPlan:
    """Bestimmt, was erfasst wird.

    - Alle fehlenden Tage zwischen DATA_START_DATE und gestern, höchstens die letzten ``max_days``.
    - ``refresh`` bzw. ``refresh_days``: zusätzlich die letzten N Tage (gespeicherte) neu abrufen.
    - ``signal_candidates``: gespeicherte Tage ohne Akku-/Netzsignale; nur deren Signale werden nachgeladen
      (ebenfalls begrenzt auf das Fenster der letzten ``max_days`` Tage).
    """
    yesterday = today - timedelta(days=1)
    if yesterday < data_start:
        return CollectionPlan(before_start=True)
    span = (yesterday - data_start).days + 1
    missing = [data_start + timedelta(days=i) for i in range(span) if (data_start + timedelta(days=i)) not in stored]
    refresh_days = max(refresh_days, 1 if refresh else 0)
    for offset in range(refresh_days):
        day = yesterday - timedelta(days=offset)
        if day >= data_start and day in stored:
            missing.append(day)
    missing = sorted(set(missing))
    skipped = max(0, len(missing) - max_days)
    days = missing[-max_days:] if missing else []

    window_start = yesterday - timedelta(days=max_days - 1)
    signal_days = sorted(d for d in signal_candidates if window_start <= d <= yesterday and d not in days)
    return CollectionPlan(days=days, signal_days=signal_days, skipped_older=skipped, up_to_date=not days and not signal_days)


# ------------------------------------------------------------------------- Erfassung


@dataclass
class CollectionResult:
    records: list[dict] = field(default_factory=list)
    skipped: dict[date, str] = field(default_factory=dict)


async def collect_records(
    config: Config,
    days: list[date],
    *,
    include_hourly: bool = True,
    chooser: Callable[[list[dict]], dict] | None = None,
    load_weather_fn: Callable[..., sources.WeatherData] = sources.load_weather,
    fetch_actuals_fn: Callable[..., Any] = sources.fetch_actuals,
    load_horizon_fn: Callable[..., pd.Series | None] = sources.load_horizon,
) -> CollectionResult:
    """Erfasst Datensätze für ``days``. Einzelne Tage ohne Daten werden übersprungen (nicht der ganze Lauf)."""
    result = CollectionResult()
    if not days:
        return result
    days = sorted(days)

    log.info("Wetter: rufe Strahlungs- und Sonnenscheindaten ab ...")
    weather = load_weather_fn(config, days[0], days[-1])
    computable = []
    for day in days:
        if day in weather.source_by_day:
            computable.append(day)
        else:
            result.skipped[day] = "Wetterdaten sind noch nicht vollständig verfügbar"
    if not computable:
        return result

    actual = await fetch_actuals_fn(config, computable, include_hourly=include_hourly, chooser=chooser)

    log.info("Modell: berechne das wetterbasierte PV-Potenzial ...")
    horizon = load_horizon_fn(config, paths.horizon_cache_path()) if config.horizon_enabled else None
    hourly_potential = model.estimate_hourly_potential(
        weather.frame, weather.elevation, actual.inverter_power_kw, config, horizon
    )
    wall_potential = model.hourly_wall_clock(hourly_potential)
    wall_days = wall_potential.index.date

    for day in computable:
        if day in actual.errors:
            result.skipped[day] = actual.errors[day]
            continue
        day_hours = wall_potential[wall_days == day].dropna()
        if day_hours.empty:
            result.skipped[day] = "Für den Tag konnte kein Potenzial berechnet werden"
            continue
        measured = actual.hourly.get(day, {})
        signals = actual.signals.get(day, {})
        profile = []
        for timestamp, value in day_hours.items():
            label = timestamp.strftime("%H:00")
            point = {"hour": label, "actual_kwh": measured.get(label), "potential_kwh": round(float(value), 3)}
            point.update(signals.get(label, {}))  # soc_pct / feed_in_w, soweit AlphaESS sie liefert
            profile.append(point)
        result.records.append(
            {
                "date": day.isoformat(),
                "actual_kwh": round(actual.energy[day], 2),
                "potential_kwh": round(float(day_hours.sum()), 2),
                "sunshine_hours": round(weather.sunshine_hours[day], 2),
                "weather_source": weather.source_by_day[day],
                "hourly": profile,
            }
        )
        if day in actual.signals:  # Leistungsdaten wurden erfolgreich abgerufen (auch wenn ohne Akku-/Netzfelder)
            result.records[-1]["signals_checked"] = True
    return result


async def collect_signals(
    config: Config,
    days: list[date],
    *,
    chooser: Callable[[list[dict]], dict] | None = None,
    fetch_actuals_fn: Callable[..., Any] = sources.fetch_actuals,
) -> dict[date, dict[str, dict[str, float]]]:
    """Lädt nur die Akku-/Netzsignale für bereits gespeicherte Tage. Rückgabe: Tag -> Stunde -> Signale,
    nur für Tage, deren Leistungsdaten erfolgreich abgerufen wurden (Ergebnis kann leer sein)."""
    if not days:
        return {}
    actual = await fetch_actuals_fn(config, sorted(days), include_hourly=True, chooser=chooser)
    return {day: actual.signals[day] for day in days if day in actual.signals}


# ------------------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=paths.APP_NAME,
        description="Erfasst PV-Tageswerte und öffnet den lokalen HTML-Bericht.",
    )
    parser.add_argument("--offline", action="store_true", help="Nur vorhandene lokale Daten anzeigen (kein Abruf).")
    parser.add_argument("--refresh", action="store_true", help="Den Vortag auch dann neu abrufen, wenn er schon gespeichert ist.")
    parser.add_argument("--refresh-days", type=int, metavar="N", help="Die letzten N Tage neu abrufen (1 bis 92), auch wenn sie schon gespeichert sind.")
    parser.add_argument("--no-open", action="store_true", help="Bericht erzeugen, aber nicht im Browser öffnen (z. B. für Aufgabenplanung).")
    parser.add_argument("--verbose", action="store_true", help="Ausführliche Protokollierung.")
    return parser


def _try_config() -> tuple[Config | None, str | None]:
    try:
        config = load_config()
    except ConfigError as error:
        return None, str(error)
    REDACTOR.add(*config.secrets)
    return config, None


def _fallback_curtailment() -> curtailment.CurtailmentSettings:
    """Ohne vollständige Konfiguration (z. B. --offline): Einstellungen aus der Umgebung, sonst Standardwerte."""
    try:
        return curtailment_settings_from_env(os.environ)
    except ConfigError as error:
        log.warning("%s Es gelten die Standardwerte der Abregelungserkennung.", error)
        return curtailment.CurtailmentSettings()


def update_history(payload: dict, config: Config, options: argparse.Namespace) -> bool:
    """Erfasst fehlende Tage und aktualisiert ``payload['records']``. Gibt zurück, ob sich etwas geändert hat."""
    today = sources.local_today(config.timezone)
    refresh_days = options.refresh_days or 0
    if not 0 <= refresh_days <= 92:
        raise ConfigError("--refresh-days erwartet eine Zahl von 1 bis 92.")
    plan = plan_days(
        storage.stored_dates(payload["records"]),
        today,
        config.data_start_date,
        config.backfill_max_days,
        refresh=options.refresh,
        refresh_days=refresh_days,
        signal_candidates=storage.days_missing_signals(payload["records"]) if config.curtailment.enabled else frozenset(),
    )
    if plan.before_start:
        first_full = config.data_start_date + timedelta(days=1)
        log.info("Die Erfassung beginnt am %s.", config.data_start_date.strftime("%d.%m.%Y"))
        log.info("Der erste vollständige Tagesdatensatz ist am %s verfügbar.", first_full.strftime("%d.%m.%Y"))
        return False
    if plan.up_to_date:
        log.info("Alle Tage bis gestern sind bereits gespeichert; kein Abruf nötig.")
        return False
    if plan.skipped_older:
        log.warning(
            "%d ältere fehlende Tage werden nicht nachgeholt (Backfill-Fenster: %d Tage, BACKFILL_MAX_DAYS).",
            plan.skipped_older,
            config.backfill_max_days,
        )

    config = prompt_credentials(config)
    REDACTOR.add(*config.secrets)
    if plan.days:
        log.info(
            "Datenabruf für %s wird gestartet.",
            ", ".join(day.isoformat() for day in plan.days) if len(plan.days) <= 3 else f"{len(plan.days)} Tage ({plan.days[0]} bis {plan.days[-1]})",
        )
    if plan.signal_days:
        log.info(
            "Akku-/Netzdaten für die Abregelungserkennung werden für %d bereits gespeicherte Tage nachgeladen (%s).",
            len(plan.signal_days),
            ", ".join(day.isoformat() for day in plan.signal_days),
        )

    async def gather():
        collected = await collect_records(config, plan.days, chooser=chooser_for_terminal()) if plan.days else CollectionResult()
        if plan.days and plan.signal_days:
            await asyncio.sleep(config.api_request_interval_seconds)  # API-Schutzpause zwischen zwei Anmeldungen
        signals = await collect_signals(config, plan.signal_days, chooser=chooser_for_terminal()) if plan.signal_days else {}
        return collected, signals

    result, signal_result = asyncio.run(gather())

    for record in result.records:
        payload["records"] = storage.upsert_record(payload["records"], record)
        log.info(
            "Erfasst: %s | Ist %.2f kWh | Potenzial %.2f kWh | Sonne %.2f h (Wetter: %s)",
            record["date"],
            record["actual_kwh"],
            record["potential_kwh"],
            record["sunshine_hours"],
            record["weather_source"],
        )
    for day, reason in sorted(result.skipped.items()):
        log.warning("Übersprungen: %s – %s. Beim nächsten Start wird es erneut versucht.", day.isoformat(), reason)

    for day, signals in sorted(signal_result.items()):
        payload["records"] = storage.apply_signals(payload["records"], day, signals)
        if signals:
            log.info("Akku-/Netzdaten nachgeladen: %s (%d Stunden mit Signalen).", day.isoformat(), len(signals))
        else:
            log.warning("AlphaESS liefert für %s keine Akku-/Netzdaten mehr; der Tag wird nicht erneut abgefragt.", day.isoformat())
    for day in sorted(set(plan.signal_days) - set(signal_result)):
        log.warning("Nachladen für %s nicht möglich; beim nächsten Start wird es erneut versucht.", day.isoformat())
    return bool(result.records) or bool(signal_result)


def run(options: argparse.Namespace) -> int:
    log.info("PV-Tagesbericht wird gestartet.")
    data_path = paths.data_path()
    payload = storage.load_payload(data_path)
    original = copy.deepcopy(payload)
    log.info("%d lokale Tagesdatensätze geladen.", len(payload["records"]))

    config, config_error = _try_config()
    exit_code = 0
    if options.offline:
        if config_error:
            log.info("Konfiguration unvollständig; es werden alle gespeicherten Datensätze angezeigt.")
    elif config is None:
        log.error("Aktualisierung fehlgeschlagen: %s", config_error)
        log.info("Der Bericht wird mit den zuletzt lokal gespeicherten Daten geöffnet.")
        exit_code = 1
    else:
        has_credentials = bool(config.alphaess_app_id and config.alphaess_app_secret)
        log.info(
            "AlphaESS-Zugangsdaten gefunden; Werte werden nicht angezeigt."
            if has_credentials
            else "AlphaESS-Zugangsdaten fehlen oder sind unvollständig."
        )
        try:
            update_history(payload, config, options)
        except Exception as error:  # Netzwerk-, API- oder Modellfehler: mit lokalen Daten weiterarbeiten
            log.error("Aktualisierung fehlgeschlagen: %s", error)
            log.debug("Details", exc_info=True)
            log.info("Der Bericht wird mit den zuletzt lokal gespeicherten Daten geöffnet.")
            exit_code = 1

    if payload != original:
        storage.save_payload(payload, data_path)

    display = storage.valid_records(payload["records"], config.data_start_date if config else None)
    display = curtailment.annotate_records(display, config.curtailment if config else _fallback_curtailment())
    flagged = sum(r.get("curtailment", {}).get("hours", 0) for r in display)
    if flagged:
        log.info("Abregelung: %d Stunde(n) in der Historie als wahrscheinlich abgeregelt markiert.", flagged)
    log.info("Erzeuge eigenständigen HTML-Bericht ...")
    report_path = resolve_report_path(config.report_path if config else None, paths.app_directory(), paths.app_data_directory())
    create_standalone_report(display, paths.resource_root(), report_path)
    log.info("HTML-Bericht erstellt: %s", report_path)
    log.info("Lokale Daten: %s", data_path)
    if not options.no_open:
        log.info("Öffne Bericht im Standardbrowser ...")
        if not webbrowser.open(report_path.resolve().as_uri()):
            log.warning("Browser konnte nicht gestartet werden. Datei manuell öffnen: %s", report_path)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    setup_logging(paths.log_directory(), verbose=options.verbose)
    exit_code = 1
    try:
        exit_code = run(options)
    except Exception:  # Unerwartet: sichtbar machen, nicht still schließen
        log.exception("Unerwarteter Fehler. Details stehen in der Logdatei: %s", paths.log_directory())
    if is_interactive() and not options.no_open:
        try:
            input("Eingabetaste zum Schließen drücken ...")
        except EOFError:
            pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
