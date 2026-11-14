"""Step 7: meal log and the user's usual portions. Photos and coordinates are never stored.

Every row belongs to one user: an account id from the web app, or '' for the local user
(the CLI, or the web app with sign-in turned off). A Diary only sees its own user's rows.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .schemas import MealResult

_MEALS = """
CREATE TABLE IF NOT EXISTS meals (
    id         INTEGER PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT '',
    local_date TEXT NOT NULL,
    meal_slot  TEXT NOT NULL,
    taken_at   TEXT NOT NULL,
    result     TEXT NOT NULL
);
"""
_USUAL_PORTIONS = """
CREATE TABLE IF NOT EXISTS usual_portions (
    user_id     TEXT    NOT NULL DEFAULT '',
    name        TEXT    NOT NULL,
    grams_total REAL    NOT NULL,
    corrections INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, name)
);
"""
_MEALS_INDEX = "CREATE INDEX IF NOT EXISTS meals_by_user_date ON meals (user_id, local_date, taken_at);"


class Diary:
    def __init__(self, path: Path, user_id: str | None = None) -> None:
        """`user_id` None means the local user."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._user = user_id or ""
        self._con = sqlite3.connect(path)
        self._con.executescript(_MEALS + _USUAL_PORTIONS)
        _migrate(self._con)
        self._con.executescript(_MEALS_INDEX)

    def close(self) -> None:
        self._con.close()

    def save(self, result: MealResult) -> MealResult:
        ctx = result.context
        row = (ctx.taken_at.date().isoformat(), ctx.meal_slot, ctx.taken_at.isoformat())
        with self._con:
            if result.id is None:
                cur = self._con.execute(
                    "INSERT INTO meals (user_id, local_date, meal_slot, taken_at, result) VALUES (?, ?, ?, ?, '')",
                    (self._user, *row),
                )
                result = result.model_copy(update={"id": cur.lastrowid})
            self._con.execute(
                "UPDATE meals SET local_date = ?, meal_slot = ?, taken_at = ?, result = ? WHERE id = ? AND user_id = ?",
                (*row, result.model_dump_json(), result.id, self._user),
            )
        return result

    def get(self, meal_id: int) -> MealResult | None:
        row = self._con.execute(
            "SELECT result FROM meals WHERE id = ? AND user_id = ?", (meal_id, self._user)
        ).fetchone()
        return MealResult.model_validate_json(row[0]) if row else None

    def meals_between(self, first: date, last: date) -> list[MealResult]:
        rows = self._con.execute(
            "SELECT result FROM meals WHERE user_id = ? AND local_date BETWEEN ? AND ? ORDER BY local_date, taken_at",
            (self._user, first.isoformat(), last.isoformat()),
        ).fetchall()
        return [MealResult.model_validate_json(r[0]) for r in rows]

    def remember_portion(self, name: str, grams: int) -> None:
        with self._con:
            self._con.execute(
                """
                INSERT INTO usual_portions (user_id, name, grams_total, corrections) VALUES (?, ?, ?, 1)
                ON CONFLICT (user_id, name) DO UPDATE SET
                    grams_total = grams_total + excluded.grams_total,
                    corrections = corrections + 1,
                    updated_at  = datetime('now')
                """,
                (self._user, _portion_key(name), grams),
            )

    def usual_portions(self, limit: int = 20) -> dict[str, int]:
        rows = self._con.execute(
            """
            SELECT name, grams_total / corrections FROM usual_portions
            WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?
            """,
            (self._user, limit),
        ).fetchall()
        return {name: round(grams) for name, grams in rows}


def _portion_key(name: str) -> str:
    return " ".join(name.lower().split())


def _migrate(con: sqlite3.Connection) -> None:
    """Upgrade a diary from before accounts: its meals and usual portions become the local user's."""
    if _has_user_id(con, "meals") and _has_user_id(con, "usual_portions"):
        return
    con.execute("BEGIN IMMEDIATE")  # one migrating connection at a time; check again under the lock
    try:
        if not _has_user_id(con, "meals"):
            con.execute("ALTER TABLE meals ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            con.execute("DROP INDEX IF EXISTS meals_by_date")
        if not _has_user_id(con, "usual_portions"):
            # The key changes from (name) to (user_id, name), which needs a new table.
            con.execute("ALTER TABLE usual_portions RENAME TO usual_portions_old")
            con.execute(_USUAL_PORTIONS)
            con.execute(
                """
                INSERT INTO usual_portions (user_id, name, grams_total, corrections, updated_at)
                SELECT '', name, grams_total, corrections, updated_at FROM usual_portions_old
                """
            )
            con.execute("DROP TABLE usual_portions_old")
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _has_user_id(con: sqlite3.Connection, table: str) -> bool:
    return any(column[1] == "user_id" for column in con.execute(f"PRAGMA table_info({table})"))
