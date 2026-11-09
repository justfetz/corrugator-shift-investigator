# CLAUDE.md

Guidance for Claude Code sessions in `corrugator-shift-investigator`. The repo is the project's memory: chats are temporary, so anything worth keeping is committed here, written into an issue, or put in a PR. Read this file, then the issue you were given.

## What this is

A bounded agent that investigates why a corrugator shift was bad, on synthetic data. The offline router and parameterized DuckDB tools compute every number; a model (Anthropic or OpenAI, server key only) may choose tools and write the narrative, never the numbers. The public demo runs on Cloud Run with the offline planner by default. Target behavior: Datasette-fast. A question answers in tens of milliseconds and the URL is the state. The ordered backlog is issues #17 to #29.

## Commands

```bash
pip install -e . pytest                              # Python >= 3.11; deps are duckdb and reportlab
python -m pytest -q                                  # 453 tests, about 65 s
node --check src/investigator/web/app.js && node tests/test_ui.cjs
python3 evals/run.py                                 # 160 golden cases offline, about 6 s; the regression gate
python3 evals/run.py --live --provider anthropic     # needs ANTHROPIC_API_KEY; records fixtures under evals/fixtures/
python3 evals/make_value_goldens.py                  # only after an intended generator change
python -m investigator.server --port 7860            # local workbench at http://127.0.0.1:7860
```

## Layout

- `src/investigator/`: `server.py` (http.server; `/api/config`, `/api/ask`, `/api/report`) · `agent.py` (bounded tool loop and the offline `parse_request`) · `analysis.py` (parameterized DuckDB tools) · `record_store.py` (two synthetic months in DuckDB; loads the prebuilt folder when `INVESTIGATOR_PREBUILT` names one) · `prebuilt.py` (`python -m investigator.prebuilt <folder>`, run at image build) · `synth.py`, `fixture.py`, `archive.py` (generators) · `anthropic_provider.py`, `openai_provider.py` · `narrative.py` · `chart.py`, `pdf_charts.py`, `reports.py`, `periods.py` · `web/` (`app.js`, `index.html`, `style.css`)
- `evals/`: `golden.json` (87 cases), `golden_values.json` (67), `run.py`, `fixtures/`, `history.jsonl`, `README.md` (the seven case types)
- `tests/`: pytest suites and `test_ui.cjs`
- `docs/`: `AGENT_CONTRACT.md`, `AGENT_EXECUTION.md`, `ANTHROPIC_DEMO.md`, `DEPLOYMENT.md` (keyless deploy, decisions on record), `TESTING.md`, `METRICS.md` · `archive/` (historical reviews, not current)
- `.github/workflows/`: `tests.yml` (push and PR), `deploy-cloudrun.yml` (main deploys to Cloud Run)

## Rules

- Numbers come from tool results, never from prose. A narrative that states a number no tool returned is a bug, and the bug becomes a golden case.
- Every bug becomes a golden case in `evals/golden.json` before the fix.
- The offline path never calls a model. Live calls are bounded in calls, seconds and bytes, and go only through the provider adapters.
- Public mode never accepts a visitor key.
- Keep this file short. Procedures live in `.claude/skills/`.

## How sessions work

- One issue, one session, one PR. Branch `issue-<n>-<slug>`. Claude never merges; the owner reviews and merges.
- Start by reading this file and the issue, then run the tests and evals before changing anything so the baseline is known.
- Done when: tests pass; `python3 evals/run.py` passes with no case removed or weakened; each acceptance criterion in the issue is demonstrated in the PR description with the command and its output; docs the change touches are updated in the same PR.
- PR description: what changed, how it was tested (paste the eval table), and the eval and latency delta.
- Before opening the PR, have the `reviewer` subagent check the diff against the issue. Fix correctness gaps; ignore style nits.
- Use `/fix-issue <n>`, `/add-golden-case <what>` and `/run-evals` for the repeatable parts.

## Permissions and safety

- Run freely: tests, evals, the local server, `gh` reads, `git` on your own branch.
- Ask first: anything that changes Cloud Run scaling or settings, billing, IAM or secrets, and anything that deletes data. `.claude/settings.json` denies the obvious commands outright.
- Keys never appear in code, committed env files, PR text or chat. Cloud Run mounts them from Secret Manager by name.
