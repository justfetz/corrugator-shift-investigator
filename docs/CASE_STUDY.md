# Case study: why was that shift bad?

Shift Investigator answers a corrugator superintendent's morning question, such as why second shift was slow or which downtime reason cost the most minutes, from synthetic production records ([try it](OVERVIEW.md#try-it)). Fixed, parameterized queries compute every number; a model, when asked, may choose the queries and write the narrative, never a number ([where a number comes from](EVALS.md#where-a-number-comes-from)).

The figures on this page are filled in from the eval history, the changelog and the cost model when the docs are built, never typed ([docsite.py](../src/investigator/docsite.py)). To keep a copy, print the page or save it as PDF; the print layout drops the navigation.

## The problem

A corrugator glues paper into board at the wet end, then slits, scores and cuts it into orders at the dry end. A shift goes bad when the machine stops (maintenance or operator downtime), runs slower than its grade should, or makes board nobody ships: side trim, shear at order changes, and dry-end rejects for bond, warp or misalignment ([the records](DATA.md)). The morning question is plain: what went wrong last shift, and who do I ask?

The owner's targets give each loss a band (home run, target, flag) over its own denominator: downtime over scheduled minutes, trim and shear over gross square feet, dry-end waste over the yield left after trim and shear ([#101](https://github.com/justfetz/corrugator-shift-investigator/issues/101)). Putting those losses in dollars is harder than it looks:

