"""Exact over summary (issue #113): a question that asks for one quantity opens with that quantity, in the unit asked, or
is refused with what can be counted; the KPI summary (`measure=kpis`) answers "how did we do", never a count or a unit.

The catch-all audit lives here as AUDIT: the issue's seven rows, the owner's interview phrasings (#24) and the count and
unit phrasings that landed on the KPI summary on main. Each must land on a measure that answers it (`evals/run.py`
states the guard and the headline-unit rule on its own; this file runs them over the audit as well as the goldens)."""
import json
import re
import sys
from pathlib import Path

import pytest

from investigator.agent import (QUANTITY_HELP, QUANTITY_METRICS, Agent, Context, catch_all_guard, parse_request, question_scope,
                                quantity_request, route, slug)
from investigator.records import STOP_PLACES, STOP_REASONS
from investigator.record_store import default_store
from investigator.urls import canonical_url, page_url, redirect_for, resolve_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'evals'))
from run import asked_unit_failures, catch_all_failures  # noqa: E402

DAY = '2026-09-14'
GOLDEN = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']

# The audit (issue #113, owner's comment of 2026-11-11), asked with shift 2 and Day on Mon 14 Sep. Before this change the
# first group landed on measure=kpis; the rest opened with another unit (MSF for feet, a stop count for minutes, a
# percent for minutes or stops) or were refused with the generic help. The PR's before/after table lists each one.
AUDIT = [
    'how many setups did we run last week', 'how many paper changes last week', 'how many order changes this week',
    'what was our lineal per setup last week', 'how many minutes of maintenance downtime yesterday', 'how many stops did we have yesterday',
    'how many setups yesterday', 'How many missed splices this week?', 'how many step-ups did we have this week',
    'how many rolls did we use this week', 'how many boards did we lose at the knife this week', 'how many orders did we run this week',
    'how many wet ends this week', 'how many paper changes this month', 'number of setups this month', 'count of stops this week',
    'how many stops this week', 'how many feet did we run last week', 'what was our feet per paper change this week',
    'minutes per stop this week', 'average minutes per stop last month', 'how many minutes did we lose to missed splices this week',
    'how many stops at the stacker this week', 'how many changeovers this week', 'how many order changes last week',
    'how many setups this period', 'how many paper changes on shift 1 last week', 'how many setups did we run on shift 3 last month',
    'How many splices did we miss yesterday?', 'how many rolls did we run yesterday', 'how many sheets did we make this week',
    'how many sheets did we run yesterday', 'how many boards did we lose at the stacker yesterday', 'how many orders did we run yesterday',
    'how many minutes of downtime last week', 'how many minutes of operator downtime this week', 'how many hours of downtime this month',
    'how much downtime in minutes yesterday', 'total downtime minutes this week', 'how many lineal feet did we run this week',
    'how many minutes of steam / moisture this month', ('How did shift 1 go compared with last week on lineal per paper change?', 1),
    'How did we do on setups this month?', 'Compare stops this week to last week', 'How many tons did we ship?',
]


def landing(question, scope='shift', shift=2, period='day', day=DAY):
    """(canonical URL, request, context) of a question, or (None, the refusal, None)."""
    store = default_store()
    context = Context(day, shift, scope=scope, period=period)
    try:
        request, asked = route(question, context, store.production_days)
    except ValueError as exc:
        return None, str(exc), None
    return canonical_url(request, asked), request, asked


def test_the_guard_flags_a_quantity_question_on_the_kpi_summary():
    """The rule in evals/run.py, held to the URLs main resolved the issue's rows to (its table), and to the questions the
    KPI summary is for."""
    for question, url in [('how many setups did we run last week', '/corr/1/week/2026-09-07/shift/2?measure=kpis'),
                          ('how many order changes this week', '/corr/1/day/2026-09-14/shift/2?measure=kpis&window=week'),
                          ('what was our lineal per setup last week', '/corr/1/week/2026-09-07/shift/2?measure=kpis'),
                          ('how many minutes of downtime this month', '/corr/1/day/2026-09-14?measure=kpis&window=month')]:
        assert catch_all_failures(question, url), question
        assert not catch_all_failures(question, url.replace('measure=kpis', 'measure=quantity&metric=setups')), question
    for question in ('Compare this month to last', 'How did September go versus August?', 'How did shift 3 go this month?', 'Compare all shifts'):
        assert not catch_all_failures(question, '/corr/1/day/2026-09-14?measure=kpis&window=month'), question


