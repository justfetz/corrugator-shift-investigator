import json
import pytest
from investigator.anthropic_provider import (AnthropicPlanner, Budget, DEFAULT_MODEL, PRICING, API_VERSION,
                                             COMPACTED_NOTE, MAX_REQUEST_BYTES, RESERVE_FLOOR_MICRO_USD,
                                             anthropic_tools, reserve_micro_usd, resolve_model, transport)
from investigator.agent import Agent, Context, TOOL_SCHEMAS, PROCESS_CONTEXT
from investigator.analysis import Analysis
from investigator.fixture import generate

KEY = 'sk-ant-fake-test-key-not-a-real-credential'


def reply(blocks, stop_reason='tool_use', status=200, usage=None):
    body = {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': DEFAULT_MODEL, 'stop_reason': stop_reason,
            'content': blocks, 'usage': usage if usage is not None else {'input_tokens': 100, 'output_tokens': 50}}
    return status, json.dumps(body).encode()


def tool_use(name, args, call_id='toolu_1'):
    return [{'type': 'thinking', 'thinking': '', 'signature': 'sig'}, {'type': 'tool_use', 'id': call_id, 'name': name, 'input': args}]


@pytest.fixture
def analysis():
    a = Analysis(generate())
    yield a
    a.close()


def test_tool_schemas_map_to_input_schema():
    tools = anthropic_tools(TOOL_SCHEMAS)
    names = [t['name'] for t in tools]
    assert names[-2:] == ['finish', 'cannot_answer'] and 'get_shift_kpis' in names
    for tool in tools:
        assert set(tool) == {'name', 'description', 'input_schema', 'strict'}
        assert tool['input_schema']['additionalProperties'] is False
        assert set(tool['input_schema']['required']) == set(tool['input_schema']['properties'])
        assert 'parameters' not in tool
    # The API refuses more than 20 strict tools per request ("Too many strict tools (21)" took every
    # live golden down on 2026-10-22). Investigation tools are strict; the two control tools are
    # validated locally and sent without strict mode.
    assert sum(t['strict'] for t in tools) <= 20
    assert all(t['strict'] for t in tools if t['name'] not in ('finish', 'cannot_answer'))
    assert not any(t['strict'] for t in tools if t['name'] in ('finish', 'cannot_answer'))


def test_strict_cap_holds_as_tools_grow():
    from investigator.anthropic_provider import MAX_STRICT_TOOLS
    many = TOOL_SCHEMAS + [{**TOOL_SCHEMAS[0], 'name': f'extra_{i}'} for i in range(8)]
    tools = anthropic_tools(many)
    assert len(tools) == len(many) + 2 and sum(t['strict'] for t in tools) == MAX_STRICT_TOOLS


