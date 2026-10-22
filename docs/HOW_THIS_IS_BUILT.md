# How this is built

A ten-minute walk through the Corrugator Shift Investigator in the order the data flows: from one seed to four record families, through the invariants that hold them together, into a store and a set of bounded tools, through a planner that may only choose, out as evidence with a trail, and finally into a golden set that owns its own truth. Every section names the file that does the work and the test that proves it. Nothing here is a real plant, a vendor protocol, or a proof of cause; the last section says so plainly.

```
 seed 2026 ──► synth.generate_months() ──► four record families ──► content hash 7facc4a1…
                     ▲                      dry-end setups   7,968
                     │                      wet ends         3,712
              stories.py (answer keys)      shifts             129
              read by the generator only    stops            1,081
                                                   │
                                                   ▼
                                  RecordStore: one in-memory DuckDB, built once
                                  setups · customers · rejects · wet_ends · shifts · stops
                                      │                                  │
                         day_analysis(day, corrupt)              six period tools
                         five per-day tables, validated          any day / week / month window
                         nine daily tools                        ratios of sums, previous window beside
                                      │                                  │
                                      └──────────────┬───────────────────┘
                                                     ▼
                     Agent loop: planner proposes ──► app validates ──► tool runs ──► evidence
                     OfflinePlanner | AnthropicPlanner (server key) | OpenAIPlanner (your key)
                     bounded: calls, seconds, context bytes, request bytes, spend
                                                     │
                                                     ▼
                     answer: sections · charts · tables · trace · narrative with citations
                                                     │
                                                     ▼
                     evals/run.py  routing · faithfulness · grounding · refusal · value · investigation · data_quality
                     golden_values.json ◄── make_value_goldens.py: plain sums over the records, stamped with the hash
```

## 1. The seed and the four record families

Everything starts with `synth.generate_months(seed=2026)` in `src/investigator/synth.py`. It lays out every Monday-to-Friday production day of August and September 2026 — 43 days, three 480-minute shifts each, starting 07:00, 15:00 and 23:00 — for one machine called `CORR-1`, and emits four families of plain records defined in `src/investigator/records.py`. The same seed gives byte-identical JSONL files and one SHA-256 content hash over all four; change the seed, the months or the machine id and the hash changes.

The field names and units are borrowed from the shape of a corrugator controller's own record types, so the data reads like plant records to anyone who has seen plant records: a dry-end setup record ("P04P"-shaped), a wet-end record ("P04G"), a shift record ("P04T") and a stop record ("P04S"). They are *not* those records. This is the project's own documented demo schema (`demo-records/v1`, field by field in [RECORDS.md](RECORDS.md)); no vendor wire format, field semantics or protocol compatibility is claimed. The borrowed shape is the point: an agent that learns to read these will not be confused by the real thing's vocabulary, and nothing here needs a machine connection.

One example of each, taken from Monday 14 September, shift 2:

**Dry-end setup** — one record per knife combination. Three minutes and twenty-seven seconds of a 275-BC run on an 87-inch web, two orders across the web (a 20-inch sheet in one out and a 32-inch sheet in two outs, so 84 inches occupied and 3 inches of trim, centered), one stop inside it:

