"""The URL grammar (issue #20): a typed question and its canonical URL are the same state.

`canonical_url(parse_request(q))` is the address of a question; `resolve_url` is its inverse. The
round trip is proved on every question the golden set asks (the answers compare equal step by step),
and the grammar refuses what it cannot name instead of guessing."""
import http.client
import json
import re
from pathlib import Path
from threading import Thread
import pytest
from investigator.agent import Agent, Context, parse_request
from investigator.record_store import default_store
from investigator.server import make_server
from investigator.urls import (PATH, canonical_url, machine_slug, page_url, question_url, redirect_for, redirect_target,
                               resolve_url, scope_url, INTENT_MEASURES, STORE_MEASURES)

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
        assert redirect_target(url, resolved) is None, 'a canonical URL is never redirected (issue #70)'
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


def test_one_speed_or_volume_rule_and_why_slow_is_overview_at_every_grain():
    """Review of PR 81: the day and the period parse paths share one speed-versus-volume rule (a bare
    volume word beside a speed word does not veto it), and a why/slow cue keeps the overview form over a
    named window (`measure=overview&window=`), which the grammar resolves back to the same plan."""
    for text in ('did we make our speed target', 'what speed did we make', 'speed and output'):
        context = Context('2026-09-14', 2)
        context.scope = 'shift'
        assert canonical_url(parse_request(text, context), context) == '/corr/1/day/2026-09-14/shift/2?measure=fpm', text
        assert canonical_url(parse_request(text + ' this week', context), context) == \
            '/corr/1/day/2026-09-14/shift/2?measure=fpm&window=week', text
    for period, url in (('day', '/corr/1/day/2026-09-14/shift/2?measure=overview&window=week'),
                        ('week', '/corr/1/week/2026-09-14/shift/2?measure=overview'),
                        ('month', '/corr/1/month/2026-09-14/shift/2?measure=overview&window=week')):
        context = Context('2026-09-14', 2, period=period)
        context.scope = 'shift'
        assert canonical_url(parse_request('why was this shift slow this week', context), context) == url, period
    resolved = resolve_url('/corr/1/day/2026-09-14/shift/2?measure=overview&window=2026-08', default_store())
    assert [(s['tool'], s['arguments']['window']) for s in resolved['request']['plan']] == [
        (tool, {'period': 'month', 'anchor': '2026-08-01'}) for tool in ('get_period_kpis', 'get_downtime_pareto', 'get_waste_breakdown')]
    assert page_url(resolved) == '/corr/1/day/2026-09-14/shift/2?measure=overview&window=2026-08'


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
    ('/corr/1/day/2026-09-14?measure=downtime&window=week', 'window= does not apply'),
    ('/corr/1/day/2026-09-14?measure=overview&window=week&kind=maintenance', 'kind= narrows the day overview'),
    ('/corr/1/week/2026-09-14?measure=overview&kind=operator', 'kind= narrows the day overview'),
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


