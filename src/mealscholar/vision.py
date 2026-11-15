"""Step 3: Gemini names each food and estimates grams. It never outputs nutrient numbers."""

from __future__ import annotations

import base64
import io
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import TYPE_CHECKING, Protocol

from google import genai
from PIL import Image, ImageOps
from pydantic import ValidationError

from .schemas import MealAnalysis, MealContext

if TYPE_CHECKING:
    from .dining import DishFacts

log = logging.getLogger(__name__)

MAX_IMAGE_SIDE = 1536
REQUEST_TIMEOUT_S = 60.0
"""Wall-clock budget of one Gemini call, so a stalled request frees its worker instead of hanging."""
ATTEMPTS = 2
"""Calls per analysis at most: a truncated or schema-invalid reply is retried once with the same input."""
FINAL_STATUSES = ("failed", "cancelled", "budget_exceeded", "requires_action")
"""Interaction statuses that a retry would not change."""
MENU_NAMES = 400
"""Hall dishes the model may pick from at most, listed in the prompt and allowed as menu_item: one
day's menu whole (Worcester serves about 170 a day), the meal on at the photo's time first
(campus.candidates), or the likeliest of several days' when the date isn't known
(campus.window_candidates), or the past dishes when no menu is posted (dining.CATALOG_LIMIT)."""
ENUM_NAMES = 200
"""Dish names at most sent as the menu_item enum, so the model can only answer a listed name. Gemini
took 200 (and a day's 115) but refused 400 ("too much branching for serving"), so a longer list,
such as the days around an undated photo, goes as a plain string field and pipeline.Workflow drops
any name that isn't listed."""
ENUM_CHARS = 12_000
"""Characters of dish names at most in the enum (200 real names are about 4,200)."""

SYSTEM_PROMPT = """\
You estimate food portions from meal photos for a nutrition logger.

- List every distinct food and drink as its own item. Split a plate into components only
  when they are visibly separate (rice beside chicken = 2 items; fried rice = 1 item).
  Sauces, dressings and spreads that are visible get their own item.
- Skip food that is cut off by the edge of the photo unless it is the subject of the photo.
- Estimate edible weight in grams as low / likely / high. Judge size against objects in the
  photo: dinner plate ~26 cm, side plate ~18 cm, fork ~19 cm, spoon ~15 cm, chopsticks
  ~23 cm, soda can ~12 cm tall, adult hand ~18 cm long. Name the object you used in
  scale_reference. Photo estimates usually undercount large portions; do not shrink them.
- Never output calories or nutrient amounts; a database computes those.
- fdc_query names the item the way USDA FNDDS does: main food first, then comma-separated
  descriptors. Examples: "Rice, white, cooked, made with oil", "Rice, fried, with chicken",
  "Broccoli, cooked, no added fat", "Chicken breast, grilled without sauce, skin not eaten",
  "Pizza, cheese, from restaurant or fast food, thin crust", "Soft drink, cola",
  "Coffee, Latte", "Soup, pho, with meat", "Egg, whole, fried with oil".
- hidden_fat_likely is true when cooking oil, butter, dressing or sauce is probably there
  but not visible.
- box_2d is the item's bounding box [ymin, xmin, ymax, xmax] normalized to 0-1000.
- menu_item and servings_low/likely/high: when the message lists a dining hall's dishes, set
  menu_item to a listed name, exactly as written, only when the food in the photo IS that
  dish, judged from what you can see: its broth, noodles, protein, toppings and shape must
  fit. A similar dish of the same kind is not a match: pho is not ramen, a wrap is not a
  burrito, a quesadilla is not a taco. A station that lists parts (broth, noodles, a protein)
  gets one item per visible part that is listed. When nothing listed is that food, menu_item
  stays null and you name the food from what the photo shows; never pick a dish only because
  the hall serves it. When you set menu_item, name is the menu's dish name.
  servings_low/likely/high count the dish's listed serving on the plate, as a range like grams.
- question: null for almost every photo. Ask only about a drink you can't judge from the
  photo, when the answer changes its calories a lot: diet or regular soda, sweetened or
  unsweetened tea or coffee, which milk, or juice or water. Food never gets a question: give
  your best estimate of portions, sauces, shared plates and hidden pieces, and the person can
  correct it in the app. When two listed dishes both genuinely fit a food, set menu_item to the
  likelier one without asking. Never ask about cooking oil, butter, recipes or ingredients (they
  didn't cook it), and never ask for grams, ounces, spoons or anything already visible. Ask it
  in one sentence.
- Only a meal counts: real food or drink that is about to be eaten or drunk. Otherwise return
  is_food=false and an empty items list. That includes a photo with no food at all, and one that
  shows only food models or replicas (plastic display food), food on display in a shop window or
  display case, pictures of food (a menu, a screen, a poster), pet food, or a plate that has been
  eaten from with only scraps left.
"""

