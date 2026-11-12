"""The agent workflows of issue #126: the builder (`.github/workflows/agent.yml`), the reviewer
(`.github/workflows/agent-review.yml`) and their rules (`.github/scripts/agent.py`).

The rules are pure functions and are tested here directly: which events start a run, the branch a run
works on and when it refuses (one pull request per issue), the queue that lets three agents run at
once, the merge tier, the golden check and the verdict. The script's subcommands are then run end to
end against a stand-in `gh` (as test_repo_hygiene.py runs the deploy's smoke test against a stand-in
curl), which is how the acceptance criteria are shown without GitHub: labelling an issue twice opens
one pull request, a fix-first verdict dispatches exactly one fix round on the same branch, a docs-only
approve turns on auto-merge and a `src/` approve does not. The workflow files are checked as text (CI
installs no YAML library) and parsed when PyYAML is present.
"""
import importlib.util
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github' / 'scripts' / 'agent.py'
BUILDER = ROOT / '.github' / 'workflows' / 'agent.yml'
REVIEWER = ROOT / '.github' / 'workflows' / 'agent-review.yml'

spec = importlib.util.spec_from_file_location('agent_rules', SCRIPT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
bspec = importlib.util.spec_from_file_location('backlog', ROOT / '.claude' / 'skills' / 'issues' / 'backlog.py')
backlog = importlib.util.module_from_spec(bspec)
bspec.loader.exec_module(backlog)

OWNER = 'justfetz'
TITLE = "Agents start from a label: Claude Code GitHub Action on 'agent-ready' issues, automatic reviewer on every PR"


def pull(number, ref, body='', labels=(), sha='a' * 40, state='open'):
    return {'number': number, 'title': f'PR {number}', 'body': body, 'state': state, 'labels': [{'name': l} for l in labels],
            'head': {'ref': ref, 'sha': sha, 'repo': {'full_name': 'o/r'}}}


# ------------------------------------------------------------------------------------------- the rules

def test_branch_names_follow_claude_md():
    assert agent.branch_name(126, TITLE) == 'issue-126-agents-start-from-a-label-claude-code'
    assert len(agent.slug(TITLE)) <= 40
    assert agent.branch_name(20, '04 · URL-addressable questions: the Datasette model') == 'issue-20-url-addressable-questions-the-datasette'
    assert agent.slug('') == 'work' and agent.slug('x' * 60) == 'x' * 40


def test_the_backlog_and_the_workflows_read_closes_the_same_way():
    assert backlog.CLOSES is agent.CLOSES
    assert agent.linked_issues('Closes #126. Also fixes #12, resolves #126 again') == [126, 12]
    assert agent.linked_issue('nothing here') is None


@pytest.mark.parametrize('event, plan', [
    ({'action': 'labeled', 'label': {'name': 'agent-ready'}, 'issue': {'number': 7}}, ('build', '7', '', 'true')),
    ({'action': 'unlabeled', 'label': {'name': 'agent-ready'}, 'issue': {'number': 7}}, ('stop', '7', '', 'true')),
    ({'action': 'labeled', 'label': {'name': 'bug'}, 'issue': {'number': 7}}, ('skip', '', '', 'false')),
    ({'action': 'labeled', 'label': {'name': 'agent-ready'}, 'issue': {'number': 7, 'pull_request': {}}}, ('skip', '', '', 'false')),
])
def test_the_label_starts_and_stops_a_build(event, plan):
    got = agent.event_plan('issues', event, OWNER)
    assert (got['mode'], got['issue'], got['pr'], got['needs_label']) == plan


def comment_event(body, login=OWNER, pr=False):
    return {'action': 'created', 'comment': {'body': body, 'user': {'login': login}},
            'issue': {'number': 9, **({'pull_request': {'url': 'x'}} if pr else {})}}


def test_only_the_owners_at_claude_comment_starts_the_agent():
    on_issue = agent.event_plan('issue_comment', comment_event('@claude please start'), OWNER)
    assert (on_issue['mode'], on_issue['issue'], on_issue['needs_label'], on_issue['add_label']) == ('build', '9', 'true', 'true'), \
        'the same as the label: the gate puts agent-ready on the issue'
    on_pr = agent.event_plan('issue_comment', comment_event('@claude rename the helper', pr=True), OWNER)
    assert (on_pr['mode'], on_pr['pr'], on_pr['request']) == ('fix', '9', '@claude rename the helper')
    for event in (comment_event('@claude start', login='someone-else'), comment_event('no mention at all'),
                  comment_event('ping @claude-bot'), comment_event('mail me at me@claude.ai'),
                  {**comment_event('@claude'), 'action': 'edited'}):
        assert agent.event_plan('issue_comment', event, OWNER)['mode'] == 'skip', event


def test_the_reviewer_dispatches_fix_rounds_into_the_issues_concurrency_group():
    build = agent.event_plan('workflow_dispatch', {'inputs': {'issue': '12'}}, OWNER)
    fix = agent.event_plan('workflow_dispatch', {'inputs': {'issue': '12', 'pr': '40', 'round': '1'}}, OWNER)
    assert (build['mode'], fix['mode'], fix['pr'], fix['round']) == ('build', 'fix', '40', '1')
    assert agent.concurrency_key(build['issue'], build['pr']) == agent.concurrency_key(fix['issue'], fix['pr']) == 'issue-12'
    assert agent.concurrency_key('', '40') == 'pr-40'
    assert agent.event_plan('workflow_dispatch', {'inputs': {'issue': 'twelve'}}, OWNER)['mode'] == 'skip'


def test_one_pull_request_per_issue():
    first = agent.plan_branch(126, TITLE, [], [])
    assert first == {'action': 'create', 'branch': 'issue-126-agents-start-from-a-label-claude-code', 'reason': 'new branch from main'}
    opened = pull(200, first['branch'], body='Closes #126')
    again = agent.plan_branch(126, TITLE, [opened], [first['branch']])
    assert again['action'] == 'refuse' and again['pr'] == 200 and 'already in PR #200' in again['reason']
    # a pull request from the issue's branch counts even if its body forgot "Closes #126"
    assert agent.plan_branch(126, TITLE, [pull(201, 'issue-126-x')], ['issue-126-x'])['action'] == 'refuse'
    # one that closes it from any branch counts too (a session's PR)
    assert agent.plan_branch(126, TITLE, [pull(202, 'claude/happy', body='Fixes #126')], [])['action'] == 'refuse'
    # another issue's pull request does not
    assert agent.plan_branch(126, TITLE, [pull(203, 'issue-12-y', body='Closes #12')], [])['action'] == 'create'


def test_a_branch_left_without_a_pull_request_is_resumed_and_a_closed_ones_is_not():
    assert agent.plan_branch(126, TITLE, [], ['issue-126-old-title']) == {
        'action': 'resume', 'branch': 'issue-126-old-title', 'reason': 'an earlier run stopped before its pull request'}
    closed = agent.plan_branch(126, TITLE, [], ['issue-126-old'], [pull(150, 'issue-126-old', state='closed')])
    assert closed['action'] == 'refuse' and 'PR #150' in closed['reason'] and 'delete the branch' in closed['reason']
    two = agent.plan_branch(126, TITLE, [], ['issue-126-a', 'issue-126-b', 'issue-1260-c'])
    assert two['action'] == 'refuse' and '2 branches' in two['reason'], 'issue-1260- is another issue'


def run(run_id, status='in_progress'):
    return {'id': run_id, 'status': status}


def test_at_most_three_agents_run_at_once_first_come_first_served():
    me = 100
    three = [run(10), run(20, status='queued'), run(30, status='waiting')]
    assert agent.queue_ahead(three, me) == [10, 20, 30] and len(agent.queue_ahead(three, me)) >= agent.MAX_AGENTS
    assert agent.queue_ahead(three[:2], me) == [10, 20]   # two ahead: this one starts
    assert agent.queue_ahead([run(5, status='completed'), run(150), run(160)], me) == [], 'finished and later runs are not ahead'
    assert agent.MAX_AGENTS == 3 and agent.MAX_ROUNDS == 2


@pytest.mark.parametrize('files, expected', [
    (['docs/AGENTS.md'], 'auto'),
    (['tests/test_agent.py', 'tests/ui_fixtures/why_slow.json'], 'auto'),
    (['README.md', 'evals/README.md', 'docs/TESTING.md'], 'auto'),
    (['docs/AGENTS.md', 'src/investigator/agent.py'], 'owner'),
    (['src/investigator/web/app.js'], 'owner'),
    (['evals/golden.json'], 'owner'),
    (['evals/golden_values.json', 'tests/test_evals.py'], 'owner'),
    (['src/investigator/synth.py'], 'owner'),
    (['src/investigator/cost_model.py', 'src/investigator/rates.json'], 'owner'),
    (['CLAUDE.md'], 'owner'),
    (['.github/workflows/agent.yml'], 'owner'),
    ([], 'owner'),
])
def test_only_docs_and_tests_are_in_the_auto_merge_tier(files, expected):
    assert agent.tier(files) == expected


def test_a_rename_counts_by_both_paths():
    listing = [{'filename': 'docs/settings.json', 'previous_filename': '.claude/settings.json', 'status': 'renamed'},
               {'filename': 'docs/AGENTS.md', 'status': 'modified'}]
    files = agent.changed_paths(listing)
    assert files == ['docs/settings.json', '.claude/settings.json', 'docs/AGENTS.md']
    d = agent.decide(**GREEN, files=files)
    assert (d['verdict'], d['kind'], d['next'], d['tier']) == ('needs-owner', 'eyes', None, 'owner')
    out_of_src = agent.changed_paths([{'filename': 'docs/cost_model.py', 'previous_filename': 'src/investigator/cost_model.py'}])
    assert agent.tier(out_of_src) == 'owner'


def test_a_claude_md_or_claude_folder_at_any_depth_needs_the_owner():
    assert agent.owner_eyes(['docs/CLAUDE.md', 'tests/CLAUDE.local.md', 'tests/.claude/settings.json', 'docs/claude.md',
                             'docs/NOT_CLAUDE.md.txt']) == ['docs/CLAUDE.md', 'tests/CLAUDE.local.md', 'tests/.claude/settings.json']
    d = agent.decide(**GREEN, files=['docs/CLAUDE.md'])
    assert agent.tier(['docs/CLAUDE.md']) == 'auto' and (d['verdict'], d['next']) == ('needs-owner', None), \
        'in the docs tier by path, but instructions to every reviewer: never auto-merged'


def test_what_the_owner_looks_at():
    assert agent.owner_eyes(['src/investigator/web/style.css', 'src/investigator/server.py', 'Dockerfile', '.github/x.yml',
                             '.claude/skills/a/SKILL.md', 'CLAUDE.md', 'deploy/huggingface/README.md', 'docs/A.md',
                             'src/investigator/analysis.py']) == [
        'src/investigator/web/style.css', 'src/investigator/server.py', 'Dockerfile', '.github/x.yml',
        '.claude/skills/a/SKILL.md', 'CLAUDE.md', 'deploy/huggingface/README.md']


def golden_text(cases):
    return json.dumps({'about': 'x', 'cases': cases})


def test_golden_problems_catch_deleted_cases_removed_keys_and_loosened_budgets():
    base = (ROOT / 'evals' / 'golden.json').read_text(encoding='utf-8')
    assert agent.golden_problems(base, base, 'evals/golden.json') == []
    data = json.loads(base)
    first = data['cases'][0]
    grown = json.loads(base)
    grown['cases'].append({**first, 'id': 'a-new-case'})
    grown['cases'][1]['expect']['new_key'] = True
    assert agent.golden_problems(base, json.dumps(grown), 'evals/golden.json') == [], 'adding is never weakening'
    weak = json.loads(base)
    del weak['cases'][0]
    weak['cases'][0]['expect'].pop(sorted(weak['cases'][0]['expect'])[0])
    problems = agent.golden_problems(base, json.dumps(weak), 'evals/golden.json')
    assert problems[0] == f"evals/golden.json: case `{first['id']}` was deleted"
    assert any('lost `expect.' in p for p in problems)
    cases = [{'id': 'v', 'mode': 'offline', 'expect': {'values': {'a': 1, 'b': 2}, 'max_ms': 250, 'max_cost_usd': 0.01}}]
    loosened = [{'id': 'v', 'mode': 'live', 'expect': {'values': {'a': 1}, 'max_ms': 500, 'max_cost_usd': 0.01}}]
    assert agent.golden_problems(golden_text(cases), golden_text(loosened), 'g') == [
        'g: case `v` changed mode from offline to live', 'g: case `v` lost `expect.values.b`',
        'g: case `v` raised `expect.max_ms` from 250 to 500']
    assert agent.golden_problems(golden_text(cases), None, 'g') == ['g was deleted']
    assert agent.golden_problems(None, golden_text(cases), 'g') == [], 'a new golden file is not a weakening'


def test_every_other_change_to_an_existing_case_is_listed_for_the_reviewer():
    cases = [{'id': 'r', 'mode': 'offline', 'question': 'Why?', 'expect': {'forbidden': ['a', 'b'], 'status': 'complete', 'values': {'k': 1}}}]
    changed = [{'id': 'r', 'mode': 'offline', 'question': 'Why not?', 'expect': {'forbidden': ['a'], 'status': 'complete', 'values': {'k': 999}}},
               {'id': 'new', 'mode': 'offline', 'expect': {}}]
    assert agent.golden_problems(golden_text(cases), golden_text(changed), 'g') == [], 'none of these is a hard failure'
    assert agent.golden_changes(golden_text(cases), golden_text(changed), 'g') == [
        'g: case `r` changed `question`', 'g: case `r` changed `expect.forbidden`: ["a", "b"] -> ["a"]',
        'g: case `r` changed `expect.values`: {"k": 1} -> {"k": 999}']
    assert agent.golden_changes(golden_text(cases), golden_text(cases), 'g') == []


GREEN = dict(issue=126, ci='success', evals='pass')


def test_a_docs_only_approve_auto_merges_and_a_src_approve_does_not():
    docs = agent.decide(**GREEN, files=['docs/AGENTS.md', 'tests/test_agent_workflows.py'])
    assert (docs['verdict'], docs['label'], docs['next'], docs['tier']) == ('approve', 'review: approve', 'auto-merge', 'auto')
    src = agent.decide(**GREEN, files=['src/investigator/analysis.py', 'tests/test_analysis.py'])
    assert (src['verdict'], src['label'], src['next'], src['tier']) == ('approve', 'review: approve', None, 'owner')


def test_fix_first_goes_back_to_the_builder_twice_then_to_the_owner():
    gap = [{'where': 'src/investigator/agent.py:10', 'gap': 'criterion 2 is not shown'}]
    for rounds in (0, 1):
        d = agent.decide(**GREEN, files=['src/x.py'], gaps=gap, rounds=rounds, agent_built=True)
        assert (d['verdict'], d['next']) == ('fix-first', 'fix') and d['reasons'] == ['src/investigator/agent.py:10: criterion 2 is not shown']
    done = agent.decide(**GREEN, files=['src/x.py'], gaps=gap, rounds=2, agent_built=True)
    assert (done['verdict'], done['kind'], done['next']) == ('needs-owner', 'unresolved', None)
    assert done['reasons'][0].startswith('2 fix rounds did not close')
    theirs = agent.decide(**GREEN, files=['src/x.py'], gaps=gap, rounds=5, agent_built=False)
    assert (theirs['verdict'], theirs['next']) == ('fix-first', None), "someone else's pull request is labelled, never pushed to"


def test_measured_failures_are_fix_first_whatever_the_reviewer_said():
    for kwargs, text in ((dict(ci='failure', ci_detail='`deterministic` failure'), 'CI failed'),
                         (dict(evals='fail', evals_detail='293 passed, 1 failed'), 'evals/run.py` failed'),
                         (dict(golden=['evals/golden.json: case `why-slow` was deleted']), 'was deleted'),
                         (dict(conflicts=True), 'conflicts with main')):
        d = agent.decide(**{**GREEN, **kwargs}, files=['docs/a.md'])
        assert d['verdict'] == 'fix-first' and any(text in r for r in d['reasons']), (kwargs, d)


@pytest.mark.parametrize('kwargs, text, kind', [
    (dict(issue=None), 'names no issue', 'unresolved'),
    (dict(reviewer=False), 'wrote no verdict', 'unresolved'),
    (dict(ci='missing', ci_detail='no `deterministic` check'), 'CI did not pass', 'unresolved'),
    (dict(ci='pending'), 'CI did not pass', 'unresolved'),
    (dict(evals='missing'), 'evals did not run', 'unresolved'),
    (dict(issue_labels=['waiting-on-owner']), 'waiting-on-owner', 'eyes'),
    (dict(files=['src/investigator/web/app.js']), 'src/investigator/web/app.js', 'eyes'),
    (dict(files=['.github/workflows/agent.yml']), '.github/workflows/agent.yml', 'eyes'),
    (dict(files=['Dockerfile', 'docs/DEPLOYMENT.md']), 'Dockerfile', 'eyes'),
])
def test_needs_owner(kwargs, text, kind):
    d = agent.decide(**{**GREEN, 'files': ['docs/a.md'], **kwargs})
    assert (d['verdict'], d['label'], d['next'], d['kind']) == ('needs-owner', 'review: needs-owner', None, kind)
    assert text in ' '.join(d['reasons'])


def as_comment(body, login=agent.REVIEW_BOT, cid=1):
    return {'id': cid, 'user': {'login': login}, 'body': body}


def test_the_verdict_comment_carries_its_head_round_and_gaps():
    gaps = [{'where': 'a.py:1', 'gap': 'one'}, {'where': 'b.py:2', 'gap': 'two'}]
    d = agent.decide(**GREEN, files=['src/x.py'], gaps=gaps, agent_built=True)
    body = agent.verdict_comment(d, sha='c' * 40, issue=126, number=200, rnd=1, run_url='https://run', checks=['CI: success'])
    assert body.startswith(f'<!-- agent-review sha={"c" * 40} verdict=fix-first round=1 -->\n**Review: fix first**')
    assert '@claude fix the gaps above on this branch (fix round 1 of 2)' in body
    assert body.rstrip().endswith('_Generated by [Claude Code](https://claude.ai/code)_')
    assert agent.gaps_of(body) == '1. a.py:1: one\n2. b.py:2: two'
    marks = agent.review_marks([as_comment(body), as_comment(body.replace('round=1', 'round=2'), login=OWNER, cid=2)])
    assert [(m['sha'][:3], m['verdict'], m['round']) for m in marks] == [('ccc', 'fix-first', 1)], 'only the workflow writes verdicts'
    assert agent.rounds_spent([as_comment(body)]) == 1 and agent.rounds_spent([]) == 0
    assert agent.round_for_head([as_comment(body)], 'c' * 40) == 1 and agent.round_for_head([as_comment(body)], 'd' * 40) == 0
    again = agent.verdict_comment(d, sha='c' * 40, issue=126, number=200, rnd=1, run_url='u', checks=[], already=True)
    assert 'already sent back as fix round 1 of 2' in again and '@claude' not in again
    approve = agent.verdict_comment(agent.decide(**GREEN, files=['docs/a.md']), sha='d' * 40, issue=126, number=201, rnd=0,
                                    run_url='u', checks=[])
    assert 'Auto-merge is on' in approve and '@claude' not in approve
    owner = agent.verdict_comment(agent.decide(**GREEN, files=['src/a.py']), sha='d' * 40, issue=126, number=201, rnd=0,
                                  run_url='u', checks=[])
    assert '/approve-pr 201' in owner and 'Auto-merge' not in owner


def test_status_reads_the_label_the_way_the_workflow_writes_it():
    sha = 'e' * 40
    approve = agent.verdict_comment(agent.decide(**GREEN, files=['src/a.py']), sha=sha, issue=126, number=5, rnd=0, run_url='u', checks=[])
    marks = agent.review_marks([as_comment(approve)])
    row = agent.status_row(pull(5, 'issue-126-x', labels=['review: approve'], sha=sha), ['src/a.py'], marks)
    assert (row['status'], row['tier'], row['rounds']) == ('review: approve', 'owner', 0)
    moved = agent.status_row(pull(5, 'issue-126-x', labels=['review: approve'], sha='f' * 40), ['src/a.py'], marks)
    assert moved['status'] == 'review: stale', 'a label from an older head is not an approve'
    relabelled = agent.status_row(pull(5, 'issue-126-x', labels=['review: needs-owner'], sha=sha), ['src/a.py'], marks)
    assert relabelled['status'] == 'review: stale'
    assert agent.status_row(pull(5, 'b'), [], [])['status'] == 'review: none'
    for files, kind in ((['src/investigator/web/app.js'], 'eyes'), (['src/a.py'], 'unresolved')):
        d = agent.decide(**GREEN, files=files) if kind == 'eyes' else agent.decide(**GREEN, files=files, reviewer=False)
        marks = agent.review_marks([as_comment(agent.verdict_comment(d, sha=sha, issue=126, number=5, rnd=0, run_url='u', checks=[]))])
        row = agent.status_row(pull(5, 'b', labels=['review: needs-owner'], sha=sha), files, marks)
        assert (row['status'], row['kind']) == ('review: needs-owner', kind)


def test_ci_state():
    def check(conclusion, status='completed', name='deterministic'):
        return {'name': name, 'status': status, 'conclusion': conclusion, 'html_url': 'https://x'}
    assert agent.ci_state([])[0] == 'missing'
    assert agent.ci_state([check('success', name='judge')])[0] == 'missing'
    assert agent.ci_state([check(None, status='in_progress'), check('success')])[0] == 'pending'
    assert agent.ci_state([check('success'), check('success')])[0] == 'success'
    assert agent.ci_state([check('success'), check('failure')])[0] == 'failure'
    assert agent.ci_state([check('cancelled')])[0] == 'missing'


def test_the_run_cost_comes_from_the_actions_execution_file(tmp_path):
    path = tmp_path / 'execution.json'
    path.write_text(json.dumps([{'type': 'system'}, {'type': 'result', 'subtype': 'success', 'total_cost_usd': 1.234,
                                                      'num_turns': 41, 'duration_ms': 600000}]))
    assert agent.run_cost(str(path)) == {'total_cost_usd': 1.234, 'num_turns': 41, 'duration_ms': 600000, 'subtype': 'success'}
    assert agent.run_cost(str(tmp_path / 'missing.json')) is None and agent.run_cost('') is None
    out = subprocess.run(['python3', str(SCRIPT), 'cost', str(path), '--label', 'agent build'], capture_output=True, text=True)
    assert out.stdout.strip() == 'agent build: $1.23, 41 turns, 10.0 min, success'


# ------------------------------------------------------------------------ the script against a stand-in gh

GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
state = json.load(open(os.environ['STUB_STATE']))
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
if args[0] == 'api':
    path = args[1]
    method = args[args.index('-X') + 1] if '-X' in args else 'GET'
    if method != 'GET':
        print('{}')
        sys.exit(0)
    if path not in state:
        sys.stderr.write('gh: Not Found (HTTP 404)\n')
        print('{"message": "Not Found"}')
        sys.exit(1)
    print(json.dumps([state[path]] if '--slurp' in args else state[path]))
    sys.exit(0)
if args[:2] == ['pr', 'merge'] and '--auto' in args and state.get('merge_clean'):
    sys.stderr.write('GraphQL: Pull request is in clean status (enablePullRequestAutoMerge)\n')
    sys.exit(1)
sys.exit(0)
'''


class Stub:
    """A git checkout with an `origin/main`, a stand-in `gh` that answers GET paths from `state` and logs
    every call, and the environment a workflow step gets."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        (tmp_path / 'bin').mkdir()
        (tmp_path / 'bin' / 'gh').write_text(GH_STUB)
        (tmp_path / 'bin' / 'gh').chmod(0o755)
        self.repo = tmp_path / 'repo'
        self.repo.mkdir()
        git = ['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@t']
        subprocess.run([*git, 'init', '-q', '-b', 'main'], check=True)
        subprocess.run([*git, 'commit', '-q', '--allow-empty', '-m', 'main'], check=True)
        subprocess.run([*git, 'update-ref', 'refs/remotes/origin/main', 'HEAD'], check=True)
        self.git = git
        self.state = {}
        self.event = ('workflow_dispatch', {})
        self.log = tmp_path / 'gh.log'

    def run(self, *args):
        (self.root / 'state.json').write_text(json.dumps(self.state))
        self.log.write_text('')
        out = self.root / 'github_output'
        out.write_text('')
        env = {**os.environ, 'PATH': f'{self.root / "bin"}:{os.environ["PATH"]}', 'STUB_STATE': str(self.root / 'state.json'),
               'STUB_LOG': str(self.log), 'GITHUB_REPOSITORY': 'o/r', 'GITHUB_REPOSITORY_OWNER': 'o', 'GITHUB_OUTPUT': str(out),
               'GITHUB_RUN_ID': '77', 'GITHUB_EVENT_NAME': self.event[0], 'GITHUB_EVENT_PATH': str(self.root / 'event.json')}
        (self.root / 'event.json').write_text(json.dumps(self.event[1]))
        proc = subprocess.run(['python3', str(SCRIPT), *args], cwd=self.repo, env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        text = out.read_text()
        heredoc = re.compile(r'^(\w+)<<(EOF_\w+)\n(.*?)\n\2$', re.M | re.S)
        outputs = {m.group(1): m.group(3) for m in heredoc.finditer(text)}
        outputs.update(re.findall(r'^(\w+)=(.*)$', heredoc.sub('', text), re.M))
        return proc, calls, outputs

    def branch(self):
        return subprocess.run([*self.git, 'branch', '--show-current'], capture_output=True, text=True).stdout.strip()


def posted(calls, path_end):
    """The -f fields of the POST/PATCH calls whose path ends with `path_end`."""
    return [dict(f.split('=', 1) for f in c[c.index('-f') + 1::2] if '=' in f) for c in calls
            if c[0] == 'api' and c[1].endswith(path_end) and '-X' in c and c[c.index('-X') + 1] in ('POST', 'PATCH')]


def test_labelling_an_issue_twice_opens_one_pull_request(tmp_path):
    """Acceptance 1, offline: the first label makes the issue's branch from main and the prompt is the
    repository's /fix-issue; once that run's pull request is open, the second label refuses and says why."""
    stub = Stub(tmp_path)
    stub.state = {'repos/o/r/issues/126': {'number': 126, 'title': TITLE, 'state': 'open', 'labels': [{'name': 'agent-ready'}]},
                  'repos/o/r/pulls?state=open&per_page=100': [], 'repos/o/r/git/matching-refs/heads/issue-126-': []}
    proc, calls, out = stub.run('plan', '--mode', 'build', '--issue', '126', '--needs-label', 'true')
    assert proc.returncode == 0, proc.stderr
    branch = 'issue-126-agents-start-from-a-label-claude-code'
    assert (out['go'], out['branch'], out['prompt']) == ('true', branch, '/fix-issue 126')
    assert stub.branch() == branch, 'the build checks out the issue branch, made from origin/main'
    assert posted(calls, 'issues/126/comments')[0]['body'].startswith(f'Agent run started on #126, branch `{branch}`')
    # the run opened its pull request; the owner labels the issue again
    stub.state['repos/o/r/pulls?state=open&per_page=100'] = [pull(200, branch, body='Closes #126')]
    stub.state['repos/o/r/git/matching-refs/heads/issue-126-'] = [{'ref': f'refs/heads/{branch}'}]
    stub.state[f'repos/o/r/pulls?state=closed&head=o:{branch}&per_page=10'] = []
    proc, calls, out = stub.run('plan', '--mode', 'build', '--issue', '126', '--needs-label', 'true')
    assert proc.returncode == 0 and out == {'go': 'false'}, (proc.stderr, out)
    assert 'already in PR #200' in posted(calls, 'issues/126/comments')[0]['body']
    # the label was removed while the run waited in the queue: it does nothing, says nothing
    stub.state['repos/o/r/issues/126']['labels'] = []
    proc, calls, out = stub.run('plan', '--mode', 'build', '--issue', '126', '--needs-label', 'true')
    assert out == {'go': 'false'} and not posted(calls, 'comments')


def test_verify_makes_the_pull_request_close_the_issue_and_labels_it(tmp_path):
    stub = Stub(tmp_path)
    branch = 'issue-126-x'
    stub.state = {f'repos/o/r/pulls?state=open&head=o:{branch}&per_page=10': [pull(200, branch, body='What changed')]}
    proc, calls, _ = stub.run('verify', '--mode', 'build', '--issue', '126', '--branch', branch)
    assert proc.returncode == 0, proc.stderr
    assert posted(calls, 'pulls/200') == [{'body': 'What changed\n\nCloses #126'}]
    assert posted(calls, 'issues/200/labels') == [{'labels[]': 'agent-built'}]
    stub.state = {f'repos/o/r/pulls?state=open&head=o:{branch}&per_page=10': []}
    proc, calls, _ = stub.run('verify', '--mode', 'build', '--issue', '126', '--branch', branch)
    assert proc.returncode == 1 and 'ended without a pull request' in posted(calls, 'issues/126/comments')[0]['body']


def review_state(stub, *, files, labels=('agent-built',), comments=(), check='success', issue_labels=('agent-ready',), mergeable=True):
    sha = 'b' * 40
    stub.state = {f'repos/o/r/commits/{sha}/check-runs?per_page=100': {'check_runs': [
                      {'name': 'deterministic', 'status': 'completed', 'conclusion': check, 'html_url': 'https://ci'}]},
                  'repos/o/r/pulls/200': {**pull(200, 'issue-126-x', body='Closes #126', labels=labels, sha=sha), 'mergeable': mergeable},
                  'repos/o/r/issues/200': pull(200, 'issue-126-x', labels=labels),
                  'repos/o/r/issues/200/comments?per_page=100': list(comments),
                  'repos/o/r/issues/126': {'number': 126, 'title': TITLE, 'state': 'open', 'labels': [{'name': l} for l in issue_labels]}}
    for folder in ('.agent-facts', '.agent-judge', '.agent-evals'):
        (stub.repo / folder).mkdir(exist_ok=True)
    (stub.repo / '.agent-facts' / 'meta.json').write_text(json.dumps(
        {'pr': 200, 'sha': sha, 'issue': 126, 'issue_labels': list(issue_labels), 'files': files, 'golden': []}))
    (stub.repo / '.agent-judge' / 'gaps.json').unlink(missing_ok=True)
    (stub.repo / '.agent-evals' / 'evals.exit').write_text('0\n')
    (stub.repo / '.agent-evals' / 'evals.txt').write_text('...\n294 passed, 0 failed, 15 skipped\n')
    return sha


VERDICT_ARGS = ('--poll', '0', '--ci-timeout', '0', '--mergeable-tries', '1')


def verdict(stub, sha, gaps):
    (stub.repo / '.agent-judge' / 'gaps.json').write_text(json.dumps(gaps))
    return stub.run('verdict', '--pr', '200', '--sha', sha, *VERDICT_ARGS)


def test_a_fix_first_verdict_comments_labels_and_dispatches_one_fix_round(tmp_path):
    """Acceptance 2, offline: the verdict comment and the `review:` label land on the head, and fix-first on
    the agent's own pull request dispatches agent.yml once, for round 1, on the same pull request."""
    stub = Stub(tmp_path)
    sha = review_state(stub, files=['src/investigator/agent.py'])
    proc, calls, out = verdict(stub, sha, [{'where': 'src/investigator/agent.py:3', 'gap': 'criterion 2 has no test'}])
    assert proc.returncode == 0, proc.stderr
    assert (out['verdict'], out['next'], out['round']) == ('fix-first', 'fix', '1')
    body = posted(calls, 'issues/200/comments')[0]['body']
    assert body.startswith(f'<!-- agent-review sha={sha} verdict=fix-first round=1 -->')
    assert '1. src/investigator/agent.py:3: criterion 2 has no test' in body and '@claude fix the gaps above' in body
    assert posted(calls, 'issues/200/labels') == [{'labels[]': 'review: fix-first'}]
    dispatches = [c for c in calls if c[:2] == ['workflow', 'run']]
    assert dispatches == [['workflow', 'run', 'agent.yml', '--repo', 'o/r', '--ref', 'main', '-f', 'issue=126', '-f', 'pr=200', '-f', 'round=1']]
    assert not [c for c in calls if c[:2] == ['pr', 'merge']]
    # two rounds spent: the third fix-first goes to the owner, nothing is dispatched
    spent = [as_comment(f'<!-- agent-review sha={"9" * 40} verdict=fix-first round={r} -->', cid=r) for r in (1, 2)]
    sha = review_state(stub, files=['src/investigator/agent.py'], comments=spent)
    proc, calls, out = verdict(stub, sha, [{'where': 'a.py:1', 'gap': 'still missing'}])
    assert (out['verdict'], out['next']) == ('needs-owner', '') and not [c for c in calls if c[:2] == ['workflow', 'run']]
    # the owner removed agent-ready: the gaps are labelled, the agent is not sent back
    sha = review_state(stub, files=['src/investigator/agent.py'], issue_labels=())
    proc, calls, out = verdict(stub, sha, [{'where': 'a.py:1', 'gap': 'x'}])
    assert (out['verdict'], out['next']) == ('fix-first', '') and not [c for c in calls if c[:2] == ['workflow', 'run']]


def test_the_fix_round_starts_only_for_the_verdict_that_sent_it(tmp_path):
    stub = Stub(tmp_path)
    sha = 'b' * 40
    body = f'<!-- agent-review sha={sha} verdict=fix-first round=1 -->\n**Review: fix first**\n\n1. a.py:1: the gap\n'
    stub.state = {'repos/o/r/pulls/200': pull(200, 'issue-126-x', body='Closes #126', labels=['agent-built'], sha=sha),
                  'repos/o/r/issues/200/comments?per_page=100': [as_comment(body)],
                  'repos/o/r/issues/126': {'number': 126, 'title': TITLE, 'state': 'open', 'labels': [{'name': 'agent-ready'}]}}
    subprocess.run([*stub.git, 'update-ref', 'refs/remotes/origin/issue-126-x', 'HEAD'], check=True)
    proc, calls, out = stub.run('plan', '--mode', 'fix', '--issue', '126', '--pr', '200', '--round', '1', '--needs-label', 'true')
    assert proc.returncode == 0 and out['go'] == 'true', (proc.stderr, out)
    assert stub.branch() == 'issue-126-x' and out['before'] == sha
    assert 'fix round 1 of 2' in out['prompt'] and '1. a.py:1: the gap' in out['prompt'] and 'Do not open another pull request' in out['prompt']
    # a stale dispatch (round 2 asked, but the newest verdict is round 1) does not start
    subprocess.run([*stub.git, 'checkout', '-q', 'main'], check=True)
    subprocess.run([*stub.git, 'branch', '-q', '-D', 'issue-126-x'], check=True)
    proc, calls, out = stub.run('plan', '--mode', 'fix', '--issue', '126', '--pr', '200', '--round', '2', '--needs-label', 'true')
    assert out == {'go': 'false'}


def test_a_docs_only_approve_turns_on_auto_merge_and_a_src_approve_does_not(tmp_path):
    """Acceptance 3, offline: GitHub auto-merge with a merge commit for docs and tests only, pinned to the
    reviewed head; nothing for a pull request that touches src/."""
    stub = Stub(tmp_path)
    sha = review_state(stub, files=['docs/AGENTS.md', 'tests/test_agent_workflows.py'])
    proc, calls, out = verdict(stub, sha, [])
    assert proc.returncode == 0 and (out['verdict'], out['next']) == ('approve', 'auto-merge'), proc.stderr
    assert [c for c in calls if c[:2] == ['pr', 'merge']] == [['pr', 'merge', '200', '--auto', '--merge', '--match-head-commit', sha, '--repo', 'o/r']]
    assert posted(calls, 'issues/200/labels') == [{'labels[]': 'review: approve'}]
    # checks already green, so GitHub will not queue it: merged at once, still pinned to the head
    stub.state['merge_clean'] = True
    proc, calls, out = verdict(stub, sha, [])
    assert [c for c in calls if c[:2] == ['pr', 'merge']][-1] == ['pr', 'merge', '200', '--merge', '--match-head-commit', sha, '--repo', 'o/r']
    stub.state.pop('merge_clean')
    sha = review_state(stub, files=['src/investigator/analysis.py', 'docs/METRICS.md'])
    proc, calls, out = verdict(stub, sha, [])
    assert (out['verdict'], out['next']) == ('approve', '') and not [c for c in calls if c[:2] in (['pr', 'merge'], ['workflow', 'run'])]
    assert '/approve-pr 200' in posted(calls, 'issues/200/comments')[0]['body']


def test_no_reviewer_verdict_and_failing_ci(tmp_path):
    stub = Stub(tmp_path)
    sha = review_state(stub, files=['docs/a.md'])
    proc, calls, out = stub.run('verdict', '--pr', '200', '--sha', sha, *VERDICT_ARGS)
    assert out['verdict'] == 'needs-owner' and 'wrote no verdict' in posted(calls, 'issues/200/comments')[0]['body']
    assert 'verdict=needs-owner round=0 kind=unresolved -->' in posted(calls, 'issues/200/comments')[0]['body']
    sha = review_state(stub, files=['docs/a.md'], check='failure')
    proc, calls, out = verdict(stub, sha, [])
    assert (out['verdict'], out['next']) == ('fix-first', 'fix')


# ------------------------------------------------------------------------------------ the workflow files

def job(text, name):
    """One job's block of a workflow, as text."""
    start = text.index(f'\n  {name}:\n')
    nxt = re.search(r'\n  [a-z]+:\n', text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def step_script(text, name):
    start = text.index('run: |\n', text.index(f'- name: {name}')) + len('run: |\n')
    lines = []
    for line in text[start:].splitlines():
        if line.strip() and not line.startswith(' ' * 10):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines)) + '\n'


def test_the_builder_starts_from_the_label_or_the_owners_comment_only():
    text = BUILDER.read_text(encoding='utf-8')
    on = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
    assert re.search(r'^  issues:\n    types: \[labeled, unlabeled\]$', on, re.M)
    assert re.search(r'^  issue_comment:\n    types: \[created\]$', on, re.M)
    assert re.search(r'^  workflow_dispatch:$', on, re.M) and 'pull_request' not in on
    gate = job(text, 'gate')
    assert "github.event.label.name == 'agent-ready'" in gate
    assert 'github.event.comment.user.login == github.repository_owner' in gate and "contains(github.event.comment.body, '@claude')" in gate
    assert 'python3 .github/scripts/agent.py gate' in gate


def test_the_builder_runs_once_per_issue_three_at_a_time_and_stops_on_unlabel():
    text = BUILDER.read_text(encoding='utf-8')
    build, stop, queue = job(text, 'build'), job(text, 'stop'), job(text, 'queue')
    group = 'group: agent-${{ needs.gate.outputs.key }}'
    assert group in build and 'cancel-in-progress: false' in build
    assert group in stop and 'cancel-in-progress: true' in stop and "needs.gate.outputs.mode == 'stop'" in stop
    assert 'needs: [gate, queue]' in build and 'agent.py queue --run-id "$GITHUB_RUN_ID"' in queue
    assert 'timeout-minutes: 60' in build
    assert "--max-turns ${{ vars.AGENT_MAX_TURNS || '200' }}" in build
    assert 'concurrency:' not in text.split('\njobs:', 1)[0], 'no workflow-level group: every comment would queue in it'


def test_the_builder_pushes_as_the_app_and_runs_the_repositorys_procedure():
    build = job(BUILDER.read_text(encoding='utf-8'), 'build')
    assert 'uses: anthropics/claude-code-action@v1' in build
    assert 'prompt: ${{ steps.plan.outputs.prompt }}' in build
    assert 'persist-credentials: false' in build and 'github_token:' not in build, \
        "a GITHUB_TOKEN push starts no workflow: Tests, Preview and the review would never see the agent's commits"
    for permission in ('contents: write', 'pull-requests: write', 'issues: write', 'id-token: write'):
        assert permission in build
    allowed = re.search(r'--allowedTools "([^"]+)"', build).group(1).split(',')
    denied = re.search(r'--disallowedTools "([^"]+)"', build).group(1).split(',')
    assert 'Bash(gh pr create:*)' in allowed and 'Bash(git push:*)' in allowed
    assert not [t for t in allowed if re.match(r'Bash\((gh pr merge|gh api|curl|wget|git rebase)', t)]
    for rule in ('Bash(gh pr merge:*)', 'Bash(gh api:*)', 'Bash(git push --force:*)', 'Bash(git rebase:*)', 'Edit(.github/**)', 'Write(.github/**)'):
        assert rule in denied, rule
    order = [build.index(s) for s in ('Choose the branch, or refuse', 'Run Claude Code', 'Check the outcome', 'Record the run')]
    assert order == sorted(order)


def test_the_reviewer_runs_mains_rules_and_runs_pr_code_only_without_secrets():
    text = REVIEWER.read_text(encoding='utf-8')
    on = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
    assert re.search(r'^  pull_request_target:\n    types: \[opened, synchronize, reopened, ready_for_review\]$', on, re.M)
    assert not re.search(r'^  pull_request:', on, re.M), 'the PR would run its own version of the review rules'
    reset, evals, judge, verdict = (job(text, n) for n in ('reset', 'evals', 'judge', 'verdict'))
    assert 'github.event.pull_request.head.repo.full_name == github.repository' in reset, 'forks never reach a job'
    assert 'secrets.' not in evals and 'GH_TOKEN' not in evals and 'permissions:\n      contents: read\n    steps' in evals
    for block in (reset, judge, verdict):
        assert 'ref: main' in block, 'the scripts and the .claude/ config come from main'
    assert 'path: pr' in judge and 'python3 ../.github/scripts/agent.py prepare' in judge
    assert 'github_token: ${{ github.token }}' in judge and 'write' not in judge.split('steps:', 1)[0], 'the model gets a read-only token'
    allowed = re.search(r'--allowedTools "([^"]+)"', judge).group(1).split(',')
    assert set(allowed) == {'Read', 'Glob', 'Grep', 'LS', 'Write', 'Bash(git -C pr diff:*)', 'Bash(git -C pr log:*)', 'Bash(git -C pr show:*)'}
    assert '.claude/agents/reviewer.md' in judge and '.agent-review/gaps.json' in judge
    assert 'agent.py verdict --pr "$PR" --sha "$SHA"' in verdict and 'actions: write' in verdict
    # the facts are the verdict job's own; the other jobs hand on one or two files each, into their own folders
    assert 'path: pr' in verdict and 'python3 ../.github/scripts/agent.py prepare --pr "$PR" --out ../.agent-facts' in verdict
    assert '--facts .agent-facts --gaps .agent-judge/gaps.json --evals .agent-evals' in verdict
    assert 'path: .agent-judge' in verdict and 'path: .agent-evals' in verdict and 'path: .agent-review' not in verdict
    assert 'path: |\n            .agent-evals/evals.exit\n            .agent-evals/evals.txt\n' in evals
    assert 'path: .agent-review/gaps.json' in judge
    assert "needs.reset.outputs.draft != 'true'" in evals and "needs.reset.outputs.draft != 'true'" in judge
    assert '.agent-review/golden.md' in judge and 'do not repeat' not in judge
    assert 'cancel-in-progress: true' in text.split('\njobs:', 1)[0]


@pytest.mark.parametrize('path', [BUILDER, REVIEWER])
def test_no_secret_value_is_in_a_workflow_or_echoed(path):
    text = path.read_text(encoding='utf-8')
    assert set(re.findall(r'secrets\.(\w+)', text)) == {'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'}
    for line in (l.strip() for l in text.splitlines() if '${{ secrets.' in l):
        assert re.fullmatch(r"(anthropic_api_key|claude_code_oauth_token): \$\{\{ secrets\.\w+ \}\}|HAVE_(KEY|OAUTH): \$\{\{ secrets\.\w+ != '' \}\}", line), line
    assert not re.search(r'sk-ant-|ghp_|github_pat_', text)


@pytest.mark.parametrize('path, step', [(BUILDER, 'Check that an Anthropic credential is configured'),
                                        (REVIEWER, 'Check that an Anthropic credential is configured')])
def test_a_run_without_any_credential_says_how_to_add_one(tmp_path, path, step):
    script = step_script(path.read_text(encoding='utf-8'), step)
    output = tmp_path / 'out'
    for have in ({}, {'HAVE_KEY': 'true'}, {'HAVE_OAUTH': 'true'}, {'HAVE_KEY': 'true', 'HAVE_OAUTH': 'true'}):
        output.write_text('')
        env = {**os.environ, 'HAVE_KEY': 'false', 'HAVE_OAUTH': 'false', 'GITHUB_OUTPUT': str(output), **have}
        proc = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
        if not have:
            assert 'ANTHROPIC_API_KEY' in proc.stdout and 'docs/AGENTS.md' in proc.stdout
            assert (proc.returncode, output.read_text()) == ((1, '') if path is BUILDER else (0, ''))
        else:
            assert proc.returncode == 0 and '::' not in proc.stdout
            assert output.read_text() == ('' if path is BUILDER else 'ok=true\n')


def test_the_workflows_parse_when_pyyaml_is_available():
    yaml = pytest.importorskip('yaml')
    builder = yaml.safe_load(BUILDER.read_text())
    reviewer = yaml.safe_load(REVIEWER.read_text())
    assert set(builder['jobs']) == {'gate', 'stop', 'queue', 'build'}
    assert set(reviewer['jobs']) == {'reset', 'evals', 'judge', 'verdict'}
    assert builder['jobs']['build']['timeout-minutes'] == 60 and builder['jobs']['build']['needs'] == ['gate', 'queue']
    assert reviewer['jobs']['verdict']['needs'] == ['reset', 'evals', 'judge']
    assert reviewer['jobs']['verdict']['if'] == "${{ !cancelled() && needs.reset.result == 'success' && needs.reset.outputs.draft != 'true' }}"
    # a ` #` in a plain YAML scalar starts a comment: the stop step's message must survive whole
    assert builder['jobs']['stop']['steps'][0]['run'].rstrip().endswith("cancelled by this job's concurrency group\"")
    claude_args = next(s for s in builder['jobs']['build']['steps'] if s.get('id') == 'claude')['with']['claude_args']
    assert claude_args.startswith("--max-turns ${{ vars.AGENT_MAX_TURNS || '200' }} ") and '--disallowedTools "' in claude_args
    # YAML reads the key `on` as True
    assert set(builder[True]) == {'issues', 'issue_comment', 'workflow_dispatch'}
    assert set(reviewer[True]) == {'pull_request_target', 'workflow_dispatch'}


# ---------------------------------------------------------------------------------- CLAUDE.md and the skills

def test_claude_md_names_the_auto_merge_tier_exactly():
    text = (ROOT / 'CLAUDE.md').read_text(encoding='utf-8')
    line = next(l for l in text.splitlines() if 'Claude never merges on its own' in l)
    tier_clause = line.split('merges itself', 1)[0]
    assert set(re.findall(r'`([^`]+)`', tier_clause)) == {*agent.AUTO_MERGE_DIRS, *agent.AUTO_MERGE_FILES}
    assert agent.LABELS['approve'] in line and '/approve-pr <n>' in line and 'merge commit' in line
    agents = (ROOT / 'docs' / 'AGENTS.md').read_text(encoding='utf-8')
    auto_row = next(l for l in agents.splitlines() if l.startswith('| **auto** |'))
    assert set(re.findall(r'`([^`]+)`', auto_row.split('|')[2])) == {*agent.AUTO_MERGE_DIRS, *agent.AUTO_MERGE_FILES}


def test_review_pr_and_approve_pr_read_the_label_the_way_the_workflow_does():
    for skill in ('review-pr', 'approve-pr'):
        text = (ROOT / '.claude' / 'skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
        assert 'python3 .github/scripts/agent.py status $ARGUMENTS' in text, skill
        for label in agent.LABELS.values():
            assert f'`{label}' in text, (skill, label)   # `review: needs-owner` may carry its kind: `review: needs-owner (eyes)`
        assert '`review: stale`' in text
    approve = (ROOT / '.claude' / 'skills' / 'approve-pr' / 'SKILL.md').read_text(encoding='utf-8')
    assert '`review: needs-owner (eyes)`' in approve and '`review: needs-owner (unresolved)`' in approve
    review = (ROOT / '.claude' / 'skills' / 'review-pr' / 'SKILL.md').read_text(encoding='utf-8')
    assert '`decide` in `.github/scripts/agent.py`' in review
    eyes = next(line for line in review.splitlines() if line.lstrip().startswith("- `needs the owner's eyes"))
    for path in (*agent.OWNER_EYES_DIRS, 'server.py', 'Dockerfile', 'CLAUDE.md'):
        assert f'`{path}`' in eyes, path


def test_the_issues_skill_lists_the_merge_batch_in_merge_order():
    issues = {12: {'number': 12, 'title': '08c · Precompute per-shift KPIs'}, 20: {'number': 20, 'title': '04 · URL-addressable questions'},
              30: {'number': 30, 'title': 'No order prefix'}}
    pulls = [pull(50, 'issue-30-a', body='Closes #30', labels=['review: approve']),
             pull(51, 'issue-12-b', body='Closes #12', labels=['review: approve']),
             pull(52, 'issue-20-c', body='Closes #20', labels=['review: fix-first']),
             pull(53, 'issue-20-d', body='Closes #20', labels=['review: approve']),
             pull(54, 'claude/x', body='no issue', labels=['review: approve'])]
    files = {50: ['docs/A.md'], 51: ['src/investigator/analysis.py'], 53: ['tests/test_urls.py'], 54: ['src/a.py'], 55: ['src/b.py']}
    pulls.append(pull(55, 'issue-12-e', body='Closes #12', labels=['review: approve'], sha='f' * 40))   # pushed since its approve

    def approved(n, sha='a' * 40):
        return [as_comment(f'<!-- agent-review sha={sha} verdict=approve round=0 -->', cid=n)]
    marks = {n: agent.review_marks(approved(n)) for n in (50, 51, 53, 54, 55)}
    batch = backlog.merge_batch(pulls, issues, files, marks)
    assert [(r['number'], r['issue'], r['tier']) for r in batch] == [(53, 20, 'auto'), (51, 12, 'owner'), (50, 30, 'auto'), (54, None, 'owner')]
    assert 55 not in [r['number'] for r in batch], 'an approve for an older head is stale, out of the batch'


def test_a_second_review_of_the_same_head_spends_no_second_round(tmp_path):
    """A re-run of the review (or a reopen) on a head already sent back as round 1 records round 1 again and
    dispatches nothing: only the builder's next push earns round 2."""
    stub = Stub(tmp_path)
    sha = 'b' * 40
    sent = [as_comment(f'<!-- agent-review sha={sha} verdict=fix-first round=1 -->\n1. a.py:1: x', cid=1)]
    sha = review_state(stub, files=['src/investigator/agent.py'], comments=sent)
    proc, calls, out = verdict(stub, sha, [{'where': 'a.py:1', 'gap': 'x'}])
    assert proc.returncode == 0, proc.stderr
    assert (out['verdict'], out['next'], out['round']) == ('fix-first', 'fix', '1')
    assert not [c for c in calls if c[:2] == ['workflow', 'run']], 'no second dispatch for the same head'
    assert 'already sent back as fix round 1 of 2' in posted(calls, 'issues/200/comments')[0]['body']


def test_a_branch_that_conflicts_with_main_is_fix_first(tmp_path):
    stub = Stub(tmp_path)
    sha = review_state(stub, files=['docs/a.md'], mergeable=False)
    proc, calls, out = verdict(stub, sha, [])
    assert out['verdict'] == 'fix-first' and 'conflicts with main' in posted(calls, 'issues/200/comments')[0]['body']
    assert not [c for c in calls if c[:2] == ['pr', 'merge']]


def test_the_owners_comment_on_an_issue_puts_the_label_on_it(tmp_path):
    stub = Stub(tmp_path)
    stub.event = ('issue_comment', {'action': 'created', 'comment': {'body': '@claude go', 'user': {'login': 'o'}}, 'issue': {'number': 31}})
    proc, calls, out = stub.run('gate')
    assert proc.returncode == 0, proc.stderr
    assert (out['mode'], out['issue'], out['key'], out['needs_label']) == ('build', '31', 'issue-31', 'true')
    assert posted(calls, 'issues/31/labels') == [{'labels[]': 'agent-ready'}]
    stub.event = ('issues', {'action': 'unlabeled', 'label': {'name': 'agent-ready'}, 'issue': {'number': 31}})
    proc, calls, out = stub.run('gate')
    assert (out['mode'], out['key']) == ('stop', 'issue-31') and not calls, 'the stop job shares the build\'s group'
    stub.event = ('issue_comment', {'action': 'created', 'comment': {'body': '@claude rename it', 'user': {'login': 'o'}},
                                    'issue': {'number': 200, 'pull_request': {'url': 'x'}}})
    stub.state = {'repos/o/r/pulls/200': pull(200, 'issue-31-x', body='Closes #31')}
    proc, calls, out = stub.run('gate')
    assert (out['mode'], out['pr'], out['issue'], out['key']) == ('fix', '200', '31', 'issue-31'), 'a PR fix joins its issue\'s group'
    assert out['request'] == '@claude rename it' and not posted(calls, 'labels')


def test_verify_asks_for_a_new_review_when_it_had_to_add_closes(tmp_path):
    stub = Stub(tmp_path)
    branch = 'issue-126-x'
    stub.state = {f'repos/o/r/pulls?state=open&head=o:{branch}&per_page=10': [pull(200, branch, body='No link')]}
    proc, calls, _ = stub.run('verify', '--mode', 'build', '--issue', '126', '--branch', branch)
    assert [c for c in calls if c[:2] == ['workflow', 'run']] == [
        ['workflow', 'run', 'agent-review.yml', '--repo', 'o/r', '--ref', 'main', '-f', 'pr=200']]
    stub.state = {f'repos/o/r/pulls?state=open&head=o:{branch}&per_page=10': [pull(200, branch, body='Closes #126')]}
    proc, calls, _ = stub.run('verify', '--mode', 'build', '--issue', '126', '--branch', branch)
    assert not [c for c in calls if c[:2] == ['workflow', 'run']] and not posted(calls, 'pulls/200')


def test_the_queue_starts_with_fewer_than_three_ahead_and_gives_up_loudly(tmp_path):
    stub = Stub(tmp_path)
    runs = 'repos/o/r/actions/workflows/agent.yml/runs?per_page=100'
    stub.state = {runs: {'workflow_runs': [run(10), run(20), run(30, status='completed'), run(90)]}}
    proc, calls, _ = stub.run('queue', '--run-id', '77', '--issue', '126', '--poll', '0', '--max-wait', '0')
    assert proc.returncode == 0 and '2 agent run(s) ahead; starting' in proc.stdout
    assert len(calls) == 1, 'one API call per poll'
    stub.state = {runs: {'workflow_runs': [run(10), run(20), run(30)]}}
    proc, calls, _ = stub.run('queue', '--run-id', '77', '--issue', '126', '--poll', '0', '--max-wait', '0')
    assert proc.returncode == 1 and 'gave up' in posted(calls, 'issues/126/comments')[0]['body']
    stub.state = {}   # the listing fails: a warning, then the deadline, never a silent crash
    proc, calls, _ = stub.run('queue', '--run-id', '77', '--issue', '126', '--poll', '0', '--max-wait', '0')
    assert proc.returncode == 1 and 'could not list' in proc.stdout and posted(calls, 'issues/126/comments')
