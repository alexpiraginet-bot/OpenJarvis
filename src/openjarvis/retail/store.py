"""SQLite store for footfall events and point-of-sale transactions.

Everything is local — the counts never leave the machine, which matters
because footfall data is commercially sensitive (a landlord who can see your
capture rate can price your rent against it).

Ingestion is idempotent: events carry a deterministic ``event_id`` derived
from their payload, so a sensor replaying its buffer after a network drop
cannot double-count.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openjarvis.retail.types import (
    DIR_IN,
    DIR_OUT,
    DIR_PASS,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
    FootfallEvent,
    IntervalCounts,
    Sale,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 is unsupported anyway
    ZoneInfo = None  # type: ignore[assignment]

DEFAULT_TIMEZONE = "America/Sao_Paulo"

# Bucket sizes, in seconds.  ``day`` is handled separately because a calendar
# day is not always 86400 seconds long under DST.
_INTERVAL_SECONDS: Dict[str, int] = {
    "15min": 900,
    "30min": 1800,
    "hour": 3600,
}
INTERVALS = tuple(_INTERVAL_SECONDS) + ("day",)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS footfall_events (
    event_id    TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    zone        TEXT NOT NULL,
    direction   TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    sensor_id   TEXT NOT NULL DEFAULT '',
    sensor_kind TEXT NOT NULL DEFAULT '',
    site_id     TEXT NOT NULL DEFAULT '',
    confidence  REAL NOT NULL DEFAULT 1.0,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
"""

