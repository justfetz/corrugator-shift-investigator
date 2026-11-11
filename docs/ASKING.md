# Asking questions

A question is read against the scope in the left rail, answered by the offline planner in tens of milliseconds, and given an address. This page lists what it understands, what it refuses and how to read what comes back. [The workbench, step by step](WORKBENCH.md) walks the page itself; [the URL grammar](URLS.md) is the full reference for addresses.

## The scope comes first

The rail's three controls are part of every question:

| Control | Values | What it means |
| --- | --- | --- |
| Production day | Monday to Friday, 3 August to 30 September 2026 | the anchor; a day runs 07:00 to 07:00 and is labeled by its starting date |
| Shift | 1 (07–15), 2 (15–23), 3 (23–07), All | one 480-minute shift, or All for the whole production day and comparisons between shifts |
| Window | Day, Week, Month, 4-4-5 | the window a question that names none is asked over: the day, its Monday-to-Friday week, its calendar month, or its 4-4-5 accounting period |

The scope is authoritative. With one shift selected, "Compare that with first shift" is refused ("Selected shift only") rather than quietly querying another shift: choose **All** first. A window the question names still wins, so "this week" under Day asks about the week.

## Phrasings the offline planner answers

Every row below was resolved through the offline router on Monday 14 September, shift 2, Window Day; the address column is where it lands (the path `/corr/1/day/2026-09-14/shift/2` is left out).

| Ask | What runs | Address query |
| --- | --- | --- |
| "Why was this shift slow? Chart downtime by reason." | KPIs, downtime by reason, wet-end speed against grade targets, notes | `?measure=overview` |
| "Summarize performance, downtime and waste. Show supporting charts." | the shift overview with three charts | `?measure=summary` |
| "Show speed to target" | KPIs and each wet-end run against its grade target | `?measure=fpm` |
| "fpm by grade", "Average speed this week" | speed by grade against target; the week's speed | `?measure=fpm&by=grade`, `?measure=fpm&window=week` |
| "What were the top downtime reasons yesterday?", "Show downtime by reason" (all shifts) | maintenance and operator downtime against their bands, reasons ranked by % of scheduled minutes with stops and minutes per stop, planned maintenance last, and each flagged shift with its supervisor slot and the leading reason's recurrence; yesterday is the production day before the selected one | `?measure=review` |
| "Which equipment caused the most downtime?" | downtime grouped by place | `?measure=downtime&by=place` |
| "Which downtime reason cost the most minutes this week?" | the downtime Pareto with share and the previous window | `?measure=pareto&window=week` |
| "Where did we stop most often this week?" | the Pareto by place, counted | `?measure=pareto&window=week&by=place&unit=count` |
| "Show dry-end quality waste" | dry-end rejects by reason | `?measure=quality` |
| "Is it bond or warp?", "Which warp are we seeing this month, up warp or twist warp?" | the waste breakdown with reported sub-reasons | `?measure=waste`, `?measure=waste&window=month` |
| "How did this week go versus the previous one?" | the KPI tiles against the previous window | `?measure=kpis&window=week` |
| "How did September go versus August?" | the same tiles for two named months | `?measure=kpis&window=2026-09&compare=2026-08` |
| "Stops per day this week" | stops per production day and the weekday profile | `?measure=stops&window=week` |
| "Trend of trim over eight weeks", "Trend of speed over eight weeks" | a weekly line for one metric | `?measure=trend&metric=trim_pct`, `?measure=trend&metric=speed_all_fpm` |
| "Volume by grade this month" | totals by board grade with speed and allocated downtime | `?measure=grade&window=month` |
| "How much paper did we consume this month?" | rollstock consumed per grade and web width | `?measure=rollstock&window=month` |
| "Over/under-run by knife this week" | nominal against actual cuts per knife | `?measure=overrun&window=week` |
| "What were the top 3 contributors to speed variations?", "What drove speed this week?" | the speed gap against target split into stops, setups, below-target running and grade mix | `?measure=speedvar`, `?measure=speedvar&window=week` |
| "Where is the speed opportunity this month?" | typical against best-fifth speed by grade and width | `?measure=opportunity&window=month` |
| "Where did the money go this shift?" | the shift's losses priced and ranked by dollars | `?measure=dollars` |
| "Where is the money this month?" | the levers ranked in dollars a year, each controllable or schedule-driven | `?measure=money&window=month` |
| "Where's my biggest opportunity?", "What went wrong yesterday?" | the supervisor lens: the controllable losses ranked, side trim and planned maintenance apart | `?measure=money&lens=supervisor` |
| "What does trim cost?", "What's trim costing us this month?" | the ownership lens: the total cost of waste, side trim first, with its band | `?measure=money&lens=ownership`, `?measure=money&window=month&lens=ownership` |
| "Show setup matrix", "Show orders and quantities", "Show shift and downtime notes", "Show rankings" | the per-shift matrices, notes and rankings (Day only) | `?measure=setups`, `orders`, `notes`, `rank` |

With All selected, "Compare all shifts" runs KPIs, downtime and speed for each shift. The example chips under *Ask in one click* are these questions; under Week, Month or 4-4-5 they say "this week", "this month" or "this period".

## What is refused, and why

A refusal says what to ask instead; it never guesses.

- **No measurable goal.** "How can we be better?", "Adjust the knife settings to cut trim" and "How much will we save if we fix the splicer?" are refused: the records hold no intervention outcomes, no machine state and no costs. Choose an objective the records can answer: fewer downtime minutes, less recorded waste, or better grade-target attainment in the selected scope.
- **Relative days.** "What happened yesterday?" asks you to use the day control: answers anchor on the selected production day.
- **An ambiguous window.** A bare "Compare last week" asks which two windows to compare, for example "this week compared with last week".
- **A per-shift view over a period.** The setup matrix, orders, notes and rankings are per shift and per day; under Week, Month or 4-4-5 they ask you to switch Window back to Day.
- **A window outside the data.** A weekend, or any window before 3 August 2026, is unavailable with its reason, never zero. A comparison is withheld unless both windows are complete.

## Reading an answer

Read an answer as **recorded finding → question to investigate → something a person verifies**. A large knife-jam total says where to look with the crew; it does not prove a worn part or justify changing machine settings. Notes describe reported observations. A target gap is not guaranteed recoverable output, and this dataset cannot establish savings, profitability or the best plant-wide improvement.

Every figure names the query and row it came from: the `(query N · row M)` link beside a finding opens the query box at that step, with its SQL and bound values, and "What came back, per query" at the foot of the card holds each step's full result. "Complete" means the investigation finished, not that it proved a cause. If an answer misses your intent, narrow the question and read the queries it ran.

## Every answer is an address

An offline answer's address goes into the address bar as it lands: the path is the scope (`/corr/<machine>/<grain>/<day>[/shift/<n>]`) and the query is the question (`measure=` and its modifiers). Back and forward reopen earlier cards, a bookmark or a pasted link reproduces the answer in any browser, and adding `.json` to the path or `format=pdf` to the query gives the same answer as data or as a PDF. A typed question is a shortcut: `GET /q?day=…&shift=…&q=…` redirects to the one canonical address for it. [The URL grammar](URLS.md) has every measure, modifier and alias.

## Asking the analyst

**Ask the analyst** on an answer, or beside the ask bar, sends the question to a model once: it plans the queries and writes an interpretation whose every number is filled in from a query row. It appears only when the site has a model configured, it takes seconds instead of milliseconds, and its answer has no address, since a model's choices are not reproducible from a URL. [The analyst](ANALYST.md) says what it may and may not do.