REFINE_PROMPT = """\
Your earlier analysis of a meal photo:
{analysis}

You asked: {question}
The user answered: {answer}

Return the full corrected analysis. Change only what the answer affects (grams, cooking method, \
fdc_query, hidden_fat_likely); add an item if the answer reveals one. If the answer pins down an \
amount, such as a drink size, a count, or how much dressing, narrow grams_low and grams_high of \
the items it affects to match. Keep menu_item and the servings unless the answer changes them. Update \
the existing items in place; never add a second entry for a food that is already listed. Set question \
to null."""

_MENU_FIELDS = ("menu_item", "servings_low", "servings_likely", "servings_high")


def closed_set(menu_names: Sequence[str], limit: int = ENUM_NAMES) -> bool:
    """Whether these names go to Gemini as an enum: some, and not more than `limit` names or
    ENUM_CHARS characters of them."""
    return 0 < len(menu_names) <= limit and sum(len(name) for name in menu_names) <= ENUM_CHARS


def response_schema(menu_names: Sequence[str] = ()) -> dict:
    """MealAnalysis as the JSON schema sent to Gemini: the menu fields have defaults so stored
    meals validate, but here they are required-but-nullable like the rest (see schemas.py). With
    `menu_names` (the hall's dishes listed in the prompt) that closed_set allows, menu_item can
    only be one of them or null; a longer list leaves it a plain string."""
    schema = MealAnalysis.model_json_schema()
    item = schema["$defs"]["FoodItem"]
    item["required"] = [*item.get("required", []), *(f for f in _MENU_FIELDS if f not in item.get("required", []))]
    if closed_set(menu_names):
        field = item["properties"]["menu_item"]
        field["anyOf"] = [{"type": "string", "enum": list(dict.fromkeys(menu_names))}, {"type": "null"}]
    return schema


def _response_format(menu_names: Sequence[str] = ()) -> dict:
    return {"type": "text", "mime_type": "application/json", "schema": response_schema(menu_names)}


class VisionError(RuntimeError):
    pass


class VisionModel(Protocol):
    """`menu_names`: the hall dishes listed in the prompt, the only names menu_item may take."""

    def analyze(self, jpeg: bytes, context: str, menu_names: Sequence[str] = ()) -> MealAnalysis: ...

    def refine(
        self, analysis: MealAnalysis, question: str, answer: str, menu_names: Sequence[str] = ()
    ) -> MealAnalysis: ...


