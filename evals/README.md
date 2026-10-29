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

`tests/test_evals.py` runs the same offline cases and recorded fixtures in CI,
so a change that breaks a golden case fails the build; `tests/test_goldens_values.py`
fails the build when the committed value goldens are not what the generator produces.

## Seven case types

| Type | What it proves | How it is checked |
| --- | --- | --- |
| routing | the planner picked the right tools with the right arguments | required tool calls (argument subset), optional allow-list, call count, chart and table counts, selected shifts |
| faithfulness | every number in the answer came out of a tool row | each numeric token in the prose must equal some tool-row value under one of the application's formats (`.1f`, `.2f`, `,.0f`, `g`, …), a row count, a below-target count, or a stated rule constant; a refused call's own message, quoted verbatim, is tool output too |
| grounding | findings cite evidence that exists and nothing is silently dropped | narrative citations resolve to `(result_id, row, field)` in the trace; non-limitation paragraphs cite something; a requested chart that could not be drawn is disclosed |
| refusal | out-of-scope requests are refused rather than answered | expected `ValueError` text, or a `partial`/`unavailable` status with the disclaimer, or forbidden phrases absent; a window before the dataset is unavailable, never zero; a weekend day is refused with the calendar reason |
| value | a tool's numbers equal a second, plain computation over the records | one tool call through the real agent loop; `expect.values` maps a row selector to fields to `[expected, tolerance]`; `row_count`, `coverage` and reported `excluded_records` alongside |
| investigation | a multi-turn question finds a seeded story in the evidence, reports its numbers, and asserts no cause the evidence lacks | evidence rules (tool, argument subset, window covering the story period, a row and a comparison), the found numbers present in the prose, forbidden causal patterns absent |
| data_quality | a corrupt record is flagged, excluded, reported, never averaged in | coverage `incomplete` for its shift only, the excluded record reported with its raw value, the impossible number absent from the answer, rankings withheld; the matching value case pins the remaining sums |

## Global invariants

Every case, whatever its type, also checks:

- offline runs report zero model calls and $0;
- every executed tool is in `TOOL_SCHEMAS`, and every tool call stays inside the selected shift scope;
- chart rows equal the rows of the result they cite;
- tables shown to the user are complete (row count equals an independent tool call);
- narrative numbers and citations are faithful, as above.

## How the value goldens are made

`make_value_goldens.py` generates every number in `golden_values.json` from plain
Python sums over `synth.generate_months()`: it has its own day / week / month
calendar arithmetic, its own ratio-of-sums tile formulas, its own Pareto
ordering, and never imports the DuckDB path the tools use for a number (it
imports the application's tile catalog only to check that the two lists have not
drifted apart). Each expectation is `[expected, tolerance]`; the tolerance covers
the tool's display rounding (one decimal for feet and MSF, four for percentages,
two for minutes) plus floating-point noise between a DuckDB sum and a Python
sum. Integers, strings, lists and `null` are exact: August's previous window is
July, which is outside the dataset, so its tiles must come back `null`, not `0`.

The file carries the dataset `content_hash`. `run.py` compares it with the live
store before scoring and fails every value case when they differ ("stale
goldens; run make_value_goldens.py"), so an intended generator change forces a
deliberate regeneration and an unintended one is caught. The 58 cases cover
every KPI tile for September and August (all shifts and per shift), one week
and one production day per shift, the first week (no previous window) and the
last week (partial), `compare_periods` both ways, the downtime Pareto of the
missed-splice week by reason, place and kind, the waste breakdown for both
months and the story week, the stops profile for both months, the trim, warp,
stops and MSF-per-day trends, the three daily tools for three production days,
and the corrupt-record day.

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
cent with `gpt-4.1-mini`; the table prints the measured cost per case. Fifteen
Anthropic recordings are committed under `fixtures/anthropic/`; none for OpenAI.

## Tracking runs over time

`python3 evals/run.py --track` appends one JSON line to `evals/history.jsonl`
after the score table: the UTC time, the git commit, the dataset hash, the
case count, pass / fail / skip per type for the offline cases, and per provider
the model its recordings were made with, pass / fail / skip per type and the
recorded cost. A run is comparable with the one before it when the dataset hash
and the model match; a changed prompt is flagged by the replay warning. The
file is the answer to "did that change make the agent better or worse": read
the two lines, not the vibes.

## Known defects

A case may carry `known_defect`. It is run, shown as `xfail` in the table with
the defect text, and does not count against the exit status; when it starts
passing the note says to remove the marker. None is marked today. The last one,
`july-reason-surfaced`, recorded that an investigation whose only tool call was
refused ended `planner_error` with the window reason left in the trace; the
agent now answers `unavailable` with the tool's own reason as the text (the
offline planner and both live adapters alike), and the case is an ordinary
passing refusal case.

## What this does not prove

The pattern checks on live narrative (`narrative_not_match`, `forbidden`) are
heuristics: a narrative can avoid the forbidden phrases and still give poor
advice. They catch the specific failures listed in `docs/TESTING.md`, not prose
quality in general. Read the recorded fixtures when a live case passes for the
first time.
