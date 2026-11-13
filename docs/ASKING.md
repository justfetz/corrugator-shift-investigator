# Asking questions

A question is read against the scope in the left rail, answered by the offline planner in tens of milliseconds, and given an address. This page lists what it understands, what it refuses and how to read what comes back. [The workbench, step by step](WORKBENCH.md) walks the page itself; [the URL grammar](URLS.md) is the full reference for addresses.

## The scope comes first

The rail's three controls are part of every question:

| Control | Values | What it means |
| --- | --- | --- |
| Production day | Monday to Friday, the window the page footer names (from 1 January 2025 to the day before the site was last deployed) | the anchor; a day runs 07:00 to 07:00 and is labeled by its starting date |
| Shift | 1 (07–15), 2 (15–23), 3 (23–07), All | one 480-minute shift, or All for the whole production day and comparisons between shifts |
| Window | Day, Week, Month, 4-4-5 | the window a question that names none is asked over: the day, its Monday-to-Friday week, its calendar month, or its 4-4-5 accounting period |

A shift or window the question names wins over the controls, and the address says so: with shift 2 selected, "Why was shift 1 slow?" answers shift 1 (`/corr/1/day/2026-09-14/shift/1?measure=overview`), "this week" under Day asks about the week, and "How much downtime on 1st shift last week?" answers the week before for shift 1 (`/corr/1/week/2026-09-07/shift/1?measure=pareto`). A comparison still needs its scope: with one shift selected, "Compare that with first shift" is refused ("Selected shift only") rather than quietly querying another shift, so choose **All** first. [The URL grammar](URLS.md#the-questions-own-scope-issue-105) has the rules. A month named without a year is the latest month of that name on or before the selected day: "How did December go?" asked on 14 September 2026 is December 2025.

## Phrasings the offline planner answers

Every row below was resolved through the offline router on Monday 14 September, shift 2, Window Day; the address column is where it lands (the path `/corr/1/day/2026-09-14/shift/2` is left out).

| Ask | What runs | Address query |
| --- | --- | --- |
| "Why was this shift slow? Chart downtime by reason." | KPIs, downtime by reason, wet-end speed against grade targets, notes | `?measure=overview` |
| "Summarize performance, downtime and waste. Show supporting charts." | the shift overview with three charts | `?measure=summary` |
| "Show speed to target" | KPIs and each wet-end run against its grade target | `?measure=fpm` |
| "fpm by grade", "Average speed this week" | speed by grade against target; the week's speed | `?measure=fpm&by=grade`, `?measure=fpm&window=week` |
| "What were the top downtime reasons yesterday?", "Show downtime by reason" | the owner's default: a downtime question with no shift and no window is the previous production day, all shifts, whatever the shift control says. Maintenance and operator downtime against their bands, reasons ranked by % of scheduled minutes with stops, minutes per stop and the previous production day's share, planned maintenance last, and each flagged shift with its supervisor slot and the leading reason's recurrence. Name a shift ("for this shift") for that shift's breakdown | `/corr/1/day/<previous day>?measure=review` |
| "Which equipment caused the most downtime?" | downtime grouped by place | `?measure=downtime&by=place` |
| "Which downtime reason cost the most minutes this week?" | the downtime Pareto with share and the previous window | `?measure=pareto&window=week` |
| "Where did we stop most often this week?" | the Pareto by place, counted | `?measure=pareto&window=week&by=place&unit=count` |
| "Show dry-end quality waste" | dry-end rejects by reason | `?measure=quality` |
| "Is it bond or warp?", "Which warp are we seeing this month, up warp or twist warp?" | the waste breakdown with reported sub-reasons | `?measure=waste`, `?measure=waste&window=month` |
| "How many setups did we run last week?", "How many order changes this week?", "What was our lineal per setup last week?", "How many sheets did we make this week?" | one quantity (issue #113): the count or unit asked opens the answer, the previous window and its band beside, the tiles it is made of after it and the other KPI tiles folded. "Last week" moves the scope to that week; an order change is a setup an order change started (change code O) | `/corr/1/week/2026-09-07?measure=quantity&metric=setups`, `/corr/1/day/2026-09-14?measure=quantity&window=week&metric=order_changes` |
| "How many stops did we have yesterday?", "How many minutes of maintenance downtime yesterday?", "How many stops did we have?" | a quantity with no shift named is every shift together, over the window it names ("yesterday" is the production day before the selected one) or, with none named, the selected day: the stops (downtime minutes and minutes per stop after), or the maintenance minutes with their share of scheduled minutes and band (each maintenance reason after) | `/corr/1/day/<previous day>?measure=quantity&metric=stops`, `…&metric=maintenance_minutes`, `/corr/1/day/<selected day>?measure=quantity&metric=stops` |
| "What was our MSF per day this week?", "How much did we make per day this week?" | the volume per production day, the like-for-like volume when windows hold different numbers of days | `/corr/1/day/2026-09-14?measure=quantity&window=week&metric=msf_per_day` |
| "Average stop length this week", "How long is our average stop?" | minutes per stop: an average or typical stop is the window's downtime minutes over its stops | `/corr/1/day/2026-09-14?measure=quantity&window=week&metric=minutes_per_stop` |
| "Downtime per day this week", "How many minutes of downtime per day this week?" | the downtime of each production day from the stops profile, the day with the most minutes first, then each weekday's minutes per day (the week when no window is named) | `/corr/1/day/2026-09-14?measure=quantity&window=week&metric=downtime_per_day` |
| "How many missed splices this week?", "How many minutes did we lose to missed splices?", "How many stops at the stacker?" | one stop reason or place, counted or in minutes ("how long did knife jams take"; a changeover's time is its order change stops), with its share of the window and the week before | `/corr/1/day/2026-09-14?measure=quantity&window=week&metric=stops&reason=missed-splice`, `…&metric=stops&place=stacker` |
| "How many square feet of trim this week?", "What percent was shear this week?", "How much waste in MSF this week?" | one waste line's area, or its percent of gross (a dry-end line of the yield after trim and shear), the week before beside; waste in MSF is the bale | `/corr/1/day/2026-09-14?measure=quantity&window=week&metric=waste_sqft&waste=trim`, `…&metric=waste_pct&waste=shear`, `…&metric=bale_msf` |
| "How did this week go versus the previous one?" | the KPI tiles against the previous window | `?measure=kpis&window=week` |
| "How did September go versus August?" | the same tiles for two named months | `?measure=kpis&window=2026-09&compare=2026-08` |
| "Stops per day this week" | stops per production day and the weekday profile; a count per day, so every shift together unless the question names one, as "how many stops per day this week" is | `?measure=stops&window=week` |
| "Trend of trim over eight weeks", "Trend of speed over eight weeks" | a weekly line for one metric | `?measure=trend&metric=trim_pct`, `?measure=trend&metric=speed_all_fpm` |
| "Volume by grade this month" | totals by board grade with speed and allocated downtime | `?measure=grade&window=month` |
| "How much paper did we consume this month?" | rollstock consumed per grade and web width | `?measure=rollstock&window=month` |
| "Over/under-run by knife this week" | nominal against actual cuts per knife | `?measure=overrun&window=week` |
| "What were the top 3 contributors to speed variations?", "What drove speed this week?" | the speed gap against each grade's trailing-year baseline split into stops, setups, running under baseline and grade mix (schedule-driven, not priced), with planned maintenance (schedule-driven) and external stops (a power outage) as buckets of their own | `?measure=speedvar`, `?measure=speedvar&window=week` |
| "Where is the speed opportunity this month?" | typical against best-fifth speed by grade and width | `?measure=opportunity&window=month` |
| "Where did the money go this shift?" | the shift's losses priced and ranked by dollars; planned maintenance and a power outage apart, with the all-in total beside | `?measure=dollars` |
| "Where is the money this month?" | the levers ranked in dollars a year, each controllable or schedule-driven | `?measure=money&window=month` |
| "Where's my biggest opportunity?", "What went wrong yesterday?" | the supervisor lens: the controllable losses ranked, side trim and planned maintenance apart | `?measure=money&lens=supervisor` |
| "What does trim cost?", "What's trim costing us this month?" | the ownership lens: the total cost of waste, side trim first, with its band, then every other waste line priced once (the boards and planning / admin included, so the total is the waste breakdown's); the dry-end waste (tag scrap) as one subtotal with bond, warp, misalignment, moisture and other beneath it, each with its own cause | `?measure=money&lens=ownership`, `?measure=money&window=month&lens=ownership` |
| "Show setup matrix", "Show orders and quantities", "Show shift and downtime notes", "Show rankings" | the per-shift matrices, notes and rankings (Day only) | `?measure=setups`, `orders`, `notes`, `rank` |

With All selected, "Compare all shifts" runs KPIs, downtime and speed for each shift. The example chips under *Ask in one click* are these questions; under Week, Month or 4-4-5 they say "this week", "this month" or "this period".

## What is refused, and why

A refusal says what to ask instead; it never guesses.

- **No measurable goal.** "How can we be better?", "Adjust the knife settings to cut trim" and "How much will we save if we fix the splicer?" are refused: the records hold no intervention outcomes, no machine state and no costs. Choose an objective the records can answer: fewer downtime minutes, less recorded waste, or better grade-target attainment in the selected scope.
- **Today, and a day with nothing to measure.** "Yesterday" is the production day before the selected one ("Why was yesterday slow?" on Monday 14 September answers Friday 11 September), but "How did today go?" asks you to use the day control, and "What happened yesterday?" is refused like any question that names nothing to measure.
- **A count the records do not hold.** "How many rolls did we use?", "How many boards did we lose at the stacker?", "How many step-ups?", "How many splices did we run?", "How many orders?", "How many hours of downtime?", "How many reject sheets?", "What was our minutes per setup?", "Feet per stop this week", "Cost per setup", "Sheets per minute", "Square feet per minute", "How long did setups take?", "Average setup length", "How long was the longest stop?", "Average stops this week", "How long were we slow?" and "How much downtime in hours?" are refused with what can be counted: paper is recorded in lineal feet, MSF and paper runs, rejects in square feet by reason, a missed splice as a stop reason, downtime in minutes. A question that names a count or a unit the router cannot place ("How did we do on glue this week?", "How many widgets on shift 2?") is refused the same way, never answered with the KPI summary or the overview (issue #113). A quantity that names no shift is answered for every shift together ("what percent downtime this week", "number of stops by reason this week" too); say "this shift" or name the shift for one shift's. A quantity asked of two shifts side by side ("how much scrap on shift 1 versus shift 2", "compare shifts on downtime minutes") is refused rather than answered with the shift comparison, which opens with speed, and so is a quantity of a ranked shift ("how many minutes of downtime did the slowest shift have"): the router does not rank shifts while it reads a question. Stops or minutes of each place, reason or kind ("how many minutes of downtime at each place this week", "stops by kind this week") are the Pareto grouped by it, in the unit asked, and a breakdown asked in a unit ("waste breakdown in percent") is the breakdown. An average stop by reason is in the downtime review, beside each reason's minutes and stops.
- **An ambiguous window.** A bare "Compare last week" asks which two windows to compare, for example "this week compared with last week".
- **A per-shift view over a period.** The setup matrix, orders, notes and rankings are per shift and per day; under Week, Month or 4-4-5 they ask you to switch Window back to Day.
- **A window outside the data.** A weekend, or any window before the year's first day or after its last, is unavailable with its reason, never zero. A comparison is withheld unless both windows are complete.

## Reading an answer

Read an answer as **recorded finding → question to investigate → something a person verifies**. A large knife-jam total says where to look with the crew; it does not prove a worn part or justify changing machine settings. Notes describe reported observations. A target gap is not guaranteed recoverable output, and this dataset cannot establish savings, profitability or the best plant-wide improvement.

Every figure names the query and row it came from: the `(query N · row M)` link beside a finding opens the query box at that step, with its SQL and bound values, and "What came back, per query" at the foot of the card holds each step's full result. "Complete" means the investigation finished, not that it proved a cause. If an answer misses your intent, narrow the question and read the queries it ran.

## The money on every card

Every card shows its window's money, priced at the rates in force: a dollar line under the headline (`≈ $7,762 recorded losses this shift · $5.81M/yr at this rate`, a chip per bucket), the levers ranked by dollars a year under the charts (hover a bar for its formula; Table shows the same rows), dollars on each tile that measures a loss, and the arithmetic at the bottom of the card. The rates are assumptions, the owner's post by default and never a plant's: **Your rates** in the rail shows each with its source, **Change rates** edits them and re-prices the whole page in the browser with no request, and the address then carries them as `rates=`, so the link reproduces the same figures. **Show as** switches tiles and bars between minutes, percent and dollars. A contribution margin is not set until you set one: until then no lost contribution is priced, never as $0. [The cost model](COST_MODEL.md#on-the-card-issue-31b) has every formula.

## Every answer is an address

An offline answer's address goes into the address bar as it lands: the path is the scope (`/corr/<machine>/<grain>/<day>[/shift/<n>]`) and the query is the question (`measure=` and its modifiers). Back and forward reopen earlier cards, a bookmark or a pasted link reproduces the answer in any browser, and adding `.json` to the path or `format=pdf` to the query gives the same answer as data or as a PDF. A typed question is a shortcut: `GET /q?day=…&shift=…&q=…` redirects to the one canonical address for it. [The URL grammar](URLS.md) has every measure, modifier and alias.

## Asking the analyst

**Ask the analyst** on an answer, or beside the ask bar, sends the question to a model once: it plans the queries and writes an interpretation whose every number is filled in from a query row. It appears only when the site has a model configured, it takes seconds instead of milliseconds, and its answer has no address, since a model's choices are not reproducible from a URL. [The analyst](ANALYST.md) says what it may and may not do.
