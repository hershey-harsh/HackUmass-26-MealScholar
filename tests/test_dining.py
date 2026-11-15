"""Dining menus over HTTP from a fake umass_dining source: locations, menus, the next meal by
typical hours, dish search, the user's flags and alerts, the favorites email and its command, and
a day's dishes with their nutrition labels (for campus.py).

MongoDB is never contacted; MongoDiningSource's queries are checked against stand-in collections.
"""

import copy
import hashlib
import logging
import re
import smtplib
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from mealscholar import cli, mailer
from mealscholar.auth import AuthService
from mealscholar.auth_store import MemoryAuthStore, utc_now
from mealscholar.config import SmtpSettings, load_settings
from mealscholar.dining import (
    CATALOG_MEAL,
    MEALS,
    Alert,
    DigestReport,
    Dining,
    DiningUnavailable,
    MongoDiningSource,
    favorites_email,
    send_favorites_digests,
    starts_hint,
)
from mealscholar.mailer import OutboxSender, SmtpSender, make_sender
from mealscholar.prefs import LOCAL_USER, MemoryPrefsStore, Prefs, PrefsService
from mealscholar.web import create_app

EASTERN = ZoneInfo("America/New_York")
YESTERDAY, TODAY, TOMORROW, NEXT_DAY = (date(2026, 11, 14) + timedelta(days=n) for n in (-1, 0, 1, 2))
PASSWORD = "correct horse battery"
SIGN_IN = {"detail": "Sign in to continue."}
SUBJECT = "Your favorites are on the menu"
NOT_CONNECTED = {"detail": "Dining menus aren't connected."}
UNAVAILABLE = {"detail": "Dining menus aren't available right now. Try again in a minute."}


# --- the fake umass_dining database -------------------------------------------


def item(name, kcal, protein, carbs, fat, allergens=(), diets=(), *, serving="1 serving", price=None, sodium=None):
    """A daily_menus item as the scraper writes it. Sodium is on the menu_items rows only (menu_rows)."""
    return {
        "dish_id": hashlib.sha1(name.encode()).hexdigest(),
        "name": name,
        "serving_size": serving,
        "price": price,
        "calories": kcal,
        "protein_g": protein,
        "total_carb_g": carbs,
        "total_fat_g": fat,
        "sodium_mg": sodium,
        "allergens": list(allergens),
        "diets": list(diets),
        "carbon_rating": "low",
        "healthfulness_score": 3,
        "icons": [],
    }


WAFFLE = item(
    "Belgian Waffle", 410, 9, 52, 18, ["Milk", "Eggs", "Wheat", "Gluten"], ["Vegetarian", "VGT"], serving="1 waffle"
)
EGGS = item("Scrambled Eggs", 180, 12, 2, 13, ["Eggs"], ["Vegetarian", "Halal"])
TIKKA = item("Chicken Tikka Masala", 320, 24, 14, 18, ["Milk"], ["Halal"], serving="6 OZ", sodium=620)
CHILI = item("Vegan Chili", 240, 11, 38, 5, ["Soy"], ["Plant Based", "Vegetarian", "Local"], serving="8 OZL")
PIZZA = item("Cheese Pizza", 280, 12, 33, 11, ["Milk", "Wheat", "Gluten"], ["Vegetarian"], serving="1 slice")
PAD_THAI = item("Pad Thai", 450, 15, 60, 16, ["Peanuts", "Soy", "Eggs", "Fish"])
SALAD = item("Garden Salad", 60, 2, 10, 1, [], ["Plant Based", "Vegetarian", "Local"])
CLUB = item("Turkey Club", 520, 30, 42, 24, ["Wheat", "Gluten", "Eggs"], serving="1 sandwich", price=8.49)

RAW_MEAL = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "late_night": "Late Night",
    "grab_n_go": "Grab N Go",
    "daily_offerings": "Daily Offerings",
}


def menu(tid, name, day, meals):
    """A daily_menus document. meals: {meal key: {category: [items]}}."""
    return {
        "_id": f"{tid}|{day.isoformat()}",
        "location_id": tid,
        "location_name": name,
        "date": day.isoformat(),
        "weekday": f"{day:%A}",
        "meals": [
            {
                "meal": key,
                "meal_raw": RAW_MEAL[key],
                "categories": [{"category": category, "items": items} for category, items in categories.items()],
            }
            for key, categories in meals.items()
        ],
        "item_count": sum(len(items) for categories in meals.values() for items in categories.values()),
    }


def menu_rows(menus):
    """The menu_items documents the scraper writes next to the daily_menus ones: one per listed
    item (the odd documents of test_odd_documents_are_read_carefully have none)."""
    rows = []
    for doc in menus:
        for meal in doc["meals"]:
            if not isinstance(meal, dict):
                continue
            for category in meal["categories"]:
                for i in category["items"]:
                    if not isinstance(i, dict) or "dish_id" not in i:
                        continue
                    rows.append(
                        {
                            "location_id": doc["location_id"],
                            "date": doc["date"],
                            "name": i["name"],
                            "meal": meal["meal"],
                            "category": category["category"],
                            "dish_id": i["dish_id"],
                            "serving_size": i["serving_size"],
                            "calories": i["calories"],
                            "nutrition": {
                                "protein_g": i["protein_g"],
                                "total_carb_g": i["total_carb_g"],
                                "total_fat_g": i["total_fat_g"],
                                "sodium_mg": i["sodium_mg"],
                            },
                            "healthfulness_score": i["healthfulness_score"],
                        }
                    )
    return rows


def dates(*days):
    return [day.isoformat() for day in days]


