# FoodSmart

Snap a meal photo, get foods, grams and nutrients — as ranges, never a single fake-precise number.

Gemini identifies the foods and estimates grams. **It never outputs nutrient numbers**: code looks
each food up in a local copy of USDA FoodData Central and multiplies.

## Workflow

| # | Step | Module | Output |
|---|---|---|---|
| 0 | Capture | `cli.py` (`analyze PHOTO`) | Photo (JPEG, PNG, HEIC) |
| 1 | Metadata | `metadata.py` | Capture time (EXIF original + offset → device clock → upload time); location only if opted in (device coords → photo GPS) |
| 2 | Context | `context.py` | Meal slot; food place at the location via OpenStreetMap (name, type, cuisine, chain). **Coordinates are dropped here** |
| 3 | Vision | `vision.py` | Gemini structured JSON: items, grams low/likely/high, cooking method, bounding box, hidden-fat flag, scale reference, ≤1 question |
| 4 | Nutrition source | `nutrition.py` | Best USDA match (Survey/FNDDS, Foundation, SR Legacy) + alternatives |
| 5 | Compute | `compute.py` | grams × per-100 g → per-item and meal totals, % Daily Value |
| 6 | Show / answer | `cli.py`, `pipeline.py` | Ranges; answering the question re-runs Gemini on the analysis (text only) and recomputes |
| 7 | Correct and log | `pipeline.correct`, `diary.py` | Exact grams or a different USDA food; corrections become "usual portions" fed back to Gemini |
| 8 | Insights | `insights.py` | Day totals; 7-day averages and micronutrient low-days |

## Setup

Needs [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```sh
uv sync
export GEMINI_API_KEY=...            # https://aistudio.google.com/apikey
uv run foodsmart fdc-import          # one-time: downloads ~17 MB from USDA, builds ~/.foodsmart/fdc.sqlite
```

## Use

```sh
uv run foodsmart analyze lunch.jpg                         # asks the clarifying question if there is one
uv run foodsmart analyze lunch.jpg --use-photo-location    # opt in to the photo's GPS for the place lookup
uv run foodsmart analyze lunch.jpg --lat 42.39 --lon -72.53 --taken-at 2026-11-14T12:30-05:00   # what a phone app would send
uv run foodsmart analyze lunch.jpg --answer "fried in oil" --json   # non-interactive, JSON for a frontend

uv run foodsmart show 3
uv run foodsmart search "rice, white, cooked"              # find FDC ids
uv run foodsmart fix 3 1 --grams 300                       # meal 3, item 1: exact weight
uv run foodsmart fix 3 1 --food 2708404                    # switch the USDA match
uv run foodsmart day                                       # today's totals
uv run foodsmart week                                      # 7-day averages, micronutrient trends
```

`--json` prints the full `MealResult` (see `schemas.py`) — the contract for a web or mobile frontend.

## Configuration

| Variable | Default | |
|---|---|---|
| `GEMINI_API_KEY` | — | Required for `analyze` |
| `FOODSMART_MODEL` | `gemini-4-hydrogen` | e.g. `gemini-3.5-flash-lite` for cheaper, faster runs |
| `FOODSMART_THINKING` | `low` | Gemini thinking level: `minimal`, `low`, `medium`, `high` |
| `FOODSMART_HOME` | `~/.foodsmart` | USDA database, downloads, diary |
| `FOODSMART_NOMINATIM_URL` | public OSM Nominatim | Public instance allows 1 request/second |

## Rules the code enforces

- The model identifies foods and grams; nutrients come from USDA data and arithmetic.
- Every number is a range until the user gives an exact weight.
- Location is opt-in; raw coordinates are never stored; photos are never stored; the image sent
  to Gemini is re-encoded without EXIF, and requests use `store=False`.
- Micronutrients are labelled as estimates; weekly views average only logged days.
- No calorie targets, no insulin or carb dosing advice.

## Development

```sh
uv run pytest
```

## Not in v1

From the agreed scope: chain-restaurant menu nutrition (v2), barcode scanning (v2), allergen
flags, processing level (NOVA) and camera-roll backfill (v3).

## Data

- U.S. Department of Agriculture, Agricultural Research Service. FoodData Central. fdc.nal.usda.gov (CC0).
- Place data © OpenStreetMap contributors (ODbL), via Nominatim.
