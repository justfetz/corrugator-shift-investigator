"""The question log (issue #125): one JSON line per question event on stdout, with no header, IP or key value in it.

A public server with the log on writes to a StringIO here instead of stdout. A line is written after its response,
so each check waits for the lines it expects.
"""
import http.client
import io
import json
from pathlib import Path
import random
import secrets
import string
import threading
import time
from threading import Thread
from urllib.parse import urlencode

import pytest

from investigator.questionlog import QuestionLog, check_comment, flags_of, redact, valid_id
from investigator.server import DemoGuards, PublicDemo, Workbench, make_server

ORIGIN = 'https://demo.example'
LINE, SESSION = '0123456789abcdef', 'fedcba9876543210'
QUESTION_KEYS = {'severity', 'message', 'log', 'v', 'event', 'ts', 'line', 'session', 'question', 'truncated', 'scope',
                 'follow_ups', 'url', 'measure', 'status', 'refusal', 'server_ms', 'flags', 'version', 'revision', 'dataset'}
ANSWER_KEYS = {'severity', 'message', 'log', 'v', 'event', 'ts', 'line', 'session', 'url', 'measure', 'status', 'refusal',
               'server_ms', 'version', 'revision', 'dataset'}
ANALYST_KEYS = {'severity', 'message', 'log', 'v', 'event', 'ts', 'line', 'session', 'question', 'truncated', 'scope', 'mode',
                'status', 'narrative', 'model_calls', 'tool_calls', 'refusal', 'server_ms', 'flags', 'version', 'revision', 'dataset'}
FEEDBACK_KEYS = {'severity', 'message', 'log', 'v', 'event', 'ts', 'line', 'session', 'card', 'vote', 'comment', 'version',
                 'revision', 'dataset'}


def start(public=True, guards=None):
    demo = PublicDemo(origins=(ORIGIN,), embed_origins=(), live_model=False) if public else None
    server = make_server(0, public=demo, host='127.0.0.1', guards=guards)
    server.app.qlog.stream = io.StringIO()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(server, thread):
    server.shutdown(); thread.join(); server.server_close(); server.app.close()


@pytest.fixture
def public():
    server, thread = start()
    yield server
    stop(server, thread)


