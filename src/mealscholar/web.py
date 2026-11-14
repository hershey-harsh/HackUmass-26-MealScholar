"""Web front end: a JSON API over the workflow, plus the static site in `site/`.

Photos arrive as the raw request body and stay in memory; they are never written to disk.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from .auth import AuthService, DiaryOwner, install_auth
from .busy import Busy, install_busy, open_busy
from .config import USER_AGENT, Settings
from .context import NominatimPlaces, PlaceLookup
from .diary import Diary
from .dining import Dining, install_dining, open_dining
from .insights import HealthScore, health_score, summarize_days
from .nutrients import NUTRIENTS
from .nutrition import FoodDatabase, FoodDatabaseMissing
from .pipeline import BadCorrection, UnknownFood, Workflow, correct
from .prefs import LOCAL_USER, MAX_LOCATION_ID, PrefsStore, install_prefs, open_prefs_store
from .schemas import MealResult, Range
from .vision import GeminiVision, VisionError, VisionModel

SITE = Path(__file__).parent / "site"
MAX_PHOTO_BYTES = 25 * 1024 * 1024

log = logging.getLogger(__name__)


class Status(BaseModel):
    gemini_ready: bool
    food_db_ready: bool
    auth_required: bool
    email_provider: str


class NutrientInfo(BaseModel):
    key: str
    label: str
    unit: str
    daily_value: float | None
    micro: bool


class FoodOption(BaseModel):
    fdc_id: int
    description: str
    data_type: str
    energy_kcal_per_100g: float | None


class FoodSearch(BaseModel):
    foods: list[FoodOption]
    exact: bool
    """False when no food contains every word of the main food; results are only suggestions."""


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=500)


class Correction(BaseModel):
    grams: int | None = Field(default=None, gt=0, le=10_000)
    servings: float | None = Field(default=None, gt=0, le=50)
    """How many of the campus dish's listed serving ('1 Slice'); only for an item with a `dish`."""
    fdc_id: int | None = None


class Erased(BaseModel):
    meals: int = Field(description="How many meals were deleted.")


class DayView(BaseModel):
    day: date
    meals: list[MealResult]
    totals: dict[str, Range]
    daily_value_pct: dict[str, Range]
    health: HealthScore | None = None
    """The day's nutrient-balance score (insights.health_score); None for a day with no meals."""


