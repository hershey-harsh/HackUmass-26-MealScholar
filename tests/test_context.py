from datetime import datetime

import httpx
import pytest

from foodsmart.context import NominatimPlaces, build_context
from foodsmart.schemas import PhotoMetadata


def failing_places(handler) -> NominatimPlaces:
    places = NominatimPlaces("https://nominatim.example/reverse", "foodsmart-tests")
    places._client = httpx.Client(transport=httpx.MockTransport(handler))
    return places


def timeout(request):
    raise httpx.ReadTimeout("The read operation timed out", request=request)


def connect_error(request):
    raise httpx.ConnectError("[Errno 8] nodename nor servname provided", request=request)


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (lambda request: httpx.Response(429, request=request), "HTTP 429"),
        (lambda request: httpx.Response(503, request=request), "HTTP 503"),
        (timeout, "timed out"),
        (connect_error, "ConnectError"),
        (lambda request: httpx.Response(200, request=request, content=b"<html>"), "JSONDecodeError"),
    ],
)
def test_failed_place_lookup_note_has_no_coordinates(handler, reason):
    """The note is stored with the meal; httpx's own error text holds the URL with lat/lon."""
    meta = PhotoMetadata(
        taken_at=datetime(2026, 11, 14, 12, 30),
        time_source="device",
        latitude=42.389123,
        longitude=-72.528456,
        location_source="device",
    )
    context = build_context(meta, failing_places(handler))
    assert context.place is None
    assert context.notes == [f"Place lookup failed ({reason}); continuing without it."]
