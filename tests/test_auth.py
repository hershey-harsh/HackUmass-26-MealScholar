"""Accounts over HTTP: sessions, email links, limits, cross-site checks and per-user diaries.

Everything runs on the in-memory account store. Set MEALSCHOLAR_TEST_MONGODB_URI to also run the
store tests against MongoDB; they use the throwaway database `mealscholar_test` and drop it.
"""

import base64
import hashlib
import io
import logging
import os
import re
import shutil
import smtplib
import sqlite3
import threading
import tracemalloc
import unicodedata
from contextlib import closing, suppress
from dataclasses import replace
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import mealscholar.auth
import mealscholar.web
from mealscholar import cli, mailer
from mealscholar.auth import AuthError, AuthService, Client, RateLimiter, Rule, hash_password, verify_password
from mealscholar.auth_store import (
    EmailTaken,
    EmailToken,
    MemoryAuthStore,
    MongoAuthStore,
    OutboxEmail,
    Session,
    utc_now,
)
from mealscholar.config import SmtpSettings, load_settings
from mealscholar.diary import Diary
from mealscholar.mailer import (
    Email,
    SmtpSender,
    html_email,
    make_sender,
    password_changed_email,
    password_reset_email,
    verification_email,
)
from mealscholar.schemas import FoodItem, MealAnalysis, MealContext, MealResult
from mealscholar.web import create_app

PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new password"
SIGN_IN = {"detail": "Sign in to continue."}
BAD_LOGIN = {"detail": "Email or password is incorrect."}
BAD_LINK = "That link is invalid or has expired."
JPEG = {"Content-Type": "image/jpeg"}
MONGO_TEST_DB = "mealscholar_test"


class Clock:
    """The account service's clock, moved by hand. Starts at the real time."""

    def __init__(self) -> None:
        self.now = utc_now()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FakeVision:
    """Sees rice and a cola and asks one question; remembers the context of every photo."""

    def __init__(self) -> None:
        self.contexts: list[str] = []
        self.answers = 0

    def analyze(self, jpeg: bytes, context: str, menu_names=()) -> MealAnalysis:
        self.contexts.append(context)
        return MealAnalysis(
            is_food=True,
            items=[
                food("white rice", "Rice, white, cooked", 150, 200, 260),
                food("cola", "Soft drink, cola", 330, 355, 400),
            ],
            scale_reference="dinner fork",
            question="Was the rice cooked with oil?",
        )

    def refine(self, analysis: MealAnalysis, question: str, answer: str, menu_names=()) -> MealAnalysis:
        self.answers += 1
        return analysis


def food(name: str, query: str, low: int, likely: int, high: int) -> FoodItem:
    return FoodItem(
        name=name,
        fdc_query=query,
        cooking_method="unknown",
        grams_low=low,
        grams_likely=likely,
        grams_high=high,
        box_2d=None,
        hidden_fat_likely=False,
    )


def photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "tan").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def home(tmp_path, food_db_path):
    shutil.copy(food_db_path, tmp_path / "fdc.sqlite")
    return tmp_path


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_app(home, clock):
    """App factory: environment variables as keyword arguments, in-memory accounts by default."""

    def make(*, store=None, vision=None, **env):
        settings = load_settings({"MEALSCHOLAR_HOME": str(home), **env})
        auth = AuthService.from_settings(settings, store=store or MemoryAuthStore(), clock=clock)
        return create_app(settings, vision=vision or FakeVision(), auth=auth)

    return make


def browser(app, headers=None, ip="testclient") -> TestClient:
    """A client with its own cookie jar, over HTTPS so that Secure cookies are sent back."""
    return TestClient(app, base_url="https://testserver", headers=headers, client=(ip, 50000))


def register(client, email="sam@example.com", password=PASSWORD, **fields):
    return client.post("/api/auth/register", json={"email": email, "password": password, **fields})


def login(client, email="sam@example.com", password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def emailed_token(app, param: str) -> str:
    """The token in the newest queued email's link (?verify=... or ?reset=...)."""
    text = app.state.auth.store.recent_emails(1)[0].text
    return re.search(rf"/\?{param}=([\w-]+)", text).group(1)


def session_cookie(response):
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie["ms_session"]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- sign up, sign in, sign out ------------------------------------------------------


def test_register_signs_in_and_logout_signs_out(make_app, clock):
    app = make_app()
    client = browser(app)

    res = register(client, email="  Sam@Example.COM ", name="  Sam   Lee ")
    assert res.status_code == 201
    assert set(res.json()) == {"user"}  # no demo link unless it is turned on
    user = res.json()["user"]
    assert user == {
        "id": user["id"],
        "email": "sam@example.com",
        "name": "Sam Lee",
        "email_verified": False,
        "created_at": user["created_at"],
    }
    assert datetime.fromisoformat(user["created_at"]) == clock.now
    assert client.get("/api/auth/me").json() == {"user": user}

    token = client.cookies["ms_session"]
    res = client.post("/api/auth/logout")
    assert res.status_code == 204
    assert session_cookie(res).value == "" and session_cookie(res)["max-age"] == "0"
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").json() == SIGN_IN
    # The server forgot the session too, so a copy of the old cookie doesn't work.
    copy = TestClient(app, base_url="https://testserver", cookies={"ms_session": token})
    assert copy.get("/api/auth/me").json() == SIGN_IN

    res = login(client, email=" SAM@example.com")
    assert res.status_code == 200
    assert res.json() == {"user": user}
    assert client.get("/api/auth/me").json() == {"user": user}
    assert client.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/logout").status_code == 204  # also fine when signed out


@pytest.mark.parametrize(("env", "secure"), [({}, True), ({"MEALSCHOLAR_COOKIE_SECURE": "0"}, False)])
def test_session_cookie_flags_and_only_its_hash_is_stored(make_app, clock, env, secure):
    app = make_app(**env)
    res = register(browser(app))

    cookie = session_cookie(res)
    assert len(cookie.value) >= 43
    assert cookie["httponly"] is True
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == "/"
    assert cookie["max-age"] == str(30 * 24 * 60 * 60)
    assert bool(cookie["secure"]) is secure

    store = app.state.auth.store
    assert store.session(cookie.value, clock.now) is None
    assert store.session(sha256(cookie.value), clock.now).user_id == res.json()["user"]["id"]


def test_a_forged_or_expired_session_is_signed_out(make_app, clock):
    app = make_app()
    client = browser(app)
    register(client)
    forged = TestClient(app, base_url="https://testserver", cookies={"ms_session": "x" * 43})
    assert forged.get("/api/auth/me").json() == SIGN_IN
    assert forged.get("/api/days/2026-11-14").json() == SIGN_IN

    clock.advance(days=29)
    assert client.get("/api/auth/me").status_code == 200
    clock.advance(days=1, seconds=1)
    assert client.get("/api/auth/me").json() == SIGN_IN


def test_duplicate_email_is_a_conflict(make_app):
    app = make_app()
    assert register(browser(app)).status_code == 201
    res = register(browser(app), email="SAM@example.com ", password="some other password")
    assert res.status_code == 409
    assert res.json()["detail"] == "An account with that email already exists."
    assert "set-cookie" not in res.headers


def test_wrong_password_and_unknown_email_get_the_same_answer(make_app):
    client = browser(make_app())
    register(client)
    for res in (
        login(client, password="not the password"),
        login(client, email="nobody@example.com"),
        login(client, email="not an email"),
        login(client, password="x" * 1000),
    ):
        assert res.status_code == 401
        assert res.json() == BAD_LOGIN
        assert "set-cookie" not in res.headers
    assert client.get("/api/auth/me").status_code == 200  # a failed sign-in keeps the current session


@pytest.mark.parametrize(
    ("body", "field", "detail"),
    [
        ({"email": "sam@example"}, "email", "Enter a valid email address."),
        ({"email": "sam @example.com"}, "email", "Enter a valid email address."),
        ({"email": "a" * 250 + "@x.io"}, "email", "Enter a valid email address."),
        ({"password": "short"}, "password", "Use at least 8 characters for your password."),
        ({"password": "x" * 129}, "password", "Use at most 128 characters for your password."),
        ({"password": " Sam@Example.com"}, "password", "Your password can't be your email address."),
        ({"name": "n" * 81}, "name", "Keep your name to 80 characters or fewer."),
        ({"name": "Sam\x00"}, "name", "Your name has characters that can't be used."),
        ({"password": None}, "password", "Missing password."),
        ({"email": 7}, "email", "Invalid email."),
        # Control and invisible characters: NUL, a terminal escape, DEL, zero-width space, bidi override
        ({"email": "sam\x00@example.com"}, "email", "Enter a valid email address."),
        ({"email": "sam\x1b[31m@example.com"}, "email", "Enter a valid email address."),
        ({"email": "sam\x7f@example.com"}, "email", "Enter a valid email address."),
        ({"email": "sam\u200b@example.com"}, "email", "Enter a valid email address."),
        ({"email": "sam\u202e@example.com"}, "email", "Enter a valid email address."),
        # Refused on their raw length (U+FDFA is 18 characters after NFKC)
        ({"password": "\ufdfa" * 1000}, "password", "Use at most 128 characters for your password."),
        ({"name": "ab " * 1000}, "name", "Keep your name to 80 characters or fewer."),
    ],
)
def test_registration_says_which_field_is_wrong(make_app, body, field, detail):
    form = {"email": "sam@example.com", "password": PASSWORD, **body}
    form = {key: value for key, value in form.items() if value is not None}  # None: leave the field out
    res = browser(make_app()).post("/api/auth/register", json=form)
    assert res.status_code == 400
    assert res.json() == {"detail": detail, "field": field}
    assert "set-cookie" not in res.headers


def test_one_address_in_any_unicode_form_is_one_account(make_app):
    app = make_app()
    composed = "jos\u00e9@example.com"  # é as one character
    decomposed = unicodedata.normalize("NFD", composed)  # e, then a combining accent
    assert register(browser(app), email=decomposed).json()["user"]["email"] == composed
    assert register(browser(app), email=composed).status_code == 409
    assert login(browser(app), email=decomposed).status_code == 200
    browser(app).post("/api/auth/forgot-password", json={"email": decomposed})
    assert app.state.auth.store.recent_emails(1)[0].purpose == "reset_password"

    lone_surrogate = b'{"email": "sam\\ud800@example.com", "password": "correct horse battery"}'
    res = browser(app).post("/api/auth/register", content=lone_surrogate, headers={"Content-Type": "application/json"})
    assert res.json() == {"detail": "Enter a valid email address.", "field": "email"}


def peak_memory(call) -> int:
    """Bytes allocated at the high point of `call`; an AuthError counts as an answer."""
    tracemalloc.start()
    try:
        with suppress(AuthError):
            call()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_huge_fields_are_refused_before_they_are_normalised(make_app):
    """Normalising or splitting megabytes of text takes many times its size in memory (NFKC turns
    U+FDFA into 18 characters), so text far over its limit is refused on its raw length."""
    auth = make_app().state.auth
    client = Client("192.0.2.1")
    user = auth.register("sam@example.com", PASSWORD, None, client).user
    password, name, email = "\ufdfa" * 1_000_000, "ab " * 1_000_000, "a" * 1_000_000 + "@example.com"
    for call in (
        lambda: auth.register("alex@example.com", password, None, client),
        lambda: auth.register("alex@example.com", PASSWORD, name, client),
        lambda: auth.register(email, PASSWORD, None, client),
        lambda: auth.login("sam@example.com", password, client),
        lambda: auth.forgot_password(email, client),
        lambda: auth.change_password(user, PASSWORD, password, client),
        lambda: auth.change_password(user, password, NEW_PASSWORD, client),
    ):
        assert peak_memory(call) < 2**20  # for text of 1 to 3 MB


def test_auth_bodies_are_capped_and_answers_are_not_cached(make_app):
    client = browser(make_app())
    me, logout = "/api/auth/me", "/api/auth/logout"
    answers = [register(client), client.get(me), client.post(logout), client.get(me)]
    assert [res.status_code for res in answers] == [201, 200, 204, 401]
    assert all(res.headers["cache-control"] == "no-store" for res in answers)
    assert "cache-control" not in client.get("/api/status").headers

    too_large = {"detail": "That request is too large."}
    res = client.post("/api/auth/login", json={"email": "sam@example.com", "password": "x" * 20_000})
    assert res.status_code == 413 and res.json() == too_large
    chunks = iter([b'{"email": "sam@example.com", "password": "', b"x" * 20_000, b'"}'])  # no Content-Length
    res = client.post("/api/auth/login", content=chunks, headers={"Content-Type": "application/json"})
    assert res.status_code == 413 and res.json() == too_large
    assert res.headers["cache-control"] == "no-store"


def test_password_hash_format():
    encoded = hash_password("pässwörd ünicode")
    scheme, n, r, p, salt, key = encoded.split("$")
    assert (scheme, n, r, p) == ("scrypt", "131072", "8", "1")
    assert len(base64.b64decode(salt)) == 16 and len(base64.b64decode(key)) == 32
    assert verify_password("pässwörd ünicode", encoded)
    assert verify_password(unicodedata.normalize("NFD", "pässwörd ünicode"), encoded)  # same text, other encoding
    assert not verify_password("passwort unicode", encoded)
    assert hash_password("same") != hash_password("same")  # fresh salt every time
    for broken in ("", "scrypt$1$2$3", encoded.replace("scrypt$", "bcrypt$"), encoded.replace("$8$", "$x$")):
        assert not verify_password("anything", broken)


def test_passwords_hashed_before_the_work_factor_upgrade_still_verify():
    salt = b"legacy-salt-1234"
    key = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=64 * 2**20)
    encoded = f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong password", encoded)


