from datetime import UTC, datetime, timedelta

import pytest
from umass_cams import busy
from umass_cams.busy import Thresholds

NY = busy.LOCAL_TZ


def test_summarize_window_median_and_distinct_tracks():
    assert busy.summarize_window([3, 5, 4, 9, 4], [1, 2, 3, 3, 4]) == (4, 4)
    assert busy.summarize_window([], None) == (0, 0)
    assert busy.summarize_window([2, 4], {7, 8, 9}) == (3, 3)


def test_percentile_interpolates():
    assert busy.percentile([1, 2, 3, 4, 5], 50) == 3
    assert busy.percentile([1, 2, 3, 4], 50) == 2.5
    assert busy.percentile([10], 75) == 10
    assert busy.percentile([0, 10], 40) == 4.0
    with pytest.raises(ValueError):
        busy.percentile([], 50)


def test_hour_of_week_uses_local_time():
    # Monday 2026-11-16 12:30 in New York = 17:30 UTC
    ts = datetime(2026, 11, 16, 17, 30, tzinfo=UTC)
    assert busy.hour_of_week(ts) == 0 * 24 + 12
    sun = datetime(2026, 11, 15, 23, 10, tzinfo=NY)
    assert busy.hour_of_week(sun) == 6 * 24 + 23


def test_thresholds_validate():
    with pytest.raises(ValueError):
        Thresholds(quiet_max=10, busy_min=5, full=20)
    th = Thresholds(quiet_max=5, busy_min=15, full=30)
    assert busy.level_from_thresholds(5, th) == "quiet"
    assert busy.level_from_thresholds(6, th) == "moderate"
    assert busy.level_from_thresholds(15, th) == "busy"
    assert busy.score_from_thresholds(15, th) == 0.5
    assert busy.score_from_thresholds(100, th) == 1.0
    for yid in ("r154Wa3jr38", "gvs6Mf-vR7o", "-yaPv3fQqiw", "urmHVZff1Io"):
        assert isinstance(busy.thresholds_for(yid), Thresholds)
    assert busy.thresholds_for("nope") is busy.DEFAULT_THRESHOLDS


def test_level_from_history_percentiles():
    same_hour = [2, 4, 4, 5, 6, 8, 10, 12, 15, 20]
    th = Thresholds(quiet_max=2, busy_min=18, full=30)  # wide bounds: the percentiles decide in between
    # p40 = 5.6, p75 = 12.75
    assert busy.level_from_history(5, same_hour, th)[0] == "quiet"
    assert busy.level_from_history(8, same_hour, th)[0] == "moderate"
    assert busy.level_from_history(13, same_hour, th)[0] == "busy"
    level, rank = busy.level_from_history(20, same_hour, th)
    assert level == "busy" and rank == pytest.approx(0.95)
    assert busy.level_from_history(0, same_hour, th)[1] == 0.0


def test_level_from_history_keeps_absolute_bounds():
    th = Thresholds(quiet_max=6, busy_min=16, full=35)  # Franklin
    # closed hall overnight: every prior night was empty, p40 == p75 == 0
    zeros = [0] * 40
    assert busy.level_from_history(0, zeros, th)[0] == "quiet"
    assert busy.level_from_history(1, zeros, th)[0] == "quiet"  # a cleaner is not "busy"
    assert busy.level_from_history(6, zeros, th)[0] == "quiet"
    # usual noon is 40: 30 or 20 people are still a busy hall, never "quiet"
    forties = [40] * 40
    assert busy.level_from_history(30, forties, th)[0] == "busy"
    assert busy.level_from_history(20, forties, th)[0] == "busy"
    assert busy.level_from_history(16, forties, th)[0] == "busy"
    # between the bounds a flat reference cannot rank anything -> moderate
    assert busy.level_from_history(10, [10] * 20, th)[0] == "moderate"
    assert busy.level_from_history(12, [10] * 20, th)[0] == "moderate"
    # between the bounds a spread reference decides
    spread = [7, 8, 8, 9, 10, 11, 12, 13, 14, 15]  # p40 = 9.6, p75 = 12.75
    assert busy.level_from_history(8, spread, th)[0] == "quiet"
    assert busy.level_from_history(11, spread, th)[0] == "moderate"
    assert busy.level_from_history(14, spread, th)[0] == "busy"


