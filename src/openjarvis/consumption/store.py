"""SQLite store for product specs, stock readings, and replenishments.

Local-only, following the same pattern as the footfall and cold-chain stores:
readings dedupe on a deterministic id so a scale or camera resending a batch
cannot inflate consumption, while product specs are upserted because a portion
standard gets revised.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openjarvis.consumption.types import ProductSpec, Replenishment, StockReading

_CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS consumption_products (
    product_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT '',
    portion_grams REAL,
    site_id       TEXT NOT NULL DEFAULT '',
    metadata      TEXT NOT NULL DEFAULT '{}',
    updated_at    REAL NOT NULL
);
"""

_CREATE_READINGS_TABLE = """
CREATE TABLE IF NOT EXISTS consumption_readings (
    reading_id TEXT PRIMARY KEY,
    ts         REAL NOT NULL,
    product_id TEXT NOT NULL,
    grams      REAL NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'spot',
    source     TEXT NOT NULL DEFAULT 'scale',
    site_id    TEXT NOT NULL DEFAULT '',
    metadata   TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
"""

_CREATE_REPLENISHMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS consumption_replenishments (
    replenishment_id TEXT PRIMARY KEY,
    ts               REAL NOT NULL,
    product_id       TEXT NOT NULL,
    grams            REAL NOT NULL,
    site_id          TEXT NOT NULL DEFAULT '',
    metadata         TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL
);
"""

_CREATE_INDEXES = (
    """CREATE INDEX IF NOT EXISTS idx_consumption_readings_product_ts
       ON consumption_readings(product_id, ts);""",
    "CREATE INDEX IF NOT EXISTS idx_consumption_readings_ts"
    " ON consumption_readings(ts);",
    """CREATE INDEX IF NOT EXISTS idx_consumption_refills_product_ts
       ON consumption_replenishments(product_id, ts);""",
)


class ConsumptionStore:
    """Local SQLite store for consumption data."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_PRODUCTS_TABLE)
            self._conn.execute(_CREATE_READINGS_TABLE)
            self._conn.execute(_CREATE_REPLENISHMENTS_TABLE)
            for statement in _CREATE_INDEXES:
                self._conn.execute(statement)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "ConsumptionStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- Products -----------------------------------------------------------

    def register_product(self, spec: ProductSpec) -> None:
        """Insert or update a product spec."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO consumption_products
                    (product_id, name, category, portion_grams, site_id,
                     metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    portion_grams = excluded.portion_grams,
                    site_id = excluded.site_id,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                """,
                (
                    spec.product_id,
                    spec.name,
                    spec.category,
                    spec.portion_grams,
                    spec.site_id,
                    json.dumps(spec.metadata, ensure_ascii=False),
                    time.time(),
                ),
            )

    def products(self, *, site_id: Optional[str] = None) -> Dict[str, ProductSpec]:
        """Return registered products keyed by id, optionally filtered by site."""
        if site_id:
            rows = self._conn.execute(
                "SELECT * FROM consumption_products WHERE site_id = ?"
                " ORDER BY product_id",
                (site_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM consumption_products ORDER BY product_id"
            ).fetchall()

        return {
            row["product_id"]: ProductSpec(
                product_id=row["product_id"],
                name=row["name"],
                category=row["category"],
                portion_grams=row["portion_grams"],
                site_id=row["site_id"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        }

    # -- Readings -----------------------------------------------------------

    def record_readings(self, readings: Iterable[StockReading]) -> int:
        """Insert *readings*, ignoring ones already stored."""
        rows = [
            (
                reading.reading_id,
                reading.epoch,
                reading.product_id,
                reading.grams,
                reading.kind,
                reading.source,
                reading.site_id,
                json.dumps(reading.metadata, ensure_ascii=False),
                time.time(),
            )
            for reading in readings
        ]
        if not rows:
            return 0

        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO consumption_readings
                    (reading_id, ts, product_id, grams, kind, source, site_id,
                     metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self._conn.total_changes - before

    def record_reading(self, reading: StockReading) -> int:
        """Insert one reading.  Returns 1 if written, 0 if already present."""
        return self.record_readings([reading])

    def record_replenishments(self, replenishments: Iterable[Replenishment]) -> int:
        """Insert *replenishments*, ignoring ones already stored."""
        rows = [
            (
                refill.replenishment_id,
                refill.epoch,
                refill.product_id,
                refill.grams,
                refill.site_id,
                json.dumps(refill.metadata, ensure_ascii=False),
                time.time(),
            )
            for refill in replenishments
        ]
        if not rows:
            return 0

        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO consumption_replenishments
                    (replenishment_id, ts, product_id, grams, site_id, metadata,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self._conn.total_changes - before

    def _scope(
        self,
        start: Optional[datetime],
        end: Optional[datetime],
        product_id: Optional[str],
        site_id: Optional[str],
    ) -> tuple[str, List[Any]]:
        """Build a shared WHERE clause."""
        clauses: List[str] = []
        params: List[Any] = []
        if product_id:
            clauses.append("product_id = ?")
            params.append(product_id)
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start.timestamp())
        if end is not None:
            clauses.append("ts <= ?")
            params.append(end.timestamp())
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def readings(
        self,
        *,
        product_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        site_id: Optional[str] = None,
    ) -> List[StockReading]:
        """Return stock readings in the range, oldest first."""
        where, params = self._scope(start, end, product_id, site_id)
        rows = self._conn.execute(
            f"SELECT * FROM consumption_readings {where} ORDER BY ts ASC", params
        ).fetchall()

        return [
            StockReading(
                timestamp=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
                product_id=row["product_id"],
                grams=row["grams"],
                kind=row["kind"],
                source=row["source"],
                site_id=row["site_id"],
                reading_id=row["reading_id"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def replenishments(
        self,
        *,
        product_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        site_id: Optional[str] = None,
    ) -> List[Replenishment]:
        """Return replenishments in the range, oldest first."""
        where, params = self._scope(start, end, product_id, site_id)
        rows = self._conn.execute(
            f"SELECT * FROM consumption_replenishments {where} ORDER BY ts ASC",
            params,
        ).fetchall()

        return [
            Replenishment(
                timestamp=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
                product_id=row["product_id"],
                grams=row["grams"],
                site_id=row["site_id"],
                replenishment_id=row["replenishment_id"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def latest_reading(self, product_id: str) -> Optional[StockReading]:
        """Return the most recent reading for one product."""
        row = self._conn.execute(
            "SELECT * FROM consumption_readings WHERE product_id = ?"
            " ORDER BY ts DESC LIMIT 1",
            (product_id,),
        ).fetchone()
        if row is None:
            return None
        return StockReading(
            timestamp=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
            product_id=row["product_id"],
            grams=row["grams"],
            kind=row["kind"],
            source=row["source"],
            site_id=row["site_id"],
            reading_id=row["reading_id"],
            metadata=json.loads(row["metadata"]),
        )


__all__ = ["ConsumptionStore"]