```json
{"setup_id": "CORR2026091416091908", "machine_id": "CORR-1", "start_date": "20260914", "start_time": "160919",
 "end_date": "20260914", "end_time": "162246", "lineal_ft": 5239.9, "avg_speed_fpm": 389.6, "web_width_in": 87,
 "quality_id": "275-BC", "total_nominal_ft": 5191.3, "total_produced_sqft": 37989.3, "trim_sqft": 1297.9, "trim_pct": 3.42,
 "change_code": "C", "stop_time_sec": 134, "number_of_stops": 1, "shift_number": 2, "chop_sqft": 349.2, "kqf_sqft": 388.2,
 "customers": [{"order_number": "SO-831284", "sheet_width_in": 20, "sheet_length_in": 26, "number_of_outs": 1, "cutoff_allocation": 1,
                "nominal_cuts": 2396, "actual_cuts": 2352, "hqm_waste_sqft": 21.7, "asm_waste_sqft": 83.0, "stack_height_sheets": 80, "part_number": "P-77557"},
               {"order_number": "SO-831286", "sheet_width_in": 32, "sheet_length_in": 58, "number_of_outs": 2, "cutoff_allocation": 2,
                "nominal_cuts": 1074, "actual_cuts": 1081, "hqm_waste_sqft": 77.3, "asm_waste_sqft": 206.2, "stack_height_sheets": 132, "part_number": "P-38514"}],
 "rejects": [{"reason": "BOND", "cutoff_allocation": 1, "sheets": 11, "sqft": 39.7}, {"reason": "WARP", "cutoff_allocation": 1, "sheets": 12, "sqft": 43.3},
             {"reason": "MISALIGNMENT", "cutoff_allocation": 1, "sheets": 6, "sqft": 21.7}, {"reason": "BOND", "cutoff_allocation": 2, "sheets": 7, "sqft": 90.2},
             {"reason": "WARP", "cutoff_allocation": 2, "sheets": 9, "sqft": 116.0}, {"reason": "MISALIGNMENT", "cutoff_allocation": 2, "sheets": 6, "sqft": 77.3}],
 "production_date": "20260914", "wet_end_id": "WE20260914030", "split_from_setup_id": null}
```

Read it the way the tools do: 5,239.9 lineal feet × 87 inches / 12 = 37,989 gross square feet, trim and chops included. The chops (349.2 sq ft, exactly eleven 34-inch full-web cuts) are subtracted to get the travel that reached the knives, and both knives' nominal cuts multiply back to that travel within one cutoff length (2,396 × 26 in and 1,074 × 58 in are both about 5,190 ft). `change_code: C` says knife 1's order continued from the previous setup while knife 2's changed. The six reject lines are per reason and per knife position; they add up to `kqf_sqft`.

**Wet end** — one paper run: consecutive setups sharing grade and web width. This one holds three setups, the one above in the middle; its totals are exact sums of theirs:

```json
{"wet_end_id": "WE20260914030", "quality_id": "275-BC", "web_width_in": 87, "start_date": "20260914", "start_time": "160252",
 "end_date": "20260914", "end_time": "163022", "duration": "002730", "lineal_ft": 11400.9, "total_produced_sqft": 82656.6,
 "side_trim_sqft": 2823.3, "max_speed_fpm": 467.2, "avg_speed_fpm": 414.6,
 "setup_ids": ["CORR2026091416025207", "CORR2026091416091908", "CORR2026091416224609"], "shift_numbers": [2], "production_date": "20260914"}
```

**Shift** — 480 minutes, a crew label that is never a person, exact sums over the shift's setups, and one non-protocol field, `bale_sqft`, a scale reading of baled waste that is deliberately a little more than trim + chops + rejects so the "unexplained" tile has something honest to show:

```json
{"shift_number": 2, "production_date": "20260914", "group_name": "Crew B", "start_time": "150000", "end_time": "230000", "duration": "080000",
 "total_lineal_ft": 291063.5, "total_produced_sqft": 2252174.3, "side_trim_sqft": 72531.8, "chop_sqft": 14142.2, "reject_sqft": 25298.8,
 "max_speed_fpm": 850.0, "avg_speed_fpm": 606.4, "wet_end_ids": ["WE20260914028", "…", "WE20260914051"], "scheduled_minutes": 480, "bale_sqft": 119729.4}
```

**Stop** — always inside exactly one setup, with a kind, a place, a reason and a note that is labelled unverified because it is:

```json
{"stop_id": "ST20260914161821", "start_time": "161821", "end_time": "162035", "duration_sec": 134, "kind": "Operator", "place": "Splicer 3",
 "reason": "Missed splice", "shift_number": 2, "production_date": "20260914", "setup_id": "CORR2026091416091908",
 "user_info": "Splice missed at Splicer 3; web break, chops run until the web settled (synthetic note, unverified)"}
```

## 2. The invariants that hold the families together

