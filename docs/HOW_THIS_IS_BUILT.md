# How this is built

A ten-minute walk through the Corrugator Shift Investigator in the order the data flows: from one seed to the record contract's families, through the invariants that hold them together, into a store and a set of bounded tools, through a planner that may only choose, out as evidence with a trail, and finally into a golden set that owns its own truth. Every section names the file that does the work and the test that proves it. Nothing here is a real plant, a vendor protocol, or a proof of cause; the last section says so plainly.

```
 seed 2026 + date ──► synth.generate_day() ×262 ──► record families ──► content hash b89074754b8d…
                     ▲                         (the year ending 2026-09-30)
                     │                      dry-end setups  33,441   (63,045 order lanes, 244,892 reject lines)
              stories.py (answer keys)      wet ends        11,971
              read by the generator only    shifts             786
                                            stops            6,690
                                            rolls           63,629   + the customer dimension, 49
                                                   │
                                                   ▼
                                  RecordStore: one in-memory DuckDB, built once
                    setups · setup_orders · customers · rejects · wet_ends · shifts · stops · rolls
                                      │                                  │
                         day_analysis(day, corrupt)              the period tools
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

Everything starts with `synth.generate_day(day, seed=2026)` in `src/investigator/synth.py`. For one Monday-to-Friday production day (three 480-minute shifts starting 07:00, 15:00 and 23:00) on one machine called `CORR-1`, it draws the day's orders (a customer, a sheet, a quantity, a grade) and schedules them into dry-end setups the way a corrugator scheduler does: two orders paired across the two cutoff knives to fill the web, the shorter lane setting the setup's length, the other's remainder carried into the next setup. It lays the setups out over the shifts with their stops and emits the records defined in `src/investigator/records.py`. Every random draw of the day comes from streams keyed by the seed and a date (the day's own, its week's and its month's), and nothing carries into the next day, so a day is byte-identical whatever range it is generated in (issue #35). The dataset is a rolling year: every production day from 365 days before `as_of` to `as_of`, 262 days for the year ending 2026-09-30 that the tests and the golden set read; the hosted demo's year ends on the day before its image was built and moves every night. The same seed gives byte-identical JSONL files and one SHA-256 content hash over the range; change the seed, the window or the machine id and the hash changes.

The records follow [the record contract](RECORD_CONTRACT.md) (`demo-records/v2`, issue #100). It describes a generic corrugator dry-end setup record: the setup is the fact table, with its order lanes embedded as a machine writes them. Customers are a dimension. The paper runs (wet ends), their roll stock, the downtime log, the reject lines (category · reason · position) and the shifts link to it by key. Each field is mapped to the typical MES field it stands for. No vendor wire format, field semantics or protocol compatibility is claimed. The shape is the point: an agent that learns to read these will not be confused by real plant records, and nothing here needs a machine connection.

One example of each, from Monday 14 September, shift 2, as the generator writes it today.

**Dry-end setup.** Eleven minutes of a 275-BC run on 95-inch paper. Two orders across the web: a 60-inch sheet one out on knife 1, continuing from the setup before, and a 33-inch sheet one out on knife 2. That makes 93 inches used and 2 inches of trim, centered. There is one stop inside it:

```json
{"setup_id": "CORR2026091421225934", "production_date": "20260914", "shift": 2, "start_time": "212259", "end_time": "213434",
 "run_seconds": 617, "lineal_ft": 4869.7, "scheduled_lineal_ft": 4922.5, "avg_fpm": 420.4, "web_width_in": 95, "quality_id": "275-BC",
 "gross_sqft": 38551.8, "trim_sqft": 804.5, "trim_pct": 2.11, "shear_sqft": 336.5, "swing_knife_sqft": null, "reject_sqft": 355.6,
 "cutoff_waste_sqft": 41.0, "stacker_waste_sqft": 27.0, "change_code": "C", "stop_seconds": 78, "stops": 1, "wet_end_id": "WE20260914033",
 "lanes": [{"lane": 1, "cutoff_knife": 1, "order_number": "SO-2609140006", "customer_id": "CU006", "order_quantity": 2490, "outs": 1,
            "sheet_width_in": 60, "sheet_length_in": 66, "nominal_cuts": 895, "actual_cuts": 877, "cutoff_waste_boards": 1, "stacker_waste_boards": 0},
           {"lane": 2, "cutoff_knife": 2, "order_number": "SO-2609140012", "customer_id": "CU002", "order_quantity": 1830, "outs": 1,
            "sheet_width_in": 33, "sheet_length_in": 59, "nominal_cuts": 1001, "actual_cuts": 981, "cutoff_waste_boards": 1, "stacker_waste_boards": 2}],
 "rejects": [{"lane": 1, "category": "bond", "reason": "Blisters (tension blister)", "liner": "top", "side": null, "sheets": 3, "sqft": 82.5},
             {"lane": 1, "category": "warp", "reason": "Up warp", "liner": null, "side": null, "sheets": 5, "sqft": 137.5},
             {"lane": 2, "category": "bond", "reason": "Delamination", "liner": "top", "side": "drive", "sheets": 2, "sqft": 27.0}, "…"]}
