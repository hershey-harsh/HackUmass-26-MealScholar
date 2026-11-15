"""Registry of UMass Dining webcams and a Playwright frame grabber.

Every stream here is embedded by UMass Dining on its own public location page
(https://www.umassdining.com/locations-menus/<slug>), either as the page's
<iframe src="https://www.youtube.com/embed/<id>"> or as a "Livestream - ..."
link in the page's livestream list. `location_id` / `location_name` are the
`_id` / `name` of the umass_dining.locations collection (read-only).

Frame grabbing opens the public YouTube embed the way a visitor's browser
would (autoplay + mute + playsinline, with the dining page as Referer), clicks
the player if it stays paused, waits for videoWidth > 0, readyState >= 2 and
currentTime advancing between two polls, and only then copies the <video>
element to a canvas about every 2 seconds. Player screenshots (diagnostics)
are taken only while the player is not playing, never of the video itself.
No cookies, no credentials, no attempt to get past YouTube's sign-in or bot
checks: if the player shows one, the grab reports it and stops. A window whose
frames never change (the player froze on one picture while its clock ran on) is
reported as "stalled" and never counted.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

PAGE_BASE = "https://www.umassdining.com/locations-menus/"


@dataclass(frozen=True)
class Stream:
    location_id: int
    location_name: str
    slug: str
    youtube_id: str
    label: str
    primary: bool = True
    note: str = ""

    @property
    def page_url(self) -> str:
        return PAGE_BASE + self.slug

    @property
    def embed_url(self) -> str:
        return f"https://www.youtube.com/embed/{self.youtube_id}?autoplay=1&mute=1&playsinline=1"

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.youtube_id}"


# Verified 2026-11-14 by fetching every page under /locations-menus (index,
# worcester, franklin, berkshire, hampshire, campus-center, grab-n-go, campus,
# student-businesses, around-campus and its 20 cafe sub-pages). Titles from the
# public oembed endpoint.
STREAMS: tuple[Stream, ...] = (
    # The real cams are the "Livestream" links on each hall page. The video embedded at the top of
    # those pages (r154Wa3jr38, gvs6Mf-vR7o, -yaPv3fQqiw, urmHVZff1Io) is a 2022 promo upload, not
    # a live stream, so it is not listed. Franklin's page lists no livestream.
    Stream(1, "Worcester", "worcester", "7wHgOpwHz8k", "Worcester Commons North", True, "livestream list"),
    Stream(1, "Worcester", "worcester", "89KPOnjHEC4", "Worcester Commons South", False, "livestream list"),
    Stream(4, "Berkshire", "berkshire", "AoceCqa6MWw", "Berkshire Commons Entrance", True, "livestream list"),
    Stream(3, "Hampshire", "hampshire", "RprORF_ggOA", "Hampshire Commons South", True, "livestream list"),
    Stream(
        3,
        "Hampshire",
        "hampshire",
        "TWC87AgKJHA",
        "Hampshire Commons North",
        False,
        "livestream list; YouTube says 'This live stream recording is not available' (dead)",
    ),
    Stream(
        1724,
        "Roots Café",
        "around-campus/roots-cafe",
        "nMFduwGyYUY",
        "Roots Cafe",
        False,
        "livestream list; the page iframe CRUPtCDSp7Y is a 'Welcome Message from Cafes' video, not a cam",
    ),
)

DEAD_IDS = frozenset({"TWC87AgKJHA"})


def primary_streams() -> list[Stream]:
    """One live camera per dining common that has one (Franklin has none)."""
    return [s for s in STREAMS if s.primary]


def all_live_streams() -> list[Stream]:
    return [s for s in STREAMS if s.youtube_id not in DEAD_IDS]


def by_youtube_id(youtube_id: str) -> Stream:
    for s in STREAMS:
        if s.youtube_id == youtube_id:
            return s
    raise KeyError(youtube_id)


def check_against_locations(db) -> list[str]:
    """Read-only check that every stream's location_id/name exists in umass_dining.locations.

    Returns a list of human-readable problems (empty when everything matches).
    """
    problems: list[str] = []
    ids = {s.location_id for s in STREAMS}
    found = {d["_id"]: d.get("name") for d in db["locations"].find({"_id": {"$in": list(ids)}}, {"name": 1})}
    for s in STREAMS:
        name = found.get(s.location_id)
        if name is None:
            problems.append(f"{s.youtube_id}: location_id {s.location_id} not in locations")
        elif name != s.location_name:
            problems.append(f"{s.youtube_id}: name {s.location_name!r} != locations.name {name!r}")
    return problems


# --------------------------------------------------------------------------
# Frame grabber
# --------------------------------------------------------------------------

JS_PLAYER_STATE = """() => {
  const v = document.querySelector('video');
  const text = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 240);
  if (!v) return {video: false, w: 0, h: 0, t: 0, paused: true, ready: 0, text};
  return {video: true, w: v.videoWidth, h: v.videoHeight, t: v.currentTime, paused: v.paused,
          ready: v.readyState, err: v.error ? v.error.code : null, text};
}"""

JS_GRAB_JPEG = """() => {
  const v = document.querySelector('video');
  if (!v || !v.videoWidth) return null;
  const c = document.createElement('canvas');
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  return c.toDataURL('image/jpeg', 0.9);
}"""


def classify_player_text(text: str) -> str | None:
    """Map the player's visible text to a stable failure reason."""
    t = (text or "").lower()
    if "not a bot" in t or "sign in to confirm" in t:
        return "youtube_bot_check"
    if "error 153" in t or "configuration error" in t:
        return "youtube_player_error_153"
    if "unavailable" in t or "not available" in t:
        return "stream_unavailable"
    if "offline" in t or "will begin shortly" in t or "waiting for" in t:
        return "stream_offline"
    if "private video" in t:
        return "stream_private"
    return None


