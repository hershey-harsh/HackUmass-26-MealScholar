"""Account emails. Nothing is delivered yet: messages are queued in the email_outbox collection,
where a delivery worker or a real provider can pick them up later.

Emails contain no user-written text (such as the account name), so a sign-up with someone
else's address can't be used to send them arbitrary content.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .auth_store import AuthStore, EmailPurpose, OutboxEmail, utc_now

OUTBOX_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Email:
    to: str
    subject: str
    text: str
    purpose: EmailPurpose


class EmailSender(Protocol):
    provider: str

    def send(self, email: Email) -> None: ...


class OutboxSender:
    """Queues each message in the store's email_outbox, kept for 7 days."""

    provider = "outbox"

    def __init__(self, store: AuthStore, clock: Callable[[], datetime] = utc_now) -> None:
        self._store = store
        self._clock = clock

    def send(self, email: Email) -> None:
        now = self._clock()
        self._store.queue_email(
            OutboxEmail(
                to=email.to,
                subject=email.subject,
                text=email.text,
                purpose=email.purpose,
                created_at=now,
                expires_at=now + OUTBOX_TTL,
                status="queued",
                provider=self.provider,
            )
        )


class SmtpSender:
    """Placeholder for delivery over SMTP; not implemented yet."""

    provider = "smtp"

    def send(self, email: Email) -> None:
        raise NotImplementedError(
            "Sending email over SMTP is not implemented yet. Set MEALSCHOLAR_EMAIL_PROVIDER=outbox "
            "to queue messages in email_outbox, or implement SmtpSender.send in mealscholar/mailer.py."
        )


def make_sender(provider: str, store: AuthStore) -> EmailSender:
    if provider == "outbox":
        return OutboxSender(store)
    if provider == "smtp":
        return SmtpSender()
    raise ValueError(f"Unknown email provider {provider!r}; use outbox or smtp.")


def verification_email(to: str, link: str) -> Email:
    return Email(
        to=to,
        subject="Confirm your MealScholar email",
        text=(
            "Confirm your email address to finish setting up your MealScholar account:\n\n"
            f"{link}\n\n"
            "The link works for 24 hours. If you didn't sign up, you can ignore this.\n"
        ),
        purpose="verify_email",
    )


def password_reset_email(to: str, link: str) -> Email:
    return Email(
        to=to,
        subject="Reset your MealScholar password",
        text=(
            "Someone asked to reset the password for your MealScholar account. To choose a new one, open:\n\n"
            f"{link}\n\n"
            "The link expires in 1 hour and works once. If you didn't ask for this, you can ignore "
            "this email; your password stays the same.\n"
        ),
        purpose="reset_password",
    )
