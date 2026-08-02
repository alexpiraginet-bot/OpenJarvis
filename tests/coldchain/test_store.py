"""Tests for the cold-chain SQLite store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.coldchain.store import ColdChainStore
from openjarvis.coldchain.types import EquipmentSpec, TemperatureReading

_START = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path):
    with ColdChainStore(tmp_path / "coldchain.db") as instance:
        yield instance


def _spec(equipment_id: str = "freezer-01", **kwargs) -> EquipmentSpec:
    defaults = {
        "name": "Freezer de estoque",
        "min_celsius": -22.0,
        "max_celsius": -18.0,
    }
    defaults.update(kwargs)
    return EquipmentSpec(equipment_id=equipment_id, **defaults)


def _reading(minute: int, celsius: float, **kwargs) -> TemperatureReading:
    return TemperatureReading(
        timestamp=_START + timedelta(minutes=minute),
        celsius=celsius,
        equipment_id=kwargs.pop("equipment_id", "freezer-01"),
        **kwargs,
    )


def test_schema_created_on_init(tmp_path):
    path = tmp_path / "nested" / "coldchain.db"
    with ColdChainStore(path):
        pass
    assert path.exists()


def test_register_and_read_equipment(store):
    store.register_equipment(_spec())
    loaded = store.get_equipment("freezer-01")

    assert loaded is not None
    assert loaded.name == "Freezer de estoque"
    assert loaded.min_celsius == -22.0
    assert loaded.max_celsius == -18.0


def test_register_equipment_upserts(store):
    """A revised procedure changes the setpoint without duplicating the row."""
    store.register_equipment(_spec())
    store.register_equipment(_spec(max_celsius=-16.0, name="Freezer revisado"))

    equipment = store.equipment()
    assert len(equipment) == 1
    assert equipment[0].max_celsius == -16.0
    assert equipment[0].name == "Freezer revisado"


def test_get_unknown_equipment_returns_none(store):
    assert store.get_equipment("nao-existe") is None


def test_equipment_filtered_by_site(store):
    store.register_equipment(_spec("freezer-01", site_id="matriz"))
    store.register_equipment(_spec("freezer-02", site_id="filial"))

    matriz = store.equipment(site_id="matriz")
    assert [spec.equipment_id for spec in matriz] == ["freezer-01"]
    assert len(store.equipment()) == 2


def test_record_readings_is_idempotent(store):
    reading = _reading(0, -20.0, sensor_id="ds18b20-a")
    assert store.record_reading(reading) == 1
    assert store.record_reading(reading) == 0
    assert len(store.readings()) == 1


def test_replayed_batch_does_not_duplicate(store):
    """A probe reconnecting and flushing its buffer must not double-write."""
    batch = [_reading(0, -20.0), _reading(5, -20.1), _reading(10, -19.9)]
    assert store.record_readings(batch) == 3
    assert store.record_readings(batch) == 0
    assert len(store.readings()) == 3


def test_record_empty_iterable(store):
    assert store.record_readings([]) == 0


def test_readings_round_trip_preserves_values(store):
    store.record_readings([_reading(0, -20.5, sensor_id="probe-a")])
    loaded = store.readings()

    assert len(loaded) == 1
    assert loaded[0].celsius == -20.5
    assert loaded[0].sensor_id == "probe-a"
    assert loaded[0].timestamp == _START


def test_readings_filtered_by_equipment(store):
    store.record_readings(
        [
            _reading(0, -20.0, equipment_id="freezer-01"),
            _reading(0, -13.0, equipment_id="vitrine-01"),
        ]
    )
    assert len(store.readings(equipment_id="vitrine-01")) == 1


def test_readings_filtered_by_time_range(store):
    store.record_readings([_reading(0, -20.0), _reading(120, -20.1)])
    recent = store.readings(start=_START + timedelta(hours=1))
    assert len(recent) == 1
    assert recent[0].celsius == -20.1


def test_readings_are_ordered_oldest_first(store):
    store.record_readings([_reading(10, -19.0), _reading(0, -21.0)])
    values = [r.celsius for r in store.readings()]
    assert values == [-21.0, -19.0]


def test_latest_reading(store):
    store.record_readings([_reading(0, -21.0), _reading(10, -19.0)])
    latest = store.latest_reading("freezer-01")
    assert latest is not None
    assert latest.celsius == -19.0


def test_latest_reading_without_data(store):
    assert store.latest_reading("freezer-01") is None
