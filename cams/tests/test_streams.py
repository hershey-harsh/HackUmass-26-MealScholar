import re

from umass_cams import streams


def test_registry_is_consistent():
    ids = [s.youtube_id for s in streams.STREAMS]
    assert len(ids) == len(set(ids))
    for s in streams.STREAMS:
        assert re.fullmatch(r"[A-Za-z0-9_-]{11}", s.youtube_id)
        assert s.page_url.startswith("https://www.umassdining.com/locations-menus/")
        assert s.embed_url == f"https://www.youtube.com/embed/{s.youtube_id}?autoplay=1&mute=1&playsinline=1"


def test_primary_cams_are_the_halls_live_streams():
    prim = streams.primary_streams()
    # Franklin's page lists no livestream; the video at the top of each hall page is a 2022 upload
    assert sorted((s.location_id, s.location_name, s.youtube_id) for s in prim) == [
        (1, "Worcester", "7wHgOpwHz8k"),
        (3, "Hampshire", "RprORF_ggOA"),
        (4, "Berkshire", "AoceCqa6MWw"),
    ]
    old_uploads = {"r154Wa3jr38", "gvs6Mf-vR7o", "-yaPv3fQqiw", "urmHVZff1Io"}
    assert not old_uploads & {s.youtube_id for s in streams.STREAMS}
    assert all(s.youtube_id not in streams.DEAD_IDS for s in streams.all_live_streams())
    assert "TWC87AgKJHA" not in {s.youtube_id for s in streams.all_live_streams()}


def test_by_youtube_id():
    assert streams.by_youtube_id("7wHgOpwHz8k").location_name == "Worcester"


def test_classify_player_text():
    c = streams.classify_player_text
    assert c("Sign in to confirm you’re not a bot This helps protect our community") == "youtube_bot_check"
    assert c("Video player configuration error Error 153") == "youtube_player_error_153"
    assert c("This live stream recording is not available.") == "stream_unavailable"
    assert c("Live stream offline") == "stream_offline"
    assert c("") is None
    assert c("Worcester Dining Common at UMass Amherst") is None


class _FakeColl:
    def __init__(self, rows):
        self.rows = rows

    def find(self, q, proj):
        ids = set(q["_id"]["$in"])
        return [r for r in self.rows if r["_id"] in ids]


def test_check_against_locations_reports_mismatch():
    db = {
        "locations": _FakeColl(
            [
                {"_id": 1, "name": "Worcester"},
                {"_id": 2, "name": "Franklin"},
                {"_id": 3, "name": "Hampshire"},
                {"_id": 4, "name": "Berkshire"},
                {"_id": 1724, "name": "Roots Café"},
            ]
        )
    }
    assert streams.check_against_locations(db) == []
    db2 = {"locations": _FakeColl([{"_id": 1, "name": "Worcester DC"}])}
    problems = streams.check_against_locations(db2)
    assert any("not in locations" in p for p in problems)
    assert any("7wHgOpwHz8k" in p and "!=" in p for p in problems)


# --------------------------------------------------------------------------
# FrameGrabber with a fake Playwright page (no browser): playback detection and
# the screenshot rule (review findings).
# --------------------------------------------------------------------------


def _jpeg_data_url():
    import base64

    import cv2
    import numpy as np

    img = np.zeros((360, 640, 3), np.uint8)
    return "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()


class _FakePage:
    """Returns one player state per poll from `states` (the last one repeats)."""

    def __init__(self, states, grab=None, state_raises_at=None):
        self.states = list(states)
        self.grab = grab or _jpeg_data_url
        self.state_raises_at = state_raises_at
        self.state_polls = 0
        self.grab_calls = 0
        self.screenshots = []

    def set_extra_http_headers(self, h):
        pass

    def goto(self, *a, **k):
        pass

    def query_selector(self, sel):
        return None

    @property
    def mouse(self):
        class M:
            def click(self, *a, **k):
                pass

        return M()

    def evaluate(self, js):
        if js is streams.JS_PLAYER_STATE:
            self.state_polls += 1
            if self.state_raises_at is not None and self.state_polls >= self.state_raises_at:
                raise RuntimeError("page crashed")
            return self.states[min(self.state_polls - 1, len(self.states) - 1)]
        self.grab_calls += 1
        return self.grab()

    def screenshot(self, path):
        self.screenshots.append(path)


def _grabber(page, tmp_path=None):
    class Ctx:
        def new_page(self):
            return page

        def close(self):
            pass

    class Browser:
        def new_context(self, **k):
            return Ctx()

    g = streams.FrameGrabber(engines=("chromium",), screenshot_dir=str(tmp_path) if tmp_path else None, poll_s=0.01)
    g._browser = lambda engine: Browser()
    g.live = lambda stream: True  # the live check reads YouTube; these tests are about the player
    return g