WORCESTER, BLUE_WALL, BERKSHIRE = "Worcester Dining Commons", "Blue Wall", "Berkshire Dining Commons"
LOCATIONS = [
    {
        "_id": 1,
        "name": WORCESTER,
        "area": "Northeast",
        "is_dining_commons": True,
        "has_prices": False,
        "dates_available": dates(YESTERDAY, TODAY, TOMORROW),
    },
    {
        "_id": 7,
        "name": BLUE_WALL,
        "area": "Campus Center",
        "is_dining_commons": False,
        "has_prices": True,
        "dates_available": dates(YESTERDAY, TOMORROW, NEXT_DAY),
    },
    {
        "_id": 2,
        "name": BERKSHIRE,
        "area": "Southwest",
        "is_dining_commons": True,
        "has_prices": False,
        "dates_available": dates(TOMORROW),
    },
    {  # only past menus
        "_id": 3,
        "name": "Franklin Dining Commons",
        "area": "Central",
        "is_dining_commons": True,
        "has_prices": False,
        "dates_available": ["2026-11-10"],
    },
]
MENUS = [
    menu(1, WORCESTER, YESTERDAY, {"lunch": {"Entrées": [TIKKA]}}),
    menu(
        1,
        WORCESTER,
        TODAY,
        {  # out of order on purpose: the API lists meals in serving order
            "dinner": {"Entrées": [PAD_THAI, TIKKA], "World Flavors": [TIKKA]},
            "breakfast": {"Breakfast Entrées": [WAFFLE, EGGS]},
            "lunch": {"Entrées": [TIKKA, CHILI], "Pizza": [PIZZA]},
            "late_night": {"Pizza": [PIZZA]},
            "daily_offerings": {"Salad Bar": [SALAD]},
        },
    ),
    menu(1, WORCESTER, TOMORROW, {"lunch": {"Pizza": [PIZZA]}, "dinner": {"Entrées": [TIKKA]}}),  # no breakfast
    menu(7, BLUE_WALL, YESTERDAY, {"grab_n_go": {"Sandwiches": [CLUB]}}),
    menu(7, BLUE_WALL, TOMORROW, {"grab_n_go": {"Sandwiches": [CLUB], "Pizza": [PIZZA]}}),
    menu(7, BLUE_WALL, NEXT_DAY, {"grab_n_go": {"Sandwiches": [CLUB]}}),
    menu(2, BERKSHIRE, TOMORROW, {"breakfast": {"Griddle": [WAFFLE]}, "dinner": {"Noodles": [PAD_THAI]}}),
]
DISHES = [
    {"_id": "d1", "name": "Cheese Pizza", "calories": 280, "location_ids": [1, 7], "occurrence_count": 40},
    {"_id": "d2", "name": "Chicken Tikka Masala", "calories": 320, "location_ids": [1], "occurrence_count": 12},
    {"_id": "d3", "name": "Mac & Cheese (GF)", "calories": 410, "location_ids": [2], "occurrence_count": 9},
    {"_id": "d4", "name": "Pad Thai", "calories": 450, "location_ids": [1, 2], "occurrence_count": 7},
    {"_id": "d5", "name": "cheese pizza", "calories": 300, "location_ids": [2, 99], "occurrence_count": 5},
    {"_id": "d6", "name": "General Tso's Chicken", "calories": 390, "location_ids": [1], "occurrence_count": 3},
]


class FakeDining:
    """The umass_dining collections in memory, answering as MongoDiningSource does."""

    def __init__(self, locations=LOCATIONS, menus=MENUS, dishes=DISHES, items=None):
        self._locations = locations
        self._menus = {doc["_id"]: doc for doc in menus}
        self._items = menu_rows(menus) if items is None else items
        self._dishes = dishes
        self.reads = Counter()
        self.down = False

    def _read(self, collection):
        if self.down:
            raise ServerSelectionTimeoutError("No servers found yet")
        self.reads[collection] += 1

    def locations(self):
        self._read("locations")
        return copy.deepcopy(self._locations)

    def daily_menus(self, ids, *, names_only=False):
        self._read("daily_menus")
        docs = [copy.deepcopy(self._menus[i]) for i in ids if i in self._menus]
        if names_only:  # as the projection does: items keep only their name
            for doc in docs:
                for meal in doc["meals"]:
                    for category in meal["categories"]:
                        category["items"] = [{"name": i["name"]} for i in category["items"]]
        return docs

    def menu_items(self, location_id, day):
        self._read("menu_items")
        return copy.deepcopy([r for r in self._items if r.get("location_id") == location_id and r.get("date") == day])

    def menu_items_between(self, location_ids, start, end):
        self._read("menu_items")
        found = [r for r in self._items if r.get("location_id") in location_ids and start <= r.get("date", "") <= end]
        return copy.deepcopy(found)

    def dishes(self, pattern, limit):
        self._read("dishes")
        regex = re.compile(pattern, re.IGNORECASE)  # as {"$regex": pattern, "$options": "i"}
        found = [dish for dish in self._dishes if regex.search(dish["name"])]
        return copy.deepcopy(sorted(found, key=lambda d: (-d["occurrence_count"], d["name"]))[:limit])

    def catalog(self, location_ids, limit):
        self._read("dishes")
        found = [dish for dish in self._dishes if set(dish.get("location_ids", ())) & set(location_ids)]
        return copy.deepcopy(sorted(found, key=lambda d: (-d["occurrence_count"], d["name"]))[:limit])


class Clock:
    """Campus time, set by hand."""

    def __init__(self) -> None:
        self.now = datetime.combine(TODAY, time(12, 30), EASTERN)

    def __call__(self) -> datetime:
        return self.now

    def at(self, hour, minute=0, second=0, day=TODAY) -> None:
        self.now = datetime.combine(day, time(hour, minute, second), EASTERN)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def source():
    return FakeDining()


@pytest.fixture
def make_app(tmp_path, clock, source):
    """App factory with the fake menus (dining=False: no MONGODB_URI) and in-memory accounts."""

    def make(*, dining=True, prefs=None, **env):
        settings = load_settings({"MEALSCHOLAR_HOME": str(tmp_path), **env})
        auth = AuthService.from_settings(settings, store=MemoryAuthStore())
        menus = Dining(source, clock=clock) if dining else None
        return create_app(settings, auth=auth, prefs=prefs or MemoryPrefsStore(), dining=menus)

    return make


def browser(app) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def register(client, email="sam@example.com") -> str:
    res = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert res.status_code == 201
    return res.json()["user"]["id"]


def save_prefs(client, body):
    res = client.patch("/api/me/prefs", json=body)
    assert res.status_code == 200, res.json()
    return res.json()


def served(doc, category=None):
    """An item as the API returns it."""
    out = {
        "dish_id": doc["dish_id"],
        "name": doc["name"],
        "calories": doc["calories"],
        "protein_g": doc["protein_g"],
        "carbs_g": doc["total_carb_g"],
        "fat_g": doc["total_fat_g"],
        "allergens": doc["allergens"],
        "diets": [diet for diet in doc["diets"] if diet != "VGT"],
        "price": doc["price"],
        "serving_size": doc["serving_size"],
    }
    return out if category is None else {**out, "category": category}


def alert(name, location_id, day, meal):
    names = {1: WORCESTER, 7: BLUE_WALL, 2: BERKSHIRE}
    return {
        "name": name,
        "location_id": location_id,
        "location_name": names[location_id],
        "date": day.isoformat(),
        "meal": meal,
        "label": MEALS[meal],
    }


# --- public menus -------------------------------------------------------------


def test_locations_with_a_menu_today_or_later(make_app, clock):
    client = browser(make_app())
    res = client.get("/api/dining/locations")
    assert res.status_code == 200
    assert res.json() == [  # dining commons first, then by name; Franklin has only past menus
        {
            "id": 2,
            "name": BERKSHIRE,
            "area": "Southwest",
            "is_dining_commons": True,
            "has_prices": False,
            "next_date": "2026-11-15",
            "dates": ["2026-11-15"],
        },
        {
            "id": 1,
            "name": WORCESTER,
            "area": "Northeast",
            "is_dining_commons": True,
            "has_prices": False,
            "next_date": "2026-11-14",
            "dates": ["2026-11-13", "2026-11-14", "2026-11-15"],
        },
        {
            "id": 7,
            "name": BLUE_WALL,
            "area": "Campus Center",
            "is_dining_commons": False,
            "has_prices": True,
            "next_date": "2026-11-15",
            "dates": ["2026-11-13", "2026-11-15", "2026-11-16"],
        },
    ]
    clock.at(9, day=NEXT_DAY)
    assert [loc["id"] for loc in client.get("/api/dining/locations").json()] == [7]