# --- rate limits ---------------------------------------------------------------------


def test_login_is_limited_per_ip_and_email(make_app):
    app = make_app()
    client = browser(app)
    register(client)
    for _ in range(10):
        assert login(client, password="wrong password").status_code == 401
    res = login(client)  # even the right password has to wait
    assert res.status_code == 429
    assert 0 < int(res.headers["Retry-After"]) <= 15 * 60
    assert res.json() == {"detail": "Too many attempts. Try again in 15 minutes."}
    # Other emails from this IP, and this email from other IPs, are not held up.
    assert login(client, email="other@example.com").json() == BAD_LOGIN
    assert login(browser(app, {"CF-Connecting-IP": "203.0.113.9"}, ip="127.0.0.1")).status_code == 200  # via tunnel
    assert login(browser(app, ip="198.51.100.4")).status_code == 200


def test_register_is_limited_per_ip(make_app):
    app = make_app()
    allowed = mealscholar.auth.REGISTER_PER_IP.limit
    assert allowed >= 20  # people on one campus Wi-Fi share an address
    for i in range(allowed):
        assert register(browser(app), email=f"user{i}@example.com").status_code == 201
    res = register(browser(app), email="one-too-many@example.com")
    assert res.status_code == 429 and int(res.headers["Retry-After"]) > 0
    tunnel = browser(app, {"CF-Connecting-IP": "203.0.113.9"}, ip="127.0.0.1")
    assert register(tunnel, email="one-too-many@example.com").status_code == 201


def test_forgot_password_is_limited_per_email_and_ip(make_app):
    client = browser(make_app())

    def forgot(email):
        return client.post("/api/auth/forgot-password", json={"email": email}).status_code

    assert [forgot("sam@example.com") for _ in range(4)] == [202, 202, 202, 429]  # 3 per email
    assert [forgot(f"user{i}@example.com") for i in range(3)] == [202, 202, 429]  # 5 per IP; refusals don't count


def test_rate_limiter_slides_and_counts_nothing_it_refuses():
    now = [1000.0]
    limiter = RateLimiter(clock=lambda: now[0])
    per_ip, per_email = Rule("ip", 3, 60), Rule("email", 2, 60)

    def attempt(email):
        return limiter.hit((per_ip, "192.0.2.1"), (per_email, email))

    assert attempt("a") is None and attempt("a") is None
    now[0] = 1010.0
    assert attempt("a") == 50  # the first attempt leaves the window at 1060
    assert attempt("b") is None  # the refused attempt was not counted against the IP
    assert attempt("c") == 50
    now[0] = 1060.0
    assert attempt("c") is None


def test_photos_and_answers_share_an_account_budget_across_networks(make_app, clock, monkeypatch):
    monkeypatch.setattr(mealscholar.web, "ANALYSIS_PER_USER", Rule("analysis-user", 3, 600))
    vision = FakeVision()
    app = make_app(vision=vision)
    app.state.auth.limiter = RateLimiter(clock=lambda: clock.now.timestamp())
    sam = browser(app, ip="198.51.100.1")
    register(sam)
    meals = [sam.post("/api/meals", content=photo(), headers=JPEG).json() for _ in range(2)]
    answered = sam.post(f"/api/meals/{meals[0]['id']}/answer", json={"answer": "yes"})
    assert answered.status_code == 200

    # A new network or cookie jar cannot reset this account's allowance.
    elsewhere = browser(app, ip="203.0.113.1")
    elsewhere.cookies.update(sam.cookies)
    limited = elsewhere.post("/api/meals", content=b"not an image")
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) == 600
    limited_answer = elsewhere.post(f"/api/meals/{meals[1]['id']}/answer", json={"answer": "yes"})
    assert limited_answer.status_code == 429
    assert (len(vision.contexts), vision.answers) == (2, 1)  # neither refusal called the model

    lee = browser(app, ip="198.51.100.1")
    register(lee, email="lee@example.com")
    assert lee.post("/api/meals", content=photo(), headers=JPEG).status_code == 200
    clock.advance(seconds=600)
    assert elsewhere.post(f"/api/meals/{meals[1]['id']}/answer", json={"answer": "yes"}).status_code == 200
    assert (len(vision.contexts), vision.answers) == (3, 2)


