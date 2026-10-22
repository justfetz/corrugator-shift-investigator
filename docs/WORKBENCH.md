# Use the local learning interface

Install and run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m investigator.server
```

Open http://127.0.0.1:8765/ on the same computer. The server is local only. No API key, model call or email service is involved.

Use Python 3.11 or newer. Run the installation commands once; on subsequent visits run only the server command in the project virtual environment. Stop it with Ctrl+C in that terminal. Python changes require a restart; frontend changes require a page reload. To use another port, append `--port 8766` and open `http://127.0.0.1:8766/`. Use the numeric loopback host, not localhost.

1. Keep the default second shift and ask "Why was second shift slow? Chart downtime by reason."
2. Read the KPI cards and charts. Grade target markers use each wet-end run's grade.
3. Expand "How the agent got here" and a tool call. Inspect its arguments, exact SQL, bound values, returned rows, record references and time.
4. Choose All shifts / comparison, then ask "Compare that with first shift". The default Selected shift only setting deliberately blocks that comparison. Changing scope resets the visible conversation. Follow-ups retain history while the selected controls stay unchanged.
5. Enable "Include a bad trim record" and repeat. The affected metrics become incomplete, with the rejected source value visible in the trace.
6. Save a Markdown report. This downloads a local report; it does not send email.

The date selector covers the record store's 43 production days: Monday to Friday from 3 August to 30 September 2026, generated once from one seed when the server starts (about four seconds) as the record families described in [RECORDS](RECORDS.md). Weekends are plant-down; selecting one is refused with the reason. Two months of one machine with three seeded effects to find; this is a demonstration dataset, not a calibrated statistical sample.

To materialize and validate all XML days:

```powershell
.\.venv\Scripts\python -m investigator.archive
```

This writes the legacy one-day XML fixture for 30 days / 90 shifts under ignored artifacts/month, with a file/hash/seed manifest. It is the older generator kept for its own tests; it is not the dataset in the day selector, which comes from the two-month record store (write those files with `synth.write_jsonl`, see RECORDS.md).

The offline planner is deliberately limited. Unsupported questions return a clear error. The optional OpenAI adapter selects tools alongside the offline router. Application code formats the calculated summaries in either mode; daily OpenAI mode adds a separate model interpretation. See AGENT_CONTRACT.md for permissions and limits.

### Ask about a shift, week or month versus the previous one

With Production day selected, these plain phrasings run the period tools over the whole two months (offline, no model call) and chart the result:

- "Compare this month to last" or "How did this week go versus the previous one?" — the dashboard tiles (volume MSF, lineal feet, speed with and without stops, downtime, stops, minutes per stop, trim, shear, dry-end with its bond/warp/misalignment split, bale, unexplained, maintenance + PM, operator, setups, paper changes) with the previous window, the delta and whether lower or higher is better.
- "How did September go versus August?" — the same table for two named months.
- "How did shift 2 go compared with last week?" — a named shift filters the window; under Selected shift only the selected shift is used.
- "Which downtime reason cost the most minutes this month?" — a Pareto with share and cumulative share and last month's minutes beside; "where did we stop most often this week" groups by place and counts.
- "Is it bond or warp?" — the waste breakdown (day window unless you name a week or month).
- "Stops per day this week" — per production day plus the Monday-to-Friday profile.
- "Trend of trim over eight weeks" — a weekly line for one metric (trim, shear, dry-end, warp, bond, misalignment, speed, downtime, stops, minutes per stop, MSF per day).

The window is anchored on the selected day: a week is its Monday–Friday production week, a month its calendar month. Windows before 3 August 2026 are unavailable, never zero, and deltas are withheld unless both windows are complete. "Yesterday" and "today" still ask you to use the day control, and a bare "Compare last week" is refused as ambiguous. Every percentage is a ratio of sums over the window (METRICS.md).


## Optional OpenAI mode

### Does the key summarize for me?

Yes. Daily OpenAI mode now selects evidence, then requests a short narrative with findings, hypotheses, checks and limitations. It appears above the calculated evidence. Numeric values are inserted from validated field references; citations identify the shift, result, row and field. The writer uses up to twelve preview rows per result and must disclose missing evidence. Period reports never call the model.

Use Summary + charts for an overview of speed, downtime and waste. With Include shifts set to All shifts / full 24-hour day, the default scope covers all three shifts; an explicitly named shift can narrow a daily question. The overview supplies three supporting charts per shift. Offline mode provides the same calculated overview for free; OpenAI adds a model-written interpretation.

Narrative validation rejects unknown references, unavailable values, literal numeric claims, malformed output and performance findings from incomplete evidence. It cannot prove that prose is relevant, causal or correct; number words and qualitative claims still need human review. Hypotheses are unverified. Budget exhaustion, a failed provider call or invalid narrative preserves the calculated report and charts with an explicit notice. The extra call shares the existing ten-call/time/spending limits. Live quality and endpoint compatibility still need a deliberate evaluation with a real key.

Select OpenAI in the mode control to use your own API account. See [OpenAI/BYOK setup and limits](OPENAI_BYOK.md). Real model access requires your key and incurs API charges; default offline use remains free.

For “How can we be better?”, first choose an objective: fewer downtime minutes, less recorded waste, or better grade-target attainment within the selected day and scope. Ask for the relevant breakdown and review the events with the crew. Adding a key supplies no missing plant knowledge, proven root cause, or savings estimate. Findings are investigation leads; target gaps are not guaranteed recoverable output. Read the README's “Ask a question the records can answer” section and `/guide#asking-questions` before interpreting broad improvement requests. Model tool selection remains fallible and awaits live evaluation.


