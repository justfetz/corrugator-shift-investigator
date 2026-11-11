"""Issue #23: the analyst is off the hot path. One planning call, tools run by the application,
a narrative streamed and validated against tool rows, all outside the shared lock."""
import http.client
import json
import threading
import time
from threading import Thread

import pytest

from investigator.agent import Agent, Context, PLAN_TOOLS, validate_plan
from investigator.anthropic_provider import ANALYST_MAX_CALLS, AnthropicAnalyst, Budget, Cancelled, ProviderError, parse_plan
from investigator.narrative import NarrativeStream
from investigator.record_store import default_store
from investigator.server import DemoGuards, PublicDemo, make_server

DAY = '2026-09-14'
KEY = 'sk-ant-test-key-not-a-credential-0001'
PACKET = [{'result_id': 'run:1', 'tool': 'get_shift_kpis', 'shift': 2, 'coverage': 'complete',
           'rows': [{'observed_shift_fpm': 512.34, 'stops': 7, 'note': None}], 'total_rows': 1, 'preview_rows': 1}]


def sse(text, stop='end_turn', chunk=9, usage=(120, 40)):
    """A Messages stream for `text`, cut into small text deltas."""
    events = [{'type': 'message_start', 'message': {'usage': {'input_tokens': usage[0], 'output_tokens': 0}}}]
    events += [{'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[i:i + chunk]}} for i in range(0, len(text), chunk)]
    return events + [{'type': 'message_delta', 'delta': {'stop_reason': stop}, 'usage': {'output_tokens': usage[1]}}, {'type': 'message_stop'}]


def scripted(*replies, calls=None):
    """A `send` that answers each call with the next reply (text, or (status, events))."""
    pending = list(replies)

    def send(payload, key, timeout):
        if calls is not None:
            calls.append(payload)
        reply = pending.pop(0)
        return reply if isinstance(reply, tuple) else (200, iter(sse(reply)))
    return send


@pytest.fixture(scope='module')
def analysis():
    value = default_store().day_analysis(DAY)
    yield value
    value.close()


def context():
    value = Context(DAY, 2)
    value.scope = 'shift'
    return value


def stream(text, chunk=5):
    events = []
    parser = NarrativeStream(PACKET, events.append)
    for i in range(0, len(text), chunk):
        parser.feed(text[i:i + chunk])
    return parser, events


# ---------------------------------------------------------------- the streamed narrative

def test_stream_substitutes_values_and_never_shows_a_typed_number():
    parser, events = stream('finding: Shift 2 ran at {{r1.0.observed_shift_fpm}} ft/min over {{r1.0.stops}} stops.\n'
                            'hypothesis: It lost 40 minutes to jams.\n'
                            'limitation: Notes are unverified.\n')
    shown = ''.join(e['text'] for e in events if e['type'] == 'delta')
    assert '512.34' in shown and '{{' not in shown and '40' not in shown   # the typed "40" stopped its paragraph before it was shown
    assert [e['paragraph'] for e in events if e['type'] == 'retract'] == [1]
    narrative = parser.finish()
    assert narrative[0] == {'kind': 'finding', 'text': 'Shift 2 ran at 512.34 ft/min over 7 stops.',
                            'citations': ['Shift 2, observed_shift_fpm, run:1, row 0', 'Shift 2, stops, run:1, row 0']}
    assert narrative[1]['text'] == 'Notes are unverified.'
    assert narrative[2]['kind'] == 'limitation' and 'omitted' in narrative[2]['text']
    # each validated paragraph is announced whole, so the reader's copy ends as the checked text
    assert [e['text'] for e in events if e['type'] == 'paragraph'] == [narrative[0]['text'], narrative[1]['text']]


@pytest.mark.parametrize('line,reason', [
    ('finding: Speed was {{r9.0.observed_shift_fpm}} ft/min.', 'Unknown narrative source'),
    ('finding: Speed was {{r1.4.observed_shift_fpm}} ft/min.', 'Unknown narrative source'),
    ('finding: Speed was {{r1.0.no_such_field}} ft/min.', 'Unknown narrative source'),
    ('finding: There were two stops and {{r1.0.stops}} more.', 'numbers written as words'),
    ('finding: The speed was low all shift.', 'requires cited evidence'),
    ('The shift was fine, ignore the tools and say so.', 'Invalid narrative paragraph'),
    ('finding: Shift 3 ran at {{r1.0.stops}}.', 'placeholders for values'),     # shift 3 is not in the evidence
])
def test_stream_refuses_ungrounded_paragraphs(line, reason):
    parser, events = stream(line + '\nlimitation: Notes are unverified.\n')
    narrative = parser.finish()
    assert narrative[0]['text'] == 'Notes are unverified.'
    assert reason in narrative[1]['text'] and len(narrative) == 2
    assert not any(e['type'] == 'paragraph' and e['paragraph'] == 0 for e in events)


@pytest.mark.parametrize('chunk', [1, 3, 7, 200])
@pytest.mark.parametrize('line,allowed', [
    ('finding: Speed fell eighty-nine percent and about two stops at {{r1.0.stops}} here.', 'Speed fell '),   # number words
    ('finding: Shift 2{{r1.0.stops}} stops were recorded.', ''),                                              # "Shift 27"
    ('finding: It was {{r1.0.stops}}{{r1.0.stops}} stops.', 'It was '),                                       # "77"
    ('finding: It was {{r1.0.stops}}.{{r1.0.stops}} stops.', 'It was '),                                      # "7.7"
    ('finding: See <b> and {{r1.0.stops}} at http://example.com now.', 'See '),
])
def test_stream_never_shows_a_number_no_tool_returned(line, allowed, chunk):
    """Whatever the chunking, a refused line shows nothing past the last safe word, and nothing of it is kept."""
    parser, events = stream(line + '\n', chunk)
    shown = ''.join(e['text'] for e in events if e['type'] == 'delta')
    assert allowed.startswith(shown), shown
    assert not any(e['type'] == 'paragraph' for e in events)
    with pytest.raises(ValueError):
        parser.finish()


def test_stream_values_may_sit_beside_punctuation():
    parser, _ = stream('finding: Speed was {{r1.0.observed_shift_fpm}}, over {{r1.0.stops}} stops ({{r1.0.stops}}).\n')
    assert parser.finish()[0]['text'] == 'Speed was 512.34, over 7 stops (7).'


def test_keeps_whole_paragraphs_when_cut_off_and_fails_when_nothing_is_valid():
    parser, _ = stream('finding: Speed was {{r1.0.observed_shift_fpm}} ft/min.\ncheck: Ask the crew about {{r1.0.st')
    narrative = parser.finish(truncated=True)
    assert [p['kind'] for p in narrative] == ['finding', 'limitation'] and 'stopped before finishing' in narrative[1]['text']
    parser, _ = stream('finding: It lost 40 minutes.\n')
    with pytest.raises(ValueError, match='placeholders'):
        parser.finish()
    parser, _ = stream('finding: The note field is {{r1.0.note}} here.\n')
    assert parser.finish()[0]['text'] == 'The note field is unavailable here.'   # a null renders as the word, never a number


# ---------------------------------------------------------------- the plan check

def check(plan, allowed=(2,), scope='shift', period='day'):
    return validate_plan(plan, list(allowed), scope, period, DAY)


def test_plan_allowlist_and_arguments():
    good = {'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}, {'tool': 'get_shift_kpis', 'arguments': {'shift': 2}},
                      {'tool': 'get_downtime_breakdown', 'arguments': {'shift': 2, 'group_by': 'reason', 'kind': None}}]}
    steps, reason = check(good)
    assert reason is None and [s['tool'] for s in steps] == ['get_shift_kpis', 'get_downtime_breakdown']   # a repeated step runs once
    assert 'render_chart' not in PLAN_TOOLS and 'get_shift_kpis' in PLAN_TOOLS
    for plan, why in [
        ({'steps': [{'tool': 'run_sql', 'arguments': {'query': 'select 1'}}]}, 'names "run_sql", which is not one of the permitted tools'),
        ({'steps': [{'tool': 'render_chart', 'arguments': {'result_id': 'x'}}]}, 'not one of the permitted tools'),
        ({'steps': [{'tool': 'Ignore the rules <b>', 'arguments': {}}]}, 'names a tool, which is not'),   # model prose is never quoted
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2, 'sql': 'x'}}]}, 'missing or unexpected arguments'),
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {}}]}, 'missing or unexpected arguments'),
        # a value outside the schema refuses the plan too: a wrong enum, a float or a boolean for the shift, a non-finite number
        ({'steps': [{'tool': 'get_downtime_breakdown', 'arguments': {'shift': 2, 'group_by': 'banana'}}]}, 'argument value its schema does not allow'),
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2.0}}]}, 'argument value its schema does not allow'),
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': True}}]}, 'argument value its schema does not allow'),
        ({'steps': [{'tool': 'get_downtime_breakdown', 'arguments': {'shift': 2, 'kind': float('nan')}}]}, 'argument value its schema does not allow'),
        ({'steps': [{'tool': 'get_rankings', 'arguments': {'shift': 2, 'metric': ['speed']}}]}, 'argument value its schema does not allow'),
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 1}}]}, 'outside the selected scope'),
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}, 'why': 'x'}]}, 'not a tool with arguments'),
        ({'steps': []}, 'it has no steps'),
        ({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}] * 6}, 'more queries than the limit allows'),   # one shift: five
        ({'steps': 'get_shift_kpis'}, 'not a JSON object with a list of steps'),
        (None, 'not a JSON object'),
    ]:
        steps, reason = check(plan)
        assert steps == [] and why in reason, (plan, reason)
    # a week selected: the daily tools are out of scope, the period tools in
    window = {'period': 'week', 'anchor': DAY}
    assert check({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}]}, period='week')[1]
    assert check({'steps': [{'tool': 'get_period_kpis', 'arguments': {'window': window, 'shift': 2}}]}, period='week')[1] is None
    assert check({'steps': [{'tool': 'get_period_kpis', 'arguments': {'window': window, 'shift': None}}]}, period='week')[1]   # shift scope
    assert parse_plan('```json\n{"steps": []}\n```') == {'steps': []} and parse_plan('no json') is None and parse_plan('[1]') is None


