"""GET /api/dining/busy: the latest hall cam reading per hall, from umass_dining.occupancy."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from mealscholar.busy import Busy
from mealscholar.config import load_settings
from mealscholar.web import create_app

NOW = datetime(2026, 11, 14, 22, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self, docs=(), error=None):
        self.docs = list(docs)
        self.error = error
        self.calls = []

    def latest(self, since):
        self.calls.append(since)
        if self.error:
            raise self.error
        return [{"_id": d["location_id"], "doc": d} for d in self.docs]


def reading(location_id, name, level, people, minutes_ago=2, trend="steady"):
    taken = (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None)  # pymongo hands back naive UTC
    return {
        "location_id": location_id,
        "location_name": name,
        "level": level,
        "people": people,
        "trend": trend,
        "ts": taken,
    }


class Ticker:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def client_for(tmp_path, busy):
    settings = load_settings({"MEALSCHOLAR_HOME": str(tmp_path), "MEALSCHOLAR_AUTH_REQUIRED": "0"})
    return TestClient(create_app(settings, busy=busy))


def test_latest_reading_per_hall(tmp_path):
    source = FakeSource(
        [
            reading(4, "Berkshire", "busy", 31, trend="rising"),
            reading(1, "Worcester", "quiet", 8),
            reading(3, "Hampshire", "unknown", 0),  # the cam couldn't be read: left out
            reading(2, "Franklin", "moderate", 12, trend="sideways"),
        ]
    )
    res = client_for(tmp_path, Busy(source, clock=lambda: NOW)).get("/api/dining/busy")
    assert res.status_code == 200
    at = "2026-11-14T21:58:00Z"
    assert res.json()["halls"] == [
        {"location_id": 1, "name": "Worcester", "level": "quiet", "people": 8, "trend": "steady", "updated_at": at},
        {"location_id": 2, "name": "Franklin", "level": "moderate", "people": 12, "trend": None, "updated_at": at},
        {"location_id": 4, "name": "Berkshire", "level": "busy", "people": 31, "trend": "rising", "updated_at": at},
    ]
    assert source.calls == [NOW - timedelta(minutes=15)]  # only readings from the last 15 minutes


def test_readings_are_cached_for_30_seconds(tmp_path):
    source, ticker = FakeSource([reading(1, "Worcester", "quiet", 8)]), Ticker()
    client = client_for(tmp_path, Busy(source, clock=lambda: NOW, monotonic=ticker))
    client.get("/api/dining/busy")
    ticker.t = 29
    client.get("/api/dining/busy")
    assert len(source.calls) == 1
    ticker.t = 31
    client.get("/api/dining/busy")
    assert len(source.calls) == 2


@pytest.mark.parametrize(
    ("busy", "detail"),
    [
        (None, "Hall cams aren't connected on this server."),
        (Busy(FakeSource(error=ServerSelectionTimeoutError("no servers"))), "Couldn't read the hall cams right now."),
    ],
)
def test_503_without_cam_readings(tmp_path, busy, detail):
    res = client_for(tmp_path, busy).get("/api/dining/busy")
    assert (res.status_code, res.json()) == (503, {"detail": detail})
