# Agents: from a label to a pull request

Work on this repository can start with a label (issue #126). The owner labels an issue `agent-ready`; a GitHub Actions run of Claude Code works it with this repository's own procedure (`/fix-issue <n>`, CLAUDE.md, the `reviewer` agent) and opens one pull request that closes it; a second workflow reviews every pull request against its issue and labels it; and every pull request merges on the owner's word, except documentation-only and test-only ones the reviewer approves, which merge themselves once the owner turns that tier on (it is off by default, [below](#what-merges-itself)). The owner is asked only for product decisions (`waiting-on-owner`), for anything a visitor would see or anything in the workflows and rules (`review: needs-owner`), for GCP, IAM and secrets, and for the daily merge batch.

Two workflow files and one script do it: [`.github/workflows/agent.yml`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/workflows/agent.yml) (the builder), [`.github/workflows/agent-review.yml`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/workflows/agent-review.yml) (the reviewer), and [`.github/scripts/agent.py`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/scripts/agent.py), which holds every rule below so that the workflows, the `/review-pr`, `/approve-pr` and `/issues` skills and `tests/test_agent_workflows.py` read the same ones. Both workflows run Anthropic's official [Claude Code GitHub Action](https://code.claude.com/docs/en/github-actions) (`anthropics/claude-code-action@v1`). [`.github/CODEOWNERS`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/CODEOWNERS) makes GitHub itself hold everything outside the auto-merge tier for the owner's review.

## Fix before enabling `agent-ready`

The workflows refuse what they can check. These they cannot, so they are the owner's, done once before the first label (the coordinator's review of PR #132, findings H1 to H5):

| What | Why | Where it is done |
| --- | --- | --- |
| **`main` protected** (H1) | The agent's token can push branches; only protection keeps a push to `main` from deploying the public demo. The build job checks that `main` has a classic rule requiring a status check, or a ruleset requiring a pull request or a status check, and refuses to run the agent otherwise. | "Set up once", step 3 |
| **Code-owner review and the `agent-review` check required on `main`** (H2) | The agent holds a token that can merge over the REST API whatever its tool list says. With "Require review from Code Owners", GitHub refuses any merge outside the auto-merge tier until the owner approves; with `agent-review` a required status check, it refuses any merge, auto tier included, that the review workflow did not approve on that head (the agent's token cannot write a status). | "Set up once", step 3 |
| **Preview runs only its base's scripts** (H4) | `preview.yml` runs the pull request's own `.github/scripts/*.py` and `*.sh` after the Google Cloud credential is on disk, and nothing stops an agent from changing those files (its `.github/` rule covers its edit tools only, not `python3` or `git checkout`). The agent's pushes start no workflow, so Preview never runs on them by itself today, but it does as soon as anyone pushes to or reopens the pull request. | Issue #133 (`preview.yml`) |
| **The Workload Identity pool trusts named workflows only** (H5) | The pool's condition is `assertion.repository == '<repo>'` ([DEPLOYMENT.md](DEPLOYMENT.md#keyless-deploy-workload-identity-federation)), so any job in this repository that holds `id-token: write` can become the deployer. No job of the builder holds it (that is why the agent pushes with the job's own token, not the Claude GitHub App, whose token the action gets only by an OIDC exchange); `preview.yml` and the deploy still do. | Issue #133 (the pool condition, the owner's IAM) |

Also for #133: `deploy-cloudrun.yml` takes a `workflow_dispatch` on any branch and deploys that branch to production. No job the agent runs in holds `actions: write`; the `verify` job that does runs `main`'s script on `plan`'s outputs only.

## The flow

```
 owner: label the issue agent-ready   (or comment @claude on it)
            │
            ▼
 agent.yml    gate ──► queue (at most 3 at once) ──► build (one run per issue, 60 min)
              refuses unless main is protected; checks out main, branch issue-<n>-<slug>,
              runs /fix-issue <n>; Claude pushes and opens ONE pull request "Closes #n"
              with the job's own token; the run labels it agent-built
            │
            ▼
              verify: one pull request, or a pushed fix; then starts Tests on the branch and
              the review of the pull request (a GITHUB_TOKEN push starts no workflow by itself)
            │
            ▼
 agent-review.yml   on every head: evals, test count, reviewer agent, CI, golden diff
              ──► one verdict comment, one label and one `agent-review` commit status on that head
            │
     ┌──────┴────────────────────────┬──────────────────────────────┐
 review: fix-first             review: approve                review: needs-owner
 agent-built: back to          the merge batch, /approve-pr;  the owner looks: the
 agent.yml for a fix round     docs/ and tests/ only:         preview, workflows, a
 on the same branch (two       auto-merge after CI when       pending decision, or two
 at most, then needs-owner)    AGENT_AUTO_MERGE is on         rounds that did not fix it
```

## Labels

| Label | Put on | By | Means |
| --- | --- | --- | --- |
| `agent-ready` | an issue | only the owner (the builder ignores anyone else's) | Start (or resume) an agent run on this issue. Removing it, by anyone, stops the run. |
| `agent-built` | a pull request | the builder | The agent opened it; the reviewer may send it back for fix rounds. |
| `review: approve` | a pull request | the reviewer | Every acceptance criterion shown, CI green, evals pass, no golden case weakened, no file deleted, no test lost, nothing the owner must look at. In the merge batch. |
| `review: fix-first` | a pull request | the reviewer | Gaps to fix on this branch; the comment lists them. |
| `review: needs-owner` | a pull request | the reviewer | The owner's eyes or decision are needed; the comment says what. |
| `waiting-on-owner` | an issue | a session | The session asked the owner something in the issue (unchanged, [CLAUDE.md](../CLAUDE.md)). |

The review workflow's `reset` job runs `gh label create` for each of the five agent labels on every review, which creates any that is missing and leaves the rest alone; putting a label on through the API would create it too. To label an issue before the first review has run, create `agent-ready` yourself ("Set up once", step 5). A label is about one head: every push, and every edit of the body or the base, removes the `review:` label (and any auto-merge) and sets the head's `agent-review` status back to pending until the new head is reviewed.

## What starts and stops a run

Only the repository owner starts a run (H3); `agent.yml`'s `gate` condition and `agent.py`'s `event_plan` check the same thing.

| Event | Effect |
| --- | --- |
| `agent-ready` added to an issue by the owner | A build: the issue's branch from `main`, or the one branch an earlier run left without a pull request. Added by anyone else (a collaborator, the Claude app, the Codex connector, a workflow), nothing. |
| `@claude` in a comment by the owner, on an issue | The same as the label: the run puts `agent-ready` on the issue, so removing it stops the run and its pull request gets fix rounds. |
| `@claude` in a comment by the owner, on a pull request | A fix run on that pull request's branch, with the comment as the request. Anyone else's `@claude` does nothing. |
| `review: fix-first` on an `agent-built` pull request | The review workflow dispatches a fix round (`agent.yml` with `issue`, `pr` and a `round` of 1 or 2) on the same branch. That is the only run `github-actions[bot]` may start, and `plan` checks that the newest verdict sent this head back as this round. |
| `agent-ready` removed | The issue's running build or fix run is cancelled; a queued build or reviewer fix round does nothing when it starts. A queued `@claude` fix run on a pull request still runs: it is the owner's own request. |
| Actions → Agent → *Run workflow*, by the owner | A build (`issue` only) or a fix run (`issue` and `pr`) by hand. A dispatch by anyone else does nothing. |

**One run per issue.** The build job's concurrency group is the issue, so a second label waits behind the first run, and every run first checks for an open pull request that closes the issue or comes from an `issue-<n>-` branch. If there is one, the run refuses and says so on the issue: labelling an issue again never opens a second pull request. A branch whose pull request was closed is not reused; delete it to start the issue over.

**At most three at once.** The `queue` job waits until fewer than three earlier Agent runs are still going, first come first served, polling every two minutes with two API calls (the runs in progress and the runs queued; they share the workflow token's hourly request budget with Preview and the review). After 330 minutes in the queue a run gives up and says so on the issue; label it again to retry. A single concurrency group cannot do this: GitHub runs one job per group and keeps only the newest waiting one, cancelling the rest, so one `agents` group would mean one agent at a time and would silently drop the issues labelled in between.

## The review verdict

The reviewer agent ([.claude/agents/reviewer.md](../.claude/agents/reviewer.md)) reads the issue, the diff and every changed expectation of an existing golden case, and lists correctness gaps; it never chooses the label. Its `Read` rule covers the workspace only (the runner's own folders are denied), it has no shell, it may write its gaps file and nothing else, and everything it writes is scrubbed of key patterns, kept to one line and capped before the verdict posts it. The workflow measures everything else and `decide` in `agent.py` turns it into one verdict. Files are counted by every path they touch, so a rename out of `src/` or `.claude/` into `docs/` is not a docs-only change, and a symlink counts by its target too, so `docs/x -> ../src/…` is a `src/` change.

- **`review: fix-first`**: a gap the reviewer named; a golden case deleted, an `expect` key removed, a case's `mode` changed or a `max_ms` / `max_cost_usd` raised (checked against the merge base, whatever the pull request says); **a deleted file**; **fewer tests collected** (`pytest --collect-only`, deselected tests not counted) than on the merge base; a failing `deterministic` check; failing `python3 evals/run.py`; or a branch that conflicts with `main`. The deleted file and the test count are rules, not the model's judgement: when an issue asks for a deletion, the owner merges it with `/approve-pr` and "approve anyway" (a fix round is told not to undo what the issue asks for). An `agent-built` pull request whose issue still carries `agent-ready` goes back to the builder, at most **two** rounds; a third fix-first becomes `review: needs-owner`. Anyone else's pull request is labelled and left to its author.
- **`review: needs-owner`**, of two kinds, named in the comment's marker and by `status`:
  - *eyes*: the issue is labelled `waiting-on-owner`, or the diff touches what the owner looks at before merge: `src/investigator/web/`, `src/investigator/server.py`, the `Dockerfile`, `deploy/`, `.github/`, a `CLAUDE.md` or `.claude/` folder at any depth (Claude Code reads a nested `CLAUDE.md` as instructions), or a symlink that points outside the repository. The owner looks, then may merge.
  - *unresolved*: a base other than `main`; no linked issue (`Closes #n`); no reviewer verdict (no credential, or the run failed); CI that did not finish within 30 minutes, evals that did not run, or a test count that was not measured; or two fix rounds that left gaps. Nothing is approved until it is resolved; `/approve-pr` treats it as not reviewed.
- **`review: approve`**: none of the above.

The verdict comment starts with a hidden marker (`<!-- agent-review sha=… verdict=… round=… -->`) naming the head it judged, and the same verdict is the head's **`agent-review` commit status** (success, failure or pending). `python3 .github/scripts/agent.py status <pr>` reads the label, the newest marker and that status together, the way `/review-pr` and `/approve-pr` do: `review: approve` only when all three agree on the pull request's current head, `review: stale` when the head moved since, when no status confirms the comment, or when the body now closes another issue or the base is no longer `main` (an edit made with a workflow token starts no review). The status matters because the builder's token writes comments as `github-actions[bot]` too, but it is never granted `statuses: write`.

## What merges itself

| Tier | Which pull requests | How they merge |
| --- | --- | --- |
| **auto** | every changed file is under `docs/` or `tests/`, or is `README.md` or `evals/README.md` (a symlink counts by its target too) | Only while the repository variable `AGENT_AUTO_MERGE` is `true`: on `review: approve` the review workflow turns on GitHub auto-merge with a merge commit (no squash), pinned to the reviewed head, and GitHub merges once the required checks pass. Off by default: the pull request joins the merge batch like any other. (An agent run's own token could still merge an approved one; the required `agent-review` check is what holds every merge to an approved head.) |
| **owner** | anything else: `src/` (the tools, the planner, data generation, the cost model and money, `web/`), `evals/golden*.json`, the workflows, the skills, CLAUDE.md | The owner's merge word, `/approve-pr <n>`, which checks CI, the eval diff and the `review:` label first. GitHub enforces it: these paths are the owner's in `.github/CODEOWNERS`. |

**Why tiered auto-merge is off by default.** A merge that auto-merge makes with the workflow's own token is GitHub acting for that token, and GitHub starts no workflow from it: the merged pull request is not deployed on push (the nightly deploy of `main`, [DEPLOYMENT.md](DEPLOYMENT.md), "The nightly deploy", takes it the next weekday morning, so only the `/docs` pages and the changelog can lag, and the deploy's changelog check runs then), and Preview's close job does not run, so a `pr-<n>` tag, if the pull request had one, stays on the Cloud Run service until it is removed by hand (`gcloud run services update-traffic <service> --remove-tags pr-<n>`). The fix the coordinator asked for, merging with the Claude GitHub App's token, is not available: the action gets that token only inside its own step, by an OIDC exchange that needs `id-token: write` (dropped for H5), and revokes it when the step ends (its *Revoke app token* step). So the owner turns the tier on knowingly, with the variable, or leaves the merge to `/approve-pr`, which merges as the owner and so deploys on push. A token of the owner's own GitHub App would merge with workflows starting normally; that is a follow-up, not wired here.

Protected `main` keeps requiring the `deterministic` and `agent-review` checks, for both tiers. That is what stops the agent's own token from merging an auto-tier pull request the review did not approve: without the required `agent-review` check, GitHub would ask only CI of a docs-only pull request.

**The merge batch.** `/issues` lists the open pull requests whose `review: approve` is for their current head (as `agent.py status` reads it) in merge order (the backlog order of the issue each closes, then the pull request number) with their tier. The coordinator's daily summary quotes that list, and the owner merges them in that order.

## Caps and cost

- Builds: 60 minutes each (`timeout-minutes`), `--max-turns` 200 (`AGENT_MAX_TURNS`), at most three at once, one per issue.
- Reviews: 20 minutes for the reviewer job, `--max-turns` 40 (`REVIEW_MAX_TURNS`); one per head (a new push, or an edit of the body or the base, cancels the review in flight; an edit of the title alone reviews nothing).
- Fix rounds: at most two per pull request from the reviewer; the owner's own `@claude` fix runs are not counted.
- Each build and fix round writes its cost, turns and minutes to the run's job summary. The dollar cap is the key's spend limit; [DEPLOYMENT.md](DEPLOYMENT.md#agent-runs-caps-and-monthly-spend) has the monthly note.

## Set up once (the owner)

1. **One Anthropic credential**, as a repository secret: `ANTHROPIC_API_KEY`, a key used only by the agents with its own monthly spend limit in the Claude Console (not the demo's key, which stays in Secret Manager). Or `CLAUDE_CODE_OAUTH_TOKEN` (a subscription token from `claude setup-token`). The workflows learn only whether one exists, the two Claude Code steps alone receive it, and no value is in a workflow file or a log.
2. **Settings → Actions → General → Workflow permissions**: tick **Allow GitHub Actions to create and approve pull requests** (the agent opens its pull request with the build job's own token). Each job asks for exactly what it uses; only if the repository or organization caps the `GITHUB_TOKEN` at read, also choose *Read and write permissions*.
3. **Protect `main`** (Settings → Rules → Rulesets → *New branch ruleset*, target the default branch): require a pull request before merging, with **Require review from Code Owners** and **Dismiss stale pull request approvals when new commits are pushed** (required approvals may stay at 0: the code-owner rule alone holds every path outside the auto tier); require the status checks `deterministic` and `agent-review` (each appears in the list once it has run on a pull request); block force pushes; restrict deletions. Put the *Repository admin* role in the bypass list, so the owner can still merge a pull request the owner opened (a session's, which the owner cannot approve) or one the review left at `needs-owner`. The build refuses to run until `main` is protected (it reads `GET branches/main` and the rules that apply to `main`).
4. **The fixes of #133** ("Fix before enabling `agent-ready`", above): `preview.yml` runs only its base's scripts, and the Workload Identity pool trusts named workflows only.
5. **The `agent-ready` label**, if an issue is to be labelled before the review workflow has run once (it creates the other four):

```bash
gh label create agent-ready --color 0e8a16 --description "Start an agent run on this issue (docs/AGENTS.md)"
```

Optional repository variables: `AGENT_AUTO_MERGE` (`true` turns on the auto tier, with the trade-off [above](#what-merges-itself)), `AGENT_MODEL` (passed as `--model`; unset uses Claude Code's default), `AGENT_MAX_TURNS`, `REVIEW_MAX_TURNS`.

The Claude GitHub App is not needed by these workflows. It would let the agent push as the app, so its pushes start Tests, Preview and the review by themselves, but the action reaches the app only through an OIDC token, which needs `id-token: write` in the job the agent runs in. That can be revisited once the pool trusts named workflows only (#133).

## How to stop a run

- Remove `agent-ready` from the issue: the running build or fix round is cancelled, a queued one does nothing.
- Or cancel the run in the Actions tab (Agent → the run → *Cancel workflow*).
- To stop all agents: Actions → Agent → *Disable workflow* (and Agent review, to stop reviews). Nothing else depends on them.
- An open pull request is never touched by a new build; close it, and delete its branch to let the issue start over.

## What an agent can and cannot do

**Enforced by GitHub and the workflows**, whatever the agent runs:

- Only the owner starts a run (`gate`); the review workflow may start only a fix round for a head it sent back.
- The agent's credential is the build job's own `GITHUB_TOKEN`, valid for that job only, with `contents`, `pull-requests` and `issues` write and nothing else: no `actions` (it cannot dispatch a workflow), no `statuses` (it cannot forge a verdict's commit status), no `id-token` (it cannot mint an OIDC token for Google Cloud or anything else), no `workflows` (GitHub refuses its push of any change under `.github/workflows/`).
- It cannot push to `main`; nothing merges into `main` without the review workflow's approve on that head (the required `agent-review` check), and nothing outside the auto tier without the owner's review, once "Set up once" step 3 is done; the build refuses to run before `main` is protected.
- What leaves the build job for the job that dispatches workflows is `plan`'s outputs, set before the agent ran; `verify` checks the outcome with `main`'s script from a fresh checkout.
- It cannot touch the review's facts: the review workflow runs on `pull_request_target`, so its definition and scripts come from `main`; the pull request's code runs only in the job that runs the evals and counts the tests, with no secret and a read-only token; every job hands its result to the verdict as job outputs, which only that job can set (an artifact could be uploaded under any name by any job of the run); the verdict job works out the files list, the golden check, the deletions, the symlinks and the issue itself, from its own checkout, and takes only the gaps from the model and the merge base's test count from a job that runs `main`'s history only. Fork pull requests are never reviewed.

**Guard rails, not a sandbox.** The builder runs with a fixed tool list (`--allowedTools` in `agent.yml`): reading and editing files, Python and Node (the tests and the evals), `git` without force pushes or rebases, and `gh` only to read issues and pull requests and to create and edit its own pull request. It may not use `gh pr merge`, `gh api` or `curl`, nor edit `.github/`, `.claude/` or any `CLAUDE.md`; the project's `.claude/settings.json` deny list applies as in any session. Python can still do whatever the job's token can: push to any unprotected branch (a `+` refspec included), comment and label, edit any pull request's title, body or base (a workflow-token edit starts no review, so `status` binds a verdict to its issue and base), merge a pull request that meets `main`'s rules (with step 3, one the review approved), and edit the files those rules name. An issue that needs a workflow, skill or CLAUDE.md change is done in a session. The reviewer has a read-only token, no shell, reads the workspace and writes its list of gaps; the review workflow, not the model, comments, labels, sets the status and merges.

## Troubleshooting

- **The run fails at "Refuse while main is unprotected".** Protect `main` ("Set up once", step 3). The run says so on the issue.
- **The run fails at "Check that an Anthropic credential is configured".** Set one of the credentials in step 1.
- **A run ended without a pull request**, and its log shows "GitHub Actions is not permitted to create or approve pull requests". Tick the setting in step 2; label the issue again to resume on the same branch.
- **No CI or no review on the agent's pull request.** The `verify` job starts both after every build and fix round; its comment on the pull request says which one failed. Start Tests by hand on the branch (Actions → Tests → *Run workflow*) or the review (Actions → Agent review → *Run workflow*, with the pull request's number).
- **No preview on the agent's pull request.** Expected: Preview starts on a pull request's events, and a `GITHUB_TOKEN` push or pull request starts none. After #133, close and reopen the pull request to get one.
- **The verdict warns "auto-merge not enabled".** Allow auto-merge (Settings → General → Pull Requests) and protect `main`; or leave `AGENT_AUTO_MERGE` unset.
- **The run refused.** Its comment on the issue says why: an open pull request already closes the issue, or a branch from an earlier pull request is in the way.
- **A fix round pushed nothing.** The head is unchanged, and a head is sent back at most once (a second review of it spends no second round). When the gaps left are a deleted file or fewer tests that the issue asks for (the agent is told not to undo those), merge with `/approve-pr` and "approve anyway"; otherwise comment `@claude` on the pull request with what to do.
- **A run was cancelled** (the label removed, or the 60-minute cap). `verify` does not run after a cancelled build, so a pull request it may have opened has no `agent-built` label, CI or review yet: start Tests and the review by hand (above), or close it and label the issue again.
- **`status` says `review: stale` right after an approve.** The head's `agent-review` commit status is missing or says otherwise: run Agent review again for the pull request.