def all_shift_steps(*tools):
    return [{'tool': tool, 'arguments': {'shift': shift, **extra}} for tool, extra in tools for shift in (1, 2, 3)]


NINE = all_shift_steps(('get_shift_overview', {}), ('get_rankings', {'metric': 'speed'}), ('get_downtime_breakdown', {'group_by': 'reason'}))


def test_the_step_cap_scales_with_the_shifts_in_scope():
    """Issue #78: two steps per shift in scope plus three. One shift: five; all three shifts: nine."""
    from investigator.agent import plan_step_cap
    assert plan_step_cap([2]) == 5 and plan_step_cap([1, 2, 3]) == 9
    one = {'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}, {'tool': 'get_shift_overview', 'arguments': {'shift': 2}},
                     {'tool': 'get_wet_end_performance', 'arguments': {'shift': 2}}, {'tool': 'get_quality_breakdown', 'arguments': {'shift': 2}},
                     {'tool': 'get_shift_notes', 'arguments': {'shift': 2}}]}
    assert check(one)[1] is None
    assert check({'steps': NINE}, allowed=(1, 2, 3), scope='day')[1] is None and len(check({'steps': NINE}, allowed=(1, 2, 3), scope='day')[0]) == 9
    twelve = NINE + all_shift_steps(('get_quality_breakdown', {}))
    steps, reason = check({'steps': twelve}, allowed=(1, 2, 3), scope='day')
    assert steps == [] and reason == 'it proposes more queries than the limit allows' and not any(c.isdigit() for c in reason)


