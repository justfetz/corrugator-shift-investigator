"""The URL grammar (issue #20): a typed question and its canonical URL are the same state.

`canonical_url(parse_request(q))` is the address of a question; `resolve_url` is its inverse. The
round trip is proved on every question the golden set asks (the answers compare equal step by step),
and the grammar refuses what it cannot name instead of guessing."""
import json
import re
from pathlib import Path
import pytest
from investigator.agent import Agent, Context, parse_request
from investigator.record_store import default_store
from investigator.urls import (PATH, canonical_url, machine_slug, page_url, question_url, resolve_url, scope_url,
                               INTENT_MEASURES, STORE_MEASURES)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
RUN_ID = re.compile(r'[0-9a-f]{32}')


def answer(store, day, shift, scope, period, corrupt, text, request=None, history=()):
    """One offline answer, reduced to what must be equal between a question and its address."""
    analysis = store.day_analysis(day, corrupt)
    try:
        context = Context(day, shift, period=period)
        context.scope, context.history = scope, list(history)
        result = Agent(analysis).ask(text, context, request=request)
        strip = lambda value: json.loads(RUN_ID.sub('RUN', json.dumps(value)))
        return {'tools': strip([(t['tool'], t['arguments']) for t in result['trace']]), 'status': result['status'],
                'sections': strip(result['sections']), 'charts': len(result['charts']), 'shifts': result['selected_shifts'],
                'tables': [t['title'] for t in result['tables']], 'history': len(result['context']['history']),
                'period_label': result.get('period_label')}, context
    finally:
        analysis.close()


@pytest.mark.parametrize('case', [c for c in GOLDEN if c.get('question') or c.get('turns')], ids=lambda c: c['id'])
def test_every_golden_question_round_trips_through_its_url(case):
    """For each turn: the request's URL resolves to a request that answers identically (same tools and
    arguments, sections, charts, tables, status), the URL is idempotent, and a recorded expect.url matches."""
    store = default_store()
    day, scope, shift = case.get('day', '2026-09-14'), case.get('scope', 'shift'), case.get('shift', 2)
    period, corrupt = case.get('period', 'day'), case.get('corrupt', False)
    turns = case.get('turns') or [case['question']]
    context = Context(day, shift, period=period)
    context.scope = scope
    after, url = [], None
    for text in turns:
        try:
            request = parse_request(text, context)
        except ValueError:
            return   # a refusal of the router has no address; the server answers /q with the same text
        url = canonical_url(request, context, corrupt, store.machine_id, after)
        try:
            store.check_day(day)
        except ValueError as exc:   # a weekend day: the URL is refused with the same calendar reason
            with pytest.raises(ValueError, match=re.escape(str(exc))):
                resolve_url(url, store)
            return
        resolved = resolve_url(url, store)
        assert page_url(resolved) == url, 'the address of the resolved state is the address itself'
        assert (resolved['day'], resolved['scope'], resolved['shift'], resolved['tool_period'], resolved['corrupt']) == (day, scope, shift, period, corrupt), 'the path is the controls'
        asked, context = answer(store, day, shift, scope, period, corrupt, text, history=context.history)
        resolved['context'].history = resolved['history']
        addressed, _ = answer(store, resolved['day'], resolved['shift'], resolved['scope'], resolved['tool_period'], resolved['corrupt'],
                              resolved['text'], resolved['request'], resolved['history'])
        assert addressed == asked, url
        after.append(resolved['text'])
    if 'url' in case['expect']:
        assert resolve_url(url, store)['text'] == case['expect']['url']


