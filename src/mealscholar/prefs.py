"""Onboarding preferences: goals, dining halls, diet, allergens, favorite dishes and alerts.

One document per user in the user_prefs collection of the accounts database, or in memory without
MONGODB_URI. It holds these settings only; the account itself stays in auth_store.py.

`install_prefs` adds GET and PATCH /api/me/prefs. When sign-in is optional
(MEALSCHOLAR_AUTH_REQUIRED=0) and nobody is signed in, they use the single local user's preferences.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import unicodedata
from collections.abc import Callable, Collection, Mapping
from datetime import datetime
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import PyMongoError

from .auth import BODY_TOO_LARGE, AuthError, DiaryOwner
from .auth_store import AuthStore, MongoAuthStore, utc_now
from .nutrients import BY_KEY

LOCAL_USER = "local"
"""Whose preferences the web app uses when sign-in is optional and nobody is signed in."""

DIETS = ("Halal", "Local", "Plant Based", "Vegetarian")
ALLERGENS = ("Corn", "Milk", "Soy", "Gluten", "Wheat", "Eggs", "Sesame", "Fish", "Tree Nuts", "Shellfish", "Peanuts")
"""The diet and allergen labels UMass Dining puts on menu items."""

MIN_CALORIE_GOAL, MAX_CALORIE_GOAL = 500, 6000
GOAL_MAX: dict[str, int] = {
    "protein_g": 400,
    "carbs_g": 1000,
    "fat_g": 400,
    "fiber_g": 150,
    "sugars_g": 400,
    "sodium_mg": 10_000,
}
"""The highest daily goal for each nutrient. The lowest is 0, and null means no goal."""
MAX_HALLS, MAX_FAVORITES = 10, 50
MIN_FAVORITE, MAX_FAVORITE = 2, 120
MAX_LOCATION_ID = 2**31 - 1
MAX_PREFS_BODY = 32 * 1024
"""Bytes. Fifty favorites fit many times over; bigger bodies are refused before they are parsed."""

NOT_AN_OBJECT = "Send the preferences as a JSON object."
_PLAIN_NAME = re.compile(r"[A-Za-z0-9_]{1,40}")
_PUNCTUATION = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"})


def dish_key(name: str) -> str:
    """A dish name without case, width, extra spaces or curly quotes, to match favorites to menus."""
    return " ".join(unicodedata.normalize("NFKC", name).translate(_PUNCTUATION).casefold().split())


# --- the preferences ----------------------------------------------------------


class NutrientGoals(BaseModel):
    """Daily goals. The defaults are the FDA Daily Values; sugars have none. None: no goal."""

    protein_g: float | None = BY_KEY["protein_g"].daily_value
    carbs_g: float | None = BY_KEY["carbs_g"].daily_value
    fat_g: float | None = BY_KEY["fat_g"].daily_value
    fiber_g: float | None = BY_KEY["fiber_g"].daily_value
    sugars_g: float | None = BY_KEY["sugars_g"].daily_value
    sodium_mg: float | None = BY_KEY["sodium_mg"].daily_value


class Favorite(BaseModel):
    name: str

    @property
    def key(self) -> str:
        """dish_key(name). It is stored next to the name too, but not sent to the client."""
        return dish_key(self.name)


class Notify(BaseModel):
    in_app: bool = True
    """Show favorites that are on the menu in the app (GET /api/me/alerts)."""
    email: bool = False
    """Email them (dining.send_favorites_digests)."""


class Prefs(BaseModel):
    onboarded: bool = False
    calorie_goal: int | None = None
    nutrient_goals: NutrientGoals = Field(default_factory=NutrientGoals)
    dining_halls: list[int] = Field(default_factory=list)
    """Dining location ids, the user's main one first."""
    diets: list[str] = Field(default_factory=list)
    avoid_allergens: list[str] = Field(default_factory=list)
    favorites: list[Favorite] = Field(default_factory=list)
    notify: Notify = Field(default_factory=Notify)
    updated_at: datetime | None = None
    """The last change; None until the user saves anything."""