def test_locations_list_their_days_with_a_menu_from_a_week_back(clock):
    week_back = TODAY - timedelta(days=7)
    hall = dict(LOCATIONS[0], dates_available=dates(week_back - timedelta(days=1), week_back, TODAY, NEXT_DAY))
    [info] = Dining(FakeDining(locations=[hall]), clock=clock).open_locations()
    assert info.dates == (week_back, TODAY, NEXT_DAY)


def test_menu_for_a_day(make_app):
    client = browser(make_app())
    res = client.get("/api/dining/menu", params={"location_id": 1})  # today on campus
    assert res.status_code == 200
    body = res.json()
    assert (body["location"], body["date"]) == ({"id": 1, "name": WORCESTER}, "2026-11-14")
    assert [(meal["meal"], meal["label"]) for meal in body["meals"]] == [
        ("breakfast", "Breakfast"),
        ("lunch", "Lunch"),
        ("dinner", "Dinner"),
        ("late_night", "Late Night"),
        ("daily_offerings", "Daily Offerings"),
    ]
    breakfast = {"category": "Breakfast Entrées", "items": [served(WAFFLE), served(EGGS)]}
    assert body["meals"][0]["categories"] == [breakfast]
    assert body["meals"][0]["categories"][0]["items"][0]["diets"] == ["Vegetarian"]  # other diet codes are left out
    assert [c["category"] for c in body["meals"][2]["categories"]] == ["Entrées", "World Flavors"]

    cafe = client.get("/api/dining/menu", params={"location_id": 7, "date": "2026-11-15"}).json()
    assert cafe["meals"] == [
        {
            "meal": "grab_n_go",
            "label": "Grab 'n Go",
            "categories": [
                {"category": "Sandwiches", "items": [served(CLUB)]},
                {"category": "Pizza", "items": [served(PIZZA)]},
            ],
        }
    ]
    assert cafe["meals"][0]["categories"][0]["items"][0]["price"] == 8.49


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"location_id": 7, "date": "2026-11-14"}, 404),  # Blue Wall has no menu today
        ({"location_id": 999}, 404),
        ({"location_id": 1, "date": "2026-02-30"}, 422),
        ({"location_id": 1, "date": "11/14/2026"}, 422),
        ({"location_id": 1, "date": "1763096400"}, 422),
        ({"location_id": "1|2026-11-14"}, 422),
        ({"location_id": 0}, 422),
        ({"location_id[$ne]": 1}, 422),
        ({}, 422),
    ],
)
def test_menu_lookups_that_find_nothing(make_app, params, status):
    res = browser(make_app()).get("/api/dining/menu", params=params)
    assert res.status_code == status
    if status == 404:
        assert res.json() == {"detail": "No menu for that location and date."}


# --- the next meal, by typical hours ------------------------------------------


@pytest.mark.parametrize(
    ("at", "day", "meal", "hint"),
    [
        ((6, 0), TODAY, "breakfast", "Breakfast, usually 7 to 10:30 am"),  # before breakfast
        ((10, 29, 59), TODAY, "breakfast", "Breakfast, usually 7 to 10:30 am"),
        ((10, 45), TODAY, "lunch", "Lunch, usually 11 am to 4 pm"),  # between meals: the next one
        ((12, 30), TODAY, "lunch", "Lunch, usually 11 am to 4 pm"),  # midday
        ((16, 15), TODAY, "dinner", "Dinner, usually 4:30 to 9 pm"),
        ((21, 30), TODAY, "late_night", "Late Night, usually 9 pm to midnight"),  # late evening
        ((23, 59, 30), TOMORROW, "lunch", "Lunch, usually 11 am to 4 pm"),  # rolls over; tomorrow has no breakfast
    ],
)
def test_next_meal_by_typical_hours(make_app, clock, at, day, meal, hint):
    clock.at(*at)
    res = browser(make_app()).get("/api/dining/next", params={"location_id": 1})
    assert res.status_code == 200
    body = res.json()
    assert (body["date"], body["meal"], body["label"]) == (day.isoformat(), meal, MEALS[meal])
    assert body["starts_hint"] == hint
    assert body["location"] == {"id": 1, "name": WORCESTER}


def test_next_meal_items_carry_their_category(make_app):
    body = browser(make_app()).get("/api/dining/next", params={"location_id": 1}).json()
    assert body["items"] == [served(TIKKA, "Entrées"), served(CHILI, "Entrées"), served(PIZZA, "Pizza")]


def test_next_meal_skips_meals_a_day_doesnt_have(make_app, clock):
    client = browser(make_app())
    clock.at(16, 15, day=TOMORROW)
    assert client.get("/api/dining/next", params={"location_id": 1}).json()["meal"] == "dinner"
    clock.at(21, 30, day=TOMORROW)  # no late night, and no menus after that
    res = client.get("/api/dining/next", params={"location_id": 1})
    assert res.status_code == 404
    assert res.json() == {"detail": "No upcoming menus at Worcester Dining Commons."}

    res = client.get("/api/dining/next", params={"location_id": 999})
    assert res.status_code == 404 and res.json() == {"detail": "There's no dining location 999."}


def test_a_cafe_without_set_meals_has_its_menu_next(make_app, clock):
    client = browser(make_app())
    body = client.get("/api/dining/next", params={"location_id": 7}).json()  # nothing today: tomorrow
    assert (body["date"], body["meal"]) == ("2026-11-15", "grab_n_go")
    assert body["starts_hint"] == "Grab 'n Go, hours vary by location"
    assert [i["name"] for i in body["items"]] == ["Turkey Club", "Cheese Pizza"]
    clock.at(23, 0, day=TOMORROW)
    assert client.get("/api/dining/next", params={"location_id": 7}).json()["date"] == "2026-11-15"
    clock.at(23, 59, 30, day=TOMORROW)
    assert client.get("/api/dining/next", params={"location_id": 7}).json()["date"] == "2026-11-16"


def test_starts_hints():
    assert [starts_hint(meal, label) for meal, label in MEALS.items()] == [
        "Breakfast, usually 7 to 10:30 am",
        "Lunch, usually 11 am to 4 pm",
        "Dinner, usually 4:30 to 9 pm",
        "Late Night, usually 9 pm to midnight",
        "Grab 'n Go, hours vary by location",
        "Daily Offerings, hours vary by location",
    ]


# --- dish search --------------------------------------------------------------


def test_dish_search_merges_names_and_puts_common_dishes_first(make_app):
    client = browser(make_app())
    res = client.get("/api/dining/dishes", params={"q": "CHEESE"})
    assert res.status_code == 200
    assert res.json() == [
        {"name": "Cheese Pizza", "calories": 280, "locations": [BERKSHIRE, WORCESTER, BLUE_WALL], "seen_on": 45},
        {"name": "Mac & Cheese (GF)", "calories": 410, "locations": [BERKSHIRE], "seen_on": 9},
    ]
    found = client.get("/api/dining/dishes", params={"q": "ch", "limit": 2}).json()
    assert [dish["name"] for dish in found] == ["Cheese Pizza", "Chicken Tikka Masala"]
    assert client.get("/api/dining/dishes", params={"q": "  a  "}).json() == []  # one letter once trimmed


