# Synthetic record families

`src/investigator/records.py` defines the plain dataclasses of record contract v2 (`demo-records/v2`, issue #100): the dry-end setup record (one per run setup, up to three order lanes across the web; the fact table), the wet-end record (one paper run), the shift record, the stop record (the downtime log), the roll record (roll stock) and the customer dimension. Their field names and units mirror the shape in which a corrugator's MES records a **generic** dry-end setup, so the data reads like plant records; they are our own documented demo schema, not any vendor's table, and nothing here claims wire-format compatibility. [The record contract](RECORD_CONTRACT.md) maps every field to its typical MES field and its store column, states the identities every setup satisfies and holds the reject taxonomy.

`src/investigator/synth.py` generates any range of production days of one machine from a seed, one day at a time: every random draw of a day comes from streams keyed by the seed and a date (issue #35), so a day is byte-identical in every range that contains it. The data starts on 1 January 2025 (`synth.DATASET_FIRST_DAY`, owner 2026-11-11) and ends on `as_of` (`INVESTIGATOR_AS_OF`; the image's build date minus one day; 2026-09-30 for the tests and the golden set): a fixed start and a rolling end. `src/investigator/record_store.py` builds that window once per process (or loads it prebuilt) into one in-memory DuckDB, derives the per-day tables the daily tools read (`RecordStore.day_analysis`, see [AGENT_EXECUTION](AGENT_EXECUTION.md)) and answers the period tools over the whole window ([METRICS](METRICS.md)). The one-day XML fixture (`fixture.generate()`) remains only for its own tests.

## Generating

```python
from datetime import date
from investigator.synth import generate_day, generate_months, generate_window, write_jsonl, read_jsonl
data = generate_window(date(2026, 9, 30), seed=2026, machine_id="CORR-1")   # 1 January 2025 to 30 September 2026
data = generate_months(seed=2026, months=(("2026", 8), ("2026", 9)))          # or any months
day = generate_day(date(2026, 9, 14))     # one production day: the same records as inside either range
data["content_hash"]            # sha256 over the JSONL files of the range
write_jsonl(data, "artifacts/synthetic")   # one .jsonl per family and dimension + manifest.json
same = read_jsonl("artifacts/synthetic")   # dataclasses again; manifest hash must match
```

The same seed gives byte-identical JSONL, and a day gives the same records whatever range it is generated in. Two months generate in about a second and a half, the golden window (456 production days) in about fifteen seconds. Generated files are ignored by git (`*.jsonl`); write them under `artifacts/`.

## Units and conventions

- Feet, inches, square feet, ft/min, whole seconds. Dates `YYYYMMDD`, times `HHMMSS`, durations `HHMMSS`, all in the fixed demo-local clock (no timezone, no DST).
- Production days are Monday to Friday. Weekends are plant-down and emit no records; there is no holiday calendar. A production day starts 07:00 and is labelled by its start date; shift 1 = 07:00, 2 = 15:00, 3 = 23:00, each 480 scheduled minutes.
- `lineal_ft` counts every foot of web through the machine: order sheets *and* full-web shear chops. Gross area is therefore exactly `lineal_ft x web_width_in / 12`. Order travel (what reached the cutoff knives) is `lineal_ft - chop_sqft x 12 / web_width_in`, and it is the setup's longest lane.
- Lanes share the web's travel: a lane runs `cuts x sheet_length_in / 12` feet, and the lanes of a setup end within one knife rotation. Outs multiply sheets and used width, never distance.
- Web geometry: `sum(sheet_width_in x number_of_outs) + trim_in = web_width_in`, trim split evenly, web centered, and the web is the narrowest of 90, 95, 100, 105, 110 in at least as wide as the used width plus the day's minimum trim. Sheet widths are in eighths of an inch. Grades and target speeds are `fixture.GRADES`.
- Averages include downtime: a setup's `avg_speed_fpm` is lineal over elapsed minutes with stops inside; a shift's is total lineal over 480 minutes. `max_speed_fpm` on wet ends and shifts is the highest running speed (lineal over running minutes) of their setups.
- Rounding: feet, square feet and speeds carry one decimal, percentages two. Aggregates are sums of the rounded setup values, rounded once more.

## DryEndSetupRecord (one per run setup)

| Field | Unit | Meaning |
| --- | --- | --- |
| `setup_id` | text | 20 characters: 4-char machine code + start `YYYYMMDDHHMMSS` + 2-digit sequence in the shift |
| `machine_id` | text | the corrugator (`CORR-1`) |
| `start_date`, `start_time`, `end_date`, `end_time` | YYYYMMDD / HHMMSS | record interval; end = start + running + stop seconds |
| `actual_run_sec` | s | running time: elapsed minus `stop_time_sec` |
| `lineal_ft` | ft | as run: the longest lane's actual travel plus the shear chops |
| `total_nominal_ft` | ft | scheduled: the longest lane's nominal cuts x its length / 12 |
| `avg_speed_fpm` | ft/min | `lineal_ft` / elapsed minutes, stops included |
| `web_width_in` | in | paper width, one of 90, 95, 100, 105, 110 |
| `quality_id` | text | board grade key of `fixture.GRADES` |
| `total_produced_sqft` | sq ft | gross area, trim and chops included |
| `trim_sqft` | sq ft | side trim = order travel x `trim_in` / 12 |
| `trim_pct` | % | 100 x `trim_in` / `web_width_in` |
| `change_code` | O / S | a code with a lookup (`records.CHANGE_CODES`): **O** a regular order change; **S** shift split, continuing `split_from_setup_id` across a shift boundary |
| `stop_time_sec`, `number_of_stops` | s, count | the sum and the count of the StopRecords naming this `setup_id` |
| `shift_number` | 1-3 | shift the record lies in |
| `chop_sqft` | sq ft | full-web shear chops, 34 inches each, part of gross |
| `swing_knife_sqft` | sq ft / null | the swing knife's area; null on the demo machine, which has none, never zero |
| `is_fanfold` | bool | the setup ran to a fanfold delivery (one lane) |
| `sheet_type`, `filler` | text, bool | product / bottom_sheet / cap_sheet (product when any lane is); a filler lane was added to fill a tough trim |
| `lanes` | list | 1-3 `OrderLane`, one order per lane |
| `production_date` | YYYYMMDD | production day (07:00 start) of the shift |
| `rejects` | list | `RejectEntry` lines |
| `wet_end_id` | text | the paper run that aggregates this setup |
| `split_from_setup_id` | text / null | for `S`, the record this one continues |

### OrderLane

| Field | Unit | Meaning |
| --- | --- | --- |
| `lane` | 1-3 | position across the web |
| `order_number` | text | `SO-YYMMDDnnnn`: the production day and the order's sequence in it; unique per order, once per setup; a remainder runs again later in its day |
| `part_number` | text | `P-nnnnn` (`PLT-wwll` for pallet sheets) |
| `customer_id` | text | the customer dimension's key (C000 is the plant) |
| `order_quantity` | sheets | the order's demand over all its setups; 0 for a filler lane |
| `sheet_width_in`, `sheet_length_in` | in | ordered sheet, width in eighths (12-60), length = the cutoff (20-92) |
| `number_of_outs` | count | sheets across the web for this lane |
| `cutoff_allocation` | 1-2 | the cutoff knife (lane N -> knife N -> stacker N) |
| `nominal_cuts`, `actual_cuts` | count | scheduled and as run; actual within +-10% of nominal, most within +-2% (the over/under-run window) |
| `sheet_type`, `filler` | text, bool | product, or a pallet bottom / cap sheet run as a filler |
| `cutoff_waste_boards`, `stacker_waste_boards` | count | default waste-board counts at an order change: boards lost at the lane's cutoff knife and its stacker (0 on an S split) |
| `cutoff_waste_sqft`, `stacker_waste_sqft` | sq ft | the boards x the lane's sheet size |
| `stack_height_sheets` | count | sheets per stack |
| `folds`, `sheets_per_pallet`, `pallets`, `perforation_length_in` | | fanfold only, else null |

### RejectEntry

| Field | Unit | Meaning |
| --- | --- | --- |
| `category` | key | the taxonomy's category (`reject_reasons.json`): bond, warp, cracking, misalignment, handling, paper, changeover, print, admin; its family and waste line follow from the taxonomy |
| `reason` | text | the reported reason within the category, a recorded observation, not a proven cause |
| `liner`, `side` | text / null | where on the board, as attributes: liner top / bottom / medium / DB / SF, side drive / operator / middle |
| `lane` | 1-3 | whose sheets were rejected |
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

## RollRecord (one per roll fed into a paper run)

| Field | Unit | Meaning |
| --- | --- | --- |
| `roll_id` | text | `R` + production date + 4-digit sequence |
| `wet_end_id` | text | the paper run it fed |
| `stand`, `paper_grade` | text | the roll stand of the grade's construction and the paper on it |
| `width_in` | in | the run's web width |
| `lineal_ft` | ft | feet fed into the run (a medium's include its flute take-up: C 1.43, B 1.32, E 1.25) |
| `peel_sqft` | sq ft | outer wraps stripped at roll prep |
| `core_sqft`, `spliced_out` | sq ft, bool | paper left on the core when the roll was spliced out; 0 for the last roll on a stand, returned to stock at the paper change |
| `production_date` | YYYYMMDD | the run's production day |

## ShiftRecord (one per 480-minute shift)

| Field | Unit | Meaning |
| --- | --- | --- |
| `shift_number`, `production_date` | | 1 / 2 / 3 within the production day |
| `group_name` | text | crew label `Crew A` / `Crew B` / `Crew C`, rotating weekly; never a person's name |
| `start_date`, `start_time`, `end_date`, `end_time`, `duration` | | fixed window; duration `080000` |
| `total_lineal_ft`, `total_produced_sqft`, `side_trim_sqft`, `chop_sqft` | ft, sq ft | exact sums of the setups carrying this date and shift |
| `reject_sqft` | sq ft | dry-end quality rejects (the four dry-end families) |
| `changeover_sqft`, `admin_sqft` | sq ft | changeover rejects (counted with shear) and planning / admin entries |
| `cutoff_waste_sqft`, `stacker_waste_sqft` | sq ft | the lanes' cutoff-knife and stacker waste boards |
| `max_speed_fpm` | ft/min | highest running speed of its setups |
| `avg_speed_fpm` | ft/min | `total_lineal_ft` / 480, downtime included |
| `wet_end_ids` | list | wet ends with at least one setup in the shift |
| `scheduled_minutes` | min | 480 |
| `bale_sqft` | sq ft | non-protocol scale reading of baled waste = every recorded waste line above + an unexplained remainder of 0.1-0.4% of gross, so bale percent reads slightly above the sum of its parts |

## StopRecord (one row of the downtime log)

| Field | Unit | Meaning |
| --- | --- | --- |
| `stop_id` | text | `ST` + start `YYYYMMDDHHMMSS` |
| `start_date`, `start_time`, `end_date`, `end_time`, `duration_sec` | | whole seconds; stops never overlap and always sit inside one setup |
| `kind` | Maintenance / Operator / PM | the stop category; PM = the scheduled planned-maintenance window, every PM stop carries the reason `Planned maintenance` |
| `place` | text | the reason group: Upper knife, Lower knife, Shear knife, Slitter/scorer, Double backer, Splicer 1-5, Glue dams, Stacker, Single facer, Steam system, Power |
| `reason` | text | nine unplanned reasons - Knife / slitter jam, Missed splice, Raw material (roll), Stacker, Order change, Glue / starch, Steam / moisture, Power outage, Other - plus `Planned maintenance`, used only by `PM`-kind stops so the planned window is its own Pareto row rather than inflating Other |
| `shift_number`, `production_date` | | the shift the stop fell in |
| `setup_id` | text | the setup record that contains it |
| `comment` | text | the operator's free text, verbatim; often empty (`""`) |

## CustomerRecord (the customer dimension)

`customer_id`, `name`, `destination`, `ship_to`; no measures. Forty invented customers and `C000`, the plant itself (pallet sheets).

## Generator behaviour and the bands it keeps

These are simulation choices (`synth.py` constants), not plant standards. `tests/test_records_invariants.py` and `tests/test_record_contract.py` check each line for every record of August and September 2026.

- **Seeding by date** (issue #35): a production day draws from `Random(f"{seed}:{day}")`; its week's speed factor from `Random(f"{seed}:week:{monday}")`; its month's power outage from `Random(f"{seed}:outage:{yyyy-mm}")`, chosen among every shift of the whole month; crews rotate weekly counted from Monday 2026-08-03. Nothing carries from one production day into the next, so a day regenerates byte-identically in any range, and moving the window by a day changes no day it keeps (`tests/test_synth_determinism.py`).
- **Scheduling** (issue #100): each day's orders are drawn (customer, sheet width x length in eighths and inches, quantity of 250 or more sheets rounded to 50, quality ID; about 1% fanfold) and scheduled into setups on one grade at a time (blocks of 4-14 setups): the order being continued or the oldest open one leads, paired with each of the grade's six open orders at the outs that fill each paper width, on the narrowest paper that fits with the day's minimum trim (2 in, 1 in a side). Leaving the running paper costs a 3-point penalty and a pallet-sheet filler lane another 3 points, so paper runs last about three setups and fillers are rare. The lane that finishes first sets the length; a lane within 1,000 ft of finishing finishes too, and the remainder of the other order leads the next setup. The over/under-run is drawn once per setup and both lanes follow it, so they stay within one knife rotation as run.
- **Time**: setups tile each shift with no gaps; a setup that would cross the boundary between shifts 1 and 2 or 2 and 3 is split there (first part keeps its code, second part is `S` and shares the lanes; the cuts are divided by running time). A remainder with under 60 s of running time is not run; a first part under 60 s stretches the prior record instead. At the end of shift 3 (07:00 the next morning) the open run ends and the next production day starts fresh, as after a weekend, so no record or paper run crosses a production day; orders still open then stay open.
- **Output**: about 2.5 million gross sq ft per shift (6-8.5 million per production day) on a 101-104 in average web (about 99 in of used width) on a 110-inch machine; about 7,000 lineal ft per dry-end setup (roughly 120 setups a day) and about 23,000 lineal ft per paper run (roughly 37 paper changes a day); week-to-week volume moves a few percent through a per-week speed draw. Running speed is the grade target x crew factor (0.91-0.97) x shift factor x noise; displayed averages include stops.
- **Downtime**: Maintenance + PM between 2% and 3% of scheduled minutes per month; PM is one 120-135 minute window every Thursday on shift 1 (the rebuild week runs 180 min), recorded with kind `PM` and reason `Planned maintenance`, plus unplanned maintenance stops (Monday x1.40, August x1.08; operator stops Monday x1.15), one 10-15 minute power outage per month. Operator stops separately 1-3%. About half the stops carry a comment (four in five order changes carry none).
- **Waste per month**: side trim about 3.0% of gross before the seeded story (schedule-driven), shear chops 0.4-1.0%; dry-end quality rejects about 1.0-1.1% split bond 0.40 / warp 0.30-0.42 / misalignment 0.20 / other 0.10 points (cracking, handling, paper, print); changeover rejects about 0.15% (counted with shear), planning / admin entries about 0.04%, cutoff-knife and stacker waste boards about 0.2% together. Rejects are episodic: a lane records a category on a share of its setups (bond and warp 60%, the small categories 8-15%). Inside warp about 42% up, 24% down, 18% twist, 10% S, 6% end to end; inside bond delamination leads. Rejected sheets are discarded; they are never replaced in the records. Each missed splice adds a run of 8-14 chops (a web break).
- **Roll stock**: each paper run mounts fresh rolls of its width on every stand of its grade (three for single wall, five for double wall); a roll holds 12,000-24,000 ft, its outer 15-80 ft are peeled at prep, and a roll spliced out leaves 60-200 ft on its core.
- **Determinism**: the content hash of August and September 2026 is pinned in `tests/test_synth_determinism.py`, and that of the golden window in `evals/golden_values.json`; change them only with an intended generator change.

## Seeded stories

The answer keys live in `src/investigator/stories.py` and are read only by the generator; no tool, analysis, agent, server or report module imports them (pinned by test). The records must reveal them on their own:

1. A slitter/scorer rebuild in the Thursday 20 August PM window; afterwards the scheduler's minimum trim allowance creeps about a tenth of an inch per week through 31 October 2026, so side trim of gross creeps up with it, then returns to its pre-story level; knife / slitter jam stops rise over the same weeks, concentrating on the slitter/scorer, and end with the creep.
2. A missed-splice problem on Splicer 3 in the week of 7-13 September: missed-splice minutes roughly triple, stop counts rise, shear chops follow.
3. Warp rejects are higher on the first production day after a weekend (Monday x1.5) and in August (0.42% of gross) than in September (0.30%) or any other month.

The stories keep their 2026 dates in every window. The first ends on 31 October 2026 (owner, PR #91; the jam effect with it, issue #100): side trim of gross runs about 3.0% before the rebuild, 3.5% in September and 4.0% in late October, and is back at about 3.0% from November (`min_trim_in` in `synth.py`, pinned by `test_trim_creep_ends_on_31_october_and_trim_returns_to_its_pre_story_level`); the knife / slitter jam rate is three times the baseline after the rebuild and back at it from November (`test_knife_slitter_jams_end_with_the_trim_creep_on_31_october`).

## Not modelled

Holidays, timezone / DST, overlapping or shift-crossing stops, an order ledger beyond each lane's demand (the daily order matrix keeps requested / remaining sheets null), reject replacement, accepted-shipment accounting, paper recipes and basis weights, the swing knife's area (the demo machine has none), a third lane (two cutoff knives), and any vendor field semantics beyond the definitions above and in [the record contract](RECORD_CONTRACT.md).
