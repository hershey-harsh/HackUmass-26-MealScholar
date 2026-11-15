"""GET /api/dining/busy: the latest hall cam reading per hall, from umass_dining.occupancy."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from mealscholar.busy import Busy, MongoOccupancySource
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


def reading(location_id, name, level, people, minutes_ago=2, trend="steady", youtube_id=None):
    taken = (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None)  # pymongo hands back naive UTC
    return {
        "youtube_id": youtube_id,
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
    no_clip = {"live_url": None, "clip_url": None, "poster_url": None, "clip_at": None}
    assert res.json()["halls"] == [
        {"location_id": 1, "name": "Worcester", "level": "quiet", "people": 8, "trend": "steady", "updated_at": at}
        | no_clip,
        {"location_id": 2, "name": "Franklin", "level": "moderate", "people": 12, "trend": None, "updated_at": at}
        | no_clip,
        {"location_id": 4, "name": "Berkshire", "level": "busy", "people": 31, "trend": "rising", "updated_at": at}
        | no_clip,
    ]
    assert source.calls == [NOW - timedelta(minutes=15)]  # only readings from the last 15 minutes


class FakeOccupancy:
    """umass_dining.occupancy for MongoOccupancySource: runs the $match, $sort and $group it sends."""

    def __init__(self, docs):
        self.docs = docs
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        docs = list(self.docs)
        for stage in pipeline:
            ((op, arg),) = stage.items()
            if op == "$match":
                docs = [d for d in docs if all(matches(d.get(key), cond) for key, cond in arg.items())]
            elif op == "$sort":
                ((key, order),) = arg.items()
                docs.sort(key=lambda d: d[key], reverse=order < 0)
            elif op == "$group":
                first = {}
                for d in docs:
                    first.setdefault(d[arg["_id"].removeprefix("$")], d)
                docs = [{"_id": key, "doc": d} for key, d in first.items()]
            else:
                raise NotImplementedError(op)
        return docs


def matches(value, cond):
    if not isinstance(cond, dict):
        return value == cond
    for op, operand in cond.items():
        if op == "$gte":  # stored times are naive UTC
            if value is None or value.replace(tzinfo=UTC) < operand:
                return False
        elif op == "$in":
            if value not in operand:
                return False
        else:
            raise NotImplementedError(op)
    return True


def test_a_pass_that_could_not_read_a_cam_leaves_the_last_reading(tmp_path):
    """Worcester's newest reading is unknown (that pass couldn't read its cam): the good one from 4
    minutes ago is still shown, rather than the hall going missing until the next pass."""
    occupancy = FakeOccupancy(
        [
            reading(1, "Worcester", "quiet", 8, minutes_ago=4) | {"primary": True},
            reading(1, "Worcester", "unknown", 0, minutes_ago=1) | {"primary": True},
            reading(2, "Franklin", "busy", 30, minutes_ago=1) | {"primary": True},
            reading(2, "Franklin", "moderate", 12, minutes_ago=20) | {"primary": True},  # too old
            reading(3, "Hampshire", "unknown", 0, minutes_ago=2) | {"primary": True},  # nothing readable lately
            reading(4, "Berkshire", "quiet", 3, minutes_ago=1) | {"primary": False},  # not the hall's main cam
        ]
    )
    source = MongoOccupancySource("mongodb://unused", "umass_dining")
    source._client = {"umass_dining": {"occupancy": occupancy}}
    halls = client_for(tmp_path, Busy(source, clock=lambda: NOW)).get("/api/dining/busy").json()["halls"]
    assert [(h["location_id"], h["level"], h["people"], h["updated_at"]) for h in halls] == [
        (1, "quiet", 8, "2026-11-14T21:56:00Z"),
        (2, "busy", 30, "2026-11-14T21:59:00Z"),
    ]
    ((match, *_),) = occupancy.pipelines
    assert match["$match"]["level"] == {"$in": ["quiet", "moderate", "busy"]}


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


def cam_files(tmp_path, location_id, minutes_ago, poster=True):
    cams = tmp_path / "cams"
    cams.mkdir(exist_ok=True)
    when = (NOW - timedelta(minutes=minutes_ago)).timestamp()
    clip = cams / f"{location_id}.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4)
    os.utime(clip, (when, when))
    if poster:
        jpg = cams / f"{location_id}.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0 poster")
        os.utime(jpg, (when, when))
    return int(when)


def test_readings_link_the_live_cam_and_a_fresh_clip(tmp_path):
    source = FakeSource(
        [
            reading(1, "Worcester", "quiet", 8, youtube_id="r154Wa3jr38"),
            reading(2, "Franklin", "moderate", 12, youtube_id="gvs6Mf-vR7o"),
            reading(3, "Hampshire", "quiet", 4, youtube_id="../../etc"),  # not a YouTube id: no link
            reading(4, "Berkshire", "busy", 31, youtube_id="-yaPv3fQqiw"),
        ]
    )
    v1 = cam_files(tmp_path, 1, minutes_ago=1.5)  # written right after the reading's capture (2 minutes ago)
    cam_files(tmp_path, 2, minutes_ago=16)  # older than 15 minutes: not shown
    v4 = cam_files(tmp_path, 4, minutes_ago=1, poster=False)
    halls = {h["location_id"]: h for h in client_for(tmp_path, Busy(source, clock=lambda: NOW)).get(
        "/api/dining/busy"
    ).json()["halls"]}  # fmt: skip
    assert halls[1]["live_url"] == "https://www.youtube.com/watch?v=r154Wa3jr38"
    assert halls[1]["clip_url"] == f"/api/dining/cams/1/clip.mp4?v={v1}"
    assert halls[1]["poster_url"] == f"/api/dining/cams/1/poster.jpg?v={v1}"
    assert halls[1]["clip_at"] == "2026-11-14T21:58:30Z"
    assert halls[2]["live_url"] == "https://www.youtube.com/watch?v=gvs6Mf-vR7o"
    assert (halls[2]["clip_url"], halls[2]["poster_url"], halls[2]["clip_at"]) == (None, None, None)
    assert halls[3]["live_url"] is None
    assert halls[4]["clip_url"] == f"/api/dining/cams/4/clip.mp4?v={v4}" and halls[4]["poster_url"] is None


def test_a_clip_from_an_earlier_pass_is_not_linked(tmp_path):
    """This pass's encode failed: the clip on disk is from the pass before, so its boxes would not match
    the count next to it. The same goes for a clip much newer than the reading."""
    source = FakeSource(
        [reading(1, "Worcester", "quiet", 8, minutes_ago=2), reading(2, "Franklin", "quiet", 3, minutes_ago=9)]
    )
    cam_files(tmp_path, 1, minutes_ago=5)  # the previous pass
    cam_files(tmp_path, 2, minutes_ago=1)  # 8 minutes after Franklin's reading (its writes failed since)
    halls = client_for(tmp_path, Busy(source, clock=lambda: NOW)).get("/api/dining/busy").json()["halls"]
    assert [(h["clip_url"], h["poster_url"], h["clip_at"]) for h in halls] == [(None, None, None)] * 2


def test_cam_clip_and_poster_files(tmp_path):
    cam_files(tmp_path, 1, minutes_ago=3)
    client = client_for(tmp_path, Busy(FakeSource(), clock=lambda: NOW))
    clip = client.get("/api/dining/cams/1/clip.mp4?v=123")
    assert clip.status_code == 200
    assert clip.headers["content-type"] == "video/mp4"
    assert clip.headers["cache-control"] == "public, max-age=60"
    assert clip.content == (tmp_path / "cams" / "1.mp4").read_bytes()
    part = client.get("/api/dining/cams/1/clip.mp4", headers={"Range": "bytes=0-15"})  # Safari asks for ranges
    assert part.status_code == 206 and len(part.content) == 16
    poster = client.get("/api/dining/cams/1/poster.jpg")
    assert (poster.status_code, poster.headers["content-type"]) == (200, "image/jpeg")


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/dining/cams/2/clip.mp4", 404),  # no clip for this hall
        ("/api/dining/cams/2/poster.jpg", 404),
        ("/api/dining/cams/0/clip.mp4", 422),
        ("/api/dining/cams/-1/clip.mp4", 422),
        ("/api/dining/cams/1.mp4/clip.mp4", 422),
        ("/api/dining/cams/..%2F1/clip.mp4", 404),  # not a route
        ("/api/dining/cams/99999999999/clip.mp4", 422),
        ("/api/dining/cams/1/clip.webm", 404),
    ],
)
def test_cam_files_take_only_a_hall_id(tmp_path, path, status):
    cam_files(tmp_path, 1, minutes_ago=3)
    client = client_for(tmp_path, Busy(FakeSource(), clock=lambda: NOW))
    assert client.get(path).status_code == status


def test_cam_files_without_cams(tmp_path):
    client = client_for(tmp_path, None)
    assert client.get("/api/dining/cams/1/clip.mp4").status_code == 404