def test_the_owner_examples_resolve():
    """The four shapes from the issue, in this grammar: the path is the controls, the query the plan."""
    store = default_store()
    downtime = resolve_url('/corr/1/day/2026-09-14/shift/2?measure=downtime&by=reason', store)
    assert downtime['request']['intent'] == 'downtime' and downtime['request']['shifts'] == [2] and downtime['scope'] == 'shift'
    fpm = resolve_url('/corr/1/week/2026-09-14?measure=fpm', store)
    assert [s['tool'] for s in fpm['request']['plan']] == ['get_period_kpis', 'get_grade_totals'] and fpm['tool_period'] == 'week'
    waste = resolve_url('/corr/1/period/2026-09-14/shift/3?measure=waste', store)
    assert waste['request']['plan'] == [{'tool': 'get_waste_breakdown', 'arguments': {'window': {'period': 'fiscal', 'anchor': '2026-09-14'}, 'shift': 3}}]
    assert waste['control_period'] == 'accounting'
    month = resolve_url('/corr/1/day/2026-09-14?measure=kpis&window=2026-09&compare=2026-08', store)
    assert month['request']['plan'] == [{'tool': 'compare_periods', 'arguments': {'current_window': {'period': 'month', 'anchor': '2026-09-01'},
                                                                                    'previous_window': {'period': 'month', 'anchor': '2026-08-01'}, 'shift': None}}]
    assert resolve_url('/corr/1/month/2026-09-14?measure=kpis&compare=none', store)['request']['plan'][0]['arguments']['compare'] is False
    report = resolve_url('/corr/1/month/2026-09-14/shift/2.json?measure=report&corrupt=1', store)
    assert report['report'] and report['request'] is None and report['corrupt'] and report['json'] and report['text'] == '/corr/1/month/2026-09-14/shift/2?measure=report&corrupt=1'
    assert resolve_url('/corr/1/day/2026-09-14/shift/2.json?measure=notes&want=chart', store)['request']['asked'] == ['chart']
    assert machine_slug('CORR-1') == '1' and machine_slug('BHS-2') == 'bhs-2'
    assert scope_url('2026-09-14', 'accounting', 'day', 2) == '/corr/1/period/2026-09-14'
    assert question_url('2026-09-14', 2, 'shift', 'day', question='Why was this shift slow?') == '/q?day=2026-09-14&shift=2&scope=shift&period=day&q=Why%20was%20this%20shift%20slow%3F'
    assert PATH.match('/corr/1/day/2026-09-14/shift/2.json') and not PATH.match('/corr/1/day/2026-09-14/shift/4')


def test_speed_by_grade_and_speed_over_a_window_are_fpm_urls():
    """Issue #74: a measure word decides the intent, a grouping word only the breakdown. `fpm by grade`
    is measure=fpm&by=grade (speed against each grade target, charted in ft/min) under every grain, and
    `fpm` over a named window is the period speed plan, not the generic KPI tiles."""
    store = default_store()
    for grain, period in (('day', 'day'), ('week', 'week'), ('month', 'month'), ('period', 'fiscal')):
        resolved = resolve_url(f'/corr/1/{grain}/2026-09-14?measure=fpm&by=grade', store)
        assert resolved['request']['plan'] == [{'tool': 'get_grade_totals', 'arguments': {
            'window': {'period': period, 'anchor': '2026-09-14'}, 'shift': None, 'measure': 'speed'}}]
        assert page_url(resolved) == f'/corr/1/{grain}/2026-09-14?measure=fpm&by=grade'
    week = resolve_url('/corr/1/day/2026-09-14/shift/2?measure=fpm&window=week', store)
    assert [(s['tool'], s['arguments']['window']['period']) for s in week['request']['plan']] == [('get_period_kpis', 'week'), ('get_grade_totals', 'week')]
    assert week['request']['plan'][1]['arguments']['measure'] == 'speed'
    assert resolve_url('/corr/1/day/2026-09-14?measure=fpm&by=grade&window=month&shifts=3', store)['request']['plan'][0]['arguments'] == \
        {'window': {'period': 'month', 'anchor': '2026-09-14'}, 'shift': 3, 'measure': 'speed'}
    for text, url in (('fpm by grade', '/corr/1/day/2026-09-14/shift/2?measure=fpm&by=grade'),
                      ('Which grade is slowest against target this month?', '/corr/1/day/2026-09-14/shift/2?measure=fpm&window=month&by=grade'),
                      ('volume by grade this month', '/corr/1/day/2026-09-14/shift/2?measure=grade&window=month'),
                      ('average speed this week', '/corr/1/day/2026-09-14/shift/2?measure=fpm&window=week')):
        context = Context('2026-09-14', 2)
        context.scope = 'shift'
        assert canonical_url(parse_request(text, context), context) == url, text
    analysis = store.day_analysis('2026-09-14', False)
    try:
        context = Context('2026-09-14', 2)
        context.scope = 'shift'
        result = Agent(analysis).ask('fpm by grade', context)
        assert [(c['unit'], c['y']) for c in result['charts']] == [('ft/min', 'speed_run_fpm')] and 'y2' not in result['charts'][0]
        assert result['charts'][0]['title'].startswith('Speed by grade vs target')
    finally:
        analysis.close()
    with pytest.raises(ValueError, match='measure is volume or speed'):
        store.get_grade_totals({'period': 'week', 'anchor': '2026-09-14'}, measure='tons')


