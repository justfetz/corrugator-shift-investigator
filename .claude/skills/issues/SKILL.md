---
name: issues
description: The ordered backlog in one screen - every open issue with a computed status (ready, blocked by, in PR, waiting on owner), the next three to start, and what the owner owes
---
Show the backlog of justfetz/corrugator-shift-investigator. Read-only; edit no issue.

1. Run `python3 .claude/skills/issues/backlog.py` and show its table as is. It sorts by the order prefix in the title (`04 ·`, `08c ·`), and computes the status: `in PR #n` (an open PR says `Closes #n`), `blocked by #m` (the body says "after #m" / "depends on #m" / "after 08c" and #m is open), `waiting on owner` (the `waiting-on-owner` label), else `ready`.
2. Under the table, say in one line each why the first three `ready` issues are the ones to start, in order, and name any `ready` issue that should wait anyway (a design decision pending on the epic #44, a dependency the body does not state). Take the reason from the issue text, not from memory.
3. If the owner asked about a specific issue, `gh issue view <n>` and quote its acceptance criteria.

4. Under that, the script prints **the merge batch**: the open pull requests labelled `review: approve` in merge order (the backlog order of the issue each closes, then the pull request number), each with its merge tier (`auto`: docs and tests only, auto-merges after CI; `owner`: waits for `/approve-pr <n>`). The coordinator's daily summary quotes this list as is.

Sessions put the `waiting-on-owner` label on an issue when they ask the owner something in it; the owner removes it when answered. `--json` prints the rows and the merge batch (`{"issues": [...], "merge_batch": [...]}`) for other tools.
