# The record contract

The record contract (`demo-records/v2`, issue #100) is the shape of every synthetic record the investigator reads. It describes a **generic corrugator dry-end setup record**. Corrugators record setups in much the same way whoever built the machine, so the contract keeps what the fields mean and names no vendor, product or source-system table. The dry-end setup is the **fact table**: one row per run setup, with up to three order lanes across the web. Customers are a **dimension**. The paper runs (wet ends), the roll stock they consume, the downtime log, the reject lines and the shifts are linked to the setup by key.

`src/investigator/records.py` defines the records, `src/investigator/synth.py` generates them ([the record families](RECORDS.md) says how) and `src/investigator/record_store.py` loads them into DuckDB under the table names below. Every number a tool returns is a fixed query over these tables. `tests/test_record_contract.py` checks that this page names every record field and every table column, and `tests/test_records_invariants.py` checks the identities on every record.

**Columns of the tables below.** *Ours* is the DuckDB column the tools read. *Record field* is the field in the JSONL record when it differs (the record keeps a date and a time apart, as a machine writes them). *Typical MES field* is the generic name a corrugator's production record gives the same meaning, where one exists: `N` is the lane number. *(ours)* marks a field the contract adds and the machine does not record.

Units: feet, inches, square feet, ft/min, whole seconds. Dates are `YYYYMMDD` and times `HHMMSS` in the records (DATE and TIMESTAMP in the store), on the fixed demo-local clock (no timezone, no DST).

## How the tables link

```
setups                the fact table: one row per run setup
├── setup_orders      its lanes 1-3, each → customers (the dimension)
├── rejects           category · reason · position, per lane
├── stops             the downtime log
└── wet_ends          its paper run → rolls (the roll stock)
shifts                sums of the setups of one (production_date, shift)
```

## setups: the dry-end setup, the fact table

| Ours | Record field | Typical MES field | Notes |
| --- | --- | --- | --- |
| `setup_id` | | `RunSetup` | key: machine code + start stamp + sequence in the shift, 20 characters |
| | `machine_id` | machine | `CORR-1`; one machine per store, so not a column |
| `production_date`, `shift` | | `ProductionDate`, `ShiftNumber` | the 07:00 production day; shift 1, 2 or 3 |
| `started`, `ended` | `start_date` + `start_time`, `end_date` + `end_time` | `StartDate` + `StartTime`, `EndDate` + `EndTime` | the setup's clock; setups tile each shift with no gap |
| `elapsed_seconds` | (ended − started) | (ours) | the setup clock, stops included |
| `run_seconds` | | `ActualRunTime` | running time: elapsed minus stop seconds |
| `lineal_ft` | | `LinealFeetProduced` | **actual** web travel: order sheets and shear chops |
| `scheduled_lineal_ft` | | `TotalScheduledNominalFeet` | **new**: the plan, the longest lane's nominal travel |
| `avg_fpm` | | `AverageSpeedFPM` | lineal feet over elapsed minutes, stops included |
| `web_width_in` | | `WebWidth` | 90 to 110 in, 5-inch steps (110, 105, 100, 95, 90) |
| `quality_id` | | `QualityID` | **new** in name: the board grade key (`fixture.GRADES`, e.g. `32-C`, `275-BC`); it was `grade` |
| `target` | (from `fixture.GRADES`) | (ours) | the grade's target running speed, ft/min |
| `gross_sqft` | | `TotalSquareFootageProduced` | web width × lineal feet / 12 |
| `trim_sqft`, `trim_pct` | | `TrimSquareFootage`, `TrimPercentage` | side trim; `trim_pct` is the setup's geometric trim, 100 × trim inches / web width |
| `shear_sqft` | | `ChopSquareFootage` | the shear chop: full-web 34-inch cuts at order changes and web breaks, part of gross |
| `swing_knife_sqft` | | `SwingKnifeSquareFootage` | swing-knife area; **null** when the machine has no swing knife (CORR-1 has none), never zero |
| `reject_sqft` | | (sum of the reject lines) | dry-end quality rejects: the lines whose category counts as dry-end |
| `cutoff_waste_sqft`, `stacker_waste_sqft` | | (sum of the lanes) | the lanes' waste boards at the cutoff knives and the stackers |
| `change_code` | | `ChangeCode` | a code with a lookup, [below](#change-codes) |
| `stop_seconds`, `stops` | | `StopTimeForThisOrder`, `NumberOfStops` | equal to the sum and the count of the setup's rows in `stops` |
| `is_fanfold` | | `IsFanfold` | the setup runs fanfold; its lanes carry the fanfold fields |
| `sheet_type` | | (ours) | **new**: `product`, `bottom_sheet` or `cap_sheet`, from lane 1's order |
| `filler` | | (ours) | **new**: a lane of plant-use sheets was added to fill a tough trim |
| `wet_end_id` | | (ours) | the paper run (wet end) the setup belongs to |
| `split_from` | | (ours) | for change code `S`, the setup this one continues across a shift boundary |
| | `lanes` | `…N` fields | the order lanes, one row each in `setup_orders` |
| | `rejects` | | the reject lines, one row each in `rejects` |

## setup_orders: the order lanes

The setup record carries its lanes embedded, as a machine writes them. The store gives one row per lane, keyed by `setup_id` and `lane`. Outs belong to the lane, not to the customer.

| Ours | Typical MES field | Notes |
| --- | --- | --- |
| `setup_id`, `production_date`, `shift` | | the setup the lane is part of |
| `lane` | the `N` of the lane fields | 1 to 3, across the web |
| `cutoff_knife` | `CutoffAllocationN` | the cutoff knife the lane is assigned to: lane 1 → knife 1 → stacker 1; the machine has two |
| `order_number`, `part_number` | `OrderNumberN`, `PartNumberN` | `SO-YYMMDDnnnn` (the production day and the order's sequence in it), `P-nnnnn` |
| `customer_id` | `CustomerNameN` | → `customers`; the name lives in the dimension |
| `order_quantity` | `OrderQuantityN` | sheets the order asks for (a filler lane: the sheets it made) |
| `outs` | `NumberOfOutsN` | sheets across the web for this lane |
| `sheet_width_in`, `sheet_length_in` | `SheetWidthN`, `SheetLengthN` | the sheet; its length is the cutoff (12 to 60 by 20 to 92 in) |
| `nominal_cuts` | `NominalCutsN` | scheduled cuts on this setup |
| `actual_cuts` | `ActualCutsN` | counted cuts; within ±10% of nominal, most within ±2% |
| `cutoff_waste_boards`, `stacker_waste_boards` | `CutoffWasteN`, `StackerWasteN` | boards lost at the lane's cutoff knife and at its stacker: the default counts the record carries |
| `cutoff_waste_sqft`, `stacker_waste_sqft` | | the boards × the lane's sheet area; their own waste lines, never dry-end quality |
| `stack_height` | | sheets per stack (`stack_height_sheets` in the record) |
| `folds`, `sheets_per_pallet`, `pallets`, `perforation_length_in` | `FoldsN`, `SheetsPerPalletN`, `PalletsN`, `PerforationLengthN` | fanfold lanes only; null on every other lane |
| `width_share` | (ours) | the lane's share of the setup's used width, outs × sheet width / used width; the shares of a setup add to one |
| `allocated_trim_sqft`, `allocated_shear_sqft` | (ours) | the lane's part of the setup's side trim and shear chops: the setup's figure × `width_share` (identity 6) |

## customers: the dimension

| Ours | Notes |
| --- | --- |
| `customer_id` | `CU001` to `CU048`; `CU000` is the plant itself (bottom sheets, cap sheets, fillers) |
| `name` | a synthetic label, `Customer 017 (produce)`, never a real company |
| `segment`, `destination`, `ship_to` | the market served, a region and distance, a ship-to code |

No measures: what was made for a customer is summed from its lanes.

## rejects: category · reason · position

One line per lane and category of a setup. The reasons are the owner-approved taxonomy, kept as data in `src/investigator/reject_reasons.json`: **category → reason**, with **position as attributes, never as reasons** (`liner` top / bottom / medium / DB / SF, `side` drive / operator / middle). "Bottom liner" is an identity, not a cause; the categories group what damages the board.

| Ours | Record field | Notes |
| --- | --- | --- |
| `setup_id`, `production_date`, `shift` | | the setup |
| `lane` | | whose sheets were rejected |
| `category` | | the taxonomy key, below |
| `reason` | | a reason of that category, e.g. `S warp`, `Delamination`, `Misalign` |
| `liner`, `side` | | the positions the reason takes; null where it takes none |
| `counted_as` | (from the taxonomy) | `dry_end`, `changeover` or `excluded` |
| `sheets` | | individual sheets |
| `sqft` | | sheets × sheet width × sheet length / 144 |

| Category | Key | Controllable | Counted as |
| --- | --- | --- | --- |
| Bond / delamination (temperature) | `bond` | yes | dry-end |
| Warp (temperature) | `warp` | yes | dry-end |
| Cracking / surface | `cracking` | yes | dry-end |
| Misalignment / scoring / slitting | `misalignment` | yes | dry-end |
| Handling damage | `handling` | yes | dry-end |
| Paper / mill | `paper` | partly (receiving and roll prep) | dry-end |
| Changeover | `changeover` | yes | with shear and sequencing, not dry-end quality |
| Print | `print` | not stated | dry-end |
| Planning / admin (not production waste) | `planning` | no | excluded, reported separately |

The reasons inside each category are in the JSON. The demo generator emits only the four categories the dry-end tiles show (`bond`, `warp`, `misalignment`, `cracking`), so the four add up to the dry-end total; the store classifies a line of any category by its `counted_as`, so a record that carries a changeover or planning line keeps it out of dry-end waste. The tools do not yet add changeover lines to shear or list planning lines as their own report; with none generated nothing is lost today, and a record that carried them would show their area in the unexplained remainder until they do. There is no moisture category: dry board sits under bond and warp is its own category, so moisture's old tile is now cracking / surface. Dry-end waste as a percent of the yield after trim and shear is the targets issue's change (#101); the tiles here are still a percent of gross.

## stops: the downtime log

| Ours | Record field | Notes |
| --- | --- | --- |
| `stop_id` | | key: `ST` + start stamp |
| `setup_id` | | → the run setup; "setup 1234 had 2 stops, down 973 seconds" is that setup's `stops` and `stop_seconds` |
| `production_date`, `shift` | | the shift the stop fell in |
| `started`, `ended` | `start_date` + `start_time`, `end_date` + `end_time` | always inside the setup; stops never overlap |
| `seconds` | | ended − started |
| `kind` | | the category: `Maintenance`, `Operator` or `PM` (the scheduled planned-maintenance window); it drives the downtime bands of #101 |
| `place` | | the reason group, where: splicers 1 to 5, stacker, knives, slitter/scorer, glue dams, steam system, single facer, double backer, power |
| `reason` | | what: knife / slitter jam, missed splice, raw material (roll), stacker, order change, glue / starch, steam / moisture, power outage, other; `Planned maintenance` for every PM stop |
| `comment` | | free text; about 40% left empty, the rest plausible crew notes ending `(synthetic note, unverified)`; quoted verbatim, never read as a number |

## wet_ends: the paper runs

| Ours | Record field | Notes |
| --- | --- | --- |
| `wet_end_id` | | key: `WE` + production date + sequence |
| `production_date` | | of its first setup |
| `quality_id`, `web_width_in` | | constant over its setups |
| `started`, `ended` | `start_date` + `start_time`, `end_date` + `end_time`, `duration` | first setup's start to last setup's end |
| `lineal_ft`, `gross_sqft`, `trim_sqft` | | exact sums of its setups |
| `max_fpm`, `avg_fpm` | | highest running speed of its setups; lineal over elapsed minutes |
| `setup_count` | `setup_ids` | its setups, contiguous in time |
| | `shifts` | the shifts it spans (one or two) |

## rolls: the roll stock

| Ours | Notes |
| --- | --- |
| `roll_id` | key: `RL` + the wet end's date and sequence + web position + roll number |
| `wet_end_id`, `production_date` | the paper run the roll fed |
| `position` | the web it fed: `SF1 liner`, `SF1 medium`, `DB liner` (single wall); plus `SF2 liner`, `SF2 medium` (double wall) |
| `paper_grade`, `width_in` | the paper (e.g. `42# liner`, `26# medium`) and its width, the run's web |
| `lineal_ft`, `consumed_sqft` | paper fed into the machine; a medium includes its flute take-up |
| `peel_sqft` | outer wraps stripped at roll prep (about 0.5% of roll stock consumed) |
| `core_sqft` | paper left on the core at a splice (about 0.1%); zero for a roll pulled at the paper change, whose butt goes back to stock |
| `ended_at` | `splice` or `paper change` |

A paper change (a new wet end) starts fresh rolls of the new width at every position. Peel and core are recorded on the rolls, not in the dry-end bale reading, and no tile reads them yet: their bands are #101's.

## shifts

| Ours | Record field | Notes |
| --- | --- | --- |
| `production_date`, `shift` | | 07:00, 15:00, 23:00 |
| `crew` | `group_name` | `Crew A` / `B` / `C`, rotating weekly; never a person |
| | `start_date`, `start_time`, `end_date`, `end_time`, `duration` | the fixed 480-minute window |
| `lineal_ft`, `gross_sqft`, `trim_sqft`, `shear_sqft`, `reject_sqft`, `cutoff_waste_sqft`, `stacker_waste_sqft` | | exact sums of the shift's setups |
| `bale_sqft` | | the scale reading of baled waste: trim + shear + rejects + waste boards + an unexplained 0.1-0.4% of gross |
| `max_fpm`, `avg_fpm` | | highest running speed of its setups; lineal over 480 minutes |
| `scheduled_minutes` | | 480 |
| `wet_end_count` | `wet_end_ids` | wet ends with a setup in the shift |

## The setup math

The generator schedules orders into setups and does not draw setups at random (owner, issue #100). Every setup satisfies these identities, and `tests/test_records_invariants.py` checks each one:

1. `used_width = Σ lanes (outs × sheet_width)`; `trim_in = web_width − used_width`, split evenly on both sides; `trim_pct = trim_in / web_width`.
2. Per lane: lineal = cuts × sheet length / 12; sheets = cuts × outs.
3. The lanes of a setup end within **one knife rotation**: the lane that needs the least travel sets the length (its cuts are ⌈remaining sheets / outs⌉), and each other lane runs only as far as its pair, ⌊guide inches / its sheet length⌋.
4. `scheduled_lineal_ft` is the longest lane's plan; `lineal_ft` is the actual travel (the order travel plus the shear chops).
5. An order is complete when the sheets scheduled over its setups reach its quantity. A lane that did not finish carries its remainder into the next setup. That setup is often one knife with more outs, possibly on narrower paper: the scheduler picks the narrowest web that leaves the minimum trim.
6. Gross = web width × lineal / 12; trim sq ft = trim inches × order travel / 12. A customer's share of a setup's waste is the setup's waste × the lane's share of the used width: `setup_orders.width_share`, with the lane's part of the setup's trim and shear beside it (its rejects and waste boards are already its own). One departure, for the owner to confirm: the owner wrote trim = trim inches × lineal, and here it is over the order travel, because the shear chop runs the full web and carries no side trim of its own (the chop's area is all in `shear_sqft`).

**The owner's worked example**, reproduced exactly by `tests/test_setup_math.py`. Orders: AC Box 15 × 35 in, 2,000 sheets; Jerry's Packaging 17 × 47 in, 3,000 sheets.

- Setup 1, 100-in web, two knives, 2 in of trim a side: 3 out × 15 in (AC Box) + 3 out × 17 in (Jerry's) = 96 in used, trim 4 in = 4.0% (2 out of AC Box would be 81 in, too little). AC Box sets the length: ⌈2,000 / 3⌉ = 667 cuts × 35 in / 12 = 1,945.4 ft. Jerry's runs only as far: ⌊1,945.4 × 12 / 47⌋ = 496 cuts = 1,942.7 ft (2.7 ft apart, under one 47-in cut), 1,488 sheets; 1,512 remain.
- Setup 2, Jerry's remainder, one knife: 5 out × 17 in = 85 in; ⌈1,512 / 5⌉ = 303 cuts × 47 in / 12 = 1,186.8 ft, 1,515 sheets (3 over). On 90-in paper the trim is 5 in = 5.6%; on 95 in 10.5%; on 100 in 15%.

Trim therefore comes out of the pairing. Its average lands near the 3% target only when the pairing is good, which is why trim is schedule-driven.

**One record of each**, from 14 September 2026, shift 2. A 275-BC setup on 95-in paper: lane 1 continues order `SO-2609140006` from the setup before (2,490 sheets ordered, 1,595 cut there), and lane 2 starts `SO-2609140012`:

```json
{"setup_id": "CORR2026091421225934", "machine_id": "CORR-1", "production_date": "20260914", "shift": 2,
 "start_date": "20260914", "start_time": "212259", "end_date": "20260914", "end_time": "213434", "run_seconds": 617,
 "lineal_ft": 4869.7, "scheduled_lineal_ft": 4922.5, "avg_fpm": 420.4, "web_width_in": 95, "quality_id": "275-BC",
 "gross_sqft": 38551.8, "trim_sqft": 804.5, "trim_pct": 2.11, "shear_sqft": 336.5, "swing_knife_sqft": null,
 "reject_sqft": 355.6, "cutoff_waste_sqft": 41.0, "stacker_waste_sqft": 27.0, "change_code": "C", "stop_seconds": 78,
 "stops": 1, "is_fanfold": false, "sheet_type": "product", "filler": false, "wet_end_id": "WE20260914033",
 "lanes": [{"lane": 1, "cutoff_knife": 1, "order_number": "SO-2609140006", "part_number": "P-85022", "customer_id": "CU006",
            "order_quantity": 2490, "outs": 1, "sheet_width_in": 60, "sheet_length_in": 66, "nominal_cuts": 895, "actual_cuts": 877,
            "cutoff_waste_boards": 1, "stacker_waste_boards": 0, "cutoff_waste_sqft": 27.5, "stacker_waste_sqft": 0.0,
            "stack_height_sheets": 117, "folds": null, "sheets_per_pallet": null, "pallets": null, "perforation_length_in": null},
           {"lane": 2, "cutoff_knife": 2, "order_number": "SO-2609140012", "part_number": "P-84296", "customer_id": "CU002",
            "order_quantity": 1830, "outs": 1, "sheet_width_in": 33, "sheet_length_in": 59, "nominal_cuts": 1001, "actual_cuts": 981,
            "cutoff_waste_boards": 1, "stacker_waste_boards": 2, "cutoff_waste_sqft": 13.5, "stacker_waste_sqft": 27.0,
            "stack_height_sheets": 128, "folds": null, "sheets_per_pallet": null, "pallets": null, "perforation_length_in": null}],
 "rejects": [{"lane": 1, "category": "bond", "reason": "Blisters (tension blister)", "liner": "top", "side": null, "sheets": 3, "sqft": 82.5},
             {"lane": 1, "category": "warp", "reason": "Up warp", "liner": null, "side": null, "sheets": 5, "sqft": 137.5},
             {"lane": 1, "category": "misalignment", "reason": "Light scores", "liner": null, "side": null, "sheets": 1, "sqft": 27.5},
             {"lane": 2, "category": "bond", "reason": "Delamination", "liner": "top", "side": "drive", "sheets": 2, "sqft": 27.0},
             {"lane": 2, "category": "warp", "reason": "Up warp", "liner": null, "side": null, "sheets": 5, "sqft": 67.6},
             {"lane": 2, "category": "misalignment", "reason": "Misalign", "liner": null, "side": "operator", "sheets": 1, "sqft": 13.5}],
 "split_from": null}
```

Read it with the identities. 60 + 33 = 93 in used on 95-in paper, so the trim is 2 in = 2.11%. Lane 1 has 895 sheets left of its order and needs less travel, so it sets the length: 895 cuts × 66 in = 4,922.5 ft, the scheduled lineal. Lane 2 runs as far: ⌊895 × 66 / 59⌋ = 1,001 cuts = 4,921.6 ft, 0.9 ft apart, and its 829 remaining sheets carry into the next setup (change code `C` there too). The actual travel ran 1.9% under the plan: 4,827.2 ft of order travel plus fifteen 34-inch chops (42.5 ft, 336.5 sq ft) makes 4,869.7 lineal ft, and 877 actual cuts of lane 1. Gross is 4,869.7 × 95 / 12 = 38,551.8 sq ft, and trim is 4,827.2 × 2 / 12 = 804.5 sq ft.

The setup's one stop, its customers, one roll of its paper run, and the paper run:

```json
{"stop_id": "ST20260914212259", "setup_id": "CORR2026091421225934", "production_date": "20260914", "shift": 2,
 "start_date": "20260914", "start_time": "212259", "end_date": "20260914", "end_time": "212417", "seconds": 78,
 "kind": "Operator", "place": "Stacker", "reason": "Order change", "comment": ""}
{"customer_id": "CU006", "name": "Customer 006 (beverage)", "segment": "beverage", "destination": "Central region, 125 mi", "ship_to": "CU006-ST1"}
{"roll_id": "RL20260914033301", "wet_end_id": "WE20260914033", "production_date": "20260914", "position": "SF2 liner",
 "paper_grade": "42# liner", "width_in": 95, "lineal_ft": 17409.9, "consumed_sqft": 137828.4, "peel_sqft": 325.3,
 "core_sqft": 220.1, "ended_at": "splice"}
{"wet_end_id": "WE20260914033", "production_date": "20260914", "quality_id": "275-BC", "web_width_in": 95,
 "start_date": "20260914", "start_time": "210149", "end_date": "20260914", "end_time": "214551", "duration": "004402",
 "lineal_ft": 17692.5, "gross_sqft": 140065.6, "trim_sqft": 4322.0, "max_fpm": 473.6, "avg_fpm": 401.8,
 "setup_ids": ["CORR2026091421014933", "CORR2026091421225934", "CORR2026091421343435"], "shifts": [2]}
```

## Change codes

| Code | Meaning |
| --- | --- |
| `O` | a regular order change: no order is shared with the setup before |
| `S` | a shift change: the setup split at the shift boundary, continuing `split_from` with the same lanes |
| `C` | a continuation: an order carried from the setup before (possibly onto another paper width of the same grade, at other outs) while another lane changed |

Other codes are added to `records.CHANGE_CODES` as they appear.

## Not modeled

| Typical MES field or measure | Why |
| --- | --- |
| Operator and supervisor names | the records name crews (`Crew A`), never people |
| Steam, pre-heater, glue and plate temperatures; moisture readings | no sensor data: the dry-end categories say what damaged the board, not the temperature that did it, and no answer may assert a temperature as a cause |
| Basis weight, roll weight, paper cost per ton | the rolls carry area, not weight, so tons and paper cost are not computed |
| A third lane on a shared knife | the contract has lanes 1-3, and the generator pairs across the machine's two knives only |
| Swing-knife area on CORR-1 | the machine has no swing knife: `swing_knife_sqft` is null |
| Score positions, flute profiles, slitter-scorer settings | the setup records what was cut, not how the knives were set |
| Due dates, promised quantities, over/under-run allowances | an order carries its quantity only; whether a short or long order was accepted is the customer's business |
| Changeover, print, handling, paper and planning reject lines | in the taxonomy, so a record that carries them is classified; the generator emits none |
| Wet-end funnels (width sequences) and step-up speed loss | the wet-end flow and calibration issue (#102) |
| Holidays, timezone and daylight saving time | the demo clock is fixed, Monday to Friday |

## From v1

Contract v2 replaced the earlier demo schema (`demo-records/v1`) in one change, and the golden values were regenerated once (issue #100). Renamed: `total_produced_sqft` → `gross_sqft`, `chop_sqft` → `shear_sqft`, `total_nominal_ft` → `scheduled_lineal_ft`, `avg_speed_fpm` → `avg_fpm`, `stop_time_sec` / `number_of_stops` → `stop_seconds` / `stops`, `shift_number` → `shift`, `split_from_setup_id` → `split_from`, the customer blocks → `lanes` (`number_of_outs` → `outs`, `cutoff_allocation` → `lane` and `cutoff_knife`), a stop's `duration_sec` / `user_info` → `seconds` / `comment`. The store's tables took the same names (`feet` → `lineal_ft`, `gross` → `gross_sqft`, `grade` → `quality_id`, the lane table `customers` → `setup_orders`). `kqf_sqft` used to hold the dry-end reject total, which is now `reject_sqft`; the swing knife is `swing_knife_sqft`. The lanes' cutoff and stacker figures are now waste-board counts, not reject areas. Reject lines moved from four families (bond, warp, misalignment, moisture) to the owner's categories and reasons with positions.
