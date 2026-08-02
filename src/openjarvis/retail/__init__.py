"""Retail footfall analytics — passersby, entries, and conversion.

The package answers one question for a physical storefront: of the people who
walked past, how many came in, and of those, how many bought?

Layers, bottom up:

``types``
    Sensor-agnostic event model (:class:`~openjarvis.retail.types.FootfallEvent`,
    :class:`~openjarvis.retail.types.Sale`).

``ingest``
    Adapters from a manual tally sheet, a JSON webhook, or a POS export.

``store``
    Local SQLite persistence and time bucketing in the store's timezone.

``metrics``
    Capture rate, conversion rate, and the revenue gap to a target.

Nothing here assumes a particular sensor.  A store can start with a person
counting for fifteen minutes a day and later swap in a camera or radar
without changing the analytics or breaking the historical series.
"""

from openjarvis.retail.metrics import (
    format_summary,
    funnel,
    occupancy_series,
    opportunity_gap,
    peak_buckets,
    per_bucket,
)
from openjarvis.retail.store import DEFAULT_TIMEZONE, INTERVALS, FootfallStore
from openjarvis.retail.types import (
    DIR_IN,
    DIR_OUT,
    DIR_PASS,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
    FootfallEvent,
    FunnelMetrics,
    IntervalCounts,
    Sale,
)

__all__ = [
    "DEFAULT_TIMEZONE",
    "DIR_IN",
    "DIR_OUT",
    "DIR_PASS",
    "INTERVALS",
    "FootfallEvent",
    "FootfallStore",
    "FunnelMetrics",
    "IntervalCounts",
    "Sale",
    "ZONE_CORRIDOR",
    "ZONE_ENTRANCE",
    "format_summary",
    "funnel",
    "occupancy_series",
    "opportunity_gap",
    "peak_buckets",
    "per_bucket",
]
