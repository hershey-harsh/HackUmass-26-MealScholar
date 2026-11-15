"""Campus menu facts, pure: serving sizes, which dishes to match and the one the model named, a
serving count as a range, the label scaled to the plate, and whether it fits what USDA says."""

from datetime import date, time

import pytest

from mealscholar.campus import (
    candidates,
    dish_nutrients,
    find_dish,
    label_fits,
    meal_order,
    minutes_away,
    serving_grams,
    servings_range,
    window_candidates,
)
from mealscholar.dining import MEAL_HOURS, MEALS, DishFacts
from mealscholar.schemas import Range


def dish(name, meal="lunch", serving=None, day=None, **per_serving):
    return DishFacts(
        dish_id=None,
        name=name,
        meal=meal,
        category=None,
        serving_size=serving,
        serving_grams=serving_grams(serving),
        per_serving=per_serving,
        healthfulness_score=None,
        date=day,
    )


def bounds(r):
    return r.low, r.likely, r.high


@pytest.mark.parametrize(
    ("text", "grams"),
    [
        ("3 OZ", 85.05),
        ("2 1/2 OZ", 70.87),
        ("1/2 oz", 14.17),
        ("4oz", 113.4),
        ("20 OZL", 591.4),
        ("12 ozl", 354.84),
        ("5 2/3 OZL", 167.56),
        ("8 fl oz", 236.56),
        ("1 EACH", None),
        ("1 Slice", None),
        ("1/6 PIZZA", None),
        ("1/2 Sandwich", None),
        ("1 roll", None),
        ("0 oz", None),
        ("", None),
        (None, None),
    ],
)
def test_serving_grams_table(text, grams):
    assert serving_grams(text) == grams


@pytest.mark.parametrize(
    ("at", "first", "then"),
    [
        (time(12, 30), "lunch", ("breakfast", "dinner", "late_night")),  # breakfast ended 2 h ago, dinner in 4 h
        (time(15, 30), "lunch", ("dinner", "breakfast", "late_night")),  # slot 'snack', but lunch is on until 16:00
        (time(16, 5), "lunch", ("dinner", "late_night", "breakfast")),  # 5 min after lunch, 25 before dinner
        (time(16, 15), "lunch", ("dinner", "late_night", "breakfast")),  # a tie goes to the earlier meal
        (time(16, 45), "dinner", ("lunch", "late_night", "breakfast")),  # slot 'snack' until 17:00, dinner since 16:30
        (time(21, 30), "late_night", ("dinner", "lunch", "breakfast")),  # slot 'dinner' until 22:00
        (time(0, 30), "late_night", ("dinner", "breakfast", "lunch")),  # 31 min after the 23:59 close
        (time(5, 0), "breakfast", ("late_night", "lunch", "dinner")),
    ],
)
def test_meal_order_follows_the_clock_not_the_slot(at, first, then):
    assert meal_order(at) == (first, "grab_n_go", "daily_offerings", *then)


def test_meal_order_names_every_meal():
    for hour in range(24):
        order = meal_order(time(hour))
        assert sorted(order) == sorted(MEALS)
        assert order[0] in MEAL_HOURS  # a timed meal first, never Grab 'n Go


def test_minutes_away_is_zero_inside_the_hours_and_wraps_at_midnight():
    assert minutes_away(time(16, 30), time(16, 30), time(21)) == 0
    assert minutes_away(time(21), time(16, 30), time(21)) == 0
    assert minutes_away(time(16, 5), time(11), time(16)) == 5
    assert minutes_away(time(16, 5), time(16, 30), time(21)) == 25
    assert minutes_away(time(0, 30), time(21), time(23, 59)) == 31
    assert minutes_away(time(23, 0), time(7), time(10, 30)) == 8 * 60


