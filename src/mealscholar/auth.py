"""Accounts: registration, sign-in sessions, email confirmation and password reset.

Passwords are hashed with scrypt. The browser holds a random session token in an HttpOnly
cookie and the server stores only its SHA-256, so a copy of the database can't sign anyone in.
Account emails go through mailer.py.

`install_auth` adds the /api/auth routes to the app; `DiaryOwner` guards the meal and diary routes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import math
import re
import secrets
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import cache
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth_store import AuthStore, EmailTaken, EmailToken, OutboxEmail, Session, TokenPurpose, User, open_store, utc_now
from .config import Settings
from .mailer import Email, EmailSender, make_sender, password_changed_email, password_reset_email, verification_email

log = logging.getLogger(__name__)

SESSION_COOKIE = "ms_session"
SESSION_TTL = timedelta(days=30)
SESSION_TOUCH_EVERY = timedelta(minutes=5)
"""How often a session's last_seen_at is written, at most."""
TOKEN_TTL: dict[TokenPurpose, timedelta] = {"verify_email": timedelta(hours=24), "reset_password": timedelta(hours=1)}
LINK_PARAM: dict[TokenPurpose, str] = {"verify_email": "verify", "reset_password": "reset"}

MIN_PASSWORD, MAX_PASSWORD = 8, 128
MAX_EMAIL, MAX_NAME, MAX_USER_AGENT, MAX_TOKEN = 254, 80, 200, 128
MAX_AUTH_BODY = 16 * 1024
"""Bytes. Account forms are small; bigger request bodies are refused before they are read."""
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

SIGN_IN = "Sign in to continue."
BAD_LOGIN = "Email or password is incorrect."
EMAIL_TAKEN = "An account with that email already exists."
BAD_LINK = "That link is invalid or has expired."


# --- passwords ----------------------------------------------------------------

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2**17, 8, 1, 32
_SCRYPT_MAXMEM = 256 * 2**20
_hash_slots = threading.BoundedSemaphore(4)  # each current scrypt run needs about 128 MiB


def hash_password(password: str) -> str:
    """'scrypt$N$r$p$<salt>$<key>' with base64 salt and key."""
    salt = secrets.token_bytes(16)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, key = encoded.split("$")
        expected = base64.b64decode(key, validate=True)
        if scheme != "scrypt" or not expected:
            return False
        actual = _scrypt(password, base64.b64decode(salt, validate=True), int(n), int(r), int(p), len(expected))
    except ValueError:  # malformed hash, or a password that isn't valid text
        return False
    return hmac.compare_digest(actual, expected)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    # NFKC: the same password typed on different keyboards or systems gives the same bytes.
    secret = unicodedata.normalize("NFKC", password).encode()
    with _hash_slots:
        return hashlib.scrypt(secret, salt=salt, n=n, r=r, p=p, dklen=length, maxmem=_SCRYPT_MAXMEM)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@cache
def _dummy_hash() -> str:
    """Checked against when the email has no account, so both cases take as long."""
    return hash_password(secrets.token_urlsafe(16))


# --- rate limits ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    limit: int
    window: float
    """Seconds."""


# "IP" here is Client.network: the IPv4 address, or the IPv6 /64.
LOGIN_PER_ACCOUNT = Rule("login", 10, 15 * 60)  # per (IP, email)
LOGIN_PER_IP = Rule("login-ip", 50, 15 * 60)
REGISTER_PER_IP = Rule("register", 30, 60 * 60)  # a whole room on campus Wi-Fi can share one address
FORGOT_PER_IP = Rule("forgot-ip", 5, 60 * 60)
FORGOT_PER_EMAIL = Rule("forgot-email", 3, 60 * 60)
# Per account from every network together, and silent (the answer stays 202), so many networks
# can't flood one inbox with reset emails and the cap doesn't say whether the account exists.
FORGOT_MAIL_PER_USER = Rule("forgot-mail", 5, 60 * 60)
RESEND_PER_USER = Rule("resend", 3, 60 * 60)
CHANGE_PASSWORD_PER_USER = Rule("change-password", 10, 15 * 60)
DELETE_ACCOUNT_PER_USER = Rule("delete-account", 10, 15 * 60)
ANALYSIS_PER_USER = Rule("analysis-user", 10, 10 * 60)
ANALYSIS_PER_IP = Rule("analysis-ip", 100, 10 * 60)  # campus Wi-Fi can share one network