- **The records hold no costs.** Every dollar is a recorded quantity at an assumed, named rate, never a plant's own figure ([cost model](COST_MODEL.md), [rates.json](../src/investigator/rates.json)).
- **A lost minute is priced from both sides.** It is {{rates.machine_minute}} of machine time at {{rates.hour}}, or {{rates.msf_per_minute}} of board not made (MSF: thousand square feet), priced at a contribution margin the owner sets ({{rates.margin}}). Both price the same lost capacity, so they sit side by side and are never added ([formulas](COST_MODEL.md#formulas)).
- **Trim is width, not time.** It costs paper and no run time: each point of side trim over the cost model's {{rates.trim_target}} threshold is {{rates.trim_point_year}} a year on one machine at {{rates.paper}} ([cost_model.py](../src/investigator/cost_model.py)). The owner's bands reach the tools with [#101](https://github.com/justfetz/corrugator-shift-investigator/issues/101).

## The design in one diagram

```diagram
  a question, or the address of an earlier answer
       │
       ▼
  offline router ────── words to a fixed plan; no model, no charge
       ▼
  DuckDB tools ──────── parameterized, read-only queries; every number is a row
       ▼
  answer shape ──────── headline, findings citing their query and row, where to look
       ▼
  card and URL ──────── the address is the state: paste it, get the same answer
       ┆
       ┆  opt-in, per answer: Ask the analyst
       ▼
  model proposes a plan ───► app checks every step ───► the same tools run
  model writes placeholders ───► server fills each from its row ───► validators
```

- **Numbers only from tools.** The router picks a plan, the tools compute, and the answer shape places each bold figure beside the reference of its row ([agent.py](../src/investigator/agent.py), [analysis.py](../src/investigator/analysis.py), [shapes.py](../src/investigator/shapes.py)).
- **The analyst is a bounded, checked plan.** On the default Anthropic path, a plan call and a streamed narrative call; application code checks every step against the allowlist, its schema and the scope before anything runs, and never holds the shared lock across a model call ([the analyst](ANALYST.md), [off the shared lock](ANTHROPIC_DEMO.md#off-the-shared-lock)).
- **The URL is the state.** Every offline answer has a canonical address that resolves back to the same request ([the URL grammar](URLS.md), [urls.py](../src/investigator/urls.py)).

## How we know the numbers are right

Every golden case has a type and expectations written down; the last tracked run, by type, on dataset {{evals.dataset}} ([numbers and evals](EVALS.md), [last_run.json](../evals/last_run.json)). Every case is browsable at `/evals` in the app ([the set in the app](EVALS.md#the-set-in-the-app-evals)).

{{evals.table}}

- **Value goldens are computed a second way.** {{evals.value}} cases hold a tool's numbers against plain Python sums over the records, stamped with the dataset hash ([make_value_goldens.py](../evals/make_value_goldens.py)).
- **Each question is asked again over its URL.** After the in-process run, every offline question but a scripted analyst plan goes over GET: {{evals.over_url}} are answered there with no model call and inside their time budgets, and a refusal must come back with the same text ([run.py](../evals/run.py), [the URL path](../evals/README.md#the-url-path)).
- **Validators guard the prose.** A digit or number word the model typed withdraws its line; so does an ordering word its cited values contradict ([narrative.py](../src/investigator/narrative.py), [test_narrative_comparisons.py](../tests/test_narrative_comparisons.py)).
- **Live rounds are recorded** and replayed through the real agent and validators; a recording made against other data skips, never scored ([fixtures](../evals/fixtures/), [live cases](../evals/README.md#live-cases-and-fixtures)).
- **Every pull request** runs the tests and the evals ([tests.yml](../.github/workflows/tests.yml)) and gets its own preview revision with no traffic ([preview.yml](../.github/workflows/preview.yml)); the review loop is under [built with](#built-with).

## Results

**{{evals.passed}} of {{evals.scored}} scored golden runs pass, and an offline answer takes {{latency.p50}} at p50 and {{latency.p95}} at p95**, in the last tracked run ({{evals.ran}}, commit {{evals.commit}}; [history.jsonl](../evals/history.jsonl)).{{evals.skipped}}

| Measure | Last tracked run | Source |
| --- | --- | --- |
| Golden runs | {{evals.runs}} now, up from {{history.first_cases}} on {{history.first}}, over {{history.runs}} tracked runs | [history.jsonl](../evals/history.jsonl) |
| Analyst, first words (p50) | {{live.first_words}} | written by the re-recording in [#71](https://github.com/justfetz/corrugator-shift-investigator/issues/71) |
| Analyst, recorded cost per case | {{live.cost_per_case}} on {{live.model}}, over {{live.cases}} recorded cases | [history.jsonl](../evals/history.jsonl), [cost per round](ANTHROPIC_DEMO.md#cost-per-round) |

{{live.recordings}} The model comparison fills in with the bake-off, a row per model from its newest tracked line ([#115](https://github.com/justfetz/corrugator-shift-investigator/issues/115)).

{{models.table}}

## Trade-offs and what I'd do next

- **Rules before a model for routing.** The router is instant, free and testable, and a question it cannot read is refused rather than guessed; the price is phrasing coverage. Next: the model reads only the questions the router cannot, as a translator to a checked URL ([#104](https://github.com/justfetz/corrugator-shift-investigator/issues/104)).
- **Synthetic data.** A seed gives byte-identical records with planted stories, so every eval is reproducible and no plant's data is exposed; the price is realism. Next: a generic dry-end setup record with order lanes, roll stock and a reject taxonomy, from the owner's schema ([#100](https://github.com/justfetz/corrugator-shift-investigator/issues/100), [data](DATA.md)).
- **A trail, not a trace.** Each answer carries its own trail of tools, arguments, rows and SQL, but no request leaves a trace across the service. Next: OpenTelemetry spans with prompt versions ([#114](https://github.com/justfetz/corrugator-shift-investigator/issues/114), [evidence and tracing](AGENT_EXECUTION.md#evidence-and-tracing)).
- **Digits, not judgment.** The checks prove every number came from a row, not that the words around it are wise. Next: a judge model for the narrative, live only, calibrated against the owner's grades ([#25](https://github.com/justfetz/corrugator-shift-investigator/issues/25), [the rubric](../evals/README.md#the-narrative-rubric)).

## Built with

Claude Code sessions do the work and the owner decides; the repository is the memory ([CLAUDE.md](../CLAUDE.md)).

- **One issue, one session, one pull request.** A session reads the issue, runs the tests and evals first, adds a golden case before a fix, and pastes the eval table into its pull request ([fix-issue](../.claude/skills/fix-issue/SKILL.md)).
- **Fresh-context review.** A reviewer subagent sees only the diff and the issue, and reports correctness gaps against the acceptance criteria ([reviewer.md](../.claude/agents/reviewer.md)).
- **The owner merges.** A review scores every criterion and looks at the preview; the merge refuses unless CI is green, no golden case was weakened and the verdict was approve ([review-pr](../.claude/skills/review-pr/SKILL.md), [approve-pr](../.claude/skills/approve-pr/SKILL.md)).
- **The changelog is the evidence.** {{changelog.pulls}} pull requests merged between {{changelog.first}} and {{changelog.last}}, each linked to its diff ([changelog](CHANGELOG.md)).
