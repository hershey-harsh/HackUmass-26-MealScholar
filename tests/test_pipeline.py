import math
from datetime import date, datetime

import pytest
from PIL import Image

from mealscholar.campus import serving_grams
from mealscholar.diary import Diary
from mealscholar.dining import DiningUnavailable, DishFacts
from mealscholar.insights import summarize_days
from mealscholar.nutrients import NUTRIENTS
from mealscholar.pipeline import BadCorrection, UnknownFood, Workflow, correct, meal_name
from mealscholar.schemas import FoodItem, MealAnalysis, MenuRef, Range
from mealscholar.vision import MENU_NAMES

WORCESTER, BERKSHIRE = "Worcester Dining Commons", "Berkshire Dining Commons"
LUNCH_DAY = date(2026, 11, 13)
MICRO_KEYS = [n.key for n in NUTRIENTS if n.micro]


def item(name, query, low, likely, high, *, method="unknown", hidden_fat=False, menu_item=None, servings=None):
    low_s, likely_s, high_s = servings if servings is not None else (None, None, None)
    return FoodItem(
        name=name,
        fdc_query=query,
        cooking_method=method,
        grams_low=low,
        grams_likely=likely,
        grams_high=high,
        box_2d=None,
        hidden_fat_likely=hidden_fat,
        menu_item=menu_item,
        servings_low=low_s,
        servings_likely=likely_s,
        servings_high=high_s,
    )


def dish(name, serving, kcal, *, meal="lunch", protein=5.0, carbs=20.0, fat=5.0, score=4, **more):
    per_serving = {"energy_kcal": kcal, "protein_g": protein, "carbs_g": carbs, "fat_g": fat, **more}
    return DishFacts(
        dish_id=f"d-{name.lower().replace(' ', '-')}",
        name=name,
        meal=meal,
        category=None,
        serving_size=serving,
        serving_grams=serving_grams(serving),
        per_serving=per_serving,
        healthfulness_score=score,
    )


class FakeVision:
    def __init__(self, analysis):
        self.analysis = analysis
        self.context = None
        self.refined_with = None

    def analyze(self, jpeg, context):
        self.context = context
        return self.analysis

    def refine(self, analysis, question, answer):
        self.refined_with = (question, answer)
        rice = analysis.items[0].model_copy(update={"hidden_fat_likely": True})
        return analysis.model_copy(update={"items": [rice, *analysis.items[1:]]})


class FakeMenus:
    """dining.Dining as the pipeline sees it: a hall's dishes for a day, and its name."""

    names = {1: WORCESTER, 2: BERKSHIRE}

    def __init__(self, facts):
        self.facts = facts  # {(location_id, day): [DishFacts]}
        self.down = False

    def dishes_on(self, location_id, day):
        if self.down:
            raise DiningUnavailable("MongoDB is down")
        return tuple(self.facts.get((location_id, day), ()))

    def location_name(self, location_id):
        if self.down:
            raise DiningUnavailable("MongoDB is down")
        return self.names.get(location_id)


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


@pytest.fixture
def campus():
    """Worcester's menu on the lunch day; Berkshire has none."""
    lunch = [
        dish("Jasmine Rice", "4 OZ", 186, protein=4, carbs=41, fat=0, sodium_mg=5, score=5),
        dish("Cheese Pizza", "1 Slice", 212, protein=9, carbs=26, fat=8),
        dish("Scrambled Eggs", "3 OZ", 112, protein=9, carbs=1, fat=8),
        dish("Chicken Piccata Pizza", "1/6 PIZZA", 803, protein=35, carbs=80, fat=38),
        dish("12 oz Regular Coffee", "12 OZL", 0, protein=0, carbs=0, fat=0),
        dish("16 oz Latte", "16 OZL", 0, protein=0, carbs=0, fat=0),
    ]
    dinner = [dish("Pad Thai", "8 OZ", 450, meal="dinner")]
    return FakeMenus({(1, LUNCH_DAY): lunch + dinner})


