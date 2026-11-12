# The record contract

The investigator's records describe a **generic corrugator dry-end setup record**: one row per run setup, up to three orders (lanes) across the web, the way corrugators record a setup whatever their make. Nothing here is one vendor's table, and no vendor, product or source-system table name appears in the code, the docs or the goldens (a test in `tests/test_repo_hygiene.py` fails if one does). The field *meanings* are the plant's: run setup, quality ID, scheduled against actual lineal feet, the shear chop, the swing knife, order lanes with outs, cuts, cutoff knife and stacker waste boards, change codes O and S.

This page is contract v2 (`demo-records/v2`, issue #100). The dry-end setup is the fact table; the generator schedules orders into it and every production day regenerates from it. [The record families](RECORDS.md) documents the JSONL records field by field; [metrics](METRICS.md) gives the formulas the tools compute from them.

The data starts on **1 January 2025** and ends yesterday: a fixed start and a rolling end (owner, 2026-11-11), so a year-to-date has a full previous year under it. `/api/health` reports `store.window.first_day` = 2025-01-01.

## The tables

The store holds the contract as DuckDB tables (`record_store.TABLES`):

| Table | One row per | Key | Links |
| --- | --- | --- | --- |
| `setups` | run setup (the fact table) | `setup_id` | `wet_end_id` → `wet_ends`; `split_from` → `setups` |
| `setup_orders` | order lane of a setup (the lane view; the record keeps the lanes embedded on the setup, as the MES does) | `setup_id`, `lane` | `customer_id` → `customers` |
| `customers` | customer (a dimension, no measures) | `customer_id` | |
| `rejects` | reject line: one lane's rejected sheets by category and reason | `setup_id`, `position` (lane), `category`, `reason` | `category`, `reason` → `reject_reasons` |
| `reject_reasons` | reason of the reject taxonomy (`reject_reasons.json`) | `category`, `reason` | |
| `stops` | row of the downtime log | `stop_id` | `setup_id` → `setups` |
| `wet_ends` | paper run (consecutive setups on one grade and web width) | `wet_end_id` | |
| `rolls` | roll of paper fed into a paper run | `roll_id` | `wet_end_id` → `wet_ends` |
| `shifts` | 480-minute shift | `production_date`, `shift` | |

The store keeps the working column names its tools already read where they predate the contract, and gives every new column the contract's name. In the tables below, **Ours** is the contract's name; where the store column differs it is in brackets.

## The dry-end setup (`setups`)

| Ours [store column] | Record field | Typical MES field | Notes |
| --- | --- | --- | --- |
| `setup_id` | `setup_id` | `RunSetup` | key: machine code + start stamp + sequence in the shift |
| `production_date`, `shift` | `production_date`, `shift_number` | `ProductionDate`, `ShiftNumber` | a production day starts 07:00 and carries its start date |
| `started`, `ended` | `start_date` + `start_time`, `end_date` + `end_time` | `StartDate` + `StartTime`, `EndDate` + `EndTime` | the record keeps the MES's date and time pairs; the store joins them |
| `run_seconds` | `actual_run_sec` | `ActualRunTime` | running time: elapsed minus the stops inside the setup. Elapsed is the store's `seconds` |
| `lineal_ft` [`feet`] | `lineal_ft` | `LinealFeetProduced` | as run: the longest lane's actual cuts × its length / 12, plus the shear chops |
| `scheduled_lineal_ft` | `total_nominal_ft` | `TotalScheduledNominalFeet` | the plan: the longest lane's nominal cuts × its length / 12 |
| `avg_fpm` | `avg_speed_fpm` | `AverageSpeedFPM` | lineal / elapsed minutes, stops included |
| `web_width_in` [`width_in`] | `web_width_in` | `WebWidth` | 90–110 in, 5-in steps: the narrowest paper at least as wide as the used width plus the minimum trim |
| `quality_id` [`grade`] | `quality_id` | `QualityID` | the board grade key (`fixture.GRADES`: 26-C, 32-C, 55-C, 200-C, 275-BC, 275-EB) |
| `gross_sqft` [`gross`] | `total_produced_sqft` | `TotalSquareFootageProduced` | web × lineal / 12 |
| `trim_sqft` [`trim`], `trim_pct` | `trim_sqft`, `trim_pct` | `TrimSquareFootage`, `TrimPercentage` | trim inches × order travel / 12; trim inches / web × 100. The store adds `trim_in` |
| `shear_sqft` [`shear`] | `chop_sqft` | `ChopSquareFootage` | the shear chops: full-web, 34 in each, part of gross |
| `swing_knife_sqft` | `swing_knife_sqft` | `SwingKnifeSquareFootage` | optional: null when the machine has no swing knife (most machines; the demo corrugator has none), **never zero** |
| `change_code` | `change_code` | `ChangeCode` | a code with a lookup (`records.CHANGE_CODES`): `O` a regular order change, `S` a shift split (the setup continues `split_from` across a shift boundary); others are added as they appear |
| `stop_seconds`, `stops` | `stop_time_sec`, `number_of_stops` | `StopTimeForThisOrder`, `NumberOfStops` | the setup's summary of the downtime log: the sum and the count of its stop rows |
| `is_fanfold` | `is_fanfold` | `isFanfold` | the setup ran to a fanfold delivery (one lane; about 1% of orders, single wall only) |
| `wet_end_id` | `wet_end_id` | (ours) | the paper run the setup belongs to |
| `sheet_type`, `filler` | `sheet_type`, `filler` | (ours) | `product`, `bottom_sheet`, `cap_sheet` (product when any lane is); `filler` when a pallet-sheet lane was added to fill a tough trim |
| `lanes` | `len(lanes)` | (ours) | 1–3 |
| `rejects` | the dry-end families of `rejects` | (ours) | dry-end quality reject sq ft (bond, warp, misalignment, other) |
| `changeover_sqft`, `admin_sqft` | the changeover and admin lines of `rejects` | (ours) | changeover rejects (counted with shear) and planning / admin entries |
| `cutoff_waste_sqft`, `stacker_waste_sqft` | sums over `lanes` | (ours) | the lanes' cutoff-knife and stacker waste boards in sq ft |
| `split_from` | `split_from_setup_id` | (ours; carried by `ChangeCode` S) | the setup an S record continues |
| `target` | (`fixture.GRADES`) | (ours) | the grade's target speed, a configured table |

## Order lanes (`setup_orders`)

One row per lane; lane N of the setup record carries one customer's slice of the web. Outs live here, never on the customer.

| Ours | Record field | Typical MES field | Notes |
| --- | --- | --- | --- |
| `lane` | `lane` | the N of the lane's fields | 1–3 across the web |
| `order_number`, `part_number` | `order_number`, `part_number` | `OrderNumberN`, `PartNumberN` | `SO-YYMMDDnnnn`: the production day and the order's sequence in it |
| `customer_id` | `customer_id` | `CustomerNameN` | the customer dimension's key (the MES carries the name) |
| `order_quantity` | `order_quantity` | (ours) | the sheets the order asks for over all its setups; 0 for a filler lane |
| `outs` | `number_of_outs` | `NumberOfOutsN` | sheets across the web for this lane |
| `sheet_width_in`, `sheet_length_in` | `sheet_width_in`, `sheet_length_in` | `SheetWidthN`, `SheetLengthN` | widths in eighths of an inch; the length is the cutoff |
| `nominal_cuts`, `actual_cuts` | `nominal_cuts`, `actual_cuts` | `NominalCutsN`, `ActualCutsN` | scheduled and as run; actual within ±10% (the over/under-run window) |
| `cutoff_knife` | `cutoff_allocation` | `CutoffAllocationN` | the knife the lane is assigned to: lane 1 → cutoff knife 1 → stacker 1 |
| `cutoff_waste_boards`, `stacker_waste_boards` | the same | `CutoffKnifeWasteN`, `StackerWasteN` | the default waste-board counts the record carries: boards lost at the lane's cutoff knife and at its stacker at an order change (none on an S split) |
| `cutoff_waste_sqft`, `stacker_waste_sqft` | the same | (ours) | the boards × the lane's sheet size; their own waste lines, never dry-end quality |
| `sheet_type`, `filler` | the same | (ours) | pallet bottom and cap sheets are the plant's own (customer C000) |
| `stack_height` | `stack_height_sheets` | (ours) | sheets per stack at the stacker |
| `folds`, `sheets_per_pallet`, `pallets`, `perforation_length_in` | the same | `FoldsN`, `SheetsPerPalletN`, `PalletsN`, `PerforationLengthN` | fanfold delivery only; null otherwise |
| `width_share` | (derived) | (ours) | the lane's outs × sheet width / the setup's used width |
| `allocated_trim_sqft`, `allocated_shear_sqft` | (derived) | (ours) | the setup's side trim and shear chops × `width_share`: each customer's part of the setup's own waste (identity 6); a setup's lanes add up to its trim and shear |

## The identities every setup satisfies

The generator schedules orders into setups (owner, 2026-11-11), so every setup row satisfies these, and `tests/test_record_contract.py` and `tests/test_records_invariants.py` check each one over every record of August and September 2026 (the acceptance test of identity 1 over every setup of the golden window):

1. `used_width = Σ lanes (outs × sheet_width)`; `trim_in = web_width − used_width`, split evenly both sides; `trim_pct = trim_in / web_width`. The web is the narrowest paper at least as wide as the used width plus the day's minimum trim (the acceptance test: in the store's lane view, `Σ outs × sheet_width_in + the day's minimum trim ≤ width_in`, with `trim_in` exactly the rest and the web the narrowest that fits, for every setup from 1 January 2025 to 30 September 2026).
2. Per lane: `lineal_ft = cuts × sheet_length / 12`; `sheets = cuts × outs`.
3. Lanes on one setup end within **one knife rotation**: `|lineal₁ − lineal₂| < max(sheet_length) / 12`, as scheduled and as run.
4. The setup's order travel (lineal minus the shear chops) is the longest lane as run; `scheduled_lineal_ft` is the longest lane as scheduled.
5. An order is complete when its scheduled sheets (nominal cuts × outs) over its setups reach the demand; until then the remainder rolls into a later setup of its day (often on one knife at more outs, possibly on narrower paper). An order never runs again once complete. A setup closed at a shift boundary before its last cuts ran (under a minute of running left) gives those sheets back to its orders, so the only orders left short are the ones their production day closed on (nothing carries into the next day). Counted on actual cuts, an order lands anywhere in the over/under-run window around its demand.
6. Gross = web × lineal; trim sq ft = trim inches × order travel; a setup's own waste (side trim and shear chops) is allocated to its customers by each lane's share of the used width (`setup_orders.width_share`, `allocated_trim_sqft`, `allocated_shear_sqft`); reject lines and waste boards are already each lane's own.