# --- checking a PATCH body ----------------------------------------------------


def validate_changes(body: Any, known_halls: Callable[[], Collection[int]] | None = None) -> dict[str, Any]:
    """The MongoDB $set for a PATCH body: whole fields, and dotted paths for the members of
    nutrient_goals and notify, which merge into the saved ones. Raises AuthError (400, naming the
    field) at the first thing that is wrong. `known_halls` gives the location ids that exist."""
    if not isinstance(body, dict):
        raise AuthError(400, NOT_AN_OBJECT)
    changes: dict[str, Any] = {}
    for field, value in body.items():
        match field:
            case "onboarded":
                changes[field] = _flag(value, field)
            case "calorie_goal":
                changes[field] = _calorie_goal(value)
            case "nutrient_goals":
                changes.update(_nutrient_goals(value))
            case "dining_halls":
                changes[field] = _dining_halls(value, known_halls)
            case "diets":
                changes[field] = _choices(value, field, DIETS, "diets")
            case "avoid_allergens":
                changes[field] = _choices(value, field, ALLERGENS, "allergens")
            case "favorites":
                changes[field] = _favorites(value)
            case "notify":
                changes.update(_notify(value))
            case "updated_at":
                pass  # set by the server; accepted so a client can send back what it read
            case _:
                raise _unknown(field)
    return changes


def _bad(detail: str, field: str | None) -> AuthError:
    return AuthError(400, detail, field=field)


def _unknown(name: str, parent: str | None = None) -> AuthError:
    """The client chose the name, so it is repeated back only when it is plain."""
    path = f"{parent}.{name}" if parent else name
    if _PLAIN_NAME.fullmatch(name):
        return _bad(f"Unknown field: {path}.", path)
    return _bad("Unknown field.", parent)


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)  # JSON true is not a number


def _flag(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise _bad(f"Set {field} to true or false.", field)
    return value


def _calorie_goal(value: Any) -> int | None:
    if value is not None and not (_whole(value) and MIN_CALORIE_GOAL <= value <= MAX_CALORIE_GOAL):
        raise _bad(f"Choose a calorie goal from {MIN_CALORIE_GOAL} to {MAX_CALORIE_GOAL:,}, or none.", "calorie_goal")
    return value


def _nutrient_goals(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict):
        raise _bad("Send nutrient_goals as an object.", "nutrient_goals")
    changes: dict[str, float | None] = {}
    for key, goal in value.items():
        if key not in GOAL_MAX:
            raise _unknown(key, "nutrient_goals")
        field = f"nutrient_goals.{key}"
        if goal is not None:
            # NaN and infinity fail the range check too
            if isinstance(goal, bool) or not isinstance(goal, int | float) or not 0 <= goal <= GOAL_MAX[key]:
                nutrient = BY_KEY[key]
                raise _bad(
                    f"Choose a {nutrient.label.lower()} goal from 0 to {GOAL_MAX[key]:,} {nutrient.unit}, or none.",
                    field,
                )
            goal = round(float(goal), 1)
        changes[field] = goal
    return changes


def _dining_halls(value: Any, known_halls: Callable[[], Collection[int]] | None) -> list[int]:
    if not isinstance(value, list) or not all(_whole(v) and 0 < v <= MAX_LOCATION_ID for v in value):
        raise _bad("Send dining_halls as a list of location ids.", "dining_halls")
    halls = list(dict.fromkeys(value))
    if len(halls) > MAX_HALLS:
        raise _bad(f"Choose up to {MAX_HALLS} dining locations.", "dining_halls")
    if known_halls is not None:
        known = known_halls()
        if unknown := [hall for hall in halls if hall not in known]:
            raise _bad(f"Unknown dining location: {unknown[0]}.", "dining_halls")
    return halls


def _choices(value: Any, field: str, vocabulary: tuple[str, ...], noun: str) -> list[str]:
    """A subset of `vocabulary`, matched without case, in the vocabulary's order."""
    names = {name.casefold(): name for name in vocabulary}
    if not isinstance(value, list) or not all(isinstance(v, str) and _folded(v) in names for v in value):
        choices = ", ".join(vocabulary[:-1]) + f" and {vocabulary[-1]}"
        raise _bad(f"Choose {noun} from {choices}.", field)
    chosen = {names[_folded(v)] for v in value}
    return [name for name in vocabulary if name in chosen]


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def _favorites(value: Any) -> list[dict[str, str]]:
    """[{name, key}], without repeats of the same key; the first spelling wins."""
    if not isinstance(value, list) or not all(
        isinstance(v, dict) and set(v) == {"name"} and isinstance(v["name"], str) for v in value
    ):
        raise _bad('Send favorites as a list of {"name": ...} objects.', "favorites")
    favorites: dict[str, dict[str, str]] = {}
    for favorite in value:
        name = _favorite_name(favorite["name"])
        key = dish_key(name)
        favorites.setdefault(key, {"name": name, "key": key})
    if len(favorites) > MAX_FAVORITES:
        raise _bad(f"Keep your favorites to {MAX_FAVORITES} or fewer.", "favorites")
    return list(favorites.values())


def _favorite_name(text: str) -> str:
    if len(text) <= 4 * MAX_FAVORITE:  # far longer text is refused below without being normalised
        text = unicodedata.normalize("NFC", " ".join(text.split()))
    if not MIN_FAVORITE <= len(text) <= MAX_FAVORITE:
        raise _bad(f"Keep each favorite between {MIN_FAVORITE} and {MAX_FAVORITE} characters.", "favorites")
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in text):
        raise _bad("A favorite has characters that can't be used.", "favorites")
    return text


