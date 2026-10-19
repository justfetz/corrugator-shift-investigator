import json
import pytest
from investigator.gemini_provider import GeminiPlanner, gemini_schema, access_token, endpoint_host, MODEL
from investigator.openai_provider import Budget, ProviderError
from investigator.agent import Agent, Context, TOOL_SCHEMAS
from investigator.analysis import Analysis
from investigator.fixture import generate

TOKEN = 'ya29.fake-access-token-not-a-credential'
PROJECT = 'felix-august'


def response(name, args, finish='STOP', usage=None, extra_parts=()):
    return {'candidates': [{'finishReason': finish, 'content': {'role': 'model', 'parts': [
        *extra_parts, {'functionCall': {'name': name, 'args': args}, 'thoughtSignature': 'sig-' + name}]}}],
        'usageMetadata': usage if usage is not None else {'promptTokenCount': 100, 'candidatesTokenCount': 50, 'totalTokenCount': 150}}


def test_schema_conversion_is_vertex_shaped():
    kind = next(s for s in TOOL_SCHEMAS if s['name'] == 'get_downtime_breakdown')['parameters']
    converted = gemini_schema(kind)
    assert 'additionalProperties' not in converted
    assert converted['properties']['kind'] == {'type': 'string', 'nullable': True, 'enum': ['Maintenance', 'Operator']}
    assert converted['properties']['shift'] == {'type': 'integer', 'enum': [1, 2, 3]}
    assert converted['required'] == ['shift']
    with pytest.raises(ProviderError):
        gemini_schema({'type': ['string', 'integer']})


def test_endpoint_host_global_and_regional():
    assert endpoint_host('global') == 'aiplatform.googleapis.com'
    assert endpoint_host('us-central1') == 'us-central1-aiplatform.googleapis.com'


def test_access_token_prefers_environment(monkeypatch):
    monkeypatch.setenv('GOOGLE_OAUTH_ACCESS_TOKEN', TOKEN)
    assert access_token() == TOKEN
    monkeypatch.delenv('GOOGLE_OAUTH_ACCESS_TOKEN')
    monkeypatch.setattr('investigator.gemini_provider.METADATA_TOKEN_URL', 'http://127.0.0.1:9/token')
    with pytest.raises(ProviderError, match='service identity'):
        access_token(timeout=0.2)


def test_loop_echoes_model_turns_and_keeps_token_out_of_payload(tmp_path):
    requests = []

    def send(payload, token, timeout):
        assert token == TOKEN
        assert TOKEN not in json.dumps(payload)
        assert payload['toolConfig']['functionCallingConfig']['mode'] == 'ANY'
        assert payload['generationConfig'] == {'maxOutputTokens': 1024, 'temperature': 0}
        for decl in payload['tools'][0]['functionDeclarations']:
            assert 'additionalProperties' not in json.dumps(decl)
        requests.append(payload)
        names = [d['name'] for d in payload['tools'][0]['functionDeclarations']]
        if names == ['write_narrative']:
            assert payload['toolConfig']['functionCallingConfig']['allowedFunctionNames'] == ['write_narrative']
            assert payload['systemInstruction']['parts'][0]['text'].startswith('Write a short superintendent')
            packet = json.loads(payload['contents'][-1]['parts'][0]['text'])['evidence']
            rid = packet[0]['result_id']
            return response('write_narrative', {'paragraphs': [{'kind': 'finding',
                'text': 'The leading recorded stop is {{0}}, totaling {{1}} minutes.',
                'facts': [{'result_id': rid, 'row': 0, 'field': 'category'}, {'result_id': rid, 'row': 0, 'field': 'down_minutes'}]}]})
        assert {'finish', 'cannot_answer'} <= set(names)
        assert payload['systemInstruction']['parts'][0]['text'].startswith('All records are synthetic')
        if len(requests) == 1:
            assert len(payload['contents']) == 1 and payload['contents'][0]['role'] == 'user'
            return response('get_downtime_breakdown', {'shift': 2, 'kind': None, 'group_by': 'reason'})
        # Every model turn is echoed verbatim, thought signature included, then the function response follows.
        assert payload['contents'][-2]['role'] == 'model'
        assert payload['contents'][-2]['parts'][-1]['thoughtSignature'].startswith('sig-')
        fr = payload['contents'][-1]['parts'][0]['functionResponse']
        data = fr['response']['result']
        assert fr['name'] == data['tool']
        if len(requests) == 2:
            assert 'trace' not in data['data'] and 'source_ids' not in data['data']['rows'][0]
            return response('render_chart', {'result_id': data['result_id']})
        first = json.loads(json.dumps(payload['contents'][2]['parts'][0]['functionResponse']['response']['result']))
        return response('finish', {'result_ids': [first['result_id']]})

    planner = GeminiPlanner(PROJECT, Budget(tmp_path / 'budget.db'), send, token=TOKEN)
    a = Analysis(generate())
    try:
        result = Agent(a, planner, max_seconds=60).ask('Please investigate the stopping causes', Context(a.day))
        assert result['status'] == 'complete'
        assert result['usage']['model_calls'] == 4
        assert result['usage']['model'] == MODEL
        assert result['usage']['reserved_usd'] == pytest.approx(.08)
        assert result['usage']['input_tokens'] == 400 and result['usage']['output_tokens'] == 200
        assert result['usage']['cost_usd'] == pytest.approx((400 * 0.25 + 200 * 1.50) / 1_000_000)
        assert result['narrative_status'] == 'available'
        assert 'Knife jam' in result['narrative'][0]['text'] and '12.00 minutes' in result['narrative'][0]['text']
        assert 'Gemini' in result['mode']
    finally:
        a.close()


