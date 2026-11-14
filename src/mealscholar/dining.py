"""UMass Dining menus, from the umass_dining database that a separate scraper fills. This app only
reads it.

The /api/dining routes are public. The /api/me ones add the user's preferences (prefs.py): the
next meal with their flags on each dish, and their favorites on upcoming menus, which feed the
in-app bell and the favorites email. Dining.dishes_on reads a hall's dishes for a day with their
own nutrition labels (the menu_items collection), for campus.py to match a photo against.

Reads are cached in process for 5 minutes. Queries are built only from ints, ISO dates and an
escaped search pattern; nothing from a request body goes into one.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import groupby
from typing import Annotated, Any, Protocol
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from .auth_store import AuthStore, open_store, utc_now
from .campus import serving_grams
from .config import Settings
from .mailer import Email, EmailSender
from .prefs import ALLERGENS, DIETS, MAX_LOCATION_ID, MyPrefs, Prefs, PrefsStore, dish_key, no_store, open_prefs_store

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
WORCESTER = 1
"""Worcester Dining Commons: the next meal shown to users who haven't chosen a dining hall."""
CACHE_TTL = timedelta(minutes=5)
CACHE_SIZE = 1024
"""Cached reads at most; the least recently used go first."""
MAX_ALERT_DAYS = 14
MAX_ALERTS = 200
DISH_SCAN = 100
"""Dish documents read per search, before dishes with the same name are merged."""

NOT_CONNECTED = "Dining menus aren't connected."
UNAVAILABLE = "Dining menus aren't available right now. Try again in a minute."

MEALS: dict[str, str] = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "late_night": "Late Night",
    "grab_n_go": "Grab 'n Go",
    "daily_offerings": "Daily Offerings",
}
"""Meal keys and their labels, in the order meals are listed."""

# Typical Dining Commons hours, used to pick the next meal. The menus carry no times, and real
# hours change with the hall, the day and breaks, so answers say "usually". A meal is the next
# one until it ends:
#   breakfast    7:00 to 10:30
#   lunch       11:00 to 16:00
#   dinner      16:30 to 21:00
#   late_night  21:00 to 23:59
# Grab 'n Go and daily offerings have no set hours. They count only on days with none of these
# meals (cafés), until the end of the day.
MEAL_HOURS: dict[str, tuple[time, time]] = {
    "breakfast": (time(7), time(10, 30)),
    "lunch": (time(11), time(16)),
    "dinner": (time(16, 30), time(21)),
    "late_night": (time(21), time(23, 59)),
}
ALL_DAY_MEALS = ("grab_n_go", "daily_offerings")
END_OF_DAY = time(23, 59)

_MEAL_RANK = {key: rank for rank, key in enumerate(MEALS)}
_ALLERGENS = {name.casefold(): name for name in ALLERGENS}
_DIETS = {name.casefold(): name for name in DIETS}


# --- what the API returns -----------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)  # cached and shared between requests


class LocationRef(_Frozen):
    id: int
    name: str


class LocationInfo(_Frozen):
    id: int
    name: str
    area: str | None
    is_dining_commons: bool
    has_prices: bool
    next_date: date
    """The first date, today or later, with a menu."""


class Item(_Frozen):
    dish_id: str | None
    name: str
    calories: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    allergens: tuple[str, ...]
    """As labelled by UMass Dining; an item without labels isn't necessarily free of allergens."""
    diets: tuple[str, ...]
    """Only the tags in prefs.DIETS; other codes in the data are left out."""
    price: float | None
    serving_size: str | None


class Category(_Frozen):
    category: str
    items: tuple[Item, ...]


class Meal(_Frozen):
    meal: str
    label: str
    categories: tuple[Category, ...]


class Menu(_Frozen):
    location: LocationRef
    date: dt.date
    meals: tuple[Meal, ...]
    """In MEALS order; meals and categories without items are left out."""


class NextItem(Item):
    category: str


class NextMeal(_Frozen):
    location: LocationRef
    date: dt.date
    meal: str
    label: str
    starts_hint: str
    """Typical hours, such as "Dinner, usually 4:30 to 9 pm"; real hours vary."""
    items: tuple[NextItem, ...]


class FlaggedItem(NextItem):
    favorite: bool
    avoid: bool
    """Labelled with an allergen the user avoids."""
    fits_diet: bool
    """Tagged with every diet the user chose (always true when they chose none)."""


