"""Paths and settings for the sampler on the Mac mini.

Settings come from ~/Services/umass-cams/.env (mode 600). MONGODB_URI is not
copied there: if it is missing, it is read from the file named by
UMASS_CAMS_MONGO_ENV (default ~/Services/mealscholar/.env). The URI is never
logged or printed.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

SERVICE_DIR = Path(os.environ.get("UMASS_CAMS_HOME", Path.home() / "Services" / "umass-cams"))
ENV_FILE = SERVICE_DIR / ".env"
STATE_DIR = SERVICE_DIR / "state"
DEMO_DIR = SERVICE_DIR / "demo"
MODEL_DIR = SERVICE_DIR / "models"
LATEST_JSON = STATE_DIR / "latest.json"

DB_NAME = "umass_dining"
OCCUPANCY_COLLECTION = "occupancy"
LOCATIONS_COLLECTION = "locations"

# Counting setup, chosen on 2026-11-14 from 60+ detector configurations (yolo11 n..x at 640-2560, tile
# grids, preprocessing, pose weights, RT-DETR) scored against 14 frames of the three primary cams
# that were counted by eye three times each (Worcester 72-77 people, Berkshire 32-36, Hampshire
# 26-27). The old setup (yolo11n, full frame at 1280, conf 0.35) saw 9 of Worcester's 74 and 1 of
# Hampshire's 27 (MAE 40 people). This one: yolo11l on the full frame at 1280 plus 2x2 tiles with
# 20 % overlap, each tile upsampled to 1280, the union merged by greedy NMS at IoU 0.5 with the
# boxes a tile edge cut folded into the box that holds them (SEAM_CONTAIN; without it a seated
# person on a seam was two or three boxes, and the count only looked right because those
# duplicates offset misses elsewhere), cut at a per-camera conf: MAE 3.6, bias -1.2, 12 of 14
# frames inside the by-eye low-high range (Worcester bias +0.7, Berkshire +0.5, Hampshire -6.0:
# the blurred lens hides people from any detector). No single conf is honest for all three cams:
# the close-up Berkshire view over-counts at the conf the hazy, high Worcester view needs.
# UMASS_CAMS_MODEL is read from the process environment only (the launchd job sets none).
MODEL_NAME = os.environ.get("UMASS_CAMS_MODEL", "yolo11l.pt")
IMGSZ = 1280  # the streams are 1280x720; at 640 people across the room are missed
TILES = (2, 2)  # columns, rows; (1, 1) counts the full frame only
TILE_OVERLAP = 0.2  # 1280x720 -> tiles 711x400 at x 0/569, y 0/320
TILE_IMGSZ = 1280  # each tile upsampled ~1.8x: the small, far, half-hidden people get pixels
MERGE_IOU = 0.5  # one box per person across tiles and the full frame
SEAM_CONTAIN = 0.8  # a tile-edge fragment is dropped when a kept box holds this much of it (detect.merge_boxes)
CONF_DEFAULT = 0.20  # cams not in CAMERA_CONF (not benchmarked)
CAMERA_CONF: dict[str, float] = {
    "7wHgOpwHz8k": 0.075,  # Worcester Commons North: high, hazy, dozens of small seated people
    "AoceCqa6MWw": 0.25,  # Berkshire Commons Entrance: close-up, over-counts at a lower conf
    "RprORF_ggOA": 0.05,  # Hampshire Commons South: blurred lens, every box is weak
}
# "auto" = the Mac's GPU (MPS) when torch has it, else CPU. Counts are identical on both (checked on
# the 14 frames); the GPU is ~4x faster. UMASS_CAMS_DEVICE=cpu pins it.
DEVICE = os.environ.get("UMASS_CAMS_DEVICE", "auto")

WINDOW_S = 16.0
FRAME_INTERVAL_S = 0.5  # 32 frames a window: the clip plays smoothly
# Counting costs 2.3 s a frame on the CPU (4 threads, as launchd runs it) and 0.6 s on MPS, so a
# window is counted on COUNT_FRAMES frames spread evenly over it (first and last included, 2.5 s
# apart); the median of those is the reading, and the clip shows every frame with the boxes of the
# last counted one (detect.run_window). Measured 2026-11-14, three halls with 6 counted frames each,
# grab and clip included: 67 s on MPS (22-23 s a hall, 4 s of it counting) and 117 s on the CPU
# (31-52 s a hall, 13-14 s of it counting; the 52 s was a slow player start, not the counter).
# run_window drops planned frames when the pass would overrun the budget.
COUNT_FRAMES = 6
# Four halls take ~85 s when WebKit plays at once; 146 s was measured with three WebKit timeouts
# falling back to Chromium. launchd starts a pass every 180 s and never overlaps two.
TOTAL_BUDGET_S = 150.0
# A hall still to come needs this much of the budget (play wait + window + a few counted frames); the
# hall being counted stops counting early rather than eat into it.
HALL_RESERVE_S = 35.0
CLIP_SIZE = (960, 540)  # the boxed clip and poster the app shows; frames are 1280x720


def conf_for(youtube_id: str) -> float:
    """The camera's confidence cut (see CAMERA_CONF)."""
    return CAMERA_CONF.get(youtube_id, CONF_DEFAULT)


LOCAL_TZ = "America/New_York"


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        values.update({k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None})
    # Process environment wins over the file.
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


SETTINGS = _load_env()


def setting(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, SETTINGS.get(key, default))


def mongodb_uri() -> str:
    """Return the Mongo URI without ever printing it."""
    uri = setting("MONGODB_URI")
    if uri:
        return uri
    ref = Path(setting("UMASS_CAMS_MONGO_ENV", str(Path.home() / "Services" / "mealscholar" / ".env")))
    if ref.exists():
        uri = dotenv_values(ref).get("MONGODB_URI")
        if uri:
            return uri
    raise RuntimeError("MONGODB_URI not set and not found in the referenced env file")


def playwright_browsers_path() -> str | None:
    return setting("PLAYWRIGHT_BROWSERS_PATH")


def clip_dir() -> Path:
    """Where each hall's latest boxed clip goes (<location_id>.mp4 and .jpg). The MealScholar app reads
    them from its data folder (MEALSCHOLAR_HOME/cams), so that is the default."""
    return Path(
        setting("UMASS_CAMS_CLIP_DIR") or Path.home() / "Services" / "mealscholar" / "data" / "cams"
    ).expanduser()


def ensure_dirs() -> None:
    for d in (STATE_DIR, DEMO_DIR, MODEL_DIR):
        d.mkdir(parents=True, exist_ok=True)
