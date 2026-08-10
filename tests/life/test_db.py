"""Tests for the dual-backend database layer.

The translation tests are the load-bearing ones: they run without PostgreSQL
installed, and they are what stops a `?` inside a string literal from being
mangled on the way to psycopg.
"""

from __future__ import annotations

import pytest

from openjarvis.life.db import (
    POSTGRES,
    SQLITE,
    Database,
    connect,
    detect_backend,
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
