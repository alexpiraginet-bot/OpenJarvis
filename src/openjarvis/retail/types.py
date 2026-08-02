"""Core types for retail footfall analytics.

The domain model is deliberately sensor-agnostic: a break-beam counter, an
overhead camera, an mmWave radar, or a human with a clicker all produce the
same :class:`FootfallEvent`.  Swapping hardware never changes the analytics.

Two zones matter for a storefront:

``corridor``
    The mall walkway in front of the store.  People crossing it are
    *passantes* (passersby) — the addressable audience.

``entrance``
    The store threshold.  People crossing inward are *entrantes* (entries);
    outward crossings let us track live occupancy.

Combined with point-of-sale transactions, the three counts form the retail
funnel: passersby -> entries -> transactions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# --- Zones -----------------------------------------------------------------

ZONE_CORRIDOR = "corridor"
ZONE_ENTRANCE = "entrance"

ZONES = (ZONE_CORRIDOR, ZONE_ENTRANCE)

# --- Directions ------------------------------------------------------------

DIR_IN = "in"
DIR_OUT = "out"
DIR_PASS = "pass"

DIRECTIONS = (DIR_IN, DIR_OUT, DIR_PASS)

# --- Sensor kinds ----------------------------------------------------------
# Free-form, but these are the ones the built-in adapters emit.  Recorded on
# every event so a later accuracy audit can weigh sources against each other.

SENSOR_MANUAL = "manual"
SENSOR_CAMERA = "camera"
SENSOR_MMWAVE = "mmwave"
SENSOR_BEAM = "beam"
SENSOR_WIFI_CSI = "wifi_csi"


def _stable_event_id(
    *, ts: float, zone: str, direction: str, sensor_id: str, count: int
) -> str:
    """Derive a deterministic event id so repeated deliveries collapse.

    Sensors retry.  A radar that loses its uplink for a minute will replay its
    buffer, and a webhook with no ack will fire twice.  Hashing the payload
    makes ingestion idempotent without the sensor having to track state.
    """
    raw = f"{ts:.3f}|{zone}|{direction}|{sensor_id}|{count}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class FootfallEvent:
    """A single counted crossing.

    ``count`` is normally 1 (one person crossed one line), but batch sources
    — a manual tally, or a sensor that reports per-minute totals — collapse
    several crossings into one event.
    """

    timestamp: datetime
    zone: str
    direction: str
    count: int = 1
    sensor_id: str = ""
    sensor_kind: str = ""
    site_id: str = ""
    confidence: float = 1.0
    event_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.zone not in ZONES:
            raise ValueError(f"unknown zone {self.zone!r}; expected one of {ZONES}")
        if self.direction not in DIRECTIONS:
            raise ValueError(
                f"unknown direction {self.direction!r}; expected one of {DIRECTIONS}"
            )
        if self.count < 0:
            raise ValueError(f"count must be non-negative, got {self.count}")
        # Naive datetimes are assumed to be UTC — storing epoch seconds below
        # would otherwise silently shift by the host's offset.
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if not self.event_id:
            self.event_id = _stable_event_id(
                ts=self.timestamp.timestamp(),
                zone=self.zone,
                direction=self.direction,
                sensor_id=self.sensor_id,
                count=self.count,
            )

    @property
    def epoch(self) -> float:
        """Event time as epoch seconds."""
        return self.timestamp.timestamp()


@dataclass(slots=True)
class Sale:
    """A point-of-sale transaction.

    Sourced from whatever the store already uses (Stone, Sicoob, a CSV
    export).  Only the fields the funnel needs are modelled.
    """

    timestamp: datetime
    amount: float = 0.0
    items: int = 0
    site_id: str = ""
    source: str = ""
    sale_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if not self.sale_id:
            raw = f"{self.timestamp.timestamp():.3f}|{self.amount}|{self.source}"
            self.sale_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @property
    def epoch(self) -> float:
        """Sale time as epoch seconds."""
        return self.timestamp.timestamp()


@dataclass(slots=True)
class IntervalCounts:
    """Raw counts for one time bucket, before any rates are derived."""

    bucket_start: datetime
    bucket_end: datetime
    passersby: int = 0
    entries: int = 0
    exits: int = 0
    transactions: int = 0
    revenue: float = 0.0
    items: int = 0
    site_id: str = ""

    @property
    def net_occupancy_change(self) -> int:
        """Entries minus exits within this bucket.

        Running occupancy is the cumulative sum of this across buckets; it is
        not meaningful on its own for a single bucket.
        """
        return self.entries - self.exits


@dataclass(slots=True)
class FunnelMetrics:
    """Derived retail funnel rates.

    Every rate is ``None`` when its denominator is zero.  This is deliberate:
    a closed hour has an *undefined* capture rate, not a 0% one, and folding
    those zeros into an average would drag the store's numbers down for no
    reason.
    """

    passersby: int = 0
    entries: int = 0
    transactions: int = 0
    revenue: float = 0.0
    items: int = 0
    capture_rate: Optional[float] = None
    conversion_rate: Optional[float] = None
    pass_to_sale_rate: Optional[float] = None
    average_ticket: Optional[float] = None
    items_per_transaction: Optional[float] = None
    revenue_per_entry: Optional[float] = None
    revenue_per_passerby: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        """Return a plain dict, suitable for JSON serialization."""
        return {
            "passersby": self.passersby,
            "entries": self.entries,
            "transactions": self.transactions,
            "revenue": round(self.revenue, 2),
            "items": self.items,
            "capture_rate": self.capture_rate,
            "conversion_rate": self.conversion_rate,
            "pass_to_sale_rate": self.pass_to_sale_rate,
            "average_ticket": self.average_ticket,
            "items_per_transaction": self.items_per_transaction,
            "revenue_per_entry": self.revenue_per_entry,
            "revenue_per_passerby": self.revenue_per_passerby,
        }


__all__ = [
    "DIRECTIONS",
    "DIR_IN",
    "DIR_OUT",
    "DIR_PASS",
    "FootfallEvent",
    "FunnelMetrics",
    "IntervalCounts",
    "SENSOR_BEAM",
    "SENSOR_CAMERA",
    "SENSOR_MANUAL",
    "SENSOR_MMWAVE",
    "SENSOR_WIFI_CSI",
    "Sale",
    "ZONES",
    "ZONE_CORRIDOR",
    "ZONE_ENTRANCE",
]