def one(food_item, question=None):
    return MealAnalysis(is_food=True, items=[food_item], scale_reference=None, question=question)


def analyze(food_db, analysis, meal_photo, *, menus=None, location_id=None, device_time=None):
    workflow = Workflow(vision=FakeVision(analysis), foods=food_db, menus=menus)
    result = workflow.analyze(
        meal_photo, device_time=device_time or datetime(2026, 11, 13, 12, 30), location_id=location_id
    )
    return workflow, result


def bounds(r):
    return r.low, r.likely, r.high


def test_totals_are_grams_times_database_values_with_ranges(food_db, analysis, meal_photo):
    _, result = analyze(food_db, analysis, meal_photo)

    rice, pizza, sauce = result.items
    assert (rice.match.fdc_id, pizza.match.fdc_id, sauce.match) == (1, 6, None)
    kcal = result.totals["energy_kcal"]
    assert kcal.likely == pytest.approx(129 * 2.0 + 266 * 1.2)
    # The foods aren't all off in the same direction at once, so the meal range is narrower than
    # every low and every high added up. Part of the error is shared, so it's wider than for
    # independent errors.
    for side, widths in (
        (kcal.likely - kcal.low, (129 * 0.5, 266 * 0.2)),
        (kcal.high - kcal.likely, (129 * 0.6, 266 * 0.3)),
    ):
        assert math.hypot(*widths) < side < sum(widths)
    assert result.daily_value_pct["protein_g"].likely == pytest.approx(100 * (2.6 * 2.0 + 11 * 1.2) / 50)
    # Rice has no vitamin D row, so the vitamin D total covers the pizza only.
    assert "vitamin_d_ug" in result.partial_nutrients
    assert any("Zzyzx" in n or "mystery sauce" in n for n in result.notes)
    assert result.context.meal_slot == "lunch"
    assert result.menu is None and result.name == "white rice, pizza slice and mystery sauce"
    assert all(r.source == "usda" and r.dish is None for r in result.items)


def test_loose_lookup_is_suggested_not_counted(food_db, meal_photo):
    # "Cola bowl" shares only "cola" with "Soft drink, cola"; counting it would put wrong numbers in the totals.
    bowl_only = MealAnalysis(
        is_food=True, items=[item("cola bowl", "Cola bowl", 100, 150, 200)], scale_reference=None, question=None
    )
    _, result = analyze(food_db, bowl_only, meal_photo)

    (bowl,) = result.items
    assert bowl.match is None and 4 in [a.fdc_id for a in bowl.alternatives]
    assert result.totals == {}


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
    # A weighed food adds no width: the meal's range is the pizza's alone.
    kcal = fixed.totals["energy_kcal"]
    assert (kcal.low, kcal.likely, kcal.high) == pytest.approx((288 + 266 * 1.0, 288 + 266 * 1.2, 288 + 266 * 1.5))
    with pytest.raises(UnknownFood):
        correct(result, 0, food_db, fdc_id=999_999)


def test_diary_round_trip_and_usual_portions(tmp_path, food_db, analysis, meal_photo):
    _, result = analyze(food_db, analysis, meal_photo)
    diary = Diary(tmp_path / "diary.sqlite")

    saved = diary.save(result)
    assert diary.get(saved.id) == saved
    assert diary.meals_between(saved.context.taken_at.date(), saved.context.taken_at.date()) == [saved]
    # Meals logged before a change to the totals math are shown with today's totals.
    diary.save(saved.model_copy(update={"totals": {"energy_kcal": Range(low=1, likely=2, high=3)}}))
    assert diary.get(saved.id).totals == saved.totals

    diary.remember_portion("White  Rice", 300)
    diary.remember_portion("white rice", 400)
    assert diary.usual_portions() == {"white rice": 350}