```

(A few fields are left out here; [the contract](RECORD_CONTRACT.md#the-setup-math) prints the whole record.) Read it the way the tools do. Lane 1 had 895 sheets left of its order and needs the least travel, so it sets the length: 895 cuts × 66 in = 4,922.5 ft, the scheduled lineal. Lane 2 runs as far as its pair, ⌊895 × 66 / 59⌋ = 1,001 cuts, ending within one 59-inch cut of lane 1, and its 829 remaining sheets carry into the next setup. The run made 1.9% less travel than planned, which is why the actual cuts are 877 and 981. Fifteen 34-inch full-web chops (336.5 sq ft) sit inside the 4,869.7 lineal ft, so gross is 4,869.7 × 95 / 12 = 38,551.8 sq ft and trim is the order travel × 2 in / 12. `change_code: C` says an order continued from the previous setup. The reject lines carry the owner's category and reason with the position as attributes; they add up to `reject_sqft`.

**Wet end.** One paper run: consecutive setups sharing grade and web width. This one holds three setups, the one above in the middle; its totals are exact sums of theirs:

```json
{"wet_end_id": "WE20260914033", "quality_id": "275-BC", "web_width_in": 95, "start_time": "210149", "end_time": "214551", "duration": "004402",
 "lineal_ft": 17692.5, "gross_sqft": 140065.6, "trim_sqft": 4322.0, "max_fpm": 473.6, "avg_fpm": 401.8,
 "setup_ids": ["CORR2026091421014933", "CORR2026091421225934", "CORR2026091421343435"], "shifts": [2], "production_date": "20260914"}
```

**Rolls.** The run fed five webs (275-BC is double wall). Each roll records the paper fed, the wraps peeled at prep, and what stayed on its core when it was spliced:

```json
{"roll_id": "RL20260914033301", "wet_end_id": "WE20260914033", "position": "SF2 liner", "paper_grade": "42# liner", "width_in": 95,
 "lineal_ft": 17409.9, "consumed_sqft": 137828.4, "peel_sqft": 325.3, "core_sqft": 220.1, "ended_at": "splice"}
```

**Shift.** 480 minutes, a crew label that is never a person, exact sums over the shift's setups, and one non-protocol field, `bale_sqft`. It is a scale reading of baled waste, deliberately a little more than trim + chops + rejects + waste boards so the "unexplained" tile has something honest to show:

```json
{"shift": 2, "production_date": "20260914", "group_name": "Crew B", "start_time": "150000", "end_time": "230000", "duration": "080000",
 "lineal_ft": 275676.7, "gross_sqft": 2238765.7, "trim_sqft": 73113.1, "shear_sqft": 13650.9, "reject_sqft": 25855.8,
 "cutoff_waste_sqft": 2694.5, "stacker_waste_sqft": 1488.7, "max_fpm": 854.6, "avg_fpm": 574.3,
 "wet_end_ids": ["WE20260914022", "…", "WE20260914036"], "scheduled_minutes": 480, "bale_sqft": 123040.4}
```

**Stop.** A row of the downtime log, always inside exactly one setup, with a kind, a place, a reason and a comment that is often empty. When there is one, it is labelled unverified because it is:

```json
{"stop_id": "ST20260914212259", "setup_id": "CORR2026091421225934", "shift": 2, "start_time": "212259", "end_time": "212417",
 "seconds": 78, "kind": "Operator", "place": "Stacker", "reason": "Order change", "comment": ""}
