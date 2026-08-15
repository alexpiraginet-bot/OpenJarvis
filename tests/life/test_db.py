"""Tests for the dual-backend database layer.

The translation tests are the load-bearing ones: they run without PostgreSQL
installed, and they are what stops a `?` inside a string literal from being
mangled on the way to psycopg.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from openjarvis.life.db import (
    POSTGRES,
    SQLITE,
    Database,
    configured_database_target,
    connect,
    detect_backend,
    normalize_postgres_dsn,
    translate,
)

# -- Backend detection -------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "postgres://user:pw@host/db",
        "postgresql://user:pw@host/db",
        "postgresql+psycopg://user:pw@host/db",
    ],
)
def test_dsns_are_recognised_as_postgres(target):
    assert detect_backend(target) == POSTGRES


@pytest.mark.parametrize("target", ["/tmp/life.db", "life.db", "./data/life.db", ""])
def test_paths_are_recognised_as_sqlite(target):
    assert detect_backend(target) == SQLITE


def test_serverless_database_target_prefers_transaction_pooler(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_LIFE_DB", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://direct.example/postgres")
    monkeypatch.setenv(
        "POSTGRES_PRISMA_URL", "postgres://transaction-pooler.example/postgres"
    )

    assert configured_database_target() == (
        "postgres://transaction-pooler.example/postgres"
    )


def test_explicit_database_target_overrides_provider_urls(monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_DB", "postgres://explicit.example/postgres")
    monkeypatch.setenv(
        "POSTGRES_PRISMA_URL", "postgres://transaction-pooler.example/postgres"
    )

    assert configured_database_target() == "postgres://explicit.example/postgres"


def test_normalize_postgres_dsn_removes_provider_metadata():
    dsn = (
        "postgres://user:secret@pooler.example:6543/postgres"
        "?sslmode=require&supa=base-pooler.x&pgbouncer=true"
    )

    assert normalize_postgres_dsn(dsn) == (
        "postgres://user:secret@pooler.example:6543/postgres?sslmode=require"
    )


# -- Placeholder translation -------------------------------------------------


def test_placeholders_become_psycopg_style():
    assert translate("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_a_question_mark_inside_a_literal_is_left_alone():
    """Otherwise a stored string like 'e aí?' would corrupt the statement."""
    assert translate("SELECT * FROM t WHERE a = 'e aí?' AND b = ?") == (
        "SELECT * FROM t WHERE a = 'e aí?' AND b = %s"
    )


def test_literal_percent_is_escaped():
    """psycopg reads a bare % as a placeholder marker."""
    assert translate("SELECT '100%' FROM t") == "SELECT '100%%' FROM t"


def test_escaped_quotes_inside_a_literal_do_not_end_it():
    sql = "SELECT * FROM t WHERE a = 'it''s ok?' AND b = ?"
    assert translate(sql) == "SELECT * FROM t WHERE a = 'it''s ok?' AND b = %s"


def test_translation_leaves_plain_sql_untouched():
    sql = "CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY)"
    assert translate(sql) == sql


# -- SQLite behaviour --------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    database = connect(str(tmp_path / "t.db"))
    database.execute("CREATE TABLE t (id TEXT PRIMARY KEY, n INTEGER, s TEXT)")
    database.commit()
    yield database
    database.close()


def test_sqlite_round_trip(db):
    db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("a", 1, "um"))
    db.commit()
    row = db.execute("SELECT * FROM t WHERE id = ?", ("a",)).fetchone()
    assert row["n"] == 1
    assert row["s"] == "um"


def test_read_result_is_buffered_before_a_later_commit(db):
    """A concurrent request must not invalidate an already-returned result."""
    db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("a", 1, "um"))
    db.commit()

    result = db.execute("SELECT * FROM t ORDER BY id")
    db.execute("UPDATE t SET n = ? WHERE id = ?", (2, "a"))
    db.commit()

    row = result.fetchone()
    assert row["id"] == "a"
    assert row["n"] == 1
    assert result.fetchone() is None


def test_rowcount_is_available(db):
    db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("a", 1, "um"))
    db.commit()
    cursor = db.execute("DELETE FROM t WHERE id = ?", ("a",))
    assert cursor.rowcount == 1


def test_executemany_inserts_every_row(db):
    db.executemany(
        "INSERT INTO t (id, n, s) VALUES (?, ?, ?)",
        [("a", 1, "um"), ("b", 2, "dois")],
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) AS c FROM t").fetchone()["c"] == 2


def test_executemany_with_no_rows_is_a_no_op(db):
    assert db.executemany("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", []) is None


def test_portable_upsert_runs_on_sqlite(db):
    """`ON CONFLICT DO NOTHING` must work on both backends; check the one here."""
    db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("a", 1, "um"))
    db.execute(
        "INSERT INTO t (id, n, s) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        ("a", 99, "outro"),
    )
    db.commit()
    assert db.execute("SELECT n FROM t WHERE id = ?", ("a",)).fetchone()["n"] == 1


def test_portable_upsert_with_update_runs_on_sqlite(db):
    db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("a", 1, "um"))
    db.execute(
        "INSERT INTO t (id, n, s) VALUES (?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET n = excluded.n",
        ("a", 42, "um"),
    )
    db.commit()
    assert db.execute("SELECT n FROM t WHERE id = ?", ("a",)).fetchone()["n"] == 42


def test_rollback_discards_the_transaction(db):
    db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("a", 1, "um"))
    db.rollback()
    assert db.execute("SELECT COUNT(*) AS c FROM t").fetchone()["c"] == 0


def test_caught_nested_transaction_failure_rolls_back_to_savepoint(db):
    with db.transaction():
        db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("outer", 1, "ok"))
        try:
            with db.transaction():
                db.execute(
                    "INSERT INTO t (id, n, s) VALUES (?, ?, ?)",
                    ("inner", 2, "rollback"),
                )
                raise ValueError("domain failure")
        except ValueError:
            pass
        db.execute("INSERT INTO t (id, n, s) VALUES (?, ?, ?)", ("after", 3, "ok"))

    rows = db.execute("SELECT id FROM t ORDER BY id").fetchall()
    assert [row["id"] for row in rows] == ["after", "outer"]


def test_sqlite_backend_is_reported(db):
    assert db.backend == SQLITE


def test_connect_creates_missing_parent_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "life.db"
    database = Database(str(nested))
    database.close()
    assert nested.exists()


def test_missing_psycopg_raises_a_actionable_error(monkeypatch):
    """A hosted deploy without the extra should say which extra to install."""
    import builtins

    from openjarvis.life.db import DatabaseError

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("no psycopg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(DatabaseError, match="life-postgres"):
        Database("postgres://user:pw@host/db")


def test_postgres_executescript_does_not_commit_an_enclosing_transaction():
    """A schema failure after DDL must roll the whole PostgreSQL migration back."""

    class _Cursor:
        description = None

        def execute(self, _statement):
            return None

    class _Connection:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0

        @contextmanager
        def pipeline(self):
            yield

        def cursor(self):
            return _Cursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    connection = _Connection()
    database = object.__new__(Database)
    database._backend = POSTGRES
    database._lock = threading.RLock()
    database._transaction_depth = 0
    database._conn = connection

    with database.transaction():
        database.executescript(["CREATE TABLE example (id TEXT)"])
        assert connection.commits == 0

    assert connection.commits == 1
