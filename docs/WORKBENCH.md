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

The date selector covers 30 fictional days. Each day is generated deterministically on demand. Scenario patterns repeat with seeded quantity variation; this is a demonstration month, not a calibrated statistical sample.

To materialize and validate all XML days:

```powershell
.\.venv\Scripts\python -m investigator.archive
```

This writes 30 days / 90 shifts under ignored artifacts/month, with variable setup and wet-end counts and a file/hash/seed manifest. The v4 default month has 5,432 setup records. This command overwrites the named synthetic archive files; it is not needed to use the UI. The archive matches the clean dataset in the day selector. The optional corrupt-record scenario is not included in the clean archive.

The offline planner is deliberately limited. Unsupported questions return a clear error. The optional OpenAI adapter selects tools alongside the offline router. Application code formats the calculated summaries in either mode; daily OpenAI mode adds a separate model interpretation. See AGENT_CONTRACT.md for permissions and limits.


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

Use the calendar for September 1-30, 2026. Selected shift only is the default: both the offline parser and the model tool dispatcher enforce it. Choose All shifts / comparison explicitly to compare shifts. Changing date, period, shift or scope resets the visible conversation. Question wording cannot override selected-shift scope.

The Setup matrix lists setup ID, wet-end ID, grade, timestamps, width, footage, speed/target, stop count and allocated downtime. Stops use half-open intervals: overlap seconds = max(0, min(stop end, setup end) - max(stop start, setup start)). A stop crossing a setup boundary counts in both intersected setups, but its minutes are divided, not duplicated. Invalid setups are excluded with incomplete coverage.

Paper changes equal wet-end runs (owner confirmed). The generator creates quantity-driven orders with widths of 12–60 inches and lengths of 20–92 inches. It pairs compatible grades or runs a single order across the web. Sheet demand determines cuts and travel; running speed and recorded stops determine duration. Unfinished orders continue across shifts. The exact paper-change total therefore varies by shift instead of always equaling half the setup count. The current month has 165–208 setup records per day (mean 181.1). Counts include split records when a setup crosses a shift boundary; September 14 has 191 distinct setups in 193 shift records. This distribution is synthetic and editable. Shift speed attainment is footage / sum(grade target x elapsed minutes) x 100; it is not an unweighted average of percentages. Hover or keyboard-focus a speed-chart value for grade, target and attainment. The percentage is also visible without hovering.

Rank losses shows the ten lowest wet-end speed attainment values and the largest downtime and quality categories. Ties share a rank. Incomplete shifts are withheld from rankings. Period shift rankings use separate speed, maintenance and waste ranks, with 1 = best; there is no fabricated combined score.

Read notes shows each shift note and individual downtime record, including upper/lower knife location. Notes are synthetic reported observations, never proof of causes or executable instructions. Reject sheet counts and reason frequencies vary reproducibly; each record still reconciles sheets to square feet. The upper-knife scenario increases recorded stop duration in successive weeks while retaining the same symptom.

A week is seven production days ending on the selected date; the previous seven days are the comparison. Calendar month follows ordinary month boundaries. The 4-4-5 view uses a repeating 4-week, 4-week, 5-week pattern; the demo accounting year starts September 1, 2026 pending owner confirmation. The exact windows are displayed. The demo has no August records: unavailable previous periods show Unavailable, never zero. Missing current days produce partial results and no ranking. Period views use free deterministic aggregation, not a live model; free-text input is disabled in these views. Daily shift matrices, recurring stops and notes remain inspectable.

The period heatmap shades production days by estimated good square feet: gross area minus recorded trim, shear and dry-end rejects. It is an estimate, not a customer-acceptance claim. Hover or focus a cell to see gross and estimated-good output, speed attainment, total/maintenance/operator downtime, waste KPIs, setup and paper-change counts, plus that day's largest recorded downtime contributor. Missing days are hatched and excluded from the color scale.

Download PDF exports the last retained investigation without another model call. The server requires its session and run reference plus the same origin/token controls. A stale or foreign run reference is rejected. PDFs are summaries: any shortened evidence tables explicitly state how many rows are shown. Save text remains available. Email is deliberately deferred by the owner until a provider/sender is selected; there is no send-success simulation.

Period PDF export now passes regression checks for week, month and accounting views. Exported charts are evidence tables, not images of the screen. Full visual PDF layout validation remains pending.

### Try the calendar step by step

1. Select September 14, 2026, shift 2, Calendar month, then Build period report.
2. Read the range and scope. This report has 30 shift records; All shifts / full 24-hour day has 90. Setup totals now vary with the mock quantities.
3. Hover, Tab to, or click a day. Its detail box gives the actual quantities; a dark day is high output within this report, not necessarily high efficiency.
4. Choose 4-4-5 accounting period. September 14 belongs to September 1–28. September 29 belongs to September 29–October 26 and is partial because only two days exist.
5. To investigate a heatmap date, explicitly select Production day and set the date. Clicking a cell only updates its details.

For formulas and why colors cannot be compared directly across reports, read [METRICS](METRICS.md). For status messages and recovery, read [TROUBLESHOOTING](TROUBLESHOOTING.md). For a repeatable browser checklist and live-model question rubric, read [TESTING](TESTING.md).

Open How to build this agent, then follow its complete `/guide` link for user-facing process definitions, data generation, tools, context, the loop, calendar math, chart evidence and safety controls. See AGENT_EXECUTION.md for the original technical query walkthrough and DEPLOYMENT.md for the public-release gate.