@pytest.mark.parametrize("required", ["0", "1"])
def test_network_budget_covers_accounts_guests_and_answers(make_app, monkeypatch, required):
    monkeypatch.setattr(mealscholar.web, "ANALYSIS_PER_IP", Rule("analysis-ip", 2, 600))
    vision = FakeVision()
    app = make_app(vision=vision, MEALSCHOLAR_AUTH_REQUIRED=required)
    first = browser(app, ip="198.51.100.1")
    second = browser(app, ip="198.51.100.1")
    outside = browser(app, ip="203.0.113.1")
    if required == "1":
        for i, client in enumerate((first, second, outside)):
            assert register(client, email=f"user{i}@example.com").status_code == 201
    meal = first.post("/api/meals", content=photo(), headers=JPEG).json()
    assert second.post("/api/meals", content=photo(), headers=JPEG).status_code == 200
    limited = first.post(f"/api/meals/{meal['id']}/answer", json={"answer": "yes"})
    assert limited.status_code == 429 and 0 < int(limited.headers["Retry-After"]) <= 600
    assert first.post("/api/meals", content=photo(), headers=JPEG).status_code == 429
    assert (len(vision.contexts), vision.answers) == (2, 0)
    assert outside.post("/api/meals", content=photo(), headers=JPEG).status_code == 200


@pytest.mark.parametrize(
    ("first_ip", "second_ip", "limited"),
    [
        ("127.0.0.1", "127.0.0.1", False),  # the trusted tunnel forwards distinct visitors
        ("192.168.1.50", "192.168.1.50", True),  # a LAN caller cannot spoof that header
        ("2001:db8::1", "2001:db8::2", True),
        ("2001:db8::1", "2001:db8:0:1::1", False),
        ("::ffff:198.51.100.1", "198.51.100.1", True),
    ],
)
def test_analysis_uses_the_existing_trusted_network_identity(make_app, monkeypatch, first_ip, second_ip, limited):
    monkeypatch.setattr(mealscholar.web, "ANALYSIS_PER_IP", Rule("analysis-ip", 1, 600))
    vision = FakeVision()
    app = make_app(vision=vision, MEALSCHOLAR_AUTH_REQUIRED="0")
    first = browser(app, {"CF-Connecting-IP": "203.0.113.1"}, ip=first_ip)
    second = browser(app, {"CF-Connecting-IP": "203.0.113.2"}, ip=second_ip)
    assert first.post("/api/meals", content=photo(), headers=JPEG).status_code == 200
    result = second.post("/api/meals", content=photo(), headers=JPEG)
    assert result.status_code == (429 if limited else 200)
    assert len(vision.contexts) == (1 if limited else 2)
    assert first.post("/api/meals", content=photo(), headers=JPEG).status_code == 429


def test_an_ipv6_slash_64_counts_as_one_client(make_app, clock):
    """One machine can pick any address in its /64. Sessions still record the full address."""
    app = make_app()
    register(browser(app, ip="2001:db8:1::1"))
    for i in range(10):
        assert login(browser(app, ip=f"2001:db8::{i + 1:x}"), password="wrong password").status_code == 401
    assert login(browser(app, ip="2001:db8::ffff:abcd")).status_code == 429  # a new address, the same /64
    res = login(browser(app, ip="2001:db8:0:1::7"))  # the next /64 is someone else
    assert res.status_code == 200
    assert app.state.auth.store.session(sha256(session_cookie(res).value), clock.now).ip == "2001:db8:0:1::7"

    for _ in range(10):
        login(browser(app, ip="198.51.100.4"), password="wrong password")
    assert login(browser(app, ip="::ffff:198.51.100.4")).status_code == 429  # the same IPv4 client


def test_cf_connecting_ip_counts_only_from_the_local_tunnel(make_app, clock):
    """cloudflared connects from this machine; on the local network anyone can send the header."""
    app = make_app()
    register(browser(app))
    for i in range(10):
        lan = browser(app, {"CF-Connecting-IP": f"203.0.113.{i}"}, ip="192.168.1.50")
        assert login(lan, password="wrong password").status_code == 401
    assert login(browser(app, {"CF-Connecting-IP": "203.0.113.99"}, ip="192.168.1.50")).status_code == 429
    for peer in ("127.0.0.1", "::1"):
        res = login(browser(app, {"CF-Connecting-IP": "203.0.113.99"}, ip=peer))
        assert res.status_code == 200
        assert app.state.auth.store.session(sha256(session_cookie(res).value), clock.now).ip == "203.0.113.99"


# --- email links ---------------------------------------------------------------------


def test_email_verification_link(make_app, clock):
    app = make_app(MEALSCHOLAR_PUBLIC_URL="https://meals.example/")
    client = browser(app)
    register(client)

    (email,) = app.state.auth.store.recent_emails(20)
    assert (email.to, email.subject) == ("sam@example.com", "Confirm your MealScholar email")
    assert email.purpose == "verify_email"
    assert (email.status, email.provider) == ("queued", "outbox")
    assert email.expires_at - email.created_at == timedelta(days=7)
    assert "If you didn't sign up, you can ignore this." in email.text
    token = re.search(r"https://meals\.example/\?verify=([\w-]+)", email.text).group(1)
    assert app.state.auth.store.email_token(token, "verify_email", clock.now) is None  # stored as a hash
    assert app.state.auth.store.email_token(sha256(token), "verify_email", clock.now) is not None

    stranger = browser(app)  # the link works on a device that isn't signed in
    res = stranger.post("/api/auth/verify-email", json={"token": token})
    assert res.status_code == 200
    assert res.json()["user"]["email_verified"] is True
    assert "set-cookie" not in res.headers
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is True

    again = stranger.post("/api/auth/verify-email", json={"token": token})
    assert again.status_code == 400 and again.json()["detail"] == BAD_LINK
    for bad in ("", "nope", "x" * 500, "é" * 43):
        assert stranger.post("/api/auth/verify-email", json={"token": bad}).json()["detail"] == BAD_LINK
    res = client.post("/api/auth/resend-verification")
    assert res.status_code == 409 and res.json()["detail"] == "Your email is already confirmed."


def test_verification_link_expires_after_a_day(make_app, clock):
    app = make_app()
    client = browser(app)
    register(client)
    token = emailed_token(app, "verify")
    clock.advance(hours=24, seconds=1)
    res = client.post("/api/auth/verify-email", json={"token": token})
    assert res.status_code == 400 and res.json()["detail"] == BAD_LINK
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is False


def test_resend_verification(make_app):
    app = make_app()
    client = browser(app)
    register(client)
    first = emailed_token(app, "verify")
    for _ in range(3):
        res = client.post("/api/auth/resend-verification")
        assert res.status_code == 202 and res.json() == {}
    res = client.post("/api/auth/resend-verification")
    assert res.status_code == 429 and int(res.headers["Retry-After"]) > 0
    assert len(app.state.auth.store.recent_emails(20)) == 4

    newest = emailed_token(app, "verify")
    assert newest != first
    assert client.post("/api/auth/verify-email", json={"token": newest}).json()["user"]["email_verified"] is True
    assert browser(app).post("/api/auth/resend-verification").json() == SIGN_IN


def test_password_reset_signs_out_everywhere_and_signs_in(make_app):
    app = make_app()
    laptop, phone, other = browser(app), browser(app), browser(app)
    register(laptop)
    login(phone)

    res = other.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert res.status_code == 202 and res.json() == {}
    assert len(app.state.auth.store.recent_emails(20)) == 1  # nothing sent for an unknown email

    res = other.post("/api/auth/forgot-password", json={"email": " Sam@Example.com"})
    assert res.status_code == 202 and res.json() == {}
    email = app.state.auth.store.recent_emails(1)[0]
    assert (email.subject, email.purpose) == ("Reset your MealScholar password", "reset_password")
    assert "1 hour" in email.text
    token = emailed_token(app, "reset")

    weak = other.post("/api/auth/reset-password", json={"token": token, "password": "short"})
    assert weak.status_code == 400 and weak.json()["field"] == "password"
    res = other.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})  # link still good
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "sam@example.com"
    assert res.json()["user"]["email_verified"] is True  # the link proved they read the inbox
    assert session_cookie(res).value

    assert other.get("/api/auth/me").status_code == 200
    assert laptop.get("/api/auth/me").json() == SIGN_IN
    assert phone.get("/api/auth/me").json() == SIGN_IN
    assert login(browser(app)).json() == BAD_LOGIN
    assert login(browser(app), password=NEW_PASSWORD).status_code == 200

    reused = other.post("/api/auth/reset-password", json={"token": token, "password": "yet another password"})
    assert reused.status_code == 400 and reused.json()["detail"] == BAD_LINK


