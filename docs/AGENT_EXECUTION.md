# How a question becomes evidence and a chart

This describes the implemented code as reviewed September 7, 2026. It replaces the original proposed first-slice design. See [the agent contract](AGENT_CONTRACT.md) for permissions, [metrics](METRICS.md) for formulas, and [the review](REVIEW_2026-09-07.md) for known defects.

## Three execution paths

1. **Production day / Offline:** the application parses a supported question pattern and runs a predetermined bounded tool plan.
2. **Production day / OpenAI:** the provider chooses one function at a time; application code validates scope, executes queries, and formats the numerical answer.
3. **Week, month, accounting:** the server calls PeriodReports directly. There is no model call, natural-language planning, or provider permission on this path.

Choosing OpenAI does not add new calculation tools, dates, or production authority. It changes who chooses the next permitted tool and adds a bounded narrative after successful evidence retrieval.

## Follow one request

In the UI, choose September 14, shift 2, Selected shift only, Production day, and Offline. Ask "Why was this shift slow? Chart downtime by reason."

The browser POSTs JSON to /api/ask with question, day, shift, scope, period, mode, corrupt, and optional session_id. It sends the workbench token in a header. A live BYOK request also uses X-OpenAI-Key, which is kept out of the JSON and cleared from the field on submit.

The server validates the request before selecting a dataset. A day is generated with seed 7 plus its offset from September 1. Analysis validates the XML and loads private in-memory DuckDB tables. Up to three day/scenario Analysis objects are retained by the server.

The offline plan for this question requests KPIs, downtime by reason, wet-end performance, notes, and the two corresponding charts. The result has numeric summaries, chart/table payloads, trace entries, coverage, context, usage, and a run ID.

The model path can choose a different sequence. That is a capability and an evaluation responsibility: the application currently validates legal tool use, not whether the sequence fully answers the user's question.

## Actual tool contracts

All data tools take an integer shift in {1,2,3}; the current Analysis object owns the date. Do not put production_day in tool arguments. Extra arguments are rejected.

| Tool | Additional arguments | Result |
| --- | --- | --- |
| get_shift_kpis | None | One row of calculated KPIs and coverage |
| get_shift_overview | None | KPIs, largest losses and three chart evidence components; four fixed read queries |
| get_order_matrix | None | Order fragments: dimensions, outs, cuts, demand, production, overrun and remaining quantities |
| get_downtime_breakdown | kind: null/Maintenance/Operator; group_by: reason/place/kind | Grouped stop minutes, shift percentages and source IDs |
| get_wet_end_performance | None | Wet-end ID, grade, target, actual elapsed-time speed and source IDs |
| get_quality_breakdown | None | Dry-end reject area by reason |
| get_setup_matrix | None | Setup intervals, counts, allocated stop minutes and performance |
| get_shift_notes | None | Shift note plus individual downtime observations |
| get_rankings | metric: speed/downtime/quality | Up to ten rows; empty when coverage is incomplete |
| render_chart | result_id only; no shift argument | Chart rows copied from successful chartable evidence in this run |

The live provider schemas require all declared properties, including null kind when no filter applies. The application methods allow omitted optional arguments for offline/internal calls. Ranking metric and grouping values are validated in the data methods. The dispatcher enforces tool names, required/extra arguments, integer shifts, current scope and chart provenance.

Example model-selected tool request:

~~~json
{"tool":"get_downtime_breakdown","arguments":{"shift":2,"kind":null,"group_by":"reason"}}
~~~

For the unfiltered request, the actual query shape is:

~~~sql
SELECT reason AS category,
       sum(seconds)/60 AS down_minutes,
       sum(seconds)/28800*100 AS shift_percent,
       list(id ORDER BY id) AS source_ids
FROM stops
WHERE shift = ?
GROUP BY reason
ORDER BY down_minutes DESC, category
~~~

The bound value is 2. Python chooses the grouping identifier from an allowlist; values remain parameters. A kind filter adds AND kind = ? with its own bound parameter. No visitor SQL is accepted.

## Validation before analysis

