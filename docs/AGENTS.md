# Agents: from a label to a pull request

Work on this repository starts with a label (issue #126). The owner labels an issue `agent-ready`; a GitHub Actions run of Claude Code works it with this repository's own procedure (`/fix-issue <n>`, CLAUDE.md, the `reviewer` agent) and opens one pull request that closes it; a second workflow reviews every pull request against its issue and labels it; documentation-only and test-only pull requests the reviewer approves merge themselves, and everything else waits for the owner's merge word. The owner is asked only for product decisions (`waiting-on-owner`), for anything a visitor would see or anything in the workflows and rules (`review: needs-owner`), for GCP, IAM and secrets, and for the daily merge batch.

Two workflow files and one script do it: [`.github/workflows/agent.yml`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/workflows/agent.yml) (the builder), [`.github/workflows/agent-review.yml`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/workflows/agent-review.yml) (the reviewer), and [`.github/scripts/agent.py`](https://github.com/justfetz/corrugator-shift-investigator/blob/main/.github/scripts/agent.py), which holds every rule below so that the workflows, the `/review-pr`, `/approve-pr` and `/issues` skills and `tests/test_agent_workflows.py` read the same ones. Both workflows run Anthropic's official [Claude Code GitHub Action](https://code.claude.com/docs/en/github-actions) (`anthropics/claude-code-action@v1`).

## The flow

```
 owner: label the issue agent-ready   (or comment @claude on it)
                 │
                 ▼
 agent.yml   gate ──► queue (at most 3 agents at once) ──► build (one run per issue, 60 min)
             checks out main, branch issue-<n>-<slug>, runs /fix-issue <n>;
             Claude pushes and opens ONE pull request that says "Closes #n"; the run labels it agent-built
                 │
                 ▼
 agent-review.yml   on every push to a pull request: the evals, the reviewer agent, CI, the golden diff
             ──► one verdict comment and one label on the head
                 │
     ┌───────────┼─────────────────────────────┬───────────────────────────┐
 review: fix-first               review: approve                    review: needs-owner
 an agent-built PR goes back     docs/ and tests/ only:             the owner looks: the preview,
 to agent.yml for a fix round    auto-merge after CI                the workflows, a pending decision,
 on the same branch (at most     anything else: the merge batch,    or two fix rounds that did not
 two rounds, then needs-owner)   merged by the owner, /approve-pr   close the gaps
```

## Labels

| Label | Put on | By | Means |
| --- | --- | --- | --- |
| `agent-ready` | an issue | the owner | Start (or resume) an agent run on this issue. Removing it stops the run. |
| `agent-built` | a pull request | the builder | The agent opened it; the reviewer may send it back for fix rounds. |
| `review: approve` | a pull request | the reviewer | Every acceptance criterion shown, CI green, evals pass, no golden case weakened, nothing the owner must look at. In the merge batch. |
| `review: fix-first` | a pull request | the reviewer | Gaps to fix on this branch; the comment lists them. |
| `review: needs-owner` | a pull request | the reviewer | The owner's eyes or decision are needed; the comment says what. |
| `waiting-on-owner` | an issue | a session | The session asked the owner something in the issue (unchanged, [CLAUDE.md](../CLAUDE.md)). |

The review workflow creates the five agent labels on its first run if they are missing. A label is about one head: every push removes the `review:` label (and any auto-merge) until the new head is reviewed.

## What starts and stops a run

| Event | Effect |
| --- | --- |
| `agent-ready` added to an issue | A build: the issue's branch from `main`, or the one branch an earlier run left without a pull request. |
| `@claude` in a comment by the owner, on an issue | The same as the label. |
| `@claude` in a comment by the owner, on a pull request | A fix run on that pull request's branch, with the comment as the request. Anyone else's `@claude` does nothing. |
| `review: fix-first` on an `agent-built` pull request | The review workflow dispatches a fix round (`agent.yml` with `issue`, `pr`, `round`) on the same branch. |
| `agent-ready` removed | The issue's running build or fix round is cancelled; one still queued does nothing when it starts. |
| Actions → Agent → *Run workflow* | A build (`issue` only) or a fix run (`issue` and `pr`) by hand. |

**One run per issue.** The build job's concurrency group is the issue, so a second label waits behind the first run, and every run first checks for an open pull request that closes the issue or comes from an `issue-<n>-` branch. If there is one, the run refuses and says so on the issue: labelling an issue again never opens a second pull request. A branch whose pull request was closed is not reused; delete it to start the issue over.

**At most three at once.** The `queue` job waits until fewer than three earlier Agent runs are still going, first come first served. A single concurrency group cannot do this: GitHub runs one job per group and keeps only the newest waiting one, cancelling the rest, so one `agents` group would mean one agent at a time and would silently drop the issues labelled in between.

## The review verdict

The reviewer agent ([.claude/agents/reviewer.md](../.claude/agents/reviewer.md)) reads the issue and the diff and lists correctness gaps; it never chooses the label. The workflow measures everything else and `decide` in `agent.py` turns it into one verdict:

- **`review: fix-first`**: a gap the reviewer named; a golden case deleted, an `expect` key removed, a case's `mode` changed or a `max_ms` / `max_cost_usd` raised (checked against the merge base, whatever the pull request says); a failing `deterministic` check; failing `python3 evals/run.py`; or a branch that conflicts with `main`. An `agent-built` pull request whose issue still carries `agent-ready` goes back to the builder, at most **two** rounds; a third fix-first becomes `review: needs-owner`. Anyone else's pull request is labelled and left to its author.
- **`review: needs-owner`**: no linked issue (`Closes #n`); no reviewer verdict (no credential, or the run failed); CI that did not finish within 30 minutes; the issue labelled `waiting-on-owner`; or a diff touching what the owner looks at before merge: `src/investigator/web/`, `src/investigator/server.py`, the `Dockerfile`, `deploy/`, `.github/`, `.claude/` or `CLAUDE.md`.
- **`review: approve`**: none of the above.

The verdict comment starts with a hidden marker (`<!-- agent-review sha=… verdict=… round=… -->`) naming the head it judged. `python3 .github/scripts/agent.py status <pr>` reads the label and the newest marker together, the way `/review-pr` and `/approve-pr` do: `review: approve` only when both agree on the pull request's current head, `review: stale` when the head moved since.

## What merges itself

| Tier | Which pull requests | How they merge |
| --- | --- | --- |
| **auto** | every changed file is under `docs/` or `tests/`, or is `README.md` or `evals/README.md` | On `review: approve` the review workflow turns on GitHub auto-merge with a merge commit (no squash); GitHub merges once the required checks pass. |
| **owner** | anything else: `src/` (the tools, the planner, data generation, the cost model and money, `web/`), `evals/golden*.json`, the workflows, the skills, CLAUDE.md | The owner's merge word, `/approve-pr <n>`, which checks CI, the eval diff and the `review:` label first. |

Protected `main` keeps requiring the `deterministic` check, for both tiers. A merge that auto-merge makes is GitHub acting for the workflow's token, and GitHub starts no workflow from that, so the merge of an auto-tier pull request is not deployed on push; the nightly deploy of `main` ([DEPLOYMENT.md](DEPLOYMENT.md), "The nightly deploy") takes it the next weekday morning, and only the `/docs` pages and the changelog can differ in the meantime.

**The merge batch.** `/issues` lists the open pull requests labelled `review: approve` in merge order (the backlog order of the issue each closes, then the pull request number) with their tier. The coordinator's daily summary quotes that list, and the owner merges the owner-tier ones in that order.

## Caps and cost

- Builds: 60 minutes each (`timeout-minutes`), `--max-turns` 200 (`AGENT_MAX_TURNS`), at most three at once, one per issue.
- Reviews: 20 minutes for the reviewer job, `--max-turns` 40 (`REVIEW_MAX_TURNS`); one per head (a new push cancels the review of the old one).
- Fix rounds: at most two per pull request from the reviewer; the owner's own `@claude` fix runs are not counted.
- Each build and fix round writes its cost, turns and minutes to the run's job summary. The dollar cap is the key's spend limit; [DEPLOYMENT.md](DEPLOYMENT.md#agent-runs-caps-and-monthly-spend) has the monthly note.

## Set up once (the owner)

1. **The Claude GitHub App** on this repository ([github.com/apps/claude](https://github.com/apps/claude)). The agent pushes and opens its pull request as the app. That matters: GitHub starts no workflow from a push made with a workflow's own `GITHUB_TOKEN`, so Tests, Preview and the review would never run on the agent's work.
2. **One Anthropic credential**, as a repository secret: `ANTHROPIC_API_KEY`, a key used only by the agents with its own monthly spend limit in the Claude Console (not the demo's key, which stays in Secret Manager). Or `CLAUDE_CODE_OAUTH_TOKEN` (a subscription token from `claude setup-token`). The workflows learn only whether one exists, the two Claude Code steps alone receive it, and no value is in a workflow file or a log. (The action also documents keyless workload identity federation; it is not wired here, and would remove the secret the way #28 removed the deploy key.)
3. **Settings → General → Pull Requests → Allow auto-merge.**
4. **Protect `main`** (Settings → Branches or Rules) and require the status check `deterministic`, if it is not already.
5. **Workflow permissions.** Each job asks for exactly what it uses (`contents`, `pull-requests` and `issues: write` for the builder and the verdict, `actions: write` to dispatch a fix round, `id-token: write` for the app token). If the repository or organization caps the `GITHUB_TOKEN` at read, allow read and write under Settings → Actions → General.
6. **The `agent-ready` label**, if an issue is to be labelled before the review workflow has run once:

```bash
gh label create agent-ready --color 0e8a16 --description "Start an agent run on this issue (docs/AGENTS.md)"
```

Optional repository variables: `AGENT_MODEL` (passed as `--model`; unset uses Claude Code's default), `AGENT_MAX_TURNS`, `REVIEW_MAX_TURNS`.

## How to stop a run

- Remove `agent-ready` from the issue: the running build or fix round is cancelled, a queued one does nothing.
- Or cancel the run in the Actions tab (Agent → the run → *Cancel workflow*).
- To stop all agents: Actions → Agent → *Disable workflow* (and Agent review, to stop reviews). Nothing else depends on them.
- An open pull request is never touched by a new build; close it, and delete its branch to let the issue start over.

## What an agent can and cannot do

The builder runs with a fixed tool list (`--allowedTools` in `agent.yml`): reading and editing files, Python and Node (the tests and the evals), `git` without force pushes or rebases, and `gh` only to read issues and pull requests and to create and edit its own pull request. It cannot merge (`gh pr merge` and `gh api` are denied), cannot edit `.github/`, cannot reach the network with `curl`, and the project's `.claude/settings.json` deny list (force pushes, `gcloud` writes) applies as in any session. An issue that needs a workflow change is done in a session. The reviewer has a read-only token and can only read files and write its list of gaps; the review workflow, not the model, comments, labels and merges.

These are guard rails, not a sandbox: Python can do whatever the runner can. What actually bounds an agent is who can start one (the owner's label and comments only), protected `main`, the owner tier, and `review: needs-owner` on any change to `.github/`, `.claude/` or CLAUDE.md. The review workflow runs on `pull_request_target`, so its definition and scripts come from `main` and a pull request cannot rewrite the rules that review and merge it; the pull request's code runs only in the job that runs the evals, with no secret and a read-only token, and fork pull requests are never reviewed.

## Troubleshooting

- **No CI on the agent's commits, or no review of its pull request.** The push came from the workflow's `GITHUB_TOKEN`, not the app. Check that the Claude GitHub App is installed on the repository; the verdict then reads `review: needs-owner` with "CI did not pass on this head (missing)".
- **The run fails at "Check that an Anthropic credential is configured".** Set one of the credentials in step 2.
- **The verdict warns "auto-merge not enabled".** Allow auto-merge (step 3) and protect `main` (step 4).
- **The run refused.** Its comment on the issue says why: an open pull request already closes the issue, or a branch from an earlier pull request is in the way.
- **A run ended without a pull request.** Its comment links the run; label the issue again to resume on the same branch.
