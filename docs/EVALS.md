# Numbers and evals

The rule is short: numbers come from tool results, never from prose. This page says where a number on an answer comes from, how the golden set checks that it is right, how a score is computed, and where to watch it in the app. [The golden set in full](../evals/README.md) is the reference; [metrics and formulas](METRICS.md) defines every quantity.

## Where a number comes from

1. **A tool computes it.** Every figure is a column of a row a fixed, parameterized DuckDB query returned over the selected scope. Percentages are ratios of sums over the window, never averages of per-shift percentages, and a tool says which direction is better for each measure.
2. **Application code places it.** The offline answer's headline and findings are built by `shapes.py` from those rows; each bold figure carries the reference `(query N · row M)` of the row it came from, and the charts are drawn from the same rows.
3. **A model, if asked, only points at it.** The analyst writes placeholders, and the server fills each from the cited row ([the analyst](ANALYST.md)).

A narrative that states a number no tool returned is a bug, and every bug becomes a golden case before it is fixed.

## The golden set

Two files hold the cases. `evals/golden.json` is questions with written expectations: which tools must run with which arguments, which charts, what the answer must and must not say, which address the question resolves to, how fast it must be. `evals/golden_values.json` is generated, never typed: `evals/make_value_goldens.py` computes every expected number a second way, with plain Python sums over the record families and its own calendar arithmetic, and stamps the file with the dataset's content hash so a changed generator fails every value case until the goldens are deliberately regenerated.

| Type | What it proves |
| --- | --- |
| routing | the planner picked the right tools, arguments and charts, and the question resolves to its canonical address |
| faithfulness | every number in the prose equals a tool-row value under one of the application's formats |
| grounding | findings cite evidence that exists, and nothing requested is silently dropped |
| refusal | an out-of-scope request is refused with its reason, and an unavailable window is never zero |
| value | a tool's numbers equal the second, independent computation |
| investigation | a multi-turn question finds a planted story, reports its numbers, and asserts no cause the evidence lacks |
| data_quality | a corrupt record is flagged, excluded and reported, never averaged in |

## How a score is computed

