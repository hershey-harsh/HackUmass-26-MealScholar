"""Step 4: USDA FoodData Central lookup against a local SQLite copy.

The local copy removes the API rate limit (1,000 requests/hour per IP; DEMO_KEY far less)
and keeps the demo working on bad Wi-Fi. FDC data is public domain (CC0).
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import ijson

from .nutrients import per_100g

DOWNLOAD_BASE = "https://fdc.nal.usda.gov/fdc-datasets/"


@dataclass(frozen=True, slots=True)
class Dataset:
    data_type: str
    filename: str
    root_key: str


DATASETS: tuple[Dataset, ...] = (
    Dataset("Survey (FNDDS)", "FoodData_Central_survey_food_json_2024-10-31.zip", "SurveyFoods"),
    Dataset("Foundation", "FoodData_Central_foundation_food_json_2026-10-29.zip", "FoundationFoods"),
    Dataset("SR Legacy", "FoodData_Central_sr_legacy_food_json_2018-04.zip", "SRLegacyFoods"),
)

_SCHEMA = """
CREATE TABLE foods (
    fdc_id      INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    data_type   TEXT NOT NULL,
    category    TEXT NOT NULL,
    nutrients   TEXT NOT NULL
);
CREATE VIRTUAL TABLE foods_fts USING fts5(
    description,
    content='foods',
    content_rowid='fdc_id',
    tokenize='porter unicode61 remove_diacritics 2'
);
"""


@dataclass(frozen=True, slots=True)
class Food:
    fdc_id: int
    description: str
    data_type: str
    per_100g: dict[str, float]


class FoodDatabaseMissing(RuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"USDA food database not found at {path}. Run `foodsmart fdc-import` first.")


def build_database(
    db_path: Path,
    download_dir: Path,
    *,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Download the FDC JSON datasets (if absent) and build the SQLite search index."""
    download_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)

    counts: dict[str, int] = {}
    con = sqlite3.connect(tmp_path)
    try:
        con.executescript(_SCHEMA)
        for ds in DATASETS:
            zip_path = download_dir / ds.filename
            if not zip_path.exists():
                log(f"Downloading {ds.filename} ...")
                _download(DOWNLOAD_BASE + ds.filename, zip_path)
            log(f"Importing {ds.data_type} ...")
            before = con.total_changes
            con.executemany("INSERT OR IGNORE INTO foods VALUES (?, ?, ?, ?, ?)", _rows(zip_path, ds))
            counts[ds.data_type] = con.total_changes - before
        con.execute("INSERT INTO foods_fts(foods_fts) VALUES ('rebuild')")
        con.commit()
    finally:
        con.close()
    tmp_path.replace(db_path)
    return counts


def _download(url: str, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        with part.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    part.replace(dest)


def _rows(zip_path: Path, ds: Dataset) -> Iterator[tuple[int, str, str, str, str]]:
    with zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.endswith(".json"))
        with zf.open(member) as f:
            for food in ijson.items(f, f"{ds.root_key}.item", use_float=True):
                nutrients = per_100g(
                    (fn["nutrient"]["number"], fn["nutrient"]["unitName"], fn.get("amount"))
                    for fn in food.get("foodNutrients", ())
                    if "nutrient" in fn and "number" in fn["nutrient"]
                )
                if nutrients:
                    yield (
                        food["fdcId"],
                        food["description"],
                        ds.data_type,
                        _category(food),
                        json.dumps(nutrients, separators=(",", ":")),
                    )


def _category(food: dict) -> str:
    if wweia := food.get("wweiaFoodCategory"):
        return wweia.get("wweiaFoodCategoryDescription", "")
    return (food.get("foodCategory") or {}).get("description", "")


# Words that carry no identity in FNDDS-style descriptions.
_STOPWORDS = frozenset(
    {"a", "an", "and", "as", "for", "from", "in", "made", "nfs", "ns", "of", "or", "the", "to", "type", "with"}
)
# Qualifiers that change nutrition a lot (diet cola, non-dairy latte); a description carrying
# one the query did not ask for is penalized beyond an ordinary extra word.
_NEGATIONS = frozenset(
    {
        "no", "non", "without", "meatless", "diet", "free", "nonfat", "decaffeinated",
        "unsweetened", "light", "reduced", "low",
    }
)
# Forms nobody eats as-is: a plate never holds lemonade concentrate or dry instant tea, but they
# often share the query's words ("Lemonade, pink" -> "Lemonade, frozen concentrate, pink",
# 192 kcal/100 g vs 25-46 for the drink). Penalized harder than a negation, unless the query asks
# for the form or the entry is its prepared version ("..., frozen concentrate, prepared with water").
_PROCESSED = frozenset(
    {
        "concentrate",
        "concentrated",
        "powder",
        "powdered",
        "dry",
        "dried",
        "instant",
        "dehydrated",
        "condensed",
        "unprepared",
    }
)
_PROCESSED_PENALTY = 5
_NICHE_CATEGORY = re.compile(r"^(baby|formula|fast foods|restaurant foods|american indian)", re.IGNORECASE)
# "NFS" / "NS as to ..." entries are USDA's generic defaults: the right pick when the photo
# cannot resolve the detail.
_GENERIC_MARKER = re.compile(r"\b(NFS|NS as to)\b")
_SOURCE_PENALTY = {"Survey (FNDDS)": 0.0, "Foundation": 0.3, "SR Legacy": 0.6}
_CANDIDATE_POOL = 100