def request(server, method, path, body=None, headers=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    try:
        client.putrequest(method, path, skip_accept_encoding=True)
        for name, value in (headers or {}).items():
            client.putheader(name, value)
        if body is not None:
            client.putheader('Content-Length', str(len(body)))
        client.endheaders(body.encode() if isinstance(body, str) else body)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def lines(server, count, timeout=5):
    """The log lines once there are `count` of them (each is written after its response)."""
    deadline = time.monotonic() + timeout
    while True:
        out = [json.loads(line) for line in server.app.qlog.stream.getvalue().splitlines()]
        if len(out) >= count or time.monotonic() > deadline:
            assert len(out) == count, out
            return out
        time.sleep(0.01)


def ask(server, question, headers, **controls):
    """The page's ask: GET /q with the ids, then the redirect target with the same headers (fetch keeps them)."""
    query = urlencode({'day': '2026-09-14', 'shift': '2', 'scope': 'shift', 'period': 'day', **controls, 'q': question})
    status, response_headers, body = request(server, 'GET', '/q?' + query, headers=headers)
    if status != 302:
        return status, json.loads(body)
    status, _, body = request(server, 'GET', response_headers['Location'], headers={**headers, 'Accept': 'application/json'})
    return status, json.loads(body)


def ids(line=LINE, session=SESSION):
    return {'X-Question-Line': line, 'X-Question-Session': session}


def test_a_question_and_its_answer_are_two_lines_with_these_fields(public):
    status, result = ask(public, 'how many setups did we run last week', ids())
    assert status == 200 and result['url'] == '/corr/1/week/2026-09-07/shift/2?measure=kpis'
    question, answer = lines(public, 2)
    assert set(question) == QUESTION_KEYS and set(answer) == ANSWER_KEYS
    assert question['log'] == answer['log'] == 'question_log' and question['v'] == 1
    assert (question['event'], answer['event']) == ('question', 'answer')
    assert question['line'] == answer['line'] == LINE and question['session'] == answer['session'] == SESSION
    assert question['question'] == 'how many setups did we run last week' and question['truncated'] is False
    assert question['scope'] == {'day': '2026-09-14', 'shift': 2, 'scope': 'shift', 'period': 'day', 'corrupt': False}
    assert question['url'] == answer['url'] == '/corr/1/week/2026-09-07/shift/2?measure=kpis'
    assert question['measure'] == answer['measure'] == 'kpis'
    assert question['status'] == 'routed' and answer['status'] == 'complete' and question['refusal'] is None
    assert question['flags'] == ['catch_all', 'scope_moved']   # the #113 example, asked with a day on the rail
    assert isinstance(question['server_ms'], float) and isinstance(answer['server_ms'], float)
    assert question['dataset'] == public.app.store.content_hash[:12] and question['version'] == '0.1.0'
    assert question['ts'].endswith('Z') and question['severity'] == 'INFO'


def test_a_refused_question_is_one_line_and_the_after_chain_is_not_kept(public):
    status, body = ask(public, "what's the weather", ids())
    assert status == 400
    (line,) = lines(public, 1)
    assert line['status'] == 'refused' and line['flags'] == ['refused'] and line['url'] is None
    assert line['refusal'].startswith('Offline mode supports shift performance')
    prior = '/corr/1/day/2026-09-14/shift/2?measure=overview'
    status, result = ask(public, 'downtiem on this shift', ids(line='00000000000000aa'), after=prior)
    assert status == 200
    question, answer = lines(public, 3)[1:]
    assert question['follow_ups'] == 1 and 'after' not in question['url'] and 'after' not in answer['url']
    assert question['flags'] == ['typo_corrected'] and question['measure'] == 'downtime'


def test_the_visitors_rates_are_not_kept(public):
    # rates= carries the visitor's own cost figures (Codex on PR 131): the logged address keeps the routing, not them
    status, result = ask(public, 'Where is the money?', ids(), rates='paper:61.25,margin:37.5')
    assert status == 200 and 'rates=paper:61.25' in result['url']
    question, answer = lines(public, 2)
    assert question['url'] == answer['url'] == '/corr/1/day/2026-09-14/shift/2?measure=dollars'
    text = public.app.qlog.stream.getvalue()
    assert '61.25' not in text and '37.5' not in text and 'rates' not in text


def test_no_id_no_line_and_the_loopback_workbench_logs_nothing():
    server, thread = start(public=False)
    try:
        assert server.app.qlog.enabled is False
        assert json.loads(request(server, 'GET', '/api/config')[2])['question_log'] is False
        assert ask(server, 'Why was this shift slow?', ids())[0] == 200
    finally:
        stop(server, thread)
    assert server.app.qlog.stream.getvalue() == ''
    server, thread = start()
    try:
        assert json.loads(request(server, 'GET', '/api/config')[2])['question_log'] is True
        assert ask(server, 'Why was this shift slow?', {})[0] == 200                 # a pasted /q link, curl, a reload replay
        assert ask(server, 'Why was this shift slow?', ids(line='0123456789ABCDEF'))[0] == 200   # not an id the page draws
        time.sleep(0.2)
        assert server.app.qlog.stream.getvalue() == ''
    finally:
        stop(server, thread)


def test_the_switch():
    assert QuestionLog.from_env(None, environ={}).enabled is False
    assert QuestionLog.from_env(object(), environ={}).enabled is True
    assert QuestionLog.from_env(None, environ={'QUESTION_LOG': '1'}).enabled is True
    assert QuestionLog.from_env(object(), environ={'QUESTION_LOG': '0'}).enabled is False
    assert QuestionLog.from_env(None, environ={'K_REVISION': 'svc-00042-abc'}).revision == 'svc-00042-abc'
    with pytest.raises(ValueError):
        QuestionLog.from_env(None, environ={'QUESTION_LOG': 'yes'})


# ---- the fuzz: no header value, IP or key can appear in a line ----------------------------------------------

SENSITIVE = ('User-Agent', 'Cookie', 'X-Forwarded-For', 'X-Real-IP', 'Forwarded', 'Authorization', 'X-Anthropic-Key',
             'X-OpenAI-Key', 'X-Workbench-Token', 'Referer', 'Origin', 'Accept-Language', 'X-Cloud-Trace-Context',
             'X-Client-Data', 'Via', 'True-Client-IP', 'CF-Connecting-IP')


def random_value(rng):
    """A header value the log could leak: a key, an IP, a hex id of the page's own shape, an email, or noise."""
    kind = rng.randrange(6)
    if kind == 0:
        return 'sk-ant-api03-' + ''.join(rng.choices(string.ascii_letters + string.digits + '-_', k=40))
    if kind == 1:
        return f'198.51.{rng.randrange(256)}.{rng.randrange(256)}'
    if kind == 2:
        return ''.join(rng.choices('0123456789abcdef', k=16))         # shaped like an id, in a header that is not one
    if kind == 3:
        return f'visitor{rng.randrange(10**6)}@example.org'
    if kind == 4:
        return 'Mozilla/5.0 (' + ''.join(rng.choices(string.ascii_letters, k=24)) + ')'
    return ''.join(rng.choices(string.ascii_letters + string.digits + ' ;=,./:+', k=rng.randrange(20, 60))).strip() or 'x' * 20


def test_fuzzed_headers_never_reach_a_line():
    rng = random.Random(125)
    server, thread = start(guards=DemoGuards(trust_proxy=True, rate_limit_per_min=10000))
    token = json.loads(request(server, 'GET', '/api/config')[2])['token']
    sent, expected = [], 0
    try:
        for i in range(30):
            headers = {name: random_value(rng) for name in rng.sample(SENSITIVE, rng.randrange(6, len(SENSITIVE)))}
            headers.update({f'X-Fuzz-{rng.randrange(1000)}': random_value(rng) for _ in range(rng.randrange(1, 4))})
            line = ''.join(rng.choices('0123456789abcdef', k=16))
            # the two id headers themselves: a well-formed line id, and a session that is sometimes malformed
            session = random_value(rng) if i % 3 == 0 else ''.join(rng.choices('0123456789abcdef', k=16))
            headers.update(ids(line=line, session=session))
            # every value but the two well-formed ids, which are the line's own
            sent.extend(v for k, v in headers.items() if not (k in ('X-Question-Line', 'X-Question-Session') and valid_id(v)))
            question = rng.choice(['Why was this shift slow?', 'how many setups did we run last week', 'hello there'])
            status, _ = ask(server, question, {**headers, 'Origin': ORIGIN, 'X-Workbench-Token': token} if i % 2 else headers)
            expected += 2 if status == 200 else 1
            if i % 4 == 0:   # a vote with the same headers (the POST guard's own Origin and token, so it is accepted)
                body = json.dumps({'line': line, 'session': None, 'vote': rng.choice(['up', 'down'])})
                code = request(server, 'POST', '/api/feedback', body, {**headers, 'Origin': ORIGIN, 'X-Workbench-Token': token,
                                                                         'Content-Type': 'application/json'})[0]
                assert code == 200
                expected += 1
        logged = lines(server, expected)
    finally:
        stop(server, thread)
    text = server.app.qlog.stream.getvalue()
    leaked = [value for value in sent if value and value in text]
    assert leaked == [], leaked
    for value in ('127.0.0.1', token, ORIGIN):
        assert value not in text
    assert all(entry['session'] is None or len(entry['session']) == 16 for entry in logged)
    assert {entry['event'] for entry in logged} == {'question', 'answer', 'feedback'}


def test_a_key_email_phone_or_ip_typed_into_a_question_is_replaced():
    typed = 'why slow sk-ant-api03-AbC123def456GhI789jkl012MnO me@example.com 555-123-4567 10.1.2.3 on 2026-09-14 shift 2'
    out = redact(typed, 500)
    for secret in ('sk-ant', 'me@example.com', '555-123-4567', '10.1.2.3'):
        assert secret not in out
    assert out == 'why slow [key] [email] [number] [ip] on 2026-09-14 shift 2'
    # IPv6 too (Codex on PR 131): compressed, full, with a zone, IPv4-mapped; a clock time or a MAC address is left alone
    for address in ('2001:db8:85a3::8a2e:370:7334', 'fe80::1', 'fe80::1%eth0', '::ffff:10.1.2.3', '::1',
                    '2001:0db8:85a3:0000:0000:8a2e:0370:7334'):
        assert redact(f'from {address} why slow', 500) == 'from [ip] why slow', address
    assert redact('downtime 07:00:00 to 15:00 on 00:1a:2b:3c:4d:5e', 500) == 'downtime 07:00:00 to 15:00 on 00:1a:2b:3c:4d:5e'
    assert redact('Note:: a, b', 500) == 'Note:: a, b'
    # the coordinator's review of PR 131: a bracketed address with a port, an IPv4 address before a full stop
    assert redact('at [2001:db8::1]:443 ok', 500) == 'at [[ip]]:443 ok'
    assert redact('my box is 10.1.2.3.', 500) == 'my box is [ip].'
    # personal numbers: nine or more digits with separators, a 3-4 local number; dates, times and figures are kept
    for number in ('020 7946 0958', '030 12345678', '123-4567', '123-45-6789', '4111 1111 1111 1111', '4111-1111-1111-1111',
                   '(555) 123-4567', '+44 20 7946 0958', '5551234567'):
        assert redact(f'call {number} now', 500) == 'call [number] now', number
    for kept in ('what happened 2026-09-14 15:00 on shift 2', 'downtime on 2026-09-14 and 2026-09-15', '13,233.4 MSF and 1,529,065 ft',
                 'why was 600.0 ft/min 75%', 'see /corr/1/week/2026-09-07/shift/2?measure=kpis'):
        assert redact(kept, 500) == kept, kept
    # key shapes with / and + (an AWS secret key), mixed case
    assert redact('aws wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY and Ab+9/xyzXYZ012345678901', 500) == 'aws [key] and [key]'
    # invisible format characters (category Cf) are removed: bidi overrides, zero-width, BOM, the tag characters
    assert redact('rev\u202eesrever zero\u200bwidth \u2066iso\u2069 rlm\u200f bom\ufeff tag\U000e0041\U000e0049', 500) == 'revesrever zerowidth iso rlm bom tag'
    assert redact('how-many-setups-did-we-run-last-week', 500) == 'how-many-setups-did-we-run-last-week'
    assert len(redact('x' * 2000, 500)) == 500
    # replaced before the cut: nothing straddling the 500th character survives as a fragment
    assert redact('x' * 495 + ' me@example.com', 500) == 'x' * 495 + ' [ema'
    assert 'sk-ant' not in redact('y ' * 243 + 'sk-ant-api03-AbC123def456GhI789', 500)


# ---- flags ------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def app():
    bench = Workbench()
    bench.qlog = QuestionLog(True, stream=io.StringIO(), dataset='test')
    yield bench
    bench.close()


def logged(app, question, period='day'):
    seen = {}
    query = urlencode({'day': '2026-09-14', 'shift': '2', 'scope': 'shift', 'period': period, 'q': question})
    try:
        url = app.question_url(query, seen)
        return app.qlog.question(LINE, None, seen, url=url, ms=1.0)
    except ValueError as exc:
        return app.qlog.question(LINE, None, seen, refusal=str(exc), ms=1.0)


@pytest.mark.parametrize('question,period', [
    ('how many setups did we run last week', 'day'), ('how many setups did we run last week', 'week'),
    ('how many paper changes last week', 'day'), ('how many order changes this week', 'day'),
    ('what was our lineal per setup last week', 'day'), ('how many orders this week', 'week')])
def test_the_113_examples_are_catch_all(app, question, period):
    line = logged(app, question, period)
    assert line['measure'] == 'kpis' and 'catch_all' in line['flags'], line


@pytest.mark.parametrize('question,period', [
    ('How did this week go versus the previous one?', 'week'), ('How did September go versus August?', 'day'),
    ('how many setups yesterday', 'day'), ('Why was this shift slow?', 'day'),
    ('How did shift 1 go compared with last week on lineal per paper change?', 'day')])
def test_a_kpi_question_or_a_count_that_lands_right_is_not(app, question, period):
    assert 'catch_all' not in logged(app, question, period)['flags']


def test_no_golden_answered_right_on_kpis_is_flagged_catch_all():
    # every golden whose expected address is measure=kpis is a right answer there (the coordinator's review of PR 131)
    cases = json.loads((Path(__file__).resolve().parents[1] / 'evals' / 'golden.json').read_text())['cases']
    on_kpis = [c for c in cases if '?measure=kpis' in ((c.get('expect') or {}).get('url') or '') and isinstance(c.get('question'), str)]
    assert len(on_kpis) >= 10
    assert [c['id'] for c in on_kpis if 'catch_all' in flags_of(c['question'], measure='kpis')] == []


def test_refused_typo_and_moved_scope(app):
    assert logged(app, 'hello')['flags'] == ['refused']
    assert logged(app, 'downtiem on this shift')['flags'] == ['typo_corrected']
    assert logged(app, 'how much downtime on 1st shift last week')['flags'] == ['scope_moved']
    assert flags_of('Why slow?', status='plan_rejected') == ['analyst_plan_rejected']


# ---- the analyst event --------------------------------------------------------------------------------------

def stub_model(monkeypatch, text):
    def stub(payload, key, timeout):
        return 200, iter([{'type': 'message_start', 'message': {'usage': {'input_tokens': 1, 'output_tokens': 0}}},
                          {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text}},
                          {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 1}}])
    monkeypatch.setattr('investigator.anthropic_provider.stream_transport', stub)


