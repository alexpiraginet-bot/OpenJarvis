"""Retail funnel math: passersby -> entries -> transactions -> revenue.

Three rates carry almost all the decision-making weight for a storefront:

``capture_rate``
    entries / passersby.  How good the storefront is at pulling people in.
    Moves with signage, lighting, product visibility, staff posture.

``conversion_rate``
    transactions / entries.  How good the operation is at closing.  Moves
    with staffing, queue length, pricing, stock.

``pass_to_sale_rate``
    transactions / passersby.  The end-to-end number, and the one that maps
    to rent: it says what a given amount of mall traffic is worth.

Separating capture from conversion is the point of counting two lines instead
of one.  A store with falling sales has a very different problem depending on
which of the two dropped, and a single door counter cannot tell them apart.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from openjarvis.retail.types import FunnelMetrics, IntervalCounts


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    """Divide, returning ``None`` rather than raising when undefined.

    See :class:`FunnelMetrics` for why ``None`` beats ``0.0`` here.
    """
    if not denominator:
        return None
    return numerator / denominator


def funnel(counts: Iterable[IntervalCounts]) -> FunnelMetrics:
    """Aggregate *counts* into a single set of funnel metrics.

    Rates are computed from the summed totals, not averaged across buckets.
    Averaging per-bucket rates would weight a dead Tuesday morning the same
    as a Saturday afternoon and quietly understate the store.
    """
    passersby = entries = transactions = items = 0
    revenue = 0.0

    for bucket in counts:
        passersby += bucket.passersby
        entries += bucket.entries
        transactions += bucket.transactions
        items += bucket.items
        revenue += bucket.revenue

    return FunnelMetrics(
        passersby=passersby,
        entries=entries,
        transactions=transactions,
        revenue=revenue,
        items=items,
        capture_rate=_safe_div(entries, passersby),
        conversion_rate=_safe_div(transactions, entries),
        pass_to_sale_rate=_safe_div(transactions, passersby),
        average_ticket=_safe_div(revenue, transactions),
        items_per_transaction=_safe_div(items, transactions),
        revenue_per_entry=_safe_div(revenue, entries),
        revenue_per_passerby=_safe_div(revenue, passersby),
    )


def per_bucket(
    counts: Iterable[IntervalCounts],
) -> List[Tuple[IntervalCounts, FunnelMetrics]]:
    """Return ``(counts, metrics)`` for each bucket, preserving order."""
    return [(bucket, funnel([bucket])) for bucket in counts]


def occupancy_series(
    counts: Sequence[IntervalCounts], *, start_occupancy: int = 0
) -> List[int]:
    """Return running occupancy at the end of each bucket.

    Occupancy is the cumulative sum of entries minus exits.  Counting error
    accumulates here — a door sensor that misses one exit per hundred
    crossings drifts upward all day — so the series is clamped at zero and is
    best read within a single trading day, reset each morning.
    """
    occupancy = start_occupancy
    series: List[int] = []
    for bucket in counts:
        occupancy = max(0, occupancy + bucket.net_occupancy_change)
        series.append(occupancy)
    return series


def peak_buckets(
    counts: Iterable[IntervalCounts],
    *,
    key: str = "entries",
    limit: int = 3,
) -> List[IntervalCounts]:
    """Return the *limit* busiest buckets, sorted descending by *key*.

    *key* is any integer/float attribute of :class:`IntervalCounts`
    (``passersby``, ``entries``, ``transactions``, ``revenue``).
    """
    buckets = list(counts)
    if buckets and not hasattr(buckets[0], key):
        raise ValueError(f"IntervalCounts has no attribute {key!r}")
    return sorted(buckets, key=lambda b: getattr(b, key), reverse=True)[:limit]


def opportunity_gap(
    metrics: FunnelMetrics,
    *,
    target_capture_rate: Optional[float] = None,
    target_conversion_rate: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """Estimate the revenue unlocked by hitting a target rate.

    This is the number that justifies spending on the storefront: it converts
    "capture rate is 4%" into "one point of capture is worth R$X over the
    period you just measured".

    The estimate holds average ticket and the downstream rate fixed, so treat
    it as an order-of-magnitude planning figure, not a forecast — lifting
    capture usually pulls in more marginal browsers, which tends to soften
    conversion rather than leave it flat.

    Returns ``None`` for any leg whose inputs are missing.
    """
    result: Dict[str, Optional[float]] = {
        "capture_gap_revenue": None,
        "conversion_gap_revenue": None,
    }

    ticket = metrics.average_ticket

    if (
        target_capture_rate is not None
        and metrics.capture_rate is not None
        and metrics.conversion_rate is not None
        and ticket is not None
        and target_capture_rate > metrics.capture_rate
    ):
        extra_entries = metrics.passersby * (target_capture_rate - metrics.capture_rate)
        result["capture_gap_revenue"] = extra_entries * metrics.conversion_rate * ticket

    if (
        target_conversion_rate is not None
        and metrics.conversion_rate is not None
        and ticket is not None
        and target_conversion_rate > metrics.conversion_rate
    ):
        extra_sales = metrics.entries * (
            target_conversion_rate - metrics.conversion_rate
        )
        result["conversion_gap_revenue"] = extra_sales * ticket

    return result


def format_summary(metrics: FunnelMetrics, *, currency: str = "R$") -> str:
    """Render *metrics* as a compact human-readable block.

    Used by the connector to build the document body an agent reads, so the
    wording is plain Portuguese rather than metric names.
    """

    def pct(value: Optional[float]) -> str:
        return f"{value * 100:.2f}%" if value is not None else "—"

    def money(value: Optional[float]) -> str:
        return f"{currency} {value:,.2f}" if value is not None else "—"

    lines = [
        f"Passantes: {metrics.passersby}",
        f"Entrantes: {metrics.entries}",
        f"Transações: {metrics.transactions}",
        f"Faturamento: {money(metrics.revenue)}",
        f"Taxa de captura (entrantes/passantes): {pct(metrics.capture_rate)}",
        f"Taxa de conversão (vendas/entrantes): {pct(metrics.conversion_rate)}",
        f"Conversão total (vendas/passantes): {pct(metrics.pass_to_sale_rate)}",
        f"Ticket médio: {money(metrics.average_ticket)}",
        f"Receita por entrante: {money(metrics.revenue_per_entry)}",
        f"Receita por passante: {money(metrics.revenue_per_passerby)}",
    ]
    return "\n".join(lines)


__all__ = [
    "format_summary",
    "funnel",
    "occupancy_series",
    "opportunity_gap",
    "peak_buckets",
    "per_bucket",
]
