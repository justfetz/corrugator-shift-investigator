---
name: review-pr
description: Test a pull request before approving it - pull its head, run the tests and evals, read CI and the preview URL, score every acceptance criterion of its issue, give a verdict
---
Review pull request #$ARGUMENTS on justfetz/corrugator-shift-investigator. Read-only: edit nothing on the branch, push nothing, comment nothing unless asked.

1. `gh api repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS` → head sha, branch, body, `mergeable_state`. The issue is the `Closes #n` in the body; `gh api repos/justfetz/corrugator-shift-investigator/issues/<n>` for its acceptance criteria (REST: `gh issue view` uses GraphQL, which sessions cannot reach). No issue → the verdict cannot be `approve`; say so. Then `python3 .github/scripts/agent.py status $ARGUMENTS`: the `review:` label the review workflow left, read the way the workflow reads it (`review: approve` only when the label, the newest verdict comment and the head's `agent-review` commit status agree on the current head; a label from an older head, or a comment no status confirms, reads `review: stale`; `review: needs-owner` carries its kind, `eyes` or `unresolved`). Quote it in the report.
2. Fetch and test the head in a worktree, so `main` stays untouched:
   ```bash
   git fetch origin pull/$ARGUMENTS/head:pr-$ARGUMENTS && git worktree add /tmp/pr-$ARGUMENTS pr-$ARGUMENTS
   cd /tmp/pr-$ARGUMENTS && pip install -q -e . && python -m pytest -q && python3 evals/run.py && node --check src/investigator/web/app.js && node tests/test_ui.cjs
   ```
   Record the test count, the eval table and any failure verbatim.
3. Golden cases: `git diff origin/main...pr-$ARGUMENTS -- evals/`. A deleted case, a removed `expect` key, a widened tolerance or a lowered threshold is `fix first`, whatever the PR says.
4. CI: `gh api repos/justfetz/corrugator-shift-investigator/commits/<sha>/check-runs --jq '.check_runs[]|{name,conclusion}'`. Preview: the PR comment starting `<!-- preview-url -->` (`gh api repos/.../issues/$ARGUMENTS/comments`) carries the URL of the tagged Cloud Run revision; if the Preview workflow is still running, say so and give the owner the run link.
5. Bot review threads: `gh api repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS/comments` and `.../pulls/$ARGUMENTS/reviews` (the Codex connector reviews every PR). Every thread, outdated or not, is either fixed (name the commit, and check the code really changed) or answered on the thread with the reason; an unanswered one is `fix first`. Outdated only means the lines moved.
6. The reviewer's three rules on the diff: no number reaches the narrative except from a tool result; the offline path makes no model call; public mode accepts no visitor key.
7. Report a table: *criterion · shown / not shown · evidence*, where evidence is a quoted line from the PR description or from your own run. Then: tests, evals, CI, golden diff, preview URL.
8. Verdict, one line, by the review workflow's rules (`decide` in `.github/scripts/agent.py`; the three verdicts are its three labels):
   - `approve` (`review: approve`) — every criterion shown, CI green, the evals pass, no golden weakened, and nothing in `agent.py`'s `OWNER_EYES` changed (or the owner already looked).
   - `fix first: <what>` (`review: fix-first`) — a criterion not shown, a failing check or evals, a weakened case, a deleted file, fewer tests collected than on the merge base (`python -m pytest --collect-only -q` on both), or a branch that conflicts with `main`.
   - `needs the owner's eyes at <preview URL>: <what to click>` (`review: needs-owner`) — *eyes*: the diff touches `src/investigator/web/`, `server.py`, the `Dockerfile`, `deploy/`, `.github/`, or a `CLAUDE.md` or `.claude/` at any depth (renames count by both paths), a symlink pointing outside the repository (a symlink counts by its target), or the issue is `waiting-on-owner`; for `web/`, name the walkthrough step from `docs/TESTING.md` that exercises the change. *unresolved*: a base other than `main`, no linked issue, no reviewer verdict, CI, evals or the test count that did not finish, or two fix rounds that left gaps.
   If your verdict differs from the label, say which and why; you never change the label (read-only).
9. `git worktree remove /tmp/pr-$ARGUMENTS --force && git branch -D pr-$ARGUMENTS`.