def test_an_analyst_round_is_a_line_with_its_plan_status(monkeypatch, tmp_path):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-server-key-for-tests-only-0001')
    stub_model(monkeypatch, '{"steps": [{"tool": "drop_tables", "arguments": {}}]}')
    server, thread = start(public=False, guards=DemoGuards(ledger=tmp_path / 'ledger.sqlite3'))
    server.app.qlog.enabled = True
    try:
        token = json.loads(request(server, 'GET', '/api/config')[2])['token']
        headers = {'Content-Type': 'application/json', 'X-Workbench-Token': token, **ids(),
                   'X-Anthropic-Key': 'sk-ant-visitor-key-must-not-be-logged-0002'}
        body = json.dumps({'day': '2026-09-14', 'shift': 2, 'question': 'Why was this shift slow?', 'mode': 'anthropic'})
        status, _, stream = request(server, 'POST', '/api/ask', body, headers)
        assert status == 200 and b'"type": "done"' in stream
        refused = json.dumps({'day': '2026-09-13', 'shift': 2, 'question': 'Why was this shift slow?', 'mode': 'anthropic'})
        assert request(server, 'POST', '/api/ask', refused, {**headers, **ids(line='00000000000000bb')})[0] == 400
        round_, refusal = lines(server, 2)
    finally:
        stop(server, thread)
    assert set(round_) == ANALYST_KEYS and round_['event'] == 'analyst' and round_['line'] == LINE
    assert round_['mode'] == 'anthropic' and round_['status'] == 'plan_rejected' and round_['flags'] == ['analyst_plan_rejected']
    assert round_['model_calls'] == 1 and round_['question'] == 'Why was this shift slow?'
    assert round_['scope'] == {'day': '2026-09-14', 'shift': 2, 'scope': 'shift', 'period': 'day', 'corrupt': False}
    assert refusal['status'] == 'refused' and 'Sunday' in refusal['refusal'] and refusal['scope']['day'] == '2026-09-13'
    assert 'sk-ant' not in server.app.qlog.stream.getvalue()


