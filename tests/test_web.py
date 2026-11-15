import io
import logging
import struct
import zlib
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_metadata import IPHONE_HEIC, heic

import mealscholar.web
from mealscholar.auth import AuthService, RateLimiter, Rule
from mealscholar.auth_store import MemoryAuthStore
from mealscholar.config import load_settings
from mealscholar.dining import Dining
from mealscholar.insights import SCORE_MINUS, SCORE_PLUS
from mealscholar.schemas import FoodItem, MealAnalysis
from mealscholar.vision import VisionError
from mealscholar.web import create_app


class FailingVision:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc

    def analyze(self, jpeg, context, menu_names=()):
        raise self.exc or AssertionError("the photo should have been refused before Gemini")

    def refine(self, analysis, question, answer, menu_names=()):
        raise AssertionError("not used")


class DisplayCaseVision:
    """What Gemini said for plastic display food (#49): dishes and a question, but not a meal."""

    def analyze(self, jpeg, context, menu_names=()):
        crepe = FoodItem(
            name="crepe",
            fdc_query="Crepe, plain",
            cooking_method="unknown",
            grams_low=120,
            grams_likely=160,
            grams_high=200,
            box_2d=None,
            hidden_fat_likely=False,
        )
        question = "Which of these items from the display case are you eating?"
        return MealAnalysis(is_food=False, items=[crepe], scale_reference=None, question=question)

    def refine(self, analysis, question, answer, menu_names=()):
        raise AssertionError("not used")


class CampusVision:
    """Gemini with Worcester's menu in the prompt: the rice is the listed Jasmine Rice. Remembers
    the dishes it was allowed to name, call by call."""

    def __init__(self):
        self.menu_names = []

    def analyze(self, jpeg, context, menu_names=()):
        self.menu_names.append(list(menu_names))
        rice = FoodItem(
            name="white rice",
            fdc_query="Rice, white, cooked",
            cooking_method="steamed",
            grams_low=150,
            grams_likely=200,
            grams_high=260,
            box_2d=None,
            hidden_fat_likely=False,
            menu_item="Jasmine Rice",
        )
        return MealAnalysis(is_food=True, items=[rice], scale_reference=None, question=None)

    def refine(self, analysis, question, answer, menu_names=()):
        raise AssertionError("not used")


class SliceVision:
    """Gemini with the menu in the prompt: one or two slices of the listed Cheese Pizza."""

    def analyze(self, jpeg, context, menu_names=()):
        pizza = FoodItem(
            name="pizza slice",
            fdc_query="Pizza, cheese, from restaurant or fast food, thin crust",
            cooking_method="baked",
            grams_low=100,
            grams_likely=120,
            grams_high=150,
            box_2d=None,
            hidden_fat_likely=False,
            menu_item="Cheese Pizza",
            servings_low=1,
            servings_likely=1.5,
            servings_high=2,
        )
        return MealAnalysis(is_food=True, items=[pizza], scale_reference=None, question=None)

    def refine(self, analysis, question, answer, menu_names=()):
        raise AssertionError("not used")


def menu_doc(name, dish_id, serving, kcal, nutrition, day):
    return {
        "location_id": 1,
        "date": day,
        "name": name,
        "meal": "lunch",
        "category": None,
        "dish_id": dish_id,
        "serving_size": serving,
        "calories": kcal,
        "nutrition": nutrition,
        "healthfulness_score": 5,
    }


class MenuSource:
    """The umass_dining collections as the pipeline needs them: one hall, Jasmine Rice every day
    and, with `pizza`, a Cheese Pizza listed per slice."""

    def __init__(self, *, pizza=False):
        self.pizza = pizza

    def locations(self):
        return [{"_id": 1, "name": "Worcester Dining Commons", "is_dining_commons": True, "dates_available": []}]

    def daily_menus(self, ids, *, names_only=False):
        return []

    def menu_items(self, location_id, day):
        if location_id != 1:
            return []
        rice = {"protein_g": 4, "total_carb_g": 41, "total_fat_g": 0, "sodium_mg": 5}
        docs = [menu_doc("Jasmine Rice", "d1", "4 OZ", 186, rice, day)]
        if self.pizza:
            pizza = {"protein_g": 9, "total_carb_g": 26, "total_fat_g": 8}
            docs.append(menu_doc("Cheese Pizza", "d2", "1 Slice", 212, pizza, day))
        return docs

    def menu_items_between(self, location_ids, start, end):
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        return [doc for i in location_ids for day in days for doc in self.menu_items(i, day.isoformat())]

    def dishes(self, pattern, limit):
        return []

    def catalog(self, location_ids, limit):
        return []


