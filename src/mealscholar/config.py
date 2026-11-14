"""Settings from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path

DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_PUBLIC_URL = "http://127.0.0.1:8000"
DEFAULT_SENDER_NAME = "MealScholar"
EMAIL_PROVIDERS = ("outbox", "smtp")
THINKING_LEVELS = ("minimal", "low", "medium", "high", "xhigh")
"""Gemini thinking levels the installed SDK accepts (google.genai.interactions.ThinkingLevel)."""
USER_AGENT = "MealScholar/0.1 (HackUMass prototype; https://github.com/hershey-harsh/HackUmass-26-MealScholar)"


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """A mail relay for MEALSCHOLAR_EMAIL_PROVIDER=smtp: STARTTLS on port 587, or plain on a local relay."""

    host: str
    port: int = 587
    user: str | None = None
    password: str | None = None
    sender: str = ""
    """The From header, such as "MealScholar <no-reply@example.com>"."""
    starttls: bool = True
    timeout: float = 10.0
    """Seconds per socket operation, so one dead relay never holds a worker for long."""


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
    smtp: SmtpSettings | None = None
    """Set when MEALSCHOLAR_SMTP_HOST is; required with email_provider "smtp"."""

    @property
    def email_sending(self) -> bool:
        """Account emails really go out: the smtp provider with a relay host, a From address and,
        with a login user, its password. The outbox only queues them, so the app must not say
        "we sent you a link"."""
        s = self.smtp
        return (
            self.email_provider == "smtp"
            and s is not None
            and bool(s.host and s.sender)
            and (s.user is None or bool(s.password))
        )

    @property
    def food_db(self) -> Path:
        return self.home / "fdc.sqlite"

    @property
    def diary_db(self) -> Path:
        return self.home / "diary.sqlite"

    @property
    def downloads(self) -> Path:
        return self.home / "downloads"

    @property
    def photos(self) -> Path:
        """Logged meals' photos (photos.py)."""
        return self.home / "photos"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    email_provider = _email_provider(env.get("MEALSCHOLAR_EMAIL_PROVIDER") or "outbox")
    smtp = _smtp(env)
    if email_provider == "smtp" and (smtp is None or not smtp.sender):
        raise ValueError("MEALSCHOLAR_EMAIL_PROVIDER=smtp needs MEALSCHOLAR_SMTP_HOST and MEALSCHOLAR_EMAIL_FROM.")
    if smtp is not None and smtp.sender:
        sender_address(smtp.sender)  # a malformed From fails at start-up, not on the first email
    return Settings(
        gemini_api_key=env.get("GEMINI_API_KEY") or None,
        gemini_model=env.get("MEALSCHOLAR_MODEL", DEFAULT_MODEL),
        thinking_level=_thinking_level(env.get("MEALSCHOLAR_THINKING") or "low"),
        home=Path(env.get("MEALSCHOLAR_HOME", "~/.mealscholar")).expanduser(),
        nominatim_url=env.get("MEALSCHOLAR_NOMINATIM_URL", DEFAULT_NOMINATIM_URL),
        mongodb_uri=env.get("MONGODB_URI") or None,
        mongodb_db=env.get("MEALSCHOLAR_MONGODB_DB") or "mealscholar",
        dining_db=env.get("MEALSCHOLAR_DINING_DB") or "umass_dining",
        auth_required=_flag(env, "MEALSCHOLAR_AUTH_REQUIRED", True),
        public_url=_public_url(env.get("MEALSCHOLAR_PUBLIC_URL") or DEFAULT_PUBLIC_URL),
        cookie_secure=_flag(env, "MEALSCHOLAR_COOKIE_SECURE", True),
        email_provider=email_provider,
        demo_verify_link=_flag(env, "MEALSCHOLAR_DEMO_VERIFY_LINK", False),
        dev_outbox=_flag(env, "MEALSCHOLAR_DEV_OUTBOX", False),
        smtp=smtp,
    )


def sender_address(sender: str) -> tuple[str, str]:
    """(display name, address) from MEALSCHOLAR_EMAIL_FROM, such as "MealScholar <no-reply@example.com>",
    or a bare address, which gets the name MealScholar. Raises ValueError unless it is one address."""
    found = getaddresses([sender]) if "\r" not in sender and "\n" not in sender else []
    name, address = found[0] if len(found) == 1 else ("", "")
    local, _, domain = address.rpartition("@")
    if not local or "." not in domain or any(c.isspace() or c in "<>,;" for c in address):
        raise ValueError(
            f"MEALSCHOLAR_EMAIL_FROM must be one address, like 'MealScholar <no-reply@example.com>', not {sender!r}."
        )
    return name.strip() or DEFAULT_SENDER_NAME, address


def _smtp(env: Mapping[str, str]) -> SmtpSettings | None:
    host = env.get("MEALSCHOLAR_SMTP_HOST", "").strip()
    if not host:
        return None
    return SmtpSettings(
        host=host,
        port=_port(env.get("MEALSCHOLAR_SMTP_PORT") or "587"),
        user=env.get("MEALSCHOLAR_SMTP_USER") or None,
        password=env.get("MEALSCHOLAR_SMTP_PASSWORD") or None,
        sender=env.get("MEALSCHOLAR_EMAIL_FROM", "").strip(),
        starttls=_flag(env, "MEALSCHOLAR_SMTP_STARTTLS", True),
        timeout=_timeout(env.get("MEALSCHOLAR_SMTP_TIMEOUT") or "10"),
    )


def _port(value: str) -> int:
    try:
        port = int(value.strip())
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError(f"MEALSCHOLAR_SMTP_PORT must be a port number from 1 to 65535, not {value!r}.")
    return port


def _timeout(value: str) -> float:
    try:
        seconds = float(value.strip())
    except ValueError:
        seconds = 0.0
    if not seconds > 0:
        raise ValueError(f"MEALSCHOLAR_SMTP_TIMEOUT must be a number of seconds above 0, not {value!r}.")
    return seconds


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
        raise ValueError(f"MEALSCHOLAR_PUBLIC_URL must start with http:// or https://, not {value!r}.")
    return url


def _email_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in EMAIL_PROVIDERS:
        raise ValueError(f"MEALSCHOLAR_EMAIL_PROVIDER must be one of {', '.join(EMAIL_PROVIDERS)}, not {value!r}.")
    return provider


def _thinking_level(value: str) -> str:
    level = value.strip().lower()
    if level not in THINKING_LEVELS:
        raise ValueError(f"MEALSCHOLAR_THINKING must be one of {', '.join(THINKING_LEVELS)}, not {value!r}.")
    return level
