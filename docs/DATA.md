# Data

Every record the investigator reads is synthetic. One generator, one seed and one machine produce the production days the rail offers; three stories are planted in them for the tools to find. Nothing comes from a real plant, and nothing reaches the store at run time.

## What is in the store

- **One machine.** `CORR-1`, a 110-inch corrugator (`/corr/1/…` in an address).
- **From 1 January 2025 to yesterday.** Every Monday to Friday from 1 January 2025 (a fixed start, owner 2026-11-11, so a year-to-date has a full previous year under it) to the window's last calendar day (`as_of`): about 485 production days of three 480-minute shifts in November 2026. On the hosted demo `as_of` is the day before the image was built, so the window ends yesterday and its end moves forward every weekday night; tests and the golden set pin it to 30 September 2026 (`INVESTIGATOR_AS_OF`), 456 production days from 1 January 2025. A production day runs 07:00 to 07:00 and is labeled by its starting date; weekends are plant-down and emit no records, and there is no holiday calendar. `/api/health` reports the served window as `store.window` (`as_of`, `first_day`, `last_day`, `production_days`), and the page footer names it.
- **The record contract.** Dry-end run setups, the fact table, with their order lanes (up to three across the web) and reject lines; wet ends (paper runs) and the rolls that fed them; shifts; the downtime log; the customer dimension; and the reject taxonomy (category, reason, position). Their field names and units mirror how a corrugator's MES records a generic dry-end setup, never one vendor's table. [The record contract](RECORD_CONTRACT.md) maps every field and states the identities every setup satisfies; [the record families](RECORDS.md) defines every field.
- **Orders scheduled into setups, one day at a time.** `synth.generate_day(day, seed=2026)` draws a day's orders and schedules them into setups the way a corrugator scheduler does: two orders paired across the knives at the outs that fill the web, on the narrowest paper that fits, the first to finish setting the length and the remainder rolling into a next setup, so trim comes out of the pairing. Every draw comes from streams keyed by the seed and the date (the day itself, its production week's speed factor, its month's power outage), so a day is byte-identical in any window that holds it, and moving the window never changes a day it keeps. `generate_window(as_of)` concatenates the window; a content hash covers it. The hash's first twelve characters are the `dataset_hash` of `/api/health`; value goldens and recordings are stamped with it, so a generator change cannot slip past the golden set.
- **A derived table.** `shift_kpis` holds one row of summed quantities per shift, built with the store, so a month report is a lookup rather than a scan ([metrics](METRICS.md#the-shift_kpis-table)).
- **A corrupt record on request.** *Include a bad trim record* under *Agent and advanced* injects one invalid setup into shift 2 of the selected day, to show a record flagged, excluded and reported instead of averaged in.

## The invariants

The generator's output satisfies physics and accounting rules, each pinned by a test over every record of August and September 2026 (and the per-day determinism over windows that share days): the lanes' outs times sheet widths plus the trim make the web; gross area is lineal feet times web width; the lanes of a setup end within one knife rotation and the longest is the setup; an order runs until its sheets reach its demand; wet-end and shift totals are exact sums of their setups; setups tile each shift with no gap; stops sit inside one setup, never overlap, and add up to each setup's stop seconds and count. The monthly bands it keeps (downtime, trim, shear and dry-end waste) are simulation choices, listed in [the record families](RECORDS.md#generator-behaviour-and-the-bands-it-keeps), not plant standards.

## The three planted stories

The answer keys are in `src/investigator/stories.py`, which only the generator imports; no tool, planner, server or report can read them (a test pins that), so an investigation has to find them in the records.

1. **Trim creep after a rebuild.** A slitter/scorer rebuild in the Thursday 20 August maintenance window; afterwards the minimum trim the crews allow creeps up, so side trim of gross rises about 0.1 percentage point a week until 31 October 2026, then returns to its pre-story level; knife and slitter jam stops rise over the same weeks, concentrating on the slitter/scorer, and end with it.
2. **Missed splices on Splicer 3.** In the week of 7–13 September missed-splice minutes roughly triple, stop counts rise with them, and shear chops follow.
3. **Monday warp.** Warp rejects run about 1.5 times higher on the first production day after a weekend, and higher in August than in September.

The stories keep their 2026 dates in every window, so a window that starts after them simply holds none of them.

The investigation cases of the golden set ask about each story in a superintendent's words, and pass only when the answer finds the evidence and asserts no cause the records lack ([numbers and evals](EVALS.md)).

## When the data is built

The image generates the store once, at build time: `python -m investigator.prebuilt /app/prebuilt` builds 1 January 2025 to yesterday (about 27 s), and `--as-of 2026-09-30 /app/prebuilt-golden` builds the golden window the `/evals` page scores against. The server loads the first folder at start-up in about 400 ms instead of generating for about 25 s; the loopback workbench generates when the server starts (`INVESTIGATOR_AS_OF` pins its window's end, otherwise it ends yesterday). The data never changes while a server runs: there is no upload, no import and no job that adds days.

## The nightly window

A "daily load" is the window's end moving by one day, and nothing is loaded: the Cloud Run deploy also runs every weekday at 13:00 UTC (07:00 or 08:00 in Chicago, once the overnight shift has ended) and rebuilds the image, so the new day is generated from its date; the start stays on 1 January 2025 ([deployment](DEPLOYMENT.md), "The nightly deploy"). Its smoke test fails the deploy unless `/api/health` reports a window that ends yesterday, so an image that froze an older window cannot pass silently. A window reaching before the data's first day or after its last is unavailable with the tool's reason, never zero, and a month named without a year means the latest such month on or before the selected day. A year period (`period=year`) is not built yet (issue #106).
