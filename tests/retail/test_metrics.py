"""Tests for the retail funnel math."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.retail.metrics import (
    format_summary,
    funnel,
    occupancy_series,
    opportunity_gap,
    peak_buckets,
    per_bucket,
)
from openjarvis.retail.types import FunnelMetrics, IntervalCounts

_BASE = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


def _bucket(offset_hours: int = 0, **kwargs) -> IntervalCounts:
    start = _BASE + timedelta(hours=offset_hours)
    return IntervalCounts(
        bucket_start=start, bucket_end=start + timedelta(hours=1), **kwargs
    )


def test_funnel_rates():
    metrics = funnel(
        [_bucket(passersby=1000, entries=50, transactions=20, revenue=400.0, items=60)]
    )
    assert metrics.capture_rate == 0.05
    assert metrics.conversion_rate == 0.4
    assert metrics.pass_to_sale_rate == 0.02
    assert metrics.average_ticket == 20.0
    assert metrics.items_per_transaction == 3.0
    assert metrics.revenue_per_entry == 8.0
    assert metrics.revenue_per_passerby == 0.4


def test_rates_are_none_when_denominator_is_zero():
    """A closed hour has an undefined capture rate, not a 0% one."""
    metrics = funnel([_bucket()])
    assert metrics.capture_rate is None
    assert metrics.conversion_rate is None
    assert metrics.average_ticket is None


def test_funnel_sums_totals_before_dividing():
    """Rates come from summed totals, not an average of per-bucket rates.

    The quiet bucket must not get the same weight as the busy one.
    """
    metrics = funnel(
        [
            _bucket(0, passersby=100, entries=50),
            _bucket(1, passersby=900, entries=50),
        ]
    )
    # Averaging per-bucket rates would give (0.5 + 0.0556) / 2 = 0.278.
    assert metrics.capture_rate == 100 / 1000


def test_funnel_over_empty_input():
    metrics = funnel([])
    assert metrics.passersby == 0
    assert metrics.capture_rate is None


def test_per_bucket_preserves_order():
    buckets = [
        _bucket(0, passersby=100, entries=10),
        _bucket(1, passersby=200, entries=40),
    ]
    results = per_bucket(buckets)
    assert [b.bucket_start for b, _ in results] == [
        buckets[0].bucket_start,
        buckets[1].bucket_start,
    ]
    assert results[0][1].capture_rate == 0.1
    assert results[1][1].capture_rate == 0.2


def test_occupancy_series_accumulates_and_clamps():
    buckets = [
        _bucket(0, entries=10, exits=4),
        _bucket(1, entries=2, exits=5),
        # More exits than have ever entered — miscounts must not go negative.
        _bucket(2, entries=0, exits=20),
    ]
    assert occupancy_series(buckets) == [6, 3, 0]


def test_occupancy_series_honours_start_occupancy():
    assert occupancy_series([_bucket(0, entries=1, exits=0)], start_occupancy=5) == [6]


def test_peak_buckets_sorts_descending():
    buckets = [
        _bucket(0, entries=5),
        _bucket(1, entries=30),
        _bucket(2, entries=12),
    ]
    top = peak_buckets(buckets, key="entries", limit=2)
    assert [b.entries for b in top] == [30, 12]


def test_peak_buckets_rejects_unknown_key():
    with pytest.raises(ValueError, match="no attribute"):
        peak_buckets([_bucket(0, entries=1)], key="nonsense")


def test_opportunity_gap_capture_leg():
    """Lifting capture from 5% to 6% on 1000 passersby adds 10 entries."""
    metrics = funnel(
        [_bucket(passersby=1000, entries=50, transactions=20, revenue=400.0)]
    )
    gap = opportunity_gap(metrics, target_capture_rate=0.06)
    # 10 extra entries * 0.4 conversion * R$20 ticket = R$80
    assert gap["capture_gap_revenue"] == pytest.approx(80.0)


def test_opportunity_gap_conversion_leg():
    metrics = funnel(
        [_bucket(passersby=1000, entries=50, transactions=20, revenue=400.0)]
    )
    gap = opportunity_gap(metrics, target_conversion_rate=0.5)
    # 5 extra sales * R$20 ticket = R$100
    assert gap["conversion_gap_revenue"] == pytest.approx(100.0)


def test_opportunity_gap_ignores_targets_below_current():
    """A target already met is not an opportunity."""
    metrics = funnel(
        [_bucket(passersby=1000, entries=50, transactions=20, revenue=400.0)]
    )
    gap = opportunity_gap(metrics, target_capture_rate=0.01, target_conversion_rate=0.1)
    assert gap["capture_gap_revenue"] is None
    assert gap["conversion_gap_revenue"] is None


def test_opportunity_gap_without_data():
    gap = opportunity_gap(FunnelMetrics(), target_capture_rate=0.1)
    assert gap["capture_gap_revenue"] is None


def test_format_summary_renders_undefined_rates_as_dash():
    text = format_summary(funnel([_bucket()]))
    assert "Taxa de captura (entrantes/passantes): —" in text
    assert "Passantes: 0" in text


def test_format_summary_renders_percentages_and_money():
    metrics = funnel(
        [_bucket(passersby=1000, entries=50, transactions=20, revenue=400.0)]
    )
    text = format_summary(metrics)
    assert "Taxa de captura (entrantes/passantes): 5.00%" in text
    assert "Ticket médio: R$ 20.00" in text
