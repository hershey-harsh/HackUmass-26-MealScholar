"""Step 7: meal log and the user's usual portions. Photos and coordinates are never stored."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .schemas import MealResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id         INTEGER PRIMARY KEY,
    local_date TEXT NOT NULL,
    meal_slot  TEXT NOT NULL,
    taken_at   TEXT NOT NULL,
    result     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meals_by_date ON meals (local_date, taken_at);
CREATE TABLE IF NOT EXISTS usual_portions (
    name        TEXT PRIMARY KEY,
    grams_total REAL    NOT NULL,
    corrections INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


class Diary:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.executescript(_SCHEMA)

    def close(self) -> None:
        self._con.close()

    def save(self, result: MealResult) -> MealResult:
        ctx = result.context
        row = (ctx.taken_at.date().isoformat(), ctx.meal_slot, ctx.taken_at.isoformat())
        with self._con:
            if result.id is None:
                cur = self._con.execute(
                    "INSERT INTO meals (local_date, meal_slot, taken_at, result) VALUES (?, ?, ?, '')", row
                )
                result = result.model_copy(update={"id": cur.lastrowid})
            self._con.execute(
                "UPDATE meals SET local_date = ?, meal_slot = ?, taken_at = ?, result = ? WHERE id = ?",
                (*row, result.model_dump_json(), result.id),
            )
        return result

    def get(self, meal_id: int) -> MealResult | None:
        row = self._con.execute("SELECT result FROM meals WHERE id = ?", (meal_id,)).fetchone()
        return MealResult.model_validate_json(row[0]) if row else None

    def meals_between(self, first: date, last: date) -> list[MealResult]:
        rows = self._con.execute(
            "SELECT result FROM meals WHERE local_date BETWEEN ? AND ? ORDER BY local_date, taken_at",
            (first.isoformat(), last.isoformat()),
        ).fetchall()
        return [MealResult.model_validate_json(r[0]) for r in rows]

    def remember_portion(self, name: str, grams: int) -> None:
        with self._con:
            self._con.execute(
                """
                INSERT INTO usual_portions (name, grams_total, corrections) VALUES (?, ?, 1)
                ON CONFLICT (name) DO UPDATE SET
                    grams_total = grams_total + excluded.grams_total,
                    corrections = corrections + 1,
                    updated_at  = datetime('now')
                """,
                (_portion_key(name), grams),
            )

    def usual_portions(self, limit: int = 20) -> dict[str, int]:
        rows = self._con.execute(
            "SELECT name, grams_total / corrections FROM usual_portions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {name: round(grams) for name, grams in rows}


def _portion_key(name: str) -> str:
    return " ".join(name.lower().split())
