"""What Gemini is told: the output schema as sent, and the context prompt with a hall's menu."""

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

from mealscholar.campus import serving_grams
from mealscholar.dining import DishFacts
from mealscholar.schemas import MealAnalysis, MealContext
from mealscholar.vision import MENU_NAMES, REFINE_PROMPT, SYSTEM_PROMPT, context_prompt, response_schema

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
    assert lines[6].startswith("If a food is one of these dishes, set menu_item to that exact name")
    assert "name stays what the photo shows" in lines[6] and "Estimate grams for every item either way." in lines[6]
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
