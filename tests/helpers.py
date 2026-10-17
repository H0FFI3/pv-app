"""Hilfen für Tests: synthetisches Wetter und Fake-Quellen (keine Netzwerkzugriffe)."""

from datetime import date, timedelta

import pandas as pd
import pvlib


def clear_sky_weather(latitude, longitude, timezone, start: date, end: date, altitude=300.0, temp=15.0, wind=2.0):
    """Stündliches Klarhimmel-Wetter; Index = Intervall-Mittelpunkte (HH:30), wie bei Open-Meteo."""
    index = pd.date_range(
        pd.Timestamp(start).tz_localize(timezone) + pd.Timedelta(minutes=30),
        pd.Timestamp(end + timedelta(days=1)).tz_localize(timezone) - pd.Timedelta(minutes=30),
        freq="h",
    )
    location = pvlib.location.Location(latitude, longitude, altitude=altitude, tz=timezone)
    sky = location.get_clearsky(index)
    return pd.DataFrame(
        {"temp_air": temp, "wind_speed": wind, "ghi": sky["ghi"], "dni": sky["dni"], "dhi": sky["dhi"]},
        index=index,
    )


class FakeTime:
    """Manuelle Uhr: ``sleep`` verschiebt die Zeit und protokolliert die Pausen."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


class FakeAlphaess:
    def __init__(self, systems=None, energy=None, power=None, fail_power=False):
        self.systems = systems if systems is not None else [{"sysSn": "SN123456", "poinv": 8.0, "minv": "Test"}]
        self.energy = energy or {}
        self.power = power or {}
        self.fail_power = fail_power
        self.closed = False
        self.calls = []

    async def getESSList(self):
        self.calls.append("list")
        return self.systems

    async def getOneDateEnergyBySn(self, serial, day):
        self.calls.append(("energy", day))
        return self.energy.get(day)

    async def getOneDayPowerBySn(self, serial, day):
        self.calls.append(("power", day))
        if self.fail_power:
            raise RuntimeError("Leistungsdaten nicht verfügbar")
        return self.power.get(day, [])

    async def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def samples_for_day(day: date, start_hour=6, end_hour=18, watts=1200, step_minutes=5):
    """5-Minuten-Leistungsproben (lokale Uhrzeit als Text, wie von AlphaESS geliefert)."""
    result = []
    moment = pd.Timestamp(day) + pd.Timedelta(hours=start_hour)
    stop = pd.Timestamp(day) + pd.Timedelta(hours=end_hour)
    while moment < stop:
        result.append({"uploadTime": moment.strftime("%Y-%m-%d %H:%M:%S"), "ppv": watts})
        moment += pd.Timedelta(minutes=step_minutes)
    return result
