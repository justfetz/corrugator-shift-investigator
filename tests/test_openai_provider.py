import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from investigator.openai_provider import Budget, OpenAIPlanner, MODEL
from investigator.agent import Agent,Context
from investigator.analysis import Analysis
from investigator.fixture import generate
from investigator.server import Workbench

KEY='sk-fake-test-key-not-a-real-credential'


def response(name,args,call_id='call-1',status='completed'):
    return {'status':status,'usage':{'input_tokens':100,'output_tokens':50},
            'output':[{'type':'function_call','id':'fc-'+call_id,'call_id':call_id,'name':name,'arguments':json.dumps(args)}]}


def test_responses_loop_and_no_key_in_payload(tmp_path):
    requests=[]
    def send(payload,key,timeout):
        assert key==KEY
        assert KEY not in json.dumps(payload)
        assert payload['model']==MODEL and payload['store'] is False
        assert payload['parallel_tool_calls'] is False
        assert payload['max_output_tokens']==1024
        for tool in payload['tools']:
            assert tool['strict'] is True
            assert tool['parameters']['additionalProperties'] is False
            assert set(tool['parameters']['required'])==set(tool['parameters']['properties'])
        requests.append(payload)
        if payload['tools'][0]['name']=='write_narrative':
            packet=json.loads(payload['input'][-1]['content'])['evidence']
            rid=packet[0]['result_id']
            return response('write_narrative',{'paragraphs':[{'kind':'finding',
                'text':'The leading recorded stop is {{0}}, totaling {{1}} minutes.',
                'facts':[{'result_id':rid,'row':0,'field':'category'},
                         {'result_id':rid,'row':0,'field':'down_minutes'}]}]})
        if len(requests)==1:
            return response('get_downtime_breakdown',{'shift':2,'kind':None,'group_by':'reason'})
        outputs=[i for i in payload['input'] if i.get('type')=='function_call_output']
        data=json.loads(outputs[-1]['output'])
        if len(requests)==2:
            assert outputs[-1]['call_id']=='call-1'
            assert 'trace' not in data['data']
            assert 'source_ids' not in data['data']['rows'][0]
            return response('render_chart',{'result_id':data['result_id']},'call-2')
        first=json.loads(outputs[0]['output'])
        return response('finish',{'result_ids':[first['result_id']]},'call-3')
    budget=Budget(tmp_path/'budget.db')
    planner=OpenAIPlanner(KEY,budget,send)
    a=Analysis(generate())
    try:
        result=Agent(a,planner,max_seconds=60).ask('Please investigate the stopping causes',Context(a.day))
        assert result['status']=='complete'
        assert result['usage']['model_calls']==4
        assert result['usage']['reserved_usd']==pytest.approx(.08)
        assert result['narrative_status']=='available'
        assert 'Knife jam' in result['narrative'][0]['text']
        assert '12.00 minutes' in result['narrative'][0]['text']
        assert len(result['charts'])==1
        assert any('Knife jam: 12 min' in s for s in result['sections'])
        assert KEY not in json.dumps(result)
    finally:
        planner.close();a.close()
    assert planner._key is None


def test_budget_persists_and_failed_requests_stay_reserved(tmp_path):
    path=tmp_path/'budget.db'
    budget=Budget(path,limit_usd=.02)
    def fail(*args):raise TimeoutError('network')
    planner=OpenAIPlanner(KEY,budget,fail)
    a=Analysis(generate())
    try:
        result=Agent(a,planner).ask('Show speed',Context(a.day))
        assert result['status']=='planner_error'
        assert result['usage']['model_calls']==1
        with pytest.raises(ValueError,match='budget exhausted'):
            Budget(path,limit_usd=.02).reserve()
    finally:planner.close();a.close()


def test_atomic_budget_reservations(tmp_path):
    budget=Budget(tmp_path/'budget.db',limit_usd=.02)
    def attempt(_):
        try:budget.reserve();return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt,range(4)))==1


def test_incomplete_response_executes_no_tools(tmp_path):
    planner=OpenAIPlanner(KEY,Budget(tmp_path/'budget.db'),lambda *args:response('get_shift_kpis',{'shift':2},status='incomplete'))
    a=Analysis(generate())
    try:
        result=Agent(a,planner).ask('Show speed',Context(a.day))
        assert result['status']=='planner_error' and not result['trace']
    finally:planner.close();a.close()


def test_live_without_key_never_falls_back(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    app=Workbench()
    try:
        with pytest.raises(ValueError,match='needs your key'):
            app.ask({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'openai'})
    finally:app.close()


def test_model_call_limit_before_network(tmp_path):
    planner=OpenAIPlanner(KEY,Budget(tmp_path/'budget.db'),lambda *args:pytest.fail('Network should not run'))
    planner.usage['model_calls']=10
    with pytest.raises(ValueError,match='limit reached'):
        planner.next_step({})
    planner.close()


def test_bad_key_not_echoed(tmp_path):
    with pytest.raises(ValueError) as exc:
        OpenAIPlanner('invalid-secret-value',Budget(tmp_path/'budget.db'))
    assert 'invalid-secret-value' not in str(exc.value)
