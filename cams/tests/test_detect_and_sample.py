from datetime import UTC, datetime

import numpy as np
from umass_cams import busy, config, sample
from umass_cams.detect import Detection, annotate, contact_sheet, status_tile
from umass_cams.streams import by_youtube_id


def test_annotate_draws_green_boxes_and_count():
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    dets = [Detection(1, 100, 100, 200, 300, 0.9), Detection(2, 300, 50, 380, 200, 0.6)]
    out = annotate(frame, dets, "Test")
    assert out.shape == frame.shape
    assert not np.array_equal(out, frame)
    # the box edge is drawn in BGR (0,200,0)
    edge = out[150, 100]
    assert tuple(int(v) for v in edge) == (0, 200, 0)
    # the input is untouched
    assert frame.max() == 0


def test_contact_sheet_and_status_tile():
    tiles = [
        status_tile("youtube_bot_check\nSign in", title="A"),
        np.full((720, 1280, 3), 90, np.uint8),
        status_tile("x"),
        status_tile("y"),
    ]
    sheet = contact_sheet(tiles, cols=2, tile_size=(320, 180))
    assert sheet.shape == (360, 640, 3)


def test_build_doc_shapes():
    s = by_youtube_id("gvs6Mf-vR7o")
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    a = busy.assess(people=7, youtube_id=s.youtube_id, history=[], now=now)
    doc = sample.build_doc(s, now, 20.0, 10, 7, 9, a, "yolo11n", "chromium", extra={"counts": [7] * 10})
    for k in (
        "location_id",
        "location_name",
        "youtube_id",
        "ts",
        "window_s",
        "frames",
        "people",
        "tracks",
        "level",
        "score",
        "trend",
        "forecast_30m",
        "model",
        "source",
        "confidence",
    ):
        assert k in doc
    assert (
        doc["location_id"] == 2 and doc["level"] == "moderate" and doc["source"] == "youtube-embed/playwright/chromium"
    )
    assert "reason" not in doc
    # no frame-like payloads ever
    assert not any(isinstance(v, (bytes, np.ndarray)) for v in doc.values())

    off = sample.build_doc(s, now, 20.0, 0, 0, 0, None, "yolo11n", "webkit", reason="youtube_bot_check")
    assert off["level"] == "unknown" and off["reason"] == "youtube_bot_check" and off["score"] is None
    js = sample.json_safe(off)
    assert js["ts"] == "2026-11-20T17:00:00Z"


def test_write_latest(tmp_path):
    s = by_youtube_id("r154Wa3jr38")
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    doc = sample.build_doc(s, now, 20.0, 0, 0, 0, None, "yolo11n", None, reason="stream_offline")
    path = tmp_path / "latest.json"
    sample.write_latest([doc], path)
    import json

    data = json.loads(path.read_text())
    assert data["halls"]["1"]["level"] == "unknown" and data["halls"]["1"]["reason"] == "stream_offline"
    assert len(data["cameras"]) == 1


def test_config_never_exposes_uri(monkeypatch, tmp_path):
    ref = tmp_path / "other.env"
    ref.write_text("MONGODB_URI=mongodb://example.invalid/x\n")
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setenv("UMASS_CAMS_MONGO_ENV", str(ref))
    monkeypatch.setattr(config, "SETTINGS", {})
    assert config.mongodb_uri() == "mongodb://example.invalid/x"
    monkeypatch.setenv("UMASS_CAMS_MONGO_ENV", str(tmp_path / "missing.env"))
    try:
        config.mongodb_uri()
    except RuntimeError as e:
        assert "mongodb://" not in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_mongo_write_failure_does_not_abort_the_pass(monkeypatch, tmp_path):
    """Review finding: a Mongo write failing mid-pass skipped the remaining halls and latest.json."""
    from umass_cams import streams

    class FakeGrabber:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def grab(self, stream, **kw):
            return streams.GrabResult(
                stream=stream,
                engine="chromium",
                status="youtube_bot_check",
                player_text="Sign in to confirm you're not a bot",
            )

    class FakeColl:
        writes = 0

        def update_one(self, *a, **k):
            FakeColl.writes += 1
            if FakeColl.writes >= 2:
                raise ConnectionError("connection closed")  # Mongo drops after the first hall

    monkeypatch.setattr(streams, "FrameGrabber", FakeGrabber)
    monkeypatch.setattr(sample, "open_collection", lambda dry_run: FakeColl())
    monkeypatch.setattr(sample, "STORE_FAILURES", 0)
    for name in ("STATE_DIR", "DEMO_DIR", "MODEL_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setattr(config, "LATEST_JSON", tmp_path / "state" / "latest.json")

    assert sample.main([]) == 0
    assert FakeColl.writes == 4  # every hall was attempted
    assert sample.STORE_FAILURES == 3
    import json

    data = json.loads(config.LATEST_JSON.read_text())
    assert sorted(data["halls"]) == ["1", "2", "3", "4"]
    assert all(h["reason"] == "youtube_bot_check" for h in data["halls"].values())


def test_latest_json_written_even_when_the_grabber_dies(monkeypatch, tmp_path):
    from umass_cams import streams

    class DyingGrabber:
        def __init__(self, **kw):
            self.n = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def grab(self, stream, **kw):
            self.n += 1
            if self.n == 3:
                raise KeyboardInterrupt  # not an Exception: escapes the per-hall handler
            return streams.GrabResult(stream=stream, engine="webkit", status="stream_offline")

    monkeypatch.setattr(streams, "FrameGrabber", DyingGrabber)
    for name in ("STATE_DIR", "DEMO_DIR", "MODEL_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setattr(config, "LATEST_JSON", tmp_path / "state" / "latest.json")
    import pytest

    with pytest.raises(KeyboardInterrupt):
        sample.main(["--no-mongo"])
    import json

    data = json.loads(config.LATEST_JSON.read_text())
    assert sorted(data["halls"]) == ["1", "2"]  # the two halls sampled before the crash
