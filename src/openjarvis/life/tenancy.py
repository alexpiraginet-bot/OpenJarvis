"""User identity and bearer tokens for the hosted, multi-client Life OS.

OpenJarvis upstream is single-user: one global ``OPENJARVIS_API_KEY`` guards
the whole server. Serving *clients* needs the opposite shape — many users, each
seeing only their own life — so this module issues per-user tokens that the
Life API resolves to a ``user_id``, and every downstream query is scoped by it.

Credential handling uses only the standard library:

* Passwords are hashed with ``hashlib.scrypt`` (memory-hard, so GPU cracking
  of a leaked database stays expensive) over a per-user 16-byte salt.
* Tokens are 32 bytes of ``secrets`` entropy; the database stores only their
  SHA-256, so a dump yields nothing usable. SHA-256 without a salt is correct
  *here* — unlike a password, a 256-bit random token has no guessable
  preimage, so there is no dictionary to attack.
* Every comparison goes through ``hmac.compare_digest`` to avoid leaking
  material through timing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.life.db import Database, connect
from openjarvis.life.schema import ensure_schema

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

#: Tokens outlive a phone sitting in a pocket for a weekend but not a stolen
#: device forever. Clients refresh by logging in again.
DEFAULT_TOKEN_TTL_DAYS = 90

MIN_PASSWORD_LENGTH = 8

#: Failed logins tolerated before the address is locked out. Generous enough
#: to absorb a fat-fingered password on a phone keyboard, tight enough that an
#: online guessing attack gets nowhere.
MAX_LOGIN_FAILURES = 5

#: How long a lockout lasts, and how long a quiet period erases the counter.
LOCKOUT_SECONDS = 900
FAILURE_WINDOW_SECONDS = 900


class AuthError(RuntimeError):
    """Raised when a credential is malformed, duplicated or rejected."""


@dataclass(frozen=True, slots=True)
class User:
    """A client of the assistant — the tenant every Life row belongs to."""

    id: str
    email: str
    name: str
    timezone: str
    currency: str
    locale: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API responses. Never includes credential material."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "timezone": self.timezone,
            "currency": self.currency,
            "locale": self.locale,
            "created_at": self.created_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return derived.hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _is_unique_violation(exc: BaseException) -> bool:
    """Whether a driver exception is a unique-constraint violation.

    sqlite3 and psycopg each raise their own ``IntegrityError``, and importing
    psycopg here just to catch it would make the SQLite path depend on the
    Postgres extra. Matching on the class name keeps one code path for both.
    """
    for klass in type(exc).__mro__:
        if klass.__name__ in ("IntegrityError", "UniqueViolation"):
            return True
    return False


def _attempt_key(email: str) -> str:
    """Hash an address for the throttle table, so it stores no addresses."""
    return hashlib.sha256(_normalize_email(email).encode("utf-8")).hexdigest()


class UserStore:
    """Registry of clients and their bearer tokens, on SQLite or Postgres."""

    def __init__(
        self,
        db_path: str | Path = "",
        *,
        db: Optional[Database] = None,
    ) -> None:
        """Open (or adopt) the Life database and ensure the schema exists.

        Passing ``db`` lets :class:`~openjarvis.life.store.LifeStore` share a
        single connection so identity and domain data live in one database and
        one transaction scope.
        """
        self._owns_db = db is None
        self._db = db if db is not None else connect(str(db_path) or None)
        if self._owns_db:
            ensure_schema(self._db)

    @property
    def connection(self) -> Database:
        """The underlying database, for stores that share this connection."""
        return self._db

    # -- Users ---------------------------------------------------------------

    def create_user(
        self,
        email: str,
        password: str,
        *,
        name: str = "",
        timezone_name: str = "America/Sao_Paulo",
        currency: str = "BRL",
        locale: str = "pt-BR",
    ) -> User:
        """Register a client and return the stored :class:`User`.

        Raises :class:`AuthError` on a blank/short password or an email that is
        already registered.
        """
        email = _normalize_email(email)
        if not email or "@" not in email:
            raise AuthError("A valid email is required")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )

        salt = secrets.token_bytes(16)
        user = User(
            id=uuid.uuid4().hex,
            email=email,
            name=name.strip() or email.split("@")[0],
            timezone=timezone_name,
            currency=currency,
            locale=locale,
            created_at=_now(),
        )
        try:
            self._db.execute(
                "INSERT INTO users (id, email, name, password_hash, salt,"
                " timezone, currency, locale, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user.id,
                    user.email,
                    user.name,
                    _hash_password(password, salt),
                    salt.hex(),
                    user.timezone,
                    user.currency,
                    user.locale,
                    user.created_at,
                ),
            )
            self._db.commit()
        except Exception as exc:
            # Both drivers raise their own IntegrityError subclass; the unique
            # index on `email` is the only constraint this INSERT can violate.
            if not _is_unique_violation(exc):
                raise
            self._db.rollback()
            raise AuthError("Email is already registered") from exc
        return user

    def authenticate(self, email: str, password: str) -> Optional[User]:
        """Return the matching user, or ``None`` when credentials are wrong.

        Callers must not distinguish "no such email" from "bad password" in
        their responses — that difference is a user-enumeration oracle.
        """
        row = self._db.execute(
            "SELECT * FROM users WHERE email = ?", (_normalize_email(email),)
        ).fetchone()
        if row is None:
            # Spend comparable time on a miss so response latency does not
            # reveal whether the email exists.
            _hash_password(password, b"\x00" * 16)
            return None
        expected = row["password_hash"]
        actual = _hash_password(password, bytes.fromhex(row["salt"]))
        if not hmac.compare_digest(expected, actual):
            return None
        return self._row_to_user(row)

    # -- Brute-force protection ---------------------------------------------

    def seconds_until_unlocked(
        self, email: str, *, now: Optional[datetime] = None
    ) -> int:
        """Seconds an address must wait before it may try again. 0 = allowed.

        Callers must consult this *before* verifying a password, and must
        answer identically whether or not the address is registered — a
        lockout that only happens for real accounts tells an attacker which
        addresses exist.
        """
        now = now or datetime.now(timezone.utc)
        row = self._db.execute(
            "SELECT locked_until FROM login_attempts WHERE key = ?",
            (_attempt_key(email),),
        ).fetchone()
        if row is None or not row["locked_until"]:
            return 0
        try:
            locked_until = datetime.fromisoformat(row["locked_until"])
        except ValueError:
            return 0
        remaining = (locked_until - now).total_seconds()
        return max(0, int(remaining))

    def record_login_failure(
        self, email: str, *, now: Optional[datetime] = None
    ) -> int:
        """Count a failed attempt and lock out once the threshold is crossed.

        The counter resets after a quiet period rather than accumulating
        forever, so a typo last month plus a typo today is not a lockout.
        Returns the remaining lockout in seconds (0 when still allowed).
        """
        now = now or datetime.now(timezone.utc)
        key = _attempt_key(email)
        row = self._db.execute(
            "SELECT failures, first_failure_at FROM login_attempts WHERE key = ?",
            (key,),
        ).fetchone()

        failures = 1
        first_failure_at = now
        if row is not None:
            try:
                previous_start = datetime.fromisoformat(row["first_failure_at"])
            except ValueError:
                previous_start = now
            within_window = (
                now - previous_start
            ).total_seconds() <= FAILURE_WINDOW_SECONDS
            if within_window:
                failures = int(row["failures"]) + 1
                first_failure_at = previous_start

        locked_until = ""
        if failures >= MAX_LOGIN_FAILURES:
            locked_until = (now + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()

        self._db.execute(
            "INSERT INTO login_attempts (key, failures, first_failure_at,"
            " locked_until) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET failures = excluded.failures,"
            " first_failure_at = excluded.first_failure_at,"
            " locked_until = excluded.locked_until",
            (key, failures, first_failure_at.isoformat(), locked_until or None),
        )
        self._db.commit()
        return LOCKOUT_SECONDS if locked_until else 0

    def clear_login_failures(self, email: str) -> None:
        """Forget an address's failures — called on every successful login."""
        self._db.execute(
            "DELETE FROM login_attempts WHERE key = ?", (_attempt_key(email),)
        )
        self._db.commit()

    def get_user(self, user_id: str) -> Optional[User]:
        """Look up a user by id."""
        row = self._db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def update_user(self, user_id: str, **fields: Any) -> Optional[User]:
        """Update mutable profile fields (name, timezone, currency, locale)."""
        allowed = ("name", "timezone", "currency", "locale")
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_user(user_id)
        assignments = ", ".join(f"{col} = ?" for col in updates)
        self._db.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            (*updates.values(), user_id),
        )
        self._db.commit()
        return self.get_user(user_id)

    def count_users(self) -> int:
        """Total registered clients — used to gate first-run bootstrap."""
        row = self._db.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) if row else 0

    # -- Tokens --------------------------------------------------------------

    def issue_token(
        self,
        user_id: str,
        *,
        label: str = "",
        ttl_days: int = DEFAULT_TOKEN_TTL_DAYS,
    ) -> str:
        """Mint a bearer token and return the plaintext — the only time it exists.

        Only the hash is persisted, so a lost token cannot be recovered, only
        replaced.
        """
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        self._db.execute(
            "INSERT INTO auth_tokens (token_hash, user_id, label, created_at,"
            " expires_at) VALUES (?, ?, ?, ?, ?)",
            (_hash_token(token), user_id, label, _now(), expires),
        )
        self._db.commit()
        return token

    def resolve_token(self, token: str) -> Optional[str]:
        """Return the ``user_id`` for a live token, else ``None``.

        Expired tokens are deleted on sight rather than merely rejected, so the
        table does not accumulate dead rows for every phone a client ever used.
        """
        if not token:
            return None
        token_hash = _hash_token(token)
        row = self._db.execute(
            "SELECT user_id, expires_at FROM auth_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        expires_at = row["expires_at"]
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                    self.revoke_token(token)
                    return None
            except ValueError:
                # An unparseable expiry is a corrupt row, not a valid session.
                self.revoke_token(token)
                return None
        self._db.execute(
            "UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now(), token_hash),
        )
        self._db.commit()
        return str(row["user_id"])

    def revoke_token(self, token: str) -> bool:
        """Invalidate a single token (logout). Returns whether one was removed."""
        cur = self._db.execute(
            "DELETE FROM auth_tokens WHERE token_hash = ?", (_hash_token(token),)
        )
        self._db.commit()
        return cur.rowcount > 0

    def revoke_all_tokens(self, user_id: str) -> int:
        """Invalidate every session for a user (lost device). Returns the count."""
        cur = self._db.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
        self._db.commit()
        return cur.rowcount

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Active sessions for a user, newest first. Never exposes token hashes."""
        rows = self._db.execute(
            "SELECT label, created_at, last_used_at, expires_at FROM auth_tokens"
            " WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the connection when this store owns it."""
        if self._owns_db:
            self._db.close()

    @staticmethod
    def _row_to_user(row: Any) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            timezone=row["timezone"],
            currency=row["currency"],
            locale=row["locale"],
            created_at=row["created_at"],
        )