```

## 2. The invariants that hold the families together

Each line is one rule the generator keeps for every record of both months and the test in `tests/test_records_invariants.py` (unless named otherwise) that walks every record to prove it.

- The calendar is every weekday × 3 shifts of 480 minutes at 07:00 / 15:00 / 23:00, labelled by the production day that starts at 07:00 (the invariant tests walk August and September 2026: 43 weekdays) — `test_calendar_is_three_shifts_per_weekday_of_both_months`.
- The web is centered: the lanes' outs × sheet width fit inside the web less the recorded trim, with 1 to 7 inches of trim a side — `test_lanes_fit_the_web_less_the_trim_for_every_setup` (and over the whole year in the store, `tests/test_record_contract.py`).
- The owner's six setup identities: used width + trim = web; a lane's travel is its cuts × sheet length; the lanes end within one knife rotation; the scheduled lineal is the longest lane; an order's scheduled sheets reach its quantity; gross = web × lineal and trim = trim inches × order travel — `test_setup_identities`, with the worked example in `tests/test_setup_math.py`.
- Counted cuts stay within ±10 % of nominal, mostly within ±2 % — `test_lineal_consistency_and_overrun_window`.
- Area reconciles: chops are whole 34-inch cuts; each reject line is sheets × sheet area and the dry-end lines sum to `reject_sqft`; waste boards are boards × sheet area; a setup's speed is lineal over elapsed minutes including its stops — `test_setup_area_accounting`.
- One order per lane, and the change code tells the truth: `O` shares no order with the previous setup, `C` carries one, `S` continues all lanes across a shift boundary — `test_one_order_per_lane_and_change_codes`.
- A wet end is the exact aggregate of its contiguous setups and spans at most two shifts — `test_wet_end_is_the_exact_aggregate_of_its_setups`.
- A shift is the exact aggregate of its setups, and the bale reading exceeds the accounted waste by 0.1–0.4 % of gross — `test_shift_is_the_exact_aggregate_of_its_setups`.
- Setups tile every shift with no gap; every stop sits inside one setup and stops never overlap; for every setup its stop rows' seconds sum to `stop_seconds` and their count is `stops` — `test_time_partition_and_the_downtime_log`.
- Maintenance + PM downtime is 2–3 % of scheduled minutes per month and operator downtime 1–3 %, with one PM window every Thursday on shift 1, recorded as kind `PM` with its own reason `Planned maintenance` — `test_downtime_budget_per_month`.
- Per month, side trim is 2.5–3.5 % of gross, shear chops 0.4–1.0 %, waste boards 0.1–0.4 %, dry-end rejects 0.9–1.3 % — `test_waste_bands_per_month`.
- Each production day makes 6–8.5 million gross square feet from 90–160 setup records — `test_output_per_production_day`.
- The customers are a dimension every lane points at, and the roll stock feeds every paper run with peel and core — `test_customers_are_a_dimension_every_lane_points_at`, `test_roll_stock_feeds_every_paper_run_with_peel_and_core`.
- The same seed gives the same bytes: the content hash is pinned — `tests/test_synth_determinism.py::test_pinned_content_hash`.
- The store's per-day tables and period tools reconcile to plain Python sums over the families, field by field — `tests/test_record_store.py`, `tests/test_period_tools.py`.

These are demo choices, not plant standards (the simulation constants are at the top of `synth.py`). What matters is that they are stated, kept, and checked.

## 3. The seeded stories, and why the answer keys stay away from the agent

Three effects are planted in the records by `src/investigator/stories.py`:

1. A slitter/scorer rebuild in the Thursday 20 August PM window. Afterwards side trim creeps about 0.1 percentage point a week until 31 October, then returns to its pre-story level, and knife / slitter jam stops rise, concentrating on the slitter/scorer.
2. A missed-splice problem on Splicer 3 in the week of 7 September: missed-splice minutes roughly triple, and the shear chop follows because every missed splice is a web break and a run of full-width chops.
3. Warp rejects run 1.5× on the first production day after a weekend and higher in August than in September; bond and misalignment stay flat.

Only the generator imports `stories.py`. The analysis layer, the agent, the server, the reports and the narrative never do, and `test_answer_keys_are_not_imported_by_any_tool_layer` fails the build if one starts to. So an investigation has to find the effects in the records — the trim trend, the place Pareto, the Monday waste breakdown — rather than look the answer up, and the tests that use the keys (`test_story_signals_are_visible_in_the_records`, the investigation goldens) use them only to build expectations.

## 4. The record store and its day tables

`src/investigator/record_store.py` builds the year once per process (about twelve seconds generating, or about 200 ms loading the folder the image prebuilt) into one in-memory DuckDB with external access disabled: `setups`, `customers`, `rejects`, `wet_ends`, `shifts`, `stops`. Two doors lead out of it.

`day_analysis(production_day, corrupt=False)` validates each setup of one day (finite non-negative quantities, a known web width and grade, gross = lineal × width / 12, waste within gross, a sane interval, one to three knife positions in order, reject lines that reconcile) and loads the five per-day tables the daily tools have always read. A record that fails validation is excluded with its raw values and makes its shift's coverage `incomplete`; the `corrupt` toggle injects a 99,999,999 sq ft trim value into the first setup of shift 2 so that path can be demonstrated on demand. A weekend date or a date outside the window (30 September 2025 – 30 September 2026 in the tests) is refused with the calendar reason; the server returns the same message as a 400.

The six period tools read the store directly with bound window dates, so "this month versus last" is one fixed read over the whole dataset, not a loop over days.

## 5. The tools

Every tool is a fixed, parameterized query: grouping identifiers come from an allow-list, every visitor value is a bound parameter, results are capped at 1,000 rows and interrupted after two seconds, and each result carries its SQL, parameters, timing, `coverage` and `excluded_records`. There is no arbitrary SQL, shell, file, network, email or machine-control tool ([AGENT_CONTRACT.md](AGENT_CONTRACT.md)).

The nine daily tools (`src/investigator/analysis.py`) take a shift: `get_shift_kpis` (output, speed to grade target, downtime by kind, waste percentages, throughput), `get_shift_overview` (KPIs plus three chart components in one call), `get_downtime_breakdown` (stops by reason, place or kind, optional kind filter), `get_wet_end_performance` (each paper run against its grade target over elapsed time), `get_quality_breakdown` (rejected area by reason), `get_setup_matrix`, `get_order_matrix`, `get_shift_notes` (untrusted text, rendered as text) and `get_rankings` (one metric, withheld when coverage is incomplete).

The PDF report (`reports.py` + `pdf_charts.py`) is built server-side from the retained investigation: every chart becomes a native vector drawing from the tool rows (never a screenshot), each followed by its data table, and a speed-opportunity result adds an opportunities section with the reject penalty beside each gap.

The eleven period tools (`record_store.py`) take a window `{period: day|week|month|fiscal, anchor}` (fiscal = the 4-4-5 accounting period, 4-4-5 weeks from Monday 3 August) and an optional shift: `get_period_kpis` (twenty-five dashboard tiles with the previous window beside, and a production-day count for both windows so a 21-day month against a 22-day one, or a 5-week period against a 4-week one, is flagged rather than compared raw), `compare_periods` (the same table for two arbitrary windows), `get_downtime_pareto` (share and cumulative share, previous window beside), `get_waste_breakdown` (trim, shear, the cutoff-knife and stacker waste boards, bond, warp, misalignment, cracking, bale, unexplained, with each dry-end category's reported reasons as `details`), `get_stops_profile` (per production day plus the Monday-to-Friday profile), `get_trend` (a weekly series of one allow-listed metric, 1–26 weeks), `get_grade_totals` (volume, lineal, speed against target, allocated downtime and run length by board grade) and `get_overrun` (nominal versus actual cuts by knife, with the share of lines inside the ±2 % band, drawn as a bullet chart), `get_speed_opportunity` (typical versus best-fifth speed by grade and width or crew, opportunity hours, and the reject rate of the fast setups beside it) `get_rollstock` (paper consumed from the wet-end records: lineal feet and MSF per grade and web width, paper runs, the previous window beside) and `get_speed_variance` (the speed gap against target, the previous window or another shift, split into stops, setups, below-target running and grade mix that add up to it, drawn as a waterfall). Three rules run through all of them:

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

**How the goldens are made.** With synthetic data we own the truth, so an expected answer is never typed by hand and never read back from the tool it checks. `evals/make_value_goldens.py` computes every value case by plain Python sums over `synth.generate_window()` for the golden window (the year ending 2026-09-30) — its own calendar arithmetic, its own ratio-of-sums formulas, its own Pareto ordering — and writes `golden_values.json` stamped with the dataset content hash. The runner scores the golden set against the store of that window, whatever window the serving process holds, and compares the hash with it before scoring and fails every value case when they differ, so an intended generator change forces a deliberate regeneration and an accidental one is caught. A tolerance rides with each number to cover the tool's display rounding; integers, strings and `null` are exact. The committed file must be byte-for-byte what the generator produces (`tests/test_goldens_values.py`).

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
