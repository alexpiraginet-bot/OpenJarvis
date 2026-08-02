"""SQLite store for temperature readings and equipment specs.

Local-only, like the rest of the store-operations data.  The reading history
is the compliance record, so it is append-only in practice: readings dedupe
on a deterministic id rather than being overwritten.

Equipment specs, by contrast, are upserted — a setpoint gets revised when the
procedure changes, and the current spec is what alerting evaluates against.
Historical readings keep their timestamps, so a report can always be
recomputed, but note that recomputing an old period uses today's spec.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional

from openjarvis.coldchain.types import EquipmentSpec, TemperatureReading

_CREATE_EQUIPMENT_TABLE = """
CREATE TABLE IF NOT EXISTS coldchain_equipment (
    equipment_id              TEXT PRIMARY KEY,
    name                      TEXT NOT NULL DEFAULT '',
    min_celsius               REAL NOT NULL,
    max_celsius               REAL NOT NULL,
    tolerance_minutes         REAL NOT NULL DEFAULT 15.0,
    expected_interval_seconds REAL NOT NULL DEFAULT 300.0,
    site_id                   TEXT NOT NULL DEFAULT '',
    metadata                  TEXT NOT NULL DEFAULT '{}',
    updated_at                REAL NOT NULL
);
"""

_CREATE_READINGS_TABLE = """
CREATE TABLE IF NOT EXISTS coldchain_readings (
    reading_id   TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    celsius      REAL NOT NULL,
    equipment_id TEXT NOT NULL,
    sensor_id    TEXT NOT NULL DEFAULT '',
    site_id      TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL
);
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_coldchain_ts ON coldchain_readings(ts);",
    """CREATE INDEX IF NOT EXISTS idx_coldchain_equipment_ts
       ON coldchain_readings(equipment_id, ts);""",
)


class ColdChainStore:
    """Local SQLite store for equipment specs and temperature readings."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_EQUIPMENT_TABLE)
            self._conn.execute(_CREATE_READINGS_TABLE)
            for statement in _CREATE_INDEXES:
                self._conn.execute(statement)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "ColdChainStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- Equipment ----------------------------------------------------------

    def register_equipment(self, spec: EquipmentSpec) -> None:
        """Insert or update an equipment spec."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO coldchain_equipment
                    (equipment_id, name, min_celsius, max_celsius,
                     tolerance_minutes, expected_interval_seconds, site_id,
                     metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(equipment_id) DO UPDATE SET
                    name = excluded.name,
                    min_celsius = excluded.min_celsius,
                    max_celsius = excluded.max_celsius,
                    tolerance_minutes = excluded.tolerance_minutes,
                    expected_interval_seconds = excluded.expected_interval_seconds,
                    site_id = excluded.site_id,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                """,
                (
                    spec.equipment_id,
                    spec.name,
                    spec.min_celsius,
                    spec.max_celsius,
                    spec.tolerance_minutes,
                    spec.expected_interval_seconds,
                    spec.site_id,
                    json.dumps(spec.metadata, ensure_ascii=False),
                    time.time(),
                ),
            )

    def _row_to_spec(self, row: sqlite3.Row) -> EquipmentSpec:
        """Rebuild an EquipmentSpec from a database row."""
        return EquipmentSpec(
            equipment_id=row["equipment_id"],
            name=row["name"],
            min_celsius=row["min_celsius"],
            max_celsius=row["max_celsius"],
            tolerance_minutes=row["tolerance_minutes"],
            expected_interval_seconds=row["expected_interval_seconds"],
            site_id=row["site_id"],
            metadata=json.loads(row["metadata"]),
        )

    def equipment(self, *, site_id: Optional[str] = None) -> List[EquipmentSpec]:
        """Return registered equipment, optionally filtered by site."""
        if site_id:
            rows = self._conn.execute(
                "SELECT * FROM coldchain_equipment WHERE site_id = ?"
                " ORDER BY equipment_id",
                (site_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM coldchain_equipment ORDER BY equipment_id"
            ).fetchall()
        return [self._row_to_spec(row) for row in rows]

    def get_equipment(self, equipment_id: str) -> Optional[EquipmentSpec]:
        """Return one equipment spec, or ``None`` when it is not registered."""
        row = self._conn.execute(
            "SELECT * FROM coldchain_equipment WHERE equipment_id = ?",
            (equipment_id,),
        ).fetchone()
        return self._row_to_spec(row) if row is not None else None

    # -- Readings -----------------------------------------------------------

    def record_readings(self, readings: Iterable[TemperatureReading]) -> int:
        """Insert *readings*, ignoring ones already stored.

        Returns the number of rows written, so a caller can distinguish a
        fresh batch from a sensor replaying its buffer after a reconnect.
        """
        rows = [
            (
                reading.reading_id,
                reading.epoch,
                reading.celsius,
                reading.equipment_id,
                reading.sensor_id,
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
                INSERT OR IGNORE INTO coldchain_readings
                    (reading_id, ts, celsius, equipment_id, sensor_id, site_id,
                     metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self._conn.total_changes - before

    def record_reading(self, reading: TemperatureReading) -> int:
        """Insert one reading.  Returns 1 if written, 0 if already present."""
        return self.record_readings([reading])

    def readings(
        self,
        *,
        equipment_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        site_id: Optional[str] = None,
    ) -> List[TemperatureReading]:
        """Return readings in the range, oldest first."""
        clauses: List[str] = []
        params: List[Any] = []
        if equipment_id:
            clauses.append("equipment_id = ?")
            params.append(equipment_id)
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start.timestamp())
        if end is not None:
            clauses.append("ts <= ?")
            params.append(end.timestamp())
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        rows = self._conn.execute(
            f"SELECT * FROM coldchain_readings {where} ORDER BY ts ASC", params
        ).fetchall()

        return [
            TemperatureReading(
                timestamp=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
                celsius=row["celsius"],
                equipment_id=row["equipment_id"],
                sensor_id=row["sensor_id"],
                site_id=row["site_id"],
                reading_id=row["reading_id"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def latest_reading(self, equipment_id: str) -> Optional[TemperatureReading]:
        """Return the most recent reading for one equipment."""
        row = self._conn.execute(
            "SELECT * FROM coldchain_readings WHERE equipment_id = ?"
            " ORDER BY ts DESC LIMIT 1",
            (equipment_id,),
        ).fetchone()
        if row is None:
            return None
        return TemperatureReading(
            timestamp=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
            celsius=row["celsius"],
            equipment_id=row["equipment_id"],
            sensor_id=row["sensor_id"],
            site_id=row["site_id"],
            reading_id=row["reading_id"],
            metadata=json.loads(row["metadata"]),
        )


__all__ = ["ColdChainStore"]
