# Golden evals

`run.py` runs the cases in `golden.json` and prints one score table. It is the
answer to "how do you know the agent is right?" for this project.

```
python3 evals/run.py                 # offline cases, plus replay of any recorded live fixture
python3 evals/run.py --live          # also run the live cases against OpenAI and record fixtures
python3 evals/run.py --only why-slow # one case
```

`tests/test_evals.py` runs the same offline cases and recorded fixtures in CI,
so a change that breaks a golden case fails the build.

## Four case types

| Type | What it proves | How it is checked |
| --- | --- | --- |
| routing | the planner picked the right tools with the right arguments | required tool calls (argument subset), optional allow-list, call count, chart and table counts, selected shifts |
| faithfulness | every number in the answer came out of a tool row | each numeric token in the prose must equal some tool-row value under one of the application's formats (`.1f`, `.2f`, `,.0f`, `g`, …), a row count, a below-target count, or a stated rule constant |
| grounding | findings cite evidence that exists and nothing is silently dropped | narrative citations resolve to `(result_id, row, field)` in the trace; non-limitation paragraphs cite something; a requested chart that could not be drawn is disclosed |
| refusal | out-of-scope requests are refused rather than answered | expected `ValueError` text, or a `partial`/`unavailable` status with the disclaimer, or forbidden phrases absent |

## Global invariants

Every case, whatever its type, also checks:

- offline runs report zero model calls and $0;
- every executed tool is in `TOOL_SCHEMAS`, and every tool call stays inside the selected shift scope;
- chart rows equal the rows of the result they cite;
- tables shown to the user are complete (row count equals an independent tool call);
- narrative numbers and citations are faithful, as above.

## Live cases and fixtures

Live cases use the OpenAI planner. `--live` reads `OPENAI_API_KEY` from the
environment (never a flag), runs each live case, and writes the model's
responses to `evals/fixtures/<id>.json` with a digest of the developer prompt.
Replay feeds those responses back through the planner's transport hook; the
agent, tools and validators run for real. If the developer prompt changes, replay
warns that the fixture is stale. The budget ledger used during evals is a
temporary file; the service's ledger is never touched.

Nine live cases cost roughly a cent each with `gpt-4.1-mini`; the table prints
the measured cost per case.

## What this does not prove

The pattern checks on live narrative (`narrative_not_match`) are heuristics: a
narrative can avoid the forbidden phrases and still give poor advice. They catch
the specific failures listed in `docs/TESTING.md`, not prose quality in general.
Read the recorded fixtures when a live case passes for the first time.
