"""Cold-chain connector — daily temperature compliance per equipment.

Reads the local :class:`~openjarvis.coldchain.store.ColdChainStore` and emits
one ``Document`` per equipment per day, so refrigeration status joins the same
digest and retrieval path as the rest of the operation's data.

Local-only: there is no API and no account.  Readings arrive through
:mod:`openjarvis.coldchain.ingest` — a probe's payload or a digitized log.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from openjarvis.coldchain.monitor import format_summary, summarize
from openjarvis.coldchain.store import ColdChainStore
from openjarvis.coldchain.types import (
    SEVERITY_CRITICAL,
    ComplianceSummary,
    EquipmentSpec,
)
from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_DB_PATH = str(DEFAULT_CONFIG_DIR / "coldchain" / "coldchain.db")

# How far back to look when a sync is not given an explicit `since`.
_DEFAULT_LOOKBACK_DAYS = 30


def _day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """Return midnight-to-midnight bounds for the day containing *day*."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


@ConnectorRegistry.register("coldchain")
class ColdChainConnector(BaseConnector):
    """Sync daily temperature compliance from the local cold-chain store."""

    connector_id = "coldchain"
    display_name = "Cold Chain"
    auth_type = "local"

    def __init__(
        self,
        *,
        db_path: str = _DEFAULT_DB_PATH,
        site_id: Optional[str] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._site_id = site_id
        self._status = SyncStatus()

    def is_connected(self) -> bool:
        return self._db_path.exists()

    def disconnect(self) -> None:
        """Remove the local database.

        There are no credentials to revoke — the readings *are* the data, and
        they are the compliance record, so deleting them is not reversible.
        """
        if self._db_path.exists():
            self._db_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield one Document per equipment per day in the window."""
        if not self._db_path.exists():
            self._status.state = "idle"
            self._status.error = f"no cold-chain database at {self._db_path}"
            return

        start = since or datetime.now(timezone.utc) - timedelta(
            days=_DEFAULT_LOOKBACK_DAYS
        )
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        self._status.state = "syncing"
        self._status.items_synced = 0

        store = ColdChainStore(self._db_path)
        try:
            specs = store.equipment(site_id=self._site_id)
            for spec in specs:
                readings = store.readings(equipment_id=spec.equipment_id, start=start)
                if not readings:
                    continue

                for day_start, day_end in self._days_covered(readings):
                    summary = summarize(
                        readings,
                        spec,
                        period_start=day_start,
                        period_end=day_end,
                    )
                    if summary.reading_count == 0:
                        continue
                    yield self._build_document(spec, summary)
                    self._status.items_synced += 1
        finally:
            store.close()

        self._status.state = "idle"
        self._status.error = None
        self._status.last_sync = datetime.now()

    def _days_covered(self, readings) -> List[tuple[datetime, datetime]]:
        """Return the distinct day windows the readings fall into."""
        seen = {}
        for reading in readings:
            start, end = _day_bounds(reading.timestamp)
            seen[start] = end
        return [(start, seen[start]) for start in sorted(seen)]

    def _build_document(
        self, spec: EquipmentSpec, summary: ComplianceSummary
    ) -> Document:
        """Render one equipment-day into a Document."""
        day_label = summary.period_start.strftime("%Y-%m-%d")
        label = spec.name or spec.equipment_id

        body = [
            format_summary(summary),
            f"Faixa declarada: {spec.min_celsius:.1f}°C a {spec.max_celsius:.1f}°C "
            f"(tolerância {spec.tolerance_minutes:.0f} min)",
        ]

        for excursion in summary.excursions:
            body.append(
                f"— Desvio {excursion.direction} de "
                f"{excursion.start:%H:%M} a {excursion.end:%H:%M}: pico "
                f"{excursion.peak_celsius:.1f}°C, "
                f"{excursion.duration_minutes:.0f} min"
            )

        return Document(
            doc_id=f"coldchain-{spec.equipment_id}-{day_label}",
            source="coldchain",
            doc_type="daily_coldchain",
            content="\n".join(body),
            title=f"Temperatura — {label} — {day_label}",
            timestamp=summary.period_start,
            metadata={
                "day": day_label,
                "equipment_id": spec.equipment_id,
                "site_id": spec.site_id,
                "severity": summary.severity,
                "critical": summary.severity == SEVERITY_CRITICAL,
                "summary": json.dumps(summary.as_dict(), ensure_ascii=False),
            },
        )

    def sync_status(self) -> SyncStatus:
        return self._status


__all__ = ["ColdChainConnector"]
