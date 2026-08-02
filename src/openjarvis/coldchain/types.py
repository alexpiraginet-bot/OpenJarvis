"""Core types for cold-chain temperature monitoring.

The module does **not** decide what temperature is safe.  Safe ranges come
from the operator's own written procedure and the equipment manufacturer's
spec, declared here as an :class:`EquipmentSpec`.  What the module does is
enforce the declared spec, detect deviations, and produce the evidence trail
that a food-safety audit asks for.

That split matters: regulation (ANVISA RDC 216 in Brazil) requires that
temperature control be monitored and recorded, but the actual setpoints
depend on the product and the equipment.  Hard-coding numbers here would
produce confident, wrong compliance claims.

Two concepts carry most of the weight:

``Excursion``
    A contiguous run of readings outside the declared range, lasting longer
    than the spec's tolerance.  The tolerance is what separates a door left
    open during service from a compressor that failed overnight — without
    it, every service window would raise an alarm and the alarms would stop
    being read.

``MonitoringGap``
    A stretch with no readings at all.  A gap is a compliance finding in its
    own right: you cannot prove a freezer held temperature during hours it
    was not being watched.  Systems that only look at recorded values score
    a dead sensor as a perfect day.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# --- Excursion directions --------------------------------------------------

ABOVE = "above"
BELOW = "below"

# --- Severity --------------------------------------------------------------
# For frozen product, warming is the direction that threatens safety; running
# colder than spec generally costs texture and energy, not safety. Callers
# that need a different weighting should read `direction` and decide.

SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


@dataclass(slots=True)
class EquipmentSpec:
    """Declared operating range for one piece of equipment.

    ``tolerance_minutes``
        How long the equipment may sit outside range before it counts as an
        excursion.  A gelato display case opened for every customer will
        cross its limit briefly all day; a five to fifteen minute tolerance
        keeps that noise out of the log without hiding a real failure.

    ``expected_interval_seconds``
        How often a reading is expected.  Used to detect monitoring gaps and
        to compute coverage, so set it to match the sensor's actual polling
        period rather than an aspiration.
    """

    equipment_id: str
    name: str = ""
    min_celsius: float = -30.0
    max_celsius: float = -18.0
    tolerance_minutes: float = 15.0
    expected_interval_seconds: float = 300.0
    site_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.min_celsius > self.max_celsius:
            raise ValueError(
                f"{self.equipment_id}: min_celsius ({self.min_celsius}) is above "
                f"max_celsius ({self.max_celsius})"
            )
        if self.tolerance_minutes < 0:
            raise ValueError(f"{self.equipment_id}: tolerance_minutes must be >= 0")
        if self.expected_interval_seconds <= 0:
            raise ValueError(
                f"{self.equipment_id}: expected_interval_seconds must be > 0"
            )
        if not self.name:
            self.name = self.equipment_id

    def deviation(self, celsius: float) -> float:
        """Return how far *celsius* falls outside the range, or 0.0 if inside.

        Always non-negative — pair it with :meth:`direction_of` to know which
        way the equipment drifted.
        """
        if celsius > self.max_celsius:
            return celsius - self.max_celsius
        if celsius < self.min_celsius:
            return self.min_celsius - celsius
        return 0.0

    def direction_of(self, celsius: float) -> Optional[str]:
        """Return ``ABOVE``/``BELOW`` when out of range, else ``None``."""
        if celsius > self.max_celsius:
            return ABOVE
        if celsius < self.min_celsius:
            return BELOW
        return None

    def in_range(self, celsius: float) -> bool:
        """Return True when *celsius* sits within the declared range."""
        return self.min_celsius <= celsius <= self.max_celsius


@dataclass(slots=True)
class TemperatureReading:
    """A single measurement from one sensor."""

    timestamp: datetime
    celsius: float
    equipment_id: str
    sensor_id: str = ""
    site_id: str = ""
    reading_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Naive datetimes are assumed UTC so stored epochs do not shift with
        # whatever timezone the host happens to run in.
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if not self.equipment_id:
            raise ValueError("reading requires an equipment_id")
        if not self.reading_id:
            raw = (
                f"{self.timestamp.timestamp():.3f}|{self.equipment_id}|{self.sensor_id}"
            )
            self.reading_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @property
    def epoch(self) -> float:
        """Reading time as epoch seconds."""
        return self.timestamp.timestamp()


@dataclass(slots=True)
class Excursion:
    """A qualifying deviation outside the declared range.

    ``degree_minutes`` integrates deviation over time (°C x minutes).  It is
    the number that distinguishes a shallow, long drift from a brief, severe
    spike — two failures that share a duration and a peak but do very
    different things to product.
    """

    equipment_id: str
    start: datetime
    end: datetime
    direction: str
    peak_celsius: float
    max_deviation: float
    degree_minutes: float
    reading_count: int
    site_id: str = ""

    @property
    def duration_seconds(self) -> float:
        """Length of the excursion in seconds."""
        return (self.end - self.start).total_seconds()

    @property
    def duration_minutes(self) -> float:
        """Length of the excursion in minutes."""
        return self.duration_seconds / 60.0

    def as_dict(self) -> Dict[str, Any]:
        """Return a plain dict, suitable for JSON serialization."""
        return {
            "equipment_id": self.equipment_id,
            "site_id": self.site_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "direction": self.direction,
            "duration_minutes": round(self.duration_minutes, 1),
            "peak_celsius": round(self.peak_celsius, 2),
            "max_deviation": round(self.max_deviation, 2),
            "degree_minutes": round(self.degree_minutes, 1),
            "reading_count": self.reading_count,
        }


@dataclass(slots=True)
class MonitoringGap:
    """A stretch of time with no readings for a piece of equipment."""

    equipment_id: str
    start: datetime
    end: datetime
    site_id: str = ""

    @property
    def duration_seconds(self) -> float:
        """Length of the gap in seconds."""
        return (self.end - self.start).total_seconds()

    @property
    def duration_minutes(self) -> float:
        """Length of the gap in minutes."""
        return self.duration_seconds / 60.0

    def as_dict(self) -> Dict[str, Any]:
        """Return a plain dict, suitable for JSON serialization."""
        return {
            "equipment_id": self.equipment_id,
            "site_id": self.site_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_minutes": round(self.duration_minutes, 1),
        }


@dataclass(slots=True)
class ComplianceSummary:
    """Monitoring outcome for one equipment over one period.

    ``coverage_ratio`` is the share of the period actually covered by
    readings.  An audit trail with 60% coverage is a different document from
    one with 99%, even when neither recorded an excursion.
    """

    equipment_id: str
    period_start: datetime
    period_end: datetime
    reading_count: int = 0
    coverage_ratio: float = 0.0
    in_range_ratio: Optional[float] = None
    min_celsius: Optional[float] = None
    max_celsius: Optional[float] = None
    mean_celsius: Optional[float] = None
    excursions: List[Excursion] = field(default_factory=list)
    gaps: List[MonitoringGap] = field(default_factory=list)
    site_id: str = ""
    name: str = ""

    @property
    def worst_excursion(self) -> Optional[Excursion]:
        """The excursion with the greatest thermal load, if any."""
        if not self.excursions:
            return None
        return max(self.excursions, key=lambda e: e.degree_minutes)

    @property
    def severity(self) -> str:
        """Overall verdict for the period.

        Any qualifying excursion is critical — it is a deviation the operator
        has to account for.  Incomplete monitoring with no excursion is a
        warning: nothing is known to be wrong, but the record has holes.
        """
        if self.excursions:
            return SEVERITY_CRITICAL
        if self.reading_count == 0 or self.gaps:
            return SEVERITY_WARNING
        return SEVERITY_OK

    def as_dict(self) -> Dict[str, Any]:
        """Return a plain dict, suitable for JSON serialization."""
        return {
            "equipment_id": self.equipment_id,
            "name": self.name,
            "site_id": self.site_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "reading_count": self.reading_count,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "in_range_ratio": (
                round(self.in_range_ratio, 4)
                if self.in_range_ratio is not None
                else None
            ),
            "min_celsius": self.min_celsius,
            "max_celsius": self.max_celsius,
            "mean_celsius": (
                round(self.mean_celsius, 2) if self.mean_celsius is not None else None
            ),
            "severity": self.severity,
            "excursions": [e.as_dict() for e in self.excursions],
            "gaps": [g.as_dict() for g in self.gaps],
        }


__all__ = [
    "ABOVE",
    "BELOW",
    "SEVERITY_CRITICAL",
    "SEVERITY_OK",
    "SEVERITY_WARNING",
    "ComplianceSummary",
    "EquipmentSpec",
    "Excursion",
    "MonitoringGap",
    "TemperatureReading",
]