Each line is one rule the generator keeps for every record of both months and the test in `tests/test_records_invariants.py` (unless named otherwise) that walks every record to prove it.

- The calendar is 43 weekdays × 3 shifts of 480 minutes at 07:00 / 15:00 / 23:00, labelled by the production day that starts at 07:00 — `test_calendar_is_three_shifts_per_weekday_of_both_months`.
- The web is centered: ordered widths × outs plus trim equal the web width, with 1 to 7 inches of trim per side — `test_web_geometry_is_centered_within_trim_limits`.
- Knives share travel: every knife's nominal cuts × cutoff equal the order travel within one cutoff, and counted cuts stay within ±10 % of nominal, mostly within ±2 % — `test_lineal_consistency_and_overrun_window`.
- Area reconciles: gross = lineal × width / 12; chops are whole 34-inch cuts; trim = order travel × trim inches / 12; each reject line is sheets × width × length / 144 and they sum to `kqf_sqft`; a setup's speed is lineal over elapsed minutes including its stops — `test_setup_area_accounting`.
- One order per knife position, and the change code tells the truth: `O` shares no order with the previous setup, `C` keeps knife 1's order, `S` continues all orders across a shift boundary — `test_one_order_per_knife_and_continuation_codes`.
- A wet end is the exact aggregate of its contiguous setups and spans at most two shifts — `test_wet_end_is_the_exact_aggregate_of_its_setups`.
- A shift is the exact aggregate of its setups, and the bale reading exceeds the accounted waste by 0.1–0.4 % of gross — `test_shift_is_the_exact_aggregate_of_its_setups`.
- Setups tile every shift with no gap; every stop sits inside one setup, stops never overlap, and a setup's stop count and seconds equal its stops — `test_time_partition_setups_tile_shifts_and_stops_sit_inside`.
- Maintenance + PM downtime is 2–3 % of scheduled minutes per month and operator downtime 1–3 %, with one PM window every Thursday on shift 1, recorded as kind `PM` with its own reason `Planned maintenance` — `test_downtime_budget_per_month`.
- Per month, side trim is 2.5–3.5 % of gross, shear chops 0.4–1.0 %, dry-end rejects 0.8–1.4 % — `test_waste_bands_per_month`.
- Each production day makes 5.5–7.5 million gross square feet from 130–230 setup records — `test_output_per_production_day`.
- The same seed gives the same bytes: the content hash is pinned — `tests/test_synth_determinism.py::test_pinned_content_hash`.
- The store's per-day tables and period tools reconcile to plain Python sums over the families, field by field — `tests/test_record_store.py`, `tests/test_period_tools.py`.

These are demo choices, not plant standards (the simulation constants are at the top of `synth.py`). What matters is that they are stated, kept, and checked.

## 3. The seeded stories, and why the answer keys stay away from the agent

Three effects are planted in the records by `src/investigator/stories.py`:

1. A slitter/scorer rebuild in the Thursday 20 August PM window. Afterwards side trim creeps about 0.1 percentage point a week and knife / slitter jam stops rise, concentrating on the slitter/scorer.
2. A missed-splice problem on Splicer 3 in the week of 7 September: missed-splice minutes roughly triple, and the shear chop follows because every missed splice is a web break and a run of full-width chops.
3. Warp rejects run 1.5× on the first production day after a weekend and higher in August than in September; bond and misalignment stay flat.

Only the generator imports `stories.py`. The analysis layer, the agent, the server, the reports and the narrative never do, and `test_answer_keys_are_not_imported_by_any_tool_layer` fails the build if one starts to. So an investigation has to find the effects in the records — the trim trend, the place Pareto, the Monday waste breakdown — rather than look the answer up, and the tests that use the keys (`test_story_signals_are_visible_in_the_records`, the investigation goldens) use them only to build expectations.

## 4. The record store and its day tables

`src/investigator/record_store.py` builds the two months once per process (about two seconds generating, under two loading) into one in-memory DuckDB with external access disabled: `setups`, `customers`, `rejects`, `wet_ends`, `shifts`, `stops`. Two doors lead out of it.

