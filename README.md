# How to Build an Agent: Corrugator Shift Investigator

Build a manufacturing investigation agent from process knowledge, reliable tools, and evidence. This project is a worked example for learning how agents are constructed: interview a domain expert, generate consistent synthetic production records, implement calculations, connect a language model to bounded tools, and evaluate its behavior.

**Status: local learning workbench implemented.** Calendar day/week/month reports, setup matrices, speed attainment, rankings, operator/shift notes and PDF downloads are available. Try a simple interface with 30 selectable synthetic days, tested SQL tools, a bounded offline planner, follow-up context, evidence-linked charts and report export. An optional OpenAI/BYOK adapter is implemented but awaits live validation with your key; email delivery and public hosting are not connected. See [Run the workbench](docs/WORKBENCH.md).

## 1. Start with a job a person actually needs done

A superintendent asks: "Why was this shift bad? Show me the biggest losses and what needs investigation."

The agent should inspect speed, downtime and waste; compare results with explicit targets; chart the evidence; and produce an investigation report. Recorded defects such as warp are observations, not proof of a mechanical root cause.

The learning objective is to understand the complete agent loop, including tool selection, validation, follow-up investigation, stopping conditions and failure handling.

## 2. Interview the domain expert before writing the math

Keep an evidence ledger that distinguishes owner-confirmed rules, proposed simulation choices and unresolved questions. The [project interview and build brief](https://github.com/justfetz/corrugator-shift-investigator/issues/1) is the current source of those decisions.

Confirmed process structure:

- A production day starts at 07:00 and ends at 07:00 the following calendar day, labeled by the starting date.
- Shift 1 is 07:00-15:00, shift 2 is 15:00-23:00, and shift 3 is 23:00-07:00. Plant timezone and daylight-saving handling remain to be selected.
- A shift contains wet-end groupings by paper width and board grade, with individual dry-end setups.
- Changing either knife's order combination starts a new setup. An order on the other knife may continue across that boundary.
- The two cutoff knives are upper and lower; each spans the machine width. Upper feeds upper stacker, lower feeds lower stacker.
- Operator is left and drive is right in our diagram orientation. Cross-web placement is independent of knife assignment. The web is centered.

## 3. Define measurements before generating records

Use raw quantities to calculate KPIs; aggregate numerators and denominators before calculating percentages.

| Measure | Definition or guidance |
| --- | --- |
| Shift average speed | Lineal feet / all shift minutes, including downtime |
| Maintenance downtime | Maintenance minutes / shift minutes x 100; target 2.5% |
| Operator downtime | Operator minutes / shift minutes x 100; separate target 2.5% |
| Trim | Side-trim square feet / gross square feet x 100; flag above 3.25% per setup |
| Shear waste | Sheared square feet / gross square feet x 100; typical 0.4-1%, not yet an alarm rule |
| Dry-end waste | Rejected square feet / gross square feet x 100; flag above 1% |
| Throughput | Gross square feet / lineal feet x 12; average gross web width in inches |
| Run length | Lineal per dry-end setup and per wet-end change; boundary counting to be defined |

Gross square footage includes all material through the shear before waste deductions. Dry-end rejects can be entered in square feet or individual sheets: sheets x width inches x length inches / 144. Do not multiply individual rejected sheets by outs again. Initial reject reasons are Warp, Bond and Misalignment.

Downtime uses kind, place and reason/reason group. Examples include Maintenance or Operator kind, a knife or wet-end location, and recorded symptoms such as a jam or missed splice. The final taxonomy is still being defined.

## 4. Generate physically consistent synthetic XML

Create our own documented demo schema. Do not claim OEM protocol compatibility. Separate orders, wet-end runs, setups, knife assignments, downtime events and waste events.

For a simple paired segment, 200 cuts at 45 inches and 300 cuts at 30 inches each correspond to 750 machine lineal feet. Outs multiply sheet counts and occupied width, not machine lineal travel. Exact integer cut alignment is a proposed simplification for the first generator, not a claim about every real transition.

Two 30-inch outs plus one 34-inch out occupy 94 inches of a 98-inch web, leaving 2 inches of trim on each side. A full-width shear chop approximately 34 inches long has area paper width x 34 / 144 square feet.

Use a reproducible seed and explicit scenarios. Keep scenario answer keys outside the agent's accessible evidence so tests measure investigation rather than answer retrieval. Keep paper recipes optional; do not infer strength or furnish from fictional grade labels.

Proposed editable speed table, based on owner guidance rather than external standards:

| Fictional board grade | Construction | Target ft/min |
| --- | --- | ---: |
| 26-C | Single wall | 900 |
| 32-C | Single wall | 900 |
| 55-C | Single wall | 850 |
| 200-C | Single wall | 800 |
| 275-BC | Double wall | 500 |
| 275-EB | Double wall | 500 |

Owner confirmed: wet-end actual speed uses its full elapsed duration, including downtime. Compare each wet-end ID against its grade target using this basis. Demo paper widths are 98, 95, 92 and 87 inches. Approximate shift scale is 50-75 dry-end records and roughly half as many wet-end records. About 200,000 lineal is a bad-shift example; 275,000-300,000 is contextual good-shift guidance, not a universal grade-independent target.

## 5. Build and test tools before connecting the model

Proposed tools include `get_shift_kpis`, `get_run_timeline`, `compare_shifts`, `get_loss_breakdown`, `get_process_rule` and `render_chart`. Each needs a small input schema, validated filters, bounded results and structured evidence with record references, units and data-quality coverage.

Treat XML as untrusted input: disable external entities and DTD processing, enforce size/depth limits, and validate the schema. Check finite numbers, nonnegative quantities, time ordering, geometry and quantity reconciliation. Preserve bad source values with errors; do not silently clamp them. Exclude invalid contributions from affected calculations and clearly mark incomplete results.

## 6. Implement a bounded agent loop

1. Receive a question and scoped conversation context.
2. Ask the model for a permitted tool call or a final answer.
3. Validate tool name, arguments and remaining budget in application code.
4. Execute the tool against the read-only demo dataset.
5. Return structured evidence to the model.
6. Continue only while additional evidence is useful and limits allow it.
7. Return an answer with supporting numbers, charts, uncertainties and a reviewable next action.

The model chooses an investigation path. Application code owns calculations and permissions. It cannot execute arbitrary SQL, Python, shell commands, HTML or JavaScript. Set tool-call, token, time and result-size limits. A failed tool must produce an explicit failure, not fabricated evidence.

## 7. Make the investigation visible

Build chat with example questions, bar/trend/timeline charts and an analysis-steps panel showing tools used, filters and evidence. Render validated chart specifications in trusted UI code. Show grade context when comparing shifts; a double-wall run at 500 ft/min may meet its target while a single-wall run at 725 misses its target.

## 8. Deliver useful reports with controlled actions

Subscribers can select reports for shift 1, 2 and/or 3, a production-day comparison, or both. Daily reports compare shifts and explain grade mix, losses and data-quality exceptions.

Planned delivery controls: verified email, unsubscribe, private recipient storage, rate limits and idempotent delivery records. The agent prepares content; application code selects verified recipients and dispatches it. No email sending, paid provider or public deployment is enabled yet.

## 9. Evaluate correctness and cost

Test known-answer calculations, order continuation, unequal cut lengths, width fit, midnight boundaries, overlapping downtime, corrupt numeric values and waste reconciliation. Test questions that lack evidence, prompt-injection attempts, forbidden tool calls, chart/data agreement and delivery isolation.

Measure tool accuracy, evidence-supported answers, latency and cost per investigation. Enforce server-side usage budgets and keep saved examples available when live usage is exhausted. OpenAI is available as an optional daily planner; hosting remains open; no running-cost promise has been validated.

## 10. Build in reviewable stages

1. Consolidate the interview into reviewed process definitions and an agent contract.
2. Implement the XML schema, generator and reconciliation tests.
3. Implement deterministic analysis tools and known-answer tests.
4. Implement the bounded model/tool loop and adversarial evaluations.
5. Build the public-facing chat, charts and report preview.
6. Add verified subscriptions and controlled report delivery.
7. Review deployment, costs and limitations before publishing.

Next unresolved definitions include waste-category overlap accounting, plant timezone/daylight-saving behavior and event allocation at boundaries. This project is separate from Talk to My Machine and has no access to live machinery or private production stores.

## Follow a query through the agent

Read [How a question becomes evidence and a chart](docs/AGENT_EXECUTION.md) for tool contracts, parameterized SQL, context management, the execution loop, chart validation and the first milestone's acceptance checks.


## Inspect the working agent boundary

Start with [the local interface walkthrough](docs/WORKBENCH.md), then read [the agent contract](docs/AGENT_CONTRACT.md) and [execution guide](docs/AGENT_EXECUTION.md). The offline router makes tool behavior reproducible alongside optional live-model integration. The in-app build guide explains the same boundary.


## Connect a low-cost live model

Read [OpenAI and BYOK mode](docs/OPENAI_BYOK.md) for the request-scoped key flow, model pricing, spending guard and current validation limits. Offline mode remains the default.
