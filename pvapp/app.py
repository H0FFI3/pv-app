"""Ablauf der App: Historie laden, fehlende Tage erfassen (Backfill), Bericht erzeugen und öffnen."""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from . import model, paths, sources, storage
from .config import Config, ConfigError, load_config
from .interactive import chooser_for_terminal, is_interactive, prompt_credentials
from .logging_setup import REDACTOR, setup_logging
from .report import create_standalone_report, resolve_report_path

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- Planung


@dataclass
class CollectionPlan:
    days: list[date] = field(default_factory=list)
    before_start: bool = False  # gestern liegt noch vor DATA_START_DATE
    skipped_older: int = 0  # fehlende Tage außerhalb des Backfill-Fensters
    up_to_date: bool = False


def plan_days(
    stored: set[date],
    today: date,
    data_start: date,
    max_days: int,
    refresh: bool = False,
) -> CollectionPlan:
    """Bestimmt, welche Tage erfasst werden: alle fehlenden Tage zwischen DATA_START_DATE und gestern,
    höchstens die letzten ``max_days``. Mit ``refresh`` wird zusätzlich gestern neu abgerufen."""
    yesterday = today - timedelta(days=1)
    if yesterday < data_start:
        return CollectionPlan(before_start=True)
    span = (yesterday - data_start).days + 1
    missing = [data_start + timedelta(days=i) for i in range(span) if (data_start + timedelta(days=i)) not in stored]
    if refresh and yesterday in stored:
        missing.append(yesterday)
    skipped = max(0, len(missing) - max_days)
    days = missing[-max_days:] if missing else []
    return CollectionPlan(days=days, skipped_older=skipped, up_to_date=not days)


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
        profile = [
            {
                "hour": timestamp.strftime("%H:00"),
                "actual_kwh": measured.get(timestamp.strftime("%H:00")),
                "potential_kwh": round(float(value), 3),
            }
            for timestamp, value in day_hours.items()
        ]
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
    return result


# ------------------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=paths.APP_NAME,
        description="Erfasst PV-Tageswerte und öffnet den lokalen HTML-Bericht.",
    )
    parser.add_argument("--offline", action="store_true", help="Nur vorhandene lokale Daten anzeigen (kein Abruf).")
    parser.add_argument("--refresh", action="store_true", help="Den Vortag auch dann neu abrufen, wenn er schon gespeichert ist.")
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


def update_history(payload: dict, config: Config, options: argparse.Namespace) -> bool:
    """Erfasst fehlende Tage und aktualisiert ``payload['records']``. Gibt zurück, ob sich etwas geändert hat."""
    today = sources.local_today(config.timezone)
    plan = plan_days(
        storage.stored_dates(payload["records"]),
        today,
        config.data_start_date,
        config.backfill_max_days,
        refresh=options.refresh,
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
    log.info(
        "Datenabruf für %s wird gestartet.",
        ", ".join(day.isoformat() for day in plan.days) if len(plan.days) <= 3 else f"{len(plan.days)} Tage ({plan.days[0]} bis {plan.days[-1]})",
    )
    result = asyncio.run(collect_records(config, plan.days, chooser=chooser_for_terminal()))

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
    return bool(result.records)


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
