"""The UI test's why-slow answer (tests/ui_fixtures/why_slow.json, issue #65) is the offline result of its golden
URL. If the generator or the planner changes, this fails and names the command that refreshes the fixture."""
import json
from pathlib import Path

from investigator.server import Workbench

FIXTURE = Path(__file__).parent / 'ui_fixtures' / 'why_slow.json'
REFRESH = ("python3 -c \"import json;from investigator.server import Workbench;"
           "r,_=Workbench().answer_url('/corr/1/day/2026-09-14/shift/2?measure=overview');r.pop('report_markdown',None);"
           "json.dump(r,open('tests/ui_fixtures/why_slow.json','w'),separators=(',',':'),sort_keys=True)\"")


def test_why_slow_ui_fixture_is_the_offline_answer_of_its_url():
    saved = json.loads(FIXTURE.read_text())
    golden = next(c for c in json.loads((Path(__file__).parents[1] / 'evals' / 'golden.json').read_text())['cases'] if c['id'] == 'why-slow')
    assert saved['url'] == golden['expect']['url']
    result, _ = Workbench().answer_url(saved['url'])
    fresh = json.loads(json.dumps(result))
    for key in ('sections', 'status', 'url', 'selected_shifts', 'production_day'):
        assert saved[key] == fresh[key], f'{key} drifted; refresh with: {REFRESH}'
    assert [e['tool'] for e in saved['trace']] == [e['tool'] for e in fresh['trace']], REFRESH
    assert [c.get('title') for c in saved['charts']] == [c.get('title') for c in fresh['charts']], REFRESH


MONEY = Path(__file__).parent / 'ui_fixtures' / 'money_week.json'


def test_money_week_ui_fixture_is_the_offline_answer_of_its_url():
    """Issue #31b's window card: the levers of the week of 14 September for shift 2, as the server answers them."""
    saved = json.loads(MONEY.read_text())
    result, _ = Workbench().answer_url(saved['url'])
    result.pop('report_markdown', None)
    fresh = json.loads(json.dumps(result))
    refresh = REFRESH.replace('why_slow', 'money_week').replace('/corr/1/day/2026-09-14/shift/2?measure=overview', saved['url'])
    for key in ('sections', 'url'):
        assert saved[key] == fresh[key], f'{key} drifted; refresh with: {refresh}'
    no_id = lambda charts: [{k: v for k, v in c.items() if k != 'source_result_id'} for c in charts]   # a run's own result id
    assert no_id(saved['charts']) == no_id(fresh['charts']), f'charts drifted; refresh with: {refresh}'
    assert [e['data'] for e in saved['trace'] if e['tool'] == 'get_opportunities'] == [{**e['data'], 'trace': saved['trace'][0]['data']['trace']} for e in fresh['trace'] if e['tool'] == 'get_opportunities'], refresh


LENSES = {'supervisor': Path(__file__).parent / 'ui_fixtures' / 'money_supervisor.json',
          'ownership': Path(__file__).parent / 'ui_fixtures' / 'money_ownership.json'}


def test_the_lens_ui_fixtures_are_the_offline_answers_of_their_urls():
    """Issue #101's two lenses on the month's levers, as the server answers them: the UI test changes a rate on each and
    holds the card's order to the lens (supervisor: schedule-driven levers after the ranked ones, unranked; ownership:
    side trim first)."""
    for lens, path in LENSES.items():
        saved = json.loads(path.read_text())
        assert saved['url'] == f'/corr/1/month/2026-09-14?measure=money&lens={lens}'
        result, _ = Workbench().answer_url(saved['url'])
        result.pop('report_markdown', None)
        fresh = json.loads(json.dumps(result))
        refresh = REFRESH.replace('why_slow', path.stem).replace('/corr/1/day/2026-09-14/shift/2?measure=overview', saved['url'])
        for key in ('sections', 'url'):
            assert saved[key] == fresh[key], f'{key} drifted; refresh with: {refresh}'
        lever = lambda r: [{k: v for k, v in e['data'].items() if k != 'trace'} for e in r['trace'] if e['tool'] == 'get_opportunities']
        assert lever(saved) == lever(fresh), refresh
        assert lever(saved)[0]['lens'] == lens


APART = Path(__file__).parent / 'ui_fixtures' / 'money_apart.json'


def test_money_apart_ui_fixture_is_the_offline_answer_of_its_url():
    """The owner's ruling on PR #111: Thursday's first shift, whose planned maintenance is on the money card apart from the
    ranked losses, with the all-in total beside the recorded losses'."""
    saved = json.loads(APART.read_text())
    assert saved['url'] == '/corr/1/day/2026-09-17/shift/1?measure=dollars'
    result, _ = Workbench().answer_url(saved['url'])
    result.pop('report_markdown', None)
    fresh = json.loads(json.dumps(result))
    refresh = REFRESH.replace('why_slow', APART.stem).replace('/corr/1/day/2026-09-14/shift/2?measure=overview', saved['url'])
    for key in ('sections', 'url'):
        assert saved[key] == fresh[key], f'{key} drifted; refresh with: {refresh}'
    money = lambda r: [{k: v for k, v in e['data'].items() if k != 'trace'} for e in r['trace'] if e['tool'] == 'get_shift_dollars']
    assert money(saved) == money(fresh), refresh
    assert money(saved)[0]['apart_gaps'] == 1


DAY = Path(__file__).parent / 'ui_fixtures' / 'money_day.json'


def test_money_day_ui_fixture_is_the_offline_answer_of_its_url():
    """Issue #31b's all-shifts day card on corrupt data: shift 2 is incomplete, so the day's money (get_day_dollars, the
    server's own total) counts shifts 1 and 3 and names shift 2 in excluded_shifts."""
    saved = json.loads(DAY.read_text())
    result, _ = Workbench().answer_url(saved['url'])
    result.pop('report_markdown', None)
    fresh = json.loads(json.dumps(result))
    refresh = REFRESH.replace('why_slow', 'money_day').replace('/corr/1/day/2026-09-14/shift/2?measure=overview', saved['url'])
    assert saved['url'] == '/corr/1/day/2026-09-14?measure=dollars&corrupt=1'
    for key in ('sections', 'url'):
        assert saved[key] == fresh[key], f'{key} drifted; refresh with: {refresh}'
    day = lambda r: [{k: v for k, v in e['data'].items() if k != 'trace'} for e in r['trace'] if e['tool'] == 'get_day_dollars']
    assert day(saved) == day(fresh), refresh
    assert day(saved)[0]['excluded_shifts'] == [2]