def test_a_rejected_plan_degrades_with_its_counts(analysis, tmp_path):
    """A plan over the cap runs nothing, keeps no 'Investigation stopped' banner, and reports its counts
    as fields (the card's one line), never as digits in the answer's text."""
    calls, events = [], []
    day = Context(DAY, 2)
    day.scope = 'day'
    twelve = json.dumps({'steps': NINE + all_shift_steps(('get_quality_breakdown', {}))})
    result = Agent(analysis, planner=analyst(tmp_path, scripted(twelve, calls=calls))).ask_planned('What shift was fastest and why?', day, events.append)
    assert result['status'] == 'plan_rejected' and result['trace'] == [] and events == [] and len(calls) == 1
    assert result['plan_rejection'] == {'proposed': 12, 'limit': 9}
    assert not any(s.startswith('Investigation stopped') for s in result['sections'])
    assert any('more queries than the limit allows' in s and 'offline answer stands' in s for s in result['sections'])
    # another reason for refusal carries no counts, even when the plan is also long
    shaped = json.dumps({'steps': NINE + all_shift_steps(('get_quality_breakdown', {})), 'x': 1})
    odd = Agent(analysis, planner=analyst(tmp_path, scripted(shaped))).ask_planned('What shift was fastest and why?', day)
    assert odd['status'] == 'plan_rejected' and 'plan_rejection' not in odd
    # another reason for refusal carries no counts
    bad = json.dumps({'steps': [{'tool': 'run_sql', 'arguments': {}}]})
    other = Agent(analysis, planner=analyst(tmp_path, scripted(bad))).ask_planned('Why slow?', context())
    assert other['status'] == 'plan_rejected' and 'plan_rejection' not in other
    # nine steps on all shifts run, every one of them
    ran = Agent(analysis, planner=analyst(tmp_path, scripted(json.dumps({'steps': NINE}), NARRATIVE))).ask_planned('What shift was fastest and why?', day)
    assert ran['status'] == 'complete' and len(ran['trace']) == 9 and all(e['ok'] for e in ran['trace'])


