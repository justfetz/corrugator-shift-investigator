# Synthetic record families

`src/investigator/records.py` defines four plain dataclasses whose field names and units mirror the shape of a corrugator controller's own record types: the dry-end setup record ("P04P"-shaped), the wet-end record ("P04G"-shaped), the shift record ("P04T"-shaped) and the stop record ("P04S"-shaped). They are **our own documented demo schema** (`demo-records/v1`). The names are borrowed so the data reads like plant records; nothing here claims OEM protocol or wire-format compatibility, and no vendor field semantics are asserted beyond what this page states.

`src/investigator/synth.py` generates two full months (August and September 2026) of one machine from a seed. These records are the app's data source: `src/investigator/record_store.py` builds them once per process into one in-memory DuckDB, derives the per-day tables the daily tools read (`RecordStore.day_analysis`, see [AGENT_EXECUTION](AGENT_EXECUTION.md)) and answers the period tools over the whole two months ([METRICS](METRICS.md)). The one-day XML fixture (`fixture.generate()`) remains only for its own tests.

## Generating

```python
from investigator.synth import generate_months, write_jsonl, read_jsonl
data = generate_months(seed=2026, months=(("2026", 8), ("2026", 9)), machine_id="CORR-1")
data["content_hash"]            # sha256 over the four JSONL files
write_jsonl(data, "artifacts/synthetic")   # four .jsonl files + manifest.json
same = read_jsonl("artifacts/synthetic")   # dataclasses again; manifest hash must match
```

The same seed gives byte-identical JSONL. Two months generate in well under three seconds. Generated files are ignored by git (`*.jsonl`); write them under `artifacts/`.

## Units and conventions

- Feet, inches, square feet, ft/min, whole seconds. Dates `YYYYMMDD`, times `HHMMSS`, durations `HHMMSS`, all in the fixed demo-local clock (no timezone, no DST).
- Production days are Monday to Friday. Weekends are plant-down and emit no records; there is no holiday calendar. A production day starts 07:00 and is labelled by its start date; shift 1 = 07:00, 2 = 15:00, 3 = 23:00, each 480 scheduled minutes.
- `lineal_ft` counts every foot of web through the machine: order sheets *and* full-web shear chops. Gross area is therefore exactly `lineal_ft x web_width_in / 12`. Order travel (what reached the cutoff knives) is `lineal_ft - chop_sqft x 12 / web_width_in`.
- Knives share travel: nominal cuts on each knife multiply out to the same order travel within one cutoff length. Outs multiply sheets and occupied width, never distance.
- Web geometry: `sum(sheet_width_in x number_of_outs) + trim_in = web_width_in`, trim split evenly (1-7 inches per side), web centered. Web widths 98, 95, 92, 87. Grades and target speeds are `fixture.GRADES`.
- Averages include downtime: a setup's `avg_speed_fpm` is lineal over elapsed minutes with stops inside; a shift's is total lineal over 480 minutes. `max_speed_fpm` on wet ends and shifts is the highest running speed (lineal over running minutes) of their setups.
- Rounding: feet, square feet and speeds carry one decimal, percentages two. Aggregates are sums of the rounded setup values, rounded once more.

## DryEndSetupRecord (one per dry-end setup)

