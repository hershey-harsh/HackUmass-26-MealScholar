"""Step 1: load the photo and work out when (and optionally where) the meal happened."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from PIL import ExifTags, Image
from pillow_heif import register_heif_opener

from .schemas import PhotoMetadata

# iPhones save HEIC by default.
register_heif_opener()

_EXIF_TIME_FORMAT = "%Y:%m:%d %H:%M:%S"


def load_image(photo: Path | BinaryIO) -> Image.Image:
    """`photo` may be an in-memory stream so uploads never touch the disk."""
    img = Image.open(photo)
    img.load()
    return img


def extract_metadata(
    img: Image.Image,
    *,
    device_time: datetime | None = None,
    device_location: tuple[float, float] | None = None,
    read_photo_gps: bool = False,
    now: datetime | None = None,
) -> PhotoMetadata:
    """Time: photo EXIF -> device clock at capture -> upload time.

    Location is opt-in: device coordinates when the caller passes them, otherwise photo GPS
    only when `read_photo_gps` is set. Uploads often arrive with GPS stripped (iOS share
    flows, Android scoped storage), so missing GPS is normal, not an error.
    """
    exif = img.getexif()

    if (exif_time := _exif_time(exif)) is not None:
        taken_at, time_source = exif_time, "exif"
    elif device_time is not None:
        taken_at, time_source = device_time, "device"
    else:
        taken_at, time_source = now or datetime.now().astimezone(), "upload"

    latitude = longitude = location_source = None
    if device_location is not None:
        (latitude, longitude), location_source = device_location, "device"
    elif read_photo_gps and (gps := _exif_gps(exif)) is not None:
        (latitude, longitude), location_source = gps, "exif"

    return PhotoMetadata(
        taken_at=taken_at,
        time_source=time_source,
        latitude=latitude,
        longitude=longitude,
        location_source=location_source,
    )


def _exif_time(exif: Image.Exif) -> datetime | None:
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    candidates = (
        (sub.get(ExifTags.Base.DateTimeOriginal), sub.get(ExifTags.Base.OffsetTimeOriginal)),
        (sub.get(ExifTags.Base.DateTimeDigitized), sub.get(ExifTags.Base.OffsetTimeDigitized)),
        # IFD0 DateTime is the last-modified time (editing changes it): last resort.
        (exif.get(ExifTags.Base.DateTime), sub.get(ExifTags.Base.OffsetTime)),
    )
    for value, offset in candidates:
        if (parsed := _parse_exif_time(value, offset)) is not None:
            return parsed
    return None


def _parse_exif_time(value: object, offset: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip("\x00 "), _EXIF_TIME_FORMAT)
    except ValueError:  # blank or "0000:00:00 00:00:00" placeholders
        return None
    tz = _parse_offset(offset)
    return parsed.replace(tzinfo=tz) if tz else parsed


def _parse_offset(offset: object) -> timezone | None:
    """EXIF OffsetTime* is "+HH:MM" / "-HH:MM"; without it the time is local but zone-less."""
    if not isinstance(offset, str):
        return None
    text = offset.strip("\x00 ")
    if len(text) != 6 or text[0] not in "+-" or text[3] != ":":
        return None
    try:
        delta = timedelta(hours=int(text[1:3]), minutes=int(text[4:6]))
    except ValueError:
        return None
    return timezone(-delta if text[0] == "-" else delta)


def _exif_gps(exif: Image.Exif) -> tuple[float, float] | None:
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    lat = _degrees(gps.get(ExifTags.GPS.GPSLatitude), gps.get(ExifTags.GPS.GPSLatitudeRef), "S")
    lon = _degrees(gps.get(ExifTags.GPS.GPSLongitude), gps.get(ExifTags.GPS.GPSLongitudeRef), "W")
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


def _degrees(dms: object, ref: object, negative_ref: str) -> float | None:
    if not isinstance(dms, tuple) or len(dms) != 3:
        return None
    try:
        degrees, minutes, seconds = (float(part) for part in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    if math.isnan(value):  # IFDRational with a zero denominator
        return None
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    if isinstance(ref, str) and ref.strip("\x00 ").upper() == negative_ref:
        value = -value
    return value