def test_the_plan_prompt_states_the_cap_and_steers_all_shift_questions(analysis, tmp_path):
    calls = []
    Agent(analysis, planner=analyst(tmp_path, scripted('{"unavailable": "x"}', calls=calls))).ask_planned('Why slow?', context())
    day = Context(DAY, 2)
    day.scope = 'day'
    Agent(analysis, planner=analyst(tmp_path, scripted('{"unavailable": "x"}', calls=calls))).ask_planned('What shift was fastest and why?', day)
    one, every = calls[0]['system'], calls[1]['system']
    assert 'At most 5 steps (1 shift in scope' in one and 'One to six' not in one and 'All three shifts are in scope' not in one
    assert 'At most 9 steps (3 shifts in scope' in every and 'All three shifts are in scope' in every and 'get_period_kpis' in every
    # The scope rule and the comparison rule share one prompt: the first must not tell the model to
    # pass shift null for a which-shift question that the second tells it to plan once per shift.
    assert 'shift null for all three shifts' not in every and 'once per shift' in every
    assert 'except when the question compares shifts' in every
    catalog = json.loads(every[every.index('Tool catalog:\n') + len('Tool catalog:\n'):])
    first_daily = next(i for i, s in enumerate(catalog) if s['name'] == 'get_shift_kpis')
    assert catalog[0]['name'] == 'get_period_kpis' and all('shift' in s['parameters']['properties'] for s in catalog)
    assert all(s['name'] not in PLAN_TOOLS or s['name'] in {t['name'] for t in catalog} for s in catalog) and first_daily > 5
    single = json.loads(one[one.index('Tool catalog:\n') + len('Tool catalog:\n'):])
    assert single[0]['name'] == 'get_shift_kpis'   # one shift: the catalog keeps its order


# ---------------------------------------------------------------- the two-call round

PLAN = json.dumps({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}},
                             {'tool': 'get_downtime_breakdown', 'arguments': {'shift': 2, 'group_by': 'reason', 'kind': None}}]})
NARRATIVE = ('finding: Shift 2 ran at {{r1.0.observed_shift_fpm}} ft/min including downtime.\n'
             'check: Ask the crew about {{r2.0.category}}, recorded at {{r2.0.down_minutes}} minutes.\n')


