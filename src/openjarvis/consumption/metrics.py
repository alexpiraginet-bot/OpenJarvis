"""Consumption maths: mass consumed, mix share, and availability correction.

The interesting function here is :func:`mix`, and the interesting column it
produces is ``demand_share``.

Raw share answers "what did we sell?".  It is also the number that quietly
sabotages production planning, because a flavour that ran out at two o'clock
contributes only the mass it managed to sell before disappearing.  Plan from
raw share and you under-produce the fastest sellers every cycle, which makes
them run out earlier, which lowers their share further.

``demand_share`` breaks that loop by ranking on depletion rate — mass per hour
*while actually available* — instead of total mass.  The two agree when
nothing ran out, and diverge exactly when something did.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from openjarvis.consumption.types import (
    MixEntry,
    ProductConsumption,
    ProductSpec,
    Replenishment,
    StockReading,
)

# Below this many grams a tub counts as empty.  Scales drift and scoops leave
# residue, so an exact zero almost never appears in real readings.
DEFAULT_STOCKOUT_THRESHOLD_GRAMS = 50.0


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    """Divide, returning ``None`` rather than raising when undefined."""
    if not denominator:
        return None
    return numerator / denominator


def _hours(start: datetime, end: datetime) -> float:
    """Return the gap between two moments in hours."""
    return (end - start).total_seconds() / 3600.0


def consume(
    readings: Sequence[StockReading],
    replenishments: Sequence[Replenishment] = (),
    *,
    period_start: datetime,
    period_end: datetime,
    stockout_threshold_grams: float = DEFAULT_STOCKOUT_THRESHOLD_GRAMS,
    specs: Optional[Dict[str, ProductSpec]] = None,
) -> List[ProductConsumption]:
    """Derive per-product consumption from consecutive stock readings.

    ``period_start`` and ``period_end`` must bound the **trading window**, not
    the calendar day. Availability is measured against this period, so handing
    in midnight-to-midnight for a shop open eight hours counts the closed
    hours as time every product was on offer — which inflates each
    availability denominator and washes out the very stockout correction
    :func:`mix` exists to make.

    Consumption is computed segment by segment — ``previous - current +
    replenished`` — rather than as a single first-to-last difference, so a tub
    topped up mid-service is accounted for instead of reading as a light day.

    A segment counts as *available* when the product had stock at its start or
    was replenished during it.  The tail after the last reading counts as
    unavailable when that reading was at or below the threshold, which is what
    captures the case this whole function exists for: a flavour gone by 14:00
    in a period that runs to 22:00.
    """
    specs = specs or {}
    by_product: Dict[str, List[StockReading]] = {}
    for reading in readings:
        if period_start <= reading.timestamp <= period_end:
            by_product.setdefault(reading.product_id, []).append(reading)

    refills_by_product: Dict[str, List[Replenishment]] = {}
    for refill in replenishments:
        if period_start <= refill.timestamp <= period_end:
            refills_by_product.setdefault(refill.product_id, []).append(refill)

    period_hours = _hours(period_start, period_end)
    results: List[ProductConsumption] = []

    for product_id, product_readings in by_product.items():
        ordered = sorted(product_readings, key=lambda r: r.timestamp)
        refills = sorted(
            refills_by_product.get(product_id, []), key=lambda r: r.timestamp
        )

        grams_consumed = 0.0
        hours_available = 0.0
        stockout = False
        anomaly = False

        # Before the first reading there is no evidence of absence, so the
        # head of the period counts as available.
        hours_available += _hours(period_start, ordered[0].timestamp)

        for previous, current in zip(ordered, ordered[1:]):
            added = sum(
                refill.grams
                for refill in refills
                if previous.timestamp < refill.timestamp <= current.timestamp
            )
            delta = previous.grams + added - current.grams
            if delta < 0:
                # More product than could possibly be there: someone topped
                # the tub up without recording it. The true consumption is
                # unknowable, so contribute nothing and say so.
                anomaly = True
                delta = 0.0
            grams_consumed += delta

            segment_hours = _hours(previous.timestamp, current.timestamp)
            if previous.grams > stockout_threshold_grams or added > 0:
                hours_available += segment_hours
            else:
                stockout = True

        # The tail: an empty tub at the last reading stays empty.
        tail_hours = _hours(ordered[-1].timestamp, period_end)
        if ordered[-1].grams > stockout_threshold_grams:
            hours_available += tail_hours
        elif tail_hours > 0:
            stockout = True

        spec = specs.get(product_id)
        results.append(
            ProductConsumption(
                product_id=product_id,
                name=spec.name if spec else product_id,
                site_id=spec.site_id if spec else "",
                grams_consumed=grams_consumed,
                hours_available=hours_available,
                period_hours=period_hours,
                reading_count=len(ordered),
                stockout=stockout,
                unrecorded_replenishment=anomaly,
            )
        )

    return sorted(results, key=lambda c: c.grams_consumed, reverse=True)


def mix(
    consumptions: Iterable[ProductConsumption],
    *,
    specs: Optional[Dict[str, ProductSpec]] = None,
    by: str = "share",
) -> List[MixEntry]:
    """Turn per-product consumption into shares of the whole.

    ``by`` orders the result: ``"share"`` ranks by what was actually consumed,
    ``"demand_share"`` by availability-corrected rate — the order to plan
    production from.

    Shares are ``None``, never ``0.0``, when there is nothing to divide by. A
    period with no consumption has an undefined mix, and zeros would average
    into later reports as though they were measurements.
    """
    if by not in ("share", "demand_share"):
        raise ValueError(f"unknown ordering {by!r}; expected share or demand_share")

    specs = specs or {}
    items = list(consumptions)
    total_grams = sum(item.grams_consumed for item in items)
    total_rate = sum(
        item.grams_per_hour for item in items if item.grams_per_hour is not None
    )

    entries: List[MixEntry] = []
    for item in items:
        spec = specs.get(item.product_id)
        rate = item.grams_per_hour
        portion = spec.portion_grams if spec else None

        entries.append(
            MixEntry(
                product_id=item.product_id,
                name=item.name or (spec.name if spec else item.product_id),
                grams_consumed=item.grams_consumed,
                share=_safe_div(item.grams_consumed, total_grams),
                demand_share=(
                    _safe_div(rate, total_rate) if rate is not None else None
                ),
                grams_per_hour=rate,
                availability_ratio=item.availability_ratio,
                stockout=item.stockout,
                unrecorded_replenishment=item.unrecorded_replenishment,
                estimated_servings=(item.grams_consumed / portion if portion else None),
            )
        )

    return sorted(
        entries,
        key=lambda entry: getattr(entry, by) or 0.0,
        reverse=True,
    )


def time_to_empty_hours(
    current_grams: float, grams_per_hour: Optional[float]
) -> Optional[float]:
    """Hours until a product runs out at its recent depletion rate.

    ``None`` when the rate is unknown or not positive — a product that is not
    moving has no meaningful time to empty, and reporting a huge number would
    read as a prediction rather than as an absence of one.
    """
    if grams_per_hour is None or grams_per_hour <= 0:
        return None
    return current_grams / grams_per_hour


def format_mix(entries: Sequence[MixEntry], *, limit: int = 10) -> str:
    """Render a mix as a readable ranking in Portuguese.

    Shares are shown to whole percentages. Measured mass carries real
    uncertainty — a scale reading twice a day, or a camera estimate — and a
    decimal place would advertise precision the input does not have.
    """

    def pct(value: Optional[float]) -> str:
        return f"{value * 100:.0f}%" if value is not None else "—"

    if not entries:
        return "Sem consumo registrado no período."

    lines = []
    for entry in entries[:limit]:
        line = (
            f"{entry.name}: {pct(entry.share)} ({entry.grams_consumed / 1000:.1f} kg)"
        )
        if entry.suppressed:
            line += f" — esgotou; demanda real ~{pct(entry.demand_share)}"
        elif entry.stockout:
            line += " — esgotou"
        if entry.unrecorded_replenishment:
            line += " [reposição não registrada]"
        lines.append(line)

    hidden = len(entries) - len(lines)
    if hidden > 0:
        lines.append(f"(+{hidden} outros)")

    return "\n".join(lines)


__all__ = [
    "DEFAULT_STOCKOUT_THRESHOLD_GRAMS",
    "consume",
    "format_mix",
    "mix",
    "time_to_empty_hours",
]
