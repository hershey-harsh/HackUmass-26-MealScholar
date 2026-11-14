from datetime import datetime

import pytest
from PIL import Image

from foodsmart.diary import Diary
from foodsmart.pipeline import UnknownFood, Workflow, correct
from foodsmart.schemas import FoodItem, MealAnalysis


def item(name, query, low, likely, high, *, method="unknown", hidden_fat=False):
    return FoodItem(
        name=name,
        fdc_query=query,
        cooking_method=method,
        grams_low=low,
        grams_likely=likely,
        grams_high=high,
        box_2d=None,
        hidden_fat_likely=hidden_fat,
    )


class FakeVision:
    def __init__(self, analysis):
        self.analysis = analysis
        self.refined_with = None

    def analyze(self, jpeg, context):
        return self.analysis

    def refine(self, analysis, question, answer):
        self.refined_with = (question, answer)
        rice = analysis.items[0].model_copy(update={"hidden_fat_likely": True})
        return analysis.model_copy(update={"items": [rice, *analysis.items[1:]]})


@pytest.fixture
def meal_photo(tmp_path):
    path = tmp_path / "meal.jpg"
    Image.new("RGB", (64, 48), "tan").save(path)
    return path


@pytest.fixture
def analysis():
    return MealAnalysis(
        is_food=True,
        items=[
            item("white rice", "Rice, white, cooked", 150, 200, 260, method="steamed"),
            item("pizza slice", "Pizza, cheese, from restaurant or fast food, thin crust", 100, 120, 150),
            item("mystery sauce", "Zzyzx", 10, 20, 30),
        ],
        scale_reference="dinner fork",
        question="Was the rice cooked with oil?",
    )


def analyze(food_db, analysis, meal_photo):
    workflow = Workflow(vision=FakeVision(analysis), foods=food_db)
    result = workflow.analyze(meal_photo, device_time=datetime(2026, 11, 13, 12, 30))
    return workflow, result


def test_totals_are_grams_times_database_values_with_ranges(food_db, analysis, meal_photo):
    _, result = analyze(food_db, analysis, meal_photo)

    rice, pizza, sauce = result.items
    assert (rice.match.fdc_id, pizza.match.fdc_id, sauce.match) == (1, 6, None)
    kcal = result.totals["energy_kcal"]
    assert kcal.low == pytest.approx(129 * 1.5 + 266 * 1.0)
    assert kcal.likely == pytest.approx(129 * 2.0 + 266 * 1.2)
    assert kcal.high == pytest.approx(129 * 2.6 + 266 * 1.5)
    assert result.daily_value_pct["protein_g"].likely == pytest.approx(100 * (2.6 * 2.0 + 11 * 1.2) / 50)
    # Rice has no vitamin D row, so the vitamin D total covers the pizza only.
    assert "vitamin_d_ug" in result.partial_nutrients
    assert any("Zzyzx" in n or "mystery sauce" in n for n in result.notes)
    assert result.context.meal_slot == "lunch"


def test_answer_recomputes_from_refined_analysis(food_db, analysis, meal_photo):
    workflow, result = analyze(food_db, analysis, meal_photo)
    answered = workflow.answer(result.model_copy(update={"id": 7}), "yes, oil")

    assert workflow.vision.refined_with == ("Was the rice cooked with oil?", "yes, oil")
    assert answered.items[0].match.fdc_id == 3  # "made with oil"
    assert (answered.id, answered.answer, answered.question) == (7, "yes, oil", None)


def test_correction_collapses_range_and_swaps_match(food_db, analysis, meal_photo):
    _, result = analyze(food_db, analysis, meal_photo)
    old_match = result.items[0].match

    fixed = correct(result, 0, food_db, grams=300, fdc_id=2)

    rice = fixed.items[0]
    assert (rice.item.grams_low, rice.item.grams_likely, rice.item.grams_high) == (300, 300, 300)
    assert rice.match.fdc_id == 2 and old_match in rice.alternatives
    assert rice.nutrients["energy_kcal"].likely == pytest.approx(96 * 3)
    assert fixed.totals["energy_kcal"].likely == pytest.approx(96 * 3 + 266 * 1.2)
    with pytest.raises(UnknownFood):
        correct(result, 0, food_db, fdc_id=999_999)


def test_diary_round_trip_and_usual_portions(tmp_path, food_db, analysis, meal_photo):
    _, result = analyze(food_db, analysis, meal_photo)
    diary = Diary(tmp_path / "diary.sqlite")

    saved = diary.save(result)
    assert diary.get(saved.id) == saved
    assert diary.meals_between(saved.context.taken_at.date(), saved.context.taken_at.date()) == [saved]

    diary.remember_portion("White  Rice", 300)
    diary.remember_portion("white rice", 400)
    assert diary.usual_portions() == {"white rice": 350}