def test_day_total_is_narrower_than_its_meals_added_up(food_db, analysis, meal_photo):
    _, lunch = analyze(food_db, analysis, meal_photo)

    (day,) = summarize_days([lunch, lunch])

    meal, total = lunch.totals["energy_kcal"], day.totals["energy_kcal"]
    assert total.likely == pytest.approx(2 * meal.likely)
    assert meal.high - meal.likely < total.high - total.likely < 2 * (meal.high - meal.likely)


# --- campus menu matching (only with an explicit hall) -------------------------


def test_menu_dish_with_a_weight_serving_uses_its_label_and_usda_micros(food_db, analysis, meal_photo, campus):
    rice = analysis.items[0].model_copy(update={"menu_item": "Jasmine Rice"})
    analysis = analysis.model_copy(update={"items": [rice, *analysis.items[1:]]})
    _, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1)

    rice = result.items[0]
    assert rice.source == "campus" and rice.match.fdc_id == 1  # USDA rice donates micronutrients only
    per_gram = 186 / 113.4  # the label's 186 kcal per 4 OZ, over the model's 150/200/260 g
    assert bounds(rice.nutrients["energy_kcal"]) == pytest.approx((246.0, 328.0, 426.4), abs=0.1)
    assert bounds(rice.nutrients["sodium_mg"]) == pytest.approx((5 * 150 / 113.4, 5 * 200 / 113.4, 5 * 260 / 113.4))
    assert rice.nutrients["energy_kcal"].likely == pytest.approx(per_gram * 200)
    assert (rice.dish.name, rice.dish.serving_size, rice.dish.serving_grams) == ("Jasmine Rice", "4 OZ", 113.4)
    assert (rice.dish.healthfulness_score, rice.dish.meal, rice.dish.location_name) == (5, "lunch", WORCESTER)
    assert rice.dish.per_serving["energy_kcal"] == 186
    assert result.menu == MenuRef(location_id=1, location_name=WORCESTER, date=LUNCH_DAY, dishes=7)
    assert result.name == "white rice, pizza slice and mystery sauce"
    assert f"Campus menu: {WORCESTER}, 2026-11-13." in result.notes
    assert result.totals["energy_kcal"].likely == pytest.approx(per_gram * 200 + 266 * 1.2)


def test_menu_dish_counted_in_servings_keeps_a_range(food_db, meal_photo, campus):
    query = "Pizza, cheese, from restaurant or fast food, thin crust"
    pizza = item("pizza slice", query, 100, 120, 150, menu_item="Cheese Pizza", servings=(1, 1.5, 2))
    _, result = analyze(food_db, one(pizza), meal_photo, menus=campus, location_id=1)

    (slice_,) = result.items
    assert slice_.source == "campus" and slice_.match.fdc_id == 6
    assert bounds(slice_.nutrients["energy_kcal"]) == pytest.approx((212, 318, 424))
    assert bounds(slice_.nutrients["vitamin_d_ug"]) == pytest.approx((0.2 * 1.0, 0.2 * 1.2, 0.2 * 1.5))
    assert all(key in result.partial_nutrients for key in MICRO_KEYS if key != "vitamin_d_ug")
    assert "vitamin_d_ug" not in result.partial_nutrients

    pinned = pizza.model_copy(update={"servings_low": 2, "servings_likely": 2, "servings_high": 2})
    _, result = analyze(food_db, one(pinned), meal_photo, menus=campus, location_id=1)
    low, likely, high = bounds(result.items[0].nutrients["energy_kcal"])
    assert low < likely < high and likely == pytest.approx(424)  # widened by the model's own 100/120 and 150/120
    assert (low, high) == pytest.approx((424 * 100 / 120, 424 * 150 / 120))


