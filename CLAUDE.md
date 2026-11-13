# CLAUDE.md

Guidance for Claude Code sessions in `corrugator-shift-investigator`. The repo is the project's memory: chats are temporary, so anything worth keeping is committed here, written into an issue, or put in a PR. Read this file, then the issue you were given.

## What this is

A bounded agent that investigates why a corrugator shift was bad, on synthetic data. The offline router and parameterized DuckDB tools compute every number; a model (Anthropic or OpenAI, server key only) may choose tools and write the narrative, never the numbers. The public demo is <https://shift.talktomymachine.ai> on Cloud Run; every question is answered by the offline planner first, and the model is an opt-in, streamed "Ask the analyst" per answer that never holds the shared lock. Target behavior: Datasette-fast. A question answers in tens of milliseconds and the URL is the state. `/issues` shows the ordered backlog.

## What good looks like

This is the owner's public portfolio piece, so trust beats breadth.

- Exact over summary. A question that names a measure (how many setups, the minutes of one stop reason, the MSF of a grade) leads with that measure, or says plainly that it cannot answer it. Answering a different question, such as the KPI tiles for "how many setups", is a bug and becomes a golden case.
- Plant-real and vendor-free. The records read like a generic corrugator's dry end and wet end, in a corrugated plant's own units. No vendor names, vendor record codes or real plant data appear anywhere in the repo.
- Dollars are quantities priced at named, assumed rates (`rates.json`, `docs/COST_MODEL.md`), never presented as a plant's own figures.
- Every figure can be checked: a bold figure cites its query and row, and the case behind each behavior is browsable on `/evals`.

## Commands

```bash
pip install -e . pytest                              # Python >= 3.11; deps are duckdb and reportlab
python -m pytest -q                                  # about 5 minutes; the Anthropic recordings skip while they are stale, and one test without the judge extra
node --check src/investigator/web/app.js && node tests/test_ui.cjs
python3 evals/run.py                                 # the regression gate, about 21 s; offline cases also run over their URL; stale live recordings skip until re-recorded
python3 evals/run.py --live --provider anthropic     # needs ANTHROPIC_API_KEY; records fixtures under evals/fixtures/
python3 evals/make_value_goldens.py                  # only after an intended generator change
python3 evals/make_client_money.py                   # only after an intended cost-model or money.js change (needs node); then make_value_goldens
pip install -e '.[judge]' && python3 evals/run.py --judge   # the narrative judge (DeepEval G-Eval): billed, never the gate
python -m investigator.server --port 7860            # local workbench at http://127.0.0.1:7860
```

## Layout