def _series(now, values, step_s=180):
    n = len(values)
    return [(now - timedelta(seconds=step_s * (n - i)), v) for i, v in enumerate(values)]


def test_trend_rising_falling_steady():
    now = datetime(2026, 11, 16, 17, 30, tzinfo=UTC)
    assert busy.trend(_series(now, [4, 4, 5, 9, 11]), now) == "rising"
    assert busy.trend(_series(now, [12, 11, 10, 5, 4]), now) == "falling"
    assert busy.trend(_series(now, [8, 9, 8, 8, 9]), now) == "steady"
    assert busy.trend(_series(now, [1, 2]), now) == "steady"  # too few points
    # samples older than 15 minutes are ignored
    old = _series(now - timedelta(minutes=40), [0, 0, 0, 0])
    assert busy.trend(old + _series(now, [10, 10, 10]), now) == "steady"
    # a one-person wobble on a small base is not a trend
    assert busy.trend(_series(now, [1, 1, 2, 2]), now) == "steady"


def _history(now, days, value_fn, every_min=30):
    """Samples every `every_min` minutes over `days` days ending at now."""
    out = []
    t = now - timedelta(days=days)
    while t <= now:
        out.append((t, value_fn(t)))
        t += timedelta(minutes=every_min)
    return out


def test_same_hour_samples_and_forecast():
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)  # Friday 12:00 NY

    def value(t):
        return 20 if busy.hour_of_week(t) == busy.hour_of_week(now) else 3

    hist = _history(now, 14, value)
    same = busy.same_hour_samples(hist, now)
    assert same and all(v == 20 for v in same)
    assert busy.history_days(hist) >= 14
    # 30 min ahead is still 12:xx NY -> same hour -> 20; 90 min ahead -> 3
    assert busy.forecast_30m(hist, now) == 20
    assert busy.forecast_30m(hist, now, ahead_s=90 * 60) == 3
    assert busy.forecast_30m([], now) is None
    # samples outside the 14-day window are ignored
    stale = [(now - timedelta(days=20), 99)]
    assert busy.same_hour_samples(stale, now) == []


def test_assess_cold_start_uses_thresholds():
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    a = busy.assess(people=3, youtube_id="r154Wa3jr38", history=[], now=now)
    assert a.method == "thresholds" and a.level == "quiet" and a.trend == "steady"
    assert a.forecast_30m is None and 0 <= a.score <= 1 and a.confidence == 0.4
    b = busy.assess(people=30, youtube_id="r154Wa3jr38", history=[], now=now)
    assert b.level == "busy" and b.score > a.score


def test_assess_switches_to_history_after_three_days():
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)

    def value(t):
        return 30 if busy.hour_of_week(t) == busy.hour_of_week(now) else 2

    # one day of history spans 2 local dates, 1 of them prior (< 3) -> still thresholds
    one_day = _history(now - timedelta(hours=1), 1, value, every_min=15)
    assert busy.history_days(one_day) == 2 and busy.history_days(one_day, now) == 1
    a = busy.assess(people=30, youtube_id="gvs6Mf-vR7o", history=one_day, now=now)
    assert a.method == "thresholds"
    # 3-7 days: only one prior Friday (4 samples at 12:xx) -> hour-of-day fallback over every prior day
    four_days = _history(now - timedelta(hours=1), 4, value, every_min=15)
    b = busy.assess(people=30, youtube_id="gvs6Mf-vR7o", history=four_days, now=now)
    assert b.method == "history" and b.reference["basis"] == "hour_of_day"
    assert b.level == "busy"  # 30 vs mostly-2 samples at 12:xx on other days (and 30 >= busy_min anyway)
    # two full weeks: enough same hour-of-week samples (every 3 min -> 20/hour/week)
    two_weeks = _history(now - timedelta(hours=1), 14, value, every_min=3)
    c = busy.assess(people=30, youtube_id="gvs6Mf-vR7o", history=two_weeks, now=now)
    assert c.method == "history" and c.reference["basis"] == "hour_of_week"
    # 30 equals every same-hour sample (flat history, p40 == p75 == 30) but is above Franklin's
    # busy_min of 16: the absolute bound wins, a packed hall is busy even when it is always packed
    assert c.level == "busy" and c.score == pytest.approx(30 / 35, abs=1e-3)
    assert c.reference["p40"] == c.reference["p75"] == 30 and c.reference["rank"] == 0.5
    d = busy.assess(people=10, youtube_id="gvs6Mf-vR7o", history=two_weeks, now=now)
    assert d.level == "moderate" and d.reference["rank"] == 0.0  # flat reference, between the bounds
    e = busy.assess(people=4, youtube_id="gvs6Mf-vR7o", history=two_weeks, now=now)
    assert e.level == "quiet"  # <= quiet_max
    assert c.forecast_30m == 30
    assert c.reference["same_hour_n"] >= 6 and c.confidence > b.confidence > 0.4


