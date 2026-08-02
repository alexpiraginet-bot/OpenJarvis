# Product Consumption

Recovers the product mix when the point of sale cannot report it — a gelateria
that rings up "copo pequeno" without recording which flavours went into it. The
mix is invisible in sales data, and the only way back to it is measuring what
left the tub.

The unit is **mass**. Production happens in kilograms, so a mix in kilograms is
the one that can be acted on. Servings would additionally require a portion size
that nobody measures reliably.

## The correction that matters

Raw share answers "what did we sell?". It is also the number that quietly
sabotages production planning:

> A flavour that ran out at 14:00 contributes only the mass it managed to sell
> before disappearing. Plan from raw share and you under-produce the fastest
> sellers every cycle — which makes them run out earlier, which lowers their
> share further.

`demand_share` breaks that loop by ranking on **depletion rate while actually
available** rather than total mass. The two agree when nothing ran out, and
diverge exactly when something did.

```
=== por consumo bruto ===
Chocolate: 47% (4.0 kg)
Pistache: 35% (3.0 kg) — esgotou; demanda real ~62%
Morango: 18% (1.5 kg)

=== por demanda (ordem para produzir) ===
Pistache: 62%  (745 g/h, 33 porções)
Chocolate: 28%  (333 g/h, 44 porções)
Morango: 10%  (125 g/h, 17 porções)
```

Pistachio looks like a mid-table flavour at 35% of mass consumed. It is actually
62% of demand — it just spent two thirds of the day absent. Producing to the
first table guarantees it runs out again tomorrow.

## Start today, with a scale

```csv
data,hora,sabor,gramas,tipo
2026-08-02,08:00,pistache,3000,abertura
2026-08-02,20:00,pistache,420,fechamento
```

```python
from openjarvis.consumption import ConsumptionStore, ProductSpec, consume, mix
from openjarvis.consumption.ingest import readings_from_csv

store = ConsumptionStore("~/.openjarvis/consumption/consumption.db")
store.register_product(ProductSpec("pistache", name="Pistache", portion_grams=90))
store.record_readings(readings_from_csv("pesagem.csv", site_id="matriz"))
```

Mass may be given in grams or in a `kg` column — the adapter converts, so nobody
has to remember which unit the system wanted. Comma decimals, unit suffixes
(`420 g`, `3,5 kg`), Portuguese or English headers, and a spreadsheet BOM are all
handled, and `Pistache` / `pistache` normalize to one flavour rather than two.

Record top-ups, or a refilled tub reads as a suspiciously light day:

```python
from openjarvis.consumption.ingest import replenishment_from_row

store.record_replenishments([replenishment_from_row(row, site_id="matriz")])
```

## Adding a camera later

A camera estimating fill level writes the same `StockReading` a scale does:

```json
{"timestamp": "2026-08-02T14:03:00-03:00",
 "product_id": "pistache", "grams": 1840, "source": "camera"}
```

The two are complements, not alternatives. Scale readings are the ground truth a
vision estimate has to be calibrated against — without them there is no way to
know whether the estimate is right.

## Reading the results

```python
entries = mix(
    consume(readings, replenishments,
            period_start=opening, period_end=closing, specs=specs),
    specs=specs,
    by="demand_share",     # the order to plan production from
)
```

!!! warning "Pass the trading window, not the calendar day"
    Availability is measured against the period you hand in. Midnight-to-midnight
    for a shop open twelve hours counts the closed hours as time every product
    was on offer, which inflates each availability denominator and washes out the
    entire correction. `ConsumptionConnector` derives the window from the
    readings themselves — the open and close weighings already describe the
    trading day.

| Field | Meaning |
|---|---|
| `share` | Raw share of mass consumed — what happened |
| `demand_share` | Share corrected for availability — what to produce to |
| `grams_per_hour` | Depletion rate while available |
| `availability_ratio` | Share of the window the product was on offer |
| `stockout` | Ran out at some point |
| `suppressed` | A stockout held it below its real demand |
| `unrecorded_replenishment` | Ended heavier than possible — someone topped up off the books |

Shares are `None`, never `0.0`, when there is nothing to divide by. A period with
no consumption has an undefined mix, and zeros would average into later reports
as though they were measurements.

## Predicting a stockout

```python
from openjarvis.consumption import time_to_empty_hours

latest = store.latest_reading("pistache")
time_to_empty_hours(latest.grams, entry.grams_per_hour)   # e.g. 2.4
```

`None` when the rate is unknown or not positive — a product that is not moving
has no meaningful time to empty, and a huge number would read as a prediction
rather than as the absence of one.

## Connector

`ConsumptionConnector` emits one `Document` per trading day and names the
flavours whose share understates their demand:

```python
from openjarvis.connectors.consumption import ConsumptionConnector

for doc in ConsumptionConnector(site_id="matriz").sync():
    print(doc.content)
```

```
Consumo total: 7.0 kg

Chocolate: 57% (4.0 kg)
Pistache: 43% (3.0 kg) — esgotou; demanda real ~69%

Sabores reprimidos por ruptura: Pistache. O % consumido subestima a demanda
desses sabores — planeje produção pelo % de demanda.
```

Shares render as whole percentages on purpose. Measured mass carries real
uncertainty — a scale read twice a day, or a camera estimate — and a decimal
place would advertise precision the input does not have.
