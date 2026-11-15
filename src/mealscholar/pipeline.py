"""Steps 1-7 wired together: photo -> metadata -> context -> Gemini -> USDA -> math -> result.

With a dining location named by the caller, the hall's dishes go into the Gemini prompt and are
the only names the model may give as menu_item (vision.response_schema); every food it says is a
listed dish takes the dish's own label (campus.py), checked against USDA; everything else, and
every micronutrient, comes from USDA as before. Which dishes: with a certain date (the photo's
EXIF time, or a day the user picked) that day's menu; without one (a screenshot, an AirDropped or
edited photo) the menus of the days around it, the likeliest first.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

from PIL import Image

from .campus import candidates, dish_nutrients, find_dish, label_fits, window_candidates
from .compute import item_nutrients, summarize
from .context import PlaceLookup, build_context
from .dining import CATALOG_MEAL, DiningUnavailable, DishFacts
from .metadata import extract_metadata, load_image
from .nutrients import BY_KEY
from .nutrition import Food, FoodDatabase, Matches
from .schemas import (
    CookingMethod,
    DishMatch,
    FoodItem,
    FoodMatch,
    ItemResult,
    MealAnalysis,
    MealContext,
    MealResult,
    MenuRef,
)
from .vision import MENU_NAMES, VisionModel, closed_set, context_prompt, prepare_image

log = logging.getLogger(__name__)

# Extra ranking words for the USDA lookup, from what the photo shows.
_COOKING_HINTS: dict[CookingMethod, tuple[str, ...]] = {
    "raw": ("raw",),
    "boiled": ("cooked",),
    "steamed": ("cooked",),
    "grilled": ("cooked",),
    "roasted": ("cooked",),
    "baked": ("cooked",),
    "pan_fried": ("fried", "cooked"),
    "stir_fried": ("fried", "cooked"),
    "deep_fried": ("fried",),
    "unknown": (),
}

LATE_NIGHT_ENDS = time(4)
"""Before this hour a photo may belong to the previous day's late_night menu, which UMass keys
under that day's date (a 00:30 plate is on the 13th's menu, not the 14th's)."""

WINDOW_DAYS = 14
"""Without a certain date, the menus of this many days before the photo's day are used, and that day's.
Never a later day's: the time sent without EXIF is the file's or the upload's, so the meal was eaten
by then, and a menu posted for tomorrow can't be on the plate."""

NOT_CONNECTED = "Campus menus aren't connected; USDA used."
UNAVAILABLE = "Campus menu unavailable right now; USDA used."


class UnknownFood(LookupError):
    pass


class BadCorrection(ValueError):
    """A correction that can't apply to this item; the message is for the user."""


class Menus(Protocol):
    """A hall's dishes with their labels (dining.Dining): for a day, for several days, and from its
    past menus."""

    def dishes_on(self, location_id: int, day: date) -> Sequence[DishFacts]: ...

    def dishes_between(self, location_id: int, start: date, end: date) -> Sequence[DishFacts]: ...

    def served_before(self, location_id: int) -> Sequence[DishFacts]: ...

    def location_name(self, location_id: int) -> str | None: ...


@dataclass(frozen=True)
class _Menu:
    """The dishes a meal is matched against: none without an explicit hall."""

    ref: MenuRef | None = None
    facts: Sequence[DishFacts] = ()
    """The candidates, at most MENU_NAMES, in campus.candidates or campus.window_candidates order;
    the only names menu_item may take."""
    notes: tuple[str, ...] = ()

    @property
    def names(self) -> list[str]:
        return [dish.name for dish in self.facts]