@pytest.mark.parametrize('question', AUDIT, ids=lambda q: q if isinstance(q, str) else q[0])
def test_no_audited_question_lands_on_the_kpi_summary_and_each_opens_with_its_unit(question):
    """Every audited question lands on a measure that answers it, or is refused with what can be counted; an answer's
    headline carries the unit asked (run.py's rule), and its address answers the same. A question asked with another
    shift selected names it beside the question (the golden case's controls)."""
    question, shift = (question, 2) if isinstance(question, str) else question
    url, request, asked = landing(question, shift=shift)
    if url is None:
        assert QUANTITY_HELP in request or 'Offline mode supports' in request, (question, request)
        return
    assert not catch_all_failures(question, url), (question, url)
    store = default_store()
    analysis = store.day_analysis(asked.production_day)
    try:
        result = Agent(analysis).ask(question, asked, request=request)
    finally:
        analysis.close()
    assert result['status'] == 'complete', (question, result['sections'][:2])
    assert not asked_unit_failures(question, result['sections']), (question, result['sections'][0])
    resolved = resolve_url(url, store)
    assert page_url(resolved) == url and redirect_for(url, store) is None, url
    assert resolved['request'].get('lead') == request.get('lead') and resolved['request']['plan'] == request['plan'], url


def test_the_seven_rows_of_the_issue_land_on_one_quantity():
    """The issue's table, by row: the measure and the metric each lands on, and the scope it is answered in."""
    rows = {'how many setups did we run last week': '/corr/1/week/2026-09-07/shift/2?measure=quantity&metric=setups',
            'how many paper changes last week': '/corr/1/week/2026-09-07/shift/2?measure=quantity&metric=paper_changes',
            'how many order changes this week': '/corr/1/day/2026-09-14/shift/2?measure=quantity&window=week&metric=order_changes',
            'what was our lineal per setup last week': '/corr/1/week/2026-09-07/shift/2?measure=quantity&metric=lineal_per_setup',
            'how many minutes of maintenance downtime yesterday': '/corr/1/day/2026-09-11?measure=quantity&metric=maintenance_minutes',
            'how many stops did we have yesterday': '/corr/1/day/2026-09-11?measure=quantity&metric=stops',
            'how many setups yesterday': '/corr/1/day/2026-09-11/shift/2?measure=quantity&metric=setups'}
    for question, url in rows.items():
        assert landing(question)[0] == url, question


def test_the_router_refuses_a_quantity_it_cannot_place_rather_than_summarize():
    """The last line of the router (issue #113, item 3): a plan that is the KPI summary alone, asked by a question that
    names a countable noun or a unit, is refused with what can be counted until the model reads such questions (#104)."""
    summary = {'intent': 'period_compare', 'plan': [{'tool': 'get_period_kpis', 'arguments': {}}]}
    with pytest.raises(ValueError, match='one quantity at a time'):
        catch_all_guard('how did we do on setups this month', summary)
    catch_all_guard('how did we do this month', summary)   # the summary is what that question asks for
    catch_all_guard('how many setups this month', {**summary, 'intent': 'period_quantity'})
    context = Context(DAY, 2, scope='shift', period='day')
    for question in ('How did we do on setups this month?', 'Compare stops this week to last week', 'How did August go on paper changes?'):
        with pytest.raises(ValueError, match='one quantity at a time'):
            parse_request(question, context)
    assert parse_request('How did August go?', context)['plan'][0]['tool'] == 'get_period_kpis'


@pytest.mark.parametrize('question, refusal', [
    ('How many step-ups did we have this week?', 'no step-ups'), ('How many rolls did we use?', 'do not count rolls'),
    ('How many boards did we lose at the stacker?', 'do not count boards'), ('How many splices did we run?', 'not every splice'),
    ('How many orders did we run?', 'distinct orders'), ('How many customers did we run this week?', 'do not count customers'),
    ('How many hours of downtime this month?', 'in minutes, not hours'), ('How many pallets did we ship?', 'no shipping'),
])
def test_a_count_the_records_do_not_hold_is_refused_with_what_can_be_counted(question, refusal):
    with pytest.raises(ValueError, match=refusal) as caught:
        parse_request(question, Context(DAY, 2, scope='shift', period='day'))
    assert QUANTITY_HELP in str(caught.value)


