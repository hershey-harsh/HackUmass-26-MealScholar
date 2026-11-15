"""Demo: a 20 s annotated clip per hall (green boxes, ids, live count) and one
contact sheet of the four halls, into ~/Services/umass-cams/demo/.

    python -I -m umass_cams.demo [--window 20] [--out DIR] [--all]

Halls whose stream cannot be played get a status tile on the sheet (and the
player screenshot next to it) instead of a clip. Nothing here touches Mongo.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2

from . import busy, config
from .streams import FrameGrabber, all_live_streams, primary_streams

log = logging.getLogger("umass_cams.demo")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=float, default=20.0)
    ap.add_argument(
        "--interval", type=float, default=0.5, help="seconds between frames; short, so tracking can follow people"
    )
    ap.add_argument("--out", default=str(config.DEMO_DIR))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bp = config.playwright_browsers_path()
    if bp:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", bp)
    streams = all_live_streams() if args.all else primary_streams()
    if args.only:
        wanted = set(args.only.split(","))
        streams = [s for s in all_live_streams() if s.youtube_id in wanted]

    from .detect import PersonTracker, annotate, contact_sheet, run_window, status_tile, write_annotated_mp4

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tiles = []
    summary = {}
    with FrameGrabber(screenshot_dir=str(out / "player-state")) as grabber:
        for s in streams:
            t0 = time.time()
            name = s.slug.replace("/", "_")
            res = grabber.grab(s, window_s=args.window, interval_s=args.interval, play_timeout_s=25.0)
            if not res.ok:
                msg = f"{res.status}\n{res.player_text[:70]}\n{res.player_text[70:140]}\nengine {res.engine}"
                tile = status_tile(msg, title=s.label)
                if res.screenshot and os.path.exists(res.screenshot):
                    shot = cv2.imread(res.screenshot)
                    if shot is not None:
                        tile = cv2.resize(shot, (640, 360))
                        tile = annotate(tile, [], s.label, res.status)
                tiles.append(tile)
                summary[s.youtube_id] = {
                    "hall": s.location_name,
                    "status": res.status,
                    "text": res.player_text[:160],
                    "screenshot": res.screenshot,
                    "seconds": round(res.seconds, 1),
                }
                log.warning("%s: no clip, %s (%s)", s.location_name, res.status, res.player_text[:60])
                continue
            tracker = PersonTracker(conf=config.conf_for(s.youtube_id))
            win = run_window(tracker, res.frames)  # counts config.COUNT_FRAMES of them, boxes held in between
            counts, ids, per_frame = win.counts, win.ids, win.per_frame
            people, tracks = busy.summarize_window(counts, ids)
            level = busy.level_from_thresholds(people, busy.thresholds_for(s.youtube_id))
            clip = write_annotated_mp4(per_frame, out / f"{name}_{stamp}.mp4", title=s.label, fps=1.0 / args.interval)
            ts, frame, dets = per_frame[-1]
            tiles.append(annotate(frame, dets, s.label, f"median {people}, {tracks} ids, {level}"))
            summary[s.youtube_id] = {
                "hall": s.location_name,
                "status": "ok",
                "frames": len(counts),
                "clip_frames": len(per_frame),
                "counts": counts,
                "detector": tracker.setup,
                "count_s": round(win.seconds, 1),
                "people": people,
                "tracks": tracks,
                "level": level,
                "clip": str(clip),
                "video_size": list(res.video_size),
                "seconds": round(time.time() - t0, 1),
            }
            log.info(
                "%s: %s frames=%d counts=%s people=%d tracks=%d level=%s",
                s.location_name,
                clip.name,
                len(counts),
                counts,
                people,
                tracks,
                level,
            )
    sheet_path = out / f"contact-sheet_{stamp}.png"
    cv2.imwrite(str(sheet_path), contact_sheet(tiles))
    (out / f"summary_{stamp}.json").write_text(json.dumps(summary, indent=1))
    log.info("contact sheet: %s", sheet_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
