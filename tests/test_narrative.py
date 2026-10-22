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
    {'facts':[{'result_id':'e1','row':99,'field':'down_minutes'}]},   # beyond the preview rows
    {'facts':[{'result_id':'e1','row':0,'field':'profit'}]},
    {'facts':[{'result_id':'e1','row':True,'field':'down_minutes'}]},
    {'text':'Uncited assertion.','facts':[]}, {'kind':'execute'},
    {'text':'Too long '*100}, {'text':'Recorded downtime {{00}} minutes.'},
])
def test_bad_narratives_rejected(change):
    value=deepcopy(VALID);value['paragraphs'][0].update(change)
    with pytest.raises(ValueError):render_narrative(value,PACKET)


WIDE=[{'result_id':'e1','shift':2,'coverage':'complete','rows':[{'a':1.0,'b':2.0,'c':3.0,'d':4.0,'e':5.0,'f':6.0,'g':7.0}]}]


def _facts(*fields):
    return [{'result_id':'e1','row':0,'field':f} for f in fields]


def test_five_placed_facts_render_and_seven_are_too_many():
    five={'paragraphs':[{'kind':'hypothesis','text':'Uncertain: {{0}} {{1}} {{2}} {{3}} {{4}} units.','facts':_facts('a','b','c','d','e')}]}
    assert render_narrative(five,WIDE)[0]['text']=='Uncertain: 1.00 2.00 3.00 4.00 5.00 units.'
    seven={'paragraphs':[{'kind':'finding','text':'{{0}} {{1}} {{2}} {{3}} {{4}} {{5}} {{6}}','facts':_facts('a','b','c','d','e','f','g')}]}
    with pytest.raises(ValueError,match='size limit'):render_narrative(seven,WIDE)


def test_unplaced_trailing_facts_are_dropped_not_fatal():
    value={'paragraphs':[{'kind':'check','text':'Ask the crew about the {{0}} percent operator time.','facts':_facts('a','b','c')}]}
    p=render_narrative(value,WIDE)[0]
    assert p['text']=='Ask the crew about the 1.00 percent operator time.' and len(p['citations'])==1
    # A gap or a placeholder beyond the facts still fails.
    for text in ('{{0}} and {{2}}','{{0}} {{1}} {{2}} {{3}}'):
        bad={'paragraphs':[{'kind':'finding','text':text,'facts':_facts('a','b','c')}]}
        with pytest.raises(ValueError):render_narrative(bad,WIDE)


def test_shift_is_answered_from_result_metadata():
    value={'paragraphs':[{'kind':'finding','text':'Shift {{0}} recorded {{1}} minutes of downtime.',
                         'facts':[{'result_id':'e1','row':0,'field':'shift'},{'result_id':'e1','row':0,'field':'down_minutes'}]}]}
    p=render_narrative(value,PACKET)[0]
    assert p['text']=='Shift 2 recorded 12.50 minutes of downtime.' and p['citations'][0].startswith('Shift 2, shift, e1')
    packet=deepcopy(PACKET);packet[0]['shift']=None   # an all-shifts result has no shift to name
    with pytest.raises(ValueError,match='Unknown narrative field'):render_narrative(value,packet)


def test_prose_without_placeholders_keeps_its_citations():
    """A paragraph that names a ranking or a category in words, with no digits, keeps every
    cited row as a pointer to the evidence; an uncited non-limitation paragraph still fails."""
    value={'paragraphs':[{'kind':'finding','text':'Order change led the recorded stops, ahead of raw material and the missed splice.','facts':_facts('a','b','c')}]}
    p=render_narrative(value,WIDE)[0]
    assert p['text'].startswith('Order change led') and len(p['citations'])==3
    uncited={'paragraphs':[{'kind':'finding','text':'Uncited assertion.','facts':[]}]}
    with pytest.raises(ValueError,match='cited evidence'):render_narrative(uncited,WIDE)
    with_digits={'paragraphs':[{'kind':'finding','text':'Order change cost 12 minutes.','facts':_facts('a')}]}
    with pytest.raises(ValueError,match='placeholders'):render_narrative(with_digits,WIDE)


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


def test_period_findings_rest_on_the_rows_that_exist():
    """A trend reaching before the dataset and a month whose previous month is unavailable are
    'partial' envelopes, but their rows are exact: findings may cite them, and the packet discloses
    the missing weeks / the previous window's coverage for the writer to state as a limitation."""
    from investigator.narrative import evidence_packet
    from investigator.record_store import default_store
    store=default_store()
    trend=store.get_trend('speed_all_fpm',weeks=8,anchor='2026-09-14',shift=2)
    august=store.get_period_kpis({'period':'month','anchor':'2026-08-14'})
    day=store.day_analysis('2026-09-14')
    try:
        assert trend['coverage']=='partial' and trend['missing_weeks']==['2026-07-27'] and august['coverage']=='partial'
        packet=evidence_packet([{'result_id':'t:1','tool':'get_trend','ok':True,'data':trend},
                                {'result_id':'a:1','tool':'get_period_kpis','ok':True,'data':august}])
        assert packet[0]['coverage']=='complete' and packet[0]['missing_weeks']==['2026-07-27']
        assert packet[1]['coverage']=='complete' and packet[1]['previous_window_coverage']=='unavailable'
        rendered=render_narrative({'paragraphs':[
            {'kind':'finding','text':'Speed reached {{0}} ft/min in the week of {{1}}.','facts':[{'result_id':'t:1','row':5,'field':'value'},{'result_id':'t:1','row':5,'field':'week_start'}]},
            {'kind':'finding','text':'August volume was {{0}} MSF.','facts':[{'result_id':'a:1','row':0,'field':'current'}]}]},packet)
        assert rendered[0]['text'].startswith('Speed reached 621.20 ft/min in the week of 2026-09-07')
        # a daily result with an excluded record stays partial and still bars findings
        corrupt=store.day_analysis('2026-09-14',corrupt=True)
        try:
            kpis=corrupt.get_shift_kpis(2)
            assert kpis['coverage']!='complete'
            bad=evidence_packet([{'result_id':'k:1','tool':'get_shift_kpis','ok':True,'data':kpis}])
            with pytest.raises(ValueError,match='Incomplete evidence'):
                render_narrative({'paragraphs':[{'kind':'finding','text':'Speed was {{0}} ft/min.','facts':[{'result_id':'k:1','row':0,'field':'observed_shift_fpm'}]}]},bad)
        finally:
            corrupt.close()
    finally:
        day.close()


def test_null_field_renders_as_the_word_unavailable():
    packet=[{'result_id':'o1','shift':2,'coverage':'complete','rows':[{'order_id':'SO-1','requested_sheets':None,'produced_sheets':120}]}]
    value={'paragraphs':[{'kind':'finding','text':'Order {{0}} produced {{1}} sheets against a requested quantity of {{2}}.',
                         'facts':[{'result_id':'o1','row':0,'field':'order_id'},{'result_id':'o1','row':0,'field':'produced_sheets'},{'result_id':'o1','row':0,'field':'requested_sheets'}]}]}
    p=render_narrative(value,packet)[0]
    assert p['text']=='Order SO-1 produced 120 sheets against a requested quantity of unavailable.'
    beyond={'paragraphs':[{'kind':'finding','text':'{{0}}','facts':[{'result_id':'o1','row':12,'field':'order_id'}]}]}
    with pytest.raises(ValueError,match='only the 1 preview rows'):render_narrative(beyond,packet)