# Issue #70: hand-typed spellings of a canonical state, as (you may type, it becomes): the table of docs/URLS.md.
# The first two are issue #20's own example URLs; the third is its rollstock example, on a production day.
D, W, M, P = '/corr/1/day/2026-09-14', '/corr/1/week/2026-09-14', '/corr/1/month/2026-09-14', '/corr/1/period/2026-09-14'
ALIASES = [
    (f'{D}/shift/2?measure=downtime&by=reason&compare=prev', f'{D}/shift/2?measure=pareto'),
    (f'{P}/shift/3?measure=waste&by=grade', f'{P}/shift/3?measure=grade'),
    (f'{M}?measure=rollstock&compare=prev', f'{M}?measure=rollstock'),
    # compare=prev where the answer carries the previous window already: prev is its default
    (f'{W}?measure=kpis&compare=prev', f'{W}?measure=kpis'),
    (f'{D}/shift/2?measure=pareto&by=place&unit=count&compare=prev', f'{D}/shift/2?measure=pareto&by=place&unit=count'),
    (f'{W}?measure=waste&compare=prev', f'{W}?measure=waste'),
    (f'{M}/shift/1?measure=grade&compare=prev', f'{M}/shift/1?measure=grade'),
    (f'{W}/shift/2?measure=overview&by=kind&compare=prev', f'{W}/shift/2?measure=overview&by=kind'),
    (f'{M}?measure=fpm&compare=prev', f'{M}?measure=fpm'),
    (f'{P}?measure=summary&compare=prev', f'{P}?measure=summary'),
    (f'{D}/shift/2?measure=overview&window=week&compare=prev', f'{D}/shift/2?measure=overview&window=week'),
    (f'{D}/shift/2?measure=fpm&window=2026-08&compare=prev', f'{D}/shift/2?measure=fpm&window=2026-08'),
    # under a ranged grain downtime, quality and compare already are the Pareto, the waste breakdown and the KPI tiles
    (f'{W}/shift/2?measure=downtime&by=place&compare=prev', f'{W}/shift/2?measure=pareto&by=place'),
    (f'{W}/shift/2?measure=downtime', f'{W}/shift/2?measure=pareto'),
    (f'{M}?measure=quality&compare=prev', f'{M}?measure=waste'),
    (f'{W}?measure=compare&shifts=1,3&compare=prev', f'{W}?measure=kpis&shifts=1,3'),
    # under Day the same three take that store form, against the previous production day
    (f'{D}/shift/2?measure=downtime&by=kind&compare=prev', f'{D}/shift/2?measure=pareto&by=kind'),
    (f'{D}/shift/3?measure=quality&compare=prev', f'{D}/shift/3?measure=waste'),
    (f'{D}?measure=compare&shifts=2,1&compare=prev', f'{D}?measure=kpis&shifts=2,1'),
    # and a day measure that leads with the KPI tiles becomes the KPI compare
    (f'{D}/shift/2?measure=overview&compare=prev', f'{D}/shift/2?measure=kpis'),
    (f'{D}?measure=fpm&shifts=3&compare=prev', f'{D}?measure=kpis&shifts=3'),
    (f'{D}/shift/1?measure=summary&compare=prev', f'{D}/shift/1?measure=kpis'),
    # waste by grade is the totals by grade, waste by setup the setup matrix
    (f'{D}/shift/2?measure=waste&by=grade&window=month&compare=prev', f'{D}/shift/2?measure=grade&window=month'),
    (f'{D}/shift/2?measure=waste&by=setup&corrupt=1', f'{D}/shift/2?measure=setups&corrupt=1'),
    # a spelled-out default or another field order is the same state
    (f'{D}/shift/2?by=place&measure=pareto&unit=minutes', f'{D}/shift/2?measure=pareto&by=place'),
]


@pytest.mark.parametrize('typed, canonical', ALIASES)
def test_an_alias_resolves_to_its_canonical_state(typed, canonical):
    """The alias and its canonical address are one state: the same plan over the same shifts with the same stated
    objectives, so the same tools run (under a ranged grain the router's own labels of a plan, intent=period_downtime
    and group_by for the Pareto, may differ; the redirect serves the canonical address). The canonical address is a
    fixed point, so a redirect never loops."""
    store = default_store()
    alias, state = resolve_url(typed, store), resolve_url(canonical, store)
    assert page_url(alias) == canonical and alias['text'] == canonical
    same = lambda request: {k: v for k, v in request.items() if 'plan' not in request or k not in ('intent', 'group_by')}
    assert same(alias['request']) == same(state['request']) and alias['corrupt'] == state['corrupt']
    assert page_url(state) == canonical and redirect_target(canonical, state) is None
    assert redirect_target(typed, alias) == canonical
    path, query = canonical.split('?', 1)
    json_alias = typed.replace('?', '.json?', 1)
    assert redirect_target(json_alias, resolve_url(json_alias, store)) == f'{path}.json?{query}'   # the representation is kept
    assert redirect_target(typed + '&format=pdf', resolve_url(typed + '&format=pdf', store)) == canonical + '&format=pdf'


