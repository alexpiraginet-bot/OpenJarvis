# Retail Footfall

Measures the funnel for a physical storefront: **passersby → entries →
transactions**. It answers the question a single door counter cannot — when
sales drop, was it the storefront failing to pull people in, or the operation
failing to close them?

Everything runs locally. Footfall data is commercially sensitive (a landlord
who can see your capture rate can price rent against it), so the counts never
leave the machine.

## The three rates

| Rate | Formula | What moves it |
|---|---|---|
| **Taxa de captura** (capture) | entries / passersby | Signage, lighting, product visibility, staff posture |
| **Taxa de conversão** (conversion) | transactions / entries | Staffing, queue length, pricing, stock |
| **Conversão total** (pass-to-sale) | transactions / passersby | The end-to-end number — what mall traffic is actually worth |

Rates are `None`, never `0.0`, when the denominator is zero. A closed hour has
an *undefined* capture rate, and folding those zeros into an average would drag
the store's numbers down for no reason.

## Start today, without hardware

Counting every passerby by hand is not sustainable. Counting for fifteen
minutes at a few fixed times a day is — and sampled capture rate is enough to
establish a baseline before any sensor arrives.

Record a tally sheet as CSV:

```csv
data,hora,passantes,entrantes
2026-08-02,14:00,120,8
2026-08-02,15:00,90,11
```

Portuguese or English headers both work (`passantes`/`passersby`,
`entrantes`/`entries`, `saidas`/`exits`), as does a single `timestamp` column
instead of `data` + `hora`.

```python
from openjarvis.retail import FootfallStore, funnel, format_summary
from openjarvis.retail.ingest import events_from_tally_csv, sales_from_rows

store = FootfallStore("~/.openjarvis/retail/footfall.db")

store.record_events(events_from_tally_csv("contagem.csv", site_id="matriz"))

# Sales from a POS export. Brazilian amount formats (`1.234,56`) parse
# correctly rather than silently becoming 1.23.
store.record_sales(
    sales_from_rows(pos_rows, site_id="matriz", source="stone")
)

print(format_summary(funnel(store.intervals(interval="day"))))
```

```
Passantes: 210
Entrantes: 19
Transações: 12
Faturamento: R$ 248.00
Taxa de captura (entrantes/passantes): 9.05%
Taxa de conversão (vendas/entrantes): 63.16%
Conversão total (vendas/passantes): 5.71%
Ticket médio: R$ 20.67
Receita por entrante: R$ 13.05
Receita por passante: R$ 1.18
```

## Connecting a sensor later

Any counter — camera, mmWave radar, break beam — posts the same payload:

```json
{"timestamp": "2026-08-02T14:03:11-03:00",
 "zone": "entrance", "direction": "in", "count": 1,
 "sensor_id": "door-01", "sensor_kind": "mmwave"}
```

```python
from openjarvis.retail.ingest import event_from_payload

store.record_event(event_from_payload(payload, site_id="matriz"))
```

Two zones matter: `corridor` (the mall walkway, direction `pass`) and
`entrance` (the threshold, direction `in` / `out`). Because the analytics layer
never learns what produced an event, swapping hardware keeps the historical
series comparable.

Ingestion is idempotent — every event carries a deterministic id derived from
its payload, so a sensor replaying its buffer after a network drop cannot
double-count.

## Sizing the opportunity

`opportunity_gap` converts a rate into money, which is what justifies spending
on the storefront:

```python
from openjarvis.retail import funnel, opportunity_gap

metrics = funnel(store.intervals(interval="day"))
gap = opportunity_gap(metrics, target_capture_rate=0.06)
print(gap["capture_gap_revenue"])   # extra revenue at 6% capture
```

The estimate holds average ticket and the downstream rate fixed, so treat it as
an order-of-magnitude planning figure, not a forecast — lifting capture usually
pulls in more marginal browsers, which tends to soften conversion rather than
leave it flat.

## Peak hours and occupancy

```python
from openjarvis.retail import occupancy_series, peak_buckets

hours = store.intervals(interval="hour", fill_empty=True)
peak_buckets(hours, key="entries", limit=3)
occupancy_series(hours)   # running headcount, clamped at zero
```

Occupancy accumulates counting error — a door sensor that misses one exit per
hundred crossings drifts upward all day — so read it within a single trading
day rather than across weeks.

## Connector

`RetailFootfallConnector` emits one `Document` per trading day, so store
performance joins the same digest and retrieval path as email and calendar:

```python
from openjarvis.connectors.retail_footfall import RetailFootfallConnector

connector = RetailFootfallConnector(site_id="matriz")
for doc in connector.sync():
    print(doc.title, doc.metadata["metrics"]["capture_rate"])
```

Buckets are anchored to the store's local timezone (`America/Sao_Paulo` by
default), so a 23:00 sale belongs to that trading day rather than the next UTC
one.
