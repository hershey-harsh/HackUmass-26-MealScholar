import io
import logging
import struct
import zlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from foodsmart.config import load_settings
from foodsmart.vision import VisionError
from foodsmart.web import create_app


class FailingVision:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc

    def analyze(self, jpeg, context):
        raise self.exc or AssertionError("the photo should have been refused before Gemini")

    def refine(self, analysis, question, answer):
        raise AssertionError("not used")


def client(home, vision) -> TestClient:
    settings = load_settings({"FOODSMART_HOME": str(home), "FOODSMART_AUTH_REQUIRED": "0"})
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
    with caplog.at_level(logging.WARNING, logger="foodsmart.web"):
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