class MyNextMeal(NextMeal):
    items: tuple[FlaggedItem, ...]


class Dish(_Frozen):
    name: str
    calories: float | None
    locations: tuple[str, ...]
    seen_on: int
    """How many times the dish has been on a menu."""


class Alert(_Frozen):
    name: str
    """The dish as the menu spells it."""
    location_id: int
    location_name: str
    date: dt.date
    meal: str
    label: str


@dataclass(frozen=True, slots=True)
class Location:
    id: int
    name: str
    area: str | None
    is_dining_commons: bool
    has_prices: bool
    dates: tuple[date, ...]
    """Dates with a menu, in order."""


class DishFacts(_Frozen):
    """A dish on a hall's menu with its own nutrition label, for campus.py; not an API answer."""

    dish_id: str | None
    name: str
    meal: str
    """A MEALS key, or whatever the menu says, in lower case."""
    category: str | None
    serving_size: str | None
    """As labelled: '3 OZ', '20 OZL', '1 Slice'."""
    serving_grams: float | None
    """campus.serving_grams(serving_size); None for a counted serving."""
    per_serving: dict[str, float]
    """Nutrient keys (LABEL_KEYS) with the label's value for one serving. A nutrient the label
    lacks is left out, so the meal's totals report it as partial rather than count it as 0."""
    healthfulness_score: int | None
    """UMass Dining's own score, 0 to 7."""


# --- reading the database -----------------------------------------------------


class DiningSource(Protocol):
    """Raw documents from the umass_dining collections."""

    def locations(self) -> Iterable[Mapping[str, Any]]: ...

    def daily_menus(self, ids: Sequence[str], *, names_only: bool = False) -> Iterable[Mapping[str, Any]]:
        """The daily_menus documents with these _ids ("<tid>|<YYYY-MM-DD>"). With names_only,
        items carry only their name."""
        ...

    def menu_items(self, location_id: int, day: str) -> Iterable[Mapping[str, Any]]:
        """The menu_items documents of one location on one day (YYYY-MM-DD), in menu order, with
        their nutrition facts."""
        ...

    def dishes(self, pattern: str, limit: int) -> Iterable[Mapping[str, Any]]:
        """Up to `limit` dishes whose name matches the regular expression `pattern`, ignoring
        case, most often seen first."""
        ...


_ITEM_FIELDS = (
    "dish_id",
    "name",
    "serving_size",
    "price",
    "calories",
    "protein_g",
    "total_carb_g",
    "total_fat_g",
    "allergens",
    "diets",
)
_FACT_FIELDS = ("name", "meal", "category", "dish_id", "serving_size", "calories", "nutrition", "healthfulness_score")


class MongoDiningSource:
    """Reads the locations, daily_menus, menu_items and dishes collections. Never writes."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @classmethod
    def connect(cls, uri: str, database: str) -> MongoDiningSource:
        client = MongoClient(uri, serverSelectionTimeoutMS=10_000, appname="mealscholar")
        return cls(client[database])

    def locations(self) -> Iterable[Mapping[str, Any]]:
        fields = ("name", "area", "is_dining_commons", "has_prices", "dates_available")
        return self._db["locations"].find({}, dict.fromkeys(fields, True))

    def daily_menus(self, ids: Sequence[str], *, names_only: bool = False) -> Iterable[Mapping[str, Any]]:
        fields = ["location_name", "meals.meal", "meals.meal_raw", "meals.categories.category"]
        fields += [f"meals.categories.items.{name}" for name in (("name",) if names_only else _ITEM_FIELDS)]
        return self._db["daily_menus"].find({"_id": {"$in": [str(i) for i in ids]}}, dict.fromkeys(fields, True))

    def menu_items(self, location_id: int, day: str) -> Iterable[Mapping[str, Any]]:
        # One query, on the location_id_1_date_1 index. Never the dishes collection: documents
        # with the same name differ by hall.
        query = {"location_id": int(location_id), "date": str(day)}
        return self._db["menu_items"].find(query, dict.fromkeys(_FACT_FIELDS, True))

    def dishes(self, pattern: str, limit: int) -> Iterable[Mapping[str, Any]]:
        fields = ("name", "calories", "location_ids", "occurrence_count")
        return (
            self._db["dishes"]
            .find({"name": {"$regex": str(pattern), "$options": "i"}}, dict.fromkeys(fields, True))
            .sort([("occurrence_count", -1), ("name", 1)])
            .limit(int(limit))
        )


class DiningUnavailable(ConnectionError):
    """MongoDB didn't answer a menu read."""


