"""Logged meals keep their photo: upright JPEGs without metadata, shown only to their owner and
deleted with the meal, the history or the account. Storing one never fails the analysis."""

import hashlib
import io
import logging

import pytest
from PIL import ExifTags, Image, ImageCms
from test_auth import JPEG, PASSWORD, FakeVision, browser, register
from test_metadata import IPHONE_HEIC, heic
from test_web import CampusVision, DisplayCaseVision, client

from mealscholar.auth import AuthService
from mealscholar.auth_store import MemoryAuthStore
from mealscholar.config import load_settings
from mealscholar.web import create_app

NO_METADATA = {*range(0xE1, 0xF0), 0xFE}
"""JPEG markers that carry metadata: APP1 to APP15 (EXIF, XMP, ICC, IPTC...) and COM. APP0 is JFIF."""


@pytest.fixture
def home(tmp_path, food_db_path):
    (tmp_path / "fdc.sqlite").symlink_to(food_db_path)
    return tmp_path


def accounts(home):
    """The app with sign-in required and accounts in memory."""
    settings = load_settings({"MEALSCHOLAR_HOME": str(home)})
    auth = AuthService.from_settings(settings, store=MemoryAuthStore())
    return create_app(settings, vision=CampusVision(), auth=auth)


def camera_jpeg(size=(2000, 1000), left=(220, 30, 30), right=(30, 30, 220)) -> bytes:
    """A phone-style JPEG stored sideways (EXIF orientation 6 turns it upright, `left` on top) with
    GPS, a camera make, XMP, a comment and a color profile, none of which may be kept."""
    img = Image.new("RGB", size, right)
    img.paste(left, (0, 0, size[0] // 2, size[1]))
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.Base.Make] = "SecretCam"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef], gps[ExifTags.GPS.GPSLatitude] = "N", (42.0, 23.0, 21.0)
    gps[ExifTags.GPS.GPSLongitudeRef], gps[ExifTags.GPS.GPSLongitude] = "W", (72.0, 31.0, 41.0)
    buf = io.BytesIO()
    img.save(
        buf,
        "JPEG",
        exif=exif,
        xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/">secret-xmp</x:xmpmeta>',
        comment=b"secret-comment",
        icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
    )
    return buf.getvalue()


def markers(jpeg: bytes) -> set[int]:
    """The JPEG's segment markers before the image data."""
    found, i = set(), 2
    while jpeg[i] == 0xFF and jpeg[i + 1] != 0xDA:
        found.add(jpeg[i + 1])
        i += 2 + int.from_bytes(jpeg[i + 2 : i + 4], "big")
    return found