def test_every_measure_has_a_meaning_under_every_grain():
    """Each measure resolves (or is refused with the router's own reason) under each grain and scope."""
    store = default_store()
    for grain in ('day', 'week', 'month', 'period'):
        for tail in ('', '/shift/2'):
            for measure in [*INTENT_MEASURES.values(), *STORE_MEASURES, 'report']:
                url = f'/corr/1/{grain}/2026-09-14{tail}?measure={measure}' + ('&metric=trim_pct' if measure == 'trend' else '')
                try:
                    resolved = resolve_url(url, store)
                except ValueError as exc:
                    assert (measure in ('setups', 'orders', 'notes', 'rank') and grain != 'day' and 'Switch Period to Day' in str(exc)) or \
                           (measure == 'report' and grain == 'day' and 'needs Week, Month or 4-4-5' in str(exc)), (url, str(exc))
                    continue
                # one spelling per state: the address of the resolved state is the address. Under a ranged grain
                # the day-shaped downtime, quality and compare are the Pareto, the waste breakdown and the KPI tiles,
                # so they normalize to pareto, waste and kpis once and stay there.
                alias = {'downtime': 'pareto', 'quality': 'waste', 'compare': 'kpis'} if grain != 'day' else {}
                normalized = page_url(resolved)
                assert normalized == url.replace('measure=' + measure, 'measure=' + alias.get(measure, measure)), url
                assert page_url(resolve_url(normalized, store)) == normalized