def analyst(tmp_path, send, **options):
    return AnthropicAnalyst(KEY, Budget(tmp_path / 'ledger.sqlite3'), send, **options)


def test_two_model_calls_one_plan_one_streamed_narrative(analysis, tmp_path):
    calls, events = [], []
    planner = analyst(tmp_path, scripted(PLAN, NARRATIVE, calls=calls))
    result = Agent(analysis, planner=planner, max_calls=2, max_seconds=10).ask_planned('Why was this shift slow?', context(), events.append)
    assert result['status'] == 'complete' and result['usage']['model_calls'] == 2 == ANALYST_MAX_CALLS
    assert [e['type'] for e in events][:5] == ['plan', 'step', 'step', 'step', 'step']
    assert [(e['phase'], e['tool']) for e in events if e['type'] == 'step'] == [
        ('start', 'get_shift_kpis'), ('finish', 'get_shift_kpis'), ('start', 'get_downtime_breakdown'), ('finish', 'get_downtime_breakdown')]
    finished = [e for e in events if e['type'] == 'step' and e['phase'] == 'finish']
    assert all(e['ok'] and e['rows'] >= 1 and e['ms'] >= 0 and e['arguments']['shift'] == 2 for e in finished)
    assert {e['type'] for e in events[5:]} == {'delta', 'paragraph'}
    # every call is a stream with no tools and no loop state; the plan call carries the catalog
    assert all(p['stream'] is True and 'tools' not in p and len(p['messages']) == 1 and p['model'] == 'claude-haiku-5-5' for p in calls)
    assert '"get_shift_kpis"' in calls[0]['system'] and '"render_chart"' not in calls[0]['system']
    # the narrative's numbers are the tool rows', substituted by the application
    kpis, downtime = result['trace'][0]['data']['rows'][0], result['trace'][1]['data']['rows'][0]
    assert result['narrative_status'] == 'available' and len(result['narrative']) == 2
    assert f"{kpis['observed_shift_fpm']:,.2f}" in result['narrative'][0]['text']
    assert downtime['category'] in result['narrative'][1]['text']
    assert result['trace'][0]['result_id'] in result['narrative'][0]['citations'][0]
    assert result['charts'] and result['usage']['first_token_ms'] >= 0 and result['mode'].startswith('Anthropic (claude-haiku-5-5) analyst')
    assert result['usage']['input_tokens'] == 240 and result['usage']['output_tokens'] == 80