`day_analysis(production_day, corrupt=False)` validates each setup of one day (finite non-negative quantities, a known web width and grade, gross = lineal × width / 12, waste within gross, a sane interval, one to three knife positions in order, reject lines that reconcile) and loads the five per-day tables the daily tools have always read. A record that fails validation is excluded with its raw values and makes its shift's coverage `incomplete`; the `corrupt` toggle injects a 99,999,999 sq ft trim value into the first setup of shift 2 so that path can be demonstrated on demand. A weekend date or a date outside 3 August – 30 September is refused with the calendar reason; the server returns the same message as a 400.

The six period tools read the store directly with bound window dates, so "this month versus last" is one fixed read over the whole dataset, not a loop over days.

## 5. The tools

Every tool is a fixed, parameterized query: grouping identifiers come from an allow-list, every visitor value is a bound parameter, results are capped at 1,000 rows and interrupted after two seconds, and each result carries its SQL, parameters, timing, `coverage` and `excluded_records`. There is no arbitrary SQL, shell, file, network, email or machine-control tool ([AGENT_CONTRACT.md](AGENT_CONTRACT.md)).

The nine daily tools (`src/investigator/analysis.py`) take a shift: `get_shift_kpis` (output, speed to grade target, downtime by kind, waste percentages, throughput), `get_shift_overview` (KPIs plus three chart components in one call), `get_downtime_breakdown` (stops by reason, place or kind, optional kind filter), `get_wet_end_performance` (each paper run against its grade target over elapsed time), `get_quality_breakdown` (rejected area by reason), `get_setup_matrix`, `get_order_matrix`, `get_shift_notes` (untrusted text, rendered as text) and `get_rankings` (one metric, withheld when coverage is incomplete).

The six period tools (`record_store.py`) take a window `{period: day|week|month, anchor}` and an optional shift: `get_period_kpis` (twenty dashboard tiles with the previous window beside), `compare_periods` (the same table for two arbitrary windows), `get_downtime_pareto` (share and cumulative share, previous window beside), `get_waste_breakdown` (trim, shear, bond, warp, misalignment, bale, unexplained), `get_stops_profile` (per production day plus the Monday-to-Friday profile) and `get_trend` (a weekly series of one allow-listed metric, 1–26 weeks). Three rules run through all of them:

- **Ratio of sums.** Every percentage is summed numerators over summed denominators across the window — September side trim is sum(trim sq ft) / sum(gross sq ft) over 4,115 setup records, never the mean of 22 daily percentages, and the test suite pins that the two differ. Deltas are the difference of the two displayed tiles, so a tile reconciles on screen.
- **The `better` map.** Each tile says which direction is good — `higher` for volume and speed, `lower` for downtime and waste, `neutral` for counts that are context, not a score — and the `change` label (`better`, `worse`, `same`, `neutral`) is computed from it, so the interface never has to guess what a red arrow means.
- **Withheld comparisons.** A window must overlap the dataset. Coverage is `complete`, `partial` or `unavailable` per window; deltas and change labels are withheld unless both windows are complete, previous tiles read `null` when the previous window is unavailable, and weekends and July are never zero.

Formulas and targets are in [METRICS.md](METRICS.md).

## 6. The planners

A planner implements one method, `next_step(context) -> {tool, arguments} | {final: [ids]} | {unavailable: reason}`. It chooses; it never calculates.

- **OfflinePlanner** (`src/investigator/agent.py`) is rule-based: a keyword router maps supported question shapes ("why was this shift slow", "compare this month to last", "which downtime reason cost the most minutes this week", "trend of trim over eight weeks") to a fixed tool plan. It is deterministic, free, and deliberately not presented as a language model. Unsupported questions raise a clear refusal.
- **AnthropicPlanner** (`src/investigator/anthropic_provider.py`) runs the same contract over the Messages API for the hosted demo. The server holds `ANTHROPIC_API_KEY`; visitors never supply or see a key. Each round allows at most ten model calls in 60 seconds, 24 KB per request, 1,024 output tokens, one `tool_use` block per turn with `finish` and `cannot_answer` as the only ways to end; a text-only reply closes with the evidence gathered so far, and `refusal` or `max_tokens` executes nothing. In public mode a per-IP rate limit (`DEMO_RATE_LIMIT_PER_MIN`), a service-wide daily cap on rounds (`DEMO_DAILY_CAP`) and the Anthropic console spend limit sit in front of it ([ANTHROPIC_DEMO.md](ANTHROPIC_DEMO.md)).
- **OpenAIPlanner** (`src/investigator/openai_provider.py`) is bring-your-own-key on the loopback workbench: the key travels in a header for one request, is never stored, and the strict Responses function schemas, the same call / time / byte limits and the same spending reservation apply ([OPENAI_BYOK.md](OPENAI_BYOK.md)).