def test_analyst_clicks_refused_by_a_window_are_lines_and_fuzzed_headers_never_reach_one(monkeypatch, tmp_path):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-server-key-for-tests-only-0001')
    stub_model(monkeypatch, '{"unavailable": "test"}')
    rng = random.Random(6)
    server, thread = start(public=False, guards=DemoGuards(rate_limit_per_min=6, ledger=tmp_path / 'ledger.sqlite3'))
    server.app.qlog.enabled = True
    sent = []
    try:
        token = json.loads(request(server, 'GET', '/api/config')[2])['token']
        body = json.dumps({'day': '2026-09-14', 'shift': 2, 'question': 'Why was this shift slow?', 'mode': 'anthropic'})
        for i in range(7):   # six rounds in the per-IP window, then a refusal before the round begins
            headers = {name: random_value(rng) for name in rng.sample([n for n in SENSITIVE if n not in ('Origin', 'X-Workbench-Token')], 8)}
            sent.extend(headers.values())
            status = request(server, 'POST', '/api/ask', body, {**headers, 'Content-Type': 'application/json', 'X-Workbench-Token': token,
                                                               **ids(line=f'{i:016x}')})[0]
            assert status == (200 if i < 6 else 429)
        logged = lines(server, 7)
    finally:
        stop(server, thread)
    # a fuzzed X-Anthropic-Key that is not shaped like a key is the loopback key field's own refusal, named without the value
    assert all(entry['status'] == 'unavailable' or entry['refusal'].startswith('Enter a valid Anthropic API key') for entry in logged[:6])
    assert logged[-1]['status'] == 'limited'
    assert logged[-1]['refusal'] == 'too many questions, slow down' and logged[-1]['mode'] == 'anthropic' and logged[-1]['line'] == f'{6:016x}'
    text = server.app.qlog.stream.getvalue()
    assert [value for value in sent if value in text] == [] and token not in text and '127.0.0.1' not in text