def test_a_count_that_does_not_fit_the_plate_falls_back_to_usda(food_db, meal_photo, campus):
    query = "Pizza, cheese, from restaurant or fast food, thin crust"
    three = item("chicken pizza", query, 360, 420, 480, menu_item="Chicken Piccata Pizza", servings=(3, 3, 3))
    _, result = analyze(food_db, one(three), meal_photo, menus=campus, location_id=1)

    (pizza,) = result.items
    assert (pizza.source, pizza.dish, pizza.match.fdc_id) == ("usda", None, 6)
    assert pizza.nutrients["energy_kcal"].likely == pytest.approx(266 * 4.2)
    assert any(
        "Chicken Piccata Pizza (1/6 PIZZA)" in n and "didn't fit the estimated amount" in n for n in result.notes
    )

    one_slice = three.model_copy(update={"servings_low": 1, "servings_likely": 1, "servings_high": 1})
    _, result = analyze(food_db, one(one_slice), meal_photo, menus=campus, location_id=1)
    assert result.items[0].source == "campus"
    assert result.items[0].nutrients["energy_kcal"].likely == pytest.approx(803)


def test_zero_calorie_labels_are_trusted_only_when_usda_agrees(food_db, meal_photo, campus):
    latte = item("latte", "Soft drink, cola", 355, 355, 355, menu_item="16 oz Latte")
    _, result = analyze(food_db, one(latte), meal_photo, menus=campus, location_id=1)
    assert (result.items[0].source, result.items[0].match.fdc_id) == ("usda", 4)
    assert any("16 oz Latte (16 OZL)" in n and "didn't fit" in n for n in result.notes)

    coffee = item("black coffee", "Soft drink, cola, diet", 355, 355, 355, menu_item="12 oz Regular Coffee")
    _, result = analyze(food_db, one(coffee), meal_photo, menus=campus, location_id=1)
    assert result.items[0].source == "campus"
    assert result.items[0].nutrients["energy_kcal"].likely == 0


def test_servings_zero_or_missing_fall_back_with_a_note(food_db, meal_photo, campus):
    query = "Pizza, cheese, from restaurant or fast food, thin crust"
    zero = item("pizza slice", query, 100, 120, 150, menu_item="Cheese Pizza", servings=(0, 0, 0))
    assert (zero.menu_item, zero.servings_likely) == ("Cheese Pizza", None)  # the schema nulls the count
    for pizza in (zero, item("pizza slice", query, 100, 120, 150, menu_item="Cheese Pizza")):
        _, result = analyze(food_db, one(pizza), meal_photo, menus=campus, location_id=1)
        assert (result.items[0].source, result.items[0].match.fdc_id) == ("usda", 6)
        assert any("Cheese Pizza (1 Slice)" in n and "no serving count was estimated" in n for n in result.notes)


def test_menu_item_null_never_matches_by_name(food_db, meal_photo, campus):
    query = "Egg omelet or scrambled egg, with cheese, made with oil"
    for menu_item, source in ((None, "usda"), ("Scrambled Egg", "campus"), ("Chicken Piccata", "usda")):
        egg = item("scrambled egg", query, 80, 100, 120, menu_item=menu_item)
        _, result = analyze(food_db, one(egg), meal_photo, menus=campus, location_id=1)
        assert result.items[0].source == source, menu_item
        assert (result.items[0].dish is None) == (source == "usda")
    assert result.items[0].match.fdc_id == 19


def test_items_not_on_the_menu_take_the_usda_path_unchanged(food_db, analysis, meal_photo, campus):
    _, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1)

    rice, pizza, sauce = result.items
    assert (rice.match.fdc_id, pizza.match.fdc_id, sauce.match) == (1, 6, None)
    assert all(r.source == "usda" and r.dish is None for r in result.items)
    assert any("No close USDA match for 'mystery sauce'" in n for n in result.notes)
    assert result.menu.dishes == 7


def test_without_a_hall_nothing_is_matched(food_db, analysis, meal_photo, campus):
    rice = analysis.items[0].model_copy(update={"menu_item": "Jasmine Rice"})
    analysis = analysis.model_copy(update={"items": [rice, *analysis.items[1:]]})
    workflow, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=None)

    assert result.menu is None
    assert all(r.source == "usda" for r in result.items)
    assert workflow.vision.context.startswith("Photo taken Fri 2026-11-13 12:30 local time (lunch).")
    assert "Today's menu" not in workflow.vision.context


