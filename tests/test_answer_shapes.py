"""Answer shapes (issue #75): each intent answers with a headline, findings and where to look, built from the
tool rows the answer already has. These tests hold the shape and its provenance from outside the application:
the evidence references are resolved against the trace here, and every figure is matched against the values of
the row it cites in the formats the evals accept (evals/run.py `renderings`)."""
import json
import re
import sys
from pathlib import Path

import pytest

from investigator.agent import Agent, Context, parse_request
from investigator.record_store import default_store

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'evals'))
from run import RULE_CONSTANTS, renderings  # noqa: E402

DAY = '2026-09-14'
GOLDEN = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
BOLD = re.compile(r'\*\*([^*]+)\*\*')
REF = re.compile(r' \(query (\d+)(?: · (row|detail|weekday) (\d+))?\)$')
LOOK = re.compile(r' → “([^”]+)”$')
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
PARTS = {'row': 'rows', 'detail': 'details', 'weekday': 'weekday_profile'}
MAX_WORDS = 40      # the owner's rule: no paragraph over about forty words
MAX_CLAUSES = 3     # and no list of more than three like items joined into one sentence with semicolons


def ask(question, scope='shift', period='day', shift=2, corrupt=False, day=DAY):
    store = default_store()
    analysis = store.day_analysis(day, corrupt)
    try:
        context = Context(day, shift, period=period)
        context.scope = scope
        return Agent(analysis).ask(question, context), context
    finally:
        analysis.close()


def plain(section):
    """What a reader sees and counts: the markers, the evidence reference and the where-to-look question removed."""
    text = section[2:] if section.startswith('- ') else section
    return LOOK.sub('', REF.sub('', text)).replace('**', '')


def cited(section, trace):
    """(row values, record) the section's evidence reference names, or a failure message."""
    m = REF.search(section)
    assert m, f'no evidence reference: {section!r}'
    step, part, index = int(m[1]), m[2], m[3]
    assert 0 < step <= len(trace), f'query {step} is not a step of the trace: {section!r}'
    source = trace[step - 1]
    assert source['ok'] and source['tool'] != 'render_chart', f'query {step} is not a successful tool call: {section!r}'
    data = source['data']
    if part:
        records = data.get(PARTS[part]) or []
        assert int(index) < len(records), f'query {step} has no {part} {index}: {section!r}'
        return records[int(index)], source
    return None, source


def test_why_slow_renders_headline_findings_chart_and_where_to_look_each_number_from_its_row():
    """The acceptance case: one headline, three to five findings with exactly one bold figure each, the chart for the
    measure, and where to look. Every number on a line that cites a row is a value of that row (or a rule constant,
    or a count of the cited result's rows); the figures in bold are values of the row they cite."""
    result, context = ask('Why was this shift slow?')
    sections, trace = result['sections'], result['trace']
    head = sections[0]
    assert head.startswith('**') and len(BOLD.findall(head)) == 1, head
    assert re.match(r'^\*\*[0-9.]+%\*\* of grade target · ', head), 'the headline is the asked measure: speed against grade target'
    findings = []
    for s in sections[1:]:
        if not s.startswith('- '):
            break
        findings.append(s)
    assert 3 <= len(findings) <= 5, findings
    assert sum(1 for s in sections if '**' in s and not s.startswith('- ')) == 1, 'exactly one headline'
    for line in [head, *findings]:
        figures = BOLD.findall(line)
        assert len(figures) == 1, f'exactly one bold figure: {line!r}'
        row, source = cited(line, trace)
        rows = source['data']['rows']
        if row is None:   # a count over the whole result: its rows, or how many of them are below target
            values = renderings(len(rows)) | renderings(sum(r['actual_fpm'] < r['target_fpm'] for r in rows))
        else:
            values = set().union(*(renderings(v) for v in row.values() if not isinstance(v, (str, list, dict))))
        for token in NUMBER.findall(figures[0]):
            assert token in values, f'the bold figure {token!r} is not a value of the row it cites: {line!r}'
        allowed = values | set(RULE_CONSTANTS) | renderings(len(rows))   # beside it: the row's other values, a rule, a row count
        names = [v for v in (row or {}).values() if isinstance(v, str)]
        rest = REF.sub('', line)
        for name in names:
            rest = rest.replace(name, ' ')
        rest = re.sub(r'\b[Ss]hift [123]\b', ' ', rest)
        for token in NUMBER.findall(rest):
            assert token in allowed, f'{token!r} on {line!r} is not a value of the row it cites'
    assert [c['unit'] for c in result['charts']] == ['ft/min', 'minutes'], 'the chart for the measure first (speed against grade target), then the downtime that explains it'
    looks = sections[sections.index('Where to look') + 1:]
    assert 2 <= len(looks) <= 3 and all(s.startswith('- ') and LOOK.search(s) for s in looks), looks
    for s in looks:
        request = parse_request(LOOK.search(s)[1], context)   # each a question the offline router answers in this scope
        assert request['intent'] in ('matrix', 'downtime', 'quality'), request['intent']
    assert not any('ft/min including downtime;' in s for s in sections), 'the generic KPI paragraph is gone'


