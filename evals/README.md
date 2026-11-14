# Golden evals

`run.py` runs the cases in `golden.json` and `golden_values.json` and prints one
score table. It is the answer to "how do you know the agent is right?" for this
project, and `golden_values.json` is the answer to "how do we make the golden
sets?": with synthetic data we own the truth, so expected numbers are computed a
second way and never typed.

```
python3 evals/run.py                              # offline cases, plus replay of any recorded live fixture
python3 evals/run.py --live                       # also run the live variants against the one provider whose key is set
python3 evals/run.py --live --provider anthropic  # ANTHROPIC_API_KEY (or --provider openai with OPENAI_API_KEY)
python3 evals/run.py --only why-slow              # one case
python3 evals/make_value_goldens.py               # regenerate golden_values.json after an intended generator change
```

A case the owner set carries `reviewed_by`: `owner-pending` until the owner has read
it on the preview, then `owner`. Issue #101's cases, including the power-outage, planned-maintenance and money-card
cases added with the owner's decisions on PR #111, are `owner`: the owner read and approved them on 2026-11-12.

`tests/test_evals.py` runs the same offline cases and recorded fixtures in CI,
so a change that breaks a golden case fails the build; `tests/test_goldens_values.py`
fails the build when the committed value goldens are not what the generator produces.

## Seven case types

| Type | What it proves | How it is checked |
| --- | --- | --- |
| routing | the planner picked the right tools with the right arguments | required tool calls (argument subset), optional allow-list, call count, chart and table counts, chart units in order (`chart_units`), selected shifts; `expect.url`, the canonical address the question resolves to (`GET /q` must redirect there) |
| faithfulness | every number in the answer came out of a tool row | each numeric token in the prose must equal some tool-row value under one of the application's formats (`.1f`, `.2f`, `,.0f`, `g`, …), a row count, a below-target count, or a stated rule constant; a refused call's own message, quoted verbatim, is tool output too |
| grounding | findings cite evidence that exists and nothing is silently dropped | narrative citations resolve to `(result_id, row, field)` in the trace; non-limitation paragraphs cite something; a requested chart that could not be drawn is disclosed |
| refusal | out-of-scope requests are refused rather than answered | expected `ValueError` text, or a `partial`/`unavailable` status with the disclaimer, or forbidden phrases absent; a window before the dataset is unavailable, never zero; a weekend day is refused with the calendar reason |
| value | a tool's numbers equal a second, plain computation over the records | one tool call through the real agent loop; `expect.values` maps a row selector to fields to `[expected, tolerance]`; `row_count`, `coverage` and reported `excluded_records` alongside |
| investigation | a multi-turn question finds a seeded story in the evidence, reports its numbers, and asserts no cause the evidence lacks | evidence rules (tool, argument subset, window covering the story period, a row and a comparison), the found numbers present in the prose, forbidden causal patterns absent |
| data_quality | a corrupt record is flagged, excluded, reported, never averaged in | coverage `incomplete` for its shift only, the excluded record reported with its raw value, the impossible number absent from the answer, rankings withheld; the matching value case pins the remaining sums |

## The URL path

