"""Sampler CLI: one pass over the dining-hall cams.

For each hall: capture a ~20 s window, detect + track people, compute the busy
level, upsert one occupancy document, then write state/latest.json and log one
line. One hall failing never stops the others; an offline or blocked stream is
recorded as level "unknown" with a reason. Whole run stays under 120 s.

    python -I -m umass_cams.sample [--all] [--window 20] [--dry-run] [--screenshots DIR]
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import busy, config
from .streams import Stream, all_live_streams, primary_streams

log = logging.getLogger("umass_cams.sample")

SOURCE = "youtube-embed/playwright"


def build_doc(
    stream: Stream,
    ts: datetime,
    window_s: float,
    frames: int,
    people: int,
    tracks: int,
    assessment: busy.Assessment | None,
    model: str,
    engine: str | None,
    reason: str | None = None,
    extra: dict | None = None,
) -> dict:
    """The occupancy document. Counts and derived numbers only; never a frame."""
    doc = {
        "location_id": stream.location_id,
        "location_name": stream.location_name,
        "youtube_id": stream.youtube_id,
        "camera": stream.label,
        "primary": stream.primary,
        "ts": ts,
        "window_s": float(window_s),
        "frames": int(frames),
        "people": int(people),
        "tracks": int(tracks),
        "level": assessment.level if assessment else "unknown",
        "score": float(assessment.score) if assessment else None,
        "trend": assessment.trend if assessment else "unknown",
        "forecast_30m": assessment.forecast_30m if assessment else None,
        "method": assessment.method if assessment else None,
        "confidence": assessment.confidence if assessment else 0.0,
        "reference": assessment.reference if assessment else None,
        "model": model,
        "source": SOURCE + (f"/{engine}" if engine else ""),
        "host": socket.gethostname(),
    }
    if reason:
        doc["reason"] = reason
    if extra:
        doc.update(extra)
    return doc


def load_history(
    coll, stream: Stream, now: datetime, days: int = busy.HISTORY_WINDOW_DAYS
) -> list[tuple[datetime, int]]:
    cur = coll.find(
        {"youtube_id": stream.youtube_id, "level": {"$ne": "unknown"}, "ts": {"$gte": now - timedelta(days=days)}},
        {"ts": 1, "people": 1},
    ).sort("ts", 1)
    out = []
    for d in cur:
        ts = d["ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        out.append((ts, int(d.get("people", 0))))
    return out


def ensure_indexes(coll) -> None:
    coll.create_index([("location_id", 1), ("ts", -1)], name="location_ts")
    coll.create_index([("youtube_id", 1), ("ts", -1)], name="youtube_ts")


def json_safe(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            out[k] = v.astimezone(UTC).isoformat().replace("+00:00", "Z")
        elif k == "_id":
            out[k] = str(v)
        else:
            out[k] = v
    return out


def sample_one(
    grabber, tracker_factory, stream: Stream, coll, now: datetime, window_s: float, deadline: float, dry_run: bool
) -> dict:
    t0 = time.time()
    model_name = "yolo11n"
    res = grabber.grab(
        stream, window_s=window_s, interval_s=config.FRAME_INTERVAL_S, play_timeout_s=20.0, deadline=deadline
    )
    engine = res.engine
    if not res.ok:
        reason = res.status
        doc = build_doc(
            stream,
            now,
            window_s,
            0,
            0,
            0,
            None,
            model_name,
            engine,
            reason=reason,
            extra={"player_text": res.player_text[:160], "grab_s": round(res.seconds, 1)},
        )
        log.warning(
            "%s (%s): unknown, %s [%s] %.1fs",
            stream.location_name,
            stream.youtube_id,
            reason,
            res.player_text[:60],
            time.time() - t0,
        )
        return _store(coll, doc, dry_run)

    from .detect import run_window  # heavy import only when there are frames

    tracker = tracker_factory()
    counts, ids, _ = run_window(tracker, res.frames)
    people, tracks = busy.summarize_window(counts, ids)
    history = load_history(coll, stream, now) if coll is not None else []
    assessment = busy.assess(people, stream.youtube_id, history, now, frames=len(counts))
    doc = build_doc(
        stream,
        now,
        window_s,
        len(counts),
        people,
        tracks,
        assessment,
        tracker.model_name,
        engine,
        extra={"counts": counts, "video_size": list(res.video_size), "grab_s": round(res.seconds, 1)},
    )
    log.info(
        "%s (%s): people=%d tracks=%d level=%s score=%.2f trend=%s forecast_30m=%s frames=%d %.1fs",
        stream.location_name,
        stream.youtube_id,
        people,
        tracks,
        assessment.level,
        assessment.score,
        assessment.trend,
        assessment.forecast_30m,
        len(counts),
        time.time() - t0,
    )
    return _store(coll, doc, dry_run)


STORE_FAILURES = 0


def _store(coll, doc: dict, dry_run: bool) -> dict:
    """Upsert one doc. A Mongo failure is logged (type only) and never raised: the pass goes on
    with the other halls and the doc still reaches latest.json."""
    global STORE_FAILURES
    if coll is not None and not dry_run:
        key = {"youtube_id": doc["youtube_id"], "ts": doc["ts"]}
        try:
            coll.update_one(key, {"$set": doc}, upsert=True)
        except Exception as e:
            STORE_FAILURES += 1
            log.error(
                "%s (%s): mongo write failed (%s); doc kept for latest.json only",
                doc.get("location_name"),
                doc.get("youtube_id"),
                type(e).__name__,
            )
    return doc


def open_collection(dry_run: bool):
    """The occupancy collection, or None when Mongo is unreachable (logged, type only)."""
    try:
        from pymongo import MongoClient

        client = MongoClient(config.mongodb_uri(), serverSelectionTimeoutMS=10_000)
        coll = client[config.DB_NAME][config.OCCUPANCY_COLLECTION]
        if not dry_run:
            ensure_indexes(coll)
        return coll
    except Exception as e:
        log.error("mongo unavailable (%s); continuing without storage", type(e).__name__)
        return None


def write_latest(docs: list[dict], path: Path | None = None) -> None:
    path = path or config.LATEST_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "halls": {str(d["location_id"]): json_safe(d) for d in docs if d.get("primary")},
        "cameras": [json_safe(d) for d in docs],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="sample every live camera, not just the hall iframes")
    ap.add_argument("--window", type=float, default=config.WINDOW_S, help="seconds per hall window")
    ap.add_argument("--budget", type=float, default=config.TOTAL_BUDGET_S, help="total seconds for the run")
    ap.add_argument("--dry-run", action="store_true", help="no Mongo writes")
    ap.add_argument("--no-mongo", action="store_true", help="do not connect to Mongo at all")
    ap.add_argument("--screenshots", default=None, help="save player screenshots on failure to this dir")
    ap.add_argument("--only", default=None, help="comma-separated youtube ids to sample")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    config.ensure_dirs()
    t_start = time.time()
    deadline = t_start + args.budget
    streams = all_live_streams() if args.all else primary_streams()
    if args.only:
        wanted = set(args.only.split(","))
        streams = [s for s in streams if s.youtube_id in wanted] or [
            s for s in all_live_streams() if s.youtube_id in wanted
        ]

    coll = None if args.no_mongo else open_collection(args.dry_run)

    from .streams import FrameGrabber

    bp = config.playwright_browsers_path()
    if bp:
        import os

        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", bp)

    def tracker_factory():
        from .detect import PersonTracker

        return PersonTracker()

    docs: list[dict] = []
    try:
        with FrameGrabber(screenshot_dir=args.screenshots) as grabber:
            for s in streams:
                now = datetime.now(UTC).replace(microsecond=0)
                remaining = deadline - time.time()
                if remaining < 8:
                    doc = build_doc(s, now, args.window, 0, 0, 0, None, "yolo11n", None, reason="skipped_time_budget")
                    log.warning("%s: skipped, %.0fs left in budget", s.location_name, remaining)
                    docs.append(_store(coll, doc, args.dry_run))
                    continue
                try:
                    docs.append(sample_one(grabber, tracker_factory, s, coll, now, args.window, deadline, args.dry_run))
                except Exception as e:  # one hall never stops the others
                    log.exception("%s: failed: %s", s.location_name, e)
                    doc = build_doc(
                        s,
                        now,
                        args.window,
                        0,
                        0,
                        0,
                        None,
                        "yolo11n",
                        None,
                        reason=f"error:{type(e).__name__}",
                        extra={"player_text": str(e)[:160]},
                    )
                    docs.append(_store(coll, doc, args.dry_run))
    finally:
        # Whatever happened (a browser launch failure, a crash between halls), the halls that
        # were sampled reach latest.json so the app never serves an older pass than it has to.
        if docs:
            write_latest(docs)
    if STORE_FAILURES:
        log.error("%d of %d mongo writes failed this pass", STORE_FAILURES, len(docs))
    log.info("done: %d cameras in %.1fs -> %s", len(docs), time.time() - t_start, config.LATEST_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