Both live adapters receive the same process rules and the same tool schemas, see compact tool results (no SQL, no source ids, matrices previewed to twelve rows), and may add a short narrative afterwards through a `write_narrative` tool whose numbers are placeholders that application code fills from cited evidence fields; a narrative with a literal digit, an unknown field or a finding on incomplete evidence is rejected (`src/investigator/narrative.py`).

## 7. The agent loop and its bounds

`Agent.ask(question, context)` in `agent.py` is the whole loop: ask the planner for one step; validate it in application code (a known tool, exactly the declared arguments, a shift inside the selected scope — under "selected shift only" even a period tool must name that shift); execute the tool; add the result to the run's evidence store under `run_id:step`; repeat until `finish` names successful evidence or a bound trips. Offline bounds are 20 tool calls and ten seconds; live bounds are ten calls and 60 seconds plus the adapter's own ten model calls; the planner context is capped at 200,000 serialized characters. A rejected tool call consumes a call and stays in the trace with its reason; when every call was rejected and the planner has no evidence to finish on, the answer is `unavailable` and the rejection reasons are its text ("How did July go?" answers that the month window is outside the dataset range), never a `planner_error` with the reason buried in the trace. `render_chart` may cite only a successful, chartable result from this run and copies its rows. Context carries the production day, shift, scope, dataset hash and at most four recent question / scope entries; a changed dataset hash clears history.

The answer is assembled by application code, not the model: `sections` (plain-text evidence lines in which every number is a tool-row value), `charts` (deep copies of their source rows), `tables`, the `trace` (every tool call with arguments, result or error, SQL, parameters and timing), `narrative` paragraphs with citations of the form `Shift 2, observed_shift_fpm, <result_id>, row 0`, and `usage` (tool calls, model calls, tokens, estimated and reserved cost). A question that asks for a chart and gets none is marked `partial`; a question about profit or savings gets the standing disclosure that no costs or outcomes exist in the records. The trace is inspectable execution evidence, not a durable audit log.

## 8. The golden set

`evals/run.py` runs `evals/golden.json` and `evals/golden_values.json` and prints one score table; `tests/test_evals.py` runs the same offline cases in CI. Seven case types, each proving one thing:

| Type | Proves | Scored by |
| --- | --- | --- |
| routing | the planner picked the right tools and arguments for a phrasing | required calls (argument subset), allow-list, counts |
| faithfulness | every number in the answer came out of a tool row | every numeric token must equal a tool value under one of the application's formats |
| grounding | citations resolve and nothing was silently dropped | `(result_id, row, field)` lookups; a chart that could not be drawn is disclosed |
| refusal | out-of-scope requests are refused rather than answered | the refusal text, or a non-complete status with the disclosure, or forbidden phrases absent; July is unavailable, never zero; a Saturday is refused with the calendar reason |
| value | a tool's numbers equal a second, plain computation | 58 generated cases, 3,145 `[expected, tolerance]` pairs |
| investigation | a superintendent's multi-turn question finds a seeded story | required evidence covering the story window, its numbers in the prose, no cause asserted that the evidence lacks |
| data_quality | the corrupt record is flagged, excluded, reported, never averaged in | coverage, the reported raw value, the impossible number absent, rankings withheld, the remaining sums pinned |