# One question per intent of the offline router, in the scope it is asked in. The URL grammar resolves three period
# intents as aliases of others (urls.PERIOD_FORMS: a day-shaped downtime or quality question under a ranged window is
# the Pareto or the waste breakdown, the same plan and the same canonical address), so they share that answer.
INTENT_QUESTIONS = [
    ('summary', 'Summarize this shift', 'shift', 'day'),
    ('orders', 'Show orders and quantities', 'shift', 'day'),
    ('ranking', 'Show rankings', 'shift', 'day'),
    ('matrix', 'Show setup matrix', 'shift', 'day'),
    ('notes', 'Show shift and downtime notes', 'shift', 'day'),
    ('compare', 'Compare all shifts', 'day', 'day'),
    ('overview', 'Why was this shift slow?', 'shift', 'day'),
    ('quality', 'Show dry-end quality waste', 'shift', 'day'),
    ('downtime', 'Show downtime by reason', 'shift', 'day'),
    ('speed', 'Show speed to target', 'shift', 'day'),
    ('dollars', 'Where did the money go this shift?', 'shift', 'day'),
    ('period_compare', 'Compare this week to last', 'day', 'day'),
    ('period_overview', 'Why was this shift slow?', 'shift', 'week'),
    ('period_summary', 'Summarize this shift', 'shift', 'week'),
    ('period_speed', 'What grades ran slowest this week?', 'day', 'day'),   # 'last week' moves the controls (issue #105)
    ('period_grade', 'Volume by grade this month', 'day', 'day'),
    ('period_pareto', 'Which downtime reason cost the most minutes this week?', 'day', 'day'),
    ('period_waste', 'Waste breakdown for the week', 'day', 'day'),
    ('period_stops', 'Stops per day this week', 'day', 'day'),
    ('period_trend', 'Trend of trim over eight weeks', 'day', 'day'),
    ('period_rollstock', 'How much paper did we consume this month?', 'day', 'day'),
    ('period_overrun', 'Over/under-run by knife this week', 'day', 'day'),
    ('period_speed_opportunity', 'Where is the speed opportunity this month?', 'day', 'day'),
    ('period_money', 'Where is the money this month?', 'day', 'day'),
    ('period_speedvar', 'What were the top 3 contributors to speed variations?', 'shift', 'day'),
]
ALIASES = [('period_downtime', 'Show downtime by reason', 'period_pareto'), ('period_quality', 'Show dry-end quality waste', 'period_waste')]


def opening(sections):
    """The opening sentence of an answer with its figures masked: what would read the same whatever was asked."""
    return re.sub(r'\d[\d,]*(?:\.\d+)?', 'N', REF.sub('', sections[0]))


def test_no_two_intents_share_an_opening_sentence():
    openings = {}
    for intent, question, scope, period in INTENT_QUESTIONS:
        result, context = ask(question, scope=scope, period=period)
        assert context.history[-1]['intent'] == intent, (question, context.history[-1]['intent'])
        assert result['sections'][0].count('**') == 2, f'{intent} opens with its headline: {result["sections"][0]!r}'
        openings[intent] = opening(result['sections'])
    shared = {o: [i for i in openings if openings[i] == o] for o in openings.values()}
    assert all(len(v) == 1 for v in shared.values()), {o: v for o, v in shared.items() if len(v) > 1}
    for alias, question, canonical in ALIASES:   # the same plan answers the same way
        result, context = ask(question, scope='day', period='week')
        assert context.history[-1]['intent'] == alias
        week, _ = ask({'period_pareto': 'Which downtime reason cost the most minutes?', 'period_waste': 'Waste breakdown'}[canonical], scope='day', period='week')
        assert opening(result['sections']) == opening(week['sections'])
    # dollars over a window (`measure=dollars` under a ranged grain, intent period_dollars) is the levers' plan
    from investigator.urls import resolve_url
    store = default_store()
    resolved = resolve_url('/corr/1/week/2026-09-14?measure=dollars', store)
    assert resolved['request']['intent'] == 'period_dollars'
    analysis = store.day_analysis(DAY)
    try:
        dollars = Agent(analysis, store=store).ask(resolved['text'], resolved['context'], request=resolved['request'])
    finally:
        analysis.close()
    money, _ = ask('Where is the money?', scope='day', period='week')
    assert opening(dollars['sections']) == opening(money['sections'])