@pytest.mark.parametrize(
    ("q", "names"),
    [
        (".*", []),
        ("((", []),
        ("[a-z]+", []),
        ("\\\\", []),
        ("$where", []),
        ('{"$ne": ""}', []),
        ("a|e", []),
        ("^Ch", []),
        ("e{2}", []),
        ("(GF)", ["Mac & Cheese (GF)"]),
        ("& ch", ["Mac & Cheese (GF)"]),
        ("tso's", ["General Tso's Chicken"]),
    ],
)
def test_dish_search_matches_text_literally(make_app, q, names):
    res = browser(make_app()).get("/api/dining/dishes", params={"q": q})
    assert res.status_code == 200
    assert [dish["name"] for dish in res.json()] == names


@pytest.mark.parametrize(
    "params", [{"q": "a"}, {"q": "x" * 81}, {"q": "pizza", "limit": 21}, {"q": "pizza", "limit": 0}, {}]
)
def test_dish_search_limits(make_app, params):
    assert browser(make_app()).get("/api/dining/dishes", params=params).status_code == 422


# --- caching and failures -----------------------------------------------------


def test_reads_are_cached_for_five_minutes(make_app, source, clock):
    client = browser(make_app())
    for _ in range(3):
        assert client.get("/api/dining/locations").status_code == 200
        assert client.get("/api/dining/menu", params={"location_id": 1}).status_code == 200
        assert client.get("/api/dining/menu", params={"location_id": 7}).status_code == 404  # "no menu" too
        assert client.get("/api/dining/dishes", params={"q": "pizza"}).status_code == 200
        assert client.get("/api/dining/next", params={"location_id": 1}).status_code == 200
    assert source.reads == {"locations": 1, "daily_menus": 2, "dishes": 1}

    client.get("/api/dining/menu", params={"location_id": 1, "date": "2026-11-15"})  # another query
    assert source.reads["daily_menus"] == 3

    clock.now += timedelta(minutes=4, seconds=59)
    client.get("/api/dining/locations")
    assert source.reads["locations"] == 1
    clock.now += timedelta(seconds=1)
    client.get("/api/dining/locations")
    client.get("/api/dining/menu", params={"location_id": 1})
    assert (source.reads["locations"], source.reads["daily_menus"]) == (2, 4)


def test_without_mongodb_dining_answers_503(make_app):
    client = browser(make_app(dining=False))
    for path in (
        "/api/dining/locations",
        "/api/dining/menu?location_id=1",
        "/api/dining/next?location_id=1",
        "/api/dining/dishes?q=pizza",
    ):
        res = client.get(path)
        assert res.status_code == 503 and res.json() == NOT_CONNECTED
    assert client.get("/api/me/alerts").json() == SIGN_IN  # sign-in is checked first

    register(client)
    for path in ("/api/me/dining/next", "/api/me/alerts"):
        res = client.get(path)
        assert res.status_code == 503 and res.json() == NOT_CONNECTED
    assert client.get("/api/me/prefs").status_code == 200  # preferences work without the menus


def test_a_database_outage_is_a_503(make_app, source):
    client = browser(make_app())
    register(client)
    source.down = True
    for res in (
        client.get("/api/dining/locations"),
        client.get("/api/dining/menu", params={"location_id": 1}),
        client.get("/api/dining/dishes", params={"q": "pizza"}),
        client.patch("/api/me/prefs", json={"dining_halls": [1]}),  # the ids can't be checked
    ):
        assert res.status_code == 503 and res.json() == UNAVAILABLE
    source.down = False
    assert client.get("/api/dining/locations").status_code == 200  # failures aren't cached


# --- the user's next meal and alerts ------------------------------------------


def test_my_next_meal_flags_each_item(make_app):
    client = browser(make_app())
    register(client)
    favorite = [{"name": "chicken  tikka masala"}]
    save_prefs(client, {"favorites": favorite, "avoid_allergens": ["milk"], "diets": ["Halal"]})

    res = client.get("/api/me/dining/next")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert (body["location"]["id"], body["meal"]) == (1, "lunch")  # Worcester when no dining hall is chosen
    assert body["items"][0] == {**served(TIKKA, "Entrées"), "favorite": True, "avoid": True, "fits_diet": True}
    assert [(i["name"], i["favorite"], i["avoid"], i["fits_diet"]) for i in body["items"]] == [
        ("Chicken Tikka Masala", True, True, True),
        ("Vegan Chili", False, False, False),
        ("Cheese Pizza", False, True, False),
    ]

    save_prefs(client, {"dining_halls": [7, 1], "diets": [], "avoid_allergens": []})
    body = client.get("/api/me/dining/next").json()
    assert (body["location"]["id"], body["date"], body["meal"]) == (7, "2026-11-15", "grab_n_go")  # the first hall
    assert all(i["fits_diet"] and not i["avoid"] for i in body["items"])  # nothing chosen: nothing ruled out

    body = client.get("/api/me/dining/next", params={"location_id": 2}).json()
    assert (body["location"]["name"], body["meal"]) == (BERKSHIRE, "breakfast")
    assert client.get("/api/me/dining/next", params={"location_id": 0}).status_code == 422

    public = client.get("/api/dining/next", params={"location_id": 1}).json()
    assert "favorite" not in public["items"][0]


def test_my_routes_need_a_session_unless_sign_in_is_optional(make_app):
    client = browser(make_app())
    for path in ("/api/me/dining/next", "/api/me/alerts"):
        res = client.get(path)
        assert res.status_code == 401 and res.json() == SIGN_IN

    local = browser(make_app(MEALSCHOLAR_AUTH_REQUIRED="0"))
    save_prefs(local, {"favorites": [{"name": "Vegan Chili"}]})
    assert local.get("/api/me/dining/next").json()["items"][1]["favorite"] is True
    assert local.get("/api/me/alerts").json() == [alert("Vegan Chili", 1, TODAY, "lunch")]


