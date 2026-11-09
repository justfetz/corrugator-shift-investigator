"""The /evals page (issue #26): the golden set as data, the badge, and runs that never reach a model."""
import http.client
import json
import re
import shutil
from pathlib import Path
from threading import Thread
import urllib.request

import pytest

from investigator.evalboard import EvalBoard, describe_call, describe_expect, evals_dir
from investigator.server import WEB, PublicDemo, make_server

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
VALUES = json.loads((ROOT / 'evals' / 'golden_values.json').read_text())['cases']
HISTORY = [json.loads(line) for line in (ROOT / 'evals' / 'history.jsonl').read_text().splitlines() if line.strip()]


def serve(public=None):
    server = make_server(0, public=public, host='127.0.0.1')
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(scope='module')
def running():
    server, thread = serve()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def request(server, method, path, body=None, headers=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def run(server, payload, **extra):
    headers = {'Content-Type': 'application/json', 'X-Workbench-Token': server.app.token, **extra}
    status, _, body = request(server, 'POST', '/api/evals/run', json.dumps(payload), headers)
    return status, json.loads(body)


def no_model(monkeypatch):
    """Any path to a provider fails the test: both adapters' transports and urllib itself."""
    def refuse(*args, **kwargs):
        raise AssertionError('the /evals page reached a model')
    monkeypatch.setattr('investigator.anthropic_provider.transport', refuse)
    monkeypatch.setattr('investigator.openai_provider.transport', refuse)
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)


def test_every_golden_case_appears_exactly_once_and_counts_match_the_last_history_line(running):
    status, headers, body = request(running, 'GET', '/evals.json')
    assert status == 200 and headers['Content-Type'].startswith('application/json')
    assert headers['Access-Control-Allow-Origin'] == '*'          # embeddable on the blog
    board = json.loads(body)
    ids = [case['id'] for case in board['cases']]
    assert sorted(ids) == sorted(c['id'] for c in GOLDEN + VALUES) and len(ids) == len(set(ids))
    assert board['counts']['cases'] == len(GOLDEN) + len(VALUES)
    newest = HISTORY[-1]
    runs = [r for case in board['cases'] for r in case['runs']]
    assert board['counts']['runs'] == len(runs) == newest['cases'] == board['last_run']['total']
    tallies = list(newest['offline'].values()) + [t for p in newest['live'].values() for t in p['types'].values()]
    passed = sum(t['pass'] for t in tallies)
    assert board['last_run']['passed'] == passed and board['last_run']['ran_at'] == newest['ran_at']
    assert board['badge'] == f'{passed}/{newest["cases"]} golden · {newest["latency_ms"]["p50"]:.0f} ms p50'
    assert [h['ran_at'] for h in board['history']] == [h['ran_at'] for h in HISTORY]   # the trend is the shipped file, line for line
    assert board['history'][-1]['p50_ms'] == newest['latency_ms']['p50']
    # every case says what it expects in words, and what was asked or called
    assert all(case['expected'] and (case['question'] or case['call']) for case in board['cases'])
    # the badge on the main page is the same string, from /api/config
    config = json.loads(request(running, 'GET', '/api/config')[2])
    assert config['evals']['badge'] == board['badge'] and config['evals']['total'] == newest['cases']


def test_last_run_file_is_the_newest_history_line_and_covers_every_run(running):
    recorded = json.loads((ROOT / 'evals' / 'last_run.json').read_text())
    assert (recorded['ran_at'], recorded['commit']) == (HISTORY[-1]['ran_at'], HISTORY[-1]['commit']), \
        'run python3 evals/run.py --track and commit history.jsonl and last_run.json together'
    board = running.app.evals.board()
    runs = [r for case in board['cases'] for r in case['runs']]
    assert sorted(r['id'] for r in runs) == sorted(row['id'] for row in recorded['rows'])
    assert all(r['last'] and r['last']['outcome'] in ('pass', 'fail', 'skip', 'xfail') for r in runs)
    assert sum(r['last']['outcome'] == 'pass' for r in runs) == board['last_run']['passed']


