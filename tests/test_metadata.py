from datetime import datetime, timedelta, timezone

from PIL import ExifTags, Image

from mealscholar.metadata import extract_metadata, load_image


def photo(tmp_path, *, original=None, offset=None, modified=None, gps=None):
    img = Image.new("RGB", (32, 32), "white")
    exif = Image.Exif()
    if modified:
        exif[ExifTags.Base.DateTime] = modified
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    if original:
        sub[ExifTags.Base.DateTimeOriginal] = original
    if offset:
        sub[ExifTags.Base.OffsetTimeOriginal] = offset
    if gps:
        (lat, lat_ref), (lon, lon_ref) = gps
        g = exif.get_ifd(ExifTags.IFD.GPSInfo)
        g[ExifTags.GPS.GPSLatitudeRef], g[ExifTags.GPS.GPSLatitude] = lat_ref, lat
        g[ExifTags.GPS.GPSLongitudeRef], g[ExifTags.GPS.GPSLongitude] = lon_ref, lon
    path = tmp_path / "meal.jpg"
    img.save(path, exif=exif)
    return load_image(path)


def test_capture_time_uses_original_with_offset_not_last_modified(tmp_path):
    img = photo(tmp_path, original="2026:11:13 12:41:07", offset="-05:00", modified="2026:11:14 09:00:00")
    meta = extract_metadata(img)
    assert meta.time_source == "exif"
    assert meta.taken_at == datetime(2026, 11, 13, 12, 41, 7, tzinfo=timezone(timedelta(hours=-5)))


def test_time_falls_back_to_device_then_upload(tmp_path):
    img = photo(tmp_path)
    device = datetime(2026, 11, 13, 8, 0)
    assert extract_metadata(img, device_time=device).taken_at == device
    now = datetime(2026, 11, 13, 20, 0)
    meta = extract_metadata(img, now=now)
    assert (meta.taken_at, meta.time_source) == (now, "upload")


def test_photo_gps_is_opt_in_and_south_west_are_negative(tmp_path):
    img = photo(tmp_path, gps=(((33.0, 51.0, 54.0), "S"), ((151.0, 12.0, 36.0), "W")))
    assert extract_metadata(img).latitude is None
    meta = extract_metadata(img, read_photo_gps=True)
    assert meta.location_source == "exif"
    assert round(meta.latitude, 4) == -33.865 and round(meta.longitude, 4) == -151.21


def test_device_location_wins_over_photo_gps(tmp_path):
    img = photo(tmp_path, gps=(((10.0, 0.0, 0.0), "N"), ((20.0, 0.0, 0.0), "E")))
    meta = extract_metadata(img, device_location=(42.39, -72.53), read_photo_gps=True)
    assert (meta.latitude, meta.longitude, meta.location_source) == (42.39, -72.53, "device")