class RateLimiter:
    """Sliding-window limits kept in process memory; they reset when the server restarts."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, Hashable], deque[float]] = {}
        self._windows: dict[str, float] = {}
        self._recorded = 0

    def hit(self, *checks: tuple[Rule, Hashable]) -> int | None:
        """Count one attempt against each (rule, subject). If any is already at its limit, count
        nothing and return the seconds until the attempt would be allowed."""
        now = self._clock()
        with self._lock:
            wait = 0.0
            for rule, subject in checks:
                hits = self._hits.get((rule.name, subject))
                while hits and hits[0] <= now - rule.window:
                    hits.popleft()
                if hits and len(hits) >= rule.limit:
                    wait = max(wait, hits[0] + rule.window - now)
            if wait > 0:
                return max(1, math.ceil(wait))
            for rule, subject in checks:
                self._windows[rule.name] = rule.window
                self._hits.setdefault((rule.name, subject), deque()).append(now)
            self._recorded += 1
            if self._recorded % 1024 == 0:  # forget subjects with no attempts left in their window
                self._hits = {k: h for k, h in self._hits.items() if h and h[-1] > now - self._windows[k[0]]}
            return None


# --- accounts ---------------------------------------------------------------------


class AuthError(Exception):
    """An answer for the client: HTTP status, a plain message, and the form field it is about."""

    def __init__(self, status: int, detail: str, *, field: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.field = field
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Client:
    """Who is asking. New sessions record the IP and user agent; rate limits go by `network`."""

    ip: str
    user_agent: str | None = None

    @property
    def network(self) -> str:
        """The IPv4 address, or the IPv6 /64: one machine or home network can usually pick any
        address in its /64, so counting single IPv6 addresses would hardly limit anyone."""
        address = _ip(self.ip)
        if address is None:
            return self.ip
        if address.version == 6:
            return str(ipaddress.ip_network((address, 64), strict=False))
        return str(address)


@dataclass(frozen=True, slots=True)
class SignedIn:
    user: User
    token: str
    """The raw session token, for the cookie only."""
    demo_url: str | None = None


class AuthService:
    def __init__(
        self,
        store: AuthStore,
        sender: EmailSender,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utc_now,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.store = store
        self.sender = sender
        self.settings = settings
        self.limiter = limiter or RateLimiter()
        self._clock = clock
        _dummy_hash()  # now, so the first sign-in with an unknown email isn't slower than later ones

    @classmethod
    def from_settings(
        cls, settings: Settings, *, store: AuthStore | None = None, clock: Callable[[], datetime] = utc_now
    ) -> AuthService:
        if store is None:
            store = open_store(settings)
        if settings.demo_verify_link:
            log.warning(
                "MEALSCHOLAR_DEMO_VERIFY_LINK=1: API responses include email links, so anyone who knows an "
                "account's email can reset its password. Use it only for demos."
            )
        if settings.dev_outbox:
            log.warning(
                "MEALSCHOLAR_DEV_OUTBOX=1: /api/auth/dev/outbox shows queued emails, password reset links "
                "included, to anyone who can reach the server. Use it only on your own machine."
            )
        if settings.cookie_secure and settings.public_url.startswith("http://"):
            log.warning(
                "MEALSCHOLAR_COOKIE_SECURE=1 with an http:// MEALSCHOLAR_PUBLIC_URL: browsers keep the session "
                "cookie only over HTTPS (some also on localhost). Set MEALSCHOLAR_COOKIE_SECURE=0 to sign in "
                "over plain HTTP, such as from a phone on the local network."
            )
        return cls(store, make_sender(settings.email_provider, store, settings.smtp), settings, clock=clock)

    # sessions

    def session_user_id(self, token: str | None) -> str | None:
        session = self._session(token)
        return session.user_id if session else None

    def session_user(self, token: str | None) -> User | None:
        session = self._session(token)
        return self.store.user_by_id(session.user_id) if session else None

    def logout(self, token: str | None) -> None:
        if _plausible_token(token):
            self.store.delete_session(_digest(token))

    def limit_analysis(self, owner: str | None, client: Client) -> None:
        """Photos and clarifying answers share a budget; guests are limited by network."""
        network = (ANALYSIS_PER_IP, client.network)
        if owner is None:
            self._limit(network)
        else:
            self._limit((ANALYSIS_PER_USER, owner), network)

    # account flows

    def register(self, email: str, password: str, name: str | None, client: Client) -> SignedIn:
        email = _checked_email(email)
        name = _checked_name(name)
        _check_password(password, email, field="password")
        self._limit((REGISTER_PER_IP, client.network))
        if self.store.user_by_email(email) is not None:
            raise AuthError(409, EMAIL_TAKEN, field="email")
        try:
            user = self.store.create_user(email, name, hash_password(password), self._clock())
        except EmailTaken:
            raise AuthError(409, EMAIL_TAKEN, field="email") from None
        signed_in = self._sign_in(user, client)
        return replace(signed_in, demo_url=self._demo(self._email_link(user, "verify_email")))

    def login(self, email: str, password: str, client: Client) -> SignedIn:
        address = _normalized_email(email)
        # Invalid emails share one bucket per network, so junk input can't grow the limiter's memory.
        self._limit((LOGIN_PER_ACCOUNT, (client.network, address or "")), (LOGIN_PER_IP, client.network))
        user = self.store.user_by_email(address) if address else None
        if _length(password) > MAX_PASSWORD:  # no account can have it
            raise AuthError(401, BAD_LOGIN)
        if user is None:
            verify_password(password, _dummy_hash())
            raise AuthError(401, BAD_LOGIN)
        if not verify_password(password, user.password_hash):
            raise AuthError(401, BAD_LOGIN)
        return self._sign_in(user, client)

    def verify_email(self, token: str) -> User:
        found = self._email_token(token, "verify_email", use=True)
        user = self.store.mark_email_verified(found.user_id, self._clock()) if found else None
        if user is None:
            raise AuthError(400, BAD_LINK, field="token")
        return user

    def resend_verification(self, user: User) -> str | None:
        """Returns the link when demo links are on."""
        if user.email_verified_at is not None:
            raise AuthError(409, "Your email is already confirmed.")
        self._limit((RESEND_PER_USER, user.id))
        return self._demo(self._email_link(user, "verify_email"))

    def forgot_password(
        self, email: str, client: Client, defer: Callable[[Callable[[], None]], object] | None = None
    ) -> None:
        """Emails a reset link if the account exists. The answer never says whether it does and
        never contains the link, even with demo links on: anyone can ask for a reset, so showing
        the link would hand the account to whoever asked. `defer` runs the lookup and the email
        after the answer is sent (the web app's background tasks), so it takes the same time
        either way; without it they run now."""
        address = _normalized_email(email)
        if address is None:  # no account can have it: nothing to count or send
            return
        # Per (network, email), so one network can't use up another person's attempts.
        self._limit((FORGOT_PER_IP, client.network), (FORGOT_PER_EMAIL, (client.network, address)))
        (defer or _run_now)(lambda: self._reset_link(address))

    def _reset_link(self, address: str) -> None:
        try:
            user = self.store.user_by_email(address)
            if user and self.limiter.hit((FORGOT_MAIL_PER_USER, user.id)) is None:
                self._email_link(user, "reset_password")
        except Exception:  # after the answer: log it, never the address or the link
            log.exception("Couldn't make a password reset link")

    def reset_password(self, token: str, password: str, client: Client) -> SignedIn:
        """Sets the new password, signs the user out everywhere, then signs this browser in."""
        found = self._email_token(token, "reset_password", use=False)
        user = self.store.user_by_id(found.user_id) if found else None
        if user is None:
            raise AuthError(400, BAD_LINK, field="token")
        _check_password(password, user.email, field="password")
        # Use up the link only now, so a rejected password doesn't cost the user their link.
        if self._email_token(token, "reset_password", use=True) is None:
            raise AuthError(400, BAD_LINK, field="token")
        user = self.store.mark_email_verified(user.id, self._clock()) or user  # the link proves they read this inbox
        return self._new_password(user, password, client)

    def change_password(self, user: User, current: str, new: str, client: Client) -> SignedIn:
        """Signs out every session, this browser's too (its cookie may have been copied), then
        signs this browser in again."""
        _check_password(new, user.email, field="new_password")
        self._limit((CHANGE_PASSWORD_PER_USER, user.id))
        if _length(current) > MAX_PASSWORD or not verify_password(current, user.password_hash):
            raise AuthError(401, "Your current password is incorrect.", field="current_password")
        return self._new_password(user, new, client)

    def delete_account(self, user: User, password: str, erase: Callable[[str], object] | None = None) -> None:
        """Checks the password, runs `erase` with the user id (the app's own data for this user:
        meals, preferences), then deletes the account and signs it out everywhere. A wrong
        password deletes nothing."""
        self._limit((DELETE_ACCOUNT_PER_USER, user.id))
        if _length(password) > MAX_PASSWORD or not verify_password(password, user.password_hash):
            raise AuthError(401, "That password is incorrect.", field="password")
        if erase is not None:
            erase(user.id)
        self.store.delete_user(user.id)

    def recent_emails(self, limit: int = 20) -> list[OutboxEmail]:
        return self.store.recent_emails(limit)

    # helpers

    def _session(self, token: str | None) -> Session | None:
        if not _plausible_token(token):
            return None
        now = self._clock()
        session = self.store.session(_digest(token), now)
        if session is not None and now - session.last_seen_at >= SESSION_TOUCH_EVERY:
            self.store.touch_session(session.token_hash, now)
        return session

    def _sign_in(self, user: User, client: Client) -> SignedIn:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self.store.create_session(
            Session(
                token_hash=_digest(token),
                user_id=user.id,
                created_at=now,
                expires_at=now + SESSION_TTL,
                last_seen_at=now,
                user_agent=(client.user_agent or "")[:MAX_USER_AGENT] or None,
                ip=client.ip,
            )
        )
        self.store.record_login(user.id, now)
        return SignedIn(user, token)

    def _new_password(self, user: User, password: str, client: Client) -> SignedIn:
        """Set the password, cancel open reset links and end every session; then sign this browser
        in and tell the account's address, so an owner who didn't do it can reset it back."""
        now = self._clock()
        user = self.store.set_password_hash(user.id, hash_password(password), now) or user
        self.store.revoke_email_tokens(user.id, "reset_password", now)
        self.store.delete_user_sessions(user.id)
        signed_in = self._sign_in(user, client)
        if user.email_verified_at is not None:  # an unconfirmed address may not be the owner's: no mail to it
            self._send(password_changed_email(user.email, self.settings.public_url))
        return signed_in

    def _email_link(self, user: User, purpose: TokenPurpose) -> str:
        """Store a new one-time token, email its link to the user, and return the link."""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self.store.create_email_token(
            EmailToken(_digest(token), user.id, purpose, created_at=now, expires_at=now + TOKEN_TTL[purpose])
        )
        link = f"{self.settings.public_url}/?{LINK_PARAM[purpose]}={token}"
        compose = verification_email if purpose == "verify_email" else password_reset_email
        self._send(compose(user.email, link))
        return link

    def _send(self, email: Email) -> None:
        try:
            self.sender.send(email)
        except Exception:
            # Best effort: the account change already happened, and the user can ask for another email.
            log.exception("Couldn't send the %s email", email.purpose)

    def _email_token(self, token: str, purpose: TokenPurpose, *, use: bool) -> EmailToken | None:
        if not _plausible_token(token):
            return None
        find = self.store.use_email_token if use else self.store.email_token
        return find(_digest(token), purpose, self._clock())

    def _demo(self, link: str) -> str | None:
        return link if self.settings.demo_verify_link else None

    def _limit(self, *checks: tuple[Rule, Hashable]) -> None:
        wait = self.limiter.hit(*checks)
        if wait is not None:
            minutes = math.ceil(wait / 60)
            when = "1 minute" if minutes == 1 else f"{minutes} minutes"
            raise AuthError(429, f"Too many attempts. Try again in {when}.", retry_after=wait)


def _run_now(work: Callable[[], None]) -> None:
    work()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _plausible_token(token: str | None) -> bool:
    """Tokens are short URL-safe ASCII; anything else can't match one."""
    return bool(token) and len(token) <= MAX_TOKEN and token.isascii()


def _utf8(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:  # lone surrogates from JSON escapes
        return False
    return True


def _far_too_long(text: str, limit: int) -> bool:
    """Over 4 times the limit: too long in any form. Checked before text is normalised or split,
    which can take many times its size (NFKC turns U+FDFA into 18 characters)."""
    return len(text) > 4 * limit


def _normalized_email(email: str) -> str | None:
    """Trimmed, NFC and lower case, so one address can't make two accounts; None if it isn't one."""
    if _far_too_long(email, MAX_EMAIL):
        return None
    email = unicodedata.normalize("NFC", email.strip()).lower()
    if len(email) > MAX_EMAIL or EMAIL_PATTERN.fullmatch(email) is None:
        return None
    # Category C: control, invisible (zero-width, bidi), surrogate, private-use and unassigned characters.
    if any(unicodedata.category(c).startswith("C") for c in email):
        return None
    return email


def _checked_email(email: str) -> str:
    address = _normalized_email(email)
    if address is None:
        raise AuthError(400, "Enter a valid email address.", field="email")
    return address


def _checked_name(name: str | None) -> str | None:
    name = name or ""
    if not _far_too_long(name, MAX_NAME):  # collapse spaces; text that long is refused either way
        name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise AuthError(400, f"Keep your name to {MAX_NAME} characters or fewer.", field="name")
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in name):
        raise AuthError(400, "Your name has characters that can't be used.", field="name")
    return name or None


def _length(password: str) -> int:
    """Characters after NFKC, the form that is hashed. Text far over the limit is measured as it is."""
    if _far_too_long(password, MAX_PASSWORD):
        return len(password)
    return len(unicodedata.normalize("NFKC", password))


def _check_password(password: str, email: str, *, field: str) -> None:
    length = _length(password)
    if length < MIN_PASSWORD:
        raise AuthError(400, f"Use at least {MIN_PASSWORD} characters for your password.", field=field)
    if length > MAX_PASSWORD:
        raise AuthError(400, f"Use at most {MAX_PASSWORD} characters for your password.", field=field)
    if not _utf8(password):
        raise AuthError(400, "Your password has characters that can't be used.", field=field)
    if unicodedata.normalize("NFKC", password).strip().casefold() == email.casefold():
        raise AuthError(400, "Your password can't be your email address.", field=field)


# --- HTTP -------------------------------------------------------------------------


class Credentials(BaseModel):
    email: str
    password: str


class Registration(Credentials):
    name: str | None = None


class LinkToken(BaseModel):
    token: str


class EmailAddress(BaseModel):
    email: str


class PasswordReset(BaseModel):
    token: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class AccountDeletion(BaseModel):
    password: str


class UserInfo(BaseModel):
    id: str
    email: str
    name: str | None
    email_verified: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> UserInfo:
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
        )


