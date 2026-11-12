---
name: fix-issue
description: Work one GitHub issue end to end - read it, baseline, implement, test, evals, review, PR
disable-model-invocation: true
---
Work issue #$ARGUMENTS on justfetz/corrugator-shift-investigator.

1. `gh api repos/justfetz/corrugator-shift-investigator/issues/$ARGUMENTS --jq '.title, .body'` and its comments (`.../issues/$ARGUMENTS/comments`); `gh issue view` and `gh pr create` use GraphQL, which sessions cannot reach, so use REST throughout. The acceptance criteria are the definition of done. If an open PR already closes it (`python3 .claude/skills/issues/backlog.py` shows the issue as `in PR #m`), stop and name that PR: one session per issue. Its next task is the newest coordinator comment on that PR.
2. Baseline: `python -m pytest -q` and `python3 evals/run.py`. Note the numbers.
3. Work on the branch the session gives you, from `main` (`issue-$ARGUMENTS-<slug>` if it gives none). For a bug, add the golden case first (`/add-golden-case`) and watch it fail.
4. Implement the smallest change that meets every acceptance criterion. Update the docs the change touches.
5. Run the tests, the evals, and `node --check src/investigator/web/app.js && node tests/test_ui.cjs`.
6. Ask the `reviewer` subagent to check the diff against the issue. Fix correctness gaps.
7. If `main` moved while you worked, `git merge origin/main` into your branch (never rebase: force pushes are denied), resolve the eval files as CLAUDE.md says (then `python3 evals/run.py --track` and commit `history.jsonl` and `last_run.json` together), rerun step 5. Commit, push, and open the PR with `gh api repos/justfetz/corrugator-shift-investigator/pulls -f title=… -f head=<branch> -f base=main -F body=@<file>`; the title is the changelog line. The body has: what changed; how it was tested (paste the eval table); each acceptance criterion with the command and output that demonstrates it; `Closes #$ARGUMENTS`. Do not merge.
8. If the change touches `web/`, the server or the Dockerfile: wait for the Preview workflow's comment (3–5 min) and paste the URL into the PR description. `*.run.app` is not reachable from a cloud session's network, so do not try to open it and do not ask to switch environments for it: the owner looks at it during `/review-pr`. Render locally instead (`python -m investigator.server`, headless Chrome if present) and describe what you checked. The preview is a tagged Cloud Run revision with no traffic; it is removed when the PR closes.