@pytest.mark.parametrize('url, reason', [
    ('/corr/1/day/2026-09-14/shift/2', 'Add measure='),
    ('/corr/2/day/2026-09-14?measure=kpis', 'No machine'),
    ('/corr/1/day/2026-09-12?measure=kpis', 'Saturday'),
    ('/corr/1/day/2026-10-01?measure=kpis', 'outside the dataset'),
    ('/corr/1/day/2026-09-14?measure=kpis&bogus=1', 'Unknown query field'),
    ('/corr/1/day/2026-09-14?measure=kpis&measure=waste', 'Repeated query field'),
    ('/corr/1/day/2026-09-14?measure=tons', 'Unknown measure'),
    ('/corr/1/day/2026-09-14?measure=downtime&by=crew', 'by is reason, place or kind'),
    ('/corr/1/day/2026-09-14?measure=downtime&kind=pm', 'kind is maintenance or operator'),
    ('/corr/1/day/2026-09-14?measure=trend', 'metric is one of'),
    ('/corr/1/day/2026-09-14?measure=trend&metric=trim_pct&weeks=30', '1 to 26'),
    ('/corr/1/day/2026-09-14?measure=waste&metric=trim_pct', 'metric= does not apply'),
    ('/corr/1/day/2026-09-14?measure=overview&window=week', 'window= does not apply'),
    ('/corr/1/day/2026-09-14?measure=kpis&window=2026-13', 'window is week, month, period'),
    ('/corr/1/day/2026-09-14?measure=kpis&compare=last', 'compare is prev, none'),
    ('/corr/1/week/2026-09-14?measure=kpis&compare=2026-08', 'compare takes a date'),
    ('/corr/1/day/2026-09-14/shift/2?measure=overview&shifts=1', 'Selected shift only'),
    ('/corr/1/day/2026-09-14?measure=overview&shifts=1,1', 'distinct shifts'),
    ('/corr/1/day/2026-09-14?measure=waste&shifts=1,2', 'runs for one shift'),
    ('/corr/1/day/2026-09-14?measure=fpm&by=width', 'by is grade for measure=fpm'),
    ('/corr/1/day/2026-09-14?measure=fpm&by=grade&shifts=1,2', 'not split by shift'),
    ('/corr/1/day/2026-09-14?measure=fpm&kind=maintenance', 'kind= does not apply'),
    ('/corr/1/day/2026-09-14?measure=kpis&corrupt=yes', 'corrupt is 1'),
    ('/corr/1/day/2026-09-14?measure=kpis&format=xml', 'format is json or pdf'),
    ('/corr/1/day/2026-09-14.json?measure=kpis&format=pdf', 'does not take format='),
    ('/corr/1/day/2026-09-14?measure=kpis&want=money', 'want lists chart'),
    ('/corr/1/day/2026-09-14?measure=kpis&after=/corr/1/day/2026-09-15?measure=kpis', 'another production day'),
    ('/corr/1/day/2026-09-14?measure=kpis&after=/corr/1/day/2026-09-14?measure=kpis%26after=/corr/1/day/2026-09-14?measure=kpis', 'no after= of its own'),
    ('/corr/1/day/2026-09-14?measure=kpis&' + '&'.join(['after=/corr/1/day/2026-09-14?measure=kpis'] * 9), 'At most 8'),
    ('/api/ask', 'Not a question URL'),
    ('/corr/1/day/2026-09-14?measure=kpis' + '&want=chart' * 400, 'longer than 2000'),
])
def test_what_the_grammar_refuses(url, reason):
    with pytest.raises(ValueError, match=reason):
        resolve_url(url, default_store())


def test_follow_up_context_rides_in_the_url():
    """`after=` rebuilds the history the session used to keep: the prior views' addresses, shifts and intents,
    at most the agent's max_turns of them; the view's own address never carries its chain into the history."""
    store = default_store()
    first = '/corr/1/day/2026-09-14?measure=overview&shifts=2'
    second = canonical_url(resolve_url('/corr/1/day/2026-09-14?measure=compare&shifts=2,1', store)['request'],
                           Context('2026-09-14', 2), False, store.machine_id, [first])
    assert second == '/corr/1/day/2026-09-14?measure=compare&shifts=2,1&after=/corr/1/day/2026-09-14?measure=overview%26shifts=2'
    resolved = resolve_url(second, store)
    assert resolved['history'] == [{'question': first, 'shifts': [2], 'intent': 'overview'}]
    assert resolved['text'] == '/corr/1/day/2026-09-14?measure=compare&shifts=2,1' and page_url(resolved) == second
    chain = [f'/corr/1/day/2026-09-14?measure=trend&metric=trim_pct&weeks={n}' for n in range(1, 7)]
    resolved = resolve_url(canonical_url(resolve_url(first, store)['request'], Context('2026-09-14', 2), False, 'CORR-1', chain), store, max_turns=4)
    assert [h['question'] for h in resolved['history']] == chain[-4:]
    report = resolve_url('/corr/1/week/2026-09-14?measure=kpis&after=/corr/1/week/2026-09-14?measure=report', store)
    assert report['history'] == [] and report['after'] == ['/corr/1/week/2026-09-14?measure=report']   # a report is not a question
