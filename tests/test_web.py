import io
import logging
import struct
import zlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mealscholar.config import load_settings
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


def client(home, vision) -> TestClient:
    settings = load_settings({"MEALSCHOLAR_HOME": str(home), "MEALSCHOLAR_AUTH_REQUIRED": "0"})
    return TestClient(create_app(settings, vision=vision), raise_server_exceptions=False)


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
