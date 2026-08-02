"""Tests for the footfall SQLite store and its time bucketing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.retail.store import FootfallStore
from openjarvis.retail.types import (
    DIR_IN,
    DIR_OUT,
    DIR_PASS,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
    FootfallEvent,
    Sale,
)

# 14:00 in São Paulo (UTC-3) is 17:00 UTC — the offset is what makes the
# timezone-anchored bucketing tests meaningful.
_SP = timezone(timedelta(hours=-3))


@pytest.fixture()
def store(tmp_path):
    with FootfallStore(tmp_path / "footfall.db") as instance:
        yield instance


def _event(hour: int, zone: str, direction: str, count: int = 1, **kwargs):
    return FootfallEvent(
        timestamp=datetime(2026, 8, 2, hour, 0, tzinfo=_SP),
        zone=zone,
        direction=direction,
        count=count,
        **kwargs,
    )


def test_schema_created_on_init(tmp_path):
    path = tmp_path / "nested" / "footfall.db"
    with FootfallStore(path):
        pass
    assert path.exists()


def test_record_events_is_idempotent(store):
    event = _event(14, ZONE_ENTRANCE, DIR_IN, sensor_id="door-01")
    assert store.record_event(event) == 1
    assert store.record_event(event) == 0
    assert len(store.raw_events()) == 1


def test_replayed_batch_does_not_double_count(store):
    """A sensor flushing its buffer twice must not inflate the numbers."""
    batch = [
        _event(14, ZONE_ENTRANCE, DIR_IN, sensor_id="door-01"),
        _event(15, ZONE_ENTRANCE, DIR_IN, sensor_id="door-01"),
    ]
    assert store.record_events(batch) == 2
    assert store.record_events(batch) == 0

    buckets = store.intervals(interval="day")
    assert buckets[0].entries == 2


def test_record_events_with_empty_iterable(store):
    assert store.record_events([]) == 0
    assert store.record_sales([]) == 0


def test_intervals_split_zones_and_directions(store):
    store.record_events(
        [
            _event(14, ZONE_CORRIDOR, DIR_PASS, count=120),
            _event(14, ZONE_ENTRANCE, DIR_IN, count=8),
            _event(14, ZONE_ENTRANCE, DIR_OUT, count=5),
        ]
    )
    buckets = store.intervals(interval="hour")
    assert len(buckets) == 1
    assert buckets[0].passersby == 120
    assert buckets[0].entries == 8
    assert buckets[0].exits == 5


def test_sales_join_the_same_buckets(store):
    store.record_events([_event(14, ZONE_ENTRANCE, DIR_IN, count=10)])
    store.record_sales(
        [
            Sale(
                timestamp=datetime(2026, 8, 2, 14, 30, tzinfo=_SP),
                amount=18.0,
                items=2,
                source="stone",
            ),
            Sale(
                timestamp=datetime(2026, 8, 2, 14, 45, tzinfo=_SP),
                amount=22.0,
                items=3,
                source="stone",
            ),
        ]
    )
    buckets = store.intervals(interval="hour")
    assert len(buckets) == 1
    assert buckets[0].transactions == 2
    assert buckets[0].revenue == 40.0
    assert buckets[0].items == 5


def test_buckets_are_anchored_to_store_timezone(tmp_path):
    """A 23:00 local sale belongs to the local trading day, not the UTC one."""
    with FootfallStore(
        tmp_path / "footfall.db", timezone_name="America/Sao_Paulo"
    ) as store:
        # 23:30 in São Paulo is 02:30 UTC the *next* calendar day.
        store.record_events(
            [
                FootfallEvent(
                    timestamp=datetime(2026, 8, 2, 23, 30, tzinfo=_SP),
                    zone=ZONE_ENTRANCE,
                    direction=DIR_IN,
                )
            ]
        )
        days = store.intervals(interval="day")

    assert len(days) == 1
    assert days[0].bucket_start.strftime("%Y-%m-%d") == "2026-08-02"


def test_hour_buckets_are_separate(store):
    store.record_events(
        [
            _event(14, ZONE_ENTRANCE, DIR_IN, count=3),
            _event(16, ZONE_ENTRANCE, DIR_IN, count=7),
        ]
    )
    buckets = store.intervals(interval="hour")
    assert [b.entries for b in buckets] == [3, 7]


def test_fill_empty_inserts_gap_buckets(store):
    store.record_events(
        [
            _event(14, ZONE_ENTRANCE, DIR_IN, count=3),
            _event(17, ZONE_ENTRANCE, DIR_IN, count=7),
        ]
    )
    sparse = store.intervals(interval="hour")
    dense = store.intervals(interval="hour", fill_empty=True)

    assert len(sparse) == 2
    # 14h, 15h, 16h, 17h — the two quiet hours are materialized as zeros.
    assert len(dense) == 4
    assert [b.entries for b in dense] == [3, 0, 0, 7]


def test_fifteen_minute_buckets(store):
    store.record_events(
        [
            FootfallEvent(
                timestamp=datetime(2026, 8, 2, 14, 7, tzinfo=_SP),
                zone=ZONE_ENTRANCE,
                direction=DIR_IN,
            ),
            FootfallEvent(
                timestamp=datetime(2026, 8, 2, 14, 22, tzinfo=_SP),
                zone=ZONE_ENTRANCE,
                direction=DIR_IN,
            ),
        ]
    )
    buckets = store.intervals(interval="15min")
    assert len(buckets) == 2
    assert buckets[0].bucket_start.minute == 0
    assert buckets[1].bucket_start.minute == 15


def test_intervals_filter_by_site(store):
    store.record_events(
        [
            _event(14, ZONE_ENTRANCE, DIR_IN, count=5, site_id="matriz"),
            _event(14, ZONE_ENTRANCE, DIR_IN, count=9, site_id="filial"),
        ]
    )
    matriz = store.intervals(interval="day", site_id="matriz")
    assert len(matriz) == 1
    assert matriz[0].entries == 5


def test_intervals_filter_by_time_range(store):
    store.record_events(
        [
            _event(10, ZONE_ENTRANCE, DIR_IN, count=1),
            _event(20, ZONE_ENTRANCE, DIR_IN, count=1),
        ]
    )
    buckets = store.intervals(
        start=datetime(2026, 8, 2, 18, tzinfo=_SP), interval="day"
    )
    assert len(buckets) == 1
    assert buckets[0].entries == 1


def test_intervals_rejects_unknown_interval(store):
    with pytest.raises(ValueError, match="unknown interval"):
        store.intervals(interval="fortnight")


def test_intervals_on_empty_store(store):
    assert store.intervals(interval="day") == []
    assert store.intervals(interval="day", fill_empty=True) == []


def test_sites_lists_distinct_ids(store):
    store.record_events(
        [
            _event(14, ZONE_ENTRANCE, DIR_IN, site_id="matriz"),
            _event(15, ZONE_ENTRANCE, DIR_IN, site_id="filial"),
        ]
    )
    store.record_sales(
        [
            Sale(
                timestamp=datetime(2026, 8, 2, 16, tzinfo=_SP),
                amount=5.0,
                site_id="quiosque",
            )
        ]
    )
    assert store.sites() == ["filial", "matriz", "quiosque"]