def _is_none(value: Any) -> bool:
    return value is None


class UserReply(BaseModel):
    user: UserInfo


class RegisterReply(UserReply):
    demo_verify_url: str | None = Field(default=None, exclude_if=_is_none)


class VerificationReply(BaseModel):
    demo_verify_url: str | None = Field(default=None, exclude_if=_is_none)


class ResetReply(BaseModel):
    """Always empty: the reset link only ever goes out by email."""


class OutboxItem(BaseModel):
    to: str
    subject: str
    text: str
    purpose: str
    status: str
    provider: str
    created_at: datetime


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _service(request: Request) -> AuthService:
    return request.app.state.auth


Auth = Annotated[AuthService, Depends(_service)]


def current_user(request: Request, auth: Auth) -> User:
    user = auth.session_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise AuthError(401, SIGN_IN)
    return user


def diary_owner(request: Request, auth: Auth) -> str | None:
    """Whose diary a meal route reads and writes: the signed-in user's id, or None (the local
    diary) when sign-in is optional and nobody is signed in."""
    user_id = auth.session_user_id(request.cookies.get(SESSION_COOKIE))
    if user_id is None and auth.settings.auth_required:
        raise AuthError(401, SIGN_IN)
    return user_id


CurrentUser = Annotated[User, Depends(current_user)]
DiaryOwner = Annotated[str | None, Depends(diary_owner)]


