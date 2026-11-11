# Synthetic record families

The records follow [the record contract](RECORD_CONTRACT.md) (`demo-records/v2`): a generic corrugator dry-end setup as the fact table, its order lanes, the customer dimension, the paper runs (wet ends) and the roll stock they consume, the downtime log, the reject lines and the shifts. Every field is defined there, with the typical MES field it stands for. This page covers how `src/investigator/synth.py` generates them and the bands it keeps. The schema is our own documented demo schema. It describes how corrugators in general record setups and claims no vendor's wire format or protocol.

`synth.py` generates any range of production days of one machine from a seed, one day at a time. Every random draw of a day comes from streams keyed by the seed and a date (issue #35), so a day is byte-identical in every range that contains it. The app's data source is a rolling year: every production day from 365 days before `as_of` to `as_of` (`INVESTIGATOR_AS_OF`; the image's build date minus one day; 2026-09-30 for the tests and the golden set). `src/investigator/record_store.py` builds that window once per process (or loads it prebuilt) into one in-memory DuckDB, derives the per-day tables the daily tools read (`RecordStore.day_analysis`, see [AGENT_EXECUTION](AGENT_EXECUTION.md)) and answers the period tools over the whole window ([METRICS](METRICS.md)). The one-day XML fixture (`fixture.generate()`) remains only for its own tests and keeps its own paper widths.

## Generating

```python
from datetime import date
from investigator.synth import customers, generate_day, generate_months, generate_window, write_jsonl, read_jsonl
data = generate_window(date(2026, 9, 30), seed=2026, machine_id="CORR-1")   # the year ending 2026-09-30
data = generate_months(seed=2026, months=(("2026", 8), ("2026", 9)))          # or any months
day = generate_day(date(2026, 9, 14))     # one production day: the same records as inside either range
customers(seed=2026)            # the customer dimension, the same in every range
data["content_hash"]            # sha256 over the six JSONL files of the range
write_jsonl(data, "artifacts/synthetic")   # one .jsonl per family and dimension + manifest.json
same = read_jsonl("artifacts/synthetic")   # dataclasses again; manifest hash must match
```

The same seed gives byte-identical JSONL, and a day gives the same records whatever range it is generated in. Two months generate in about a second and a quarter, the year in about eight. Generated files are ignored by git (`*.jsonl`); write them under `artifacts/`.

## Units and conventions

- Feet, inches, square feet, ft/min, whole seconds. Dates `YYYYMMDD`, times `HHMMSS`, durations `HHMMSS`, all in the fixed demo-local clock (no timezone, no DST).
- Production days are Monday to Friday. Weekends are plant-down and emit no records; there is no holiday calendar. A production day starts 07:00 and is labelled by its start date; shift 1 = 07:00, 2 = 15:00, 3 = 23:00, each 480 scheduled minutes.
- `lineal_ft` counts every foot of web through the machine: order sheets *and* full-web shear chops. Gross area is therefore exactly `lineal_ft x web_width_in / 12`. Order travel (what reached the cutoff knives) is `lineal_ft - shear_sqft x 12 / web_width_in`.
- Web geometry: `sum(outs x sheet_width_in) + trim_in = web_width_in`, trim split evenly (1-7 inches a side), web centered. Paper widths 90-110 in, 5-inch steps, on a 110-inch machine. Grades and target speeds are `fixture.GRADES`.
- Averages include downtime: a setup's `avg_fpm` is lineal over elapsed minutes with stops inside; a shift's is total lineal over 480 minutes. `max_fpm` on wet ends and shifts is the highest running speed (lineal over running minutes) of their setups.
- Rounding: feet, square feet and speeds carry one decimal, percentages two. Aggregates are sums of the rounded setup values, rounded once more.

## How a day is scheduled

The generator schedules orders into setups the way the owner's worked example does ([the setup math](RECORD_CONTRACT.md#the-setup-math)); it does not draw setups at random.

- **Orders.** Each production day draws its own orders when the running grade needs them: a customer (weighted, a few large ones), a sheet of 12-60 by 20-92 inches, a quantity, the grade. The quantity is the sheets that about 6,000-24,000 ft of travel makes at the order's typical outs (about half a web). Two percent are fanfold orders, three percent the plant's own bottom and cap sheets.
- **Paper runs.** A grade runs as a campaign of one to three paper runs (wet ends) in a row; each paper run has its width (weighted to about a 98-inch average) and two to four setups. Every fifth new grade starts with a one-order paper run on the narrowest paper that fits that order.
- **Pairing.** Each setup takes the order carried from the setup before (else the oldest open order) on knife 1 and pairs it with one of the 24 open orders of the grade on knife 2. It picks the outs of both lanes that use the most width while leaving the crews' trim allowance (the day's target share of the web, less 0.4 in, at least 2 in). When nothing pairs within 4 in of the allowance, a lane of plant-use bottom sheets fills the trim (`filler`). The lane that needs less travel sets the length; the other runs as far, and its remainder carries into the next setup (change code `C`), on the next paper width if the paper run ends. No order is left with less than 1,000 ft to run, and no setup runs less than 1,000 ft.
- **The last remainder of a campaign** runs alone: on the current paper when its trim is within 2 in of the allowance, else on the narrowest paper that fits it (a new one-setup paper run) or beside a filler lane when even that trims more than 6%.
- **Actual against plan.** A setup's actual travel differs from its plan by a shared over/under-run (85% within 2%, the rest up to 7.5%), so every lane's actual cuts move together and stay within ±10% of nominal.
- **Waste boards.** Each lane carries default waste-board counts at its cutoff knife (2-6 on an order change, 1-4 on a continuation, 0-1 on a shift split) and at its stacker (1-5, 0-3, 0-1): about 0.2% of gross, their own waste lines.
- **Rolls.** Each paper run feeds three webs (single wall) or five (double wall), a medium with its flute take-up (B 1.36, C 1.43, E 1.24). Rolls of 14,000-30,000 ft (liners) or 20,000-42,000 ft (mediums) are spliced as they run out. Peel is 0.15-0.45% of each roll; a spliced roll leaves 0.12-0.36% on its core. A paper change starts fresh rolls of the new width, and the roll on the stand at the end is pulled with its butt returned.
- **Comments.** About 40% of the downtime log is left without a comment; the rest carry one of a few plausible notes per reason (`Operator missed splice at Splicer 3, not paying attention (synthetic note, unverified)`).

