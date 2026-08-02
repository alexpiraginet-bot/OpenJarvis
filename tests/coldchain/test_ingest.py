"""Tests for the cold-chain ingest adapters."""

from __future__ import annotations

import pytest

from openjarvis.coldchain.ingest import (
    reading_from_payload,
    reading_from_row,
    readings_from_csv,
    spec_from_dict,
)


def test_reading_from_manual_log_row():
    reading = reading_from_row(
        {
            "data": "2026-08-02",
            "hora": "08:00",
            "equipamento": "freezer-01",
            "temperatura": "-19,5",
        },
        site_id="matriz",
    )
    assert reading.celsius == pytest.approx(-19.5)
    assert reading.equipment_id == "freezer-01"
    assert reading.site_id == "matriz"
    assert reading.sensor_id == "manual"
    assert reading.timestamp.hour == 8


def test_comma_decimal_and_degree_suffix_are_parsed():
    """A handwritten log digitized in a Brazilian sheet gives `-19,5 °C`."""
    reading = reading_from_row(
        {"data": "2026-08-02", "equipamento": "f1", "temperatura": "-19,5 °C"}
    )
    assert reading.celsius == pytest.approx(-19.5)


def test_plain_decimal_is_parsed():
    reading = reading_from_row(
        {"data": "2026-08-02", "equipamento": "f1", "temperatura": "-19.5"}
    )
    assert reading.celsius == pytest.approx(-19.5)


def test_english_headers_are_accepted():
    reading = reading_from_row(
        {"date": "2026-08-02", "time": "08:00", "equipment": "f1", "celsius": "-20"}
    )
    assert reading.celsius == pytest.approx(-20.0)


def test_equipment_fallback_for_single_equipment_logs():
    reading = reading_from_row(
        {"data": "2026-08-02", "temperatura": "-20"}, equipment_id="freezer-01"
    )
    assert reading.equipment_id == "freezer-01"


def test_row_without_equipment_or_fallback_raises():
    with pytest.raises(ValueError, match="no equipment column"):
        reading_from_row({"data": "2026-08-02", "temperatura": "-20"})


def test_row_without_temperature_raises():
    with pytest.raises(ValueError, match="no temperature column"):
        reading_from_row({"data": "2026-08-02", "equipamento": "f1"})


def test_row_without_date_raises():
    with pytest.raises(ValueError, match="no date or timestamp"):
        reading_from_row({"equipamento": "f1", "temperatura": "-20"})


def test_csv_round_trip(tmp_path):
    path = tmp_path / "temperaturas.csv"
    # The first row quotes its comma decimal, the way a spreadsheet export
    # does; the second uses a plain decimal point.
    path.write_text(
        "data,hora,equipamento,temperatura\n"
        '2026-08-02,08:00,freezer-01,"-19,5"\n'
        "2026-08-02,14:00,freezer-01,-20.1\n",
        encoding="utf-8",
    )
    readings = readings_from_csv(path, site_id="matriz")

    assert len(readings) == 2
    assert readings[0].celsius == pytest.approx(-19.5)
    assert readings[1].celsius == pytest.approx(-20.1)
    assert all(r.site_id == "matriz" for r in readings)


def test_csv_tolerates_bom(tmp_path):
    path = tmp_path / "temperaturas.csv"
    path.write_text(
        "data,equipamento,temperatura\n2026-08-02,f1,-20\n", encoding="utf-8-sig"
    )
    assert len(readings_from_csv(path)) == 1


def test_reading_from_probe_payload():
    reading = reading_from_payload(
        {
            "timestamp": "2026-08-02T03:15:00",
            "equipment_id": "freezer-01",
            "celsius": -19.4,
            "sensor_id": "ds18b20-a",
        },
        site_id="matriz",
    )
    assert reading.celsius == pytest.approx(-19.4)
    assert reading.equipment_id == "freezer-01"
    assert reading.sensor_id == "ds18b20-a"


def test_payload_accepts_epoch_seconds():
    reading = reading_from_payload(
        {"timestamp": 1785690000, "equipment_id": "f1", "celsius": -20}
    )
    assert reading.equipment_id == "f1"


def test_payload_requires_timestamp():
    with pytest.raises(ValueError, match="missing 'timestamp'"):
        reading_from_payload({"equipment_id": "f1", "celsius": -20})


def test_payload_requires_celsius():
    with pytest.raises(ValueError, match="missing 'celsius'"):
        reading_from_payload({"timestamp": "2026-08-02T00:00:00", "equipment_id": "f1"})


def test_payload_requires_equipment_id():
    with pytest.raises(ValueError, match="requires an equipment_id"):
        reading_from_payload({"timestamp": "2026-08-02T00:00:00", "celsius": -20})


def test_spec_from_dict():
    spec = spec_from_dict(
        {
            "equipment_id": "vitrine-01",
            "name": "Vitrine de gelato",
            "min_celsius": -14.0,
            "max_celsius": -12.0,
            "tolerance_minutes": 20.0,
        }
    )
    assert spec.name == "Vitrine de gelato"
    assert spec.tolerance_minutes == 20.0
    # Unspecified fields fall back to defaults.
    assert spec.expected_interval_seconds == 300.0


def test_spec_requires_explicit_range():
    """Ranges come from the written procedure — never invented here."""
    with pytest.raises(ValueError, match="requires 'min_celsius'"):
        spec_from_dict({"equipment_id": "vitrine-01"})


def test_spec_requires_equipment_id():
    with pytest.raises(ValueError, match="requires 'equipment_id'"):
        spec_from_dict({"min_celsius": -14.0, "max_celsius": -12.0})


def test_spec_rejects_inverted_range():
    with pytest.raises(ValueError, match="is above"):
        spec_from_dict(
            {"equipment_id": "f1", "min_celsius": -12.0, "max_celsius": -14.0}
        )