def create_app(
    settings: Settings,
    *,
    vision: VisionModel | None = None,
    places: PlaceLookup | None = None,
    auth: AuthService | None = None,
    prefs: PrefsStore | None = None,
    dining: Dining | None = None,
    busy: Busy | None = None,
) -> FastAPI:
    """`vision` and `places` default to Gemini (when a key is set) and OpenStreetMap Nominatim;
    `auth` to accounts stored as `settings` say (MongoDB, or memory); `prefs` to the accounts'
    database; `dining` to the UMass Dining menus when MONGODB_URI is set (else they answer 503)."""
    if vision is None and settings.gemini_api_key:
        vision = GeminiVision(settings.gemini_api_key, settings.gemini_model, settings.thinking_level)
    if places is None:
        places = NominatimPlaces(settings.nominatim_url, USER_AGENT)

    app = FastAPI(title="MealScholar", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    auth = auth or AuthService.from_settings(settings)
    install_auth(app, auth)
    dining = dining or open_dining(settings)
    install_dining(app, dining)
    install_busy(app, busy or open_busy(settings))
    prefs = prefs or open_prefs_store(auth.store)
    install_prefs(app, prefs, dining)

    # SQLite connections are opened per request: FastAPI runs sync handlers on a thread pool.
    # `owner` is the signed-in user's id, or None for the local diary (see auth.diary_owner).
    @contextmanager
    def stores(owner: str | None) -> Iterator[tuple[FoodDatabase, Diary]]:
        with closing(FoodDatabase(settings.food_db)) as foods, closing(Diary(settings.diary_db, owner)) as diary:
            yield foods, diary

    def workflow(foods: FoodDatabase) -> Workflow:
        if vision is None:
            raise HTTPException(503, "Set GEMINI_API_KEY in .env, then restart `mealscholar serve`.")
        return Workflow(vision=vision, foods=foods, places=places, menus=dining)

    def logged(diary: Diary, meal_id: int) -> MealResult:
        result = diary.get(meal_id)
        if result is None:
            raise HTTPException(404, f"No meal #{meal_id}.")
        return result

    def error(status: int, reply: str | None = None):
        """`reply` replaces the exception's text in the response when that text can hold server
        paths or a provider's raw error; the full text goes to the server log instead."""

        async def handler(request: Request, exc: Exception) -> JSONResponse:
            if reply is None:
                return JSONResponse({"detail": str(exc)}, status_code=status)
            # The path only: the query can carry the device location.
            log.warning("%s %s answered %d: %s", request.method, request.url.path, status, exc)
            return JSONResponse({"detail": reply}, status_code=status)

        return handler

    app.add_exception_handler(FoodDatabaseMissing, error(503, "The food database isn't set up on the server yet."))
    app.add_exception_handler(VisionError, error(502, "Couldn't analyze the photo right now. Try again in a moment."))
    app.add_exception_handler(UnknownFood, error(404))
    app.add_exception_handler(BadCorrection, error(400))

    @app.get("/api/status")
    def status() -> Status:
        return Status(
            gemini_ready=vision is not None,
            food_db_ready=settings.food_db.exists(),
            auth_required=settings.auth_required,
            email_provider=settings.email_provider,
        )

    @app.get("/api/nutrients")
    def nutrients() -> list[NutrientInfo]:
        return [
            NutrientInfo(key=n.key, label=n.label, unit=n.unit, daily_value=n.daily_value, micro=n.micro)
            for n in NUTRIENTS
        ]

    @app.post("/api/meals")
    async def analyze(
        request: Request,
        owner: DiaryOwner,
        lat: float | None = None,
        lon: float | None = None,
        location_id: Annotated[int | None, Query(ge=1, le=MAX_LOCATION_ID)] = None,
        taken_at: datetime | None = None,
    ) -> MealResult:
        """Body: the photo bytes (JPEG, PNG, HEIC). `lat`/`lon`: device location, opt-in.
        `location_id`: the dining location whose menu the foods are matched against (from
        /api/dining/nearest or the user's dining halls); without it nutrients come from USDA only.
        `taken_at`: the phone's local time at capture with its UTC offset, ISO 8601 such as
        2026-11-14T12:30:00-05:00 (URL-encode a '+'; an unencoded '+' is read as a space and
        answers 422). Used only when the photo carries no EXIF time; `context.time_source` is
        then 'device'. Send local time with its offset, not UTC: a 'Z' or +00:00 time is accepted
        but the diary files the meal under that clock's date, so an evening meal sent as UTC
        lands on the next day."""
        if (lat is None) != (lon is None):
            raise HTTPException(400, "lat and lon go together.")
        if taken_at is not None and taken_at.tzinfo is None:
            raise HTTPException(400, "taken_at needs a UTC offset, such as 2026-11-14T12:30:00-05:00.")
        photo = bytearray()
        async for chunk in request.stream():
            photo += chunk
            if len(photo) > MAX_PHOTO_BYTES:
                raise HTTPException(413, f"Photos are limited to {MAX_PHOTO_BYTES // 2**20} MB.")
        if not photo:
            raise HTTPException(400, "Send the photo as the request body.")
        location = (lat, lon) if lat is not None and lon is not None else None
        return await run_in_threadpool(_analyze, bytes(photo), location, owner, location_id, taken_at)

    def _analyze(
        photo: bytes,
        location: tuple[float, float] | None,
        owner: str | None,
        location_id: int | None,
        taken_at: datetime | None,
    ) -> MealResult:
        with stores(owner) as (foods, diary):
            try:
                result = workflow(foods).analyze(
                    io.BytesIO(photo),
                    device_time=taken_at,
                    device_location=location,
                    usual_portions=diary.usual_portions(),
                    location_id=location_id,
                )
            except Image.DecompressionBombError as exc:
                raise HTTPException(413, "That photo has too many pixels. Send a regular camera photo.") from exc
            # Not an image, or cut off (UnidentifiedImageError is an OSError too). In this block only the
            # in-memory photo raises OSError: Gemini, place and database failures have their own types.
            except OSError as exc:
                raise HTTPException(415, "Couldn't read that image. Use a JPEG, PNG or HEIC photo.") from exc
            return diary.save(result) if result.items else result

    @app.get("/api/meals/{meal_id}")
    def show(meal_id: int, owner: DiaryOwner) -> MealResult:
        with closing(Diary(settings.diary_db, owner)) as diary:
            return logged(diary, meal_id)

    @app.delete("/api/meals/{meal_id}", status_code=204)
    def delete(meal_id: int, owner: DiaryOwner) -> None:
        """Remove a meal from the diary, such as one logged from the wrong photo. 404 unless it's yours."""
        with closing(Diary(settings.diary_db, owner)) as diary:
            if not diary.delete(meal_id):
                raise HTTPException(404, f"No meal #{meal_id}.")

    @app.delete("/api/meals")
    def clear_history(owner: DiaryOwner) -> Erased:
        """Delete every meal in the diary and the portions learned from corrections; goals and
        settings stay. With nobody signed in, that is the local diary."""
        with closing(Diary(settings.diary_db, owner)) as diary:
            return Erased(meals=diary.clear())

    def erase(owner: str | None) -> int:
        """Delete the meals, learned portions and preferences kept for `owner` (None: the local user)."""
        with closing(Diary(settings.diary_db, owner)) as diary:
            meals = diary.clear()
        prefs.delete(owner or LOCAL_USER)
        return meals

    # POST /api/auth/delete-account runs this before it deletes the account itself.
    app.state.erase_user_data = erase

    @app.delete("/api/me")
    def erase_me(owner: DiaryOwner) -> Erased:
        """Delete everything kept for whoever asks: meals, learned portions, goals and settings, so
        the app starts over. A signed-in account stays; POST /api/auth/delete-account removes it too."""
        return Erased(meals=erase(owner))

    @app.post("/api/meals/{meal_id}/answer")
    def answer(meal_id: int, body: Answer, owner: DiaryOwner) -> MealResult:
        """Answer the clarifying question: Gemini revises the analysis and everything is recomputed."""
        with stores(owner) as (foods, diary):
            result = logged(diary, meal_id)
            if not result.question:
                raise HTTPException(409, "This meal has no open question.")
            return diary.save(workflow(foods).answer(result, body.answer.strip()))

    @app.patch("/api/meals/{meal_id}/items/{index}")
    def fix(meal_id: int, index: int, body: Correction, owner: DiaryOwner) -> MealResult:
        """Exact grams collapse the item's range and become a usual portion; fdc_id swaps the match.
        `servings` counts a campus dish's listed serving and keeps its label; grams on a dish listed
        per count ('1 Slice') switch the item to USDA, with a note. 400 for servings on a food that
        isn't a campus dish, or grams together with servings (see pipeline.correct)."""
        if body.grams is None and body.servings is None and body.fdc_id is None:
            raise HTTPException(400, "Send grams, servings and/or fdc_id.")
        with stores(owner) as (foods, diary):
            result = logged(diary, meal_id)
            if not 0 <= index < len(result.items):
                raise HTTPException(404, f"Meal #{meal_id} has no item {index}.")
            corrected = correct(result, index, foods, grams=body.grams, servings=body.servings, fdc_id=body.fdc_id)
            result = diary.save(corrected)
            if body.grams is not None:
                diary.remember_portion(result.items[index].item.name, body.grams)
            return result

    @app.get("/api/foods")
    def search(q: str = Query(min_length=1, max_length=200), limit: int = Query(8, ge=1, le=25)) -> FoodSearch:
        with closing(FoodDatabase(settings.food_db)) as foods:
            found = foods.search(q, limit=limit)
        return FoodSearch(
            foods=[
                FoodOption(
                    fdc_id=f.fdc_id,
                    description=f.description,
                    data_type=f.data_type,
                    energy_kcal_per_100g=f.per_100g.get("energy_kcal"),
                )
                for f in found.foods
            ],
            exact=found.exact,
        )

    @app.get("/api/days/{day}")
    def day(day: date, owner: DiaryOwner) -> DayView:
        """Meals and totals for one local date. Information only: no targets."""
        with closing(Diary(settings.diary_db, owner)) as diary:
            summaries = summarize_days(diary.meals_between(day, day))
        if not summaries:
            return DayView(day=day, meals=[], totals={}, daily_value_pct={})
        s = summaries[0]
        return DayView(
            day=s.day,
            meals=s.meals,
            totals=s.totals,
            daily_value_pct=s.daily_value_pct,
            health=health_score(s.totals, s.daily_value_pct),
        )

    app.mount("/", StaticFiles(directory=SITE, html=True), name="site")
    return app
