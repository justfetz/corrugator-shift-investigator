"""The cost model (issue #31): rates, pricing, the two money tools, routing and rates in the URL."""
import pytest

from investigator import cost_model
from investigator.agent import Agent, Context, parse_request
from investigator.narrative import evidence_packet
from investigator.openai_provider import compact_result
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
    for bad in ('paper', 'paper:x', 'paper:0', 'paper:-5', 'paper:inf', 'tons:3', 'paper:55,paper:56', 'trim:101'):
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
    assert {'Speed gap to best-fifth speed', 'Side trim over the 3% target', 'Downtime: Order change (setup time)'} <= levers
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
    assert 'usd_basis' not in compact['data']['rows'][0] and 'usd' in compact['data']['rows'][0]


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
