"""Onboarding preferences over HTTP: defaults, validation, partial updates, sign-in and storage.

Everything runs in memory and MongoDB is never contacted; MongoPrefsStore's queries are checked
against a stand-in collection.
"""

import copy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pymongo import ReturnDocument

from foodsmart.auth import AuthService
from foodsmart.auth_store import MemoryAuthStore, utc_now
from foodsmart.config import load_settings
from foodsmart.dining import Dining
from foodsmart.prefs import (
    GOAL_MAX,
    LOCAL_USER,
    MemoryPrefsStore,
    MongoPrefsStore,
    NutrientGoals,
    Prefs,
    PrefsService,
    dish_key,
)
from foodsmart.web import create_app

PASSWORD = "correct horse battery"
SIGN_IN = {"detail": "Sign in to continue."}
NOT_AN_OBJECT = {"detail": "Send the preferences as a JSON object."}
TOO_LARGE = {"detail": "That request is too large."}
FDA_DAILY_VALUES = {"protein_g": 50, "carbs_g": 275, "fat_g": 78, "fiber_g": 28, "sugars_g": None, "sodium_mg": 2300}
DEFAULTS = {
    "onboarded": False,
    "calorie_goal": None,
    "nutrient_goals": FDA_DAILY_VALUES,
    "dining_halls": [],
    "diets": [],
    "avoid_allergens": [],
    "favorites": [],
    "notify": {"in_app": True, "email": False},
    "updated_at": None,
}


class HallsOnly:
    """A dining source that knows two locations and has no menus."""

    def locations(self):
        return [
            {"_id": 1, "name": "Worcester Dining Commons", "is_dining_commons": True, "dates_available": []},
            {"_id": 7, "name": "Blue Wall", "is_dining_commons": False, "dates_available": []},
        ]

    def daily_menus(self, ids, *, names_only=False):
        return []

    def dishes(self, pattern, limit):
        return []


@pytest.fixture
def make_app(tmp_path):
    """App factory: environment variables as keyword arguments; accounts and prefs in memory."""

    def make(*, prefs=None, dining=None, **env):
        settings = load_settings({"FOODSMART_HOME": str(tmp_path), **env})
        auth = AuthService.from_settings(settings, store=MemoryAuthStore())
        return create_app(settings, auth=auth, prefs=prefs or MemoryPrefsStore(), dining=dining)

    return make


def browser(app) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def register(client, email="sam@example.com") -> str:
    res = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert res.status_code == 201
    return res.json()["user"]["id"]


def patch(client, body):
    return client.patch("/api/me/prefs", json=body)


# --- reading and updating -----------------------------------------------------


def test_defaults_until_anything_is_saved(make_app):
    client = browser(make_app())
    register(client)
    res = client.get("/api/me/prefs")
    assert res.status_code == 200
    assert res.json() == DEFAULTS
    assert res.headers["cache-control"] == "no-store"


def test_patch_changes_only_what_it_sends(make_app):
    client = browser(make_app())
    register(client)

    before = utc_now()
    res = patch(client, {"calorie_goal": 2200, "nutrient_goals": {"protein_g": 120, "sugars_g": 36.27}})
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    prefs = res.json()
    goals = {**FDA_DAILY_VALUES, "protein_g": 120, "sugars_g": 36.3}
    assert prefs == {**DEFAULTS, "calorie_goal": 2200, "nutrient_goals": goals, "updated_at": prefs["updated_at"]}
    assert before <= datetime.fromisoformat(prefs["updated_at"]) <= utc_now()

    # nutrient_goals and notify merge into what is saved; null means no goal
    prefs = patch(client, {"notify": {"email": True}, "nutrient_goals": {"sodium_mg": None}}).json()
    assert prefs["notify"] == {"in_app": True, "email": True}
    assert prefs["nutrient_goals"] == {**goals, "sodium_mg": None}
    assert prefs["calorie_goal"] == 2200

    # lists replace what is saved; names are matched without case and kept in their usual order
    patch(client, {"dining_halls": [3, 4], "diets": ["Halal"]})
    prefs = patch(
        client,
        {
            "onboarded": True,
            "dining_halls": [7, 1, 7],
            "diets": ["  plant based", "Halal", "halal"],
            "avoid_allergens": ["tree nuts", "Milk"],
            "favorites": [{"name": "Pad Thai"}],
        },
    ).json()
    assert prefs["onboarded"] is True
    assert prefs["dining_halls"] == [7, 1]  # the first one is the user's main hall
    assert prefs["diets"] == ["Halal", "Plant Based"]
    assert prefs["avoid_allergens"] == ["Milk", "Tree Nuts"]
    assert prefs["favorites"] == [{"name": "Pad Thai"}]
    assert client.get("/api/me/prefs").json() == prefs

    cleared = patch(client, {"calorie_goal": None, "diets": [], "favorites": [], "dining_halls": []}).json()
    assert cleared == {
        **prefs,
        "calorie_goal": None,
        "diets": [],
        "favorites": [],
        "dining_halls": [],
        "updated_at": cleared["updated_at"],
    }


