# Metrics, targets, and calendar interpretation

This is the calculation reference for the current synthetic workbench. The implementation lives in `analysis.py`, `fixture.py`, and `periods.py`. These definitions describe what the code measures; they do not establish causal explanations or achievable savings.

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
| Trim / shear / dry-end waste | 100 × sum(category area) / sum(G) | Area-weighted percentages |
| Throughput | 12 × sum(G) / sum(F) | Average gross web width in inches; this label does not mean output per hour |
| Dry-end setups | Count of valid setup records | Invalid setups are excluded |
| Paper changes | Count of distinct wet-end IDs per day and shift | Sum those counts across periods; IDs repeat between days |
| Lineal / setup | sum(F) / valid setup count | Gross machine footage per setup |
| Lineal / wet-end | sum(F) / distinct wet-end count | Available in KPI evidence; not a dedicated UI card |

Example: 10 minutes at a 900 target and 10 minutes at a 500 target have a combined target of 14,000 feet. If those runs produce 10,500 feet, attainment is 75%. It is not obtained by taking an unweighted average of their individual percentages.

Period speed is total feet / (480 × number of observed shifts). Period downtime percentages average shift percentages because every supported shift has exactly the same 480-minute denominator. Period waste is weighted by gross area. The current implementation reconstructs target footage from each daily footage/attainment pair; it does not retain an independent raw target-footage field in the period response. Zero or wholly invalid shifts would require additional handling before supporting external data.

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

Maintenance and operator downtime each flag above 2.5%. Dry-end waste flags above 1%. Setup trim flags above 3.25% in tool evidence. The UI's aggregate trim card does not apply the per-setup threshold to a period average. Shear's 0.4–1% range is contextual guidance, not an implemented alarm. A speed-attainment card below 100% is highlighted.

Changing a target affects generated footage, validation, and attainment. It is a process-rule change requiring deliberate review, not an interface preference. Do not infer a target from the best observed day. The 200,000 and 275,000–300,000 lineal examples in the README are contextual shift examples, not grade-independent thresholds.

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
| Production day | Selected date 07:00 through next date 07:00 | No automatic previous-day report |
| Week ending on date | Selected date and preceding six production dates | Preceding seven production dates |
| Calendar month | Calendar month containing selected date | Previous calendar month |
| 4-4-5 accounting period | Containing period in repeating 4, 4, 5 week pattern | Immediately preceding accounting period |

The demo year starts September 1, 2026, a Tuesday. P1 is September 1–28; P2 is September 29–October 26. The displayed heatmap still uses Sunday–Saturday columns: weekday presentation is independent of the accounting anchor. The demo only contains September 1–30. P2 therefore has two observed days and 26 unavailable days. Fiscal anchor, week convention, 53-week years, and timezone/DST require plant confirmation before extending the calendar.

Missing periods are unavailable, not zero. Current partial totals describe observed contributions. Rankings and comparison deltas require complete windows. A bad setup conservatively makes all evidence for its shift incomplete, including otherwise sound downtime records. Daily investigations can finish with `status=complete` while their evidence has `coverage=incomplete`; check both fields. Period investigations instead use `status=partial` when coverage is incomplete.

## Reading the heatmap

Each available cell is a production date aggregated over the selected shift scope. Its value is estimated good square feet. For the available values in that report:

    density = (value − minimum) / (maximum − minimum)
    alpha = 0.22 + 0.78 × density
    background = rgba(8, 127, 117, alpha)

If all available values are equal, alpha is 1 for every available cell. Missing/incomplete dates are hatched and excluded from scaling. Blank positions before the first date align the weekday columns; they are not missing production records.

Colors are relative to the current report. The same shade in another report need not mean the same output. Darker means more estimated output, not better efficiency or target attainment. Width, grade mix, shift scope, and waste all affect comparisons. Read the tooltip values and speed attainment before judging a day. The legend currently has no numeric endpoints, and dark-cell day-number contrast still needs improvement; see the dated review.

Hover, keyboard-focus, or click a day to read output, speed attainment, downtime by kind, waste, setup/paper-change totals, and the largest recorded downtime contributor. This is a detail selection, not navigation to a daily investigation. Choose Production day and its date explicitly to investigate that date.

The renderer assigns `cell.style.backgroundColor` directly. Keep the restrictive server CSP; do not restore a `style` attribute or weaken CSP to recreate the gradient. The current CSS still contains the old custom-property expression, but available cells use the direct property assignment. A DOM-stub unit test cannot prove CSP/browser behavior, so browser verification remains necessary.