def _terms(text: str) -> list[str]:
    seen: list[str] = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return seen


def _stem(word: str) -> str:
    """Light suffix stripping used only for ranking (FTS5's porter stemmer handles recall).

    Applied identically to query and description words, so it only needs to be consistent.
    """
    if len(word) > 4 and word.endswith(("ies", "ied")):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh", "o")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def _quoted(terms: list[str]) -> list[str]:
    return [f'"{t}"' for t in terms]


@dataclass(frozen=True, slots=True)
class _Scorer:
    """Orders candidates: everyday foods before brand/baby items, then foods whose name starts
    with the query's main food (FNDDS names it first, as in "Pizza, cheese, ..." and
    "Pizza with pepperoni, ..."), then by how well the description covers the query and
    nothing more.

    bm25 alone prefers the shortest description ("Rice, white, cooked, glutinous" over the
    generic "Rice, white, cooked, NS as to fat") and ranks "Topping from cheese pizza" above
    "Pizza, cheese, ...", both wrong for nutrition.
    """

    weights: dict[str, float]
    head_prefix: str

    @classmethod
    def for_query(cls, query: str, hints: tuple[str, ...]) -> _Scorer:
        head_text = query.split(",", 1)[0]
        weights = {_stem(t): 1.0 for t in _terms(" ".join((query, *hints)))}
        weights.update({_stem(t): 2.0 for t in _terms(head_text)})
        return cls(weights, head_text.strip().lower())

    def key(self, row: tuple) -> tuple[bool, bool, float, float, int]:
        _, description, data_type, _, category, bm25 = row
        words = _terms(description)
        stems = {_stem(w) for w in words}
        matched = sum(w for t, w in self.weights.items() if t in stems)
        extras = stems - self.weights.keys()
        negations = sum(1 for w in words if w in _NEGATIONS and _stem(w) not in self.weights)
        processed = 0 if "prepared" in words else sum(
            1 for w in words if w in _PROCESSED and _stem(w) not in self.weights
        )
        score = (
            3 * matched
            - len(extras)
            - 2 * negations
            - _PROCESSED_PENALTY * processed
            - _SOURCE_PENALTY.get(data_type, 0.6)
        )
        if _GENERIC_MARKER.search(description):
            score += 0.5
        # FNDDS names "X with Y" foods X first ("Pizza with pepperoni"); any other word after the
        # main food makes another food ("Salad dressing", "Onion dip").
        lead = description.lower().split(",", 1)[0]
        head_first = lead == self.head_prefix or lead.startswith(self.head_prefix + " with ")
        niche = bool(_NICHE_CATEGORY.match(category))
        return niche, not head_first, -score, bm25, len(description)


class FoodDatabase:
    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise FoodDatabaseMissing(path)
        self._con = sqlite3.connect(path, check_same_thread=False)

    def close(self) -> None:
        self._con.close()

    def get(self, fdc_id: int) -> Food | None:
        row = self._con.execute(
            "SELECT fdc_id, description, data_type, nutrients FROM foods WHERE fdc_id = ?", (fdc_id,)
        ).fetchone()
        return _food(row) if row else None

    def search(self, query: str, *, hints: tuple[str, ...] = (), limit: int = 5) -> list[Food]:
        """Rank foods for an FNDDS-style query such as "Rice, white, cooked, made with oil".

        The words before the first comma name the food and must all match; the remaining
        words and `hints` (e.g. "cooked", "oil") only improve ranking. If no food contains
        every head word, any word may match.
        """
        head = _terms(query.split(",", 1)[0])
        terms = _terms(" ".join((query, *hints)))
        if not terms:
            return []
        any_term = " OR ".join(_quoted(terms))
        expressions = [any_term]
        if head:
            expressions.insert(0, f"({' AND '.join(_quoted(head))}) AND ({any_term})")
        scorer = _Scorer.for_query(query, hints)
        for expression in expressions:
            rows = self._con.execute(
                """
                SELECT f.fdc_id, f.description, f.data_type, f.nutrients, f.category, bm25(foods_fts)
                FROM foods_fts
                JOIN foods AS f ON f.fdc_id = foods_fts.rowid
                WHERE foods_fts MATCH ?
                ORDER BY bm25(foods_fts)
                LIMIT ?
                """,
                (expression, _CANDIDATE_POOL),
            ).fetchall()
            if rows:
                rows.sort(key=scorer.key)
                return [_food(r[:4]) for r in rows[:limit]]
        return []


def _food(row: tuple[int, str, str, str]) -> Food:
    fdc_id, description, data_type, nutrients = row
    return Food(fdc_id, description, data_type, json.loads(nutrients))