@dataclass
class Workflow:
    vision: VisionModel
    foods: FoodDatabase
    places: PlaceLookup | None = None
    menus: Menus | None = None

    def analyze(
        self,
        photo: Path | BinaryIO,
        *,
        device_time: datetime | None = None,
        device_location: tuple[float, float] | None = None,
        read_photo_gps: bool = False,
        usual_portions: Mapping[str, int] | None = None,
        location_id: int | None = None,
    ) -> MealResult:
        """`location_id` names the dining hall whose menu the foods are matched against; without
        it nothing is matched (the location and the user's halls are never used to pick one)."""
        img = load_image(photo)
        meta = extract_metadata(
            img,
            device_time=device_time,
            device_location=device_location,
            read_photo_gps=read_photo_gps,
        )
        context = build_context(meta, self.places)
        return self._analyze(img, context, self._menu(context, location_id), usual_portions)

    def rematch(
        self,
        result: MealResult,
        photo: Path | BinaryIO,
        *,
        location_id: int,
        day: date | None = None,
        meal: str | None = None,
        usual_portions: Mapping[str, int] | None = None,
    ) -> MealResult:
        """Analyze a logged meal's stored photo again against `location_id`'s dishes: with `day`,
        that day's menu as a certain date (the user said when), `meal` first; without it, the way
        the meal's own time allows. The meal keeps its id, time and photo; its answer goes. Raises
        DiningUnavailable when the hall's dishes can't be read, so a logged meal isn't replaced by
        a USDA-only one the user didn't ask for."""
        img = load_image(photo)
        menu = self._menu(result.context, location_id, day=day, meal=meal)
        if menu.ref is None:
            raise DiningUnavailable(menu.notes[0] if menu.notes else UNAVAILABLE)
        fresh = self._analyze(img, result.context, menu, usual_portions)
        return fresh.model_copy(update={"id": result.id, "photo_url": result.photo_url})

    def _analyze(
        self,
        img: Image.Image,
        context: MealContext,
        menu: _Menu,
        usual_portions: Mapping[str, int] | None = None,
    ) -> MealResult:
        ref = menu.ref
        window = (ref.start, ref.end) if ref is not None and not ref.certain and ref.start and ref.end else None
        hall = ref.location_name if ref is not None else None
        prompt = context_prompt(context, usual_portions or {}, menu.facts, hall=hall, window=window)
        analysis = self.vision.analyze(prepare_image(img), prompt, menu.names)
        result = self._result(context, analysis, menu)
        _log_match(result, menu)
        return result

    def answer(self, result: MealResult, answer: str) -> MealResult:
        """Apply the user's answer to the clarifying question and recompute everything."""
        if not result.question:
            raise ValueError("This meal has no open question.")
        menu = self._menu_for(result)
        analysis = self.vision.refine(result.analysis(), result.question, answer, menu.names)
        refined = self._result(result.context, analysis.model_copy(update={"question": None}), menu)
        return refined.model_copy(update={"id": result.id, "answer": answer, "photo_url": result.photo_url})

    def _menu(
        self, context: MealContext, location_id: int | None, *, day: date | None = None, meal: str | None = None
    ) -> _Menu:
        """The hall's dishes for the photo: one day's when the date is certain (EXIF, or `day`
        given), else the days around it (_window_menu)."""
        if location_id is None:
            return _Menu()
        if self.menus is None:
            return _Menu(notes=(NOT_CONNECTED,))
        try:
            name = self.menus.location_name(location_id)
            if name is None:
                return _Menu(notes=(f"No dining location {location_id}; USDA used.",))
            if day is not None or context.time_source == "exif":
                return self._day_menu(location_id, name, context.taken_at, day, meal)
            return self._window_menu(location_id, name, context.taken_at, meal=meal)
        except DiningUnavailable:
            return _Menu(notes=(UNAVAILABLE,))
        except OverflowError:  # a device time at the very ends of the calendar (year 1 or 9999)
            return _Menu(notes=(f"No campus menu near {context.taken_at:%Y-%m-%d}; USDA used.",))

    def _day_menu(self, location_id: int, name: str, taken_at: datetime, day: date | None, meal: str | None) -> _Menu:
        """One day's menu, the meal on at `taken_at` (or `meal`) first: `day`'s, else the photo's
        (_menu_day)."""
        assert self.menus is not None
        if day is None:
            day, facts = self._menu_day(location_id, taken_at)
        else:
            facts = self.menus.dishes_on(location_id, day)
        listed = candidates(facts, taken_at.time(), meal)[:MENU_NAMES]
        first = meal or next((dish.meal for dish in listed if dish.meal != CATALOG_MEAL), None)
        ref = MenuRef(location_id=location_id, location_name=name, date=day, dishes=len(listed), meal=first)
        return _Menu(ref, listed, (_menu_note(ref, facts),))

    def _window_menu(
        self, location_id: int, name: str, taken_at: datetime, guess: date | None = None, meal: str | None = None
    ) -> _Menu:
        """The photo's date isn't known: the hall's dishes on every day with a menu up to WINDOW_DAYS
        before it, the guessed day first (campus.window_candidates), `meal` first in each
        day when the user named one, then its past dishes, MENU_NAMES at most."""
        assert self.menus is not None
        if guess is None:
            guess = taken_at.date()
            if taken_at.time() < LATE_NIGHT_ENDS:
                guess -= timedelta(days=1)  # a 00:30 plate: the night before's menu first
        facts = self.menus.dishes_between(location_id, guess - timedelta(days=WINDOW_DAYS), max(guess, taken_at.date()))
        listed = window_candidates(facts, guess, taken_at.time(), meal)
        if len(listed) < MENU_NAMES:
            listed = window_candidates([*facts, *self.menus.served_before(location_id)], guess, taken_at.time(), meal)
        listed = listed[:MENU_NAMES]
        days = sorted({dish.date for dish in listed if dish.date is not None})  # the days the model was shown
        ref = MenuRef(
            location_id=location_id,
            location_name=name,
            date=guess,
            dishes=len(listed),
            meal=meal,
            certain=False,
            start=days[0] if days else None,
            end=days[-1] if days else None,
        )
        return _Menu(ref, listed, (_window_note(ref),))

    def _menu_day(self, location_id: int, taken_at: datetime) -> tuple[date, Sequence[DishFacts]]:
        """The menu day for the photo: its date, except before LATE_NIGHT_ENDS the previous day
        when that day has a late_night menu."""
        assert self.menus is not None
        day = taken_at.date()
        if taken_at.time() < LATE_NIGHT_ENDS:
            before = day - timedelta(days=1)
            facts = self.menus.dishes_on(location_id, before)
            if any(dish.meal == "late_night" for dish in facts):
                return before, facts
        return day, self.menus.dishes_on(location_id, day)

    def _menu_for(self, result: MealResult) -> _Menu:
        """The same hall and day (or days) the meal was analyzed with, read again for the answer."""
        ref = result.menu
        if ref is None:
            return _Menu()
        if self.menus is None:
            return _Menu(ref, notes=("Campus menus aren't connected; USDA used for this answer.",))
        taken_at = result.context.taken_at
        try:
            if ref.certain:
                menu = self._day_menu(ref.location_id, ref.location_name, taken_at, ref.date, ref.meal)
            else:
                menu = self._window_menu(ref.location_id, ref.location_name, taken_at, ref.date, ref.meal)
        except DiningUnavailable:
            return _Menu(ref, notes=(UNAVAILABLE,))
        return _Menu(ref, menu.facts, menu.notes)

    def _result(self, context: MealContext, analysis: MealAnalysis, menu: _Menu | None = None) -> MealResult:
        menu = menu or _Menu()
        notes: list[str] = list(menu.notes)
        items: list[ItemResult] = []
        for item in analysis.items if analysis.is_food else ():
            dish = find_dish(item.menu_item, menu.facts) if menu.ref is not None else None
            if item.menu_item is not None and (dish is not None or menu.facts or menu.ref is None):
                # Only a listed name, spelled as listed, stays: one the model made up never reaches
                # the result. (With the menus offline nothing is listed, so the name is kept.)
                item = item.model_copy(update=_listed(dish))
            hints = _COOKING_HINTS[item.cooking_method] + (("oil",) if item.hidden_fat_likely else ())
            found = self.foods.search(item.fdc_query, hints=hints)
            if not found.exact:
                by_name = self.foods.search(item.name, hints=hints)
                if by_name.exact or not found.foods:
                    found = by_name
            if dish is not None and (campus := _campus_item(item, dish, found, menu.ref, notes)) is not None:
                items.append(campus)
                continue
            if found.exact:
                items.append(_item_result(item, found.foods[0], [_match(f) for f in found.foods[1:]]))
                continue
            # A loose match ("Ramen bowl" for an acai bowl) would put wrong numbers in the totals;
            # offer it as a suggestion instead.
            items.append(ItemResult(item=item, match=None, alternatives=[_match(f) for f in found.foods]))
            notes.append(f"No close USDA match for '{item.name}'; not counted until you pick one.")
        if not analysis.is_food:
            notes.append("No food or drink detected in the photo.")
        return summarize(
            MealResult(
                context=context,
                items=items,
                menu=menu.ref,
                name=meal_name([r.item for r in items]),
                scale_reference=analysis.scale_reference,
                # Nothing to ask about a photo that isn't a meal, and answering would re-run Gemini.
                question=analysis.question if analysis.is_food else None,
                notes=notes,
            )
        )