def test_what_get_returns_can_be_sent_back(make_app):
    """updated_at is the server's: a client may send it back, and it is ignored."""
    store = MemoryPrefsStore()
    client = browser(make_app(prefs=store))
    user_id = register(client)

    assert patch(client, {}).json() == DEFAULTS  # nothing to change, nothing saved
    assert patch(client, {"updated_at": "2020-01-01T00:00:00Z"}).json() == DEFAULTS
    assert store.get(user_id) is None

    saved = patch(client, {"onboarded": True, "favorites": [{"name": "Pad Thai"}], "notify": {"in_app": False}}).json()
    again = patch(client, saved)
    assert again.status_code == 200
    assert again.json() == {**saved, "updated_at": again.json()["updated_at"]}
    assert again.json()["updated_at"] >= saved["updated_at"]


def test_favorites_are_deduplicated_and_stored_with_a_key(make_app):
    store = MemoryPrefsStore()
    client = browser(make_app(prefs=store))
    user_id = register(client)

    tso, fullwidth_pad_thai = "General Tso\u2019s Chicken", "\uff30\uff41\uff44 Thai"  # a curly quote; wide letters
    names = [f"  {tso.replace(' ', '   ')} ", "general tso's chicken", fullwidth_pad_thai, "PAD THAI", "Pad thai"]
    res = patch(client, {"favorites": [{"name": name} for name in names]})
    assert res.json()["favorites"] == [{"name": tso}, {"name": fullwidth_pad_thai}]
    assert store.get(user_id)["favorites"] == [
        {"name": tso, "key": "general tso's chicken"},
        {"name": fullwidth_pad_thai, "key": "pad thai"},
    ]

    fifty_and_a_repeat = [{"name": f"Dish {i}"} for i in range(50)] + [{"name": "dish 0"}]
    assert len(patch(client, {"favorites": fifty_and_a_repeat}).json()["favorites"]) == 50  # repeats don't count


def test_dish_key():
    assert dish_key("  General Tso\u2019s   CHICKEN ") == "general tso's chicken"
    assert dish_key("\uff30\uff41\uff44 Thai") == "pad thai"
    assert dish_key("Crème Brûlée") == dish_key("Cre\u0300me Bru\u0302le\u0301e")
    assert dish_key("\u201cSpecial\u201d \u2013 Soup") == '"special" - soup'


# --- validation ---------------------------------------------------------------

CALORIES = "Choose a calorie goal from 500 to 6,000, or none."
HALL_IDS = "Send dining_halls as a list of location ids."
DIETS = "Choose diets from Halal, Local, Plant Based and Vegetarian."
ALLERGENS = (
    "Choose allergens from Corn, Milk, Soy, Gluten, Wheat, Eggs, Sesame, Fish, Tree Nuts, Shellfish and Peanuts."
)
FAVORITES = 'Send favorites as a list of {"name": ...} objects.'
FAVORITE_LENGTH = "Keep each favorite between 2 and 120 characters."