@dataclass
class GrabResult:
    stream: Stream
    engine: str
    status: str  # "ok" | a reason from classify_player_text | "no_video" | "timeout" | "stalled" | "error:<...>"
    frames: list[tuple[float, np.ndarray]] = field(default_factory=list)  # (unix ts, BGR frame)
    player_text: str = ""
    video_size: tuple[int, int] = (0, 0)
    seconds: float = 0.0
    screenshot: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.frames)


STALL_DIFF = 0.5
"""Mean absolute gray difference (0..255) between consecutive frames below which the picture is not
moving. Live video, even of an empty room, differs by a few levels from frame to frame (compression
noise, lighting); a stalled player hands back the same decoded picture every time, difference 0."""


def frames_stalled(frames: list[tuple[float, np.ndarray]], min_diff: float = STALL_DIFF) -> bool:
    """True when the window holds several frames and none differs from the one before it: the player
    froze on one picture (its clock kept running, so playback detection passed), and counting it would
    report the hall from minutes ago as the hall now."""
    if len(frames) < 2:
        return False
    prev = None
    for _, frame in frames:
        small = frame[::4, ::4]
        gray = small.mean(axis=2) if small.ndim == 3 else small.astype(float)
        if prev is not None and float(np.abs(gray - prev).mean()) >= min_diff:
            return False
        prev = gray
    return True


def _decode_data_url(data_url: str) -> np.ndarray | None:
    import cv2  # local import keeps the registry importable without OpenCV

    raw = base64.b64decode(data_url.split(",", 1)[1])
    arr = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


