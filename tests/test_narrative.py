from copy import deepcopy
import json
import pytest
from investigator.narrative import render_narrative
from investigator.openai_provider import OpenAIPlanner, Budget
from investigator.analysis import Analysis
from investigator.fixture import generate
from investigator.agent import Agent, Context
from investigator.server import Workbench


PACKET=[{'result_id':'e1','shift':2,'coverage':'complete','rows':[{'down_minutes':12.5}]}]
VALID={'paragraphs':[{'kind':'finding','text':'Recorded downtime totals {{0}} minutes.',
                     'facts':[{'result_id':'e1','row':0,'field':'down_minutes'}]}]}


def test_values_are_substituted_from_evidence():
    p=render_narrative(VALID,PACKET)[0]
    assert p['text']=='Recorded downtime totals 12.50 minutes.'
    assert 'e1' in p['citations'][0] and 'down_minutes' in p['citations'][0]


@pytest.mark.parametrize('change',[
    {'text':'We can save 999 dollars.'}, {'text':'<script>run()</script>'},
    {'facts':[{'result_id':'foreign','row':0,'field':'down_minutes'}]},
    {'facts':[{'result_id':'e1','row':99,'field':'down_minutes'}]},
    {'facts':[{'result_id':'e1','row':0,'field':'profit'}]},
    {'facts':[{'result_id':'e1','row':True,'field':'down_minutes'}]},
    {'text':'Uncited assertion.','facts':[]}, {'kind':'execute'},
    {'text':'Too long '*100}, {'text':'Recorded downtime {{00}} minutes.'},
])
def test_bad_narratives_rejected(change):
    value=deepcopy(VALID);value['paragraphs'][0].update(change)
    with pytest.raises(ValueError):render_narrative(value,PACKET)


def test_incomplete_evidence_only_supports_limitations():
    packet=deepcopy(PACKET);packet[0]['coverage']='incomplete'
    with pytest.raises(ValueError):render_narrative(VALID,packet)
    value=deepcopy(VALID);value['paragraphs'][0]['kind']='limitation'
    assert render_narrative(value,packet)


def test_narrative_failure_keeps_charts_and_evidence(tmp_path):
    def send(payload,key,timeout):
        if payload['tools'][0]['name']=='write_narrative':raise TimeoutError('private upstream error')
        outputs=[i for i in payload['input'] if i.get('type')=='function_call_output']
        name='finish' if outputs else 'get_shift_overview'
        args={'result_ids':[json.loads(outputs[0]['output'])['result_id']]} if outputs else {'shift':2}
        return {'status':'completed','usage':{'input_tokens':100,'output_tokens':50},'output':[
            {'type':'function_call','id':'fc1','call_id':'call1','name':name,'arguments':json.dumps(args)}]}
    planner=OpenAIPlanner('sk-test-not-real',Budget(tmp_path/'budget.db'),send)
    a=Analysis(generate())
    try:
        result=Agent(a,planner).ask('Summarize this shift with charts',Context(a.day,scope='shift'))
        assert result['status']=='complete' and result['narrative_status']=='unavailable'
        assert len(result['charts'])==3 and result['evidence_ids']
        assert result['usage']['model_calls']==3
        assert 'private upstream error' not in json.dumps(result)
        assert any('narrative is unavailable' in p for p in result['sections'])
    finally:planner.close();a.close()


def test_summary_all_shifts_and_followup_history():
    app=Workbench()
    payload={'day':'2026-09-14','shift':2,'scope':'day','question':'Summarize performance with charts'}
    try:
        result=app.ask(payload)
        assert result['selected_shifts']==[1,2,3]
        assert len(result['charts'])==9 and result['usage']['model_calls']==0
        for chart in result['charts']:
            source=next(e for e in result['trace'] if e['result_id']==chart['source_result_id'])
            assert any(c['rows']==chart['rows'] for c in source['data']['components'])
        follow=app.ask({**payload,'question':'Show speed','session_id':result['session_id']})
        assert len(follow['context']['history'])==2 and follow['context']['shift']==2
    finally:app.close()


def test_financial_objective_is_explicitly_partial():
    a=Analysis(generate())
    try:
        result=Agent(a).ask('How can we improve profit on this shift?',Context(a.day,scope='shift'))
        assert result['status']=='partial'
        assert any('financial objective is unavailable' in p for p in result['sections'])
    finally:a.close()


def test_explicit_chart_cannot_silently_finish_without_one():
    class Planner:
        def next_step(self,ctx):
            if not ctx['results']:return {'tool':'get_shift_kpis','arguments':{'shift':2}}
            return {'final':[ctx['results'][0]['result_id']]}
    a=Analysis(generate())
    try:
        result=Agent(a,Planner()).ask('Chart downtime',Context(a.day,scope='shift'))
        assert result['status']=='partial' and not result['charts']
    finally:a.close()


def test_narrative_call_respects_shared_call_cap(tmp_path):
    planner=OpenAIPlanner('sk-test-not-real',Budget(tmp_path/'budget.db'),lambda *args:pytest.fail('No network at cap'))
    planner.usage['model_calls']=10
    try:
        with pytest.raises(ValueError,match='limit reached'):planner.summarize('Summarize',[])
        assert planner.usage['reserved_usd']==0
    finally:planner.close()


@pytest.mark.parametrize('period',['week','month','accounting'])
def test_period_exports_are_free(period):
    app=Workbench()
    try:
        result=app.ask({'day':'2026-09-29','shift':2,'scope':'day','period':period,'question':'Build report'})
        assert result['usage']['model_calls']==0
        pdf=app.pdf({'session_id':result['session_id'],'run_id':result['run_id']})
        assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF')
    finally:app.close()
