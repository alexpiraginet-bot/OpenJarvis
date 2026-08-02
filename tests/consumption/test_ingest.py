"""Tests for the consumption ingest adapters."""

from __future__ import annotations

import pytest

from openjarvis.consumption.ingest import (
    product_from_dict,
    reading_from_payload,
    reading_from_row,
    readings_from_csv,
    replenishment_from_row,
)
from openjarvis.consumption.types import (
    KIND_CLOSE,
    KIND_OPEN,
    KIND_SPOT,
    SOURCE_CAMERA,
)


def test_reading_from_sheet_row():
    reading = reading_from_row(
        {
            "data": "2026-08-02",
            "hora": "08:00",
            "sabor": "Pistache",
            "gramas": "3000",
            "tipo": "abertura",
        },
        site_id="matriz",
    )
    assert reading.product_id == "pistache"
    assert reading.grams == pytest.approx(3000.0)
    assert reading.kind == KIND_OPEN
    assert reading.site_id == "matriz"


def test_portuguese_kind_aliases():
    for word, expected in (("fechamento", KIND_CLOSE), ("início", KIND_OPEN)):
        reading = reading_from_row(
            {"data": "2026-08-02", "sabor": "x", "gramas": "100", "tipo": word}
        )
        assert reading.kind == expected


def test_kind_defaults_to_spot():
    reading = reading_from_row({"data": "2026-08-02", "sabor": "x", "gramas": "100"})
    assert reading.kind == KIND_SPOT


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown kind"):
        reading_from_row(
            {"data": "2026-08-02", "sabor": "x", "gramas": "100", "tipo": "meio"}
        )


def test_kilograms_column_is_converted():
    """Nobody should have to remember which unit the system wanted."""
    reading = reading_from_row({"data": "2026-08-02", "sabor": "x", "kg": "3,5"})
    assert reading.grams == pytest.approx(3500.0)


def test_unit_suffix_is_stripped():
    reading = reading_from_row({"data": "2026-08-02", "sabor": "x", "gramas": "420 g"})
    assert reading.grams == pytest.approx(420.0)


def test_comma_decimal_is_parsed():
    reading = reading_from_row({"data": "2026-08-02", "sabor": "x", "gramas": "1234,5"})
    assert reading.grams == pytest.approx(1234.5)


def test_product_id_is_normalized():
    """`Pistache` and `pistache` must not become two flavours."""
    first = reading_from_row({"data": "2026-08-02", "sabor": " Pistache ", "kg": "1"})
    second = reading_from_row({"data": "2026-08-02", "sabor": "pistache", "kg": "1"})
    assert first.product_id == second.product_id == "pistache"


def test_english_headers_are_accepted():
    reading = reading_from_row(
        {"date": "2026-08-02", "time": "08:00", "flavour": "pistachio", "grams": "3000"}
    )
    assert reading.product_id == "pistachio"


def test_row_without_product_raises():
    with pytest.raises(ValueError, match="no product column"):
        reading_from_row({"data": "2026-08-02", "gramas": "100"})


def test_row_without_mass_raises():
    with pytest.raises(ValueError, match="no mass column"):
        reading_from_row({"data": "2026-08-02", "sabor": "x"})


def test_row_without_date_raises():
    with pytest.raises(ValueError, match="no date or timestamp"):
        reading_from_row({"sabor": "x", "gramas": "100"})


def test_csv_round_trip(tmp_path):
    path = tmp_path / "pesagem.csv"
    path.write_text(
        "data,hora,sabor,gramas,tipo\n"
        "2026-08-02,08:00,pistache,3000,abertura\n"
        "2026-08-02,20:00,pistache,420,fechamento\n",
        encoding="utf-8",
    )
    readings = readings_from_csv(path, site_id="matriz")

    assert len(readings) == 2
    assert readings[0].kind == KIND_OPEN
    assert readings[1].grams == pytest.approx(420.0)


def test_csv_tolerates_bom(tmp_path):
    path = tmp_path / "pesagem.csv"
    path.write_text("data,sabor,kg\n2026-08-02,pistache,3\n", encoding="utf-8-sig")
    assert len(readings_from_csv(path)) == 1


def test_reading_from_camera_payload():
    reading = reading_from_payload(
        {
            "timestamp": "2026-08-02T14:03:00",
            "product_id": "pistache",
            "grams": 1840,
            "source": "camera",
        },
        site_id="matriz",
    )
    assert reading.source == SOURCE_CAMERA
    assert reading.grams == pytest.approx(1840.0)


def test_payload_accepts_epoch_seconds():
    reading = reading_from_payload(
        {"timestamp": 1785690000, "product_id": "x", "grams": 100}
    )
    assert reading.product_id == "x"


def test_payload_requires_timestamp():
    with pytest.raises(ValueError, match="missing 'timestamp'"):
        reading_from_payload({"product_id": "x", "grams": 100})


def test_payload_requires_grams():
    with pytest.raises(ValueError, match="missing 'grams'"):
        reading_from_payload({"timestamp": "2026-08-02T08:00:00", "product_id": "x"})


def test_payload_requires_product_id():
    with pytest.raises(ValueError, match="requires a product_id"):
        reading_from_payload({"timestamp": "2026-08-02T08:00:00", "grams": 100})


def test_negative_grams_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        reading_from_payload(
            {"timestamp": "2026-08-02T08:00:00", "product_id": "x", "grams": -5}
        )


def test_replenishment_from_row():
    refill = replenishment_from_row(
        {"data": "2026-08-02", "hora": "15:00", "sabor": "pistache", "kg": "3"},
        site_id="matriz",
    )
    assert refill.grams == pytest.approx(3000.0)
    assert refill.product_id == "pistache"


def test_replenishment_must_be_positive():
    with pytest.raises(ValueError, match="must be > 0"):
        replenishment_from_row(
            {"data": "2026-08-02", "sabor": "pistache", "gramas": "0"}
        )


def test_product_from_dict():
    spec = product_from_dict(
        {
            "product_id": "pistache",
            "name": "Pistache siciliano",
            "portion_grams": 90,
            "category": "gelato",
        }
    )
    assert spec.name == "Pistache siciliano"
    assert spec.portion_grams == pytest.approx(90.0)


def test_product_from_dict_without_portion():
    spec = product_from_dict({"product_id": "pistache"})
    assert spec.portion_grams is None
    assert spec.name == "pistache"


def test_product_requires_id():
    with pytest.raises(ValueError, match="requires 'product_id'"):
        product_from_dict({"name": "sem id"})


def test_portion_must_be_positive():
    with pytest.raises(ValueError, match="portion_grams must be > 0"):
        product_from_dict({"product_id": "x", "portion_grams": 0})