def _notify(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise _bad("Send notify as an object.", "notify")
    changes: dict[str, bool] = {}
    for key, flag in value.items():
        if key not in Notify.model_fields:
            raise _unknown(key, "notify")
        changes[f"notify.{key}"] = _flag(flag, f"notify.{key}")
    return changes


# --- storage ------------------------------------------------------------------


class PrefsStore(Protocol):
    def ensure_indexes(self) -> None: ...

    def get(self, user_id: str) -> dict[str, Any] | None:
        """The saved document without _id, or None if the user never saved any."""
        ...

    def update(self, user_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        """Set fields (dotted paths reach into nutrient_goals and notify), creating the document
        if there is none, and return it as saved."""
        ...

    def with_email_alerts(self) -> list[tuple[str, dict[str, Any]]]:
        """(user id, document) for every user with notify.email on."""
        ...


def open_prefs_store(users: AuthStore) -> PrefsStore:
    """In the accounts' MongoDB database when they are there, otherwise in memory."""
    if not isinstance(users, MongoAuthStore):
        return MemoryPrefsStore()
    store = MongoPrefsStore(users.db)
    try:
        store.ensure_indexes()
    except PyMongoError as exc:
        raise ConnectionError(f"Can't set up preferences in MongoDB; check MONGODB_URI. ({exc})") from exc
    return store


class MemoryPrefsStore:
    """Same behaviour as MongoPrefsStore, in this process only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: dict[str, dict[str, Any]] = {}

    def ensure_indexes(self) -> None:
        pass

    def get(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._docs.get(user_id))

    def update(self, user_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            doc = self._docs.setdefault(user_id, {})
            for path, value in changes.items():
                *parents, name = path.split(".")
                target = doc
                for parent in parents:
                    target = target.setdefault(parent, {})
                target[name] = copy.deepcopy(value)
            return copy.deepcopy(doc)

    def with_email_alerts(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return [
                (user_id, copy.deepcopy(doc))
                for user_id, doc in self._docs.items()
                if doc.get("notify", {}).get("email") is True
            ]


class MongoPrefsStore:
    """Collection user_prefs, one document per user. _id is the user id as a string: an account's
    ObjectId in hex, or "local"."""

    def __init__(self, db: Database) -> None:
        self._prefs = db["user_prefs"]

    def ensure_indexes(self) -> None:
        """Idempotent; run at startup."""
        self._prefs.create_index("notify.email")

    def get(self, user_id: str) -> dict[str, Any] | None:
        return self._prefs.find_one({"_id": _user_id(user_id)}, {"_id": False})

    def update(self, user_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        return self._prefs.find_one_and_update(
            {"_id": _user_id(user_id)},
            {"$set": dict(changes)},
            projection={"_id": False},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

    def with_email_alerts(self) -> list[tuple[str, dict[str, Any]]]:
        docs = self._prefs.find({"notify.email": True})
        return [(str(doc.pop("_id")), doc) for doc in docs]


def _user_id(value: str) -> str:
    if not isinstance(value, str):  # anything else could be a query operator
        raise TypeError(f"A user id is a string, not {type(value).__name__}.")
    return value


# --- service and HTTP ---------------------------------------------------------


class KnownHalls(Protocol):
    def location_ids(self) -> Collection[int]: ...


class PrefsService:
    def __init__(
        self, store: PrefsStore, halls: KnownHalls | None = None, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        """`halls`: the dining menus, when connected, to check dining_halls against."""
        self.store = store
        self._halls = halls
        self._clock = clock

    def get(self, user_id: str) -> Prefs:
        return Prefs.model_validate(self.store.get(user_id) or {})

    def update(self, user_id: str, body: Any) -> Prefs:
        """Apply a PATCH body. If any part of it is wrong, raises AuthError and saves nothing."""
        changes = validate_changes(body, self._halls.location_ids if self._halls is not None else None)
        if not changes:
            return self.get(user_id)
        return Prefs.model_validate(self.store.update(user_id, {**changes, "updated_at": self._clock()}))


def no_store(response: Response) -> None:
    """/api/me answers are personal: no browser or proxy cache keeps them."""
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/me", tags=["me"], dependencies=[Depends(no_store)])


def prefs_owner(owner: DiaryOwner) -> str:
    """The signed-in user's id, or LOCAL_USER when sign-in is optional and nobody is signed in."""
    return owner or LOCAL_USER


PrefsOwner = Annotated[str, Depends(prefs_owner)]


def _service(request: Request) -> PrefsService:
    return request.app.state.prefs


Service = Annotated[PrefsService, Depends(_service)]


def my_prefs(owner: PrefsOwner, prefs: Service) -> Prefs:
    return prefs.get(owner)


MyPrefs = Annotated[Prefs, Depends(my_prefs)]


@router.get("/prefs")
def read_prefs(prefs: MyPrefs) -> Prefs:
    """The saved preferences, with defaults for anything not set yet (then updated_at is null)."""
    return prefs


_ANY_PREFS = {"$ref": "#/components/schemas/Prefs"}  # for /api/docs: every field is optional


@router.patch("/prefs", openapi_extra={"requestBody": {"content": {"application/json": {"schema": _ANY_PREFS}}}})
async def update_prefs(request: Request, owner: PrefsOwner, prefs: Service) -> Prefs:
    """Body: any subset of the fields. nutrient_goals and notify merge into the saved ones; the
    lists replace theirs. Anything invalid is a 400 naming the field, and nothing is saved."""
    body = await _json_body(request)
    return await run_in_threadpool(prefs.update, owner, body)


async def _json_body(request: Request) -> Any:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_PREFS_BODY:
        raise AuthError(413, BODY_TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_PREFS_BODY:
            raise AuthError(413, BODY_TOO_LARGE)
    try:
        return json.loads(body)
    except (ValueError, RecursionError):  # not JSON, not UTF-8, or nested absurdly deep
        raise AuthError(400, NOT_AN_OBJECT) from None


def install_prefs(app: FastAPI, store: PrefsStore, halls: KnownHalls | None = None) -> None:
    """Add GET and PATCH /api/me/prefs. Errors use the auth error format, so install_auth first."""
    app.state.prefs = PrefsService(store, halls)
    app.include_router(router)