def correct(
    result: MealResult,
    item_index: int,
    foods: FoodDatabase,
    *,
    grams: int | None = None,
    servings: float | None = None,
    fdc_id: int | None = None,
) -> MealResult:
    """User correction: exact grams, or a count of the campus dish's listed serving, collapse the
    amount (the user typed it); fdc_id swaps the USDA match. A campus dish keeps its label when it
    is listed per weight, or when the user counted its servings. Grams on a dish listed per count
    ('1 Slice'), or any fdc_id, switch the item to USDA for good: menu_item is cleared so a later
    answer() can't match the dish again. BadCorrection for grams with servings, or servings on a
    food that isn't a campus dish."""
    if grams is not None and servings is not None:
        raise BadCorrection("Send grams or servings, not both.")
    items = list(result.items)
    notes = list(result.notes)
    current = items[item_index]
    item, dish = current.item, current.dish
    if servings is not None and dish is None:
        raise BadCorrection("This food isn't a campus dish; send grams.")
    if fdc_id is not None:
        food = foods.get(fdc_id)
        if food is None:
            raise UnknownFood(f"No USDA food with FDC id {fdc_id}.")
        alternatives = [a for a in (current.match, *current.alternatives) if a and a.fdc_id != fdc_id]
        dish = None
    else:
        food = foods.get(current.match.fdc_id) if current.match else None
        alternatives = current.alternatives
    if servings is not None and dish is not None:
        update = {"servings_low": servings, "servings_likely": servings, "servings_high": servings}
        if dish.serving_grams is not None:
            grams = round(servings * dish.serving_grams)
        elif item.servings_likely:
            # A counted serving: the gram range follows the new count, keeping its shape, so the
            # micronutrients (USDA per gram) track the correction while staying an estimate range.
            factor = servings / item.servings_likely
            update |= {k: round(getattr(item, k) * factor) for k in ("grams_low", "grams_likely", "grams_high")}
        item = item.model_copy(update=update)
    if grams is not None:
        item = item.model_copy(update={"grams_low": grams, "grams_likely": grams, "grams_high": grams})
        if dish is not None and dish.serving_grams is None:
            # The app's only amount control sends grams; a counted serving can't be scaled by them.
            notes.append(
                f"'{item.name}' has an amount in grams now, so USDA numbers are used; "
                f"the menu lists {dish.name} per {dish.serving_size}."
            )
            dish = None
    campus = _campus_result(item, dish, food, alternatives, widen=servings is None) if dish is not None else None
    if campus is not None:
        items[item_index] = campus
    else:
        if current.dish is not None or fdc_id is not None:  # the user chose USDA; the dish stays gone
            cleared = {"menu_item": None, "servings_low": None, "servings_likely": None, "servings_high": None}
            item = item.model_copy(update=cleared)
        items[item_index] = _item_result(item, food, alternatives)
    return summarize(result.model_copy(update={"items": items, "notes": notes}))