def test_candidates_order():
    facts = [
        dish("Pad Thai", "dinner"),
        dish("Chicken Tikka Masala", "dinner"),
        dish("Belgian Waffle", "breakfast"),
        dish("Scrambled Eggs", "breakfast"),
        dish("Chicken Tikka Masala", "lunch"),
        dish("Vegan Chili", "lunch"),
        dish("Cheese Pizza", "lunch"),
        dish("Cheese Pizza", "late_night"),
        dish("Garden Salad", "daily_offerings"),
        dish("Mystery Stew", "brunch"),  # not a MEALS key: last
    ]
    assert [(d.name, d.meal) for d in candidates(facts, time(18))] == [
        ("Pad Thai", "dinner"),
        ("Chicken Tikka Masala", "dinner"),
        ("Garden Salad", "daily_offerings"),
        ("Vegan Chili", "lunch"),
        ("Cheese Pizza", "lunch"),  # served at two meals, listed once
        ("Belgian Waffle", "breakfast"),
        ("Scrambled Eggs", "breakfast"),
        ("Mystery Stew", "brunch"),
    ]
    assert [(d.name, d.meal) for d in candidates(facts, time(21, 30))][:2] == [
        ("Cheese Pizza", "late_night"),
        ("Garden Salad", "daily_offerings"),
    ]
    assert [d.meal for d in candidates(facts, time(8))] == [
        "breakfast",
        "breakfast",
        "daily_offerings",
        "lunch",
        "lunch",
        "lunch",
        "dinner",
        "brunch",
    ]
    assert candidates([], time(12)) == []


def test_candidates_put_a_chosen_meal_first():
    facts = [dish("Pad Thai", "dinner"), dish("Belgian Waffle", "breakfast"), dish("Vegan Chili", "lunch")]
    assert meal_order(time(12), "dinner")[:2] == ("dinner", "lunch")
    assert len(meal_order(time(12), "dinner")) == len(MEALS)
    assert [d.name for d in candidates(facts, time(12), "dinner")] == ["Pad Thai", "Vegan Chili", "Belgian Waffle"]


def test_window_candidates_guessed_day_first_then_nearest_days_then_past_dishes():
    """No date on the photo: the guessed day's meal on now, the rest of that day, the days around it
    nearest first (the day before ahead of the day after), then the hall's past dishes; each dish
    once, keeping the day and meal it was served."""
    nov = lambda d: date(2026, 11, d)  # noqa: E731
    facts = [
        dish("Chicken Pho Broth (Fish)", "dinner", day=nov(11)),
        dish("Pho Noodles", "dinner", day=nov(11)),
        dish("Belgian Waffle", "breakfast", day=nov(13)),
        dish("Tantanmen Ramen Bar", "lunch", day=nov(14)),
        dish("Chicken Shoyu Ramen Bar (Fish, Shellfish)", "dinner", day=nov(14)),
        dish("Scrambled Eggs", "breakfast", day=nov(14)),
        dish("Chicken Pho Broth (Fish)", "lunch", day=nov(15)),  # the 15th repeats the 11th's pho
        dish("Pho Noodles", "dinner", day=nov(15)),
        dish("Green Curry Pho Broth (Anchovies)", "dinner", day=nov(15)),
        dish("Tantanmen Ramen Bar", "dinner", day=nov(16)),
        dish("Cheese Pizza", "any"),  # dining.CATALOG_MEAL: the hall's past dishes
        dish("Belgian Waffle", "any"),
    ]
    found = window_candidates(facts, nov(14), time(15, 33))
    assert [(d.name, d.date, d.meal) for d in found] == [
        ("Tantanmen Ramen Bar", nov(14), "lunch"),  # 15:33: lunch first
        ("Chicken Shoyu Ramen Bar (Fish, Shellfish)", nov(14), "dinner"),
        ("Scrambled Eggs", nov(14), "breakfast"),
        ("Belgian Waffle", nov(13), "breakfast"),  # the day before
        ("Chicken Pho Broth (Fish)", nov(15), "lunch"),  # the day after: its lunch, then its dinner
        ("Pho Noodles", nov(15), "dinner"),
        ("Green Curry Pho Broth (Anchovies)", nov(15), "dinner"),
        ("Cheese Pizza", None, "any"),  # the 11th and 16th only had dishes listed already
    ]
    assert window_candidates([], nov(14), time(12)) == []


def test_find_dish_takes_only_a_listed_name():
    facts = [
        dish("Scrambled Eggs", "breakfast"),
        dish("Chicken Piccata Pizza"),
        dish("Mac & Cheese (GF)"),
        dish("Eggs Benedict", "breakfast"),
    ]
    assert find_dish("scrambled  eggs", facts) is facts[0]  # case and spaces don't count
    assert find_dish("Scrambled Egg", facts) is None  # a near name is not the listed one
    assert find_dish("Chicken Piccata", facts) is None  # another dish
    assert find_dish("Tantanmen Ramen Bar", facts) is None  # made up: never reaches the result
    assert find_dish("Mac & Cheese (GF)", facts) is facts[2]
    assert find_dish("Lobster Bisque", facts) is None
    assert find_dish(None, facts) is None
    assert find_dish("ab", [dish("ab")]) is None  # too short to name a dish
    assert find_dish("  ab  ", [dish("ab")]) is None
    assert find_dish("Scrambled Eggs", []) is None
    twice = [dish("Scrambled Eggs", "breakfast"), dish("scrambled eggs", "lunch")]
    assert find_dish("Scrambled Eggs", twice) is twice[0]  # the first listed


