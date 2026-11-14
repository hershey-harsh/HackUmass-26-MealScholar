"""Nutrient catalog: USDA FoodData Central nutrient numbers, units, and FDA Daily Values."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Nutrient:
    key: str
    label: str
    unit: str
    """Canonical unit as normalized by `normalize_unit`: kcal, g, mg, µg."""
    fdc_numbers: tuple[str, ...]
    """FDC nutrient numbers in preference order; the first one present wins."""
    daily_value: float | None
    """FDA Daily Value for adults and children 4+ (21 CFR 101.9); None when there is none."""
    micro: bool = False
    """Micronutrients are least reliable from photos and are labelled as estimates."""


NUTRIENTS: tuple[Nutrient, ...] = (
    # Foundation Foods often carry only Atwater energy (958 specific, 957 general) instead of 208.
    Nutrient("energy_kcal", "Calories", "kcal", ("208", "958", "957"), None),
    Nutrient("protein_g", "Protein", "g", ("203",), 50),
    Nutrient("carbs_g", "Carbohydrate", "g", ("205",), 275),
    Nutrient("fat_g", "Fat", "g", ("204",), 78),
    Nutrient("sat_fat_g", "Saturated fat", "g", ("606",), 20),
    Nutrient("fiber_g", "Fiber", "g", ("291",), 28),
    Nutrient("sugars_g", "Total sugars", "g", ("269",), None),
    Nutrient("sodium_mg", "Sodium", "mg", ("307",), 2300),
    Nutrient("cholesterol_mg", "Cholesterol", "mg", ("601",), 300),
    Nutrient("calcium_mg", "Calcium", "mg", ("301",), 1300, micro=True),
    Nutrient("iron_mg", "Iron", "mg", ("303",), 18, micro=True),
    Nutrient("potassium_mg", "Potassium", "mg", ("306",), 4700, micro=True),
    Nutrient("magnesium_mg", "Magnesium", "mg", ("304",), 420, micro=True),
    Nutrient("zinc_mg", "Zinc", "mg", ("309",), 11, micro=True),
    Nutrient("vitamin_a_ug", "Vitamin A (RAE)", "µg", ("320",), 900, micro=True),
    Nutrient("vitamin_c_mg", "Vitamin C", "mg", ("401",), 90, micro=True),
    Nutrient("vitamin_d_ug", "Vitamin D", "µg", ("328",), 20, micro=True),
    Nutrient("vitamin_b12_ug", "Vitamin B12", "µg", ("418",), 2.4, micro=True),
    Nutrient("folate_dfe_ug", "Folate (DFE)", "µg", ("435",), 400, micro=True),
)

BY_KEY: dict[str, Nutrient] = {n.key: n for n in NUTRIENTS}


def normalize_unit(unit: str) -> str:
    u = unit.strip().lower()
    return "µg" if u in ("ug", "µg", "mcg") else u


def per_100g(rows: Iterable[tuple[str, str, float | None]]) -> dict[str, float]:
    """Map FDC (nutrient number, unit, amount per 100 g) rows onto catalog keys.

    Rows whose unit differs from the catalog unit are ignored rather than converted,
    so an IU vitamin D value can never be read as µg.
    """
    by_number: dict[str, tuple[str, float]] = {}
    for number, unit, amount in rows:
        if amount is not None and number not in by_number:
            by_number[number] = (normalize_unit(unit), float(amount))
    out: dict[str, float] = {}
    for nutrient in NUTRIENTS:
        for number in nutrient.fdc_numbers:
            hit = by_number.get(number)
            if hit is not None and hit[0] == nutrient.unit:
                out[nutrient.key] = hit[1]
                break
    return out