def test_forgot_password_answers_202_to_any_text(make_app):
    app = make_app()
    client = browser(app)
    for text in ("not-an-email", "", "sam\x00@example.com", "x" * 5000):
        res = client.post("/api/auth/forgot-password", json={"email": text})
        assert res.status_code == 202 and res.json() == {}
    assert app.state.auth.store.recent_emails(20) == []
    answers = [client.post("/api/auth/forgot-password", json={"email": f"user{i}@example.com"}) for i in range(6)]
    assert [res.status_code for res in answers] == [202] * 5 + [429]  # the text above used none of the 5 an hour


def test_reset_link_lasts_an_hour_and_a_reset_cancels_older_links(make_app, clock):
    app = make_app()
    client = browser(app)
    register(client)

    def reset_link():
        client.post("/api/auth/forgot-password", json={"email": "sam@example.com"})
        return emailed_token(app, "reset")

    expired = reset_link()
    clock.advance(hours=1, seconds=1)
    res = client.post("/api/auth/reset-password", json={"token": expired, "password": NEW_PASSWORD})
    assert res.status_code == 400 and res.json()["detail"] == BAD_LINK

    older, newer = reset_link(), reset_link()
    assert client.post("/api/auth/reset-password", json={"token": newer, "password": NEW_PASSWORD}).status_code == 200
    res = client.post("/api/auth/reset-password", json={"token": older, "password": "yet another password"})
    assert res.status_code == 400 and res.json()["detail"] == BAD_LINK


def test_change_password(make_app):
    app = make_app()
    laptop, phone = browser(app), browser(app)
    register(laptop)
    login(phone)

    def change(current, new, client=laptop):
        return client.post("/api/auth/change-password", json={"current_password": current, "new_password": new})

    wrong = change("not my password", NEW_PASSWORD)
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "Your current password is incorrect.", "field": "current_password"}
    weak = change(PASSWORD, "short")
    assert weak.status_code == 400 and weak.json()["field"] == "new_password"
    ok = change(PASSWORD, NEW_PASSWORD)
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "sam@example.com"

    assert laptop.get("/api/auth/me").status_code == 200  # signed in again, with a new cookie
    assert phone.get("/api/auth/me").json() == SIGN_IN  # the others are signed out
    assert login(browser(app)).json() == BAD_LOGIN
    assert login(browser(app), password=NEW_PASSWORD).status_code == 200
    assert change(NEW_PASSWORD, "whatever it is", client=browser(app)).json() == SIGN_IN


def test_change_password_cancels_reset_links_and_replaces_this_session(make_app):
    """Whoever holds a reset link or a copy of the cookie loses it when the password changes."""
    app = make_app()
    laptop = browser(app)
    register(laptop)
    laptop.post("/api/auth/forgot-password", json={"email": "sam@example.com"})
    link = emailed_token(app, "reset")
    old = laptop.cookies["ms_session"]

    res = laptop.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert res.status_code == 200
    assert session_cookie(res).value not in ("", old)
    assert laptop.get("/api/auth/me").status_code == 200
    copy = TestClient(app, base_url="https://testserver", cookies={"ms_session": old})
    assert copy.get("/api/auth/me").json() == SIGN_IN
    res = browser(app).post("/api/auth/reset-password", json={"token": link, "password": "the attacker's choice"})
    assert res.status_code == 400 and res.json()["detail"] == BAD_LINK


def test_access_log_hides_email_link_tokens(make_app, caplog):
    """Opening an emailed link is GET /?verify=<token>, and uvicorn logs paths with their query."""
    make_app()
    access = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        for path in ("/?verify=SECRET-1", "/?reset=SECRET_2&next=1", "/index.html?a=1&verify=SECRET3"):
            access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.9:50000", "GET", path, "1.1", 200)
    assert "SECRET" not in caplog.text
    assert '"GET /?reset=[hidden]&next=1 HTTP/1.1" 200' in caplog.text


def test_access_log_hides_coordinates(make_app, caplog):
    """The app sends the device location as ?lat=&lon= on POST /api/meals and GET /api/dining/nearest."""
    make_app()
    access = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        for method, path in (
            ("POST", "/api/meals?lat=42.389123&lon=-72.528456"),
            ("GET", "/api/dining/nearest?lon=-72.528456&lat=42.389123"),
        ):
            access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.9:50000", method, path, "1.1", 200)
    assert "42.38" not in caplog.text and "72.52" not in caplog.text
    assert '"POST /api/meals?lat=[hidden]&lon=[hidden] HTTP/1.1" 200' in caplog.text


def test_the_dummy_hash_is_ready_before_the_first_sign_in(make_app, monkeypatch):
    """Made at startup, so the first sign-in with an unknown email after a restart isn't the slow one."""
    mealscholar.auth._dummy_hash.cache_clear()
    app = make_app()

    def no_hashing(password):
        raise AssertionError("a password was hashed during sign-in")

    monkeypatch.setattr(mealscholar.auth, "hash_password", no_hashing)
    assert login(browser(app), email="nobody@example.com").json() == BAD_LOGIN


def test_dev_outbox_only_when_enabled(make_app):
    client = browser(make_app())
    register(client)
    assert client.get("/api/auth/dev/outbox").status_code == 404

    app = make_app(MEALSCHOLAR_DEV_OUTBOX="1")
    client = browser(app)
    register(client)
    client.post("/api/auth/forgot-password", json={"email": "sam@example.com"})
    emails = client.get("/api/auth/dev/outbox").json()
    assert [e["subject"] for e in emails] == ["Reset your MealScholar password", "Confirm your MealScholar email"]
    assert set(emails[0]) == {"to", "subject", "text", "purpose", "status", "provider", "created_at"}
    for _ in range(25):
        app.state.auth.sender.send(verification_email("sam@example.com", "https://meals.example/?verify=x"))
    assert len(client.get("/api/auth/dev/outbox").json()) == 20


def test_demo_links_only_when_enabled(make_app):
    client = browser(make_app())
    assert "demo_verify_url" not in register(client).json()
    assert client.post("/api/auth/resend-verification").json() == {}
    assert client.post("/api/auth/forgot-password", json={"email": "sam@example.com"}).json() == {}

    app = make_app(MEALSCHOLAR_DEMO_VERIFY_LINK="1", MEALSCHOLAR_PUBLIC_URL="https://meals.example")
    client = browser(app)
    url = register(client).json()["demo_verify_url"]
    assert url.startswith("https://meals.example/?verify=")
    assert url.split("verify=")[1] == emailed_token(app, "verify")
    assert (
        client.post("/api/auth/resend-verification")
        .json()["demo_verify_url"]
        .startswith("https://meals.example/?verify=")
    )
    res = client.post("/api/auth/verify-email", json={"token": url.split("verify=")[1]})
    assert res.json()["user"]["email_verified"] is True

    # Reset links never come back in a response, even in demo mode: anyone can ask for one.
    assert client.post("/api/auth/forgot-password", json={"email": "sam@example.com"}).json() == {}
    assert emailed_token(app, "reset")  # it still went to the outbox
    assert client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"}).json() == {}


