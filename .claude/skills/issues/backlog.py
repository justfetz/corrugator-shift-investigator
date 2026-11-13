"""The ordered backlog with a computed status per issue (the `/issues` skill, issue #46).

Reads open issues and pull requests through `gh api` (REST; GraphQL is not available from
Claude Code sessions) and prints one table sorted by the order prefix in the title
(`04 ·`, `08c ·`; unprefixed issues sort last by number). Status, in priority order:

  in PR #n          an open pull request body says Closes/Fixes/Resolves #<issue>
  blocked by #m     the body's lead says "after #m", "depends on #m", "after 08c" (an order prefix)
                    and #m is still open
  waiting on owner  the issue carries the `waiting-on-owner` label (sessions add it when they
                    ask the owner something; the owner removes it when answered)
  ready             none of the above

Under the table, the merge batch: open pull requests labelled `review: approve` (the review workflow's
label, issue #126) in merge order, the backlog order of the issue each closes and then the pull request
number, with the merge tier from `.github/scripts/agent.py` (`auto`: docs and tests only, merges itself
after CI while the owner has AGENT_AUTO_MERGE on; `owner`: waits for /approve-pr). The coordinator's daily
summary quotes it.

Read-only. `python3 .claude/skills/issues/backlog.py [--json]`.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

# The review workflow's rules (labels, merge tier, what "closes" means), loaded from their one file.
_spec = importlib.util.spec_from_file_location('agent_rules', Path(__file__).resolve().parents[3] / '.github' / 'scripts' / 'agent.py')
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

REPO = 'justfetz/corrugator-shift-investigator'
ORDER = re.compile(r'^\s*(\d{1,2})([a-z]?)\s*·')
CLOSES = agent.CLOSES
BLOCKED = re.compile(r'\b(?:after|depends on|needs|once|blocked by|waits? (?:for|on))\s+(?:#|issue #?)?(\d{1,2}[a-z]?)\b', re.I)
OWNER_LABEL = 'waiting-on-owner'
OWNER_ASK = re.compile(r'^.*\bowner\b.*$', re.I | re.M)


def gh(path: str):
    # One compact JSON object per line (--jq '.[]'), so a page boundary never has to be found inside the text:
    # splitting the concatenated arrays on '][' broke on an issue body that contained those two characters.
    out = subprocess.run(['gh', 'api', path, '--paginate', '--jq', '.[]'], capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def order_key(title: str):
    m = ORDER.match(title)
    if not m:
        return (99, '', 0)
    return (int(m.group(1)), m.group(2), 0)


def status_of(issue: dict, open_numbers: set, by_order: dict, closes: dict) -> tuple[str, str]:
    """(status, the owner's ask when waiting). `by_order` maps an order prefix ('08c', '04') to
    the open issue number carrying it, so "after 08c" and "depends on #04" both resolve."""
    n = issue['number']
    if n in closes:
        return f'in PR #{closes[n]}', ''
    text = (issue.get('title') or '') + '\n' + (issue.get('body') or '')
    for m in BLOCKED.finditer(text[:600]):
        token = m.group(1).lower()
        dep = by_order.get(token) or by_order.get(token.zfill(2)) or (int(token) if token.isdigit() else None)
        if dep and dep != n and dep in open_numbers:
            return f'blocked by #{dep}', ''
    if OWNER_LABEL in [l['name'] for l in issue.get('labels', [])]:
        body = issue.get('body') or ''
        m = OWNER_ASK.search(body)
        return 'waiting on owner', re.sub(r'\*\*|`|^#+\s*', '', m.group(0)).strip()[:140] if m else ''
    return 'ready', ''


def merge_batch(pulls: list, issues: dict, files: dict, marks: dict, statuses: dict) -> list[dict]:
    """The approved pull requests in merge order: the backlog order of the issue each closes (an issue that
    is not open sorts last), then the pull request number. `files` maps a pull request to its changed files,
    `marks` to the review workflow's verdict comments and `statuses` to its head's commit statuses; a pull
    request is in the batch only when `agent.status_row` reads `review: approve`, i.e. the approve is for its
    current head and its `agent-review` commit status confirms it."""
    rows = []
    for pr in pulls:
        n = pr['number']
        if agent.status_row(pr, files.get(n, []), marks.get(n, []), statuses.get(n, []))['status'] != agent.LABELS['approve']:
            continue
        n = agent.linked_issue(pr.get('body'))
        issue = issues.get(n) or {}
        rows.append({'number': pr['number'], 'issue': n, 'title': pr['title'], 'tier': agent.tier(files.get(pr['number'], [])),
                     'key': (order_key(issue.get('title') or ''), n or 10**9, pr['number'])})
    rows.sort(key=lambda r: r['key'])
    return [{k: v for k, v in r.items() if k != 'key'} for r in rows]


def main(argv):
    issues = [i for i in gh(f'repos/{REPO}/issues?state=open&per_page=100') if not i.get('pull_request')]
    pulls = gh(f'repos/{REPO}/pulls?state=open&per_page=100')
    open_numbers = {i['number'] for i in issues}
    closes = {}
    for pr in pulls:
        for m in CLOSES.finditer(pr.get('body') or ''):
            closes.setdefault(int(m.group(1)), pr['number'])
    by_order = {}
    for issue in issues:
        m = ORDER.match(issue['title'])
        if m:
            by_order[(m.group(1) + m.group(2)).lower()] = issue['number']
            by_order[(m.group(1).lstrip('0') or '0') + m.group(2)] = issue['number']
    rows = []
    for issue in sorted(issues, key=lambda i: (order_key(i['title']), i['number'])):
        status, ask = status_of(issue, open_numbers, by_order, closes)
        rows.append({'number': issue['number'], 'order': ORDER.match(issue['title']).group(0).rstrip(' ·') if ORDER.match(issue['title']) else '',
                     'title': ORDER.sub('', issue['title']).strip(), 'labels': [l['name'] for l in issue.get('labels', [])],
                     'status': status, 'ask': ask, 'url': issue['html_url']})
    approved = [pr for pr in pulls if agent.LABELS['approve'] in [l['name'] for l in pr.get('labels', [])]]
    files = {pr['number']: agent.changed_paths(gh(f'repos/{REPO}/pulls/{pr["number"]}/files?per_page=100')) for pr in approved}
    marks = {pr['number']: agent.review_marks(gh(f'repos/{REPO}/issues/{pr["number"]}/comments?per_page=100')) for pr in approved}
    statuses = {pr['number']: gh(f'repos/{REPO}/commits/{pr["head"]["sha"]}/statuses?per_page=100') for pr in approved}
    batch = merge_batch(approved, {i['number']: i for i in issues}, files, marks, statuses)
    if '--json' in argv:
        print(json.dumps({'issues': rows, 'merge_batch': batch}, indent=1))
        return 0
    width = max(len(r['title']) for r in rows) if rows else 10
    print(f"{'#':>4}  {'order':5}  {'title':{min(width, 78)}}  {'labels':13}  status")
    for r in rows:
        title = r['title'] if len(r['title']) <= 78 else r['title'][:75] + '...'
        print(f"{r['number']:>4}  {r['order']:5}  {title:{min(width, 78)}}  {','.join(r['labels'])[:13]:13}  {r['status']}")
    ready = [r for r in rows if r['status'] == 'ready'][:3]
    print('\nNext three to start:', ', '.join(f"#{r['number']}" for r in ready) or 'none')
    owed = [r for r in rows if r['status'] == 'waiting on owner']
    print('The owner owes:' if owed else 'The owner owes nothing.')
    for r in owed:
        print(f"  #{r['number']}: {r['ask'] or r['title']}")
    print(f"\nMerge batch ({agent.LABELS['approve']}, in merge order):" if batch else f"\nMerge batch: no pull request is labelled {agent.LABELS['approve']}.")
    for r in batch:
        tier = (f"auto tier: merges itself after CI when AGENT_AUTO_MERGE is on, else /approve-pr {r['number']}" if r['tier'] == 'auto'
                else f"owner: /approve-pr {r['number']}")
        print(f"  #{r['number']}  closes #{r['issue'] or '?'}  {r['title'][:70]}  [{tier}]")
    print(f'\n{len(rows)} open issues, {len(pulls)} open pull requests.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