class NoPlaces:
    def at(self, latitude, longitude):
        return None


def client(home, vision, *, dining=None, places=None) -> TestClient:
    settings = load_settings({"MEALSCHOLAR_HOME": str(home), "MEALSCHOLAR_AUTH_REQUIRED": "0"})
    app = create_app(settings, vision=vision, dining=dining, places=places)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def home(tmp_path, food_db_path):
    (tmp_path / "fdc.sqlite").symlink_to(food_db_path)
    return tmp_path


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "tan").save(buf, "JPEG")
    return buf.getvalue()


def png_claiming(width: int, height: int) -> bytes:
    """A tiny PNG whose header claims a huge size, as a decompression bomb would."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\0")) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    ("path", "status"),
    [("/", 200), ("/styles.css", 200), ("/api/days/2026-11-14", 401), ("/missing-page", 404)],
)
def test_pages_and_api_errors_have_browser_protections(tmp_path, path, status):
    app = TestClient(create_app(load_settings({"MEALSCHOLAR_HOME": str(tmp_path)})))
    response = app.get(path)
    assert response.status_code == status
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    ("photo", "status"),
    [
        (png_claiming(40_000, 40_000), 413),
        (png_claiming(10_000, 6_000), 413),  # below Pillow's default warning threshold
        (jpeg()[:300], 415),  # cut off mid-upload
        (b"not an image at all", 415),
    ],
    ids=["Pillow decompression bomb", "over 50 megapixels", "truncated", "not an image"],
)
def test_photos_that_wont_decode_get_a_client_error(home, photo, status):
    res = client(home, FailingVision()).post("/api/meals", content=photo)
    assert res.status_code == status
    assert "detail" in res.json()


def test_gemini_failure_reply_hides_the_providers_error(home, caplog):
    raw = 'Gemini request failed: 403 PERMISSION_DENIED {"message": "API key not valid"}'
    with caplog.at_level(logging.WARNING, logger="mealscholar.web"):
        app = client(home, FailingVision(VisionError(raw)))
        res = app.post("/api/meals?lat=42.389123&lon=-72.528456", content=jpeg())
    assert res.status_code == 502
    assert "PERMISSION_DENIED" not in res.text and "API key" not in res.text
    assert "PERMISSION_DENIED" in caplog.text  # still there for whoever runs the server
    assert "42.38" not in caplog.text  # the log line names the path, not the query


def test_missing_food_database_reply_hides_the_server_path(tmp_path):
    res = client(tmp_path, FailingVision()).post("/api/meals", content=jpeg())
    assert res.status_code == 503
    assert str(tmp_path) not in res.text and "fdc.sqlite" not in res.text


def test_a_photo_that_is_not_a_meal_is_not_logged(home):
    app = client(home, DisplayCaseVision())
    res = app.post("/api/meals", content=jpeg())
    assert res.status_code == 200
    meal = res.json()
    assert (meal["id"], meal["items"], meal["totals"], meal["question"]) == (None, [], {}, None)
    assert meal["notes"] == ["No food or drink detected in the photo."]
    day = app.get(f"/api/days/{meal['context']['taken_at'][:10]}").json()
    assert day["meals"] == [] and day["health"] is None


def test_day_view_carries_the_score(home):
    app = client(home, CampusVision())
    meal = app.post("/api/meals", content=jpeg()).json()
    day = app.get(f"/api/days/{meal['context']['taken_at'][:10]}").json()
    assert len(day["meals"]) == 1
    health = day["health"]
    assert isinstance(health["score"], int) and 0 <= health["score"] <= 100
    expected = [*SCORE_PLUS, *SCORE_MINUS, "sugars_g"]
    assert [p["key"] for p in health["parts"]] == expected
    assert health["parts"][0]["plus"] is True and health["parts"][-1]["plus"] is False
    assert health["note"] == "A rough guide, not medical advice."


# --- when the meal happened: EXIF from a HEIC, then the phone's clock, then the upload -----


def on_day(app, day: str, meal_id: int) -> bool:
    return meal_id in [m["id"] for m in app.get(f"/api/days/{day}").json()["meals"]]


def test_heic_upload_is_dated_from_its_exif(home):
    app = client(home, CampusVision())
    res = app.post("/api/meals", content=heic(**IPHONE_HEIC), headers={"Content-Type": "image/heic"})
    assert res.status_code == 200
    meal = res.json()
    assert meal["context"]["time_source"] == "exif"
    assert meal["context"]["taken_at"].startswith("2026-11-13T12:34:56-05:00")
    assert meal["context"]["place"] is None  # photo GPS stays opt-in, so no place lookup
    assert on_day(app, "2026-11-13", meal["id"])


def test_taken_at_dates_a_photo_without_exif(home):
    app = client(home, CampusVision())
    res = app.post("/api/meals?taken_at=2026-11-13T23:50:00%2B09:00", content=jpeg())
    assert res.status_code == 200
    meal = res.json()
    assert meal["context"]["time_source"] == "device"
    assert meal["context"]["taken_at"] == "2026-11-13T23:50:00+09:00"
    assert not any("using upload time" in n for n in meal["notes"])
    assert on_day(app, "2026-11-13", meal["id"]) and not on_day(app, "2026-11-14", meal["id"])


def test_taken_at_in_utc_is_accepted_and_filed_under_that_clock(home):
    app = client(home, CampusVision())
    res = app.post("/api/meals?taken_at=2026-11-13T23:50:00Z", content=jpeg())
    assert res.status_code == 200
    meal = res.json()
    assert meal["context"]["time_source"] == "device"
    assert meal["context"]["taken_at"].endswith(("+00:00", "Z"))
    assert on_day(app, "2026-11-13", meal["id"])  # UTC's date, not the phone's: send local time


def test_taken_at_without_an_offset_is_refused(home):
    app = client(home, CampusVision())
    res = app.post("/api/meals?taken_at=2026-11-13T23:50:00", content=jpeg())
    assert res.status_code == 400
    assert res.json()["detail"] == "taken_at needs a UTC offset, such as 2026-11-14T12:30:00-05:00."
    assert app.post("/api/meals?taken_at=2026-11-13T23:50:00+09:00", content=jpeg()).status_code == 422


def test_exif_time_beats_taken_at(home):
    app = client(home, CampusVision())
    res = app.post("/api/meals?taken_at=2026-11-14T08:00:00-05:00", content=heic(**IPHONE_HEIC))
    assert res.status_code == 200
    assert res.json()["context"]["time_source"] == "exif"
    assert res.json()["context"]["taken_at"].startswith("2026-11-13T12:34:56")


# --- campus matching through the API: only an explicit location_id switches it on -----


def test_campus_match_is_in_the_api_reply(home):
    app = client(home, CampusVision(), dining=Dining(MenuSource()))
    res = app.post("/api/meals?location_id=1", content=jpeg())
    assert res.status_code == 200
    meal = res.json()
    (rice,) = meal["items"]
    assert (rice["source"], rice["dish"]["name"], rice["dish"]["serving_size"]) == ("campus", "Jasmine Rice", "4 OZ")
    assert rice["match"]["fdc_id"] == 1 and rice["dish"]["healthfulness_score"] == 5
    assert rice["nutrients"]["energy_kcal"]["likely"] == pytest.approx(186 * 200 / 113.4)
    assert rice["nutrients"]["energy_kcal"]["low"] < rice["nutrients"]["energy_kcal"]["high"]
    assert (meal["menu"]["location_id"], meal["menu"]["location_name"]) == (1, "Worcester Dining Commons")
    assert meal["menu"]["dishes"] == 1 and meal["name"] == "white rice"
    # No EXIF time: the menus of the days up to the upload's, which the reply names. Every day here
    # serves the same rice, so only the guessed day's was shown.
    assert meal["menu"]["certain"] is False and meal["menu"]["start"] == meal["menu"]["end"] == meal["menu"]["date"]
    assert rice["dish"]["date"] == meal["menu"]["date"] and rice["dish"]["meal"] == "lunch"
    assert any(n.startswith("The photo had no date, so it was matched against Worcester") for n in meal["notes"])
    assert app.get(f"/api/meals/{meal['id']}").json() == meal

    assert app.post("/api/meals?location_id=0", content=jpeg()).status_code == 422


def test_servings_and_grams_corrections_on_campus_items(home):
    app = client(home, SliceVision(), dining=Dining(MenuSource(pizza=True)))
    meal = app.post("/api/meals?location_id=1", content=jpeg()).json()
    (slice_,) = meal["items"]
    assert (slice_["source"], slice_["dish"]["serving_size"]) == ("campus", "1 Slice")
    assert slice_["dish"]["serving_grams"] is None
    assert meal["totals"]["energy_kcal"]["low"] < meal["totals"]["energy_kcal"]["high"]
    url = f"/api/meals/{meal['id']}/items/0"

    res = app.patch(url, json={"servings": 2})
    assert res.status_code == 200
    kcal = res.json()["totals"]["energy_kcal"]
    assert kcal["low"] == kcal["likely"] == kcal["high"] == pytest.approx(424)
    assert res.json()["items"][0]["source"] == "campus"
    counted = res.json()["items"][0]["item"]
    assert counted["servings_likely"] == 2
    # The gram range follows the count (1.5 slices were 100/120/150 g) so the micros track it.
    assert (counted["grams_low"], counted["grams_likely"], counted["grams_high"]) == (133, 160, 200)
    assert res.json()["items"][0]["nutrients"]["vitamin_d_ug"]["likely"] == pytest.approx(0.2 * 1.6)
    assert app.get(f"/api/meals/{meal['id']}").json() == res.json()  # the diary keeps the same pair

    res = app.patch(url, json={"grams": 100})
    assert res.status_code == 200
    (slice_,) = res.json()["items"]
    assert (slice_["source"], slice_["dish"], slice_["match"]["fdc_id"]) == ("usda", None, 6)
    assert slice_["item"]["menu_item"] is None and slice_["item"]["servings_likely"] is None
    assert slice_["nutrients"]["energy_kcal"]["likely"] == pytest.approx(266)
    note = (
        "'pizza slice' has an amount in grams now, so USDA numbers are used; the menu lists Cheese Pizza per 1 Slice."
    )
    assert note in res.json()["notes"]
    assert not any("weigh" in n for n in res.json()["notes"])
    assert app.get(f"/api/meals/{meal['id']}").json() == res.json()

    res = app.patch(url, json={"servings": 1})
    assert (res.status_code, res.json()["detail"]) == (400, "This food isn't a campus dish; send grams.")
    res = app.patch(url, json={})
    assert (res.status_code, res.json()["detail"]) == (400, "Send grams, servings and/or fdc_id.")
    assert app.patch(url, json={"servings": 0}).status_code == 422
    assert app.patch(url, json={"servings": 51}).status_code == 422


def test_without_menus_location_id_is_a_note(home):
    app = client(home, CampusVision(), dining=None)
    res = app.post("/api/meals?location_id=1", content=jpeg())
    assert res.status_code == 200
    meal = res.json()
    assert "Campus menus aren't connected; USDA used." in meal["notes"]
    assert meal["items"][0]["source"] == "usda" and meal["menu"] is None


def test_lat_lon_alone_never_matches(home):
    app = client(home, CampusVision(), dining=Dining(MenuSource()), places=NoPlaces())
    res = app.post("/api/meals?lat=42.3932825&lon=-72.5250891", content=jpeg())
    assert res.status_code == 200
    meal = res.json()
    assert all(r["source"] == "usda" and r["dish"] is None for r in meal["items"])
    assert meal["menu"] is None and not any("Campus" in n for n in meal["notes"])


# --- POST /api/meals/{id}/menu: the user names the hall and day, the stored photo is matched again -----


def https(app, ip="testclient") -> TestClient:
    return TestClient(app, base_url="https://testserver", client=(ip, 50000), raise_server_exceptions=False)


def test_rematch_runs_the_stored_photo_against_the_named_hall_and_day(home):
    vision = CampusVision()
    app = client(home, vision, dining=Dining(MenuSource(pizza=True)))
    meal = app.post("/api/meals?location_id=1&taken_at=2026-11-14T15:33:00-05:00", content=jpeg()).json()
    assert meal["menu"]["certain"] is False and meal["context"]["time_source"] == "device"

    res = app.post(f"/api/meals/{meal['id']}/menu", json={"location_id": 1, "date": "2026-11-11", "meal": "dinner"})
    assert res.status_code == 200
    fixed = res.json()
    assert (fixed["id"], fixed["photo_url"], fixed["context"]) == (meal["id"], meal["photo_url"], meal["context"])
    assert fixed["menu"] == {
        "location_id": 1,
        "location_name": "Worcester Dining Commons",
        "date": "2026-11-11",
        "dishes": 2,
        "meal": "dinner",
        "certain": True,
        "start": None,
        "end": None,
    }
    (rice,) = fixed["items"]
    assert (rice["source"], rice["dish"]["date"], rice["dish"]["meal"]) == ("campus", "2026-11-11", "lunch")
    assert vision.menu_names[-1] == ["Jasmine Rice", "Cheese Pizza"]
    assert app.get(f"/api/meals/{meal['id']}").json() == fixed  # saved under the same id
    assert len(app.get("/api/days/2026-11-14").json()["meals"]) == 1  # not a second meal

    # Hall only: the meal's own time decides, as when it was logged (no date on it, so the window).
    hall_only = app.post(f"/api/meals/{meal['id']}/menu", json={"location_id": 1}).json()
    assert hall_only["menu"]["certain"] is False


def test_rematch_refusals(home):
    vision = CampusVision()
    app = client(home, vision, dining=Dining(MenuSource()))
    meal = app.post("/api/meals", content=jpeg()).json()
    url = f"/api/meals/{meal['id']}/menu"
    calls = len(vision.menu_names)

    for body, status in (
        ({"location_id": 0}, 422),
        ({"location_id": 1, "meal": "brunch"}, 422),
        ({"location_id": 1, "date": "11/14/2026"}, 422),
        ({"location_id": 9}, 404),
    ):
        assert app.post(url, json=body).status_code == status, body
    assert app.post("/api/meals/999/menu", json={"location_id": 1}).json() == {"detail": "No meal #999."}

    (home / "photos" / "local" / f"{meal['id']}.jpg").unlink()  # a meal whose photo wasn't kept
    res = app.post(url, json={"location_id": 1})
    assert (res.status_code, res.json()["detail"]) == (409, f"Meal #{meal['id']} has no saved photo to match again.")
    assert len(vision.menu_names) == calls  # none of them reached Gemini

    no_menus = client(home, CampusVision(), dining=None)
    other = no_menus.post("/api/meals", content=jpeg()).json()
    assert no_menus.post(f"/api/meals/{other['id']}/menu", json={"location_id": 1}).status_code == 503


def test_rematch_is_only_for_the_owner_and_counts_against_the_analysis_limit(home, monkeypatch):
    monkeypatch.setattr(mealscholar.web, "ANALYSIS_PER_USER", Rule("analysis-user", 2, 600))
    settings = load_settings({"MEALSCHOLAR_HOME": str(home)})
    auth = AuthService.from_settings(settings, store=MemoryAuthStore())
    vision = CampusVision()
    app = create_app(settings, vision=vision, auth=auth, dining=Dining(MenuSource()))
    app.state.auth.limiter = RateLimiter()
    sam, alex = https(app, "198.51.100.1"), https(app, "198.51.100.2")
    for who, email in ((sam, "sam@example.com"), (alex, "alex@example.com")):
        res = who.post("/api/auth/register", json={"email": email, "password": "correct horse battery"})
        assert res.status_code == 201
    meal = sam.post("/api/meals", content=jpeg(), headers={"Content-Type": "image/jpeg"}).json()
    url = f"/api/meals/{meal['id']}/menu"

    res = alex.post(url, json={"location_id": 1})
    assert (res.status_code, res.json()) == (404, {"detail": f"No meal #{meal['id']}."})  # as for no meal at all
    assert https(app).post(url, json={"location_id": 1}).status_code == 401  # signed out

    assert sam.post(url, json={"location_id": 1, "date": "2026-11-13"}).status_code == 200  # the 2nd analysis
    limited = sam.post(url, json={"location_id": 1, "date": "2026-11-13"})
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    assert len(vision.menu_names) == 2  # the refusal never called the model


class GoneVision(CampusVision):
    """The first look finds the rice; a second finds no meal."""

    def analyze(self, jpeg, context, menu_names=()):
        if self.menu_names:
            self.menu_names.append(list(menu_names))
            return MealAnalysis(is_food=False, items=[], scale_reference=None, question=None)
        return super().analyze(jpeg, context, menu_names)


class FlakyMenuSource(MenuSource):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.down = False

    def menu_items(self, location_id, day):
        if self.down:
            from pymongo.errors import ServerSelectionTimeoutError

            raise ServerSelectionTimeoutError("down")
        return super().menu_items(location_id, day)


def test_rematch_never_replaces_a_logged_meal_with_nothing(home):
    app = client(home, GoneVision(), dining=Dining(MenuSource()))
    meal = app.post("/api/meals?location_id=1&taken_at=2026-11-14T15:33:00-05:00", content=jpeg()).json()
    res = app.post(f"/api/meals/{meal['id']}/menu", json={"location_id": 1, "date": "2026-11-13"})
    assert res.status_code == 422
    assert app.get(f"/api/meals/{meal['id']}").json() == meal

    source = FlakyMenuSource()
    app = client(home, CampusVision(), dining=Dining(source))
    meal = app.post("/api/meals?location_id=1&taken_at=2026-11-14T15:33:00-05:00", content=jpeg()).json()
    source.down = True  # the hall list is cached, the day's menu can't be read
    res = app.post(f"/api/meals/{meal['id']}/menu", json={"location_id": 1, "date": "2026-11-12"})
    assert res.status_code == 503
    assert app.get(f"/api/meals/{meal['id']}").json() == meal
