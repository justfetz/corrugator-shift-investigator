"""Issue #31b: the money on the card. The server side of it: what /api/config tells the client, the rate-free
terms every priced row carries, that the models never see them, and that the client formula (web/money.js) gives
the server's own figures at any rates, prose included. The browser side is in tests/test_ui.cjs."""
import http.client
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from threading import Thread

import pytest

from investigator import cost_model
from investigator.agent import Agent
from investigator.narrative import evidence_packet
from investigator.openai_provider import compact_result
from investigator.record_store import default_store
from investigator.server import Workbench, make_server

ROOT = Path(__file__).resolve().parent.parent
DAY = '2026-09-14'
NODE = shutil.which('node')


@pytest.fixture(scope='module')
def bench():
    return Workbench()


@pytest.fixture(scope='module')
def running():
    server = make_server(0, host='127.0.0.1')
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def get(server, path):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
    try:
        client.request('GET', path)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def test_config_gives_the_client_the_rates_and_constants_and_serves_the_formula(running):
    status, _, body = get(running, '/api/config')
    model = json.loads(body)['cost_model']
    assert status == 200 and [r['short'] for r in model['rates']] == ['paper', 'hour', 'margin', 'trim']
    by = {r['id']: r for r in model['rates']}
    assert by['paper_usd_msf']['value'] == 55 and by['paper_usd_msf']['source'] == 'post' and by['machine_usd_hour']['value'] == 1200
    assert by['margin_usd_msf']['value'] is None and by['trim_target_pct']['max'] == 100   # margin not set, never $0
    assert model['constants']['msf_per_minute'] == pytest.approx(5.4) and model['constants']['annual_msf_per_machine'] == 1_800_000
    assert model['margin_not_set'] == 'margin not set' and "not a real plant's" in model['note']
    status, headers, script = get(running, '/money.js')
    assert status == 200 and headers['Content-Type'].startswith('text/javascript') and b'const Money=' in script
    index = (ROOT / 'src' / 'investigator' / 'web' / 'index.html').read_text()
    assert index.index('/trail.js') < index.index('/money.js') < index.index('/app.js')
    # No rate or assumption is written into the client formula's code (comments and messages aside): it reads them from /api/config.
    code = re.sub(r"'[^'\n]*'|`[^`]*`", "''", re.sub(r'//.*', '', (ROOT / 'src' / 'investigator' / 'web' / 'money.js').read_text()))
    for number in (r'55', r'1200', r'675', r'5\.4', r'1800000', r'3\.25', r'480', r'1440'):
        assert not re.search(rf'(?<![\w.]){number}(?![\w.])', code), number


def test_config_carries_the_servers_own_rates(monkeypatch):
    monkeypatch.setenv('INVESTIGATOR_RATES', 'paper:61,margin:30')
    by = {r['id']: r for r in cost_model.client_config()['rates']}
    assert (by['paper_usd_msf']['value'], by['paper_usd_msf']['source'], by['paper_usd_msf']['default']) == (61, 'server', 55)
    assert (by['margin_usd_msf']['value'], by['margin_usd_msf']['source']) == (30, 'server')


def test_every_priced_row_carries_the_call_that_priced_it(bench):
    """price_terms is the pricing call, rate-free and unrounded: making it again gives the row, at any rates."""
    rates = cost_model.effective({'paper_usd_msf': 61.5, 'margin_usd_msf': 12})
    for url in ('/corr/1/day/2026-09-14/shift/2?measure=dollars', '/corr/1/week/2026-09-14/shift/2?measure=money',
                '/corr/1/day/2026-09-14/shift/2?measure=overview', '/corr/1/month/2026-09-14?measure=waste',
                '/corr/1/week/2026-09-14/shift/2?measure=opportunity', '/corr/1/day/2026-09-14/shift/2?measure=quality'):
        result, _ = bench.answer_url(url + '&rates=paper:61.5,margin:12')
        priced = [e for e in result['trace'] if e['ok'] and e['tool'] in cost_model.PRICED_TOOLS + ('get_shift_dollars', 'get_opportunities')]
        assert priced, url
        for entry in priced:
            for row in entry['data']['rows']:
                t = row['price_terms']
                if t is None:
                    assert row['usd'] is None
                    continue
                if 'trim_pct' in t:   # the trim lever: its quantity is the points over the target, itself a rate
                    over = max(0.0, t['trim_pct'] - rates['values']['trim_target_pct'])
                    assert t['quantity'] == pytest.approx(over / 100 * t['gross_sqft'])
                again = cost_model.price(t['kind'], t['quantity'], rates, t['gross_sqft'], t['minutes'])
                assert {k: v for k, v in again.items() if k != 'price_terms'} == {k: row[k] for k in again if k != 'price_terms'}, (url, row)


def test_the_models_never_see_the_terms(bench):
    result, _ = bench.answer_url('/corr/1/day/2026-09-14/shift/2?measure=dollars')
    step = result['trace'][0]
    assert 'price_terms' in step['data']['rows'][0]
    assert 'price_terms' not in evidence_packet([step])[0]['rows'][0]
    assert 'price_terms' not in compact_result(step)['data']['rows'][0]


def test_the_client_money_golden_holds_the_tool_as_it_runs_now():
    """evals/client_money.json's input is get_shift_dollars at the post's rates: regenerate it
    (python3 evals/make_client_money.py) after an intended change to the cost model."""
    doc = json.loads((ROOT / 'evals' / 'client_money.json').read_text())
    os.environ.pop('INVESTIGATOR_RATES', None)
    data = Agent(default_store().day_analysis(doc['day']))._execute('get_shift_dollars', {'shift': doc['shift']}, {}, cost_model.effective())
    data = {k: v for k, v in data.items() if k != 'trace'}
    data['rows'] = [{k: v for k, v in r.items() if k != 'source_ids'} for r in data['rows']]
    assert json.loads(json.dumps(data)) == doc['input'], 'run python3 evals/make_client_money.py, then evals/make_value_goldens.py'
    assert doc['cfg'] == cost_model.client_config()


