"""Tests for the retail footfall domain model."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openjarvis.retail.types import (
    DIR_IN,
    DIR_PASS,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
    FootfallEvent,
    FunnelMetrics,
    IntervalCounts,
    Sale,
)


def test_event_id_is_deterministic():
    """Identical payloads collapse to the same id so replays cannot double-count."""
    moment = datetime(2026, 8, 2, 14, 3, 11, tzinfo=timezone.utc)
    first = FootfallEvent(
        timestamp=moment, zone=ZONE_ENTRANCE, direction=DIR_IN, sensor_id="door-01"
    )
    second = FootfallEvent(
        timestamp=moment, zone=ZONE_ENTRANCE, direction=DIR_IN, sensor_id="door-01"
    )
    assert first.event_id == second.event_id


def test_event_id_differs_by_sensor():
    """Two sensors counting the same instant are two real crossings."""
    moment = datetime(2026, 8, 2, 14, 3, 11, tzinfo=timezone.utc)
    door = FootfallEvent(
        timestamp=moment, zone=ZONE_ENTRANCE, direction=DIR_IN, sensor_id="door-01"
    )
    corridor = FootfallEvent(
        timestamp=moment, zone=ZONE_ENTRANCE, direction=DIR_IN, sensor_id="door-02"
    )
    assert door.event_id != corridor.event_id


def test_explicit_event_id_is_preserved():
    """A sensor that supplies its own id keeps it."""
    event = FootfallEvent(
        timestamp=datetime(2026, 8, 2, tzinfo=timezone.utc),
        zone=ZONE_CORRIDOR,
        direction=DIR_PASS,
        event_id="sensor-supplied-id",
    )
    assert event.event_id == "sensor-supplied-id"


def test_naive_timestamp_is_treated_as_utc():
    """Naive datetimes must not silently shift by the host's offset."""
    event = FootfallEvent(
        timestamp=datetime(2026, 8, 2, 14, 0, 0),
        zone=ZONE_CORRIDOR,
        direction=DIR_PASS,
    )
    assert event.timestamp.tzinfo is timezone.utc
    assert event.epoch == datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc).timestamp()


def test_rejects_unknown_zone():
    with pytest.raises(ValueError, match="unknown zone"):
        FootfallEvent(
            timestamp=datetime(2026, 8, 2, tzinfo=timezone.utc),
            zone="stockroom",
            direction=DIR_IN,
        )


def test_rejects_unknown_direction():
    with pytest.raises(ValueError, match="unknown direction"):
        FootfallEvent(
            timestamp=datetime(2026, 8, 2, tzinfo=timezone.utc),
            zone=ZONE_ENTRANCE,
            direction="sideways",
        )


def test_rejects_negative_count():
    with pytest.raises(ValueError, match="non-negative"):
        FootfallEvent(
            timestamp=datetime(2026, 8, 2, tzinfo=timezone.utc),
            zone=ZONE_ENTRANCE,
            direction=DIR_IN,
            count=-1,
        )


def test_sale_id_is_deterministic():
    moment = datetime(2026, 8, 2, 14, 3, tzinfo=timezone.utc)
    first = Sale(timestamp=moment, amount=18.5, source="stone")
    second = Sale(timestamp=moment, amount=18.5, source="stone")
    assert first.sale_id == second.sale_id


def test_net_occupancy_change():
    bucket = IntervalCounts(
        bucket_start=datetime(2026, 8, 2, 14, tzinfo=timezone.utc),
        bucket_end=datetime(2026, 8, 2, 15, tzinfo=timezone.utc),
        entries=12,
        exits=9,
    )
    assert bucket.net_occupancy_change == 3


def test_funnel_metrics_as_dict_rounds_revenue():
    metrics = FunnelMetrics(revenue=123.456, capture_rate=0.05)
    payload = metrics.as_dict()
    assert payload["revenue"] == 123.46
    assert payload["capture_rate"] == 0.05
