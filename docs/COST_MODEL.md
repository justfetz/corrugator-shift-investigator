# Cost model: data to dollars

How the investigator turns the records' quantities into dollars (issue #31): the rates, where each one comes from, and the formula behind every priced field. The rates and assumptions live in `src/investigator/rates.json`; the arithmetic is `src/investigator/cost_model.py`. What a live model round costs to run is a different question, answered in [Anthropic in the hosted demo](ANTHROPIC_DEMO.md#cost-per-round).

## Rates and their sources

The records hold no costs. `cost_model.py` prices the tools' quantities at assumed rates from
`src/investigator/rates.json`; the defaults are the owner's post
([Where the money hides in a corrugator's numbers](https://justfetz.github.io/posts/corrugator-dashboard.html)).
Every dollar is synthetic data at an assumed rate, never a real plant's figure, and every answer that
carries dollars names its rates in a provenance line ("paper $55/MSF (post), machine time $1,200/hr
(post), contribution margin not set (assumption: owner to set), trim target 3.25% (process rule)").

| Rate or assumption | Default | Source | `rates=` key |
| --- | --- | --- | --- |
| Paper cost | $55 / MSF | post | `paper` |
| Machine time (bottleneck shadow price) | $1,200 / hr | post ("more than $1,200 an hour") | `hour` |
| Board contribution margin | not set | not in the post; owner to set | `margin` |
| Side-trim target | 3.25 % of gross | the process rule (setup trim flag) | `trim` |
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
`get_speed_opportunity` (opportunity hours x 60 minutes), and on the two money tools:

- `get_shift_dollars(shift)`: where the money went in one shift, ranked by dollars. Gaps: each downtime
  reason (minutes), the speed gap to the required 675 ft/min over the running minutes (480 minus
  downtime; the lineal feet not made / 675 = machine minutes), side trim, shear and each dry-end reason.
  Totals: `total_usd`, `total_paper_usd`, `total_machine_time_usd`, `total_usd_per_year`,
  `total_contribution_usd` (null while the margin is not set). Asked as "where did the money go this shift".
- `get_opportunities(window, shift)`: where to look, the levers ranked in $/yr: the speed gap to each
  cell's best-fifth speed (`get_speed_opportunity`'s total hours), side-trim points over the trim target
  (points / 100 x gross, priced as paper: per year this is the post's trim formula), each downtime reason
  (Order change is labelled setup time), and all recorded shear and each dry-end reason (no benchmark for
  those, so the whole loss). Each lever names the records behind it. It ranks levers and names records; it
  never prescribes a machine adjustment. Asked as "where is the money this month", "what's costing us",
  "opportunities"; under the Day control "where is the money" is the shift's own `get_shift_dollars`.

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
priced rows of the tools it plans. #23 / #31b retune the prompts and offer them. Money questions are
answered offline: every one goes to the offline planner first.

Not yet on the card: the dollar line, dollars on tiles and bars, the units toggle and the editable rates
panel are #31b (`web/`). The tools here give that card the same numbers the goldens check.
