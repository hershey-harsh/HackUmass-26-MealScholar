"""What Gemini is told (the output schema as sent, the prompts, the context with a hall's menu) and how
one call is bounded: a timeout, one retry on an unusable reply, none on an SDK error or a final status."""

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from mealscholar.campus import serving_grams
from mealscholar.dining import DishFacts
from mealscholar.schemas import MealAnalysis, MealContext
from mealscholar.vision import (
    ENUM_CHARS,
    ENUM_NAMES,
    MENU_NAMES,
    MENU_RULE,
    REFINE_PROMPT,
    SYSTEM_PROMPT,
    GeminiVision,
    VisionError,
    closed_set,
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
    assert lines[6] == MENU_RULE
    assert "Today's menu" not in context_prompt(context, {}, [], hall="Worcester Dining Commons")

    many = [dish(f"Dish {n}") for n in range(MENU_NAMES + 20)]
    assert context_prompt(context, {}, many, hall="Hall").count("\n- ") == MENU_NAMES


def test_the_menu_rule_is_strict_not_eager():
    """The model names a listed dish only when the photo shows that dish; a similar one is not it."""
    for text in (SYSTEM_PROMPT, MENU_RULE):
        assert "pho is not ramen" in text and "exactly as written" in text
        assert "settle what the food is" not in text and "same kind" in text  # "a similar dish of the same kind"
    assert (
        "IS that\n  dish, judged from what you can see: its broth, noodles, protein, toppings and shape"
        in SYSTEM_PROMPT
    )
    assert "menu_item\n  stays null and you name the food from what the photo shows" in SYSTEM_PROMPT
    assert "When you set menu_item, name is the menu's dish name." in SYSTEM_PROMPT
    # Two listed dishes that both fit: the likelier one, without a question.
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "set menu_item to the likelier one without asking" in flat
    # The example names no dish a hall really serves next to noodle soups, so it can't prime a pick.
    assert "Tantanmen" not in SYSTEM_PROMPT and "Pho or" not in SYSTEM_PROMPT
    # Questions are for drinks the photo can't settle, almost never; food gets a best estimate.
    assert "Ask only about a drink you can't judge from the photo" in flat
    assert "diet or regular soda" in flat and "Food never gets a question" in flat
    assert "Never ask about cooking oil" in flat and "Ask it in one sentence." in flat
    assert "only when the food in the photo is that dish" in MENU_RULE
    assert "Otherwise leave menu_item and the servings null and name the food from the photo." in MENU_RULE


def menu_item_field(schema):
    return schema["$defs"]["FoodItem"]["properties"]["menu_item"]


def test_menu_item_is_an_enum_of_the_listed_names():
    names = ["Chicken Pho Broth (Fish)", "Pho Noodles", "Tantanmen Ramen Bar", "Pho Noodles"]
    field = response_schema(names)["$defs"]["FoodItem"]["properties"]["menu_item"]
    assert field["anyOf"] == [
        {"type": "string", "enum": ["Chicken Pho Broth (Fish)", "Pho Noodles", "Tantanmen Ramen Bar"]},
        {"type": "null"},
    ]
    plain = response_schema()["$defs"]["FoodItem"]["properties"]["menu_item"]
    assert plain["anyOf"] == [{"type": "string"}, {"type": "null"}]
    # The rest of the schema is the same either way.
    with_enum = response_schema(names)
    with_enum["$defs"]["FoodItem"]["properties"]["menu_item"] = plain
    assert with_enum == response_schema()
    # Too many names, or too long a list, go as the plain string; the pipeline checks the name.
    # Gemini refuses a 400-name enum ("too much branching"): an undated photo's list goes plain.
    assert ENUM_NAMES < MENU_NAMES
    assert closed_set(["x"] * ENUM_NAMES) and not closed_set(["x"] * (ENUM_NAMES + 1)) and not closed_set([])
    assert not closed_set(["x" * (ENUM_CHARS // 2 + 1)] * 2)
    assert menu_item_field(response_schema(["x"] * (ENUM_NAMES + 1))) == plain


def test_context_prompt_for_a_photo_without_a_date_names_the_days():
    context = MealContext(taken_at=datetime(2026, 11, 14, 15, 33), time_source="device", meal_slot="lunch")
    menu = [dish("Tantanmen Ramen Bar"), dish("Chicken Pho Broth (Fish)", "dinner", "12 OZL")]
    window = (date(2026, 11, 10), date(2026, 11, 28))
    lines = context_prompt(context, {}, menu, hall="Worcester", window=window).splitlines()
    assert lines[1] == (
        "The photo has no date, so these are dishes Worcester served from Tue Nov 10 to Sat Nov 28, "
        "the likeliest first:"
    )
    assert lines[2:4] == ["- Tantanmen Ramen Bar (serving: unknown)", "- Chicken Pho Broth (Fish) (serving: 12 OZL)"]
    assert lines[4] == MENU_RULE


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


def test_the_listed_names_go_as_the_enum_and_a_refused_enum_falls_back_once():
    names = ["Chicken Pho Broth (Fish)", "Tantanmen Ramen Bar"]
    vision, fake = vision_with(reply(VALID))
    vision.analyze(b"jpeg", "ctx", names)
    menu_item = fake.calls[0]["response_format"]["schema"]["$defs"]["FoodItem"]["properties"]["menu_item"]
    assert menu_item["anyOf"][0]["enum"] == names

    refused = RuntimeError("400 INVALID_ARGUMENT: schema too complex")
    refused.status_code = 400
    vision, fake = vision_with(refused, reply(VALID), reply(VALID), reply(VALID))
    assert vision.analyze(b"jpeg", "ctx", names).items[0].name == "cheese pizza"
    first, second = (call["response_format"]["schema"] for call in fake.calls)
    assert "enum" in menu_item_field(first)["anyOf"][0] and "enum" not in menu_item_field(second)["anyOf"][0]
    # That size isn't sent as an enum again; a shorter list still is.
    vision.analyze(b"jpeg", "ctx", names)
    vision.analyze(b"jpeg", "ctx", names[:1])
    third, fourth = (call["response_format"]["schema"] for call in fake.calls[2:])
    assert "enum" not in menu_item_field(third)["anyOf"][0] and menu_item_field(fourth)["anyOf"][0]["enum"] == names[:1]

    # A 400 the plain field fails too (a bad key, say) isn't about the enum: the limit stays.
    bad_key = RuntimeError("400 INVALID_ARGUMENT: API key not valid")
    bad_key.status_code = 400
    vision, fake = vision_with(bad_key, bad_key, reply(VALID))
    with pytest.raises(VisionError, match="API key"):
        vision.analyze(b"jpeg", "ctx", names)
    vision.analyze(b"jpeg", "ctx", names)
    assert menu_item_field(fake.calls[2]["response_format"]["schema"])["anyOf"][0]["enum"] == names

    vision, fake = vision_with(refused)  # without the enum a 400 is final, as before
    with pytest.raises(VisionError, match="INVALID_ARGUMENT"):
        vision.analyze(b"jpeg", "ctx")
    assert len(fake.calls) == 1

    vision, fake = vision_with(reply(VALID))
    vision.refine(MealAnalysis.model_validate_json(VALID), "Pho or ramen?", "pho", names)
    assert menu_item_field(fake.calls[0]["response_format"]["schema"])["anyOf"][0]["enum"] == names


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
