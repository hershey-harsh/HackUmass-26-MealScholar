"""Account emails. By default messages are queued in the email_outbox collection, where a delivery
worker or a real provider can pick them up later; with MEALSCHOLAR_EMAIL_PROVIDER=smtp they go out
over SMTP from a small worker pool, so no request waits on a mail relay.

Emails contain no user-written text (such as the account name), so a sign-up with someone
else's address can't be used to send them arbitrary content.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

from .auth_store import AuthStore, EmailPurpose, OutboxEmail, utc_now
from .config import SmtpSettings

log = logging.getLogger(__name__)

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
    """Delivers over SMTP with STARTTLS. In background mode (the web app) `send` hands the message
    to a worker and returns at once, so sign-up and forgot-password take the same time whether or
    not an email goes out; a failed send is logged, never answered to the client. Synchronous mode
    (the digest command) raises, so the caller can count failures. Nothing is written to the outbox."""

    provider = "smtp"

    def __init__(self, settings: SmtpSettings, *, background: bool = True) -> None:
        self.settings = settings
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="smtp") if background else None
        self._pending: set[Future[None]] = set()

    def send(self, email: Email) -> None:
        if self._pool is None:
            self._deliver(email)
            return
        future = self._pool.submit(self._deliver_logged, email)
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)

    def drain(self, timeout: float | None = None) -> None:
        """Waits for every submitted send to finish (tests, and the end of a command)."""
        wait(list(self._pending), timeout=timeout)

    def _deliver_logged(self, email: Email) -> None:
        masked = _mask(email.to)
        try:
            self._deliver(email)
        except (smtplib.SMTPException, OSError) as exc:  # OSError covers socket.timeout and ssl.SSLError
            # Only the error's name and SMTP code: a relay's reply text repeats the address and the login user.
            log.warning("Couldn't send the %s email to %s: %s", email.purpose, masked, _describe(exc))
        else:
            log.info("Sent the %s email to %s", email.purpose, masked)

    def _deliver(self, email: Email) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["From"] = s.sender
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.text)
        # smtplib.SMTP is looked up at call time so tests can monkeypatch mealscholar.mailer.smtplib.SMTP.
        with smtplib.SMTP(s.host, s.port, timeout=s.timeout) as smtp:
            smtp.ehlo()
            if s.starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if s.user:
                smtp.login(s.user, s.password or "")
            smtp.send_message(msg)


def _describe(exc: BaseException) -> str:
    """'SMTPRecipientsRefused (550)': the type and reply code, never the relay's text."""
    codes: list[int] = []
    if isinstance(exc, smtplib.SMTPResponseException):
        codes = [exc.smtp_code]
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = sorted({code for code, _ in exc.recipients.values()})
    name = type(exc).__name__
    return f"{name} ({', '.join(str(c) for c in codes)})" if codes else name


def _mask(address: str) -> str:
    """s***@example.com: enough for a log line, not an address."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def make_sender(
    provider: str, store: AuthStore, smtp: SmtpSettings | None = None, *, background: bool = True
) -> EmailSender:
    if provider == "outbox":
        return OutboxSender(store)
    if provider == "smtp":
        if smtp is None:
            raise ValueError("MEALSCHOLAR_EMAIL_PROVIDER=smtp needs MEALSCHOLAR_SMTP_HOST and MEALSCHOLAR_EMAIL_FROM.")
        return SmtpSender(smtp, background=background)
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
