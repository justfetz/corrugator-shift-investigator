# Agent contract and teaching workbench

## Purpose and trigger

A user question wakes one bounded investigation. The agent requests evidence, optionally charts it, and returns a report. This is a learning/demo agent over synthetic records, with no scheduling or equipment authority.

## Current execution mode

The default OfflinePlanner is rule-based and understands only supported patterns. Optional OpenAIPlanner implements native Responses function calling through the same dispatcher; see OPENAI_BYOK.md. AnthropicPlanner implements the same Planner contract over the Messages API with tool_use blocks and runs the hosted demo with a server-held key behind per-IP and daily round guards; see ANTHROPIC_DEMO.md. PeriodReports is a separate deterministic aggregation path. Live quality is scored by the golden set's live cases; the Anthropic recordings replay in CI, the OpenAI ones are not recorded.

The OpenAI adapter implements Planner.next_step(context), returning either a tool request or a final list of evidence IDs. The application validates both. The answer renderer retains deterministic calculated evidence. After successful daily investigation, the OpenAI adapter can write a bounded narrative with fact placeholders. Application code validates field references and substitutes their values. It labels prose as model interpretation; schema/reference checks do not prove its meaning.

## Context and memory

Application-owned production day, selected shift, a fixed process reference, available tool schemas and at most four recent question/scope entries accompany current-run evidence. Generated data and hidden scenario answers are not put into the prompt. Changing the day or dataset version clears incompatible history. Tool evidence IDs exist only for one investigation; a chart cannot reference another session/run's ID.

No conversation or subscriber information is persisted. The local server retains at most 32 in-memory contexts and three loaded day/scenario datasets. Browser controls own date/shift selection; the follow-up parser preserves the selected day. Ambiguous date-range questions fail explicitly in offline mode.

## Tools and permissions

- get_shift_kpis: output, gross area, downtime by kind, waste, throughput and lineal per setup/run.
- get_downtime_breakdown: allowed kind filter and grouping by reason, place or kind.
- get_wet_end_performance: actual elapsed-time speed versus grade-specific target.
- get_quality_breakdown: rejected area by the expanded warp, bond and misalignment observation categories.
- get_shift_overview: KPI row plus largest losses and three chart evidence components from fixed queries.
- get_order_matrix: dimensions, outs, cuts, requested/planned/produced sheets and remaining quantities per fragment (the record families carry no order ledger, so requested and remaining sheets are null).
- render_chart: supported bar or line specification referencing a successful result from this run.

Period tools read the whole record store (two production months, Monday to Friday) instead of the selected day. Each takes a `window` of `{"period": "day|week|month|fiscal", "anchor": "YYYY-MM-DD"}` (fiscal = the 4-4-5 accounting period; KPI envelopes flag unequal production-day counts) — week is the Mon–Fri production week containing the anchor, month the calendar month, and the previous window is computed the same way — plus an optional `shift` (null = all three shifts; under Selected shift only the selected shift is enforced in application code). All are read-only, bounded (fixed queries, bound values, 1,000-row limit, two-second interrupt), and return `window`, `previous_window`, `coverage`, `source_rows` and rows whose percentages are ratios of sums (METRICS.md).

- get_period_kpis(window, compare, shift): the dashboard tiles with current, previous, delta, `better` direction and a `change` label; deltas withheld unless both windows are complete.
- compare_periods(current_window, previous_window, shift): the same table for two arbitrary windows.
- get_downtime_pareto(window, group_by reason|place|kind, measure minutes|count, shift): ordered with share and cumulative share, previous-window minutes alongside.
- get_waste_breakdown(window, shift): trim, shear, bond, warp, misalignment, moisture, bale and unexplained (plus `details`: reported sub-reasons with share of dry-end) as sq ft and % of gross, with the previous window.
- get_stops_profile(window, shift): stops, minutes and minutes per stop per production day plus the Mon–Fri weekday profile.
- get_trend(metric, weeks 1–26, anchor, shift): weekly series of one allow-listed metric.
- get_grade_totals(window, shift): per board grade, setups, wet ends, lineal ft, volume MSF and share, speed without stops and elapsed speed from the setup clocks, % of grade target, allocated stops and minutes, lineal per setup and per paper change, trim and dry-end %, previous window's volume.
- get_overrun(window, shift): nominal versus actual cuts per knife (cuts, sheets, % of nominal) with the share of customer lines inside, over and under the ±2 % band; last row all knives.
- get_speed_opportunity(window, slice_by width|crew, shift): per grade and width (or crew), typical speed without stops vs the best fifth of setups, % of target for both, gap, opportunity hours, dry-end % of the fast fifth vs the rest; short setups excluded and counted; no temperatures modelled.

Forbidden: shell, arbitrary SQL/code, external URLs, filesystem browsing, machinery control, schedule changes, subscriber access or email sending. SQL values are bound separately and grouping identifiers are allowlisted.