Every offline view is a URL (issue #20, `docs/URLS.md`). After the in-process run, `run_case` sends each
offline question to `GET /q?…&q=` on a loopback server started once per run: the redirect must land on
`expect.url` when the case records one (every routing case does; the value is the last turn's address
without its `after=` chain), a refusal must come back as the same text (`raises`), and the answer
served at the canonical address is scored with the same invariants and expectations as the in-process
one, within the case's own `max_ms` on the server's time (`Server-Timing`, so the HTTP round trip is
outside the clock). Failures are prefixed `URL:`; the table's note carries `URL <ms> ms` and the
summary prints the server p50 and p95 over GET. Value cases have no question and are not sent; a
live row checks only that its question resolves to its `expect.url`.

A question that names its own shift or a relative window (issue #105: "How much donwtime on 1st shift last
week?" on the shift 2 / Day controls) runs in the scope it names on both paths: `/q` moves it, and in-process
the runner reads it with `agent.question_scope` and answers on that day's records. The tools-inside-the-scope
invariant takes its scope from what the case states, never from the answer: the shift of its `expect.url` for the
last turn (a scripted analyst plan, which has no URL, states `expect.shifts`), else the case's controls, so a
question moved to the wrong shift fails even where nothing else pins it.

## Global invariants

Every case, whatever its type, also checks:

- offline runs report zero model calls and $0;
- every executed tool is in `TOOL_SCHEMAS`, and every tool call stays inside the selected shift scope;
- chart rows equal the rows of the result they cite;
- tables shown to the user are complete (row count equals an independent tool call);
- narrative numbers and citations are faithful, as above (an evidence reference `(query N · row M)` and a
  where-to-look question `→ “…”` state no measurement and are not read as numbers);
- every number on a cited line is a value of the row it cites (issue #75, `unprovenanced`): a section with a `**figure**`
  carries exactly one, and one evidence reference that resolves to a successful step of the trace and a row of
  its result (`detail M`, `weekday M`, `flag M`: its sub-reason, weekday and flag lists); every number in the figure renders a
  value of that row, or, with no row named, a count of the result's rows, a below-target count or a total of its
  envelope; every other number on the line is one of those, a count of the result's rows or a rule constant. A figure
  moved to the wrong row, changed by a digit, stated beside a row it does not cite, or citing a chart step fails the case.

Two more hold "exact over summary" (issue #113), stated in `run.py` on their own, not imported from the router:

- **the catch-all guard**: a question that names a countable noun (setups, paper changes, order changes, stops,
  splices, step-ups, rolls, sheets, boards, orders, customers, a per-setup or per-paper-change ratio), a stop reason or
  place, a board grade, or asks for a unit (minutes, hours, feet, MSF, square feet, tons, percent, dollars or cost),
  or asks "how many" of anything, never resolves to the KPI summary, `measure=kpis`; and a quantity asked ("how many",
  "how much", "how long", "how often", a unit) never resolves to the overview, the summary or the shift comparison
  (`measure=compare`, which opens with speed) either, unless it asks why, about speed or to summarize (checked on
  the `GET /q` redirect of every case, live ones included). The KPI summary is for "how did we do". One quantity
  (`measure=quantity`) never answers a breakdown, a grouping ("at each place", "by reason"), a comparison of shifts or a
  shift named by its rank ("the slowest shift"), and a quantity measure (`measure=quantity`, or `measure=stops`, the stops
  of each production day) is never answered for the selected shift when the question names no shift.
  The plain-quantity gate states the general rule on the URL (round 5 of the review of PR 143): once the question loses
  what its `measure=quantity` URL consumed (the ask word "how long", the ratio's "per" and the average it is, a waste
  line's percent, the words of `reason=`, `place=` or `waste=`, the window, a window compared with the one before, and
  the shift), no word of a closed vocabulary may be left: a ranking (which, most, fewest, best, worst, rank, top, most
  common, compare, versus, better …), a grouping (by, each, every, per, a day, daily, across, type, category, splicer,
  knife, day …), a statistic (average, mean, median, typical, maximum, minimum, longest, shortest), a subset (long, short,
  longer than, over N) or a percent (percent, percentage, share, %). And a ratio kept for all stops or for the line
  (minutes per stop, each day's downtime, feet per setup), or the stops of each production day (`measure=stops`), never
  answers a question that names one stop reason, place or kind, and an average stop is its length only (its count, rate,
  cost or reason is never minutes per stop).
- **the unit asked opens the answer**: a count or unit question ("how many setups", "number of paper changes", "how
  many minutes of …", "minutes per stop", "feet per setup", "how many lineal feet") carries that unit in its headline
  (the first section with a bold figure that is not a bullet), in its lead clause, the text before the first ` · `, so a
  unit that only follows the figure does not count (`**10 stops** · 17.62 minutes` does not answer "how many minutes"):
  setups, paper changes, order changes, stops (missed splices are stops), sheets, minutes (or min; "how long" and "how much
  time" ask minutes), hours, ft per setup (or ft/setup), ft per paper change, minutes per stop, lineal ft, sq ft, a percent,
  MSF; a ratio in the unit it names ("minutes per setup" wants minutes per setup, never feet per setup; "feet per stop" wants
  ft per stop, never the stops; "MSF per day" wants MSF per production day; any other word before "per" is its numerator,
  so "cost per setup" wants cost per setup and only a refusal passes; "per minute" too, so "square feet per minute" is never
  the speed). The numerator is the last unit or countable noun before "per" in its clause ("how much downtime did we have
  per stop" wants minutes per stop), and a duration asked with none named is minutes ("how long are we down per stop").
  "Stops per day" and "downtime per day" are the stops or minutes of each production day, not a ratio. An average or
  typical stop wants minutes per stop, an average of a total or of a setup's length a ratio, a run length feet per setup,
  feet a minute the speed, and the longest or shortest of anything says so: an answer of the window's total fails each.

`tests/test_quantity_questions.py` runs both over the issue's catch-all audit as well: the issue's seven rows, the owner's
interview phrasings and every count or unit phrasing that landed on the KPI summary before; each lands on a measure that
answers it (`measure=quantity`, the Pareto, the review) or is refused with what can be counted.

Two expectations hold the answer shape itself. `sections_match` takes one pattern or a list of patterns, each
of which some section must match. `shape` (`why-slow-answer-shape`, `period-slowest-grades-answer-shape`)
checks one headline, `bullets: [min, max]` finding bullets right under it with one bold figure each, an
optional `headline_match`, and `look: [min, max]` where-to-look lines that each end with a question.

## Latency budget

Target behavior is Datasette-fast: a question answers in tens of milliseconds.
The runner times every case (the agent's turns only; the dataset build, fixture
I/O and the checks are outside the clock), prints the ms column in the table,
and holds the offline set to two budgets:

- **per case**: `expect.max_ms`, default 250 ms for an offline case (`DEFAULT_MAX_MS`);
  a case over it fails with `<ms> ms, budget <max_ms> ms` whatever else it got right.
  A live or replayed run is never timed against it, since its time is the provider's
  (a `live: true` case shares its `expect` with its live variant).
- **per run**: the offline p50 must be at or under 100 ms and the p95 at or under
  250 ms (`LATENCY_BUDGET_MS`, nearest-rank percentiles). The summary prints
  `offline latency: p50 .. p95 ..` with a verdict per percentile, each breach
  adds one to the exit status, and `tests/test_evals.py::test_offline_latency_budget`
  applies the same gate in CI over the times the case tests just measured. The gate
  is over the whole offline set: a filtered `--only` run prints its percentiles and
  is not gated, since a subset's p50 is just those cases' own time.

Three tests pin the gate. `test_slow_tool_fails_the_latency_budget`: a 300 ms delay
on every daily tool call pushes `why-slow` past a second and fails it on the time
alone, and the same case with `max_ms: 10000` passes again.
`test_latency_budget_times_a_refusal_but_never_a_replay`: a refusal that takes
300 ms fails on time alone, and a replayed recording slowed the same way still
passes. `test_run_level_breach_raises_the_exit_status_and_fails_the_ci_gate`:
with a percentile budget set below the fastest case, the CLI exit status rises by
one per percentile over budget although every case passed,
`test_offline_latency_budget` fails on the same numbers, and an `--only` run under
the same budget exits 0.

Baseline, 2026-11-09 sandbox (4 cores), five uncontended runs of the 145 offline
cases (the tracked `history.jsonl` line and four re-measurements): p50 11–13 ms,
p95 60–66 ms; every case between 0 ms (a refusal that stops
before its first tool call) and 116 ms (`story-monday-warp`, four turns and eight
tool calls), half the set under 13 ms. That is wider than the 13–60 ms the issue
quotes for single questions, because the set also holds the refusals and the
multi-turn stories.

These cases carry an explicit `max_ms: 500`: the two multi-turn stories
(`story-monday-warp`, `story-trim-creep`), the eight- and nine-week trends
(`period-trend-minutes-per-stop`, `period-trim-trend`, `period-trend-warp-weekly`,
`period-moisture-trend`, `period-other-dry-end-trend`, `period-run-length-trend`, `value-trend-trim-8w`,
`value-trend-warp-8w`, `value-trend-stops-9w-missing-weeks`) and the three-shift
compare (`period-control-compare-shifts`). They run 52–116 ms uncontended; no
other case passes 53 ms. The three shifts' money with the day's total
(`money-day-ranks-shifts-without-the-outage`, PR #111: seven tool calls, the day pricing each shift
again) runs 210 ms in-process and 204 ms over GET in a warm, uncontended run, and 236–294 ms over GET
run alone, so it carries the same 500, as does the same question on 2025-08-21
(`money-day-side-trim-schedule-driven`, issue #135: 203–207 ms in-process, 212–241 ms over GET, uncontended).
Under about 2x CPU contention (four busy loops on the
four cores, two runs) four of them crossed the 250 ms default (`story-monday-warp`
418 ms, `story-trim-creep` 339 ms, `value-trend-trim-8w` 302 ms,
`period-trend-minutes-per-stop` 266 ms), all of them stayed under 500 ms, the next
slowest case reached 156 ms, and the run-level gate held at p50 26 ms, p95 232 ms.
Raise a budget only with the number that justifies it in the PR; never to make
a slow change pass.

## How the value goldens are made

One value case is not by hand: `value-dollars-shift-0914-shift-2-client` (issue #31b) holds the
server's `get_shift_dollars` at other rates to what the card's client formula (`web/money.js`) makes of
the same shift's quantities at the post's rates. `python3 evals/make_client_money.py` (needs node) writes
those figures to `client_money.json` after an intended change to the cost model or the client formula;
`make_value_goldens.py` turns them into the case, exact to the cent.

`make_value_goldens.py` generates every number in `golden_values.json` from plain
Python sums over `synth.generate_window()` for the golden window, from the data's
first day to `as_of` (2026-09-30; every weekday from 1 January 2025, issue #100): it has its own day /
week / month / 4-4-5 calendar arithmetic, its own ratio-of-sums tile formulas, its own Pareto
ordering, and never imports the DuckDB path the tools use for a number (it
imports the application's tile catalog only to check that the two lists have not
drifted apart; the reject taxonomy's families it reads from `reject_reasons.json`, a configured
table like the grade targets). Each expectation is `[expected, tolerance]`; the tolerance covers
the tool's display rounding (one decimal for feet and MSF, four for percentages,
two for minutes) plus floating-point noise between a DuckDB sum and a Python
sum. Integers, strings, lists and `null` are exact: the data's first week's
previous window is before the dataset, so its tiles must come back `null`, not `0`.

**The golden window** (issues #35, #100). The app's dataset runs from 1 January 2025 to a
last day the nightly deploy moves; the golden set's does not move. `run.py` scores every case against
the store of the window ending `GOLDEN_AS_OF` (2026-09-30): the process's own store
when it already holds that window (pytest, whose `tests/conftest.py` pins
`INVESTIGATOR_AS_OF=2026-09-30`, or a command line that sets it), otherwise a
second store built once: from the prebuilt folder `INVESTIGATOR_GOLDEN_PREBUILT`
names when the image carries it (the deployed `/evals` page), else generated
(about 25 s of a plain `python3 evals/run.py`). Every production day is generated from its own
date-keyed draws, so the golden window's days are the same days any later
window holds.

The file carries the dataset `content_hash`. `run.py` compares it with the golden
store before scoring and fails every value case when they differ ("stale
goldens; run make_value_goldens.py"), so an intended generator change forces a
deliberate regeneration and an unintended one is caught. The cases cover
every KPI tile for September and August (all shifts and per shift), one week
and one production day per shift, the data's first and second weeks, first month and first 4-4-5
period (no or a partial previous window) and the last week (partial),
`compare_periods` both ways, the downtime Pareto of the
missed-splice week by reason, place and kind, the waste breakdown for both
months, the story week and the data's first week (every waste line of record contract v2, and good output, gross minus every line), the add-back of the waste lines and good output to gross for a shift and for September (owner ruling (d), 2026-11-12), the stops profile for both
months, totals by grade and rollstock for both months and the data's first week,
the trim, warp, stops (reaching before the data) and MSF-per-day trends, the three daily tools for three production days,
the corrupt-record day, and the money tools' dollars (issue #31a: shift dollars for two shifts, one
with a visitor's rates; the September waste breakdown and the story week's downtime Pareto priced;
the opportunities for September and for one week with a margin set), and the speed variance (issue #92:
September against target, a week against the previous one, shift 3 against shift 1), and the counts a count question
opens with (issue #113: setups counted record by record, paper changes as the wet-end run records of the window, order
changes as the setups whose change code is O, for the week of 7 September, all shifts and shift 2, and Friday 11 September).

Row selectors: `metric=volume_msf` (the first row whose field prints as the
value), `#0` (row by index), `weekday_profile:weekday=Mon` (another row set),
`$` (the result envelope: `total_minutes`, `missing_weeks`, `comparison`).

## Investigation cases

The three `investigation` cases ask, as a superintendent would, about the three
seeded stories (`src/investigator/stories.py`, which no tool reads): the trim
creep after the slitter rebuild week, the Splicer 3 missed-splice week, and
Monday warp. Each is scored by application rules, not by comparing prose:

- **required evidence**: a `get_trend` result for `trim_pct` whose rows include
  the week of 17 August and rise at least 0.4 pp by the week of 28 September;
  a place Pareto covering 7–11 September where Splicer 3 minutes are at least
  3x the previous week; a day waste breakdown for Monday 14 September where
  warp is at least 1.2x the Friday before, and the September weekday profile
  where Monday has the most stops per day — and so on, listed under
  `expect.evidence` with a `why` on each rule;
- **numbers in the prose**: the value each rule found must appear in the
  answer under one of the application's formats;
- **forbidden claims**: regular expressions for asserting the slitter rebuild,
  a splicer fault, or weekend moisture as the cause. The evidence holds stop
  reasons, reject reasons and trends, not diagnoses, so "coincides with",
  "warrants inspection" and "ask the crew" pass while "caused by the rebuild"
  fails.

A teaching point the goldens surfaced: at month grain the slitter/scorer's
downtime **minutes** fall in September because August holds the 180-minute
rebuild window itself; its stop **count** rises. The rule uses the count.

## Live cases and fixtures

`mode: live` cases and every case marked `live: true` (the three investigations
and the new refusal cases, which carry an `expect_live` block) also run against a
live planner. `--live` reads `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` from the
environment (never a flag), picks the provider whose key is set (`--provider`
when both are), runs each live case, and writes the model's responses to
`evals/fixtures/<provider>/<id>.json` with a digest of the system prompt.
Replay feeds those responses back through the planner's transport hook — the
Anthropic adapter's `(status, body)` or the OpenAI adapter's parsed JSON — so
the agent, tools and validators run for real. If the prompt changes, replay
warns that the fixture is stale. The budget ledger used during evals is a
temporary file; the service's ledger is never touched.

Live results are scored by pass rate per type (`THRESHOLDS`): routing ≥ 0.9,
faithfulness, grounding, refusal, value and data_quality 1.0, investigation ≥
0.8. Offline cases stay pass / fail one by one. The exit status counts failed
offline cases plus live (provider, type) pairs under their threshold.
A live row costs a fraction of a cent with `claude-haiku-5-5` and roughly a
cent with `gpt-4.1-mini`; the table prints the measured cost per case. The
Anthropic recordings are committed under `fixtures/anthropic/`; none for OpenAI.

## Tracking runs over time

`python3 evals/run.py --track` appends one JSON line to `evals/history.jsonl`
after the score table: the UTC time, the git commit, the dataset hash, the
case count, pass / fail / skip per type for the offline cases, `latency_ms`
(the offline p50 and p95 of that run, in ms), and per provider the model its
recordings were made with, pass / fail / skip per type and the recorded cost. `--track` records whole-set runs only and refuses `--only`. The same
flag rewrites `evals/last_run.json`: one row per run of that newest line (id,
outcome, ms, note), stamped with the line's time and commit. Commit the two
files together; `tests/test_evalboard.py` fails when they disagree. A
run is comparable with the one before it when the dataset hash and the model
match, and the latency with the one before it when the machine does too; a changed prompt is flagged by the replay warning. The
file is the answer to "did that change make the agent better or worse": read
the two lines, not the vibes. With `--judge`, the line also carries `judge` and
the flag writes `evals/judge_last_run.json` beside it ([the narrative
judge](#the-narrative-judge)); commit the three together.

## The set in the app: `/evals`

The server shows the set to a visitor (issue #26). `src/investigator/evalboard.py`
reads this folder as shipped (the checkout, or `/app/evals` in the image, named
by `INVESTIGATOR_EVALS`) and loads this `run.py` to run a case; nothing on the
page writes here. The page also shows the newest tracked judged run and its
agreement with the owner (`judge` in `/evals.json`: the rubric, the scores per
narrative, the agreement, and the number of grades, never their text). The one
writer is the owner's grading page, `/evals/grade`, on the local workbench only
([calibration](#calibration-the-owners-grades)).

- `GET /evals`: every case of `golden.json` and `golden_values.json` exactly
  once, with its question (or the scripted call of a value case), its type, its
  expectations in plain words ("Calls get_downtime_breakdown for shift 2 by
  reason") and its result in the last tracked run. Opening a case runs it on
  the server and draws the actual tool trail beside the expectation, with the
  same trail row the main page draws. `/evals?case=<id>` opens that case;
  `/evals?type=<type>` filters.
- `GET /evals.json`: the same data as JSON, readable from another origin
  (`Access-Control-Allow-Origin: *`) so the scoreboard can be embedded:
  `badge`, `counts`, `last_run`, `history` (one entry per `history.jsonl` line
  with `pass_rate_pct`, `p50_ms`, `p95_ms`) and `cases`.
- `POST /api/evals/run` with `{"id": "<run id>"}` runs one row and returns its
  outcome, failures and the trace of every turn; `{"all": true}` runs every row
  and returns the score (answered from the previous whole-set run for a minute).
  It carries the same token and Origin checks as `/api/ask`.
- The badge on the main page (`<passed>/<runs> golden · <p50> ms p50`) is `evals.badge` of
  `/api/config`: passed / runs and the offline p50 of the newest history line.

A run id is the case id for the offline run and `<id>@<provider>` for its
recorded variant: a case with a recording makes two runs, so the run count is
the case count plus the recorded cases. A live run on the page is always a **replay** of the committed
fixture (`live=False` in `run_case_detail`): the page shows the recording's
model, its tool choices and its recorded cost, and no request from the page can
reach a provider, whatever keys the server holds. Latency on the page is this
server's own; the tracked numbers are the machine that ran `--track`.

## Scripted plans and the analyst's recordings

A case with a `plan` field (`analyst-plan-out-of-list-tool`) puts a plan a model
might return through the real plan check and tool execution
(`Agent.ask_planned`) with no model: it is an offline case, free and
deterministic. That is how "an out-of-list tool refuses the whole plan" is a
golden case rather than a hope about a model.

A plan case may also carry `narrative`: the lines a model might stream, with
placeholders such as `{{r1.0.observed_shift_fpm}}`. They are fed in small deltas
through the real stream validator (`NarrativeStream`), so a narrative a model
wrote is a golden case too. Two cases of issue #79 replay the live exchange
in which the analyst wrote "Shift 2 ran fastest at 562.75 ft/min including
downtime, against 608.92 ft/min on Shift 1 and 592.11 ft/min on Shift 3" over
its own KPI rows: `analyst-fastest-shift-contradicted` must withdraw that line
(`comparisons_withdrawn: 1`, with the limitation line *A comparison was withdrawn
because it contradicted its own figures.*) and keep the rest, and
`analyst-fastest-shift-consistent`, the corrected line, must survive whole
(`narrative_match`, `comparisons_withdrawn: 0`). A third,
`analyst-orderings-per-shift`, holds what review of the first check found: two
true lines it withdrew ("the largest stop reason" of each shift's own breakdown;
"fastest" beside a speed-to-target pair) must be kept, and a false comparison
whose subjects are named in words ("Shift 2 ran faster than Shift 1, … against
…") must be withdrawn. Every run that writes a
narrative counts the withdrawn lines in `usage.comparisons_withdrawn`, and the
summary prints them per kind of run:
`comparisons withdrawn: replayed anthropic 0 in 11 narratives  offline 2 in 3 narratives`.

A plan case marked `live: true` runs its question against the real analyst only
under `--live` (its `plan` and `narrative` are not used then); unrecorded, it
adds no skipped `@live` row, since its offline row already puts the round
through the real checks. The acceptance run of issue #79 is five of those:

```
for i in 1 2 3 4 5; do python3 evals/run.py --live --provider anthropic --only analyst-fastest-shift-contradicted --no-record | grep -E '^analyst|comparisons withdrawn'; done
```

The Anthropic analyst (issue #23) makes two streamed calls: one plan, one
narrative. `--live --provider anthropic` records that shape: the fixture is
stamped `shape: "plan"` and stores each call's stream events. A live or replayed
analyst case fails if any turn made more than two model calls, or an answer with
a narrative did not make exactly two. A live run prints
`live latency: p50 … p95 … first_token_p50 … model calls per case: at most N`,
and `--track` writes those numbers to the history line as `live_ms`; replays are
never timed. A fixture without the stamp was recorded with the earlier tool loop
and replays through it until it is re-recorded.

## Known defects

A case may carry `known_defect`. It is run, shown as `xfail` in the table with
the defect text, and does not count against the exit status; when it starts
passing the note says to remove the marker. None is marked today. The last one,
`july-reason-surfaced` (now `month-before-dataset-reason-surfaced`: since issue
#35 July is inside the year window, so the case asks about August 2025, before
the year's first day), recorded that an investigation whose only tool call was
refused ended `planner_error` with the window reason left in the trace; the
agent now answers `unavailable` with the tool's own reason as the text (the
offline planner and both live adapters alike), and the case is an ordinary
passing refusal case.

## The narrative rubric

What a live narrative is judged on. The deterministic checks enforce what they
can; the DeepEval judge below scores all five on every narrative, each on its
own:

1. *Grounded*: every number in the narrative appears in a tool result. Enforced:
   values arrive only through placeholders, and the faithfulness invariant checks
   every numeric token.
2. *Recorded, not proven*: losses are stated as what the data recorded; no causal
   claim the tools did not make. Pattern checks only (`forbidden`, `narrative_not_match`).
3. *Prescribes nothing*: no "should", no action list. Judge only (`prescribes_nothing`).
4. *Unavailable is not zero*: a value a tool returned as unavailable is never
   rendered as 0 or dropped silently. Enforced for nulls (they render as the word).
5. *Comparisons are consistent with the cited figures* (issue #79): an ordering
   word (*fastest*, *most*, *below*, *rose*) agrees with the values the line cites.
   Enforced for the orderings whose subject the rules in `narrative.py` can pin
   down (`ordering_contradicted`); the judge scores every comparison
   (`comparisons_consistent`), and the ones the rules leave alone are its job:
   actual against target first ("ran above target at 467.45 ft/min against a
   target of 500.00 ft/min"), and orderings the rules do not know ("Shift 2 ran
   closer to target, 90.87 percent against 95.81", which the scripted
   `analyst-orderings-per-shift` round keeps on the rolling year's data).

## The narrative judge

Issue #25. Everything above scores numbers, tools and patterns; nothing reads the
prose a model writes as prose. The judge does: a DeepEval **G-Eval** metric per
rubric line, scored 0 or 1 (strict mode) and reported line by line, never blended.
It is a model call, so it is **live only**: it never runs in the CI gate, on the
server or from `/evals`, and its scores change no exit status until its agreement
with the owner passes the bar below.

```
pip install -e '.[judge]'                                          # DeepEval 4.3.4, the optional extra; not in the image or CI
python3 evals/run.py --judge --judge-secret anthropic-api-key --judge-project <project>   # the key from Secret Manager by name; judge, print only
python3 evals/run.py --judge --judge-secret anthropic-api-key --track   # and record it: history.jsonl + judge_last_run.json
python3 evals/run.py --live --provider anthropic --judge           # judge fresh live narratives (ANTHROPIC_API_KEY set)
DEEPEVAL_JUDGE=claude-sonnet-5-5 python3 evals/run.py --judge      # another judge model (or --judge-model)
```

The key: the provider's variable when it is set (`ANTHROPIC_API_KEY` for the
Claude judges, `OPENAI_API_KEY` for `gpt-4.1-mini`), else the Google Secret
Manager secret named by `--judge-secret` or `DEEPEVAL_JUDGE_SECRET`, read with
your own `gcloud` credentials (`gcloud secrets versions access latest
--secret=<name> --project=<project>`, the project from `--judge-project` or
`DEEPEVAL_JUDGE_PROJECT`, else gcloud's default), so it never sits in a shell
history or a file. The name is the
one the deploy uses (`anthropic-api-key` in [DEPLOYMENT.md](../docs/DEPLOYMENT.md)),
or a judge-only secret with its own spend limit.

**What it judges.** Every narrative the run writes: each recorded live run
replayed (or run fresh under `--live`), and each scripted analyst round
(`plan` with `narrative`). A narrative is named `<run id>:<turn>`
(`story-monday-warp@anthropic:3`), and its `digest` hashes the exact question and
text, so a score or a grade always belongs to the words it was given. The judge
reads `investigator.judge.narrative_cases`: the question and its scope, the
narrative as a reader sees it (`Finding: ... (cites r1 row 0 trim_pct)`), and the
tool results it was written from, rows first, floats to four decimals, the
writer's twelve-row preview, at most 24,000 characters. Only the scripted narratives can be
judged and graded today: since the rolling year (#91) every
live recording is stale and replays nothing, so the recorded narratives
come back once the live recordings are re-recorded (#71). Until then the target
of twenty reads "20 once the live recordings are re-recorded (#71)" wherever it is
shown.

**The rubric** (`investigator.judge.RUBRIC`, the exact evaluation steps the judge
reads; its 12-character digest is stamped on every judged run):

| Line | Key | Criterion |
| --- | --- | --- |
| Grounded | `grounded` | Every number in the narrative appears in a tool result. |
| Recorded, not proven | `recorded_not_proven` | Losses are stated as what the data recorded; no causal claim the tools did not make. |
| Prescribes nothing | `prescribes_nothing` | No "should", no action list: the narrative interprets, it does not instruct. |
| Unavailable is not zero | `unavailable_not_zero` | A value a tool returned as unavailable is never rendered as 0 or dropped silently. |
| Comparisons match the figures | `comparisons_consistent` | Every ordering word and every comparison with a target agrees with the values the narrative cites. |

One boundary is a choice to calibrate, not a given: a *check* line that puts a
question to the crew ("ask the crew whether…", "compare against a prior shift")
passes `prescribes_nothing`, because the analyst's prompt asks for exactly that; an
instruction to change the machine, the process or staffing fails it. If the owner
disagrees, the step's wording changes, not the grades.

**Swappable.** The judge model is a config value: `DEEPEVAL_JUDGE` (or
`--judge-model`) names any priced model, `claude-haiku-5-5` by default,
`claude-sonnet-5-5` or `gpt-4.1-mini-2025-04-14`; its key is the provider's
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`), never a flag. Calls go through the
project's own adapters to their fixed hosts (`investigator.judge.JudgeClient`),
bounded at 200 calls, 900 s (counted from the first judged narrative) and $1.00
per run, 30 s, 64 KB out and 512 KB back per call. A bound reached mid-run, a
refused key, a busy model or an unreachable host stops the judge: the remaining
lines are unscored and say why, and a run that scored nothing is printed but not
tracked. Any other error unscores that one line and the run goes on. The metric is
swappable too: `judge(cases, client, metrics=...)` takes any DeepEval metric per
line. DeepEval's telemetry and its `.env` loading are switched off before it is
imported.

**Cost.** Five calls per narrative, one per line. `--judge --track` writes the
history line's `judge` block (model, metric, rubric digest, pass / fail per line,
`cost_usd` and `cost_per_case_usd`, and the agreement at that time) and
`evals/judge_last_run.json` (every narrative's five scores and the judge's reason
for each), stamped with that line's time and commit; `/evals` shows both, the
reasons on the local workbench only (they are model prose that no check here
reads, so the hosted board carries the scores without them). At the
request sizes above (about 11,500 input tokens across a narrative's five calls, at three bytes a token)
Haiku 5.5's list price is a little over a tenth of a cent per narrative; the tracked
`cost_per_case_usd` is the measured figure.

### Calibration: the owner's grades

The judge is trusted only as far as it agrees with the owner. On the local
workbench (`python -m investigator.server`), **`/evals/grade`** lists every
narrative above with the tool results it was written from. The owner marks each
good or bad, ticks the rubric lines it passes, and says why (required for every
grade, good ones too). The page saves to `evals/judge_calibration.json` and never shows the judge's
own scores, so the grades are not anchored on them. The hosted demo has no
grading route. Twenty grades are the target, once the live recordings are
re-recorded (#71). A grade is counted the same way everywhere: `/evals`, the
grading page and the eval table count every saved grade, and the grading page also
says how many are set aside because their text has changed or no longer replays.

Agreement is per line: of the narratives both graded as the same text (same
`digest`; a grade of text that has since changed, or that the judge did not score,
is set aside and counted as such), how often the
judge's 0/1 equals the owner's pass/fail, and Cohen's kappa (agreement beyond
chance; *undefined* when both raters gave every narrative the same mark). The
overall row compares the owner's good/bad with all five lines passing. Below 90%
on a line, that line's wording in the rubric is what gets fixed, not the owner's
grades. Every run prints it (`judge vs owner: ...` after `--judge`, or the stored
judged run's line otherwise), and so does `/evals`.

### Agreement with the owner

No agreement is measured yet: the owner has graded none of the narratives, and no
judged run is tracked. Once both exist, this table is the measured one
(`tests/test_judge.py` holds it to the committed files):

| Line | Agreement | Percent | Kappa |
| --- | --- | --- | --- |
| `grounded` | - | - | - |
| `recorded_not_proven` | - | - | - |
| `prescribes_nothing` | - | - | - |
| `unavailable_not_zero` | - | - | - |
| `comparisons_consistent` | - | - | - |

## What this does not prove

The pattern checks on live narrative (`narrative_not_match`, `forbidden`) are
heuristics: a narrative can avoid the forbidden phrases and still give poor
advice. They catch the specific failures listed in `docs/TESTING.md`, not prose
quality in general. Read the recorded fixtures when a live case passes for the
first time. The ordering check is rule-based in the same way: it withdraws a
line only when it can tell which cited value a word is about, so a subject named
only by a pronoun ("Shift 2 ran … . It was the fastest of the three."), an
ordering word it does not know ("Shift 2 led the day"), an ordering over values
the line does not cite, or a comparison across two fields, is not caught by it.
Actual against target is the case that matters: `actual_fpm` and `target_fpm`
are different fields of the same row, so "ran above target at 467.45 ft/min
against a target of 500.00 ft/min" is kept even though it is false. Covering it
means pairing a row's `*_fpm` with its `target_fpm` in `_placed_pair`; until
then it is the judge's (`comparisons_consistent`).
