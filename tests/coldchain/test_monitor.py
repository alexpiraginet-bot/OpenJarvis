"""Tests for excursion detection, gap detection, and compliance summaries.

The failures that matter here are rare in real data, so the series are built
by hand: a service-window blip that must be ignored, an overnight compressor
failure that must not be, and a dead sensor that must not read as a clean day.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.coldchain.monitor import (
    current_status,
    find_excursions,
    find_gaps,
    format_summary,
    summarize,
)
from openjarvis.coldchain.types import (
    ABOVE,
    BELOW,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARNING,
    EquipmentSpec,
    TemperatureReading,
)

_START = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)

# A gelato storage freezer: -22 to -18, five-minute polling, fifteen minutes
# of tolerance so routine door openings do not register.
_FREEZER = EquipmentSpec(
    equipment_id="freezer-01",
    name="Freezer de estoque",
    min_celsius=-22.0,
    max_celsius=-18.0,
    tolerance_minutes=15.0,
    expected_interval_seconds=300.0,
)


def _series(values, *, start=_START, step_minutes=5, spec=_FREEZER):
    """Build evenly spaced readings from a list of temperatures."""
    return [
        TemperatureReading(
            timestamp=start + timedelta(minutes=step_minutes * index),
            celsius=value,
            equipment_id=spec.equipment_id,
        )
        for index, value in enumerate(values)
    ]


# --- Excursions ------------------------------------------------------------


def test_no_excursion_when_in_range():
    readings = _series([-20.0, -19.8, -20.2, -19.5, -20.1])
    assert find_excursions(readings, _FREEZER) == []


def test_service_window_blip_is_not_an_excursion():
    """A door opened during service warms briefly — that is not a failure.

    Two readings above range spans 5 minutes, under the 15-minute tolerance.
    """
    readings = _series([-20.0, -17.5, -17.0, -19.8, -20.1])
    assert find_excursions(readings, _FREEZER) == []


def test_sustained_warming_is_an_excursion():
    """A compressor failure warms steadily and does not recover."""
    readings = _series([-20.0, -17.0, -15.0, -12.0, -9.0, -6.0])
    excursions = find_excursions(readings, _FREEZER)

    assert len(excursions) == 1
    excursion = excursions[0]
    assert excursion.direction == ABOVE
    assert excursion.peak_celsius == -6.0
    assert excursion.max_deviation == pytest.approx(12.0)
    # Five out-of-range readings, from minute 5 to minute 25.
    assert excursion.duration_minutes == pytest.approx(20.0)
    assert excursion.reading_count == 5


def test_excursion_duration_is_measured_between_out_of_range_readings():
    readings = _series([-20.0, -17.0, -16.0, -17.0, -16.5, -20.0])
    excursion = find_excursions(readings, _FREEZER)[0]
    # Four out-of-range readings at 5-minute spacing span 15 minutes.
    assert excursion.duration_minutes == pytest.approx(15.0)


def test_degree_minutes_separates_shallow_drift_from_severe_spike():
    """Same duration and reading count, very different thermal load."""
    shallow = _series([-17.0] * 5)
    severe = _series([-5.0] * 5)

    shallow_load = find_excursions(shallow, _FREEZER)[0].degree_minutes
    severe_load = find_excursions(severe, _FREEZER)[0].degree_minutes

    assert shallow_load == pytest.approx(20.0)  # 1.0 °C over 20 min
    assert severe_load == pytest.approx(260.0)  # 13.0 °C over 20 min
    assert severe_load > shallow_load


def test_too_cold_is_a_separate_direction():
    readings = _series([-25.0, -26.0, -27.0, -25.5])
    excursion = find_excursions(readings, _FREEZER)[0]
    assert excursion.direction == BELOW
    assert excursion.peak_celsius == -27.0
    assert excursion.max_deviation == pytest.approx(5.0)


def test_warm_then_cold_are_two_excursions():
    """Drifting warm and then running cold are distinct failures."""
    readings = _series(
        [-17.0, -16.0, -15.0, -17.0, -26.0, -27.0, -26.0, -25.0],
    )
    excursions = find_excursions(readings, _FREEZER)
    assert [e.direction for e in excursions] == [ABOVE, BELOW]


def test_recovery_closes_an_excursion():
    readings = _series([-17.0, -16.0, -15.0, -16.0, -20.0, -20.1, -20.0])
    excursions = find_excursions(readings, _FREEZER)
    assert len(excursions) == 1
    assert excursions[0].end == _START + timedelta(minutes=15)


def test_single_out_of_range_reading_does_not_qualify():
    """One sample is no evidence of duration."""
    readings = _series([-20.0, -10.0, -20.0])
    assert find_excursions(readings, _FREEZER) == []


def test_zero_tolerance_catches_any_run():
    spec = EquipmentSpec(
        equipment_id="vitrine",
        min_celsius=-14.0,
        max_celsius=-12.0,
        tolerance_minutes=0.0,
    )
    readings = _series([-13.0, -10.0, -13.0], spec=spec)
    assert len(find_excursions(readings, spec)) == 1


def test_excursion_as_dict_is_json_ready():
    readings = _series([-20.0, -17.0, -15.0, -12.0, -9.0])
    payload = find_excursions(readings, _FREEZER)[0].as_dict()
    assert payload["direction"] == ABOVE
    assert payload["equipment_id"] == "freezer-01"
    assert isinstance(payload["start"], str)


# --- Gaps ------------------------------------------------------------------


def test_no_gap_when_readings_are_regular():
    readings = _series([-20.0] * 12)  # 55 minutes of 5-minute readings
    gaps = find_gaps(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(minutes=55),
    )
    assert gaps == []


def test_gap_detected_when_sensor_goes_silent():
    early = _series([-20.0, -20.1])
    late = _series([-20.0, -20.2], start=_START + timedelta(hours=4))
    gaps = find_gaps(
        early + late,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(hours=4, minutes=5),
    )
    assert len(gaps) == 1
    assert gaps[0].duration_minutes == pytest.approx(235.0)


def test_gap_at_the_start_of_the_period_counts():
    """A sensor that starts reporting at noon leaves the morning unmonitored."""
    readings = _series([-20.0, -20.1], start=_START + timedelta(hours=12))
    gaps = find_gaps(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(hours=13),
    )
    assert len(gaps) == 2  # midnight->noon, and the tail after the last reading
    assert gaps[0].duration_minutes == pytest.approx(720.0)


def test_period_with_no_readings_is_one_long_gap():
    gaps = find_gaps(
        [],
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(hours=24),
    )
    assert len(gaps) == 1
    assert gaps[0].duration_minutes == pytest.approx(1440.0)


def test_jitter_within_the_multiplier_is_not_a_gap():
    readings = [
        TemperatureReading(timestamp=_START, celsius=-20.0, equipment_id="freezer-01"),
        # 12 minutes later: over the 5-minute interval but under 3x.
        TemperatureReading(
            timestamp=_START + timedelta(minutes=12),
            celsius=-20.0,
            equipment_id="freezer-01",
        ),
    ]
    gaps = find_gaps(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(minutes=12),
    )
    assert gaps == []


# --- Summaries -------------------------------------------------------------


def test_summary_of_a_clean_period():
    readings = _series([-20.0] * 12)
    summary = summarize(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(minutes=55),
    )
    assert summary.severity == SEVERITY_OK
    assert summary.reading_count == 12
    assert summary.coverage_ratio == pytest.approx(1.0)
    assert summary.in_range_ratio == pytest.approx(1.0)
    assert summary.excursions == []


def test_summary_flags_excursion_as_critical():
    readings = _series([-20.0, -17.0, -15.0, -12.0, -9.0, -6.0])
    summary = summarize(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(minutes=25),
    )
    assert summary.severity == SEVERITY_CRITICAL
    assert summary.worst_excursion is not None
    assert summary.worst_excursion.peak_celsius == -6.0


def test_dead_sensor_is_a_warning_not_a_clean_day():
    """The failure mode this whole module exists to prevent."""
    summary = summarize(
        [],
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(hours=24),
    )
    assert summary.severity == SEVERITY_WARNING
    assert summary.reading_count == 0
    assert summary.coverage_ratio == pytest.approx(0.0)
    assert summary.in_range_ratio is None


def test_summary_reports_observed_range():
    readings = _series([-21.0, -20.0, -19.0])
    summary = summarize(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(minutes=10),
    )
    assert summary.min_celsius == -21.0
    assert summary.max_celsius == -19.0
    assert summary.mean_celsius == pytest.approx(-20.0)


def test_summary_ignores_readings_outside_the_period():
    inside = _series([-20.0, -20.1])
    outside = _series([-5.0], start=_START + timedelta(days=2))
    summary = summarize(
        inside + outside,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(minutes=5),
    )
    assert summary.reading_count == 2
    assert summary.max_celsius == -20.0


def test_partial_coverage_is_reported():
    # One hour of readings inside a two-hour period.
    readings = _series([-20.0] * 13)
    summary = summarize(
        readings,
        _FREEZER,
        period_start=_START,
        period_end=_START + timedelta(hours=2),
    )
    assert summary.coverage_ratio == pytest.approx(0.5)
    assert summary.severity == SEVERITY_WARNING


# --- Live status -----------------------------------------------------------


def test_current_status_in_range():
    readings = _series([-20.0, -20.1])
    status = current_status(readings, _FREEZER, now=_START + timedelta(minutes=6))
    assert status["severity"] == SEVERITY_OK
    assert status["stale"] is False
    assert status["celsius"] == -20.1


def test_current_status_escalates_a_building_deviation():
    """Critical while it is still developing, not only after it ends."""
    readings = _series([-20.0, -17.0, -16.0, -15.0, -14.0])
    status = current_status(readings, _FREEZER, now=_START + timedelta(minutes=21))
    assert status["severity"] == SEVERITY_CRITICAL
    assert status["out_of_range_minutes"] == pytest.approx(15.0)


def test_current_status_within_tolerance_is_only_a_warning():
    readings = _series([-20.0, -17.0])
    status = current_status(readings, _FREEZER, now=_START + timedelta(minutes=6))
    assert status["severity"] == SEVERITY_WARNING
    assert "within tolerance" in status["reason"]


def test_current_status_marks_silence_as_stale():
    readings = _series([-20.0])
    status = current_status(readings, _FREEZER, now=_START + timedelta(hours=3))
    assert status["stale"] is True
    assert status["severity"] == SEVERITY_WARNING


def test_current_status_with_no_readings():
    status = current_status([], _FREEZER, now=_START)
    assert status["severity"] == SEVERITY_WARNING
    assert status["stale"] is True
    assert status["celsius"] is None


# --- Rendering -------------------------------------------------------------


def test_format_summary_reports_clean_period():
    readings = _series([-20.0] * 12)
    text = format_summary(
        summarize(
            readings,
            _FREEZER,
            period_start=_START,
            period_end=_START + timedelta(minutes=55),
        )
    )
    assert "Desvios: nenhum" in text
    assert "Cobertura do monitoramento: 100.0%" in text
    assert "Situação: ok" in text


def test_format_summary_reports_worst_excursion():
    readings = _series([-20.0, -17.0, -15.0, -12.0, -9.0, -6.0])
    text = format_summary(
        summarize(
            readings,
            _FREEZER,
            period_start=_START,
            period_end=_START + timedelta(minutes=25),
        )
    )
    assert "Desvios: 1" in text
    assert "Pior desvio" in text
    assert "Situação: critical" in text