def test_tool_use_rounds_final_round_and_no_key_in_payload(tmp_path, analysis):
    requests, reserves = [], []
    def send(payload, key, timeout):
        assert key == KEY and KEY not in json.dumps(payload)
        reserves.append(reserve_micro_usd(DEFAULT_MODEL, len(json.dumps(payload).encode()), payload['max_tokens']))
        assert 0 < timeout <= 20
        assert payload['model'] == DEFAULT_MODEL and payload['max_tokens'] <= (4096 if payload['tools'][0]['name'] == 'write_narrative' else 2048)
        assert 'temperature' not in payload  # non-default sampling is rejected by the target models
        assert payload['tool_choice'] == {'type': 'auto', 'disable_parallel_tool_use': True}
        assert payload['output_config'] == {'effort': 'low'}
        assert payload['messages'][0]['role'] == 'user'
        requests.append(payload)
        if payload['tools'][0]['name'] == 'write_narrative':
            assert payload['system'].startswith('Write a short superintendent')
            packet = json.loads(payload['messages'][-1]['content'])['evidence']
            rid = packet[0]['result_id']
            return reply(tool_use('write_narrative', {'paragraphs': [{'kind': 'finding',
                'text': 'The leading recorded stop is {{0}}, totaling {{1}} minutes.',
                'facts': [{'result_id': rid, 'row': 0, 'field': 'category'}, {'result_id': rid, 'row': 0, 'field': 'down_minutes'}]}]}))
        assert payload['system'].startswith(PROCESS_CONTEXT) and 'You choose investigation tools' in payload['system']
        if len(requests) == 1:
            return reply(tool_use('get_downtime_breakdown', {'shift': 2, 'kind': None, 'group_by': 'reason'}))
        # The assistant turn is echoed with its thinking block, then the tool_result follows under the same id.
        assistant = payload['messages'][-2]
        assert assistant['role'] == 'assistant' and assistant['content'][0]['type'] == 'thinking'
        result_block = payload['messages'][-1]['content'][0]
        assert result_block['type'] == 'tool_result' and result_block['tool_use_id'] == assistant['content'][1]['id']
        data = json.loads(result_block['content'])
        if len(requests) == 2:
            assert 'trace' not in data['data'] and 'source_ids' not in data['data']['rows'][0]
            return reply(tool_use('render_chart', {'result_id': data['result_id']}, 'toolu_2'))
        first = json.loads(payload['messages'][2]['content'][0]['content'])
        return reply(tool_use('finish', {'result_ids': [first['result_id']]}, 'toolu_3'))
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), send)
    try:
        result = Agent(analysis, planner, max_calls=10, max_seconds=60).ask('Please investigate the stopping causes', Context(analysis.day))
        assert result['status'] == 'complete'
        assert result['usage']['model_calls'] == 4
        assert result['usage']['reserved_usd'] == pytest.approx(sum(reserves)/1_000_000)
        print('reserves', reserves)
        assert result['usage']['cost_usd'] == pytest.approx((400*0.10+200*0.50)/1_000_000)
        assert result['narrative_status'] == 'available'
        assert 'Knife jam' in result['narrative'][0]['text']
        assert len(result['charts']) == 1
        assert result['mode'].startswith('Anthropic (claude-haiku-5-5)')
        assert KEY not in json.dumps(result)
    finally:
        planner.close()
    assert planner._key is None


def test_text_only_reply_finishes_with_gathered_evidence(tmp_path, analysis):
    calls = []
    def send(payload, key, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return reply(tool_use('get_shift_kpis', {'shift': 2}))
        if payload['tools'][0]['name'] == 'write_narrative':
            return reply([{'type': 'text', 'text': 'no tool call'}], 'end_turn')
        return reply([{'type': 'text', 'text': 'That is the evidence.'}], 'end_turn')
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), send)
    try:
        result = Agent(analysis, planner, max_calls=10, max_seconds=60).ask('Show speed', Context(analysis.day))
        assert result['status'] == 'complete' and len(result['evidence_ids']) == 1
        assert result['narrative_status'] == 'unavailable'  # a text-only narrative reply is refused, evidence kept
    finally:
        planner.close()


@pytest.mark.parametrize('ending', ['finish', 'cannot_answer', 'text'])
def test_only_tool_call_refused_ends_unavailable_with_the_tool_reason(tmp_path, ending):
    """The model's only tool call asks for July, which the store refuses; however the
    model then ends (finish on the failed id, cannot_answer, or plain text) the answer
    reads unavailable with the tool's own reason, the same as the offline planner."""
    from investigator.record_store import default_store
    calls = []
    def send(payload, key, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return reply(tool_use('get_period_kpis', {'window': {'period': 'month', 'anchor': '2026-07-01'}, 'compare': True, 'shift': None}))
        result_block = payload['messages'][-1]['content'][0]
        failed = json.loads(result_block['content'])
        assert result_block['type'] == 'tool_result' and failed['ok'] is False and 'outside the dataset range' in failed['error']['message']
        if ending == 'finish':
            return reply(tool_use('finish', {'result_ids': [failed['result_id']]}, 'toolu_2'))
        if ending == 'cannot_answer':
            return reply(tool_use('cannot_answer', {'reason': 'July is not in the dataset'}, 'toolu_2'))
        return reply([{'type': 'text', 'text': 'July is not available.'}], 'end_turn')
    store_analysis = default_store().day_analysis('2026-09-14')
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), send)
    try:
        result = Agent(store_analysis, planner, max_calls=10, max_seconds=60).ask('How did July go?', Context('2026-09-14', 2, scope='day'))
        assert result['status'] == 'unavailable' and result['usage']['model_calls'] == 2
        [step] = result['trace']
        assert step['tool'] == 'get_period_kpis' and not step['ok']
        assert step['error']['message'] in result['sections'] and 'outside the dataset range 2026-08-03 to 2026-09-30' in step['error']['message']
        assert result['charts'] == [] and result['tables'] == [] and result['evidence_ids'] == [] and result['narrative_status'] == 'unavailable'
    finally:
        planner.close()
        store_analysis.close()


