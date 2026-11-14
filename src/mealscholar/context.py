"""Step 2: meal slot from local time and the food place at the photo location.

Raw coordinates stop here: MealContext keeps only the place name, type, and cuisine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import httpx

from .schemas import MealContext, MealSlot, PhotoMetadata, Place

# OpenStreetMap amenity=* and shop=* values that sell prepared food or drink.
FOOD_PLACE_TYPES = frozenset(
    {
        "restaurant",
        "fast_food",
        "cafe",
        "food_court",
        "bar",
        "pub",
        "biergarten",
        "ice_cream",
        "bakery",
        "deli",
        "pastry",
        "confectionery",
    }
)


class PlaceLookupError(RuntimeError):
    pass


class PlaceLookup(Protocol):
    def at(self, latitude: float, longitude: float) -> Place | None: ...


class NominatimPlaces:
    """OpenStreetMap reverse geocoding. Public instance policy: max 1 request/second and an
    identifying User-Agent (https://operations.osmfoundation.org/policies/nominatim/)."""

    def __init__(self, url: str, user_agent: str, timeout: float = 5.0) -> None:
        self._url = url
        self._client = httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout)

    def at(self, latitude: float, longitude: float) -> Place | None:
        try:
            resp = self._client.get(
                self._url,
                params={
                    "format": "jsonv2",
                    "lat": f"{latitude:.6f}",
                    "lon": f"{longitude:.6f}",
                    "zoom": 18,
                    "extratags": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        # The message ends up in the meal's notes, which are stored. httpx puts the request URL,
        # coordinates included, in its error text, so the message is built without it.
        except httpx.HTTPStatusError as exc:
            raise PlaceLookupError(f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise PlaceLookupError("timed out") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise PlaceLookupError(type(exc).__name__) from exc
        return place_from_nominatim(data)


def place_from_nominatim(data: dict) -> Place | None:
    kind = data.get("type")
    if data.get("category") not in ("amenity", "shop") or kind not in FOOD_PLACE_TYPES:
        return None
    tags = data.get("extratags") or {}
    return Place(
        name=data.get("name") or kind.replace("_", " "),
        kind=kind,
        cuisine=tags.get("cuisine"),
        chain=bool(tags.get("brand") or tags.get("brand:wikidata")),
    )


def meal_slot(taken_at: datetime) -> MealSlot:
    hour = taken_at.hour + taken_at.minute / 60
    if 4 <= hour < 10.5:
        return "breakfast"
    if 10.5 <= hour < 15:
        return "lunch"
    if 17 <= hour < 22:
        return "dinner"
    return "snack"


def build_context(meta: PhotoMetadata, places: PlaceLookup | None) -> MealContext:
    notes: list[str] = []
    place: Place | None = None
    if meta.time_source == "upload":
        notes.append("Photo has no capture time; using upload time.")
    if meta.latitude is None or meta.longitude is None:
        notes.append("No location used.")
    elif places is not None:
        try:
            place = places.at(meta.latitude, meta.longitude)
        except PlaceLookupError as exc:
            notes.append(f"Place lookup failed ({exc}); continuing without it.")
    return MealContext(
        taken_at=meta.taken_at,
        time_source=meta.time_source,
        meal_slot=meal_slot(meta.taken_at),
        place=place,
        notes=notes,
    )
