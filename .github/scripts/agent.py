"""The agents' rules (issue #126), in one place for the two workflows and the skills that read them.

`.github/workflows/agent.yml` (the builder) and `.github/workflows/agent-review.yml` (the reviewer)
call this script for every decision that is not the model's: which events start a run, which branch
a run works on and when it refuses, the queue that keeps at most three builders running, the review
verdict and its `review:` label, the fix rounds, and the merge tier. The `/review-pr`, `/approve-pr`
and `/issues` skills read the verdict with the same functions (`status`), so a label means the same
thing in a workflow, a session and the backlog.

Everything above `# I/O` is pure and tested offline in tests/test_agent_workflows.py. Below it, the
subcommands talk to GitHub through `gh api` (the runner's `gh`, authenticated by GH_TOKEN) and to the
workflow through $GITHUB_OUTPUT. Nothing here reads or prints a secret.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import posixpath
import urllib.parse
from pathlib import Path

REPO = os.environ.get('GITHUB_REPOSITORY') or 'justfetz/corrugator-shift-investigator'
READY = 'agent-ready'            # the owner's label that starts a builder run on an issue
BUILT = 'agent-built'            # put on a pull request the builder opened; only these get fix rounds
WAITING = 'waiting-on-owner'     # a session asked the owner something in the issue
VERDICTS = ('approve', 'fix-first', 'needs-owner')
LABELS = {v: f'review: {v}' for v in VERDICTS}
LABEL_COLORS = {READY: '0e8a16', BUILT: 'c5def5', LABELS['approve']: '2ea44f',
                LABELS['fix-first']: 'd93f0b', LABELS['needs-owner']: 'fbca04'}
LABEL_TEXT = {READY: 'Start an agent run on this issue (docs/AGENTS.md)',
              BUILT: 'Opened by the agent workflow; the reviewer may send it back for fixes',
              LABELS['approve']: 'Reviewer: every criterion shown, CI green; in the merge batch',
              LABELS['fix-first']: 'Reviewer: gaps to fix on this branch before merge',
              LABELS['needs-owner']: "Reviewer: the owner's eyes or decision are needed"}
MAX_ROUNDS = 2                   # fix rounds the reviewer may send to the builder before it asks the owner
MAX_AGENTS = 3                   # builder runs past the queue at once
RUNNING = ('in_progress', 'queued')   # the run statuses the queue counts (one listing each per poll)
CI_CHECK = 'deterministic'       # the job of tests.yml that protected main requires
REVIEW_BOT = 'github-actions[bot]'   # the author of verdict comments and of the reviewer's fix-round dispatches
# The verdict also lands as a commit status on the head it judged. The builder's own token (the agent's
# pushes come from it, docs/AGENTS.md) can write comments as REVIEW_BOT, but it is never granted
# `statuses: write`, so `status` believes a verdict only when the status agrees with the comment.
REVIEW_CONTEXT = 'agent-review'
REVIEW_STATE = {'approve': 'success', 'fix-first': 'failure', 'needs-owner': 'pending'}
STALE = 'stale: '                # a fix round's refusal that is expected (a newer head or review): logged, not commented
REVIEWING = 'reviewing: the head, the body or the base changed'   # the `reset` job's pending status, before a new verdict
BASE = 'main'                    # the only base a pull request is reviewed and merged against
# The ruleset rules that refuse a direct push to BASE (N2), as `protection_state` names them.
PUSH_BLOCKING = {'pull_request': 'requires a pull request', 'update': 'restricts updates'}
# The auto-merge tier: a pull request whose every file is one of these merges itself once the reviewer
# labels it `review: approve` and CI is green. Anything else waits for the owner's /approve-pr.
AUTO_MERGE_DIRS = ('docs/', 'tests/')
AUTO_MERGE_FILES = ('README.md', 'evals/README.md')
# Paths the owner looks at before an approve: what a visitor sees (the preview), the workflows and the
# container, and the rules the agents follow: a CLAUDE.md or a .claude/ folder at ANY depth, since Claude
# Code reads a nested CLAUDE.md as instructions. A diff touching one is `review: needs-owner` at best.
OWNER_EYES_DIRS = ('src/investigator/web/', '.github/', '.claude/', 'deploy/')
OWNER_EYES_FILES = ('src/investigator/server.py', 'Dockerfile', 'CLAUDE.md')
OWNER_EYES_NAMES = ('CLAUDE.md', 'CLAUDE.local.md')
OUTSIDE = ('/', '../')            # a symlink's target outside the repository (L5): always the owner's eyes
GOLDEN_FILES = ('evals/golden.json', 'evals/golden_values.json')
FILES_API_CAP = 3000              # GitHub's pull request files listing stops here (N4)
CUT_SHORT = '(the files API lists 3,000 files at most; there may be more)'   # not in the auto tier
LOOSENED_UP = ('max_ms', 'max_cost_usd')   # a golden budget that may only go down
# What "closes" means everywhere: .claude/skills/issues/backlog.py imports this pattern.
CLOSES = re.compile(r'\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)', re.I)
# A comment asks for the agent only with a line that starts with the mention (N1): see `mentions_claude`.
MENTION = re.compile(r'@claude(?![\w-]|\.\w)', re.I)
# A fence opens with three backticks or tildes, indented three spaces at most; a backtick fence's info string has no
# backtick (CommonMark: "```x`" is a paragraph line, not a fence).
FENCE = re.compile(r' {0,3}(`{3,}(?=[^`]*$)|~{3,})')
# What a `>` quote looks like at a line's start, inside list items too (`- > quoted`, `1. > quoted`, nested and
# indented ones): an indented code line that starts with `>` counts as well, which only means a missed start.
QUOTE = re.compile(r'[ \t]*(?:(?:[-+*]|\d{1,9}[.)])[ \t]+)*>')
# Raw HTML that can run on across blank lines (CommonMark HTML blocks 1 to 5): a mention inside one starts nothing.
# Each kind is matched on its own and the regions are joined: one alternation takes non-overlapping matches, so an
# earlier inline `<!--` or `<script>` could swallow the opening tag of a later block and hide that block's region.
HTML_REGIONS = [re.compile(p, re.S | re.I) for p in (
    r'<!--.*?(?:-->|\Z)', r'<\?.*?(?:\?>|\Z)', r'<![A-Za-z].*?(?:>|\Z)', r'<!\[CDATA\[.*?(?:\]\]>|\Z)',
    r'<(script|pre|style|textarea)\b.*?(?:</\1\s*>|\Z)')]
HIDDEN = '\0'       # what a fenced line becomes: no backtick left, and not blank
AFTER_CR = '\x01'   # opens a line that a bare carriage return began: a line for Markdown, never one the mention starts
ORDER = re.compile(r'^\s*(\d{1,2})([a-z]?)\s*·')
# Keys and tokens a model or a pull request's output could echo; scrubbed from everything the verdict posts. A PEM
# block goes whole, from its BEGIN line to its END line (or to the end of the text when it has none), and an AWS
# access key id (`AKIA…`, or `ASIA…` for a temporary one) by its fixed shape.
SECRET = re.compile(r'-----BEGIN ([A-Z0-9 ]+)-----(?:.*?-----END \1-----|.*)'
                    r'|sk-ant-[A-Za-z0-9_-]{6,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}'
                    r'|(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])', re.S)
MAX_GAPS, GAP_CHARS, WHERE_CHARS = 20, 400, 120
MARKER = re.compile(r'<!-- agent-review sha=([0-9a-f]{7,40}) verdict=([a-z-]+) round=(\d+)(?: kind=([a-z]+))?(?: issue=(\d+))? -->')
FOOTER = '\n\n---\n_Generated by [Claude Code](https://claude.ai/code)_\n'


# ---------------------------------------------------------------------------------------------- rules

def slug(title: str, limit: int = 40) -> str:
    """A branch-safe slug of an issue title: lower-case words joined by hyphens, cut at a word boundary."""
    words = re.findall(r'[a-z0-9]+', ORDER.sub('', title or '').lower())
    out = ''
    for word in words:
        longer = f'{out}-{word}' if out else word
        if len(longer) > limit:
            break
        out = longer
    return out or (words[0][:limit] if words else 'work')


def branch_name(number: int, title: str) -> str:
    return f'issue-{number}-{slug(title)}'


def linked_issues(body: str | None) -> list[int]:
    """Every issue a pull request body closes, in order, without repeats."""
    seen = []
    for m in CLOSES.finditer(body or ''):
        if int(m.group(1)) not in seen:
            seen.append(int(m.group(1)))
    return seen


def linked_issue(body: str | None) -> int | None:
    found = linked_issues(body)
    return found[0] if found else None


def closes_its_spans(line: str) -> bool:
    """Whether every run of backticks on `line` opens a code span that closes on the same line, read the way
    CommonMark reads them (a backslash-escaped character opens nothing; a span ends at the next run of the same
    length; inside a span a backslash is literal)."""
    i, n = 0, len(line)
    while i < n:
        if line[i] == '\\' and i + 1 < n and not line[i + 1].isalnum() and not line[i + 1].isspace():
            i += 2
        elif line[i] == '`':
            j = i
            while j < n and line[j] == '`':
                j += 1
            close = re.compile(f'(?<!`){"`" * (j - i)}(?!`)').search(line, j)
            if not close:
                return False
            i = close.end()
        else:
            i += 1
    return True


def mentions_claude(body: str | None) -> bool:
    """Whether a comment asks for the agent (N1): a line that starts with `@claude`, in column one, outside fenced
    code, inline code, raw HTML and `>` quotes. The coordinator's reviews and the sessions' replies are posted as the
    owner and quote the mention in backticks, in code blocks and in quotes; none of those starts anything.

    The rule is built so that it never starts a run CommonMark shows inside code or a quote; in doubt it starts
    nothing (a missed start costs the owner a rephrased comment, an unwanted one an agent run):
    - fenced blocks are dropped first (a backtick fence's info string has no backtick). A fence that opens indented
      (as inside a list item), below a `<` line of its block (inside an HTML block) or inside raw HTML may belong to a
      container CommonMark closes on its own, and the tracking would fall out of step: nothing after one counts;
    - a line that opens a quote (inside a list item too) quotes every line after it up to a blank line, as
      Markdown's lazy continuation does;
    - a code span cannot cross a blank line, so it can reach the mention's line only from an earlier line of the
      same blank-line-delimited block: the mention counts only when every earlier line of its block closes all its
      own code spans and holds no `<` (raw HTML and autolinks outrank code spans), and when no HTML comment,
      processing instruction, declaration, CDATA section or `<pre>`-like block left open earlier covers it;
    - a bare carriage return breaks a line for the fences and the quotes, as in Markdown, but the mention counts only
      on a line that opens the comment or follows a newline, as agent.yml's condition reads it."""
    kept, fence, openers, lt = [], '', [], False   # openers: (line, opened indented or below a `<` line of its block)
    for line in (body or '').replace('\r\n', '\n').replace('\r', '\n' + AFTER_CR).split('\n'):
        bare = line.lstrip(AFTER_CR)
        m = FENCE.match(bare)
        if fence:   # inside a fenced block until a fence of the same character, at least as long, alone on its line
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not bare[m.end():].strip():
                fence = ''
            kept.append(HIDDEN)
        elif m:
            fence = m.group(1)
            openers.append((len(kept), bare[:1] in ' \t' or lt))
            kept.append(HIDDEN)
        else:
            lt = bool(bare.strip()) and (lt or '<' in bare)
            kept.append(line)
    text = '\n'.join(kept)
    starts = [0]
    for line in kept[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    regions = [(r.start(), r.end()) for pattern in HTML_REGIONS for r in pattern.finditer(text)]
    # Past the first fence whose container is in doubt, the fences may be out of step with Markdown's: nothing counts.
    end = next((n for n, doubtful in openers if doubtful or any(s < starts[n] < e for s, e in regions)), len(kept))
    quoted, clean = False, True   # clean: every earlier line of this block closes its spans and has no `<`
    for line, at in zip(kept[:end], starts):
        bare = line.lstrip(AFTER_CR)
        if not bare.strip():
            quoted, clean = False, True
            continue
        if QUOTE.match(bare):
            quoted = True
        elif (not quoted and clean and MENTION.match(line)   # never on a line a bare carriage return began
              and not any(start < at < end for start, end in regions)):
            return True
        clean = clean and '<' not in bare and closes_its_spans(bare)
    return False


def labels_of(item: dict) -> list[str]:
    return [label['name'] if isinstance(label, dict) else label for label in item.get('labels') or []]


def event_plan(name: str, event: dict, owner: str) -> dict:
    """What one workflow event asks of the builder. Modes: `build` (start or resume an issue), `fix` (a
    round on a pull request's branch), `stop` (the label was removed: cancel the issue's run) and `skip`.

    Only the owner starts work (H3): by labelling an issue `agent-ready`, or by a comment with a line that
    starts with `@claude` (`mentions_claude`; on an issue: the same as the label, which the gate then puts on
    the issue; on a pull request: a fix run with the comment as the request), or by dispatching the workflow
    by hand. The reviewer starts a fix round by dispatching it with `pr`, a `round` of 1 or more and the id
    of the verdict comment that sent the head back (N3); that dispatch comes from REVIEW_BOT, which may
    start nothing else. Anyone who can remove the label may stop a run: stopping is the safe direction.
    agent.yml's `gate` condition says the same, more loosely (it cannot read Markdown)."""
    skip = {'mode': 'skip', 'issue': '', 'pr': '', 'round': '0', 'verdict': '', 'needs_label': 'false', 'request': '',
            'add_label': 'false'}
    sender = (event.get('sender') or {}).get('login') or ''
    if name == 'issues':
        issue = event.get('issue') or {}
        if (event.get('label') or {}).get('name') != READY or 'pull_request' in issue:
            return {**skip, 'why': f'not the {READY} label on an issue'}
        mode = {'labeled': 'build', 'unlabeled': 'stop'}.get(event.get('action'))
        if not mode:
            return {**skip, 'why': f"action {event.get('action')!r}"}
        if mode == 'build' and sender != owner:
            return {**skip, 'why': f'only the owner\'s {READY} label starts a run, not {sender or "an unknown sender"}\'s'}
        return {**skip, 'mode': mode, 'issue': str(issue['number']), 'needs_label': 'true'}
    if name == 'issue_comment':
        comment = event.get('comment') or {}
        issue = event.get('issue') or {}
        if event.get('action') != 'created':
            return {**skip, 'why': 'not a new comment'}
        if (comment.get('user') or {}).get('login') != owner:
            return {**skip, 'why': 'only the owner starts the agent from a comment'}
        if not mentions_claude(comment.get('body')):
            return {**skip, 'why': 'no line of the comment starts with @claude (outside code and quotes)'}
        if 'pull_request' in issue:
            return {**skip, 'mode': 'fix', 'pr': str(issue['number']), 'request': comment.get('body') or ''}
        return {**skip, 'mode': 'build', 'issue': str(issue['number']), 'needs_label': 'true', 'add_label': 'true'}
    if name == 'workflow_dispatch':
        inputs = event.get('inputs') or {}
        issue = str(inputs.get('issue') or '').strip().lstrip('#')
        pr = str(inputs.get('pr') or '').strip().lstrip('#')
        rnd = str(inputs.get('round') or '0').strip() or '0'
        verdict = str(inputs.get('verdict') or '').strip()
        if not issue.isdigit() or (pr and not pr.isdigit()) or not rnd.isdigit() or (verdict and not verdict.isdigit()):
            return {**skip, 'why': 'issue, pr, round and verdict must be numbers'}
        if sender == REVIEW_BOT:
            if not pr or int(rnd) < 1 or not verdict:
                return {**skip, 'why': f'{REVIEW_BOT} dispatches only fix rounds (a pr, a round of 1 or more and its verdict comment)'}
        elif sender != owner:
            return {**skip, 'why': f'only the owner or the review workflow dispatches the agent, not {sender or "an unknown sender"}'}
        if pr:
            return {**skip, 'mode': 'fix', 'issue': issue, 'pr': pr, 'round': rnd, 'verdict': verdict, 'needs_label': 'true'}
        return {**skip, 'mode': 'build', 'issue': issue, 'needs_label': 'true'}
    return {**skip, 'why': f'event {name}'}


def concurrency_key(issue: str, pr: str) -> str:
    """The builder's concurrency group: one run per issue (a fix round on its pull request included)."""
    return f'issue-{issue}' if issue else f'pr-{pr}'


def plan_branch(number: int, title: str, open_pulls: list, branches: list, closed_pulls: list = ()) -> dict:
    """Where a build of issue `number` works, or why it refuses. One pull request per issue: an open pull
    request that closes the issue (or comes from its `issue-<n>-` branch) refuses the run; one existing
    `issue-<n>-` branch without a pull request is resumed (an earlier run stopped before opening it); a
    branch whose pull request was closed is not reused."""
    prefix = f'issue-{number}-'
    for pr in open_pulls:
        if number in linked_issues(pr.get('body')) or pr['head']['ref'].startswith(prefix):
            return {'action': 'refuse', 'branch': pr['head']['ref'], 'pr': pr['number'],
                    'reason': f'issue #{number} is already in PR #{pr["number"]} (`{pr["head"]["ref"]}`); '
                              f'comment `@claude` on that pull request to change it'}
    mine = sorted(b for b in branches if b.startswith(prefix))
    if not mine:
        return {'action': 'create', 'branch': branch_name(number, title), 'reason': 'new branch from main'}
    if len(mine) > 1:
        return {'action': 'refuse', 'branch': '', 'reason': f'issue #{number} has {len(mine)} branches '
                f'({", ".join(f"`{b}`" for b in mine)}); delete all but one, then label it again'}
    used = [pr for pr in closed_pulls if pr['head']['ref'] == mine[0]]
    if used:
        return {'action': 'refuse', 'branch': mine[0], 'reason': f'`{mine[0]}` already had PR #{used[0]["number"]}, '
                f'now closed; delete the branch to start issue #{number} over'}
    return {'action': 'resume', 'branch': mine[0], 'reason': 'an earlier run stopped before its pull request'}


def queue_ahead(runs: list, me: int) -> list[int]:
    """The Agent runs ahead of run `me` in the queue: earlier runs (run ids only grow) not yet completed.
    A run starts building when fewer than MAX_AGENTS are ahead of it, so at most MAX_AGENTS build at once,
    first come first served. (A single GitHub concurrency group would run one at a time and cancel all but
    the newest waiting run.) Runs that skip at the gate or only stop a build complete within seconds, so
    counting them while they last delays a start by one poll at most."""
    return sorted({r['id'] for r in runs if r['id'] < me and r.get('status') != 'completed'})


def in_auto_tier(path: str) -> bool:
    return path.startswith(AUTO_MERGE_DIRS) or path in AUTO_MERGE_FILES


def tier(files) -> str:
    """`auto` when every file is documentation or a test; `owner` otherwise (and for an empty diff)."""
    files = list(files)
    return 'auto' if files and all(in_auto_tier(f) for f in files) else 'owner'


def owner_eyes(files) -> list[str]:
    """The paths the owner looks at. A path is also read as a folder (`f + '/'`), so a symlink whose target is
    `.claude` or `.github` itself counts like a file inside it."""
    return [f for f in files if f'{f}/'.startswith(OWNER_EYES_DIRS) or f in OWNER_EYES_FILES
            or f.rsplit('/', 1)[-1] in OWNER_EYES_NAMES or '/.claude/' in f'/{f}/' or f == '..' or f.startswith(OUTSIDE)]


def name_status(raw: str) -> tuple[list[str], list[str]]:
    """(every path, the deleted paths) a diff touches, from `git diff --name-status -z -M base...head` (N4: the
    pull request files API stops at 3,000 files, git does not). Records are `<status>` NUL `<path>` NUL, and for a
    rename or a copy `<status>` NUL `<old path>` NUL `<new path>` NUL. A renamed file counts by both paths, so
    moving `.claude/settings.json` to `docs/` is not a docs-only change; a rename is not a deletion."""
    parts = raw.split('\0')
    paths, removed, i = [], [], 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        names = [n for n in parts[i + 1:i + (3 if status[:1] in 'RC' else 2)] if n]
        paths += [n for n in names if n not in paths]
        if status[:1] == 'D' and names:
            removed.append(names[0])
        i += 3 if status[:1] in 'RC' else 2
    return paths, removed


def link_target(path: str, target: str) -> str:
    """Where a symlink at `path` points, as a path from the repository root (L5): a link is classified by
    its target, so `docs/x -> ../src/investigator/agent.py` is a `src/` change. A target outside the
    repository keeps a leading `/` or `../`, which OUTSIDE puts in front of the owner."""
    if target.startswith('/'):
        return '/' + target.lstrip('/')
    return posixpath.normpath(posixpath.join(posixpath.dirname(path), target))


def symlink_entries(raw: str) -> list[tuple[str, str]]:
    """(path, blob) of every symlink a diff adds or changes, from `git diff --raw -z --no-abbrev --no-renames`:
    records of `:<old mode> <new mode> <old blob> <new blob> <status>` NUL `<path>` NUL."""
    parts = raw.split('\0')
    out = []
    for head, path in zip(parts[0::2], parts[1::2]):
        fields = head.lstrip(':').split()
        if len(fields) == 5 and fields[1] == '120000' and fields[4] != 'D':
            out.append((path, fields[3]))
    return out


def protection_state(branch: dict, rules: list) -> tuple[bool, str]:
    """Whether `main` refuses a direct push (H1): only a ruleset rule that requires a pull request or restricts
    updates counts (N2). A rule that only requires status checks does not: GitHub takes a direct push of a commit
    whose required checks already passed, and `verify` runs Tests on every agent head. Classic branch protection
    does not count either: `GET branches/main` shows a read token its required checks, never whether it requires a
    pull request, so it is named in the reason only (`GET rules/branches/main` lists rulesets, not classic rules)."""
    kinds = sorted({r.get('type') for r in rules or [] if isinstance(r, dict) and r.get('type')})
    blocking = [PUSH_BLOCKING[k] for k in kinds if k in PUSH_BLOCKING]
    if blocking:
        return True, f'a ruleset on `{BASE}` {" and ".join(blocking)}'
    classic = (' Classic branch protection is on, but a read token cannot see whether it requires a pull request;'
               ' add the ruleset.' if (branch or {}).get('protected') else '')
    return False, (f'no ruleset on `{BASE}` requires a pull request or restricts updates'
                   + (f' (its rules: {", ".join(kinds)})' if kinds else '') + '.' + classic)


def collected_count(text: str | None) -> int | None:
    """The number of tests `pytest --collect-only -q` reported; None without its output, 0 when it printed no
    count (a collection that crashed or found nothing)."""
    if text is None:
        return None
    # `2/3 tests collected (1 deselected)`: the first number is what runs, so a deselection lowers the count.
    m = re.findall(r'(\d+)(?:/\d+)? tests? collected|collected (\d+) items?', text)
    return int(next(g for g in m[-1] if g)) if m else 0


def scrub(text, limit: int) -> str:
    """Text the verdict posts that a model or a pull request's code wrote: one line, no key or token (M1), no
    HTML comment that could pass for a verdict marker, at most `limit` characters."""
    text = SECRET.sub('[redacted]', ' '.join(str(text or '').split()))
    text = text.replace('<!--', '&lt;!--')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def clean_gaps(gaps: list) -> list[dict]:
    """The reviewer's gaps as the verdict comment may post them: scrubbed, capped in length and in number."""
    out = [{'where': scrub(g.get('where'), WHERE_CHARS), 'gap': scrub(g.get('gap'), GAP_CHARS)} for g in gaps]
    if len(out) > MAX_GAPS:
        out = out[:MAX_GAPS - 1] + [{'where': 'diff', 'gap': f'{len(out) - MAX_GAPS + 1} more gaps not shown (see the run)'}]
    return out


def changed_paths(api_files: list) -> list[str]:
    """Every path a pull request touches, from GitHub's files listing, for `status` and `/issues`, which show the
    tier (the verdict reads git, `name_status`): a renamed file counts by its old path too, so moving
    `.claude/settings.json` to `docs/` is not a docs-only change. A listing of FILES_API_CAP files may be cut
    short, so it gets a path outside the auto tier and shows as the owner's."""
    out = []
    for f in api_files:
        for path in (f.get('filename'), f.get('previous_filename')):
            if path and path not in out:
                out.append(path)
    return out + ([CUT_SHORT] if len(api_files) >= FILES_API_CAP else [])


def golden_cases(text: str) -> dict:
    """A golden file's cases by id; raises ValueError when the file is not a golden file."""
    data = json.loads(text)
    cases = data.get('cases') if isinstance(data, dict) else None
    if not isinstance(cases, list) or not all(isinstance(c, dict) and 'id' in c for c in cases):
        raise ValueError('no list of cases with ids')
    return {c['id']: c for c in cases}


def golden_problems(base_text: str | None, head_text: str | None, name: str) -> list[str]:
    """A golden file weakened between main and the head: a case deleted, an `expect` key (or a key of a
    dict-valued one, such as a value case's `values`) removed, a case's `mode` changed, or a budget
    (`max_ms`, `max_cost_usd`) raised. New cases and new keys are fine."""
    if not base_text:
        return []
    if not head_text:
        return [f'{name} was deleted']
    try:
        base = golden_cases(base_text)
    except ValueError:
        return []   # main's own file is broken: nothing to compare against
    try:
        head = golden_cases(head_text)
    except ValueError as err:
        return [f'{name} is not a readable golden file on this head ({str(err)[:120]})']
    out = [f'{name}: case `{cid}` was deleted' for cid in base if cid not in head]
    for cid, old in base.items():
        new = head.get(cid)
        if new is None:
            continue
        if old.get('mode') != new.get('mode'):
            out.append(f'{name}: case `{cid}` changed mode from {old.get("mode")} to {new.get("mode")}')
        was, now = old.get('expect') or {}, new.get('expect') or {}
        for key in was:
            if key not in now:
                out.append(f'{name}: case `{cid}` lost `expect.{key}`')
            elif isinstance(was[key], dict) and isinstance(now[key], dict):
                out += [f'{name}: case `{cid}` lost `expect.{key}.{sub}`' for sub in was[key] if sub not in now[key]]
        for key in LOOSENED_UP:
            if isinstance(was.get(key), (int, float)) and isinstance(now.get(key), (int, float)) and now[key] > was[key]:
                out.append(f'{name}: case `{cid}` raised `expect.{key}` from {was[key]} to {now[key]}')
    return out


def golden_changes(base_text: str | None, head_text: str | None, name: str) -> list[str]:
    """Every other change to an existing case (a value, a list, a pattern, the question): not a failure by
    itself (an intended generator change regenerates the values; a wrong expectation gets fixed), so it is
    listed for the reviewer to judge against the issue."""
    if not base_text or not head_text:
        return []
    try:
        base, head = golden_cases(base_text), golden_cases(head_text)
    except ValueError:
        return []   # golden_problems reports an unreadable head
    out = []
    for cid, old in base.items():
        new = head.get(cid)
        if new is None:
            continue
        for key in sorted(set(old) | set(new)):
            if key in ('expect', 'mode') or old.get(key) == new.get(key):
                continue
            out.append(f'{name}: case `{cid}` changed `{key}`')
        was, now = old.get('expect') or {}, new.get('expect') or {}
        for key in sorted(set(was) & set(now)):
            if was[key] != now[key]:
                out.append(f'{name}: case `{cid}` changed `expect.{key}`: {json.dumps(was[key])[:200]} -> {json.dumps(now[key])[:200]}')
        # a key the case did not have: often a tighter check, but a budget (`max_ms`) above the default loosens it
        out += [f'{name}: case `{cid}` added `expect.{key}`: {json.dumps(now[key])[:200]}' for key in sorted(set(now) - set(was))]
    return out


def decide(*, issue, issue_labels=(), reviewer=True, gaps=(), golden=(), ci='success', ci_detail='',
           evals='pass', evals_detail='', files=(), rounds=0, agent_built=False, conflicts=False,
           base=BASE, removed=(), collected=None, baseline=None, auto_merge=False) -> dict:
    """The review verdict, its label and what happens next. The model contributes only `gaps` (and
    `reviewer=False` when it wrote no verdict); every other input is a fact the workflow measured.

    - fix-first: the reviewer's gaps, a weakened golden, a deleted file, fewer tests collected than on the
      merge base, failing CI, failing evals, or a branch that conflicts with main. The deleted file and
      the test count are rules, not the model's judgement (M3). An agent-built pull request goes back to
      the builder (`next: fix`) until MAX_ROUNDS rounds were spent; then needs-owner. Anyone else's pull
      request is labelled and left to its author.
    - needs-owner, of two kinds. `eyes`: the issue waits on the owner, or the diff is in OWNER_EYES (what
      a visitor sees, the workflows, the agents' rules, a symlink out of the repository); the owner looks,
      then may merge. `unresolved`: a base other than main, no linked issue, no reviewer verdict, CI,
      evals or the test count that did not finish, or MAX_ROUNDS fix rounds that left gaps; nothing is
      approved until it is resolved.
    - approve: none of the above. In the auto tier (docs and tests only) `next: auto-merge` when the
      owner turned the tier on (`auto_merge`, the repository variable AGENT_AUTO_MERGE); otherwise the
      pull request joins the owner's merge batch."""
    def verdict(name, reasons, nxt=None, kind=''):
        return {'verdict': name, 'label': LABELS[name], 'reasons': list(reasons), 'next': nxt, 'tier': tier(files), 'kind': kind}
    if base != BASE:
        return verdict('needs-owner', [f'the pull request targets `{base}`, not `{BASE}`: the review, the golden check and the merge '
                                       f'tier are for `{BASE}` only'], kind='unresolved')
    if issue is None:
        return verdict('needs-owner', ['the pull request names no issue (`Closes #n`), so nothing defines done'], kind='unresolved')
    fix = [f"{g.get('where') or 'diff'}: {g.get('gap')}" for g in gaps]
    fix += list(golden)
    fix += [f'deletes `{path}`: a removed file is fix-first by rule (when the issue asks for the deletion, the owner merges it '
            'with `/approve-pr` and "approve anyway")' for path in removed]
    if isinstance(collected, int) and isinstance(baseline, int) and 0 < baseline and collected < baseline:
        fix.append(f'collects {collected} tests, {baseline - collected} fewer than its merge base ({baseline}): a test was removed '
                   'or is no longer collected (when the issue asks for that, the owner merges it with `/approve-pr` and "approve anyway")'
                   if collected else
                   f'pytest collected no tests on this head (the merge base collects {baseline}): see the evals job')
    if conflicts:
        fix.append('the branch conflicts with main: `git merge origin/main`, resolve, push (never rebase)')
    if ci == 'failure':
        fix.append(f'CI failed on this head: {ci_detail or CI_CHECK}')
    if evals == 'fail':
        fix.append(f'`python3 evals/run.py` failed on this head: {evals_detail or "see the run"}')
    if fix:
        if agent_built and rounds >= MAX_ROUNDS:
            return verdict('needs-owner', [f'{MAX_ROUNDS} fix rounds did not close these gaps:', *fix], kind='unresolved')
        return verdict('fix-first', fix, 'fix' if agent_built else None)
    if not reviewer:
        return verdict('needs-owner', ['the reviewer wrote no verdict (no Anthropic credential, or the run failed: see its log)'],
                       kind='unresolved')
    if ci != 'success':
        return verdict('needs-owner', [f'CI did not pass on this head ({ci}{": " + ci_detail if ci_detail else ""})'], kind='unresolved')
    if evals != 'pass':
        return verdict('needs-owner', ['the evals did not run on this head'], kind='unresolved')
    if not isinstance(collected, int) or not isinstance(baseline, int) or baseline <= 0:
        return verdict('needs-owner', ['the test count was not measured on this head and its merge base '
                                       f'(head: {collected if collected is not None else "none"}, merge base: '
                                       f'{baseline if baseline is not None else "none"})'], kind='unresolved')
    if WAITING in issue_labels:
        return verdict('needs-owner', [f'issue #{issue} is labelled `{WAITING}`: a decision is pending there'], kind='eyes')
    eyes = owner_eyes(files)
    if eyes:
        shown = ', '.join(f'`{f}`' for f in eyes[:6]) + (f' and {len(eyes) - 6} more' if len(eyes) > 6 else '')
        return verdict('needs-owner', [f'touches {shown}: the owner looks before merge (the preview for `web/`, '
                                       'the workflows, the container, the agents\' rules)'], kind='eyes')
    return verdict('approve', ['every acceptance criterion is shown, CI is green, the evals pass and no golden case is weakened'],
                   'auto-merge' if tier(files) == 'auto' and auto_merge else None)


def review_marks(comments: list) -> list[dict]:
    """The verdict comments the review workflow wrote, oldest first: sha, verdict, round."""
    out = []
    for c in comments:
        if (c.get('user') or {}).get('login') != REVIEW_BOT:
            continue
        m = MARKER.search(c.get('body') or '')
        if m:
            out.append({'sha': m.group(1), 'verdict': m.group(2), 'round': int(m.group(3)), 'kind': m.group(4) or '',
                        'issue': int(m.group(5)) if m.group(5) else None, 'id': c.get('id'), 'body': c.get('body') or ''})
    return out


def rounds_spent(comments: list) -> int:
    return max([m['round'] for m in review_marks(comments)] or [0])


def round_for_head(comments: list, sha: str) -> int:
    """The fix round this head was already sent back as, or 0. A second review of the same head (a re-run,
    a reopen) must not spend another round: only a new push by the builder earns the next one."""
    return max([m['round'] for m in review_marks(comments) if m['verdict'] == 'fix-first' and m['sha'] == sha] or [0])


def gaps_of(body: str) -> str:
    """The numbered gaps of a verdict comment: the text the builder's fix round works from."""
    lines = []
    for line in body.splitlines():
        if re.match(r'^\d+\. ', line):
            lines.append(line)
    return '\n'.join(lines)


def gaps_digest(gaps: str) -> str:
    """A short digest of a verdict comment's gaps (`gaps_of`), which the review workflow writes into the head's
    `agent-review` commit status next to a fix-first verdict (N3). The builder's token can write and edit comments
    as REVIEW_BOT but never a status, so a fix round believes a comment's gaps only when a status carries their digest."""
    text = '\n'.join(line.strip() for line in (gaps or '').splitlines() if line.strip())
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def verdict_for_round(comment, statuses: list, *, number: int, sha: str, rnd: int) -> tuple[dict | None, str]:
    """The verdict comment a fix round was dispatched for, or why it does not send this head back (N3). Only the
    comment whose id the review workflow put in the dispatch is read, never the newest marker on the pull request
    (any token with `issues: write` can post one as REVIEW_BOT). It must be REVIEW_BOT's, on this pull request, a
    fix-first marker for the current head and this round; the head's newest `agent-review` verdict status (a `reset`'s
    pending one skipped) must say fix-first, and one of the review workflow's fix-first statuses on the head must carry
    the digest of this comment's gaps, so an edited or a forged comment sends nothing. A reason that starts with STALE
    is expected (a newer head or verdict) and said quietly; any other is said on the pull request."""
    if not isinstance(comment, dict) or not isinstance(comment.get('body'), str):
        return None, 'the verdict comment the dispatch names could not be read'
    if (comment.get('user') or {}).get('login') != REVIEW_BOT:
        return None, f'the comment the dispatch names was not written by {REVIEW_BOT}'
    if not str(comment.get('issue_url') or '').endswith(f'/issues/{number}'):
        return None, f'the comment the dispatch names is not on PR #{number}'
    marks = review_marks([comment])
    mark = marks[0] if marks else None
    if not mark or mark['verdict'] != 'fix-first':
        return None, 'the comment the dispatch names is not a fix-first verdict'
    if mark['round'] != rnd:
        return None, f'the verdict comment sent its head back as fix round {mark["round"]}, not {rnd}'
    # A new push, or a review in flight or done since, makes the round moot: the next verdict decides (said quietly).
    if mark['sha'] != sha:
        return None, f'{STALE}the verdict comment is for head {mark["sha"][:7]}, not the current head {sha[:7]}'
    # The review workflow's verdict statuses on the head, newest first. A `reset` (a review in flight: an edit of the
    # body while the round waited in the queue) is skipped, since that review finds the head already sent back and
    # dispatches nothing itself; refusing here would lose the round.
    mine = [s for s in statuses or [] if isinstance(s, dict) and s.get('context') == REVIEW_CONTEXT
            and (s.get('creator') or {}).get('login') == REVIEW_BOT and not (s.get('description') or '').startswith(REVIEWING)]
    if not mine:   # the verdict job sets its status before it dispatches: none means the statuses could not be read
        return None, f'no `{REVIEW_CONTEXT}` verdict status of the review workflow could be read on head {sha[:7]}'
    newest = mine[0]
    if newest.get('state') != REVIEW_STATE['fix-first'] or (newest.get('description') or '').split(' ', 1)[0] != 'fix-first':
        return None, f'{STALE}the head\'s newest `{REVIEW_CONTEXT}` verdict ({newest.get("description") or "none"}) is not fix-first'
    digest = f'gaps {gaps_digest(gaps_of(mark["body"]))}'
    if not any(s.get('state') == REVIEW_STATE['fix-first'] and (s.get('description') or '').endswith(digest) for s in mine):
        return None, f'no `{REVIEW_CONTEXT}` status of the review workflow carries this comment\'s gaps (edited after it was posted?)'
    return mark, ''


def verdict_comment(decision: dict, *, sha: str, issue, number: int, rnd: int, run_url: str, checks: list[str],
                    already: bool = False) -> str:
    title = {'approve': 'approve', 'fix-first': 'fix first', 'needs-owner': "needs the owner's eyes"}[decision['verdict']]
    kind = f' kind={decision["kind"]}' if decision.get('kind') else ''
    bound = f' issue={issue}' if issue else ''
    lines = [f'<!-- agent-review sha={sha} verdict={decision["verdict"]} round={rnd}{kind}{bound} -->',
             f'**Review: {title}** · head `{sha[:7]}`' + (f' · issue #{issue}' if issue else '') + f' · [run]({run_url})', '']
    reasons = decision['reasons']
    head, items = ([reasons[0]], reasons[1:]) if reasons and reasons[0].endswith(':') else ([], reasons)
    if decision['verdict'] == 'fix-first' or head:
        lines += head + [f'{i}. {reason}' for i, reason in enumerate(items, 1)]
    else:
        lines += items
    lines.append('')
    if decision['next'] == 'fix' and already:
        lines.append(f'This head was already sent back as fix round {rnd} of {MAX_ROUNDS}; the agent\'s next push is reviewed again.')
    elif decision['next'] == 'fix':
        lines.append(f'@claude fix the gaps above on this branch (fix round {rnd} of {MAX_ROUNDS}); the agent workflow is on it.')
    elif decision['next'] == 'auto-merge':
        lines.append('Merge tier: **auto** (only documentation and tests changed). Auto-merge is on: GitHub merges this '
                     'with a merge commit once the required checks pass.')
    elif decision['verdict'] == 'approve' and decision.get('tier') == 'auto':
        lines.append('Merge tier: **auto** (only documentation and tests changed), but tiered auto-merge is off (the repository '
                     f'variable `AGENT_AUTO_MERGE`, docs/AGENTS.md). In the merge batch; the owner merges it with `/approve-pr {number}`.')
    elif decision['verdict'] == 'approve':
        lines.append(f'Merge tier: **owner**. In the merge batch; the owner merges it with `/approve-pr {number}`.')
    elif decision['verdict'] == 'fix-first':
        lines.append("Fix these on the branch; every push is reviewed again.")
    lines += ['', '<details><summary>What the workflow measured</summary>', '', *[f'- {c}' for c in checks], '', '</details>']
    return '\n'.join(lines) + FOOTER


def run_cost(path: str | None) -> dict | None:
    """The cost of one Claude Code run from the action's execution file (its final `result` message)."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, TypeError, ValueError):
        return None
    for item in reversed(data if isinstance(data, list) else [data]):
        if isinstance(item, dict) and item.get('type') == 'result':
            return {k: item.get(k) for k in ('total_cost_usd', 'num_turns', 'duration_ms', 'subtype')}
    return None


def ci_state(check_runs: list) -> tuple[str, str]:
    """(success | failure | pending | missing, detail) over the check runs named CI_CHECK on one commit."""
    runs = [r for r in check_runs if r.get('name') == CI_CHECK]
    if not runs:
        return 'missing', f'no `{CI_CHECK}` check on this commit'
    if any(r.get('status') != 'completed' for r in runs):
        return 'pending', f'`{CI_CHECK}` still running'
    bad = [r for r in runs if r.get('conclusion') in ('failure', 'timed_out', 'action_required', 'startup_failure')]
    if bad:
        return 'failure', f'`{CI_CHECK}` {bad[0]["conclusion"]}: {bad[0].get("html_url") or ""}'.strip()
    if any(r.get('conclusion') == 'success' for r in runs):
        return 'success', f'`{CI_CHECK}` passed'
    return 'missing', f'`{CI_CHECK}` ended {runs[0].get("conclusion")}'


def fix_prompt(*, pr: int, issue, branch: str, rnd: int, gaps: str, request: str) -> str:
    who = (f'The reviewer workflow sent pull request #{pr} back (fix round {rnd} of {MAX_ROUNDS}). Its gaps:\n\n{gaps}'
           if rnd else f"The owner asked, in a comment on pull request #{pr}:\n\n{request.strip()}")
    return (f'{who}\n\nYou are on `{branch}`, the pull request\'s own branch' + (f', for issue #{issue}' if issue else '') + '. '
            'Read CLAUDE.md, the pull request (`gh pr view ' + str(pr) + '`) and its issue first. Fix exactly this, the '
            'smallest change that does it, with a test or golden case for any behaviour you change. Then follow steps 5 to 7 '
            'of `.claude/skills/fix-issue/SKILL.md`: the tests, the evals and the UI checks; `git merge origin/main` if '
            'main moved (never rebase); commit and `git push` to this same branch, and update the pull request '
            'description (`gh pr edit`) where its test numbers or criteria changed. If a gap is a deleted file or fewer tests and '
            'the issue asks for exactly that, do not undo it: say so in the pull request description, push nothing, and the owner '
            'decides. Do not open another pull request, '
            'do not merge, and do not edit `.github/`, `.claude/` or a `CLAUDE.md`.')


# ------------------------------------------------------------------------------------------------ I/O

def gh(*args: str, check: bool = True, stdin: str | None = None) -> str:
    proc = subprocess.run(['gh', *args], capture_output=True, text=True, input=stdin)
    if check and proc.returncode:
        raise SystemExit(f'gh {" ".join(args[:2])} failed: {proc.stderr.strip()[:500]}')
    return proc.stdout


def api(path: str, *fields: str, method: str = 'GET', paginate: bool = False, check: bool = True):
    args = ['api', path]
    if method != 'GET':
        args += ['-X', method]
    if paginate:
        args += ['--paginate', '--slurp']
    for field in fields:
        args += ['-f', field]
    out = gh(*args, check=check)
    if not out.strip():
        return None
    data = json.loads(out)
    if not paginate:
        return data
    if not isinstance(data, list):
        return []   # a failed call (check=False) answers one error object, not a list of pages
    items = []   # --slurp: one JSON value per page; a list endpoint's page is a list, a search's an object holding one
    for page in data:
        items += page if isinstance(page, list) else next((v for v in page.values() if isinstance(v, list)), [])
    return items


def output(**values) -> None:
    lines = []
    for key, value in values.items():
        value = '' if value is None else str(value)
        if '\n' in value:
            delim = f'EOF_{os.urandom(6).hex()}'
            lines.append(f'{key}<<{delim}\n{value}\n{delim}')
        else:
            lines.append(f'{key}={value}')
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with open(target, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
    else:
        print('\n'.join(lines))


def get_issue(n) -> dict | None:
    """An open-or-closed issue (not a pull request), or None when the number names none."""
    if not n:
        return None
    issue = api(f'repos/{REPO}/issues/{n}', check=False)
    return issue if isinstance(issue, dict) and 'title' in issue and not issue.get('pull_request') else None


def run_url() -> str:
    return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPO}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '0')}"


def comment(number, body: str) -> dict:
    """Post a comment; GitHub's answer (its `id` names the comment a fix round is dispatched for)."""
    posted = api(f'repos/{REPO}/issues/{number}/comments', f'body={body}', method='POST')
    return posted if isinstance(posted, dict) else {}


def set_review_status(sha: str, decision: dict, body: str = '') -> None:
    """The verdict as the head's `agent-review` commit status, which `status` requires next to the comment; for
    fix-first it carries the digest of the comment's gaps, which a fix round requires (N3)."""
    api(f'repos/{REPO}/statuses/{sha}', f'state={REVIEW_STATE[decision["verdict"]]}', f'context={REVIEW_CONTEXT}',
        f'description={status_description(decision, gaps_of(body))}', f'target_url={run_url()}', method='POST')


def set_review_label(number, label: str | None) -> None:
    current = labels_of(api(f'repos/{REPO}/issues/{number}'))
    for name in LABELS.values():
        if name in current and name != label:
            api(f'repos/{REPO}/issues/{number}/labels/{urllib.parse.quote(name, safe="")}', method='DELETE', check=False)
    if label and label not in current:
        api(f'repos/{REPO}/issues/{number}/labels', f'labels[]={label}', method='POST')


def cmd_gate(_args) -> int:
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    plan = event_plan(os.environ.get('GITHUB_EVENT_NAME', ''), event, os.environ.get('GITHUB_REPOSITORY_OWNER', ''))
    if plan['mode'] == 'fix' and not plan['issue']:
        pr = api(f'repos/{REPO}/pulls/{plan["pr"]}')
        plan['issue'] = str(linked_issue(pr.get('body')) or '')
    if plan['add_label'] == 'true':
        # The same as the label, exactly: removing it stops this run too, and its pull request gets fix
        # rounds. Added with this job's token, so it starts no second run.
        api(f'repos/{REPO}/issues/{plan["issue"]}/labels', f'labels[]={READY}', method='POST')
    plan['key'] = concurrency_key(plan['issue'], plan['pr']) if plan['mode'] != 'skip' else ''
    print(f"mode={plan['mode']} issue={plan['issue'] or '-'} pr={plan['pr'] or '-'} round={plan['round']}"
          + (f" ({plan['why']})" if plan.get('why') else ''))
    output(**{k: plan[k] for k in ('mode', 'issue', 'pr', 'round', 'verdict', 'key', 'needs_label', 'request')})
    return 0


def cmd_queue(args) -> int:
    """Wait for a place among the MAX_AGENTS. One API call per poll and status in RUNNING (all runs share
    the token's hourly budget with Preview and the review); a failed call is retried at the next poll. After --max-wait
    minutes the run gives up and says so where it was started, rather than being dropped silently."""
    me = int(args.run_id)
    deadline = time.time() + args.max_wait * 60
    while True:
        # Only the runs still going (L3): the newest 100 runs of any status could push a long-running one out.
        listings = [api(f'repos/{REPO}/actions/workflows/{args.workflow}/runs?status={status}&per_page=100', check=False)
                    for status in RUNNING]
        if all(isinstance(x, dict) and 'workflow_runs' in x for x in listings):
            ahead = queue_ahead([r for x in listings for r in x['workflow_runs']], me)
            if len(ahead) < MAX_AGENTS:
                print(f'{len(ahead)} agent run(s) ahead; starting (at most {MAX_AGENTS} at once)')
                return 0
            print(f'waiting: {len(ahead)} agent runs ahead ({", ".join(map(str, ahead))}); at most {MAX_AGENTS} at once', flush=True)
        else:
            print('::warning::could not list the agent runs; trying again at the next poll', flush=True)
        if time.time() > deadline:
            retry = ('comment `@claude` on this pull request with what to fix (a re-run of the review does not send the '
                     'same head back twice)' if args.pr else f'label the issue `{READY}` again (or comment `@claude` on it)')
            where = args.pr or args.issue
            if where:
                comment(where, f'The agent waited {args.max_wait} minutes for a place among the {MAX_AGENTS} that run at once '
                               f'and gave up. [Run]({run_url()}); to retry, {retry}.' + FOOTER)
            print(f'::error::no place in the queue after {args.max_wait} minutes')
            return 1
        time.sleep(args.poll)


def cmd_protected(args) -> int:
    """Refuse to run the agent while `main` takes a direct push (H1). The agent's token can write branches;
    only protection on `main` stops a push there from deploying the public demo. Fails closed: rules that
    cannot be read count as none, and only a ruleset that requires a pull request or restricts updates counts (N2)."""
    branch = api(f'repos/{REPO}/branches/{BASE}', check=False)
    rules = api(f'repos/{REPO}/rules/branches/{BASE}?per_page=100', check=False)
    ok, why = protection_state(branch if isinstance(branch, dict) else {}, rules if isinstance(rules, list) else [])
    if ok:
        print(f'`{BASE}` is protected: {why}')
        return 0
    where = args.pr or args.issue
    if where:
        comment(where, f'The agent did not start: `{BASE}` is not protected: {why} The agent can push branches, so it runs only '
                       f'while a ruleset on `{BASE}` requires a pull request (or restricts updates): docs/AGENTS.md, "Set up once", '
                       f'step 3. ([run]({run_url()}))' + FOOTER)
    print(f'::error::{BASE} is not protected: {why} The agent does not run (docs/AGENTS.md, "Set up once")')
    return 1


def issue_branches(n: int) -> list[str]:
    refs = api(f'repos/{REPO}/git/matching-refs/heads/issue-{n}-', check=False) or []
    return [r['ref'].removeprefix('refs/heads/') for r in refs]


def git(*args: str) -> None:
    subprocess.run(['git', *args], check=True)


def cmd_plan(args) -> int:
    """Pick the branch and the prompt, or refuse. Runs inside the build job after it holds the issue's
    concurrency group, so a run that waited behind another for the same issue sees that one's PR."""
    owner = REPO.split('/')[0]
    if args.mode == 'build':
        n = int(args.issue)
        issue = api(f'repos/{REPO}/issues/{n}')
        if issue.get('pull_request') or issue['state'] != 'open':
            print(f'#{n} is not an open issue; nothing to do')
            output(go='false')
            return 0
        if args.needs_label == 'true' and READY not in labels_of(issue):
            print(f'#{n} no longer carries {READY}; nothing to do')
            output(go='false')
            return 0
        pulls = api(f'repos/{REPO}/pulls?state=open&per_page=100', paginate=True)
        branches = issue_branches(n)
        closed = []
        for b in branches:
            closed += api(f'repos/{REPO}/pulls?state=closed&head={owner}:{b}&per_page=10') or []
        plan = plan_branch(n, issue['title'], pulls, branches, closed)
        print(f"{plan['action']}: {plan['branch'] or '-'} ({plan['reason']})")
        if plan['action'] == 'refuse':
            comment(n, f'The agent did not start on #{n}: {plan["reason"]}. ([run]({run_url()}))' + FOOTER)
            output(go='false')
            return 0
        if plan['action'] == 'create':
            git('checkout', '-b', plan['branch'], 'origin/main')
        else:
            git('checkout', '-b', plan['branch'], f'origin/{plan["branch"]}')
        comment(n, f'Agent run started on #{n}, branch `{plan["branch"]}` ({plan["action"]}). [Run]({run_url()}) · '
                   f'remove `{READY}` to stop it.' + FOOTER)
        output(go='true', branch=plan['branch'], issue=n, prompt=f'/fix-issue {n}', before='')
        return 0
    # a fix round on a pull request's own branch
    p = int(args.pr)
    pr = api(f'repos/{REPO}/pulls/{p}')
    rnd = int(args.round or 0)
    issue_n = linked_issue(pr.get('body'))
    refuse, loud = None, not rnd   # a reviewer's round that no longer applies (a new push, the label removed) ends quietly
    if pr['state'] != 'open':
        refuse = f'PR #{p} is not open'
    elif pr['head']['repo'] is None or pr['head']['repo']['full_name'] != REPO:
        refuse = f'PR #{p} comes from a fork; the agent pushes only to branches of {REPO}'
    elif rnd:
        issue = get_issue(issue_n) or {}
        if BUILT not in labels_of(pr):
            refuse = f'PR #{p} was not opened by the agent (no `{BUILT}` label)'
        elif not issue_n or READY not in labels_of(issue):
            refuse = f'issue #{issue_n or "?"} no longer carries `{READY}`'
        elif rnd > MAX_ROUNDS:
            refuse, loud = f'round {rnd} is past the cap of {MAX_ROUNDS}', True
        elif not str(args.verdict or '').isdigit():
            refuse, loud = 'a fix round names the verdict comment that sent the head back (`verdict`), and this dispatch names none', True
        else:
            # Only the comment the review workflow named (N3), checked against the head's commit statuses.
            sha = pr['head']['sha']
            latest, why = verdict_for_round(api(f'repos/{REPO}/issues/comments/{args.verdict}', check=False),
                                            api(f'repos/{REPO}/commits/{sha}/statuses?per_page=100', paginate=True, check=False),
                                            number=p, sha=sha, rnd=rnd)
            if why:
                refuse, loud = why.removeprefix(STALE), not why.startswith(STALE)
    if refuse:
        print(f'refuse: {refuse}')
        if loud:
            comment(p, f'The agent did not start: {refuse}. ([run]({run_url()}))' + FOOTER)
        output(go='false')
        return 0
    gaps = gaps_of(latest['body']) if rnd else ''
    branch = pr['head']['ref']
    git('checkout', '-b', branch, f'origin/{branch}')
    comment(p, f'Agent fix {"round " + str(rnd) if rnd else "run"} started on `{branch}`. [Run]({run_url()})' + FOOTER)
    output(go='true', branch=branch, issue=issue_n or '', before=pr['head']['sha'],
           prompt=fix_prompt(pr=p, issue=issue_n, branch=branch, rnd=rnd, gaps=gaps, request=args.request or ''))
    return 0


def cmd_verify(args) -> int:
    """After the model: a build must have opened exactly one PR from its branch that closes the issue; a fix
    round must have pushed. Says so on the issue or the pull request either way."""
    owner = REPO.split('/')[0]
    if args.mode == 'build':
        n = int(args.issue)
        pulls = api(f'repos/{REPO}/pulls?state=open&head={owner}:{args.branch}&per_page=10') or []
        if len(pulls) != 1:
            comment(n, f'The agent run on #{n} ended without a pull request from `{args.branch}`. '
                       f'[Run]({run_url()}); label it `{READY}` again to resume the branch.' + FOOTER)
            print(f'::error::no pull request from {args.branch}')
            return 1
        pr = pulls[0]
        api(f'repos/{REPO}/issues/{pr["number"]}/labels', f'labels[]={BUILT}', method='POST')
        if n not in linked_issues(pr.get('body')):
            api(f'repos/{REPO}/pulls/{pr["number"]}', f'body={(pr.get("body") or "").rstrip()}\n\nCloses #{n}', method='PATCH')
        comment(n, f'Opened PR #{pr["number"]} from `{args.branch}`. The workflow starts its CI and its review next. '
                   f'[Run]({run_url()})' + FOOTER)
        print(f'PR #{pr["number"]} from {args.branch}')
        return start_ci_and_review(args.branch, pr['number'])
    head = api(f'repos/{REPO}/branches/{args.branch}')['commit']['sha']
    if head == args.before:
        comment(args.pr, f'The agent fix run pushed nothing to `{args.branch}`. If the gaps left are a deleted file or fewer tests '
                         'that the issue asks for, the owner merges with `/approve-pr` and "approve anyway"; otherwise comment '
                         f'`@claude` with what to do. [Run]({run_url()})' + FOOTER)
        print('::error::the fix round pushed no commit')
        return 1
    print(f'{args.branch}: {args.before[:7]} -> {head[:7]}')
    return start_ci_and_review(args.branch, args.pr)


def start_ci_and_review(branch: str, pr) -> int:
    """Start CI and the review on the agent's push. The agent pushes with the build job's GITHUB_TOKEN, and GitHub
    starts no workflow from that token's pushes or pull requests, so the workflow asks for both: Tests on the
    branch (its `deterministic` check lands on the head commit, which the pull request and protected main read)
    and the review of the pull request. `verify` runs in its own job, the only one holding `actions: write`,
    from main's script with `plan`'s outputs, so the agent decides nothing that is dispatched."""
    failed = []
    for argv in (['workflow', 'run', 'tests.yml', '--repo', REPO, '--ref', branch],
                 ['workflow', 'run', 'agent-review.yml', '--repo', REPO, '--ref', BASE, '-f', f'pr={pr}']):
        sent = subprocess.run(['gh', *argv], capture_output=True, text=True)
        print(f"{' '.join(argv[:3])}: {'started' if not sent.returncode else 'failed: ' + sent.stderr.strip()[:200]}")
        if sent.returncode:
            failed.append(argv[2])
    if failed:
        comment(pr, f'The workflow could not start {" and ".join(f"`{w}`" for w in failed)} for this pull request. Run them by hand '
                    f'(Actions → Tests → *Run workflow* on `{branch}`; Actions → Agent review → *Run workflow* with pr {pr}). '
                    f'([run]({run_url()}))' + FOOTER)
        return 1
    return 0


def cmd_reset(args) -> int:
    """A new head (or a new body or base): its old verdict no longer holds. Drop the review label and any
    auto-merge. Creates any of the agent labels that is missing (`gh label create` refuses one that exists).
    Fails loudly when auto-merge is on and cannot be turned off (L2): a stale approve must not merge."""
    for name, color in LABEL_COLORS.items():
        gh('label', 'create', name, '--color', color, '--description', LABEL_TEXT[name], '--repo', REPO, check=False)
    set_review_label(args.pr, None)
    pr = api(f'repos/{REPO}/pulls/{args.pr}') or {}
    # An edit of the body or the base keeps the sha, and with it the old `agent-review: success` that a required
    # check would still accept; the head waits for its new verdict instead.
    if (pr.get('head') or {}).get('sha'):
        api(f'repos/{REPO}/statuses/{pr["head"]["sha"]}', 'state=pending', f'context={REVIEW_CONTEXT}',
            f'description={REVIEWING}', f'target_url={run_url()}', method='POST')
    if pr.get('auto_merge'):
        off = subprocess.run(['gh', 'pr', 'merge', str(args.pr), '--disable-auto', '--repo', REPO], capture_output=True, text=True)
        if off.returncode:
            print(f'::error::auto-merge is on for PR #{args.pr} and could not be turned off: {off.stderr.strip()[:300]}')
            return 1
        print(f'auto-merge turned off for PR #{args.pr}')
    return 0


def cmd_prepare(args) -> int:
    """The reviewer's inputs, written to .agent-review/: the pull request, its issue, the diff, the changed
    files and the golden check. Facts only; the model reads them, the verdict job decides. Runs in a checkout of
    the head with main's history (`origin/main`), which both review jobs make with `fetch-depth: 0`."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pr = api(f'repos/{REPO}/pulls/{args.pr}')
    n = linked_issue(pr.get('body'))
    issue = get_issue(n)
    # The files from git, not the pull request files API, which stops at 3,000 (N4): every path against the merge
    # base with main, renames by both paths, deletions apart.
    files, removed = name_status(subprocess.run(['git', 'diff', '--name-status', '-z', '-M', f'origin/{BASE}...HEAD'],
                                                capture_output=True, text=True, check=True).stdout)
    # A symlink counts by where it points too (L5): its target joins the files the tier and OWNER_EYES read.
    raw = subprocess.run(['git', 'diff', '--raw', '-z', '--no-abbrev', '--no-renames', f'origin/{BASE}...HEAD'],
                         capture_output=True, text=True, check=True).stdout
    links = {}
    for path, blob in symlink_entries(raw):
        target = subprocess.run(['git', 'cat-file', '-p', blob], capture_output=True, text=True, check=True).stdout
        links[path] = link_target(path, target.strip())
    files += [target for target in links.values() if target not in files]
    golden, changes = [], []
    # Against the merge base, not main's tip: a case main gained after the merge ref was made is not "deleted".
    base_ref = subprocess.run(['git', 'merge-base', 'origin/main', 'HEAD'], capture_output=True, text=True).stdout.strip() or 'origin/main'
    for name in GOLDEN_FILES:
        base = subprocess.run(['git', 'show', f'{base_ref}:{name}'], capture_output=True, text=True).stdout or None
        head = Path(name).read_text(encoding='utf-8') if Path(name).exists() else None
        golden += golden_problems(base, head, name)
        changes += golden_changes(base, head, name)
    diff = subprocess.run(['git', 'diff', 'origin/main...HEAD'], capture_output=True, text=True).stdout
    (out / 'pr.md').write_text(f'# PR #{args.pr}: {pr["title"]}\n\nBranch `{pr["head"]["ref"]}`, head {pr["head"]["sha"]}\n\n'
                               f'{pr.get("body") or ""}\n', encoding='utf-8')
    (out / 'issue.md').write_text(f'# Issue #{n}: {issue["title"]}\n\n{issue.get("body") or ""}\n' if issue else
                                  '# No linked issue\n', encoding='utf-8')
    (out / 'diff.patch').write_text(diff, encoding='utf-8')
    (out / 'golden.md').write_text('# Changed expectations of existing golden cases\n\n' +
                                   ('\n'.join(f'- {c}' for c in changes) or 'None.') + '\n', encoding='utf-8')
    meta = {'pr': int(args.pr), 'sha': pr['head']['sha'], 'base': pr['base']['ref'], 'issue': n if issue else None,
            'issue_labels': labels_of(issue) if issue else [], 'files': files, 'golden': golden,
            'removed': removed, 'links': links}
    (out / 'meta.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
    print(f'PR #{args.pr} at {pr["head"]["sha"][:7]}: issue #{n or "-"}, {len(files)} files, tier {tier(files)}, '
          f'{len(golden)} golden problem(s), {len(meta["removed"])} removed, {len(links)} symlink(s)')
    output(issue=meta['issue'] or '', sha=meta['sha'], review='true' if meta['issue'] else 'false')
    return 0


def read_gaps(path: Path) -> list | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [{'where': str(g.get('where') or ''), 'gap': str(g.get('gap') or '')} for g in data
            if isinstance(g, dict) and str(g.get('gap') or '').strip()]


def evals_result(folder: Path) -> tuple[str, str]:
    """(pass | fail | missing, the summary line) from the evals job's artifact. That job runs the pull request's
    code, so its text is scrubbed and capped before it is posted."""
    try:
        code = int((folder / 'evals.exit').read_text().strip())
    except (OSError, ValueError):
        return 'missing', ''
    try:
        tail = [line for line in (folder / 'evals.txt').read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
    except OSError:
        tail = []
    summary = next((line for line in reversed(tail) if re.search(r'\d+ passed', line)), tail[-1] if tail else '')
    return ('pass' if code == 0 else 'fail'), scrub(summary, 200)


def read_collected(path: Path) -> int | None:
    """The test count in a `pytest --collect-only -q` output file; None when the file is missing."""
    try:
        return collected_count(path.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None


def mergeable(p: int, tries: int, poll: int):
    """GitHub computes `mergeable` in the background after a push and answers null until then."""
    for attempt in range(tries):
        pr = api(f'repos/{REPO}/pulls/{p}')
        if pr.get('mergeable') is not None or attempt == tries - 1:
            return pr
        time.sleep(poll)


def cmd_verdict(args) -> int:
    """Decide, comment, label, then a fix round or auto-merge. The facts (`--facts`: files, golden check,
    issue) come from this job's own run of `prepare` on a checkout it made; only the model's gaps and the
    evals' exit code come from the other jobs, each from its own artifact, so neither the pull request's
    code (which ran in the evals job) nor the model can rewrite the files list or the golden check."""
    p, sha = int(args.pr), args.sha
    try:
        meta = json.loads(Path(args.facts, 'meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        meta = None
    if meta is None:
        # This job's own `prepare` failed (an API call, an unreadable checkout): never approve, but say so on
        # the head rather than leave it unlabelled after `reset` took the old label off.
        decision = {'verdict': 'needs-owner', 'label': LABELS['needs-owner'], 'next': None, 'tier': 'owner', 'kind': 'unresolved',
                    'reasons': ['the review could not work out the pull request\'s facts (its `prepare` step failed: see the run); '
                                'run the Agent review workflow again for this pull request']}
        comment(p, verdict_comment(decision, sha=sha, issue=None, number=p, rnd=0, run_url=run_url(), checks=[]))
        set_review_label(p, decision['label'])
        set_review_status(sha, decision)
        print('::error::no facts for this head; labelled needs-owner')
        output(verdict='needs-owner', next='', round=0, issue='')
        return 1
    if meta['sha'] != sha:
        print(f'::warning::the review inputs are for {meta["sha"][:7]}, not {sha[:7]}; nothing decided')
        return 0
    deadline = time.time() + args.ci_timeout
    while True:
        state, detail = ci_state(api(f'repos/{REPO}/commits/{sha}/check-runs?per_page=100')['check_runs'])
        if state not in ('pending', 'missing') or time.time() > deadline:
            break
        time.sleep(args.poll)
    pr = mergeable(p, args.mergeable_tries, args.poll)
    if pr['head']['sha'] != sha:
        print(f'the head moved to {pr["head"]["sha"][:7]}; the next review decides')
        return 0
    comments = api(f'repos/{REPO}/issues/{p}/comments?per_page=100', paginate=True)
    rounds = rounds_spent(comments)
    already = round_for_head(comments, sha)
    gaps = read_gaps(Path(args.gaps))
    gaps = clean_gaps(gaps) if gaps is not None else None
    evals, evals_detail = evals_result(Path(args.evals))
    collected = read_collected(Path(args.evals) / 'collected.txt')
    baseline = read_collected(Path(args.baseline) / 'collected.txt')
    auto_merge = os.environ.get('AGENT_AUTO_MERGE', '').strip().lower() == 'true'
    # Fix rounds go back to the builder only for its own pull requests, and only while the owner keeps the
    # issue labelled agent-ready (removing the label stops the agent; the pull request is then the owner's).
    issue_now = get_issue(meta['issue'])
    agent_built = BUILT in labels_of(pr) and bool(issue_now) and READY in labels_of(issue_now)
    decision = decide(issue=meta['issue'], issue_labels=labels_of(issue_now) if issue_now else meta['issue_labels'], reviewer=gaps is not None, gaps=gaps or [],
                      golden=meta['golden'], ci=state, ci_detail=detail, evals=evals, evals_detail=evals_detail,
                      files=meta['files'], rounds=already - 1 if already else rounds, agent_built=agent_built,
                      conflicts=pr.get('mergeable') is False, base=(pr.get('base') or {}).get('ref') or meta.get('base') or '',
                      removed=meta.get('removed') or [], collected=collected, baseline=baseline, auto_merge=auto_merge)
    rnd = (already or rounds + 1) if decision['next'] == 'fix' else 0
    checks = [f'CI: {state} ({detail})', f'evals: {evals}' + (f' ({evals_detail})' if evals_detail else ''),
              f'golden: {len(meta["golden"])} case(s) weakened' if meta['golden'] else 'golden: no case removed or weakened',
              f'reviewer: {"no verdict" if gaps is None else f"{len(gaps)} gap(s)"}',
              f'tests collected: {collected if collected is not None else "not measured"} on this head, '
              f'{baseline if baseline is not None else "not measured"} on its merge base',
              f'removed files: {len(meta.get("removed") or [])}' + (f'; symlinks: {", ".join(f"`{a}` → `{b}`" for a, b in meta["links"].items())}'
                                                                     if meta.get('links') else ''),
              f'base: `{(pr.get("base") or {}).get("ref") or "?"}`',
              f'merge tier: {decision["tier"]} ({len(meta["files"])} file(s))' + (', auto-merge on' if auto_merge else ', auto-merge off'),
              f'fix rounds so far: {rounds} of {MAX_ROUNDS}' + ('' if agent_built else ' (not an agent pull request: no fix rounds)')]
    body = verdict_comment(decision, sha=sha, issue=meta['issue'], number=p, rnd=rnd, run_url=run_url(), checks=checks,
                           already=bool(already))
    posted = comment(p, body)
    set_review_label(p, decision['label'])
    set_review_status(sha, decision, body)
    print(f"{decision['label']} on PR #{p} at {sha[:7]}; next: {decision['next'] or 'the owner'}")
    output(verdict=decision['verdict'], next=decision['next'] or '', round=rnd, issue=meta['issue'] or '')
    if decision['next'] == 'fix' and not already:
        # The fix round reads this comment and no other (N3): its id goes in the dispatch.
        cid = str(posted.get('id') or '')
        sent = subprocess.run(['gh', 'workflow', 'run', 'agent.yml', '--repo', REPO, '--ref', 'main',
                               '-f', f'issue={meta["issue"]}', '-f', f'pr={p}', '-f', f'round={rnd}', '-f', f'verdict={cid}'],
                              capture_output=True, text=True) if cid.isdigit() else None
        if sent is None or sent.returncode:
            why = sent.stderr.strip()[:200] if sent is not None else 'GitHub returned no id for the verdict comment'
            comment(p, f'The fix round could not be started ({why}). Comment `@claude` on this pull '
                       'request with the gaps above to start it by hand.' + FOOTER)
            print(f'::error::could not dispatch fix round {rnd}')
            return 1
    elif decision['next'] == 'auto-merge':
        merge = subprocess.run(['gh', 'pr', 'merge', str(p), '--auto', '--merge', '--match-head-commit', sha, '--repo', REPO],
                               capture_output=True, text=True)
        if merge.returncode and 'clean status' in (merge.stderr + merge.stdout).lower():
            # Nothing left to wait for (the checks already passed): GitHub will not queue it, so merge now.
            merge = subprocess.run(['gh', 'pr', 'merge', str(p), '--merge', '--match-head-commit', sha, '--repo', REPO],
                                   capture_output=True, text=True)
        if merge.returncode:
            print(f"::warning::auto-merge not enabled on PR #{p}: {merge.stderr.strip()[:300]} "
                  "(Settings > General > Allow auto-merge, and protect main with the required check)")
    return 0


def cmd_status(args) -> int:
    """The review verdict of a pull request as the workflow left it, for /review-pr, /approve-pr and /issues.
    Prints `review: <verdict>` (or `review: none` / `review: stale`) and what it rests on."""
    pr = api(f'repos/{REPO}/pulls/{args.pr}')
    files = changed_paths(api(f'repos/{REPO}/pulls/{args.pr}/files?per_page=100', paginate=True))
    marks = review_marks(api(f'repos/{REPO}/issues/{args.pr}/comments?per_page=100', paginate=True))
    statuses = api(f'repos/{REPO}/commits/{pr["head"]["sha"]}/statuses?per_page=100', paginate=True)
    row = status_row(pr, files, marks, statuses)
    print(json.dumps(row, indent=1) if args.json else
          f"{row['status']}{' (' + row['kind'] + ')' if row['kind'] else ''} · head {row['head'][:7]} · label {row['label'] or 'none'} · tier {row['tier']}"
          f" · fix rounds {row['rounds']} of {MAX_ROUNDS}\n{row['why']}")
    return 0


def review_status(statuses: list) -> dict | None:
    """The newest `agent-review` commit status the review workflow wrote on a commit (`GET commits/{sha}/statuses`
    lists the newest first)."""
    return next((s for s in statuses or [] if s.get('context') == REVIEW_CONTEXT
                 and (s.get('creator') or {}).get('login') == REVIEW_BOT), None)


def status_description(decision: dict, gaps: str = '') -> str:
    """The `agent-review` status text: the verdict first (what `status` compares), and for fix-first the digest of
    the comment's gaps last (what a fix round checks, `verdict_for_round`). GitHub keeps 140 characters."""
    kind = f' ({decision["kind"]})' if decision.get('kind') else ''
    digest = f' · gaps {gaps_digest(gaps)}' if decision['verdict'] == 'fix-first' else ''
    return f'{decision["verdict"]}{kind} · tier {decision.get("tier") or "owner"}{digest}'[:140]


def status_row(pr: dict, files: list, marks: list, statuses: list) -> dict:
    """`review: approve` only when the label, the newest verdict comment and the head's `agent-review` commit
    status agree, and the comment was written for the pull request's current head. A label left from an older
    head, or a verdict comment without its commit status, reads `review: stale`: the builder's token can write
    a comment as REVIEW_BOT, never a status."""
    head = pr['head']['sha']
    label = next((name for name in LABELS.values() if name in labels_of(pr)), None)
    latest = marks[-1] if marks else None
    mark = review_status(statuses)
    if not label and not latest:
        status, why = 'review: none', 'the review workflow has not reviewed this pull request'
    elif not latest or not (head.startswith(latest['sha']) or latest['sha'].startswith(head)):
        status, why = 'review: stale', f'the newest verdict is for {latest["sha"][:7] if latest else "no head"}, not the current head'
    elif label != LABELS.get(latest['verdict']):
        status, why = 'review: stale', f'the label ({label or "none"}) and the newest verdict ({latest["verdict"]}) disagree'
    elif latest.get('issue') and linked_issue(pr.get('body')) != latest['issue']:
        status, why = 'review: stale', (f'the body now closes #{linked_issue(pr.get("body")) or "nothing"}, not the '
                                        f'#{latest["issue"]} the verdict was for (an edit with a workflow token starts no review)')
    elif ((pr.get('base') or {}).get('ref') or BASE) != BASE:
        status, why = 'review: stale', f'the base is now `{pr["base"]["ref"]}`, not `{BASE}`'
    elif (not mark or mark.get('state') != REVIEW_STATE.get(latest['verdict'])
          or (mark.get('description') or '').split(' ', 1)[0] != latest['verdict']):
        status, why = 'review: stale', (f'the head\'s `{REVIEW_CONTEXT}` commit status ({(mark or {}).get("description") or "none"}) '
                                        f'does not confirm the newest verdict ({latest["verdict"]})')
    else:
        status, why = label, f'verdict comment and `{REVIEW_CONTEXT}` status for the current head ({head[:7]})'
    kind = latest['kind'] if latest and status == LABELS['needs-owner'] else ''
    return {'number': pr['number'], 'status': status, 'kind': kind, 'label': label, 'head': head, 'why': why,
            'tier': tier(files), 'rounds': max([m['round'] for m in marks] or [0])}


def cmd_cost(args) -> int:
    cost = run_cost(args.file)
    if not cost:
        print(f'{args.label}: cost not reported (no result in the execution file)')
        return 0
    usd = cost.get('total_cost_usd')
    minutes = (cost.get('duration_ms') or 0) / 60000
    print(f"{args.label}: {'$%.2f' % usd if isinstance(usd, (int, float)) else 'cost unknown'}, "
          f"{cost.get('num_turns') or '?'} turns, {minutes:.1f} min, {cost.get('subtype') or 'unknown'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('gate')
    q = sub.add_parser('queue')
    q.add_argument('--run-id', required=True)
    q.add_argument('--workflow', default='agent.yml')
    q.add_argument('--poll', type=int, default=120)
    q.add_argument('--max-wait', type=int, default=330)
    q.add_argument('--issue', default='')
    q.add_argument('--pr', default='')
    p = sub.add_parser('plan')
    for name in ('--mode', '--issue', '--pr', '--round', '--verdict', '--needs-label', '--request'):
        p.add_argument(name, default='')
    v = sub.add_parser('verify')
    for name in ('--mode', '--issue', '--pr', '--branch', '--before'):
        v.add_argument(name, default='')
    pro = sub.add_parser('protected')
    pro.add_argument('--issue', default='')
    pro.add_argument('--pr', default='')
    r = sub.add_parser('reset')
    r.add_argument('--pr', required=True)
    pre = sub.add_parser('prepare')
    pre.add_argument('--pr', required=True)
    pre.add_argument('--out', default='.agent-review')
    ver = sub.add_parser('verdict')
    ver.add_argument('--pr', required=True)
    ver.add_argument('--sha', required=True)
    ver.add_argument('--facts', default='.agent-facts')
    ver.add_argument('--gaps', default='.agent-judge/gaps.json')
    ver.add_argument('--evals', default='.agent-evals')
    ver.add_argument('--baseline', default='.agent-baseline')
    ver.add_argument('--mergeable-tries', type=int, default=6)
    ver.add_argument('--ci-timeout', type=int, default=1800)
    ver.add_argument('--poll', type=int, default=30)
    s = sub.add_parser('status')
    s.add_argument('pr')
    s.add_argument('--json', action='store_true')
    c = sub.add_parser('cost')
    c.add_argument('file', nargs='?', default='')
    c.add_argument('--label', default='run')
    args = parser.parse_args(argv)
    return {'gate': cmd_gate, 'queue': cmd_queue, 'protected': cmd_protected, 'plan': cmd_plan, 'verify': cmd_verify,
            'reset': cmd_reset, 'prepare': cmd_prepare, 'verdict': cmd_verdict, 'status': cmd_status,
            'cost': cmd_cost}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