_CREATE_SALES_TABLE = """
CREATE TABLE IF NOT EXISTS retail_sales (
    sale_id     TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    amount      REAL NOT NULL DEFAULT 0.0,
    items       INTEGER NOT NULL DEFAULT 0,
    site_id     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_footfall_ts ON footfall_events(ts);",
    "CREATE INDEX IF NOT EXISTS idx_footfall_site_ts ON footfall_events(site_id, ts);",
    "CREATE INDEX IF NOT EXISTS idx_sales_ts ON retail_sales(ts);",
    "CREATE INDEX IF NOT EXISTS idx_sales_site_ts ON retail_sales(site_id, ts);",
)


def _resolve_tz(tz: str):
    """Return a tzinfo for *tz*, falling back to UTC when unavailable."""
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz)
    except Exception:
        return timezone.utc


def _floor_to_bucket(moment: datetime, interval: str) -> datetime:
    """Floor a timezone-aware *moment* to the start of its bucket."""
    if interval == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    seconds = _INTERVAL_SECONDS[interval]
    minutes = (seconds // 60) if seconds >= 60 else 1
    floored_minute = (moment.minute // minutes) * minutes
    return moment.replace(minute=floored_minute, second=0, microsecond=0)


def _next_bucket(bucket_start: datetime, interval: str) -> datetime:
    """Return the start of the bucket following *bucket_start*.

    Day buckets advance by calendar date rather than by 86400 seconds, so a
    DST transition shortens or lengthens the day instead of shifting every
    subsequent boundary.
    """
    if interval == "day":
        next_day: date = bucket_start.date() + timedelta(days=1)
        return datetime(
            next_day.year,
            next_day.month,
            next_day.day,
            tzinfo=bucket_start.tzinfo,
        )
    return bucket_start + timedelta(seconds=_INTERVAL_SECONDS[interval])


class FootfallStore:
    """Local SQLite store for footfall events and sales."""

    def __init__(self, db_path: str | Path, *, timezone_name: str = DEFAULT_TIMEZONE):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._timezone_name = timezone_name
        self._tz = _resolve_tz(timezone_name)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_EVENTS_TABLE)
            self._conn.execute(_CREATE_SALES_TABLE)
            for statement in _CREATE_INDEXES:
                self._conn.execute(statement)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "FootfallStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- Ingestion ----------------------------------------------------------

    def record_events(self, events: Iterable[FootfallEvent]) -> int:
        """Insert *events*, ignoring ones already stored.

        Returns the number of rows actually written, so a caller can tell a
        genuinely new batch from a replayed one.
        """
        rows = [
            (
                event.event_id,
                event.epoch,
                event.zone,
                event.direction,
                event.count,
                event.sensor_id,
                event.sensor_kind,
                event.site_id,
                event.confidence,
                json.dumps(event.metadata, ensure_ascii=False),
                time.time(),
            )
            for event in events
        ]
        if not rows:
            return 0

        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO footfall_events
                    (event_id, ts, zone, direction, count, sensor_id,
                     sensor_kind, site_id, confidence, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self._conn.total_changes - before

    def record_event(self, event: FootfallEvent) -> int:
        """Insert a single event.  Returns 1 if written, 0 if already present."""
        return self.record_events([event])

    def record_sales(self, sales: Iterable[Sale]) -> int:
        """Insert *sales*, ignoring ones already stored."""
        rows = [
            (
                sale.sale_id,
                sale.epoch,
                sale.amount,
                sale.items,
                sale.site_id,
                sale.source,
                json.dumps(sale.metadata, ensure_ascii=False),
                time.time(),
            )
            for sale in sales
        ]
        if not rows:
            return 0

        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO retail_sales
                    (sale_id, ts, amount, items, site_id, source, metadata,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self._conn.total_changes - before

    def record_sale(self, sale: Sale) -> int:
        """Insert a single sale.  Returns 1 if written, 0 if already present."""
        return self.record_sales([sale])

    # -- Queries ------------------------------------------------------------

    def _range_clause(
        self, start: Optional[datetime], end: Optional[datetime], site_id: Optional[str]
    ) -> tuple[str, List[Any]]:
        """Build a shared WHERE clause for both tables."""
        clauses: List[str] = []
        params: List[Any] = []
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start.timestamp())
        if end is not None:
            clauses.append("ts < ?")
            params.append(end.timestamp())
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def raw_events(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        site_id: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """Return raw event rows in the range, oldest first."""
        where, params = self._range_clause(start, end, site_id)
        cursor = self._conn.execute(
            f"SELECT * FROM footfall_events {where} ORDER BY ts ASC", params
        )
        return cursor.fetchall()

    def intervals(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        interval: str = "hour",
        site_id: Optional[str] = None,
        fill_empty: bool = False,
    ) -> List[IntervalCounts]:
        """Aggregate events and sales into time buckets.

        Buckets are anchored to the store's local timezone, not UTC — a
        "day" is the local trading day, which is the only framing a retail
        report can be read in.

        With *fill_empty*, buckets with no activity are included between the
        first and last bucket that has any, so a time series has no gaps.
        Closed hours then appear as zero-traffic buckets, whose rates the
        metrics layer correctly reports as undefined rather than 0%.
        """
        if interval not in INTERVALS:
            raise ValueError(
                f"unknown interval {interval!r}; expected one of {INTERVALS}"
            )

        where, params = self._range_clause(start, end, site_id)
        buckets: Dict[datetime, IntervalCounts] = {}

        def bucket_for(epoch: float) -> IntervalCounts:
            local = datetime.fromtimestamp(epoch, tz=self._tz)
            bucket_start = _floor_to_bucket(local, interval)
            existing = buckets.get(bucket_start)
            if existing is None:
                existing = IntervalCounts(
                    bucket_start=bucket_start,
                    bucket_end=_next_bucket(bucket_start, interval),
                    site_id=site_id or "",
                )
                buckets[bucket_start] = existing
            return existing

        event_rows = self._conn.execute(
            f"SELECT ts, zone, direction, count FROM footfall_events {where}", params
        ).fetchall()

        for row in event_rows:
            bucket = bucket_for(row["ts"])
            zone, direction, count = row["zone"], row["direction"], row["count"]
            if zone == ZONE_CORRIDOR and direction == DIR_PASS:
                bucket.passersby += count
            elif zone == ZONE_ENTRANCE and direction == DIR_IN:
                bucket.entries += count
            elif zone == ZONE_ENTRANCE and direction == DIR_OUT:
                bucket.exits += count

        sale_rows = self._conn.execute(
            f"SELECT ts, amount, items FROM retail_sales {where}", params
        ).fetchall()

        for row in sale_rows:
            bucket = bucket_for(row["ts"])
            bucket.transactions += 1
            bucket.revenue += row["amount"]
            bucket.items += row["items"]

        if not buckets:
            return []

        ordered = sorted(buckets)
        if fill_empty:
            filled: List[IntervalCounts] = []
            cursor_bucket = ordered[0]
            last = ordered[-1]
            while cursor_bucket <= last:
                existing = buckets.get(cursor_bucket)
                filled.append(
                    existing
                    if existing is not None
                    else IntervalCounts(
                        bucket_start=cursor_bucket,
                        bucket_end=_next_bucket(cursor_bucket, interval),
                        site_id=site_id or "",
                    )
                )
                cursor_bucket = _next_bucket(cursor_bucket, interval)
            return filled

        return [buckets[key] for key in ordered]

    def sites(self) -> List[str]:
        """Return the distinct site ids seen across events and sales."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT site_id FROM footfall_events
            UNION
            SELECT DISTINCT site_id FROM retail_sales
            """
        ).fetchall()
        return sorted(row["site_id"] for row in rows if row["site_id"])


__all__ = [
    "DEFAULT_TIMEZONE",
    "INTERVALS",
    "FootfallStore",
]
