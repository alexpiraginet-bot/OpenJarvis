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
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.life.schema import ensure_schema

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

#: Tokens outlive a phone sitting in a pocket for a weekend but not a stolen
#: device forever. Clients refresh by logging in again.
DEFAULT_TOKEN_TTL_DAYS = 90

MIN_PASSWORD_LENGTH = 8


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


class UserStore:
    """SQLite-backed registry of users and their bearer tokens."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Open (or adopt) the Life database and ensure the schema exists.

        Passing ``conn`` lets :class:`~openjarvis.life.store.LifeStore` share a
        single connection so identity and domain data live in one file and one
        transaction scope.
        """
        self._db_path = str(db_path)
        self._owns_conn = conn is None
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
        self._conn = conn
        ensure_schema(self._conn)

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection, for stores that share this database."""
        return self._conn

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
            self._conn.execute(
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
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AuthError("Email is already registered") from exc
        return user

    def authenticate(self, email: str, password: str) -> Optional[User]:
        """Return the matching user, or ``None`` when credentials are wrong.

        Callers must not distinguish "no such email" from "bad password" in
        their responses — that difference is a user-enumeration oracle.
        """
        row = self._conn.execute(
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

    def get_user(self, user_id: str) -> Optional[User]:
        """Look up a user by id."""
        row = self._conn.execute(
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
        self._conn.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            (*updates.values(), user_id),
        )
        self._conn.commit()
        return self.get_user(user_id)

    def count_users(self) -> int:
        """Total registered clients — used to gate first-run bootstrap."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
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
        self._conn.execute(
            "INSERT INTO auth_tokens (token_hash, user_id, label, created_at,"
            " expires_at) VALUES (?, ?, ?, ?, ?)",
            (_hash_token(token), user_id, label, _now(), expires),
        )
        self._conn.commit()
        return token

    def resolve_token(self, token: str) -> Optional[str]:
        """Return the ``user_id`` for a live token, else ``None``.

        Expired tokens are deleted on sight rather than merely rejected, so the
        table does not accumulate dead rows for every phone a client ever used.
        """
        if not token:
            return None
        token_hash = _hash_token(token)
        row = self._conn.execute(
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
        self._conn.execute(
            "UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now(), token_hash),
        )
        self._conn.commit()
        return str(row["user_id"])

    def revoke_token(self, token: str) -> bool:
        """Invalidate a single token (logout). Returns whether one was removed."""
        cur = self._conn.execute(
            "DELETE FROM auth_tokens WHERE token_hash = ?", (_hash_token(token),)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def revoke_all_tokens(self, user_id: str) -> int:
        """Invalidate every session for a user (lost device). Returns the count."""
        cur = self._conn.execute(
            "DELETE FROM auth_tokens WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()
        return cur.rowcount

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Active sessions for a user, newest first. Never exposes token hashes."""
        rows = self._conn.execute(
            "SELECT label, created_at, last_used_at, expires_at FROM auth_tokens"
            " WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the connection when this store owns it."""
        if self._owns_conn:
            self._conn.close()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            timezone=row["timezone"],
            currency=row["currency"],
            locale=row["locale"],
            created_at=row["created_at"],
        )
