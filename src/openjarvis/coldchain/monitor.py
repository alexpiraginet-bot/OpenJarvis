"""Excursion detection, monitoring-gap detection, and compliance summaries.

All functions are pure: they take readings and a spec and return findings.
Nothing here talks to a database or a sensor, so the food-safety logic can be
tested against hand-written series — which is the only honest way to verify
it, since the failures that matter are rare in real data.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from openjarvis.coldchain.types import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARNING,
    ComplianceSummary,
    EquipmentSpec,
    Excursion,
    MonitoringGap,
    TemperatureReading,
)

# A gap is declared when the gap between consecutive readings exceeds the
# expected interval by this factor.  Sensors jitter; 3x tolerates ordinary
# scheduling slop without hiding a sensor that genuinely stopped reporting.
DEFAULT_GAP_MULTIPLIER = 3.0


def _sorted(readings: Sequence[TemperatureReading]) -> List[TemperatureReading]:
    """Return readings ordered by time."""
    return sorted(readings, key=lambda r: r.timestamp)


def _runs_out_of_range(
    readings: Sequence[TemperatureReading], spec: EquipmentSpec
) -> List[List[TemperatureReading]]:
    """Group readings into contiguous out-of-range runs.

    A run ends when the equipment returns to range *or* when it crosses to
    the other side of the range — drifting warm and then running cold are two
    distinct failures and should not be merged into one event.
    """
    runs: List[List[TemperatureReading]] = []
    current: List[TemperatureReading] = []
    current_direction: Optional[str] = None

    for reading in readings:
        direction = spec.direction_of(reading.celsius)
        if direction is None:
            if current:
                runs.append(current)
                current = []
                current_direction = None
            continue

        if current and direction != current_direction:
            runs.append(current)
            current = []

        current.append(reading)
        current_direction = direction

    if current:
        runs.append(current)

    return runs


def _degree_minutes(
    run: Sequence[TemperatureReading], spec: EquipmentSpec
) -> Tuple[float, float, float]:
    """Return ``(degree_minutes, max_deviation, peak_celsius)`` for a run.

    Deviation is integrated over time with the trapezoid rule, so a shallow
    drift lasting hours and a brief severe spike produce different numbers
    even when their peak deviation matches.
    """
    deviations = [spec.deviation(r.celsius) for r in run]
    max_deviation = max(deviations)

    # The peak is the reading furthest outside the range, which for a "below"
    # run is the coldest and for an "above" run the warmest.
    peak_index = deviations.index(max_deviation)
    peak_celsius = run[peak_index].celsius

    degree_minutes = 0.0
    for (left, left_dev), (right, right_dev) in zip(
        zip(run, deviations), zip(run[1:], deviations[1:])
    ):
        minutes = (right.timestamp - left.timestamp).total_seconds() / 60.0
        degree_minutes += (left_dev + right_dev) / 2.0 * minutes

    return degree_minutes, max_deviation, peak_celsius


def find_excursions(
    readings: Sequence[TemperatureReading], spec: EquipmentSpec
) -> List[Excursion]:
    """Return the excursions in *readings* that qualify under *spec*.

    A run qualifies when it lasts at least ``spec.tolerance_minutes``.  Its
    duration is measured between the first and last out-of-range reading, so
    a lone out-of-range sample spans zero minutes and only qualifies when the
    tolerance is zero — the conservative reading, since one sample is no
    evidence of duration.
    """
    excursions: List[Excursion] = []

    for run in _runs_out_of_range(_sorted(readings), spec):
        duration_minutes = (run[-1].timestamp - run[0].timestamp).total_seconds() / 60.0
        if duration_minutes < spec.tolerance_minutes:
            continue

        degree_minutes, max_deviation, peak_celsius = _degree_minutes(run, spec)
        direction = spec.direction_of(run[0].celsius)
        excursions.append(
            Excursion(
                equipment_id=spec.equipment_id,
                start=run[0].timestamp,
                end=run[-1].timestamp,
                direction=direction or "",
                peak_celsius=peak_celsius,
                max_deviation=max_deviation,
                degree_minutes=degree_minutes,
                reading_count=len(run),
                site_id=spec.site_id,
            )
        )

    return excursions


def find_gaps(
    readings: Sequence[TemperatureReading],
    spec: EquipmentSpec,
    *,
    period_start: datetime,
    period_end: datetime,
    gap_multiplier: float = DEFAULT_GAP_MULTIPLIER,
) -> List[MonitoringGap]:
    """Return stretches of the period with no readings.

    The edges count: a sensor that starts reporting at noon leaves the whole
    morning unmonitored, and that is exactly the hole an auditor asks about.
    """
    threshold = spec.expected_interval_seconds * gap_multiplier
    ordered = _sorted(
        [r for r in readings if period_start <= r.timestamp <= period_end]
    )
    gaps: List[MonitoringGap] = []

    if not ordered:
        if (period_end - period_start).total_seconds() > threshold:
            gaps.append(
                MonitoringGap(
                    equipment_id=spec.equipment_id,
                    start=period_start,
                    end=period_end,
                    site_id=spec.site_id,
                )
            )
        return gaps

    def add_gap(start: datetime, end: datetime) -> None:
        if (end - start).total_seconds() > threshold:
            gaps.append(
                MonitoringGap(
                    equipment_id=spec.equipment_id,
                    start=start,
                    end=end,
                    site_id=spec.site_id,
                )
            )

    add_gap(period_start, ordered[0].timestamp)
    for left, right in zip(ordered, ordered[1:]):
        add_gap(left.timestamp, right.timestamp)
    add_gap(ordered[-1].timestamp, period_end)

    return gaps


def summarize(
    readings: Sequence[TemperatureReading],
    spec: EquipmentSpec,
    *,
    period_start: datetime,
    period_end: datetime,
    gap_multiplier: float = DEFAULT_GAP_MULTIPLIER,
) -> ComplianceSummary:
    """Build the compliance record for one equipment over one period."""
    scoped = _sorted([r for r in readings if period_start <= r.timestamp <= period_end])

    gaps = find_gaps(
        scoped,
        spec,
        period_start=period_start,
        period_end=period_end,
        gap_multiplier=gap_multiplier,
    )
    excursions = find_excursions(scoped, spec)

    period_seconds = (period_end - period_start).total_seconds()
    gap_seconds = sum(gap.duration_seconds for gap in gaps)
    coverage = 0.0
    if period_seconds > 0:
        coverage = max(0.0, (period_seconds - gap_seconds) / period_seconds)

    summary = ComplianceSummary(
        equipment_id=spec.equipment_id,
        name=spec.name,
        site_id=spec.site_id,
        period_start=period_start,
        period_end=period_end,
        reading_count=len(scoped),
        coverage_ratio=coverage,
        excursions=excursions,
        gaps=gaps,
    )

    if scoped:
        values = [r.celsius for r in scoped]
        summary.min_celsius = min(values)
        summary.max_celsius = max(values)
        summary.mean_celsius = sum(values) / len(values)
        in_range = sum(1 for value in values if spec.in_range(value))
        summary.in_range_ratio = in_range / len(values)

    return summary


def current_status(
    readings: Sequence[TemperatureReading],
    spec: EquipmentSpec,
    *,
    now: datetime,
    gap_multiplier: float = DEFAULT_GAP_MULTIPLIER,
) -> dict:
    """Evaluate the live state of one equipment, for an alerting loop.

    Returns the current severity plus how long the equipment has been out of
    range, so a caller can escalate a deviation that is still building rather
    than waiting for it to end.

    Silence is treated as a fault, not as good news: a sensor that stopped
    reporting yields ``warning`` with ``stale`` set, because an unmonitored
    freezer and a healthy one look identical from the outside.
    """
    ordered = _sorted(readings)
    if not ordered:
        return {
            "equipment_id": spec.equipment_id,
            "severity": SEVERITY_WARNING,
            "stale": True,
            "reason": "no readings",
            "celsius": None,
            "out_of_range_minutes": 0.0,
        }

    latest = ordered[-1]
    age_seconds = (now - latest.timestamp).total_seconds()
    stale = age_seconds > spec.expected_interval_seconds * gap_multiplier

    direction = spec.direction_of(latest.celsius)
    out_of_range_minutes = 0.0
    if direction is not None:
        # Walk back while the equipment stayed out of range on the same side.
        streak_start = latest.timestamp
        for reading in reversed(ordered):
            if spec.direction_of(reading.celsius) != direction:
                break
            streak_start = reading.timestamp
        out_of_range_minutes = (latest.timestamp - streak_start).total_seconds() / 60.0

    if direction is not None and out_of_range_minutes >= spec.tolerance_minutes:
        severity = SEVERITY_CRITICAL
        reason = f"{direction} range for {out_of_range_minutes:.0f} min"
    elif stale:
        severity = SEVERITY_WARNING
        reason = f"no reading for {age_seconds / 60.0:.0f} min"
    elif direction is not None:
        severity = SEVERITY_WARNING
        reason = f"{direction} range, within tolerance"
    else:
        severity = SEVERITY_OK
        reason = "in range"

    return {
        "equipment_id": spec.equipment_id,
        "severity": severity,
        "stale": stale,
        "reason": reason,
        "celsius": latest.celsius,
        "out_of_range_minutes": out_of_range_minutes,
    }


def format_summary(summary: ComplianceSummary, *, unit: str = "°C") -> str:
    """Render a compliance summary as a readable block in Portuguese.

    Used for the connector's document body and for operator-facing reports,
    so the wording follows the operation's vocabulary rather than the type
    names.
    """
    label = summary.name or summary.equipment_id
    lines = [
        f"Equipamento: {label}",
        f"Leituras: {summary.reading_count}",
        f"Cobertura do monitoramento: {summary.coverage_ratio * 100:.1f}%",
    ]

    if summary.reading_count:
        lines.append(
            f"Faixa observada: {summary.min_celsius:.1f}{unit} a "
            f"{summary.max_celsius:.1f}{unit} (média {summary.mean_celsius:.1f}{unit})"
        )
    if summary.in_range_ratio is not None:
        lines.append(f"Leituras dentro da faixa: {summary.in_range_ratio * 100:.1f}%")

    if summary.excursions:
        lines.append(f"Desvios: {len(summary.excursions)}")
        worst = summary.worst_excursion
        if worst is not None:
            lines.append(
                f"Pior desvio: {worst.duration_minutes:.0f} min, pico "
                f"{worst.peak_celsius:.1f}{unit} "
                f"({worst.degree_minutes:.0f} {unit}·min acumulados)"
            )
    else:
        lines.append("Desvios: nenhum")

    if summary.gaps:
        total_gap = sum(gap.duration_minutes for gap in summary.gaps)
        lines.append(
            f"Falhas de monitoramento: {len(summary.gaps)} "
            f"({total_gap:.0f} min sem leitura)"
        )

    lines.append(f"Situação: {summary.severity}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_GAP_MULTIPLIER",
    "SEVERITY_CRITICAL",
    "SEVERITY_OK",
    "SEVERITY_WARNING",
    "current_status",
    "find_excursions",
    "find_gaps",
    "format_summary",
    "summarize",
]
