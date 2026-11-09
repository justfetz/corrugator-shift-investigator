---
name: fix-issue
description: Work one GitHub issue end to end - read it, baseline, implement, test, evals, review, PR
disable-model-invocation: true
---
Work issue #$ARGUMENTS on justfetz/corrugator-shift-investigator.

1. `gh issue view $ARGUMENTS`. The acceptance criteria are the definition of done.
2. Baseline: `python -m pytest -q` and `python3 evals/run.py`. Note the numbers.
3. Branch `issue-$ARGUMENTS-<slug>` from `main`. For a bug, add the golden case first (`/add-golden-case`) and watch it fail.
4. Implement the smallest change that meets every acceptance criterion. Update the docs the change touches.
5. Run the tests, the evals, and `node --check src/investigator/web/app.js && node tests/test_ui.cjs`.
6. Ask the `reviewer` subagent to check the diff against the issue. Fix correctness gaps.
7. Commit, push, and `gh pr create` with: what changed; how it was tested (paste the eval table); each acceptance criterion with the command and output that demonstrates it; `Closes #$ARGUMENTS`. Do not merge.
8. If the change touches `web/`, the server or the Dockerfile: wait for the Preview workflow's comment (3–5 min), open the URL, ask a question, and add what you saw to the PR description. The preview is a tagged Cloud Run revision with no traffic; it is removed when the PR closes.