def test_expectations_read_in_plain_words():
    assert describe_call('get_downtime_breakdown', {'shift': 2, 'group_by': 'reason', 'kind': None}) == 'get_downtime_breakdown for shift 2 by reason'
    assert describe_call('get_period_kpis', {'window': {'period': 'month', 'anchor': '2026-09-14'}, 'compare': True, 'shift': None}) == \
        'get_period_kpis for all shifts over the month containing 2026-09-14 against the previous window'
    lines = describe_expect({'status': 'complete', 'tools': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}], 'charts': 2, 'raises': 'Ambiguous'})
    assert lines == ['Refused before any tool runs, with a reason matching /Ambiguous/', 'Ends with status "complete"',
                     'Calls get_shift_kpis for shift 2', 'Draws exactly 2 charts']
    # no expectation key of the real set is left unexplained
    known = len(describe_expect({}))
    for case in GOLDEN + VALUES:
        for block in (case.get('expect', {}), case.get('expect_live', {})):
            for key, value in block.items():
                assert len(describe_expect({key: value})) > known, f'{case["id"]}: expect.{key} has no plain-words line'


def test_page_and_scripts_are_served_and_reference_real_controls(running):
    for path in ('/evals', '/evals?case=why-slow', '/evals?type=refusal'):
        status, headers, body = request(running, 'GET', path)
        assert status == 200 and b'How the numbers are checked' in body and "script-src 'self'" in headers['Content-Security-Policy']
    assert request(running, 'GET', '/evalsx')[0] == 404
    for path in ('/evals.js', '/trail.js'):
        status, headers, body = request(running, 'GET', path)
        assert status == 200 and headers['Content-Type'].startswith('text/javascript') and headers['ETag']
    html, script = (WEB / 'evals.html').read_text(encoding='utf-8'), (WEB / 'evals.js').read_text(encoding='utf-8')
    assert set(re.findall(r"\$\('([^']+)'\)", script)) <= set(re.findall(r'id="([^"]+)"', html))
    assert 'style=' not in html and '<script>' not in html               # the CSP allows neither inline styles nor inline scripts
    index = (WEB / 'index.html').read_text(encoding='utf-8')
    assert index.index('/trail.js') < index.index('/app.js') and 'id="evals-badge"' in index and 'href="/evals"' in index


def test_run_one_offline_case_returns_the_tool_trail(running, monkeypatch):
    no_model(monkeypatch)
    status, result = run(running, {'id': 'why-slow'})
    assert status == 200 and result['outcome'] == 'pass' and result['failures'] == [] and result['model_called'] is False
    assert result['mode'] == 'offline' and result['provider'] is None and result['ms'] > 0
    trail = [(step['tool'], step['arguments'].get('group_by')) for step in result['turns'][0]['trace'] if step['ok']]
    assert ('get_downtime_breakdown', 'reason') in trail                 # the actual call the expectation names
    assert result['turns'][0]['trace'][0]['data']['trace']['sql']       # the same entries the main page draws, SQL included
    assert result['turns'][0]['usage']['model_calls'] == 0
    # a refusal has no trail, a multi-turn case has one result per turn
    status, refused = run(running, {'id': 'period-ambiguous-last-week'})
    assert status == 200 and refused['outcome'] == 'pass' and refused['turns'] == [] and 'Ambiguous window' in refused['note']
    status, story = run(running, {'id': 'story-trim-creep'})
    assert status == 200 and story['outcome'] == 'pass' and len(story['turns']) == 3


