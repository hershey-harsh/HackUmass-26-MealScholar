"""Step 5: nutrient arithmetic. Plain code, no model: grams x per-100 g values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .nutrients import NUTRIENTS
from .schemas import FoodItem, ItemResult, MealResult, Range


def item_nutrients(per_100g: Mapping[str, float], item: FoodItem) -> dict[str, Range]:
    return {
        key: Range(
            low=value * item.grams_low / 100,
            likely=value * item.grams_likely / 100,
            high=value * item.grams_high / 100,
        )
        for key, value in per_100g.items()
    }


def add_ranges(ranges: Iterable[Mapping[str, Range]]) -> dict[str, Range]:
    totals: dict[str, list[float]] = {}
    for nutrients in ranges:
        for key, r in nutrients.items():
            acc = totals.setdefault(key, [0.0, 0.0, 0.0])
            acc[0] += r.low
            acc[1] += r.likely
            acc[2] += r.high
    return {key: Range(low=lo, likely=mid, high=hi) for key, (lo, mid, hi) in totals.items()}


def daily_value_pct(totals: Mapping[str, Range]) -> dict[str, Range]:
    pct: dict[str, Range] = {}
    for n in NUTRIENTS:
        if n.daily_value is not None and (r := totals.get(n.key)) is not None:
            pct[n.key] = Range(
                low=100 * r.low / n.daily_value,
                likely=100 * r.likely / n.daily_value,
                high=100 * r.high / n.daily_value,
            )
    return pct


def summarize(result: MealResult) -> MealResult:
    """Recompute totals, % Daily Value, and which totals are missing data from some food."""
    matched: list[ItemResult] = [r for r in result.items if r.match is not None]
    totals = add_ranges(r.nutrients for r in matched)
    partial = sorted(
        {n.key for n in NUTRIENTS for r in matched if n.key not in r.nutrients},
        key=[n.key for n in NUTRIENTS].index,
    )
    return result.model_copy(
        update={
            "totals": totals,
            "daily_value_pct": daily_value_pct(totals),
            "partial_nutrients": partial,
        }
    )
