# How this is built

A ten-minute walk through the Corrugator Shift Investigator in the order the data flows: from one seed to the record families, through the invariants that hold them together, into a store and a set of bounded tools, through a planner that may only choose, out as evidence with a trail, and finally into a golden set that owns its own truth. Every section names the file that does the work and the test that proves it. Nothing here is a real plant, a vendor protocol, or a proof of cause; the last section says so plainly.

```
 seed 2026 + date ──► synth.generate_day() ×456 ──► the record contract's families ──► content hash c9754a00c4e2…
                     ▲                    (1 January 2025 to 2026-09-30)
                     │                      dry-end setups  56,555
              stories.py (answer keys)      wet ends        17,531
              read by the generator only    shifts           1,368
                                            stops           11,317
                                                   │
                                                   ▼
                                  RecordStore: one in-memory DuckDB, built once
                                  setups · setup_orders · rejects · wet_ends · rolls · shifts · stops
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

## 1. The seed and the record families

Everything starts with `synth.generate_day(day, seed=2026)` in `src/investigator/synth.py`. It lays out one Monday-to-Friday production day — three 480-minute shifts starting 07:00, 15:00 and 23:00 — for one machine called `CORR-1`: it draws the day's orders, schedules them into dry-end setups the way a corrugator scheduler does (two orders across the knives at the outs that fill the web, on the narrowest paper that fits, the first to finish setting the length, the remainder rolling into a next setup), and emits the record families defined in `src/investigator/records.py`: setups with their order lanes and reject lines, paper runs and their rolls, shifts and the downtime log. Every random draw of the day comes from streams keyed by the seed and a date (the day's own, its week's and its month's), and nothing carries into the next day, so a day is byte-identical whatever range it is generated in (issue #35). The dataset runs from 1 January 2025 to `as_of`: 456 production days for the golden window ending 2026-09-30 that the tests and the golden set read; the hosted demo's ends on the day before its image was built and moves every night. The same seed gives byte-identical JSONL files and one SHA-256 content hash over the range; change the seed, the window or the machine id and the hash changes.

The field names and units are borrowed from the shape in which a corrugator's MES records a generic dry-end run setup, so the data reads like plant records to anyone who has seen plant records: a dry-end setup record with up to three order lanes, a wet-end (paper run) record, a shift record, a downtime log and the roll stock. They are *not* any one vendor's records. This is the project's own documented demo schema (`demo-records/v2`; every field mapped to its typical MES field in [the record contract](RECORD_CONTRACT.md), the families in [RECORDS.md](RECORDS.md)); no vendor wire format, field semantics or protocol compatibility is claimed. The borrowed shape is the point: an agent that learns to read these will not be confused by the real thing's vocabulary, and nothing here needs a machine connection.

One example of each, taken from Monday 14 September, shift 2, as the generator writes it (record contract v2, issue #100):

**Dry-end setup**, one record per run setup, the fact table. Thirty-one and a half minutes of a 32-C run on a 105-inch web. Two lanes run across the web: a 54½-inch sheet at one out for a customer order of 7,650 sheets, and a 48 × 40 cap sheet the scheduler added as a filler for the plant's own pallets. That occupies 102½ inches and leaves 2½ inches of trim, centered. One stop sits inside it:

```json
{"setup_id": "CORR2026091421334230", "machine_id": "CORR-1", "start_date": "20260914", "start_time": "213342", "end_date": "20260914",
 "end_time": "220511", "actual_run_sec": 1826, "lineal_ft": 23999.0, "total_nominal_ft": 23587.5, "avg_speed_fpm": 762.3,
 "web_width_in": 105, "quality_id": "32-C", "total_produced_sqft": 209991.2, "trim_sqft": 4988.6, "trim_pct": 2.38, "change_code": "O",
 "stop_time_sec": 63, "number_of_stops": 1, "shift_number": 2, "chop_sqft": 471.0, "swing_knife_sqft": null, "is_fanfold": false,
 "sheet_type": "product", "filler": true,
 "lanes": [{"lane": 1, "order_number": "SO-2609140067", "part_number": "P-17997", "customer_id": "C036", "order_quantity": 7650,
            "sheet_width_in": 54.5, "sheet_length_in": 37, "number_of_outs": 1, "cutoff_allocation": 1, "nominal_cuts": 7650, "actual_cuts": 7766,
            "sheet_type": "product", "filler": false, "cutoff_waste_boards": 2, "stacker_waste_boards": 2, "cutoff_waste_sqft": 28.0,
            "stacker_waste_sqft": 28.0, "stack_height_sheets": 150, "folds": null, "sheets_per_pallet": null, "pallets": null, "perforation_length_in": null},
           {"lane": 2, "order_number": "SO-2609140113", "part_number": "PLT-4840", "customer_id": "C000", "order_quantity": 0,
            "sheet_width_in": 48.0, "sheet_length_in": 40, "number_of_outs": 1, "cutoff_allocation": 2, "nominal_cuts": 7076, "actual_cuts": 7183,
            "sheet_type": "cap_sheet", "filler": true, "cutoff_waste_boards": 1, "stacker_waste_boards": 4, "cutoff_waste_sqft": 13.3,
            "stacker_waste_sqft": 53.3, "stack_height_sheets": 279, "folds": null, "sheets_per_pallet": null, "pallets": null, "perforation_length_in": null}],
 "rejects": [{"category": "bond", "reason": "Delamination", "lane": 1, "sheets": 56, "sqft": 784.2, "liner": "bottom", "side": null},
             {"category": "warp", "reason": "Down warp", "lane": 1, "sheets": 20, "sqft": 280.1, "liner": null, "side": null},
             {"category": "misalignment", "reason": "Misalign", "lane": 1, "sheets": 30, "sqft": 420.1, "liner": null, "side": "operator"},
             {"category": "changeover", "reason": "Cut off", "lane": 1, "sheets": 17, "sqft": 238.1, "liner": null, "side": null},
             {"category": "warp", "reason": "Up warp", "lane": 2, "sheets": 34, "sqft": 453.3, "liner": null, "side": null},
             {"category": "paper", "reason": "Wrong color", "lane": 2, "sheets": 16, "sqft": 213.3, "liner": null, "side": null},
             {"category": "changeover", "reason": "Cut off", "lane": 2, "sheets": 9, "sqft": 120.0, "liner": null, "side": null},
             {"category": "admin", "reason": "Order fill", "lane": 2, "sheets": 31, "sqft": 413.3, "liner": null, "side": null}],
 "production_date": "20260914", "wet_end_id": "WE20260914022", "split_from_setup_id": null}