@pytest.mark.parametrize(
    ("body", "field", "detail"),
    [
        ({"onboarded": "yes"}, "onboarded", "Set onboarded to true or false."),
        ({"onboarded": 1}, "onboarded", "Set onboarded to true or false."),
        ({"onboarded": None}, "onboarded", "Set onboarded to true or false."),
        ({"calorie_goal": 499}, "calorie_goal", CALORIES),
        ({"calorie_goal": 6001}, "calorie_goal", CALORIES),
        ({"calorie_goal": 2000.5}, "calorie_goal", CALORIES),
        ({"calorie_goal": "2000"}, "calorie_goal", CALORIES),
        ({"calorie_goal": True}, "calorie_goal", CALORIES),
        ({"nutrient_goals": [50]}, "nutrient_goals", "Send nutrient_goals as an object."),
        ({"nutrient_goals": None}, "nutrient_goals", "Send nutrient_goals as an object."),
        (
            {"nutrient_goals": {"protein_g": -1}},
            "nutrient_goals.protein_g",
            "Choose a protein goal from 0 to 400 g, or none.",
        ),
        (
            {"nutrient_goals": {"carbs_g": 1000.1}},
            "nutrient_goals.carbs_g",
            "Choose a carbohydrate goal from 0 to 1,000 g, or none.",
        ),
        ({"nutrient_goals": {"fat_g": "lots"}}, "nutrient_goals.fat_g", "Choose a fat goal from 0 to 400 g, or none."),
        (
            {"nutrient_goals": {"fiber_g": True}},
            "nutrient_goals.fiber_g",
            "Choose a fiber goal from 0 to 150 g, or none.",
        ),
        (
            {"nutrient_goals": {"sugars_g": 401}},
            "nutrient_goals.sugars_g",
            "Choose a total sugars goal from 0 to 400 g, or none.",
        ),
        (
            {"nutrient_goals": {"sodium_mg": 10_001}},
            "nutrient_goals.sodium_mg",
            "Choose a sodium goal from 0 to 10,000 mg, or none.",
        ),
        (
            {"nutrient_goals": {"vitamin_c_mg": 90}},
            "nutrient_goals.vitamin_c_mg",
            "Unknown field: nutrient_goals.vitamin_c_mg.",
        ),
        ({"nutrient_goals": {"$gt": 1}}, "nutrient_goals", "Unknown field."),
        ({"dining_halls": 1}, "dining_halls", HALL_IDS),
        ({"dining_halls": [1, "2"]}, "dining_halls", HALL_IDS),
        ({"dining_halls": [0]}, "dining_halls", HALL_IDS),
        ({"dining_halls": [1.0]}, "dining_halls", HALL_IDS),
        ({"dining_halls": [True]}, "dining_halls", HALL_IDS),
        ({"dining_halls": [2**31]}, "dining_halls", HALL_IDS),
        ({"dining_halls": [{"$ne": 0}]}, "dining_halls", HALL_IDS),
        ({"dining_halls": list(range(1, 12))}, "dining_halls", "Choose up to 10 dining locations."),
        ({"diets": ["Keto"]}, "diets", DIETS),
        ({"diets": "Halal"}, "diets", DIETS),
        ({"diets": [None]}, "diets", DIETS),
        ({"avoid_allergens": ["Nuts"]}, "avoid_allergens", ALLERGENS),
        ({"avoid_allergens": [["Milk"]]}, "avoid_allergens", ALLERGENS),
        ({"favorites": "Pad Thai"}, "favorites", FAVORITES),
        ({"favorites": ["Pad Thai"]}, "favorites", FAVORITES),
        ({"favorites": [{}]}, "favorites", FAVORITES),
        ({"favorites": [{"name": "Pad Thai", "key": "x"}]}, "favorites", FAVORITES),
        ({"favorites": [{"name": {"$ne": ""}}]}, "favorites", FAVORITES),
        ({"favorites": [{"name": "P"}]}, "favorites", FAVORITE_LENGTH),
        ({"favorites": [{"name": "  P \n "}]}, "favorites", FAVORITE_LENGTH),
        ({"favorites": [{"name": "x" * 121}]}, "favorites", FAVORITE_LENGTH),
        ({"favorites": [{"name": "x" * 5000}]}, "favorites", FAVORITE_LENGTH),
        ({"favorites": [{"name": "Pad\x00Thai"}]}, "favorites", "A favorite has characters that can't be used."),
        ({"favorites": [{"name": f"Dish {i}"} for i in range(51)]}, "favorites", "Keep your favorites to 50 or fewer."),
        ({"notify": True}, "notify", "Send notify as an object."),
        ({"notify": {"email": "yes"}}, "notify.email", "Set notify.email to true or false."),
        ({"notify": {"in_app": None}}, "notify.in_app", "Set notify.in_app to true or false."),
        ({"notify": {"sms": True}}, "notify.sms", "Unknown field: notify.sms."),
        ({"theme": "dark"}, "theme", "Unknown field: theme."),
        ({"email": "someone@example.com"}, "email", "Unknown field: email."),
        ({"$set": {"onboarded": True}}, None, "Unknown field."),  # odd names aren't repeated back
        ({"x" * 41: 1}, None, "Unknown field."),
    ],
)
def test_patch_says_which_field_is_wrong(make_app, body, field, detail):
    client = browser(make_app())
    register(client)
    res = patch(client, body)
    assert res.status_code == 400
    assert res.json() == ({"detail": detail, "field": field} if field else {"detail": detail})
    assert client.get("/api/me/prefs").json() == DEFAULTS


