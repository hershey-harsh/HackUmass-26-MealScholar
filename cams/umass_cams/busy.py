"""Busy / not-busy predictor. Pure functions, no I/O.

Per hall and sample window (~20 s, ~10 frames):
  people = median person count across the frames
  tracks = number of distinct track ids seen in the window

Level:
  * the per-camera absolute thresholds (CAMERA_THRESHOLDS) are hard bounds in
    every mode: people <= quiet_max is always quiet, people >= busy_min is
    always busy. score = people / full in every mode.
  * cold start (fewer than MIN_HISTORY_DAYS distinct PRIOR days of history for
    the camera): the thresholds alone decide; between the bounds -> moderate.
  * with history: between the bounds, compare `people` with the samples from
    the same hour of the week over the last 14 days: quiet <= p40, busy >= p75,
    moderate between. The reference set is PRIOR days only: readings from the
    current local day (i.e. the readings taken minutes ago in this same hour)
    are never part of the distribution the current reading is graded against.
    Until a full week has passed (fewer than MIN_SAME_HOUR_SAMPLES prior-day
    samples at that hour-of-week) the same hour of the day across all prior
    days is used instead, flagged as basis "hour_of_day" with lower confidence.

Trend: rising / steady / falling from the last 15 minutes of samples.
Forecast: median people at the hour-of-week 30 minutes from now, when the
profile has samples for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/New_York")

LEVELS = ("quiet", "moderate", "busy", "unknown")
MIN_HISTORY_DAYS = 3
MIN_SAME_HOUR_SAMPLES = 6
HISTORY_WINDOW_DAYS = 14
TREND_WINDOW_S = 15 * 60
FORECAST_AHEAD_S = 30 * 60
QUIET_PCT = 40
BUSY_PCT = 75


@dataclass(frozen=True)
class Thresholds:
    quiet_max: int  # people <= quiet_max -> quiet
    busy_min: int  # people >= busy_min  -> busy
    full: int  # people at which score saturates to 1.0

    def __post_init__(self):
        if not (0 <= self.quiet_max < self.busy_min <= self.full):
            raise ValueError("need 0 <= quiet_max < busy_min <= full")


# Cold-start thresholds per camera (youtube id). PROVISIONAL: these are not yet
# calibrated on real frames, because the Mac mini's current egress (a Mullvad
# exit node) is bot-walled by YouTube and no frame could be captured on
# 2026-11-14. They are sized for the wide dining-room views the pages show
# (a yolo11n pass at imgsz 640 typically resolves 0-40 people in such a view)
# and MUST be re-checked against the first real frames: open the demo clips,
# note the counts at a visibly quiet and a visibly packed moment, and set
# quiet_max to roughly the quiet count and busy_min to ~60% of the packed count.
DEFAULT_THRESHOLDS = Thresholds(quiet_max=6, busy_min=18, full=35)
CAMERA_THRESHOLDS: dict[str, Thresholds] = {
    "r154Wa3jr38": Thresholds(quiet_max=8, busy_min=22, full=45),  # Worcester main (largest hall)
    "7wHgOpwHz8k": Thresholds(quiet_max=5, busy_min=14, full=30),  # Worcester Commons North
    "89KPOnjHEC4": Thresholds(quiet_max=5, busy_min=14, full=30),  # Worcester Commons South
    "gvs6Mf-vR7o": Thresholds(quiet_max=6, busy_min=16, full=35),  # Franklin main
    "-yaPv3fQqiw": Thresholds(quiet_max=6, busy_min=18, full=40),  # Berkshire main
    "AoceCqa6MWw": Thresholds(quiet_max=3, busy_min=10, full=20),  # Berkshire entrance (narrow view)
    "urmHVZff1Io": Thresholds(quiet_max=6, busy_min=18, full=40),  # Hampshire main
    "RprORF_ggOA": Thresholds(quiet_max=5, busy_min=14, full=30),  # Hampshire Commons South
    "nMFduwGyYUY": Thresholds(quiet_max=3, busy_min=8, full=16),  # Roots Cafe (small venue)
}


def thresholds_for(youtube_id: str) -> Thresholds:
    return CAMERA_THRESHOLDS.get(youtube_id, DEFAULT_THRESHOLDS)


# --------------------------------------------------------------------------
# Window summary
# --------------------------------------------------------------------------


def summarize_window(counts: list[int], track_ids: set[int] | list[int] | None = None) -> tuple[int, int]:
    """(people, tracks) for one sample window. people = median count, tracks = distinct ids."""
    if not counts:
        return 0, 0
    people = int(round(median(counts)))
    tracks = len(set(track_ids or ()))
    return people, tracks


# --------------------------------------------------------------------------
# Percentiles and hour-of-week
# --------------------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (pct in 0..100) of a non-empty list."""
    if not values:
        raise ValueError("percentile of empty list")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    frac = k - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def to_local(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(LOCAL_TZ)


def hour_of_week(ts: datetime) -> int:
    """0 = Monday 00:00-00:59 local, 167 = Sunday 23:00-23:59 local."""
    local = to_local(ts)
    return local.weekday() * 24 + local.hour


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def history_days(samples: list[tuple[datetime, int]], now: datetime | None = None) -> int:
    """Number of distinct local calendar days present in the samples.

    With `now`, the current local day is not counted: history means prior days,
    so 25 hours of readings that touch three calendar dates count as 2, not 3.
    """
    days = {to_local(ts).date() for ts, _ in samples}
    if now is not None:
        days.discard(to_local(now).date())
    return len(days)


def reference_samples(
    history: list[tuple[datetime, int]],
    now: datetime,
    window_days: int = HISTORY_WINDOW_DAYS,
    how: int | None = None,
    hod: int | None = None,
) -> list[int]:
    """People counts from prior local days within `window_days` whose hour-of-week == `how`
    or local hour-of-day == `hod`. Readings from the current local day are excluded so a
    reading is never graded against the readings taken minutes before it."""
    now = _aware(now)
    today = to_local(now).date()
    cutoff = now - timedelta(days=window_days)
    out = []
    for ts, people in history:
        ts = _aware(ts)
        if not (cutoff <= ts <= now):
            continue
        local = to_local(ts)
        if local.date() == today:
            continue
        if how is not None and local.weekday() * 24 + local.hour != how:
            continue
        if hod is not None and local.hour != hod:
            continue
        out.append(int(people))
    return out


def same_hour_samples(
    history: list[tuple[datetime, int]], now: datetime, window_days: int = HISTORY_WINDOW_DAYS, how: int | None = None
) -> list[int]:
    """Prior-day counts from the last `window_days` with the same hour-of-week as `now` (or `how`)."""
    target = hour_of_week(now) if how is None else how
    return reference_samples(history, now, window_days, how=target)


def same_hour_of_day_samples(
    history: list[tuple[datetime, int]], now: datetime, window_days: int = HISTORY_WINDOW_DAYS
) -> list[int]:
    """Prior-day counts from the last `window_days` with the same local hour of day as `now` (any weekday)."""
    return reference_samples(history, now, window_days, hod=to_local(now).hour)


# --------------------------------------------------------------------------
# Level and score
# --------------------------------------------------------------------------


def level_from_thresholds(people: int, th: Thresholds) -> str:
    if people <= th.quiet_max:
        return "quiet"
    if people >= th.busy_min:
        return "busy"
    return "moderate"


def score_from_thresholds(people: int, th: Thresholds) -> float:
    return max(0.0, min(1.0, people / float(th.full)))


def percentile_rank(people: int, same_hour: list[int]) -> float:
    """Fraction of the reference samples strictly below `people`, plus half the ties (0-1)."""
    below = sum(1 for x in same_hour if x < people)
    ties = sum(1 for x in same_hour if x == people)
    return max(0.0, min(1.0, (below + 0.5 * ties) / len(same_hour)))


def level_from_history(people: int, same_hour: list[int], th: Thresholds) -> tuple[str, float]:
    """Level against the same-hour distribution, inside the camera's hard bounds; plus the percentile rank.

    The absolute thresholds always win at the edges: an empty hall at 3 am is
    quiet even when every prior night was empty too, and a packed hall at noon
    is busy even when every prior noon was packed. Between the bounds the prior
    days decide: quiet <= p40, busy >= p75, moderate between. A flat reference
    (p40 == p75) cannot rank anything, so between the bounds it is moderate.
    """
    if people <= th.quiet_max:
        level = "quiet"
    elif people >= th.busy_min:
        level = "busy"
    else:
        p40 = percentile(same_hour, QUIET_PCT)
        p75 = percentile(same_hour, BUSY_PCT)
        if p75 > p40 and people <= p40:
            level = "quiet"
        elif p75 > p40 and people >= p75:
            level = "busy"
        else:
            level = "moderate"
    return level, percentile_rank(people, same_hour)


def trend(recent: list[tuple[datetime, int]], now: datetime, window_s: int = TREND_WINDOW_S) -> str:
    """rising / steady / falling from the samples in the last `window_s` seconds (incl. the current one).

    Compares the mean of the older half with the mean of the newer half. A move
    counts when it is at least 2 people and at least 20% of the older mean.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cutoff = now - timedelta(seconds=window_s)
    pts = sorted(((ts if ts.tzinfo else ts.replace(tzinfo=UTC)), int(p)) for ts, p in recent)
    pts = [(ts, p) for ts, p in pts if cutoff <= ts <= now]
    if len(pts) < 3:
        return "steady"
    half = len(pts) // 2
    older = [p for _, p in pts[:half]]
    newer = [p for _, p in pts[half:]]
    a = sum(older) / len(older)
    b = sum(newer) / len(newer)
    delta = b - a
    if abs(delta) >= 2 and abs(delta) >= 0.2 * max(a, 1.0):
        return "rising" if delta > 0 else "falling"
    return "steady"


def forecast_30m(
    history: list[tuple[datetime, int]],
    now: datetime,
    ahead_s: int = FORECAST_AHEAD_S,
    window_days: int = HISTORY_WINDOW_DAYS,
) -> int | None:
    """Median people at the hour-of-week `ahead_s` from now, over the last `window_days`; None without data."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    target = hour_of_week(now + timedelta(seconds=ahead_s))
    vals = same_hour_samples(history, now, window_days=window_days, how=target)
    if not vals:
        return None
    return int(round(median(vals)))


@dataclass(frozen=True)
class Assessment:
    level: str
    score: float
    trend: str
    forecast_30m: int | None
    method: str  # "thresholds" | "history"
    confidence: float  # 0-1
    reference: dict


def assess(
    people: int,
    youtube_id: str,
    history: list[tuple[datetime, int]],
    now: datetime,
    recent: list[tuple[datetime, int]] | None = None,
    frames: int = 10,
) -> Assessment:
    """Combine everything for one sample.

    history: (ts, people) samples for this camera (any range; filtered here).
    recent:  (ts, people) samples from the last 15 minutes, current one included
             (defaults to `history` filtered by time).
    """
    recent = history if recent is None else recent
    tr = trend(recent + [(now, people)], now)
    fc = forecast_30m(history, now)
    same_hour = same_hour_samples(history, now)  # prior days only
    basis = "hour_of_week"
    if len(same_hour) < MIN_SAME_HOUR_SAMPLES:
        # Same hour-of-week only recurs weekly, so for the first week of history
        # fall back to the same hour of the day across every prior day in the window.
        same_hour = same_hour_of_day_samples(history, now)
        basis = "hour_of_day"
    days = history_days(history, now)  # prior local days, today excluded
    th = thresholds_for(youtube_id)
    score = score_from_thresholds(people, th)  # people / full in every mode
    bounds = {"quiet_max": th.quiet_max, "busy_min": th.busy_min, "full": th.full, "history_days": days}
    if days >= MIN_HISTORY_DAYS and len(same_hour) >= MIN_SAME_HOUR_SAMPLES:
        level, rank = level_from_history(people, same_hour, th)
        conf = min(1.0, 0.5 + 0.02 * len(same_hour))  # 6 samples -> 0.62, 25+ -> 1.0
        if basis == "hour_of_day":
            conf *= 0.8
        ref = {
            "basis": basis,
            "same_hour_n": len(same_hour),
            "p40": percentile(same_hour, QUIET_PCT),
            "p75": percentile(same_hour, BUSY_PCT),
            "rank": round(rank, 3),
            **bounds,
        }
        method = "history"
    else:
        level = level_from_thresholds(people, th)
        conf = 0.4
        ref = bounds
        method = "thresholds"
    if frames < 5:
        conf *= 0.6
    return Assessment(
        level=level,
        score=round(score, 3),
        trend=tr,
        forecast_30m=fc,
        method=method,
        confidence=round(conf, 2),
        reference=ref,
    )
