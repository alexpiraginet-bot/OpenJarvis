"""Tests for the consumption SQLite store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.consumption.store import ConsumptionStore
from openjarvis.consumption.types import (
    KIND_CLOSE,
    KIND_OPEN,
    SOURCE_CAMERA,
    ProductSpec,
    Replenishment,
    StockReading,
)

_OPEN = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path):
    with ConsumptionStore(tmp_path / "consumption.db") as instance:
        yield instance


def _reading(hours: float, product_id: str, grams: float, **kwargs):
    return StockReading(
        timestamp=_OPEN + timedelta(hours=hours),
        product_id=product_id,
        grams=grams,
        **kwargs,
    )


def test_schema_created_on_init(tmp_path):
    path = tmp_path / "nested" / "consumption.db"
    with ConsumptionStore(path):
        pass
    assert path.exists()


def test_register_and_list_products(store):
    store.register_product(
        ProductSpec("pistache", name="Pistache siciliano", portion_grams=90.0)
    )
    products = store.products()

    assert "pistache" in products
    assert products["pistache"].name == "Pistache siciliano"
    assert products["pistache"].portion_grams == 90.0


def test_register_product_upserts(store):
    store.register_product(ProductSpec("pistache", portion_grams=90.0))
    store.register_product(ProductSpec("pistache", portion_grams=80.0))

    products = store.products()
    assert len(products) == 1
    assert products["pistache"].portion_grams == 80.0


def test_products_filtered_by_site(store):
    store.register_product(ProductSpec("pistache", site_id="matriz"))
    store.register_product(ProductSpec("chocolate", site_id="filial"))
    assert list(store.products(site_id="matriz")) == ["pistache"]


def test_product_without_portion_is_allowed(store):
    store.register_product(ProductSpec("chocolate"))
    assert store.products()["chocolate"].portion_grams is None


def test_record_readings_is_idempotent(store):
    reading = _reading(0, "pistache", 3000, kind=KIND_OPEN)
    assert store.record_reading(reading) == 1
    assert store.record_reading(reading) == 0
    assert len(store.readings()) == 1


def test_replayed_batch_does_not_duplicate(store):
    batch = [
        _reading(0, "pistache", 3000, kind=KIND_OPEN),
        _reading(12, "pistache", 500, kind=KIND_CLOSE),
    ]
    assert store.record_readings(batch) == 2
    assert store.record_readings(batch) == 0


def test_record_empty_iterables(store):
    assert store.record_readings([]) == 0
    assert store.record_replenishments([]) == 0


def test_readings_round_trip(store):
    store.record_readings(
        [_reading(0, "pistache", 3000, kind=KIND_OPEN, source=SOURCE_CAMERA)]
    )
    loaded = store.readings()

    assert len(loaded) == 1
    assert loaded[0].grams == 3000
    assert loaded[0].kind == KIND_OPEN
    assert loaded[0].source == SOURCE_CAMERA
    assert loaded[0].timestamp == _OPEN


def test_readings_filtered_by_product(store):
    store.record_readings(
        [_reading(0, "pistache", 3000), _reading(0, "chocolate", 5000)]
    )
    assert len(store.readings(product_id="chocolate")) == 1


def test_readings_filtered_by_time(store):
    store.record_readings(
        [_reading(0, "pistache", 3000), _reading(12, "pistache", 400)]
    )
    recent = store.readings(start=_OPEN + timedelta(hours=6))
    assert len(recent) == 1
    assert recent[0].grams == 400


def test_readings_are_ordered_oldest_first(store):
    store.record_readings(
        [_reading(12, "pistache", 400), _reading(0, "pistache", 3000)]
    )
    assert [r.grams for r in store.readings()] == [3000, 400]


def test_replenishments_round_trip(store):
    store.record_replenishments(
        [
            Replenishment(
                timestamp=_OPEN + timedelta(hours=5),
                product_id="pistache",
                grams=3000,
            )
        ]
    )
    loaded = store.replenishments()
    assert len(loaded) == 1
    assert loaded[0].grams == 3000
    assert loaded[0].product_id == "pistache"


def test_replenishments_are_idempotent(store):
    refill = Replenishment(
        timestamp=_OPEN + timedelta(hours=5), product_id="pistache", grams=3000
    )
    assert store.record_replenishments([refill]) == 1
    assert store.record_replenishments([refill]) == 0


def test_latest_reading(store):
    store.record_readings(
        [_reading(0, "pistache", 3000), _reading(12, "pistache", 400)]
    )
    latest = store.latest_reading("pistache")
    assert latest is not None
    assert latest.grams == 400


def test_latest_reading_without_data(store):
    assert store.latest_reading("pistache") is None
