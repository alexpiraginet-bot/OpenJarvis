"""Cold-chain temperature monitoring and compliance evidence.

Watches refrigeration equipment against ranges the operator declares, flags
deviations that outlast a tolerance, and records the continuous trail a
food-safety audit asks for.

The module enforces a declared spec; it does not decide what is safe. Setpoints
belong to the operation's written procedure and the equipment manufacturer, and
inventing them in code would produce confident, wrong compliance claims.

Layers, bottom up:

``types``
    :class:`~openjarvis.coldchain.types.EquipmentSpec`,
    :class:`~openjarvis.coldchain.types.TemperatureReading`, and the findings
    (:class:`~openjarvis.coldchain.types.Excursion`,
    :class:`~openjarvis.coldchain.types.MonitoringGap`).

``ingest``
    Adapters from a handwritten log, a probe's JSON payload, or a config dict.

``store``
    Local SQLite persistence for specs and readings.

``monitor``
    Excursion and gap detection, live status for alerting, and the period
    compliance summary.

Two failures are treated as distinct. An *excursion* is equipment outside its
range for longer than the tolerance — the tolerance is what separates a display
case opened for every customer from a compressor that failed overnight. A
*monitoring gap* is a stretch with no readings at all, which is its own finding:
an unmonitored freezer and a healthy one look identical from the outside, and a
system that only scores recorded values would call a dead sensor a perfect day.
"""

from openjarvis.coldchain.monitor import (
    current_status,
    find_excursions,
    find_gaps,
    format_summary,
    summarize,
)
from openjarvis.coldchain.store import ColdChainStore
from openjarvis.coldchain.types import (
    ABOVE,
    BELOW,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARNING,
    ComplianceSummary,
    EquipmentSpec,
    Excursion,
    MonitoringGap,
    TemperatureReading,
)

__all__ = [
    "ABOVE",
    "BELOW",
    "SEVERITY_CRITICAL",
    "SEVERITY_OK",
    "SEVERITY_WARNING",
    "ColdChainStore",
    "ComplianceSummary",
    "EquipmentSpec",
    "Excursion",
    "MonitoringGap",
    "TemperatureReading",
    "current_status",
    "find_excursions",
    "find_gaps",
    "format_summary",
    "summarize",
]
