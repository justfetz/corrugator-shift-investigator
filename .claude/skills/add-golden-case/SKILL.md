---
name: add-golden-case
description: Add a golden eval case to evals/golden.json for a question, a bug or a refusal, and make it pass
---
Add a golden case for: $ARGUMENTS

1. Pick the type from `evals/README.md` ("Seven case types"): routing (which tools, which arguments, which charts), refusal (`raises` or `forbidden`), faithfulness (`sections_match`, `prose_numbers`), grounding, investigation, data_quality.
2. Write the case in `evals/golden.json`: a short kebab-case `id`, `mode` `"offline"` unless it needs a model, and only the `expect` keys the type needs (`status`, `shifts`, `tools`, `tools_only`, `charts`, `tables`, `sections_match`, `sections_not_match`, `raises`, `max_cost_usd`, `max_ms`). Copy the shape of a neighbouring case of the same type.
3. Run `python3 evals/run.py --only <id>`. If it fails for the right reason (the bug), fix the code. If it fails because the expectation is wrong, fix the expectation and say so in the PR.
4. Run `python3 evals/run.py` and `python -m pytest -q tests/test_evals.py`.
5. In the PR, paste the case and the before and after score tables.

Never type expected numbers by hand. Value cases come from `python3 evals/make_value_goldens.py`.