def prepare_image(img: Image.Image, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    """Upright, downscaled JPEG. Re-encoding also drops EXIF, so GPS never reaches the model."""
    upright = ImageOps.exif_transpose(img).convert("RGB")
    upright.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    upright.save(buf, "JPEG", quality=85)
    return buf.getvalue()


MENU_RULE = (
    "Set menu_item to one of these names, exactly as written, only when the food in the photo is that "
    "dish: its broth, noodles, protein, toppings and shape fit. A similar dish of the same kind is not a "
    "match (pho is not ramen, a wrap is not a burrito). Otherwise leave menu_item and the servings null "
    "and name the food from the photo. With a match, servings_low/likely/high is how many of its listed "
    "servings are on the plate, as a range like grams. Estimate grams for every item either way."
)


def context_prompt(
    context: MealContext,
    usual_portions: Mapping[str, int],
    menu: Sequence[DishFacts] = (),
    hall: str | None = None,
    *,
    window: tuple[date, date] | None = None,
) -> str:
    """What the model is told besides the photo: the date and time (a 00:30 plate is late night of
    the day before), the place, the user's usual portions, and the hall's dishes when given: one
    day's menu, or with `window` (the photo had no date) the dishes of the days from its first to
    its last, likeliest first."""
    lines = [f"Photo taken {context.taken_at:%a %Y-%m-%d %H:%M} local time ({context.meal_slot})."]
    if (place := context.place) is not None:
        detail = place.kind.replace("_", " ") + (f", cuisine: {place.cuisine}" if place.cuisine else "")
        chain = " (chain)" if place.chain else ""
        lines.append(f"Taken at or near {place.name}{chain} ({detail}).")
    if usual_portions:
        known = "; ".join(f"{name}: {grams} g" for name, grams in usual_portions.items())
        lines.append(f"This user's usual portions from past corrections: {known}.")
    if menu:
        if window is not None:
            first, last = window
            header = (
                f"The photo has no date, so these are dishes {hall} served from {first:%a %b %d} to "
                f"{last:%a %b %d}, the likeliest first:"
            )
        elif all(d.meal == "any" for d in menu):  # dining.CATALOG_MEAL: no menu posted that day
            header = f"Dishes {hall} has served (no menu is posted for this day), most common first:"
        else:
            header = f"Today's menu at {hall}, dishes for this meal first:"
        lines.append(header)
        lines += [f"- {d.name} (serving: {d.serving_size or 'unknown'})" for d in menu[:MENU_NAMES]]
        lines.append(MENU_RULE)
    return "\n".join(lines)


class GeminiVision:
    def __init__(self, api_key: str, model: str, thinking_level: str, *, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._thinking_level = thinking_level
        self._timeout = timeout
        self._enum_limit = ENUM_NAMES
        """Lowered under a list size Gemini refused as an enum but answered as a plain string, so that
        size isn't sent as an enum again. A 400 for another reason (a bad key) fails both and leaves it."""

    def analyze(self, jpeg: bytes, context: str, menu_names: Sequence[str] = ()) -> MealAnalysis:
        return self._call(
            [
                {"type": "text", "text": f"{context}\n\nAnalyze this meal photo."},
                {"type": "image", "data": base64.b64encode(jpeg).decode("ascii"), "mime_type": "image/jpeg"},
            ],
            menu_names,
        )

    def refine(
        self, analysis: MealAnalysis, question: str, answer: str, menu_names: Sequence[str] = ()
    ) -> MealAnalysis:
        text = REFINE_PROMPT.format(analysis=analysis.model_dump_json(), question=question, answer=answer)
        return self._call([{"type": "text", "text": text}], menu_names)

    def _call(self, content: list[dict], menu_names: Sequence[str] = ()) -> MealAnalysis:
        """One structured-output call, bounded by the timeout. A reply the schema rejects (cut off,
        empty, or off-shape) gets one more attempt; an SDK error or a final status does not, except
        that a request refused (400) with the menu_item enum is tried once more with a plain string
        field, which pipeline.Workflow checks against the menu instead."""
        problem = "Gemini returned no text."
        refused = False
        enum = closed_set(menu_names, self._enum_limit)
        response_format = _response_format(menu_names if enum else ())
        for attempt in range(1, ATTEMPTS + 1):
            try:
                interaction = self._client.interactions.create(
                    model=self._model,
                    system_instruction=SYSTEM_PROMPT,
                    input=content,
                    response_format=response_format,
                    generation_config={"thinking_level": self._thinking_level},
                    store=False,  # don't retain meal photos server-side
                    timeout=self._timeout,  # seconds for the whole call, across continuation hops
                )
            except Exception as exc:  # the SDK's interaction error classes are not public API
                if enum and getattr(exc, "status_code", None) == 400 and attempt < ATTEMPTS:
                    log.warning("Gemini refused the menu enum (%d names): %.200s", len(menu_names), exc)
                    refused, enum, response_format = True, False, _response_format()
                    continue
                raise VisionError(f"Gemini request failed: {exc}") from exc
            status = interaction.status
            if status in FINAL_STATUSES:
                raise VisionError(f"Gemini did not complete the request (status: {status}).")
            text = interaction.output_text or ""
            if status != "incomplete" and text:
                try:
                    analysis = MealAnalysis.model_validate_json(text)
                except ValidationError as exc:
                    problem = f"Gemini output did not match the schema: {exc}"
                else:
                    if refused:  # the same request went through without the enum: its size was the problem
                        self._enum_limit = min(self._enum_limit, len(menu_names) - 1)
                    return analysis
            else:
                problem = f"Gemini returned {'a truncated reply' if text else 'no text'} (status: {status})."
            log.warning("Gemini attempt %d/%d unusable (status %s): %.200s", attempt, ATTEMPTS, status, text)
        raise VisionError(problem)
