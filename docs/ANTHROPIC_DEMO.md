# Anthropic in the hosted demo

The hosted public demo can run an opt-in analyst on Anthropic's Messages API with a key the **server** holds. Visitors never supply a key, never see one, and cannot turn the demo into a general chatbot: the system prompt and the tool list are pinned server-side, and every number in an answer is still calculated by the fixed DuckDB tools. The adapter is `src/investigator/anthropic_provider.py`; it uses `http.client` only (no SDK, no new dependency), like the OpenAI adapter in [OPENAI_BYOK.md](OPENAI_BYOK.md).

The cost discipline copies the owner's Paper Network chat Worker: a per-visitor rate limit, a service-wide daily cap on rounds, truncated message blocks, and the Anthropic console spend limit as the hard stop behind all of it. Locally, Anthropic works like OpenAI BYOK: select "Anthropic / your key", paste a key into the password field, and it travels to the loopback server under `X-Anthropic-Key` for that one request.

## How the hosted demo holds the key

1. `ANTHROPIC_API_KEY` is set only in the container's process environment. On Cloud Run the deploy workflow mounts it from Secret Manager by **name**: the repository variable `ANTHROPIC_SECRET_NAME` holds the secret's name (for example `anthropic-api-key`; empty means Anthropic is not mounted), `OPENAI_SECRET_NAME` does the same for OpenAI (no default: unset means OpenAI is not mounted, and the old default `openai-api-key` must now be named explicitly), and `PUBLIC_DEMO_LIVE_MODEL=1` enables live providers at all. The guards ride as repository variables too (`ANTHROPIC_MODEL`, `DEMO_RATE_LIMIT_PER_MIN`, `DEMO_DAILY_CAP`, `DEMO_MAX_TURNS`), and the workflow always sets `TRUST_PROXY=1` on Cloud Run. The key itself goes into Secret Manager once — `printf '%s' "$KEY" | gcloud secrets create anthropic-api-key --data-file=-` — and the Cloud Run runtime service account needs `roles/secretmanager.secretAccessor` on it (one `gcloud secrets add-iam-policy-binding` line, in [DEPLOYMENT.md](DEPLOYMENT.md)). No key is ever stored in GitHub. The app does not read `.env` files, never writes the key to disk, logs or responses, and the planner drops its reference when the investigation ends.
2. The demo must also set `PUBLIC_DEMO_LIVE_MODEL=1`. Without both, `/api/config` reports no live modes, the browser shows no **Ask the analyst** button, and a `mode: "anthropic"` request is answered `400`.
3. In public mode the server ignores `X-Anthropic-Key` and `X-OpenAI-Key` headers entirely. The visitor-key path exists on the loopback workbench only.
4. The key goes to one fixed destination, `POST https://api.anthropic.com/v1/messages`, with headers `x-api-key`, `anthropic-version: 2023-06-01` and `content-type: application/json`. No model-controlled URL exists.
5. Upstream failures never echo the provider's body. `401`/`403` become "misconfigured key", `429`/`529` become "the model is busy; retry in a minute", anything else an "upstream model error (HTTP n)".

## What one round sends

A visitor's question is always answered by the offline planner first, in one JSON response and tens of milliseconds; no model is involved and nothing below gates it. The model is a per-answer choice: **Ask the analyst** on the answer card starts one live round (`POST /api/ask` with `mode: "anthropic"`), which is exactly **two model calls and no loop** (issue #23):

