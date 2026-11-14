"""Settings from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_PUBLIC_URL = "http://127.0.0.1:8000"
EMAIL_PROVIDERS = ("outbox", "smtp")
USER_AGENT = "foodsmart/0.1 (HackUMass prototype; https://github.com/killerfrost22/26hackumass-foodsmart)"


@dataclass(frozen=True, slots=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    thinking_level: str
    home: Path
    nominatim_url: str
    # Accounts (auth.py)
    mongodb_uri: str | None = None
    """Accounts live in MongoDB when set; otherwise in memory and vanish on restart."""
    mongodb_db: str = "mealscholar"
    dining_db: str = "umass_dining"
    """UMass Dining menus, read through MONGODB_URI (dining.py); without the URI there are none."""
    auth_required: bool = True
    """Meal and diary routes need a signed-in user. Off: local single-user use."""
    public_url: str = DEFAULT_PUBLIC_URL
    """Base of the links in emails, without a trailing slash."""
    cookie_secure: bool = True
    email_provider: str = "outbox"
    demo_verify_link: bool = False
    """Return email links in API responses so a demo works without email. Never on a public server."""
    dev_outbox: bool = False

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
        mongodb_uri=env.get("MONGODB_URI") or None,
        mongodb_db=env.get("FOODSMART_MONGODB_DB") or "mealscholar",
        dining_db=env.get("FOODSMART_DINING_DB") or "umass_dining",
        auth_required=_flag(env, "FOODSMART_AUTH_REQUIRED", True),
        public_url=_public_url(env.get("FOODSMART_PUBLIC_URL") or DEFAULT_PUBLIC_URL),
        cookie_secure=_flag(env, "FOODSMART_COOKIE_SECURE", True),
        email_provider=_email_provider(env.get("FOODSMART_EMAIL_PROVIDER") or "outbox"),
        demo_verify_link=_flag(env, "FOODSMART_DEMO_VERIFY_LINK", False),
        dev_outbox=_flag(env, "FOODSMART_DEV_OUTBOX", False),
    )


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be 1 or 0, not {value!r}.")


def _public_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"FOODSMART_PUBLIC_URL must start with http:// or https://, not {value!r}.")
    return url


def _email_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in EMAIL_PROVIDERS:
        raise ValueError(f"FOODSMART_EMAIL_PROVIDER must be one of {', '.join(EMAIL_PROVIDERS)}, not {value!r}.")
    return provider
