"""Hand labels for test_pictures/ (UMass Amherst dining hall, late night, Nov 13 2026).

Food identities come from the person who ate the meal. Nothing was weighed: the gram and kcal
reference ranges are visual estimates anchored on USDA FNDDS portion weights, so they score
plausibility, not precision (derivation in BENCHMARK.md). Boxes are [ymin, xmin, ymax, xmax] on
Gemini's 0-1000 grid, drawn by hand on the upright photo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Identity:
    """How precisely a model named the one thing that matters most in the photo."""

    component: str
    exact: str
    close: str | None = None


@dataclass(frozen=True, slots=True)
class Photo:
    file: str
    subject: str
    required: dict[str, str]
    """Component -> regex over "name | fdc_query" (lowercase). Recall is computed over these."""
    exclusive: str | None = None
    """Items matching this component's regex count only for it ("cheese sauce" is not cheese)."""
    allowed_extra: str | None = None
    """Visible edge items that are fine to include or omit (the next photo's glass, etc.)."""
    identity: Identity | None = None
    grams: tuple[float, float, float] = (0, 0, 0)
    """Reference (low, likely, high) total grams of the required items: what Gemini estimates."""
    component_grams: dict[str, tuple[float, float]] = field(default_factory=dict)
    """Reference (low, high) grams for calorie-dominant components."""
    kcal: tuple[float, float, float] = (0, 0, 0)
    """Reference (low, likely, high) kcal of the required items: what the pipeline shows."""
    boxes: dict[str, list[int]] = field(default_factory=dict)
    question: str | None = None
    """Regex a useful clarifying question should match; None = not scored."""


_DRINK_Q = r"diet|zero|sugar|sweet|regular|kind|type|which|what|soda|ginger|tea|juice|alcohol|size|oz|ounce|ml"
_EDGE_DRINK = r"drink|beverage|glass|water|ale|soda|tea|juice|lemonade|seltzer"
_NON_ALCOHOLIC = r"^(?!.*(alcohol|whiskey|bourbon|beer|liquor|cocktail|rum|wine)).*"
_SALAD = {
    "dressing": r"dressing|mustard|sauce|hollandaise|vinaigrette|aioli|mayo",
    "greens": r"arugula|rocket|greens|lettuce|spinach|mesclun|spring mix|salad",
    "onion": r"onion",
    "egg": r"\begg",
    "cheese": r"cheese|cheddar|parmesan|mozzarella",
}
_SALAD_REF = {
    # greens 30-70 g, onion 15-40 g, 3 whole hard-boiled eggs 130-170 g (confirmed by the eater),
    # cheddar 8-20 g, honey mustard 25-60 g at 25 / 38 / 143 / 409 / 464 kcal per 100 g.
    "grams": (210, 270, 360),
    "component_grams": {"egg": (130, 170), "dressing": (25, 60)},
    "kcal": (350, 470, 640),
}

PHOTOS: dict[str, Photo] = {
    p.file: p
    for p in (
        Photo(
            file="IMG_0579.jpeg",
            subject="2 pizza slices (1 cheese, 1 pepperoni), top-down; glass rim at bottom edge",
            required={"pizza": r"pizza", "pepperoni": r"pepperoni"},
            allowed_extra=_EDGE_DRINK,
            # 2 slices, 190-300 g total, at 266 (cheese) / 282 (pepperoni) kcal per 100 g.
            grams=(190, 240, 300),
            kcal=(500, 650, 850),
            boxes={"cheese slice": [330, 67, 725, 695], "pepperoni slice": [258, 274, 702, 874]},
        ),
        Photo(
            file="IMG_0580.jpeg",
            subject="ginger ale with ice, top-down; pizza plate at top-left edge",
            required={"drink": r"^(?!.*pizza)"},
            allowed_extra=r"pizza",
            identity=Identity(
                "drink",
                exact=_NON_ALCOHOLIC + r"ginger ale",
                close=_NON_ALCOHOLIC + r"(soda|soft drink|carbonated|lemon.?lime|sprite|7.?up|seltzer|sparkling|cola)",
            ),
            # 150-350 g of liquid (tumbler with ice) at 34 kcal per 100 g.
            grams=(150, 240, 350),
            kcal=(50, 80, 120),
            boxes={"drink": [317, 296, 561, 701]},
            question=_DRINK_Q,
        ),
        Photo(
            file="IMG_0581.jpeg",
            subject="pink lemonade with ice, oblique; flyer in background",
            required={"drink": r"."},
            identity=Identity("drink", exact=r"(?=.*pink)(?=.*lemonade)", close=r"lemonade"),
            # 150-350 g of liquid at 25-46 kcal per 100 g (FNDDS lemonade drinks).
            grams=(150, 240, 350),
            kcal=(40, 100, 160),
            boxes={"drink": [261, 382, 597, 690]},
            question=_DRINK_Q,
        ),
        Photo(
            file="IMG_0582.jpeg",
            subject="arugula salad: red onion, 3 whole hard-boiled eggs, shredded cheddar, honey mustard; top-down",
            required=_SALAD,
            exclusive="dressing",
            identity=Identity("dressing", exact=r"honey mustard", close=r"mustard"),
            boxes={"salad": [318, 100, 820, 800]},
            question=r"dressing|sauce|mustard",
            **_SALAD_REF,
        ),
        Photo(
            file="IMG_0583.jpeg",
            subject="same salad as IMG_0582 from across the table; person, napkin dispenser, glass at right edge",
            required=_SALAD,
            exclusive="dressing",
            allowed_extra=_EDGE_DRINK,
            identity=Identity("dressing", exact=r"honey mustard", close=r"mustard"),
            boxes={"salad": [700, 319, 956, 726]},
            question=r"dressing|sauce|mustard",
            **_SALAD_REF,
        ),
    )
}

NON_MEALS: dict[str, str] = {
    "black-frame.jpg": "an all-black frame, as from a camera that fired in a pocket",
    "keyboard.jpg": "a laptop keyboard",
    "houseplant.jpg": "an amaryllis flower",
    "empty-plate.jpg": "a clean plate with a fork and knife",
    "menu-board.jpg": "a chalkboard menu, with seafood on ice below it",
    "dog-food.jpg": "dry dog food in a bowl",
    "food-replicas.jpg": "plastic food replicas in a restaurant's display case",
    "finished-plate.jpg": "a finished breakfast plate: half a grilled tomato and a few beans left",
}
"""Photos in bench/nonfood/ (phase n) that must not be logged as a meal: no items in the result."""


def item_text(item: dict) -> str:
    return f"{item['name']} | {item['fdc_query']}".lower()


def classify(photo: Photo, item: dict) -> tuple[set[str], str]:
    """Components an item covers, and its kind: "required", "edge", or "unexpected"."""
    text = item_text(item)
    if photo.exclusive and re.search(photo.required[photo.exclusive], text):
        return {photo.exclusive}, "required"
    covered = {key for key, pattern in photo.required.items() if re.search(pattern, text)}
    if covered:
        return covered, "required"
    if photo.allowed_extra and re.search(photo.allowed_extra, text):
        return set(), "edge"
    return set(), "unexpected"