class NoMenu(LookupError):
    """No such location, or nothing on its upcoming menus."""


_MISSING = object()


class _Cache:
    """Values by key until `ttl` has passed; past `size` entries the least recently used go."""

    def __init__(self, ttl: timedelta, size: int, clock: Callable[[], datetime]) -> None:
        self._ttl = ttl
        self._size = size
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, tuple[datetime, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Any:
        """The value, or _MISSING."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _MISSING
            if entry[0] <= self._clock():
                del self._entries[key]
                return _MISSING
            self._entries.move_to_end(key)
            return entry[1]

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._entries[key] = (self._clock() + self._ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._size:
                self._entries.popitem(last=False)


class Dining:
    """Menus through a 5-minute cache, the next meal by typical hours, dish search, favorite alerts
    and a day's dishes with their labels."""

    def __init__(self, source: DiningSource, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.source = source
        self._clock = clock
        self._cache = _Cache(CACHE_TTL, CACHE_SIZE, clock)

    def now(self) -> datetime:
        """The time on campus."""
        return self._clock().astimezone(EASTERN)

    def locations(self) -> tuple[Location, ...]:
        """Every location: dining commons first, then by name."""
        return self._cached(("locations",), lambda: _locations(self.source.locations()))

    def location_ids(self) -> frozenset[int]:
        return frozenset(location.id for location in self.locations())

    def location_name(self, location_id: int) -> str | None:
        return next((loc.name for loc in self.locations() if loc.id == location_id), None)

    def open_locations(self) -> list[LocationInfo]:
        """Locations with a menu today or later."""
        today = self.now().date()
        return [
            LocationInfo(
                id=location.id,
                name=location.name,
                area=location.area,
                is_dining_commons=location.is_dining_commons,
                has_prices=location.has_prices,
                next_date=upcoming[0],
            )
            for location in self.locations()
            if (upcoming := [day for day in location.dates if day >= today])
        ]

    def menu(self, location_id: int, day: date) -> Menu | None:
        return self._menus([(location_id, day)])[location_id, day]

    def dishes_on(self, location_id: int, day: date) -> tuple[DishFacts, ...]:
        """The location's dishes on `day` with their nutrition labels, in menu order, once per meal
        and dish name; a dish whose label can't be right is left out (_dish_facts). Empty when
        there is no menu."""
        key = ("facts", int(location_id), day)
        return self._cached(key, lambda: _dish_facts_on(self.source.menu_items(int(location_id), day.isoformat())))

    def next_meal(self, location_id: int) -> NextMeal:
        """The meal on now, or the next one, at the location, by MEAL_HOURS. Meals that aren't on
        a day's menu are skipped. Raises NoMenu when there is nothing ahead."""
        location = next((loc for loc in self.locations() if loc.id == location_id), None)
        if location is None:
            raise NoMenu(f"There's no dining location {location_id}.")
        now = self.now()
        for day in location.dates:
            if day < now.date():
                continue
            menu = self.menu(location.id, day)
            meal = _current_meal(menu, now.time() if day == now.date() else None) if menu else None
            if menu and meal:
                return _next_meal(menu, meal)
        raise NoMenu(f"No upcoming menus at {location.name}.")

    def dishes(self, query: str, limit: int) -> list[Dish]:
        """Dishes with `query` anywhere in the name, ignoring case; dishes with the same name are
        merged, most often seen first."""
        text = " ".join(query.split())
        if len(text) < 2:
            return []
        return list(self._cached(("dishes", text), lambda: self._search(text))[:limit])

    def alerts(self, prefs: Prefs, days: int) -> list[Alert]:
        """Each favorite on the menus of the user's dining halls (every dining commons if they
        chose none) from today through `days` days ahead: once per dish, location and meal, by
        date and meal. At most MAX_ALERTS."""
        if not 1 <= days <= MAX_ALERT_DAYS:
            raise ValueError(f"days must be from 1 to {MAX_ALERT_DAYS}.")
        favorites = {favorite.key for favorite in prefs.favorites}
        if not favorites:
            return []
        today = self.now().date()
        halls = prefs.dining_halls or [loc.id for loc in self.locations() if loc.is_dining_commons]
        wanted = [(hall, today + timedelta(days=n)) for hall in halls for n in range(days + 1)]
        found: dict[tuple[str, int, date, str], Alert] = {}
        for menu in self._menus(wanted, names_only=True).values():
            for meal, item in _dishes_on(menu):
                key = dish_key(item.name)
                if key in favorites and (key, menu.location.id, menu.date, meal.meal) not in found:
                    found[key, menu.location.id, menu.date, meal.meal] = Alert(
                        name=item.name,
                        location_id=menu.location.id,
                        location_name=menu.location.name,
                        date=menu.date,
                        meal=meal.meal,
                        label=meal.label,
                    )
        alerts = sorted(
            found.values(), key=lambda a: (a.date, _meal_rank(a.meal), a.location_name.casefold(), a.name.casefold())
        )
        return alerts[:MAX_ALERTS]

    # helpers

    def _menus(
        self, wanted: Iterable[tuple[int, date]], *, names_only: bool = False
    ) -> dict[tuple[int, date], Menu | None]:
        """Menus by (location id, date), None where there is none. What the cache lacks is read in
        one query."""
        kind = "names" if names_only else "menu"
        menus: dict[tuple[int, date], Menu | None] = {}
        for key in dict.fromkeys(wanted):
            menus[key] = self._cache.get((kind, *key))
        missing = {_menu_id(*key): key for key, menu in menus.items() if menu is _MISSING}
        if missing:
            docs = self._read(lambda: list(self.source.daily_menus(list(missing), names_only=names_only)))
            found = {missing[doc["_id"]]: doc for doc in docs if doc.get("_id") in missing}
            for key in missing.values():
                doc = found.get(key)
                menu = self._menu(doc, *key) if doc else None
                self._cache.put((kind, *key), menu)
                menus[key] = menu
        return menus

    def _menu(self, doc: Mapping[str, Any], location_id: int, day: date) -> Menu:
        name = _text(doc.get("location_name")) or self.location_name(location_id) or ""
        return _parse_menu(doc, LocationRef(id=location_id, name=name), day)

    def _search(self, text: str) -> tuple[Dish, ...]:
        text = text.replace("\x00", "")
        if not text.strip():
            return ()
        docs = list(self.source.dishes(re.escape(text), DISH_SCAN))  # escaped: matched as plain text
        locations = self.locations()
        names = {loc.id: loc.name for loc in locations}
        rank = {loc.id: n for n, loc in enumerate(locations)}
        first: dict[str, tuple[str, float | None]] = {}  # the most often seen spelling and its calories
        places: dict[str, set[int]] = defaultdict(set)
        seen: dict[str, int] = defaultdict(int)
        for doc in docs:
            name = _text(doc.get("name"))
            if name is None:
                continue
            key = dish_key(name)
            first.setdefault(key, (name, _number(doc.get("calories"))))
            places[key].update(i for i in _list(doc.get("location_ids")) if _whole(i) and i in names)
            seen[key] += _count(doc.get("occurrence_count"))
        dishes = [
            Dish(
                name=name,
                calories=calories,
                locations=tuple(names[i] for i in sorted(places[key], key=rank.__getitem__)),
                seen_on=seen[key],
            )
            for key, (name, calories) in first.items()
        ]
        return tuple(sorted(dishes, key=lambda d: (-d.seen_on, d.name.casefold())))

    def _cached[T](self, key: Hashable, load: Callable[[], T]) -> T:
        found = self._cache.get(key)
        if found is _MISSING:
            found = self._read(load)
            self._cache.put(key, found)
        return found

    def _read[T](self, read: Callable[[], T]) -> T:
        try:
            return read()
        except PyMongoError as exc:
            log.warning("Couldn't read the dining menus: %s", exc)
            raise DiningUnavailable(UNAVAILABLE) from exc


def _menu_id(location_id: int, day: date) -> str:
    return f"{int(location_id)}|{day.isoformat()}"


def _current_meal(menu: Menu, now: time | None) -> Meal | None:
    """The meal on now or next on this day's menu; `now` is None for a day still ahead."""
    meals = {meal.meal: meal for meal in menu.meals}
    if timed := [key for key in MEAL_HOURS if key in meals]:
        return next((meals[key] for key in timed if now is None or now < MEAL_HOURS[key][1]), None)
    if now is None or now < END_OF_DAY:
        return next((meals[key] for key in ALL_DAY_MEALS if key in meals), None)
    return None


def _next_meal(menu: Menu, meal: Meal) -> NextMeal:
    return NextMeal(
        location=menu.location,
        date=menu.date,
        meal=meal.meal,
        label=meal.label,
        starts_hint=starts_hint(meal.meal, meal.label),
        items=tuple(
            NextItem(**dict(item), category=category.category)
            for category in meal.categories
            for item in category.items
        ),
    )


def starts_hint(meal: str, label: str) -> str:
    """Typical hours, such as "Dinner, usually 4:30 to 9 pm", since the menus have none."""
    if meal not in MEAL_HOURS:
        return f"{label}, hours vary by location"
    start, end = MEAL_HOURS[meal]
    if end >= END_OF_DAY:
        return f"{label}, usually {_clock(start)} {_half(start)} to midnight"
    if _half(start) == _half(end):
        return f"{label}, usually {_clock(start)} to {_clock(end)} {_half(end)}"
    return f"{label}, usually {_clock(start)} {_half(start)} to {_clock(end)} {_half(end)}"


def _clock(t: time) -> str:
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d}" if t.minute else str(hour)


def _half(t: time) -> str:
    return "am" if t.hour < 12 else "pm"


def flag_items(meal: NextMeal, prefs: Prefs) -> MyNextMeal:
    """The meal with the user's favorite, avoid and fits_diet flags on each item."""
    favorites = {favorite.key for favorite in prefs.favorites}
    avoid, diets = set(prefs.avoid_allergens), set(prefs.diets)
    items = tuple(
        FlaggedItem(
            **dict(item),
            favorite=dish_key(item.name) in favorites,
            avoid=not avoid.isdisjoint(item.allergens),
            fits_diet=diets.issubset(item.diets),
        )
        for item in meal.items
    )
    return MyNextMeal(**{**dict(meal), "items": items})


def _dishes_on(menu: Menu | None) -> Iterable[tuple[Meal, Item]]:
    if menu is None:
        return
    for meal in menu.meals:
        for category in meal.categories:
            for item in category.items:
                yield meal, item


def _meal_rank(meal: str) -> int:
    return _MEAL_RANK.get(meal, len(_MEAL_RANK))


# --- parsing documents (written by the scraper, so every field is checked) ----


def _locations(docs: Iterable[Mapping[str, Any]]) -> tuple[Location, ...]:
    locations = []
    for doc in docs:
        tid, name = doc.get("_id"), _text(doc.get("name"))
        if not _whole(tid) or name is None:
            continue
        dates = sorted({day for text in _list(doc.get("dates_available")) if (day := _date(text))})
        locations.append(
            Location(
                id=tid,
                name=name,
                area=_text(doc.get("area")),
                is_dining_commons=doc.get("is_dining_commons") is True,
                has_prices=doc.get("has_prices") is True,
                dates=tuple(dates),
            )
        )
    return tuple(sorted(locations, key=lambda loc: (not loc.is_dining_commons, loc.name.casefold(), loc.id)))


def _parse_menu(doc: Mapping[str, Any], location: LocationRef, day: date) -> Menu:
    meals = []
    for meal in _docs(doc.get("meals")):
        key = (_text(meal.get("meal")) or "").lower()
        categories = []
        for category in _docs(meal.get("categories")):
            items = tuple(item for raw in _docs(category.get("items")) if (item := _item(raw)))
            if items:
                categories.append(Category(category=_text(category.get("category")) or "Other", items=items))
        if key and categories:
            label = MEALS.get(key) or _text(meal.get("meal_raw")) or key.replace("_", " ").title()
            meals.append(Meal(meal=key, label=label, categories=tuple(categories)))
    meals.sort(key=lambda meal: _meal_rank(meal.meal))
    return Menu(location=location, date=day, meals=tuple(meals))


def _item(doc: Mapping[str, Any]) -> Item | None:
    name = _text(doc.get("name"))
    if name is None:
        return None
    allergens = (_ALLERGENS.get(text.casefold(), text) for text in _texts(doc.get("allergens")))
    diets = (_DIETS[text.casefold()] for text in _texts(doc.get("diets")) if text.casefold() in _DIETS)
    return Item(
        dish_id=_text(doc.get("dish_id")),
        name=name,
        calories=_number(doc.get("calories")),
        protein_g=_number(doc.get("protein_g")),
        carbs_g=_number(doc.get("total_carb_g")),
        fat_g=_number(doc.get("total_fat_g")),
        allergens=tuple(dict.fromkeys(allergens)),
        diets=tuple(dict.fromkeys(diets)),
        price=_number(doc.get("price")),
        serving_size=_text(doc.get("serving_size")),
    )


LABEL_KEYS: dict[str, tuple[str, ...]] = {
    "energy_kcal": ("calories",),
    "protein_g": ("nutrition", "protein_g"),
    "carbs_g": ("nutrition", "total_carb_g"),
    "fat_g": ("nutrition", "total_fat_g"),
    "sat_fat_g": ("nutrition", "sat_fat_g"),
    "fiber_g": ("nutrition", "dietary_fiber_g"),
    "sugars_g": ("nutrition", "sugars_g"),
    "sodium_mg": ("nutrition", "sodium_mg"),
    "cholesterol_mg": ("nutrition", "cholesterol_mg"),
}
"""Nutrient keys (nutrients.py) and the field of a menu_items document that holds each, per serving."""
_MACROS = ("protein_g", "carbs_g", "fat_g")
MAX_HEALTHFULNESS = 7


def _dish_facts_on(docs: Iterable[Mapping[str, Any]]) -> tuple[DishFacts, ...]:
    """In document order, which is menu order. A dish listed twice at one meal (two categories)
    counts once, the first time."""
    found: dict[tuple[str, str], DishFacts] = {}
    for doc in docs:
        if (dish := _dish_facts(doc)) is not None:
            found.setdefault((dish.meal, dish_key(dish.name)), dish)
    return tuple(found.values())


def _dish_facts(doc: Mapping[str, Any]) -> DishFacts | None:
    """A menu_items document as a dish with its label, or None when it has no name or meal, or a
    label that can't be right: calories missing ('Fountain Drink'), or 0 next to protein, carbs or
    fat that aren't ('20 oz Latte'). A 0-kcal label with all three at 0 is kept (water, black
    coffee); whether to trust it is decided per plate by campus.label_fits. A nutrient that is
    missing, unreadable or negative is left out of per_serving rather than read as 0."""
    name = _text(doc.get("name"))
    meal = (_text(doc.get("meal")) or "").lower()
    if name is None or not meal:
        return None
    per_serving = {}
    for key, path in LABEL_KEYS.items():
        value = _number(_lookup(doc, path))
        if value is not None and value >= 0:
            per_serving[key] = value
    kcal = per_serving.get("energy_kcal")
    if kcal is None or (kcal == 0 and any(per_serving.get(key) != 0 for key in _MACROS)):
        return None
    serving_size = _text(doc.get("serving_size"))
    score = doc.get("healthfulness_score")
    return DishFacts(
        dish_id=_text(doc.get("dish_id")),
        name=name,
        meal=meal,
        category=_text(doc.get("category")),
        serving_size=serving_size,
        serving_grams=serving_grams(serving_size),
        per_serving=per_serving,
        healthfulness_score=score if _whole(score) and 0 <= score <= MAX_HEALTHFULNESS else None,
    )


def _lookup(doc: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = doc
    for name in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(name)
    return value


def _docs(value: Any) -> list[Mapping[str, Any]]:
    return [v for v in _list(value) if isinstance(v, Mapping)]


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _text(value: Any) -> str | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _texts(value: Any) -> list[str]:
    """A list of labels, or one comma-separated string of them."""
    if isinstance(value, str):
        value = value.split(",")
    return [text for v in _list(value) if (text := _text(v))]


def _number(value: Any) -> float | None:
    if isinstance(value, str):  # a price may be text such as "$4.99"
        try:
            value = float(value.strip().removeprefix("$"))
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count(value: Any) -> int:
    return value if _whole(value) and value > 0 else 0


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# --- HTTP ---------------------------------------------------------------------


# --- nearest dining hall ---------------------------------------------------------

# Campus coordinates (OpenStreetMap). The database has none; everything in an area shares
# that area's building. Anything else is placed at the Campus Center.
AREA_COORDS: dict[str, tuple[float, float]] = {
    "worcester": (42.3932825, -72.5250891),
    "franklin": (42.3892709, -72.5225274),
    "hampshire": (42.3838047, -72.5305113),
    "berkshire": (42.3819458, -72.5300022),
    "campus-center": (42.3915299, -72.5268798),
}
AREA_ALIASES = {
    "northeast": "worcester",
    "central": "franklin",
    "southwest": "berkshire",
    "campus center": "campus-center",
}
CAMPUS_CENTER = AREA_COORDS["campus-center"]


def _coords(location: Location) -> tuple[float, float]:
    key = (location.area or "").strip().casefold()
    return AREA_COORDS.get(AREA_ALIASES.get(key, key), CAMPUS_CENTER)


NEAR_CAMPUS_KM = 3.0
"""Beyond this, the user isn't on campus, so no hall is "nearest"."""


class NearestHall(_Frozen):
    location: LocationRef
    distance_m: int
    on_campus: bool
    note: str
    """The sentence the app shows, e.g. "Nearest dining hall found: Worcester. Change dining hall if this is wrong." """


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def nearest_hall(locations: Sequence[Location], lat: float, lon: float) -> NearestHall | None:
    """The closest dining commons with a menu, or None when there are none."""
    halls = [loc for loc in locations if loc.is_dining_commons and loc.dates]
    if not halls:
        return None
    here = (lat, lon)
    best = min(halls, key=lambda loc: _distance_m(here, _coords(loc)))
    distance = _distance_m(here, _coords(best))
    on_campus = distance <= NEAR_CAMPUS_KM * 1000
    if on_campus:
        note = f"Nearest dining hall found: {best.name}. Change dining hall if this is wrong."
    else:
        note = f"You don't look to be on campus, so we picked {best.name}. Change dining hall if this is wrong."
    return NearestHall(
        location=LocationRef(id=best.id, name=best.name), distance_m=round(distance), on_campus=on_campus, note=note
    )


router = APIRouter(prefix="/api/dining", tags=["dining"])
me_router = APIRouter(prefix="/api/me", tags=["me"], dependencies=[Depends(no_store)])


def _dining(request: Request) -> Dining:
    dining = request.app.state.dining
    if dining is None:
        raise HTTPException(503, NOT_CONNECTED)
    return dining


def _iso_date(value: Any) -> Any:
    """Only YYYY-MM-DD; a date parameter alone would also take other forms, such as Unix times."""
    if isinstance(value, str) and _date(value) is None:
        raise ValueError("Use a date such as 2026-11-14.")
    return value


DiningMenus = Annotated[Dining, Depends(_dining)]
LocationId = Annotated[int, Query(ge=1, le=MAX_LOCATION_ID)]
OptionalLocationId = Annotated[int | None, Query(ge=1, le=MAX_LOCATION_ID)]
IsoDate = Annotated[date | None, Query(alias="date"), BeforeValidator(_iso_date)]


@router.get("/locations")
def locations(dining: DiningMenus) -> list[LocationInfo]:
    """Locations with a menu today or later: dining commons first, then by name."""
    return dining.open_locations()


@router.get("/menu")
def menu(dining: DiningMenus, location_id: LocationId, day: IsoDate = None) -> Menu:
    """A location's menu on `date` (YYYY-MM-DD; default today on campus)."""
    found = dining.menu(location_id, day or dining.now().date())
    if found is None:
        raise HTTPException(404, "No menu for that location and date.")
    return found


@router.get("/next")
def next_meal(dining: DiningMenus, location_id: LocationId) -> NextMeal:
    """The meal on now, or the next one, at the location, judged by typical hours (starts_hint)."""
    return _next_meal_or_404(dining, location_id)


@router.get("/nearest")
def nearest(
    dining: DiningMenus,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
) -> NearestHall:
    """The nearest dining commons to a device location. Coordinates are used once and never stored."""
    found = nearest_hall(dining.locations(), lat, lon)
    if found is None:
        raise HTTPException(404, "No dining hall has a menu right now.")
    return found


@router.get("/dishes")
def dishes(
    dining: DiningMenus,
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[Dish]:
    """For the favorites picker: dishes with `q` in the name, ignoring case, most often served first."""
    return dining.dishes(q, limit)


@me_router.get("/dining/next")
def my_next_meal(prefs: MyPrefs, dining: DiningMenus, location_id: OptionalLocationId = None) -> MyNextMeal:
    """/api/dining/next with the user's flags on each item. Without location_id: the user's first
    dining hall, or Worcester."""
    if location_id is None:
        location_id = prefs.dining_halls[0] if prefs.dining_halls else WORCESTER
    return flag_items(_next_meal_or_404(dining, location_id), prefs)


@me_router.get("/alerts")
def my_alerts(
    prefs: MyPrefs, dining: DiningMenus, days: Annotated[int, Query(ge=1, le=MAX_ALERT_DAYS)] = 7
) -> list[Alert]:
    """The user's favorites on menus from today through `days` days ahead, for the in-app bell.
    The bell is the client's to hide while notify.in_app is off."""
    return dining.alerts(prefs, days)


def _next_meal_or_404(dining: Dining, location_id: int) -> NextMeal:
    try:
        return dining.next_meal(location_id)
    except NoMenu as exc:
        raise HTTPException(404, str(exc)) from None


def open_dining(settings: Settings) -> Dining | None:
    """The menus in MongoDB when MONGODB_URI is set. Without it there are none, and the dining
    routes answer 503."""
    if not settings.mongodb_uri:
        return None
    return Dining(MongoDiningSource.connect(settings.mongodb_uri, settings.dining_db))


def install_dining(app: FastAPI, dining: Dining | None) -> None:
    """Add the /api/dining routes, and /api/me/dining/next and /api/me/alerts."""
    app.state.dining = dining
    app.include_router(router)
    app.include_router(me_router)
    app.add_exception_handler(DiningUnavailable, _unavailable)


async def _unavailable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": UNAVAILABLE}, status_code=503)


# --- the favorites email ------------------------------------------------------

FAVORITES_SUBJECT = "Your favorites are on the menu"


def favorites_email(to: str, alerts: Sequence[Alert], public_url: str) -> Email:
    """The alerts by day, in plain text. Dish and location names come from the menus, never from
    what the user typed (see mailer.py)."""
    lines = ["Some of your favorite dishes are on the UMass Dining menu:"]
    for day, on_day in groupby(alerts, key=lambda alert: alert.date):
        lines += ["", f"{day:%A, %B} {day.day}"]
        for (label, place), same in groupby(on_day, key=lambda alert: (alert.label, alert.location_name)):
            lines.append(f"  {label} at {place}: {', '.join(alert.name for alert in same)}")
    lines += [
        "",
        f"See the menus: {public_url}/",
        "",
        "You get this email because email alerts are on in your MealScholar settings. "
        "Turn them off there to stop these emails.",
    ]
    return Email(to=to, subject=FAVORITES_SUBJECT, text="\n".join(lines) + "\n", purpose="favorites_digest")


@dataclass(frozen=True, slots=True)
class DigestReport:
    users: int
    """With email alerts on."""
    queued: int
    failed: int


def send_favorites_digests(
    users: AuthStore, prefs: PrefsStore, dining: Dining, sender: EmailSender, public_url: str, *, days: int = 1
) -> DigestReport:
    """Email every user with notify.email on whose favorites are on their dining halls' menus from
    today through `days` days ahead. Only confirmed addresses get one, since anyone can sign up
    with someone else's address."""
    if not 1 <= days <= MAX_ALERT_DAYS:
        raise ValueError(f"days must be from 1 to {MAX_ALERT_DAYS}.")
    subscribed = queued = failed = 0
    for user_id, doc in prefs.with_email_alerts():
        subscribed += 1
        user = users.user_by_id(user_id)
        if user is None or user.email_verified_at is None:
            continue
        try:
            saved = Prefs.model_validate(doc)
        except ValidationError:
            log.warning("Skipped the unreadable preferences of user %s", user_id)
            failed += 1
            continue
        alerts = dining.alerts(saved, days)
        if not alerts:
            continue
        try:
            sender.send(favorites_email(user.email, alerts, public_url))
        except Exception:
            log.exception("Couldn't send a favorites email")  # best effort, like the account emails
            failed += 1
        else:
            queued += 1
    return DigestReport(users=subscribed, queued=queued, failed=failed)


def open_digest(settings: Settings) -> tuple[AuthStore, PrefsStore, Dining]:
    """The accounts, preferences and menus in MongoDB, for `mealscholar alerts-digest`."""
    dining = open_dining(settings)
    if dining is None:
        raise ValueError("alerts-digest needs MONGODB_URI: accounts, preferences and menus are in MongoDB.")
    users = open_store(settings)
    return users, open_prefs_store(users), dining
