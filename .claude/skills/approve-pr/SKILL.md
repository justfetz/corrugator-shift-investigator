---
name: approve-pr
description: Owner-invoked merge of a reviewed pull request - refuses unless CI is green, no golden case was weakened and the review verdict (the `review:` label) is approve; then merges and confirms the deploy
disable-model-invocation: true
---
The owner has invoked this skill, which is the approval of record for pull request #$ARGUMENTS on justfetz/corrugator-shift-investigator. Merge it only if every check below passes; otherwise stop at the first failure, name it, and merge nothing.

1. `gh api repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS` → `state` must be `open`, `mergeable_state` must be `clean`; note the head sha.
2. `gh api repos/justfetz/corrugator-shift-investigator/commits/<sha>/check-runs --jq '.check_runs[]|{name,status,conclusion}'` → every check `completed` and `success`. A check still running means wait or stop, never merge past it.
3. `git fetch origin main pull/$ARGUMENTS/head:pr-$ARGUMENTS && git diff origin/main...pr-$ARGUMENTS -- evals/`. A deleted case or a removed `expect` key in `evals/golden.json` or `evals/golden_values.json` blocks the merge unless the owner wrote "approve anyway" in this session; quote the diff when you stop.
4. The review verdict must be approve. Run `python3 .github/scripts/agent.py status $ARGUMENTS`, which reads the `review:` label exactly as the review workflow writes it:
   - `review: approve` (the label and the newest verdict comment agree on the current head) → go on.
   - `review: needs-owner (eyes)`: the diff touches what the owner looks at (the preview for `web/`, workflows, rules) or the issue waits on the owner → ask once whether the owner looked or decided; stop if the answer is no.
   - `review: needs-owner (unresolved)`: no linked issue, no reviewer verdict, CI or evals that did not finish, or two fix rounds that left gaps → it is not reviewed: stop on no linked issue or on gaps left (quote them); otherwise run `/review-pr $ARGUMENTS` here and go on only on its `approve`.
   - `review: fix-first` → stop, and quote the verdict comment's gaps.
   - `review: stale` or `review: none` (the head moved, or the workflow did not run) → run `/review-pr $ARGUMENTS` in this session; go on only on its `approve`, or on `needs the owner's eyes` after the question above.
5. Merge: `gh api -X PUT repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS/merge -f merge_method=merge --jq '{merged,sha}'`.
6. Confirm the deploy without touching the service: poll `gh api "repos/justfetz/corrugator-shift-investigator/actions/runs?branch=main&event=push&per_page=3" --jq '.workflow_runs[]|{name,status,conclusion,id}'` until **Deploy Cloud Run** completes (up to 10 minutes), then read its log (`gh api repos/.../actions/runs/<id>/jobs`, then `gh api repos/.../actions/jobs/<job id>/logs`) and report the `GET .../api/health -> HTTP 200 {...}` line and the `Live at` line. Also confirm the Preview workflow's `closed` run succeeded, so the PR's tag is gone.
7. Report: merged sha, deploy result, `boot_ms` and `dataset_hash` from the health line, the live URL. Then `git branch -D pr-$ARGUMENTS`.

Never `gcloud run services update`, never force-push, never merge a PR whose issue is unknown.
