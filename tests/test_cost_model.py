"""The cost model (issue #31): rates, pricing, the two money tools, routing and rates in the URL."""
import pytest

from investigator import cost_model
from investigator.agent import Agent, Context, parse_request
from investigator.narrative import evidence_packet
from investigator.openai_provider import DOLLAR_FIELDS, compact_result
from investigator.record_store import default_store
from investigator.server import Workbench
from investigator.urls import canonical_url, page_url, resolve_url

DAY = '2026-09-14'
COMPONENTS = ('paper_usd', 'machine_time_usd', 'contribution_usd')


@pytest.fixture(scope='module')
def store():
    return default_store()


@pytest.fixture(scope='module')
def analysis(store):
    return store.day_analysis(DAY)


def rates(text=''):
    return cost_model.effective(cost_model.parse_rates(text))


def test_the_posts_example_reproduces():
    """0.1 point of side trim across two machines = 0.001 x 3.6M MSF x $55 = $198,000 a year."""
    assert cost_model.trim_points_usd_per_year(0.1, machines=2) == pytest.approx(198_000)
    assert cost_model.MSF_PER_MINUTE == pytest.approx(5.4)          # one minute at 675 ft/min on 96 in net
    assert cost_model.DAYS_PER_YEAR * 7000 == pytest.approx(1_800_000)


def test_rates_parse_format_and_layer(monkeypatch):
    assert cost_model.parse_rates('margin:40,paper:60') == {'margin_usd_msf': 40.0, 'paper_usd_msf': 60.0}
    assert cost_model.format_rates(cost_model.parse_rates('margin:40,paper:60')) == 'paper:60,margin:40'
    for bad in ('paper', 'paper:x', 'paper:0', 'paper:-5', 'paper:inf', 'tons:3', 'paper:55,paper:56', 'trim:101',
                'paper:\uff15\uff15', 'hour:\u0661\u0662\u0660\u0660'):   # full-width and Arabic-Indic digits: money.js refuses them too
        with pytest.raises(ValueError):
            cost_model.parse_rates(bad)
    monkeypatch.setenv(cost_model.ENV, 'paper:50,hour:1000')
    layered = cost_model.effective({'paper_usd_msf': 70.0})
    assert layered['values']['paper_usd_msf'] == 70 and layered['sources']['paper_usd_msf'] == 'yours'
    assert layered['values']['machine_usd_hour'] == 1000 and layered['sources']['machine_usd_hour'] == 'server'
    assert layered['values']['margin_usd_msf'] is None
    monkeypatch.setenv(cost_model.ENV, 'paper:abc')
    with pytest.raises(ValueError, match=cost_model.ENV):
        cost_model.effective()


def test_margin_not_set_is_never_zero(analysis):
    data = cost_model.shift_dollars(analysis, 2, rates())
    assert data['contribution_status'] == 'margin not set' and data['total_contribution_usd'] is None
    for row in data['rows']:
        assert row['contribution_usd'] is None and row['contribution_status'] == 'margin not set'
        assert 'margin not set' in row['usd_basis']
    assert 'contribution margin not set' in data['provenance']


def test_changing_one_rate_changes_its_dollars_and_nothing_else(analysis):
    base = cost_model.shift_dollars(analysis, 2, rates('margin:40'))
    for text, changed, factor in (('paper:110,margin:40', 'paper_usd', 2), ('hour:2400,margin:40', 'machine_time_usd', 2),
                                  ('margin:80', 'contribution_usd', 2)):
        other = cost_model.shift_dollars(analysis, 2, rates(text))
        before, after = {r['gap']: r for r in base['rows']}, {r['gap']: r for r in other['rows']}
        assert before.keys() == after.keys()
        for gap, row in before.items():
            assert after[gap]['quantity'] == row['quantity'] and after[gap]['msf_not_made'] == row['msf_not_made']
            for key in COMPONENTS:
                if row[key] is None:
                    assert after[gap][key] is None
                elif key == changed:
                    assert after[gap][key] == pytest.approx(row[key] * factor, abs=0.02)
                else:
                    assert after[gap][key] == row[key], (text, gap, key)
            # usd is paper plus machine time: contribution never enters it
            assert after[gap]['usd'] == pytest.approx((after[gap]['paper_usd'] or 0) + after[gap]['machine_time_usd'], abs=0.011)


def test_shift_dollars_rank_and_add_up(analysis):
    data = cost_model.shift_dollars(analysis, 2, rates())
    usd = [r['usd'] for r in data['rows']]
    assert usd == sorted(usd, reverse=True) and [r['rank'] for r in data['rows']] == list(range(1, len(usd) + 1))
    assert data['total_usd'] == pytest.approx(sum(usd), abs=0.02)
    kinds = {r['kind'] for r in data['rows']}
    assert {'downtime', 'speed', 'trim', 'waste'} <= kinds | {'setup'} and 'source_ids' in data['rows'][0]
    trim = next(r for r in data['rows'] if r['gap'] == 'Side trim')
    assert trim['machine_time_usd'] == 0 and trim['paper_usd'] == pytest.approx(trim['quantity'] / 1000 * 55, abs=0.01)


