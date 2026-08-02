"""Tests for the footfall ingest adapters."""

from __future__ import annotations

import pytest

from openjarvis.retail.ingest import (
    event_from_payload,
    events_from_tally_csv,
    events_from_tally_row,
    sales_from_rows,
)
from openjarvis.retail.types import (
    DIR_IN,
    DIR_OUT,
    DIR_PASS,
    SENSOR_MANUAL,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
)


def test_tally_row_splits_into_zone_events():
    events = events_from_tally_row(
        {"data": "2026-08-02", "hora": "14:00", "passantes": "120", "entrantes": "8"}
    )
    assert len(events) == 2

    corridor = next(e for e in events if e.zone == ZONE_CORRIDOR)
    assert corridor.direction == DIR_PASS
    assert corridor.count == 120
    assert corridor.sensor_kind == SENSOR_MANUAL

    entrance = next(e for e in events if e.zone == ZONE_ENTRANCE)
    assert entrance.direction == DIR_IN
    assert entrance.count == 8


def test_tally_row_skips_zero_and_blank_counts():
    events = events_from_tally_row(
        {"data": "2026-08-02", "hora": "14:00", "passantes": "0", "entrantes": ""}
    )
    assert events == []


def test_tally_row_accepts_english_headers():
    events = events_from_tally_row(
        {"date": "2026-08-02", "time": "14:00", "passersby": "50", "entries": "5"}
    )
    assert {e.zone for e in events} == {ZONE_CORRIDOR, ZONE_ENTRANCE}


def test_tally_row_accepts_exits():
    events = events_from_tally_row(
        {"data": "2026-08-02", "hora": "14:00", "saidas": "4"}
    )
    assert len(events) == 1
    assert events[0].direction == DIR_OUT


def test_tally_row_accepts_single_timestamp_column():
    events = events_from_tally_row(
        {"timestamp": "2026-08-02T14:30:00", "entrantes": "3"}
    )
    assert events[0].timestamp.hour == 14
    assert events[0].timestamp.minute == 30


def test_tally_row_defaults_missing_time_to_midnight():
    events = events_from_tally_row({"data": "2026-08-02", "entrantes": "3"})
    assert events[0].timestamp.hour == 0


def test_tally_row_without_date_raises():
    with pytest.raises(ValueError, match="no date or timestamp"):
        events_from_tally_row({"entrantes": "3"})


def test_tally_csv_round_trip(tmp_path):
    path = tmp_path / "contagem.csv"
    path.write_text(
        "data,hora,passantes,entrantes\n"
        "2026-08-02,14:00,120,8\n"
        "2026-08-02,15:00,90,11\n",
        encoding="utf-8",
    )
    events = events_from_tally_csv(path, site_id="matriz")

    assert len(events) == 4
    assert all(event.site_id == "matriz" for event in events)
    assert sum(e.count for e in events if e.zone == ZONE_CORRIDOR) == 210
    assert sum(e.count for e in events if e.zone == ZONE_ENTRANCE) == 19


def test_tally_csv_tolerates_bom(tmp_path):
    """Spreadsheets exported on Windows lead with a BOM."""
    path = tmp_path / "contagem.csv"
    path.write_text("data,hora,passantes\n2026-08-02,14:00,10\n", encoding="utf-8-sig")
    events = events_from_tally_csv(path)
    assert len(events) == 1
    assert events[0].count == 10


def test_event_from_payload():
    event = event_from_payload(
        {
            "timestamp": "2026-08-02T14:03:11",
            "zone": "entrance",
            "direction": "in",
            "count": 1,
            "sensor_id": "door-01",
            "sensor_kind": "mmwave",
        },
        site_id="matriz",
    )
    assert event.zone == ZONE_ENTRANCE
    assert event.direction == DIR_IN
    assert event.sensor_id == "door-01"
    assert event.site_id == "matriz"


def test_event_from_payload_accepts_epoch_seconds():
    event = event_from_payload(
        {"timestamp": 1785690000, "zone": "corridor", "direction": "pass"}
    )
    assert event.zone == ZONE_CORRIDOR


def test_event_from_payload_requires_timestamp():
    with pytest.raises(ValueError, match="missing 'timestamp'"):
        event_from_payload({"zone": "entrance", "direction": "in"})


def test_event_from_payload_rejects_bad_zone():
    with pytest.raises(ValueError, match="unknown zone"):
        event_from_payload(
            {"timestamp": "2026-08-02T14:00:00", "zone": "roof", "direction": "in"}
        )


def test_sales_parse_brazilian_amount_format():
    """`1.234,56` must become 1234.56, not 1.23."""
    sales = sales_from_rows(
        [{"data": "2026-08-02", "hora": "14:00", "amount": "1.234,56", "items": "3"}],
        source="stone",
    )
    assert sales[0].amount == pytest.approx(1234.56)
    assert sales[0].items == 3


def test_sales_parse_plain_decimal_and_currency_prefix():
    sales = sales_from_rows(
        [
            {"data": "2026-08-02", "hora": "14:00", "amount": "18.50"},
            {"data": "2026-08-02", "hora": "15:00", "amount": "R$ 22,00"},
        ]
    )
    assert sales[0].amount == pytest.approx(18.50)
    assert sales[1].amount == pytest.approx(22.00)


def test_sales_tolerate_missing_amount():
    sales = sales_from_rows([{"data": "2026-08-02", "hora": "14:00"}])
    assert sales[0].amount == 0.0
