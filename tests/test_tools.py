import asyncio
from datetime import date

from helpers import FakeTime
from tools.alphaess_fields import describe_fields, mask


class Client:
    def __init__(self, samples):
        self.samples = samples

    async def getOneDateEnergyBySn(self, serial, day):
        return {"epv": 12.3, "eInput": 4.1, "eOutput": 6.0, "sysSn": "SECRET-SN-1", "theDate": day}

    async def getOneDayPowerBySn(self, serial, day):
        return self.samples

    async def getLastPowerData(self, serial):
        return {"ppv": 1500, "ppvDetail": {"ppv1": 800, "ppv2": 700}, "soc": 55}


def run(samples):
    clock = FakeTime()
    lines = asyncio.run(describe_fields(Client(samples), "SN", date(2026, 10, 14), pause_seconds=10, sleep=clock.sleep))
    return "\n".join(lines), clock


def test_fields_are_listed_and_serials_masked():
    samples = [{"uploadTime": "2026-10-14 12:00:00", "ppv": 3000, "cbat": 100, "feedIn": 4000, "sysSn": "SECRET-SN-1"}]
    text, clock = run(samples)
    assert "SECRET-SN-1" not in text and "sysSn: ***" in text
    assert "ppvDetail: (Objekt) Felder: ppv1, ppv2" in text
    assert "cbat: Minimum 100, Maximum 100" in text and "feedIn: Minimum 4000" in text
    assert clock.sleeps == [10, 10]  # API-Schutzpause zwischen den Abfragen


def test_missing_signal_fields_are_called_out():
    text, _ = run([{"uploadTime": "2026-10-14 12:00:00", "ppv": 3000}])
    assert "cbat: NICHT VORHANDEN" in text and "feedIn: NICHT VORHANDEN" in text


def test_mask_hides_identifying_values_only():
    assert mask("sysSn", "AB123") == "***" and mask("minv", "Storion") == "'Storion'" and mask("cbat", 99.5) == "99.5"
