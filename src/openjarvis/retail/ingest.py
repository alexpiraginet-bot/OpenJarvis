"""Adapters that turn sensor output — or a human with a clicker — into events.

Every adapter returns plain :class:`FootfallEvent` / :class:`Sale` objects, so
the analytics layer never learns what produced them.  That is what lets a
store start on manual tallies today and swap in a camera next month without
rewriting anything downstream, and it keeps the historical series comparable
across the change.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from openjarvis.retail.types import (
    DIR_IN,
    DIR_OUT,
    DIR_PASS,
    SENSOR_MANUAL,
    ZONE_CORRIDOR,
    ZONE_ENTRANCE,
    FootfallEvent,
    Sale,
)

# Column aliases accepted in manual tally sheets.  Portuguese first, since
# that is what the person filling the sheet will actually type.
_PASSERSBY_COLUMNS = ("passantes", "passersby", "fluxo", "corredor")
_ENTRIES_COLUMNS = ("entrantes", "entries", "entradas", "entrou")
_EXITS_COLUMNS = ("saidas", "saídas", "exits", "saiu")
_DATE_COLUMNS = ("data", "date", "dia")
_TIME_COLUMNS = ("hora", "time", "horario", "horário")
_TIMESTAMP_COLUMNS = ("timestamp", "datetime", "momento")


def _first_present(row: Dict[str, Any], names: Sequence[str]) -> Optional[str]:
    """Return the first non-empty value among *names*, case-insensitively."""
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def _parse_int(value: Optional[str]) -> int:
    """Parse a count, tolerating blanks and stray whitespace."""
    if value is None or str(value).strip() == "":
        return 0
    return int(float(str(value).strip()))


def _row_timestamp(row: Dict[str, Any]) -> datetime:
    """Build a datetime from either a full timestamp or date + time columns."""
    combined = _first_present(row, _TIMESTAMP_COLUMNS)
    if combined:
        return datetime.fromisoformat(combined.strip())

    day = _first_present(row, _DATE_COLUMNS)
    if not day:
        raise ValueError(f"row has no date or timestamp column: {row!r}")

    clock = _first_present(row, _TIME_COLUMNS) or "00:00"
    clock = clock.strip()
    if len(clock.split(":")) == 2:
        clock = f"{clock}:00"
    return datetime.fromisoformat(f"{day.strip()}T{clock}")


def events_from_tally_row(
    row: Dict[str, Any],
    *,
    site_id: str = "",
    sensor_id: str = "manual",
) -> List[FootfallEvent]:
    """Turn one manual tally row into up to three events.

    A tally row is what someone standing in the store actually records::

        data,hora,passantes,entrantes
        2026-08-02,14:00,45,6

    Counting every passerby by hand is not sustainable, but counting for
    fifteen minutes at a few fixed times a day is — and sampled capture rate
    is enough to establish a baseline before any hardware arrives.
    """
    moment = _row_timestamp(row)
    events: List[FootfallEvent] = []

    specs = (
        (_PASSERSBY_COLUMNS, ZONE_CORRIDOR, DIR_PASS),
        (_ENTRIES_COLUMNS, ZONE_ENTRANCE, DIR_IN),
        (_EXITS_COLUMNS, ZONE_ENTRANCE, DIR_OUT),
    )

    for columns, zone, direction in specs:
        count = _parse_int(_first_present(row, columns))
        if count <= 0:
            continue
        events.append(
            FootfallEvent(
                timestamp=moment,
                zone=zone,
                direction=direction,
                count=count,
                sensor_id=sensor_id,
                sensor_kind=SENSOR_MANUAL,
                site_id=site_id,
                metadata={"ingest": "tally"},
            )
        )

    return events


def events_from_tally_csv(
    path: str | Path,
    *,
    site_id: str = "",
    sensor_id: str = "manual",
) -> List[FootfallEvent]:
    """Read a manual tally CSV into events.

    Accepts either ``data``/``hora`` columns or a single ``timestamp``
    column, and Portuguese or English count headers.
    """
    events: List[FootfallEvent] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            events.extend(
                events_from_tally_row(row, site_id=site_id, sensor_id=sensor_id)
            )
    return events


def event_from_payload(payload: Dict[str, Any], *, site_id: str = "") -> FootfallEvent:
    """Build an event from a sensor's JSON payload.

    This is the shape a networked counter should POST — an ESPHome node, a
    camera counter, or anything else::

        {"timestamp": "2026-08-02T14:03:11-03:00",
         "zone": "entrance", "direction": "in", "count": 1,
         "sensor_id": "door-01", "sensor_kind": "mmwave"}

    ``timestamp`` may be an ISO-8601 string or epoch seconds.  Sensors with
    no clock should send epoch seconds from the gateway that received them.
    """
    raw_ts = payload.get("timestamp")
    if raw_ts is None:
        raise ValueError("payload is missing 'timestamp'")
    if isinstance(raw_ts, (int, float)):
        moment = datetime.fromtimestamp(float(raw_ts))
    else:
        moment = datetime.fromisoformat(str(raw_ts))

    return FootfallEvent(
        timestamp=moment,
        zone=str(payload.get("zone", ZONE_ENTRANCE)),
        direction=str(payload.get("direction", DIR_IN)),
        count=int(payload.get("count", 1)),
        sensor_id=str(payload.get("sensor_id", "")),
        sensor_kind=str(payload.get("sensor_kind", "")),
        site_id=str(payload.get("site_id", site_id)),
        confidence=float(payload.get("confidence", 1.0)),
        event_id=str(payload.get("event_id", "")),
        metadata=dict(payload.get("metadata", {})),
    )


def sales_from_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    site_id: str = "",
    source: str = "",
    amount_column: str = "amount",
    items_column: str = "items",
) -> List[Sale]:
    """Build sales from generic tabular rows (a POS export, for example).

    Amounts are parsed leniently so a Brazilian export using ``1.234,56``
    lands as ``1234.56`` rather than raising or, worse, silently becoming
    ``1.23``.
    """
    sales: List[Sale] = []
    for row in rows:
        moment = _row_timestamp(row)
        raw_amount = str(row.get(amount_column, "0") or "0").strip()
        raw_amount = raw_amount.replace("R$", "").strip()
        if "," in raw_amount:
            raw_amount = raw_amount.replace(".", "").replace(",", ".")
        sales.append(
            Sale(
                timestamp=moment,
                amount=float(raw_amount or 0.0),
                items=_parse_int(str(row.get(items_column, "0"))),
                site_id=site_id,
                source=source,
                sale_id=str(row.get("sale_id", "") or ""),
            )
        )
    return sales


__all__ = [
    "event_from_payload",
    "events_from_tally_csv",
    "events_from_tally_row",
    "sales_from_rows",
]
