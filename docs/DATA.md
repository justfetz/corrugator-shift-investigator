# Data

Every record the investigator reads is synthetic. One generator, one seed and one machine produce the production days the rail offers; three stories are planted in them for the tools to find. Nothing comes from a real plant, and nothing reaches the store at run time.

## What is in the store

- **One machine.** `CORR-1`, a 110-inch corrugator (`/corr/1/…` in an address).
- **Two months.** Every Monday to Friday of August and September 2026: 43 production days of three 480-minute shifts. A production day runs 07:00 to 07:00 and is labeled by its starting date; weekends are plant-down and emit no records, and there is no holiday calendar.
- **Four record families.** Dry-end setups (with their customer blocks and reject lines), wet ends, shifts and stops, whose field names and units mirror a corrugator controller's own record types. [The record families](RECORDS.md) defines every field.
- **One seed.** `synth.generate_months(seed=2026)` gives byte-identical records for the same seed, and a content hash over them. The hash's first twelve characters are the `dataset_hash` of `/api/health`; value goldens and recordings are stamped with it, so a generator change cannot slip past the golden set.
- **A derived table.** `shift_kpis` holds one row of summed quantities per shift, built with the store, so a month report is a lookup rather than a scan ([metrics](METRICS.md#the-shift_kpis-table)).
- **A corrupt record on request.** *Include a bad trim record* under *Agent and advanced* injects one invalid setup into shift 2 of the selected day, to show a record flagged, excluded and reported instead of averaged in.

## The invariants

The generator's output satisfies physics and accounting rules, each pinned by a test over every record of both months: gross area is lineal feet times web width; both knives share the machine's travel; wet-end and shift totals are exact sums of their setups; setups tile each shift with no gap; stops sit inside one setup and never overlap. The monthly bands it keeps (downtime, trim, shear and dry-end waste) are simulation choices, listed in [the record families](RECORDS.md#generator-behaviour-and-the-bands-it-keeps), not plant standards.

## The three planted stories

The answer keys are in `src/investigator/stories.py`, which only the generator imports; no tool, planner, server or report can read them (a test pins that), so an investigation has to find them in the records.

1. **Trim creep after a rebuild.** A slitter/scorer rebuild in the Thursday 20 August maintenance window; afterwards side trim creeps about 0.1 percentage point a week and knife and slitter jam stops rise, concentrating on the slitter/scorer.
2. **Missed splices on Splicer 3.** In the week of 7–13 September missed-splice minutes roughly triple, stop counts rise with them, and shear chops follow.
3. **Monday warp.** Warp rejects run about 1.5 times higher on the first production day after a weekend, and higher in August than in September.

The investigation cases of the golden set ask about each story in a superintendent's words, and pass only when the answer finds the evidence and asserts no cause the records lack ([numbers and evals](EVALS.md)).

## When the data is built

The image generates the store once, at build time (`python -m investigator.prebuilt /app/prebuilt`), and the server loads that folder at start-up in a fraction of a second instead of generating for a few seconds. The loopback workbench generates it when the server starts. The data never changes while a server runs: there is no upload, no import and no job that adds days.

## Not yet: a rolling year

Today's window is fixed, so July and earlier are unavailable (never zero) and there is no year-to-date. Issue #35, in review as pull request #91, replaces the two months with every weekday of the year ending yesterday, seeds each day on its own so a day never changes when the window moves, and rebuilds the image every weekday night so the window moves forward a day. Until it merges, everything on this site describes the two months above.