def test_servings_range():
    # 3/3/3 servings is one number; the model's 360/420/480 g says how unsure it is, so it widens.
    assert servings_range((3, 3, 3), (360, 420, 480)) == pytest.approx((2.5714, 3, 3.4286), abs=1e-3)
    assert servings_range((1, 1.5, 2), (100, 120, 150)) == (1, 1.5, 2)  # already wider
    assert servings_range((2, 1, 3), (100, 100, 100)) == (1, 2, 3)  # sorted
    assert servings_range((3, 3, 3.5), (360, 420, 480)) == pytest.approx((2.5714, 3, 3.5), abs=1e-3)  # never narrowed
    assert servings_range((0, 0, 0), (100, 100, 100)) is None
    assert servings_range((1, 1, 1), (0, 0, 0)) is None
    assert servings_range((-1, 1, 1), (100, 100, 100)) == (0, 1, 1)
    assert servings_range(None, (100, 100, 100)) is None


def test_dish_nutrients_weight_and_count_paths():
    weighed = dish("Hash Browns", serving="3 OZ", energy_kcal=112, sodium_mg=180)
    found = dish_nutrients(weighed, (60, 90, 120), (1, 1, 1))  # a weight serving: the count is not used
    assert set(found) == {"energy_kcal", "sodium_mg"}  # a nutrient the label lacks stays absent
    assert bounds(found["energy_kcal"]) == pytest.approx((79.0, 118.5, 158.0), abs=0.1)
    assert bounds(found["sodium_mg"]) == pytest.approx((127.0, 190.5, 254.0), abs=0.1)
    assert dish_nutrients(weighed, (120, 90, 60), None)["energy_kcal"] == found["energy_kcal"]

    counted = dish("Cheese Pizza", serving="1 Slice", energy_kcal=212, carbs_g=26)
    found = dish_nutrients(counted, (100, 120, 150), (1, 1.5, 2))  # a count already wider than the grams
    assert bounds(found["energy_kcal"]) == (212, 318, 424)
    assert bounds(found["carbs_g"]) == (26, 39, 52)
    # The count goes through servings_range: 3/3/3 slices with 360/420/480 g is 2.571/3/3.429 slices,
    # never one number (the README rule), and a negative low is clamped at 0.
    found = dish_nutrients(counted, (360, 420, 480), (3, 3, 3))
    assert bounds(found["energy_kcal"]) == pytest.approx((545.14, 636, 726.86), abs=0.01)
    assert found["energy_kcal"].low < found["energy_kcal"].likely < found["energy_kcal"].high
    assert bounds(found["carbs_g"]) == pytest.approx((66.86, 78, 89.14), abs=0.01)
    assert bounds(dish_nutrients(counted, (100, 120, 150), (-1, 1, 1))["energy_kcal"]) == (0, 212, 212)
    assert dish_nutrients(counted, (100, 120, 150), (2, 1, 3))["energy_kcal"] == Range(low=212, likely=424, high=636)
    # A count the user typed is exact: no widening by the model's grams.
    assert dish_nutrients(counted, (360, 420, 480), (3, 3, 3), widen=False)["energy_kcal"] == Range(
        low=636, likely=636, high=636
    )
    assert dish_nutrients(counted, (100, 120, 150), (0, 0, 0), widen=False) is None
    # No usable count: the caller falls back to USDA.
    assert dish_nutrients(counted, (100, 120, 150), None) is None
    assert dish_nutrients(counted, (100, 120, 150), (0, 0, 0)) is None
    assert dish_nutrients(counted, (0, 0, 0), (1, 1, 1)) is None


@pytest.mark.parametrize(
    ("campus_kcal", "usda_kcal", "fits"),
    [
        (0, None, False),  # a zero label needs USDA to agree
        (150, None, True),
        (3.5, 2.0, True),  # both under KCAL_FLOOR: water, diet soda
        (0, 199, False),
        (2409, 1117, False),  # three '1/6 PIZZA' servings against USDA for 420 g
        (636, 1117, True),
        (300, 100, False),
        (200, 100, True),
        (49, 100, False),
        (50, 100, True),
        (0, 0, True),
    ],
)
def test_label_fits(campus_kcal, usda_kcal, fits):
    assert label_fits(campus_kcal, usda_kcal) is fits
