"""How busy each dining hall is right now, from the hall cams.

The cams sampler (cams/, launchd org.mannava.umass-cams.sample) watches each hall's live cam
every 3 minutes and writes one reading per camera to umass_dining.occupancy: the people in
view (the median over 20 seconds) and a level, quiet, moderate or busy. GET /api/dining/busy
answers with the latest reading per hall from the main camera, if it is recent. Read-only.

The sampler also leaves each hall's latest boxed clip (green boxes on the people it counted) in
MEALSCHOLAR_HOME/cams as <location_id>.mp4 and <location_id>.jpg; GET /api/dining/cams/{id}/clip.mp4
and /poster.jpg serve them, and a reading links them while they are under 15 minutes old.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .auth_store import utc_now
from .config import Settings

log = logging.getLogger(__name__)

FRESH_FOR = timedelta(minutes=15)
"""Older readings are left out: the sampler or a cam is down."""
CACHE_SECONDS = 30.0
LEVELS = ("quiet", "moderate", "busy")
CLIP_FRESH_FOR = timedelta(minutes=15)
"""An older clip is not linked: it no longer shows the hall right now."""
CLIP_PASS = (timedelta(seconds=-5), timedelta(minutes=4))
"""A clip is linked only when it was written in the same pass as the reading: the sampler writes it right
after the reading's 16-second capture, and a pass starts every 3 minutes (4 allows for the 30-second
reply cache). A clip left over from an earlier pass, because this pass's encode failed, would show other
people than the count next to it."""
CLIP_MAX_AGE = 60
"""Seconds a browser or Cloudflare may keep a clip. The ?v= in the reading's URL changes with each new one."""
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
MAX_LOCATION_ID = 2**31 - 1


class HallBusy(BaseModel):
    location_id: int
    name: str
    level: Literal["quiet", "moderate", "busy"]
    people: int = Field(description="People in view of the hall's cam, the median over 16 seconds.")
    trend: Literal["rising", "falling", "steady"] | None = None
    updated_at: datetime
    live_url: str | None = Field(default=None, description="The hall's live cam on YouTube.")
    clip_url: str | None = Field(
        default=None, description="The last 16 seconds of the cam with a green box on each person counted (MP4)."
    )
    poster_url: str | None = Field(default=None, description="The clip's last frame (JPEG).")
    clip_at: datetime | None = Field(default=None, description="When the clip was written.")


class BusyReply(BaseModel):
    halls: list[HallBusy] = Field(description="Halls with a recent reading, in location id order.")


class OccupancySource(Protocol):
    def latest(self, since: datetime) -> Iterable[Mapping[str, Any]]:
        """The newest reading with a level per hall from its main camera, taken at `since` or later."""
        ...


class MongoOccupancySource:
    def __init__(self, uri: str, database: str) -> None:
        self._uri = uri
        self._database = database
        self._client: MongoClient | None = None

    def latest(self, since: datetime) -> Iterable[Mapping[str, Any]]:
        if self._client is None:  # connect on first use, so start-up never waits on it
            self._client = MongoClient(self._uri, serverSelectionTimeoutMS=5_000, appname="mealscholar-busy")
        # Only readings with a level: a pass that couldn't read a cam (unknown) leaves the hall's
        # reading from a few minutes before on show, instead of hiding the hall until the next pass.
        return self._client[self._database]["occupancy"].aggregate(
            [
                {"$match": {"primary": True, "ts": {"$gte": since}, "level": {"$in": list(LEVELS)}}},
                {"$sort": {"ts": -1}},
                {"$group": {"_id": "$location_id", "doc": {"$first": "$$ROOT"}}},
            ]
        )


