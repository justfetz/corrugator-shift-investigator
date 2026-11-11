# Shift Investigator documentation

The Shift Investigator answers a superintendent's question about a corrugator, such as "why was second shift slow?" or "which downtime reason cost the most minutes this month?", from synthetic production records. Fixed, parameterized queries compute every number. A language model, when you ask for one, may choose which of those queries to run and write a short interpretation around their results; it never supplies a number.

The project is a worked example of building an agent: start from a manufacturing expert's process rules, generate records that obey them, give the agent a small set of read-only calculation tools, and keep a golden set of cases that says whether the answers are right. [The case study](CASE_STUDY.md) tells the whole story on one page: the problem, the design, how the numbers are checked, the results and the trade-offs.

## Try it

1. **Set the scope** in the left rail: a production day in the rolling year the page footer names, every weekday up to the day before the site was last deployed (the ‹ › steppers skip weekends), a shift (1, 2, 3, or All for the whole production day) and a window (Day, Week, Month or 4-4-5). The sentence under the controls reads the selection back, including what the answer will be compared with.
2. **Ask.** Pick a question under *Ask in one click*, or type one into the ask bar at the bottom and press Enter. Every question is answered by the offline planner first: fixed queries, no model, no charge, in tens of milliseconds. [Asking questions](ASKING.md) lists the phrasings it understands.
3. **Read the card.** The headline figure and what it is held against, findings whose bold figures each link to the query and row they came from, the charts, *Where to look*, the one-line query box (**Show SQL** opens the exact SQL with its bound values), and the KPI tiles against the previous window.
4. **Keep the address.** Every offline answer is a URL: paste it anywhere and the same answer comes back ([the URL grammar](URLS.md)).
5. **Ask the analyst**, when the site offers it: the button on an answer sends that question to a model once, which picks the queries and writes an interpretation under the numbers. On the hosted demo that model is Anthropic's, on the site's own key; no visitor key is ever accepted ([the analyst](ANALYST.md)).

All of it is synthetic: one machine, a rolling year that moves forward every weekday night, three planted stories to find. Nothing is connected to a real plant.

## The pipeline in five steps

- **Records from a seed.** One seed produces a rolling year of production (every weekday from a year before the dataset's last day to it, each day from its own date-keyed draws) as four record families shaped like a corrugator's own records: dry-end setups, wet ends, shifts and stops. The same seed gives byte-identical records and one content hash. Three stories are planted; their answer keys are read by the generator only ([data](DATA.md)).
- **Invariants, each with a test.** Gross area equals lineal feet times web width; both knives share the machine's travel; wet-end and shift totals are exact sums of their setups; setups tile every shift with no gap; stops sit inside one setup and never overlap.
- **A store and bounded tools.** The records load once into an in-memory DuckDB. Daily tools and period tools answer fixed, parameterized queries; every percentage is a ratio of sums over the window, never an average of averages, and each tile says which direction is better ([metrics](METRICS.md)).
- **A planner that only chooses.** The offline router maps a question to a fixed plan. The analyst (Anthropic by default) proposes one plan from the same tool list, and application code checks every step before anything runs. Application code owns the math, the scope, the bounds on calls, time and spend, and the trail of every query.
- **A golden set that owns the truth.** Expected numbers are computed a second way, by plain sums over the record families, and stamped with the dataset hash; investigation cases must find the planted stories and may not assert a cause the evidence lacks ([numbers and evals](EVALS.md); every case is browsable at `/evals` in the app).

[How this is built](HOW_THIS_IS_BUILT.md) walks the same pipeline in detail, in ten minutes.

## Rules the process sets

A production day runs from 07:00 to 07:00 and is labeled by its starting date. Shift 1 is 07:00–15:00, shift 2 is 15:00–23:00 and shift 3 is 23:00–07:00, each 480 minutes. A dry-end setup changes when either knife's order combination changes; a paper change is a wet-end run and holds one or more setups. Speed is measured over elapsed time, including downtime. Maintenance and operator downtime are each compared with a 2.5% shift target; trim above 3.25% per setup and dry-end waste above 1% are flagged. Recorded reasons and notes are observations, not proof of a mechanical cause.

## What it will not do

- **Assert a cause.** Stop reasons, reject reasons and notes are recorded observations. An answer can say what coincides with what and what warrants a look with the crew; it does not turn a symptom into a diagnosis or prescribe a machine adjustment.
- **Promise savings.** The records hold no costs. The dollars on an answer are its quantities priced at assumed, named rates ([cost model](COST_MODEL.md)), not the plant's figures; a target gap is not guaranteed recoverable output.
- **Act.** There is no arbitrary SQL, shell, file browser, email, schedule write or machine control. Reports are downloads (Markdown and PDF); nothing is sent.
- **Pretend a window exists.** A window before the year's first day or after its last, or a weekend, is unavailable with its reason, never zero; a comparison is withheld when either window is incomplete.
- **Stand in for a production service.** The hosted demo has bounded guards, not identity, durable rate limits or centralized budgets ([deployment](DEPLOYMENT.md)). The 4-4-5 start date, plant timezone and daylight-saving behavior still need the owner's confirmation before this could represent a real plant.
