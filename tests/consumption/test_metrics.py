"""Tests for consumption maths — mass, mix share, and availability correction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.consumption.metrics import (
    consume,
    format_mix,
    mix,
    time_to_empty_hours,
)
from openjarvis.consumption.types import (
    KIND_CLOSE,
    KIND_OPEN,
    ProductSpec,
    Replenishment,
    StockReading,
)

_OPEN = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)
_CLOSE = _OPEN + timedelta(hours=12)


def _reading(hours: float, product_id: str, grams: float, kind: str = "spot"):
    return StockReading(
        timestamp=_OPEN + timedelta(hours=hours),
        product_id=product_id,
        grams=grams,
        kind=kind,
    )


# --- Consumption -----------------------------------------------------------


def test_consumption_from_two_readings():
    readings = [
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(12, "chocolate", 1000, KIND_CLOSE),
    ]
    result = consume(readings, period_start=_OPEN, period_end=_CLOSE)

    assert len(result) == 1
    assert result[0].grams_consumed == pytest.approx(4000.0)
    assert result[0].hours_available == pytest.approx(12.0)
    assert result[0].grams_per_hour == pytest.approx(4000 / 12)
    assert result[0].stockout is False


def test_replenishment_is_accounted_for():
    """A tub topped up mid-service must not read as a light day."""
    readings = [
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(12, "chocolate", 2000, KIND_CLOSE),
    ]
    refills = [
        Replenishment(
            timestamp=_OPEN + timedelta(hours=6), product_id="chocolate", grams=3000
        )
    ]
    result = consume(readings, refills, period_start=_OPEN, period_end=_CLOSE)

    # 5000 + 3000 - 2000, not 5000 - 2000.
    assert result[0].grams_consumed == pytest.approx(6000.0)


def test_unrecorded_replenishment_is_flagged_not_swallowed():
    """Ending heavier than it started means someone topped up off the books."""
    readings = [
        _reading(0, "chocolate", 2000, KIND_OPEN),
        _reading(12, "chocolate", 4000, KIND_CLOSE),
    ]
    result = consume(readings, period_start=_OPEN, period_end=_CLOSE)

    assert result[0].unrecorded_replenishment is True
    assert result[0].grams_consumed == pytest.approx(0.0)


def test_segment_wise_accounting_across_several_readings():
    readings = [
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(4, "chocolate", 3500),
        _reading(8, "chocolate", 2200),
        _reading(12, "chocolate", 1000, KIND_CLOSE),
    ]
    result = consume(readings, period_start=_OPEN, period_end=_CLOSE)
    assert result[0].grams_consumed == pytest.approx(4000.0)
    assert result[0].reading_count == 4


def test_stockout_shortens_availability():
    """A flavour gone by 12:00 was not on offer until 20:00."""
    readings = [
        _reading(0, "pistache", 3000, KIND_OPEN),
        _reading(4, "pistache", 20),
    ]
    result = consume(readings, period_start=_OPEN, period_end=_CLOSE)

    assert result[0].stockout is True
    assert result[0].hours_available == pytest.approx(4.0)
    assert result[0].period_hours == pytest.approx(12.0)
    assert result[0].availability_ratio == pytest.approx(4 / 12)


def test_refilled_after_emptying_counts_as_available():
    readings = [
        _reading(0, "pistache", 3000, KIND_OPEN),
        _reading(4, "pistache", 10),
        _reading(12, "pistache", 1500, KIND_CLOSE),
    ]
    refills = [
        Replenishment(
            timestamp=_OPEN + timedelta(hours=5), product_id="pistache", grams=3000
        )
    ]
    result = consume(readings, refills, period_start=_OPEN, period_end=_CLOSE)

    # The empty segment was replenished, so it still counts as available.
    assert result[0].hours_available == pytest.approx(12.0)
    assert result[0].grams_consumed == pytest.approx(2990 + 1510)


def test_rate_is_none_when_never_available():
    readings = [_reading(0, "pistache", 0, KIND_OPEN)]
    result = consume(readings, period_start=_OPEN, period_end=_CLOSE)
    assert result[0].hours_available == pytest.approx(0.0)
    assert result[0].grams_per_hour is None


def test_readings_outside_the_period_are_ignored():
    readings = [
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(12, "chocolate", 1000, KIND_CLOSE),
        _reading(48, "chocolate", 9999),
    ]
    result = consume(readings, period_start=_OPEN, period_end=_CLOSE)
    assert result[0].reading_count == 2


def test_consume_with_no_readings():
    assert consume([], period_start=_OPEN, period_end=_CLOSE) == []


# --- Mix and the availability correction -----------------------------------


def _reversal_case():
    """Pistachio outsells chocolate per hour but runs out at midday.

    Raw mass makes chocolate the top flavour; rate makes pistachio nearly
    seventy percent of demand. This reversal is the reason the module exists.
    """
    readings = [
        _reading(0, "pistache", 3000, KIND_OPEN),
        _reading(4, "pistache", 20),
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(12, "chocolate", 1000, KIND_CLOSE),
    ]
    return consume(readings, period_start=_OPEN, period_end=_CLOSE)


def test_raw_share_ranks_chocolate_first():
    entries = mix(_reversal_case())
    assert entries[0].product_id == "chocolate"
    assert entries[0].share == pytest.approx(4000 / 6980, rel=1e-3)
    assert entries[1].share == pytest.approx(2980 / 6980, rel=1e-3)


def test_demand_share_reverses_the_ranking():
    entries = mix(_reversal_case(), by="demand_share")
    assert entries[0].product_id == "pistache"
    # 745 g/h vs 333 g/h
    assert entries[0].demand_share == pytest.approx(0.691, abs=0.005)
    assert entries[1].demand_share == pytest.approx(0.309, abs=0.005)


def test_suppressed_flags_the_divergence():
    entries = {entry.product_id: entry for entry in mix(_reversal_case())}
    assert entries["pistache"].suppressed is True
    assert entries["chocolate"].suppressed is False


def test_shares_agree_when_nothing_ran_out():
    """With equal availability the correction changes nothing."""
    readings = [
        _reading(0, "a", 4000, KIND_OPEN),
        _reading(12, "a", 1000, KIND_CLOSE),
        _reading(0, "b", 3000, KIND_OPEN),
        _reading(12, "b", 2000, KIND_CLOSE),
    ]
    for entry in mix(consume(readings, period_start=_OPEN, period_end=_CLOSE)):
        assert entry.share == pytest.approx(entry.demand_share)
        assert entry.suppressed is False


def test_shares_sum_to_one():
    entries = mix(_reversal_case())
    assert sum(entry.share for entry in entries) == pytest.approx(1.0)
    assert sum(entry.demand_share for entry in entries) == pytest.approx(1.0)


def test_shares_are_none_when_nothing_was_consumed():
    """An idle period has an undefined mix, not a zero one."""
    readings = [
        _reading(0, "a", 1000, KIND_OPEN),
        _reading(12, "a", 1000, KIND_CLOSE),
    ]
    entries = mix(consume(readings, period_start=_OPEN, period_end=_CLOSE))
    assert entries[0].share is None
    assert entries[0].demand_share is None


def test_estimated_servings_uses_the_declared_portion():
    specs = {"chocolate": ProductSpec("chocolate", portion_grams=90.0)}
    readings = [
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(12, "chocolate", 1400, KIND_CLOSE),
    ]
    entries = mix(consume(readings, period_start=_OPEN, period_end=_CLOSE), specs=specs)
    assert entries[0].estimated_servings == pytest.approx(3600 / 90)


def test_estimated_servings_is_none_without_a_portion():
    readings = [
        _reading(0, "chocolate", 5000, KIND_OPEN),
        _reading(12, "chocolate", 1000, KIND_CLOSE),
    ]
    entries = mix(consume(readings, period_start=_OPEN, period_end=_CLOSE))
    assert entries[0].estimated_servings is None


def test_spec_names_are_carried_through():
    specs = {"pistache": ProductSpec("pistache", name="Pistache siciliano")}
    readings = [
        _reading(0, "pistache", 3000, KIND_OPEN),
        _reading(12, "pistache", 500, KIND_CLOSE),
    ]
    consumptions = consume(readings, period_start=_OPEN, period_end=_CLOSE, specs=specs)
    assert mix(consumptions, specs=specs)[0].name == "Pistache siciliano"


def test_mix_rejects_unknown_ordering():
    with pytest.raises(ValueError, match="unknown ordering"):
        mix(_reversal_case(), by="vibes")


def test_mix_of_nothing():
    assert mix([]) == []


# --- Prediction ------------------------------------------------------------


def test_time_to_empty():
    assert time_to_empty_hours(1500, 500.0) == pytest.approx(3.0)


def test_time_to_empty_is_none_without_movement():
    assert time_to_empty_hours(1500, 0.0) is None
    assert time_to_empty_hours(1500, None) is None
    assert time_to_empty_hours(1500, -10.0) is None


# --- Rendering -------------------------------------------------------------


def test_format_mix_lists_shares():
    text = format_mix(mix(_reversal_case()))
    assert "chocolate: 57%" in text
    assert "4.0 kg" in text


def test_format_mix_explains_a_suppressed_flavour():
    text = format_mix(mix(_reversal_case()))
    assert "esgotou; demanda real ~69%" in text


def test_format_mix_truncates_and_counts_the_rest():
    readings = []
    for index in range(5):
        readings.append(_reading(0, f"sabor-{index}", 1000, KIND_OPEN))
        readings.append(_reading(12, f"sabor-{index}", 100, KIND_CLOSE))
    entries = mix(consume(readings, period_start=_OPEN, period_end=_CLOSE))
    text = format_mix(entries, limit=2)
    assert "(+3 outros)" in text


def test_format_mix_of_empty_period():
    assert "Sem consumo" in format_mix([])