def _state(w=640, t=0.0, paused=False, ready=4, video=True, text=""):
    return {"video": video, "w": w, "h": 360 if w else 0, "t": t, "paused": paused, "ready": ready, "text": text}


def test_grabber_waits_for_current_time_to_advance(tmp_path):
    # width known, not paused, but currentTime stuck at 0 and nothing decoded: never "playing"
    page = _FakePage([_state(t=0.0, ready=0)])
    g = _grabber(page, tmp_path)
    r = g._grab_with("chromium", streams.by_youtube_id("7wHgOpwHz8k"), 4, 2, 0.3, None)
    assert r.status == "timeout" and r.frames == [] and page.grab_calls == 0
    assert page.state_polls > 1
    # a stalled player with a width is never screenshotted (it may be showing a frame)
    assert page.screenshots == [] and r.screenshot is None
    # a decoded frame whose clock does not move is still not playing
    page = _FakePage([_state(t=3.5, ready=4)])
    r = _grabber(page)._grab_with("chromium", streams.by_youtube_id("7wHgOpwHz8k"), 4, 2, 0.3, None)
    assert r.status == "timeout" and page.grab_calls == 0


def test_grabber_captures_once_playback_advances():
    page = _FakePage([_state(t=0.0, ready=0), _state(t=0.0, ready=1), _state(t=0.4, ready=4), _state(t=0.9, ready=4)])
    g = _grabber(page)
    r = g._grab_with("chromium", streams.by_youtube_id("RprORF_ggOA"), 0.04, 0.02, 2.0, None)
    assert r.status == "ok" and len(r.frames) == 2 and r.video_size == (640, 360)
    assert page.state_polls == 3  # first poll can never count; third sees t advance with ready >= 2
    assert r.frames[0][1].shape == (360, 640, 3)


def test_grabber_never_screenshots_the_playing_video(tmp_path):
    def boom():
        raise RuntimeError("simulated failure mid-window")

    page = _FakePage([_state(t=0.0), _state(t=0.5)], grab=boom)
    g = _grabber(page, tmp_path)
    r = g._grab_with("chromium", streams.by_youtube_id("7wHgOpwHz8k"), 4, 2, 2.0, None)
    assert r.status == "error:RuntimeError" and r.frames == []
    assert page.screenshots == [] and r.screenshot is None
    assert list(tmp_path.iterdir()) == []


def test_grabber_screenshots_only_the_pre_playback_player(tmp_path):
    # no <video> at all, then the page crashes: that player state is screenshotted for diagnostics
    page = _FakePage([_state(video=False, w=0)], state_raises_at=2)
    g = _grabber(page, tmp_path)
    r = g._grab_with("chromium", streams.by_youtube_id("7wHgOpwHz8k"), 4, 2, 2.0, None)
    assert r.status == "error:RuntimeError"
    assert len(page.screenshots) == 1 and page.screenshots[0].endswith("player_worcester_7wHgOpwHz8k_chromium.png")
    # the bot wall is a pre-playback state too
    page = _FakePage([_state(video=False, w=0, text="Sign in to confirm you're not a bot")])
    r = _grabber(page, tmp_path)._grab_with("chromium", streams.by_youtube_id("7wHgOpwHz8k"), 4, 2, 2.0, None)
    assert r.status == "youtube_bot_check" and len(page.screenshots) == 1


def test_a_stream_that_is_not_live_is_never_grabbed():
    g = streams.FrameGrabber(engines=("chromium",))
    g._browser = lambda engine: (_ for _ in ()).throw(AssertionError("no browser for a recording"))
    g.live = lambda stream: False
    assert g.grab(streams.by_youtube_id("7wHgOpwHz8k")).status == "not_live"
    g.live = lambda stream: None  # the watch page couldn't be read
    assert g.grab(streams.by_youtube_id("7wHgOpwHz8k")).status == "live_check_failed"


def test_is_live_reads_the_watch_page(monkeypatch):
    import io
    import urllib.request

    pages = {
        "live": b'..."isLiveContent":true,..."isLive":true...',
        "ended": b'..."isLiveContent":true,..."isLive":false...',
        "upload": b'..."isLiveContent":false...',
    }

    class Opener:
        def __init__(self, page):
            self.page = page

        def open(self, req, timeout):
            return io.BytesIO(pages[self.page])

    for name, want in (("live", True), ("ended", False), ("upload", False)):
        monkeypatch.setattr(urllib.request, "build_opener", lambda *h, n=name: Opener(n))
        assert streams.is_live("7wHgOpwHz8k") is want
