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

MODEL_NAME = os.environ.get("UMASS_CAMS_MODEL", "yolo11n.pt")
CONF = 0.35
IMGSZ = 1280  # the streams are 1280x720; at 640 people across the room are missed
WINDOW_S = 16.0
FRAME_INTERVAL_S = 0.5  # 32 frames a window: the clip plays smoothly and ByteTrack keeps ids between frames
# Four halls take ~85 s when WebKit plays at once; 146 s was measured with three WebKit timeouts
# falling back to Chromium. launchd starts a pass every 180 s and never overlaps two.
TOTAL_BUDGET_S = 150.0
CLIP_SIZE = (960, 540)  # the boxed clip and poster the app shows; frames are 1280x720

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
