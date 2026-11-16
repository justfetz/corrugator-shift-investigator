"""The question lab (`evals/lab.py`), the `analyst` agent and the `/question-lab` skill (issue #177).

The lab asks many questions through the server's own offline path and writes each card for the analyst to read; these
tests pin what it asks (the page's canned questions under every scope and period, a file's own controls), that it calls
no model, and that its flags and tool lines say what the card read. The agent and the skill are prose; the tests pin
that every file they send a reader to exists and that the two name each other.
"""
import importlib.util
import re
from pathlib import Path

import pytest

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
    asked = lab.load(args_of('--ask', '[scope=day period=week] how many setups', '--scope', 'both', '--period', 'day,month'))
    assert [(t, c['scope'], c['period']) for t, c, _ in asked] == [('how many setups', 'day', 'week')]   # an --ask sets its controls too
    assert [(t, c['scope'], c['period']) for t, c, _ in items] == [
        ('how many setups', 'day', 'day'), ('how many setups', 'day', 'month'),
        ('how many stops', 'shift', 'day'), ('how many stops', 'shift', 'month'), ('how many stops', 'day', 'day'), ('how many stops', 'day', 'month')]


@pytest.mark.parametrize('line, says', [
    ('[perod=week] how many setups', "unknown control 'perod'"),
    ('[scope=all] how many setups', "scope='all'"),
    ('[period=fiscal] how many setups', "period='fiscal'"),
    ('[shift=4] how many setups', "shift='4'"),
    ('[day=2026-9-14] how many setups', "day='2026-9-14'"),
    ('[week] how many setups', "'week' is not key=value"),
])
def test_a_control_the_lab_does_not_take_is_a_lab_error(line, says):
    """An unknown key or a value its key does not take stops the lab with an error naming every key and the values each takes
    (the coordinator's review of aad1b3c, item 5): never a card, whose refusal the analyst would grade as the router's."""
    with pytest.raises(lab.LabError) as error:
        lab.parse_line(line, {'day': '2026-09-14', 'shift': 2}, 'q.txt line 3: ')
    message = str(error.value)
    assert message.startswith('q.txt line 3: ') and says in message
    for allowed in ('day: a production day as YYYY-MM-DD', 'shift: 1, 2 or 3', 'scope: shift (the selected shift) or day (All)',
                    'period: day, week, month or accounting'):
        assert allowed in message


def test_a_bad_control_stops_the_lab_before_any_card(tmp_path, capsys):
    """A file with one bad control line, a JSON entry with an unknown key, a bad --period or a day outside the golden store:
    the lab exits 2 with `lab error:`, asks nothing and writes no file."""
    good_then_bad = tmp_path / 'q.txt'
    good_then_bad.write_text('[scope=day] how many setups\n[perod=week] how many stops\n')
    entries = tmp_path / 'q.json'
    entries.write_text('[{"question": "how many setups", "scopes": "day"}]')
    out = tmp_path / 'out'
    for argv in ([str(good_then_bad)], [str(entries)], ['--ask', 'how many setups', '--period', 'day,fiscal'], ['--ask', '[perod=week] how many setups'],
                 ['--ask', 'how many setups', '--day', '2026-09-19']):
        assert lab.main([*argv, '--name', 'bad', '--out', str(out)]) == 2
        assert capsys.readouterr().err.startswith('lab error: ')
    assert not out.exists()


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
    # shifts named in the plural, by ordinals or with '&' (the coordinator's review of aad1b3c, item 1): a headline on one of
    # them is not flagged as one the question did not name, and a card that answered all three is flagged
    for question in ('Show the notes for shifts 1 and 2', 'Show the notes for the first and second shift', 'Show the notes for shift 1 & 2',
                     'Show the notes for the 1st and 2nd shifts'):
        flags = lab.flags_of(question, day, record)
        assert 'under All, leads with one shift' not in flags, question
        assert flags[-1] == 'names shifts 1, 2, answered shifts [1, 2, 3]', (question, flags)
    assert 'headline has no figure' in lab.flags_of('q', {'scope': 'shift'}, {**record, 'headline': 'No figure here'})


def test_the_agent_and_the_skill_send_readers_to_files_that_exist():
    """Every repository path the analyst agent and the skill name exists; the skill hands the lab's JSON to the agent, and
    the agent writes its review beside it, never into code, goldens or docs."""
    agent, skill = AGENT.read_text(), SKILL.read_text()
    # the agent reads and never writes (the coordinator's review of aad1b3c, item 2): no Bash, which writes anywhere; its review
    # is its message, and the skill writes the file and runs any re-asks
    assert agent.startswith('---\nname: analyst\n') and '\ntools: Read, Grep, Glob\n' in agent.split('\n---\n')[0] + '\n'
    assert 'Bash' not in agent.split('\n---\n')[0]
    assert 'final message' in agent and 'artifacts/lab/<name>.review.md' in skill and 'Re-asks' in agent and 'Re-asks' in skill
    assert skill.startswith('---\nname: question-lab\n')
    paths = set(re.findall(r'`((?:docs|src|evals|\.claude)/[^`*<>]+\.(?:md|json|py))`', agent + skill))
    assert {'docs/METRICS.md', 'docs/ASKING.md', 'src/investigator/targets.json', 'src/investigator/semantic_model.json', '.claude/agents/analyst.md'} <= paths
    assert 'evals/lab.py' in agent and 'evals/lab.py' in skill
    assert [p for p in paths if not (ROOT / p).exists()] == []
    for verdict in ('right', 'not crisp', 'different question', 'refused but answerable', 'refused rightly'):
        assert f'**{verdict}**' in agent
    assert 'artifacts/lab/<name>.review.md' in agent and 'Never edit code, goldens or docs' in agent
    # every candidate is the owner's to read (item 3): the agent writes the field, the skill checks it and keeps it
    assert agent.count('"reviewed_by": "owner-pending"') >= 3 and '"reviewed_by": "owner-pending"' in skill
    # #166's five crispness rules, verbatim (item 4)
    for rule in ('1. Lead with the exact number asked, in its unit, with its denominator and filter.',
                 "2. Stop after the measure's own breakdown: about five lines, then a link. Some answers today run 74–80 lines.",
                 '3. Apply every word that scopes the question, or refuse that word by name. Correct obvious typos.',
                 '4. Put the scope and the exclusions in the headline. "What\'s trim costing us" gives shift 2\'s $110,482 for the month; '
                 "the plant's is $312,286.",
                 '5. Give refusals and pushback a specific reply with the nearest number the records hold, and make sure the reply is true.'):
        assert rule in agent, rule
    assert 'artifacts/lab/' in (ROOT / '.gitignore').read_text() or 'artifacts/' in (ROOT / '.gitignore').read_text()