1. **Plan.** One call returns a JSON object, `{"steps": [{"tool": ..., "arguments": {...}}, ...]}` (at most two steps per shift in scope plus three: five for one shift, nine for all three; `plan_step_cap` in `agent.py`, issue #78), or `{"unavailable": "<reason>"}`. The request carries no `tools`: `system` is the shared PROCESS_CONTEXT, the selected-scope rule, the planning rules with the step cap and the number of shifts in scope stated, and the tool catalog (every entry of `TOOL_SCHEMAS` except `render_chart`) as JSON; with all three shifts in scope the period tools come first in the catalog and the rules steer a which-shift question to `get_period_kpis` once per shift; the one user message is the question, production day, scope, selected shift and bounded history.
2. **The application checks the plan** (`validate_plan` in `agent.py`) before anything runs: every tool must be on the allowlist, every argument name must be in that tool's schema and every value must fit its declared type and enum, and every step must sit inside the selected shift and period, and the plan must have at least one step and no more than the cap. One bad step refuses the **whole** plan: no query runs, no narrative is written, the status is `plan_rejected`, and the offline answer stands with a one-line note and no "Investigation stopped" banner. A plan over the cap carries `plan_rejection: {proposed, limit}`, and the card reads *The analyst proposed N queries; the limit is M, so none ran* with `analyst · plan rejected · 1 model call` under it (the counts are fields, not digits in the answer text, which holds only tool numbers). A tool name the model wrote is quoted in that note only when it looks like an identifier.
3. **The tools run** in the plan's order, in application code, exactly as for an offline question; the trace has the same shape, so the query box and `/evals` show it unchanged. A value a tool refuses (a window outside the data) is a failed step in the trace, not a rejected plan.
4. **Narrative.** A second call receives the question and the compact evidence packet (previews of 12 rows, results named `r1`, `r2`, ...) and writes two to six lines, each `kind: text` with placeholders such as `{{r1.0.observed_shift_fpm}}`. The server substitutes each placeholder from the tool row as the text streams, so a reader never sees a number the model typed: a digit outside a placeholder stops that paragraph at once and withdraws it. When a line ends it goes through the same rules as before (`_render_paragraph` in `narrative.py`: cited evidence, preview rows only, no numbers in words, incomplete evidence supports limitations only), and only validated paragraphs are kept in the answer. Those rules prove every digit came from a tool row, not that the words about the digits are true, so a line is also checked for the order it asserts (issue #79, `ordering_contradicted`): a superlative (*fastest*, *most*, *best*) against the same-quantity values the line cites (a reason in one shift's breakdown against that breakdown's other rows, a shift's KPI against the other shifts'), a comparison (*below*, *more than*, *ahead of*) between the two values of the same field around it or the two names on either side (an actual against its target is two fields, so *above target* is not checked), and a direction (*rose*, *fell by*, *up from*) against the cited previous-window pair or the sign of a cited delta. A line whose own figures contradict it is withdrawn like any other failed line, the answer carries one limitation line *A comparison was withdrawn because it contradicted its own figures.*, and `usage.comparisons_withdrawn` counts the withdrawn lines. A claim whose subject the rules cannot pin down, a negated claim and a question are left alone.

Both calls send `stream: true`, `output_config: {effort: "low"}` and thinking off (`{type: "disabled"}` on `claude-haiku-5-5`, `{type: "between_tools"}` on `claude-sonnet-5-5`, which rejects `disabled`); `max_tokens` is 1,024 for the plan and 2,048 for the narrative. Sampling parameters and a forced tool choice are not sent (both models reject them). Each request is bounded to 64 KB and each response to 512 KB, with a 20-second socket timeout; the round as a whole is bounded to **2 calls and 10 seconds**. A narrative cut off by the clock or by `max_tokens` keeps its finished paragraphs and says one was cut. `max_tokens` on the plan, a `refusal` stop and any upstream error execute nothing.

### The response is a stream

A live mode answers `200` with `Content-Type: application/x-ndjson`, one JSON event per line, chunked and flushed as each happens (offline keeps its single JSON document; a request refused before the round starts is still a plain `400` or `429`):

| Event | When | Carries |
| --- | --- | --- |
| `start` | the round is accepted | `mode`, `session_id` |
| `plan` | the plan passed the check | `steps` |
| `step` | a tool starts and finishes | `phase` (`start` / `finish`), `index`, `tool`, `arguments`; on finish `ok`, `rows`, `ms`, `result_id` |
| `delta` | narrative text arrives | `paragraph`, `kind`, `text` (values already substituted) |
| `paragraph` | a line passed validation | `paragraph`, `kind`, `text`, `citations`: replaces the preview |
| `retract` | a line failed validation | `paragraph`, `reason`: the preview is removed (for a contradicted ordering the reason is the limitation line the answer then shows) |
| `done` | the end | `result`: the same object an offline answer returns, with `narrative`, `usage.model_calls`, `usage.first_token_ms` and `usage.comparisons_withdrawn` |
| `error` | the round failed | `error` |

### Off the shared lock

Offline investigations run one at a time under `app.lock`, for milliseconds. A live round never holds that lock across a model call: it is taken only while one of the round's tools reads the database and while the conversation context is updated (the agent's `guard`). Live rounds have their own semaphore, **two in flight per container** (`LIVE_CONCURRENCY`); a third is answered `429` with `{"code": "busy"}` and `Retry-After: 3` instead of queueing. The server is one thread per request in both modes, so an offline question that arrives while an analyst call is in flight is answered at once (`tests/test_analyst.py` holds a stubbed model call for five seconds and requires the offline answers in under 250 ms). When the visitor's connection closes, the next stream event from the provider cancels the call: the provider connection is closed and the slot is released. Cancellation is checked between stream events, not during a blocked read, so a call that has gone quiet ends at its own deadline instead.

OpenAI mode (loopback, your key; or the hosted demo with an OpenAI server key) keeps the tool loop of up to ten calls, but runs through the same stream and the same guard: `step` events as tools finish, no `plan` or `delta`, then `done`.

## The four environment variables

All four are **off unless set** so the loopback workbench is unchanged.

| Variable | Default | Effect |
| --- | --- | --- |
| `DEMO_RATE_LIMIT_PER_MIN` | unset (off) | Maximum `/api/ask` rounds per client IP in a fixed 60-second window (the Worker uses 20). Over the limit: `429`, header `Retry-After`, body `{"error": "too many questions, slow down", "code": "rate_limited"}`. Offline rounds count too, including a URL answer (`/corr/….json` or `format=pdf`, issue #20); the page, the `/q` redirect and `/api/report` do not. |
| `DEMO_DAILY_CAP` | unset (off) | Maximum live-model rounds per UTC day for **everyone together** (the Worker uses 400). Counted in `artifacts/model-budget.sqlite3`, table `demo_rounds`, before the model is asked, so a failed round still counts. Over the cap: `429`, `Retry-After` until UTC midnight, body `{"error": "today's budget for the chat is used up", "code": "budget"}`. Offline rounds stay free and uncounted. |
| `DEMO_MAX_TURNS` | unset (4) | Number of question/scope entries carried into the next question's context (1-50). The Worker's equivalent is `MAX_TURNS`. |
| `TRUST_PROXY` | unset (off) | `1` reads the client IP from the **rightmost** `X-Forwarded-For` entry, which is the one a single trusted proxy appends; Cloud Run sits behind such a proxy. Unset, the header is ignored and every request behind a proxy shares one address, which makes the rate limit service-wide rather than per visitor. Never set it when the app is reachable without a proxy. |

### What the counters cover: one container each

Every guard in the table is counted **per container**, and none is shared between containers:

| Counter | Where it lives | Survives a restart of the same container | Shared across Cloud Run instances | Reset by a deploy or scale-to-zero |
| --- | --- | --- | --- | --- |
| `DEMO_RATE_LIMIT_PER_MIN` window | process memory | no | no | yes |
| Live-round semaphore (2 in flight) | process memory | no | no | yes |
| `DEMO_DAILY_CAP` rounds (`demo_rounds`) | `artifacts/model-budget.sqlite3` on the container's own disk | yes | no | yes |
| Monthly `Budget` reservations (`budget`) | the same file | yes | no | yes |

Cloud Run runs up to two instances (`--max-instances 2`), scales to zero when idle, and gives each new instance an empty `artifacts/` unless a volume is mounted. So the effective ceilings are: daily cap up to **2 × `DEMO_DAILY_CAP`** while two instances are up, and again from zero after each cold start or deploy; the $5 monthly ledger likewise per instance and per cold start; two live rounds in flight per instance, four in total. A round is counted before the model is asked and a reservation is never refunded, so within one container the ledger only overstates. Across containers it understates, which is why the console spend limit below, not these counters, is the stop.

Related: `ANTHROPIC_MODEL` selects `claude-haiku-5-5` (default, cheapest) or `claude-sonnet-5-5`; any other value stops the server at boot. The monthly `Budget` ledger (at most $5 per UTC month per installation, shared across providers) still reserves a guard amount before every call, about twice that call's worst case: $0.002 for Haiku 5.5, $0.03 for Sonnet 5.5, $0.02 for OpenAI. Invalid or oversized values of the demo variables stop the server at boot rather than guessing.

`/api/config` reports `live_modes` (which providers the server's own key enables), `live_models` and `demo_guards`, so the interface shows only what is configured.

## The console spend limit is the stop

Every control above is a per-process or per-container ledger. Cloud Run can run two instances and recreates `artifacts/` on each deploy unless persistent storage is mounted, so the counters are **discipline, not a guarantee**. Set a monthly spend limit on the Anthropic console for a key used only by this demo, set it to what you are willing to lose, and alert below it. The displayed estimates ignore cache discounts and thinking tokens are billed as output.

## Cost per round

List prices read from Anthropic's pricing on 2026-10-22 (display estimates; recheck before changing the model):

| Model | Input $/MTok | Output $/MTok | Typical round (2 calls, ~3K input and ~300 output tokens each) | Worst case (2 calls at the 64 KB request cap, 1,024 planning / 2,048 narrative output tokens) |
| --- | ---: | ---: | ---: | ---: |
| `claude-haiku-5-5` (default) | 0.10 | 0.50 | about $0.001 | about $0.006 |
| `claude-sonnet-5-5` | 2.00 | 10.00 | about $0.02 | about $0.12 |

Haiku 5.5 prices above are the rate card for prompts of 100K tokens or fewer; this adapter never sends more than 64 KB (under 20K tokens) per request. The worst case is arithmetic on the caps (two requests of 64 KB at 3 bytes per token, plus 3,072 output tokens); the typical figures are estimates until the live goldens are re-recorded in the two-call shape, when `evals/run.py --live` prints the recorded cost per case. At the Worker's 400 rounds per day Haiku 5.5 stays well under $1 a day in the typical case and about $2.50 at the worst case; Sonnet 5.5 is roughly twenty times that.

The reservation taken before each call is sized from the request actually sent (about twice the list-price worst case for that request: input estimated at 3 bytes per token, output at the call's `max_tokens`, floor $0.0005), so a two-call Haiku round reserves roughly $0.004-0.006 and the $5 monthly `Budget` guard allows on the order of 800-1,200 rounds per UTC month **per container** before answering "Monthly application model budget exhausted" (status `planner_error` in the `done` event, not a `429`). A Sonnet round reserves about twenty times as much. Reservations are never refunded, so the ledger always overstates spend. Serving the Worker's volume for a full month therefore needs a deliberate decision to raise the `Budget` ceiling in `openai_provider.py` (and the console limit with it), or a lower `DEMO_DAILY_CAP`; nothing here changes that ceiling silently.

## What is not verified

The two-call analyst is tested against simulated streams (`tests/test_analyst.py`: the plan check, the streamed validator, cancellation, the stream's event order, the concurrency test) and rendered in a browser against a local server with a scripted stream. It has **not yet been run against the real endpoint from this repository**: the request shape (`stream`, the per-model `thinking` value, no tools) follows the current API reference, and the first `python3 evals/run.py --live --provider anthropic` will confirm it or return an HTTP 400 that names the field. That run also re-records the live goldens in the new shape (stamped `shape: "plan"`), checks that an answer with a narrative used exactly two model calls, and prints the round time and the time to the first narrative token; with `--track` they are written to `history.jsonl` as `live_ms`. Until then `evals/fixtures/anthropic/` holds the recordings made with the earlier tool loop (`AnthropicPlanner`, kept in `anthropic_provider.py` only to replay them), and those are what CI replays. See [DEPLOYMENT.md](DEPLOYMENT.md) for the Cloud Run environment.