def _client(request: Request) -> Client:
    peer = request.client.host if request.client else ""
    ip = peer
    # cloudflared, on this machine, passes the visitor's address in CF-Connecting-IP. Anyone could
    # send that header (`serve --host 0.0.0.0`), so it only counts from a loopback peer.
    if (address := _ip(peer)) is not None and address.is_loopback:
        ip = request.headers.get("cf-connecting-ip", "").strip() or peer
    return Client(ip=ip[:64] or "unknown", user_agent=request.headers.get("user-agent"))


RequestClient = Annotated[Client, Depends(_client)]


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP address in `text`, with IPv4-mapped IPv6 (::ffff:192.0.2.1) as IPv4; None if it isn't one."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _start_session(request: Request, response: Response, auth: AuthService, signed_in: SignedIn) -> None:
    """Set the new session cookie and end the session this browser had before, if any."""
    auth.logout(request.cookies.get(SESSION_COOKIE))
    response.set_cookie(
        SESSION_COOKIE,
        signed_in.token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        secure=auth.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/register", status_code=201)
def register(body: Registration, request: Request, response: Response, auth: Auth) -> RegisterReply:
    """Creates the account, signs it in and emails a confirmation link."""
    signed_in = auth.register(body.email, body.password, body.name, _client(request))
    _start_session(request, response, auth, signed_in)
    return RegisterReply(user=UserInfo.of(signed_in.user), demo_verify_url=signed_in.demo_url)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, auth: Auth) -> UserReply:
    signed_in = auth.login(body.email, body.password, _client(request))
    _start_session(request, response, auth, signed_in)
    return UserReply(user=UserInfo.of(signed_in.user))


