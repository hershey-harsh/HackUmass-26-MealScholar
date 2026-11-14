"""How busy each dining hall is right now, from the hall cams.

The cams sampler (cams/, launchd org.mannava.umass-cams.sample) watches each hall's live cam
every 3 minutes and writes one reading per camera to umass_dining.occupancy: the people in
view (the median over 20 seconds) and a level, quiet, moderate or busy. GET /api/dining/busy
answers with the latest reading per hall from the main camera, if it is recent. Read-only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Request
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


class HallBusy(BaseModel):
    location_id: int
    name: str
    level: Literal["quiet", "moderate", "busy"]
    people: int = Field(description="People in view of the hall's cam, the median over 20 seconds.")
    trend: Literal["rising", "falling", "steady"] | None = None
    updated_at: datetime


class BusyReply(BaseModel):
    halls: list[HallBusy] = Field(description="Halls with a recent reading, in location id order.")


class OccupancySource(Protocol):
    def latest(self, since: datetime) -> Iterable[Mapping[str, Any]]:
        """The newest reading per hall from its main camera, taken at `since` or later."""
        ...


class MongoOccupancySource:
    def __init__(self, uri: str, database: str) -> None:
        self._uri = uri
        self._database = database
        self._client: MongoClient | None = None

    def latest(self, since: datetime) -> Iterable[Mapping[str, Any]]:
        if self._client is None:  # connect on first use, so start-up never waits on it
            self._client = MongoClient(self._uri, serverSelectionTimeoutMS=5_000, appname="mealscholar-busy")
        return self._client[self._database]["occupancy"].aggregate(
            [
                {"$match": {"primary": True, "ts": {"$gte": since}}},
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
        self._clock = clock
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
        for row in self._source.latest(self._clock() - FRESH_FOR):
            doc = row.get("doc", row)
            if doc.get("level") not in LEVELS or doc.get("location_id") is None:
                continue  # the cam could not be read (unknown)
            trend = doc.get("trend")
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
                )
            )
        return halls


router = APIRouter(prefix="/api/dining", tags=["dining"])


@router.get("/busy")
def busy_now(request: Request) -> BusyReply:
    """How busy each hall is, from its live cam: the latest reading per hall from the last 15
    minutes. Halls without one are left out."""
    busy: Busy | None = request.app.state.busy
    if busy is None:
        raise HTTPException(503, "Hall cams aren't connected on this server.")
    try:
        return busy.now()
    except PyMongoError as exc:
        log.warning("Couldn't read the hall cam readings: %s", type(exc).__name__)
        raise HTTPException(503, "Couldn't read the hall cams right now.") from None


def open_busy(settings: Settings) -> Busy | None:
    """Readings from MongoDB when MONGODB_URI is set; without it the route answers 503."""
    if not settings.mongodb_uri:
        return None
    return Busy(MongoOccupancySource(settings.mongodb_uri, settings.dining_db))


def install_busy(app: FastAPI, busy: Busy | None) -> None:
    app.state.busy = busy
    app.include_router(router)