| Field | Unit | Meaning |
| --- | --- | --- |
| `setup_id` | text | 20 characters: 4-char machine code + start `YYYYMMDDHHMMSS` + 2-digit sequence in the shift |
| `machine_id` | text | the corrugator (`CORR-1`) |
| `start_date`, `start_time`, `end_date`, `end_time` | YYYYMMDD / HHMMSS | record interval; end = start + running + stop seconds |
| `lineal_ft` | ft | all web travel, order sheets and chops |
| `avg_speed_fpm` | ft/min | `lineal_ft` / elapsed minutes, stops included |
| `web_width_in` | in | paper width (98, 95, 92, 87) |
| `quality_id` | text | board grade key of `fixture.GRADES` |
| `total_nominal_ft` | ft | planned order travel of the guide customer: `nominal_cuts x sheet_length_in / 12` (knives share travel; not a sum over customers) |
| `total_produced_sqft` | sq ft | gross area, trim and chops included |
| `trim_sqft` | sq ft | side trim = order travel x `trim_in` / 12 |
| `trim_pct` | % | 100 x `trim_sqft` / `total_produced_sqft` |
| `change_code` | O / S / C | why this record started: **O** the knife order combination changed; **S** shift split, continuing `split_from_setup_id` across a shift boundary; **C** continuation, knife 1's order continued while another knife's order changed |
| `stop_time_sec`, `number_of_stops` | s, count | the stops that fell inside the record (equal to the StopRecords naming this `setup_id`) |
| `shift_number` | 1-3 | shift the record lies in |
| `chop_sqft` | sq ft | full-web shear chops, 34 inches each, part of gross |
| `kqf_sqft` | sq ft | demo definition: total dry-end reject area (sum of `rejects`) |
| `customers` | list | 1-3 `CustomerBlock`, one order per knife position |
| `production_date` | YYYYMMDD | production day (07:00 start) of the shift |
| `rejects` | list | non-protocol `RejectEntry` lines |
| `wet_end_id` | text | the wet-end record that aggregates this setup |
| `split_from_setup_id` | text / null | for `S`, the record this one continues |

### CustomerBlock

| Field | Unit | Meaning |
| --- | --- | --- |
| `order_number` | text | `SO-nnnnnn`; unique per order, once per setup, may continue onto the next setup |
| `part_number` | text | `P-nnnnn` |
| `sheet_width_in`, `sheet_length_in` | in | ordered sheet; length is the cutoff (20-92) |
| `number_of_outs` | count | sheets across the web for this order |
| `cutoff_allocation` | 1-3 | knife position = position index + 1 |
| `nominal_cuts` | count | planned cuts for this record; `nominal_cuts x sheet_length_in / 12` is within one cutoff of the order travel |
| `actual_cuts` | count | counted cuts; within +-10% of nominal, most within +-2% (the over/under-run window) |
| `hqm_waste_sqft` | sq ft | reject area attributed at the cutoff knife (misalignment) |
| `asm_waste_sqft` | sq ft | reject area attributed at the stacker (bond, warp) |
| `stack_height_sheets` | count | sheets per stack |

### RejectEntry (non-protocol)

| Field | Unit | Meaning |
| --- | --- | --- |
| `reason` | BOND / WARP / MISALIGNMENT | reported observation, not a proven cause |
| `cutoff_allocation` | 1-3 | whose sheets were rejected |
| `sheets` | count | individual sheets; never multiplied by outs again |
| `sqft` | sq ft | `sheets x sheet_width_in x sheet_length_in / 144` |

## WetEndRecord (one per paper run)

| Field | Unit | Meaning |
| --- | --- | --- |
| `wet_end_id` | text | `WE` + production date + 3-digit sequence |
| `quality_id`, `web_width_in` | | constant across its setups |
| `start_date`, `start_time`, `end_date`, `end_time`, `duration` | | first setup start to last setup end; duration HHMMSS |
| `lineal_ft`, `total_produced_sqft`, `side_trim_sqft` | ft, sq ft | exact sums of its setups |
| `max_speed_fpm` | ft/min | highest running speed of its setups |
| `avg_speed_fpm` | ft/min | `lineal_ft` / elapsed minutes, stops included |
| `setup_ids` | list | setups aggregated, in time order and contiguous |
| `shift_numbers` | list | shifts spanned (one or two); a run that crosses a boundary holds an `S` setup or a setup ending exactly on it |
| `production_date` | YYYYMMDD | production day of its first setup |

## ShiftRecord (one per 480-minute shift)