def test_hall_problems_are_notes_not_errors(food_db, analysis, meal_photo, campus):
    _, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=9)
    assert "No dining location 9; USDA used." in result.notes and result.menu is None

    _, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=2)
    assert f"No campus menu for {BERKSHIRE} on 2026-11-13; USDA used." in result.notes
    assert result.menu == MenuRef(location_id=2, location_name=BERKSHIRE, date=LUNCH_DAY, dishes=0)

    campus.down = True
    _, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1)
    assert "Campus menu unavailable right now; USDA used." in result.notes and result.menu is None

    _, result = analyze(food_db, analysis, meal_photo, menus=None, location_id=1)
    assert "Campus menus aren't connected; USDA used." in result.notes and result.menu is None
    assert all(r.source == "usda" for r in result.items)


def test_late_night_after_midnight_uses_the_previous_days_menu(food_db, analysis, meal_photo, campus):
    after_midnight = datetime(2026, 11, 14, 0, 30)
    campus.facts[(1, LUNCH_DAY)].append(dish("Mozzarella Sticks", "6 EACH", 540, meal="late_night"))
    workflow, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1, device_time=after_midnight)
    assert result.menu.date == LUNCH_DAY and result.menu.dishes == 8
    assert workflow.vision.context.splitlines()[2] == "- Mozzarella Sticks (serving: 6 EACH)"

    campus.facts[(1, LUNCH_DAY)].pop()
    _, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1, device_time=after_midnight)
    assert (result.menu.date, result.menu.dishes) == (date(2026, 11, 14), 0)


def full_day():
    """A Dining Commons day the size of a real one (Worcester, 2026-11-13: 171 dishes), more than
    MENU_NAMES, with the campus fixture's Cheese Pizza served at dinner and a late-night dish."""
    counts = {"breakfast": 23, "lunch": 55, "dinner": 63, "grab_n_go": 17, "late_night": 11}
    facts = [
        dish(f"{meal.title()} dish {n}", "4 OZ", 100 + n, meal=meal)
        for meal, count in counts.items()
        for n in range(count)
    ]
    facts.append(dish("Cheese Pizza", "1 Slice", 212, meal="dinner", protein=9, carbs=26, fat=8))
    facts.append(dish("Mozzarella Sticks", "6 EACH", 540, meal="late_night"))
    return FakeMenus({(1, LUNCH_DAY): facts})


def listed_meals(prompt, menus):
    """How many dishes of each meal the prompt lists."""
    by_name = {d.name: d.meal for d in menus.facts[(1, LUNCH_DAY)]}
    names = [line[2:].split(" (serving:")[0] for line in prompt.splitlines() if line.startswith("- ")]
    assert len(names) == MENU_NAMES
    counts = {meal: sum(1 for name in names if by_name[name] == meal) for meal in set(by_name.values())}
    return {meal: count for meal, count in counts.items() if count}


def test_the_meal_on_now_is_listed_whole_when_the_day_outgrows_the_prompt(food_db, meal_photo):
    """16:45 is slot 'snack' but dinner by the dining hours; 21:30 is slot 'dinner' but late night."""
    menus = full_day()
    pizza = item(
        "pizza slice",
        "Pizza, cheese, from restaurant or fast food, thin crust",
        100,
        120,
        150,
        menu_item="Cheese Pizza",
        servings=(1, 1.5, 2),
    )
    at = datetime(2026, 11, 13, 16, 45)
    workflow, result = analyze(food_db, one(pizza), meal_photo, menus=menus, location_id=1, device_time=at)
    assert result.context.meal_slot == "snack"
    assert listed_meals(workflow.vision.context, menus) == {"dinner": 64, "grab_n_go": 17, "lunch": 19}
    assert workflow.vision.context.splitlines()[2] == "- Dinner dish 0 (serving: 4 OZ)"
    assert (result.items[0].source, result.items[0].dish.meal, result.menu.dishes) == ("campus", "dinner", 171)

    sticks = item(
        "mozzarella sticks", "Cheese, mozzarella", 100, 120, 150, menu_item="Mozzarella Sticks", servings=(1, 1, 1)
    )
    at = datetime(2026, 11, 13, 21, 30)
    workflow, result = analyze(food_db, one(sticks), meal_photo, menus=menus, location_id=1, device_time=at)
    assert result.context.meal_slot == "dinner"
    assert listed_meals(workflow.vision.context, menus) == {"late_night": 12, "grab_n_go": 17, "dinner": 64, "lunch": 7}
    assert workflow.vision.context.splitlines()[2] == "- Late_Night dish 0 (serving: 4 OZ)"
    assert result.items[0].dish.meal == "late_night"

    at = datetime(2026, 11, 13, 15, 30)
    workflow, _ = analyze(food_db, one(pizza), meal_photo, menus=menus, location_id=1, device_time=at)
    assert listed_meals(workflow.vision.context, menus) == {"lunch": 55, "grab_n_go": 17, "dinner": 28}


