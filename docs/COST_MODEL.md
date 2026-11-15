# Cost model: data to dollars

How the investigator turns the records' quantities into dollars (issue #31), and how every money answer reads one ledger (issue #164): the rates, where each one comes from, the formula behind every priced field, each lever's band and owner role, and the one place a yearly figure comes from. The rates and assumptions live in `src/investigator/rates.json`; the arithmetic is `src/investigator/cost_model.py`. What a live model round costs to run is a different question, answered in [Anthropic in the hosted demo](ANTHROPIC_DEMO.md#cost-per-round).

## Rates and their sources

The records hold no costs. `cost_model.py` prices the tools' quantities at assumed rates from
`src/investigator/rates.json`; the defaults are the owner's post
([Where the money hides in a corrugator's numbers](https://justfetz.github.io/posts/corrugator-dashboard.html)).
Every dollar is synthetic data at an assumed rate, never a real plant's figure, and every answer that
carries dollars names its rates in a provenance line ("paper $55/MSF (post), machine time $1,200/hr
(post), contribution margin not set (assumption: owner to set), trim target 3% (owner, 2026-11-10)").

| Rate or assumption | Default | Source | `rates=` key |
| --- | --- | --- | --- |
| Paper cost, per MSF of combined board (the paper in all its plies, the medium's take-up included; not one grade's price) | $55 / MSF | post; owner confirmed 2026-11-12 | `paper` |
| Machine time (bottleneck shadow price) | $1,200 / hr | post ("more than $1,200 an hour") | `hour` |
| Board contribution margin | not set | not in the post; owner to set | `margin` |
| Side-trim target | 3 % of gross (3.25 % is the flag, 2.75 % a home run) | owner, 2026-11-10 (`targets.json`, issue #101) | `trim` |
| Volume | 7,000,000 sq ft / day / machine; 3 shifts; 5 days a week | post | fixed |
| Annual volume | 1.8M MSF a year per machine (the post's 3.6M across two machines), so 257 production days | post | fixed |
| Net width | 96 in on a 110 in machine | post | fixed |
| Scheduled time | 1,440 min / day | post | fixed |
| Required run speed at 10 % downtime | 675 ft/min: the conversion from machine minutes to MSF not made, never a lever (below) | post | fixed |
| One minute of downtime at the required 675 ft/min | 675 lineal ft x 96 in / 12 = 5.4 MSF | post | derived |
| Production days a year | 1.8M MSF / 7,000 MSF a day = 257.14: the only factor a yearly figure uses, on period-to-date dollars (below) | post | derived |
| 0.1 point of side trim | 0.001 x 3.6M MSF x $55 = $198,000 a year across two machines (the post rounds it to about $200k); the post's example, quoted on the case study, never a figure an answer annualizes | post | derived |

## Overrides

Overrides layer: the defaults, then `INVESTIGATOR_RATES=paper:55,hour:1200,margin:40` on the server,
then `rates=` on the request (`/api/ask` body, `/q`, or the URL; [the URL grammar](URLS.md#room-left-in-the-grammar)). Each source is named.
A rate takes at most two decimals, so the rate a link carries is exactly the rate its answer used; an
invalid `INVESTIGATOR_RATES` stops the server at boot.

## Formulas

Three components per priced row, kept apart so that changing one rate changes only the dollars that use it:

| Field | Formula | Applies to |
| --- | --- | --- |
| `paper_usd` | waste sq ft / 1,000 x paper $/MSF | side trim, shear, the dry end, the changeover rejects, the waste boards, bale, the unexplained remainder (a negative remainder is not priced) |
| `machine_time_usd` | minutes x machine $/hr / 60; for waste board, minutes = MSF / 5.4 (the required speed's MSF a minute) | downtime, the speed lever, shear and dry-end rejects (they ran through the machine). Side trim is width, not length: it takes no extra run time |
| `usd` | `paper_usd` + `machine_time_usd` | every priced row |
| `contribution_usd` | MSF not made x margin $/MSF; MSF not made = minutes x 5.4 for time, the waste MSF for shear and rejects | null with `contribution_status` "margin not set" until a margin is set, never $0; shown beside `usd`, never added (it prices the same lost capacity as the shadow price, from the other side) |
| `usd_basis` | the formula with its numbers, e.g. "11,218.7 sq ft = 11.219 MSF x $55/MSF paper = $617; 11.219 MSF / 5.4 MSF per min = 2.08 min x $1,200/hr machine time = $41.55; contribution: margin not set" | so a superintendent can redo it on paper. A row at standard reads "$4,521 all recorded less $75.00 above standard = $4,446 at standard" |
| `usd_per_year`, `contribution_usd_per_year` | the period to date's own dollars for the row / its production days x 257.14 (`cost_model.annual`) | only a money window's levers and lines (`get_opportunities`); null on every other priced row, a shift's and a day's included |

A price is the window's own dollars. Nothing is annualized from a shift, a day or a window's own figures (the owner's call 3 on issue #164): the window factors the per-year fields carried before (1.8M MSF / the window's gross, and the year's scheduled minutes / the window's) are gone from every row, tile and formula.

## The one money ledger (issue #164)

The owner, 2026-11-14: "no money jumping around." One window's figure reads the same in every view, and a shift's dollars add to its day. One ledger (`cost_model.ledger_cells`, `ledger_sums`) prices every money view, the shift's card, the day's, a week's, a month's and a 4-4-5 period's levers and both lenses, from the same cells.

### The owner's four calls

1. **Every lever is its gap to its band in `targets.json`.** Trim, shear and dry end inside their bands price at $0. Their cost inside the band is context, shown apart as "at standard", outside the total.
2. **Two speeds, never one definition.** The speed lever is each grade's trailing-year baseline (`targets.json`, speed). The 675 ft/min required speed at 10% downtime (`rates.json`, `required_fpm`) stays beside it in every money answer, labelled, and is the conversion from machine minutes to MSF not made, never a lever. A test checks that every line of a money answer that names a speed says which one (`tests/test_money_ledger.py`).
3. **A yearly figure comes only from period to date.** Never from one shift or one day.
4. **Each band names an owner role** (`owner` in `targets.json`, and on the speed baseline), set by the owner as he sets the margin; until he does it reads "owner to set". Every lever names its role (`targets.owner`).

### The levers

| Lever (`key`) | Quantity in a cell (one shift of one day) | Band | Control | Where to look (`look_at`) |
| --- | --- | --- | --- | --- |
| Speed under the grade baseline (`speed`) | each wet-end run's part in the shift: the lineal feet it would have made at its grade's baseline (the trailing year through its own day) in its running minutes, less the feet it made, / the baseline = minutes | each grade's own baseline | controllable | the grade furthest under its baseline |
| Side trim over the 3% target (`trim`) | side trim − target % x gross, sq ft | the trim target, a rate (`rates=…trim:`) | schedule-driven (the trim band's `lever`) | the order mix and web widths on the shifts over target |
| Shear over the 0.75% target (`shear`) | shear − 0.75% x gross, sq ft | `targets.json` shear | controllable | the order and grade changes behind the chops |
| Dry end over the 1% target (`dry_end`) | dry-end rejects − 1% x the yield after trim and shear, sq ft | `targets.json` dry_end | controllable; each family's own cause in `control_note` | the largest dry-end family, by grade |
| Operator downtime over the 2.5% band (`operator`) | operator minutes − 2.5% x the shift's scheduled minutes (order changes are operator stops) | `targets.json` operator | controllable | the shift's largest operator reason |
| Maintenance downtime over the 2.5% band (`maintenance`) | unplanned maintenance minutes − 2.5% x scheduled minutes (planned maintenance and an outage are not maintenance) | `targets.json` maintenance | controllable | the shift's largest maintenance reason |

Each cell's gap is clipped at zero and rounded (0.1 sq ft, 0.01 min), and a window's lever is the sum of its cells' gaps, priced once on the window. The band is the shift's standard: a shift over it is an opportunity even when the window's average is inside it, and a lever's line leads with how many of the window's shifts were over, then the window's own share ("62 of 66 shifts over the 3% target, side trim 3.49% of gross"). So a day or a month whose own share is inside its band can still price the shifts that were over it: on 10 June 2026 the day ran 2.98% trim, two of its three shifts were over 3%, and the trim lever is those two shifts' sq ft over 3%. A window-level gap would price that day at $0, but then a day's dollars would not be its shifts' added up (the owner's rule: a shift's dollars add to its day), so the ledger reads the band at the shift. The trim cells (`price_terms.trim_cells`, each shift's side trim and gross) let the card re-derive the trim lever at another target.

### Rows shown apart, outside the total

| Group (`apart`) | Rows | Priced as |
| --- | --- | --- |
| `at-standard` | each banded lever's cost inside its band ("Side trim at standard (up to 3% of gross)", "Operator downtime within its 2.5% band", …) | all recorded less above standard, each priced on its own quantity (`cost_model.at_standard`), so the lever, its line at standard and the ownership lens's total are the same three figures in every view |
| `schedule-driven` | Downtime: Planned maintenance (scheduled) | its minutes (the owner, PR #111) |
| `external` | Downtime: Power outage (external) | its minutes |
| `no-band` | the changeover rejects; the cutoff-knife and stacker waste boards | their area; `targets.json` sets no band for them yet, so they are not levers |

Totals: `total_usd` (and its paper, machine and contribution parts, the chips, `buckets`, and the arithmetic's sums) is the levers above standard; `at_standard_usd` the rows at standard; `all_in_usd` everything recorded and priced; `apart_gaps` counts the rows apart. A lever is ranked by its dollars above standard, a tie by name; the rows apart carry no rank, in the group order above.

### Every cell rolls up

A shift's card, its day's card, a week's, a period to date's and a year to date's levers are sums of the same cells, so each lever's quantity above its band adds exactly, and its dollars and the totals add to the cent but for the rounding of each priced figure (a figure priced once on a window and the sum of its parts priced each on its own can differ by half a cent per part). `tests/test_money_ledger.py` sums shift, day, week, period-to-date and year-to-date dollars and checks they agree, at the post's rates and at others; the all-shifts day card lists each shift's own dollars, which add to its headline.

### Whole dollars that add up (PR #168)

The owner's top rule: no money jumping. Every figure a card prints agrees across views, and every set of parts it prints adds up to the total it prints, in the whole dollars printed, never only in the cents. Each printed figure is allocated from the cents by the largest-remainder method (`cost_model.whole_dollars`, `largest_remainder`; `web/money.js` allocates the same way from `display_terms`, at a visitor's rates):

- **The root** is a window's every shift (for a day card and a shift card, the day's complete shifts). The root's dollars above standard print as their nearest dollar, a tie to the even dollar.
- **Each shift's share** is its floor, plus a dollar for each of the shifts with the largest remainders, until the shares add up to the root's (a tie goes to the earlier shift).
- **Within each shift**, its levers share the shift's whole dollars the same way. The cost at standard is a root of its own, shared among the shifts and then among each shift's rows at standard, and so is each row shown apart.
- **A figure for several shifts**, and each of its lines, is those shifts' whole dollars added up.

So a shift prints the same whole dollars on its own card, on its day's card and on any card that adds it up, and on every card:

- the shifts add up to the day;
- the levers add up to the figure;
- the rows at standard add up to their total;
- everything recorded and priced is the levers, the rows at standard and the rows apart added up;
- paper and machine time add up to the arithmetic's total;
- each chip is its lever's.

The ownership lens prints the levers' own figures for its split: side trim, shear and the tag scrap are their lever's whole dollars above standard and their row at standard's, added up. The dry-end kinds share the tag scrap's whole dollars, and the waste boards' two lines share the boards row's. Planning / admin and the unexplained remainder, which no other view prints, print their nearest dollar, and all the waste recorded is its lines added up.

A KPI card's operator and maintenance tiles (`get_period_kpis`, `compare_periods`) price the ledger's minutes, each shift's rounded as the ledger rounds them, and print the ledger's whole dollars: the lever's line and its line at standard added up, with planned maintenance for the maintenance tile. So the tile and the money card of the same window agree (the review's item 9: 4-4-5 P2's operator tile read $12,991 against the ledger's $12,990).

The cents (`usd` and every `*_usd`) stay what the formulas, the receipts (`tests/test_datapages.py`) and the value goldens recompute. The whole dollars ride beside them as `usd_shown` on each row and chip, and as `total_usd_shown`, `at_standard_usd_shown`, `all_in_usd_shown`, `total_paper_usd_shown`, `total_machine_time_usd_shown`, `controllable_usd_shown`, `ptd_usd_shown` and, under the ownership lens, `above_standard_usd_shown`, `no_band_usd_shown` and `dry_end_*_shown`, on each tile and on `details`. The prose, the dollar line, the chips, the bars, the table, the arithmetic and the tiles print them.

- A printed figure is within a dollar of its cents for one shift.
- A line that adds several shifts up is within a dollar a shift. In PR #168's sweep of July to September 2026 and 23 windows, nearly every printed line sat within a dollar of its cents and none two dollars off.
- A line can read a dollar off its own formula, and each money answer says so in its notes.
- A list is ranked by the whole dollars it prints, then by its cents.

Three checks hold it:

- `displayed_sums` (`evals/run.py`) adds up the printed lines of every money answer in the golden set, as an invariant.
- `tests/test_ui.cjs` adds up the card as drawn: the chips, the ownership split, paper and machine time, and the shifts.
- `tests/money_cli.cjs parity` holds the browser's allocation to the server's at other rates.

### Side trim by shift (PR #168)

The owner's word on PR #168: "On the trim, you should see trim by shift. While technically it does not matter, it is an indicator of how a shift may have done, eg tight scores, big trim or tight trim. It is explanatory, but a superintendent would want to know."

Wherever a card for several shifts shows the trim lever (a day, a week, a month, a 4-4-5 period, every lens), or the ownership lens's trim line, it lists each shift under "Side trim by shift, explanatory (schedule-driven)". Each shift's line shows:

- its dollars: above the target, or all recorded under the ownership lens;
- its side trim % of gross;
- its setups above the per-setup trim flag (`shift_kpis.setups_above_trim_target`, 3.25%).

Each line says the order mix and web widths set it, never a crew. The lines show on a day inside the band too, and they add up to the trim line they follow. They are the envelope's `details`, each cited as `(query N · detail M)`, with a receipt that opens that shift's `shift_kpis` rows.

### Period to date and the yearly figure

The period to date is the 4-4-5 period containing the window's last day (or the card's day), from its first day inside the records to that day (`cost_model.ptd_window`). Its label carries its end day ("4-4-5 P2 to date through Mon 14 Sep", the coordinator's default on PR #168), so a day card and its week never state two yearly figures under one label. A period that began before the records is labelled from their first day ("… to date from the first day of the records, Wed 1 Jan 2025, through …").

In a period's first week, while it holds fewer than five production days, the period before it is read with it ("4-4-5 P2 and P3 to date"), so a yearly figure is never one day's. When even that leaves fewer than five production days (the records' first week), there is no yearly figure. The card says why: `ptd_year_note`, "no yearly figure: 1 production day to date, fewer than 5" (`cost_model.MIN_RATE_DAYS`; `annual` returns none under it). A lever's yearly figure is its period-to-date dollars / the period's production days x 257.14, "a year at this period's rate", and a shift's window says whose: "(shift 1's own)".

- A shift's card and a day's card state the machine's period to date (all shifts): `ptd_usd`, `ptd_usd_per_year`, `ptd_days`, `ptd_label`, each lever in `ptd_levers`. Every shift of a day states the same yearly figure; no shift states one of its own (`total_usd_per_year` and every row's `usd_per_year` are null).
- A window's levers (`get_opportunities`) state their scope's period to date: all shifts, or the shift asked ("shift 1's own"), never a shift scaled to the machine. `total_usd_per_year` is the levers' yearly figures added; `ptd_*` the same period to date as a whole.
- `ptd_terms` and each row's `year_terms` (the period to date's quantity, its days, the trim cells) re-derive the yearly figures at a visitor's rates in the browser.

## Where the dollars appear

The fields are flat on the row, beside the quantity, so the live narrative cites them like any other
field and the narrative validator substitutes every dollar from the tool result. They ride on
`get_quality_breakdown`, `get_downtime_breakdown`, `get_waste_breakdown`, `get_downtime_pareto` and
`get_speed_opportunity` (opportunity hours x 60 minutes), `get_speed_variance` (each controllable bucket's lineal feet / the
reference speed; a bucket that ran better is not priced, and the grade mix and planned maintenance are schedule-driven and external
stops (a power outage) external, none of them priced, so the envelope's
`controllable_usd` is the priced buckets and the gap as a whole is not priced; [speed variance](METRICS.md#speed-variance-issue-92)), each the window's own dollars with no yearly figure, and on the three money tools, the ledger:

- `get_shift_dollars(shift)`: one shift on the ledger. Its six levers ranked by dollars above standard, the rows apart, the machine's period to date and its rate a year, the two speeds (`required_fpm`, `observed_fpm_with_stops`, `speeds_note`) and the shift's KPI tiles (`tile_money`: the speed tile is the speed lever; side trim, the dry-end families, maintenance and operator minutes all recorded, as the ownership lens's lines of the shift). Asked as "where did the money go this shift"; "what did waste cost" on the day grain is the same card.
- `get_day_dollars(shifts)`: the day's ledger over the listed shifts that are complete (all three when omitted; an incomplete one is named in `excluded_shifts`, not added): the same cells as each shift's card, added. The offline planner adds it to a money question over several shifts; the card's headline is its total, and each shift's own `get_shift_dollars` follows it.
- `get_opportunities(window, shift, lens)`: a window on the ledger, each lever with its window dollars, its yearly figure at the scope's period-to-date rate, what it is read against (`records`), its owner role and where to look, the rows apart after it. It ranks levers and names records; it never prescribes a machine adjustment. Asked as "where is the money this month", "what's costing us", "opportunities".
  `lens` reads the same ledger the owner's two ways (issue #101): `supervisor` ranks the controllable levers and lists side trim (schedule-driven) after them with the rows apart, each lever saying what controls it (the dry-end lever names each family's own cause from `reject_reasons.json`); `ownership` is the total cost of waste, side trim first (all of it, with its band), then by dollars every other line of the waste breakdown, each priced once: shear, the changeover rejects, the cutoff-knife and stacker waste boards, planning / admin (`not production waste`), the dry-end families as one tag-scrap subtotal with each kind beneath it, and the unexplained remainder (`unattributed`); its total is the waste breakdown's for the same window. Each banded line is split at its band: `above_standard_usd` is the lever's own figure and `at_standard_usd` the rest (side trim, shear, and the dry-end subtotal's `dry_end_above_standard_usd` / `dry_end_at_standard_usd`); the envelope's `above_standard_usd`, `at_standard_usd` and `no_band_usd` add to its total. So "what does trim cost" states the trim lever's own figure as its part above standard, and the levers and the ownership lens agree for one window.

## Money questions

A money word beside another question never replaces it. Downtime, rejects, waste, the Pareto, the
overview and the comparison carry their dollars on the rows, so "which reason is costing us the most
minutes" is the Pareto, priced, and "why was this week so bad in dollars" is the overview (a why, bad or
killed question; the cost of a bare "slow", "what did slow running cost us", is the money answer, whose
speed lever is priced). On the day grain "what did waste cost" is `get_shift_dollars` (side trim, shear and the
dry end, each above its band and at standard, the changeover rejects and the waste boards apart); a named reject keeps the reject breakdown. A comparison, trend, speed against normal,
volume ("how much did we make in dollars", "output per hour in dollars"; a loss of output, "what's costing
us output", and a rate of a money word, "contribution per MSF", are money questions), rollstock, grade,
over/under-run, stops-per-day, summary, matrix, notes or ranking asked "in dollars" is refused with what
to ask instead, not answered as the levers alone.

Known gap: with Week, Month or 4-4-5 selected and no window named in the question, a money question is
answered by the levers (`get_opportunities`) whatever else it names, so "summarize this shift in dollars"
or "what did waste cost" under the Week control gives the levers, while the same question naming its
window ("what did waste cost this week") gives the priced waste breakdown.

Not built yet (issue #164's rest, each its own issue or a follow-up): year-to-date money questions (#103) and a top N beyond the ledger's top three (#94); "money this month versus last month" (refused, a router change); "up from last period" on each lever; figures rounded to three significant figures, a range where one rate drives a figure, and an "assumed rates" tag at the top that opens the rates panel; "margin not set" said once, in the rail: the card's dollar line no longer repeats it, but each money answer's notes still say it once, under its findings; the money section of the landing briefing (#162).

## The live paths, and what is not built yet

The live paths: the narrative may cite `usd`, `usd_per_year`, `paper_usd`, `machine_time_usd` and
`contribution_usd` (the formula text, the intermediate quantities and the ledger's rate-free terms stay off the packet). No live planner
is offered the money tools yet. The OpenAI planner loop also sees no dollar fields, so its requests
stay byte for byte main's (its 24,000-byte cap is tight: since #74 main's own request after one waste
breakdown is 24,040 bytes). The Anthropic tool loop and the analyst's plan catalog leave the money tools
out because their prompts still say costs are unavailable; the analyst still sees the dollars on the
priced rows of the tools it plans. Offering the money tools to the live planners is a prompt change for the live analyst (#23), not part of the card (#31b). Money questions are
answered offline: every one goes to the offline planner first.

## On the card (issue #31b, #164)

Every answer card carries the money of its scope, priced in the browser at the rates in force:

- **The dollar line** under the headline: `≈ $502 above standard this shift · $718k/yr at this period's rate (4-4-5 P2 to date through Mon 14 Sep, all shifts) · $7,898 all recorded, $6,998 of it at standard` (a window: `≈ $66,792 above standard this month · $790k/yr at this period's rate · …`, its own scope's period to date; the ownership lens: `≈ $521,172 the waste recorded this month · $412,627 at standard, $51,370 above standard, $57,175 with no band yet · …`, its three parts adding to it; a set margin: `· contribution $11,891 at $40/MSF, not added`), then one chip per lever above standard, largest first. Every figure is the whole dollars the card prints, so the chips add to the line. "Margin not set" is said once, in the rail's rates. On incomplete data the line and the arithmetic say so first: the dollars are the observed records only, and an all-shifts day counts its complete shifts and names the one left out; a window the records have not finished reads "to date".
- **The opportunities panel** under the charts: horizontal bars of the levers ranked by dollars above standard, then the rows shown apart, lighter, in the ledger's group order; each bar's dollars, its yearly figure where it has one, minutes, formula (`usd_basis`), records, owner role and where to look on hover; the top eight with the rest behind "All", and a Table flip with the same rows.
- **The arithmetic** at the bottom, with the card's own numbers: the area above the targets → MSF → paper $ at the paper rate; machine minutes (downtime over its bands, the speed under the grade baseline, the run time waste took) → MSF not made at the required run speed at 10% downtime, 675 ft/min → $ at the machine rate; the cost at standard, shown apart; the total above standard (an all-shifts day: each shift's own dollars, which add to it), and its yearly figure at this period's rate with the period to date and the 257.14 production days a year it is read from; the contribution (or "margin not set"). Then the rates line with each rate's source and the note that these are synthetic records at assumed rates, not a plant's figures.
- **Tiles**: every KPI tile that measures a loss shows the window's own dollars, labelled, never a year (the whole dollars the ledger prints for it: a shift's tiles are its own lines, a window's operator and maintenance tiles the ledger's lines of the same window, PR #168), from a tool's own `tile_money` (one entry per loss, named by its `metric`, listing the tiles it sits on in `metrics`, and saying what it measures in `measure`), never from the tile's rounded value: a window's on `get_period_kpis` / `compare_periods` (`cost_model.period_tiles`), a shift's on `get_shift_dollars` (`cost_model.shift_tiles`), the card's money answer, so a shift's tiles get their dollars when it lands after the first paint and a KPI answer keeps its #33 size. A tile is what its figure measures, all recorded (the ownership lens's line of the same window), except a shift's speed tile, which is the speed lever above standard (the grade baseline: the one speed a price uses). A window's speed tile is not priced. A count, a volume or a width (setups, MSF, throughput) has no dollars, and a period report's tiles (a whole window's row) are priced in its panel, not on the tiles. The value cases `value-kpi-*` (a window) and `value-dollars-shift-*` (a shift) hold each tile's dollars by hand.
- **Show as minutes · % · $** in the rail ($ by default): priced tiles and bars in machine minutes, in
  percent of the window (an area of the gross, a time of the scheduled minutes), or in dollars. A priced
  chart in another unit than its own is drawn as ranked bars of that unit; the speed, KPI, trend and
  other unpriced charts keep their units.
- **Your rates** in the rail: paper $/MSF, machine $/hr, board margin, trim target, each with its source
  (post, this server, owner to set, process rule, yours). "Change rates" opens the editor: a rate the
  server would refuse (`parse_rates`' rule) says why and changes nothing; an accepted one re-prices every
  card on the page at once, with no request and no model, writes `rates=` into the address (replaced, not
  pushed) and rides in every question asked after it. Back and forward keep the page's rates; a pasted
  link brings its own.

What the card states comes from the server (the #31b review: no figure is the browser's own). Each money tool
returns, beside its rows and totals, the card's summary (`cost_model.summary`): `buckets` (each lever's chip),
`area_sqft` and `area_msf` (the area priced as paper), `stop_minutes`, `speed_minutes`, `waste_run_minutes`,
`machine_minutes` and `msf_not_made` (the arithmetic's machine line), and the ledger's `at_standard_usd`, `all_in_usd`
and period to date. Every priced row carries `share_of_window_pct`, its quantity as a share of the window's gross or
scheduled minutes (the % of Show as). The value cases `value-dollars-shift-*`, `value-dollars-day-0914*`,
`value-dollars-opportunities-*`, `value-dollars-supervisor-september` and `value-dollars-ownership-september` hold all of
these by hand from the record families, cell by cell (`value-dollars-day-0914-shifts-1-3` two shifts,
`value-dollars-day-0914-corrupt` the incomplete shift left out, `value-dollars-opportunities-september-shift-1-ptd-year` a
shift's own period-to-date rate). None of them reaches a model: the narrative packet and the OpenAI compaction drop the
terms, and `get_day_dollars` is not in a model's catalog.

How the card prices: every priced row carries `price_terms`, the pricing call itself, rate-free and
unrounded (`kind`, `quantity`, the window's `gross_sqft` and scheduled `minutes`; the trim rows also
`trim_cells`, since their quantity, the gap to the target, moves with the target, a rate; a row at standard `part` "at"
with the `total` and the `over` it is the difference of). A lever's `year_terms` and an ownership line's `above_terms`
are the same for its yearly figure and its part above standard, and the envelope's `ptd_terms` for the period to date.
`web/money.js` makes the same calls with the same products, the same rounding (Python's round half to even) and the same
text, then ranks the money tools' rows as `cost_model.rank_ledger` (or the ownership lens's order) does and redoes their
totals and summary, the period to date and each tile, and rewrites the answer's prose from the re-priced rows: each
dollar figure becomes the same field re-priced (a figure followed by " at standard", " above standard in" (the period to
date) or " above standard" takes the field of that name first), the trim lever's name and records the target in force,
the rates, formula and contribution lines are written again, and a ranked money list is put in its new order. The levers
are ranked the way their lens ranks them (issue #101): under the supervisor lens the controllable levers are ranked and
side trim and the rows apart follow them with no rank, however large a rate makes them; under the ownership lens all of
side trim stays first, however small, and the dry-end subtotal is re-derived from its kinds and moves with them. The
controllable totals the supervisor headline states are re-derived too (only a line that names controllable levers takes
them). The speed variance's controllable dollars are re-derived the same way; its grade mix, planned maintenance and
external stops are never priced. "What came back, per query" shows each query's result at the rates in force too (what
the page's own address returns from the server), so a re-numbered row citation names the row it shows. An analyst's
interpretation is not rewritten (a model's sentence moved to other figures could claim what no row says, or keep a
comparison the new figures reverse): at other rates it says which rates it was written at, and the evidence under it is
re-priced. So the page after an edit and the permalink it writes show the same figures, prose included:
`tests/test_money_ui.py` checks it over a set of answers (a shift, a day, two shifts, the corrupt day, weeks and a month,
the two lenses on a month and on a shift's week, a speed variance whose mix is schedule-driven, one with planned
maintenance and an external stop each in its own bucket, and days with planned maintenance and a power outage apart,
among them) at six sets of rates (`tests/money_cli.cjs parity`), and the value case
`value-dollars-shift-0914-shift-2-client` holds the server's `get_shift_dollars` at other rates to the client's figures
(`evals/client_money.json`, written by `evals/make_client_money.py`). The terms never reach a model. A card's money is
its own `get_day_dollars`, or its only `get_shift_dollars` / `get_opportunities`, when it asked one (an analyst's plan
over two shifts is not added up in the browser), else the money answer of the scope its answer ran over, never the
rail's when the answer says otherwise: its own address, else its first windowed query ("How did September go versus
August?" under Day is priced over September), else its day queries' `production_day` and `shift` (an analyst's card has
no address of its own; several shifts are the day's all shifts), one GET per scope shared by its cards
(`measure=dollars` on the Day grain, `measure=money` on a window), sent after the card's first paint so it never slows
the offline answer, and counted as a round by the server; a reload replays the thread within its share of the rate
limit counting those GETs too. `/api/config` carries the rates, their sources and the constants, the production days a
year among them (`cost_model.client_config`).