@pytest.mark.parametrize(
    ("content", "field"),
    [
        (b'{"nutrient_goals": {"protein_g": NaN}}', "nutrient_goals.protein_g"),
        (b'{"nutrient_goals": {"protein_g": Infinity}}', "nutrient_goals.protein_g"),
        (b'{"nutrient_goals": {"protein_g": 1e999}}', "nutrient_goals.protein_g"),
        (b'{"favorites": [{"name": "Pad \\ud800 Thai"}]}', "favorites"),  # a lone surrogate
    ],
)
def test_numbers_and_text_json_allows_but_prefs_dont(make_app, content, field):
    client = browser(make_app())
    register(client)
    res = client.patch("/api/me/prefs", content=content, headers={"Content-Type": "application/json"})
    assert res.status_code == 400
    assert res.json()["field"] == field


def test_a_rejected_patch_saves_nothing(make_app):
    client = browser(make_app())
    register(client)
    saved = patch(client, {"calorie_goal": 2000, "diets": ["Halal"]}).json()
    res = patch(client, {"calorie_goal": 2500, "favorites": [{"name": "Pad Thai"}], "diets": ["Keto"]})
    assert res.status_code == 400 and res.json()["field"] == "diets"
    assert client.get("/api/me/prefs").json() == saved


@pytest.mark.parametrize(
    "content",
    [b"[]", b'"onboarded"', b"null", b"7", b"{", b"", b"\xff\xfe\xfd", b"[" * 5000 + b"]" * 5000],
)
def test_patch_needs_a_json_object(make_app, content):
    client = browser(make_app())
    register(client)
    res = client.patch("/api/me/prefs", content=content, headers={"Content-Type": "application/json"})
    assert res.status_code == 400
    assert res.json() == NOT_AN_OBJECT


def test_patch_bodies_are_capped(make_app):
    client = browser(make_app())
    register(client)
    parts = [b'{"favorites": [', b'{"name": "Pad Thai"}, ' * 2000, b'{"name": "Pad Thai"}]}']
    res = client.patch("/api/me/prefs", content=b"".join(parts), headers={"Content-Type": "application/json"})
    assert res.status_code == 413 and res.json() == TOO_LARGE
    chunks = iter(parts)  # sent without Content-Length
    res = client.patch("/api/me/prefs", content=chunks, headers={"Content-Type": "application/json"})
    assert res.status_code == 413 and res.json() == TOO_LARGE
    assert client.get("/api/me/prefs").json() == DEFAULTS


def test_dining_halls_must_exist_when_menus_are_connected(make_app):
    client = browser(make_app(dining=Dining(HallsOnly())))
    register(client)
    res = patch(client, {"dining_halls": [1, 999]})
    assert res.status_code == 400
    assert res.json() == {"detail": "Unknown dining location: 999.", "field": "dining_halls"}
    assert patch(client, {"dining_halls": [7, 1]}).json()["dining_halls"] == [7, 1]

    offline = browser(make_app())  # without the menus, ids can't be checked, so any is kept
    register(offline)
    assert patch(offline, {"dining_halls": [999]}).json()["dining_halls"] == [999]


def test_goal_limits_cover_every_goal():
    assert set(GOAL_MAX) == set(NutrientGoals.model_fields)


# --- who sees what ------------------------------------------------------------


def test_prefs_need_a_session(make_app):
    client = browser(make_app())
    for res in (client.get("/api/me/prefs"), patch(client, {"onboarded": True})):
        assert res.status_code == 401
        assert res.json() == SIGN_IN


def test_without_required_sign_in_the_local_user_has_prefs(make_app):
    store = MemoryPrefsStore()
    client = browser(make_app(prefs=store, FOODSMART_AUTH_REQUIRED="0"))
    assert client.get("/api/me/prefs").json() == DEFAULTS
    assert patch(client, {"calorie_goal": 1800}).json()["calorie_goal"] == 1800
    assert store.get(LOCAL_USER)["calorie_goal"] == 1800

    user_id = register(client)  # signing in still works, and then the account's own prefs are used
    assert client.get("/api/me/prefs").json() == DEFAULTS
    patch(client, {"calorie_goal": 2400})
    assert (store.get(user_id)["calorie_goal"], store.get(LOCAL_USER)["calorie_goal"]) == (2400, 1800)


