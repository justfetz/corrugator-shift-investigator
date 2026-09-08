from copy import deepcopy
import json
import pytest
from investigator.agent import Agent, Context, OfflinePlanner
from investigator.analysis import Analysis
from investigator.fixture import generate

@pytest.fixture
def analysis():
    a=Analysis(generate())
    yield a
    a.close()


def test_full_loop_and_followup(analysis):
    context=Context(analysis.day)
    agent=Agent(analysis)
    first=agent.ask('Why was second shift slow? Chart downtime by reason.',context)
    assert first['status']=='complete'
    assert len(first['charts'])==2
    assert first['usage']['model_calls']==0
    follow=agent.ask('Compare that with first shift',context)
    assert follow['selected_shifts']==[2,1]
    assert follow['production_day']==analysis.day
    assert follow['context']['shift']==2  # The selected control remains authoritative.
    assert len(follow['context']['history'])==2
    assert any('Shift 1' in line for line in follow['sections'])
    assert any('Shift 2' in line for line in follow['sections'])


def test_all_shifts_complete(analysis):
    result=Agent(analysis).ask('Compare all shifts',Context(analysis.day))
    assert result['status']=='complete'
    assert len(result['charts'])==6


def test_chart_rows_exactly_match_evidence(analysis):
    result=Agent(analysis).ask('Show downtime by equipment',Context(analysis.day))
    for chart in result['charts']:
        source=next(t for t in result['trace'] if t['result_id']==chart['source_result_id'])
        assert source['data']['rows']==chart['rows']
        assert source['arguments']['group_by']=='place'


def test_filter_not_silently_maintenance(analysis):
    result=Agent(analysis).ask('Was it maintenance or operator downtime?',Context(analysis.day))
    calls=[t for t in result['trace'] if t['tool']=='get_downtime_breakdown']
    assert calls[0]['arguments']['kind'] is None
    assert {r['category'] for r in calls[0]['data']['rows']}=={'Maintenance','Operator'}


@pytest.mark.parametrize('text',['What is the weather?', 'Compare last week', 'Show shift 2 on 2025-01-01', '', 'x'*2001],ids=['unsupported','relative-date','wrong-date','empty','size'])
def test_unknown_requests_fail_without_mutating_context(analysis,text):
    context=Context(analysis.day)
    with pytest.raises(ValueError):Agent(analysis).ask(text,context)
    assert context.history==[]


class RepeatPlanner:
    def __init__(self, action):self.action=action
    def next_step(self, context):return deepcopy(self.action)


def test_forbidden_tool_is_bounded(analysis):
    planner=RepeatPlanner({'tool':'execute_shell','arguments':{'command':'anything'}})
    result=Agent(analysis,planner,max_calls=2).ask('Show shift 2',Context(analysis.day))
    assert result['status']=='budget_exhausted'
    assert len(result['trace'])==2
    assert all(not t['ok'] for t in result['trace'])
    assert result['charts']==[]


def test_cannot_chart_foreign_result(analysis):
    planner=RepeatPlanner({'tool':'render_chart','arguments':{'result_id':'other-session'}})
    result=Agent(analysis,planner,max_calls=1).ask('Show shift 2',Context(analysis.day))
    assert not result['trace'][0]['ok']
    assert result['charts']==[]


def test_cannot_fabricate_final_evidence(analysis):
    result=Agent(analysis,RepeatPlanner({'final':['invented']})).ask('Show shift 2',Context(analysis.day))
    assert result['status']=='planner_error'
    assert not result['evidence_ids']


def test_incomplete_data_not_ranked():
    a=Analysis(generate(corrupt=True))
    try:
        result=Agent(a).ask('Why was second shift slow?',Context(a.day))
        assert any('Do not rank' in s for s in result['sections'])
        assert not any('ft/min including' in s for s in result['sections'])
        assert all(c['coverage']=='incomplete' for c in result['charts'])
    finally:a.close()


def test_history_bounded(analysis):
    context=Context(analysis.day)
    agent=Agent(analysis)
    for _ in range(6):agent.ask('Show speed',context)
    assert len(context.history)==4


def test_deadline_stops_before_planner(analysis):
    result=Agent(analysis,max_seconds=0).ask('Show shift 2',Context(analysis.day))
    assert result['status']=='timeout'
    assert result['usage']['tool_calls']==0


def test_quality_tool_and_wet_end_math(analysis):
    from investigator.fixture import REJECT_REASONS
    assert {r['reason'] for r in analysis.get_quality_breakdown(1)['rows']}==set(REJECT_REASONS)
    r=analysis.get_wet_end_performance(2)['rows'][0]
    assert r['elapsed_minutes']>0
    assert r['actual_fpm']==pytest.approx(r['lineal_ft']/r['elapsed_minutes'])
    assert r['target_fpm'] in (500,800,850,900)
    assert r['source_ids']

def test_context_resets_when_dataset_changes():
    clean=Analysis(generate())
    bad=Analysis(generate(corrupt=True))
    try:
        context=Context(clean.day)
        Agent(clean).ask('Show speed',context)
        result=Agent(bad).ask('Show speed',context)
        assert len(result['context']['history'])==1
        assert result['context']['dataset_version']==bad.version
    finally:
        clean.close();bad.close()
