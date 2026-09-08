"""Read-only review probes: synthetic data, scripted planners, no provider calls.

Run from repo root with .venv/Scripts/python.exe. Prints observations rather
than treating a known bug as a passing regression test. No files are exported.
"""
import json
from investigator.agent import Agent, Context
from investigator.analysis import Analysis
from investigator.fixture import generate
from investigator.server import Workbench


def emit(name, **values):
    print(json.dumps({'probe': name, **values}))


class KpiThenFinish:
    def next_step(self, context):
        if not context['results']:
            return {'tool': 'get_shift_kpis', 'arguments': {'shift': 2}}
        return {'final': [context['results'][0]['result_id']]}


class Unavailable:
    def next_step(self, context):
        return {'unavailable': 'Select a measured objective such as downtime or waste.'}


app = Workbench()
try:
    payload = {'day': '2026-09-14', 'shift': 2, 'scope': 'day', 'question': 'Compare all shifts'}
    first = app.ask(payload)
    follow = app.ask({**payload, 'session_id': first['session_id'], 'question': 'Show speed'})
    emit('comparison_history', first_focus=first['context']['shift'],
         follow_history_length=len(follow['context']['history']),
         follow_questions=[r['question'] for r in follow['context']['history']])
    for period in ('week', 'month', 'accounting'):
        result = app.ask({'day': '2026-09-14', 'shift': 2, 'scope': 'shift',
                          'question': 'Build report', 'period': period})
        try:
            pdf = app.pdf({'session_id': result['session_id'], 'run_id': result['run_id']})
            emit('period_pdf', period=period, valid_header=pdf.startswith(b'%PDF-'))
        except Exception as exc:
            emit('period_pdf', period=period, error=type(exc).__name__, detail=str(exc))
    try:
        app.ask({'day': '2026-09-14', 'shift': 2, 'question': 'Build report',
                 'period': 'month', 'mode': 'openai'})
    except ValueError as exc:
        emit('period_live_rejected', detail=str(exc))
finally:
    app.close()

analysis = Analysis(generate())
try:
    result = Agent(analysis, KpiThenFinish()).ask(
        'Chart downtime by reason and explain how we can be better.',
        Context(analysis.day, 2, scope='shift'))
    emit('completion_without_requested_chart', status=result['status'],
         charts=len(result['charts']), tools=[r['tool'] for r in result['trace']])
    result = Agent(analysis, Unavailable()).ask('How can we be better?', Context(analysis.day))
    emit('unavailable_guidance', status=result['status'], sections=result['sections'])
    for question in ('How can we be better?', 'How can we improve profit on this shift?'):
        try:
            result = Agent(analysis).ask(question, Context(analysis.day))
            emit('offline_broad_question', question=question, status=result['status'],
                 tools=[r['tool'] for r in result['trace']])
        except ValueError as exc:
            emit('offline_broad_question', question=question, error=str(exc))
finally:
    analysis.close()