# ---- feedback -----------------------------------------------------------------------------------------------

def feedback(server, payload, token, ip=None, origin=ORIGIN):
    headers = {'Content-Type': 'application/json', 'X-Workbench-Token': token, 'Origin': origin}
    if ip:
        headers['X-Forwarded-For'] = ip
    status, response_headers, body = request(server, 'POST', '/api/feedback', json.dumps(payload), headers)
    return status, response_headers, json.loads(body)


def test_feedback_is_a_line_and_accepts_no_markup(public):
    token = json.loads(request(public, 'GET', '/api/config')[2])['token']
    assert feedback(public, {'line': LINE, 'session': SESSION, 'vote': 'down'}, token)[0] == 200
    comment = {'line': LINE, 'session': SESSION, 'vote': 'down', 'comment': '  the number of setups,   not the MSF '}
    assert feedback(public, comment, token)[0] == 200
    assert feedback(public, {'line': LINE, 'card': 'analyst', 'vote': 'up'}, token)[0] == 200   # the analyst's card, same line
    vote, said, analyst = lines(public, 3)
    assert set(vote) == FEEDBACK_KEYS and vote['event'] == 'feedback' and vote['vote'] == 'down' and vote['comment'] is None
    assert said['comment'] == 'the number of setups, not the MSF' and said['card'] == 'answer'
    assert analyst['card'] == 'analyst' and analyst['session'] is None
    for bad in ('<b>more</b>', 'see `code`', '&lt;x&gt;', '[link](https://x.example)', '![x](y)', 'two\nlines', 'tab\there',
                'x' * 301, 42, 'rev\u202ebad', 'zero\u200bwidth', 'bom\ufeff', 'tag\U000e0041', 'iso\u2066x\u2069', 'rlm\u200f'):
        status, _, body = feedback(public, {'line': '00000000000000cc', 'vote': 'down', 'comment': bad}, token)
        assert status == 400 and 'plain text' in body['error'], bad
    for bad in ({'line': LINE, 'vote': 'meh'}, {'line': 'not-an-id', 'vote': 'up'}, {'line': LINE, 'vote': 'up', 'session': 'x'},
                {'line': LINE, 'vote': 'up', 'extra': 1}, {'vote': 'up'}, {'line': LINE, 'vote': 'up', 'card': 'page'}, ['up']):
        assert feedback(public, bad, token)[0] == 400, bad
    assert feedback(public, {'line': LINE, 'vote': 'up'}, token)[0] == 400                      # one vote per card
    assert feedback(public, {'line': LINE, 'vote': 'up'}, token, origin='https://evil.example')[0] == 403
    assert feedback(public, {'line': LINE, 'vote': 'up'}, 'wrong-token')[0] == 403
    assert len(lines(public, 3)) == 3   # nothing refused was logged
    assert check_comment(None) is None and check_comment('   ') is None and check_comment('Tom & Jerry') == 'Tom & Jerry'


