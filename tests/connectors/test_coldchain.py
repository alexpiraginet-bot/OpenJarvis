"""Tests for ColdChainConnector — daily temperature compliance documents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from openjarvis.coldchain.store import ColdChainStore
from openjarvis.coldchain.types import EquipmentSpec, TemperatureReading
from openjarvis.core.registry import ConnectorRegistry

_START = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)

_FREEZER = EquipmentSpec(
    equipment_id="freezer-01",
    name="Freezer de estoque",
    min_celsius=-22.0,
    max_celsius=-18.0,
    tolerance_minutes=15.0,
    site_id="matriz",
)


def _seed(db_path, values, *, spec: EquipmentSpec = _FREEZER, start=_START) -> None:
    """Populate a store with one equipment and an evenly spaced series."""
    with ColdChainStore(db_path) as store:
        store.register_equipment(spec)
        store.record_readings(
            [
                TemperatureReading(
                    timestamp=start + timedelta(minutes=5 * index),
                    celsius=value,
                    equipment_id=spec.equipment_id,
                    site_id=spec.site_id,
                )
                for index, value in enumerate(values)
            ]
        )


def test_coldchain_registered():
    """The connector is discoverable via ConnectorRegistry."""
    from openjarvis.connectors.coldchain import ColdChainConnector

    assert ConnectorRegistry.contains("coldchain")
    cls = ConnectorRegistry.get("coldchain")
    assert cls is ColdChainConnector
    assert cls.connector_id == "coldchain"
    assert cls.display_name == "Cold Chain"
    assert cls.auth_type == "local"


def test_not_connected_without_database(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    connector = ColdChainConnector(db_path=str(tmp_path / "missing.db"))
    assert connector.is_connected() is False


def test_sync_without_database_reports_error(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    connector = ColdChainConnector(db_path=str(tmp_path / "missing.db"))
    assert list(connector.sync()) == []
    assert "no cold-chain database" in (connector.sync_status().error or "")


def test_sync_yields_one_document_per_equipment_day(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 12)

    connector = ColdChainConnector(db_path=str(db_path), site_id="matriz")
    docs = list(connector.sync(since=_START - timedelta(days=1)))

    assert len(docs) == 1
    doc = docs[0]
    assert doc.source == "coldchain"
    assert doc.doc_type == "daily_coldchain"
    assert doc.doc_id == "coldchain-freezer-01-2026-08-02"
    assert "Freezer de estoque" in doc.title


def test_clean_day_is_not_flagged_critical(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 12)

    connector = ColdChainConnector(db_path=str(db_path))
    doc = next(iter(connector.sync(since=_START - timedelta(days=1))))

    assert doc.metadata["critical"] is False
    assert "Desvios: nenhum" in doc.content


def test_excursion_day_is_flagged_and_described(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0, -17.0, -15.0, -12.0, -9.0, -6.0])

    connector = ColdChainConnector(db_path=str(db_path))
    doc = next(iter(connector.sync(since=_START - timedelta(days=1))))

    assert doc.metadata["critical"] is True
    assert doc.metadata["severity"] == "critical"
    assert "Desvio above" in doc.content
    assert "pico -6.0°C" in doc.content


def test_document_states_the_declared_range(tmp_path):
    """The report must show what it was judged against, not just the verdict."""
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 12)

    connector = ColdChainConnector(db_path=str(db_path))
    doc = next(iter(connector.sync(since=_START - timedelta(days=1))))

    assert "Faixa declarada: -22.0°C a -18.0°C" in doc.content
    assert "tolerância 15 min" in doc.content


def test_document_carries_structured_summary(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0, -17.0, -15.0, -12.0, -9.0, -6.0])

    connector = ColdChainConnector(db_path=str(db_path))
    doc = next(iter(connector.sync(since=_START - timedelta(days=1))))

    summary = json.loads(doc.metadata["summary"])
    assert summary["equipment_id"] == "freezer-01"
    assert len(summary["excursions"]) == 1
    assert summary["excursions"][0]["direction"] == "above"


def test_multiple_days_yield_separate_documents(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 6)
    _seed(db_path, [-20.0] * 6, start=_START + timedelta(days=1))

    connector = ColdChainConnector(db_path=str(db_path))
    docs = list(connector.sync(since=_START - timedelta(days=1)))

    assert [doc.metadata["day"] for doc in docs] == ["2026-08-02", "2026-08-03"]


def test_multiple_equipment_yield_separate_documents(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 6)
    _seed(
        db_path,
        [-13.0] * 6,
        spec=EquipmentSpec(
            equipment_id="vitrine-01",
            name="Vitrine",
            min_celsius=-14.0,
            max_celsius=-12.0,
            site_id="matriz",
        ),
    )

    connector = ColdChainConnector(db_path=str(db_path))
    docs = list(connector.sync(since=_START - timedelta(days=1)))

    assert sorted(doc.metadata["equipment_id"] for doc in docs) == [
        "freezer-01",
        "vitrine-01",
    ]


def test_site_filter_scopes_equipment(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 6)
    _seed(
        db_path,
        [-20.0] * 6,
        spec=EquipmentSpec(
            equipment_id="freezer-02",
            min_celsius=-22.0,
            max_celsius=-18.0,
            site_id="filial",
        ),
    )

    connector = ColdChainConnector(db_path=str(db_path), site_id="filial")
    docs = list(connector.sync(since=_START - timedelta(days=1)))

    assert len(docs) == 1
    assert docs[0].metadata["equipment_id"] == "freezer-02"


def test_equipment_without_readings_is_skipped(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    with ColdChainStore(db_path) as store:
        store.register_equipment(_FREEZER)

    connector = ColdChainConnector(db_path=str(db_path))
    assert list(connector.sync(since=_START - timedelta(days=1))) == []


def test_sync_status_tracks_progress(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 6)

    connector = ColdChainConnector(db_path=str(db_path))
    list(connector.sync(since=_START - timedelta(days=1)))

    status = connector.sync_status()
    assert status.state == "idle"
    assert status.items_synced == 1
    assert status.last_sync is not None


def test_disconnect_removes_database(tmp_path):
    from openjarvis.connectors.coldchain import ColdChainConnector

    db_path = tmp_path / "coldchain.db"
    _seed(db_path, [-20.0] * 6)

    connector = ColdChainConnector(db_path=str(db_path))
    connector.disconnect()
    assert not db_path.exists()
