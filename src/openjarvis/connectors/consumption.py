"""Consumption connector — daily product mix by mass.

Reads the local :class:`~openjarvis.consumption.store.ConsumptionStore` and
emits one ``Document`` per trading day, so the flavour mix joins the same
digest and retrieval path as footfall, temperature, and the rest.

Local-only: there is no API and no account.  Readings arrive through
:mod:`openjarvis.consumption.ingest` — a weighing sheet, a scale, or a camera.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.consumption.metrics import consume, format_mix, mix
from openjarvis.consumption.store import ConsumptionStore
from openjarvis.consumption.types import MixEntry, StockReading
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_DB_PATH = str(DEFAULT_CONFIG_DIR / "consumption" / "consumption.db")

# How far back to look when a sync is not given an explicit `since`.
_DEFAULT_LOOKBACK_DAYS = 30


def _day_of(moment: datetime) -> datetime:
    """Return local midnight for the day containing *moment*."""
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


@ConnectorRegistry.register("consumption")
class ConsumptionConnector(BaseConnector):
    """Sync the daily product mix from the local consumption store."""

    connector_id = "consumption"
    display_name = "Product Consumption"
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

        There are no credentials to revoke — the readings *are* the data.
        """
        if self._db_path.exists():
            self._db_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield one Document per trading day with that day's mix."""
        if not self._db_path.exists():
            self._status.state = "idle"
            self._status.error = f"no consumption database at {self._db_path}"
            return

        start = since or datetime.now(timezone.utc) - timedelta(
            days=_DEFAULT_LOOKBACK_DAYS
        )
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        self._status.state = "syncing"
        self._status.items_synced = 0

        store = ConsumptionStore(self._db_path)
        try:
            specs = store.products(site_id=self._site_id)
            readings = store.readings(start=start, site_id=self._site_id)
            replenishments = store.replenishments(start=start, site_id=self._site_id)

            for day, window_start, window_end in self._days_covered(readings):
                entries = mix(
                    consume(
                        readings,
                        replenishments,
                        period_start=window_start,
                        period_end=window_end,
                        specs=specs,
                    ),
                    specs=specs,
                )
                if not entries:
                    continue
                yield self._build_document(day, entries)
                self._status.items_synced += 1
        finally:
            store.close()

        self._status.state = "idle"
        self._status.error = None
        self._status.last_sync = datetime.now()

    def _days_covered(
        self, readings: Sequence[StockReading]
    ) -> List[tuple[datetime, datetime, datetime]]:
        """Return ``(day, window_start, window_end)`` for each day with readings.

        The window is the span of the readings themselves, not midnight to
        midnight. Weighings happen at open and at close, so they already
        describe the trading day — and counting the closed hours as time the
        product was "available" would inflate every availability denominator
        and wash out the stockout correction that makes ``demand_share``
        worth computing.
        """
        spans: Dict[datetime, tuple[datetime, datetime]] = {}
        for reading in readings:
            day = _day_of(reading.timestamp)
            first, last = spans.get(day, (reading.timestamp, reading.timestamp))
            spans[day] = (
                min(first, reading.timestamp),
                max(last, reading.timestamp),
            )
        return [(day, spans[day][0], spans[day][1]) for day in sorted(spans)]

    def _build_document(self, day: datetime, entries: Sequence[MixEntry]) -> Document:
        """Render one day's mix into a Document."""
        day_label = day.strftime("%Y-%m-%d")
        total_grams = sum(entry.grams_consumed for entry in entries)

        body = [
            f"Consumo total: {total_grams / 1000:.1f} kg",
            "",
            format_mix(entries),
        ]

        suppressed = [entry for entry in entries if entry.suppressed]
        if suppressed:
            names = ", ".join(entry.name for entry in suppressed)
            body.append(
                f"\nSabores reprimidos por ruptura: {names}. "
                "O % consumido subestima a demanda desses sabores — "
                "planeje produção pelo % de demanda."
            )

        return Document(
            doc_id=f"consumption-{self._site_id or 'default'}-{day_label}",
            source="consumption",
            doc_type="daily_consumption",
            content="\n".join(body),
            title=f"Mix de consumo — {day_label}",
            timestamp=day,
            metadata={
                "day": day_label,
                "site_id": self._site_id or "",
                "total_grams": round(total_grams, 1),
                "has_stockout": any(entry.stockout for entry in entries),
                "mix": json.dumps(
                    [entry.as_dict() for entry in entries], ensure_ascii=False
                ),
            },
        )

    def sync_status(self) -> SyncStatus:
        return self._status


__all__ = ["ConsumptionConnector"]
