"""What Gemini is told (the output schema as sent, the prompts, the context with a hall's menu) and how
one call is bounded: a timeout, one retry on an unusable reply, none on an SDK error or a final status."""

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from mealscholar.campus import serving_grams
from mealscholar.dining import DishFacts
from mealscholar.schemas import MealAnalysis, MealContext
from mealscholar.vision import (
    MENU_NAMES,
    REFINE_PROMPT,
    SYSTEM_PROMPT,
    GeminiVision,
    VisionError,
    context_prompt,
    response_schema,
)

MENU_FIELDS = ("menu_item", "servings_low", "servings_likely", "servings_high")


def dish(name, meal="lunch", serving=None):
    return DishFacts(
        dish_id=None,
        name=name,
        meal=meal,
        category=None,
        serving_size=serving,
        serving_grams=serving_grams(serving),
        per_serving={"energy_kcal": 100},
        healthfulness_score=None,
    )


def test_menu_fields_are_required_but_nullable_for_gemini_and_optional_for_stored_rows():
    required = response_schema()["$defs"]["FoodItem"]["required"]
    assert all(field in required for field in MENU_FIELDS)
    assert "name" in required and len(required) == len(set(required))
    # Diary rows and bench runs recorded before the fields existed still validate.
    old = {
        "is_food": True,
        "items": [
            {
                "name": "white rice",
                "fdc_query": "Rice, white, cooked",
                "cooking_method": "steamed",
                "grams_low": 150,
                "grams_likely": 200,
                "grams_high": 260,
                "box_2d": None,
                "hidden_fat_likely": False,
            }
        ],
        "scale_reference": None,
        "question": None,
    }
    (item,) = MealAnalysis.model_validate_json(json.dumps(old)).items
    assert (item.menu_item, item.servings_low, item.servings_likely, item.servings_high) == (None, None, None, None)
    assert "menu_item" in SYSTEM_PROMPT and "Keep menu_item" in REFINE_PROMPT


def test_context_prompt_names_the_date_and_lists_the_menu():
    context = MealContext(taken_at=datetime(2026, 11, 13, 12, 30), time_source="device", meal_slot="lunch")
    assert context_prompt(context, {}) == "Photo taken Fri 2026-11-13 12:30 local time (lunch)."

    menu = [dish("Cheese Pizza", serving="1 Slice"), dish("Pad Thai", "dinner", "8 OZ"), dish("Water")]
    prompt = context_prompt(context, {"white rice": 200}, menu, hall="Worcester Dining Commons")
    lines = prompt.splitlines()
    assert lines[0] == "Photo taken Fri 2026-11-13 12:30 local time (lunch)."
    assert lines[1] == "This user's usual portions from past corrections: white rice: 200 g."
    assert lines[2] == "Today's menu at Worcester Dining Commons, dishes for this meal first:"
    assert lines[3:6] == [
        "- Cheese Pizza (serving: 1 Slice)",
        "- Pad Thai (serving: 8 OZ)",
        "- Water (serving: unknown)",
    ]
    assert lines[6].startswith("The photo was taken here, so for each food pick the listed dish of the same kind")
    assert "set menu_item to that exact name" in lines[6] and "Estimate grams for every item either way." in lines[6]
    assert "Today's menu" not in context_prompt(context, {}, [], hall="Worcester Dining Commons")

    many = [dish(f"Dish {n}") for n in range(MENU_NAMES + 20)]
    assert context_prompt(context, {}, many, hall="Hall").count("\n- ") == MENU_NAMES


def test_bench_fingerprint_hashes_the_schema_as_sent():
    path = Path(__file__).resolve().parent.parent / "bench" / "run.py"
    spec = importlib.util.spec_from_file_location("bench_run", path)
    run = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = run  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(run)
    told = SYSTEM_PROMPT + REFINE_PROMPT + json.dumps(response_schema(), sort_keys=True)
    assert run.PROMPT == hashlib.sha256(told.encode()).hexdigest()[:8]


VALID = json.dumps(
    {
        "is_food": True,
        "items": [
            {
                "name": "cheese pizza",
                "fdc_query": "Pizza, cheese, from restaurant or fast food, thin crust",
                "cooking_method": "baked",
                "grams_low": 100,
                "grams_likely": 130,
                "grams_high": 170,
                "box_2d": [100, 100, 900, 900],
                "hidden_fat_likely": False,
                "menu_item": None,
                "servings_low": None,
                "servings_likely": None,
                "servings_high": None,
            }
        ],
        "scale_reference": "dinner plate",
        "question": None,
    }
)


