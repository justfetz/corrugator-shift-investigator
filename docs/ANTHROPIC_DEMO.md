# Anthropic in the hosted demo

The hosted public demo can run the investigation planner on Anthropic's Messages API with a key the **server** holds. Visitors never supply a key, never see one, and cannot turn the demo into a general chatbot: the system prompt and the tool list are pinned server-side, and every number in an answer is still calculated by the fixed DuckDB tools. The adapter is `src/investigator/anthropic_provider.py`; it uses `http.client` only (no SDK, no new dependency), like the OpenAI adapter in [OPENAI_BYOK.md](OPENAI_BYOK.md).

The cost discipline copies the owner's Paper Network chat Worker: a per-visitor rate limit, a service-wide daily cap on rounds, truncated message blocks, and the Anthropic console spend limit as the hard stop behind all of it. Locally, Anthropic works like OpenAI BYOK: select "Anthropic / your key", paste a key into the password field, and it travels to the loopback server under `X-Anthropic-Key` for that one request.

## How the hosted demo holds the key

1. `ANTHROPIC_API_KEY` is set only in the container's process environment. On Cloud Run the deploy workflow mounts it from Secret Manager by **name**: the repository variable `ANTHROPIC_SECRET_NAME` holds the secret's name (for example `anthropic-api-key`; empty means Anthropic is not mounted), `OPENAI_SECRET_NAME` does the same for OpenAI (default `openai-api-key`), and `PUBLIC_DEMO_LIVE_MODEL=1` enables live providers at all. The guards ride as repository variables too (`ANTHROPIC_MODEL`, `DEMO_RATE_LIMIT_PER_MIN`, `DEMO_DAILY_CAP`, `DEMO_MAX_TURNS`), and the workflow always sets `TRUST_PROXY=1` on Cloud Run. The key itself goes into Secret Manager once — `printf '%s' "$KEY" | gcloud secrets create anthropic-api-key --data-file=-` — and the Cloud Run runtime service account needs `roles/secretmanager.secretAccessor` on it (one `gcloud secrets add-iam-policy-binding` line, in [DEPLOYMENT.md](DEPLOYMENT.md)). No key is ever stored in GitHub. The app does not read `.env` files, never writes the key to disk, logs or responses, and the planner drops its reference when the investigation ends.
2. The demo must also set `PUBLIC_DEMO_LIVE_MODEL=1`. Without both, `/api/config` reports no live modes, the browser removes the Anthropic option, and a `mode: "anthropic"` request is answered `400`.
3. In public mode the server ignores `X-Anthropic-Key` and `X-OpenAI-Key` headers entirely. The visitor-key path exists on the loopback workbench only.
4. The key goes to one fixed destination, `POST https://api.anthropic.com/v1/messages`, with headers `x-api-key`, `anthropic-version: 2023-06-01` and `content-type: application/json`. No model-controlled URL exists.
5. Upstream failures never echo the provider's body. `401`/`403` become "misconfigured key", `429`/`529` become "the model is busy; retry in a minute", anything else an "upstream model error (HTTP n)".

## What one round sends

A round is one `/api/ask` request. Inside it the planner may make up to ten model calls within 60 seconds (tool choices plus one narrative call), each bounded to a 64 KB serialized request and a 512 KB response with a 20-second socket timeout. When the running history would push a request past 64 KB, the adapter first trims the oldest tool results to a stub (result id, tool, ok, row count, and a note) so the model can still cite them; only a request that does not fit after trimming is refused. The newest tool result is never trimmed.

- `system`: the shared PROCESS_CONTEXT, the selected-scope rule and the same planner instructions the OpenAI adapter sends.
- `tools`: every entry of `TOOL_SCHEMAS` with its `parameters` mapped to Anthropic `input_schema` (all properties required, nullable filters as `anyOf`) plus `finish` and `cannot_answer`. Strict mode is **off** for every tool: the API caps strict tools at 20 per request and also caps the total compiled grammar, and on 2026-10-22 the 21-tool planner hit the first and a 19-strict-tool planner hit the second, failing every live golden. The application validates every tool call's arguments itself (a wrong shift, window, enum or type is refused and the error goes back to the model as the tool result, costing one of its ten calls), so strict mode was a convenience, not the gate; `STRICT_TOOLS` in `anthropic_provider.py` can opt a few small schemas back in.
- `tool_choice: {type: "auto", disable_parallel_tool_use: true}` and `output_config: {effort: "low"}`; `max_tokens` 1024. Sampling parameters are not sent: on `claude-haiku-5-5` and `claude-sonnet-5-5` any non-default `temperature` is rejected (HTTP 400), and Sonnet 5.5 also rejects a forced tool choice, so determinism is steered with low effort and the prompt instead.
- `messages`: the question, production day, selected shift and bounded history; then each assistant turn echoed unchanged (thinking blocks included) followed by one `tool_result` carrying the compact tool evidence (no trace, no SQL, no source IDs, matrices previewed to 12 rows).

