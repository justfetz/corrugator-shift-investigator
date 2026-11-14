# URL-addressable questions: the grammar

Every offline view of the workbench is one URL, the way a Datasette table view is (issue #20). The
**where** is the path, the **what** is the query, and a typed question is a shortcut that resolves to a
canonical URL. Paste the URL into another browser and you get the same answer; the back button, a
bookmark and a shared link all work, because the server keeps no session for this path. Live model
rounds stay on `POST /api/ask` with their session, since a model's choices are not reproducible from an
address.

The code is `src/investigator/urls.py` (`canonical_url` builds the address of a parsed request,
`resolve_url` is its inverse) and the routes in `server.py`. The names here are the public API: keep
them short and stable.

## The address

```
/corr/<machine>/<grain>/<YYYY-MM-DD>[/shift/<n>]?measure=<what>&...
```

| Part | Values | Meaning |
| --- | --- | --- |
| `corr/<machine>` | `1` today (`RecordStore.machine_id` is `CORR-1`; the slug drops the `CORR-` prefix) | which corrugator; any other slug is a 404 |
| `<grain>` | `day`, `week`, `month`, `period` | the **Period control**: `period` is the 4-4-5 accounting period (`accounting` in the UI, `fiscal` in the tools) |
| `<YYYY-MM-DD>` | a Monday–Friday production day of the record store | the **Day control**; a weekend or out-of-range date is refused with the calendar reason, like `/api/ask` |
| `/shift/<n>` | `1` (07–15), `2` (15–23), `3` (23–07); omitted | the **Shift control**: one shift, or all three (the production-day comparison) when omitted |

The path is exactly the controls, so a URL reproduces the page a visitor saw, including the Period
control they had set. A question's *own* window, when it names one ("this week" under Day, "August"),
goes in the query as `window=`.

### The question's own scope (issue #105)

A typed question that names a scope the controls do not set is answered in the scope it names, never refused
for disagreeing with them, and its address names that scope. `agent.question_scope` reads it before the router
parses the question (`agent.route`), on `/q`, on `POST /api/ask` and for the analyst alike:

- **One named shift** under Selected shift only ("first shift", "1st shift", "shift 1") becomes the selected
  shift: "Show shift 1" with shift 2 selected is `/corr/1/day/2026-09-14/shift/1?measure=overview`. A
  comparison (any of compare, versus, compared, against, than, relative to: "how did we do compared with first
  shift", "compare shift 1 with shift 3") or several named shifts keep the refusal that asks for the
  production-day scope. Under all shifts a named shift narrows with `shifts=` as before.
- **A relative window** moves the path to it: `yesterday` is the production day before the selected one (Friday
  from a Monday), `last week`, `last month` and `last period` (also `previous`, `prior`) the week, calendar month
  and 4-4-5 period before the selected day's. The path takes that window's grain and its first production day,
  since the selected day is not in it: "How much donwtime on 1st shift last week?" with shift 2 and Day on Mon 14
  Sep is `/corr/1/week/2026-09-07/shift/1?measure=review` (downtime by reason over a window is the owner's review, issue #101). A window that contains the selected day keeps the
  `window=` form above.
- A relative window that is the **base of a comparison** ("our speed vs last week", "compared with last week",
  "over / since / from last week", "compare with yesterday") is not the window asked about: the KPI tiles or the
  speed variance carry the previous window, or the router's refusal stands. Neither is one in a question that
  names another window too ("this week", a month, a date), nor "the last week", the trailing week. A relative
  window with no production day in the records is refused with the calendar reason.
- Follow-ups share the production day (`after=`, below): on `/q`, a prior view of another day than the
  question's is left off the chain, as a changed control starts a new conversation. The browser keeps its
  conversation when an answer lands on another day, and the session of `POST /api/ask` stays on the controls: a
  same-day move is a follow-up, another day's answer is not one.

Measure words are read through small typos: a word one edit from exactly one measure word (insert, delete,
substitute, or swap two neighbours; same first letter) reads as it, so "donwtime", "dowtime", "wast" and "spead"
route. Everyday English one edit from a measure ("throughout", "qualify", "steps", "spend") is left alone
(`real_words.py`, generated from word frequencies by `evals/make_real_words.py`), and so are words near no
measure: a nonsense measure is still refused.

## The question

`measure=` names the plan the offline router would run. The day-shaped measures run the nine daily
tools under the `day` grain and take their period form under a ranged grain (`agent.period_plan`); the
store measures run one record-store tool over a window.

| `measure=` | Under `day` | Under `week`, `month`, `period` |
| --- | --- | --- |
| `overview` | KPIs, downtime by `by=`, wet-end speed, notes ("why was this shift slow"); with `window=` the period form over that window ("why was this shift slow this week", "were we slow in August") | KPI tiles against the previous window, downtime Pareto by `by=`, waste breakdown |
| `summary` | `get_shift_overview` in one call, three charts | the same three store tools as `overview` |
| `downtime` | KPIs and downtime by `by=` (`kind=` narrows); under all shifts by reason, an alias of `review` (issue #101) | by reason, an alias of `review`; by `by=place` or `kind`, an alias of `pareto` |
| `fpm` | KPIs and wet-end speed against grade targets; with `window=` the period form over that window ("average speed this week", "our speed vs last week"); with `by=grade` speed without stops against each grade target, charted in ft/min ("fpm by grade") | KPI tiles and speed by grade against target; `by=grade` the speed-by-grade table alone |
| `quality` | KPIs and dry-end rejects by reason | the waste breakdown alone: an alias of `waste` |
| `compare` | KPIs, downtime and speed per shift (`shifts=` or all three) | KPI tiles per shift: an alias of `kpis` with `shifts=` |
| `setups`, `orders`, `notes`, `rank` | the setup matrix, the order matrix, the notes, the rankings (`rank=` picks the metrics) | refused: "Switch Period to Day", as the question is |
| `kpis` | `get_period_kpis` for the window with the previous window; `compare=` changes the base | the same |
| `pareto` | `get_downtime_pareto` by `by=` in `unit=` minutes or count | the same |
| `waste`, `stops`, `grade`, `overrun`, `opportunity` | the waste breakdown, stops per production day, totals by grade, over/under-run by knife, the speed opportunity by `by=width|crew` | the same |
| `rollstock` | `get_rollstock`: paper consumed from the wet-end records as lineal feet and MSF per grade and web width, paper runs and feet per run, the previous window beside ("how much paper did we consume this month") | the same |
| `dollars` | `get_shift_dollars` per shift: where the money went, ranked by dollars; over more than one shift (all shifts, or `shifts=`) also `get_day_dollars` for those shifts, the total the card states (#31b) | the money levers: normalizes to `money` |
| `money` | `get_opportunities` over the window: the levers ranked in $/yr, each controllable or schedule-driven; `lens=` reads them the owner's way | the same |
| `speedvar` | `get_speed_variance` over the window: the speed gap against each grade's trailing-year baseline split into stops, setups, running under baseline and grade mix, with planned maintenance and external stops as buckets of their own when the window holds them ("top 3 contributors to speed variation", "what drove speed", "speed loss breakdown"); `against=` changes the reference; `shifts=` lists one decomposition per shift, or with `against=shift` the shift explained and the one it is measured against | the same |
| `review` | `get_downtime_review` (issue #101): maintenance and operator downtime against their bands, every reason ranked by % of scheduled minutes with stops and minutes per stop, a power outage apart as external, planned maintenance (scheduled) last as scheduled time, and the flags with their supervisor slot and recurrence; the all-shifts downtime of a production day ("top downtime reasons yesterday", "show downtime by reason" under all shifts) | the same over the window |
| `trend` | `get_trend` of `metric=` over `weeks=` (default 8) production weeks ending with the day's week | the same |
| `semantic` | one query of [the semantic model](../src/investigator/semantic_model.json) (issue #117): `m=` names its measure, `by=` up to two of its dimensions (`by=grade,shift`), and each other dimension is a filter field taking one value (`kind=Maintenance`, `place=Double backer`, `reject_family=WARP`, `grade=26-C`, `lane=2`, `web_width=100`); `compare=prev` puts the previous window of the same grain beside every row (a slice it recorded nothing for is a zero; a previous window outside the records says so, and the change is stated only when both windows are complete; refused beside a time split, `by=day` for one, whose rows have none in common with the window before); over the Period control's window or `window=`, for one shift (the path's, or `shifts=3`) or all. The answer leads with the asked measure over the whole window as the bold figure, then one finding per row of the split, each citing its row; the table is the rows, each with the ids of the records it sums. The router does not ask it yet (phase 2) | the same |
| `report` | refused: a day is investigated with a question | the deterministic period report (heatmap, daily matrix, recurring downtime, notes) |

Modifiers, each refused when it does not apply to the measure:

| Field | Values | Default |
| --- | --- | --- |
| `window=` | `week`, `month`, `period` (that window containing the day), or a month as `YYYY-MM` (anchored on its first day, inside the dataset or not: a month outside the data's window answers "unavailable" with the tool's reason, never zero) | the Period control's window on the day |
| `by=` | `reason`, `place`, `kind` for downtime and the Pareto; `width`, `crew` for the opportunity; `grade` for `fpm` | `reason`; `width`; none |
| `kind=` | `maintenance`, `operator`, `scheduled` (`pm`, v1's code, is an alias) (day `overview`, `downtime`, `compare`; refused on the period form, which groups the Pareto with `by=kind`) | both |
| `unit=` | `minutes`, `count` (Pareto) | `minutes` |
| `metric=`, `weeks=` | a `TREND_METRICS` name; 1–26 | required; `8` |
| `compare=` | `prev`, `none`, a month `YYYY-MM`, or a date `YYYY-MM-DD` of the same grain (then `compare_periods`); on other measures `prev` alone, as an alias (below) | `prev` |
| `against=` | `speedvar` only: `baseline` (each grade's trailing-year baseline at the usual grade mix, issue #101; `target`, its old name, redirects to it), `previous` (the previous window of the same grain), `shift` (needs `shifts=` with two shifts, the one explained first: `shifts=3,1`; refused under one shift, which cannot read another) | `baseline` |
| `shifts=` | distinct shifts, e.g. `2,1`: the shifts a question named under the all-shifts scope (the order is the question's); a store measure takes one, except `kpis`, and `pareto` and `waste` under a ranged grain, which run once per shift, and `speedvar`, which runs once per shift (or, with `against=shift`, takes exactly two) | all three for day plans, all shifts together for store tools |
| `rank=` | `speed`, `downtime`, `quality` | all three |
| `lens=` | `supervisor` (the controllable losses ranked; side trim and planned maintenance schedule-driven, then a power outage external, after), `ownership` (the total cost of waste, side trim first, every waste line priced once, the dry-end kinds under one tag-scrap subtotal), for `money` (issue #101) | every lever ranked |
| `want=` | `chart`, `profit`: objectives the question stated that the tools may not meet, so the answer's status and disclaimer survive the redirect | none |
| `m=` | `semantic` only: a measure of the semantic model, by its name or one of its synonyms (`m=scrap` is `reject_sqft`); [every measure](../src/investigator/semantic_model.json) | required |
| `crew=`, `grade=`, `flute=`, `wet_end=`, `web_width=`, `place=`, `reason=`, `reject_family=`, `reject_reason=`, `lane=`, `order=`, `customer=` (and `kind=`) | `semantic` only: one value of that dimension, which the records must hold (any case, or a synonym in the plant's words: `place=DB` is `Double backer`, `reason=glue` is `Glue / starch`, `kind=pm` the planned maintenance kind); a dimension the measure's records do not carry is refused with the ones they do, and a value the records do not hold with the nearest values they do | none |
| `rates=` | the visitor's rates, `paper:<$/MSF>`, `hour:<$/hr>`, `margin:<$/MSF>`, `trim:<% target>`, comma-separated in that order, at most two decimals each, ASCII digits only ([the cost model](COST_MODEL.md#overrides)); every measure takes it, and the answer's provenance line names each rate as "yours". The page writes it (#31b): a rate changed in the rail's rates panel (**Change rates**) re-prices the page in the browser and is written into the address bar (replaced, not pushed), into every question asked after it and into the canned questions' links; a prior view (`after=`) never carries it. The period report has no dollars of its own but keeps it, since its card carries the money of its window | the server's rates (`INVESTIGATOR_RATES`, else the post's) |
| `corrupt=1` | the Advanced toggle that injects the bad trim record | off |
| `after=` | the earlier views of this conversation, each a canonical address without its own chain, at most 8; the agent keeps its `max_turns`. A period report in the chain is kept on the address but, as on the session path, enters no history | none |
| `format=` | `json`, `pdf` | the page |

One state has one spelling: the fields come in the order above and defaults are omitted. `resolve_url`
is tolerant of a hand-typed alias (below) and strict about everything else: an unknown field, a repeated
field, a modifier the measure does not read, or a shift outside the selected scope is a 400 with the reason.

## Aliases

An address that reads naturally but is not the canonical spelling still answers: the server **302**s it to
the canonical address (as `/q` does), keeping the representation (`.json`, `format=json`, `format=pdf`), so
the address bar ends on the one spelling (issue #70). The redirect runs no tool and is not a round. The
server redirects only to an address that resolves to itself and runs the same plan over the same shifts, so
nothing redirects twice or to a refusal: a ranged-grain `dollars` split over named shifts (one set of
levers per shift) has no resolvable canonical spelling yet (`measure=money&shifts=1,3` is refused, since
the money levers are not split by shift), so it answers at its own address as before. The rest of the query (`shifts=`, `window=`,
`by=`, `unit=`, `want=`, `corrupt=`, `after=`) carries over. `compare=prev` asks for the previous window of the
same grain, which these answers carry already or which their store form carries.

| You may type | It becomes |
| --- | --- |
| `measure=waste&by=grade` | `measure=grade` (volume, lineal, speed and waste by grade, the previous window's volume beside) |
| `measure=waste&by=setup` (Day) | `measure=setups` (the setup matrix) |
| `compare=prev` on `kpis`, `pareto`, `waste`, `grade`, `rollstock` | the same address without it: `prev` is the default, the previous window is beside |
| `measure=speedvar&compare=prev` | `measure=speedvar&against=previous` (the speed variance against the previous window of the same grain); `against=baseline` (or its old name `against=target`) spelled out is dropped as the default |
| `compare=prev` on `overview`, `summary`, `fpm` under a ranged grain, or `overview`, `fpm` with `window=` | the same address without it: the period form leads with the KPI tiles against the previous window |
| `measure=downtime`, `quality`, `compare` under a ranged grain (with or without `compare=prev`) | `measure=review` (by reason, issue #101; it carries the previous window, so `compare=prev` lands there too; `by=place` or `kind`, `measure=pareto`), `waste`, `kpis` |
| `measure=dollars` under a ranged grain | `measure=money` (the levers of the window in $/yr; under Day, `dollars` is the shift's own dollars; not with several `shifts=`, as above) |
| `measure=downtime&compare=prev` under Day | `measure=pareto` (`by=` kept): downtime against the previous production day |
| `measure=quality&compare=prev` under Day | `measure=waste` |
| `measure=compare&compare=prev` under Day | `measure=kpis` (`shifts=` kept: the tiles per shift) |
| `measure=overview`, `summary` or `fpm` with `compare=prev` under Day | `measure=kpis`: the day's KPI tiles against the previous production day |
| `kind=pm` | `kind=scheduled`: the planned stop's kind, which v1 recorded as `PM` (owner, 2026-11-12) |
| a default spelled out (`by=reason`, `unit=minutes`, `weeks=8`) or the fields in another order | the canonical spelling |
| `measure=semantic` with a synonym or another case: `m=scrap&by=defect`, `kind=maint`, `place=db`, `web_width=100.0` | the model's own names and the records' own values: `m=reject_sqft&by=reject_reason`, `kind=Maintenance`, `place=Double+backer`, `web_width=100` (issue #117) |

Issue #20's examples: `/corr/1/day/2026-09-14/shift/2?measure=downtime&by=reason&compare=prev` →
`/corr/1/day/2026-09-14/shift/2?measure=pareto`; `/corr/1/period/2026-09-14/shift/3?measure=waste&by=grade` →
`/corr/1/period/2026-09-14/shift/3?measure=grade`; `…/month/2026-09-14?measure=rollstock&compare=prev` →
`…/month/2026-09-14?measure=rollstock` (the path takes a production day, not `2026-09`).

An alias that cannot be normalized is a 400 naming the field: `compare=prev` on a measure that carries no
previous window (`stops`, `overrun`, `opportunity`, `trend`, `fpm&by=grade`, `setups`, `orders`, `notes`,
`rank`, `report`); `compare=none` or a comparison base on anything but `kpis`; `kind=` with `downtime` or
`compare` and `compare=prev` under Day (the Pareto groups `by=kind` instead), or `by=`/`kind=` where the target
is `kpis`; `by=` on `waste` other than `grade` or `setup`; `by=setup` under a ranged grain (the setup matrix is
per production day). A refusal on the target names the alias too, as in "measure=pareto runs for one shift …
(measure=downtime&compare=prev becomes measure=pareto)". The page itself still loads at a refused address and
shows the refusal of its `.json`.

## Examples

| Question, in the controls | Address |
| --- | --- |
| "Why was this shift slow? Chart downtime by reason." — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=overview` |
| "Which equipment caused the most downtime?" | `/corr/1/day/2026-09-14/shift/2?measure=downtime&by=place` |
| "Which downtime reason cost the most minutes this week?" — Day | `/corr/1/day/2026-09-14/shift/2?measure=pareto&window=week` |
| the same question — Week | `/corr/1/week/2026-09-14/shift/2?measure=pareto` |
| "How much donwtime on 1st shift last week?" — Day, shift 2 (the question's own shift and window, issue #105) | `/corr/1/week/2026-09-07/shift/1?measure=review` |
| "How much downtime did third shift have yesterday?" — Week, shift 1 | `/corr/1/day/2026-09-11/shift/3?measure=downtime` |
| "How did September go versus August?" — Day, all shifts | `/corr/1/day/2026-09-14?measure=kpis&window=2026-09&compare=2026-08` |
| "fpm by grade" — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=fpm&by=grade` |
| "Average speed this week" — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=fpm&window=week` |
| "Why was this shift slow this week?" — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=overview&window=week` |
| "Trend of lineal per setup over six weeks" | `/corr/1/day/2026-09-14?measure=trend&metric=lineal_per_setup&weeks=6` |
| "Compare that with first shift", after "Why was second shift slow?" — all shifts | `/corr/1/day/2026-09-14?measure=compare&shifts=2,1&after=/corr/1/day/2026-09-14?measure=overview%26shifts=2` |
| "Where did the money go this shift?" — Day, shift 2, paper at $60 | `/corr/1/day/2026-09-14/shift/2?measure=dollars&rates=paper:60` |
| "Where is the money this month?" — Day, all shifts | `/corr/1/day/2026-09-14?measure=money&window=month` |
| "What were the top 3 contributors to speed variations?" — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=speedvar` |
| "What drove speed this week versus last week?" — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=speedvar&window=week&against=previous` |
| "Why did speed differ on shift 3 versus shift 1 this week?" — Day, all shifts | `/corr/1/day/2026-09-14?measure=speedvar&window=week&against=shift&shifts=3,1` |
| "What were the top downtime reasons yesterday?" — Day 2026-09-29, all shifts | `/corr/1/day/2026-09-28?measure=review` |
| "Where's my biggest opportunity this month?" — Day, all shifts | `/corr/1/day/2026-09-14?measure=money&window=month&lens=supervisor` |
| "What's trim costing us this month?" — Day, all shifts | `/corr/1/day/2026-09-14?measure=money&window=month&lens=ownership` |
| the Period report button — Month, shift 2 | `/corr/1/month/2026-09-14/shift/2?measure=report` |
| the scope's own KPI tiles (what the page primes on load) | `/corr/1/week/2026-09-14.json?measure=kpis` |
| unplanned maintenance minutes by board grade this week (no question asks it yet: the semantic model, issue #117) — Week, all shifts | `/corr/1/week/2026-09-14?measure=semantic&m=downtime_minutes&by=grade&kind=Maintenance` |
| dry-end rejects by order on one shift — Day, shift 2 | `/corr/1/day/2026-09-14/shift/2?measure=semantic&m=reject_sqft&by=order` |

Every routing case in `evals/golden.json` records its address as `expect.url`; `python3 evals/run.py`
resolves each question through the server and fails the case when it lands elsewhere. A case may ask by its address
instead of a question (`url`, the `measure=semantic` cases): the address is resolved and answered in process as the
server answers it, then fetched over GET, where a hand-typed spelling must redirect to `expect.url`.

## The routes

| Request | Answer |
| --- | --- |
| `GET /q?day=&shift=&scope=&period=&corrupt=&q=&after=` | the `/api/ask` fields of an offline question (`period` is `day|week|month|accounting`); validated like `/api/ask`, the question parsed by the offline router, then **302** to the canonical URL. A refusal of the router is a 400 with the same text `/api/ask` would send. Every example chip is a link of this form. |
| `GET /corr/…?measure=…` | the app page; `app.js` reads the address, sets the controls and fetches the `.json`. The bare scope address (no `measure`) is the page with the controls set. An alias, in any representation below, is a **302** to the canonical address in the same representation (see Aliases). |
| `GET /corr/….json?measure=…`, `…&format=json`, or `Accept: application/json` | the JSON `/api/ask` returns for the same state, plus `url` (the page address, with the `after=` chain) and no `session_id`; the bare scope address is a 400 asking for `measure=`. `Server-Timing: app;dur=<ms>` is the server's own time. |
| `GET /corr/…?measure=…&format=pdf` | the PDF of that answer, rendered on request from the same state. No report cache, so any instance serves any PDF; `POST /api/report` remains for live answers, which have no address. |

While the hosted demo keeps its question log (issue #125, [the fields](DATA.md#the-question-log)), the page sends `/q` two
random ids as headers, `X-Question-Line` and `X-Question-Session`, which ride its redirect to the `.json` answer. They
change nothing in the answer or the address; the server only logs the question and its answer under them.

The URL path is offline only: no model, no key, no token (a GET changes nothing and carries nothing
secret). It has the other guards of `/api/ask`: the loopback `Host` rule or the public mode's proxy,
the per-process request window and the per-IP round limit when `DEMO_RATE_LIMIT_PER_MIN` is set (a
`.json` or `format=pdf` answer is a round, since it recomputes the answer; the page and the `/q`
redirect are not). Because a GET carries no token, a third-party page can make a visitor's browser
fetch `.json` addresses and spend that visitor's own rounds; it reaches no key, no model and no shared
cap. It
takes its own lock (`Workbench.read_lock`) and keeps its own three-day analysis cache, so a URL answer
never waits behind a live round that holds `Workbench.lock`. `POST /api/ask` answers in offline mode
also carry `url`, the question's own address without a conversation chain.

## What the browser does

`app.js` sends an offline question as `GET /q…` with `Accept: application/json`, follows the redirect,
renders the answer, remembers it under its `url`, and pushes that URL with `history.pushState`; a
control change pushes the bare scope address; the Period report is `measure=report` of the scope. On
`popstate`, the controls follow the address and a view this page has shown is re-rendered from memory
without a request; any other address is fetched as `.json`. Opening a canonical URL in a fresh browser
does the same on load. Follow-up context is the `after=` chain the page builds from its earlier views
(at most `max_turns − 1`), so offline needs no `session_id`; the Download PDF button of an addressed
answer is a link to `…&format=pdf`. Live mode keeps the session and the POST. On a reload of the tab the
thread above the addressed card comes back the same way (issue #86): each card is kept in `sessionStorage` as
its own address (an analyst's card as the offline address it was asked from) and fetched again as JSON;
only same-origin `/corr/…` and `/q?…` paths are replayed. Each replay counts against the request limits like any
URL answer, so a reload fetches only the newest few cards (a quarter of the per-visitor limit, at most 8) and
the older ones load when opened.

## Room left in the grammar

`rates=` (#31) carries the assumptions an answer was priced with; an unknown rate key, a repeated one or a value outside 0 < v (at most 100,000, or 100 for the trim target) is a 400 with the reason. `/q` takes `rates=` too and keeps it on the redirect.
