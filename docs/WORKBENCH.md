# Use the local learning interface

Install and run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m investigator.server
```

Open http://127.0.0.1:8765/ on the same computer. The server is local only. No API key, model call or email service is involved.

1. Keep the default second shift and ask "Why was second shift slow? Chart downtime by reason."
2. Read the KPI cards and charts. Grade target markers use each wet-end run's grade.
3. Expand "How the agent got here" and a tool call. Inspect its arguments, exact SQL, bound values, returned rows, record references and time.
4. Ask "Compare that with first shift". The selected production day carries forward.
5. Enable "Include a bad trim record" and repeat. The affected metrics become incomplete, with the rejected source value visible in the trace.
6. Save a Markdown report. This downloads a local report; it does not send email.

The date selector covers 30 fictional days. Each day is generated deterministically on demand. Scenario patterns repeat with seeded quantity variation; this is not a calibrated statistical month or a trend-analysis tool yet.

To materialize and validate all XML days:

```powershell
.\.venv\Scripts\python -m investigator.archive
```

This writes 30 days / 90 shifts / 5,400 setups / 2,700 wet-end runs under ignored artifacts/month, with a file/hash/seed manifest. The archive matches the clean dataset in the day selector. The optional corrupt-record scenario is not included in the clean archive.

The offline planner is deliberately limited. Unsupported questions return a clear error. A real model adapter will replace the router, while retaining the tested tool boundary. See AGENT_CONTRACT.md for permissions and limits.


## Optional OpenAI mode

Select OpenAI in the mode control to use your own API account. See [OpenAI/BYOK setup and limits](OPENAI_BYOK.md). Real model access requires your key and incurs API charges; default offline use remains free.


## Calendar, matrices and reports

Use the calendar for September 1-30, 2026. Selected shift only is the default: both the offline parser and the model tool dispatcher enforce it. Choose All shifts / comparison explicitly to compare shifts. Changing date, period, shift or scope resets the visible conversation. Question wording cannot override selected-shift scope.

The Setup matrix lists setup ID, wet-end ID, grade, timestamps, width, footage, speed/target, stop count and allocated downtime. Stops use half-open intervals: overlap seconds = max(0, min(stop end, setup end) - max(stop start, setup start)). A stop crossing a setup boundary counts in both intersected setups, but its minutes are divided, not duplicated. Invalid setups are excluded with incomplete coverage.

Paper changes equal wet-end runs (owner confirmed). Sixty dry-end setups and thirty paper changes per shift remain the initial generator structure. Shift speed attainment is footage / sum(grade target x elapsed minutes) x 100; it is not an unweighted average of percentages. Hover or keyboard-focus a speed-chart value for grade, target and attainment. The percentage is also visible without hovering.

Rank losses shows the ten lowest wet-end speed attainment values and the largest downtime and quality categories. Ties share a rank. Incomplete shifts are withheld from rankings. Period shift rankings use separate speed, maintenance and waste ranks, with 1 = best; there is no fabricated combined score.

Read notes shows each shift note and individual downtime record, including upper/lower knife location. Notes are synthetic reported observations, never proof of causes or executable instructions. Reject sheet counts and reason frequencies vary reproducibly; each record still reconciles sheets to square feet. The upper-knife scenario increases recorded stop duration in successive weeks while retaining the same symptom.

A week is seven production days ending on the selected date; the previous seven days are the comparison. A month is the full selected calendar month. The exact windows are displayed. The demo has no August records: unavailable previous periods show Unavailable, never zero. Missing current days produce partial results and no ranking. Week/month views use free deterministic aggregation, not a live model; free-text input is disabled in these views. Daily shift matrices, recurring stops and notes remain inspectable.

Download PDF exports the last retained investigation without another model call. The server requires its session and run reference plus the same origin/token controls. A stale or foreign run reference is rejected. PDFs are summaries: any shortened evidence tables explicitly state how many rows are shown. Save text remains available. Email is deliberately deferred by the owner until a provider/sender is selected; there is no send-success simulation.

Open How to build this agent for the in-app guide to process definitions, data generation, tools, context, the loop, chart evidence and cost controls. See AGENT_EXECUTION.md for the original step-by-step query walkthrough.