The intake accepts only the project's version-1 synthetic XML and fixed-demo-local clock. UTF-8 XML is limited to 1 MB, depth 8 and 3,000 elements; DTDs and entity declarations are rejected. There must be three fixed eight-hour shifts.

Setup intervals partition each shift. Knife geometry, gross area, trim, shear, reject-sheet conversion and grade targets reconcile. Bad setup quantities are retained in excluded_records with original attributes, and the whole setup is omitted. Structural calendar errors, overlapping stops and cross-shift events reject the input rather than being silently resolved.

There is no web upload endpoint or live plant connector. The server generates its own dataset; the intake is not a general OEM importer.

## Evidence and tracing

Each data result contains production_day, shift, dataset_version (SHA-256 of XML), rows, coverage, excluded_records, and SQL/parameters/timing. The agent wraps it in a run-specific result_id, tool name, arguments, success/error indicator, and elapsed time.

render_chart accepts only this investigation's successful downtime, wet-end, or quality result. Its rows are a deep copy of that result. Browser rendering uses text nodes and trusted SVG elements. A heatmap is separately constructed by PeriodReports from aggregated daily results.

The trace is inspectable execution evidence, not model chain-of-thought. It is not a durable audit log. Sessions and reports disappear when the process exits.

## Context and follow-ups

The context holds the selected day, focus shift, scope, dataset hash, and up to four question/shifts/intent entries. It does not retain complete prior answers or evidence rows between investigations. The model receives that bounded history plus current-run compact tool results.

Visible date, shift, scope, mode, period and corrupt-record changes reset the UI conversation. A changed dataset hash clears agent history. An all-shift comparison is allowed only when the scope control permits it.

The agent now preserves the selected control shift after comparisons. A server round-trip regression verifies retained history under unchanged controls. All-shifts scope covers all three shifts by default; explicitly named shifts narrow a daily question.

## OpenAI boundary and cost

OpenAIPlanner constructs strict Responses function schemas and requires one function call per response. A finish call supplies successful evidence IDs; application code formats calculated summaries. A separate write_narrative function returns bounded prose and field references, validated by narrative.py. Numeric placeholders are filled from evidence, then shown as Model interpretation with citations. Prose is not certified by reference validation. A cannot_answer call now displays an application-owned unavailable message with supported next steps.

The key is request-scoped unless supplied through the server environment. Before each provider attempt the adapter reserves spending in a local SQLite budget. The request/output/call limits and caveats are documented in [OPENAI_BYOK](OPENAI_BYOK.md). Automated provider tests use scripted responses and make no real model calls.

The model's process instructions discourage unsupported claims and scope changes. Application scope/tool restrictions are enforceable; semantic quality and causal interpretation are not guaranteed by those restrictions. A request explicitly naming a chart/graph/plot gets partial status if no chart was produced. Overview evidence automatically supplies three charts per shift. Semantic fulfillment beyond that remains an evaluation concern (R4).

## Period reports and heatmap

PeriodReports reads daily KPI and note snapshots, closes each Analysis database, then caches at most 60 snapshots for the clean/corrupt demo month. It constructs weighted totals, recurrence tables, shift ranks, daily rows, notes and charts. Missing dates never enter totals as zero. Comparison deltas and ranks are withheld when the necessary coverage is incomplete.

Heatmap rows contain the date, availability, output, percentages, counts, and heavy hitter. The browser normalizes available estimated-good output into alpha 0.22–1 and sets a direct rgba background. Mouse, keyboard focus and click show the same detail text. It does not navigate to the clicked production date.

## Delivery and limitations

The server retains the latest result for up to 32 sessions. Save text contains narrative sections and limitations, not all evidence tables. PDF uses the retained session/run reference and adds summary tables; no model call is made for export. The heatmap title fallback is repaired; daily and period PDF byte/access regression tests pass. Visual layout certification remains pending.

No email, schedule write, equipment action, web search or external market feed exists. Public deployment requires a separate implementation pass. The current local server, tokens, cache and rate limit are not a multi-user production service.