def test_prompt_lists_the_meals_dishes_first_and_the_date(food_db, analysis, meal_photo, campus):
    workflow, _ = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1)

    prompt = workflow.vision.context
    assert prompt.startswith("Photo taken Fri 2026-11-13 12:30 local time (lunch).")
    assert f"Today's menu at {WORCESTER}, dishes for this meal first:" in prompt
    assert prompt.index("- Cheese Pizza (serving: 1 Slice)") < prompt.index("- Pad Thai (serving: 8 OZ)")
    assert "name stays what the photo shows" in prompt


def test_answer_rematches_against_the_same_menu(food_db, analysis, meal_photo, campus):
    rice = analysis.items[0].model_copy(update={"menu_item": "Jasmine Rice"})
    analysis = analysis.model_copy(update={"items": [rice, *analysis.items[1:]]})
    workflow, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1)

    answered = workflow.answer(result, "yes, oil")
    assert answered.items[0].source == "campus" and answered.items[0].match.fdc_id == 3
    assert answered.menu == result.menu and f"Campus menu: {WORCESTER}, 2026-11-13." in answered.notes

    offline = Workflow(vision=workflow.vision, foods=food_db, menus=None).answer(result, "yes, oil")
    assert offline.items[0].source == "usda"
    assert "Campus menus aren't connected; USDA used for this answer." in offline.notes

    campus.down = True
    down = workflow.answer(result, "yes, oil")
    assert down.items[0].source == "usda" and "Campus menu unavailable right now; USDA used." in down.notes


@pytest.fixture
def campus_meal(food_db, analysis, meal_photo, campus):
    """The analysis fixture matched to Worcester: the rice is Jasmine Rice (4 OZ), the pizza a
    Cheese Pizza slice (1 Slice, 1-2 of them), the sauce not on the menu."""
    rice = analysis.items[0].model_copy(update={"menu_item": "Jasmine Rice"})
    pizza = analysis.items[1].model_copy(
        update={"menu_item": "Cheese Pizza", "servings_low": 1.0, "servings_likely": 1.5, "servings_high": 2.0}
    )
    analysis = analysis.model_copy(update={"items": [rice, pizza, analysis.items[2]]})
    workflow, result = analyze(food_db, analysis, meal_photo, menus=campus, location_id=1)
    assert [r.source for r in result.items] == ["campus", "campus", "usda"]
    return workflow, result


