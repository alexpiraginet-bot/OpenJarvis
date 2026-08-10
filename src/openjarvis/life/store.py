"""Tenant-scoped CRUD over the Life OS tables.

The store is deliberately generic. Fifteen tables with hand-written
``insert_transaction`` / ``update_transaction`` / ``list_transactions`` triplets
would be ~1500 lines of near-identical SQL, and every one of them a place to
forget the ``user_id`` predicate. Instead there is a single implementation that
takes the tenant as a *required argument* and validates table and column names
against :data:`~openjarvis.life.schema.SCHEMA` before they touch SQL.

That validation is what makes string interpolation safe here: identifiers can
never come from request data, only from the schema allow-list, while every
value is bound as a parameter.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from openjarvis.life.schema import SCHEMA, TableSpec, ensure_schema

#: Operators a caller may use in a filter. Anything else is rejected rather
#: than passed through, so no fragment of a query is attacker-controlled.
_ALLOWED_OPS = frozenset(
    {"=", "!=", "<", "<=", ">", ">=", "LIKE", "IN", "IS NULL", "IS NOT NULL"}
)

#: Columns the store owns. Payloads may never write them directly.
_RESERVED = frozenset({"id", "user_id", "created_at"})


class LifeStoreError(ValueError):
    """Raised when a table, column or operator is not in the allow-list."""


@dataclass(frozen=True, slots=True)
class Filter:
    """A validated ``WHERE`` predicate: ``(column, operator, value)``."""

    column: str
    op: str = "="
    value: Any = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spec(table: str) -> TableSpec:
    spec = SCHEMA.get(table)
    if spec is None:
        raise LifeStoreError(f"Unknown table: {table!r}")
    return spec


def _check_column(spec: TableSpec, column: str, *, readable: bool = False) -> str:
    """Validate a column name against the schema allow-list.

    ``readable=True`` also admits the store-owned columns, which are legal to
    filter and sort on even though they cannot be written.
    """
    allowed: Iterable[str] = spec.columns
    if readable:
        allowed = (*spec.columns, *_RESERVED)
    if column not in allowed:
        raise LifeStoreError(f"Unknown column {column!r} on table {spec.name!r}")
    return column


class LifeStore:
    """SQLite CRUD for every Life OS domain table, always scoped to one user."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Open (or adopt) the Life database and ensure the schema exists."""
        self._db_path = str(db_path)
        self._owns_conn = conn is None
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # WAL keeps the API responsive while a scheduled agent writes:
            # readers no longer block behind a writer. Mirrors the fix applied
            # to TelemetryStore for SQLITE_BUSY under concurrency.
            conn.execute("PRAGMA journal_mode=WAL")
        self._conn = conn
        ensure_schema(self._conn)

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection, for stores sharing this database."""
        return self._conn

    # -- Writes --------------------------------------------------------------

    def insert(self, table: str, user_id: str, data: Dict[str, Any]) -> str:
        """Insert a row for ``user_id`` and return its generated id.

        Unknown keys raise rather than being dropped: a client that sends
        ``ammount_cents`` deserves a 400, not a silently empty column.
        """
        spec = _spec(table)
        columns = [_check_column(spec, key) for key in data]
        record_id = uuid.uuid4().hex
        placeholders = ", ".join("?" for _ in range(len(columns) + 3))
        column_sql = ", ".join(("id", "user_id", *columns, "created_at"))
        self._conn.execute(
            f"INSERT INTO {spec.name} ({column_sql}) VALUES ({placeholders})",
            (record_id, user_id, *data.values(), _now()),
        )
        self._conn.commit()
        return record_id

    def update(
        self, table: str, user_id: str, record_id: str, data: Dict[str, Any]
    ) -> bool:
        """Patch a row. Returns ``False`` when it does not exist for this user.

        The ``user_id`` predicate makes a cross-tenant update a no-op rather
        than an error — there is nothing to leak either way.
        """
        spec = _spec(table)
        if not data:
            return self.get(table, user_id, record_id) is not None
        columns = [_check_column(spec, key) for key in data]
        assignments = ", ".join(f"{col} = ?" for col in columns)
        cur = self._conn.execute(
            f"UPDATE {spec.name} SET {assignments} WHERE id = ? AND user_id = ?",
            (*data.values(), record_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete(self, table: str, user_id: str, record_id: str) -> bool:
        """Delete a row. Returns whether one was removed."""
        spec = _spec(table)
        cur = self._conn.execute(
            f"DELETE FROM {spec.name} WHERE id = ? AND user_id = ?",
            (record_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_where(self, table: str, user_id: str, filters: Sequence[Filter]) -> int:
        """Delete every matching row for this user. Returns the count."""
        spec = _spec(table)
        where_sql, params = self._build_where(spec, user_id, filters)
        cur = self._conn.execute(f"DELETE FROM {spec.name} {where_sql}", params)
        self._conn.commit()
        return cur.rowcount

    # -- Reads ---------------------------------------------------------------

    def get(self, table: str, user_id: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one row by id, scoped to the user."""
        spec = _spec(table)
        row = self._conn.execute(
            f"SELECT * FROM {spec.name} WHERE id = ? AND user_id = ?",
            (record_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def list_records(
        self,
        table: str,
        user_id: str,
        *,
        filters: Sequence[Filter] = (),
        order_by: str = "created_at",
        descending: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List rows for a user, filtered and ordered."""
        spec = _spec(table)
        where_sql, params = self._build_where(spec, user_id, filters)
        order_col = _check_column(spec, order_by, readable=True)
        direction = "DESC" if descending else "ASC"
        rows = self._conn.execute(
            f"SELECT * FROM {spec.name} {where_sql}"
            f" ORDER BY {order_col} {direction} LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 1000)), max(0, offset)),
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self, table: str, user_id: str, *, filters: Sequence[Filter] = ()) -> int:
        """Count matching rows for a user."""
        spec = _spec(table)
        where_sql, params = self._build_where(spec, user_id, filters)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM {spec.name} {where_sql}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def sum_column(
        self,
        table: str,
        user_id: str,
        column: str,
        *,
        filters: Sequence[Filter] = (),
    ) -> int:
        """Sum a numeric column for a user. Returns 0 when nothing matches."""
        spec = _spec(table)
        col = _check_column(spec, column, readable=True)
        where_sql, params = self._build_where(spec, user_id, filters)
        row = self._conn.execute(
            f"SELECT COALESCE(SUM({col}), 0) AS total FROM {spec.name} {where_sql}",
            params,
        ).fetchone()
        return int(row["total"]) if row else 0

    def group_sum(
        self,
        table: str,
        user_id: str,
        group_column: str,
        sum_column: str,
        *,
        filters: Sequence[Filter] = (),
    ) -> List[Dict[str, Any]]:
        """Sum a column grouped by another — spend by category, and friends."""
        spec = _spec(table)
        group_col = _check_column(spec, group_column, readable=True)
        value_col = _check_column(spec, sum_column, readable=True)
        where_sql, params = self._build_where(spec, user_id, filters)
        rows = self._conn.execute(
            f"SELECT {group_col} AS label, COALESCE(SUM({value_col}), 0) AS total"
            f" FROM {spec.name} {where_sql} GROUP BY {group_col}"
            " ORDER BY total DESC",
            params,
        ).fetchall()
        return [{"label": row["label"], "total": int(row["total"])} for row in rows]

    # -- Internals -----------------------------------------------------------

    @staticmethod
    def _build_where(
        spec: TableSpec, user_id: str, filters: Sequence[Filter]
    ) -> Tuple[str, List[Any]]:
        """Compose a WHERE clause that always leads with the tenant predicate."""
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]
        for flt in filters:
            column = _check_column(spec, flt.column, readable=True)
            op = flt.op.upper() if flt.op.upper() in _ALLOWED_OPS else flt.op
            if op not in _ALLOWED_OPS:
                raise LifeStoreError(f"Unsupported operator: {flt.op!r}")
            if op in ("IS NULL", "IS NOT NULL"):
                clauses.append(f"{column} {op}")
                continue
            if op == "IN":
                values = list(flt.value or [])
                if not values:
                    # An empty IN () is a syntax error in SQLite; an impossible
                    # predicate is the honest translation of "match nothing".
                    clauses.append("1 = 0")
                    continue
                marks = ", ".join("?" for _ in values)
                clauses.append(f"{column} IN ({marks})")
                params.extend(values)
                continue
            clauses.append(f"{column} {op} ?")
            params.append(flt.value)
        return "WHERE " + " AND ".join(clauses), params

    def close(self) -> None:
        """Close the connection when this store owns it."""
        if self._owns_conn:
            self._conn.close()
