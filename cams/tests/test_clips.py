"""The boxed clip + poster each hall's main camera leaves for the app."""

import json
import subprocess
import time
from datetime import UTC, datetime

import cv2
import numpy as np
import pytest
from umass_cams import config, detect, sample, streams
from umass_cams.detect import Detection, clip_fps, write_clip
from umass_cams.streams import by_youtube_id

FFPROBE = "/opt/homebrew/bin/ffprobe"


def window(n=8, interval=0.5, size=(720, 1280)):
    h, w = size
    out = []
    for i in range(n):
        frame = np.full((h, w, 3), 40 + i % 16 * 10, np.uint8)
        out.append((1000.0 + i * interval, frame, [Detection(1, 100 + i * 10, 100, 220 + i * 10, 400, 0.9)]))
    return out


def test_clip_fps_follows_the_capture():
    assert clip_fps(window(33, 0.5)) == pytest.approx(2.0)
    assert clip_fps(window(5, 0.25)) == pytest.approx(4.0)
    assert clip_fps(window(1)) == 2.0  # one frame: nothing to measure


def test_write_clip_h264_960x540_and_poster(tmp_path):
    mp4, jpg = write_clip(window(), tmp_path / "1.mp4", tmp_path / "1.jpg", title="Worcester")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.jpg", "1.mp4"]  # no temp files left
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_read_frames", "-of", "json", str(mp4)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    s = json.loads(probe.stdout)["streams"][0]
    assert (s["codec_name"], s["width"], s["height"], s["pix_fmt"]) == ("h264", 960, 540, "yuv420p")
    assert int(s["nb_read_frames"]) == 8
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    assert num / den == pytest.approx(2.0, rel=0.01)
    poster = cv2.imread(str(jpg))
    assert poster.shape == (540, 960, 3)
    # the last frame's green box, scaled to 960 wide: its left edge sits at x = 170 * 0.75
    b, g, r = (int(v) for v in poster[200, round(170 * 0.75)])
    assert g > b + 60 and g > r + 60


def test_failed_encode_keeps_the_previous_clip(tmp_path):
    (tmp_path / "1.mp4").write_bytes(b"old clip")
    (tmp_path / "1.jpg").write_bytes(b"old poster")
    with pytest.raises(FileNotFoundError):
        write_clip(window(), tmp_path / "1.mp4", tmp_path / "1.jpg", ffmpeg=str(tmp_path / "no-ffmpeg"))
    bad = tmp_path / "false.sh"
    bad.write_text("#!/bin/sh\ncat >/dev/null\nexit 3\n")
    bad.chmod(0o755)
    with pytest.raises(RuntimeError):
        write_clip(window(), tmp_path / "1.mp4", tmp_path / "1.jpg", ffmpeg=str(bad))
    assert (tmp_path / "1.mp4").read_bytes() == b"old clip"
    assert (tmp_path / "1.jpg").read_bytes() == b"old poster"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.jpg", "1.mp4", "false.sh"]


def test_a_hung_ffmpeg_is_killed_and_the_old_clip_stays(tmp_path):
    (tmp_path / "1.mp4").write_bytes(b"old clip")
    hung = tmp_path / "hung.sh"
    hung.write_text("#!/bin/sh\ncat >/dev/null\nexec sleep 30\n")
    hung.chmod(0o755)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        write_clip(window(), tmp_path / "1.mp4", tmp_path / "1.jpg", ffmpeg=str(hung), timeout=0.5)
    assert time.monotonic() - t0 < 5
    assert (tmp_path / "1.mp4").read_bytes() == b"old clip"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.mp4", "hung.sh"]


def test_an_ffmpeg_that_quits_early_raises(tmp_path):
    quits = tmp_path / "quits.sh"
    quits.write_text("#!/bin/sh\nexit 1\n")  # never reads its input: the frame writes hit a closed pipe
    quits.chmod(0o755)
    with pytest.raises(RuntimeError, match="exited 1"):
        write_clip(window(), tmp_path / "1.mp4", tmp_path / "1.jpg", ffmpeg=str(quits))


def test_temp_files_of_a_killed_pass_are_cleared(tmp_path):
    for name in (".1.mp4.123.tmp", ".1.jpg.123.tmp", ".2.mp4.9.tmp"):
        (tmp_path / name).write_bytes(b"half")
    write_clip(window(2), tmp_path / "1.mp4", tmp_path / "1.jpg")
    assert sorted(p.name for p in tmp_path.iterdir()) == [".2.mp4.9.tmp", "1.jpg", "1.mp4"]  # hall 2 is its own


class FrameGrabberOK:
    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def grab(self, stream, **kw):
        frames = [(1000.0 + i * 0.5, np.zeros((720, 1280, 3), np.uint8)) for i in range(4)]
        return streams.GrabResult(stream=stream, engine="webkit", status="ok", frames=frames, video_size=(1280, 720))


class FakeTracker:
    model_name = "yolo11n"
    setup = "fake"

    def __init__(self, *a, **kw):  # sample.main builds one per hall with the camera's conf
        pass

    def track(self, frame):
        return [Detection(1, 10, 10, 50, 90, 0.9), Detection(2, 200, 10, 260, 90, 0.8)]


def test_sample_writes_a_clip_per_main_camera(monkeypatch, tmp_path):
    s = by_youtube_id("7wHgOpwHz8k")
    clips = tmp_path / "cams"
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    doc = sample.sample_one(FrameGrabberOK(), FakeTracker, s, None, now, 2.0, 1e12, True, clips)
    assert doc["people"] == 2 and doc["frames"] == 4
    assert sorted(p.name for p in clips.iterdir()) == ["1.jpg", "1.mp4"]
    assert not any(isinstance(v, (bytes, np.ndarray)) for v in doc.values())  # nothing of the clip in the doc

    side = by_youtube_id("89KPOnjHEC4")  # Worcester's second camera: no clip, the main one keeps its own
    assert not side.primary
    before = (clips / "1.mp4").stat().st_mtime_ns
    sample.sample_one(FrameGrabberOK(), FakeTracker, side, None, now, 2.0, 1e12, True, clips)
    assert (clips / "1.mp4").stat().st_mtime_ns == before


def test_a_failed_clip_never_stops_the_pass(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("ffmpeg exited 1")

    monkeypatch.setattr(detect, "write_clip", boom)
    monkeypatch.setattr(streams, "FrameGrabber", FrameGrabberOK)
    monkeypatch.setattr(sample, "STORE_FAILURES", 0)
    for name in ("STATE_DIR", "DEMO_DIR", "MODEL_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setattr(config, "LATEST_JSON", tmp_path / "state" / "latest.json")
    monkeypatch.setenv("UMASS_CAMS_CLIP_DIR", str(tmp_path / "cams"))

    import umass_cams.detect as d

    monkeypatch.setattr(d, "PersonTracker", FakeTracker)
    assert sample.main(["--no-mongo"]) == 0
    data = json.loads(config.LATEST_JSON.read_text())
    assert sorted(data["halls"]) == ["1", "3", "4"]  # Franklin has no live cam
    assert all(h["level"] != "unknown" and h["people"] == 2 for h in data["halls"].values())
    assert not (tmp_path / "cams").exists() or not any((tmp_path / "cams").iterdir())


def test_clip_dir_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("UMASS_CAMS_CLIP_DIR", str(tmp_path / "x"))
    assert config.clip_dir() == tmp_path / "x"
    monkeypatch.delenv("UMASS_CAMS_CLIP_DIR")
    monkeypatch.setattr(config, "SETTINGS", {})
    assert config.clip_dir().parts[-3:] == ("mealscholar", "data", "cams")