def test_alerts_list_favorites_on_upcoming_menus(make_app, source):
    client = browser(make_app())
    register(client)
    assert client.get("/api/me/alerts").json() == []  # no favorites, nothing to look up
    assert source.reads["daily_menus"] == 0

    favorites = ["cheese pizza", "Chicken Tikka Masala", "Turkey Club"]
    save_prefs(client, {"favorites": [{"name": name} for name in favorites]})
    res = client.get("/api/me/alerts", params={"days": 1})
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    # Every dining commons when none is chosen, so not Blue Wall; yesterday's menus don't count.
    assert res.json() == [
        alert("Cheese Pizza", 1, TODAY, "lunch"),
        alert("Chicken Tikka Masala", 1, TODAY, "lunch"),
        alert("Chicken Tikka Masala", 1, TODAY, "dinner"),  # in two categories, listed once
        alert("Cheese Pizza", 1, TODAY, "late_night"),
        alert("Cheese Pizza", 1, TOMORROW, "lunch"),
        alert("Chicken Tikka Masala", 1, TOMORROW, "dinner"),
    ]

    save_prefs(client, {"dining_halls": [7, 1]})
    assert client.get("/api/me/alerts", params={"days": 1}).json()[-4:] == [
        alert("Cheese Pizza", 1, TOMORROW, "lunch"),
        alert("Chicken Tikka Masala", 1, TOMORROW, "dinner"),
        alert("Cheese Pizza", 7, TOMORROW, "grab_n_go"),
        alert("Turkey Club", 7, TOMORROW, "grab_n_go"),
    ]


def test_alerts_cover_every_dining_commons_whatever_halls_are_chosen(make_app, source):
    client = browser(make_app())
    register(client)
    # Only Blue Wall chosen: a favorite on any dining commons' menu still counts, and so does Blue Wall.
    save_prefs(client, {"favorites": [{"name": "Pad Thai"}, {"name": "Turkey Club"}], "dining_halls": [7]})
    assert client.get("/api/me/alerts", params={"days": 1}).json() == [
        alert("Pad Thai", 1, TODAY, "dinner"),
        alert("Pad Thai", 2, TOMORROW, "dinner"),
        alert("Turkey Club", 7, TOMORROW, "grab_n_go"),
    ]


def test_alerts_window(make_app, clock):
    client = browser(make_app())
    register(client)
    save_prefs(client, {"favorites": [{"name": "Turkey Club"}], "dining_halls": [7]})

    def days(n):
        return [(a["date"], a["meal"]) for a in client.get("/api/me/alerts", params={"days": n}).json()]

    assert days(1) == [("2026-11-15", "grab_n_go")]
    assert days(2) == [("2026-11-15", "grab_n_go"), ("2026-11-16", "grab_n_go")]
    assert days(14) == days(2)
    assert days(7) == days(2)  # the default is 7
    assert client.get("/api/me/alerts").json() == client.get("/api/me/alerts", params={"days": 7}).json()
    for bad in (0, 15, "week"):
        assert client.get("/api/me/alerts", params={"days": bad}).status_code == 422

    clock.at(8, day=NEXT_DAY)
    assert days(1) == [("2026-11-16", "grab_n_go")]


# --- the favorites email ------------------------------------------------------


def test_favorites_email():
    alerts = [
        Alert.model_validate(alert("Cheese Pizza", 1, TODAY, "lunch")),
        Alert.model_validate(alert("Chicken Tikka Masala", 1, TODAY, "lunch")),
        Alert.model_validate(alert("Chicken Tikka Masala", 1, TODAY, "dinner")),
        Alert.model_validate(alert("Turkey Club", 7, TOMORROW, "grab_n_go")),
    ]
    email = favorites_email("sam@example.com", alerts, "https://meals.example")
    assert (email.to, email.subject, email.purpose) == ("sam@example.com", SUBJECT, "favorites_digest")
    assert email.text == (
        "Some of your favorite dishes are on the UMass Dining menu:\n"
        "\n"
        "Saturday, November 14\n"
        "  Lunch at Worcester Dining Commons: Cheese Pizza, Chicken Tikka Masala\n"
        "  Dinner at Worcester Dining Commons: Chicken Tikka Masala\n"
        "\n"
        "Sunday, November 15\n"
        "  Grab 'n Go at Blue Wall: Turkey Club\n"
        "\n"
        "See the menus: https://meals.example/\n"
        "\n"
        "You get this email because email alerts are on in your MealScholar settings. "
        "Turn them off there to stop these emails.\n"
    )
    html = email.html
    assert "Saturday, November 14" in html and "Grab &#x27;n Go at Blue Wall: Turkey Club" in html
    assert 'href="https://meals.example/"' in html and "See the menus" in html
    assert "<img" not in html and "src=" not in html


def accounts_with_prefs():
    """Sam, Alex, Kim and Lee, all with a favorite on the menus; only Sam should get an email."""
    users, prefs = MemoryAuthStore(), MemoryPrefsStore()
    service, now = PrefsService(prefs), utc_now()

    def account(email, body, *, confirmed=True):
        user = users.create_user(email, None, "not a real hash", now)
        if confirmed:
            users.mark_email_verified(user.id, now)
        service.update(user.id, body)

    pad_thai = [{"name": "pad thai"}]
    account("sam@example.com", {"favorites": pad_thai, "dining_halls": [2], "notify": {"email": True}})
    account("alex@example.com", {"favorites": pad_thai, "notify": {"email": True}}, confirmed=False)
    account("kim@example.com", {"favorites": pad_thai})  # email alerts off
    account("lee@example.com", {"favorites": [{"name": "Lobster Bisque"}], "notify": {"email": True}})  # not on
    service.update(LOCAL_USER, {"favorites": pad_thai, "notify": {"email": True}})  # no account, no address
    return users, prefs


def test_digest_emails_confirmed_users_with_email_alerts_on(source, clock, monkeypatch, caplog):
    users, prefs = accounts_with_prefs()
    dining = Dining(source, clock=clock)
    report = send_favorites_digests(users, prefs, dining, OutboxSender(users), "https://meals.example", days=1)
    assert report == DigestReport(users=4, queued=1, failed=0)
    (email,) = users.recent_emails(10)
    assert (email.to, email.subject, email.purpose) == ("sam@example.com", SUBJECT, "favorites_digest")
    assert (email.status, email.provider) == ("queued", "outbox")
    assert email.expires_at - email.created_at == timedelta(days=7)
    assert "Sunday, November 15\n  Dinner at Berkshire Dining Commons: Pad Thai\n" in email.text

    def refuse(host, port, timeout=None):
        raise OSError(f"[Errno 61] Connection refused: {host}:{port}")

    monkeypatch.setattr(mailer.smtplib, "SMTP", refuse)
    smtp = SmtpSender(SmtpSettings(host="smtp.invalid", sender="x@example.com"), background=False)
    with caplog.at_level(logging.WARNING, logger="mealscholar.dining"):
        failing = send_favorites_digests(users, prefs, dining, smtp, "https://meals.example", days=1)
    assert failing == DigestReport(users=4, queued=0, failed=1)  # synchronous: the command counts the failure
    assert "Couldn't send the favorites_digest email to s***@example.com: OSError" in caplog.text
    assert "sam@example.com" not in caplog.text
    with pytest.raises(ValueError, match="MEALSCHOLAR_SMTP_HOST"):
        make_sender("smtp", users, None)

    # A bounce or an auth refusal: the relay's reply text (which repeats the address and the login
    # user) and the traceback stay out of the log, same as the account emails in background mode.
    class Relay:
        error: Exception

        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            if isinstance(Relay.error, smtplib.SMTPAuthenticationError):
                raise Relay.error

        def send_message(self, msg):
            raise Relay.error

    monkeypatch.setattr(mailer.smtplib, "SMTP", Relay)
    relay = SmtpSettings(host="smtp.invalid", sender="x@example.com", user="harsh@gmail.com", password="pw")
    for Relay.error, logged in [
        (
            smtplib.SMTPRecipientsRefused({"sam@example.com": (550, b"5.1.1 user unknown")}),
            "SMTPRecipientsRefused (550)",
        ),
        (
            smtplib.SMTPAuthenticationError(535, b"5.7.8 not accepted for user harsh@gmail.com"),
            "SMTPAuthenticationError (535)",
        ),
    ]:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="mealscholar.dining"):
            report = send_favorites_digests(
                users, prefs, dining, SmtpSender(relay, background=False), "https://x", days=1
            )
        assert report == DigestReport(users=4, queued=0, failed=1)
        (record,) = caplog.records
        assert record.getMessage() == f"Couldn't send the favorites_digest email to s***@example.com: {logged}"
        assert record.exc_info is None  # no traceback: its last line would be the relay's reply
        assert "sam@example.com" not in caplog.text
        assert "user unknown" not in caplog.text
        assert "harsh@gmail.com" not in caplog.text

    kim = users.user_by_email("kim@example.com")  # confirmed; a broken document is skipped, not fatal
    prefs.update(kim.id, {"notify.email": True, "favorites": "not a list"})
    report = send_favorites_digests(users, prefs, dining, OutboxSender(users), "https://meals.example", days=1)
    assert report == DigestReport(users=5, queued=1, failed=1)
    with pytest.raises(ValueError, match="days must be from 1 to 14"):
        send_favorites_digests(users, prefs, dining, OutboxSender(users), "https://meals.example", days=15)