class Busy:
    def __init__(
        self,
        source: OccupancySource,
        *,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self.clock = clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cached: tuple[float, BusyReply] | None = None

    def now(self) -> BusyReply:
        with self._lock:
            if self._cached is not None and self._monotonic() - self._cached[0] < CACHE_SECONDS:
                return self._cached[1]
            reply = BusyReply(halls=sorted(self._read(), key=lambda h: h.location_id))
            self._cached = (self._monotonic(), reply)
            return reply

    def _read(self) -> list[HallBusy]:
        halls = []
        for row in self._source.latest(self.clock() - FRESH_FOR):
            doc = row.get("doc", row)
            if doc.get("level") not in LEVELS or doc.get("location_id") is None:
                continue  # the cam could not be read (unknown)
            trend = doc.get("trend")
            youtube_id = doc.get("youtube_id")
            taken = doc["ts"]
            if taken.tzinfo is None:  # pymongo hands back naive UTC
                taken = taken.replace(tzinfo=UTC)
            halls.append(
                HallBusy(
                    location_id=int(doc["location_id"]),
                    name=str(doc.get("location_name") or doc.get("camera") or ""),
                    level=doc["level"],
                    people=int(doc.get("people") or 0),
                    trend=trend if trend in ("rising", "falling", "steady") else None,
                    updated_at=taken,
                    live_url=f"https://www.youtube.com/watch?v={youtube_id}"
                    if isinstance(youtube_id, str) and YOUTUBE_ID.fullmatch(youtube_id)
                    else None,
                )
            )
        return halls


def with_clips(reply: BusyReply, cams_dir: Path | None, now: datetime) -> BusyReply:
    """Link each hall's boxed clip and poster when the sampler wrote them in the last 15 minutes, in the
    same pass as the hall's reading."""
    if cams_dir is None:
        return reply
    halls = []
    for hall in reply.halls:
        update: dict[str, Any] = {}
        clip = _fresh(cams_dir / f"{hall.location_id}.mp4", now)
        if clip is not None and not _same_pass(clip, hall.updated_at):
            clip = None
        if clip is not None:
            base = f"/api/dining/cams/{hall.location_id}"
            update = {"clip_url": f"{base}/clip.mp4?v={clip}", "clip_at": datetime.fromtimestamp(clip, UTC)}
            poster = _fresh(cams_dir / f"{hall.location_id}.jpg", now)
            if poster is not None and _same_pass(poster, hall.updated_at):
                update["poster_url"] = f"{base}/poster.jpg?v={poster}"
        halls.append(hall.model_copy(update=update) if update else hall)
    return BusyReply(halls=halls)


def _fresh(path: Path, now: datetime) -> int | None:
    """The file's mtime in whole seconds when it exists and is recent, else None."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return int(mtime) if now - datetime.fromtimestamp(mtime, UTC) < CLIP_FRESH_FOR else None


def _same_pass(mtime: int, reading_at: datetime) -> bool:
    written = datetime.fromtimestamp(mtime, UTC) - reading_at
    return CLIP_PASS[0] <= written < CLIP_PASS[1]


router = APIRouter(prefix="/api/dining", tags=["dining"])


@router.get("/busy")
def busy_now(request: Request) -> BusyReply:
    """How busy each hall is, from its live cam: the latest reading per hall from the last 15
    minutes. Halls without one are left out."""
    busy: Busy | None = request.app.state.busy
    if busy is None:
        raise HTTPException(503, "Hall cams aren't connected on this server.")
    try:
        reply = busy.now()
    except PyMongoError as exc:
        log.warning("Couldn't read the hall cam readings: %s", type(exc).__name__)
        raise HTTPException(503, "Couldn't read the hall cams right now.") from None
    return with_clips(reply, request.app.state.cams_dir, busy.clock())


LocationId = Annotated[int, PathParam(ge=1, le=MAX_LOCATION_ID)]


def _cam_file(request: Request, location_id: int, suffix: str, media_type: str) -> FileResponse:
    cams_dir: Path | None = request.app.state.cams_dir
    path = cams_dir / f"{location_id}{suffix}" if cams_dir is not None else None
    if path is None or not path.is_file():
        raise HTTPException(404, "No clip from this hall's cam right now.")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": f"public, max-age={CLIP_MAX_AGE}"})


@router.get("/cams/{location_id}/clip.mp4", response_class=FileResponse)
def cam_clip(request: Request, location_id: LocationId) -> FileResponse:
    """The hall cam's latest 16 seconds with a green box on each person the model counted (H.264 MP4)."""
    return _cam_file(request, location_id, ".mp4", "video/mp4")


@router.get("/cams/{location_id}/poster.jpg", response_class=FileResponse)
def cam_poster(request: Request, location_id: LocationId) -> FileResponse:
    """The last frame of the hall's latest clip (JPEG)."""
    return _cam_file(request, location_id, ".jpg", "image/jpeg")


def open_busy(settings: Settings) -> Busy | None:
    """Readings from MongoDB when MONGODB_URI is set; without it the route answers 503."""
    if not settings.mongodb_uri:
        return None
    return Busy(MongoOccupancySource(settings.mongodb_uri, settings.dining_db))


def install_busy(app: FastAPI, busy: Busy | None, cams_dir: Path | None = None) -> None:
    """`cams_dir`: where the sampler leaves the boxed clips (MEALSCHOLAR_HOME/cams)."""
    app.state.busy = busy
    app.state.cams_dir = cams_dir
    app.include_router(router)