def test_text_only_reply_without_evidence_is_unavailable(tmp_path, analysis):
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), lambda *a: reply([{'type': 'text', 'text': 'Hello'}], 'end_turn'))
    try:
        result = Agent(analysis, planner).ask('Show speed', Context(analysis.day))
        assert result['status'] == 'unavailable' and not result['trace']
    finally:
        planner.close()


@pytest.mark.parametrize('status,phrase', [(401, 'misconfigured key'), (403, 'misconfigured key'),
                                           (429, 'busy; retry in a minute'), (529, 'busy; retry in a minute'),
                                           (500, 'Upstream model error (HTTP 500)'), (400, 'Upstream model error (HTTP 400)')])
def test_http_status_mapping_never_echoes_body(tmp_path, status, phrase):
    secret_body = b'{"error":{"type":"x","message":"SECRET-UPSTREAM-DETAIL"}}'
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), lambda *a: (status, secret_body))
    with pytest.raises(ValueError) as exc:
        planner.next_step({'process': 'p', 'question': 'q', 'production_day': 'd', 'shifts': [2], 'history': [], 'tools': TOOL_SCHEMAS, 'results': []})
    assert phrase in exc.value.public_message and 'SECRET' not in str(exc.value)
    assert planner.usage['model_calls'] == 1 and planner.usage['reserved_usd'] > 0  # the reservation is kept
    planner.close()


def test_response_size_cap_and_incomplete_stop(tmp_path, analysis):
    big = AnthropicPlanner(KEY, Budget(tmp_path/'a.db'), lambda *a: (200, b'{' + b' '*512001 + b'}'))
    with pytest.raises(ValueError, match='size limit'):
        big.next_step({'process': 'p', 'question': 'q', 'production_day': 'd', 'shifts': [2], 'history': [], 'tools': TOOL_SCHEMAS, 'results': []})
    big.close()
    cut = AnthropicPlanner(KEY, Budget(tmp_path/'b.db'), lambda *a: reply(tool_use('get_shift_kpis', {'shift': 2}), 'max_tokens'))
    try:
        result = Agent(analysis, cut).ask('Show speed', Context(analysis.day))
        assert result['status'] == 'planner_error' and not result['trace']
    finally:
        cut.close()
    refused = AnthropicPlanner(KEY, Budget(tmp_path/'c.db'), lambda *a: reply([], 'refusal'))
    with pytest.raises(ValueError, match='declined'):
        refused.next_step({'process': 'p', 'question': 'q', 'production_day': 'd', 'shifts': [2], 'history': [], 'tools': TOOL_SCHEMAS, 'results': []})
    refused.close()


def test_budget_exhaustion_blocks_before_network(tmp_path):
    guard = RESERVE_FLOOR_MICRO_USD  # no call reserves less than the floor
    budget = Budget(tmp_path/'budget.db', limit_usd=guard/1_000_000)
    budget.reserve(guard)  # the month's allowance is taken by one earlier call
    planner = AnthropicPlanner(KEY, budget, lambda *a: pytest.fail('Network should not run'))
    with pytest.raises(ValueError, match='budget exhausted'):
        planner.next_step({'process': 'p', 'question': 'q', 'production_day': 'd', 'shifts': [2], 'history': [], 'tools': TOOL_SCHEMAS, 'results': []})
    assert planner.usage['model_calls'] == 0
    planner.close()


