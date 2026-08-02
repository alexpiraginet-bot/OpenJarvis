"""Core types for per-product consumption and mix.

Built for an operation whose point of sale does not break the product down —
a gelateria that rings up "copo pequeno" without recording which flavours went
in it.  The mix is then invisible in sales data, and the only way to recover it
is to measure what left the tub.

The unit is **mass**, not servings.  Production happens in kilograms, so a mix
expressed in kilograms is the one that can be acted on; servings would also
require a portion size that nobody measures reliably.

The central distinction here is between what was *consumed* and what was
*wanted*.  A flavour that ran out at 14:00 shows low consumption because it was
absent, not because it was unpopular, and planning production from raw share
therefore under-produces exactly the flavours that sell fastest.  See
:class:`MixEntry` for how availability corrects that.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# --- Reading kinds ---------------------------------------------------------

KIND_OPEN = "open"
KIND_CLOSE = "close"
KIND_SPOT = "spot"

KINDS = (KIND_OPEN, KIND_CLOSE, KIND_SPOT)

# --- Sources ---------------------------------------------------------------

SOURCE_SCALE = "scale"
SOURCE_CAMERA = "camera"
SOURCE_MANUAL = "manual"


@dataclass(slots=True)
class ProductSpec:
    """A product whose consumption is tracked — one gelato flavour, say.

    ``portion_grams`` is the intended serving size.  It is optional, and only
    used to translate mass into an estimated serving count; leaving it unset
    keeps every number in mass, which is always measured rather than assumed.
    """

    product_id: str
    name: str = ""
    category: str = ""
    portion_grams: Optional[float] = None
    site_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.product_id:
            raise ValueError("product requires a product_id")
        if self.portion_grams is not None and self.portion_grams <= 0:
            raise ValueError(f"{self.product_id}: portion_grams must be > 0")
        if not self.name:
            self.name = self.product_id


@dataclass(slots=True)
class StockReading:
    """A measured quantity of one product at a point in time.

    ``grams`` is what is left, not what was used.  Weighing a tub is a
    measurement anyone can repeat and audit; consumption is derived from
    consecutive measurements, never recorded directly.
    """

    timestamp: datetime
    product_id: str
    grams: float
    kind: str = KIND_SPOT
    source: str = SOURCE_SCALE
    site_id: str = ""
    reading_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if not self.product_id:
            raise ValueError("reading requires a product_id")
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}; expected one of {KINDS}")
        if self.grams < 0:
            raise ValueError(f"{self.product_id}: grams must be non-negative")
        if not self.reading_id:
            raw = f"{self.timestamp.timestamp():.3f}|{self.product_id}|{self.kind}"
            self.reading_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @property
    def epoch(self) -> float:
        """Reading time as epoch seconds."""
        return self.timestamp.timestamp()


@dataclass(slots=True)
class Replenishment:
    """Mass added to a product mid-period.

    Without this, a tub topped up during service reads as negative
    consumption, or — worse — as a suspiciously light day.
    """

    timestamp: datetime
    product_id: str
    grams: float
    site_id: str = ""
    replenishment_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if not self.product_id:
            raise ValueError("replenishment requires a product_id")
        if self.grams <= 0:
            raise ValueError(f"{self.product_id}: replenishment grams must be > 0")
        if not self.replenishment_id:
            raw = f"{self.timestamp.timestamp():.3f}|{self.product_id}|{self.grams}"
            self.replenishment_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @property
    def epoch(self) -> float:
        """Replenishment time as epoch seconds."""
        return self.timestamp.timestamp()


@dataclass(slots=True)
class ProductConsumption:
    """What one product consumed over one period, and for how long it existed.

    ``unrecorded_replenishment`` flags a period that ended with more product
    than it could have had.  That is not consumption going negative — it means
    someone topped the tub up without recording it, and the period's mass is
    understated. Surfacing it beats silently clamping to zero, which would
    quietly corrupt the mix.
    """

    product_id: str
    grams_consumed: float = 0.0
    hours_available: float = 0.0
    period_hours: float = 0.0
    reading_count: int = 0
    stockout: bool = False
    unrecorded_replenishment: bool = False
    name: str = ""
    site_id: str = ""

    @property
    def grams_per_hour(self) -> Optional[float]:
        """Depletion rate while the product was actually available.

        ``None`` when the product was never available — a rate over zero
        hours is undefined, not zero.
        """
        if self.hours_available <= 0:
            return None
        return self.grams_consumed / self.hours_available

    @property
    def availability_ratio(self) -> Optional[float]:
        """Share of the period the product was on offer."""
        if self.period_hours <= 0:
            return None
        return self.hours_available / self.period_hours


@dataclass(slots=True)
class MixEntry:
    """One product's place in the mix.

    ``share``
        Raw share of mass consumed.  What happened.

    ``demand_share``
        Share corrected for availability, derived from depletion rate rather
        than total mass.  What would have happened had everything stayed in
        stock all period.  This is the number to plan production from; the
        two diverge exactly when something ran out, and that divergence is
        the signal worth acting on.
    """

    product_id: str
    name: str = ""
    grams_consumed: float = 0.0
    share: Optional[float] = None
    demand_share: Optional[float] = None
    grams_per_hour: Optional[float] = None
    availability_ratio: Optional[float] = None
    stockout: bool = False
    unrecorded_replenishment: bool = False
    estimated_servings: Optional[float] = None

    @property
    def suppressed(self) -> bool:
        """True when a stockout held this product below its real demand.

        The comparison is what turns "pistachio was 9% of sales" into
        "pistachio was 9% of sales because it was gone by two o'clock".
        """
        if self.share is None or self.demand_share is None:
            return False
        return self.stockout and self.demand_share > self.share

    def as_dict(self) -> Dict[str, Any]:
        """Return a plain dict, suitable for JSON serialization."""
        return {
            "product_id": self.product_id,
            "name": self.name,
            "grams_consumed": round(self.grams_consumed, 1),
            "share": self.share,
            "demand_share": self.demand_share,
            "grams_per_hour": (
                round(self.grams_per_hour, 1)
                if self.grams_per_hour is not None
                else None
            ),
            "availability_ratio": self.availability_ratio,
            "stockout": self.stockout,
            "suppressed": self.suppressed,
            "unrecorded_replenishment": self.unrecorded_replenishment,
            "estimated_servings": (
                round(self.estimated_servings, 1)
                if self.estimated_servings is not None
                else None
            ),
        }


__all__ = [
    "KINDS",
    "KIND_CLOSE",
    "KIND_OPEN",
    "KIND_SPOT",
    "SOURCE_CAMERA",
    "SOURCE_MANUAL",
    "SOURCE_SCALE",
    "MixEntry",
    "ProductConsumption",
    "ProductSpec",
    "Replenishment",
    "StockReading",
]
