---
name: approve-pr
description: Owner-invoked merge of a reviewed pull request - refuses unless CI is green, no golden case was weakened and the review verdict was approve; then merges and confirms the deploy
disable-model-invocation: true
---
The owner has invoked this skill, which is the approval of record for pull request #$ARGUMENTS on justfetz/corrugator-shift-investigator. Merge it only if every check below passes; otherwise stop at the first failure, name it, and merge nothing.

1. `gh api repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS` → `state` must be `open`, `mergeable_state` must be `clean`; note the head sha.
2. `gh api repos/justfetz/corrugator-shift-investigator/commits/<sha>/check-runs --jq '.check_runs[]|{name,status,conclusion}'` → every check `completed` and `success`. A check still running means wait or stop, never merge past it.
3. `git fetch origin main pull/$ARGUMENTS/head:pr-$ARGUMENTS && git diff origin/main...pr-$ARGUMENTS -- evals/`. A deleted case or a removed `expect` key in `evals/golden.json` or `evals/golden_values.json` blocks the merge unless the owner wrote "approve anyway" in this session; quote the diff when you stop.
4. The verdict must be `approve`: from `/review-pr $ARGUMENTS` in this session, or from the newest coordinator review comment on the PR (`Review` or `Re-review`) when it names the current head sha. If neither exists, or the head moved after it, run `/review-pr $ARGUMENTS` now. If the verdict was `needs the owner's eyes`, ask once whether the owner looked at the preview, and stop if the answer is no.
5. Merge: `gh api -X PUT repos/justfetz/corrugator-shift-investigator/pulls/$ARGUMENTS/merge -f merge_method=merge --jq '{merged,sha}'`.
6. Confirm the deploy without touching the service: poll `gh api "repos/justfetz/corrugator-shift-investigator/actions/runs?branch=main&event=push&per_page=3" --jq '.workflow_runs[]|{name,status,conclusion,id}'` until **Deploy Cloud Run** completes (up to 10 minutes), then read its log (`gh api repos/.../actions/runs/<id>/jobs`, then `gh api repos/.../actions/jobs/<job id>/logs`) and report the `GET .../api/health -> HTTP 200 {...}` line and the `Live at` line. Also confirm the Preview workflow's `closed` run succeeded, so the PR's tag is gone.
7. Report: merged sha, deploy result, `boot_ms` and `dataset_hash` from the health line, the live URL. Then `git branch -D pr-$ARGUMENTS`.

Never `gcloud run services update`, never force-push, never merge a PR whose issue is unknown.