def meal_name(items: Sequence[FoodItem]) -> str | None:
    """'rice', 'rice and pizza', 'rice, pizza and sauce', 'rice, pizza, sauce and 2 more'."""
    names = [item.name for item in items]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _match(food: Food) -> FoodMatch:
    return FoodMatch(fdc_id=food.fdc_id, description=food.description, data_type=food.data_type)


def _item_result(item: FoodItem, food: Food | None, alternatives: list[FoodMatch]) -> ItemResult:
    if food is None:
        return ItemResult(item=item, match=None, alternatives=alternatives)
    return ItemResult(
        item=item,
        match=_match(food),
        alternatives=alternatives,
        nutrients=item_nutrients(food.per_100g, item),
    )


def _campus_item(item: FoodItem, dish: DishFacts, found: Matches, ref: MenuRef, notes: list[str]) -> ItemResult | None:
    """The item on the dish's label scaled to the plate, with micronutrients from the USDA match
    when the lookup was exact. None, with a note, when the serving count is missing for a counted
    serving or the label's calories don't fit USDA's for the estimated grams; the caller then
    takes the USDA path."""
    donor = found.foods[0] if found.exact else None
    alternatives = [_match(f) for f in (found.foods[1:] if donor else found.foods)]
    matched = DishMatch(
        dish_id=dish.dish_id,
        name=dish.name,
        location_id=ref.location_id,
        location_name=ref.location_name,
        date=dish.date or (None if dish.meal == CATALOG_MEAL else ref.date),
        meal=dish.meal,
        serving_size=dish.serving_size,
        serving_grams=dish.serving_grams,
        per_serving=dict(dish.per_serving),
        healthfulness_score=dish.healthfulness_score,
    )
    campus = _campus_result(item, matched, donor, alternatives)
    listed = f"'{item.name}' is on the menu as {dish.name} ({dish.serving_size})"
    if campus is None:
        notes.append(f"{listed}, but no serving count was estimated; USDA used.")
        return None
    usda_kcal = None
    if donor is not None and (per_100g := donor.per_100g.get("energy_kcal")) is not None:
        usda_kcal = per_100g * item.grams_likely / 100
    kcal = campus.nutrients.get("energy_kcal")
    if kcal is None or not label_fits(kcal.likely, usda_kcal):
        notes.append(f"{listed}, but its label didn't fit the estimated amount; USDA used.")
        return None
    return campus