def test_every_quantity_has_one_canonical_address_in_every_grain():
    """measure=quantity round-trips for every metric, every stop reason and place, under every grain and both scopes;
    a metric, reason or place outside the catalog is refused, and so is reason= on another measure."""
    store = default_store()
    windows = {'day': ('day', DAY), 'week': ('week', DAY), 'month': ('month', DAY), 'period': ('fiscal', DAY)}
    for grain, (period, anchor) in windows.items():
        for scope, shift in (('day', None), ('shift', 2)):
            context = Context(DAY, shift or 2, scope=scope, period=period)
            leads = [{'metric': m} for m in QUANTITY_METRICS] + [{'metric': 'stops', 'reason': r} for r in STOP_REASONS] + \
                    [{'metric': 'downtime_minutes', 'place': p} for p in STOP_PLACES]
            for lead in leads:
                request = quantity_request(lead, {'period': period, 'anchor': anchor}, shift)
                url = canonical_url(request, context)
                resolved = resolve_url(url, store)
                assert page_url(resolved) == url and resolved['request']['lead'] == lead and resolved['request']['plan'] == request['plan'], url
    for url, reason in [(f'/corr/1/week/{DAY}?measure=quantity', 'metric is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=volume_msf', 'metric is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&reason=coffee', 'reason is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&place=canteen', 'place is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=setups&reason=missed-splice', 'takes metric=stops'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&reason=stacker&place=stacker', 'one of reason= or place='),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=setups&shifts=1,3', 'not split by shift'),
                        (f'/corr/1/week/{DAY}?measure=kpis&reason=missed-splice', 'reason= does not apply'),
                        (f'/corr/1/week/{DAY}?measure=pareto&place=stacker', 'place= does not apply')]:
        with pytest.raises(ValueError, match=reason):
            resolve_url(url, store)
    assert slug('Knife / slitter jam') == 'knife-slitter-jam' and slug('Raw material (roll)') == 'raw-material-roll' and slug('Slitter/scorer') == 'slitter-scorer'


def test_a_downtime_quantity_takes_the_owner_default_and_a_setup_count_keeps_the_controls():
    """No shift and no window but yesterday: a downtime quantity is the previous production day across all shifts (#101),
    as the downtime review is; a setup count is asked of the controls; a named shift or window keeps its own scope."""
    store = default_store()
    context = Context(DAY, 2, scope='shift', period='day')
    for question in ('How many stops did we have?', 'How many minutes of maintenance downtime yesterday?', 'How many missed splices yesterday?',
                     'What was our minutes per stop?'):
        moved = question_scope(question, context, store.production_days)
        assert (moved.scope, moved.production_day, moved.read.get('default')) == ('day', '2026-09-11', 'the previous production day, all shifts'), question
    assert question_scope('How many setups did we run?', context, store.production_days) is context
    assert question_scope('How many stops this week?', context, store.production_days) is context
    assert question_scope('How many stops on shift 2 yesterday?', context, store.production_days).scope == 'shift'


def test_order_changes_are_counted_beside_the_tiles_and_never_zero_for_a_missing_window():
    """The KPI tool counts the setups an order change started (change code O) with the previous window's; a window the
    records do not hold is unavailable (None), never zero. The value goldens recount them from the raw records."""
    store = default_store()
    week = store.get_period_kpis({'period': 'week', 'anchor': '2026-09-07'}, shift=2)
    setups = next(r for r in week['rows'] if r['metric'] == 'setups')
    assert 0 < week['order_changes'] <= setups['current'] and week['order_changes_delta'] == week['order_changes'] - week['previous_order_changes']
    first = store.get_period_kpis({'period': 'week', 'anchor': store.production_days[0]})
    assert first['previous_order_changes'] is None and first['order_changes_delta'] is None


def test_a_count_or_unit_golden_states_its_headline():
    """Every golden routing case whose question asks for a count or a unit pins the headline's unit (`shape.headline_match`
    or a headline in `sections_match`), so the headline-unit rule is held case by case as well as by run.py."""
    asks = re.compile(r'\b(how many|number of|count of)\b|\bminutes per stop\b|\bper (set-?up|paper change)\b', re.I)
    for case in GOLDEN:
        question = (case.get('turns') or [case.get('question') or ''])[-1]
        if case['type'] != 'routing' or case.get('mode') != 'offline' or not asks.search(question) or 'trend' in question.lower():
            continue
        expect = case['expect']
        patterns = [expect.get('shape', {}).get('headline_match', '')] + (
            [expect['sections_match']] if isinstance(expect.get('sections_match'), str) else expect.get('sections_match', []))
        assert any(p.startswith('^\\*\\*') for p in patterns), case['id']
