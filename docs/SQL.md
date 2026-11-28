# How a question becomes SQL, and how the SQL is tested

This page follows one question from the words someone types to the SQL that runs and the rows that come back, then shows how that SQL is tested. It is written for a reader who knows SQL but not this codebase.

Every code block, plan, bound value and result row below is produced when the docs are built, by the code the app runs: the offline router, the semantic model's validator and compiler, and the record store, over the synthetic records from {{store.first}} to {{store.last}}. The test blocks are read from the test files themselves. The page's source types none of them, and the build fails when a block cannot be rendered or no longer matches what the code does (`src/investigator/sqlpage.py` renders and checks them).

Some words first. The **semantic model** ([its page](../src/investigator/semantic_model.json)) is one file that names what the plant measures (a **measure**, such as downtime minutes), how it can be sliced (a **dimension**, such as the stop reason) and which table and columns hold each (its `physical` block). A **plan** is a request in the model's terms: one measure, what it is split by, its filters, its window, its order and its limit. An **entity** is a kind of record, held in one table: a stop of the downtime log, a run setup, a reject line. A **golden case** is a question with a written expectation that every test run checks (the [golden set](../evals/README.md)), and a **verified example** is one of the owner's own questions with its plan, checked against SQL written by hand ([below](#verified-examples-a-hand-written-sql-oracle)).

## The worked question

The question {{question}}, asked on the page every golden case is asked on (the page's controls: the production day {{page.day}}, shift {{page.shift}}, Window Day). Its answer has an address, as every answer does ([the URL grammar](URLS.md)): {{address}}.

1. [The question and its reading](#the-question-and-its-reading): the plan the words are read into.
1. [Who makes the reading](#who-makes-the-reading): the offline routers today, a model that writes plans after [#172](https://github.com/justfetz/corrugator-shift-investigator/issues/172).
1. [Validation](#validation-what-is-refused-and-why): every name checked against the model and every value against the records.
1. [The model entries the compiler reads](#the-model-entries-the-compiler-reads): where each piece of the SQL comes from.
1. [The compiled statements](#the-compiled-statements-and-their-bound-values): the SQL and its bound values.
1. [The rows that come back](#the-rows-that-come-back-and-their-receipt): the values and the records behind each.

### The question and its reading

The router reads the words into this plan:

{{plan}}

`measure` is what is summed (downtime minutes), `group_by` the split (one row per stop reason), `filters` the rows kept (the stop kind Maintenance), `window` the production week of the selected day, `order` `value` (largest first) and `limit` the rows listed. `shift` null is every shift and `compare` null puts no earlier window beside it.

The answer prints the reading under its findings, so a reader can see what was asked of the data before trusting a number:

{{read_as}}

The plan is the verified example {{example.id}} (the owner's {{example.question}}) read in this week: the build fails if the router reads the question any other way.

### Who makes the reading

Today every question is read offline, by deterministic code and never a model: first by the hand router, then by the semantic parser.

- **The hand router** (`agent.parse_request`) reads it first, with rules written for the plant's common questions, and answers them through the fixed tools [below](#the-other-source-of-sql-the-fixed-tools).
- **The semantic parser** (`semantic_parse.py`) then reads it into the model's own words: each measure's and dimension's name, label and synonyms, the values the records hold, and a closed table of ranking words ("top", "most", "by"). When it uses every word of the question, and the hand router refused it or answered something else, its plan answers. Here the hand router alone {{hand.reading}}, so the parser's plan stands.

After [#172](https://github.com/justfetz/corrugator-shift-investigator/issues/172) a model reads the questions neither reader can: it writes the same plan, as JSON, and never SQL or a figure. Code validates that plan exactly as below (one retry with the refusal's reason, then a refusal), and the compiler and the store compute every number. The plan is also the address, so a reload replays the answer with no model call.

### Validation: what is refused, and why

`semantic.validate` checks a plan before anything is compiled. It turns each synonym into the model's own name ("maint" is the stop kind Maintenance), checks each filter value against the values the records hold, and refuses with the nearest valid choices: an unknown measure, dimension or value; a split the measure's records cannot reach; a time dimension as a filter (the window sets the time); a comparison or a share the measure cannot take; a limit outside the allowed range. A plan that is refused never reaches SQL.

The worked plan split by {{refused.split}} instead of the reason:

{{refused.plan}}

is refused with:

{{refused.message}}

A stop belongs to one run setup, and a setup carries several order lanes, so a stop's minutes cannot be given to one customer: the model's joins run many-to-one only, from a stop to its setup and its shift, never to an order lane.

### The model entries the compiler reads

The compiler writes SQL only from the model file. The measure:

{{model.measure}}

The dimensions the plan names, the split and the filter:

{{model.dimensions}}

And the `physical` block of the measure's entity, {{model.entity}}: the one place that names the store's table, {{model.table}}, and its columns. Every other entry names logical columns as `{placeholders}`.

{{model.physical}}

Each piece of the plan becomes a piece of SQL. The compiler aliases the entity's table `b` and writes each `{placeholder}` as that table's column:

{{pieces}}

The build checks that each of these pieces is in the statements below. The physical names, the expressions and the functions an expression may use are checked when the model loads, so a name that no table holds never reaches a statement.

### The compiled statements and their bound values

`semantic.compile_query` returns these statements for the validated plan, exactly as below, in order: {{compiled.count_words}}. The first lists the rows, each with its share of the split's total and the number of groups; the second is the total over every row, not only those listed.

{{compiled}}

Each value the plan carries, the window's dates, the filter's value and the limit, is a bound parameter (`?`), never pasted into the SQL text:

- A value typed in a question or a URL cannot change what the statement does; the statement's text depends only on the plan's shape.
- The names in the text, the table, the columns and the expressions, come only from the model file, which a visitor cannot touch.
- The same text runs for any week or any reason, so a check of the text holds for every value bound into it.

`semantic.run` executes the statements on the store under its lock, its time cap and its row cap and, when every row of a sum is listed, refuses the answer if those rows do not add up to the total statement's value. The answer's query box shows the SQL it ran, which the build checks is the statements above, joined:

{{trace.sql}}

with one list of bound values per statement:

{{trace.parameters}}

### The rows that come back, and their receipt

{{rows}}

Each row carries its **receipt**: `source_count`, the number of records the row summed, and `source_ids`, the ids of those records (the first {{receipt.cap}}, sorted; the table above prints the first few). The receipt link opens the records themselves in the data browser, `/data`, with the filter the row read, for example {{receipt.first}} for {{rows.first}}, or {{receipt.url}} for the total. The data browser runs its own fixed, parameterized query, and shows it.

The answer's first finding, as the card prints it:

{{card}}

`(query N · row M)` names the query and the row each bold figure came from; on the card it opens that query's SQL. The answer ran these steps: {{card.tools}}.

## The other source of SQL: the fixed tools

Most answers do not go through the compiler. The hand router answers the plant's common questions with **fixed tools**: Python methods whose SQL is written by hand, in `src/investigator/record_store.py` for a window (a week, a month or an accounting period: the period tools) and in `src/investigator/analysis.py` for one production day and shift (the daily tools). Each tool's name and arguments are a schema ([the agent contract](AGENT_CONTRACT.md)); the router, or the analyst's model when asked, chooses a tool and its arguments, and never writes SQL. Every value is bound; a name that varies, such as the column a Pareto groups by, is chosen from a short list in the code.

How to tell which path answered: the query box names each step's tool. `semantic_query` is the compiler; any other name is a fixed tool. The address says it too: `measure=semantic` is the compiler, any other `measure=` a fixed tool's answer.

The golden case {{fixed.case_id}} asks {{fixed.question}} on the same page, and its address is {{fixed.address}}. The router calls {{fixed.tool}}:

{{fixed.call}}

which runs this statement, written by hand in `record_store.py`; its {{fixed.group_by}} column is the one name the arguments choose, from `reason`, `place` or `kind`:

{{fixed.sql}}

It runs once for each week the answer sets side by side:

{{fixed.parameters}}

and the tool orders the rows, computes each share in Python and puts the week before beside each row:

{{fixed.rows}}

The fixed tools came first and answer most questions with the owner's own shapes (planned maintenance listed apart, the bands, the money). The compiler answers the splits and rankings no tool makes, from the model file instead of new code ([#117](https://github.com/justfetz/corrugator-shift-investigator/issues/117)).

## Where to see an answer's SQL in the app

- **Under every answer**, the query line (`queries · ms · Show SQL`) opens the query box: each step's tool and arguments, its exact SQL with `?` placeholders, the bound values after it, a Copy SQL button, the full result ("What came back") and a link to the rows the step read in `/data`. A compiler step shows its statements joined, with one list of bound values per statement, as above.
- **Beside every bold figure**, `(query N · row M)` opens that query in the box.
- **On an analyst's card**, the queries the analyst's plan ran show in the same box; its narrative has no SQL of its own, and every number in it is filled from a row of those queries.
- **In `/data`**, every table page shows "The query this page ran", with its bound values.
- **On `/evals`**, every case's tool trail has "SQL and bound values" for each step.
- **As JSON**, an address with `.json` (or `format=json`) returns the answer with each step's `trace`: its SQL and its parameters.

What shows no SQL, and what it shows instead:

- **A refused question** runs no query. The card shows the refusal and the questions that would answer.
- **A chart** has no SQL of its own. Its step says it draws the rows of an earlier query, and links to it.
- **The dollars** are priced after the SQL, in Python at the named rates (`cost_model.py`), and re-priced in the browser at a visitor's own rates (`web/money.js`). The query box shows the quantities' SQL, not the pricing; the card's money line names the rates.
- **The headline and findings** are written by application code (`shapes.py`) from the rows; the text itself has no query, and each figure in it cites its row.
- **The PDF and the Markdown report** carry the answer's text, not its SQL.
- **The SQL shown cannot be run as pasted**: its values are listed after it, not in it, so a reader binds them (or reads them in place of each `?`). The page never runs SQL a visitor edits.

## How the SQL is tested

The SQL is tested in layers, each reading the records a different way, so a mistake in one is caught by another.

### Value goldens: the numbers computed a second way

`evals/make_value_goldens.py` computes every expected number in plain Python over the records the generator returns (`synth.generate_window()`), with its own calendar arithmetic. It never imports DuckDB or the tools, so the SQL is checked against a computation that shares none of its code. It writes `evals/golden_values.json`, which is generated and never typed, and `python3 evals/run.py` runs each case through the real tool or compiler and compares.

The case {{value.id}} asks the worked week's maintenance minutes split by board grade, a split no fixed tool makes:

{{value.arguments}}

Its expected values, each with the tolerance of the tool's rounding (an integer is exact):

{{value.expect}}

written by {{value.function_name}}:

{{value.function}}

Its total, {{value.total}}, is the worked answer's total, {{answer.total}}: the same week's maintenance minutes, by another split, by code that shares nothing with the compiler. The build checks that they agree.

### Verified examples: a hand-written SQL oracle

The verified examples (`src/investigator/examples.json`) are the owner's questions to the plant's leadership, each with its definition and its exact plan. `tests/test_examples.py` holds an **oracle** for each example that answers (a refusal example has none): a SQL query written by hand from the definition, over the store's own tables, that never calls the compiler. The test {{oracle.test}} runs the example through `semantic.run` and the oracle side by side, and asserts the same rows in the same order with the same values.

The worked question is the example {{example.id}}:

{{example.plan}}

Its oracle, {{oracle.name}}, from the test file:

{{oracle}}

beside the compiler's [statements above](#the-compiled-statements-and-their-bound-values). The test runs it in the example's own window and each alternate ({{oracle.windows}}), on each of the pages {{oracle.anchors}}, so a definition that holds in one window and not another fails.

### Routing and refusal goldens

`evals/golden.json` holds questions with what must happen: a **routing** case names the address the question must resolve to and the tools that must run, with their arguments; a **refusal** case names the message a question or an address must be refused with. `python3 evals/run.py` asks each one through the router, then again over HTTP at its address (`GET /q` must redirect to the case's address, and the answer there is scored the same way).

The fixed tool's question above is this routing case:

{{fixed.case}}

and an address the validator refuses, with every stop reason the records hold in its message, is this refusal case:

{{refusal.case}}

### The reading score

Each verified example carries the owner's own words and a set of rewordings. The reading score asks each of them of the offline router, on the golden page, and counts a pass when the plan it reads is the example's. `python3 evals/run.py` prints it and `/evals` shows it, but it is reported, never a gate: it measures how much of the plant's language the offline readers understand, the baseline the planner of [#172](https://github.com/justfetz/corrugator-shift-investigator/issues/172) is meant to raise. A miss is a question the router reads as another plan or refuses, not a wrong number, so it never fails a build. The worked example's wordings today:

{{score.rows}}

A wording asked in a window the model does not read yet is answered in a stand-in window and reported apart, never counted as a pass.

### What each layer cannot catch

| Layer | Catches | Cannot catch |
| --- | --- | --- |
| Value goldens | a tool or compiler figure that differs from plain Python over the generator's records, including a store that loaded the records wrong | a definition both sides share and both get wrong; a fault in the generator itself, which both read; a question that routes to the wrong plan |
| Verified examples | compiled SQL that differs from SQL written by hand from the definition | a fault in the store's tables, which both read; the plans no example holds |
| Routing and refusal goldens | a question that reads into the wrong plan, tool or address, or is refused with the wrong reason | a wrong number from the right plan, which the value goldens check |
| Reading score | the wordings the offline readers do not understand yet | anything that should fail a build: it is never a gate |

None of them shows that the synthetic plant behaves like a real one: they show that the answers are right about these records.

## How to add a test query

1. **The question.** Write it as someone in the plant would ask it, with the page it is asked on: the day, the shift and the Window. The golden page is the default.
1. **The plan.** Write the plan in the model's names ([the semantic model](../src/investigator/semantic_model.json) lists them), and check that it validates: `semantic.validate(plan, default_store())` returns it, or says what is wrong.
1. **The oracle, by hand.** Write the SQL from the definition, over the store's tables (`stops`, `setups`, `rejects`, `rolls`, `shifts`, `setup_orders`; [the record contract](RECORD_CONTRACT.md)), never through the compiler. For a verified example, add the example to `src/investigator/examples.json` and a function named after its id to `tests/test_examples.py`. For a number a fixed tool or the compiler returns over a window, add a value case to `evals/make_value_goldens.py`, computed in plain Python over the generator's records, and run the script: it rewrites `evals/golden_values.json`, and the diff should add your case and change nothing else.
1. **The run.** `python -m pytest -q tests/test_examples.py` compares the oracle with the compiler, and `python3 evals/run.py` runs every golden case. A difference is either a bug in the compiler or the tool, or a definition the oracle reads otherwise: settle which before changing either.
1. **The owner's check.** A new case is `reviewed_by: owner-pending` until the owner has read its number on the pull request's preview; then it is `owner`.
1. **The golden case.** `/add-golden-case <what>` adds the question to `evals/golden.json` with the address it must resolve to and the tools it must run, and makes it pass. A bug becomes a golden case before it is fixed.