```

Read it the way the tools do:
- **Gross:** 23,999.0 lineal feet × 105 inches / 12 = 209,991 gross square feet, trim and chops included.
- **Order travel:** the chops (471.0 sq ft, exactly nineteen 34-inch full-web cuts) are subtracted to get the travel that reached the knives, 23,945.2 ft. That is lane 1's 7,766 cuts × 37 in, the longest lane.
- **Scheduling:** lane 1 was scheduled for 7,650 cuts (23,587.5 ft, `total_nominal_ft`) to finish its order. The over/under-run gave it 116 cuts more (1.5%).
- **The filler:** the cap sheet ran as far as its pair, ⌊7,650 × 37 / 40⌋ = 7,076 cuts scheduled. Both lanes end within one knife rotation.
- **Paper:** the paper is the narrowest of 90–110 in that holds 102½ inches plus that day's minimum trim (2.36 in: the 2-inch baseline plus the slitter rebuild's creep).
- **Rejects:** each reject line names a category and reason from `reject_reasons.json`, and a position where the reason has one (the delamination is on the bottom liner, the misalign on the operator side). The changeover lines count with shear and the order-fill line is a planning / admin entry, never dry-end quality.

**Wet end**, one paper run: consecutive setups sharing grade and web width. This one holds three setups, the one above last. Its totals are exact sums of theirs:

```json
{"wet_end_id": "WE20260914022", "quality_id": "32-C", "web_width_in": 105, "start_date": "20260914", "start_time": "211015",
 "end_date": "20260914", "end_time": "220511", "duration": "005456", "lineal_ft": 42809.0, "total_produced_sqft": 374578.7,
 "side_trim_sqft": 9057.3, "max_speed_fpm": 813.1, "avg_speed_fpm": 779.3,
 "setup_ids": ["CORR2026091421101528", "CORR2026091421191129", "CORR2026091421334230"], "shift_numbers": [2], "production_date": "20260914"}