class FrameGrabber:
    """Playwright session that can capture frames from several streams in turn.

    Tries engines in order (webkit, then chromium: some cams are H.264 only, which
    Playwright's Chromium can't play, so it would wait out its timeout first). One browser per engine is
    kept open for the life of the grabber so four halls do not pay four launches.
    """

    def __init__(
        self,
        engines: tuple[str, ...] = ("webkit", "chromium"),
        headless: bool = True,
        screenshot_dir: str | None = None,
        poll_s: float = 1.0,
        egress: str | None = None,
    ):
        """`egress`: UMASS_CAMS_EGRESS by default (see egress.py); "vpn" for the default route."""
        self.engines = engines
        self.headless = headless
        self.screenshot_dir = screenshot_dir
        self.poll_s = poll_s  # seconds between player-state polls while waiting for playback
        self.egress = egress
        self._live: dict[str, bool | None] = {}
        self._proxy = None
        self._pw = None
        self._browsers: dict[str, object] = {}

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        from . import config, egress

        mode = self.egress if self.egress is not None else config.setting("UMASS_CAMS_EGRESS")
        self._proxy = egress.proxy_for(mode)
        self._pw = sync_playwright().start()
        return self

    def __exit__(self, *exc):
        for b in self._browsers.values():
            try:
                b.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None

    def _browser(self, engine: str):
        b = self._browsers.get(engine)
        if b is not None and not b.is_connected():  # crashed or closed since the last hall: relaunch
            self._browsers.pop(engine, None)
        if engine not in self._browsers:
            kw = {"headless": self.headless}
            if self._proxy is not None:
                kw["proxy"] = {"server": self._proxy.url}
            if engine == "chromium":
                kw["args"] = ["--autoplay-policy=no-user-gesture-required", "--mute-audio"]
            self._browsers[engine] = getattr(self._pw, engine).launch(**kw)
        return self._browsers[engine]

    def grab(
        self,
        stream: Stream,
        window_s: float = 20.0,
        interval_s: float = 2.0,
        play_timeout_s: float = 20.0,
        deadline: float | None = None,
    ) -> GrabResult:
        """Capture about window_s / interval_s frames from one stream, only if it is live right now."""
        live = self.live(stream)
        if live is not True:
            return GrabResult(stream=stream, engine="-", status="not_live" if live is False else "live_check_failed")
        last: GrabResult | None = None
        for engine in self.engines:
            res = self._grab_with(engine, stream, window_s, interval_s, play_timeout_s, deadline)
            if res.ok:
                return res
            last = res
            if res.status in ("youtube_bot_check", "stream_offline", "stream_unavailable", "stream_private"):
                # Another engine will not change what YouTube decided about this stream / this address.
                if res.status != "youtube_bot_check" or engine == self.engines[-1]:
                    return res
            if deadline is not None and time.time() > deadline:
                return res
        return last  # type: ignore[return-value]

    def live(self, stream: Stream) -> bool | None:
        """Whether YouTube says the stream is live right now (the watch page's isLive), asked once
        per grabber through the same egress as the browsers. None when the page can't be read.
        A recorded video must never be counted as the hall now."""
        if stream.youtube_id not in self._live:
            self._live[stream.youtube_id] = is_live(stream.youtube_id, self._proxy.url if self._proxy else None)
        return self._live[stream.youtube_id]

    def _grab_with(self, engine, stream, window_s, interval_s, play_timeout_s, deadline) -> GrabResult:
        t0 = time.time()
        res = GrabResult(stream=stream, engine=engine, status="timeout")
        try:
            browser = self._browser(engine)
            ctx = browser.new_context(viewport={"width": 1280, "height": 720}, locale="en-US")
            page = ctx.new_page()
        except Exception as e:  # browser not installed, crashed, etc.
            res.status = f"error:launch:{type(e).__name__}"
            res.seconds = time.time() - t0
            self._browsers.pop(engine, None)
            return res
        playing = False
        video_seen = False  # the <video> reported a width: the player may be showing a frame
        try:
            page.set_extra_http_headers({"Referer": stream.page_url})
            page.goto(stream.embed_url, wait_until="domcontentloaded", timeout=30_000)
            clicks = 0
            prev_t: float | None = None  # currentTime seen at the previous poll (None until the video exists)
            state = {}
            end_wait = t0 + play_timeout_s
            while time.time() < end_wait and (deadline is None or time.time() < deadline):
                state = page.evaluate(JS_PLAYER_STATE)
                reason = classify_player_text(state.get("text", ""))
                if reason:
                    res.status = reason
                    res.player_text = state.get("text", "")
                    break
                if state.get("video"):
                    t = float(state.get("t") or 0.0)
                    video_seen = video_seen or state.get("w", 0) > 0
                    # Playback is real only when a frame is decoded (readyState >= HAVE_CURRENT_DATA)
                    # and currentTime moved since the previous poll; the first poll can never qualify.
                    if (
                        state.get("w", 0) > 0
                        and not state.get("paused")
                        and int(state.get("ready") or 0) >= 2
                        and prev_t is not None
                        and t > prev_t
                        and t > 0
                    ):
                        playing = True
                        break
                    prev_t = t
                if clicks < 4 and (not state.get("video") or state.get("paused")):
                    try:
                        btn = page.query_selector("button.ytp-large-play-button")
                        if btn:
                            btn.click(timeout=2000)
                        else:
                            page.mouse.click(640, 360)
                        clicks += 1
                    except Exception:
                        pass
                time.sleep(self.poll_s)
            res.player_text = state.get("text", "") if isinstance(state, dict) else ""
            if not playing:
                if res.status == "timeout":
                    res.status = "no_video" if not state.get("video") else "timeout"
                if not video_seen:
                    self._shot(page, res, engine, stream)
                return res
            res.video_size = (int(state["w"]), int(state["h"]))
            # Capture window
            t_start = time.time()
            n_target = max(1, int(round(window_s / interval_s)))
            next_at = t_start
            while len(res.frames) < n_target and (deadline is None or time.time() < deadline):
                now = time.time()
                if now < next_at:
                    time.sleep(min(0.25, next_at - now))
                    continue
                data = page.evaluate(JS_GRAB_JPEG)
                if data:
                    frame = _decode_data_url(data)
                    if frame is not None:
                        res.frames.append((time.time(), frame))
                next_at += interval_s
            if frames_stalled(res.frames):
                res.status = "stalled"  # one picture for the whole window: not the hall right now
                res.frames.clear()
                return res
            res.status = "ok" if res.frames else "no_frames"
            return res
        except Exception as e:
            res.status = f"error:{type(e).__name__}"
            res.player_text = str(e)[:200]
            if not playing and not video_seen:
                # Only the pre-playback player state (an error wall, a paused player) is ever
                # screenshotted. Once the video has a frame, a screenshot would be a dining-room
                # frame, and frames stay out of state/.
                try:
                    self._shot(page, res, engine, stream)
                except Exception:
                    pass
            return res
        finally:
            res.seconds = time.time() - t0
            try:
                ctx.close()
            except Exception:
                pass

    def _shot(self, page, res: GrabResult, engine: str, stream: Stream) -> None:
        """Screenshot of the player while it is NOT playing (error wall / paused state), for diagnostics."""
        if not self.screenshot_dir or res.video_size != (0, 0):
            return
        import os

        os.makedirs(self.screenshot_dir, exist_ok=True)
        path = os.path.join(
            self.screenshot_dir, f"player_{stream.slug.replace('/', '_')}_{stream.youtube_id}_{engine}.png"
        )
        page.screenshot(path=path)
        res.screenshot = path


def iter_frames(stream: Stream, window_s: float = 20.0, interval_s: float = 2.0) -> Iterator[tuple[float, np.ndarray]]:
    """Convenience: yield (ts, BGR frame) for one stream over one window."""
    with FrameGrabber() as g:
        res = g.grab(stream, window_s=window_s, interval_s=interval_s)
        if not res.ok:
            raise RuntimeError(f"{stream.label}: {res.status} ({res.player_text[:80]})")
        yield from res.frames


def is_live(youtube_id: str, proxy_url: str | None = None, timeout: float = 15.0) -> bool | None:
    """True when YouTube's watch page says the video is a live broadcast that is on now, False when
    it is a recording or an ended stream, None when the page can't be read."""
    import re
    import urllib.request

    handlers = [urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})] if proxy_url else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        f"https://www.youtube.com/watch?v={youtube_id}",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.5 Safari/605.1.15",
            "Accept-Language": "en-US",
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            page = resp.read(4_000_000).decode("utf-8", "replace")
    except OSError:
        return None
    if '"isLiveContent":true' not in page:
        return False
    return bool(re.search(r'"isLive":true', page))