def test_feedback_is_rate_limited_like_ask_on_its_own_windows(tmp_path, monkeypatch):
    server, thread = start(guards=DemoGuards(rate_limit_per_min=2, trust_proxy=True, ledger=tmp_path / 'ledger.sqlite3'))
    try:
        token = json.loads(request(server, 'GET', '/api/config')[2])['token']
        ip = '203.0.113.9'
        assert feedback(server, {'line': 'a' * 16, 'vote': 'up'}, token, ip)[0] == 200
        assert feedback(server, {'line': 'b' * 16, 'vote': 'up'}, token, ip)[0] == 200
        status, headers, body = feedback(server, {'line': 'c' * 16, 'vote': 'up'}, token, ip)
        assert status == 429 and body['code'] == 'rate_limited' and 1 <= int(headers['Retry-After']) <= 60
        assert feedback(server, {'line': 'c' * 16, 'vote': 'up'}, token, '203.0.113.10')[0] == 200   # per IP
        # feedback never spends a question: the same visitor's questions still have their own two rounds
        ask_headers = {'X-Forwarded-For': ip}
        assert ask(server, 'Why was this shift slow?', ask_headers)[0] == 200
        # and the container-wide window, as the question window
        monkeypatch.setattr('investigator.server.FEEDBACK_PER_MIN', 4)
        status, _, body = feedback(server, {'line': 'd' * 16, 'vote': 'up'}, token, '203.0.113.11')
        assert status == 429 and body['error'].startswith('Feedback limit reached')
    finally:
        stop(server, thread)