class FakeSMTP:
    """Stands in for smtplib.SMTP: records the conversation and the messages sent."""

    calls: list[str] = []
    messages: list = []
    connections: list[tuple[str, int, float]] = []
    on_send: object = None  # an exception to raise, or a threading.Event to wait for, in send_message

    def __init__(self, host, port, timeout=None):
        FakeSMTP.connections.append((host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeSMTP.calls.append("quit")
        return False

    def ehlo(self):
        FakeSMTP.calls.append("ehlo")

    def starttls(self, context=None):
        assert context is not None
        FakeSMTP.calls.append("starttls")

    def login(self, user, password):
        FakeSMTP.calls.append(f"login {user}:{password}")

    def send_message(self, msg):
        if isinstance(FakeSMTP.on_send, BaseException):
            raise FakeSMTP.on_send
        if isinstance(FakeSMTP.on_send, threading.Event):
            assert FakeSMTP.on_send.wait(30), "the test never released the relay"
        FakeSMTP.calls.append("send_message")
        FakeSMTP.messages.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.calls, FakeSMTP.messages, FakeSMTP.connections, FakeSMTP.on_send = [], [], [], None
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    yield FakeSMTP
    if isinstance(FakeSMTP.on_send, threading.Event):
        FakeSMTP.on_send.set()  # never leave a worker blocked after a failed assertion


SMTP_ENV = {
    "MEALSCHOLAR_EMAIL_PROVIDER": "smtp",
    "MEALSCHOLAR_SMTP_HOST": "smtp.example",
    "MEALSCHOLAR_SMTP_USER": "u",
    "MEALSCHOLAR_SMTP_PASSWORD": "p",
    "MEALSCHOLAR_EMAIL_FROM": "MealScholar <no-reply@example.com>",
}


def test_smtp_sender_talks_starttls_and_sends(make_app, fake_smtp):
    app = make_app(**SMTP_ENV)
    client = browser(app)
    assert register(client).status_code == 201
    sender = app.state.auth.sender
    assert isinstance(sender, SmtpSender)
    sender.drain(5)

    assert fake_smtp.connections == [("smtp.example", 587, 10.0)]
    assert [c.split(" ")[0] for c in fake_smtp.calls] == ["ehlo", "starttls", "ehlo", "login", "send_message", "quit"]
    assert "login u:p" in fake_smtp.calls
    (msg,) = fake_smtp.messages
    assert msg["From"] == "MealScholar <no-reply@example.com>"
    assert msg["To"] == "sam@example.com"
    assert msg["Subject"] == "Confirm your MealScholar email"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert parsedate_to_datetime(msg["Date"]).tzinfo is not None
    assert re.fullmatch(r"<[^@<>\s]+@example\.com>", msg["Message-ID"])
    assert msg.get_content_type() == "multipart/alternative"
    plain, html = msg.get_body(("plain",)), msg.get_body(("html",))
    link = re.search(r"https?://\S+/\?verify=[\w-]+", plain.get_content()).group(0)
    assert f'href="{link}"' in html.get_content() and "Confirm email" in html.get_content()
    assert app.state.auth.store.recent_emails(20) == []  # raw links stay out of the database
    status = client.get("/api/status").json()
    assert (status["email_provider"], status["email_sending"]) == ("smtp", True)


def test_smtp_from_gets_a_display_name_and_a_message_id_on_its_domain(fake_smtp):
    sender = SmtpSender(SmtpSettings(host="smtp.example", sender="no-reply@mealscholar.health"), background=False)
    sender.send(verification_email("sam@example.com", "https://app.example/?verify=abc"))
    (msg,) = fake_smtp.messages
    assert msg["From"] == "MealScholar <no-reply@mealscholar.health>"
    assert msg["Message-ID"].endswith("@mealscholar.health>")


@pytest.mark.parametrize(
    "email",
    [
        Email("sam@example.com\r\nBcc: x@example.com", "Hi", "text", "verify_email"),
        Email("sam@example.com", "Hi\nBcc: x@example.com", "text", "verify_email"),
        Email("sam@example.com", "Hi\r", "text", "verify_email"),
    ],
)
@pytest.mark.parametrize("background", [True, False])
def test_smtp_refuses_line_breaks_in_headers(fake_smtp, email, background):
    sender = SmtpSender(SmtpSettings(host="smtp.example", sender="x@example.com"), background=background)
    with pytest.raises(ValueError, match="line break"):
        sender.send(email)
    sender.drain(5)
    assert fake_smtp.connections == []


def test_account_emails_have_an_escaped_html_part_with_the_link():
    link = 'https://app.example/?reset=a"b<c>&d'
    for email in (
        verification_email("sam@example.com", link),
        password_reset_email("sam@example.com", link),
    ):
        assert link in email.text
        assert email.html is not None and link not in email.html  # only in escaped form
        assert 'href="https://app.example/?reset=a&quot;b&lt;c&gt;&amp;d"' in email.html
        assert ">MealScholar</p>" in email.html
        assert "You got this because" in email.html
        assert not re.search(r"<img|src=|https?://(?!app\.example)", email.html)  # nothing remote, no pixels
    changed = password_changed_email("sam@example.com", "https://app.example")
    assert changed.purpose == "password_changed"
    assert "https://app.example/" in changed.text and "Forgot password" in changed.text
    assert 'href="https://app.example/"' in changed.html
    page = html_email("<b>hi</b>", footer="a & b", details=[("<day>", ["<script>x</script>"])])
    assert "<b>hi" not in page and "&lt;b&gt;hi" in page and "a &amp; b" in page
    assert "<script>" not in page and "&lt;day&gt;" in page


def test_password_change_and_reset_send_a_notice(make_app):
    app = make_app()
    client = browser(app)
    register(client)
    store = app.state.auth.store

    # Not to an unconfirmed address: whoever signed up with it may not own it, and repeated
    # password changes would otherwise send it one email each.
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert client.post("/api/auth/change-password", json=change).status_code == 200
    assert [e.purpose for e in store.recent_emails(20)] == ["verify_email"]
    assert client.post("/api/auth/verify-email", json={"token": emailed_token(app, "verify")}).status_code == 200

    change = {"current_password": NEW_PASSWORD, "new_password": PASSWORD}
    assert client.post("/api/auth/change-password", json=change).status_code == 200
    notice = store.recent_emails(1)[0]
    assert (notice.to, notice.subject, notice.purpose) == (
        "sam@example.com",
        "Your MealScholar password was changed",
        "password_changed",
    )
    assert "If it wasn't, reset your password" in notice.text

    wrong = {"current_password": "not my password", "new_password": NEW_PASSWORD}
    assert client.post("/api/auth/change-password", json=wrong).status_code == 401
    assert store.recent_emails(1)[0] == notice  # nothing changed, nothing sent

    client.post("/api/auth/forgot-password", json={"email": "sam@example.com"})
    token = emailed_token(app, "reset")
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "short"}).status_code == 400
    assert store.recent_emails(1)[0].purpose == "reset_password"
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
    assert [e.purpose for e in store.recent_emails(20)] == [
        "password_changed",
        "reset_password",
        "password_changed",
        "verify_email",
    ]


def test_email_sending_only_with_complete_smtp_settings(make_app):
    assert load_settings({}).email_sending is False
    assert load_settings({"MEALSCHOLAR_SMTP_HOST": "smtp.example"}).email_sending is False  # provider still outbox
    assert load_settings(SMTP_ENV).email_sending is True
    assert load_settings({**SMTP_ENV, "MEALSCHOLAR_SMTP_PASSWORD": ""}).email_sending is False  # login would fail
    assert load_settings({**SMTP_ENV, "MEALSCHOLAR_SMTP_USER": "", "MEALSCHOLAR_SMTP_PASSWORD": ""}).email_sending
    assert browser(make_app()).get("/api/status").json()["email_sending"] is False


def test_smtp_sending_does_not_delay_sign_up_or_forgot_password(make_app, fake_smtp):
    # A relay that hangs until the test releases it: every request must answer while it is still
    # stuck, which a wall-clock bound could not show reliably on a busy CI runner.
    relay = fake_smtp.on_send = threading.Event()
    app = make_app(**SMTP_ENV)
    client = browser(app)
    assert register(client).status_code == 201
    for email in ("sam@example.com", "nobody@example.com"):
        assert client.post("/api/auth/forgot-password", json={"email": email}).status_code == 202, email
    assert fake_smtp.messages == []  # all three answered before the relay accepted anything
    assert "send_message" not in fake_smtp.calls
    assert len(app.state.auth.sender._pending) == 2  # the verify and reset emails are queued; nothing for nobody@

    relay.set()
    app.state.auth.sender.drain(10)
    # Two workers deliver at once when the relay lets go, so either email can land first.
    assert sorted((m["To"], m["Subject"]) for m in fake_smtp.messages) == [
        ("sam@example.com", "Confirm your MealScholar email"),
        ("sam@example.com", "Reset your MealScholar password"),
    ]
    assert app.state.auth.sender._pending == set()


@pytest.mark.parametrize(
    ("error", "logged"),
    [
        (smtplib.SMTPServerDisconnected("Connection unexpectedly closed"), "SMTPServerDisconnected"),
        (TimeoutError(), "TimeoutError"),
        # A bounce: the exception text carries the raw address, which must not reach the log.
        (
            smtplib.SMTPRecipientsRefused({"sam@example.com": (550, b"5.1.1 user unknown")}),
            "SMTPRecipientsRefused (550)",
        ),
        # A Gmail-style refusal echoes the login user in the reply text.
        (
            smtplib.SMTPAuthenticationError(535, b"5.7.8 not accepted for user harsh@gmail.com"),
            "SMTPAuthenticationError (535)",
        ),
    ],
)
def test_smtp_failure_does_not_block_sign_up(make_app, fake_smtp, caplog, error, logged):
    fake_smtp.on_send = error
    app = make_app(**SMTP_ENV)
    client = browser(app)
    with caplog.at_level(logging.WARNING, logger="mealscholar.mailer"):
        assert register(client).status_code == 201
        app.state.auth.sender.drain(5)
    assert f"Couldn't send the verify_email email to s***@example.com: {logged}" in caplog.text
    assert "sam@example.com" not in caplog.text
    assert "user unknown" not in caplog.text
    assert "harsh@gmail.com" not in caplog.text
    assert fake_smtp.messages == []
    assert app.state.auth.store.recent_emails(20) == []


