# How a question becomes evidence and a chart

Status: implementation design. The code examples describe the first build milestone; they are not yet a running service.

## Follow one question

A visitor asks: "Why was second shift slow? Chart its downtime by reason."

The application supplies the selected production day and shift, relevant metric definitions, a bounded conversation history and available tool schemas. It does not send the entire dataset or hidden scenario answers to the model.

The model requests a tool:

```json
{"tool":"get_downtime_breakdown","arguments":{"production_day":"2026-09-01","shift":2,"kind":"Maintenance","group_by":"reason"}}
```

For an unrestricted downtime question, kind may be omitted. The model must not silently narrow all downtime to Maintenance.

## Who executes the math?

Python backend functions execute deterministic calculations and parameterized DuckDB SQL. The model selects an approved function and supplies validated arguments. It has no arbitrary SQL, Python, shell, filesystem or network execution tool.

Initial tools:

| Tool | Inputs | Evidence returned |
| --- | --- | --- |
| get_shift_kpis | Production day, shift | Gross footage/area, speed, downtime, waste, targets and coverage |
| get_downtime_breakdown | Production day, shift, optional kind, allowed grouping | Aggregated durations, percentages and contributing record IDs |
| render_chart | Existing result ID, supported chart type, allowed fields | Validated chart specification referencing verified rows |

These are proposed contracts. Implement schemas, typed outputs and failure behavior before wiring them to the model.

## Prepare the data once

Synthetic XML -> safe schema validation -> normalized tables -> shift allocation -> analysis tools.

Disable XML external entities and DTD processing; impose input limits. Validate finite quantities, timestamps and production geometry. Keep original corrupt values and errors separate from validated facts. Store durations in seconds. Split events across shift boundaries and resolve overlapping stops without double counting; ambiguous classifications must be surfaced, not guessed.

Production days run 07:00 to the following 07:00, labeled by their starting date. Shifts are 07:00-15:00, 15:00-23:00 and 23:00-07:00. Timezone/DST policy remains open. Both shift and wet-end actual speed include downtime in their respective elapsed-time denominators (owner confirmed).

## Inspect an example query

The following illustrates a query over validated, non-overlapping shift allocations. The first parameter is the shift's scheduled seconds from its calendar record, not a model-selected denominator.

```sql
SELECT
    reason,
    SUM(duration_seconds) / 60.0 AS down_minutes,
    100.0 * SUM(duration_seconds) / ? AS shift_percent
FROM downtime_by_shift
WHERE production_day = ?
  AND shift_number = ?
  AND kind = ?
GROUP BY reason
ORDER BY down_minutes DESC;
```

Bind values in placeholder order: scheduled seconds, production day, shift number, kind. Python chooses a fixed query template. If group_by changes, map a schema enum to an allowlisted column; SQL parameters cannot safely stand in for identifiers. Never concatenate visitor text into SQL. A missing kind filter uses a separate approved template.

Queries run with read-only data access, result limits and timeouts. Percentages use summed numerators/denominators, not averages of row percentages. Zero denominators produce an explicit unavailable metric. Source references are returned alongside aggregates, with bounded pagination for large evidence sets.

## Return evidence, not just a number

A tool response includes result ID, dataset version, filters, units, denominator, calculated rows, contributing record references and coverage/validation warnings. Input errors, unavailable data, timeouts and incomplete coverage have distinct structured responses. No silent retry that changes the question.

Never describe maintenance downtime alone as a full explanation of slow speed. Inspect all losses and grade context when the question asks why. Recorded reasons support observed contributors, not unobserved mechanical root causes.

## Run the agent loop

1. Construct scoped context and available tool schemas.
2. Ask the model for a tool request or final response.
3. Validate the request and remaining execution budget.
4. Execute the approved backend function.
5. Append a compact tool result to context.
6. Continue if further evidence is needed, within call/token/time limits.
7. Return an evidence-backed answer, chart references and limitations.

Follow-up "Compare that with first shift" retains the production day and changes the shift scope. Store explicit application state rather than relying only on prose history. Keep old results versioned and avoid silently reusing stale data. Treat user and source text as untrusted; permissions are enforced outside the prompt.

## Render charts from the same results

The model requests a supported chart type and fields from a backend-issued result ID. The backend validates the specification; trusted browser code renders it. Do not accept model-generated executable HTML or JavaScript, arbitrary URLs, or invented data rows. Chart labels include units, filters and incomplete-data warnings.

## Show the work

An expandable analysis panel displays tool names, validated arguments, SQL templates and bound non-sensitive parameters, returned rows, chart specification, execution timing and model usage. This is an execution trace, not private model chain-of-thought. Redact secrets and subscriber details; never expose unrestricted server logs.

## First milestone and acceptance checks

Build a small reproducible dataset covering one production day and three shifts, XML ingestion, two analysis tools and a downtime bar chart. First prove it without a model, then connect the bounded loop.

- Known-answer totals match independent fixture calculations.
- Grade-aware speed uses downtime-inclusive elapsed time.
- Shift crossings, repeated order IDs and paired-knife footage reconcile.
- Invalid giant trim values are flagged and affected coverage is visible.
- Query injection and invalid groupings cannot escape the tool contract.
- Chart numbers match returned tool rows.
- Follow-up comparison uses the same production day and correct new shift.
- Failures and budget exhaustion yield explicit, useful responses.

Provider selection, email delivery, public hosting and the full dataset come after this vertical slice. No credentials or deployment are needed to begin deterministic tools and fixtures.
