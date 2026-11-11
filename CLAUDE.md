# CLAUDE.md

Guidance for Claude Code sessions in `corrugator-shift-investigator`. The repo is the project's memory: chats are temporary, so anything worth keeping is committed here, written into an issue, or put in a PR. Read this file, then the issue you were given.

## What this is

A bounded agent that investigates why a corrugator shift was bad, on synthetic data. The offline router and parameterized DuckDB tools compute every number; a model (Anthropic or OpenAI, server key only) may choose tools and write the narrative, never the numbers. The public demo runs on Cloud Run; every question is answered by the offline planner first, and the model is an opt-in, streamed "Ask the analyst" per answer that never holds the shared lock. Target behavior: Datasette-fast. A question answers in tens of milliseconds and the URL is the state. The ordered backlog is issues #17 to #29.

## Commands

```bash
pip install -e . pytest                              # Python >= 3.11; deps are duckdb and reportlab
python -m pytest -q                                  # 1517 tests, about three minutes
node --check src/investigator/web/app.js && node tests/test_ui.cjs
python3 evals/run.py                                 # 289 golden runs offline, each also over its URL, about 10 s; the regression gate
python3 evals/run.py --live --provider anthropic     # needs ANTHROPIC_API_KEY; records fixtures under evals/fixtures/
python3 evals/make_value_goldens.py                  # only after an intended generator change
pip install -e '.[judge]' && python3 evals/run.py --judge   # the narrative judge (DeepEval G-Eval): billed, never the gate
python -m investigator.server --port 7860            # local workbench at http://127.0.0.1:7860
```

## Layout

- `src/investigator/`: `server.py` (http.server; `/api/config`, `/api/ask`, `/api/report`, the URL path `/q` and `/corr/…`, `/evals`, `/evals.json`, `/api/evals/run`, the local-only `/evals/grade`, `/docs`) · `docsite.py` (the `/docs` site: `docs/*.md` rendered with a left table of contents at image build; `SECTIONS` is the TOC; `python -m investigator.docsite changelog` writes `docs/CHANGELOG.md` from the merged pull requests) · `urls.py` (the URL grammar: `canonical_url` of a parsed request and its inverse `resolve_url`) · `agent.py` (bounded tool loop, the offline `parse_request`, and `ask_planned`: the analyst's one checked plan) · `shapes.py` (each intent's answer shape: a headline, findings whose bold figure cites its row, where to look) · `analysis.py` (parameterized DuckDB tools) · `record_store.py` (two synthetic months in DuckDB plus the derived `shift_kpis` table the period tools and reports read; loads the prebuilt folder when `INVESTIGATOR_PREBUILT` names one) · `prebuilt.py` (`python -m investigator.prebuilt <folder>`, run at image build) · `synth.py`, `fixture.py`, `archive.py` (generators) · `anthropic_provider.py`, `openai_provider.py` · `narrative.py` · `chart.py`, `pdf_charts.py`, `reports.py`, `periods.py` · `evalboard.py` (the `/evals` page's data; runs a case offline or replays its fixture, never a model; saves the owner's grades from the local `/evals/grade`) · `judge.py` (the narrative judge: rubric, bounded judge client, G-Eval, agreement with the owner) · `web/` (`app.js`, `index.html`, `evals.js`, `evals.html`, `grade.js`, `grade.html`, `trail.js`, `style.css`)
- `evals/`: `golden.json` (203 cases), `golden_values.json` (80), `run.py`, `make_real_words.py` (the typo rule's real-word list; needs wordfreq), `fixtures/`, `history.jsonl` and `last_run.json` (written together by `--track`, shipped in the image, shown on `/evals`), `judge_last_run.json` (written by `--judge --track`) and `judge_calibration.json` (the owner's grades), `README.md` (the seven case types)
- `tests/`: pytest suites and `test_ui.cjs`
- `docs/`: every page of `/docs` (a new file goes into `docsite.SECTIONS`); `OVERVIEW.md`, `ASKING.md`, `ANALYST.md`, `EVALS.md`, `COST_MODEL.md`, `DATA.md` (the section landing pages), `CHANGELOG.md` (generated), `AGENT_CONTRACT.md`, `AGENT_EXECUTION.md`, `ANTHROPIC_DEMO.md`, `DEPLOYMENT.md` (keyless deploy, decisions on record), `TESTING.md`, `METRICS.md`, `URLS.md` (the public URL grammar; keep `urls.py` and it in step) · `archive/` (historical reviews, not current)
- `.github/workflows/`: `tests.yml` (push and PR), `deploy-cloudrun.yml` (main deploys to Cloud Run), `preview.yml` (each PR gets a no-traffic tagged revision; its URL is a PR comment); `.github/scripts/` holds their helpers

## Rules

- Numbers come from tool results, never from prose. A narrative that states a number no tool returned is a bug, and the bug becomes a golden case.
- Every bug becomes a golden case in `evals/golden.json` before the fix.
- The offline path never calls a model. Live calls are bounded in calls, seconds and bytes, and go only through the provider adapters.
- Public mode never accepts a visitor key.
- Keep this file short. Procedures live in `.claude/skills/`.

## How sessions work

- One issue, one session, one PR. Branch `issue-<n>-<slug>`. Claude never merges on its own; the owner reviews with `/review-pr <n>` and merges by running `/approve-pr <n>`, which checks CI, the eval diff and the review verdict first.
- To catch a branch up with `main`, merge `main` into it and push; never rebase a pushed branch. Force pushes are denied by `.claude/settings.json`, so a rebase strands the work on a branch nobody can update.
- Start by reading this file and the issue, then run the tests and evals before changing anything so the baseline is known.
- Done when: tests pass; `python3 evals/run.py` passes with no case removed or weakened; each acceptance criterion in the issue is demonstrated in the PR description with the command and its output; docs the change touches are updated in the same PR.
- PR description: what changed, how it was tested (paste the eval table), and the eval and latency delta. A few minutes after the PR opens, the Preview workflow comments a URL where that PR runs; the owner looks at it during `/review-pr` (it is not reachable from a cloud session), so render locally and quote what you checked.
- Before opening the PR, have the `reviewer` subagent check the diff against the issue. Fix correctness gaps; ignore style nits.
- Use `/issues` (the ordered backlog with a status per issue), `/fix-issue <n>`, `/add-golden-case <what>`, `/run-evals`, `/review-pr <n>` and `/approve-pr <n>` for the repeatable parts. Sessions label an issue `waiting-on-owner` when they ask the owner something in it.

## Permissions and safety

- Run freely: tests, evals, the local server, `gh` reads, `git` on your own branch.
- Ask first: anything that changes Cloud Run scaling or settings, billing, IAM or secrets, and anything that deletes data. `.claude/settings.json` denies the obvious commands outright.
- Keys never appear in code, committed env files, PR text or chat. Cloud Run mounts them from Secret Manager by name.
