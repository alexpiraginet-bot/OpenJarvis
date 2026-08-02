"""Retail footfall connector — daily store traffic and conversion.

Reads the local :class:`~openjarvis.retail.store.FootfallStore` and emits one
``Document`` per trading day so store performance joins the same digest and
retrieval path as email, calendar, and health data.

Local-only: there is no API and no account.  Data arrives through
:mod:`openjarvis.retail.ingest` — a manual tally sheet, a sensor webhook, or
a point-of-sale export.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.retail.metrics import format_summary, funnel, peak_buckets
from openjarvis.retail.store import FootfallStore
from openjarvis.retail.types import IntervalCounts

_DEFAULT_DB_PATH = str(DEFAULT_CONFIG_DIR / "retail" / "footfall.db")

# How far back to look when a sync is not given an explicit `since`.
_DEFAULT_LOOKBACK_DAYS = 30


def _format_hour_range(bucket: IntervalCounts) -> str:
    """Render a bucket as ``14h–15h`` for the report body."""
    return f"{bucket.bucket_start:%H}h–{bucket.bucket_end:%H}h"


@ConnectorRegistry.register("retail_footfall")
class RetailFootfallConnector(BaseConnector):
    """Sync daily footfall and conversion from the local retail store."""

    connector_id = "retail_footfall"
    display_name = "Retail Footfall"
    auth_type = "local"

    def __init__(
        self,
        *,
        db_path: str = _DEFAULT_DB_PATH,
        site_id: Optional[str] = None,
        currency: str = "R$",
    ) -> None:
        self._db_path = Path(db_path)
        self._site_id = site_id
        self._currency = currency
        self._status = SyncStatus()

    def is_connected(self) -> bool:
        return self._db_path.exists()

    def disconnect(self) -> None:
        """Remove the local database.

        Unlike an OAuth connector there are no credentials to revoke — the
        counts *are* the data, so disconnecting deletes them.
        """
        if self._db_path.exists():
            self._db_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield one Document per trading day with the funnel for that day."""
        if not self._db_path.exists():
            self._status.state = "idle"
            self._status.error = f"no footfall database at {self._db_path}"
            return

        start = since or datetime.now() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
        self._status.state = "syncing"
        self._status.items_synced = 0

        store = FootfallStore(self._db_path)
        try:
            days = store.intervals(start=start, interval="day", site_id=self._site_id)
            for day in days:
                hours = store.intervals(
                    start=day.bucket_start,
                    end=day.bucket_end,
                    interval="hour",
                    site_id=self._site_id,
                )
                yield self._build_document(day, hours)
                self._status.items_synced += 1
        finally:
            store.close()

        self._status.state = "idle"
        self._status.error = None
        self._status.last_sync = datetime.now()

    def _build_document(
        self, day: IntervalCounts, hours: List[IntervalCounts]
    ) -> Document:
        """Render one day's counts into a Document."""
        metrics = funnel([day])
        day_label = day.bucket_start.strftime("%Y-%m-%d")
        site_label = f" — {day.site_id}" if day.site_id else ""

        body = [format_summary(metrics, currency=self._currency)]

        if hours:
            busiest = peak_buckets(hours, key="entries", limit=3)
            ranked = ", ".join(
                f"{_format_hour_range(bucket)} ({bucket.entries} entrantes)"
                for bucket in busiest
                if bucket.entries
            )
            if ranked:
                body.append(f"\nHorários de pico: {ranked}")

            best_capture = [
                bucket for bucket in hours if bucket.passersby and bucket.entries
            ]
            if best_capture:
                top = max(best_capture, key=lambda b: b.entries / b.passersby)
                rate = top.entries / top.passersby * 100
                body.append(f"Melhor captura: {_format_hour_range(top)} ({rate:.1f}%)")

        return Document(
            doc_id=f"retail_footfall-{day.site_id or 'default'}-{day_label}",
            source="retail_footfall",
            doc_type="daily_footfall",
            content="\n".join(body),
            title=f"Fluxo e conversão{site_label} — {day_label}",
            timestamp=day.bucket_start,
            metadata={
                "day": day_label,
                "site_id": day.site_id,
                "metrics": metrics.as_dict(),
                "hourly": json.dumps(
                    [
                        {
                            "hour": bucket.bucket_start.strftime("%H:%M"),
                            "passersby": bucket.passersby,
                            "entries": bucket.entries,
                            "transactions": bucket.transactions,
                            "revenue": round(bucket.revenue, 2),
                        }
                        for bucket in hours
                    ],
                    ensure_ascii=False,
                ),
            },
        )

    def sync_status(self) -> SyncStatus:
        return self._status


__all__ = ["RetailFootfallConnector"]