def test_feedback_is_not_taken_without_the_log():
    server, thread = start(public=False)
    try:
        token = json.loads(request(server, 'GET', '/api/config')[2])['token']
        status, _, body = request(server, 'POST', '/api/feedback', json.dumps({'line': LINE, 'vote': 'up'}),
                                  {'Content-Type': 'application/json', 'X-Workbench-Token': token})
        assert status == 404 and 'not collected' in json.loads(body)['error']
    finally:
        stop(server, thread)


def test_lines_per_client_are_capped_but_answers_never_are(monkeypatch):
    monkeypatch.setattr('investigator.server.LINES_PER_MIN', 3)
    server, thread = start()
    try:
        statuses = [ask(server, 'Why was this shift slow?', ids(line=f'{i:016x}'))[0] for i in range(4)]
        time.sleep(0.3)
    finally:
        stop(server, thread)
    assert statuses == [200, 200, 200, 200]                         # every question answered
    assert len(server.app.qlog.stream.getvalue().splitlines()) == 3   # three lines (a question and its answer, then a question)


def test_a_format_character_in_a_question_is_removed_from_its_line(public):
    status, _ = ask(public, 'why\u202e was \u200bshift slow\U000e0041?', ids())
    assert status == 200
    question = lines(public, 2)[0]
    assert question['question'] == 'why was shift slow?'


# ---- after the response ---------------------------------------------------------------------------------------

class Gate(io.StringIO):
    """A stream whose write waits until the test opens it: a line written before its response would hang the request."""
    def __init__(self):
        super().__init__()
        self.open = threading.Event()

    def write(self, text):
        assert self.open.wait(5), 'the log line was written before the response reached the client'
        return super().write(text)


def test_the_line_is_written_after_the_response():
    server, thread = start()
    gate = server.app.qlog.stream = Gate()
    try:
        started = time.perf_counter()
        status, result = ask(server, 'Why was this shift slow?', ids())   # both responses arrive while the writes wait
        assert status == 200 and result['status'] == 'complete' and time.perf_counter() - started < 4
        gate.open.set()
        assert [line['event'] for line in lines(server, 2)] == ['question', 'answer']
    finally:
        gate.open.set()
        stop(server, thread)