## Calendar, matrices and reports

### Inspect an order

Choose Production day, then Orders + quantities. Each row is an order fragment on a knife. Width and length are sheet dimensions in inches; outs multiply cuts to get sheets. Requested and planned totals repeat when an order continues. Compare remaining before/after to see what each fragment produced, and planned overrun to see cutoff rounding. The same order keeps its dimensions and grade across shift boundaries. Completed gross production includes rejects; it is not accepted delivery. See METRICS.md for the simulation choices and time/area audits.

Use the calendar for the production days 3 August – 30 September 2026 (Monday to Friday). Selected shift only is the default: both the offline parser and the model tool dispatcher enforce it. Choose All shifts / comparison explicitly to compare shifts. Changing date, period, shift or scope resets the visible conversation. Question wording cannot override selected-shift scope.

The Setup matrix lists setup ID, wet-end ID, grade, timestamps, width, footage, speed/target, stop count and allocated downtime. Stops use half-open intervals: overlap seconds = max(0, min(stop end, setup end) - max(stop start, setup start)). A stop crossing a setup boundary counts in both intersected setups, but its minutes are divided, not duplicated. Invalid setups are excluded with incomplete coverage.

Paper changes equal wet-end runs (owner confirmed). The record generator lays out wet ends of one to four setups (every fifth a single order across the web) with sheet widths of 12–60 inches and cutoffs of 20–92 inches, one order per knife position; a continuing order keeps its number across setups and across a shift boundary. The exact paper-change total therefore varies by shift instead of always equaling half the setup count. The two months hold 7,968 setup records over 43 production days (about 185 per day, 150–190 by design). Counts include split records when a setup crosses a shift boundary (`change_code S`). This distribution is synthetic and editable; the records carry no order quantity ledger, so requested and remaining sheets read Unavailable in the order matrix. Shift speed attainment is footage / sum(grade target x elapsed minutes) x 100; it is not an unweighted average of percentages. Hover or keyboard-focus a speed-chart value for grade, target and attainment. The percentage is also visible without hovering.

Rank losses shows the ten lowest wet-end speed attainment values and the largest downtime and quality categories. Ties share a rank. Incomplete shifts are withheld from rankings. Period shift rankings use separate speed, maintenance and waste ranks, with 1 = best; there is no fabricated combined score.

Read notes shows the crew label for the shift (the record families carry no free-text shift note) and each individual stop record with its place and reason. Notes are synthetic reported observations, never proof of causes or executable instructions. Reject lines are per knife position and reason (bond, warp, misalignment); each still reconciles sheets to square feet. Three seeded effects are planted in the two months (RECORDS.md names them); the answer keys are read by the generator only, so an investigation has to find them in the records, for example with the weekly trend, the Pareto and the stops profile.

A week is the Monday–Friday production week containing the selected date; the preceding Monday–Friday week is the comparison. Calendar month follows ordinary month boundaries, so September compares with August. The 4-4-5 view uses a repeating 4-week, 4-week, 5-week pattern; the demo accounting year starts September 1, 2026 pending owner confirmation. The exact windows are displayed. The dataset starts on 3 August 2026: windows reaching before it (July, the week of 27 July, the first 4-4-5 period's predecessor) show Unavailable, never zero. Weekend cells are plant-down and hatched; a missing weekday produces partial results and no ranking. Period views use free deterministic aggregation, not a live model; free-text input is disabled in these views. Daily shift matrices, recurring stops and notes remain inspectable.

The period heatmap shades production days by estimated good square feet: gross area minus recorded trim, shear and dry-end rejects. It is an estimate, not a customer-acceptance claim. Hover or focus a cell to see gross and estimated-good output, speed attainment, total/maintenance/operator downtime, waste KPIs, setup and paper-change counts, plus that day's largest recorded downtime contributor. Missing days are hatched and excluded from the color scale.

Download PDF exports the last retained investigation without another model call. The server requires its session and run reference plus the same origin/token controls. A stale or foreign run reference is rejected. PDFs are summaries: any shortened evidence tables explicitly state how many rows are shown. Save text remains available. Email is deliberately deferred by the owner until a provider/sender is selected; there is no send-success simulation.

Period PDF export now passes regression checks for week, month and accounting views. Exported charts are evidence tables, not images of the screen. Full visual PDF layout validation remains pending.

### Try the calendar step by step

1. Select September 14, 2026, shift 2, Calendar month, then Build period report.
2. Read the range and scope. This report has 22 shift records (one per production day); All shifts / full 24-hour day has 66. The August comparison is real data.
3. Hover, Tab to, or click a day. Its detail box gives the actual quantities; a dark day is high output within this report, not necessarily high efficiency.
4. Choose 4-4-5 accounting period. September 14 belongs to September 1–28. September 29 belongs to September 29–October 26 and is partial because only two days exist.
5. To investigate a heatmap date, explicitly select Production day and set the date. Clicking a cell only updates its details.

For formulas and why colors cannot be compared directly across reports, read [METRICS](METRICS.md). For status messages and recovery, read [TROUBLESHOOTING](TROUBLESHOOTING.md). For a repeatable browser checklist and live-model question rubric, read [TESTING](TESTING.md).

Open How to build this agent, then follow its complete `/guide` link for user-facing process definitions, data generation, tools, context, the loop, calendar math, chart evidence and safety controls. See AGENT_EXECUTION.md for the original technical query walkthrough and DEPLOYMENT.md for the public-release gate.