def test_model_selection_and_pricing(tmp_path):
    assert resolve_model(None) == DEFAULT_MODEL and resolve_model('') == DEFAULT_MODEL
    assert resolve_model('claude-sonnet-5-5') == 'claude-sonnet-5-5'
    with pytest.raises(ValueError, match='ANTHROPIC_MODEL'):
        resolve_model('claude-opus-5-5')
    seen = []
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), lambda p, k, t: seen.append(p) or reply(tool_use('finish', {'result_ids': []})), model='claude-sonnet-5-5')
    planner.next_step({'process': 'p', 'question': 'q', 'production_day': 'd', 'shifts': [2], 'history': [], 'tools': TOOL_SCHEMAS, 'results': []})
    assert seen[0]['model'] == 'claude-sonnet-5-5'
    expected = reserve_micro_usd('claude-sonnet-5-5', len(json.dumps(seen[0]).encode()), 2048)
    assert planner.usage['reserved_usd'] == pytest.approx(expected/1_000_000)
    assert expected > reserve_micro_usd(DEFAULT_MODEL, len(json.dumps(seen[0]).encode()), 2048)
    assert planner.usage['cost_usd'] == pytest.approx((100*2.0+50*10.0)/1_000_000)
    planner.close()


def test_bad_key_not_echoed_and_call_limit(tmp_path):
    with pytest.raises(ValueError) as exc:
        AnthropicPlanner('sk-not-anthropic-secret-value', Budget(tmp_path/'budget.db'))
    assert 'secret-value' not in str(exc.value)
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), lambda *a: pytest.fail('Network should not run'))
    planner.usage['model_calls'] = 10
    with pytest.raises(ValueError, match='limit reached'):
        planner.next_step({})
    planner.close()


def test_transport_headers_and_fixed_destination(monkeypatch):
    captured = {}
    class FakeResponse:
        status = 200
        def read(self, n): return b'{"ok":true}'
    class FakeConnection:
        def __init__(self, host, timeout): captured['host'] = host; captured['timeout'] = timeout
        def request(self, method, path, body, headers): captured.update(method=method, path=path, body=body, headers=headers)
        def getresponse(self): return FakeResponse()
        def close(self): captured['closed'] = True
    monkeypatch.setattr('investigator.anthropic_provider.http.client.HTTPSConnection', FakeConnection)
    assert transport({'a': 1}, KEY, 7) == (200, b'{"ok":true}')
    assert captured['host'] == 'api.anthropic.com' and captured['path'] == '/v1/messages' and captured['method'] == 'POST'
    assert captured['headers'] == {'x-api-key': KEY, 'anthropic-version': API_VERSION, 'content-type': 'application/json'}
    assert captured['timeout'] == 7 and captured['closed'] is True


def test_mapped_tool_schemas_use_anyof_for_nullable_filters():
    """The strict validator rejects list-valued types with enums and None members (live runs 1 and 2)."""
    from investigator.agent import TOOL_SCHEMAS
    from investigator.anthropic_provider import anthropic_tools

    def walk(node):
        if isinstance(node, dict):
            assert not isinstance(node.get('type'), list), node
            if 'enum' in node:
                assert None not in node['enum'] and not isinstance(node.get('type'), list), node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    tools = anthropic_tools(TOOL_SCHEMAS)
    walk(tools)
    kind = next(t for t in tools if t['name'] == 'get_downtime_breakdown')['input_schema']['properties']['kind']
    assert kind == {'anyOf': [{'type': 'string', 'enum': ['Maintenance', 'Operator', 'PM']}, {'type': 'null'}]}
    shift = next(t for t in tools if t['name'] == 'get_period_kpis')['input_schema']['properties']['shift']
    assert shift['anyOf'][0] == {'type': 'integer', 'enum': [1, 2, 3]} and shift['anyOf'][-1] == {'type': 'null'}
    assert 'description' in shift
    # The application's own schemas are untouched: the agent still validates None as "all kinds".
    assert None in next(t for t in TOOL_SCHEMAS if t['name'] == 'get_downtime_breakdown')['parameters']['properties']['kind']['enum']