def test_starttls_can_be_turned_off(make_app, fake_smtp):
    app = make_app(**SMTP_ENV, MEALSCHOLAR_SMTP_STARTTLS="0", MEALSCHOLAR_SMTP_PORT="2525")
    assert register(browser(app)).status_code == 201
    app.state.auth.sender.drain(5)
    assert fake_smtp.connections == [("smtp.example", 2525, 10.0)]
    assert fake_smtp.calls == ["ehlo", "login u:p", "send_message", "quit"]


def test_smtp_settings_from_the_environment():
    assert load_settings({}).smtp is None
    custom = load_settings(
        {
            **SMTP_ENV,
            "MEALSCHOLAR_SMTP_PORT": "2525",
            "MEALSCHOLAR_SMTP_STARTTLS": "no",
            "MEALSCHOLAR_SMTP_TIMEOUT": "3",
        }
    ).smtp
    assert custom == SmtpSettings(
        host="smtp.example",
        port=2525,
        user="u",
        password="p",
        sender="MealScholar <no-reply@example.com>",
        starttls=False,
        timeout=3.0,
    )
    assert load_settings({"MEALSCHOLAR_SMTP_HOST": "smtp.example"}).smtp == SmtpSettings(host="smtp.example")

    for env, name in (
        ({"MEALSCHOLAR_EMAIL_PROVIDER": "smtp"}, "MEALSCHOLAR_SMTP_HOST"),
        ({"MEALSCHOLAR_EMAIL_PROVIDER": "smtp", "MEALSCHOLAR_SMTP_HOST": "smtp.example"}, "MEALSCHOLAR_EMAIL_FROM"),
        ({**SMTP_ENV, "MEALSCHOLAR_SMTP_PORT": "abc"}, "MEALSCHOLAR_SMTP_PORT"),
        ({**SMTP_ENV, "MEALSCHOLAR_SMTP_PORT": "70000"}, "MEALSCHOLAR_SMTP_PORT"),
        ({**SMTP_ENV, "MEALSCHOLAR_SMTP_TIMEOUT": "0"}, "MEALSCHOLAR_SMTP_TIMEOUT"),
        ({**SMTP_ENV, "MEALSCHOLAR_SMTP_TIMEOUT": "soon"}, "MEALSCHOLAR_SMTP_TIMEOUT"),
        ({**SMTP_ENV, "MEALSCHOLAR_EMAIL_FROM": "MealScholar"}, "MEALSCHOLAR_EMAIL_FROM"),
        ({**SMTP_ENV, "MEALSCHOLAR_EMAIL_FROM": "a@example.com, b@example.com"}, "MEALSCHOLAR_EMAIL_FROM"),
        ({**SMTP_ENV, "MEALSCHOLAR_EMAIL_FROM": "x@example.com\r\nBcc: y@example.com"}, "MEALSCHOLAR_EMAIL_FROM"),
    ):
        with pytest.raises(ValueError, match=name):
            load_settings(env)
    with pytest.raises(ValueError, match="MEALSCHOLAR_SMTP_HOST"):
        make_sender("smtp", MemoryAuthStore(), None)


def test_forgot_password_quota_is_per_network_and_email(make_app):
    app = make_app()
    register(browser(app))

    def forgot(client):
        return client.post("/api/auth/forgot-password", json={"email": "sam@example.com"}).status_code

    a = browser(app, ip="198.51.100.4")
    assert [forgot(a) for _ in range(4)] == [202, 202, 202, 429]
    before = len(app.state.auth.store.recent_emails(20))
    assert forgot(browser(app, ip="203.0.113.9")) == 202  # a stranger can't use up Sam's own attempts
    assert len(app.state.auth.store.recent_emails(20)) == before + 1


# --- cross-site requests and protected routes -------------------------------------------