def test_opportunities_rank_levers_and_name_records(store):
    data = cost_model.opportunities(store, {'period': 'month', 'anchor': DAY}, None, rates('trim:3'))
    years = [r['usd_per_year'] for r in data['rows']]
    assert years == sorted(years, reverse=True)
    levers = {r['lever'] for r in data['rows']}
    assert {"Speed under each grade's trailing-year speed", 'Side trim over the 3% target', 'Downtime: Order change (setup time)'} <= levers
    assert all(r['records'] for r in data['rows'])
    trim = next(r for r in data['rows'] if r['lever'].startswith('Side trim'))
    assert trim['quantity'] > 0 and trim['usd_per_year'] == pytest.approx(
        cost_model.trim_points_usd_per_year(trim['quantity']), rel=1e-3)   # the post's trim formula, at one machine
    assert 'prescribe' in data['method_note']


@pytest.mark.parametrize('question, period, tool', [
    ('Where did the money go this shift?', 'day', 'get_shift_dollars'),
    ('Where is the money this month?', 'day', 'get_opportunities'),
    ("What's costing us?", 'week', 'get_opportunities'),
    ('opportunities', 'month', 'get_opportunities'),
    ('Where is the speed opportunity this month?', 'day', 'get_speed_opportunity'),
    ('Which downtime reason cost the most minutes this month?', 'day', 'get_downtime_pareto'),
    ('Which crews run faster this week, and does it cost rejects?', 'day', 'get_speed_opportunity'),
])
def test_money_routing(question, period, tool):
    context = Context(DAY, 2, period=period, scope='shift')
    request = parse_request(question, context)
    planned = [s['tool'] for s in request.get('plan', [])] or (['get_shift_dollars'] if request['intent'] == 'dollars' else [])
    assert planned == [tool]


def test_priced_tools_carry_dollars_the_narrative_may_cite(analysis):
    agent = Agent(analysis)
    result = agent.ask('Where did the money go this shift?', Context(DAY, 2, scope='shift'))
    step = result['trace'][0]
    assert step['tool'] == 'get_shift_dollars' and result['status'] == 'complete'
    packet = evidence_packet([step])
    assert {'usd', 'usd_per_year', 'paper_usd', 'machine_time_usd'} <= set(packet[0]['rows'][0])
    downtime = agent._execute('get_downtime_breakdown', {'shift': 2, 'kind': None, 'group_by': 'reason'}, {}, rates())
    assert downtime['rows'][0]['machine_time_usd'] == pytest.approx(downtime['rows'][0]['down_minutes'] / 60 * 1200, abs=0.01)
    compact = compact_result({'result_id': 'x', 'tool': 'get_downtime_breakdown', 'ok': True, 'data': downtime})
    assert not set(compact['data']['rows'][0]) & set(DOLLAR_FIELDS)   # the planner chooses tools; the dollars are for the reader


def test_rates_ride_in_the_url(store):
    context = Context(DAY, 2, period='day', scope='shift')
    context.rates = cost_model.parse_rates('margin:40,paper:60')
    url = canonical_url(parse_request('where did the money go', context), context)
    assert url == '/corr/1/day/2026-09-14/shift/2?measure=dollars&rates=paper:60,margin:40'
    resolved = resolve_url(url, store)
    assert resolved['context'].rates == context.rates and page_url(resolved) == url
    with pytest.raises(ValueError, match='rates takes'):
        resolve_url('/corr/1/day/2026-09-14/shift/2?measure=dollars&rates=tons:3', store)


def test_server_prices_with_the_visitors_rates():
    app = Workbench()
    try:
        url = app.question_url('day=2026-09-14&shift=2&scope=shift&period=day&rates=paper:60&q=where+did+the+money+go')
        assert url.endswith('measure=dollars&rates=paper:60')
        result, _ = app.answer_url(url)
        assert 'paper $60/MSF (yours)' in ' '.join(result['sections'])
        asked = app.ask({'day': DAY, 'shift': 2, 'scope': 'shift', 'question': 'where did the money go', 'rates': 'hour:1500'})
        assert asked['url'].endswith('rates=hour:1500') and 'machine time $1,500/hr (yours)' in ' '.join(asked['sections'])
        with pytest.raises(ValueError):
            app.ask({'day': DAY, 'shift': 2, 'question': 'where did the money go', 'rates': 'paper:-1'})
    finally:
        app.close()


@pytest.mark.parametrize('question', [
    'Compare September with August in dollars', 'How did this week go versus last week in dollars?',
    'Trend of trim in dollars over eight weeks', 'How much paper did we consume this month in dollars?',
    'Summarize this shift in dollars', 'Show the setup matrix in dollars',
])
def test_money_never_answers_a_different_question(question):
    """A money word with another shape is refused with what to ask, never answered as something else."""
    with pytest.raises(ValueError, match='not available yet'):
        parse_request(question, Context(DAY, 2, scope='shift'))