def test_cannot_answer_and_bad_responses(tmp_path):
    a = Analysis(generate())
    try:
        planner = GeminiPlanner(PROJECT, Budget(tmp_path / 'b.db'), lambda *_: response('cannot_answer', {'reason': 'no email'}), token=TOKEN)
        result = Agent(a, planner, max_seconds=60).ask('Email this to the customer', Context(a.day))
        assert result['status'] == 'unavailable' and result['evidence_ids'] == []
        for bad in (response('get_shift_kpis', {'shift': 2}, finish='MAX_TOKENS'),
                    response('get_shift_kpis', {'shift': 2}, usage={'promptTokenCount': -1}),
                    {'candidates': []},
                    {'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': [{'text': 'no call'}]}}],
                     'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1}}):
            planner = GeminiPlanner(PROJECT, Budget(tmp_path / 'b.db'), lambda *_: bad, token=TOKEN)
            result = Agent(a, planner, max_seconds=60).ask('Show shift 2', Context(a.day))
            assert result['status'] == 'planner_error' and result['usage']['tool_calls'] == 0
    finally:
        a.close()


def test_budget_reserved_before_send_and_limits_hold(tmp_path):
    calls = []
    planner = GeminiPlanner(PROJECT, Budget(tmp_path / 'b.db', limit_usd=0.05), lambda *_: calls.append(1) or response('cannot_answer', {'reason': 'x'}), token=TOKEN)
    a = Analysis(generate())
    try:
        for _ in range(2):
            Agent(a, planner, max_seconds=60).ask('Anything', Context(a.day))
        with pytest.raises(ValueError, match='budget exhausted'):
            planner._request(planner._payload('s', [], []))
        assert len(calls) == 2
        planner.usage['model_calls'] = 10
        with pytest.raises(ValueError, match='limit reached'):
            planner.summarize('Summarize', [])
    finally:
        a.close()


@pytest.mark.parametrize('project', ['', 'has space', 'x' * 65, None])
def test_rejects_bad_project(project, tmp_path):
    with pytest.raises(ProviderError):
        GeminiPlanner(project, Budget(tmp_path / 'b.db'), token=TOKEN)


def test_server_switches_provider_by_environment(monkeypatch, tmp_path):
    from investigator import server
    from investigator.server import PublicDemo, Workbench
    monkeypatch.setenv('MODEL_BUDGET_PATH', str(tmp_path / 'ledger.sqlite3'))
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('LIVE_MODEL_PROVIDER', 'gemini')
    monkeypatch.delenv('GOOGLE_CLOUD_PROJECT', raising=False)
    assert Workbench(PublicDemo(('https://demo.example',), (), True)).live_model_available is False
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', PROJECT)
    assert Workbench(PublicDemo(('https://demo.example',), (), True)).live_model_available is True
    assert Workbench(PublicDemo(('https://demo.example',), (), False)).live_model_available is False
    assert server.live_model_name() == MODEL
    seen = []

    class Spy:
        mode = 'spy'; usage = {'model_calls': 0, 'cost_usd': 0}
        def __init__(self, project, budget, send=None, token=None, **_): seen.append(project)
        def next_step(self, context): return {'unavailable': 'spy'}
        def close(self): pass
    monkeypatch.setattr('investigator.gemini_provider.GeminiPlanner', Spy)
    payload = {'day': '2026-09-01', 'shift': 2, 'question': 'Why was second shift slow?', 'mode': 'openai'}
    Workbench(PublicDemo(('https://demo.example',), (), True)).ask(dict(payload), api_key='sk-visitor-ignored', visitor='b' * 32)
    assert seen == [PROJECT]
    monkeypatch.setenv('LIVE_MODEL_PROVIDER', 'bogus')
    with pytest.raises(ValueError, match='LIVE_MODEL_PROVIDER'):
        Workbench().live_model_available