@router.post("/logout", status_code=204)
def logout(request: Request, auth: Auth) -> Response:
    auth.logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=auth.settings.cookie_secure, httponly=True, samesite="lax")
    return response


@router.get("/me")
def me(user: CurrentUser) -> UserReply:
    return UserReply(user=UserInfo.of(user))


@router.post("/verify-email")
def verify_email(body: LinkToken, auth: Auth) -> UserReply:
    """`token` comes from the emailed link (?verify=...). Works signed in or not."""
    return UserReply(user=UserInfo.of(auth.verify_email(body.token)))


@router.post("/resend-verification", status_code=202)
def resend_verification(user: CurrentUser, auth: Auth) -> VerificationReply:
    return VerificationReply(demo_verify_url=auth.resend_verification(user))


@router.post("/forgot-password", status_code=202)
def forgot_password(body: EmailAddress, request: Request, auth: Auth, after: BackgroundTasks) -> ResetReply:
    """Same answer, in the same time, whether or not the account exists."""
    auth.forgot_password(body.email, _client(request), defer=after.add_task)
    return ResetReply()


@router.post("/reset-password")
def reset_password(body: PasswordReset, request: Request, response: Response, auth: Auth) -> UserReply:
    """`token` comes from the emailed link (?reset=...). Signs out every session, then signs in here."""
    signed_in = auth.reset_password(body.token, body.password, _client(request))
    _start_session(request, response, auth, signed_in)
    return UserReply(user=UserInfo.of(signed_in.user))


