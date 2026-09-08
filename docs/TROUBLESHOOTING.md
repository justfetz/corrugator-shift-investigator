# Troubleshooting and known limitations

Review date: September 7, 2026. Read this alongside [the workbench how-to](WORKBENCH.md) and [the review findings](REVIEW_2026-09-07.md). Do not paste API keys, environment dumps, or private production records into issue reports.

## Start or reconnect

Run from the repository root using `.venv\Scripts\python.exe -m investigator.server`, then open `http://127.0.0.1:8765/`. Use that exact host. `localhost`, a LAN address, and arbitrary Host headers are deliberately rejected. If using `--port 8766`, use that port in the browser too.

If the browser says connection refused, check the terminal: the process must still be running. Stop the server with Ctrl+C in its own terminal. If the port is occupied, inspect the listener before stopping any process, or choose another port. Do not kill unrelated Python services.

If dependencies cannot import, use the project virtual environment and run `python -m pip install -e . pytest` through that interpreter. An editable installation makes source edits available; restart for Python changes and reload the page for frontend changes. The server does not automatically reload Python modules.

## Requests and investigation status

| Symptom | Meaning and recovery |
| --- | --- |
| Selected shift only error | The question mentions another shift. Explicitly change the Shift control or choose All shifts / comparison. |
| Unsupported offline question | The router recognizes a small set of patterns. Use an example button or state a metric such as downtime, speed, or quality. |
| `unavailable` | The app shows supported next steps. Narrow the question to evidence the tools expose. |
| `complete` but the question was not answered | Completion validates evidence references, not semantic fulfillment. Check the actual tools and results; see R4. |
| `planner_error` | Provider, planner, or final-evidence validation failed. Some failures have only a generic status. Review the trace and any displayed message; partial evidence is not a successful full answer. |
| `budget_exhausted`, `timeout`, `context_limit` | A bounded investigation stopped. Narrow the request. Retained results, if any, are partial. |
| Session expired | Restart, reset, or session eviction invalidated the session. Use Reset conversation and ask again. |
| Follow-up forgets a comparison | History now persists with unchanged controls. Date/scope/mode changes, resets and dataset changes intentionally clear it. Report unexpected loss with a minimal reproduction. |
| Prior charts remain after an error | A failed request leaves the previous retained result visible. It does not constitute evidence for the new question. Reset to clear it. |

The offline router is not a reliable intent classifier. For example, “How can we be better?” is rejected, but appending “on this shift” can trigger a generic production overview. This does not establish that improvement, profit, or safety advice was answered. OpenAI mode also needs evaluation of intent fulfillment; its instructions are not an application-enforced completion check.

## Heatmap and period reports

The first period request computes validated daily snapshots; subsequent requests may use the process cache. Wait for completion before interacting. A single-threaded local server cannot serve another request while busy.

Hatching means missing or incomplete evidence. For September 29 in 4-4-5 mode, only September 29 and 30 exist. For a corrupt shift-2 period, every observed day is incomplete because the fixture adds the corrupt setup to each day. Choose clean records or a complete period to inspect the gradient.

Identical colors can be legitimate when all available outputs are equal. Colors are normalized within one report and have no shared scale across periods. See [metric and color formulas](METRICS.md). If different tooltip outputs still show identical backgrounds, reload the page and inspect browser console errors. The renderer should set a direct `rgba` background property; weakening CSP is not the remedy.

The numbered day cells provide details by mouse, click, and keyboard focus. Keyboard Tab advances through the buttons. Color contrast on the darkest cell needs refinement; this review is not a full accessibility certification.

## Downloads

The period heatmap title error is repaired. If an old server still displays `'shift'`, restart it with the current code. Week/month/accounting exports pass byte/access regressions. Save text contains narrative sections, not every table or chart.

Daily report PDF byte-format and session checks pass automated tests. That does not establish full visual PDF quality. PDF tables are summaries, with disclosed row limits; exports are not a screen capture or full evidence archive. A stale/foreign session-and-run reference is rejected. Reports live in memory and disappear on restart or eviction. Each session retains only its latest result.

## OpenAI/BYOK

Narrative unavailable means the writer call failed, reached a shared limit or returned invalid references/format. The calculated evidence and charts remain available. The writer shares the ten-call budget with planning. Numeric claims must use fact placeholders and known fields; prose still needs human review. No automatic retry occurs.

No key is needed for offline or period reports. Select Production day before OpenAI. The key field clears on submit and mode/scope resets; a second investigation requires re-entry unless a server environment key is configured. The app does not load `.env` automatically.

If a live question fails, inspect the displayed error before retrying: there are no automatic provider retries, and a failed or uncertain attempt can retain its spending reservation. The application's local budget and your provider billing/account limits are different controls. Do not delete `artifacts/model-budget.sqlite3` to clear an exhausted budget. See [BYOK behavior](OPENAI_BYOK.md).

Never enter a key into the question box. Questions and bounded synthetic evidence are sent to the provider in live mode. Do not put sensitive plant data into a question: this demo has no real-data privacy workflow. Live quality and account/model compatibility remain unverified.

## HTTP errors

| HTTP status | Typical local cause |
| --- | --- |
| 400 | Invalid fields, unsupported question/scope, expired session/report, or surfaced application error |
| 403 | Wrong Host/Origin, missing request token, or disallowed POST path |
| 404 | GET outside the static route allowlist |
| 408 | Timed-out request in the POST processing path |
| 413 | Empty or oversized request body |
| 415 | Content type is not `application/json` |
| 429 | Shared local 60-request/minute limit |
| 500 | Unexpected investigation failure; no fabricated answer |

Some body-read timeouts return 400, so status alone does not identify every cause. Browser messages and the exact failing route help distinguish transport from investigation failures.

## Tests and bug reports

Windows `PermissionError` in `pytest-of-Admin` or `.pytest_cache` is an environment-access failure, not a failed calculation assertion. Run with the required filesystem permission; do not remove tests or clear unrelated temp directories to make the suite green.

For a useful bug report, record the revision/dirty state, exact question, day/period/shift/scope/mode, corrupt-record setting, expected behavior, actual status/message, and minimal reproduction. Include only a sanitized trace from synthetic evidence. Specify whether the issue was observed in a real browser, a backend probe, or a mocked provider test. Never report a mocked test as proof of live model quality.
