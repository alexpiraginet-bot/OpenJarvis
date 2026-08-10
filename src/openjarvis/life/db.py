"""One database interface over SQLite and PostgreSQL.

The Life OS runs in two very different places. On a developer's machine and in
the test suite it is a single SQLite file: no server, no fixtures, a full run
in fifteen seconds. Hosted for real clients it is PostgreSQL, because a
serverless deployment has no durable local disk — on Vercel each invocation may
land on a different instance with its own empty `/tmp`, so a SQLite-backed
login would succeed and the very next request would 401.

Rather than fork the store, this module normalises the two drivers to one
narrow surface. The rest of the package writes ordinary SQL with ``?``
placeholders and never learns which backend it is talking to.

What actually differs, and how each is handled:

* **Placeholders** — SQLite takes ``?``, psycopg takes ``%s``. Translated here,
  quote-aware, so a ``?`` inside a string literal is left alone.
* **Literal percent signs** — harmless to SQLite, a placeholder to psycopg.
  Doubled during translation.
* **Row access** — ``sqlite3.Row`` and psycopg's ``dict_row`` both support
  ``row["column"]``, so call sites need no change.
* **Upserts** — every statement in this package uses the portable
  ``ON CONFLICT ... DO NOTHING/DO UPDATE`` form, supported by SQLite since
  3.24 and by PostgreSQL since 9.5. SQLite's ``INSERT OR IGNORE`` is
  deliberately not used.
* **DDL** — the schema sticks to ``TEXT``/``INTEGER``/``REAL`` and text primary
  keys, which mean the same thing in both. No ``AUTOINCREMENT``, no ``SERIAL``.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence
from urllib.parse import unquote_plus, urlsplit, urlunsplit

SQLITE = "sqlite"
POSTGRES = "postgres"

#: URL schemes that mean "this is a PostgreSQL DSN, not a file path".
_POSTGRES_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg://")
_NON_LIBPQ_QUERY_PARAMETERS = frozenset({"pgbouncer", "supa"})


class DatabaseError(RuntimeError):
    """Raised when a database cannot be opened or its driver is missing."""


class _BufferedCursor:
    """Cursor-compatible, already-fetched result for concurrent readers.

    ``Database`` deliberately shares one driver connection across the Life
    stores.  Returning a live cursor after releasing the connection lock lets
    another request commit while the first request is still fetching, which
    can make a valid auth token intermittently look absent on SQLite and can
    corrupt cursor state on PostgreSQL.  Read results are therefore consumed
    while the lock is held and exposed through this small cursor facade.
    """

    def __init__(self, cursor: Any) -> None:
        self.rowcount = cursor.rowcount
        self.description = cursor.description
        self.lastrowid = getattr(cursor, "lastrowid", None)
        self._rows = list(cursor.fetchall())
        self._index = 0
        close = getattr(cursor, "close", None)
        if callable(close):
            close()

    def fetchone(self) -> Any:
        """Return the next buffered row, matching DB-API cursor semantics."""
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchmany(self, size: int = 1) -> list[Any]:
        """Return at most ``size`` remaining rows."""
        stop = min(len(self._rows), self._index + max(0, size))
        rows = self._rows[self._index : stop]
        self._index = stop
        return rows

    def fetchall(self) -> list[Any]:
        """Return every remaining row."""
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def __iter__(self) -> "_BufferedCursor":
        return self

    def __next__(self) -> Any:
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


def detect_backend(target: str) -> str:
    """Classify a connection target as a Postgres DSN or a SQLite path."""
    return POSTGRES if target.startswith(_POSTGRES_SCHEMES) else SQLITE


def configured_database_target() -> str:
    """Return the durable database configured for the current runtime.

    Supabase exposes both direct/session and transaction-pooled URLs through
    its Vercel integration. Serverless functions must prefer the transaction
    pooler: concurrent cold starts otherwise consume one database session each
    and direct IPv6 connectivity is not available from every runtime.
    """
    import os

    return (
        os.environ.get("OPENJARVIS_LIFE_DB")
        or os.environ.get("POSTGRES_PRISMA_URL")
        or os.environ.get("POSTGRES_URL", "")
    )


def normalize_postgres_dsn(dsn: str) -> str:
    """Remove provider metadata that libpq/psycopg cannot parse.

    Supabase's Vercel integration appends ``supa=...`` to its pooled URL and
    its Prisma URL may append ``pgbouncer=true``. Both are hints for client
    libraries, not PostgreSQL connection parameters; psycopg rejects them as
    unknown. Every other URL component is preserved byte-for-byte.
    """
    parsed = urlsplit(dsn)
    if not parsed.query:
        return dsn
    query_parts = [
        part
        for part in parsed.query.split("&")
        if unquote_plus(part.partition("=")[0]) not in _NON_LIBPQ_QUERY_PARAMETERS
    ]
    return urlunsplit(parsed._replace(query="&".join(query_parts)))


def translate(sql: str) -> str:
    """Rewrite ``?`` placeholders to ``%s`` for psycopg, skipping literals.

    A blanket ``str.replace`` would corrupt any ``?`` inside a quoted string,
    and would leave a literal ``%`` looking like a placeholder to the driver.
    Both are handled in one pass.
    """
    out = []
    in_literal = False
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "%":
            # Doubled whether or not it sits inside a literal: psycopg
            # interpolates before the SQL is ever parsed, so a bare `%` in
            # 'desconto de 100%' is read as a placeholder marker just the same.
            out.append("%%")
        elif char == "'":
            # '' inside a literal is an escaped quote, not the end of one.
            if in_literal and index + 1 < length and sql[index + 1] == "'":
                out.append("''")
                index += 2
                continue
            in_literal = not in_literal
            out.append(char)
        elif in_literal:
            # A `?` here is data, not a placeholder — leave it be.
            out.append(char)
        elif char == "?":
            out.append("%s")
        else:
            out.append(char)
        index += 1
    return "".join(out)


class Database:
    """A connection that speaks one SQL dialect to two drivers."""

    def __init__(self, target: str) -> None:
        """Open ``target``: a Postgres DSN, or a filesystem path for SQLite."""
        self._target = target
        self._backend = detect_backend(target)
        self._lock = threading.RLock()
        self._transaction_depth = 0
        if self._backend == POSTGRES:
            self._conn = self._connect_postgres(target)
        else:
            self._conn = self._connect_sqlite(target)

    # -- Construction --------------------------------------------------------

    @staticmethod
    def _connect_sqlite(path: str) -> Any:
        resolved = Path(path)
        if resolved.parent and str(resolved.parent) not in ("", "."):
            resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(resolved), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL keeps the API responsive while a scheduled job writes: readers
        # no longer queue behind a writer.
        conn.execute("PRAGMA journal_mode=WAL")
        # SQLite does not enforce foreign keys unless asked, and silent
        # referential drift in a financial ledger is not worth the microsecond.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _connect_postgres(dsn: str) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise DatabaseError(
                "PostgreSQL support needs psycopg. Install the extra: "
                "`uv sync --extra life-postgres`."
            ) from exc
        # autocommit=False keeps explicit commit() meaningful, matching the
        # SQLite path so callers behave identically on both.
        return psycopg.connect(
            normalize_postgres_dsn(dsn),
            row_factory=dict_row,
            autocommit=False,
            connect_timeout=5,
            # Supavisor transaction mode cannot rely on a server-side prepared
            # statement surviving on the same backend connection.
            prepare_threshold=None,
        )

    # -- Properties ----------------------------------------------------------

    @property
    def backend(self) -> str:
        """``"sqlite"`` or ``"postgres"``."""
        return self._backend

    @property
    def raw(self) -> Any:
        """The underlying driver connection, for code that must know."""
        return self._conn

    # -- Queries -------------------------------------------------------------

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize work that must keep one shared connection coherent."""
        with self._lock:
            yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Keep nested domain writes inside one atomic transaction."""
        with self._lock:
            outermost = self._transaction_depth == 0
            if outermost and self._backend == SQLITE:
                self._conn.execute("BEGIN IMMEDIATE")
            self._transaction_depth += 1
            try:
                yield
            except Exception:
                self._transaction_depth -= 1
                if outermost:
                    self._conn.rollback()
                raise
            else:
                self._transaction_depth -= 1
                if outermost:
                    self._conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Run a statement and return a cursor.

        Row-producing statements are fully buffered before the connection lock
        is released.  The returned object still supports ``fetchone``,
        ``fetchmany``, ``fetchall``, iteration and ``rowcount`` on both
        backends, and rows are subscriptable by column name on both.
        """
        with self._lock:
            if self._backend == POSTGRES:
                cursor = self._conn.cursor()
                cursor.execute(translate(sql), tuple(params))
            else:
                cursor = self._conn.execute(sql, tuple(params))
            if cursor.description is not None:
                return _BufferedCursor(cursor)
            return cursor

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> Any:
        """Run a statement once per parameter tuple."""
        rows = [tuple(item) for item in seq]
        if not rows:
            return None
        with self._lock:
            if self._backend == POSTGRES:
                cursor = self._conn.cursor()
                cursor.executemany(translate(sql), rows)
                return cursor
            return self._conn.executemany(sql, rows)

    def executescript(self, statements: Iterable[str]) -> None:
        """Run a sequence of DDL statements, then commit."""
        script = list(statements)
        if not script:
            return
        if self._backend == POSTGRES:
            # Each cold start may need dozens of idempotent schema statements.
            # Pipeline them so a Vercel function in one region doesn't pay one
            # full network round trip to Supabase in another for every table.
            with self._lock, self._conn.pipeline():
                for statement in script:
                    cursor = self._conn.cursor()
                    cursor.execute(translate(statement))
                self._conn.commit()
            return
        for statement in script:
            self.execute(statement)
        self.commit()

    def commit(self) -> None:
        """Commit the open transaction."""
        with self._lock:
            if self._transaction_depth == 0:
                self._conn.commit()

    def rollback(self) -> None:
        """Discard the open transaction."""
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()


def connect(target: Optional[str] = None) -> Database:
    """Open the Life database.

    ``target`` may be a PostgreSQL DSN or a SQLite path. When omitted, the
    ``OPENJARVIS_LIFE_DB`` environment variable is used, falling back to a file
    under the OpenJarvis data directory — which is what makes `run-life.sh`
    and the test suite work with no configuration at all.
    """
    resolved = target or configured_database_target()
    if not resolved:
        from openjarvis.core.paths import get_data_dir

        resolved = str(get_data_dir() / "life.db")
    return Database(resolved)


__all__ = [
    "POSTGRES",
    "SQLITE",
    "Database",
    "DatabaseError",
    "configured_database_target",
    "connect",
    "detect_backend",
    "normalize_postgres_dsn",
    "translate",
]
