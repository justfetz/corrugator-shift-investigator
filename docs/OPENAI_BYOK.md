# OpenAI and bring-your-own-key mode

The local workbench defaults to a free offline planner. Selecting OpenAI uses real Responses API function calls through the same validated tools. The adapter is implemented and tested with simulated provider responses; real account/model access and live-model quality have not yet been verified.

## Use your account locally

Select OpenAI in the mode control. Enter your key in the password field and submit a question. The browser sends it to the loopback server in a request header; the server forwards it only to api.openai.com. The field clears on submit. The key is not stored in localStorage, cookies, conversation context, files or logs. It is used for that investigation and then the planner releases its reference. This is request-scoped handling, not a claim of cryptographic memory erasure.

For repeated development questions, you can instead supply OPENAI_API_KEY in the server process environment. The app does not auto-load .env files. A server-side key stays on the server; do not paste it into chat, commit it, or put it in frontend code. The included .env.example contains no key.

OpenAI receives the question, bounded conversation context and compact synthetic tool results. store=false is set on Responses requests; this is not a promise of zero provider retention. See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

## Low-cost default

The model is pinned to gpt-4.1-mini-2025-04-14. Its official listed standard text rates, checked September 6, 2026, are $0.40 per million input tokens and $1.60 per million output tokens. It supports Responses and function calling. This is an initial cost/latency choice, not a demonstrated winner on our evaluations. See [model documentation and pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

The narrative call shares all existing limits and reserves another $0.02 when attempted. The adapter has no automatic retries, no built-in paid tools, a 24 KB serialized request limit, max_output_tokens=1024, at most ten model calls per investigation and a 60-second planner lifetime. Socket operations have at most a 20-second timeout; this is not a hard total-request wall-clock deadline against a slowly streaming endpoint. The server is a local single-user demonstration, not public infrastructure.

Before each API attempt, reserve $0.02 in artifacts/model-budget.sqlite3. Reservations are atomic and persist across restarts. The default cap is $5 per UTC calendar month for this installation, shared across server-key and BYOK requests. Ten calls reserve at most $0.20 per investigation. Reservations are conservative guard amounts, not invoices; they are not refunded on failures or missing usage because the provider may already have processed a request. Reported cost estimates use returned input/output token counts and ignore cache discounts.

This guard depends on the pinned model/rates and request limits; recheck rates before changing the model. It does not cap unrelated account usage or replace provider billing controls. Deleting the budget database resets local accounting, so do not delete it to bypass the cap. Public deployment needs a durable service-wide budget ledger and visitor controls.

## What the model actually does

The first request contains process instructions, selected day/shift, bounded history and strict function schemas. Each response selects one function. The application executes it and sends a compact function_call_output using the provider's call_id. The full provider output is retained within that investigation to preserve the tool conversation. parallel_tool_calls=false keeps the loop easy to inspect.

finish returns successful evidence IDs. The application formats a calculated answer, then makes one additional bounded write_narrative call. The returned paragraphs use finding/hypothesis/check/limitation labels and field references. Python validates references and inserts their values into placeholders; the model cannot supply authoritative metric values. Invalid or failed narratives retain the calculated report. cannot_answer stops unsupported requests and displays supported next steps. The model cannot create arbitrary SQL, supply chart rows, read keys or send reports. See [official function-calling guide](https://developers.openai.com/api/docs/guides/function-calling).

## BYOK on a public site

This implementation demonstrates BYOK on your own machine only. A hosted proxy would necessarily handle visitors' keys, even if briefly. Before offering that publicly, implement TLS, authentication/abuse controls, request-log redaction, a clear data-use notice, and an appropriate credential design. No public key-collection endpoint has been deployed. Email subscriptions remain separate and are not implemented by this adapter.

## Tests before a real key

Use [TESTING.md](TESTING.md) for the evaluation questions and scoring rubric, including broad improvement requests, unsupported savings claims, missing charts, scope escapes and machinery-action requests. Read [REVIEW_2026-09-07.md](REVIEW_2026-09-07.md) for the original findings and implementation follow-up. Comparison history and generic unavailable guidance are repaired; explicit missing charts produce partial status. Semantic fulfillment and live prose quality still require evaluation. Do not infer live quality from the mocked tests or from the presence of an API key.

Mocked tests verify strict schemas, native function outputs, fixed model and storage settings, compact evidence, key omission from payloads/traces, incomplete responses, request limits, and concurrent/persistent budget reservations. They do not establish model reasoning quality or live endpoint compatibility. The next manual check is one deliberate question with your key, followed by the evaluation questions in the local workbench walkthrough.