## Generator behaviour and the bands it keeps

These are simulation choices (`synth.py` constants), not plant standards. `tests/test_records_invariants.py` checks each line for every record of August and September 2026.

- **Seeding by date** (issue #35): a production day draws from `Random(f"{seed}:{day}")`; its week's speed factor from `Random(f"{seed}:week:{monday}")`; its month's power outage from `Random(f"{seed}:outage:{yyyy-mm}")`, chosen among every shift of the whole month; the customer dimension from `Random(f"{seed}:customers")`; crews rotate weekly counted from Monday 2026-08-03. Nothing carries from one production day into the next (an order still open at 07:00 is not continued), so a day regenerates byte-identically in any range, and moving the window by a day changes no day it keeps (`tests/test_synth_determinism.py`).
- **Time**: setups tile each shift with no gaps; a setup that would cross the boundary between shifts 1 and 2 or 2 and 3 is split there (the first part keeps its code, the second is `S`, shares the lanes and the plan's cuts by the travel each part made). A remainder with under 60 s of running time is dropped at the boundary and the lane that was carrying on gets those cuts back; a first part under 60 s stretches the prior record instead. At the end of shift 3 (07:00 the next morning) the open run ends and the next production day starts fresh, as after a weekend, so no record or paper run crosses a production day.
- **Output**: about 2.4 million gross sq ft per shift (6-8.5 million per production day) at a 98-inch average web on a 110-inch machine; about 6,900 lineal ft per dry-end setup (roughly 110-145 setups a day) and about 19,000 lineal ft per paper run (roughly 45 paper changes a day); week-to-week volume moves a few percent through a per-week speed draw. Running speed is the grade target × crew factor (0.91-0.97) × shift factor × noise; displayed averages include stops.
- **Downtime**: Maintenance + PM between 2% and 3% of scheduled minutes per month. PM is one 120-135 minute window every Thursday on shift 1 (the rebuild week runs 180 min), recorded with kind `PM` and reason `Planned maintenance`. On top come the unplanned maintenance stops (Monday ×1.40, August ×1.08; operator stops Monday ×1.15) and one 10-15 minute power outage per month. Operator stops are separately 1-3%.
- **Waste per month**: side trim 2.5-3.5% of gross, shear chops 0.4-1.0%, waste boards 0.1-0.4%, dry-end rejects about 1.0-1.2% split bond 0.40 / warp 0.30-0.47 / misalignment 0.20 / cracking 0.10 points. Inside warp: up 40%, down 25%, twist 18%, S 10%, end to end 7%. Inside bond, delamination leads (40%) ahead of blisters, blow outs and glue dams; between flutes only on double wall. Rejected sheets are discarded; they are never replaced in the records. Each missed splice adds a run of 8-14 chops (a web break).
- **Determinism**: the content hash of August and September 2026 is pinned in `tests/test_synth_determinism.py`, and that of the golden year in `evals/golden_values.json`; change them only with an intended generator change.

## Seeded stories

The answer keys live in `src/investigator/stories.py` and are read only by the generator; no tool, analysis, agent, server or report module imports them (pinned by test). The records must reveal them on their own:

1. A slitter/scorer rebuild in the Thursday 20 August PM window. Afterwards the crews' trim allowance creeps about 0.1 percentage point per week through 31 October 2026, and knife / slitter jam stops run three times their rate, concentrating on the slitter/scorer. From 1 November both return to their pre-story level.
2. A missed-splice problem on Splicer 3 in the week of 7-13 September: missed-splice minutes roughly triple, stop counts rise, shear chops follow.
3. Warp rejects are higher on the first production day after a weekend (Monday ×1.5) and in August (0.42% of gross) than in September (0.30%) or any other month.

The stories keep their 2026 dates in every window. Side trim of gross runs about 2.8% before the rebuild, 3.2% in September and 3.7% late in October, and is back at about 2.8% from November (`trim_target_pct` in `synth.py`, pinned by `test_trim_creep_ends_on_31_october_and_trim_returns_to_its_pre_story_level`). The knife / slitter jam effect ends on the same day (issue #100, carried over from #91's review; `knife_story_on`, pinned by `test_knife_slitter_jam_effect_ends_with_the_trim_creep_on_31_october`).

## Not modelled

Holidays, timezone / DST, overlapping or shift-crossing stops, reject replacement, accepted-shipment accounting, paper recipes and weights, wet-end width funnels (#102), and the rest of [the contract's list](RECORD_CONTRACT.md#not-modeled).
