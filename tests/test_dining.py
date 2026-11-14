"""Dining menus over HTTP from a fake umass_dining source: locations, menus, the next meal by
typical hours, dish search, the user's flags and alerts, the favorites email and its command.

MongoDB is never contacted; MongoDiningSource's queries are checked against stand-in collections.
"""

import copy
import hashlib
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from foodsmart import cli
from foodsmart.auth import AuthService
from foodsmart.auth_store import MemoryAuthStore, utc_now
from foodsmart.config import load_settings
from foodsmart.dining import (
    MEALS,
    Alert,
    DigestReport,
    Dining,
    MongoDiningSource,
    favorites_email,
    send_favorites_digests,
    starts_hint,
)
from foodsmart.mailer import OutboxSender, SmtpSender
from foodsmart.prefs import LOCAL_USER, MemoryPrefsStore, Prefs, PrefsService
from foodsmart.web import create_app

EASTERN = ZoneInfo("America/New_York")
YESTERDAY, TODAY, TOMORROW, NEXT_DAY = (date(2026, 11, 14) + timedelta(days=n) for n in (-1, 0, 1, 2))
PASSWORD = "correct horse battery"
SIGN_IN = {"detail": "Sign in to continue."}
SUBJECT = "Your favorites are on the menu"
NOT_CONNECTED = {"detail": "Dining menus aren't connected."}
UNAVAILABLE = {"detail": "Dining menus aren't available right now. Try again in a minute."}


# --- the fake umass_dining database -------------------------------------------


def item(name, kcal, protein, carbs, fat, allergens=(), diets=(), *, serving="1 serving", price=None):
    """A daily_menus item as the scraper writes it."""
    return {
        "dish_id": hashlib.sha1(name.encode()).hexdigest(),
        "name": name,
        "serving_size": serving,
        "price": price,
        "calories": kcal,
        "protein_g": protein,
        "total_carb_g": carbs,
        "total_fat_g": fat,
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
TIKKA = item("Chicken Tikka Masala", 320, 24, 14, 18, ["Milk"], ["Halal"])
CHILI = item("Vegan Chili", 240, 11, 38, 5, ["Soy"], ["Plant Based", "Vegetarian", "Local"])
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

    def __init__(self, locations=LOCATIONS, menus=MENUS, dishes=DISHES):
        self._locations = locations
        self._menus = {doc["_id"]: doc for doc in menus}
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

    def dishes(self, pattern, limit):
        self._read("dishes")
        regex = re.compile(pattern, re.IGNORECASE)  # as {"$regex": pattern, "$options": "i"}
        found = [dish for dish in self._dishes if regex.search(dish["name"])]
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
        settings = load_settings({"FOODSMART_HOME": str(tmp_path), **env})
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
        },
        {
            "id": 1,
            "name": WORCESTER,
            "area": "Northeast",
            "is_dining_commons": True,
            "has_prices": False,
            "next_date": "2026-11-14",
        },
        {
            "id": 7,
            "name": BLUE_WALL,
            "area": "Campus Center",
            "is_dining_commons": False,
            "has_prices": True,
            "next_date": "2026-11-15",
        },
    ]
    clock.at(9, day=NEXT_DAY)
    assert [loc["id"] for loc in client.get("/api/dining/locations").json()] == [7]


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

    local = browser(make_app(FOODSMART_AUTH_REQUIRED="0"))
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


def test_digest_emails_confirmed_users_with_email_alerts_on(source, clock):
    users, prefs = accounts_with_prefs()
    dining = Dining(source, clock=clock)
    report = send_favorites_digests(users, prefs, dining, OutboxSender(users), "https://meals.example", days=1)
    assert report == DigestReport(users=4, queued=1, failed=0)
    (email,) = users.recent_emails(10)
    assert (email.to, email.subject, email.purpose) == ("sam@example.com", SUBJECT, "favorites_digest")
    assert (email.status, email.provider) == ("queued", "outbox")
    assert email.expires_at - email.created_at == timedelta(days=7)
    assert "Sunday, November 15\n  Dinner at Berkshire Dining Commons: Pad Thai\n" in email.text

    failing = send_favorites_digests(users, prefs, dining, SmtpSender(), "https://meals.example", days=1)
    assert failing == DigestReport(users=4, queued=0, failed=1)

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
    monkeypatch.setenv("FOODSMART_HOME", str(tmp_path))
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setenv("FOODSMART_PUBLIC_URL", "https://meals.example/")
    monkeypatch.setenv("FOODSMART_EMAIL_PROVIDER", "outbox")

    cli.main(["alerts-digest", "--days", "2"])
    assert capsys.readouterr().out == "Queued 1 favorites email(s) for 4 user(s) with email alerts on.\n"
    (email,) = users.recent_emails(10)
    assert email.to == "sam@example.com"
    assert "See the menus: https://meals.example/\n" in email.text


def test_alerts_digest_command_needs_mongodb(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("FOODSMART_HOME", str(tmp_path))
    monkeypatch.delenv("MONGODB_URI", raising=False)
    for argv, error in (
        (["alerts-digest"], "alerts-digest needs MONGODB_URI: accounts, preferences and menus are in MongoDB."),
        (["alerts-digest", "--days", "15"], "--days must be from 1 to 14."),
    ):
        with pytest.raises(SystemExit) as stopped:
            cli.main(argv)
        assert stopped.value.code == 1
        assert capsys.readouterr().err == f"error: {error}\n"


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
    db = {"locations": StubCollection(LOCATIONS), "daily_menus": StubCollection(MENUS[1:3]), "dishes": StubCollection()}
    dining = Dining(MongoDiningSource(db), clock=clock)

    assert dining.menu(1, TODAY).location.name == WORCESTER
    dining.dishes("Mac & Cheese (GF)", 5)
    dining.alerts(Prefs.model_validate({"favorites": [{"name": "Pad Thai"}], "dining_halls": [1]}), 1)

    (full, full_fields), (names, names_fields) = db["daily_menus"].finds
    assert full == {"_id": {"$in": ["1|2026-11-14"]}}
    assert names == {"_id": {"$in": ["1|2026-11-14", "1|2026-11-15"]}}
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
    from foodsmart.dining import nearest_hall

    halls_without_menus = [dict(loc, dates_available=[]) for loc in LOCATIONS]
    dining = Dining(FakeDining(locations=halls_without_menus), clock=clock)
    assert nearest_hall(dining.locations(), 42.39, -72.52) is None


def test_dish_search_drops_nul_bytes(make_app):
    client = browser(make_app())
    assert client.get("/api/dining/dishes", params={"q": "pi\x00zza"}).status_code == 200
    assert client.get("/api/dining/dishes", params={"q": "\x00\x00"}).json() == []
