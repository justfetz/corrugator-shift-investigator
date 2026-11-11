# Cost model: data to dollars

How the investigator turns the records' quantities into dollars (issue #31): the rates, where each one comes from, and the formula behind every priced field. The rates and assumptions live in `src/investigator/rates.json`; the arithmetic is `src/investigator/cost_model.py`. What a live model round costs to run is a different question, answered in [Anthropic in the hosted demo](ANTHROPIC_DEMO.md#cost-per-round).

## Rates and their sources

The records hold no costs. `cost_model.py` prices the tools' quantities at assumed rates from
`src/investigator/rates.json`; the defaults are the owner's post
([Where the money hides in a corrugator's numbers](https://justfetz.github.io/posts/corrugator-dashboard.html)).
Every dollar is synthetic data at an assumed rate, never a real plant's figure, and every answer that
carries dollars names its rates in a provenance line ("paper $55/MSF (post), machine time $1,200/hr
(post), contribution margin not set (assumption: owner to set), trim target 3% (owner, 2026-11-10)").

| Rate or assumption | Default | Source | `rates=` key |
| --- | --- | --- | --- |
| Paper cost | $55 / MSF | post | `paper` |
| Machine time (bottleneck shadow price) | $1,200 / hr | post ("more than $1,200 an hour") | `hour` |
| Board contribution margin | not set | not in the post; owner to set | `margin` |
| Side-trim target | 3 % of gross (3.25 % is the flag, 2.75 % a home run) | owner, 2026-11-10 (`targets.json`, issue #101) | `trim` |
| Volume | 7,000,000 sq ft / day / machine; 3 shifts; 5 days a week | post | fixed |
| Annual volume | 1.8M MSF a year per machine (the post's 3.6M across two machines), so 257 production days | post | fixed |
| Net width | 96 in on a 110 in machine | post | fixed |
| Scheduled time | 1,440 min / day | post | fixed |
| Required run speed at 10 % downtime | 675 ft/min | post | fixed |
| One minute of downtime at 675 ft/min | 675 lineal ft x 96 in / 12 = 5.4 MSF | post | derived |
| 0.1 point of side trim | 0.001 x 3.6M MSF x $55 = $198,000 a year across two machines (the post rounds it to about $200k) | post | derived |

## Overrides

Overrides layer: the defaults, then `INVESTIGATOR_RATES=paper:55,hour:1200,margin:40` on the server,
then `rates=` on the request (`/api/ask` body, `/q`, or the URL; [the URL grammar](URLS.md#room-left-in-the-grammar)). Each source is named.
A rate takes at most two decimals, so the rate a link carries is exactly the rate its answer used; an
invalid `INVESTIGATOR_RATES` stops the server at boot.

## Formulas

Three components per priced row, kept apart so that changing one rate changes only the dollars that use it:

| Field | Formula | Applies to |
| --- | --- | --- |
| `paper_usd` | waste sq ft / 1,000 x paper $/MSF | side trim, shear, each dry-end reason, bale, the unexplained remainder (a negative remainder is not priced) |
| `machine_time_usd` | minutes x machine $/hr / 60; for waste board, minutes = MSF / 5.4 | downtime, the speed gap, shear and dry-end rejects (they ran through the machine). Side trim is width, not length: it takes no extra run time |
| `usd` | `paper_usd` + `machine_time_usd` | every priced row |
| `contribution_usd` | MSF not made x margin $/MSF; MSF not made = minutes x 5.4 for time, the waste MSF for shear and rejects | null with `contribution_status` "margin not set" until a margin is set, never $0; shown beside `usd`, never added (it prices the same lost capacity as the shadow price, from the other side) |
| `usd_per_year`, `contribution_usd_per_year` | paper (and the run time of waste board) by volume: x 1.8M MSF / the window's gross MSF; machine time by time: x (1,440 x 257.14 min) / the window's scheduled minutes | the window's loss rate held for a year of one machine at the post's volume |
| `usd_basis` | the formula with its numbers, e.g. "11,218.7 sq ft = 11.219 MSF x $55/MSF paper = $617; 11.219 MSF / 5.4 MSF per min = 2.08 min x $1,200/hr machine time = $41.55; contribution: margin not set" | so a superintendent can redo it on paper; it ends with the per-year factor, e.g. "per year: x 751.41 (1,800,000 MSF a year / 2,395.483 MSF gross)" or "per year: x 771.43 (370,286 scheduled min a year / 480 min)" |

## Where the dollars appear

The fields are flat on the row, beside the quantity, so the live narrative cites them like any other
field and the narrative validator substitutes every dollar from the tool result. They ride on
`get_quality_breakdown`, `get_downtime_breakdown`, `get_waste_breakdown`, `get_downtime_pareto` and
`get_speed_opportunity` (opportunity hours x 60 minutes), `get_speed_variance` (each controllable bucket's lineal feet / the
reference speed; a bucket that ran better is not priced, and the grade mix is schedule-driven, never priced, so the envelope's
`controllable_usd` is the priced buckets and the gap as a whole is not priced; [speed variance](METRICS.md#speed-variance-issue-92)), and on the two money tools:

- `get_shift_dollars(shift)`: where the money went in one shift, ranked by dollars. Gaps: each downtime
  reason (minutes), the speed gap to the required 675 ft/min over the running minutes (480 minus
  downtime; the lineal feet not made / 675 = machine minutes), side trim, shear and each dry-end reason.
  Totals: `total_usd`, `total_paper_usd`, `total_machine_time_usd`, `total_usd_per_year`,
  `total_contribution_usd` (null while the margin is not set). Asked as "where did the money go this shift".
- `get_opportunities(window, shift, lens)`: where to look, the levers ranked in $/yr: the speed of each
  wet-end run under its grade's trailing-year speed (the owner's baseline, [METRICS](METRICS.md#the-owners-targets-and-bands-issue-101);
  minutes = the run's lineal feet short of the baseline in its running minutes / the baseline), side-trim
  points over the trim target (points / 100 x gross, priced as paper: per year this is the post's trim
  formula), each downtime reason (Order change is labelled setup time, Planned maintenance scheduled time),
  and all recorded shear and each dry-end reason (the whole loss). Each lever names the records behind it
  and is marked `control`: `controllable` or `schedule-driven`, with a `control_note` (sequencing,
  temperatures, the order mix and its web widths). It ranks levers and names records; it never prescribes
  a machine adjustment. Asked as "where is the money this month", "what's costing us", "opportunities";
  under the Day control "where is the money" is the shift's own `get_shift_dollars`.
  `lens` reads the same numbers the owner's two ways (issue #101): `supervisor` ranks the controllable
  losses and lists side trim and planned maintenance after, unranked ("where's my biggest opportunity",
  "what went wrong yesterday"); `ownership` is the total cost of waste, side trim first (all of it, with
  its band), then shear, each dry-end reason and the unexplained remainder (`control` `unattributed`) by
  dollars ("what does trim cost", "what's trim costing us"); `all`, the default, ranks every lever. A
  dry-end family's share is read against the yield after trim and shear (`pct_of_yield`), as the dry-end
  band is; shear and trim against gross.

## Money questions

A money word beside another question never replaces it. Downtime, rejects, waste, the Pareto, the
overview and the comparison carry their dollars on the rows, so "which reason is costing us the most
minutes" is the Pareto, priced, and "why was this week so bad in dollars" is the overview (a why, bad or
killed question; the cost of a bare "slow", "what did slow running cost us", is the money answer, whose
speed gap is priced). On the day grain "what did waste cost" is `get_shift_dollars` (side trim, shear and
dry-end together); a named reject keeps the reject breakdown. A comparison, trend, speed against normal,
volume ("how much did we make in dollars", "output per hour in dollars"; a loss of output, "what's costing
us output", and a rate of a money word, "contribution per MSF", are money questions), rollstock, grade,
over/under-run, stops-per-day, summary, matrix, notes or ranking asked "in dollars" is refused with what
to ask instead, not answered as the levers alone.

Known gap: with Week, Month or 4-4-5 selected and no window named in the question, a money question is
answered by the levers (`get_opportunities`) whatever else it names, so "summarize this shift in dollars"
or "what did waste cost" under the Week control gives the levers, while the same question naming its
window ("what did waste cost this week") gives the priced waste breakdown.

## The live paths, and what is not built yet

The live paths: the narrative may cite `usd`, `usd_per_year`, `paper_usd`, `machine_time_usd` and
`contribution_usd` (the formula text and intermediate quantities stay off the packet). No live planner
is offered the two money tools yet. The OpenAI planner loop also sees no dollar fields, so its requests
stay byte for byte main's (its 24,000-byte cap is tight: since #74 main's own request after one waste
breakdown is 24,040 bytes). The Anthropic tool loop and the analyst's plan catalog leave the two tools
out because their prompts still say costs are unavailable; the analyst still sees the dollars on the
priced rows of the tools it plans. Offering the two money tools to the live planners is a prompt change for the live analyst (#23), not part of the card (#31b). Money questions are
answered offline: every one goes to the offline planner first.

## On the card (issue #31b)

Every answer card carries the money of its scope, priced in the browser at the rates in force:

- **The dollar line** under the headline: `≈ $7,762 recorded losses this shift · $5.81M/yr at this rate ·
  contribution: margin not set` (a window: `≈ $12,922 in levers this week · …`; a set margin: `contribution
  $11,891 at $40/MSF, not added`), then one chip per bucket (trim, dry-end, shear, speed, stops, setups),
  largest first. On incomplete data the line and the arithmetic say so first: the dollars are the observed
  records only, and an all-shifts day counts its complete shifts and names the one left out.
- **The opportunities panel** under the charts: horizontal bars of the levers (or a shift's gaps) ranked
  by $/yr at this rate, each bar's dollars, minutes, formula (`usd_basis`) and records on hover, the top
  eight with the rest behind "All", and a Table flip with the same rows.
- **The arithmetic** at the bottom, four lines with the card's own numbers: waste sq ft → MSF → paper $ at
  the paper rate; machine minutes (stops, the speed gap, the run time shear and rejects took) → MSF not
  made at 675 ft/min → $ at the machine rate; side-trim points over the target → $/yr at the post's volume
  (points / 100 × 1.8M MSF × paper); the total, $ and $/yr, and the contribution (or "margin not set").
  Then the rates line with each rate's source and the note that these are synthetic records at assumed
  rates, not a plant's figures.
- **Tiles**: every KPI tile that measures a loss shows its dollars and dollars a year, from a tool's own
  `tile_money` (one entry per loss, named by its `metric` and listing the tiles it sits on in `metrics`), never
  from the tile's rounded value: a window's on `get_period_kpis` / `compare_periods` (`cost_model.period_tiles`),
  a shift's on `get_shift_dollars` (`cost_model.shift_tiles`), the card's money answer, so a shift's tiles get
  their dollars when it lands after the first paint and a KPI answer keeps its #33 size. Each entry is the
  pricing calls of the rows it agrees with:
  a shift's speed is `get_shift_dollars`' gap to 675 ft/min over running time, its dry-end tile that tool's
  reject reasons (so it equals the Dry-end chip), trim its side trim, maintenance and operator their minutes;
  a window's waste tiles are `get_waste_breakdown`'s categories (sq ft to 0.1 on the gross to 0.1; the dry-end
  tile its four reject reasons, each priced as the levers price it, so it equals the Dry-end chip), its stop
  tiles (downtime %, downtime minutes and stops, one entry) the Pareto's reasons (minutes to 0.01 each), with the
  window's area sums in `current_sums`. A window's
  speed tile is not priced (no window tool prices a gap to 675 ft/min; the levers price the gap to each cell's
  best fifth). A count, a volume or a width (setups, MSF, throughput) has no dollars, and a period report's
  tiles (a whole window's row) are priced in its panel, not on the tiles. The value cases `value-kpi-*` (a
  window) and `value-dollars-shift-*` (a shift) hold each tile's dollars by hand.
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
returns, beside its rows and totals, the card's summary (`cost_model.summary`): `buckets` (each chip's total and
$/yr), `area_sqft` and `area_msf` (the area priced as paper), `stop_minutes`, `speed_minutes`, `waste_run_minutes`,
`machine_minutes` and `msf_not_made` (the arithmetic's machine line). `get_shift_dollars` and `get_day_dollars`
also return the trim line (`trim_over_target`): `trim_pct_of_gross`, `trim_target_pct`, `trim_points_over`,
`trim_over_usd` and `trim_over_usd_per_year` (points / 100 × 1.8M MSF × paper), with `trim_over_terms` to
re-derive them at another target. Every priced row carries `share_of_window_pct`, its quantity as a share of
the window's gross or scheduled minutes (the % of Show as). An all-shifts day (or the shifts a question named,
`shifts=1,3`) reads `get_day_dollars(shifts)`: each gap of `get_shift_dollars` summed over those shifts that are
complete (an incomplete one is named in `excluded_shifts`, not added), rounded as the window tools round (0.1 sq
ft, 0.01 min) and priced once on their gross (to 0.1) and 480 minutes a complete shift, so its $/yr is the day's
own and a Day window's tiles price the same quantities as its chips. `shifts` names each shift and its coverage;
each shift's own total is its `get_shift_dollars` step beside it, which the arithmetic quotes, re-priced with the
rest. The value cases `value-dollars-shift-*`, `value-dollars-day-0914*` and
`value-dollars-opportunities-*` hold all of these by hand (`value-dollars-day-0914-shifts-1-3` two shifts,
`value-dollars-day-0914-corrupt` the incomplete shift left out). None of them reaches a model: the narrative packet
and the OpenAI compaction drop them, and `get_day_dollars` is not in a model's catalog.

How the card prices: every priced row carries `price_terms`, the pricing call itself, rate-free and
unrounded (`kind`, `quantity`, the window's `gross_sqft` and scheduled `minutes`; the trim lever also
`trim_pct`, since its quantity, the points over the target, is itself a rate). `web/money.js` makes the
same call with the same products, the same rounding (Python's round half to even) and the same text, then
ranks the money tools' rows and redoes their totals and summary, the trim line and each tile, and rewrites the answer's prose from the re-priced
rows: each dollar figure becomes the same field re-priced, the rates, formula and contribution lines are
written again, a ranked money list is put in its new order, and an all-shifts money answer is written
about the shift that lost the most at the new rates. The levers are ranked the way their lens ranks them (issue
#101): under the supervisor lens the controllable levers are ranked and the schedule-driven ones (side trim,
planned maintenance) follow them with no rank, however large a rate makes them; under the ownership lens all of side
trim stays first, however small; the controllable totals the supervisor headline states are re-derived too (a line
that names controllable losses or buckets takes them, and no other line does, so a window whose controllable total
reads as its all-lever total keeps each figure its own), and the
card's panel orders its bars by the lens before the unit. The supervisor card lists every controllable lever (five
beside the headline, the others under a label), so a lever a rate moves into the top five is written there. The
speed variance's controllable dollars are re-derived the same way; its grade mix is never priced. "What came back, per query" shows each query's result at the
rates in force too (what the page's own address returns from the server), so a re-numbered row citation names the
row it shows. An analyst's interpretation is not rewritten (a
model's sentence moved to other figures could claim what no row says, or keep a comparison the new figures
reverse): at other rates it says which rates it was written at, and the evidence under it is re-priced. So
the page after an edit and the permalink it writes show the same figures, prose included: `tests/test_money_ui.py` checks it over twenty-five answers
(the two lenses on a month and on a shift's week, a supervisor week with no planned maintenance and trim under its
target, and a speed variance whose mix is schedule-driven, among them) at six sets of rates (`tests/money_cli.cjs parity`), and the value case
`value-dollars-shift-0914-shift-2-client` holds the server's `get_shift_dollars` at other rates to the
client's figures (`evals/client_money.json`, written by `evals/make_client_money.py`). `price_terms`
never reaches a model (the narrative packet and the OpenAI compaction drop it). A card's money is its
own `get_day_dollars`, or its only `get_shift_dollars` / `get_opportunities`, when it asked one (an analyst's
plan over two shifts is not added up in the browser), else the money answer of the scope its answer ran over,
never the rail's when the answer says otherwise: its own address, else its first windowed query ("How did
September go versus August?" under Day is priced over September), else its day queries' `production_day` and
`shift` (an analyst's card has no address of its own; several shifts are the day's all shifts), one GET per scope shared by its cards
(`measure=dollars` on the Day grain, `measure=money` on a window), sent after the card's first paint so it
never slows the offline answer, and counted as a round by the server; a reload replays the thread within its share of the rate
limit counting those GETs too. `/api/config` carries the rates, their sources and the constants
(`cost_model.client_config`).