**How the goldens are made.** With synthetic data we own the truth, so an expected answer is never typed by hand and never read back from the tool it checks. `evals/make_value_goldens.py` computes every value case by plain Python sums over `synth.generate_months()` — its own calendar arithmetic, its own ratio-of-sums formulas, its own Pareto ordering — and writes `golden_values.json` stamped with the dataset content hash. The runner compares that hash with the live store before scoring and fails every value case when they differ, so an intended generator change forces a deliberate regeneration and an accidental one is caught. A tolerance rides with each number to cover the tool's display rounding; integers, strings and `null` are exact. The committed file must be byte-for-byte what the generator produces (`tests/test_goldens_values.py`).

**How an investigation is scored.** The three investigation cases ask about the three stories in plain words ("We had a rough week. Where did we lose the most downtime minutes this week?"). Rules in the case, evaluated by application code, require specific evidence — a place Pareto covering 7–11 September in which Splicer 3 minutes are at least three times the week before; a trim trend that shows the week of 17 August and rises at least 0.4 pp by the week of 28 September; the Monday 14 September waste breakdown with warp above the Friday before and bond flat — require the numbers those rules found to appear in the answer, and forbid the prose from asserting the rebuild, a splicer fault or weekend moisture as the cause. "Coincides with", "warrants inspection" and "ask the crew" pass; "caused by the rebuild" fails. The same cases run offline against the rule-based planner and, with `--live`, against Anthropic or OpenAI, whose responses are recorded as fixtures and replayed afterwards; live types pass by pass rate (routing ≥ 0.9, investigation ≥ 0.8, the rest 1.0).

One thing the goldens taught: at month grain the slitter/scorer's downtime *minutes* fall in September, because August holds the 180-minute rebuild window itself; its stop *count* rises. A rule written on minutes alone would have been wrong, and the data said so before the prose did.

## 9. The cost model

Offline is $0 and the score table asserts it. For live rounds the discipline is a reservation before every call, never a refund, and a hard stop outside the application:

| Planner | List price (per MTok in / out) | Reserved per call | Typical round | Hard stop |
| --- | ---: | ---: | ---: | --- |
| `claude-haiku-5-5` (hosted default) | $0.10 / $0.50 | $0.002 | about $0.002 | Anthropic console spend limit |
| `claude-sonnet-5-5` | $2.00 / $10.00 | $0.03 | about $0.04 | Anthropic console spend limit |
| `gpt-4.1-mini` (BYOK) | $0.40 / $1.60 | $0.02 | about a cent | your OpenAI account limit |

The monthly `Budget` ledger (`artifacts/model-budget.sqlite3`) caps reservations at $5 per installation per UTC month across providers and refuses with "monthly application model budget exhausted"; the hosted demo adds the per-IP rate limit and the daily round cap. Displayed costs use returned token counts and ignore cache discounts. The counters are per container, so the console limit is the stop that holds.

## 10. What is deliberately not built

- **No real plant and no OEM protocol.** The records are our own documented demo schema with borrowed field names; there is no machine connection, no upload endpoint, no importer, and no claim of vendor compatibility.
- **No causes asserted.** Stop reasons, reject reasons and notes are recorded observations. The tools report them, the prose may say what coincides with what and what warrants a look, and nothing — tool, planner, golden or document — turns a symptom into a diagnosis. Targets are editable simulation settings, not standards.
- **No money.** There are no costs, prices, revenue or measured intervention outcomes, so no savings or profit claim can be grounded, and the agent says so when asked.
- **No delivery.** No email, no subscribers, no schedule writes, no equipment actions, no web search.
- **No calendar beyond the demo.** No holidays, no timezone or daylight-saving handling, weekends are plant-down and emit nothing, and the 4-4-5 anchor is provisional.
- **No order ledger, no overlap policy.** Requested and remaining sheets are null in the record families; stops never overlap or cross a shift; reject replacement and accepted-shipment accounting are not modelled.
- **No multi-tenant service.** The loopback workbench and the hosted demo are a teaching server with bounded guards, not identity, durable rate limits or centralized budgets.

A completed investigation means the tool run finished with evidence a person can check, not that a root cause was proved.