def test_users_only_see_their_own_prefs(make_app):
    app = make_app()
    sam, alex = browser(app), browser(app)
    register(sam, "sam@example.com")
    register(alex, "alex@example.com")

    mine = patch(sam, {"favorites": [{"name": "Pad Thai"}], "notify": {"email": True}}).json()
    assert alex.get("/api/me/prefs").json() == DEFAULTS
    patch(alex, {"calorie_goal": 3000})
    assert sam.get("/api/me/prefs").json() == mine


def test_cross_site_patches_are_refused(make_app):
    client = browser(make_app())
    register(client)
    res = client.patch("/api/me/prefs", json={"onboarded": True}, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    assert client.get("/api/me/prefs").json()["onboarded"] is False


# --- storage ------------------------------------------------------------------


def test_memory_store_sets_dotted_paths_and_lists_email_subscribers():
    store = MemoryPrefsStore()
    assert store.get("u1") is None
    assert store.update("u1", {"notify.email": True, "nutrient_goals.protein_g": 90.0}) == {
        "notify": {"email": True},
        "nutrient_goals": {"protein_g": 90.0},
    }
    store.update("u1", {"notify.in_app": False, "favorites": [{"name": "Pad Thai", "key": "pad thai"}]})
    store.update("u2", {"notify.email": False})
    store.update("u3", {"onboarded": True})
    assert [user_id for user_id, _ in store.with_email_alerts()] == ["u1"]

    store.get("u1")["notify"]["email"] = False  # callers get copies
    prefs = Prefs.model_validate(store.get("u1"))
    assert (prefs.notify.email, prefs.notify.in_app, prefs.nutrient_goals.protein_g) == (True, False, 90.0)
    assert prefs.nutrient_goals.carbs_g == 275  # unset goals keep their defaults
    assert [(f.name, f.key) for f in prefs.favorites] == [("Pad Thai", "pad thai")]


class RecordingCollection:
    """Stands in for the user_prefs collection and records each call."""

    def __init__(self, docs=()):
        self.docs = list(docs)
        self.calls = []

    def create_index(self, keys, **options):
        self.calls.append(("create_index", keys))

    def find_one(self, query, projection=None):
        self.calls.append(("find_one", query, projection))
        return None

    def find_one_and_update(self, query, update, **options):
        self.calls.append(("find_one_and_update", query, update, options))
        return {"onboarded": True}

    def find(self, query, projection=None):
        self.calls.append(("find", query, projection))
        return iter(copy.deepcopy(self.docs))


def test_mongo_store_queries():
    """Only the user id string and the checked $set reach MongoDB."""
    collection = RecordingCollection([{"_id": "u1", "notify": {"email": True}}])
    store = MongoPrefsStore({"user_prefs": collection})
    store.ensure_indexes()
    service = PrefsService(store, clock=lambda: datetime(2026, 11, 14, 17, 0))
    assert service.get("u1") == Prefs()
    body = {"favorites": [{"name": " Pad  Thai "}], "notify": {"email": True}, "nutrient_goals": {"fat_g": 70}}
    assert service.update("u1", body).onboarded is True
    assert store.with_email_alerts() == [("u1", {"notify": {"email": True}})]
    with pytest.raises(TypeError):
        store.get({"$ne": None})

    assert collection.calls == [
        ("create_index", "notify.email"),
        ("find_one", {"_id": "u1"}, {"_id": False}),
        (
            "find_one_and_update",
            {"_id": "u1"},
            {
                "$set": {
                    "favorites": [{"name": "Pad Thai", "key": "pad thai"}],
                    "notify.email": True,
                    "nutrient_goals.fat_g": 70.0,
                    "updated_at": datetime(2026, 11, 14, 17, 0),
                }
            },
            {"projection": {"_id": False}, "upsert": True, "return_document": ReturnDocument.AFTER},
        ),
        ("find", {"notify.email": True}, None),
    ]


def test_dining_database_setting():
    assert load_settings({}).dining_db == "umass_dining"
    assert load_settings({"FOODSMART_DINING_DB": "umass_dining_copy"}).dining_db == "umass_dining_copy"