def stored(home, owner_key="local"):
    folder = home / "photos" / owner_key
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def test_a_logged_meal_keeps_its_photo_upright_and_without_metadata(home):
    app = client(home, CampusVision())
    original = camera_jpeg()
    assert markers(original) >= {0xE1, 0xE2, 0xFE}  # EXIF and XMP, ICC, comment: all there to drop
    meal = app.post("/api/meals", content=original, headers=JPEG).json()
    url = meal["photo_url"]
    assert url.startswith(f"/api/meals/{meal['id']}/photo?v=")
    assert app.get(f"/api/meals/{meal['id']}").json()["photo_url"] == url  # kept with the meal
    day = app.get(f"/api/days/{meal['context']['taken_at'][:10]}").json()
    assert [m["photo_url"] for m in day["meals"]] == [url]

    for size, query, expected in (("full", "", (640, 1280)), ("thumb", "&size=thumb", (160, 320))):
        res = app.get(url + query)
        assert res.status_code == 200, size
        assert res.headers["content-type"] == "image/jpeg"
        assert res.headers["cache-control"] == "private, max-age=86400"
        img = Image.open(io.BytesIO(res.content))
        assert img.size == expected, size  # upright: the sideways 2000 x 1000 stands up, long edge 1280 / 320
        top, bottom = img.getpixel((expected[0] // 2, 4)), img.getpixel((expected[0] // 2, expected[1] - 5))
        assert top[0] > 150 > top[2] and bottom[2] > 150 > bottom[0], size  # the left half is on top
        assert not markers(res.content) & NO_METADATA, size
        assert not img.getexif() and b"Secret" not in res.content and b"secret" not in res.content
        assert ("progressive" in img.info) == (size == "full")
    assert app.get(url + "&size=huge").status_code == 422
    assert stored(home) == [f"{meal['id']}-thumb.jpg", f"{meal['id']}.jpg"]  # and no temporary files


def test_heic_photo_is_stored_upright_without_its_exif(home):
    app = client(home, CampusVision())
    meal = app.post("/api/meals", content=heic(orientation=6, size=(40, 30), **IPHONE_HEIC)).json()
    for query in ("", "&size=thumb"):
        res = app.get(meal["photo_url"] + query)
        img = Image.open(io.BytesIO(res.content))
        assert img.size == (30, 40)  # upright, and never enlarged
        assert not img.getexif() and not markers(res.content) & NO_METADATA  # no date, no GPS


def test_a_photo_without_food_is_not_kept(home):
    app = client(home, DisplayCaseVision())
    meal = app.post("/api/meals", content=camera_jpeg()).json()
    assert (meal["id"], meal["photo_url"]) == (None, None)
    assert not (home / "photos").exists()


def test_answers_and_corrections_keep_the_photo(home):
    app = client(home, FakeVision())
    meal = app.post("/api/meals", content=camera_jpeg()).json()
    answered = app.post(f"/api/meals/{meal['id']}/answer", json={"answer": "no oil"}).json()
    assert answered["answer"] == "no oil" and answered["photo_url"] == meal["photo_url"]
    fixed = app.patch(f"/api/meals/{meal['id']}/items/0", json={"grams": 180}).json()
    assert fixed["photo_url"] == meal["photo_url"]
    assert app.get(f"/api/meals/{meal['id']}").json()["photo_url"] == meal["photo_url"]


def test_only_the_owner_gets_the_photo(home):
    app = accounts(home)
    sam, alex = browser(app), browser(app)
    sam_id = register(sam, email="sam@example.com").json()["user"]["id"]
    register(alex, email="alex@example.com")
    meal = sam.post("/api/meals", content=camera_jpeg(), headers=JPEG).json()
    url = meal["photo_url"]

    assert sam.get(url).status_code == 200
    for query in ("", "&size=thumb"):
        res = alex.get(url + query)
        assert (res.status_code, res.json()) == (404, {"detail": f"No meal #{meal['id']}."})  # as for no meal at all
    assert alex.get("/api/meals/999/photo").json() == {"detail": "No meal #999."}
    assert browser(app).get(url).status_code == 401  # signed out

    key = hashlib.sha256(sam_id.encode()).hexdigest()[:16]  # the folder doesn't name the account
    assert stored(home, key) == [f"{meal['id']}-thumb.jpg", f"{meal['id']}.jpg"]


def test_deleting_a_meal_deletes_its_photo(home):
    app = client(home, CampusVision())
    gone = app.post("/api/meals", content=camera_jpeg()).json()
    kept = app.post("/api/meals", content=camera_jpeg()).json()

    assert app.delete(f"/api/meals/{gone['id']}").status_code == 204
    assert stored(home) == [f"{kept['id']}-thumb.jpg", f"{kept['id']}.jpg"]
    assert app.get(gone["photo_url"]).json() == {"detail": f"No meal #{gone['id']}."}
    assert app.get(kept["photo_url"]).status_code == 200
    assert app.delete(f"/api/meals/{gone['id']}").status_code == 404


def test_a_reused_meal_id_gets_a_new_photo_url(home):
    """SQLite gives the next meal the id of a deleted newest one: the version keeps a cached copy of
    the old photo from showing for the new meal."""
    app = client(home, CampusVision())
    first = app.post("/api/meals", content=camera_jpeg()).json()
    assert app.delete(f"/api/meals/{first['id']}").status_code == 204
    second = app.post("/api/meals", content=camera_jpeg(left=(30, 200, 30))).json()
    assert second["id"] == first["id"] and second["photo_url"] != first["photo_url"]


def test_clearing_history_and_deleting_data_delete_the_photos(home):
    app = accounts(home)
    sam, alex = browser(app), browser(app)
    sam_key = hashlib.sha256(register(sam, email="sam@example.com").json()["user"]["id"].encode()).hexdigest()[:16]
    register(alex, email="alex@example.com")
    kept = alex.post("/api/meals", content=camera_jpeg(), headers=JPEG).json()

    for _ in range(2):
        sam.post("/api/meals", content=camera_jpeg(), headers=JPEG)
    assert len(stored(home, sam_key)) == 4
    assert sam.delete("/api/meals").json() == {"meals": 2}
    assert stored(home, sam_key) == []

    meal = sam.post("/api/meals", content=camera_jpeg(), headers=JPEG).json()
    assert sam.delete("/api/me").json() == {"meals": 1}
    assert stored(home, sam_key) == [] and sam.get(meal["photo_url"]).status_code == 404

    sam.post("/api/meals", content=camera_jpeg(), headers=JPEG)
    assert sam.post("/api/auth/delete-account", json={"password": PASSWORD}).status_code == 204
    assert stored(home, sam_key) == []
    assert alex.get(kept["photo_url"]).status_code == 200  # nobody else's photos go

    local = client(home, CampusVision())  # sign-in off: the local user's photos
    local.post("/api/meals", content=camera_jpeg())
    assert local.delete("/api/me").json() == {"meals": 1}
    assert stored(home) == []


@pytest.mark.parametrize("failure", ["no folder", "encoder"])
def test_a_photo_that_cant_be_stored_never_fails_the_meal(home, caplog, monkeypatch, failure):
    if failure == "no folder":
        (home / "photos").write_text("a file where the folder should be")
    else:

        def broken(photo):
            raise ValueError("encoder broke")

        monkeypatch.setattr("mealscholar.photos.encode", broken)
    app = client(home, CampusVision())
    with caplog.at_level(logging.WARNING, logger="mealscholar.photos"):
        res = app.post("/api/meals", content=camera_jpeg())
    assert res.status_code == 200
    meal = res.json()
    assert meal["id"] is not None and meal["items"] and meal["photo_url"] is None
    (record,) = [r for r in caplog.records if r.name == "mealscholar.photos"]
    assert record.getMessage().startswith(f"Couldn't store the photo of meal #{meal['id']}: ")
    assert "\n" not in record.getMessage() and record.exc_info is None  # one line
    assert app.get(f"/api/meals/{meal['id']}/photo").json() == {"detail": f"Meal #{meal['id']} has no photo."}
    assert app.get(f"/api/meals/{meal['id']}").json() == meal