The owner's worked example is a test, number for number, with the outs as the owner chose them: AC Box 15 × 35 in (2,000 sheets) beside Jerry's Packaging 17 × 47 in (3,000 sheets) on a 100-in web, 3 out each, uses 96 in and trims 4 in (2 in a side, 4.0%); AC Box sets the length at ⌈2,000 / 3⌉ = 667 cuts = 1,945.4 ft; Jerry's runs ⌊1,945.4 × 12 / 47⌋ = 496 cuts = 1,942.7 ft (1,488 sheets, 1,512 remain); the remainder at 5 out goes on 90-in paper, the narrowest that fits (5.6%; 10.5% on 95 in, 15% on 100 in), 303 cuts = 1,186.8 ft, 1,515 sheets.

The generator's scheduler applies the same arithmetic but chooses the outs too, so on these two orders it does better than the example: it puts AC Box at 2 out beside Jerry's at 4 out, which uses 98 in of the 100-in web at 2.0% trim, and both orders finish in one setup (1,007 and 750 cuts). For a lone 1,512-sheet remainder it picks 6 out on 105-in paper, at 2.9%. The worked-example test reproduces the example as the owner computed it and checks that the scheduler never does worse. That the scheduler may beat the example is a decision for the owner to confirm.

### How the generator schedules