def test_alerts_digest_command(monkeypatch, capsys, tmp_path, source, clock):
    users, prefs = accounts_with_prefs()
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "open_digest", lambda settings: (users, prefs, Dining(source, clock=clock)))
    monkeypatch.setenv("MEALSCHOLAR_HOME", str(tmp_path))
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setenv("MEALSCHOLAR_PUBLIC_URL", "https://meals.example/")
    monkeypatch.setenv("MEALSCHOLAR_EMAIL_PROVIDER", "outbox")

    cli.main(["alerts-digest", "--days", "2"])
    assert capsys.readouterr().out == "Queued 1 favorites email(s) for 4 user(s) with email alerts on.\n"
    (email,) = users.recent_emails(10)
    assert email.to == "sam@example.com"
    assert "See the menus: https://meals.example/\n" in email.text


def test_alerts_digest_command_needs_mongodb(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("MEALSCHOLAR_HOME", str(tmp_path))
    monkeypatch.delenv("MONGODB_URI", raising=False)
    for argv, error in (
        (["alerts-digest"], "alerts-digest needs MONGODB_URI: accounts, preferences and menus are in MongoDB."),
        (["alerts-digest", "--days", "15"], "--days must be from 1 to 14."),
    ):
        with pytest.raises(SystemExit) as stopped:
            cli.main(argv)
        assert stopped.value.code == 1
        assert capsys.readouterr().err == f"error: {error}\n"


# --- a day's dishes with their labels, for campus.py --------------------------


def test_dishes_on_reads_one_query_and_caches(source, clock):
    dining = Dining(source, clock=clock)
    facts = dining.dishes_on(1, TODAY)
    assert dining.dishes_on(1, TODAY) is facts  # the cached object
    assert source.reads == {"menu_items": 1, "locations": 1}  # locations: is there a Grab N' Go too
    assert [(dish.meal, dish.name) for dish in facts] == [  # menu order
        ("dinner", "Pad Thai"),
        ("dinner", "Chicken Tikka Masala"),  # in two categories, listed once
        ("breakfast", "Belgian Waffle"),
        ("breakfast", "Scrambled Eggs"),
        ("lunch", "Chicken Tikka Masala"),
        ("lunch", "Vegan Chili"),
        ("lunch", "Cheese Pizza"),
        ("late_night", "Cheese Pizza"),
        ("daily_offerings", "Garden Salad"),
    ]
    tikka, waffle, chili, pizza = facts[1], facts[2], facts[5], facts[6]
    assert waffle.model_dump() == {
        "dish_id": WAFFLE["dish_id"],
        "name": "Belgian Waffle",
        "meal": "breakfast",
        "category": "Breakfast Entrées",
        "serving_size": "1 waffle",
        "serving_grams": None,
        "per_serving": {"energy_kcal": 410, "protein_g": 9, "carbs_g": 52, "fat_g": 18},  # no sodium on the label
        "healthfulness_score": 3,
        "date": TODAY,
    }
    assert (pizza.serving_size, pizza.serving_grams) == ("1 slice", None)
    assert (tikka.serving_size, tikka.serving_grams, tikka.per_serving["sodium_mg"]) == ("6 OZ", 170.1, 620)
    assert (chili.serving_size, chili.serving_grams) == ("8 OZL", 236.56)
    # Blue Wall has no menu today, so the dishes it has served stand in, with no date
    assert [(d.meal, d.name, d.date) for d in dining.dishes_on(7, TODAY)] == [(CATALOG_MEAL, "Cheese Pizza", None)]
    assert dining.location_name(1) == WORCESTER
    assert dining.location_name(999) is None


def test_dishes_between_reads_the_days_and_their_grab_n_go_in_one_query(clock):
    """A photo without a date is matched against several days: one read, by day, the hall before
    its Grab N' Go, every dish with the day it was served."""
    gng = {"_id": 10667, "name": "Worcester Grab N' Go", "is_dining_commons": False, "dates_available": []}
    rows = menu_rows(MENUS)
    rows.insert(0, {**rows[-1], "location_id": 10667, "date": TODAY.isoformat(), "meal": "grab_n_go", "name": "Wrap"})
    source = FakeDining(locations=[*LOCATIONS, gng], items=rows)
    dining = Dining(source, clock=clock)

    facts = dining.dishes_between(1, YESTERDAY, TOMORROW)
    assert dining.dishes_between(1, YESTERDAY, TOMORROW) is facts  # cached
    assert source.reads == {"menu_items": 1, "locations": 1}
    assert [(d.date, d.meal, d.name) for d in facts] == [
        (YESTERDAY, "lunch", "Chicken Tikka Masala"),
        (TODAY, "dinner", "Pad Thai"),
        (TODAY, "dinner", "Chicken Tikka Masala"),
        (TODAY, "breakfast", "Belgian Waffle"),
        (TODAY, "breakfast", "Scrambled Eggs"),
        (TODAY, "lunch", "Chicken Tikka Masala"),  # the same dish on another day or meal stays
        (TODAY, "lunch", "Vegan Chili"),
        (TODAY, "lunch", "Cheese Pizza"),
        (TODAY, "late_night", "Cheese Pizza"),
        (TODAY, "daily_offerings", "Garden Salad"),
        (TODAY, "grab_n_go", "Wrap"),  # the Grab N' Go after the hall
        (TOMORROW, "lunch", "Cheese Pizza"),
        (TOMORROW, "dinner", "Chicken Tikka Masala"),
    ]
    assert dining.dishes_between(2, YESTERDAY, TODAY) == ()  # Berkshire: nothing those days
    assert [(d.meal, d.name, d.date) for d in dining.served_before(2)] == [
        (CATALOG_MEAL, "Mac & Cheese (GF)", None),
        (CATALOG_MEAL, "Pad Thai", None),
        (CATALOG_MEAL, "cheese pizza", None),
    ]


# --- MongoDB queries and odd documents ----------------------------------------


class StubCursor(list):
    def sort(self, keys):
        self.sorted_by = keys
        return self

    def limit(self, n):
        self.limited_to = n
        return self


class StubCollection:
    """Stands in for a pymongo collection: records each find and returns the documents given."""

    def __init__(self, docs=()):
        self.docs = list(docs)
        self.finds = []
        self.cursors = []

    def find(self, query, projection=None):
        self.finds.append((query, projection))
        self.cursors.append(StubCursor(copy.deepcopy(self.docs)))
        return self.cursors[-1]


def test_mongo_queries_are_built_from_checked_values(clock):
    db = {
        "locations": StubCollection(LOCATIONS),
        "daily_menus": StubCollection(MENUS[1:3]),
        "dishes": StubCollection(),
        "menu_items": StubCollection(menu_rows(MENUS[1:2])),
    }
    dining = Dining(MongoDiningSource(db), clock=clock)

    assert dining.menu(1, TODAY).location.name == WORCESTER
    dining.dishes("Mac & Cheese (GF)", 5)
    dining.alerts(Prefs.model_validate({"favorites": [{"name": "Pad Thai"}], "dining_halls": [1]}), 1)
    assert [dish.name for dish in dining.dishes_on(1, TODAY)][:2] == ["Pad Thai", "Chicken Tikka Masala"]

    assert db["menu_items"].finds == [
        (
            {"location_id": 1, "date": "2026-11-14"},
            {
                "name": True,
                "meal": True,
                "category": True,
                "dish_id": True,
                "serving_size": True,
                "calories": True,
                "nutrition": True,
                "healthfulness_score": True,
                "date": True,
                "location_id": True,
            },
        )
    ]
    db["menu_items"].finds.clear()
    dining.dishes_between(1, YESTERDAY, TOMORROW)
    ((query, fields),) = db["menu_items"].finds
    assert query == {"location_id": {"$in": [1]}, "date": {"$gte": "2026-11-13", "$lte": "2026-11-15"}}
    assert fields["date"] is True and fields["nutrition"] is True

    (full, full_fields), (names, names_fields) = db["daily_menus"].finds
    assert full == {"_id": {"$in": ["1|2026-11-14"]}}
    # alerts read every dining commons' menus (here Berkshire, Franklin and Worcester), not only the chosen hall
    commons = ["2|2026-11-14", "2|2026-11-15", "3|2026-11-14", "3|2026-11-15", "1|2026-11-14", "1|2026-11-15"]
    assert names == {"_id": {"$in": commons}}
    assert full_fields["meals.categories.items.total_carb_g"] is True and "meals" not in full_fields
    assert set(names_fields) == {
        "location_name",
        "meals.meal",
        "meals.meal_raw",
        "meals.categories.category",
        "meals.categories.items.name",
    }
    ((search, _),) = db["dishes"].finds
    assert search == {"name": {"$regex": re.escape("Mac & Cheese (GF)"), "$options": "i"}}
    assert (db["dishes"].cursors[0].sorted_by, db["dishes"].cursors[0].limited_to) == (
        [("occurrence_count", -1), ("name", 1)],
        100,
    )
    location_fields = ["name", "area", "is_dining_commons", "has_prices", "dates_available"]
    assert db["locations"].finds == [({}, dict.fromkeys(location_fields, True))]


def test_odd_documents_are_read_carefully(clock):
    """The scraper's documents are checked field by field; what can't be used is left out."""
    messy = {
        "_id": "5|2026-11-14",
        "location_id": 5,
        "date": "2026-11-14",
        "meals": [
            "not a meal",
            {"meal": "lunch", "categories": [{"category": "Empty", "items": []}]},
            {
                "meal": "brunch",
                "meal_raw": "Weekend  Brunch",
                "categories": [
                    {
                        "category": None,
                        "items": [
                            {
                                "name": " Hash  Browns ",
                                "calories": "150",
                                "protein_g": float("nan"),
                                "total_carb_g": True,
                                "price": "$2.50",
                                "allergens": "Soy, milk, Mustard",
                                "diets": ["vegetarian", "SUS"],
                                "serving_size": 4,
                            },
                            {"name": ""},
                            "junk",
                        ],
                    }
                ],
            },
            {"meal": "breakfast", "categories": [{"category": "Grill", "items": [{"name": "Toast"}]}]},
        ],
    }
    locations = [
        {"_id": 5, "name": " Roots  Café ", "dates_available": ["2026-11-14", "someday", 20261115, "2026-02-30"]},
        {"_id": "6", "name": "Not an int id"},
        {"_id": 8},
    ]
    dining = Dining(FakeDining(locations=locations, menus=[messy], dishes=[]), clock=clock)
    assert [(loc.id, loc.name, loc.dates) for loc in dining.locations()] == [(5, "Roots Café", (TODAY,))]

    found = dining.menu(5, TODAY)
    assert found.location.name == "Roots Café"  # the menu has no location_name, so the location's is used
    meals = [(meal.meal, meal.label) for meal in found.meals]
    assert meals == [("breakfast", "Breakfast"), ("brunch", "Weekend Brunch")]  # unknown meals go last
    category = found.meals[1].categories[0]
    assert category.category == "Other"
    (hash_browns,) = category.items
    assert hash_browns.model_dump() == {
        "dish_id": None,
        "name": "Hash Browns",
        "calories": 150.0,
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "allergens": ("Soy", "Milk", "Mustard"),
        "diets": ("Vegetarian",),
        "price": 2.5,
        "serving_size": "4",
    }


def test_odd_menu_items_are_read_carefully(clock):
    """Each menu_items row is checked field by field; a label that can't be right is left out."""

    def row(name, meal="lunch", **fields):
        return {"location_id": 5, "date": "2026-11-14", "name": name, "meal": meal, **fields}

    def label(kcal, protein=0, carbs=0, fat=0, **more):
        nutrition = {"protein_g": protein, "total_carb_g": carbs, "total_fat_g": fat, **more}
        return {"calories": kcal, "nutrition": nutrition}

    odd = label("150", 2, sodium_mg=-5, sugars_g="x")  # a negative or unreadable nutrient is left out
    rows = [
        row(" Hash  Browns ", "Lunch", serving_size=4, healthfulness_score=9, **odd),
        row("Fountain Drink", **label(None)),  # no calories: dropped
        row("12 oz Regular Coffee", serving_size="12 OZL", healthfulness_score=7, **label(0)),  # 0 kcal, 0/0/0: kept
        row("20 oz Latte", **label(0, protein=5)),  # 0 kcal next to protein: dropped
        row("Add Wasabi", **label(0, carbs=1)),
        row("Tea", calories=0, nutrition={"protein_g": 0, "total_fat_g": 0}),  # 0 kcal, carbs unknown: dropped
        row("Soup", calories="abc", nutrition={"protein_g": 3}),  # unreadable calories: dropped
        row("Toast", calories=90, nutrition="none"),  # calories only
        row("", **label(100)),  # no name
        row("Nameless", "", **label(100)),  # no meal
        row("Water", None, calories=0),
        row("hash browns", serving_size="3 oz", **label(160)),  # the same dish at the same meal: the first wins
        row("Hash Browns", "dinner", **label(160)),  # at another meal: listed again
        row("Elsewhere", **{**label(100), "location_id": 6}),  # another location
        row("Yesterday", **{**label(100), "date": "2026-11-13"}),
    ]
    source = FakeDining(locations=[{"_id": 5, "name": "Roots Café"}], menus=[], items=rows)
    dining = Dining(source, clock=clock)

    found = dining.dishes_on(5, TODAY)
    assert [(dish.meal, dish.name) for dish in found] == [
        ("lunch", "Hash Browns"),
        ("lunch", "12 oz Regular Coffee"),
        ("lunch", "Toast"),
        ("dinner", "Hash Browns"),
    ]
    hash_browns, coffee, toast = found[:3]
    assert hash_browns.model_dump() == {
        "dish_id": None,
        "name": "Hash Browns",
        "meal": "lunch",
        "category": None,
        "serving_size": "4",
        "serving_grams": None,
        "per_serving": {"energy_kcal": 150.0, "protein_g": 2.0, "carbs_g": 0.0, "fat_g": 0.0},  # not -5 mg sodium
        "healthfulness_score": None,  # 9 is off the scale
        "date": TODAY,
    }
    assert coffee.per_serving == {"energy_kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    assert (coffee.serving_grams, coffee.healthfulness_score) == (354.84, 7)
    assert toast.per_serving == {"energy_kcal": 90}

    source.down = True
    with pytest.raises(DiningUnavailable):
        dining.dishes_on(5, TOMORROW)
    source.down = False
    assert dining.dishes_on(5, TOMORROW) == ()  # the failure wasn't cached: read again
    assert source.reads == {"menu_items": 2, "locations": 1, "dishes": 1}  # an empty day looks at past dishes too


# --- nearest dining hall ---------------------------------------------------------


def test_nearest_hall_from_a_campus_location(make_app):
    # Standing at Worcester Commons. Fixture areas are "Northeast" (Worcester) and "Southwest" (Berkshire).
    res = browser(make_app()).get("/api/dining/nearest", params={"lat": 42.3932825, "lon": -72.5250891})
    assert res.status_code == 200
    body = res.json()
    assert body["location"]["name"] == WORCESTER
    assert body["on_campus"] is True
    assert body["note"] == f"Nearest dining hall found: {WORCESTER}. Change dining hall if this is wrong."
    assert 0 <= body["distance_m"] < 3000


def test_nearest_hall_far_from_campus_says_so(make_app):
    res = browser(make_app()).get("/api/dining/nearest", params={"lat": 42.36, "lon": -71.06})  # Boston
    body = res.json()
    assert res.status_code == 200 and body["on_campus"] is False
    assert body["note"].startswith("You don't look to be on campus")
    assert "Change dining hall if this is wrong." in body["note"]


def test_nearest_hall_validates_coordinates(make_app):
    client = browser(make_app())
    assert client.get("/api/dining/nearest", params={"lat": 91, "lon": 0}).status_code == 422
    assert client.get("/api/dining/nearest", params={"lat": "x", "lon": 0}).status_code == 422
    assert client.get("/api/dining/nearest").status_code == 422


def test_nearest_hall_needs_a_hall_with_a_menu(clock):
    from mealscholar.dining import nearest_hall

    halls_without_menus = [dict(loc, dates_available=[]) for loc in LOCATIONS]
    dining = Dining(FakeDining(locations=halls_without_menus), clock=clock)
    assert nearest_hall(dining.locations(), 42.39, -72.52) is None


def test_dish_search_drops_nul_bytes(make_app):
    client = browser(make_app())
    assert client.get("/api/dining/dishes", params={"q": "pi\x00zza"}).status_code == 200
    assert client.get("/api/dining/dishes", params={"q": "\x00\x00"}).json() == []


def test_a_hall_includes_its_grab_n_go_and_falls_back_to_dishes_it_has_served(clock):
    """Berkshire's dishes include Berkshire Grab N' Go's; on a day with no menu at either (an older
    photo, a weekend), the dishes they have served before stand in, most often seen first."""
    label = {"calories": 150, "nutrition": {"protein_g": 10.6, "total_carb_g": 5.2, "total_fat_g": 9.8}}
    locations = [
        {"_id": 4, "name": "Berkshire", "is_dining_commons": True},
        {"_id": 10666, "name": "Berkshire Grab N' Go", "is_dining_commons": False},
        {"_id": 10667, "name": "Worcester Grab N' Go", "is_dining_commons": False},
        {"_id": 7, "name": "Blue Wall", "is_dining_commons": False},
    ]
    items = [
        {"location_id": 4, "date": "2026-11-14", "name": "Tantanmen Ramen Bar", "meal": "dinner", **label},
        {"location_id": 10666, "date": "2026-11-14", "name": "Chicken Tikka Masala Rasoi", "meal": "lunch", **label},
        {"location_id": 10667, "date": "2026-11-14", "name": "Worcester Wrap", "meal": "lunch", **label},
    ]
    dishes = [
        {
            "_id": "a",
            "name": "Chicken Tikka Masala Rasoi",
            "location_ids": [1, 4, 10666],
            "occurrence_count": 8,
            **label,
        },
        {"_id": "b", "name": "Berkshire Burger", "location_ids": [4], "occurrence_count": 30, **label},
        {"_id": "c", "name": "Blue Wall Bowl", "location_ids": [7], "occurrence_count": 99, **label},
        {"_id": "d", "name": "No Label", "location_ids": [4], "occurrence_count": 50, "calories": None},
    ]
    dining = Dining(FakeDining(locations=locations, menus=[], dishes=dishes, items=items), clock=clock)

    today = dining.dishes_on(4, TODAY)
    assert [(d.meal, d.name) for d in today] == [
        ("dinner", "Tantanmen Ramen Bar"),
        ("lunch", "Chicken Tikka Masala Rasoi"),
    ]

    older = dining.dishes_on(4, TODAY - timedelta(days=38))
    assert [(d.meal, d.name) for d in older] == [
        (CATALOG_MEAL, "Berkshire Burger"),
        (CATALOG_MEAL, "Chicken Tikka Masala Rasoi"),
    ]
    assert older[1].per_serving["energy_kcal"] == 150
    assert dining.dishes_on(7, TODAY - timedelta(days=38))[0].name == "Blue Wall Bowl"  # no Grab N' Go to add