@pytest.mark.parametrize('question, intent, tool', [
    ('Which downtime reason is costing us the most minutes this month?', 'period_pareto', 'get_downtime_pareto'),
    ('Which downtime reason is costing us the most minutes?', 'period_pareto', 'get_downtime_pareto'),
    ("What's trim costing us this month?", 'period_money', 'get_opportunities'),   # the ownership lens (issue #101)
    ('Why was shift 2 slow, and what did it cost?', 'overview', None),
    ('Where should we look for the cause of the warp on shift 2?', 'quality', None),
    ('What does the speed gap cost us?', 'dollars', None),
])
def test_money_with_a_priced_shape_answers_that_shape(question, intent, tool):
    request = parse_request(question, Context(DAY, 2, scope='shift'))
    assert request['intent'] == intent and [s['tool'] for s in request.get('plan', [])] == ([tool] if tool else [])


def test_a_rate_in_the_link_is_exactly_the_rate_used(store):
    assert cost_model.format_rates(cost_model.parse_rates('hour:1234.56,paper:55.5')) == 'paper:55.5,hour:1234.56'
    with pytest.raises(ValueError, match='two decimals'):
        cost_model.parse_rates('hour:1234.567')
    app = Workbench()
    try:
        asked = app.ask({'day': DAY, 'shift': 2, 'scope': 'shift', 'question': 'where did the money go', 'rates': 'hour:1234.56'})
        again, _ = app.answer_url(asked['url'])
        assert asked['url'].endswith('rates=hour:1234.56') and again['sections'] == asked['sections']
        assert 'machine time $1,234.56/hr (yours)' in ' '.join(asked['sections'])
    finally:
        app.close()


def test_the_per_year_factor_is_in_the_formula(analysis):
    row = next(r for r in cost_model.shift_dollars(analysis, 2, rates())['rows'] if r['gap'] == 'Side trim')
    assert 'per year: x ' in row['usd_basis'] and '1,800,000 MSF a year' in row['usd_basis']
    downtime = next(r for r in cost_model.shift_dollars(analysis, 2, rates())['rows'] if r['kind'] in ('downtime', 'setup'))
    assert 'scheduled min a year / 480 min' in downtime['usd_basis']


def test_invalid_server_rates_fail_at_boot(monkeypatch):
    monkeypatch.setenv(cost_model.ENV, 'paper:abc')
    with pytest.raises(ValueError, match=cost_model.ENV):
        Workbench()


def test_openai_planner_request_with_a_priced_waste_result_is_byte_for_byte_mains(store, tmp_path):
    """The dollars add nothing to the OpenAI planner loop: neither money tool is offered, and the compact
    result a priced waste breakdown hands the next request is the unpriced one's, byte for byte. The cap is
    main's own: since #74 widened get_grade_totals, main's request after one waste breakdown is 24,040 bytes
    against the 24,000-byte cap, with or without the money tools (measured on af23a4d)."""
    import json
    from copy import deepcopy
    from investigator.openai_provider import Budget, OpenAIPlanner, OPENAI_OMITTED_TOOLS
    window = {'period': 'month', 'anchor': DAY}
    plain = deepcopy(store.get_waste_breakdown(window=window, shift=2))
    priced = cost_model.attach('get_waste_breakdown', deepcopy(plain), rates())
    assert priced['provenance'] and all('usd' in row for row in priced['rows'])
    compact = lambda data: json.dumps(compact_result({'result_id': 'r1', 'tool': 'get_waste_breakdown', 'ok': True,
                                                      'arguments': {'window': window, 'shift': 2}, 'data': data}))
    assert compact(priced) == compact(plain)
    offered = []

    def send(payload, key, timeout):
        offered.append({t['name'] for t in payload['tools']})
        return {'status': 'completed', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                'output': [{'type': 'function_call', 'id': 'fc', 'call_id': 'c1', 'name': 'cannot_answer', 'arguments': json.dumps({'reason': 'done'})}]}
    planner = OpenAIPlanner('sk-fake-test-key-not-a-real-credential', Budget(tmp_path / 'b.db'), send)
    Agent(store.day_analysis(DAY), planner, store=store, max_seconds=60).ask('Is it bond or warp this month?', Context(DAY, 2, scope='shift'))
    assert offered and not offered[0] & OPENAI_OMITTED_TOOLS and 'get_waste_breakdown' in offered[0]


def test_the_profit_disclosure_says_the_dollars_are_priced_at_assumed_rates():
    """Review of #31a: the 'no costs' preamble sat directly above priced levers. When the answer carries
    dollars it says they are these records priced at assumed rates, not the plant's; without dollars it
    is unchanged. The golden 'profit' and 'live-profit' cases match the unchanged first sentence."""
    app = Workbench()
    try:
        first = 'these records contain no costs, revenue or measured intervention outcomes'
        priced, _ = app.answer_url(f'/corr/1/day/{DAY}/shift/2?measure=money&window=month&want=profit')
        plain, _ = app.answer_url(f'/corr/1/day/{DAY}/shift/2?measure=fpm&want=profit')
        line = lambda result: next(s for s in result['sections'] if 'financial objective is unavailable' in s)
        assert first in line(priced) and "priced at assumed rates, not the plant's" in line(priced)
        assert first in line(plain) and 'assumed rates' not in line(plain)
    finally:
        app.close()
