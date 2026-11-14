"""Step 8: daily totals and weekly trends. Information, not diagnosis: goals are only the user's own (prefs.py)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from .compute import add_ranges, daily_value_pct
from .nutrients import NUTRIENTS
from .schemas import MealResult, Range

LOW_DV_PCT = 50.0
"""A micronutrient day counts as low when the likely intake is under half the Daily Value."""


@dataclass(frozen=True, slots=True)
class DaySummary:
    day: date
    meals: list[MealResult]
    totals: dict[str, Range]
    daily_value_pct: dict[str, Range]


@dataclass(frozen=True, slots=True)
class NutrientTrend:
    key: str
    average_per_day: float
    average_dv_pct: float | None
    low_days: int | None
    """Logged days under LOW_DV_PCT of the Daily Value (micronutrients only)."""


@dataclass(frozen=True, slots=True)
class PeriodReport:
    first: date
    last: date
    days_logged: int
    meals_logged: int
    trends: list[NutrientTrend]


def summarize_days(meals: list[MealResult]) -> list[DaySummary]:
    by_day: dict[date, list[MealResult]] = defaultdict(list)
    for meal in meals:
        by_day[meal.context.taken_at.date()].append(meal)
    summaries = []
    for day in sorted(by_day):
        totals = add_ranges(m.totals for m in by_day[day])
        summaries.append(DaySummary(day, by_day[day], totals, daily_value_pct(totals)))
    return summaries


def period_report(first: date, last: date, meals: list[MealResult]) -> PeriodReport:
    """Averages over days that have at least one logged meal, so unlogged days don't read as zero."""
    days = summarize_days(meals)
    trends: list[NutrientTrend] = []
    if days:
        for n in NUTRIENTS:
            values = [d.totals[n.key].likely if n.key in d.totals else 0.0 for d in days]
            average = sum(values) / len(days)
            dv_pct = 100 * average / n.daily_value if n.daily_value else None
            low_days = (
                sum(1 for v in values if 100 * v / n.daily_value < LOW_DV_PCT) if n.micro and n.daily_value else None
            )
            trends.append(NutrientTrend(n.key, average, dv_pct, low_days))
    return PeriodReport(first, last, len(days), sum(len(d.meals) for d in days), trends)