PARITY_URLS = ('/corr/1/day/2026-09-14/shift/2?measure=dollars', '/corr/1/day/2026-08-03/shift/3?measure=dollars',
               '/corr/1/day/2026-09-14?measure=dollars', '/corr/1/day/2026-09-14?measure=dollars&corrupt=1',
               '/corr/1/week/2026-09-14/shift/2?measure=money', '/corr/1/month/2026-09-14?measure=money',
               '/corr/1/week/2026-08-03?measure=money', '/corr/1/day/2026-09-14/shift/2?measure=overview',
               '/corr/1/week/2026-09-14/shift/2?measure=pareto', '/corr/1/month/2026-09-14?measure=waste',
               '/corr/1/day/2026-09-14/shift/2?measure=quality', '/corr/1/week/2026-09-14/shift/2?measure=opportunity',
               '/corr/1/week/2026-09-14/shift/2?measure=speedvar', '/corr/1/month/2026-09-14?measure=speedvar',
               '/corr/1/week/2026-09-14/shift/2?measure=overview', '/corr/1/month/2026-09-14/shift/1?measure=kpis',
               '/corr/1/day/2026-09-14/shift/3?measure=summary', '/corr/1/day/2026-09-14?measure=dollars&shifts=1,3',
               '/corr/1/month/2026-09-14?measure=kpis',
               # issue #101: the two lenses (supervisor: schedule-driven levers unranked; ownership: all of trim first) and the
               # speed variance whose grade mix is schedule-driven and unpriced
               '/corr/1/month/2026-09-14?measure=money&lens=supervisor', '/corr/1/month/2026-09-14?measure=money&lens=ownership',
               '/corr/1/week/2026-09-14/shift/2?measure=money&lens=supervisor', '/corr/1/week/2026-09-14/shift/2?measure=money&lens=ownership',
               '/corr/1/day/2026-01-26?measure=speedvar&window=week&against=shift&shifts=3,1')
PARITY_RATES = ('paper:60,hour:1350.5,margin:35,trim:3.1', 'paper:41.37,hour:5000', 'margin:12.5,trim:4', 'trim:2.5',
                'hour:1', 'paper:99999.99,margin:0.01')


@pytest.mark.skipif(NODE is None, reason='node is not installed')
def test_the_client_formula_gives_the_servers_answer_at_any_rates(bench, tmp_path):
    """Re-pricing in the browser equals asking the server at those rates: every priced row, total and provenance,
    and every line of the prose (the ranked lists in their new order, the shift that lost the most, the formula
    and rates lines). This is what makes the permalink reproduce the page after a rate edit."""
    def at(url, rates):   # rates= sits before corrupt= in the canonical order (docs/URLS.md)
        return url.replace('&corrupt=1', f'&rates={rates}&corrupt=1') if '&corrupt=1' in url else f'{url}&rates={rates}'
    cases = []
    for url in PARITY_URLS:
        before, _ = bench.answer_url(url)
        cases += [{'url': url, 'rates': rates, 'before': before, 'after': bench.answer_url(at(url, rates))[0]} for rates in PARITY_RATES]
    file = tmp_path / 'cases.json'
    file.write_text(json.dumps({'cfg': cost_model.client_config(), 'cases': cases}))
    run = subprocess.run([NODE, str(ROOT / 'tests' / 'money_cli.cjs'), 'parity', str(file)], capture_output=True, text=True)
    summary = json.loads(run.stdout or '{}')
    assert run.returncode == 0 and summary['count'] == 0, summary.get('mismatches') or run.stderr
    assert summary['cases'] == len(PARITY_URLS) * len(PARITY_RATES) and summary['fields'] > 10_000


def test_a_report_address_keeps_the_rates_its_card_is_priced_at(bench):
    """The report has no dollars of its own, but its card carries its window's money (#31b): the link keeps rates=."""
    target = bench.canonical_redirect('/corr/1/week/2026-09-14/shift/2?measure=report&rates=hour:1500&corrupt=1')
    assert target is None, target   # canonical as written: rates= kept, before corrupt=
    result, _ = bench.answer_url('/corr/1/week/2026-09-14/shift/2?measure=report&rates=hour:1500')
    assert result['url'] == '/corr/1/week/2026-09-14/shift/2?measure=report&rates=hour:1500'
    assert bench.canonical_redirect('/corr/1/week/2026-09-14/shift/2?measure=report&corrupt=1&rates=hour:1500.00') == \
        '/corr/1/week/2026-09-14/shift/2?measure=report&rates=hour:1500&corrupt=1'


def test_the_day_total_is_the_offline_planners_and_stays_in_scope():
    """Re-review 2 of PR 109: get_day_dollars reads every shift it lists, so it is off the analyst's plan allowlist and
    the scope check (shared by the tool loop and the plan check) refuses it for a shift outside the selected scope."""
    from investigator.agent import PLAN_TOOLS, scope_violation
    assert 'get_day_dollars' not in PLAN_TOOLS and 'get_shift_dollars' in PLAN_TOOLS
    assert scope_violation('get_day_dollars', {}, [2], 'shift', 'day', DAY) == 'Tool shift is outside the selected scope'
    assert scope_violation('get_day_dollars', {'shifts': [1, 3]}, [2], 'shift', 'day', DAY) == 'Tool shift is outside the selected scope'
    assert scope_violation('get_day_dollars', {'shifts': [1, 3]}, [1, 2, 3], 'day', 'day', DAY) is None
