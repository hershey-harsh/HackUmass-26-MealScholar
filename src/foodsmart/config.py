"""Settings from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "foodsmart/0.1 (HackUMass prototype; https://github.com/killerfrost22/26hackumass-foodsmart)"


@dataclass(frozen=True, slots=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    thinking_level: str
    home: Path
    nominatim_url: str

    @property
    def food_db(self) -> Path:
        return self.home / "fdc.sqlite"

    @property
    def diary_db(self) -> Path:
        return self.home / "diary.sqlite"

    @property
    def downloads(self) -> Path:
        return self.home / "downloads"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    return Settings(
        gemini_api_key=env.get("GEMINI_API_KEY") or None,
        gemini_model=env.get("FOODSMART_MODEL", DEFAULT_MODEL),
        thinking_level=env.get("FOODSMART_THINKING", "low"),
        home=Path(env.get("FOODSMART_HOME", "~/.foodsmart")).expanduser(),
        nominatim_url=env.get("FOODSMART_NOMINATIM_URL", DEFAULT_NOMINATIM_URL),
    )