def test_corrections_on_campus_items(food_db, campus_meal):
    _, result = campus_meal
    flat = lambda r: r.low == r.likely == r.high  # noqa: E731

    two_slices = correct(result, 1, food_db, servings=2).items[1]
    assert (two_slices.source, two_slices.dish.name, two_slices.match.fdc_id) == ("campus", "Cheese Pizza", 6)
    assert flat(two_slices.nutrients["energy_kcal"]) and two_slices.nutrients["energy_kcal"].likely == 424
    assert (two_slices.item.servings_low, two_slices.item.servings_likely, two_slices.item.servings_high) == (2, 2, 2)
    assert not flat(two_slices.nutrients["vitamin_d_ug"])  # micros still follow the model's gram range

    weighed_rice = correct(result, 0, food_db, grams=200).items[0]
    assert (weighed_rice.source, weighed_rice.dish.name) == ("campus", "Jasmine Rice")
    assert flat(weighed_rice.nutrients["energy_kcal"])
    assert weighed_rice.nutrients["energy_kcal"].likely == pytest.approx(186 / 113.4 * 200)

    two_rice = correct(result, 0, food_db, servings=2).items[0]
    assert (two_rice.source, two_rice.item.grams_low, two_rice.item.grams_high) == ("campus", 227, 227)
    assert flat(two_rice.nutrients["energy_kcal"])
    assert two_rice.nutrients["energy_kcal"].likely == pytest.approx(186 / 113.4 * 227)

    weighed = correct(result, 1, food_db, grams=250)
    slice_ = weighed.items[1]
    assert (slice_.source, slice_.dish, slice_.match.fdc_id, slice_.item.menu_item) == ("usda", None, 6, None)
    assert slice_.item.servings_likely is None
    assert flat(slice_.nutrients["energy_kcal"]) and slice_.nutrients["energy_kcal"].likely == pytest.approx(266 * 2.5)
    note = (
        "'pizza slice' has an amount in grams now, so USDA numbers are used; the menu lists Cheese Pizza per 1 Slice."
    )
    assert note in weighed.notes
    assert not any("weigh" in n for n in weighed.notes), "nobody weighs anything"
    assert weighed.totals["energy_kcal"].likely == pytest.approx(186 / 113.4 * 200 + 266 * 2.5)

    old_donor = result.items[0].match
    swapped = correct(result, 0, food_db, fdc_id=7).items[0]
    assert (swapped.source, swapped.dish, swapped.match.fdc_id, swapped.item.menu_item) == ("usda", None, 7, None)
    assert old_donor in swapped.alternatives
    assert swapped.nutrients["energy_kcal"].likely == pytest.approx(230 * 2)

    with pytest.raises(BadCorrection, match="not both"):
        correct(result, 0, food_db, grams=100, servings=1)
    with pytest.raises(BadCorrection, match="isn't a campus dish"):
        correct(result, 2, food_db, servings=1)
    with pytest.raises(UnknownFood):
        correct(result, 0, food_db, fdc_id=999_999)


def test_answer_after_a_usda_switch_does_not_rematch(food_db, campus_meal):
    workflow, result = campus_meal
    switched = correct(result, 0, food_db, fdc_id=7)

    answered = workflow.answer(switched, "yes, oil")
    assert (answered.items[0].source, answered.items[0].dish) == ("usda", None)
    assert answered.items[1].source == "campus"  # the slice was never switched


def test_summarize_counts_a_campus_item_without_a_usda_donor(food_db, meal_photo, campus):
    rice = item("mystery rice", "Zzyzx", 150, 200, 260, menu_item="Jasmine Rice")
    _, result = analyze(food_db, one(rice), meal_photo, menus=campus, location_id=1)

    (rice,) = result.items
    assert (rice.source, rice.match, rice.dish.name) == ("campus", None, "Jasmine Rice")
    assert result.totals["energy_kcal"].likely == pytest.approx(186 * 200 / 113.4)
    assert all(key in result.partial_nutrients for key in MICRO_KEYS) and len(MICRO_KEYS) == 10
    assert not any("No close USDA match" in n for n in result.notes)


def test_meal_name_joins_the_items():
    names = ["rice", "pizza", "sauce", "salad", "water"]
    items = [item(n, n, 1, 2, 3) for n in names]
    assert meal_name([]) is None
    assert meal_name(items[:1]) == "rice"
    assert meal_name(items[:2]) == "rice and pizza"
    assert meal_name(items[:3]) == "rice, pizza and sauce"
    assert meal_name(items) == "rice, pizza, sauce and 2 more"