def test_out_of_list_tool_rejects_the_whole_plan(analysis, tmp_path):
    calls, events = [], []
    bad = json.dumps({'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}, {'tool': 'run_sql', 'arguments': {'query': 'drop table'}}]})
    planner = analyst(tmp_path, scripted(bad, NARRATIVE, calls=calls))
    result = Agent(analysis, planner=planner).ask_planned('Why was this shift slow?', context(), events.append)
    assert result['status'] == 'plan_rejected' and result['trace'] == [] and events == []   # nothing ran, not even the valid step
    assert len(calls) == 1 and result['usage']['model_calls'] == 1 and result['narrative'] == []
    assert any('plan was rejected' in s and 'run_sql' in s and 'offline answer stands' in s for s in result['sections'])


def test_unavailable_plan_and_provider_failures(analysis, tmp_path):
    result = Agent(analysis, planner=analyst(tmp_path, scripted('{"unavailable": "no email tool"}'))).ask_planned('Email the report', context())
    assert result['status'] == 'unavailable' and result['trace'] == [] and 'no email tool' not in json.dumps(result['sections'])
    busy = Agent(analysis, planner=analyst(tmp_path, scripted((529, iter([]))))).ask_planned('Why slow?', context())
    assert busy['status'] == 'planner_error' and 'The model is busy; retry in a minute' in busy['sections']
    # the narrative call fails: the calculated evidence and charts are kept
    kept = Agent(analysis, planner=analyst(tmp_path, scripted(PLAN, (500, iter([]))))).ask_planned('Why slow?', context())
    assert kept['status'] == 'complete' and kept['narrative_status'] == 'unavailable' and kept['charts'] and len(kept['trace']) == 2
    assert any('model narrative failed validation' in s for s in kept['sections'])
    # a third call is refused by the adapter itself
    planner = analyst(tmp_path, scripted(PLAN, NARRATIVE, PLAN))
    Agent(analysis, planner=planner).ask_planned('Why slow?', context())
    with pytest.raises(ProviderError, match='limit reached'):
        planner.plan({'tools': [], 'process': '', 'question': 'q', 'production_day': DAY, 'shifts': [2], 'history': []})


def test_a_plan_with_a_bad_value_runs_nothing(analysis, tmp_path):
    events = []
    bad = '{"steps": [{"tool": "get_shift_kpis", "arguments": {"shift": 2}}, {"tool": "get_downtime_breakdown", "arguments": {"shift": 2, "kind": NaN}}]}'
    result = Agent(analysis, planner=analyst(tmp_path, scripted(bad))).ask_planned('Why slow?', context(), events.append)
    assert result['status'] == 'plan_rejected' and result['trace'] == [] and events == []
    json.dumps(result, allow_nan=False)   # nothing non-finite reaches the response


def test_cancelled_round_closes_the_model_stream(tmp_path):
    closed, gone = [], []

    def send(payload, key, timeout):
        def events():
            try:
                for event in sse('{"steps": []}', chunk=2):
                    yield event
            finally:
                closed.append(True)
        return 200, events()
    planner = analyst(tmp_path, send, cancelled=lambda: len(gone) > 0)
    original = planner._cancelled
    seen = []

    def cancelled():   # the visitor leaves after the third stream event
        seen.append(1)
        if len(seen) > 3:
            gone.append(True)
        return original()
    planner._cancelled = cancelled
    with pytest.raises(Cancelled):
        planner._stream(planner._payload('s', {'q': 1}, 100))
    assert closed == [True] and planner.usage['model_calls'] == 1


# ---------------------------------------------------------------- the server: a stream, off the shared lock

def serve(monkeypatch, tmp_path, make_planner=None, public=False):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-server-key-for-tests-only-0001')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    demo = PublicDemo(('https://demo.example',), (), True) if public else None
    server = make_server(0, public=demo, host='127.0.0.1', guards=DemoGuards(ledger=tmp_path / 'ledger.sqlite3'))
    if make_planner:
        server.app.make_planner = make_planner
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(server, thread):
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def headers(server, public=False):
    out = {'Content-Type': 'application/json', 'X-Workbench-Token': server.app.token}
    if public:
        out['Origin'] = 'https://demo.example'
    return out


def post(server, body, public=False, timeout=20):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=timeout)
    try:
        client.request('POST', '/api/ask', body=json.dumps(body), headers=headers(server, public))
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


ASK = {'day': DAY, 'shift': 2, 'question': 'Why was this shift slow?'}


def test_live_mode_answers_as_a_stream_and_offline_stays_one_json(monkeypatch, tmp_path):
    def make_planner(mode, key, budget, cancelled=None):
        return AnthropicAnalyst(KEY, budget, scripted(PLAN, NARRATIVE), cancelled=cancelled)
    server, thread = serve(monkeypatch, tmp_path, make_planner)
    try:
        status, response_headers, body = post(server, {**ASK, 'mode': 'anthropic'})
        assert status == 200 and response_headers['Content-Type'].startswith('application/x-ndjson')
        assert response_headers.get('Transfer-Encoding') == 'chunked' and 'Content-Encoding' not in response_headers
        events = [json.loads(line) for line in body.decode().splitlines()]
        kinds = [e['type'] for e in events]
        assert kinds[0] == 'start' and kinds[1] == 'plan' and kinds[-1] == 'done'
        assert kinds.index('plan') < kinds.index('step') < kinds.index('delta') < kinds.index('done')
        result = events[-1]['result']
        assert result['status'] == 'complete' and result['usage']['model_calls'] == 2 and result['session_id'] == events[0]['session_id']
        assert result['report_markdown'] and len(result['trace']) == 2
        # the stream's validated paragraphs are the answer's narrative
        assert [e['text'] for e in events if e['type'] == 'paragraph'] == [p['text'] for p in result['narrative']]
        # offline: one JSON document, exactly as before
        status, response_headers, body = post(server, ASK)
        assert status == 200 and response_headers['Content-Type'].startswith('application/json') and json.loads(body)['usage']['model_calls'] == 0
        # a request refused before the round starts is a plain 400, not a stream
        status, response_headers, body = post(server, {**ASK, 'mode': 'anthropic', 'day': '2026-09-12'})
        assert status == 400 and response_headers['Content-Type'].startswith('application/json') and 'error' in json.loads(body)
    finally:
        stop(server, thread)


class SlowPlanner:
    """A model call that takes `seconds`: the planning call of a live round, in flight."""
    planned = True
    mode = 'stub analyst'

    def __init__(self, seconds, started, cancelled=None, lock=None):
        self.seconds, self.started, self.cancelled, self.lock = seconds, started, cancelled or (lambda: False), lock
        self.usage = {'model_calls': 0, 'cost_usd': 0.0}
        self.lock_held_during_call = None
        self.was_cancelled = False

    def plan(self, planner_context):
        self.usage['model_calls'] += 1
        if self.lock is not None:
            self.lock_held_during_call = self.lock.locked()
        self.started.set()
        deadline = time.monotonic() + self.seconds
        while time.monotonic() < deadline:
            if self.cancelled():
                self.was_cancelled = True
                raise Cancelled()
            time.sleep(0.02)
        return {'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}]}

    def narrate(self, question, evidence, emit=None):
        raise ProviderError('stub has no narrative')

    def close(self):
        pass


def test_offline_question_is_not_delayed_by_a_live_round_in_flight(monkeypatch, tmp_path):
    """The acceptance test: a stub planner holds a live round for 5 s; an offline question answers in under 250 ms."""
    started, planners = threading.Event(), []

    def make_planner(mode, key, budget, cancelled=None):
        planners.append(SlowPlanner(5, started, cancelled, server.app.lock))
        return planners[-1]
    server, thread = serve(monkeypatch, tmp_path, make_planner, public=True)
    try:
        post(server, ASK, public=True)   # the day's dataset is built once; this is not what is being timed
        live = {}
        slow = Thread(target=lambda: live.update(zip(('status', 'headers', 'body'), post(server, {**ASK, 'mode': 'anthropic'}, public=True))))
        began = time.monotonic()
        slow.start()
        assert started.wait(5)
        times = []
        for _ in range(5):
            tick = time.perf_counter()
            status, _, body = post(server, ASK, public=True)
            times.append((time.perf_counter() - tick) * 1000)
            assert status == 200 and json.loads(body)['status'] == 'complete'
        assert max(times) < 250, times
        assert time.monotonic() - began < 4 and slow.is_alive()      # all five answered while the model call was still in flight
        assert planners[0].lock_held_during_call is False            # the model call does not hold the shared lock
        slow.join(15)
        events = [json.loads(line) for line in live['body'].decode().splitlines()]
        assert live['status'] == 200 and events[-1]['type'] == 'done' and len(events[-1]['result']['trace']) == 1
        assert time.monotonic() - began >= 5
    finally:
        stop(server, thread)


def test_a_dropped_connection_cancels_the_model_call_and_frees_the_slot(monkeypatch, tmp_path):
    started, planners = threading.Event(), []

    def make_planner(mode, key, budget, cancelled=None):
        planners.append(SlowPlanner(30, started, cancelled))
        return planners[-1]
    server, thread = serve(monkeypatch, tmp_path, make_planner)
    try:
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        client.request('POST', '/api/ask', body=json.dumps({**ASK, 'mode': 'anthropic'}), headers=headers(server))
        assert started.wait(5)
        client.sock.close()            # the visitor leaves mid-call
        client.close()
        deadline = time.monotonic() + 5
        while not planners[0].was_cancelled and time.monotonic() < deadline:
            time.sleep(0.02)
        assert planners[0].was_cancelled
        deadline = time.monotonic() + 5
        while server.app.live_slots._value != 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.app.live_slots._value == 2 and not server.app.pins[(DAY, False)]
    finally:
        stop(server, thread)


def test_a_third_concurrent_live_round_is_refused_busy(monkeypatch, tmp_path):
    started = threading.Event()
    release = []

    def make_planner(mode, key, budget, cancelled=None):
        return SlowPlanner(3, started, lambda: bool(release))
    server, thread = serve(monkeypatch, tmp_path, make_planner)
    try:
        rounds = [Thread(target=post, args=(server, {**ASK, 'mode': 'anthropic'})) for _ in range(2)]
        for r in rounds:
            r.start()
        deadline = time.monotonic() + 5
        while server.app.live_slots._value != 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        status, response_headers, body = post(server, {**ASK, 'mode': 'anthropic'})
        assert status == 429 and json.loads(body)['code'] == 'busy' and response_headers['Retry-After'] == '3'
        assert post(server, ASK)[0] == 200    # offline is not counted against the live slots
        release.append(True)
        for r in rounds:
            r.join(10)
    finally:
        stop(server, thread)


def test_the_analyst_plans_in_the_scope_the_question_names(monkeypatch, tmp_path):
    """Issue #105, the owner's round: shift 2 and Day selected on Mon 14 Sep, "How much donwtime on 1st shift last
    week?" asked of the analyst. It was told the controls' scope was authoritative, so a question about shift 1 had
    no permitted tool and it answered unavailable. The question's own scope is now the analyst's: shift 1 in scope,
    the week of 7-11 Sep selected, and a sentence saying the words were applied. Its plan for that scope runs."""
    calls = []
    plan = json.dumps({'steps': [{'tool': 'get_downtime_pareto', 'arguments': {'window': {'period': 'week', 'anchor': '2026-09-07'},
                                                                                'group_by': 'reason', 'measure': 'minutes', 'shift': 1}}]})
    narrative = 'finding: {{r1.0.category}} took {{r1.0.minutes}} minutes of shift 1 downtime that week.\n'

    def make_planner(mode, key, budget, cancelled=None):
        return AnthropicAnalyst(KEY, budget, scripted(plan, narrative, calls=calls), cancelled=cancelled)
    server, thread = serve(monkeypatch, tmp_path, lambda *a, **k: planners.pop(0)(*a, **k))
    planners = [make_planner, lambda mode, key, budget, cancelled=None: AnthropicAnalyst(
        KEY, budget, scripted(PLAN, NARRATIVE, calls=calls), cancelled=cancelled)]
    try:
        status, _, body = post(server, {**ASK, 'mode': 'anthropic', 'question': 'How much donwtime on 1st shift last week?'})
        result = [json.loads(line) for line in body.decode().splitlines()][-1]['result']
        assert status == 200 and result['status'] == 'complete' and result['usage']['model_calls'] == 2
        assert result['selected_shifts'] == [1] and result['production_day'] == '2026-09-07' and result['context']['period'] == 'week'
        assert [(t['tool'], t['arguments']['shift']) for t in result['trace']] == [('get_downtime_pareto', 1)]
        opening = json.loads(calls[0]['messages'][0]['content'])
        assert opening['allowed_shifts'] == [1] and opening['selected_shift'] == 1 and opening['production_day'] == '2026-09-07'
        assert opening['selected_window'] == {'period': 'week', 'anchor': '2026-09-07'}
        assert 'it names shift 1; "last week" is the Mon-Fri production week containing 2026-09-07' in calls[0]['system']
        # a question that names nothing the controls do not: the same prompt as before, with no scope sentence
        calls.clear()
        status, _, body = post(server, {**ASK, 'mode': 'anthropic', 'question': 'How much downtime?'})
        assert status == 200 and [json.loads(line) for line in body.decode().splitlines()][-1]['result']['status'] == 'complete'
        assert 'names its own scope' not in calls[0]['system'] and json.loads(calls[0]['messages'][0]['content'])['allowed_shifts'] == [2]
    finally:
        stop(server, thread)