def _campus_result(
    item: FoodItem, dish: DishMatch, donor: Food | None, alternatives: list[FoodMatch], *, widen: bool = True
) -> ItemResult | None:
    """The item on its dish's label scaled to the plate (campus.dish_nutrients), the USDA donor
    giving only the micronutrients. `widen=False` when the user typed the serving count. None
    when a counted serving has no usable count. Shared by _result and correct() so the two never
    drift."""
    grams = (item.grams_low, item.grams_likely, item.grams_high)
    servings = None
    if item.servings_likely is not None:  # the schema sets all three together
        servings = (item.servings_low or 0.0, item.servings_likely, item.servings_high or 0.0)
    campus = dish_nutrients(dish, grams, servings, widen=widen)
    if campus is None:
        return None
    micros = {k: r for k, r in item_nutrients(donor.per_100g, item).items() if BY_KEY[k].micro} if donor else {}
    return ItemResult(
        item=item,
        match=_match(donor) if donor else None,
        alternatives=alternatives,
        nutrients={**micros, **campus},
        source="campus",
        dish=dish,
    )


def _listed(dish: DishFacts | None) -> dict:
    """The menu fields of an item named as `dish`: its listed name, or none and no servings."""
    if dish is None:
        return {"menu_item": None, "servings_low": None, "servings_likely": None, "servings_high": None}
    return {"menu_item": dish.name}


def _menu_note(ref: MenuRef, facts: Sequence[DishFacts] = ()) -> str:
    if ref.dishes == 0:
        return f"No campus menu for {ref.location_name} on {ref.date}; USDA used."
    if facts and all(dish.meal == CATALOG_MEAL for dish in facts):
        return f"No menu posted for {ref.location_name} on {ref.date}; matched against dishes it has served."
    return f"Campus menu: {ref.location_name}, {ref.date}."


def _window_note(ref: MenuRef) -> str:
    if ref.dishes == 0:
        return f"The photo had no date and {ref.location_name} has no campus menus near {ref.date}; USDA used."
    if ref.start is None or ref.end is None:
        return (
            f"The photo had no date and no menu is posted for {ref.location_name} near {ref.date}; "
            "matched against dishes it has served."
        )
    return (
        f"The photo had no date, so it was matched against {ref.location_name}'s menus from "
        f"{ref.start} to {ref.end}, {ref.date} first."
    )


def _log_match(result: MealResult, menu: _Menu) -> None:
    """One line per analysis with a hall: which menus, how many dishes, how many foods matched one.
    Nothing about the person or the photo."""
    ref = result.menu
    if ref is None:
        return
    when = f"day={ref.date}" if ref.certain else f"window={ref.start}..{ref.end} guess={ref.date}"
    log.info(
        "menu match: hall=%d %s candidates=%d enum=%s matched=%d/%d",
        ref.location_id,
        when,
        len(menu.facts),
        "yes" if closed_set(menu.names) else "no",
        sum(1 for r in result.items if r.dish is not None),
        len(result.items),
    )
