"""Per-product consumption and mix, measured by mass.

Built for an operation whose point of sale does not break the product down —
a gelateria that rings up "copo pequeno" without recording which flavours went
into it. The mix is invisible in sales data, and the only way to recover it is
to measure what left the tub.

The correction that matters is availability. A flavour that ran out at 14:00
shows low consumption because it was absent, not because it was unpopular, so
planning production from raw share under-produces the fastest sellers every
cycle — which makes them run out earlier, which lowers their share further.
:func:`~openjarvis.consumption.metrics.mix` breaks that loop by also reporting
``demand_share``, ranked on depletion rate while actually available.

Layers, bottom up:

``types``
    :class:`~openjarvis.consumption.types.StockReading` (what is left),
    :class:`~openjarvis.consumption.types.Replenishment` (what was added), and
    the derived :class:`~openjarvis.consumption.types.MixEntry`.

``ingest``
    Adapters from a weighing sheet, a scale or camera payload, and product
    definitions.

``store``
    Local SQLite persistence.

``metrics``
    Consumption, mix share, availability correction, and time to empty.

Nothing assumes how the mass was measured. A tub weighed twice a day and a
camera estimating fill level produce the same
:class:`~openjarvis.consumption.types.StockReading`, so an operation can start
on a kitchen scale and add vision later without breaking the series — and the
scale readings are what make the camera trustworthy, since without them there
is no ground truth to calibrate against.
"""

from openjarvis.consumption.metrics import (
    DEFAULT_STOCKOUT_THRESHOLD_GRAMS,
    consume,
    format_mix,
    mix,
    time_to_empty_hours,
)
from openjarvis.consumption.store import ConsumptionStore
from openjarvis.consumption.types import (
    KIND_CLOSE,
    KIND_OPEN,
    KIND_SPOT,
    SOURCE_CAMERA,
    SOURCE_MANUAL,
    SOURCE_SCALE,
    MixEntry,
    ProductConsumption,
    ProductSpec,
    Replenishment,
    StockReading,
)

__all__ = [
    "DEFAULT_STOCKOUT_THRESHOLD_GRAMS",
    "KIND_CLOSE",
    "KIND_OPEN",
    "KIND_SPOT",
    "SOURCE_CAMERA",
    "SOURCE_MANUAL",
    "SOURCE_SCALE",
    "ConsumptionStore",
    "MixEntry",
    "ProductConsumption",
    "ProductSpec",
    "Replenishment",
    "StockReading",
    "consume",
    "format_mix",
    "mix",
    "time_to_empty_hours",
]