@router.post("/change-password")
def change_password(
    body: PasswordChange, request: Request, response: Response, user: CurrentUser, auth: Auth
) -> UserReply:
    """Signs out every session, then signs this browser in again with a new cookie."""
    signed_in = auth.change_password(user, body.current_password, body.new_password, _client(request))
    _start_session(request, response, auth, signed_in)
    return UserReply(user=UserInfo.of(signed_in.user))


@router.post("/delete-account", status_code=204)
def delete_account(body: AccountDeletion, request: Request, user: CurrentUser, auth: Auth) -> Response:
    """Deletes the account and everything kept for it (meals, preferences), then signs out."""
    auth.delete_account(user, body.password, getattr(request.app.state, "erase_user_data", None))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=auth.settings.cookie_secure, httponly=True, samesite="lax")
    return response


@router.get("/dev/outbox", include_in_schema=False)
def dev_outbox(auth: Auth) -> list[OutboxItem]:
    """The last 20 queued emails, for local testing; 404 unless MEALSCHOLAR_DEV_OUTBOX=1."""
    if not auth.settings.dev_outbox:
        raise AuthError(404, "Not Found")
    return [
        OutboxItem(
            to=e.to,
            subject=e.subject,
            text=e.text,
            purpose=e.purpose,
            status=e.status,
            provider=e.provider,
            created_at=e.created_at,
        )
        for e in auth.recent_emails(20)
    ]


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SameOriginWrites:
    """Refuses writes whose Origin header names another host than the request (CSRF defence).

    Requests without an Origin header pass. The public URL's host also counts as this site,
    in case a proxy rewrites the Host header.
    """

    def __init__(self, app: ASGIApp, public_host: str = "") -> None:
        self.app = app
        self.public_host = public_host.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"].upper() in UNSAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and not self._same_site(origin, headers.get("host", "")):
                response = JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _same_site(self, origin: str, host: str) -> bool:
        try:
            origin_host = urlsplit(origin.strip()).netloc.lower()
        except ValueError:  # a malformed Origin counts as another site
            return False
        return bool(origin_host) and origin_host in (host.lower(), self.public_host)


