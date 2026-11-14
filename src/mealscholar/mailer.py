"""Account emails. By default messages are queued in the email_outbox collection, where a delivery
worker or a real provider can pick them up later; with MEALSCHOLAR_EMAIL_PROVIDER=smtp they go out
over SMTP from a small worker pool, so no request waits on a mail relay.

Emails contain no user-written text (such as the account name), so a sign-up with someone
else's address can't be used to send them arbitrary content. Each one is plain text with a
simple HTML alternative: inline styles only, no images, remote assets or tracking.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from html import escape
from typing import Protocol

from .auth_store import AuthStore, EmailPurpose, OutboxEmail, utc_now
from .config import SmtpSettings, sender_address

log = logging.getLogger(__name__)

OUTBOX_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Email:
    to: str
    subject: str
    text: str
    purpose: EmailPurpose
    html: str | None = None
    """The HTML alternative to `text`; built from fixed wording and escaped values only."""


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
        """Raises ValueError at once, in either mode, for a header with a line break in it."""
        check_header(email.to, "recipient")
        check_header(email.subject, "subject")
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
        masked = mask_address(email.to)
        try:
            self._deliver(email)
        except Exception as exc:  # SMTP and socket errors, and anything else: a worker's error is never seen otherwise
            # Only the error's name and SMTP code: a relay's reply text repeats the address and the login user.
            log.warning("Couldn't send the %s email to %s: %s", email.purpose, masked, describe_error(exc))
        else:
            log.info("Sent the %s email to %s", email.purpose, masked)

    def _deliver(self, email: Email) -> None:
        s = self.settings
        name, address = sender_address(s.sender)
        msg = EmailMessage()
        msg["From"] = formataddr((name, address))
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg["Date"] = format_datetime(utc_now())
        msg["Message-ID"] = make_msgid(domain=address.rpartition("@")[2])
        msg["Auto-Submitted"] = "auto-generated"  # RFC 3834: no vacation replies to a no-reply address
        msg.set_content(email.text)
        if email.html is not None:
            msg.add_alternative(email.html, subtype="html")
        # smtplib.SMTP is looked up at call time so tests can monkeypatch mealscholar.mailer.smtplib.SMTP.
        with smtplib.SMTP(s.host, s.port, timeout=s.timeout) as smtp:
            smtp.ehlo()
            if s.starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if s.user:
                smtp.login(s.user, s.password or "")
            smtp.send_message(msg)


def check_header(value: str, what: str) -> None:
    """Refuses a line break, which would let the value start a header of its own."""
    if "\r" in value or "\n" in value:
        raise ValueError(f"The email {what} can't contain a line break.")


def describe_error(exc: BaseException) -> str:
    """'SMTPRecipientsRefused (550)': the type and reply code, never the relay's text, which repeats
    the address and the login user. For a log line about a failed send, together with mask_address."""
    codes: list[int] = []
    if isinstance(exc, smtplib.SMTPResponseException):
        codes = [exc.smtp_code]
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = sorted({code for code, _ in exc.recipients.values()})
    name = type(exc).__name__
    return f"{name} ({', '.join(str(c) for c in codes)})" if codes else name


def mask_address(address: str) -> str:
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


# --- the messages -------------------------------------------------------------------

# Colors from the app: page #EEF0F2, card #FAFAFF, ink #1C1C1C, secondary text #494949 (7.9:1 on the page).
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def html_email(
    lead: str,
    *,
    footer: str,
    link: str | None = None,
    button: str | None = None,
    details: Sequence[tuple[str, Sequence[str]]] = (),
) -> str:
    """A one-column HTML email: the MealScholar wordmark, one short sentence, optional sections of
    lines (a heading each), a button-styled link with the raw link under it, and a quiet footer.
    Every value is escaped; there are no images, remote assets or tracking."""
    e = escape
    parts = [
        '<p style="margin:0 0 28px;font-size:15px;font-weight:600;letter-spacing:-0.01em;color:#1C1C1C">'
        "MealScholar</p>",
        f'<p style="margin:0 0 24px;font-size:17px;line-height:1.5;color:#1C1C1C">{e(lead)}</p>',
    ]
    for heading, lines in details:
        parts.append(f'<p style="margin:0 0 6px;font-size:13px;font-weight:600;color:#1C1C1C">{e(heading)}</p>')
        items = "".join(f'<li style="margin:0 0 4px">{e(line)}</li>' for line in lines)
        parts.append(
            f'<ul style="margin:0 0 20px;padding:0 0 0 18px;font-size:14px;line-height:1.5;color:#494949">{items}</ul>'
        )
    if link is not None:
        href = e(link, quote=True)
        parts.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 20px"><tr>'
            f'<td style="border-radius:999px;background:#1C1C1C"><a href="{href}" style="display:inline-block;'
            f'padding:12px 22px;font-size:15px;font-weight:600;color:#FAFAFF;text-decoration:none;border-radius:999px">'
            f"{e(button or 'Open MealScholar')}</a></td></tr></table>"
        )
        parts.append(
            '<p style="margin:0 0 4px;font-size:13px;color:#494949">Or paste this link into your browser:</p>'
            f'<p style="margin:0;font-size:13px;line-height:1.5;word-break:break-all"><a href="{href}" '
            f'style="color:#1C1C1C">{e(link)}</a></p>'
        )
    body = "".join(parts)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"><title>MealScholar</title></head>'
        f'<body style="margin:0;padding:0;background:#EEF0F2;font-family:{_FONT}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF0F2">'
        '<tr><td align="center" style="padding:32px 16px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px">'
        f'<tr><td style="padding:32px 28px;background:#FAFAFF;border-radius:16px">{body}</td></tr>'
        f'<tr><td style="padding:20px 28px 0;font-size:12px;line-height:1.5;color:#494949">{e(footer)}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


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
        html=html_email(
            "Confirm your email address to finish setting up your MealScholar account.",
            link=link,
            button="Confirm email",
            footer="You got this because someone signed up for MealScholar with this address. The link works "
            "for 24 hours. If it wasn't you, you can ignore this email.",
        ),
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
        html=html_email(
            "Someone asked to reset the password for your MealScholar account. Choose a new one below.",
            link=link,
            button="Choose a new password",
            footer="You got this because someone asked to reset the password for this address. The link "
            "expires in 1 hour and works once. If it wasn't you, ignore this email; your password stays the same.",
        ),
    )


def password_changed_email(to: str, app_url: str) -> Email:
    """After a password change or reset. `app_url` is the app's address, where Forgot password is."""
    link = f"{app_url}/"
    return Email(
        to=to,
        subject="Your MealScholar password was changed",
        text=(
            "The password for your MealScholar account was just changed, and every other device was signed out.\n\n"
            "If this was you, there's nothing else to do. If it wasn't, reset your password right away: "
            f"open {link} and choose Forgot password.\n"
        ),
        purpose="password_changed",
        html=html_email(
            "The password for your MealScholar account was just changed, and every other device was signed out.",
            link=link,
            button="Open MealScholar",
            footer="You got this because the password for this address was changed. If it wasn't you, reset "
            "your password right away: open MealScholar and choose Forgot password.",
        ),
    )