def test_every_rank_word_is_true_on_every_day():
    """A rank the answer states is a rank of the figure it is said of, on every production day of the store:
    the shift a speed or summary headline calls the lowest is the lowest in percent of grade target, the run
    called the highest against its target is the highest, and the rankings headline is the extreme of the
    shifts' first-ranked rows (issue #75 review: 14 of 43 days broke the first, 20 shift-days the second)."""
    store = default_store()
    pct = re.compile(r'on shift ([123])\b[^·]* · ([0-9.]+)% of grade target, the lowest of the three shifts')
    for day in store.production_days:
        analysis = store.day_analysis(day)
        try:
            for question in ('Show speed to target', 'Summarize this shift'):
                context = Context(day, 2)
                context.scope = 'day'
                result = Agent(analysis).ask(question, context)
                rows = {t['data']['shift']: t['data']['rows'][0] for t in result['trace']
                        if t['ok'] and t['tool'] in ('get_shift_kpis', 'get_shift_overview') and t['data']['coverage'] == 'complete'}
                m = pct.search(result['sections'][0])
                assert m, (day, question, result['sections'][0])
                lowest = min(rows, key=lambda s: rows[s]['speed_to_target_pct'])
                assert int(m[1]) == lowest, (day, question, result['sections'][0])
            for shift in (1, 2, 3):
                context = Context(day, shift)
                context.scope = 'shift'
                result = Agent(analysis).ask('Show speed to target', context)
                runs = next(t['data']['rows'] for t in result['trace'] if t['ok'] and t['tool'] == 'get_wet_end_performance')
                for line in result['sections']:
                    if 'the highest of' in line:
                        row = runs[int(REF.search(line)[3])]
                        assert row['speed_to_target_pct'] == max(r['speed_to_target_pct'] for r in runs), (day, shift, line)
            context = Context(day, 2)
            context.scope = 'day'
            result = Agent(analysis).ask('Show rankings', context)
            firsts = [t['data']['rows'][0]['value'] for t in result['trace']
                      if t['ok'] and t['tool'] == 'get_rankings' and t['data']['metric'] == 'speed' and t['data']['rows']]
            head = float(re.match(r'^\*\*([0-9.]+)%', result['sections'][0])[1])
            assert f'{min(firsts):.1f}' == f'{head:.1f}', (day, result['sections'][0])
        finally:
            analysis.close()


def test_an_analyst_plan_keeps_a_finding_for_every_result():
    """An analyst's own plan is answered in the shape of the tools it ran, and every other result keeps its own
    finding with its evidence, so the calculated evidence under a narrative never drops a result it can cite."""
    sys.path.insert(0, str(ROOT / 'evals'))
    from run import ScriptedAnalyst
    store = default_store()
    analysis = store.day_analysis(DAY)
    try:
        context = Context(DAY, 2)
        context.scope = 'shift'
        steps = [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}, {'tool': 'get_quality_breakdown', 'arguments': {'shift': 2}},
                 {'tool': 'get_downtime_breakdown', 'arguments': {'shift': 2, 'group_by': 'reason', 'kind': None}},
                 {'tool': 'get_wet_end_performance', 'arguments': {'shift': 2}}, {'tool': 'get_setup_matrix', 'arguments': {'shift': 2}}]
        result = Agent(analysis, planner=ScriptedAnalyst({'steps': steps}, None)).ask_planned('Why was this shift slow?', context)
    finally:
        analysis.close()
    cited = {int(m[1]) for s in result['sections'] for m in [REF.search(s)] if m}
    assert cited >= {2, 3, 4, 5}, (cited, result['sections'])   # every result but the KPIs the shape's headline already cites
    assert 1 in cited


def tool_text(trace):
    """Long strings a tool returned (thirty characters or more): formulas and notes the answer may quote verbatim."""
    out = set()
    for step in trace:
        if not step.get('ok'):
            continue
        data = step['data']
        records = [*data.get('rows', []), *(data.get('details') or [])]
        values = [v for r in records for v in r.values()] + list(data.values())
        out |= {v for v in values if isinstance(v, str) and len(v) >= 30}
    return out


def golden_answers():
    for case in GOLDEN:
        if case.get('mode') != 'offline' or not (case.get('question') or case.get('turns')):
            continue
        yield case


@pytest.mark.parametrize('case', list(golden_answers()), ids=lambda c: c['id'])
def test_no_section_runs_on_and_every_where_to_look_question_routes(case):
    """No section over about forty words, no more than three clauses joined with semicolons (a longer list is
    bullets or a table), and each where-to-look link is a question the offline router answers in the same scope."""
    store = default_store()
    context = Context(case.get('day', DAY), case.get('shift', 2), period=case.get('period', 'day'))
    context.scope = case.get('scope', 'shift')
    try:
        analysis = store.day_analysis(context.production_day, case.get('corrupt', False))
    except ValueError:
        return   # a weekend day: refused before any answer
    try:
        agent, result = Agent(analysis), None
        for text in case.get('turns') or [case['question']]:
            try:
                result = agent.ask(text, context)
            except ValueError:
                result = None
    finally:
        analysis.close()
    if result is None:
        return
    quoted = sorted(tool_text(result['trace']), key=len, reverse=True)
    for section in result['sections']:
        text = plain(section)
        for string in quoted:   # a tool's own text quoted whole (the cost model's pricing formula) is evidence, not the answer's prose
            text = text.replace(string, ' ')
        assert len(text.split()) <= MAX_WORDS, f'{len(text.split())} words: {section!r}'
        assert len(text.split(';')) <= MAX_CLAUSES, f'{len(text.split(";"))} semicolon clauses: {section!r}'
        m = LOOK.search(section)
        if m:
            parse_request(m[1], context)