- **An offline case passes or fails on its own.** It passes only when every expectation it carries holds **and** every global invariant holds: zero model calls and $0; every tool in the catalog and inside the selected scope; chart rows equal to the rows they cite; tables complete; every number on a cited line a value of the row it cites; and exact over summary (issue #113): a question that names a countable noun or a unit never lands on the KPI summary, a quantity asked never lands on the overview, the summary or the shift comparison, one quantity never answers a breakdown, a grouping, a comparison of shifts or a ranked shift, nor a question with a word of the plain-quantity gate's closed vocabulary left once its own words are removed (a ranking, a grouping, a statistic, a subset or a percent), nor a ratio kept for all stops asked of one reason, place or kind, a quantity measure that names no shift is never answered for the selected one, and a count or unit question's headline carries the unit it asked for in its lead clause, before the first ` · `.
- **It is checked twice.** After the in-process run, the question is sent to `GET /q` on a loopback server: the redirect must land on the case's address, and the answer served there is scored with the same expectations.
- **It has a time budget.** An offline case fails above its `max_ms` (250 ms unless the case sets more), and a whole-set run fails when the offline p50 is over 100 ms or the p95 over 250 ms.
- **A live case passes by rate.** Recorded model rounds are replayed through the real agent and validators and scored per type against a threshold: routing at least 90%, investigation at least 80%, every other type 100%.
- **The total.** `python3 evals/run.py` prints one row per case, the passed/total per type, the latency percentiles and a final `N passed, F failed, S skipped`. Its exit status is the number of failed offline cases plus live types under their threshold plus latency breaches, so CI fails on any of them.
- **The history.** `python3 evals/run.py --track` appends one line per whole-set run to `evals/history.jsonl` (time, commit, dataset hash, pass/fail per type, the offline p50 and p95) and rewrites `evals/last_run.json`. Two lines are comparable when the dataset hash and the model match. The badge on the main page, such as `255/255 golden · 10 ms p50`, is read from the newest line: runs passed over runs, and its offline p50. The [case study](CASE_STUDY.md) reads its scores, latency and recorded cost from the same two files when the docs are built, and the build refuses a pair from different runs.

## The narrative judge

The checks above read numbers, tools and patterns; none of them reads a model's prose as prose. The narrative judge (issue #25) does: a DeepEval G-Eval metric per line of the rubric, each scored 0 or 1 and reported on its own. **Grounded**: every number in the narrative appears in a tool result. **Recorded, not proven**: losses are what the data recorded, with no causal claim the tools did not make. **Prescribes nothing**: it interprets and does not instruct. **Unavailable is not zero**: a value a tool returned as unavailable is never rendered as 0 or dropped silently. **Comparisons match the figures**: every ordering word and every comparison with a target agrees with the values the narrative cites, including the actual-against-target claims the deterministic ordering check misses.

It is a model call, so it is live only: `python3 evals/run.py --judge` judges every narrative the recordings replay and the scripted analyst rounds write, from the owner's machine, never in the CI gate and never from a page. The judge model is a setting (`DEEPEVAL_JUDGE`, Haiku 5.5 by default), its calls go through the project's own bounded adapters, and `--track` records the cost per judged case in `evals/history.jsonl`. It does not gate anything until it is calibrated: on the local workbench the owner grades twenty narratives at `/evals/grade` (once the live recordings, stale since the rolling year, are re-recorded: #71), and every run reports the judge's agreement with those grades per rubric line, as a percentage and Cohen's kappa. Below 90% on a line, the line's wording is what changes. [The judge in full](../evals/README.md#the-narrative-judge).

## Finding the next case: the question lab

A golden case pins one answer; finding the answers that would lose a supervisor's trust is a different job. The question lab (issue #177) asks many questions at once through the offline path: `python3 evals/lab.py --canned --scope both --period all` asks every canned question of the page under one shift and All, under Day, Week, Month and 4-4-5; a file of questions, one per line, asks the owner's own, each line optionally setting its controls (`[scope=day period=week] how many setups`; a key or value the lab does not take stops it with an error naming the allowed ones, never a card). It calls no model and writes, under the gitignored `artifacts/lab/`, a page for the owner and a JSON file in which each card carries its question, controls, address, intent, the shifts answered, the headline and first findings, the tools with their shift and window, any refusal, and a few mechanical flags (a headline under All that leads with one shift, a headline with no figure, a line too long to read on a phone). The flags say where to look; they are not verdicts.

The verdicts are the `analyst` agent's (`.claude/agents/analyst.md`): a corrugated plant's business-intelligence analyst that reads the domain docs first (the metrics and their denominators, what the router answers and refuses, the records, the cost model, the owner's bands and lenses, #166's crispness rules), then gives every card one verdict (right, not crisp, different question, refused but answerable, refused rightly) and writes a golden-case candidate for each miss, ten at a time, each marked `"reviewed_by": "owner-pending"`, for the owner to approve. It only reads (Read, Grep and Glob): its review is its message, and the `/question-lab` skill writes it to `artifacts/lab/`, runs any re-asks it lists and hands it their cards. Each approved candidate becomes a case before its fix.

## The set in the app: `/evals`

`/evals` lists every case of both files exactly once with its question, its type, its expectations in plain words and its result in the last tracked run, filtered by type with `/evals?type=<type>`. Opening a case (`/evals?case=<id>`) runs it on the server and draws the tool trail it actually produced beside what it expected. A live case on the page is always a replay of its committed recording: nothing on the page can reach a model, whatever keys the server holds. `/evals.json` is the same data as JSON, readable from another origin so the scoreboard can be embedded. The page also shows the newest tracked judged run, line by line, beside its agreement with the owner, and the verified examples' reading score (issue #187): how many of the owner's SLT questions, in his words and five rewordings each, the offline router reads as the example's exact semantic query (`src/investigator/examples.json`), with every wording it read another way, and apart, every wording asked in a stand-in window (the year to date until PR #186, read as the 4-4-5 period), which is another question and never counted as a pass. `evals/run.py` prints the same line, with the stand-in count, and the wordings it missed, then the stand-ins apart, and `--track` keeps them in `last_run.json`; like the judge, it is reported and never gates a run.

## Run it yourself

```bash
python3 evals/run.py                       # every case offline, plus replays of the recordings
python3 evals/run.py --only why-slow       # one case
python3 evals/make_value_goldens.py        # only after an intended generator change
python3 evals/lab.py --canned --scope both --period all --name canned   # the question lab: every canned card, offline, for the analyst agent
pip install -e '.[judge]' && python3 evals/run.py --judge --track   # the narrative judge: a billed model call, the judge model's key
```
