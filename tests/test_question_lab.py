"""The question lab (`evals/lab.py`), the `analyst` agent and the `/question-lab` skill (issue #177).

The lab asks many questions through the server's own offline path and writes each card for the analyst to read; these
tests pin what it asks (the page's canned questions under every scope and period, a file's own controls), that it calls
no model, and that its flags and tool lines say what the card read. The agent and the skill are prose; the tests pin
that every file they send a reader to exists and that the two name each other.
"""
import importlib.util
import re
from pathlib import Path

from investigator.record_store import default_store
from investigator.server import Workbench

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab', ROOT / 'evals' / 'lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
AGENT = ROOT / '.claude' / 'agents' / 'analyst.md'
SKILL = ROOT / '.claude' / 'skills' / 'question-lab' / 'SKILL.md'


def args_of(*argv):
    """The lab's parsed command line, defaults resolved as main() resolves them."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='*')
    parser.add_argument('--canned', action='store_true')
    parser.add_argument('--ask', action='append')
    parser.add_argument('--day', default=lab.DEFAULTS['day'])
    parser.add_argument('--shift', type=int, default=lab.DEFAULTS['shift'])
    parser.add_argument('--scope')
    parser.add_argument('--period')
    args = parser.parse_args(argv)
    args.scope = args.scope or ('both' if args.canned else lab.DEFAULTS['scope'])
    args.period = args.period or ('all' if args.canned else lab.DEFAULTS['period'])
    return args


def test_the_canned_questions_are_the_pages():
    """Every entry of `QUESTIONS` in web/app.js, read by the lab: its text, and the scope a chip fixes ("Compare all shifts")."""
    source = (ROOT / 'src/investigator/web/app.js').read_text()
    block = source[source.index('const QUESTIONS=['):]
    block = block[:block.index('\n];')]
    canned = lab.canned_questions()
    assert len(canned) == len(re.findall(r"^\s*\[", block, re.M)) > 10
    texts = [text for _, text, _, _ in canned]
    assert 'Why was this shift slow? Chart downtime by reason.' in texts and all(t.strip() for t in texts)
    assert next(scope for _, text, scope, _ in canned if text == 'Compare all shifts') == 'day'


def test_canned_alone_asks_every_scope_and_period():
    """`--canned` with nothing else is the issue's front door: every canned question under one shift and All, under Day,
    Week, Month and 4-4-5, with {period} written as the page writes it; a chip that fixes its scope or period is asked once
    for each of the controls it leaves open."""
    items = lab.load(args_of('--canned'))
    canned = lab.canned_questions()
    assert len(items) == sum((1 if scope else 2) * (1 if period else 4) for _, _, scope, period in canned)
    assert {(c['scope'], c['period']) for _, c, _ in items} == {(s, p) for s in lab.SCOPES for p in lab.PERIODS}
    assert not any('{period}' in text for text, _, _ in items)
    assert len(lab.load(args_of('--canned', '--scope', 'day', '--period', 'week'))) == len(canned)


def test_a_line_sets_its_own_controls(tmp_path):
    """`[key=value ...] question`: the line's controls win, and the command line's scopes and periods fill the rest."""
    assert lab.parse_line('[scope=day period=week day=2026-09-16 shift=3] how many setups', {'day': 'd', 'shift': 2}) == (
        'how many setups', {'day': '2026-09-16', 'shift': 3, 'scope': 'day', 'period': 'week'})
    questions = tmp_path / 'q.txt'
    questions.write_text('# a comment\n\n[scope=day] how many setups\nhow many stops\n')
    items = lab.load(args_of(str(questions), '--scope', 'both', '--period', 'day,month'))
    assert [(t, c['scope'], c['period']) for t, c, _ in items] == [
        ('how many setups', 'day', 'day'), ('how many setups', 'day', 'month'),
        ('how many stops', 'shift', 'day'), ('how many stops', 'shift', 'month'), ('how many stops', 'day', 'day'), ('how many stops', 'day', 'month')]


def test_the_lab_asks_offline_and_writes_every_field(monkeypatch):
    """Each question goes through Workbench.ask in offline mode, and no model is called; a refusal is recorded as one."""
    payloads, usages = [], []
    ask = Workbench.ask

    def spy(self, payload, *a, **k):
        payloads.append(dict(payload))
        result = ask(self, payload, *a, **k)
        usages.append(result['usage'])
        return result

    monkeypatch.setattr(Workbench, 'ask', spy)
    base = {'day': '2026-09-14', 'shift': 2}
    records = lab.run([('Show shift and downtime notes', {**base, 'scope': 'day', 'period': 'day'}, None),
                       ('How many unicorns this week?', {**base, 'scope': 'shift', 'period': 'day'}, None)], default_store())
    assert [p['mode'] for p in payloads] == ['offline', 'offline']
    assert all(u.get('model_calls', 0) == 0 and u.get('cost_usd', 0) == 0 for u in usages)
    notes, refused = records
    assert set(notes) >= {'question', 'controls', 'url', 'intent', 'shifts', 'headline', 'read_as', 'findings', 'tools', 'refusal', 'flags'}
    assert notes['url'] == '/corr/1/day/2026-09-14?measure=notes' and notes['intent'] == 'notes' and notes['shifts'] == [1, 2, 3]
    assert 'get_day_kpis(shifts 1,2,3, day)' in notes['tools']   # a day total says which shifts it read
    assert notes['headline'].startswith('**') and '(query' not in notes['headline'] and 'under All, leads with one shift' not in notes['flags']
    assert refused['refusal'] and refused['headline'] is None


def test_the_flags_say_where_to_look():
    """Under All, a headline on one shift is flagged unless the question names it; so is a card that answered other shifts
    than the ones the question names (the router's own reading of them)."""
    record = {'refusal': None, 'headline': '**9** stops on shift 1, 2 with a note', 'findings': [], 'shifts': [1, 2, 3]}
    day = {'scope': 'day'}
    assert 'under All, leads with one shift' in lab.flags_of('Show shift and downtime notes', day, record)
    assert 'under All, leads with one shift' not in lab.flags_of('Show the notes for shift 1', day, record)
    assert lab.flags_of('Show the notes for shift 1', day, record)[-1] == 'names shift 1, answered shifts [1, 2, 3]'
    assert not [f for f in lab.flags_of('Speed for shift 1 and shift 3', day, {**record, 'headline': '**1** x', 'shifts': [1, 3]})
                if f.startswith('names')]
    assert 'headline has no figure' in lab.flags_of('q', {'scope': 'shift'}, {**record, 'headline': 'No figure here'})


def test_the_agent_and_the_skill_send_readers_to_files_that_exist():
    """Every repository path the analyst agent and the skill name exists; the skill hands the lab's JSON to the agent, and
    the agent writes its review beside it, never into code, goldens or docs."""
    agent, skill = AGENT.read_text(), SKILL.read_text()
    assert agent.startswith('---\nname: analyst\n') and 'tools: Read, Grep, Glob, Bash' in agent
    assert skill.startswith('---\nname: question-lab\n')
    paths = set(re.findall(r'`((?:docs|src|evals|\.claude)/[^`*<>]+\.(?:md|json|py))`', agent + skill))
    assert {'docs/METRICS.md', 'docs/ASKING.md', 'src/investigator/targets.json', 'src/investigator/semantic_model.json', '.claude/agents/analyst.md'} <= paths
    assert 'evals/lab.py' in agent and 'evals/lab.py' in skill
    assert [p for p in paths if not (ROOT / p).exists()] == []
    for verdict in ('right', 'not crisp', 'different question', 'refused but answerable', 'refused rightly'):
        assert f'**{verdict}**' in agent
    assert 'artifacts/lab/<name>.review.md' in agent and 'Never edit code, goldens or docs' in agent
    assert 'artifacts/lab/' in (ROOT / '.gitignore').read_text() or 'artifacts/' in (ROOT / '.gitignore').read_text()