```

**Roll**, one roll on one stand of the run, with the wraps peeled at prep and the paper left on its core when it was spliced out:

```json
{"roll_id": "R202609140166", "wet_end_id": "WE20260914022", "stand": "Top liner", "paper_grade": "33 lb liner", "width_in": 105,
 "lineal_ft": 21493.2, "peel_sqft": 147.5, "core_sqft": 1663.9, "spliced_out": true, "production_date": "20260914"}
```

**Shift**, 480 minutes with a crew label that is never a person. Its totals are exact sums over the shift's setups, line by line. One field is non-protocol: `bale_sqft`, a scale reading of baled waste that is deliberately a little more than every recorded line, so the "unexplained" tile has something honest to show:

```json
{"shift_number": 2, "production_date": "20260914", "group_name": "Crew B", "start_time": "150000", "end_time": "230000", "duration": "080000",
 "total_lineal_ft": 315647.4, "total_produced_sqft": 2694376.9, "side_trim_sqft": 82194.9, "chop_sqft": 10992.0, "reject_sqft": 33025.2,
 "changeover_sqft": 4356.4, "admin_sqft": 1396.2, "cutoff_waste_sqft": 1697.6, "stacker_waste_sqft": 1913.2,
 "max_speed_fpm": 854.7, "avg_speed_fpm": 657.6, "wet_end_ids": ["WE20260914012", "…", "WE20260914025"], "scheduled_minutes": 480, "bale_sqft": 144113.5}
```

**Stop**, one row of the downtime log, always inside exactly one setup. It has a category (`kind`), a place, a reason and the operator's comment. The comment is often empty, as this one is, and is quoted verbatim when it is not:

```json
{"stop_id": "ST20260914220041", "start_time": "220041", "end_time": "220144", "duration_sec": 63, "kind": "Operator", "place": "Glue dams",
 "reason": "Glue / starch", "shift_number": 2, "production_date": "20260914", "setup_id": "CORR2026091421334230", "comment": ""}