def test_finish_drops_chart_ids_instead_of_failing(tmp_path, analysis):
    """Live run 3: the model listed the rendered chart among the evidence ids and the whole
    investigation ended planner_error. Charts are output; filter them out of finish."""
    calls = []
    def send(payload, key, timeout):
        calls.append(payload)
        if payload['tools'][0]['name'] == 'write_narrative':
            packet = json.loads(payload['messages'][-1]['content'])['evidence']
            rid = packet[0]['result_id']
            return reply(tool_use('write_narrative', {'paragraphs': [{'kind': 'finding', 'text': 'Top recorded stop: {{0}}.',
                'facts': [{'result_id': rid, 'row': 0, 'field': 'category'}]}]}))
        n = len([c for c in calls if c['tools'][0]['name'] != 'write_narrative'])
        if n == 1:
            return reply(tool_use('get_downtime_breakdown', {'shift': 2, 'kind': None, 'group_by': 'reason'}, 'toolu_a'))
        if n == 2:
            run = json.loads(payload['messages'][-1]['content'][0]['content'])['result_id'].split(':')[0]
            return reply(tool_use('render_chart', {'result_id': f'{run}:1'}, 'toolu_b'))
        run = json.loads(payload['messages'][-1]['content'][0]['content'])['result_id'].split(':')[0]
        return reply(tool_use('finish', {'result_ids': [f'{run}:1', f'{run}:2', f'{run}:nonsense']}, 'toolu_c'))
    planner = AnthropicPlanner(KEY, Budget(tmp_path / 'b.sqlite3'), send=send)
    result = Agent(analysis, planner=planner).ask('Why was this shift slow? Chart downtime by reason.', Context('2026-09-01', 2))
    assert result['status'] == 'complete', result['sections'][:2]
    assert len(result['evidence_ids']) == 1 and result['evidence_ids'][0].endswith(':1')
    assert len(result['charts']) == 1 and result['narrative_status'] == 'available'


