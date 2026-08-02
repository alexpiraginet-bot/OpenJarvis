# Cold Chain

Watches refrigeration equipment against ranges you declare, flags deviations
that outlast a tolerance, and records the continuous trail a food-safety audit
asks for.

!!! warning "The module enforces a spec — it does not decide what is safe"
    Setpoints come from your written procedure and the equipment
    manufacturer, and must be declared explicitly. There are no built-in
    "safe" temperatures, because inventing them in code would produce
    confident, wrong compliance claims. `spec_from_dict` raises rather than
    defaulting a missing range.

## Two distinct failures

**An excursion** is equipment outside its range for longer than the
tolerance. The tolerance is the whole point: a display case opened for every
customer crosses its limit all day, and without a tolerance every service
window would raise an alarm until nobody read the alarms anymore.

**A monitoring gap** is a stretch with no readings at all. It is a finding in
its own right — an unmonitored freezer and a healthy one look identical from
the outside, so a system that only scores recorded values would call a dead
sensor a perfect day. `coverage_ratio` reports how much of the period was
actually watched.

## Declaring equipment

```python
from openjarvis.coldchain import ColdChainStore, EquipmentSpec

store = ColdChainStore("~/.openjarvis/coldchain/coldchain.db")

store.register_equipment(
    EquipmentSpec(
        equipment_id="freezer-01",
        name="Freezer de estoque",
        min_celsius=-22.0,
        max_celsius=-18.0,
        tolerance_minutes=15.0,        # door openings must not raise alarms
        expected_interval_seconds=300, # match the probe's real polling period
        site_id="matriz",
    )
)
```

Specs are upserted, so revising a setpoint updates the row rather than
duplicating it. Note that recomputing an old period uses the *current* spec.

## Start today, from the paper log

If a written procedure already has someone recording temperatures on a
clipboard, digitizing that log costs nothing and immediately shows where the
current practice has holes:

```csv
data,hora,equipamento,temperatura
2026-08-02,08:00,freezer-01,"-19,5"
2026-08-02,14:00,freezer-01,-20.1
```

```python
from openjarvis.coldchain.ingest import readings_from_csv

store.record_readings(readings_from_csv("temperaturas.csv", site_id="matriz"))
```

Comma decimals (`-19,5`), a `°C` suffix, Portuguese or English headers, and a
spreadsheet BOM are all handled. Logs that track a single equipment can omit
the column and pass `equipment_id=` instead.

## Connecting probes

Any networked probe publishes the same payload:

```json
{"timestamp": "2026-08-02T03:15:00-03:00",
 "equipment_id": "freezer-01", "celsius": -19.4,
 "sensor_id": "ds18b20-a"}
```

```python
from openjarvis.coldchain.ingest import reading_from_payload

store.record_reading(reading_from_payload(payload, site_id="matriz"))
```

Ingestion is idempotent — readings carry a deterministic id, so a probe
reconnecting and flushing its buffer cannot write duplicates.

## Alerting

`current_status` is what an alerting loop calls. It escalates a deviation that
is *still building* rather than waiting for it to end, and treats silence as a
fault rather than as good news:

```python
from datetime import datetime, timezone
from openjarvis.coldchain import current_status

spec = store.get_equipment("freezer-01")
status = current_status(
    store.readings(equipment_id="freezer-01"),
    spec,
    now=datetime.now(timezone.utc),
)
# {'equipment_id': 'freezer-01', 'severity': 'critical', 'stale': False,
#  'reason': 'above range for 40 min', 'celsius': -9.0,
#  'out_of_range_minutes': 40.0}
```

| Severity | Meaning |
|---|---|
| `ok` | In range, reporting normally |
| `warning` | Out of range but within tolerance, **or** the sensor went silent |
| `critical` | Out of range past the tolerance |

## Compliance summaries

```python
from openjarvis.coldchain import summarize, format_summary

summary = summarize(
    store.readings(equipment_id="freezer-01"),
    spec,
    period_start=day_start,
    period_end=day_end,
)
print(format_summary(summary))
```

```
Equipamento: Freezer de estoque
Leituras: 288
Cobertura do monitoramento: 100.0%
Faixa observada: -21.4°C a -18.8°C (média -20.0°C)
Leituras dentro da faixa: 100.0%
Desvios: nenhum
Situação: ok
```

### Reading an excursion

`degree_minutes` integrates deviation over time (°C × min). It is what
separates a shallow drift lasting hours from a brief severe spike — two
failures that can share a duration and a peak but do very different things to
product:

```python
worst = summary.worst_excursion
worst.direction         # 'above' — the dangerous direction for frozen product
worst.duration_minutes
worst.peak_celsius
worst.degree_minutes    # cumulative thermal load
```

A run ends when the equipment returns to range *or* crosses to the other side,
so drifting warm and then running cold are reported as two events rather than
merged into one.

## Connector

`ColdChainConnector` emits one `Document` per equipment per day, so
refrigeration status joins the same digest and retrieval path as the rest of
the operation's data:

```python
from openjarvis.connectors.coldchain import ColdChainConnector

for doc in ColdChainConnector(site_id="matriz").sync():
    if doc.metadata["critical"]:
        print(doc.title, doc.content)
```

Each document states the range it was judged against, not just the verdict —
a report that omits the criteria cannot be audited.