def test_cross_site_writes_are_refused(make_app):
    app = make_app(MEALSCHOLAR_PUBLIC_URL="https://mealscholar.example")
    client = browser(app)
    body = {"email": "sam@example.com", "password": PASSWORD}
    for origin in ("https://evil.example", "null", "https://testserver.evil.example", "http://[::1"):
        res = client.post("/api/auth/register", json=body, headers={"Origin": origin})
        assert res.status_code == 403
        assert res.json() == {"detail": "Cross-site requests are not allowed."}
    evil = {"Origin": "https://evil.example"}
    for res in (
        client.patch("/api/meals/1/items/0", json={"grams": 5}, headers=evil),
        client.delete("/api/meals", headers=evil),
        client.delete("/api/me", headers=evil),
        client.post("/api/auth/delete-account", json={"password": PASSWORD}, headers=evil),
    ):
        assert res.status_code == 403

    assert client.post("/api/auth/register", json=body, headers={"Origin": "https://testserver"}).status_code == 201
    assert client.get("/api/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200  # reads pass
    assert client.post("/api/auth/logout", headers={"Origin": "https://mealscholar.example"}).status_code == 204
    assert client.post("/api/auth/login", json=body).status_code == 200  # no Origin header


def test_meal_and_diary_routes_need_a_session(make_app):
    vision = FakeVision()
    client = browser(make_app(vision=vision))
    for res in (
        client.post("/api/meals", content=photo(), headers=JPEG),
        client.get("/api/meals/1"),
        client.post("/api/meals/1/answer", json={"answer": "yes"}),
        client.patch("/api/meals/1/items/0", json={"grams": 100}),
        client.delete("/api/meals/1"),
        client.delete("/api/meals"),
        client.delete("/api/me"),
        client.post("/api/auth/delete-account", json={"password": PASSWORD}),
        client.get("/api/days/2026-11-14"),
    ):
        assert res.status_code == 401
        assert res.json() == SIGN_IN
    assert vision.contexts == []  # the photo never reached the model

    status = client.get("/api/status").json()
    assert status["auth_required"] is True and status["email_provider"] == "outbox"
    assert status["email_sending"] is False
    assert {"gemini_ready", "food_db_ready"} <= set(status)
    assert client.get("/api/nutrients").status_code == 200
    assert client.get("/api/foods", params={"q": "rice"}).json()["foods"]
    assert client.get("/").status_code == 200


# --- per-user diaries ----------------------------------------------------------------


def test_users_only_see_their_own_meals(make_app, home):
    app = make_app()
    sam, alex = browser(app), browser(app)
    register(sam, email="sam@example.com")
    register(alex, email="alex@example.com")

    meal = sam.post("/api/meals", content=photo(), headers=JPEG).json()
    meal_id, day = meal["id"], meal["context"]["taken_at"][:10]

    missing = {"detail": f"No meal #{meal_id}."}  # the same answer as for a meal that doesn't exist
    assert alex.get(f"/api/meals/{meal_id}").json() == missing
    assert alex.post(f"/api/meals/{meal_id}/answer", json={"answer": "yes, oil"}).json() == missing
    assert alex.patch(f"/api/meals/{meal_id}/items/0", json={"grams": 1}).json() == missing
    assert alex.delete(f"/api/meals/{meal_id}").json() == missing
    assert alex.get(f"/api/days/{day}").json()["meals"] == []
    assert sam.get("/api/meals/999").json() == {"detail": "No meal #999."}
    assert sam.delete("/api/meals/999").json() == {"detail": "No meal #999."}

    assert sam.get(f"/api/meals/{meal_id}").json() == meal  # untouched by alex's attempts
    assert [m["id"] for m in sam.get(f"/api/days/{day}").json()["meals"]] == [meal_id]
    assert sam.post(f"/api/meals/{meal_id}/answer", json={"answer": "yes, oil"}).json()["answer"] == "yes, oil"
    with closing(Diary(home / "diary.sqlite")) as local:  # nor does the CLI's local diary see it
        assert local.get(meal_id) is None
        assert not local.delete(meal_id)

    deleted = sam.delete(f"/api/meals/{meal_id}")
    assert (deleted.status_code, deleted.content) == (204, b"")
    assert sam.get(f"/api/meals/{meal_id}").json() == missing
    assert sam.get(f"/api/days/{day}").json()["meals"] == []


def test_clear_history_deletes_only_your_meals_and_keeps_settings(make_app, home):
    app = make_app()
    sam, alex = browser(app), browser(app)
    sam_id = register(sam, email="sam@example.com").json()["user"]["id"]
    register(alex, email="alex@example.com")
    first = sam.post("/api/meals", content=photo(), headers=JPEG).json()
    assert sam.patch(f"/api/meals/{first['id']}/items/0", json={"grams": 300}).status_code == 200
    sam.post("/api/meals", content=photo(), headers=JPEG)
    kept = alex.post("/api/meals", content=photo(), headers=JPEG).json()
    assert sam.patch("/api/me/prefs", json={"onboarded": True}).status_code == 200
    day = first["context"]["taken_at"][:10]

    assert sam.delete("/api/meals").json() == {"meals": 2}
    assert sam.get(f"/api/meals/{first['id']}").status_code == 404
    assert sam.get(f"/api/days/{day}").json()["meals"] == []
    with closing(Diary(home / "diary.sqlite", sam_id)) as diary:
        assert diary.usual_portions() == {}  # learned from the deleted meals, so gone too
    assert sam.get("/api/me/prefs").json()["onboarded"] is True  # settings stay
    assert alex.get(f"/api/meals/{kept['id']}").json() == kept  # nobody else's meals go
    assert sam.delete("/api/meals").json() == {"meals": 0}


def test_erasing_without_an_account_starts_over(make_app):
    client = browser(make_app(MEALSCHOLAR_AUTH_REQUIRED="0"))
    meal = client.post("/api/meals", content=photo(), headers=JPEG).json()
    assert client.patch("/api/me/prefs", json={"onboarded": True, "calorie_goal": 2200}).status_code == 200

    assert client.delete("/api/me").json() == {"meals": 1}
    assert client.get(f"/api/meals/{meal['id']}").status_code == 404
    prefs = client.get("/api/me/prefs").json()
    assert (prefs["onboarded"], prefs["calorie_goal"], prefs["updated_at"]) == (False, None, None)


def test_delete_account_needs_the_password_and_then_removes_everything(make_app, home):
    app = make_app()
    sam, phone, alex = browser(app), browser(app), browser(app)
    sam_id = register(sam).json()["user"]["id"]
    login(phone)
    register(alex, email="alex@example.com")
    meal = sam.post("/api/meals", content=photo(), headers=JPEG).json()
    kept = alex.post("/api/meals", content=photo(), headers=JPEG).json()
    assert sam.patch("/api/me/prefs", json={"onboarded": True}).status_code == 200

    wrong = sam.post("/api/auth/delete-account", json={"password": "not my password"})
    assert (wrong.status_code, wrong.json()) == (401, {"detail": "That password is incorrect.", "field": "password"})
    assert sam.get(f"/api/meals/{meal['id']}").json() == meal  # a wrong password deletes nothing

    res = sam.post("/api/auth/delete-account", json={"password": PASSWORD})
    assert (res.status_code, res.content) == (204, b"")
    assert session_cookie(res).value == ""
    assert sam.get("/api/auth/me").status_code == 401
    assert phone.get("/api/auth/me").status_code == 401  # signed out everywhere
    assert app.state.auth.store.user_by_id(sam_id) is None
    assert app.state.auth.store.user_by_email("sam@example.com") is None
    assert app.state.prefs.store.get(sam_id) is None
    with closing(Diary(home / "diary.sqlite", sam_id)) as diary:
        assert diary.get(meal["id"]) is None
    assert login(sam).json() == BAD_LOGIN
    assert alex.get(f"/api/meals/{kept['id']}").json() == kept
    assert register(sam).status_code == 201  # the email can sign up again


def test_usual_portions_are_per_user(make_app, home):
    vision = FakeVision()
    app = make_app(vision=vision)
    sam, alex = browser(app), browser(app)
    sam_id = register(sam, email="sam@example.com").json()["user"]["id"]
    alex_id = register(alex, email="alex@example.com").json()["user"]["id"]

    meal = sam.post("/api/meals", content=photo(), headers=JPEG).json()
    assert sam.patch(f"/api/meals/{meal['id']}/items/0", json={"grams": 300}).status_code == 200
    sam.post("/api/meals", content=photo(), headers=JPEG)
    alex.post("/api/meals", content=photo(), headers=JPEG)
    assert "usual portions from past corrections: white rice: 300 g" in vision.contexts[-2]  # sam's next photo
    assert "usual portions" not in vision.contexts[-1]  # alex's

    for user_id, portions in ((sam_id, {"white rice": 300}), (alex_id, {}), (None, {})):
        with closing(Diary(home / "diary.sqlite", user_id)) as diary:
            assert diary.usual_portions() == portions


OLD_DIARY = """
CREATE TABLE meals (
    id         INTEGER PRIMARY KEY,
    local_date TEXT NOT NULL,
    meal_slot  TEXT NOT NULL,
    taken_at   TEXT NOT NULL,
    result     TEXT NOT NULL
);
CREATE INDEX meals_by_date ON meals (local_date, taken_at);
CREATE TABLE usual_portions (
    name        TEXT PRIMARY KEY,
    grams_total REAL    NOT NULL,
    corrections INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def test_diary_from_before_accounts_keeps_its_rows_as_the_local_users(tmp_path):
    path = tmp_path / "diary.sqlite"
    context = MealContext(taken_at=datetime(2026, 11, 13, 12, 30), time_source="device", meal_slot="lunch")
    meal = MealResult(id=1, context=context, items=[])
    with closing(sqlite3.connect(path)) as con:
        con.executescript(OLD_DIARY)
        row = (context.taken_at.isoformat(), meal.model_dump_json())
        con.execute("INSERT INTO meals VALUES (1, '2026-11-13', 'lunch', ?, ?)", row)
        con.execute("INSERT INTO usual_portions (name, grams_total, corrections) VALUES ('white rice', 700, 2)")
        con.commit()

    day = date(2026, 11, 13)
    with closing(Diary(path)) as local:
        assert local.get(1) == meal
        assert local.meals_between(day, day) == [meal]
        assert local.usual_portions() == {"white rice": 350}
    with closing(Diary(path, "user-1")) as account:
        assert account.get(1) is None
        assert account.meals_between(day, day) == []
        assert account.usual_portions() == {}
        account.remember_portion("White Rice", 100)  # same food, separate row
        assert account.usual_portions() == {"white rice": 100}
        account.save(meal.model_copy(update={"answer": "not yours"}))  # can't overwrite another user's meal
    with closing(Diary(path)) as local:  # opening again changes nothing
        assert local.get(1) == meal
        assert local.usual_portions() == {"white rice": 350}

    with closing(sqlite3.connect(path)) as con:
        key = sorted((row[5], row[1]) for row in con.execute("PRAGMA table_info(usual_portions)") if row[5])
        assert [name for _, name in key] == ["user_id", "name"]
        indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "meals_by_user_date" in indexes and "meals_by_date" not in indexes


def test_without_required_sign_in_the_web_app_uses_the_local_diary(make_app, home):
    app = make_app(MEALSCHOLAR_AUTH_REQUIRED="0")
    client = browser(app)
    assert client.get("/api/status").json()["auth_required"] is False

    res = client.post("/api/meals", content=photo(), headers=JPEG)
    assert res.status_code == 200
    meal = res.json()
    assert client.get(f"/api/meals/{meal['id']}").json() == meal
    assert client.patch(f"/api/meals/{meal['id']}/items/0", json={"grams": 250}).status_code == 200
    assert client.post(f"/api/meals/{meal['id']}/answer", json={"answer": ""}).status_code == 422  # FastAPI's usual
    day = meal["context"]["taken_at"][:10]
    assert [m["id"] for m in client.get(f"/api/days/{day}").json()["meals"]] == [meal["id"]]
    with closing(Diary(home / "diary.sqlite")) as local:  # the same rows the CLI sees
        assert local.get(meal["id"]) is not None
        assert local.usual_portions() == {"white rice": 250}

    register(client)  # signing in still works, and then the account's own diary is used
    assert client.get(f"/api/meals/{meal['id']}").status_code == 404


def test_account_settings_from_the_environment():
    flags = ("auth_required", "cookie_secure", "demo_verify_link", "dev_outbox")
    defaults = load_settings({})
    assert (defaults.mongodb_uri, defaults.mongodb_db) == (None, "mealscholar")
    assert (defaults.public_url, defaults.email_provider) == ("http://127.0.0.1:8000", "outbox")
    assert [getattr(defaults, flag) for flag in flags] == [True, True, False, False]

    custom = load_settings(
        {
            "MONGODB_URI": "mongodb://localhost",
            "MEALSCHOLAR_MONGODB_DB": "other",
            "MEALSCHOLAR_AUTH_REQUIRED": "0",
            "MEALSCHOLAR_PUBLIC_URL": "https://meals.example/",
            "MEALSCHOLAR_COOKIE_SECURE": "false",
            "MEALSCHOLAR_EMAIL_PROVIDER": "SMTP",
            "MEALSCHOLAR_SMTP_HOST": "smtp.example",
            "MEALSCHOLAR_EMAIL_FROM": "no-reply@example.com",
            "MEALSCHOLAR_DEMO_VERIFY_LINK": "1",
            "MEALSCHOLAR_DEV_OUTBOX": "yes",
        }
    )
    assert (custom.mongodb_uri, custom.mongodb_db) == ("mongodb://localhost", "other")
    assert (custom.public_url, custom.email_provider) == ("https://meals.example", "smtp")
    assert [getattr(custom, flag) for flag in flags] == [False, False, True, True]
    assert defaults.thinking_level == "low"
    assert load_settings({"MEALSCHOLAR_THINKING": "High"}).thinking_level == "high"

    for name, value in (
        ("MEALSCHOLAR_AUTH_REQUIRED", "maybe"),
        ("MEALSCHOLAR_EMAIL_PROVIDER", "pigeon"),
        ("MEALSCHOLAR_PUBLIC_URL", "meals.example"),
        ("MEALSCHOLAR_THINKING", "ultra"),
    ):
        with pytest.raises(ValueError, match=name):
            load_settings({name: value})


def test_a_malformed_setting_is_a_clean_cli_error(monkeypatch, tmp_path, capsys):
    """Every command reads the settings, so a typo in an account setting must not be a traceback."""
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("MEALSCHOLAR_HOME", str(tmp_path))
    monkeypatch.setenv("MEALSCHOLAR_COOKIE_SECURE", "2")
    with pytest.raises(SystemExit) as stopped:
        cli.main(["day", "2026-11-13"])
    assert stopped.value.code == 1
    assert capsys.readouterr().err == "error: MEALSCHOLAR_COOKIE_SECURE must be 1 or 0, not '2'.\n"


def test_warns_when_the_secure_cookie_meets_a_plain_http_url(make_app, caplog):
    with caplog.at_level(logging.WARNING, logger="mealscholar.auth"):
        make_app(MEALSCHOLAR_PUBLIC_URL="https://meals.example")
        make_app(MEALSCHOLAR_COOKIE_SECURE="0")
        assert "MEALSCHOLAR_COOKIE_SECURE" not in caplog.text
        make_app()  # the defaults: a Secure cookie and http://127.0.0.1:8000
        assert "Set MEALSCHOLAR_COOKIE_SECURE=0 to sign in over plain HTTP" in caplog.text


# --- account stores (memory, and MongoDB when MEALSCHOLAR_TEST_MONGODB_URI is set) ----------


STORES = ["memory", "mongo"] if os.environ.get("MEALSCHOLAR_TEST_MONGODB_URI") else ["memory"]


@pytest.fixture(params=STORES)
def store(request):
    if request.param == "memory":
        yield MemoryAuthStore()
        return
    mongo = MongoAuthStore.connect(os.environ["MEALSCHOLAR_TEST_MONGODB_URI"], MONGO_TEST_DB)
    try:
        if mongo.db.list_collection_names():
            pytest.skip(f"{MONGO_TEST_DB} already has data; not touching it")
        try:
            mongo.ensure_indexes()
            mongo.ensure_indexes()  # idempotent
            yield mongo
        finally:
            mongo.db.client.drop_database(MONGO_TEST_DB)
    finally:
        mongo.close()


def test_store_users_sessions_tokens_and_outbox(store):
    now = utc_now()
    later = now + timedelta(minutes=1)

    user = store.create_user("sam@example.com", "Sam", "hash-1", now)
    assert store.user_by_id(user.id) == user and store.user_by_email("sam@example.com") == user
    assert (user.email_verified_at, user.last_login_at, user.created_at) == (None, None, now)
    assert store.user_by_id("0" * 24) is None and store.user_by_id("not an id") is None
    with pytest.raises(EmailTaken):
        store.create_user("sam@example.com", None, "hash-2", now)
    verified = store.mark_email_verified(user.id, later)
    assert verified.email_verified_at == later and verified.updated_at == later
    again = store.mark_email_verified(user.id, later + timedelta(minutes=1))
    assert again.email_verified_at == later  # the first time is kept
    assert store.set_password_hash(user.id, "hash-3", later).password_hash == "hash-3"
    store.record_login(user.id, later)
    assert store.user_by_id(user.id).last_login_at == later

    session = Session("h1", user.id, now, now + timedelta(days=30), now, "pytest", "192.0.2.1")
    for token_hash in ("h1", "h2", "h3"):
        store.create_session(replace(session, token_hash=token_hash))
    assert store.session("h1", now) == session
    assert store.session("h1", now + timedelta(days=30)) is None  # expired
    store.touch_session("h1", later)
    assert store.session("h1", later).last_seen_at == later
    store.delete_session("h1")
    assert store.session("h1", now) is None
    store.delete_user_sessions(user.id, keep="h2")
    assert store.session("h2", now) is not None and store.session("h3", now) is None
    store.delete_user_sessions(user.id)
    assert store.session("h2", now) is None

    token = EmailToken("t1", user.id, "verify_email", now, now + timedelta(hours=24))
    store.create_email_token(token)
    assert store.email_token("t1", "reset_password", now) is None  # wrong purpose
    assert store.email_token("t1", "verify_email", now) == token  # looking doesn't use it up
    assert store.use_email_token("t1", "verify_email", later).used_at == later
    assert store.use_email_token("t1", "verify_email", later) is None  # once only
    store.create_email_token(EmailToken("t2", user.id, "reset_password", now, now + timedelta(hours=1)))
    assert store.use_email_token("t2", "reset_password", now + timedelta(hours=1)) is None  # expired
    store.create_email_token(EmailToken("t3", user.id, "reset_password", now, now + timedelta(hours=1)))
    store.revoke_email_tokens(user.id, "reset_password", later)
    assert store.email_token("t3", "reset_password", later) is None

    first = OutboxEmail("sam@example.com", "first", "text", "verify_email", now, now + timedelta(days=7))
    store.queue_email(first)
    store.queue_email(replace(first, subject="second", created_at=later))
    assert [e.subject for e in store.recent_emails(20)] == ["second", "first"]
    assert store.recent_emails(1)[0] == replace(first, subject="second", created_at=later)


def test_account_flow_end_to_end(make_app, store):
    app = make_app(store=store)
    client = browser(app)
    user = register(client).json()["user"]
    assert client.post("/api/auth/verify-email", json={"token": emailed_token(app, "verify")}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert login(client).json()["user"]["id"] == user["id"]

    client.post("/api/auth/forgot-password", json={"email": "sam@example.com"})
    res = client.post("/api/auth/reset-password", json={"token": emailed_token(app, "reset"), "password": NEW_PASSWORD})
    assert res.status_code == 200
    assert client.get("/api/auth/me").json()["user"] == {**user, "email_verified": True}
    phone = browser(app)
    assert login(phone, password=NEW_PASSWORD).status_code == 200

    client.post("/api/auth/forgot-password", json={"email": "sam@example.com"})
    link = emailed_token(app, "reset")
    change = {"current_password": NEW_PASSWORD, "new_password": PASSWORD}
    assert client.post("/api/auth/change-password", json=change).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert phone.get("/api/auth/me").json() == SIGN_IN
    assert client.post("/api/auth/reset-password", json={"token": link, "password": NEW_PASSWORD}).status_code == 400


def test_forgot_password_mails_one_account_at_most_5_times_an_hour_from_any_network(make_app):
    app = make_app()
    register(browser(app))
    store = app.state.auth.store

    def forgot(ip):
        return browser(app, ip=ip).post("/api/auth/forgot-password", json={"email": "sam@example.com"}).status_code

    assert [forgot(f"203.0.113.{i}") for i in range(8)] == [202] * 8  # silent: the same answer as for nobody
    assert [e.purpose for e in store.recent_emails(20)].count("reset_password") == 5


def test_forgot_password_looks_up_the_account_after_answering(make_app):
    """The web route passes its background tasks as `defer`: the lookup and the email happen after
    the 202 is sent, so the answer takes the same time whether or not the account exists."""
    app = make_app()
    register(browser(app))
    auth = app.state.auth
    later = []
    for email in ("sam@example.com", "nobody@example.com"):
        auth.forgot_password(email, Client("198.51.100.1", None), defer=later.append)
    assert len(later) == 2 and [e.purpose for e in auth.store.recent_emails(20)] == ["verify_email"]
    for work in later:
        work()
    assert [e.purpose for e in auth.store.recent_emails(20)] == ["reset_password", "verify_email"]


def test_a_background_smtp_error_of_any_kind_is_logged_without_the_address(fake_smtp, monkeypatch, caplog):
    sender = SmtpSender(SmtpSettings(host="smtp.example", sender="x@example.com"))

    def broken(email):
        raise UnicodeEncodeError("ascii", "sam@example.com", 0, 1, "no")

    monkeypatch.setattr(sender, "_deliver", broken)
    with caplog.at_level(logging.WARNING, logger="mealscholar.mailer"):
        sender.send(mailer.password_changed_email("sam@example.com", "https://app.example"))
        sender.drain(5)
    assert "Couldn't send the password_changed email to s***@example.com: UnicodeEncodeError" in caplog.text
    assert "sam@example.com" not in caplog.text