BODY_TOO_LARGE = "That request is too large."


class AuthRouteGuard:
    """For /api/auth: answers 413 to request bodies over MAX_AUTH_BODY bytes before they are
    read into memory, and marks every response Cache-Control: no-store."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(router.prefix + "/"):
            await self.app(scope, receive, send)
            return

        async def send_uncached(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        length = Headers(scope=scope).get("content-length", "")
        if length.isdigit() and int(length) > MAX_AUTH_BODY:
            await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(scope, receive, send_uncached)
            return

        received = 0

        async def receive_limited() -> Message:  # bodies sent without Content-Length
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_AUTH_BODY:
                raise HTTPException(413, BODY_TOO_LARGE)
            return message

        await self.app(scope, receive_limited, send_uncached)


class HideQueryValues(logging.Filter):
    """Hides query values in uvicorn's access log, which prints every request's path and query:
    the tokens of email links (/?verify=..., /?reset=...) and device coordinates
    (POST /api/meals?lat=...&lon=..., GET /api/dining/nearest?lat=...&lon=...), which must never
    be stored."""

    PARAMS = (*LINK_PARAM.values(), "lat", "lon")
    PATTERN = re.compile(rf"([?&](?:{'|'.join(PARAMS)})=)[^&#\s]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self.PATTERN.sub(r"\1[hidden]", arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


_hide_query_values = HideQueryValues()


def install_auth(app: FastAPI, service: AuthService) -> None:
    """Add the /api/auth routes and their request checks, the cross-site write check, the auth
    error format, and the access-log filter for link tokens and coordinates."""
    app.state.auth = service
    app.include_router(router)
    app.add_exception_handler(AuthError, _auth_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_middleware(SameOriginWrites, public_host=urlsplit(service.settings.public_url).netloc)
    app.add_middleware(AuthRouteGuard)
    logging.getLogger("uvicorn.access").addFilter(_hide_query_values)  # added once, however many apps


async def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
    content = {"detail": exc.detail}
    if exc.field:
        content["field"] = exc.field
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(content, status_code=exc.status, headers=headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Auth forms get a 400 naming the field; other routes keep FastAPI's 422."""
    if not request.url.path.startswith(router.prefix + "/"):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    first = errors[0] if errors else {}
    names = [part for part in first.get("loc", ()) if isinstance(part, str) and part != "body"]
    if not names:
        return await _auth_error(request, AuthError(400, "Send the form as a JSON object."))
    detail = f"Missing {names[0]}." if first.get("type") == "missing" else f"Invalid {names[0]}."
    return await _auth_error(request, AuthError(400, detail, field=names[0]))
