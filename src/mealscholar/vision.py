"""Step 3: Gemini names each food and estimates grams. It never outputs nutrient numbers."""

from __future__ import annotations

import base64
import io
import logging
from collections.abc import Mapping, Sequence
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
MENU_NAMES = 100
"""Menu dishes listed in the prompt at most: the meal on at the photo's time first, whole
(campus.candidates), so the cap only ever cuts other meals."""
CATALOG_NAMES = 400
"""Past dishes listed when a hall has no menu that day (dining.CATALOG_LIMIT): most common first."""

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
- menu_item and servings_low/likely/high: when the message lists a dining hall's menu, the
  photo was almost certainly taken there, so match each food to the listed dish of the same
  kind (an omelet to the omelet station, a slice to the listed pizza) even when the photo
  can't confirm every detail, and let the menu settle what the food is when the photo alone
  is ambiguous (a noodle soup is the pho or the ramen the hall served that day). A station
  that lists parts (broth, noodles, a protein) gets one item per visible part. Leave
  menu_item null only when nothing listed is that kind of food. name describes the food,
  using the menu's word for it when you matched one.
- question: usually null. Ask only when one answer would change the calories a lot AND the
  person eating can answer it at a glance: which of two listed dishes it is, whether a drink
  is diet or regular, how big a cup or bowl is, whether a plate is shared or seconds, or how
  many pieces are hidden. Never ask about cooking oil, butter, recipes or ingredients (they
  didn't cook it), and never ask for grams, ounces, spoons or anything already visible.
  Ask it in one sentence.
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


def response_schema() -> dict:
    """MealAnalysis as the JSON schema sent to Gemini: the menu fields have defaults so stored
    meals validate, but here they are required-but-nullable like the rest (see schemas.py)."""
    schema = MealAnalysis.model_json_schema()
    item = schema["$defs"]["FoodItem"]
    item["required"] = [*item.get("required", []), *(f for f in _MENU_FIELDS if f not in item.get("required", []))]
    return schema


_RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": response_schema(),
}


class VisionError(RuntimeError):
    pass


class VisionModel(Protocol):
    def analyze(self, jpeg: bytes, context: str) -> MealAnalysis: ...

    def refine(self, analysis: MealAnalysis, question: str, answer: str) -> MealAnalysis: ...


def prepare_image(img: Image.Image, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    """Upright, downscaled JPEG. Re-encoding also drops EXIF, so GPS never reaches the model."""
    upright = ImageOps.exif_transpose(img).convert("RGB")
    upright.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    upright.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def context_prompt(
    context: MealContext,
    usual_portions: Mapping[str, int],
    menu: Sequence[DishFacts] = (),
    hall: str | None = None,
) -> str:
    """What the model is told besides the photo: the date and time (a 00:30 plate is late night of
    the day before), the place, the user's usual portions, and the hall's menu when one is given."""
    lines = [f"Photo taken {context.taken_at:%a %Y-%m-%d %H:%M} local time ({context.meal_slot})."]
    if (place := context.place) is not None:
        detail = place.kind.replace("_", " ") + (f", cuisine: {place.cuisine}" if place.cuisine else "")
        chain = " (chain)" if place.chain else ""
        lines.append(f"Taken at or near {place.name}{chain} ({detail}).")
    if usual_portions:
        known = "; ".join(f"{name}: {grams} g" for name, grams in usual_portions.items())
        lines.append(f"This user's usual portions from past corrections: {known}.")
    if menu:
        past = all(d.meal == "any" for d in menu)  # dining.CATALOG_MEAL: no menu posted that day
        lines.append(
            f"Dishes {hall} has served (no menu is posted for this day), most common first:"
            if past
            else f"Today's menu at {hall}, dishes for this meal first:"
        )
        lines += [
            f"- {d.name} (serving: {d.serving_size or 'unknown'})"
            for d in menu[: CATALOG_NAMES if past else MENU_NAMES]
        ]
        lines.append(
            "The photo was taken here, so for each food pick the listed dish of the same kind (stations "
            "like an omelet bar or a ramen bar count) and set menu_item to that exact name, with "
            "servings_low/likely/high as how many of its listed servings are on the plate, as a range like "
            "grams. Leave menu_item and the servings null only for a food nothing listed resembles. "
            "Estimate grams for every item either way."
        )
    return "\n".join(lines)


class GeminiVision:
    def __init__(self, api_key: str, model: str, thinking_level: str, *, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._thinking_level = thinking_level
        self._timeout = timeout

    def analyze(self, jpeg: bytes, context: str) -> MealAnalysis:
        return self._call(
            [
                {"type": "text", "text": f"{context}\n\nAnalyze this meal photo."},
                {"type": "image", "data": base64.b64encode(jpeg).decode("ascii"), "mime_type": "image/jpeg"},
            ]
        )

    def refine(self, analysis: MealAnalysis, question: str, answer: str) -> MealAnalysis:
        text = REFINE_PROMPT.format(analysis=analysis.model_dump_json(), question=question, answer=answer)
        return self._call([{"type": "text", "text": text}])

    def _call(self, content: list[dict]) -> MealAnalysis:
        """One structured-output call, bounded by the timeout. A reply the schema rejects (cut off,
        empty, or off-shape) gets one more attempt; an SDK error or a final status does not."""
        problem = "Gemini returned no text."
        for attempt in range(1, ATTEMPTS + 1):
            try:
                interaction = self._client.interactions.create(
                    model=self._model,
                    system_instruction=SYSTEM_PROMPT,
                    input=content,
                    response_format=_RESPONSE_FORMAT,
                    generation_config={"thinking_level": self._thinking_level},
                    store=False,  # don't retain meal photos server-side
                    timeout=self._timeout,  # seconds for the whole call, across continuation hops
                )
            except Exception as exc:  # the SDK's interaction error classes are not public API
                raise VisionError(f"Gemini request failed: {exc}") from exc
            status = interaction.status
            if status in FINAL_STATUSES:
                raise VisionError(f"Gemini did not complete the request (status: {status}).")
            text = interaction.output_text or ""
            if status != "incomplete" and text:
                try:
                    return MealAnalysis.model_validate_json(text)
                except ValidationError as exc:
                    problem = f"Gemini output did not match the schema: {exc}"
            else:
                problem = f"Gemini returned {'a truncated reply' if text else 'no text'} (status: {status})."
            log.warning("Gemini attempt %d/%d unusable (status %s): %.200s", attempt, ATTEMPTS, status, text)
        raise VisionError(problem)
