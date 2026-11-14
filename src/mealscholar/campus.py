"""Campus menu facts for a plate: the dish the model named on a hall's menu, its nutrition label
scaled to the estimated amount, and whether that label can be right for what is on the plate.

Pure functions, no I/O; the dishes come from dining.Dining.dishes_on. Every amount stays a range
(low, likely, high): the model estimates how much food there is, the label says what one serving
holds, and nobody weighs anything.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import time
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from .prefs import dish_key
from .schemas import Range

if TYPE_CHECKING:
    from .dining import DishFacts  # dining.py imports this module, so only the type is taken

OZ_G = 28.3495
"""Grams in an ounce by weight."""
FL_OZ_G = 29.57
"""Grams in a fluid ounce of water. The menus mark drinks, soups and gravies OZL, as in '20 OZL'."""

_SERVING = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|\.\d+)\s*(fl\.?\s*oz|ozl|oz)\.?\s*$", re.I)


def serving_grams(text: str | None) -> float | None:
    """The grams in a serving given by weight ('3 OZ', '2 1/2 oz') or by volume ('20 OZL', taken
    at water density). None for a serving that is counted, not weighed: '1 EACH', '1 Slice',
    '1/6 PIZZA'."""
    found = _SERVING.match(text) if text else None
    if found is None:
        return None
    quantity, unit = found.groups()
    grams = _quantity(quantity) * (OZ_G if unit.lower() == "oz" else FL_OZ_G)
    return round(grams, 2) if grams > 0 else None


def _quantity(text: str) -> float:
    """'2 1/2' -> 2.5, '1/2' -> 0.5, '.5' -> 0.5."""
    total = 0.0
    for part in text.split():
        if "/" in part:
            numerator, denominator = (int(n) for n in part.split("/"))
            total += numerator / denominator if denominator else 0.0
        else:
            total += float(part)
    return total


# --- which dishes to match against ----------------------------------------------

MINUTES_A_DAY = 24 * 60


def meal_order(at: time) -> tuple[str, ...]:
    """Every meal in dining.MEALS, in the order their dishes are listed for a photo taken at
    `at`: the meal on by dining.MEAL_HOURS, or the nearest one when none is (16:05 is still
    lunch, 00:30 is late night), then the meals without set hours, which are as likely at any
    time, then the other timed meals, nearest first. The clock decides, not the photo's meal
    slot: dinner starts at 16:30 while the slot stays 'snack' until 17:00, and late night at
    21:00 while the slot stays 'dinner' until 22:00."""
    from .dining import ALL_DAY_MEALS, MEAL_HOURS  # dining imports this module, so not at the top

    timed = sorted(MEAL_HOURS, key=lambda meal: minutes_away(at, *MEAL_HOURS[meal]))  # ties: MEALS order
    return (timed[0], *ALL_DAY_MEALS, *timed[1:])


def minutes_away(at: time, start: time, end: time) -> int:
    """Minutes from `at` to the nearer end of the hours `start`..`end`, 0 inside them, going
    round midnight when that is shorter (00:30 is 31 minutes after a 23:59 close)."""
    now, opens, closes = (t.hour * 60 + t.minute for t in (at, start, end))
    if opens <= now <= closes:
        return 0
    return min((opens - now) % MINUTES_A_DAY, (now - closes) % MINUTES_A_DAY)


def candidates(facts: Sequence[DishFacts], at: time) -> list[DishFacts]:
    """The dishes to match a photo taken at `at` against, meal by meal in meal_order(at), each
    in menu order. A dish served at several meals is listed once, at the first of them. The
    prompt shows only the first vision.MENU_NAMES, so the meal on now is always listed whole."""
    rank = {meal: n for n, meal in enumerate(meal_order(at))}
    chosen: dict[str, DishFacts] = {}
    for dish in sorted(facts, key=lambda dish: rank.get(dish.meal, len(rank))):  # stable: menu order within a meal
        chosen.setdefault(dish_key(dish.name), dish)
    return list(chosen.values())


# --- the dish the model named ----------------------------------------------------

MATCH_RATIO = 0.85
"""How alike the model's menu_item and a listed name must be (difflib ratio of their dish keys)
when no key matches exactly: spelling drift such as 'Scrambled Egg' for 'Scrambled Eggs' (0.96)
passes, another dish such as 'Chicken Piccata' for 'Chicken Piccata Pizza' (0.83) doesn't."""
MIN_MENU_ITEM = 3
"""Characters in a menu_item; anything shorter can't name a dish."""


