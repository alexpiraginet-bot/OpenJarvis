"""Tests for ConsumptionConnector — daily product-mix documents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.consumption.store import ConsumptionStore
from openjarvis.consumption.types import (
    KIND_CLOSE,
    KIND_OPEN,
    ProductSpec,
    StockReading,
)
from openjarvis.core.registry import ConnectorRegistry

_OPEN = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)


def _seed(db_path, *, site_id: str = "matriz") -> None:
    """Seed a store with the reversal case: pistachio runs out at midday."""
    with ConsumptionStore(db_path) as store:
        for product_id, name in (
            ("pistache", "Pistache"),
            ("chocolate", "Chocolate"),
        ):
            store.register_product(
                ProductSpec(product_id, name=name, portion_grams=90.0, site_id=site_id)
            )
        store.record_readings(
            [
                StockReading(
                    timestamp=_OPEN,
                    product_id="pistache",
                    grams=3000,
                    kind=KIND_OPEN,
                    site_id=site_id,
                ),
                StockReading(
                    timestamp=_OPEN + timedelta(hours=4),
                    product_id="pistache",
                    grams=20,
                    site_id=site_id,
                ),
                StockReading(
                    timestamp=_OPEN,
                    product_id="chocolate",
                    grams=5000,
                    kind=KIND_OPEN,
                    site_id=site_id,
                ),
                StockReading(
                    timestamp=_OPEN + timedelta(hours=12),
                    product_id="chocolate",
                    grams=1000,
                    kind=KIND_CLOSE,
                    site_id=site_id,
                ),
            ]
        )


def test_consumption_registered():
    from openjarvis.connectors.consumption import ConsumptionConnector

    assert ConnectorRegistry.contains("consumption")
    cls = ConnectorRegistry.get("consumption")
    assert cls is ConsumptionConnector
    assert cls.connector_id == "consumption"
    assert cls.display_name == "Product Consumption"
    assert cls.auth_type == "local"


def test_not_connected_without_database(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    connector = ConsumptionConnector(db_path=str(tmp_path / "missing.db"))
    assert connector.is_connected() is False


def test_sync_without_database_reports_error(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    connector = ConsumptionConnector(db_path=str(tmp_path / "missing.db"))
    assert list(connector.sync()) == []
    assert "no consumption database" in (connector.sync_status().error or "")


def test_sync_yields_one_document_per_day(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    docs = list(connector.sync(since=_OPEN - timedelta(days=1)))

    assert len(docs) == 1
    doc = docs[0]
    assert doc.source == "consumption"
    assert doc.doc_type == "daily_consumption"
    assert doc.doc_id == "consumption-matriz-2026-08-02"
    assert "2026-08-02" in doc.title


def test_document_reports_the_mix(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=_OPEN - timedelta(days=1))))

    assert "Consumo total: 7.0 kg" in doc.content
    assert "Chocolate: 57%" in doc.content
    assert "Pistache: 43%" in doc.content


def test_document_explains_a_suppressed_flavour(tmp_path):
    """The whole point: 43% understates pistachio because it ran out."""
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=_OPEN - timedelta(days=1))))

    assert "esgotou; demanda real ~69%" in doc.content
    assert "Sabores reprimidos por ruptura: Pistache" in doc.content
    assert doc.metadata["has_stockout"] is True


def test_window_comes_from_readings_not_the_calendar_day(tmp_path):
    """Closed hours must not count as time the product was on offer.

    With a midnight-to-midnight window the eight hours before opening would
    inflate every availability denominator and drag pistachio's corrected
    demand from 69% down toward the raw 43%.
    """
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=_OPEN - timedelta(days=1))))

    entries = {row["product_id"]: row for row in json.loads(doc.metadata["mix"])}
    # Chocolate spans the whole 08:00-20:00 window.
    assert entries["chocolate"]["availability_ratio"] == 1.0
    # Pistachio was gone after four of those twelve hours.
    assert entries["pistache"]["availability_ratio"] == pytest.approx(4 / 12)


def test_document_carries_structured_mix(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=_OPEN - timedelta(days=1))))

    entries = {row["product_id"]: row for row in json.loads(doc.metadata["mix"])}
    assert entries["pistache"]["suppressed"] is True
    assert entries["chocolate"]["suppressed"] is False
    assert entries["pistache"]["demand_share"] > entries["pistache"]["share"]
    # 2980 g at a 90 g portion
    assert entries["pistache"]["estimated_servings"] == round(2980 / 90, 1)


def test_multiple_days_yield_separate_documents(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)
    with ConsumptionStore(db_path) as store:
        store.record_readings(
            [
                StockReading(
                    timestamp=_OPEN + timedelta(days=1),
                    product_id="chocolate",
                    grams=4000,
                    kind=KIND_OPEN,
                    site_id="matriz",
                ),
                StockReading(
                    timestamp=_OPEN + timedelta(days=1, hours=12),
                    product_id="chocolate",
                    grams=900,
                    kind=KIND_CLOSE,
                    site_id="matriz",
                ),
            ]
        )

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    docs = list(connector.sync(since=_OPEN - timedelta(days=1)))
    assert [doc.metadata["day"] for doc in docs] == ["2026-08-02", "2026-08-03"]


def test_site_filter_scopes_readings(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path, site_id="matriz")

    connector = ConsumptionConnector(db_path=str(db_path), site_id="filial")
    assert list(connector.sync(since=_OPEN - timedelta(days=1))) == []


def test_sync_status_tracks_progress(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path), site_id="matriz")
    list(connector.sync(since=_OPEN - timedelta(days=1)))

    status = connector.sync_status()
    assert status.state == "idle"
    assert status.items_synced == 1
    assert status.last_sync is not None


def test_disconnect_removes_database(tmp_path):
    from openjarvis.connectors.consumption import ConsumptionConnector

    db_path = tmp_path / "consumption.db"
    _seed(db_path)

    connector = ConsumptionConnector(db_path=str(db_path))
    connector.disconnect()
    assert not db_path.exists()