`synth.py` draws each day's orders (customer, sheet width × length, quantity, quality ID). For each setup it takes the order to continue (a remainder) or the oldest open order of the running grade, and searches every outs of it alone and beside each of the grade's open orders at the outs that fill each paper width, scoring trim as a share of the web; leaving the running paper costs a penalty, so paper runs last about three setups, and a pallet-sheet filler lane is added only when it saves real trim. The lane that finishes first sets the length; a lane within 1,000 ft of finishing finishes too. Trim therefore comes out of the pairing: about 3.0% of gross before the seeded story. The demo machine has two cutoff knives, so the scheduler pairs at most two lanes; the contract allows three. Wet-end sequencing into funnels is issue #102.

## The downtime log (`stops`)

| Ours | Record field | Notes |
| --- | --- | --- |
| `stop_id` | `stop_id` | key |
| `setup_id` | `setup_id` | the run setup that holds the stop |
| `started`, `ended`, `seconds` | `start_*`, `end_*`, `duration_sec` | `seconds = ended − started` |
| `kind` | `kind` | the category: Maintenance, Operator, PM (the scheduled planned-maintenance window) |
| `place` | `place` | the reason group: Splicer 1–5, Stacker, Upper / Lower / Shear knife, Slitter/scorer, Double backer, Single facer, Glue dams, Steam system, Power |
| `reason` | `reason` | missed splice, knife / slitter jam, stacker, raw material, order change, …; PM stops carry Planned maintenance |
| `comment` | `comment` | the operator's free text, verbatim: about half are empty (`''`, never null), the rest plausible detail ("operator missed splice, not paying attention", "stacker boards fell over") |