- `src/investigator/`: `server.py` (http.server; `/api/config`, `/api/ask`, `/api/report`, `/api/feedback`, the URL path `/q` and `/corr/…`, `/evals`, `/evals.json`, `/api/evals/run`, the local-only `/evals/grade`, `/docs`) · `questionlog.py` (the anonymous question log of the hosted demo, issue #125: one JSON line per question, answer, analyst round and vote on stdout, no header value in a line; `QUESTION_LOG=0|1`) · `docsite.py` (the `/docs` site: `docs/*.md` rendered with a left table of contents at image build; `SECTIONS` is the TOC; `python -m investigator.docsite changelog` writes `docs/CHANGELOG.md` from the merged pull requests) · `urls.py` (the URL grammar: `canonical_url` of a parsed request and its inverse `resolve_url`) · `agent.py` (bounded tool loop, the offline `parse_request`, and `ask_planned`: the analyst's one checked plan) · `shapes.py` (each intent's answer shape: a headline, findings whose bold figure cites its row, where to look) · `analysis.py` (parameterized DuckDB tools) · `semantic_model.json` + `semantic.py` (the semantic model, issue #117: measures, dimensions and synonyms, physical names in one block; `validate`, `compile_query` to parameterized SQL, `run` with receipts; reached by `measure=semantic`, rendered at `/docs/semantic-model`) · `targets.py` + `targets.json` (the owner's bands, speed baseline, recurrence and lenses; issue #101) · `cost_model.py` and `rates.json` (the rates and the one pricing call; every priced row carries `price_terms`) · `record_store.py` (the rolling year in DuckDB plus the derived `shift_kpis` table the period tools and reports read; loads the prebuilt folder when `INVESTIGATOR_PREBUILT` names one) · `prebuilt.py` (`python -m investigator.prebuilt <folder>`, run at image build) · `synth.py`, `fixture.py`, `archive.py` (generators) · `anthropic_provider.py`, `openai_provider.py` · `narrative.py` · `chart.py`, `pdf_charts.py`, `reports.py`, `periods.py` · `evalboard.py` (the `/evals` page's data; runs a case offline or replays its fixture, never a model; saves the owner's grades from the local `/evals/grade`) · `judge.py` (the narrative judge: rubric, bounded judge client, G-Eval, agreement with the owner) · `web/` (`app.js`, `money.js` (the client formula: re-prices a card at a visitor's rates, digit for digit the server's), `index.html`, `evals.js`, `evals.html`, `grade.js`, `grade.html`, `trail.js`, `style.css`)
- `evals/`: `golden.json` (`reviewed_by: owner` marks the cases the owner set, `owner-pending` until the owner has read them), `golden_values.json` (generated by `make_value_goldens.py`, never typed), `client_money.json`, `run.py`, `make_real_words.py` (the typo rule's real-word list; needs wordfreq), `fixtures/`, `history.jsonl` and `last_run.json` (written together by `--track`, shipped in the image, shown on `/evals`), `judge_last_run.json` (written by `--judge --track`) and `judge_calibration.json` (the owner's grades), `README.md` (the seven case types)
- `tests/`: pytest suites and `test_ui.cjs`; `layout_check.cjs` and `docs_layout_check.cjs` measure the shell and the case study in headless Chromium (not in CI)
- `docs/`: every page of `/docs` (a new file goes into `docsite.SECTIONS`); `CASE_STUDY.md` (first in the TOC; it types no number: each `{{name}}` is filled at build by `docsite.figures` from `last_run.json`, `history.jsonl`, the changelog and `cost_model`, and a typed digit or number word fails the build), `OVERVIEW.md`, `ASKING.md`, `ANALYST.md`, `EVALS.md`, `COST_MODEL.md`, `DATA.md` (the section landing pages), `RECORDS.md` (the record contract), `CHANGELOG.md` (generated), `AGENT_CONTRACT.md` (the agent's tools, bounds and scope), `AGENT_EXECUTION.md`, `ANTHROPIC_DEMO.md`, `DEPLOYMENT.md` (keyless deploy, decisions on record), `TESTING.md`, `METRICS.md`, `URLS.md` (the public URL grammar; keep `urls.py` and it in step) · `archive/` (historical reviews, not current)
- `.github/workflows/`: `tests.yml` (push and PR), `deploy-cloudrun.yml` (main deploys to Cloud Run on push and every weekday night, moving the window), `preview.yml` (each PR gets a no-traffic tagged revision; its URL is a PR comment); `.github/scripts/` holds their helpers

## Google Cloud

The facts a session needs; the procedures and decisions are in `docs/DEPLOYMENT.md`.

| Setting | Value |
| --- | --- |
| Project and region | `felix-august`, `us-central1` (repository variables `GCP_PROJECT_ID`, `GCP_REGION`) |
| Service | Cloud Run `corrugator-shift-investigator`, deployed from source by Cloud Build: min 0, max 2 instances, 1 vCPU, 1 GiB, concurrency 20, timeout 180 s, public mode |
| Addresses | <https://shift.talktomymachine.ai> (Cloud Run domain mapping, a Preview feature; DNS at GoDaddy) and the two `run.app` URLs |
| Deploys | Every push to `main` and every weekday at 13:00 UTC (the window moves); each PR gets a no-traffic tagged revision `pr-<n>` (`preview.yml`) |
| Deploy credential | The stored `GCP_SA_KEY` secret until the keyless cutover (#28: Workload Identity Federation, repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_DEPLOYER_SERVICE_ACCOUNT`); #133 tightens it before agents run |
| Secrets | Secret Manager `anthropic-api-key`, mounted by name as `ANTHROPIC_API_KEY` (repository variable `ANTHROPIC_SECRET_NAME`); OpenAI not mounted |
| Logs | Cloud Logging, `_Default` bucket, 30 days. The question log (issue #125) prints one JSON line per question there, and the sink `question-log` copies it to BigQuery dataset `question_log` (90-day partitions; set up by the owner on 2026-11-13, BigQuery API enabled the same day) |
| Owner only | IAM, billing and budget alerts, secrets, the domain mapping, the WIF cutover, the question-log sink. Sessions never change these (see Permissions) |

## Rules

- Numbers come from tool results, never from prose. A narrative that states a number no tool returned is a bug, and the bug becomes a golden case.
- Every bug becomes a golden case in `evals/golden.json` before the fix.
- The offline path never calls a model. Live calls are bounded in calls, seconds and bytes, and go only through the provider adapters.
- Public mode never accepts a visitor key.
- Contracts move with their code in the same PR: `docs/RECORDS.md` with `records.py` and `synth.py`, `docs/AGENT_CONTRACT.md` with the tool schemas, bounds and scope checks, `docs/URLS.md` with `urls.py`.
- No doc states a current count of tests, cases, runs or recordings. Counts drift, and every PR conflicts on them; the test run's own output, `evals/last_run.json` and `/evals` are the counts. A dated past result ("the first slice passed 106 tests") is history and may stay.
- Keep this file short. Procedures live in `.claude/skills/`.

## How sessions work

- One issue, one session, one PR. Use the branch the session gives you and put `Closes #<n>` in the PR body. If an open PR already closes the issue, stop and say so: never two sessions on one issue.
- The coordinator, a Claude chat working with the owner, writes the issues, reviews each PR with the `reviewer` subagent, and posts the verdict and the owner's decisions as PR comments. A session's next task is the newest coordinator comment on its PR: do every item, then reply on the PR with what changed for each.
- Claude never merges on its own. A PR merges only on the owner's word for that PR, by `/approve-pr <n>` or "merge <n>" to the coordinator; both check CI, the eval diff and the review verdict first.
- To catch a branch up with `main`, merge `main` into it and push; never rebase a pushed branch. Force pushes are denied by `.claude/settings.json`, so a rebase strands the work on a branch nobody can update. In the eval files, `golden.json` merges by case `id` (keep both sides' cases), `history.jsonl` keeps both sides' lines with main's first, and `last_run.json` takes main's copy; then run `python3 evals/run.py --track` and commit both files (`tests/test_evalboard.py` checks that `last_run.json` matches the newest history line).
- Start by reading this file and the issue, then run the tests and evals before changing anything so the baseline is known.
- Done when: tests pass; `python3 evals/run.py` passes with no case removed or weakened; each acceptance criterion in the issue is demonstrated in the PR description with the command and its output; docs the change touches are updated in the same PR.
- The PR title is its changelog line: every merge is listed on `/docs/changelog` (the deploy regenerates `docs/CHANGELOG.md` from the merged PRs and fails the run when the live page misses the merge), so write the title as what changed for a reader, with the issue number.
- PR description: what changed, how it was tested (paste the eval table), and the eval and latency delta. A few minutes after the PR opens, the Preview workflow comments a URL where that PR runs; the owner looks at it during `/review-pr` (it is not reachable from a cloud session), so render locally and quote what you checked.
- Before opening the PR, have the `reviewer` subagent check the diff against the issue. Fix correctness gaps; ignore style nits. The Codex connector also reviews every PR: fix each of its findings or answer it on its thread with the reason, outdated threads included; an unanswered thread blocks approval.
- Use `/issues` (the ordered backlog with a status per issue), `/fix-issue <n>`, `/add-golden-case <what>`, `/run-evals`, `/review-questions` (last week's question log into golden-case candidates), `/review-pr <n>` and `/approve-pr <n>` for the repeatable parts. Sessions label an issue `waiting-on-owner` when they ask the owner something in it.

## Permissions and safety

- Run freely: tests, evals, the local server, `gh` reads, `git` on your own branch.
- Ask first: anything that changes Cloud Run scaling or settings, billing, IAM or secrets, and anything that deletes data. `.claude/settings.json` denies the obvious commands outright.
- Keys never appear in code, committed env files, PR text or chat. Cloud Run mounts them from Secret Manager by name.
