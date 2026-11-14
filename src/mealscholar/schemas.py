"""Data contracts passed between workflow steps."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MealSlot = Literal["breakfast", "lunch", "snack", "dinner"]
TimeSource = Literal["exif", "device", "upload"]
LocationSource = Literal["device", "exif"]
CookingMethod = Literal[
    "raw",
    "boiled",
    "steamed",
    "grilled",
    "roasted",
    "baked",
    "pan_fried",
    "stir_fried",
    "deep_fried",
    "unknown",
]


# --- Step 1: metadata -------------------------------------------------------


class PhotoMetadata(BaseModel):
    taken_at: datetime
    """Local wall-clock time of the meal; timezone-aware only when the offset is known."""
    time_source: TimeSource
    latitude: float | None = None
    longitude: float | None = None
    location_source: LocationSource | None = None


# --- Step 2: context (coordinates are dropped here) -------------------------


class Place(BaseModel):
    name: str
    kind: str
    """OpenStreetMap type, e.g. restaurant, fast_food, cafe."""
    cuisine: str | None = None
    chain: bool = False


class MealContext(BaseModel):
    taken_at: datetime
    time_source: TimeSource
    meal_slot: MealSlot
    place: Place | None = None
    notes: list[str] = []


# --- Step 3: vision output (Gemini structured output schema) ----------------
# Nullable fields have no default so the JSON schema marks them required-but-nullable,
# which is what Gemini's constrained decoding handles best.


_SERVINGS = "How many of the dish's listed serving are on the plate, as a range like grams; null unless menu_item is set."


class FoodItem(BaseModel):
    """One food or drink as seen in the photo. Deliberately carries no nutrient numbers."""

    name: str = Field(description="Plain name, e.g. 'chicken fried rice'.")
    fdc_query: str = Field(
        description=(
            "USDA FNDDS-style description: main food first, then comma-separated "
            "descriptors, e.g. 'Rice, fried, with chicken'."
        )
    )
    cooking_method: CookingMethod
    grams_low: int = Field(description="Low estimate of edible weight in grams.")
    grams_likely: int = Field(description="Most likely edible weight in grams.")
    grams_high: int = Field(description="High estimate of edible weight in grams.")
    box_2d: list[int] | None = Field(description="Bounding box [ymin, xmin, ymax, xmax] normalized to 0-1000.")
    hidden_fat_likely: bool = Field(
        description="True when oil, butter, dressing or sauce is probably present but not visible."
    )
    # Defaults keep stored meals valid; vision.response_schema() still marks them required for Gemini.
    menu_item: str | None = Field(
        default=None,
        description="Exact name of the listed menu dish this food is; null when it is not one of them or no menu was given.",
    )
    servings_low: float | None = Field(default=None, description=_SERVINGS)
    servings_likely: float | None = Field(default=None, description=_SERVINGS)
    servings_high: float | None = Field(default=None, description=_SERVINGS)

    @model_validator(mode="after")
    def _ordered_grams(self) -> FoodItem:
        low, likely, high = sorted(max(0, g) for g in (self.grams_low, self.grams_likely, self.grams_high))
        self.grams_low, self.grams_likely, self.grams_high = low, likely, high
        if self.box_2d is not None and len(self.box_2d) != 4:
            self.box_2d = None
        servings = (self.servings_low, self.servings_likely, self.servings_high)
        counted = sorted(max(0.0, float(s)) for s in servings) if None not in servings else None
        if self.menu_item is None or counted is None or counted[1] <= 0:
            # A count without a dish, a missing one, or 0 is no count at all.
            self.servings_low, self.servings_likely, self.servings_high = None, None, None
        else:
            self.servings_low, self.servings_likely, self.servings_high = counted
        return self


class MealAnalysis(BaseModel):
    is_food: bool = Field(description="False when the photo shows no food or drink.")
    items: list[FoodItem]
    scale_reference: str | None = Field(
        description="Object used to judge size, e.g. 'dinner fork'; null if none is visible."
    )
    question: str | None = Field(
        description=(
            "At most one short question about the uncertainty with the biggest calorie "
            "impact; null when nothing material is uncertain."
        )
    )


# --- Steps 4-6: nutrition and results ----------------------------------------


class FoodMatch(BaseModel):
    fdc_id: int
    description: str
    data_type: str


class Range(BaseModel):
    low: float
    likely: float
    high: float


class DishMatch(BaseModel):
    """The campus dish an item was matched to, with its own nutrition label per serving."""

    dish_id: str | None
    name: str
    location_id: int
    location_name: str
    date: date
    meal: str
    serving_size: str | None
    serving_grams: float | None
    per_serving: dict[str, float] = {}
    """Nutrient key: the label's value for one serving."""
    healthfulness_score: int | None = None
    """UMass Dining's own 0-7 rating; information, never a target."""


class MenuRef(BaseModel):
    """The hall menu a meal was matched against: the one the client named."""

    location_id: int
    location_name: str
    date: date
    dishes: int


class ItemResult(BaseModel):
    item: FoodItem
    match: FoodMatch | None
    """The USDA food, which for a campus item only donates micronutrients."""
    alternatives: list[FoodMatch] = []
    """Other database candidates the user can switch to."""
    nutrients: dict[str, Range] = {}
    source: Literal["usda", "campus"] = "usda"
    """Where the macronutrients come from: USDA per 100 g, or the campus dish's label."""
    dish: DishMatch | None = None


class MealResult(BaseModel):
    id: int | None = None
    context: MealContext
    items: list[ItemResult]
    menu: MenuRef | None = None
    name: str | None = None
    """The item names joined, e.g. 'white rice, pizza slice and mystery sauce'."""
    scale_reference: str | None = None
    question: str | None = None
    answer: str | None = None
    totals: dict[str, Range] = {}
    daily_value_pct: dict[str, Range] = {}
    partial_nutrients: list[str] = []
    """Nutrient keys some matched food has no data for, so the total undercounts."""
    notes: list[str] = []

    def analysis(self) -> MealAnalysis:
        return MealAnalysis(
            is_food=bool(self.items),
            items=[r.item for r in self.items],
            scale_reference=self.scale_reference,
            question=self.question,
        )