def test_finish_with_only_chart_ids_is_unavailable(tmp_path, analysis):
    calls = []
    def send(payload, key, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return reply(tool_use('get_downtime_breakdown', {'shift': 2, 'kind': None, 'group_by': 'reason'}, 'toolu_a'))
        if len(calls) == 2:
            run = json.loads(payload['messages'][-1]['content'][0]['content'])['result_id'].split(':')[0]
            return reply(tool_use('render_chart', {'result_id': f'{run}:1'}, 'toolu_b'))
        run = json.loads(payload['messages'][-1]['content'][0]['content'])['result_id'].split(':')[0]
        return reply(tool_use('finish', {'result_ids': [f'{run}:2']}, 'toolu_c'))
    planner = AnthropicPlanner(KEY, Budget(tmp_path / 'b.sqlite3'), send=send)
    result = Agent(analysis, planner=planner).ask('Chart downtime by reason.', Context('2026-09-01', 2))
    assert result['status'] == 'unavailable'


@pytest.mark.parametrize('scope, expected_selected, expected_scope_word', [
    ('shift', 2, 'selected shift only'), ('day', None, 'all three shifts')])
def test_first_message_states_scope_and_nulls_the_shift_in_day_scope(tmp_path, analysis, scope, expected_selected, expected_scope_word):
    """Live run 3: every month/week question was filtered to shift 2 because the first message
    always said selected_shift: 2. In production-day scope the model must be told null = all shifts."""
    seen = {}
    def send(payload, key, timeout):
        if not seen:
            seen.update(json.loads(payload['messages'][0]['content']))
            return reply(tool_use('get_period_kpis', {'window': {'period': 'month', 'anchor': '2026-09-01'}, 'compare': True,
                                                      'shift': expected_selected}, 'toolu_a'))
        return reply([{'type': 'text', 'text': 'done'}], stop_reason='end_turn')
    planner = AnthropicPlanner(KEY, Budget(tmp_path / 'b.sqlite3'), send=send)
    context = Context('2026-09-01', 2); context.scope = scope
    Agent(analysis, planner=planner).ask('How did this month go?', context)
    assert seen['selected_shift'] == expected_selected
    assert expected_scope_word in seen['scope']
    assert seen['allowed_shifts'] == ([2] if scope == 'shift' else [1, 2, 3])


def test_planner_instructions_cover_scope_and_place_versus_reason():
    from investigator.openai_provider import PLANNER_INSTRUCTIONS
    assert 'pass shift null' in PLANNER_INSTRUCTIONS
    assert 'group_by "place"' in PLANNER_INSTRUCTIONS
    assert 'a rendered chart is output, not evidence' in PLANNER_INSTRUCTIONS


def _big_result(index, size):
    return {'result_id': f'r{index}', 'tool': 'get_wet_end_performance', 'ok': True,
            'data': {'rows': [{'order_id': f'o{index}-{n}', 'note': 'x'*200} for n in range(size//210)]}}


def _ctx(results):
    return {'process': 'p', 'question': 'why slow', 'production_day': 'd', 'shifts': [1, 2, 3], 'scope': 'day',
            'allowed_shifts': [1, 2, 3], 'history': [], 'tools': TOOL_SCHEMAS, 'results': results}


def test_long_investigation_trims_older_results_instead_of_failing(tmp_path):
    sizes = []
    def send(payload, key, timeout):
        sizes.append(len(json.dumps(payload).encode()))
        n = len(sizes)
        return reply(tool_use('get_wet_end_performance', {'window': 'month'}, f'toolu_{n}'))
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'), send)
    try:
        results = []
        planner.next_step(_ctx(results))
        for i in range(5):  # five 20 KB results: the raw history would pass 100 KB
            results.append(_big_result(i, 20000))
            planner.next_step(_ctx(results))
        assert len(sizes) == 6 and max(sizes) <= MAX_REQUEST_BYTES
        tool_results = [m['content'][0] for m in planner._messages if m['role'] == 'user' and isinstance(m['content'], list)]
        assert len(tool_results) == 5
        # The newest result is sent in full; the oldest ones are stubs that keep the citation handle.
        assert json.loads(tool_results[-1]['content'])['data']['rows'][0]['order_id'] == 'o4-0'
        first = json.loads(tool_results[0]['content'])
        assert first == {'result_id': 'r0', 'tool': 'get_wet_end_performance', 'ok': True,
                         'row_count': len(_big_result(0, 20000)['data']['rows']), 'note': COMPACTED_NOTE}
        assert planner.usage['model_calls'] == 6
    finally:
        planner.close()


def test_single_oversized_result_is_still_refused(tmp_path):
    planner = AnthropicPlanner(KEY, Budget(tmp_path/'budget.db'),
                               lambda *a: reply(tool_use('get_wet_end_performance', {'window': 'month'})))
    try:
        planner.next_step(_ctx([]))
        with pytest.raises(ValueError, match='byte limit'):
            planner.next_step(_ctx([_big_result(0, MAX_REQUEST_BYTES+5000)]))
        assert planner.usage['model_calls'] == 1  # refused before the network and before any reservation
    finally:
        planner.close()


def test_reserve_scales_with_request_size_and_model():
    small = reserve_micro_usd(DEFAULT_MODEL, 1000, 2048)
    typical = reserve_micro_usd(DEFAULT_MODEL, 14000, 2048)
    worst = reserve_micro_usd(DEFAULT_MODEL, MAX_REQUEST_BYTES, 4096)
    assert RESERVE_FLOOR_MICRO_USD <= small < typical < worst
    assert worst == pytest.approx(2*((MAX_REQUEST_BYTES/3)*0.10 + 4096*0.50), abs=1)
    assert reserve_micro_usd('claude-sonnet-5-5', 14000, 2048) == pytest.approx(2*((14000/3)*2.0 + 2048*10.0), abs=1)
    assert reserve_micro_usd(DEFAULT_MODEL, 1, 1) == RESERVE_FLOOR_MICRO_USD