| Field | Unit | Meaning |
| --- | --- | --- |
| `shift_number`, `production_date` | | 1 / 2 / 3 within the production day |
| `group_name` | text | crew label `Crew A` / `Crew B` / `Crew C`, rotating weekly; never a person's name |
| `start_date`, `start_time`, `end_date`, `end_time`, `duration` | | fixed window; duration `080000` |
| `total_lineal_ft`, `total_produced_sqft`, `side_trim_sqft`, `chop_sqft`, `reject_sqft` | ft, sq ft | exact sums of the setups carrying this date and shift |
| `max_speed_fpm` | ft/min | highest running speed of its setups |
| `avg_speed_fpm` | ft/min | `total_lineal_ft` / 480, downtime included |
| `wet_end_ids` | list | wet ends with at least one setup in the shift |
| `scheduled_minutes` | min | 480 |
| `bale_sqft` | sq ft | non-protocol scale reading of baled waste = trim + chops + rejects + an unexplained remainder of 0.1-0.4% of gross, so bale percent reads slightly above the sum of its parts |

## StopRecord (one per machine stop)

| Field | Unit | Meaning |
| --- | --- | --- |
| `stop_id` | text | `ST` + start `YYYYMMDDHHMMSS` |
| `start_date`, `start_time`, `end_date`, `end_time`, `duration_sec` | | whole seconds; stops never overlap and always sit inside one setup |
| `kind` | Maintenance / Operator / PM | PM = planned maintenance window |
| `place` | text | Upper knife, Lower knife, Shear knife, Slitter/scorer, Double backer, Splicer 1-5, Glue dams, Stacker, Single facer, Steam system, Power |
| `reason` | text | Knife / slitter jam, Missed splice, Raw material (roll), Stacker, Order change, Glue / starch, Steam / moisture, Power outage, Other |
| `shift_number`, `production_date` | | the shift the stop fell in |
| `setup_id` | text | the setup record that contains it |
| `user_info` | text | synthetic operator note, always labelled unverified |

## Generator behaviour and the bands it keeps

These are simulation choices (`synth.py` constants), not plant standards. `tests/test_records_invariants.py` checks each line for every record of both months.

- **Time**: setups tile each shift with no gaps; a setup that would cross a shift boundary is split there (first part keeps its code, second part is `S` and shares the orders). A remainder with under 60 s of running time is absorbed; a first part under 60 s stretches the prior record instead. Across a weekend the open run simply ends.
- **Output**: about 5.5-7.5 million gross sq ft per production day (1.8-2.5 million per shift), 150-190 setups and roughly half as many wet ends per day. Running speed is the grade target x crew factor (0.88-0.94) x shift factor x noise; displayed averages include stops.
- **Downtime**: Maintenance + PM between 2% and 3% of scheduled minutes per month; PM is one 120-135 minute window every Thursday on shift 1 (the rebuild week runs 180 min), plus unplanned maintenance stops (Monday x1.25, August x1.08), one 10-15 minute power outage per month. Operator stops separately 1-3%.
- **Waste per month**: side trim 2.5-3.5% of gross, shear chops 0.4-1.0%, dry-end rejects 0.8-1.4% split about bond 0.40 / warp 0.30-0.42 / misalignment 0.30 points. Each missed splice adds a run of 8-14 chops (a web break).
- **Determinism**: `content_hash` is pinned in `tests/test_synth_determinism.py`; change it only with an intended generator change.

## Seeded stories

The answer keys live in `src/investigator/stories.py` and are read only by the generator; no tool, analysis, agent, server or report module imports them (pinned by test). The records must reveal them on their own:

1. A slitter/scorer rebuild in the Thursday 20 August PM window; afterwards side trim creeps about 0.1 percentage point per week and knife / slitter jam stops rise, concentrating on the slitter/scorer.
2. A missed-splice problem on Splicer 3 in the week of 7-13 September: missed-splice minutes roughly triple, stop counts rise, shear chops follow.
3. Warp rejects are higher on the first production day after a weekend (Monday x1.5) and in August (0.42% of gross) than in September (0.30%).

## Not modelled

Holidays, timezone / DST, overlapping or shift-crossing stops, an order quantity ledger (requested / remaining sheets), reject replacement, accepted-shipment accounting, paper recipes, and any vendor field semantics beyond the definitions above.