def test_live_cases_replay_the_recording_and_never_call_a_model(running, monkeypatch):
    no_model(monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-never-be-used')   # a server key changes nothing: the page replays
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-must-never-be-used')
    status, result = run(running, {'id': 'live-why-slow@anthropic'})
    assert status == 200 and result['outcome'] == 'pass' and result['mode'] == 'live' and result['model_called'] is False
    fixture = json.loads((ROOT / 'evals' / 'fixtures' / 'anthropic' / 'live-why-slow.json').read_text())
    recording = result['recording']
    assert recording['recorded_cost_usd'] == fixture['recorded_cost_usd'] and recording['model_responses'] == len(fixture['exchanges'])
    assert recording['models'] == ['claude-haiku-5-5'] and recording['stale'] is False
    assert recording['exchanges'][0]['calls'][0]['tool'] in {t for e in fixture['exchanges'] for t in e['tools']}
    assert 'signature' not in json.dumps(recording)                      # the model's choices, not its raw response blocks
    assert result['turns'][0]['trace'] and result['turns'][0]['usage']['model_calls'] == len(fixture['exchanges'])


def test_run_all_scores_the_whole_set_once_a_minute(running, monkeypatch):
    no_model(monkeypatch)
    status, result = run(running, {'all': True})
    assert status == 200 and result['total'] == HISTORY[-1]['cases'] == len(result['rows'])
    assert result['failed'] == 0 and result['passed'] + result['skipped'] == result['total'] and result['model_called'] is False
    assert result['cached'] is False and result['latency_ms']['p50'] is not None
    assert len({row['id'] for row in result['rows']}) == result['total']
    status, again = run(running, {'all': True})
    assert status == 200 and again['cached'] is True and again['ran_at'] == result['ran_at']
    assert running.app.evals.runner._DATASETS == {}                      # the run's day stores are closed, not kept


def test_run_endpoint_keeps_the_post_protections(running):
    body = json.dumps({'id': 'why-slow'})
    assert request(running, 'POST', '/api/evals/run', body, {'Content-Type': 'application/json'})[0] == 403              # no token
    assert request(running, 'POST', '/api/evals/run', body, {'Content-Type': 'application/json', 'X-Workbench-Token': running.app.token,
                                                              'Origin': 'https://evil.example'})[0] == 403
    for payload in ({'id': 'no-such-case'}, {'id': 7}, {'all': False}, {'id': 'why-slow', 'all': True}, {'id': 'why-slow', 'provider': 'anthropic'}, {}, []):
        status, result = run(running, payload)
        assert status == 400 and 'error' in result, payload
    assert request(running, 'GET', '/api/evals/run')[0] == 404          # running is a POST, never a link


def test_eval_runs_have_their_own_window_and_spend_no_question_rounds(tmp_path):
    from investigator.server import DemoGuards, EVAL_RUNS_PER_MIN
    server = make_server(0, guards=DemoGuards(rate_limit_per_min=2, ledger=tmp_path / 'ledger.sqlite3'))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(4):                                               # more than the per-IP question limit
            assert run(server, {'id': 'period-ambiguous-last-week'})[0] == 200
        assert len(server.app.requests) == 0 and len(server.app.eval_requests) == 4 and not server.app.rate_windows
        ask = json.dumps({'question': 'Show speed', 'day': '2026-09-14', 'shift': 2})
        headers = {'Content-Type': 'application/json', 'X-Workbench-Token': server.app.token}
        assert request(server, 'POST', '/api/ask', ask, headers)[0] == 200   # the question window is untouched
        server.app.eval_requests.extend([server.app.eval_requests[-1]] * (EVAL_RUNS_PER_MIN - 4))
        status, limited = run(server, {'id': 'period-ambiguous-last-week'})
        assert status == 429 and 'Golden-case run limit' in limited['error']
        assert request(server, 'POST', '/api/ask', ask, headers)[0] == 200   # and a full eval window does not block questions
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        server.app.close()


def test_public_demo_serves_the_board_and_runs_only_from_its_own_origin(monkeypatch):
    no_model(monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-never-be-used')
    public = PublicDemo(origins=('https://demo.example',), embed_origins=(), live_model=True)
    server, thread = serve(public)
    try:
        assert request(server, 'GET', '/evals.json', headers={'Host': 'demo.example'})[0] == 200
        assert run(server, {'id': 'why-slow'})[0] == 403                 # no Origin
        status, result = run(server, {'id': 'injection-user-text@anthropic'}, Origin='https://demo.example')
        assert status == 200 and result['outcome'] == 'pass' and result['model_called'] is False
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        server.app.close()


def test_the_image_layout_and_a_build_without_the_set(tmp_path, monkeypatch):
    # The image copies evals/ to /app/evals and names it in INVESTIGATOR_EVALS: the board loads from there alone.
    shipped = tmp_path / 'app' / 'evals'
    shutil.copytree(ROOT / 'evals', shipped, ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setenv('INVESTIGATOR_EVALS', str(shipped))
    assert evals_dir() == shipped
    board = EvalBoard(shipped, json.loads((shipped / 'golden_values.json').read_text())['content_hash'])
    assert board.board()['counts']['runs'] == HISTORY[-1]['cases'] and board.runner.GOLDEN == shipped / 'golden.json'
    assert not (shipped / '__pycache__').exists()                       # loading the runner writes nothing into evals/
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert 'COPY evals ./evals' in dockerfile and 'INVESTIGATOR_EVALS=/app/evals' in dockerfile
    # Without the folder (a wheel installed on its own) the page, the data and the badge are simply absent.
    monkeypatch.setenv('INVESTIGATOR_EVALS', str(tmp_path / 'missing'))
    assert evals_dir() is None
    server, thread = serve()
    try:
        assert request(server, 'GET', '/evals.json')[0] == 404 and request(server, 'GET', '/evals')[0] == 404
        assert json.loads(request(server, 'GET', '/api/config')[2])['evals'] is None
        assert run(server, {'id': 'why-slow'})[0] == 404
        assert request(server, 'GET', '/')[0] == 200
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        server.app.close()