class FakeInteractions:
    """Stands in for client.interactions (the seam bench/run.py uses): records every create() call
    and answers with the scripted replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(text, status="completed"):
    return SimpleNamespace(status=status, output_text=text)


def vision_with(*replies):
    vision = GeminiVision("k", "m", "low")
    fake = FakeInteractions(*replies)
    vision._client = SimpleNamespace(interactions=fake)
    return vision, fake


def test_create_kwargs():
    vision, fake = vision_with(reply(VALID))
    analysis = vision.analyze(b"jpegbytes", "Photo taken Fri 2026-11-13 12:30 local time (lunch).")
    assert analysis.items[0].name == "cheese pizza"
    (kwargs,) = fake.calls
    assert kwargs["model"] == "m"
    assert kwargs["timeout"] == 60.0
    assert kwargs["store"] is False
    assert kwargs["generation_config"] == {"thinking_level": "low"}
    assert kwargs["response_format"]["mime_type"] == "application/json"
    assert "menu_item" in kwargs["response_format"]["schema"]["$defs"]["FoodItem"]["required"]
    assert "cut off by the edge" in kwargs["system_instruction"] and "one sentence" in kwargs["system_instruction"]
    text, image = kwargs["input"]
    assert text["type"] == "text" and text["text"].startswith("Photo taken Fri 2026-11-13 12:30")
    assert image == {"type": "image", "data": "anBlZ2J5dGVz", "mime_type": "image/jpeg"}

    custom, fake = vision_with(reply(VALID))
    custom._timeout = 5.0
    custom.analyze(b"x", "ctx")
    assert fake.calls[0]["timeout"] == 5.0
    assert GeminiVision("k", "m", "low", timeout=12.5)._timeout == 12.5


def test_truncated_output_is_retried_once(caplog):
    vision, fake = vision_with(reply('{"is_food": true, "items": [', status="incomplete"), reply(VALID))
    with caplog.at_level("WARNING", logger="mealscholar.vision"):
        analysis = vision.analyze(b"jpeg", "ctx")
    assert analysis.is_food and len(analysis.items) == 1
    assert len(fake.calls) == 2 and fake.calls[0]["input"] == fake.calls[1]["input"]
    assert "incomplete" in caplog.text


def test_schema_miss_twice_is_a_vision_error():
    vision, fake = vision_with(reply('{"foo": 1}'), reply('{"foo": 1}'))
    with pytest.raises(VisionError, match="schema"):
        vision.analyze(b"jpeg", "ctx")
    assert len(fake.calls) == 2


def test_empty_reply_twice_is_a_vision_error():
    vision, fake = vision_with(reply("", status="completed"), reply(None, status="completed"))
    with pytest.raises(VisionError, match="no text"):
        vision.analyze(b"jpeg", "ctx")
    assert len(fake.calls) == 2


def test_failed_status_is_not_retried():
    vision, fake = vision_with(reply("", status="failed"), reply(VALID))
    with pytest.raises(VisionError, match="failed"):
        vision.analyze(b"jpeg", "ctx")
    assert len(fake.calls) == 1


def test_sdk_exception_is_a_vision_error():
    vision, fake = vision_with(RuntimeError("boom"), reply(VALID))
    with pytest.raises(VisionError, match="boom"):
        vision.analyze(b"jpeg", "ctx")
    assert len(fake.calls) == 1


def test_refine_sends_text_only():
    vision, fake = vision_with(reply(VALID))
    analysis = MealAnalysis.model_validate_json(VALID)
    vision.refine(analysis, "How many slices?", "two")
    (kwargs,) = fake.calls
    (part,) = kwargs["input"]
    assert part["type"] == "text"
    assert analysis.model_dump_json() in part["text"] and "two" in part["text"]
    assert kwargs["timeout"] == 60.0


def test_prompts_carry_the_benchmark_fixes():
    assert "never add a second entry" in REFINE_PROMPT
    assert "edge of the photo" in SYSTEM_PROMPT and "one sentence" in SYSTEM_PROMPT


def test_context_prompt_says_when_no_menu_is_posted():
    context = MealContext(taken_at=datetime(2026, 10, 7, 12, 32), time_source="exif", meal_slot="lunch")
    past = [dish("Chicken Tikka Masala Rasoi", "any", "4 oz"), dish("Berkshire Burger", "any")]
    lines = context_prompt(context, {}, past, hall="Berkshire").splitlines()
    assert lines[1] == "Dishes Berkshire has served (no menu is posted for this day), most common first:"
    assert lines[2] == "- Chicken Tikka Masala Rasoi (serving: 4 oz)"