```

## 2. The invariants that hold the families together

Each line is one rule the generator keeps for every record of both months and the test in `tests/test_records_invariants.py` (unless named otherwise) that walks every record to prove it.

- The calendar is every weekday × 3 shifts of 480 minutes at 07:00 / 15:00 / 23:00, labelled by the production day that starts at 07:00 (the invariant tests walk August and September 2026: 43 weekdays) — `test_calendar_is_three_shifts_per_weekday_of_both_months`.
- The web is centered and is the narrowest paper that fits: the lanes' sheet widths × outs plus the trim equal the web width, the trim is at least the day's minimum, and no narrower paper would hold the lanes — `test_web_geometry_is_centered_on_the_narrowest_paper`.
- Lanes share travel: a setup's lanes end within one knife rotation, as scheduled and as run, the longest lane is the setup's order travel and its scheduled lineal, and counted cuts stay within ±10 % of nominal, mostly within ±2 % — `test_lanes_end_within_one_knife_rotation_and_the_longest_lane_is_the_setup`.
- Area reconciles: gross = lineal × width / 12; chops are whole 34-inch cuts; trim = order travel × trim inches / 12; each reject line is sheets × the lane's sheet size and names a category and reason of the taxonomy with an allowed position; the lanes' cutoff-knife and stacker waste boards convert with the lane's sheet size; the swing knife is null; a setup's speed is lineal over elapsed minutes including its stops — `test_setup_area_accounting`.
- One order per lane, and the change code tells the truth: `O` starts a new lane combination, `S` continues all lanes across a shift boundary; an order that leaves the schedule and comes back later in its day keeps its sheet, grade and customer — `test_one_order_per_lane_and_change_codes`. The owner's worked example and the contract's other identities are in `tests/test_record_contract.py`.
- A wet end is the exact aggregate of its contiguous setups and spans at most two shifts — `test_wet_end_is_the_exact_aggregate_of_its_setups`.
- A shift is the exact aggregate of its setups, and the bale reading exceeds the accounted waste by 0.1–0.4 % of gross — `test_shift_is_the_exact_aggregate_of_its_setups`.
- Setups tile every shift with no gap; every stop sits inside one setup, stops never overlap, and a setup's stop count and seconds equal its stops — `test_time_partition_setups_tile_shifts_and_stops_sit_inside`.
- Maintenance + planned maintenance (scheduled) downtime is 2–3 % of scheduled minutes per month and operator downtime 1–3 %, with one planned window every Thursday on shift 1, recorded as kind `Scheduled` with its own reason `Planned maintenance` — `test_downtime_budget_per_month`.
- Per month, side trim is 2.5–3.75 % of gross (schedule-driven, September carries the story's creep), shear chops 0.4–1.0 %, dry-end rejects 0.9–1.3 % split into bond, warp, misalignment, moisture and other, changeover rejects, admin entries and the waste boards in their own bands — `test_waste_bands_per_month`.
- Each production day makes 5.5–7.5 million gross square feet from 130–230 setup records — `test_output_per_production_day`.
- The same seed gives the same bytes: the content hash is pinned — `tests/test_synth_determinism.py::test_pinned_content_hash`.
- The store's per-day tables and period tools reconcile to plain Python sums over the families, field by field — `tests/test_record_store.py`, `tests/test_period_tools.py`.

These are demo choices, not plant standards (the simulation constants are at the top of `synth.py`). What matters is that they are stated, kept, and checked.

## 3. The seeded stories, and why the answer keys stay away from the agent

Three effects are planted in the records by `src/investigator/stories.py`:

1. A slitter/scorer rebuild in the Thursday 20 August planned maintenance window. Afterwards side trim creeps about 0.1 percentage point a week until 31 October, then returns to its pre-story level, and knife / slitter jam stops rise, concentrating on the slitter/scorer.
2. A missed-splice problem on Splicer 3 in the week of 7 September: missed-splice minutes roughly triple, and the shear chop follows because every missed splice is a web break and a run of full-width chops.
3. Warp rejects run 1.5× on the first production day after a weekend and higher in August than in September; bond and misalignment stay flat.

Only the generator imports `stories.py`. The analysis layer, the agent, the server, the reports and the narrative never do, and `test_answer_keys_are_not_imported_by_any_tool_layer` fails the build if one starts to. So an investigation has to find the effects in the records — the trim trend, the place Pareto, the Monday waste breakdown — rather than look the answer up, and the tests that use the keys (`test_story_signals_are_visible_in_the_records`, the investigation goldens) use them only to build expectations.

## 4. The record store and its day tables

`src/investigator/record_store.py` builds the window once per process (about twenty-five seconds generating, or about 400 ms loading the folder the image prebuilt) into one in-memory DuckDB with external access disabled: the record contract's tables `setups`, `setup_orders`, `customers`, `rejects`, `reject_reasons`, `wet_ends`, `rolls`, `shifts`, `stops` ([the record contract](RECORD_CONTRACT.md)). Two doors lead out of it.

`day_analysis(production_day, corrupt=False)` validates each setup of one day (finite non-negative quantities, a known web width and grade, gross = lineal × width / 12, waste within gross, a sane interval, one to three order lanes in order, reject lines that reconcile with their lane's sheet size) and loads the five per-day tables the daily tools have always read. A record that fails validation is excluded with its raw values and makes its shift's coverage `incomplete`; the `corrupt` toggle injects a 99,999,999 sq ft trim value into the first setup of shift 2 so that path can be demonstrated on demand. A weekend date or a date outside the window (1 January 2025 – 30 September 2026 in the tests) is refused with the calendar reason; the server returns the same message as a 400.

The six period tools read the store directly with bound window dates, so "this month versus last" is one fixed read over the whole dataset, not a loop over days.

## 5. The tools

Every tool is a fixed, parameterized query: grouping identifiers come from an allow-list, every visitor value is a bound parameter, results are capped at 1,000 rows and interrupted after two seconds, and each result carries its SQL, parameters, timing, `coverage` and `excluded_records`. There is no arbitrary SQL, shell, file, network, email or machine-control tool ([AGENT_CONTRACT.md](AGENT_CONTRACT.md)).

The nine daily tools (`src/investigator/analysis.py`) take a shift: `get_shift_kpis` (output, speed to grade target, downtime by kind, waste percentages, throughput), `get_shift_overview` (KPIs plus three chart components in one call), `get_downtime_breakdown` (stops by reason, place or kind, optional kind filter), `get_wet_end_performance` (each paper run against its grade target over elapsed time), `get_quality_breakdown` (rejected area by reason), `get_setup_matrix`, `get_order_matrix`, `get_shift_notes` (untrusted text, rendered as text) and `get_rankings` (one metric, withheld when coverage is incomplete).

The PDF report (`reports.py` + `pdf_charts.py`) is built server-side from the retained investigation: every chart becomes a native vector drawing from the tool rows (never a screenshot), each followed by its data table, and a speed-opportunity result adds an opportunities section with the reject penalty beside each gap.

The eleven period tools (`record_store.py`) take a window `{period: day|week|month|fiscal, anchor}` (fiscal = the 4-4-5 accounting period, 4-4-5 weeks from Monday 3 August) and an optional shift: `get_period_kpis` (twenty-five dashboard tiles with the previous window beside, and a production-day count for both windows so a 21-day month against a 22-day one, or a 5-week period against a 4-week one, is flagged rather than compared raw), `compare_periods` (the same table for two arbitrary windows), `get_downtime_pareto` (share and cumulative share, previous window beside), `get_waste_breakdown` (every waste line: trim, shear, changeover, cutoff-knife and stacker waste boards, the dry-end families bond, warp, misalignment, moisture and other, planning / admin, bale and unexplained, with the reported reasons as `details`), `get_stops_profile` (per production day plus the Monday-to-Friday profile), `get_trend` (a weekly series of one allow-listed metric, 1–26 weeks), `get_grade_totals` (volume, lineal, speed against target, allocated downtime and run length by board grade) and `get_overrun` (nominal versus actual cuts by knife, with the share of lines inside the ±2 % band, drawn as a bullet chart), `get_speed_opportunity` (typical versus best-fifth speed by grade and width or crew, opportunity hours, and the reject rate of the fast setups beside it) `get_rollstock` (paper consumed from the wet-end records: lineal feet and MSF per grade and web width, paper runs, the previous window beside) and `get_speed_variance` (the speed gap against each grade's trailing-year baseline, the previous window or another shift, split into stops, setups, running under baseline and grade mix that add up to it, with planned maintenance and external stops as buckets of their own; the mix and planned maintenance schedule-driven and external stops external, none of them priced; drawn as a waterfall). Three rules run through all of them:

- **Ratio of sums.** Every percentage is summed numerators over summed denominators across the window — September side trim is sum(trim sq ft) / sum(gross sq ft) over 4,115 setup records, never the mean of 22 daily percentages, and the test suite pins that the two differ. Deltas are the difference of the two displayed tiles, so a tile reconciles on screen.
- **The `better` map.** Each tile says which direction is good — `higher` for volume and speed, `lower` for downtime and waste, `neutral` for counts that are context, not a score — and the `change` label (`better`, `worse`, `same`, `neutral`) is computed from it, so the interface never has to guess what a red arrow means.
- **Withheld comparisons.** A window must overlap the dataset. Coverage is `complete`, `partial` or `unavailable` per window; deltas and change labels are withheld unless both windows are complete, previous tiles read `null` when the previous window is unavailable, and weekends and the days before the window's first are never zero.

Formulas and targets are in [METRICS.md](METRICS.md).

## 6. The planners

A planner implements one method, `next_step(context) -> {tool, arguments} | {final: [ids]} | {unavailable: reason}`. It chooses; it never calculates.

- **OfflinePlanner** (`src/investigator/agent.py`) is rule-based: a keyword router maps supported question shapes ("why was this shift slow", "compare this month to last", "which downtime reason cost the most minutes this week", "trend of trim over eight weeks") to a fixed tool plan. It is deterministic, free, and deliberately not presented as a language model. Unsupported questions raise a clear refusal.
- **AnthropicAnalyst** (same file; issue #23) is what the product uses: one planning call that returns a JSON list of `{tool, arguments}`, checked whole against the allowlist by `validate_plan`, the tools run by `Agent.ask_planned`, then one narrative call streamed under the deterministic answer with every value substituted from a tool row. It is opt-in per answer ("Ask the analyst") and runs outside the server's shared lock; see [ANTHROPIC_DEMO.md](ANTHROPIC_DEMO.md).
- **AnthropicPlanner** (`src/investigator/anthropic_provider.py`; the earlier tool loop, kept to replay the recorded live goldens until they are re-recorded) runs the same contract over the Messages API for the hosted demo. The server holds `ANTHROPIC_API_KEY`; visitors never supply or see a key. Each round allows at most ten model calls in 60 seconds, 64 KB per serialized request (older tool results are trimmed to a citable stub before a request is refused), 2,048 output tokens for a planning step and 4,096 for the narrative, one `tool_use` block per turn with `finish` and `cannot_answer` as the only ways to end; a text-only reply closes with the evidence gathered so far, and `refusal` or `max_tokens` executes nothing. In public mode a per-IP rate limit (`DEMO_RATE_LIMIT_PER_MIN`), a service-wide daily cap on rounds (`DEMO_DAILY_CAP`) and the Anthropic console spend limit sit in front of it ([ANTHROPIC_DEMO.md](ANTHROPIC_DEMO.md)).
- **OpenAIPlanner** (`src/investigator/openai_provider.py`) is bring-your-own-key on the loopback workbench: the key travels in a header for one request, is never stored, and the strict Responses function schemas, the same call / time / byte limits and the same spending reservation apply ([OPENAI_BYOK.md](OPENAI_BYOK.md)).

Both live adapters receive the same process rules and the same tool schemas, see compact tool results (no SQL, no source ids, matrices previewed to twelve rows), and may add a short narrative afterwards through a `write_narrative` tool whose numbers are placeholders that application code fills from cited evidence fields; a narrative with a literal digit, an unknown field or a finding on incomplete evidence is rejected (`src/investigator/narrative.py`).

## 7. The agent loop and its bounds

`Agent.ask(question, context)` in `agent.py` is the whole loop: ask the planner for one step; validate it in application code (a known tool, exactly the declared arguments, a shift inside the selected scope — under "selected shift only" even a period tool must name that shift); execute the tool; add the result to the run's evidence store under `run_id:step`; repeat until `finish` names successful evidence or a bound trips. Offline bounds are 20 tool calls and ten seconds; live bounds are ten calls and 60 seconds plus the adapter's own ten model calls; the planner context is capped at 200,000 serialized characters. A rejected tool call consumes a call and stays in the trace with its reason; when every call was rejected and the planner has no evidence to finish on, the answer is `unavailable` and the rejection reasons are its text ("How did October go?" answers that the month window is outside the dataset range), never a `planner_error` with the reason buried in the trace. `render_chart` may cite only a successful, chartable result from this run and copies its rows. Context carries the production day, shift, scope, dataset hash and at most four recent question / scope entries; a changed dataset hash clears history.

The answer is assembled by application code, not the model: `sections` (plain-text evidence lines in which every number is a tool-row value, shaped per intent as a headline, findings whose bold figure cites its row, and where to look; issue #75, [agent contract](AGENT_CONTRACT.md#answer-shapes)), `charts` (deep copies of their source rows; one is drawn for every chartable piece of evidence the answer rests on, whether or not the model asked for it, and each narrative paragraph names the charts that support it), `tables`, the `trace` (every tool call with arguments, result or error, SQL, parameters and timing), `narrative` paragraphs with citations of the form `Shift 2, observed_shift_fpm, <result_id>, row 0` (every number in the prose is a placeholder the application fills from a cited row, and numbers written as words are refused too; a cited fact the writer does not place stays as a pointer, so a ranking named in words with only its values placed is fine; the selected shift number may appear as plain text when the evidence carries that shift; `shift` may also be cited as a field and is answered from the result's metadata), and `usage` (tool calls, model calls, tokens, estimated and reserved cost). A question that asks for a chart and gets none is marked `partial`; a question about profit or savings gets the standing disclosure that no costs or outcomes exist in the records. The trace is inspectable execution evidence, not a durable audit log.

## 8. The golden set

`evals/run.py` runs `evals/golden.json` and `evals/golden_values.json` and prints one score table; `tests/test_evals.py` runs the same offline cases in CI. Seven case types, each proving one thing:

| Type | Proves | Scored by |
| --- | --- | --- |
| routing | the planner picked the right tools and arguments for a phrasing | required calls (argument subset), allow-list, counts |
| faithfulness | every number in the answer came out of a tool row | every numeric token must equal a tool value under one of the application's formats |
| grounding | citations resolve and nothing was silently dropped | `(result_id, row, field)` lookups; a chart that could not be drawn is disclosed |
| refusal | out-of-scope requests are refused rather than answered | the refusal text, or a non-complete status with the disclosure, or forbidden phrases absent; a month after the window is unavailable, never zero; a Saturday is refused with the calendar reason |
| value | a tool's numbers equal a second, plain computation | 58 generated cases, 3,145 `[expected, tolerance]` pairs |
| investigation | a superintendent's multi-turn question finds a seeded story | required evidence covering the story window, its numbers in the prose, no cause asserted that the evidence lacks |
| data_quality | the corrupt record is flagged, excluded, reported, never averaged in | coverage, the reported raw value, the impossible number absent, rankings withheld, the remaining sums pinned |

**How the goldens are made.** With synthetic data we own the truth, so an expected answer is never typed by hand and never read back from the tool it checks. `evals/make_value_goldens.py` computes every value case by plain Python sums over `synth.generate_window()` for the golden window (1 January 2025 to 2026-09-30) — its own calendar arithmetic, its own ratio-of-sums formulas, its own Pareto ordering — and writes `golden_values.json` stamped with the dataset content hash. The runner scores the golden set against the store of that window, whatever window the serving process holds, and compares the hash with it before scoring and fails every value case when they differ, so an intended generator change forces a deliberate regeneration and an accidental one is caught. A tolerance rides with each number to cover the tool's display rounding; integers, strings and `null` are exact. The committed file must be byte-for-byte what the generator produces (`tests/test_goldens_values.py`).

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
- **No recorded money.** The records hold no costs, prices, revenue or measured intervention outcomes, so no savings or profit claim can be grounded, and the agent says so when asked. The dollars it shows (issue #31) are the records' quantities priced at assumed rates, named in every answer that carries them, not the plant's figures.
- **No delivery.** No email, no subscribers, no schedule writes, no equipment actions, no web search.
- **No calendar beyond the demo.** No holidays, no timezone or daylight-saving handling, weekends are plant-down and emit nothing, and the 4-4-5 anchor is provisional.
- **No order ledger, no overlap policy.** Requested and remaining sheets are null in the record families; stops never overlap or cross a shift; reject replacement and accepted-shipment accounting are not modelled.
- **No multi-tenant service.** The loopback workbench and the hosted demo are a teaching server with bounded guards, not identity, durable rate limits or centralized budgets.

A completed investigation means the tool run finished with evidence a person can check, not that a root cause was proved.