def test_every_redirect_lands_on_a_canonical_address_that_runs_the_same_plan():
    """A sweep of measures, grains, scopes and modifiers: a refused URL never redirects, and a redirect target
    resolves, runs the same plan over the same shifts, and does not redirect again. Every URL that resolves and is
    not canonical redirects: none is left answering at its own address (issue #90 gave the last of them, a
    ranged-grain downtime or quality split over named shifts, its resolvable spelling measure=pareto&shifts=1,3)."""
    store = default_store()
    tails = ['', 'compare=prev', 'shifts=1,3', 'shifts=2', 'shifts=1,3&compare=prev', 'by=place', 'by=place&compare=prev',
             'by=reason', 'by=grade', 'by=setup', 'by=grade&compare=prev', 'kind=operator&compare=prev', 'window=week',
             'window=week&compare=prev', 'window=2026-08&compare=prev', 'unit=minutes', 'compare=none', 'want=chart&compare=prev']
    redirected = stayed = 0
    for base in (D, W, M, P):
        for scope in ('', '/shift/2'):
            for measure in [*INTENT_MEASURES.values(), *STORE_MEASURES, 'report']:
                for tail in tails:
                    url = f'{base}{scope}?measure={measure}' + ('&metric=trim_pct' if measure == 'trend' else '') + (f'&{tail}' if tail else '')
                    try:
                        resolved = resolve_url(url, store)
                    except ValueError:
                        assert redirect_for(url, store) is None, url
                        continue
                    target = redirect_for(url, store)
                    if target is None:
                        stayed += redirect_target(url, resolved) is not None
                        continue
                    landed = resolve_url(target, store)
                    ran = lambda r: r['report'] or (r['request'].get('plan') or r['request']['intent'], r['request']['shifts'])
                    assert redirect_for(target, store) is None and page_url(landed) == target, url
                    assert ran(landed) == ran(resolved), url
                    redirected += 1
    assert redirected > 500 and stayed == 0, (redirected, stayed)
    assert redirect_for(f'{W}?measure=downtime&shifts=1,3', store) == f'{W}?measure=pareto&shifts=1,3'
    assert redirect_for(f'{M}?measure=quality&shifts=2,1&compare=prev', store) == f'{M}?measure=waste&shifts=2,1'


# Issue #90: the twelve URLs the sweep used to leave at their own address (downtime and quality split over shifts 1
# and 3, under the three ranged grains, with and without compare=prev), each with the canonical address it becomes.
SPLIT = [(f'{base}?measure={measure}&shifts=1,3' + tail, f'{base}?measure={target}&shifts=1,3')
         for base in (W, M, P) for measure, target in (('downtime', 'pareto'), ('quality', 'waste')) for tail in ('', '&compare=prev')]


@pytest.mark.parametrize('typed, canonical', SPLIT)
def test_a_split_shift_pareto_or_waste_breakdown_round_trips_under_a_ranged_grain(typed, canonical):
    """One breakdown per named shift under Week, Month or 4-4-5: the day-shaped spelling redirects to the store measure
    with shifts=, which resolves to the same plan and is its own canonical address (no 400, no second redirect). The
    router's own question lands on it too, and under Day a store measure is still not split by shift."""
    store = default_store()
    typed_state, state = resolve_url(typed, store), resolve_url(canonical, store)
    assert redirect_for(typed, store) == canonical and redirect_for(canonical, store) is None
    assert page_url(state) == canonical and page_url(typed_state) == canonical
    assert state['request']['plan'] == typed_state['request']['plan'] and state['request']['shifts'] == [1, 3]
    assert [s['arguments']['shift'] for s in state['request']['plan']] == [1, 3]
    grain = canonical.split('/')[3]
    context = Context('2026-09-14', 2, period={'week': 'week', 'month': 'month', 'period': 'fiscal'}[grain])
    context.scope = 'day'
    question = ('Downtime by reason' if 'pareto' in canonical else 'Waste') + ' for first shift and third shift'
    request = parse_request(question, context)
    url = canonical_url(request, context)
    assert url == canonical and canonical_url(resolve_url(url, store)['request'], context) == url
    assert resolve_url(url, store)['request']['plan'] == request['plan']
    day = canonical.replace(f'/{grain}/', '/day/')
    with pytest.raises(ValueError, match='not split by shift under Day'):
        resolve_url(day, store)


@pytest.mark.parametrize('url, reason', [
    (f'{D}?measure=stops&compare=prev','compare= does not apply to measure=stops: its answer carries no previous window'),
    (f'{W}?measure=overrun&compare=prev', 'compare= does not apply to measure=overrun'),
    (f'{D}?measure=opportunity&compare=prev', 'compare= does not apply to measure=opportunity'),
    (f'{D}?measure=trend&metric=trim_pct&compare=prev', 'compare= does not apply to measure=trend'),
    (f'{D}/shift/2?measure=fpm&by=grade&compare=prev', 'compare= does not apply to measure=fpm'),
    (f'{D}/shift/2?measure=notes&compare=prev', 'compare= does not apply to measure=notes'),
    (f'{W}?measure=report&compare=prev', 'compare= does not apply to measure=report'),
    (f'{W}?measure=setups&compare=prev', 'Switch Period to Day'),
    (f'{D}/shift/2?measure=summary&window=week&compare=prev', 'window= does not apply to measure=summary'),
    (f'{D}/shift/2?measure=downtime&kind=maintenance&compare=prev', 'kind= does not apply to measure=downtime&compare=prev under Day'),
    (f'{D}/shift/2?measure=overview&by=place&compare=prev', 'by= does not apply to measure=overview&compare=prev under Day'),
    (f'{D}?measure=compare&kind=operator&compare=prev', 'kind= does not apply to measure=compare&compare=prev under Day'),
    (f'{D}?measure=downtime&shifts=1,2&compare=prev', r'not split by shift under Day \(measure=downtime&compare=prev becomes measure=pareto\)'),
    (f'{W}?measure=pareto&compare=none', 'compare= does not apply to measure=pareto: a comparison base'),
    (f'{M}?measure=waste&compare=2026-08', 'compare= does not apply to measure=waste'),
    (f'{D}?measure=waste&by=crew', 'by=crew does not apply to measure=waste'),
    (f'{W}?measure=waste&by=setup', 'by=setup does not apply to measure=waste under Week'),
    (f'{D}?measure=waste&by=grade&unit=count', r'unit= does not apply to measure=grade \(measure=waste&by=grade becomes measure=grade\)'),
    (f'{D}?measure=downtime&by=grade', 'by is reason, place or kind'),   # by=grade is an alias on waste only
])
def test_an_alias_that_cannot_be_normalized_is_refused_naming_the_field(url, reason):
    with pytest.raises(ValueError, match=reason):
        resolve_url(url, default_store())


