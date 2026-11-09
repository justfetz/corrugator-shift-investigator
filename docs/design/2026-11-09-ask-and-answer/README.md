# Ask and answer: the accepted layout (2026-11-09)

The picture that issues **#40 → #20 → #21 → #31 → #23** are built to, under epic **#44**. The owner reviewed it on 2026-11-09 ("looks good"). `desktop.html` (1440×1040) and `phone.html` (390×844) are static HTML exported from the design canvas; `desktop.png` and `phone.png` are their renders. Open the HTML in a browser to measure spacing or copy styles; nothing in it is wired to the server.

Every number on the mockup is real: production day 2026-09-14, shift 2, from the current synthetic store (`get_shift_kpis`, `get_downtime_breakdown`, `get_quality_breakdown`), priced at the rates in the panel. The dollars are illustrative of the cost model in #31, not a claim about the synthetic plant.

## What the picture says

**Three columns of attention.** A left rail that states what you are looking at and offers the canned questions; an ask bar at the top; one answer card per question, newest on top. Nothing is below the fold on a 1040-px desktop: the queries, the money, the two charts, the KPI tiles and the follow-ups all fit without scrolling.

**The rail (left).**
- *Looking at*: production day, shift (1 · 7–3, 2 · 3–11, 3 · 11–7, All), window (Day, Week, Month, 4-4-5). The controls are the URL (#20); changing one re-asks the current question.
- *Ask in one click*: eight canned questions. Each is a fixed, parameterized query, so it answers in tens of milliseconds without a model: Why was shift 2 slow? · Where did the money go? · Did we miss splices? · Which setups were tough? · Any trouble on the discharge? · Waste by reason · This week against last. The caption says so: "Every canned question is a fixed query. No model, no wait."
- *Your rates*: paper $55 / MSF, machine time $1,200 / hr, board margin *not set*. The blog's opportunities panel, carried over (#31). "Change rates" edits them; every dollar on the page recomputes client-side, and the rates ride in the permalink.

**The ask bar (top).** One input with the placeholder naming what can be asked. Two buttons: **Ask** (deterministic, the default) and **Ask the analyst** (the model, opt-in, #23). The analyst is never on the hot path.

**The answer card, top to bottom (#40 fixes the order).**
1. Title line: the question, then `Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · 93 ms`.
2. **Query box first.** Each tool call on one line — `get_downtime_breakdown (shift=2, by=reason) · 4 rows · 2 ms` — with its SQL expanded and the bound values shown as a comment. "3 more queries" folds the rest; "Copy all SQL" copies them; the caption says "Fixed, parameterized queries. Visitors cannot edit them." This is Datasette's SQL-linked-to-its-URL idea, above the summary, not folded at the bottom.
3. **The dollar line.** `≈ $6,900 recorded losses this shift`, then chips per bucket (Trim $4,387 · Dry-end rejects $1,514 · Shear $698 · Downtime $299) and one sentence on how it was priced. Downtime is priced by lost hours until a board margin is set, and the caption says that the hour price is the bottleneck's shadow price, not a replacement cost.
4. **Two charts, dollars on the bars.** *Where the money went* (bars in dollars, the area or minutes beside each) and *Downtime by reason* (bars in minutes with the dollar figure first). Each chart has a one-line reading under it: "Trim is 3.33% against a 3.25% target: 0.08 pt over ≈ $79k a year on this machine at these volumes." Every bar links to its next question (#21).
5. **KPI tiles**: Speed 616 ft/min (89% of target) · Downtime 14.9 min (3.1% · $299) · Trim 3.33% (+0.08 pt · $4,387) · Dry-end 1.15% (above 1% · $1,514) · Setups 44 (16 paper changes). Minutes, percent and dollars together on each tile; the rail's unit toggle (#31) picks which leads.
6. **Follow-up chips** generated from the answer: Show the order changes · Which setups ran over trim? · Compare with shift 1 · Ask the analyst about this.
7. **Footer**: the permalink (`/corr/1/day/2026-09-14/shift/2?q=why-slow`, #20), "What came back, per query" (the folded trace, #33), Markdown and PDF exports.

**Earlier cards** collapse to one line (`Show the downtime breakdown · Shift 2 · 52 ms · $299 downtime · Open`) so the thread stays scannable.

**Phone (390 px).** Same card, one column, the rail behind a button, chart first, query box collapsed to a line.

## Decisions recorded on the issues

- **The reveal feels like an assistant answering** (#44 comment, owner's call): skeleton card within 16 ms, query lines tick in about 80 ms apart, the summary types in, charts draw after. On the offline path everything is already known, so the pacing is presentation only and must never slow the first number past about 400 ms or invent text that no tool returned. The owner overruled the engineering preference for an instant pop: this is a superintendent being talked to by his data for the first time.
- **Money is first-class, not a chat trick** (#31 comment): unit toggle minutes / % / $ in the rail, a rates panel mirroring the blog, instant client-side recompute, rates in the permalink, an "opportunities" canned question, a table flip on every chart.
- **Light theme by default** (#36); the toggle stays (top right).
- **Offline is the default planner** (#43, merged); the model is a choice on the card.

## Where the parts land

| Part of the picture | Issue |
| --- | --- |
| Query box at the top of the card; trace folded below | #40 |
| Permalink, URL as state, rail controls re-ask | #20 |
| Bars, tiles and rows link to their next question; follow-up chips | #21 |
| Dollar line, rates panel, unit toggle, dollars on bars | #31 |
| Ask the analyst: one planning call, streams under the deterministic answer | #23 |
| Thread of cards, skeleton, paced reveal, Markdown export | #44 |
| Evals badge in the header → `/evals` | #26 |
