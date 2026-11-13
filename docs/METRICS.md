# Metrics, targets, and calendar interpretation

This is the calculation reference for the current synthetic workbench. The data source is the record store (`record_store.py`): a rolling year of production days, Monday to Friday from 365 days before `as_of` to `as_of` (the year ending 2026-09-30 for the tests and the golden set; the demo's ends the day before its image was built), each day generated from seed 2026 and its own date (`synth.py`, [RECORDS](RECORDS.md)). The daily tools live in `analysis.py`, the period tools in `record_store.py`, the period reports in `periods.py`, and both of the last read the precomputed per-shift sums of the `shift_kpis` table ([below](#the-shift_kpis-table)); `fixture.py` is the legacy one-day XML generator kept for its own tests. These definitions describe what the code measures; they do not establish causal explanations or achievable savings.

## Quantities and denominators

For each valid setup, let F be gross machine lineal feet, G gross square feet, T trim square feet, S shear square feet, R dry-end reject square feet, m elapsed minutes, and v the grade target in ft/min. Sum quantities before calculating percentages.

| Display | Formula | Interpretation |
| --- | --- | --- |
| Speed | sum(F) / 480 per shift | Includes stops and all other time in the fixed eight-hour shift |
| Wet-end speed | sum(F) / sum(m) within a wet-end ID | Includes downtime; not running-only speed |
| Speed / target | 100 × sum(F) / sum(v × m) | Grade-duration weighted; do not average run percentages |
| Gross output | sum(G) | F × web width / 12, before waste deductions |
| Estimated good output | sum(G − T − S − R) | Accounting estimate under the demo's waste convention, not accepted/sold product |
| Maintenance / Operator | 100 × minutes of that kind / 480 | Separate measures, each with its own target |
| Total downtime | 100 × all stop minutes / 480 | Validated non-overlapping stops only |
| Trim / shear waste | 100 × sum(category area) / sum(G) | Area-weighted percentages of gross |
| Dry-end waste | 100 × sum(R) / sum(G − T − S) | The owner's denominator (issue #101): the yield after trim and shear, the board that reached the dry end |
| Throughput | 12 × sum(G) / sum(F) | Average gross web width in inches; this label does not mean output per hour |
| Dry-end setups | Count of valid setup records | Invalid setups are excluded |
| Paper changes | Count of distinct wet-end IDs per day and shift | Sum those counts across periods; IDs repeat between days |
| Lineal / setup | sum(F) / valid setup count | Gross machine footage per setup |
| Lineal / wet-end | sum(F) / distinct wet-end count | Available in KPI evidence; not a dedicated UI card |

Example: 10 minutes at a 900 target and 10 minutes at a 500 target have a combined target of 14,000 feet. If those runs produce 10,500 feet, attainment is 75%. It is not obtained by taking an unweighted average of their individual percentages.

Period speed is total feet / (480 × number of observed shifts). Period downtime percentages average shift percentages because every supported shift has exactly the same 480-minute denominator. Period waste is weighted by gross area. The period report reads each shift's row of the daily matrix from `shift_kpis` (one lookup per window, no per-day rebuild) and reconstructs target footage from each footage/attainment pair; the raw target footage is the table's `target_ft`. Zero or wholly invalid shifts would require additional handling before supporting external data.

In the daily tools `maintenance_pct` is unplanned Maintenance only; the planned window is reported separately as `pm_pct` (stop kind `PM`), and `downtime_pct` is every stop. A power outage is **External** (the owner, PR #111; `targets.json`, `external`): the records log it as a Maintenance stop, and every table the tools read (the stops table, `shift_kpis`, each day's analysis) counts it under the kind `External` instead, so it is in `downtime_pct` and in every downtime list, but never in `maintenance_pct`, `op_maint_pct`, a band or a flag. The raw records and the dataset hash are unchanged. Dry-end rejects in the record families are per knife position and reason (`BOND`, `WARP`, `MISALIGNMENT`); the quality tools read per-reason areas from the `rejects` table, so a setup with three reasons contributes to three rows (the fixture wrote one reason per setup). The record families carry no order quantity ledger: in the order matrix `requested_sheets`, `remaining_before`, `remaining_after` and `planned_overrun_sheets` are null, `planned_sheets` is this record's `nominal_cuts × outs`, `produced_sheets` is `actual_cuts × outs`, and the knife column is `knife 1/2/3` from `cutoff_allocation` (the fixture's upper/lower only covers two positions).

## Period tools (record store)

The six period tools (`get_period_kpis`, `compare_periods`, `get_downtime_pareto`, `get_waste_breakdown`, `get_stops_profile`, `get_trend`) read the whole store; the tiles (`get_period_kpis`, `compare_periods`, `get_waste_breakdown`, `get_trend`) sum `shift_kpis` rows, and the stop-level and setup-level tools (the Pareto, the stops profile, grade totals, rollstock, overrun, speed opportunity, the reject sub-reasons) stay on the raw tables. Every percentage is a ratio of summed numerators and denominators over the window, never an average of per-day or per-shift percentages: September side trim is sum(trim sq ft) / sum(gross sq ft) over the month's 4,115 setup records, not the mean of 22 daily percentages (the test suite pins that the two differ). Deltas are the difference of the two displayed tiles, so a tile reconciles on screen. An optional `shift` filter restricts every sum to one shift; under the Selected shift only scope the selected shift is enforced.

| Window | Current | Previous |
| --- | --- | --- |
| `day` | the anchor's production day (07:00 to 07:00); a weekend anchor is refused | the previous production day (Friday before a Monday) |
| `week` | the Monday–Friday production week containing the anchor (a weekend anchor maps to the week it closes) | the preceding Monday–Friday week |
| `month` | the calendar month containing the anchor | the preceding calendar month |

A window is `day`, `week` (Mon–Fri), `month` (calendar) or `fiscal`: the 4-4-5 accounting period containing the anchor, 52-week years of four 4, 4, 5-week quarters from Monday 3 August 2026 (P1 = 3–30 Aug, P2 = 31 Aug–27 Sep, P3 = 28 Sep–1 Nov, which holds three days in the year ending 30 September 2026; the prior year's periods start 52 weeks earlier; the rail's "4-4-5" window uses the same calendar). Every KPI envelope states `production_days_current` / `production_days_previous` and `equal_production_days`; when the counts differ (a 21-day August against a 22-day September, a 5-week period against a 4-week one) the `day_basis_note` says to compare `msf_per_day` and the ratio tiles rather than raw totals. A window must overlap the dataset (the rolling year; 30 September 2025 – 30 September 2026 for the tests and the golden set). Coverage is `complete` when every weekday of the window has records, `partial` when some do (in the year ending 30 September 2026: the week of 28 September has three days; the year's first week, 29 September – 3 October 2025, has four days and an `unavailable` previous week), `unavailable` when none do. Deltas and the `change` label are withheld unless both windows are complete; weekends and days outside the window are never zero.

| Tile | Formula over the window | `better` |
| --- | --- | --- |
| volume_msf | sum(gross sq ft) / 1000 | higher |
| lineal_ft | sum(lineal ft) | higher |
| msf_per_day | sum(gross sq ft) / 1000 / count of production days with records — the like-for-like volume when windows hold different numbers of days | higher |
| speed_run_fpm (without stops) | sum(lineal ft) / sum(elapsed − stop minutes of the setup records) | higher |
| speed_all_fpm (with stops) | sum(lineal ft) / sum(scheduled shift minutes) | higher |
| downtime_pct, downtime_minutes | sum(stop seconds) / sum(scheduled seconds) × 100; sum(stop seconds) / 60 | lower |
| stops, minutes_per_stop | count of stop records; downtime minutes / stops | lower |
| trim_pct, shear_pct | sum(category sq ft) / sum(gross sq ft) × 100 | lower |
| dry_end_pct | sum(reject sq ft) / sum(gross − trim − shear sq ft) × 100, of the yield after trim and shear (issue #101) | lower |
| bond_pct, warp_pct, misalignment_pct, moisture_pct | sum(reject lines of that family) / sum(gross − trim − shear) × 100; the four add to dry_end_pct.  `get_waste_breakdown` keeps every category's `pct_of_gross` (the ladder that adds up to the bale) and gives the dry-end families and their total a `pct_of_yield` beside it. `get_waste_breakdown` and the daily `get_quality_breakdown` also carry `details`: the reported sub-reasons (up / down / twist / S-warp, delamination / paper) with area and share of dry-end | lower |
| bale_pct | sum(shift `bale_sqft`) / sum(gross) × 100 — a scale reading of baled waste, not an accounting sum | lower |
| unexplained_pct | (sum(bale) − sum(trim + shear + rejects)) / sum(gross) × 100 — what the scale weighed beyond the accounted categories (0.1–0.4 % in this dataset) | lower |
| maintenance_pm_pct | sum(Maintenance + PM stop seconds) / scheduled × 100 (the owner's dashboard combines planned and unplanned) | lower |
| operator_pct | sum(Operator stop seconds) / scheduled × 100 | lower |
| setups | count of setup records (a shift-split setup is two records, as in the daily tools) | neutral |
| paper_changes | count of distinct wet-end IDs in the window (a run crossing a shift boundary counts once; the daily tool counts it in each shift it touches) | neutral |
| lineal_per_setup | sum(lineal ft) / count of setup records — average dry-end run length | higher |
| lineal_per_paper_change | sum(lineal ft) / count of distinct wet-end IDs — average paper-run length | higher |
| bale_msf | sum(shift `bale_sqft`) / 1000 — the baled-waste scale reading as area, beside bale_pct | lower |

`better` is the direction the UI should color as good: `lower`, `higher`, or `neutral` for counts that are context rather than a score. `change` is `better`, `worse`, `same`, `neutral` or null when withheld.

Beside the tiles, `get_period_kpis` counts **order changes** (issue #113): the setup records whose change code is O, the knife order combination changed, out of all the setup records (shift splits S and continuations C are the rest). It is a count beside the tiles, not a tile: `order_changes`, `previous_order_changes` and `order_changes_delta` on the envelope, null when the window holds no shift records and the delta only when both windows are complete. "How many order changes this week" opens with it (`measure=quantity&metric=order_changes`).

`get_downtime_pareto` groups the window's stops by reason, place or kind, orders by minutes or count, and gives each row its share and cumulative share of the window total plus the previous window's stops and minutes (0 when the category did not recur, null when the previous window is unavailable). The reason taxonomy is the record store's: nine unplanned reasons (Knife / slitter jam, Missed splice, Raw material (roll), Stacker, Order change, Glue / starch, Steam / moisture, Power outage, Other) plus `Planned maintenance`, the reason every `PM`-kind stop carries, so a week holding a planned window shows it as its own row instead of under Other. `get_waste_breakdown` is the waste half of the tile table as square feet and percent of gross, previous window alongside. `get_stops_profile` gives stops, minutes, minutes per stop and downtime percent for each production day plus the Monday-to-Friday profile (stops per day, minutes per day) so a first-day-after-the-weekend effect is visible. `get_trend` is the weekly series (Mon–Fri weeks, each a ratio of sums) of one metric from the allow-list `trim_pct, shear_pct, dry_end_pct, warp_pct, bond_pct, misalignment_pct, speed_run_fpm, speed_all_fpm, downtime_pct, stops, minutes_per_stop, msf_per_day, lineal_per_setup, lineal_per_paper_change`, 1–26 weeks ending with the anchor's week; weeks before the dataset are listed as missing, a short week is flagged partial.

`get_grade_totals` slices one window by board grade: setups, distinct wet ends, lineal feet, volume MSF and its share of the window, speed without stops (feet / (elapsed − setup-allocated stop seconds)) and elapsed speed (feet / elapsed seconds) from the setup records' own clocks, percent of the grade target, setup-allocated stops and minutes with their percent of the grade's elapsed time, lineal per setup and per paper change, trim and dry-end percent, and the previous window's volume with its delta. Scheduled shift minutes carry no grade, so there is no per-grade "speed with stops"; the envelope says so. `get_overrun` sums nominal and actual cuts per knife position over the window's customer lines: delta cuts, percent of nominal (positive = overrun), nominal and actual sheets (cuts × outs), sheets over and under, and the share of lines inside, over and under a ±2 % band (`band_pct`, a demo setting — the plant's acceptance range is an open owner input; the generator never leaves ±10 %, `window_pct`). The last row is all knives. The UI draws it as a bullet chart: zero at mid-axis, the band shaded, one bar per knife.

`get_rollstock` is paper consumed, read from the wet-end records (one per paper run) rather than the setups: per board grade and web width, paper runs, lineal feet, MSF and the share of the window's feet, feet per run, and the previous window's runs, feet and MSF with deltas; the envelope carries the window totals. MSF here is the paper footprint, lineal feet × web width / 12 / 1,000, the same area the board records call gross, so the window total agrees with the volume tile except for runs that straddle the window's edge (a run counts whole on the day it started here, while the tile splits it by setup date: about 60 MSF of September's 160,000); the records carry no basis weight, so tons and cost are not computed and the envelope says so. Under all shifts a run counts whole on the production day its first setup started (the wet-end record's date). Under one shift the wet-end table has no shift, so the tool takes the setups that shift ran on each run: the feet are that shift's setup feet, the run count is distinct wet ends, and a run that spans two shifts is counted in both; the `attribution_note` states which rule applied.

`get_speed_opportunity` answers "where could we run faster" the way a superintendent would check it: per board grade and web width (or crew), over every setup of the window with at least two running minutes (shorter setups never reach steady speed; they are counted in `excluded_short_setups`, never dropped silently): typical speed without stops as a ratio of sums, the median setup speed, the **best fifth** (the fastest fifth of the cell's setups by speed without stops, `ntile(5)`, again a ratio of sums, never the peak), both as percent of the grade target, the gap, and **opportunity hours** = running minutes now minus the minutes the same lineal feet would take at the best-fifth speed. Beside it sits the dry-end reject percent of the fast fifth against the rest: a positive `reject_penalty_points` means the fast setups rejected more, so that part of the gap is a limit rather than an opportunity. A cell with fewer than ten setups reports `insufficient`. The records carry no temperatures, so the heat-transfer ceiling on heavy grades and doublewall (bond needs its temperature range) is stated as an open owner input in every envelope.

### Speed variance (issue #92)

`get_speed_variance(window, shift, against, against_shift)` answers "what were the top three contributors to speed variation": why the window's speed with stops differed from a reference, as buckets that add up to the gap: stops, setups, running under baseline and the grade mix, plus planned maintenance and external stops when either side holds one (the owner, PR #111). Every grade is held at its **baseline** (the owner's speed standard, issue #101, below): its sustained average speed over the trailing year ending on the window's last day, never its target or its maximum (`baseline_since`, `baseline_through` on the envelope; a grade with no sustained run in that year would fall back to its target and be listed in `baseline_fallback`; every grade the records hold has one). Speed is the tile's speed with stops, lineal ft / scheduled minutes. For each side, per scheduled minute (T):

| Quantity | Formula |
| --- | --- |
| baseline speed | Σ(grade baseline × setup clock minutes) / T: the speed the grade baselines give at the side's own grade mix (the setup records fill every scheduled minute, so this is the time-weighted grade baseline) |
| stops | Σ(stopped minutes × the stopped setup's grade baseline) / T, every unplanned maintenance and operator reason except Order change |
| planned maintenance | the same for the PM stops (kind `PM`), its own bucket |
| external stops | the same for the External stops (a power outage), its own bucket |
| setups | the same for the Order change stops (the changeover the cost model also prices as setup time); the records carry one running speed per setup, so there is no separate ramp after a changeover |
| running under baseline | Σ(grade baseline × running minutes − lineal ft) / T, per grade (negative when a grade ran above its baseline) |
| identity | baseline speed − stops − planned maintenance − external stops − setups − running under baseline = speed with stops, exactly, because each setup's clock is its running time plus its allocated stops (it holds for any per-grade speed) |

The gap is reference speed − this speed. **Grade mix** = the reference's baseline speed − this window's; each loss bucket = this side's loss − the reference's. Both sides are held at the same baselines. In ft/min and as lineal ft over this window's scheduled minutes; `share_of_gap_pct` is each bucket over the gap (shares stay null when the gap is under 0.05 ft/min, and a bucket that ran the other way is an offset with a negative share). `details` splits stops by reason, setups and running under baseline by grade (`baseline_fpm`, `speed_to_baseline_pct`), and the mix by grade as (reference time share − this time share) × (grade baseline − reference baseline speed); each bucket's details add up to it, and the row names the detail that leads it (`top_item`).

- `against=baseline` (the default; `against=target`, its name before the owner's baseline, is read as it): the reference is each grade's baseline weighted by its share of the setup clock over the same trailing year (all shifts), with no losses. A window that ran more of a slow grade than usual is below it even if every grade ran at its baseline: that part of the gap is the mix.
- `against=previous`: the window of the same kind just before, same shift filter; refused when it is outside the store (August against July).
- `against=shift`: `against_shift` over the same window; `shift` is the one explained. Under the selected-shift scope another shift is out of reach and the call is refused.

Every bucket is marked (`control`): the grade mix is **schedule-driven** (the order book decides which grades run), planned maintenance is **schedule-driven** too (the maintenance plan sets its stops), external stops are **external** (outside the plant), and the unplanned stops, setups and running under baseline are **controllable**. Each controllable bucket is priced as machine time: its lineal ft / the reference speed = minutes at that speed, at the machine $/hr (annualized by time, contribution beside it when a margin is set). The grade mix, planned maintenance and external stops are never priced, and a bucket that ran better than the reference is a gain and is not priced, whichever way the gap goes (when the window is faster than its reference, its controllable buckets that ran worse are the offsets on the card, and they are priced). The envelope's `controllable_usd` (and `_per_year`, `_machine_minutes`, `_contribution_usd`) sums the priced buckets; the gap as a whole holds the mix, so it is not priced. The answer leads with the gap and the buckets' shares, then the three largest buckets (the ones that make the gap first, then the offsets), plus any offset larger than the third bucket when that third adds to the gap (owner, PR #91: in the week of 14 September shift 1's planned maintenance pulls back 39.5 ft/min, -94%, and gets a line beside the unplanned stops' 4.7 ft/min), each marked controllable, schedule-driven or external, a waterfall from the baseline speed through each bucket to the speed with stops, and where to look.

## The shift_kpis table

The authoritative KPI inventory. `shift_kpis` holds one row per production day, shift and variant (`corrupt` false: the records as generated; true: the data-quality demo's bad-trim record injected into the day's first shift-2 setup), 1,572 rows for the year ending 2026-09-30 (262 days). `build_shift_kpis` in `record_store.py` computes it once from the six tables: at image build into the prebuilt folder (`python -m investigator.prebuilt`, issue #19), where the store loads it with the rest, or at boot when the store generates. Validation runs once there with the per-day Analysis's rules; a setup that fails is left out of every setup column and counted in `excluded_setups`, and its shift's `coverage` is `incomplete`.

Every measure is a raw numerator or denominator, never a percentage, so any window's tile is a ratio of the sums of these columns, never an average of percentages. The third column names the formula rows each column feeds: tile keys of the period tools' table above, and fields of the period report's daily matrix (which carries the daily KPI tool's fields). `tests/test_shift_kpis.py` reads this table and the database's own column list and fails when they differ.

| Column | Type | Formula row |
| --- | --- | --- |
| `production_date` | `DATE` | key: the production day (07:00 to 07:00) |
| `shift` | `INTEGER` | key: shift 1, 2 or 3; the tools' shift filter |
| `corrupt` | `BOOLEAN` | key: false for the records as generated, true for the data-quality demo |
| `crew` | `VARCHAR` | key: the shift record's crew label, the shift's note in the report |
| `coverage` | `VARCHAR` | key: complete, or incomplete when a setup of the shift failed validation |
| `excluded_setups` | `INTEGER` | key: setups that failed validation, left out of every setup column |
| `setups` | `INTEGER` | Dry-end setups: `setups`, `valid_setups`, `lineal_per_setup` |
| `paper_changes` | `INTEGER` | Paper changes, distinct wet-end IDs in the shift: the daily `paper_changes`, `lineal_per_wet_end` |
| `wet_end_ids` | `VARCHAR[]` | Paper changes over a window, distinct IDs across the window's rows, so a run that crosses a shift boundary counts once: `paper_changes`, `lineal_per_paper_change` |
| `lineal_ft` | `DOUBLE` | sum(F): `lineal_ft`, `observed_lineal_ft`, `observed_shift_fpm`, `speed_run_fpm`, `speed_all_fpm`, `lineal_per_setup`, `lineal_per_paper_change`, `lineal_per_wet_end`, `throughput_in` |
| `target_ft` | `DOUBLE` | sum(v × m), the grade-weighted target footage: `speed_to_target_pct` |
| `elapsed_seconds` | `DOUBLE` | sum(m) of the setup records: `speed_run_fpm` |
| `setup_stop_seconds` | `DOUBLE` | Stop seconds inside the setup records, subtracted from elapsed: `speed_run_fpm` |
| `gross_sqft` | `DOUBLE` | sum(G): `volume_msf`, `msf_per_day`, `gross_sqft`, `estimated_good_sqft`, `throughput_in`, the denominator of every % of gross tile |
| `trim_sqft` | `DOUBLE` | sum(T): `trim_pct`, `estimated_good_sqft`, `unexplained_pct` |
| `shear_sqft` | `DOUBLE` | sum(S): `shear_pct`, `estimated_good_sqft`, `unexplained_pct` |
| `reject_sqft` | `DOUBLE` | sum(R): `dry_end_pct`, `estimated_good_sqft`, `unexplained_pct` |
| `bond_sqft` | `DOUBLE` | Reject lines of the BOND family: `bond_pct` |
| `warp_sqft` | `DOUBLE` | Reject lines of the WARP family: `warp_pct` |
| `misalignment_sqft` | `DOUBLE` | Reject lines of the MISALIGNMENT family: `misalignment_pct` |
| `moisture_sqft` | `DOUBLE` | Reject lines of the MOISTURE family: `moisture_pct` |
| `setups_above_trim_target` | `INTEGER` | Setups whose own trim is above 3.25 % of their gross: `setups_above_trim_target` |
| `grade_gross_sqft` | `MAP(VARCHAR, DOUBLE)` | Grade mix: sum(G) per board grade (a window sums the maps; the values add to `gross_sqft`) |
| `scheduled_seconds` | `DOUBLE` | Scheduled shift time: `speed_all_fpm`, `downtime_pct`, `maintenance_pm_pct`, `operator_pct` |
| `bale_sqft` | `DOUBLE` | The shift's bale scale reading: `bale_pct`, `bale_msf`, `unexplained_pct` |
| `stops` | `INTEGER` | Count of stop records: `stops`, `minutes_per_stop` |
| `stop_seconds` | `DOUBLE` | All stop seconds: `downtime_pct`, `downtime_minutes`, `minutes_per_stop` |
| `maintenance_seconds` | `DOUBLE` | Unplanned Maintenance stop seconds: `maintenance_pct`, `maintenance_pm_pct` |
| `pm_seconds` | `DOUBLE` | Planned (PM) stop seconds: `pm_pct`, `maintenance_pm_pct` |
| `operator_seconds` | `DOUBLE` | Operator stop seconds: `operator_pct` |
| `source_ids` | `VARCHAR[]` | The setup records summed, the evidence of every daily matrix row: `source_ids` |

The daily matrix keeps the daily KPI tool's fixed 480-minute shift for `observed_shift_fpm` and 28,800 seconds for its downtime percentages; the period tiles use `scheduled_seconds`. The stop-level and setup-level tools, and the report's recurring-downtime table and notes, read the raw tables.

## Cost model

The rates, their sources and the formula behind every dollar field moved to [the cost model](COST_MODEL.md) (issue #93).

## Configured targets

The following fictional grade settings match `fixture.GRADES`. They are supplied simulation targets, not universal corrugator standards.

| Grade | Target ft/min |
| --- | ---: |
| 26-C | 900 |
| 32-C | 900 |
| 55-C | 850 |
| 200-C | 800 |
| 275-BC | 500 |
| 275-EB | 500 |

The owner's bands ([next section](#the-owners-targets-and-bands-issue-101)) replace the earlier process rules: maintenance and operator downtime each flag above 2.5%, dry-end waste above 1.25% of the yield after trim and shear (target 1%), side trim above 3.25% (target 3%), shear above 1% (target 0.75%). Setup trim still counts the setups above the 3.25% flag in tool evidence; the UI's aggregate trim card does not apply the per-setup threshold to a period average. A speed-attainment card below 100% is highlighted.

## The owner's targets and bands (issue #101)

`src/investigator/targets.json` holds the plant's standards from the owner's golden review of 2026-11-10, each with its source; `targets.py` reads them. A band places a figure against its standard: **home run** (at or under the home-run line), **on target** (at or under the target), **over target** (above the target, at or under the flag) or **flag** ("what's going on?", above the flag). The tools put the band on the row beside the figure, as the word (`<measure>_band`, or `band` on a one-measure row) and as the words an answer prints (`..._band_text`, which names the thresholds), so the headline shows the band without the prose typing a threshold. The analyst's process context states the bands from the same file (`targets.prompt_text()`), and so does every threshold an answer names in its prose.

| Measure | Home run | Target | Flag above | Denominator | Lens |
| --- | ---: | ---: | ---: | --- | --- |
| Operator downtime | | 2.5 % | 2.5 % | scheduled minutes (480 a shift, 1,440 a day) | controllable |
| Maintenance downtime | | 2.5 % | 2.5 % | scheduled minutes | controllable |
| Operator and maintenance together | | 5 % | 5 % | scheduled minutes | controllable |
| Side trim | 2.75 % | 3 % | 3.25 % | gross sq ft | schedule-driven |
| Shear (chop) | 0.5 % | 0.75 % | 1 % | gross sq ft | controllable (sequencing) |
| Dry-end waste | 0.75 % | 1 % | 1.25 % | yield after trim and shear | controllable (temperatures) |
| Peel (roll prep) | | about 0.5 % | owner to set | roll stock consumed | controllable |
| Core (at splice) | | about 0.1 % | owner to set | roll stock consumed | controllable |

Bands ride on the daily `get_shift_kpis` row (maintenance, operator, the two together as `op_maint_pct`, trim, shear, dry-end), on the period KPI tiles (`trim_pct`, `shear_pct`, `dry_end_pct`, `operator_pct`), the waste breakdown (trim, shear, dry-end on its yield), the trend weeks of those metrics, and the downtime review. Peel and core are on record; the records carry no peel or core quantity yet, so no answer shows them.

**Speed baseline.** "Good speed" for a grade (quality ID) is its sustained average over the trailing year: lineal feet over running minutes (stops out) of every setup of the grade with at least two running minutes, in the 365 days ending on the day asked about, inside the record store (`RecordStore.grade_speed_baselines`; two months until the rolling year of #35 lands). Never the maximum speed, which is a ramp-up point. `get_wet_end_performance` compares each run's running speed (stops out by interval overlap) with its grade's baseline (`baseline_fpm`, `baseline_gap_fpm`, `baseline_gap_ft`, `under_baseline`, and `runs_under_baseline` on the envelope); the cost model's speed lever sums the gap of every run of a window under it.

**Downtime answers** (`get_downtime_review(window, shift)`). The owner's default (review of #111): a downtime question that names no shift and no window answers the **previous production day, all shifts**, whatever the shift control says ("top downtime reasons", "show downtime by reason", "how much downtime?" asked on Monday 14 Sep answer Friday 11 Sep); shift detail only when a shift is named ("for this shift", "on shift 2", "shift two", "night shift": any mention of a shift but "all shifts") or a flag pulls one in. A question that names the selected day ("on Monday", "for this day"), asks dollars ("what did downtime cost": the review prices nothing, so it stays each shift's priced downtime), filters (maintenance) or groups another way (by equipment or kind), or a Period control on Week, Month or 4-4-5 (unless "yesterday" moves it to Day) is asked of the controls as before. On the first day of the records, which has no day before it, the default is that day, all shifts. An address is explicit, so the default moves questions, never a URL: `/corr/1/day/<day>?measure=downtime` keeps main's meaning (each shift's downtime, priced), the review is `measure=review`. A where-to-look question is asked in the scope the card answered, and a downtime look under one shift names it ("Show downtime by reason for this shift"). A production day's downtime over all shifts is the review: maintenance and operator downtime lead, each and together against its band as a percent of scheduled minutes; every reason, by kind, ranked by its percent of scheduled minutes with its stop count and minutes per stop; external downtime (a power outage) is listed apart under "External, not controllable", and planned maintenance (PM) is scheduled time, listed last; neither is ranked or counted in a band. "Top downtime reasons" at any grain take the same tool; asked about "yesterday", the question moves the controls to the production day before the selected one (issue #105) and answers the review there. Under Week, Month or 4-4-5 downtime by reason is the review too, and so is downtime over a window the question names ("how much downtime last week") and downtime against the previous window (`compare=prev`); by equipment or kind, or counted, it stays the Pareto. The review keeps the previous window of the same kind beside, as the Pareto did: each reason's `previous_minutes`, `previous_stops` and `previous_pct_of_scheduled` (zero for a reason that window did not record, null when it is outside the records), the totals' `previous_*_minutes`, `previous_*_stops` (issue #113: a kind's stop count beside its minutes) and `previous_*_pct`, and "versus …" in the window line. Under one selected shift the downtime answer stays that shift's KPIs and breakdown, now ranked by percent of the shift with stops and minutes per stop, PM last. Planned maintenance and external downtime never lead an answer: the Pareto's headline is the largest unplanned reason and lists the power outage, marked external, then planned maintenance, marked scheduled time, last; the period overview's downtime finding and the shift summary's largest reason are the largest unplanned ones. "What went wrong" is the supervisor lens only when the question names no measure ("what went wrong with speed this week" stays the speed answer; "with downtime", the review).

**Flags and recurrence.** A flag is a shift whose maintenance or operator downtime is over its band (2.5% of its scheduled minutes); a power outage is external and never trips one. It names the shift, the day and its supervisor slot (the shift's crew label: who to ask) and the reason that led it, and gives that reason's recurrence over the trailing year ending on the flagged day: `times_over_band`, the shifts on which the same reason, in the same kind, was over the band on its own, out of `shifts_counted`; and `weeks_in_top_five`, the Monday-to-Friday weeks in which it was among the five reasons with the most unplanned minutes, out of `weeks_counted`. A shift-scoped answer counts that shift's own history, an all-shift one the plant's. Every flag carries its recurrence, however many fire (a month of all shifts fires about two dozen); the review lists them all, the five largest beside the top reasons and the rest after the other reasons. The recurrence is one read of the trailing years the flags span, then a few lookups per flag (bisect over the sorted dates; the weekly top fives of whole weeks computed once). The daily `get_downtime_breakdown` carries its shift's flags on a record-store day. An answer cites a flag as `(query N · flag M)`.

**Two lenses, one set of numbers.** Ownership: the total cost of waste in dollars, side trim first, then the rest by dollars a year. Bond, warp, misalignment and moisture are dry-end waste, the tag scrap (the owner, PR #111): one subtotal line (`dry_end_rank`, `dry_end_usd`, `dry_end_usd_per_year`, its rows added up; `dry_end_pct_of_yield` and its band over the yield after trim and shear) ranked among shear and the unexplained remainder, with each kind kept as its own line beneath it (rows with `group: dry_end` and no rank of their own), since the detail is what supervisors go after; shear is read against gross. Supervisor: the controllable losses ranked, side trim shown as schedule-driven; the card lists every controllable lever (the five largest beside the headline, the others under a label), then the schedule-driven ones (side trim, planned maintenance) and the external ones (a power outage), each under its own label, unranked. A rate the visitor changes re-ranks the card in the browser as the server ranks it (`web/money.js`, [the cost model](COST_MODEL.md)). On a day whose own view excludes a record (the data-quality demo) the lenses decline for the shifts it touches, since they read the record store, which holds no such record; the shift's own dollars (`get_shift_dollars`) leave it out. The question picks the lens; both are `get_opportunities` over the same quantities at the same rates ([the cost model](COST_MODEL.md)).

Changing a target affects generated footage, validation, and attainment. It is a process-rule change requiring deliberate review, not an interface preference. Do not infer a target from the best observed day. The 200,000 and 275,000–300,000 lineal examples in [the original build guide](BUILD_GUIDE.md) are contextual shift examples, not grade-independent thresholds.

## Quantity-driven mock orders — implementation follow-up

Owner inputs: sheet widths 12–60 inches, lengths 20–92 inches, roughly 150–190 dry-end setups per day, compatible grades paired, and one-order/multiple-outs cases. These inputs guide a simulation; the plant has not supplied a measured demand distribution.

The v4 generator samples driver-order demand of 650–1,750 individual sheets before allocating time. It chooses feasible centered placements on 87/92/95/98-inch webs with 2–8 inches total trim. Every fifth wet-end group runs one order. Paired groups use one to four consecutive lower orders and an upper order sized to span them; that deliberate correlation demonstrates continuation. Grade and sheet dimensions remain immutable for each order.

For paired knives, the generator uses ten compatible cutoff-length pairs spanning 20–92 inches. Single-order cut lengths are integer samples across the full range. The paired catalogue keeps exact simultaneous cutoff boundaries small; it is a disclosed simplification, not a general order optimizer.

    common_inches = lcm(cutoff lengths)
    planned_travel_inches = ceil(requested_sheets / outs × cutoff_length / common_inches) × common_inches
    produced_sheets = cuts × outs
    remaining_after = remaining_before − produced_sheets
    planned_overrun = planned_sheets − requested_sheets
    running_seconds = (net_feet + shear_feet) / running_fpm × 60
    elapsed_seconds = running_seconds + recorded_stop_seconds + boundary_idle_seconds

Running speed is grade target multiplied by a seeded factor: 0.84–0.89 on shifts one/three, 0.66–0.73 on shift two. This is a running-speed simulation factor; displayed average speed includes stops. Each new setup adds six 34-inch full-web shear chops (17 feet). A continuing frame does not add those chops again.

A cutoff-aligned partial quantity runs when a shift ends. The remaining order and setup continue into the next shift. Fractional time below one cutoff quantum is recorded as boundary idle, not fabricated output or a new downtime reason. These artificial boundary pauses preserve integer cuts; real asynchronous cutoff/shift allocation is future work.

The default month produces 165–208 setup records per day, mean 181.1, totaling 5,432 records. Counts are observations, not forced limits. September 14 produces 193 records for 191 distinct setups, 12–60-inch sheet widths and about 6.10 million gross sq ft. Each setup continuing at a shift boundary appears in both shifts' record counts. Wet-end counts also count each run observed within each shift; summing them is not a unique-day count of physical paper changes.

Orders + quantities exposes requested/planned sheets, outs, cuts, produced sheets, planned surplus and remaining quantities. Demand repeats on each continuing fragment: do not sum it across rows. Sum produced sheets. These are gross production quantities: rejects are accounted separately, so completed planned production is not proof that accepted customer demand is fulfilled. Reject replacement scheduling, independent arbitrary demand pairing and accepted shipment accounting remain open.

Each day is an independently seeded scenario. Remaining orders carry across its shifts; the final day's open orders stay visible as remaining quantities, and do not automatically carry into the next date's independent scenario.

### Output sanity check

Gross sq ft = elapsed minutes × elapsed-average ft/min × web width inches / 12. At a fixed 98-inch width, 1,800,000 gross sq ft implies 220,408 lineal feet: 459.2 ft/min over eight hours, or 153.1 ft/min over 24 hours. At 750 elapsed-average ft/min it takes about 294 minutes. Do not subtract downtime twice.

The heatmap shows estimated GOOD area, and generated widths vary. Reconcile each setup using its width and waste. One shift scope means eight hours per date; All shifts means 24 hours.

For 10,000 sheets at 30 × 45 inches, sheet area is 93,750 sq ft. At two outs, 5,000 cuts require 18,750 machine feet. At three outs, 3,334 cuts produce 10,002 sheets: an explicit surplus of two. Five 18-inch outs occupy 90 inches; a 98-inch web leaves eight total inches of trim, a 92-inch web leaves two. Web choice changes yield, not ordered sheet dimensions.

### Geometry, waste and observation categories

One or two active knives share machine travel. Outs multiply sheet counts and occupied width, not distance. Cross-web spans are contiguous and centered. Gross footage includes shear; trim applies to net footage after shear. Dry-end rejects are individual upper-knife sheets: sheets × width × length / 144. These conventions avoid overlap in this fixture; real waste overlap policy remains unconfirmed.

The generator now uses Up warp, Down warp, Bond - delamination, Bond - paper/raw-material issue and Misalignment. Each record has one reject category, so it is counted once. Raw-material wording is a reported attribution, not proof of supplier fault.

The maintenance catalogue contains Upper/Lower knife, Belt, Shear knife, Slitter/scorer, Double backer, Splicers 1–5 and Glue dams. Synthetic stop locations rotate by seed; upper-knife jams recur for the demonstration. The catalogue does not assert installed equipment or measured failure rates. Belt identity, actual splicer configuration, compound defect policy and realistic frequencies need owner calibration.

Analysis checks finite quantities, geometry, grade targets, order identity and the remaining-quantity ledger. Thirty seeded-day tests independently audit cuts, areas, quantities, continuation and all 1,440 minutes. Bad setup values remain excluded evidence; no production file is rewritten.

## Stop allocation (current implementation)

A stop intersects a setup when stop_start < setup_end and stop_end > setup_start. Allocated seconds are max(0, min(ends) − max(starts)). A one-minute stop split evenly across adjacent setups contributes 0.5 minute to each, and one stop count to each. Adding setup stop counts therefore need not equal the number of distinct stops.

Version 1 rejects overlapping stops and stops crossing shift boundaries. It handles midnight inside shift 3 because timestamps include the date. It does not implement timezone conversion, daylight saving, overlapping-stop resolution, or shift-crossing allocation. Those are future decisions, not hidden normalization rules.

## Calendar windows

| Selection | Current window | Previous window |
| --- | --- | --- |
| Production day | Selected date 07:00 through next date 07:00 | No automatic previous-day report (the `day` window of `get_period_kpis` compares with the previous production day) |
| Production week | Monday–Friday production week containing the selected date | Preceding Monday–Friday week |
| Calendar month | Calendar month containing selected date | Previous calendar month |
| 4-4-5 accounting period | Containing period of a 52-week year of four 4, 4, 5-week quarters (P1-P12) | Immediately preceding accounting period |

The production calendar is Monday to Friday over the rolling year (issue #35): for the year ending 30 September 2026, from Tuesday 30 September 2025, 262 production days and 786 shifts; weekends are plant-down and emit no records, and there is no holiday calendar. The demo 4-4-5 year starts on Monday 3 August 2026 (`FISCAL_START`): P1 is 3–30 August, P2 31 August – 27 September, P3 28 September – 1 November (5 weeks), and so on to P12; the prior year starts 52 weeks earlier, on 4 August 2025, and its periods are labelled "of the prior year" (its P12, 29 June – 2 August 2026, is P1's previous period). Every date has a period, so the whole window is covered; the window's first period (the prior year's P3, 29 September – 2 November 2025) is partial because the year opens on its second day, and its previous period is unavailable. The displayed heatmap still uses Sunday–Saturday columns: weekday presentation is independent of the accounting anchor; weekend cells are marked `plant_down` and hatched. Fiscal anchor, week convention, 53-week years, and timezone/DST require plant confirmation.

Missing periods are unavailable, not zero; a weekend is plant-down, not missing. Current partial totals describe observed contributions. Rankings and comparison deltas require complete windows. A bad setup conservatively makes all evidence for its shift incomplete, including otherwise sound downtime records. Daily investigations can finish with `status=complete` while their evidence has `coverage=incomplete`; check both fields. Period investigations instead use `status=partial` when coverage is incomplete.

## Reading the heatmap

Each available cell is a production date aggregated over the selected shift scope. Its value is estimated good square feet. For the available values in that report:

    density = (value − minimum) / (maximum − minimum)
    alpha = 0.22 + 0.78 × density
    background = rgba(8, 127, 117, alpha)

If all available values are equal, alpha is 1 for every available cell. Missing/incomplete dates are hatched and excluded from scaling. Blank positions before the first date align the weekday columns; they are not missing production records.

Colors are relative to the current report. The same shade in another report need not mean the same output. Darker means more estimated output, not better efficiency or target attainment. Width, grade mix, shift scope, and waste all affect comparisons. Read the tooltip values and speed attainment before judging a day. The legend has no numeric endpoints. Day numbers are black on light cells and white on the deepest ones: the flip is where the teal blended over the light surface crosses relative luminance 0.179, so both inks read at 4.5:1 or better at every alpha (issue #36); in the dark theme every cell's number is white.

Hover, keyboard-focus, or click a day to read output, speed attainment, downtime by kind, waste, setup/paper-change totals, and the largest recorded downtime contributor. This is a detail selection, not navigation to a daily investigation. Choose Production day and its date explicitly to investigate that date.

The renderer assigns `cell.style.backgroundColor` directly. Keep the restrictive server CSP; do not restore a `style` attribute or weaken CSP to recreate the gradient. The current CSS still contains the old custom-property expression, but available cells use the direct property assignment. A DOM-stub unit test cannot prove CSP/browser behavior, so browser verification remains necessary.
