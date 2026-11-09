---
name: review-pr
description: Test a pull request before approving it - pull its head, run the tests and evals, read CI and the preview URL, score every acceptance criterion of its issue, give a verdict
---
Review pull request #$ARGUMENTS on justfetz/corrugator-shift-investigator. Read-only: edit nothing on the branch, push nothing, comment nothing unless asked.

1. `gh api repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS` → head sha, branch, body, `mergeable_state`. The issue is the `Closes #n` in the body; `gh issue view <n>` for its acceptance criteria. No issue → the verdict cannot be `approve`; say so.
2. Fetch and test the head in a worktree, so `main` stays untouched:
   ```bash
   git fetch origin pull/$ARGUMENTS/head:pr-$ARGUMENTS && git worktree add /tmp/pr-$ARGUMENTS pr-$ARGUMENTS
   cd /tmp/pr-$ARGUMENTS && pip install -q -e . && python -m pytest -q && python3 evals/run.py && node --check src/investigator/web/app.js && node tests/test_ui.cjs
   ```
   Record the test count, the eval table and any failure verbatim.
3. Golden cases: `git diff origin/main...pr-$ARGUMENTS -- evals/`. A deleted case, a removed `expect` key, a widened tolerance or a lowered threshold is `fix first`, whatever the PR says.
4. CI: `gh api repos/justfetz/corrugator-shift-investigator/commits/<sha>/check-runs --jq '.check_runs[]|{name,conclusion}'`. Preview: the PR comment starting `<!-- preview-url -->` (`gh api repos/.../issues/$ARGUMENTS/comments`) carries the URL of the tagged Cloud Run revision; if the Preview workflow is still running, say so and give the owner the run link.
5. The reviewer's three rules on the diff: no number reaches the narrative except from a tool result; the offline path makes no model call; public mode accepts no visitor key.
6. Report a table: *criterion · shown / not shown · evidence*, where evidence is a quoted line from the PR description or from your own run. Then: tests, evals, CI, golden diff, preview URL.
7. Verdict, one line:
   - `approve` — every criterion shown, CI green, no golden weakened, nothing a visitor would see changed (or the owner already looked).
   - `fix first: <what>` — a criterion not shown, a failing check or a weakened case.
   - `needs the owner's eyes at <preview URL>: <what to click>` — the diff touches `src/investigator/web/`, `server.py` or the `Dockerfile`; name the walkthrough step from `docs/TESTING.md` that exercises the change.
8. `git worktree remove /tmp/pr-$ARGUMENTS --force && git branch -D pr-$ARGUMENTS`.