For every setup, the sum of its stop rows' seconds equals `stop_seconds` and the row count `stops`. The category drives the downtime bands (issue #101). Comments are evidence to quote, never numbers: the answers show them as reported notes, labelled unverified.

## Dry-end rejects: category, reason, position

The taxonomy is data, `src/investigator/reject_reasons.json`, owner-approved on 2026-11-11. A reject line records a **category** and a **reason**; where on the board is an **attribute**, never a reason: `liner` (top, bottom, medium, DB, SF) and `side` (drive, operator, middle). "Bottom liner" is an identity, not a cause; the focus is what damages the board. The store carries the file as `reject_reasons`, and every line of `rejects` names one of its rows.

| Category | Family (tile) | Waste line | Controllable | Reasons (positions) |
| --- | --- | --- | --- | --- |
| Bond / delamination (temperature) | bond | dry-end | yes | Delamination (liner), Between flutes (DW), Blisters (liner), Blow out (side), Glue dam, Zipper board, Fuzzy edges (side), Loose / tight edges (side), Dry board, Low caliper |
| Warp (temperature) | warp | dry-end | yes | Up warp, Down warp, Twist warp, S warp, End-to-end warp |
| Cracking / surface | other | dry-end | yes | Cracking board (liner), Wrinkles (liner), Brake line |
| Misalignment / scoring / slitting | misalignment | dry-end | yes | Misalign (side), Web walking, Wrong scores, Light scores, Score cutting, Bad slit cut, Knife not cutting to spec (the lane's knife), Frozen slitters, Knife overheating, No change in slitters, Slitter off line, Moon outs, Wrong slat cuts, Wrong cut outs, Wrong dimensions / width |
| Handling damage | other | dry-end | yes | Bander crush, Bander damage, Forklift damage, Stacker jam (the lane's stacker), Stacker in discharge, Fanfold jam, Manual discharge |
| Paper / mill | other | dry-end | partly (receiving and roll prep) | Wrinkles in paper from mill, Marks in paper / dropped roll, Wrong paper, Wrong medium, Wrong DB/SF liner (liner), Wrong flute size, Liner inside out (liner), No coating, Wrong color |
| Changeover | changeover | counted with shear | yes (sequencing) | Order change, Automatic order change, Paper change, Early paper change, Waste from a paper or flute change, Cut off, Small butt |
| Print | other | dry-end | yes | Print wrinkle, Print / no print, Streaks in print |
| Planning / admin (not production waste) | admin | reported apart | no | Order fill, Over scheduled, Overshipment\*, Max shipped\*, Count adjustment\*, Inventory write-off\*, Mis-tag\*, Tag without a unit\*, Lost unit – shipping\*, Used as dunnage, Re-arrange outs, Cap sheets, Not enough trim, Out of trim, Operator, Equipment entered as a reason, NULL, Blank |

\* Bookkeeping the plant enters downstream of the corrugator; in the taxonomy, never generated on a dry-end record (`"generated": false`). The plant list's two entries that name a machine section or a system rather than a cause are recorded as *Equipment entered as a reason*.

**The four dry-end tiles.** The dry-end tile is exactly the sum of four families: bond, warp, misalignment and **other** (cracking / surface, handling damage, paper / mill, print). The v1 records' fourth family, moisture, is not a category of the approved taxonomy: moisture-driven defects are recorded as bond (dry board) or warp, so the `moisture_pct` tile became `other_pct`, and a question about moisture rejects is answered with the dry-end rejects. Dry-end waste as a share of the yield after trim and shear is issue #101's.

## The waste lines and the bale

A window's waste breakdown (`get_waste_breakdown`) lists every line the records hold, each as sq ft and % of gross:

| Line | What it is |
| --- | --- |
| `trim` | side trim |
| `shear` | the shear chops |
| `changeover` | changeover rejects, counted with shear and sequencing, never dry-end quality |
| `cutoff_waste`, `stacker_waste` | the lanes' cutoff-knife and stacker waste boards |
| `bond`, `warp`, `misalignment`, `other`, `dry_end` | dry-end quality: the four families and their total |
| `admin` | planning / admin entries, reported apart (not production waste) |
| `bale`, `unexplained` | the shift bale's scale reading, and the bale minus every line above (0.1–0.4% of gross) |

## Roll stock (`rolls`)

| Ours | Notes |
| --- | --- |
| `roll_id` | key: R + production date + sequence |
| `wet_end_id` | the paper run the roll fed |
| `stand`, `paper_grade` | the roll stand (top liner, medium, bottom liner; double wall adds a second medium and the single-facer liner) and the paper on it |
| `width_in` | the run's web width |
| `lineal_ft` | feet the roll fed into the run (a medium's include its flute take-up) |
| `peel_sqft` | outer wraps stripped at roll prep |
| `core_sqft`, `spliced_out` | paper left on the core when the roll was spliced out; 0 for the last roll on a stand, taken down at the paper change and returned to stock |

Peel and core are paper-side measures, before the board is made: they are not part of a shift's gross or of its bale reading. Their bands are the owner's to set (issue #101).

## The customer dimension (`customers`)

`customer_id`, `name`, `destination`, `ship_to`; no measures. Forty invented customers and `C000`, the plant itself, for pallet bottom and cap sheets. An order lane names its customer by `customer_id`.

## Wet-end runs (`wet_ends`) and shifts

Unchanged in spirit from v1: a paper run is consecutive setups on one grade and web width, its totals exact sums of its setups; a shift's totals are exact sums of its setups, now with the changeover, admin and cutoff and stacker waste lines beside the dry-end rejects.

## Not modelled

- **The swing knife's square footage**: the demo machine has none, so the field is null on every setup.
- **A third lane**: the contract allows it; the demo machine's two cutoff knives mean the scheduler never pairs three.
- **Downstream bookkeeping** reasons of the planning / admin category (marked above), which no dry-end record carries.
- **An orders ledger** beyond each lane's `order_quantity`: the daily order matrix still shows requested and remaining sheets as null.
- Holidays, timezone / DST, overlapping or shift-crossing stops, reject replacement, paper basis weights and recipes, and any vendor's field semantics beyond the definitions here.
