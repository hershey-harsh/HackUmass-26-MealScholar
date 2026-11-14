"""Step 3: Gemini names each food and estimates grams. It never outputs nutrient numbers."""

from __future__ import annotations

import base64
import io
from collections.abc import Mapping
from typing import Protocol

from google import genai
from PIL import Image, ImageOps
from pydantic import ValidationError

from .schemas import MealAnalysis, MealContext

MAX_IMAGE_SIDE = 1536

SYSTEM_PROMPT = """\
You estimate food portions from meal photos for a nutrition logger.

- List every distinct food and drink as its own item. Split a plate into components only
  when they are visibly separate (rice beside chicken = 2 items; fried rice = 1 item).
  Sauces, dressings and spreads that are visible get their own item.
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
- question: at most one short question about the single uncertainty with the biggest
  calorie impact (cooking fat, drink size, sugar in a drink, hidden layer, sauce amount).
  Use null when nothing material is uncertain.
- If the photo shows no food or drink, return is_food=false and an empty items list.
"""

REFINE_PROMPT = """\
Your earlier analysis of a meal photo:
{analysis}

You asked: {question}
The user answered: {answer}

Return the full corrected analysis. Change only what the answer affects (grams, cooking method, \
fdc_query, hidden_fat_likely); add an item if the answer reveals one. If the answer pins down an \
amount, such as a drink size, a count, or how much dressing, narrow grams_low and grams_high of \
the items it affects to match. Set question to null."""

_RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": MealAnalysis.model_json_schema(),
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


def context_prompt(context: MealContext, usual_portions: Mapping[str, int]) -> str:
    lines = [f"Photo taken {context.taken_at:%H:%M} local time ({context.meal_slot})."]
    if (place := context.place) is not None:
        detail = place.kind.replace("_", " ") + (f", cuisine: {place.cuisine}" if place.cuisine else "")
        chain = " (chain)" if place.chain else ""
        lines.append(f"Taken at or near {place.name}{chain} ({detail}).")
    if usual_portions:
        known = "; ".join(f"{name}: {grams} g" for name, grams in usual_portions.items())
        lines.append(f"This user's usual portions from past corrections: {known}.")
    return "\n".join(lines)


class GeminiVision:
    def __init__(self, api_key: str, model: str, thinking_level: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._thinking_level = thinking_level

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
        try:
            interaction = self._client.interactions.create(
                model=self._model,
                system_instruction=SYSTEM_PROMPT,
                input=content,
                response_format=_RESPONSE_FORMAT,
                generation_config={"thinking_level": self._thinking_level},
                store=False,  # don't retain meal photos server-side
            )
        except Exception as exc:  # the SDK's interaction error classes are not public API
            raise VisionError(f"Gemini request failed: {exc}") from exc
        if not interaction.output_text:
            raise VisionError(f"Gemini returned no text (status: {interaction.status}).")
        try:
            return MealAnalysis.model_validate_json(interaction.output_text)
        except ValidationError as exc:
            raise VisionError(f"Gemini output did not match the schema: {exc}") from exc
