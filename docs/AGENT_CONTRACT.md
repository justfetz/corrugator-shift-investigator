# Agent contract and teaching workbench

## Purpose and trigger

A user question wakes one bounded investigation. The agent requests evidence, optionally charts it, and returns a report. This is a learning/demo agent over synthetic records, with no scheduling or equipment authority.

## Current execution mode

The current Planner implementation is explicitly rule-based and offline. It exercises the same tool-dispatch boundary intended for a future model adapter. It is not an LLM and understands only the supported examples/patterns. An optional OpenAIPlanner now implements native Responses function calling; see OPENAI_BYOK.md. Live account access and model-quality evaluation remain open.

The OpenAI adapter implements Planner.next_step(context), returning either a tool request or a final list of evidence IDs. The application validates both. The current answer renderer formats verified results deterministically; it never accepts an arbitrary model-written numerical claim as a verified metric.

## Context and memory

Application-owned production day, selected shift, a fixed process reference, available tool schemas and at most four recent question/scope entries accompany current-run evidence. Generated data and hidden scenario answers are not put into the prompt. Changing the day or dataset version clears incompatible history. Tool evidence IDs exist only for one investigation; a chart cannot reference another session/run's ID.

No conversation or subscriber information is persisted. The local server retains at most 32 in-memory contexts and three loaded day/scenario datasets. Browser controls own date/shift selection; the follow-up parser preserves the selected day. Ambiguous date-range questions fail explicitly in offline mode.

## Tools and permissions

- get_shift_kpis: output, gross area, downtime by kind, waste, throughput and lineal per setup/run.
- get_downtime_breakdown: allowed kind filter and grouping by reason, place or kind.
- get_wet_end_performance: actual elapsed-time speed versus grade-specific target.
- get_quality_breakdown: rejected area by Warp, Bond or Misalignment.
- render_chart: supported bar specification referencing a successful result from this run.

Forbidden: shell, arbitrary SQL/code, external URLs, filesystem browsing, machinery control, schedule changes, subscriber access or email sending. SQL values are bound separately and grouping identifiers are allowlisted.

## Bounds, failures and done

Maximum 20 tool calls per turn; ten-second loop deadline checked between planner steps; at most 200,000 serialized context characters. DuckDB queries use a two-second interrupt timer and a 1,000-row limit. Failed validation produces a structured tool error and consumes a call. No infinite retries. Planner exceptions, invalid final evidence, exhausted budgets or deadlines return a partial/failed status, never a fabricated success.

These are local-demo controls. The synchronous planner deadline does not preempt a blocked third-party adapter; any future adapter must enforce its own HTTP timeout, token/output limits and an application-wide spending reservation before making calls. Offline mode makes no model calls. Explicit OpenAI mode reserves spending before each request; no real API calls have been made during automated validation.

Done means the requested supported analysis has successful referenced evidence and any requested charts. Incomplete data must be disclosed and must not be ranked as valid performance. Escalate missing definitions/data or unsupported requests instead of guessing. A recorded symptom does not establish a mechanical cause.

## Interface and transport

One screen offers date/shift scope, questions, calculated metrics, charts, report download and expandable tool/context/SQL traces. It labels synthetic data and offline planning. No fabricated email signup/success flow exists.

The server binds only to 127.0.0.1. It enforces Host, Origin, a random request token, JSON content type, 16 KB request bodies and a shared 60-requests/minute limit. Static files have an explicit allowlist and a restrictive content security policy. Labels are rendered as text, not HTML. This single-process teaching server is not suitable for public hosting.

Optional WebMCP registration invokes the same visible investigation action and validates its question argument. Browsers without support use the ordinary interface. Browser-level WebMCP execution has not been verified; no claim of that validation is made.

## Tests and limits

Tests exercise a full investigation, follow-up scope, all-shift comparison, exact chart/evidence equality, missing evidence, forbidden tools, call/deadline limits, incomplete records, history retention, date changes and HTTP protections. Unit/HTTP tests and JavaScript syntax checks do not replace visual browser QA or live-model evaluation.

Still pending: live-provider validation, public service-wide budget infrastructure, range/trend tools, email provider and verified subscriptions, plant timezone/DST, overlapping-stop allocation, production security/deployment and broader scenario calibration.