def find_dish(menu_item: str | None, facts: Sequence[DishFacts]) -> DishFacts | None:
    """The listed dish the model named as menu_item: by dish key, else the closest name at
    MATCH_RATIO or better, the first listed when tied. None when it named nothing, or nothing
    listed is close. The item's own name is never matched: once the model has seen the list, its
    menu_item is the only signal ('egg' would hit 'Eggs' at 0.86 while 'mac and cheese' misses
    'Mac & Cheese' at 0.85)."""
    wanted = dish_key(menu_item) if menu_item else ""
    if len(wanted) < MIN_MENU_ITEM:
        return None
    for dish in facts:
        if dish_key(dish.name) == wanted:
            return dish
    scored = [(SequenceMatcher(None, wanted, dish_key(dish.name)).ratio(), dish) for dish in facts]
    if not scored:
        return None
    ratio, dish = max(scored, key=lambda scored: scored[0])  # the first of equals
    return dish if ratio >= MATCH_RATIO else None


# --- the label scaled to the plate -----------------------------------------------


def servings_range(
    servings: tuple[float, float, float] | None, grams: tuple[int, int, int]
) -> tuple[float, float, float] | None:
    """The model's serving count as a range at least as wide, relative to its likely value, as its
    gram range: a count given as one number (3/3/3 of a '1/6 PIZZA' slice) is as uncertain as the
    weight it came with (360/420/480 g), so it widens to 2.571/3/3.429 and never stays one number.
    None without a count, or when the likely count or weight is 0."""
    if servings is None:
        return None
    low, likely, high = sorted(max(0.0, float(count)) for count in servings)
    grams_low, grams_likely, grams_high = sorted(grams)
    if likely <= 0 or grams_likely <= 0:
        return None
    if (high - low) / likely < (grams_high - grams_low) / grams_likely:
        low = min(low, likely * grams_low / grams_likely)
        high = max(high, likely * grams_high / grams_likely)
    return low, likely, high


def dish_nutrients(
    dish: DishFacts, grams: tuple[int, int, int], servings: tuple[float, float, float] | None
) -> dict[str, Range] | None:
    """The label scaled to the plate, a range per nutrient key: by weight when the serving is one
    (the model's gram range, unchanged, over the serving's grams), else by the model's serving
    count as servings_range widens and clamps it, so a count given as one number never gives one
    number. None for a counted serving without a usable count; the caller then falls back to
    USDA."""
    if dish.serving_grams is not None:
        low, likely, high = (g / dish.serving_grams for g in sorted(grams))
    else:
        counts = servings_range(servings, grams)
        if counts is None:
            return None
        low, likely, high = counts
    return {key: Range(low=v * low, likely=v * likely, high=v * high) for key, v in dish.per_serving.items()}


# --- whether the label fits the plate --------------------------------------------

KCAL_BAND = (0.5, 2.0)
"""A campus label counts when its calories for the plate are within this factor of USDA's for the
same grams. Outside it the label and the plate disagree (three '1/6 PIZZA' servings labelled
2,409 kcal against USDA's 1,117 for 420 g) and USDA is used instead."""
KCAL_FLOOR = 10.0
"""Calories under which both sides agree there is nothing much: water, black coffee, a diet soda
(2 kcal per 100 g x 355 g = 7)."""


def label_fits(campus_kcal: float, usda_kcal: float | None) -> bool:
    """Whether the campus label's calories for the plate agree with USDA's for the same grams:
    both under KCAL_FLOOR, or within KCAL_BAND. Without a USDA match the label is trusted unless
    it says 0: a zero label is trusted only when USDA agrees (a '0 kcal' latte is not 0)."""
    if usda_kcal is None:
        return campus_kcal > 0
    if campus_kcal < KCAL_FLOOR and usda_kcal < KCAL_FLOOR:
        return True
    return KCAL_BAND[0] * usda_kcal <= campus_kcal <= KCAL_BAND[1] * usda_kcal
