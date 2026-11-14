"""Web front end: a JSON API over the workflow, plus the static site in `site/`.

Photos arrive as the raw request body and stay in memory; they are never written to disk.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import BaseModel, Field

from .config import USER_AGENT, Settings
from .context import NominatimPlaces, PlaceLookup
from .diary import Diary
from .insights import summarize_days
from .nutrients import NUTRIENTS
from .nutrition import FoodDatabase, FoodDatabaseMissing
from .pipeline import UnknownFood, Workflow, correct
from .schemas import MealResult, Range
from .vision import GeminiVision, VisionError, VisionModel

SITE = Path(__file__).parent / "site"
MAX_PHOTO_BYTES = 25 * 1024 * 1024


class Status(BaseModel):
    gemini_ready: bool
    food_db_ready: bool


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
    fdc_id: int | None = None


class DayView(BaseModel):
    day: date
    meals: list[MealResult]
    totals: dict[str, Range]
    daily_value_pct: dict[str, Range]


def create_app(
    settings: Settings,
    *,
    vision: VisionModel | None = None,
    places: PlaceLookup | None = None,
) -> FastAPI:
    """`vision` and `places` default to Gemini (when a key is set) and OpenStreetMap Nominatim."""
    if vision is None and settings.gemini_api_key:
        vision = GeminiVision(settings.gemini_api_key, settings.gemini_model, settings.thinking_level)
    if places is None:
        places = NominatimPlaces(settings.nominatim_url, USER_AGENT)

    app = FastAPI(title="FoodSmart", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    # SQLite connections are opened per request: FastAPI runs sync handlers on a thread pool.
    @contextmanager
    def stores() -> Iterator[tuple[FoodDatabase, Diary]]:
        with closing(FoodDatabase(settings.food_db)) as foods, closing(Diary(settings.diary_db)) as diary:
            yield foods, diary

    def workflow(foods: FoodDatabase) -> Workflow:
        if vision is None:
            raise HTTPException(503, "Set GEMINI_API_KEY in .env, then restart `foodsmart serve`.")
        return Workflow(vision=vision, foods=foods, places=places)

    def logged(diary: Diary, meal_id: int) -> MealResult:
        result = diary.get(meal_id)
        if result is None:
            raise HTTPException(404, f"No meal #{meal_id}.")
        return result

    def error(status: int):
        async def handler(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=status)

        return handler

    app.add_exception_handler(FoodDatabaseMissing, error(503))
    app.add_exception_handler(VisionError, error(502))
    app.add_exception_handler(UnknownFood, error(404))

    @app.get("/api/status")
    def status() -> Status:
        return Status(gemini_ready=vision is not None, food_db_ready=settings.food_db.exists())

    @app.get("/api/nutrients")
    def nutrients() -> list[NutrientInfo]:
        return [
            NutrientInfo(key=n.key, label=n.label, unit=n.unit, daily_value=n.daily_value, micro=n.micro)
            for n in NUTRIENTS
        ]

    @app.post("/api/meals")
    async def analyze(request: Request, lat: float | None = None, lon: float | None = None) -> MealResult:
        """Body: the photo bytes (JPEG, PNG, HEIC). `lat`/`lon`: device location, opt-in."""
        if (lat is None) != (lon is None):
            raise HTTPException(400, "lat and lon go together.")
        photo = bytearray()
        async for chunk in request.stream():
            photo += chunk
            if len(photo) > MAX_PHOTO_BYTES:
                raise HTTPException(413, f"Photos are limited to {MAX_PHOTO_BYTES // 2**20} MB.")
        if not photo:
            raise HTTPException(400, "Send the photo as the request body.")
        location = (lat, lon) if lat is not None and lon is not None else None
        return await run_in_threadpool(_analyze, bytes(photo), location)

    def _analyze(photo: bytes, location: tuple[float, float] | None) -> MealResult:
        with stores() as (foods, diary):
            try:
                result = workflow(foods).analyze(
                    io.BytesIO(photo), device_location=location, usual_portions=diary.usual_portions()
                )
            except UnidentifiedImageError as exc:
                raise HTTPException(415, "Couldn't read that image. Use a JPEG, PNG or HEIC photo.") from exc
            return diary.save(result) if result.items else result

    @app.get("/api/meals/{meal_id}")
    def show(meal_id: int) -> MealResult:
        with closing(Diary(settings.diary_db)) as diary:
            return logged(diary, meal_id)

    @app.post("/api/meals/{meal_id}/answer")
    def answer(meal_id: int, body: Answer) -> MealResult:
        """Answer the clarifying question: Gemini revises the analysis and everything is recomputed."""
        with stores() as (foods, diary):
            result = logged(diary, meal_id)
            if not result.question:
                raise HTTPException(409, "This meal has no open question.")
            return diary.save(workflow(foods).answer(result, body.answer.strip()))

    @app.patch("/api/meals/{meal_id}/items/{index}")
    def fix(meal_id: int, index: int, body: Correction) -> MealResult:
        """Exact grams collapse the item's range and become a usual portion; fdc_id swaps the match."""
        if body.grams is None and body.fdc_id is None:
            raise HTTPException(400, "Send grams and/or fdc_id.")
        with stores() as (foods, diary):
            result = logged(diary, meal_id)
            if not 0 <= index < len(result.items):
                raise HTTPException(404, f"Meal #{meal_id} has no item {index}.")
            result = diary.save(correct(result, index, foods, grams=body.grams, fdc_id=body.fdc_id))
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
    def day(day: date) -> DayView:
        """Meals and totals for one local date. Information only: no targets."""
        with closing(Diary(settings.diary_db)) as diary:
            summaries = summarize_days(diary.meals_between(day, day))
        if not summaries:
            return DayView(day=day, meals=[], totals={}, daily_value_pct={})
        s = summaries[0]
        return DayView(day=s.day, meals=s.meals, totals=s.totals, daily_value_pct=s.daily_value_pct)

    app.mount("/", StaticFiles(directory=SITE, html=True), name="site")
    return app
