"""Adapters that turn a weighing sheet, a scale, or a camera into readings.

Weighing tubs at open and close is unglamorous and it is also the most
accurate signal available for a fraction of the cost of anything else. It also
produces the ground truth a camera has to be calibrated against, so these two
paths are complements rather than alternatives.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from openjarvis.consumption.types import (
    KIND_SPOT,
    KINDS,
    SOURCE_MANUAL,
    SOURCE_SCALE,
    ProductSpec,
    Replenishment,
    StockReading,
)

_PRODUCT_COLUMNS = ("sabor", "produto", "product", "product_id", "flavour", "flavor")
_GRAMS_COLUMNS = ("gramas", "grams", "peso", "massa", "weight")
_KILOS_COLUMNS = ("kg", "quilos", "kilos", "peso_kg")
_KIND_COLUMNS = ("tipo", "kind", "momento")
_DATE_COLUMNS = ("data", "date", "dia")
_TIME_COLUMNS = ("hora", "time", "horario", "horário")
_TIMESTAMP_COLUMNS = ("timestamp", "datetime")

# Words an operator actually writes in the "tipo" column.
_KIND_ALIASES = {
    "abertura": "open",
    "abre": "open",
    "inicio": "open",
    "início": "open",
    "open": "open",
    "fechamento": "close",
    "fecha": "close",
    "fim": "close",
    "close": "close",
}


def _first_present(row: Dict[str, Any], names: Sequence[str]) -> Optional[str]:
    """Return the first non-empty value among *names*, case-insensitively."""
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def _parse_number(value: str) -> float:
    """Parse a mass, tolerating comma decimals and unit suffixes."""
    cleaned = value.strip().lower()
    for suffix in ("kg", "g", "gramas", "quilos"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    cleaned = cleaned.replace(",", ".")
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


def _row_grams(row: Dict[str, Any]) -> float:
    """Read mass from either a grams column or a kilograms column."""
    raw_grams = _first_present(row, _GRAMS_COLUMNS)
    if raw_grams is not None:
        return _parse_number(raw_grams)

    raw_kilos = _first_present(row, _KILOS_COLUMNS)
    if raw_kilos is not None:
        return _parse_number(raw_kilos) * 1000.0

    raise ValueError(f"row has no mass column: {row!r}")


def reading_from_row(
    row: Dict[str, Any],
    *,
    site_id: str = "",
    source: str = SOURCE_MANUAL,
) -> StockReading:
    """Build one reading from a weighing-sheet row.

    A sheet is what someone with a scale actually fills in::

        data,hora,sabor,gramas,tipo
        2026-08-02,08:00,pistache,3000,abertura
        2026-08-02,20:00,pistache,420,fechamento

    Mass may be given in grams or kilograms; a ``kg`` column is converted, so
    nobody has to remember which unit the system wanted.
    """
    product = _first_present(row, _PRODUCT_COLUMNS)
    if not product:
        raise ValueError(f"row has no product column: {row!r}")

    raw_kind = (_first_present(row, _KIND_COLUMNS) or KIND_SPOT).strip().lower()
    kind = _KIND_ALIASES.get(raw_kind, raw_kind)
    if kind not in KINDS:
        raise ValueError(f"unknown kind {raw_kind!r}; expected one of {KINDS}")

    return StockReading(
        timestamp=_row_timestamp(row),
        product_id=product.strip().lower(),
        grams=_row_grams(row),
        kind=kind,
        source=source,
        site_id=site_id,
        metadata={"ingest": "sheet"},
    )


def readings_from_csv(
    path: str | Path, *, site_id: str = "", source: str = SOURCE_MANUAL
) -> List[StockReading]:
    """Read a weighing sheet into stock readings."""
    readings: List[StockReading] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            readings.append(reading_from_row(row, site_id=site_id, source=source))
    return readings


def reading_from_payload(payload: Dict[str, Any], *, site_id: str = "") -> StockReading:
    """Build a reading from a scale or camera payload.

    Both write the same shape, which is what lets a camera be added later
    without touching anything downstream::

        {"timestamp": "2026-08-02T14:03:00-03:00",
         "product_id": "pistache", "grams": 1840,
         "source": "camera"}
    """
    raw_ts = payload.get("timestamp")
    if raw_ts is None:
        raise ValueError("payload is missing 'timestamp'")
    if isinstance(raw_ts, (int, float)):
        moment = datetime.fromtimestamp(float(raw_ts))
    else:
        moment = datetime.fromisoformat(str(raw_ts))

    if "grams" not in payload:
        raise ValueError("payload is missing 'grams'")

    return StockReading(
        timestamp=moment,
        product_id=str(payload.get("product_id", "")),
        grams=float(payload["grams"]),
        kind=str(payload.get("kind", KIND_SPOT)),
        source=str(payload.get("source", SOURCE_SCALE)),
        site_id=str(payload.get("site_id", site_id)),
        reading_id=str(payload.get("reading_id", "")),
        metadata=dict(payload.get("metadata", {})),
    )


def replenishment_from_row(row: Dict[str, Any], *, site_id: str = "") -> Replenishment:
    """Build a replenishment from a sheet row recording a top-up."""
    product = _first_present(row, _PRODUCT_COLUMNS)
    if not product:
        raise ValueError(f"row has no product column: {row!r}")

    return Replenishment(
        timestamp=_row_timestamp(row),
        product_id=product.strip().lower(),
        grams=_row_grams(row),
        site_id=site_id,
        metadata={"ingest": "sheet"},
    )


def product_from_dict(data: Dict[str, Any]) -> ProductSpec:
    """Build a product spec from a config dict."""
    if "product_id" not in data:
        raise ValueError("product spec requires 'product_id'")

    portion = data.get("portion_grams")
    return ProductSpec(
        product_id=str(data["product_id"]),
        name=str(data.get("name", "")),
        category=str(data.get("category", "")),
        portion_grams=float(portion) if portion is not None else None,
        site_id=str(data.get("site_id", "")),
        metadata=dict(data.get("metadata", {})),
    )


__all__ = [
    "product_from_dict",
    "reading_from_payload",
    "reading_from_row",
    "readings_from_csv",
    "replenishment_from_row",
]
