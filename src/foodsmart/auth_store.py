"""Account storage: users, sign-in sessions, one-time email tokens and the email outbox.

MongoAuthStore keeps them in MongoDB. MemoryAuthStore behaves the same in process memory, for
tests and local use; its accounts vanish on restart. Session and email tokens are stored only
as SHA-256 hashes; the raw email tokens exist only inside the queued email text.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from bson import ObjectId
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from .config import Settings

log = logging.getLogger(__name__)

TokenPurpose = Literal["verify_email", "reset_password"]
EmailPurpose = Literal[TokenPurpose, "favorites_digest"]


def utc_now() -> datetime:
    """Current UTC time at millisecond precision, the precision MongoDB stores."""
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class EmailTaken(Exception):
    """Another account already uses this email."""


@dataclass(frozen=True, slots=True)
class User:
    id: str
    email: str
    name: str | None
    password_hash: str
    email_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class Session:
    token_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    ip: str | None


@dataclass(frozen=True, slots=True)
class EmailToken:
    token_hash: str
    user_id: str
    purpose: TokenPurpose
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OutboxEmail:
    to: str
    subject: str
    text: str
    purpose: EmailPurpose
    created_at: datetime
    expires_at: datetime
    status: str = "queued"
    provider: str = "outbox"


class AuthStore(Protocol):
    def ensure_indexes(self) -> None: ...

    # users
    def create_user(self, email: str, name: str | None, password_hash: str, now: datetime) -> User:
        """Raises EmailTaken when another account has this email."""
        ...

    def user_by_id(self, user_id: str) -> User | None: ...

    def user_by_email(self, email: str) -> User | None: ...

    def record_login(self, user_id: str, now: datetime) -> None: ...

    def mark_email_verified(self, user_id: str, now: datetime) -> User | None:
        """Keeps the first confirmation time when called again."""
        ...

    def set_password_hash(self, user_id: str, password_hash: str, now: datetime) -> User | None: ...

    # sessions
    def create_session(self, session: Session) -> None: ...

    def session(self, token_hash: str, now: datetime) -> Session | None:
        """The session if it exists and has not expired."""
        ...

    def touch_session(self, token_hash: str, now: datetime) -> None: ...

    def delete_session(self, token_hash: str) -> None: ...

    def delete_user_sessions(self, user_id: str, *, keep: str | None = None) -> None:
        """Signs the user out everywhere, except the session whose hash is `keep`."""
        ...

    # one-time email tokens
    def create_email_token(self, token: EmailToken) -> None: ...

    def email_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        """The token if it is unused and unexpired, without using it."""
        ...

    def use_email_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        """Atomically marks an unused, unexpired token as used; None if there is no such token."""
        ...

    def revoke_email_tokens(self, user_id: str, purpose: TokenPurpose, now: datetime) -> None: ...

    # outbox
    def queue_email(self, email: OutboxEmail) -> None: ...

    def recent_emails(self, limit: int) -> list[OutboxEmail]:
        """Newest first."""
        ...


def open_store(settings: Settings) -> AuthStore:
    """MongoDB when MONGODB_URI is set, otherwise memory."""
    if not settings.mongodb_uri:
        log.warning("MONGODB_URI is not set: accounts are kept in memory and vanish on restart.")
        return MemoryAuthStore()
    try:
        store = MongoAuthStore.connect(settings.mongodb_uri, settings.mongodb_db)
        store.ensure_indexes()
    except PyMongoError as exc:
        raise ConnectionError(f"Can't set up accounts in MongoDB; check MONGODB_URI. ({exc})") from exc
    return store


class MemoryAuthStore:
    """Same behaviour as MongoAuthStore, in this process only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._user_ids: dict[str, str] = {}  # email -> user id
        self._sessions: dict[str, Session] = {}
        self._tokens: dict[str, EmailToken] = {}
        self._outbox: list[OutboxEmail] = []

    def ensure_indexes(self) -> None:
        pass

    def create_user(self, email: str, name: str | None, password_hash: str, now: datetime) -> User:
        with self._lock:
            if email in self._user_ids:
                raise EmailTaken(email)
            user = User(
                id=str(ObjectId()),
                email=email,
                name=name,
                password_hash=password_hash,
                email_verified_at=None,
                created_at=now,
                updated_at=now,
                last_login_at=None,
            )
            self._users[user.id] = user
            self._user_ids[email] = user.id
            return user

    def user_by_id(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def user_by_email(self, email: str) -> User | None:
        with self._lock:
            user_id = self._user_ids.get(email)
            return self._users[user_id] if user_id else None

    def record_login(self, user_id: str, now: datetime) -> None:
        self._update_user(user_id, last_login_at=now)

    def mark_email_verified(self, user_id: str, now: datetime) -> User | None:
        with self._lock:
            user = self._users.get(user_id)
            if user is not None and user.email_verified_at is None:
                user = self._users[user_id] = replace(user, email_verified_at=now, updated_at=now)
            return user

    def set_password_hash(self, user_id: str, password_hash: str, now: datetime) -> User | None:
        return self._update_user(user_id, password_hash=password_hash, updated_at=now)

    def _update_user(self, user_id: str, **changes: Any) -> User | None:
        with self._lock:
            user = self._users.get(user_id)
            if user is not None:
                user = self._users[user_id] = replace(user, **changes)
            return user

    def create_session(self, session: Session) -> None:
        with self._lock:
            self._purge(session.created_at)
            self._sessions[session.token_hash] = session

    def session(self, token_hash: str, now: datetime) -> Session | None:
        with self._lock:
            session = self._sessions.get(token_hash)
            return session if session is not None and session.expires_at > now else None

    def touch_session(self, token_hash: str, now: datetime) -> None:
        with self._lock:
            if (session := self._sessions.get(token_hash)) is not None:
                self._sessions[token_hash] = replace(session, last_seen_at=now)

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._sessions.pop(token_hash, None)

    def delete_user_sessions(self, user_id: str, *, keep: str | None = None) -> None:
        with self._lock:
            for token_hash, session in list(self._sessions.items()):
                if session.user_id == user_id and token_hash != keep:
                    del self._sessions[token_hash]

    def create_email_token(self, token: EmailToken) -> None:
        with self._lock:
            self._purge(token.created_at)
            self._tokens[token.token_hash] = token

    def email_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        with self._lock:
            return self._usable_token(token_hash, purpose, now)

    def use_email_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        with self._lock:
            token = self._usable_token(token_hash, purpose, now)
            if token is not None:
                token = self._tokens[token_hash] = replace(token, used_at=now)
            return token

    def _usable_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        token = self._tokens.get(token_hash)
        if token is None or token.purpose != purpose or token.used_at is not None or token.expires_at <= now:
            return None
        return token

    def revoke_email_tokens(self, user_id: str, purpose: TokenPurpose, now: datetime) -> None:
        with self._lock:
            for token_hash, token in list(self._tokens.items()):
                if token.user_id == user_id and token.purpose == purpose and token.used_at is None:
                    self._tokens[token_hash] = replace(token, used_at=now)

    def queue_email(self, email: OutboxEmail) -> None:
        with self._lock:
            self._purge(email.created_at)
            self._outbox.append(email)

    def recent_emails(self, limit: int) -> list[OutboxEmail]:
        with self._lock:
            return self._outbox[::-1][:limit]

    def _purge(self, now: datetime) -> None:
        """Drop expired records, as MongoDB's TTL indexes do."""
        self._sessions = {k: s for k, s in self._sessions.items() if s.expires_at > now}
        self._tokens = {k: t for k, t in self._tokens.items() if t.expires_at > now}
        self._outbox = [e for e in self._outbox if e.expires_at > now]


class MongoAuthStore:
    """Collections users, sessions, email_tokens and email_outbox in one database.

    Expired sessions, tokens and outbox messages are removed by TTL indexes on expires_at;
    MongoDB deletes them within about a minute, so reads also check expires_at.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._users = db["users"]
        self._sessions = db["sessions"]
        self._tokens = db["email_tokens"]
        self._outbox = db["email_outbox"]

    @classmethod
    def connect(cls, uri: str, database: str) -> MongoAuthStore:
        client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10_000, appname="mealscholar")
        return cls(client[database])

    def close(self) -> None:
        self.db.client.close()

    def ensure_indexes(self) -> None:
        """Idempotent; run at startup."""
        self._users.create_index("email", unique=True)
        self._sessions.create_index("token_hash", unique=True)
        self._sessions.create_index("user_id")
        self._sessions.create_index("expires_at", expireAfterSeconds=0)
        self._tokens.create_index("token_hash", unique=True)
        self._tokens.create_index([("user_id", ASCENDING), ("purpose", ASCENDING)])
        self._tokens.create_index("expires_at", expireAfterSeconds=0)
        self._outbox.create_index("created_at")
        self._outbox.create_index("expires_at", expireAfterSeconds=0)

    def create_user(self, email: str, name: str | None, password_hash: str, now: datetime) -> User:
        doc = {
            "email": email,
            "name": name,
            "password_hash": password_hash,
            "email_verified_at": None,
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
        }
        try:
            self._users.insert_one(doc)  # adds _id to doc
        except DuplicateKeyError as exc:
            raise EmailTaken(email) from exc
        return _user(doc)

    def user_by_id(self, user_id: str) -> User | None:
        oid = _object_id(user_id)
        doc = self._users.find_one({"_id": oid}) if oid else None
        return _user(doc) if doc else None

    def user_by_email(self, email: str) -> User | None:
        doc = self._users.find_one({"email": email})
        return _user(doc) if doc else None

    def record_login(self, user_id: str, now: datetime) -> None:
        if oid := _object_id(user_id):
            self._users.update_one({"_id": oid}, {"$set": {"last_login_at": now}})

    def mark_email_verified(self, user_id: str, now: datetime) -> User | None:
        oid = _object_id(user_id)
        if oid is None:
            return None
        doc = self._users.find_one_and_update(
            {"_id": oid, "email_verified_at": None},
            {"$set": {"email_verified_at": now, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return _user(doc) if doc else self.user_by_id(user_id)

    def set_password_hash(self, user_id: str, password_hash: str, now: datetime) -> User | None:
        if (oid := _object_id(user_id)) is None:
            return None
        doc = self._users.find_one_and_update(
            {"_id": oid},
            {"$set": {"password_hash": password_hash, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return _user(doc) if doc else None

    def create_session(self, session: Session) -> None:
        self._sessions.insert_one({**asdict(session), "user_id": ObjectId(session.user_id)})

    def session(self, token_hash: str, now: datetime) -> Session | None:
        doc = self._sessions.find_one({"token_hash": token_hash, "expires_at": {"$gt": now}})
        return _record(Session, doc, user_id=str(doc["user_id"])) if doc else None

    def touch_session(self, token_hash: str, now: datetime) -> None:
        self._sessions.update_one({"token_hash": token_hash}, {"$set": {"last_seen_at": now}})

    def delete_session(self, token_hash: str) -> None:
        self._sessions.delete_one({"token_hash": token_hash})

    def delete_user_sessions(self, user_id: str, *, keep: str | None = None) -> None:
        if (oid := _object_id(user_id)) is None:
            return
        query: dict[str, Any] = {"user_id": oid}
        if keep is not None:
            query["token_hash"] = {"$ne": keep}
        self._sessions.delete_many(query)

    def create_email_token(self, token: EmailToken) -> None:
        self._tokens.insert_one({**asdict(token), "user_id": ObjectId(token.user_id)})

    def email_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        doc = self._tokens.find_one(_usable(token_hash, purpose, now))
        return _record(EmailToken, doc, user_id=str(doc["user_id"])) if doc else None

    def use_email_token(self, token_hash: str, purpose: TokenPurpose, now: datetime) -> EmailToken | None:
        doc = self._tokens.find_one_and_update(
            _usable(token_hash, purpose, now),
            {"$set": {"used_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return _record(EmailToken, doc, user_id=str(doc["user_id"])) if doc else None

    def revoke_email_tokens(self, user_id: str, purpose: TokenPurpose, now: datetime) -> None:
        if (oid := _object_id(user_id)) is not None:
            self._tokens.update_many({"user_id": oid, "purpose": purpose, "used_at": None}, {"$set": {"used_at": now}})

    def queue_email(self, email: OutboxEmail) -> None:
        self._outbox.insert_one(asdict(email))

    def recent_emails(self, limit: int) -> list[OutboxEmail]:
        docs = self._outbox.find({}).sort([("created_at", -1), ("_id", -1)]).limit(limit)
        return [_record(OutboxEmail, doc) for doc in docs]


def _usable(token_hash: str, purpose: TokenPurpose, now: datetime) -> dict[str, Any]:
    return {"token_hash": token_hash, "purpose": purpose, "used_at": None, "expires_at": {"$gt": now}}


def _object_id(value: str) -> ObjectId | None:
    return ObjectId(value) if ObjectId.is_valid(value) else None


def _user(doc: Mapping[str, Any]) -> User:
    return _record(User, doc, id=str(doc["_id"]))


def _record[R](cls: type[R], doc: Mapping[str, Any], **overrides: Any) -> R:
    """Build a record dataclass from a document's fields (extra keys such as _id are ignored).
    MongoDB stores UTC; a client without tz_aware returns it naive, so naive means UTC."""
    values = {f.name: _utc(doc.get(f.name)) for f in fields(cls)}
    return cls(**{**values, **overrides})


def _utc(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
