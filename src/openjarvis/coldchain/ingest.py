"""Adapters that turn sensor output — or a paper log — into readings.

Most kitchens already record temperatures by hand on a clipboard because a
written procedure requires it.  Digitizing that log is the cheapest possible
start: it turns an existing obligation into a queryable series, finds the
gaps in the current practice, and costs nothing in hardware.  When probes
arrive later they write into the same table, and the history stays continuous.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from openjarvis.coldchain.types import EquipmentSpec, TemperatureReading

# Column aliases accepted in manual logs.  Portuguese first — that is what
# the person holding the probe will actually write.
_EQUIPMENT_COLUMNS = ("equipamento", "equipment", "equipment_id", "ativo")
_CELSIUS_COLUMNS = ("temperatura", "celsius", "temp", "temperature")
_SENSOR_COLUMNS = ("sensor", "sensor_id", "sonda")
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


def _parse_celsius(value: str) -> float:
    """Parse a temperature, tolerating comma decimals and a degree suffix.

    A handwritten log digitized in a Brazilian spreadsheet yields ``-19,5``
    and sometimes ``-19,5 °C``; both have to land as ``-19.5``.
    """
    cleaned = value.strip().replace("°C", "").replace("ºC", "").replace("C", "")
    cleaned = cleaned.replace(",", ".").strip()
    return float(cleaned)


def _row_timestamp(row: Dict[str, Any]) -> datetime:
    """Build a datetime from either a full timestamp or date + time columns."""
    combined = _first_present(row, _TIMESTAMP_COLUMNS)
    if combined:
        return datetime.fromisoformat(combined.strip())

    day = _first_present(row, _DATE_COLUMNS)
    if not day:
        raise ValueError(f"row has no date or timestamp column: {row!r}")

    clock = (_first_present(row, _TIME_COLUMNS) or "00:00").strip()
    if len(clock.split(":")) == 2:
        clock = f"{clock}:00"
    return datetime.fromisoformat(f"{day.strip()}T{clock}")


def reading_from_row(
    row: Dict[str, Any],
    *,
    site_id: str = "",
    equipment_id: str = "",
) -> TemperatureReading:
    """Build one reading from a manual-log row.

    *equipment_id* is a fallback for logs that track a single equipment and
    therefore omit the column.
    """
    raw_celsius = _first_present(row, _CELSIUS_COLUMNS)
    if raw_celsius is None:
        raise ValueError(f"row has no temperature column: {row!r}")

    resolved_equipment = _first_present(row, _EQUIPMENT_COLUMNS) or equipment_id
    if not resolved_equipment:
        raise ValueError(f"row has no equipment column and no fallback: {row!r}")

    return TemperatureReading(
        timestamp=_row_timestamp(row),
        celsius=_parse_celsius(raw_celsius),
        equipment_id=resolved_equipment,
        sensor_id=_first_present(row, _SENSOR_COLUMNS) or "manual",
        site_id=site_id,
        metadata={"ingest": "manual"},
    )


def readings_from_csv(
    path: str | Path,
    *,
    site_id: str = "",
    equipment_id: str = "",
) -> List[TemperatureReading]:
    """Read a manual temperature log into readings."""
    readings: List[TemperatureReading] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            readings.append(
                reading_from_row(row, site_id=site_id, equipment_id=equipment_id)
            )
    return readings


def reading_from_payload(
    payload: Dict[str, Any], *, site_id: str = ""
) -> TemperatureReading:
    """Build a reading from a probe's JSON payload.

    This is the shape a networked probe should publish — an ESPHome node over
    MQTT, or anything else::

        {"timestamp": "2026-08-02T03:15:00-03:00",
         "equipment_id": "freezer-01", "celsius": -19.4,
         "sensor_id": "ds18b20-a"}

    ``timestamp`` may be ISO-8601 or epoch seconds.
    """
    raw_ts = payload.get("timestamp")
    if raw_ts is None:
        raise ValueError("payload is missing 'timestamp'")
    if isinstance(raw_ts, (int, float)):
        moment = datetime.fromtimestamp(float(raw_ts))
    else:
        moment = datetime.fromisoformat(str(raw_ts))

    if "celsius" not in payload:
        raise ValueError("payload is missing 'celsius'")

    return TemperatureReading(
        timestamp=moment,
        celsius=float(payload["celsius"]),
        equipment_id=str(payload.get("equipment_id", "")),
        sensor_id=str(payload.get("sensor_id", "")),
        site_id=str(payload.get("site_id", site_id)),
        reading_id=str(payload.get("reading_id", "")),
        metadata=dict(payload.get("metadata", {})),
    )


def spec_from_dict(data: Dict[str, Any]) -> EquipmentSpec:
    """Build an equipment spec from a config dict.

    Ranges must come from the operation's own written procedure and the
    equipment manufacturer's spec — this only reads what was declared.
    """
    if "equipment_id" not in data:
        raise ValueError("equipment spec requires 'equipment_id'")
    if "min_celsius" not in data or "max_celsius" not in data:
        raise ValueError(
            f"{data['equipment_id']}: spec requires 'min_celsius' and 'max_celsius'"
        )

    return EquipmentSpec(
        equipment_id=str(data["equipment_id"]),
        name=str(data.get("name", "")),
        min_celsius=float(data["min_celsius"]),
        max_celsius=float(data["max_celsius"]),
        tolerance_minutes=float(data.get("tolerance_minutes", 15.0)),
        expected_interval_seconds=float(data.get("expected_interval_seconds", 300.0)),
        site_id=str(data.get("site_id", "")),
        metadata=dict(data.get("metadata", {})),
    )


__all__ = [
    "reading_from_payload",
    "reading_from_row",
    "readings_from_csv",
    "spec_from_dict",
]
