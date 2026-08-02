"""Tests for RetailFootfallConnector — local footfall/conversion documents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from openjarvis.core.registry import ConnectorRegistry
from openjarvis.retail.store import FootfallStore
from openjarvis.retail.types import (
    DIR_IN,
    DIR_PASS,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
    FootfallEvent,
    Sale,
)

_SP = timezone(timedelta(hours=-3))


def _seed(db_path, *, site_id: str = "matriz") -> None:
    """Populate a store with one afternoon of traffic and two sales."""
    with FootfallStore(db_path) as store:
        store.record_events(
            [
                FootfallEvent(
                    timestamp=datetime(2026, 8, 2, 14, tzinfo=_SP),
                    zone=ZONE_CORRIDOR,
                    direction=DIR_PASS,
                    count=200,
                    site_id=site_id,
                ),
                FootfallEvent(
                    timestamp=datetime(2026, 8, 2, 14, tzinfo=_SP),
                    zone=ZONE_ENTRANCE,
                    direction=DIR_IN,
                    count=10,
                    site_id=site_id,
                ),
                FootfallEvent(
                    timestamp=datetime(2026, 8, 2, 16, tzinfo=_SP),
                    zone=ZONE_CORRIDOR,
                    direction=DIR_PASS,
                    count=100,
                    site_id=site_id,
                ),
                FootfallEvent(
                    timestamp=datetime(2026, 8, 2, 16, tzinfo=_SP),
                    zone=ZONE_ENTRANCE,
                    direction=DIR_IN,
                    count=20,
                    site_id=site_id,
                ),
            ]
        )
        store.record_sales(
            [
                Sale(
                    timestamp=datetime(2026, 8, 2, 14, 30, tzinfo=_SP),
                    amount=18.0,
                    items=2,
                    site_id=site_id,
                    source="stone",
                ),
                Sale(
                    timestamp=datetime(2026, 8, 2, 16, 30, tzinfo=_SP),
                    amount=22.0,
                    items=1,
                    site_id=site_id,
                    source="stone",
                ),
            ]
        )


def test_retail_footfall_registered():
    """The connector is discoverable via ConnectorRegistry."""
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    assert ConnectorRegistry.contains("retail_footfall")
    cls = ConnectorRegistry.get("retail_footfall")
    assert cls is RetailFootfallConnector
    assert cls.connector_id == "retail_footfall"
    assert cls.display_name == "Retail Footfall"
    assert cls.auth_type == "local"


def test_not_connected_without_database(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    connector = RetailFootfallConnector(db_path=str(tmp_path / "missing.db"))
    assert connector.is_connected() is False


def test_sync_without_database_reports_error(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    connector = RetailFootfallConnector(db_path=str(tmp_path / "missing.db"))
    assert list(connector.sync()) == []
    assert "no footfall database" in (connector.sync_status().error or "")


def test_sync_yields_one_document_per_day(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    db_path = tmp_path / "footfall.db"
    _seed(db_path)

    connector = RetailFootfallConnector(db_path=str(db_path), site_id="matriz")
    assert connector.is_connected() is True

    docs = list(connector.sync(since=datetime(2026, 8, 1, tzinfo=_SP)))
    assert len(docs) == 1

    doc = docs[0]
    assert doc.source == "retail_footfall"
    assert doc.doc_type == "daily_footfall"
    assert doc.doc_id == "retail_footfall-matriz-2026-08-02"
    assert "2026-08-02" in doc.title


def test_document_carries_funnel_metrics(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    db_path = tmp_path / "footfall.db"
    _seed(db_path)

    connector = RetailFootfallConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=datetime(2026, 8, 1, tzinfo=_SP))))

    metrics = doc.metadata["metrics"]
    assert metrics["passersby"] == 300
    assert metrics["entries"] == 30
    assert metrics["transactions"] == 2
    assert metrics["revenue"] == 40.0
    assert metrics["capture_rate"] == 0.1
    # 2 sales / 30 entries
    assert metrics["conversion_rate"] == 2 / 30


def test_document_body_is_readable(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    db_path = tmp_path / "footfall.db"
    _seed(db_path)

    connector = RetailFootfallConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=datetime(2026, 8, 1, tzinfo=_SP))))

    assert "Passantes: 300" in doc.content
    assert "Taxa de captura (entrantes/passantes): 10.00%" in doc.content
    assert "Horários de pico:" in doc.content
    # 16h converts 20/100; 14h only 10/200 — the better hour must win.
    assert "Melhor captura: 16h–17h (20.0%)" in doc.content


def test_document_carries_hourly_breakdown(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    db_path = tmp_path / "footfall.db"
    _seed(db_path)

    connector = RetailFootfallConnector(db_path=str(db_path), site_id="matriz")
    doc = next(iter(connector.sync(since=datetime(2026, 8, 1, tzinfo=_SP))))

    hourly = json.loads(doc.metadata["hourly"])
    assert [row["hour"] for row in hourly] == ["14:00", "16:00"]
    assert hourly[1]["entries"] == 20


def test_sync_status_tracks_progress(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    db_path = tmp_path / "footfall.db"
    _seed(db_path)

    connector = RetailFootfallConnector(db_path=str(db_path), site_id="matriz")
    list(connector.sync(since=datetime(2026, 8, 1, tzinfo=_SP)))

    status = connector.sync_status()
    assert status.state == "idle"
    assert status.items_synced == 1
    assert status.last_sync is not None


def test_disconnect_removes_database(tmp_path):
    from openjarvis.connectors.retail_footfall import RetailFootfallConnector

    db_path = tmp_path / "footfall.db"
    _seed(db_path)

    connector = RetailFootfallConnector(db_path=str(db_path))
    connector.disconnect()
    assert not db_path.exists()