## Bounds, failures and done

Default offline bounds: 20 tool calls and a ten-second loop deadline checked between planner steps. The server configures live mode for ten tool calls and 60 seconds; the adapter also caps model calls at ten and uses bounded socket timeouts. Agent context is limited to 200,000 serialized characters; the live serialized request limit is 24 KB for the OpenAI adapter and 64 KB for the Anthropic adapter, which trims the oldest tool results to a stub before refusing a request. DuckDB queries use a two-second interrupt timer and a 1,000-row limit. Failed tool validation consumes a call. Period aggregation bypasses the agent loop and is bounded by the fixed demo calendar rather than these loop deadlines.

These are local-demo controls. The synchronous planner deadline does not preempt a blocked third-party adapter; any future adapter must enforce its own HTTP timeout, token/output limits and an application-wide spending reservation before making calls. Offline mode makes no model calls. Explicit OpenAI mode reserves spending before each request; no real API calls have been made during automated validation.

Intended done condition: supported analysis has successful referenced evidence and any requested charts. Finish validates successful evidence IDs. Explicit chart/graph/plot requests without a chart return partial, and overview evidence automatically supplies its three charts. Semantic fulfillment is not guaranteed. See review R4. Incomplete data must be disclosed and must not be ranked as valid performance. A daily run can be complete while its evidence coverage is incomplete. Period status is partial when its window has missing/invalid contributions. A recorded symptom does not establish a mechanical cause.

## Interface and transport

One screen offers date/shift scope, questions, calculated metrics, charts, report download and expandable tool/context/SQL traces. It labels synthetic data and offline planning. No fabricated email signup/success flow exists.

The server binds only to 127.0.0.1. It enforces Host, Origin, a random request token, JSON content type, 16 KB request bodies and a shared 60-requests/minute limit. Static files have an explicit allowlist and a restrictive content security policy. Labels are rendered as text, not HTML. This single-process teaching server is not suitable for public hosting.

Optional WebMCP registration invokes the same visible investigation action and validates its question argument. Browsers without support use the ordinary interface. Browser-level WebMCP execution has not been verified; no claim of that validation is made.

## Tests and limits

Tests exercise a full investigation, follow-up scope, all-shift comparison, exact chart/evidence equality, missing evidence, forbidden tools, call/deadline limits, incomplete records, history retention, date changes and HTTP protections. Unit/HTTP tests and JavaScript syntax checks do not replace visual browser QA or live-model evaluation.

Still pending: live-provider validation, public service-wide budget infrastructure, email provider and verified subscriptions, plant timezone/DST, overlapping-stop and cross-shift allocation, production security/deployment and broader scenario calibration. Read the implementation follow-up in REVIEW_2026-09-07.md for repaired PDF/history/guidance defects and remaining semantic-evaluation work.


## Evidence-reporting extension

New read tools: get_setup_matrix(shift), get_shift_notes(shift), get_rankings(shift, metric). Metric is restricted to speed/downtime/quality. Notes are bounded to 1,000 characters, passed as untrusted evidence, and rendered with text nodes or escaped PDF paragraphs. They cannot expand tools or shift scope. Selected-shift mode is enforced in application code even for a malicious model call. Daily OpenAI matrix results show the model at most 12 preview rows with explicit disclosure; the UI retains the full validated matrix.

The Period control is part of the scope. Under Week, Month or 4-4-5 the agent passes the selected window to the planner (`Context.period`; the offline planner uses it as the default window and turns day-shaped questions into their period form) and refuses any daily tool with the window to use instead; a window the question names still wins. Week, calendar-month and 4-4-5 **reports** are a separate deterministic job triggered by the Period report button (`report: true` on `/api/ask`). It aggregates validated per-day SQL results for the bounded Monday–Friday calendar of the record store (3 August – 30 September 2026), returning sections, tables, heatmaps, charts, coverage and dataset hashes. No model/API permission exists on this path. At most 60 compact day/scenario snapshots are cached, with no live DB connection retained. These reports do not pretend to understand arbitrary free-text range questions. Period comparison requires both windows to be complete; no interpolation or zero-fill is used. Paper-change totals count distinct wet-end IDs within each day and shift, then sum across the period. The synthetic year uses September 1, 2026 as its provisional 4-4-5 anchor pending owner confirmation.

The server retains at most 32 latest investigation reports for PDF export. The /api/report POST accepts only session_id and run_id; PDF layout is trusted application code, not submitted HTML. ReportLab is the only added application dependency. PDF export neither sends email nor incurs model cost. Host, Origin, token, request-size and rate checks apply to both POST routes. By default this is a loopback demonstration; the hosted public-demo mode (docs/DEPLOYMENT.md) swaps the loopback Host rule for an Origin allowlist, serializes investigations under a lock, and never accepts visitor keys. It is still a demo, not a multi-tenant service.