A `tool_use` block becomes `{tool, arguments}` for the application dispatcher, which validates it exactly as it validates the offline planner. `finish` returns the evidence IDs; `cannot_answer` ends with the standard unavailable message; a text-only `end_turn` closes with the successful evidence gathered so far. `max_tokens` and `refusal` stops execute nothing.

## The four environment variables

All four are **off unless set** so the loopback workbench is unchanged.

| Variable | Default | Effect |
| --- | --- | --- |
| `DEMO_RATE_LIMIT_PER_MIN` | unset (off) | Maximum `/api/ask` rounds per client IP in a fixed 60-second window (the Worker uses 20). Over the limit: `429`, header `Retry-After`, body `{"error": "too many questions, slow down", "code": "rate_limited"}`. Offline rounds count too; `/api/report` does not. |
| `DEMO_DAILY_CAP` | unset (off) | Maximum live-model rounds per UTC day for **everyone together** (the Worker uses 400). Counted in `artifacts/model-budget.sqlite3`, table `demo_rounds`, before the model is asked, so a failed round still counts. Over the cap: `429`, `Retry-After` until UTC midnight, body `{"error": "today's budget for the chat is used up", "code": "budget"}`. Offline rounds stay free and uncounted. |
| `DEMO_MAX_TURNS` | unset (4) | Number of question/scope entries carried into the next question's context (1-50). The Worker's equivalent is `MAX_TURNS`. |
| `TRUST_PROXY` | unset (off) | `1` reads the client IP from the **rightmost** `X-Forwarded-For` entry, which is the one a single trusted proxy appends; Cloud Run sits behind such a proxy. Unset, the header is ignored and every request behind a proxy shares one address, which makes the rate limit service-wide rather than per visitor. Never set it when the app is reachable without a proxy. |

Related: `ANTHROPIC_MODEL` selects `claude-haiku-5-5` (default, cheapest) or `claude-sonnet-5-5`; any other value stops the server at boot. The monthly `Budget` ledger (at most $5 per UTC month per installation, shared across providers) still reserves a guard amount before every call, about twice that call's worst case: $0.002 for Haiku 5.5, $0.03 for Sonnet 5.5, $0.02 for OpenAI. Invalid or oversized values of the demo variables stop the server at boot rather than guessing.

`/api/config` reports `live_modes` (which providers the server's own key enables), `live_models` and `demo_guards`, so the interface shows only what is configured.

## The console spend limit is the stop

Every control above is a per-process or per-container ledger. Cloud Run can run two instances and recreates `artifacts/` on each deploy unless persistent storage is mounted, so the counters are **discipline, not a guarantee**. Set a monthly spend limit on the Anthropic console for a key used only by this demo, set it to what you are willing to lose, and alert below it. The displayed estimates ignore cache discounts and thinking tokens are billed as output.

## Cost per round

List prices read from Anthropic's pricing on 2026-10-22 (display estimates; recheck before changing the model):

| Model | Input $/MTok | Output $/MTok | Typical round (3-4 calls, ~2.5K input and ~400 output tokens each) | Worst case (10 calls at the 64 KB request cap, 2,048 planning / 4,096 narrative output tokens each) |
| --- | ---: | ---: | ---: | ---: |
| `claude-haiku-5-5` (default) | 0.10 | 0.50 | about $0.002 | about $0.03 |
| `claude-sonnet-5-5` | 2.00 | 10.00 | about $0.04 | about $0.57 |

Haiku 5.5 prices above are the rate card for prompts of 100K tokens or fewer; this adapter never sends more than 64 KB (under 20K tokens) per request. At the Worker's 400 rounds per day, Haiku 5.5 stays under about $1 a day in the typical case and about $12 at the worst case; Sonnet 5.5 is roughly twenty times that. Note the interaction with the monthly `Budget` guard, which the code caps at $5 per installation. The reservation taken before each call is sized from the request actually sent (about twice the list-price worst case for that request: input estimated at 3 bytes per token, output at the call's `max_tokens`, floor $0.0005), so a typical Haiku call reserves $0.003-0.004 and a typical four-call round about $0.013; $5 therefore allows roughly 1,400 Haiku calls, about 350-400 rounds, per UTC month per container before answering "Monthly application model budget exhausted" (a `200` with status `planner_error`, not a `429`). That is well under 400 rounds a day. A Sonnet demo reserves about $0.06 per typical call and reaches the same wall after about 80 calls. Reservations are never refunded, so the ledger always overstates spend. Serving the Worker's volume therefore needs a deliberate decision to raise the `Budget` ceiling in `openai_provider.py` (and the console limit with it), or a lower `DEMO_DAILY_CAP`; nothing here changes that ceiling silently.

## What is not verified

The adapter is tested against simulated responses (`tests/test_anthropic_provider.py`) and the guards against a live loopback server (`tests/test_server.py`). No request has been made to the real endpoint from this repository, so live quality, latency and the exact token counts remain to be observed with one deliberate question before the demo is opened to anyone else. See [DEPLOYMENT.md](DEPLOYMENT.md) for the Cloud Run environment.
