"""The `/issues` skill's status rules (`.claude/skills/issues/backlog.py`, issue #46), offline.

The script's only I/O is `gh api`; everything else is pure and tested here with issues shaped
like the real ones, so a wrong status is caught without a network call.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backlog', ROOT / '.claude' / 'skills' / 'issues' / 'backlog.py')
backlog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backlog)


def issue(number, title, body='', labels=()):
    return {'number': number, 'title': title, 'body': body, 'labels': [{'name': l} for l in labels],
            'html_url': f'https://github.com/x/y/issues/{number}'}


ISSUES = [
    issue(20, '04 · URL-addressable questions: the Datasette model', '## Design'),
    issue(21, '05 · Drill-down by clicking', '**Order 5 of 13.** Depends on #04.'),
    issue(34, '08c · Precompute per-shift KPIs', '**Order: after 08b.**'),              # 08b is closed: not blocked
    issue(35, '13b · Extend the synthetic dataset', '**Order: after 08c (needs the precomputed KPIs).**'),
    issue(38, 'Production record contract', '**Owner-specified contract.**\n## Open definitions (owner)\n1. HQM?', labels=('documentation', 'waiting-on-owner')),
    issue(41, 'Preview URL per pull request', 'Order: whenever; small.'),
    issue(46, 'Claude Code operating loop', 'a status column: `waiting on owner` when ... what the owner owes'),
]
OPEN = {i['number'] for i in ISSUES}
BY_ORDER = {}
for i in ISSUES:
    m = backlog.ORDER.match(i['title'])
    if m:
        BY_ORDER[(m.group(1) + m.group(2)).lower()] = i['number']
        BY_ORDER[(m.group(1).lstrip('0') or '0') + m.group(2)] = i['number']
CLOSES = {41: 47}


def status(n):
    return backlog.status_of(next(i for i in ISSUES if i['number'] == n), OPEN, BY_ORDER, CLOSES)[0]


def test_order_key_sorts_prefixed_titles_first_then_unprefixed_by_number():
    titles = ['13b · x', '04 · y', '08c · z', '08 · w', 'no prefix']
    assert sorted(titles, key=backlog.order_key) == ['04 · y', '08 · w', '08c · z', '13b · x', 'no prefix']


def test_in_pr_wins_over_everything():
    assert status(41) == 'in PR #47'


def test_blocked_by_resolves_order_prefixes_and_zero_padded_numbers():
    assert status(21) == 'blocked by #20'      # "Depends on #04" -> the issue carrying order 04
    assert status(35) == 'blocked by #34'      # "after 08c" -> the issue carrying order 08c
    assert status(34) == 'ready'               # "after 08b": 08b is not open, so nothing blocks


def test_waiting_on_owner_comes_from_the_label_not_from_prose():
    assert status(38) == 'waiting on owner'
    assert status(46) == 'ready', 'an issue that merely mentions the owner is not waiting on them'
    _, ask = backlog.status_of(next(i for i in ISSUES if i['number'] == 38), OPEN, BY_ORDER, CLOSES)
    assert ask.startswith('Owner-specified contract')


def test_closing_keywords_match_github_forms():
    body = 'Fixes #12, resolves #13 and Closes #41.'
    assert [int(m.group(1)) for m in backlog.CLOSES.finditer(body)] == [12, 13, 41]
