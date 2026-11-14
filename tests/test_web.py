import io
import logging
import struct
import zlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mealscholar.config import load_settings
from mealscholar.dining import Dining
from mealscholar.schemas import FoodItem, MealAnalysis
from mealscholar.vision import VisionError
from mealscholar.web import create_app


class FailingVision:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc

    def analyze(self, jpeg, context):
        raise self.exc or AssertionError("the photo should have been refused before Gemini")

    def refine(self, analysis, question, answer):
        raise AssertionError("not used")


class DisplayCaseVision:
    """What Gemini said for plastic display food (#49): dishes and a question, but not a meal."""

    def analyze(self, jpeg, context):
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

    def refine(self, analysis, question, answer):
        raise AssertionError("not used")


class CampusVision:
    """Gemini with Worcester's menu in the prompt: the rice is the listed Jasmine Rice."""

    def analyze(self, jpeg, context):
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

    def refine(self, analysis, question, answer):
        raise AssertionError("not used")


class MenuSource:
    """The umass_dining collections as the pipeline needs them: one hall, Jasmine Rice every day."""

    def locations(self):
        return [{"_id": 1, "name": "Worcester Dining Commons", "is_dining_commons": True, "dates_available": []}]

    def daily_menus(self, ids, *, names_only=False):
        return []

    def menu_items(self, location_id, day):
        if location_id != 1:
            return []
        nutrition = {"protein_g": 4, "total_carb_g": 41, "total_fat_g": 0, "sodium_mg": 5}
        return [
            {
                "location_id": 1,
                "date": day,
                "name": "Jasmine Rice",
                "meal": "lunch",
                "category": "Grains",
                "dish_id": "d1",
                "serving_size": "4 OZ",
                "calories": 186,
                "nutrition": nutrition,
                "healthfulness_score": 5,
            }
        ]

    def dishes(self, pattern, limit):
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
    ("photo", "status"),
    [
        (png_claiming(40_000, 40_000), 413),
        (jpeg()[:300], 415),  # cut off mid-upload
        (b"not an image at all", 415),
    ],
    ids=["too many pixels", "truncated", "not an image"],
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
    assert app.get(f"/api/days/{meal['context']['taken_at'][:10]}").json()["meals"] == []


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
    assert any(n.startswith("Campus menu: Worcester Dining Commons, ") for n in meal["notes"])
    assert app.get(f"/api/meals/{meal['id']}").json() == meal

    assert app.post("/api/meals?location_id=0", content=jpeg()).status_code == 422


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