@pytest.fixture(scope='module')
def served():
    server = make_server(0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def get(server, path, accept='application/json'):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    try:
        client.request('GET', path, headers={'Accept': accept})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


@pytest.mark.parametrize('typed, canonical', ALIASES)
def test_an_alias_answers_through_a_302_to_its_canonical_address(served, typed, canonical):
    """302 -> canonical -> 200, as /q is: the redirect runs no tool and the canonical address answers."""
    status, headers, body = get(served, typed)
    assert status == 302 and headers['Location'] == canonical and json.loads(body)['url'] == canonical
    assert headers['Cache-Control'] == 'no-store' and 'Server-Timing' not in headers
    status, _, body = get(served, canonical)
    assert status == 200 and json.loads(body)['url'] == canonical


def test_the_issue_20_examples_redirect_in_every_representation(served):
    """The page, the .json suffix, format=json and format=pdf each redirect to the same representation of the
    canonical address, which answers; a refused alias is a 400 naming its field and the page still loads."""
    for typed, canonical in ALIASES[:2]:
        path, query = canonical.split('?', 1)
        status, headers, _ = get(served, typed, accept='text/html,application/xhtml+xml,*/*;q=0.8')
        assert status == 302 and headers['Location'] == canonical
        status, headers, page = get(served, canonical, accept='text/html')
        assert status == 200 and headers['Content-Type'].startswith('text/html') and b'Shift Investigator' in page
        status, headers, _ = get(served, typed.replace('?', '.json?', 1), accept='*/*')
        assert status == 302 and headers['Location'] == f'{path}.json?{query}'
        status, _, body = get(served, headers['Location'], accept='*/*')
        assert status == 200 and json.loads(body)['url'] == canonical
        status, headers, _ = get(served, typed + '&format=json', accept='*/*')
        assert status == 302 and headers['Location'] == canonical + '&format=json'
        assert get(served, headers['Location'], accept='*/*')[0] == 200
    status, headers, _ = get(served, ALIASES[0][0] + '&format=pdf', accept='*/*')
    assert status == 302 and headers['Location'] == ALIASES[0][1] + '&format=pdf'
    status, headers, pdf = get(served, headers['Location'], accept='*/*')
    assert status == 200 and headers['Content-Type'] == 'application/pdf' and pdf.startswith(b'%PDF-')
    refused = f'{D}/shift/2?measure=downtime&kind=maintenance&compare=prev'
    status, _, body = get(served, refused)
    assert status == 400 and json.loads(body)['error'].startswith('kind= does not apply to measure=downtime&compare=prev')
    assert get(served, refused, accept='text/html')[0] == 200   # the page loads and shows the refusal of its .json
    assert get(served, f'{D}/shift/2?measure=pareto')[0] == 200   # a canonical address answers at once
    # a split-shift Pareto under a ranged grain (issue #90): a 302 to its canonical address, which answers with both shifts
    for url in (f'{W}?measure=downtime&shifts=1,3', f'{W}?measure=downtime&shifts=1,3&compare=prev'):
        status, headers, _ = get(served, url)
        assert status == 302 and headers['Location'] == f'{W}?measure=pareto&shifts=1,3', url
        status, _, body = get(served, headers['Location'])
        assert status == 200 and json.loads(body)['selected_shifts'] == [1, 3], url


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
