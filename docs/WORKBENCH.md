# Use the local learning interface

Install and run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m investigator.server
```

Open http://127.0.0.1:8765/ on the same computer. The server is local only. No API key, model call or email service is involved.

Use Python 3.11 or newer. Run the installation commands once; on subsequent visits run only the server command in the project virtual environment. Stop it with Ctrl+C in that terminal. Python changes require a restart; frontend changes require a page reload. To use another port, append `--port 8766` and open `http://127.0.0.1:8766/`. Use the numeric loopback host, not localhost.

The page is the layout accepted in [`docs/design/2026-11-09-ask-and-answer/`](design/2026-11-09-ask-and-answer/README.md) (issue #63): a left rail, an ask bar, and one answer card per question.

- **The rail.** *Looking at* holds the whole selection: a **Production day** date with ‹ › steppers (weekends are skipped), **Shift** as buttons (1 · 7–3 / 2 · 3–11 / 3 · 11–7 / All — "All" is the 24-hour scope) and **Window** as buttons (Day / Week / Month / 4-4-5). A plain-English scope sentence under them reads the selection back, for example "Monday 14 Sep 2026 · shift 2 · 15:00–23:00 · compared with Friday 11 Sep", and says when a comparison is withheld because nothing earlier exists in the store. *Ask in one click* lists the canned questions (seven, the rest under **More questions**; the per-shift matrices are hidden under Week, Month and 4-4-5, where the **Period report** button appears). A canned question always runs the offline planner: a fixed query, no model, no wait. *Your rates* is a static placeholder until the cost model (issue #31). **Agent and advanced** is a disclosure holding the agent select (Offline / free by default, OpenAI / your key, Anthropic / your key), the key field, the "Include a bad trim record" toggle and **Clear the thread**.
- **The ask bar.** One input and **Ask**, which always runs the offline planner, whatever the agent select says. **Ask the analyst** is the only live path: it appears when the server has its own key for a live mode (or when you chose a provider and typed your key, locally), and sends the typed question to that model once. Clicking it twice while the question is out is the same card, not a second paid round.
- **The answer card.** A title line — the question, then `Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · 39 ms`, where the milliseconds are the measured wall time from the submit to the rendered card (the tooltip adds the server's own time in the queries), and the shift and window are the ones the first windowed query actually ran over: "How did September go versus August?" asked with the rail on Mon 14 Sep reads `All shifts · September 2026 (2026-09-01 to 2026-09-30)` — then the query box, the summary, the charts side by side, the KPI tiles, and a footer with the exports (**Markdown**, **PDF**) and "What came back, per query". A skeleton card appears in the same instant as the submit and the answer fills it in place; nothing on the page is disabled while a question is out. Questions asked in quick succession each get their card at once and are sent to the server one at a time, in order, so every follow-up carries the conversation the one before it left. At 1440×1040 the open card fits without scrolling. Earlier answers collapse to one line each below the open card, newest on top, with an **Open** button. The thread lives in the page's memory only (permalinks are issue #20).
- **Phone.** One column with the card first; the rail opens as a sheet from the **Questions** button.

The page is light by default, whatever the operating system prefers (issue #36); the **Dark** / **Light** button in the header pins a theme in this browser's localStorage, and that choice wins on later visits.

1. Keep the defaults (Day, shift 2) and ask "Why was second shift slow? Chart downtime by reason."
2. Read the charts and the tiles. A day question shows the shift's KPI row on seven tiles (speed with percent of target, maintenance, operator, dry-end waste, trim with shear, setups with paper changes, throughput with lineal feet per setup). Before any question, and for period questions, the strip shows the dashboard tiles for the selected window with the previous window beside; each tile's delta chip is colored by the tool's own `change` label (better / worse / same / neutral — the direction comes from the tool's `better` map, not from the sign) and reads "withheld" when the comparison is incomplete or the previous window is unavailable. Twelve tiles are visible by default; the rest sit behind "more". Grade target markers on the speed chart use each wet-end run's grade.
3. Read the query box directly under the title line (issue #40): one line per tool call, in order — `tool(arguments) · rows · ms` — with the exact parameterized SQL and its bound values and a **Copy SQL** button. The first step is open; tapping another opens it instead, and at desktop width the open step's SQL sits in a pane beside the list. Steps past the fourth (past the first on a phone) fold behind **N more queries**. It is the first thing in an answer, the way Datasette shows the SQL above the results, and it is read-only by design: every query is a parameterized tool ([agent contract](AGENT_CONTRACT.md)), so visitors cannot edit and re-run it here; copying it is how you re-run it yourself. Each box links to "What came back", the full result of that step (window, coverage, excluded records and the result JSON) under the card's footer, and every chart and table links back to the query that produced it. "How these tiles were produced" under the KPI strip shows the same for the tiles. All of it is execution evidence, not model reasoning.
4. Choose **All** under Shift, then ask "Compare that with first shift". With a single shift selected that comparison is deliberately refused ("Selected shift only"). Changing the scope keeps the cards already on the page (each carries its own scope in its title line) and starts a new server conversation; follow-ups retain history while the controls stay unchanged.
5. Open **Advanced**, enable "Include a bad trim record" and repeat. The affected metrics read Incomplete, the rejected source value shows in the trail, and rankings are withheld for that shift.
6. Save a text report or download the PDF. Both are local files; nothing is emailed.

The date selector covers the record store's 43 production days: Monday to Friday from 3 August to 30 September 2026, generated once from one seed when the server starts (about four seconds) as the record families described in [RECORDS](RECORDS.md). Weekends are plant-down; selecting one is refused with the reason. Two months of one machine with three seeded effects to find; this is a demonstration dataset, not a calibrated statistical sample.

To materialize and validate all XML days:

```powershell
.\.venv\Scripts\python -m investigator.archive
```

This writes the legacy one-day XML fixture for 30 days / 90 shifts under ignored artifacts/month, with a file/hash/seed manifest. It is the older generator kept for its own tests; it is not the dataset in the day selector, which comes from the two-month record store (write those files with `synth.write_jsonl`, see RECORDS.md).

The offline planner is deliberately limited. Unsupported questions return a clear error. The optional OpenAI adapter selects tools alongside the offline router. Application code formats the calculated summaries in either mode; daily OpenAI mode adds a separate model interpretation. See AGENT_CONTRACT.md for permissions and limits.

### Ask about a shift, week or month versus the previous one

These plain phrasings run the period tools over the whole two months (offline, no model call) and chart the result. With Window set to **Week**, **Month** or **4-4-5**, a question that names no window gets the selected one ("Why was this shift slow?" becomes the week's KPIs, downtime Pareto and waste breakdown for the selected shift; "Show speed to target" the KPIs and totals by grade), the example chips say "this week", "this month" or "this period", and a window the question names still wins. The setup matrix, orders, notes and rankings are per shift and per day, so under a period they ask you to switch back to Day. With Period on **Day** the same phrasings work by naming their window:

- "Compare this month to last" or "How did this week go versus the previous one?" — the dashboard tiles (volume MSF, lineal feet, speed with and without stops, downtime, stops, minutes per stop, trim, shear, dry-end with its bond/warp/misalignment split, bale, unexplained, maintenance + PM, operator, setups, paper changes) with the previous window, the delta and whether lower or higher is better.
- "How did September go versus August?" — the same table for two named months.
- "How did shift 2 go compared with last week?" — a named shift filters the window; with a single shift selected in the Shift segment, that shift is used.
- "Which downtime reason cost the most minutes this month?" — a Pareto with share and cumulative share and last month's minutes beside; "where did we stop most often this week" groups by place and counts.
- "Is it bond or warp?" — the waste breakdown (day window unless you name a week or month).
- "Stops per day this week" — per production day plus the Monday-to-Friday profile.
- "Trend of trim over eight weeks" — a weekly line for one metric (trim, shear, dry-end, warp, bond, misalignment, moisture, speed, downtime, stops, minutes per stop, MSF per day, lineal per setup, lineal per paper change).
- "Compare this period to the previous period" — the same tiles over 4-4-5 accounting periods; any comparison with unequal production days says so and points at MSF per day and the ratio tiles.
- "Volume by grade this month", "How much 275-BC did we run this week?", "Which grade is eating the most downtime?" — totals by board grade with speed against target and allocated downtime.
- "Over/under-run by knife this week", "Are we inside the cut range?" — nominal versus actual cuts per knife, drawn as a bullet chart against the ±2 % band.
- "Where is the speed opportunity this month?", "Which crews run faster, and does it cost rejects?" — typical versus best-fifth speed by grade and width or crew, in hours, with the dry-end rate of the fast setups beside it.
- "Which warp are we seeing this month, up warp or twist warp?" — the waste breakdown with the reported sub-reasons.

The window is anchored on the selected day: a week is its Monday–Friday production week, a month its calendar month. Windows before 3 August 2026 are unavailable, never zero, and deltas are withheld unless both windows are complete. "Yesterday" and "today" still ask you to use the day control, and a bare "Compare last week" is refused as ambiguous. Every percentage is a ratio of sums over the window (METRICS.md).


## Optional OpenAI mode

### Does the key summarize for me?

Yes. Daily OpenAI mode now selects evidence, then requests a short narrative with findings, hypotheses, checks and limitations. It appears above the calculated evidence. Numeric values are inserted from validated field references; citations identify the shift, result, row and field. The writer uses up to twelve preview rows per result and must disclose missing evidence. Period reports never call the model.

Use Summary + charts for an overview of speed, downtime and waste. With Shift on **All**, the scope covers all three shifts; an explicitly named shift can narrow a daily question. The overview supplies three supporting charts per shift. Offline mode provides the same calculated overview for free; OpenAI adds a model-written interpretation.

Narrative validation rejects unknown references, unavailable values, literal numeric claims, malformed output and performance findings from incomplete evidence. It cannot prove that prose is relevant, causal or correct; number words and qualitative claims still need human review. Hypotheses are unverified. Budget exhaustion, a failed provider call or invalid narrative preserves the calculated report and charts with an explicit notice. The extra call shares the existing ten-call/time/spending limits. Live quality and endpoint compatibility still need a deliberate evaluation with a real key.

Select OpenAI in the mode control to use your own API account. See [OpenAI/BYOK setup and limits](OPENAI_BYOK.md). Real model access requires your key and incurs API charges; default offline use remains free.

For “How can we be better?”, first choose an objective: fewer downtime minutes, less recorded waste, or better grade-target attainment within the selected day and scope. Ask for the relevant breakdown and review the events with the crew. Adding a key supplies no missing plant knowledge, proven root cause, or savings estimate. Findings are investigation leads; target gaps are not guaranteed recoverable output. Read the README's “Ask a question the records can answer” section and `/guide#asking-questions` before interpreting broad improvement requests. Model tool selection remains fallible and awaits live evaluation.


## Calendar, matrices and reports

### Inspect an order

Choose Window **Day**, then the Orders example. Each row is an order fragment on a knife. Width and length are sheet dimensions in inches; outs multiply cuts to get sheets. Requested and planned totals repeat when an order continues. Compare remaining before/after to see what each fragment produced, and planned overrun to see cutoff rounding. The same order keeps its dimensions and grade across shift boundaries. Completed gross production includes rejects; it is not accepted delivery. See METRICS.md for the simulation choices and time/area audits.

Use the production day and the Window buttons for the production days 3 August – 30 September 2026 (Monday to Friday). A single shift is the default: both the offline parser and the model tool dispatcher enforce it. Choose **All shifts** explicitly to compare shifts. Changing day, period, shift or mode resets the visible conversation. Question wording cannot override the selected shift, and under Week, Month or 4-4-5 a daily tool is refused whatever the planner proposes.

The Setup matrix lists setup ID, wet-end ID, grade, timestamps, width, footage, speed/target, stop count and allocated downtime. Stops use half-open intervals: overlap seconds = max(0, min(stop end, setup end) - max(stop start, setup start)). A stop crossing a setup boundary counts in both intersected setups, but its minutes are divided, not duplicated. Invalid setups are excluded with incomplete coverage.

Paper changes equal wet-end runs (owner confirmed). The record generator lays out wet ends of one to four setups (every fifth a single order across the web) with sheet widths of 12–60 inches and cutoffs of 20–92 inches, one order per knife position; a continuing order keeps its number across setups and across a shift boundary. The exact paper-change total therefore varies by shift instead of always equaling half the setup count. The two months hold 7,968 setup records over 43 production days (about 185 per day, 150–190 by design). Counts include split records when a setup crosses a shift boundary (`change_code S`). This distribution is synthetic and editable; the records carry no order quantity ledger, so requested and remaining sheets read Unavailable in the order matrix. Shift speed attainment is footage / sum(grade target x elapsed minutes) x 100; it is not an unweighted average of percentages. Hover or keyboard-focus a speed-chart value for grade, target and attainment. The percentage is also visible without hovering.

Rank losses shows the ten lowest wet-end speed attainment values and the largest downtime and quality categories. Ties share a rank. Incomplete shifts are withheld from rankings. Period shift rankings use separate speed, maintenance and waste ranks, with 1 = best; there is no fabricated combined score.

Read notes shows the crew label for the shift (the record families carry no free-text shift note) and each individual stop record with its place and reason. Notes are synthetic reported observations, never proof of causes or executable instructions. Reject lines are per knife position and reason (bond, warp, misalignment); each still reconciles sheets to square feet. Three seeded effects are planted in the two months (RECORDS.md names them); the answer keys are read by the generator only, so an investigation has to find them in the records, for example with the weekly trend, the Pareto and the stops profile.

A week is the Monday–Friday production week containing the selected date; the preceding Monday–Friday week is the comparison. Calendar month follows ordinary month boundaries, so September compares with August. The 4-4-5 view uses a repeating 4-week, 4-week, 5-week pattern; the demo accounting year starts September 1, 2026 pending owner confirmation. The exact windows are displayed. The dataset starts on 3 August 2026: windows reaching before it (July, the week of 27 July, the first 4-4-5 period's predecessor) show Unavailable, never zero. Weekend cells are plant-down and hatched; a missing weekday produces partial results and no ranking. The **Period report** button (shown under Week, Month and 4-4-5) uses free deterministic aggregation, not a live model; the question box beside it stays live and is answered by the period tools, offline or with a model. The report's daily shift matrices, recurring stops and notes remain inspectable.

The period heatmap shades production days by estimated good square feet: gross area minus recorded trim, shear and dry-end rejects. It is an estimate, not a customer-acceptance claim. Hover or focus a cell to see gross and estimated-good output, speed attainment, total/maintenance/operator downtime, waste KPIs, setup and paper-change counts, plus that day's largest recorded downtime contributor. Missing days are hatched and excluded from the color scale.

The card's **PDF** button exports the last retained investigation without another model call (it is offered on the card of the session's latest answer only; **Markdown** is on every card). The server requires its session and run reference plus the same origin/token controls. A stale or foreign run reference is rejected. Every chart the investigation produced is drawn in the PDF as a native vector drawing from the same tool rows the browser rendered (`pdf_charts.py`: horizontal bars with the previous window as a thin bar, the weekly trend line with hollow markers for partial weeks, the over/under-run bullet with its shaded band; the calendar heatmap stays a table), followed by its data table. When a speed-opportunity result is in the investigation, an **Opportunities (from evidence)** section lists the largest gaps with the reject penalty beside each, worded as what the evidence shows, never as a prediction. PDFs are summaries: any shortened evidence tables explicitly state how many rows are shown. Save text remains available.

**Email me this report** is designed but not built. It will exist only behind the invited login, never on the public demo: a page that takes a typed address and sends mail is a relay waiting to be abused, and "send the report to <address>" stays a refusal golden. Behind Cloudflare Access the app already knows the verified email of the person logged in, so the button sends the PDF to that address only, through a transactional mail provider whose key lives in Secret Manager, rate-limited per user, with one action-audit row per send. Until then there is no send-success simulation.

Period PDF export passes regression checks for week, month and accounting views. Charts are vector drawings built from the evidence rows, never images of the screen; a test builds the PDF for every chart kind (bar, paired bar, line, range) and the opportunities section.

### Try the calendar step by step

1. Select September 14, 2026, shift 2, Window **Month**, then **Period report**.
2. Read the scope sentence and the KPI tile strip (September against August; delta chips colored by `change`). This report has 22 shift records (one per production day); **All** has 66. The August comparison is real data.
3. Hover, Tab to, or click a day. Its detail box gives the actual quantities; a dark day is high output within this report, not necessarily high efficiency.
4. Choose Window **4-4-5**. September 14 belongs to September 1–28. September 29 belongs to September 29–October 26 and is partial because only two days exist; its tiles read "withheld".
5. To investigate a heatmap date, switch Period back to **Day** and set the date. Clicking a cell only updates its details.

For formulas and why colors cannot be compared directly across reports, read [METRICS](METRICS.md). For status messages and recovery, read [TROUBLESHOOTING](TROUBLESHOOTING.md). For a repeatable browser checklist and live-model question rubric, read [TESTING](TESTING.md).

Open How to build this agent, then follow its complete `/guide` link for user-facing process definitions, data generation, tools, context, the loop, calendar math, chart evidence and safety controls. See AGENT_EXECUTION.md for the original technical query walkthrough and DEPLOYMENT.md for the public-release gate.