def test_history_reference_excludes_the_current_day():
    """A rush on day 4 is graded against the three prior days, not against itself (review finding)."""
    noon = datetime(2026, 11, 20, 17, 45, tzinfo=UTC)  # Fri 12:45 NY
    hist = [(noon - timedelta(days=d, minutes=3 * i), 10) for d in (1, 2, 3) for i in range(0, 24 * 20)]
    hist += [(noon - timedelta(minutes=3 * i), 40) for i in range(1, 15)]  # the last 42 min of today: 40 people
    a = busy.assess(40, "gvs6Mf-vR7o", hist, noon)
    assert a.method == "history" and a.level == "busy"
    assert a.reference["p40"] == a.reference["p75"] == 10 and a.reference["rank"] == 1.0
    assert busy.history_days(hist, noon) == 4  # Mon 12:48 .. Thu 12:45 touch 4 prior dates; Fri excluded
    assert busy.history_days(hist) == 5
    # the reference sets never contain today's readings
    assert 40 not in busy.same_hour_samples(hist, noon)
    assert 40 not in busy.same_hour_of_day_samples(hist, noon)
    # same shape between the bounds: usual 8 at this hour, today 14 -> busy by percentile, not "moderate"
    hist2 = [(noon - timedelta(days=d, minutes=3 * i), 8 + (i % 3)) for d in (1, 2, 3) for i in range(0, 24 * 20)]
    hist2 += [(noon - timedelta(minutes=3 * i), 14) for i in range(1, 15)]
    b = busy.assess(14, "gvs6Mf-vR7o", hist2, noon)
    assert b.method == "history" and b.level == "busy" and b.reference["p75"] < 14


def test_history_days_counts_prior_days_only():
    """25 hours of readings touch 3 calendar dates but are 2 prior days: still cold start (review finding)."""
    now = datetime(2026, 11, 20, 5, 30, tzinfo=UTC)  # Fri 00:30 NY
    h25 = [(now - timedelta(minutes=3 * i), 5) for i in range(0, 25 * 20)]
    assert busy.history_days(h25) == 3
    assert busy.history_days(h25, now) == 2
    assert busy.assess(5, "gvs6Mf-vR7o", h25, now).method == "thresholds"


def test_history_mode_empty_hall_at_night_is_quiet_and_packed_noon_is_busy():
    """Review finding: no absolute floor in history mode."""
    now = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)  # Fri 03:00 NY

    def val(t):
        return 0 if busy.to_local(t).hour < 6 or busy.to_local(t).hour >= 22 else 15

    hist = []
    t = now - timedelta(days=14)
    while t < now:
        hist.append((t, val(t)))
        t += timedelta(minutes=3)
    for p in (0, 1):
        a = busy.assess(p, "gvs6Mf-vR7o", hist, now)
        assert a.method == "history" and a.level == "quiet", (p, a)
        assert a.score == pytest.approx(p / 35, abs=1e-3)
    noon = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    h3 = []
    t = noon - timedelta(days=14)
    while t < noon - timedelta(hours=1):
        h3.append((t, 40 if busy.to_local(t).hour == 12 else 5))
        t += timedelta(minutes=3)
    for p in (30, 25, 22):
        a = busy.assess(p, "r154Wa3jr38", h3, noon)  # Worcester busy_min 22
        assert a.method == "history" and a.level == "busy", (p, a)
    assert busy.assess(20, "r154Wa3jr38", h3, noon).level == "moderate"  # between 8 and 22, p40 == p75 == 40
    assert busy.assess(8, "r154Wa3jr38", h3, noon).level == "quiet"


def test_assess_low_frame_count_lowers_confidence():
    now = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    full = busy.assess(people=5, youtube_id="x", history=[], now=now, frames=10)
    few = busy.assess(people=5, youtube_id="x", history=[], now=now, frames=2)
    assert few.confidence < full.confidence
