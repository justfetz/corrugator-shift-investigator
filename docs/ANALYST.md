# The analyst

Every question is answered by the offline planner first. The analyst is the optional second opinion: **Ask the analyst** on an answer (or beside the ask bar, for the typed question) sends that question to a language model once. The model chooses which of the same bounded queries to run and writes a short interpretation; application code runs the queries, fills in every number and decides what the reader sees. The button appears only when the site has a model configured.

## What the model may do

- **Propose one plan.** A single model call returns a JSON plan: an ordered list of tool calls from the same catalog the offline planner uses, at most two steps per shift in scope plus three (five for one shift, nine for all three), or a reason the question cannot be answered.
- **Write the interpretation.** A second call receives the question and a compact packet of the results and writes two to six lines. Each value in a line is a placeholder such as `{{r1.0.observed_shift_fpm}}`, which the server replaces with that row's value as the text streams.
- **See dollars on priced rows.** The two money tools (`get_shift_dollars`, `get_opportunities`) are left out of the catalog the model is shown, because its prompt still says costs are unavailable; the plan check itself would accept them, as it accepts any tool on the allowlist. The day total `get_day_dollars` is the offline planner's alone: it is not on the allowlist, so a plan naming it is refused whole. The model sees dollars only on the priced rows of the tools it plans.
- **Choose emphasis.** Which results to lead with, what to call a finding and what to call a limitation are the model's; whether they hold is checked below.

## What the model may not do

- **Supply a number.** A digit the model typed outside a placeholder stops that line at once and withdraws it. Numbers written as words are refused too. The reader never sees a number no query returned.
- **Run anything off the list.** Before any query runs, application code checks every step of the plan: the tool must be on the allowlist, every argument must be in its schema with a value of the declared type, and every step must stay inside the selected shift and window. One bad step refuses the whole plan; nothing runs, and the offline answer stands with a one-line note.
- **Change the scope.** The scope is fixed before the model is asked: the rail's selection, or the shift and window the question itself names, which the application applies (issue #105). A plan cannot reach another shift, day or window than that.
- **Contradict its own figures.** A line whose words assert an order its cited values contradict ("fastest", "below", "rose") is withdrawn, and the answer says a comparison was withdrawn.
- **Reach anything else.** There is no SQL, shell, file, web search, email or machine control to call, and no model-controlled URL.
- **Spend without bounds.** A round is two model calls and ten seconds at most, each request at most 64 KB and each response at most 512 KB. On the hosted demo a per-visitor rate limit, a daily cap on rounds, a monthly reservation ledger and the provider console's spend limit sit behind that ([Anthropic in the hosted demo](ANTHROPIC_DEMO.md)).
- **Take a visitor's key.** The public demo never accepts one: the server ignores key headers in public mode and the page has no key field.

What these rules prove is that every digit came from a query row and every query stayed in scope. They do not prove the words around the digits are wise; read an analyst's lines as hypotheses to check, like any recorded finding.

## Anthropic is the default

The hosted demo's analyst runs on Anthropic's Messages API with a key the **server** holds, mounted from Secret Manager by name; visitors never supply, see or send a key. The model is `claude-haiku-5-5` unless the deployment sets `ANTHROPIC_MODEL=claude-sonnet-5-5`. When a server holds keys for both providers, the analyst uses Anthropic. The round streams: the planned queries appear with their progress as they run, then the interpretation line by line. [Anthropic in the hosted demo](ANTHROPIC_DEMO.md) has the request shape, the stream's events, the guards and the cost per round.

On the loopback workbench you can choose **Anthropic / your key** under *Agent and advanced* and paste your own key; it travels to the local server in a header for that one request and is never stored.

## OpenAI is an optional provider

An adapter for OpenAI's Responses API (`gpt-4.1-mini`) is kept as an optional provider: on the loopback workbench with your own key, or on a deployment whose operator names an OpenAI secret. It runs a tool loop of up to ten calls, one function per response, instead of the single checked plan, under the same tool validation, scope rule, narrative validator and budget ledger. It has no recorded golden fixtures, so its answers are not scored by the golden set. [OpenAI, the optional provider](OPENAI_BYOK.md) documents its key handling and limits.

## How the analyst is scored

Fifteen recorded Anthropic rounds are replayed in CI through the real agent, tools and validators, and scored by pass rate per case type; they were recorded with the earlier tool loop, and re-recording them in the one-plan shape is the owner's step in issue #71. Scripted plans and scripted narratives are golden cases too, so "an out-of-list tool refuses the whole plan" and "a contradicted comparison is withdrawn" are checked on every run without a model. [Numbers and evals](EVALS.md) explains the scoring; [the agent contract](AGENT_CONTRACT.md) and [from question to evidence](AGENT_EXECUTION.md) give the boundary in full.
