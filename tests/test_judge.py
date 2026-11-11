"""The narrative judge of issue #25: a DeepEval G-Eval rubric over the analyst's prose, live only, and the owner's
grades it is calibrated against. Nothing here calls a model: the judge model's transport is a stand-in. `judge()`,
`geval_metrics()` and `deepeval_model()` run in CI with DeepEval stubbed (it is the optional `judge` extra, not in CI);
one more test runs the real G-Eval and skips when the extra is not installed.

Since the rolling year (#91) every live recording is stale and replays nothing, so the narratives here are the
scripted analyst rounds of the golden set and narratives built in the test; nothing assumes the recordings replay."""
import asyncio
import importlib.util
import json
import shutil
from pathlib import Path
from threading import Thread, Lock
import http.client

import pytest

from investigator import judge as judging
from investigator.anthropic_provider import PRICING
from investigator.evalboard import EvalBoard
from investigator.openai_provider import ProviderError
from investigator.server import PublicDemo, make_server

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('golden_run_judge', ROOT / 'evals' / 'run.py')
golden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden)
CASES = {c['id']: c for c in golden.load_cases()}
SCRIPTED = [c['id'] for c in CASES.values() if c.get('plan') is not None and c.get('narrative')]
RUN = 'c' * 32


@pytest.fixture(scope='module', autouse=True)
def datasets():
    yield
    golden.close_datasets()


def scripted(case_id):
    """A scripted analyst round of the golden set, run offline: the plan, the tools and the stream validator, no model."""
    case = CASES[case_id]
    outcome, failures, _, _, results = golden.run_case_detail(case, None, live=False, record=False)
    assert outcome == 'pass', failures
    return case, results


def written(*paragraphs, rows=None):
    """One answer whose narrative is `paragraphs` ((kind, text, [(field, row)]) each), written from one KPI result."""
    rows = rows or [{'observed_shift_fpm': 616.1234, 'actual_fpm': 467.45, 'target_fpm': 500.0}]
    return {'narrative_status': 'available', 'production_day': '2026-09-14', 'selected_shifts': [2], 'context': {'scope': 'shift'},
            'trace': [{'result_id': f'{RUN}:1', 'tool': 'get_shift_kpis', 'arguments': {'shift': 2}, 'ok': True,
                       'data': {'shift': 2, 'coverage': 'complete', 'rows': rows}}],
            'narrative': [{'kind': kind, 'text': text, 'citations': [f'Shift 2, {field}, {RUN}:1, row {row}' for field, row in cites]}
                          for kind, text, cites in paragraphs]}


def anthropic_reply(text, input_tokens=1000, output_tokens=40, stop='end_turn'):
    return 200, json.dumps({'content': [{'type': 'thinking', 'thinking': 'x'}, {'type': 'text', 'text': text}], 'stop_reason': stop,
                            'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens}}).encode()


# ---------------------------------------------------------------- the rubric

def test_the_rubric_is_five_lines_each_scored_on_its_own_and_documented():
    assert judging.CRITERIA == ('grounded', 'recorded_not_proven', 'prescribes_nothing', 'unavailable_not_zero', 'comparisons_consistent')
    for line in judging.RUBRIC:
        assert line['name'] and line['criterion'] and len(line['steps']) >= 3
        assert any('Score 1' in step for step in line['steps']) and any('Score 0' in step for step in line['steps'])
    # line 5 is the judge's on purpose: actual against target is the comparison the rules in narrative.py miss (#79)
    assert any('against a target of 500.00 ft/min' in step for step in judging.RUBRIC[4]['steps'])
    digest = judging.rubric_digest()
    assert len(digest) == 12 and digest == judging.rubric_digest()
    readme = (ROOT / 'evals' / 'README.md').read_text()
    section = readme.split('## The narrative judge', 1)[1]
    for line in judging.RUBRIC:   # the README states every line's name, key and criterion, and how it is run and calibrated
        assert line['name'] in section and f'`{line["key"]}`' in section and line['criterion'] in section
    for needle in ('--judge', 'judge_calibration.json', 'judge_last_run.json', 'kappa', 'cost_per_case_usd', 'DEEPEVAL_JUDGE', '/evals/grade',
                   '--judge-project', judging.RERECORD_NOTE):
        assert needle in section, needle


# ---------------------------------------------------------------- judge cases

def test_a_scripted_analyst_round_becomes_one_judge_case_with_its_evidence():
    case, results = scripted('analyst-fastest-shift-consistent')
    cases = judging.narrative_cases(case['id'], [case['question']], results, 'scripted plan')
    assert [c['id'] for c in cases] == ['analyst-fastest-shift-consistent:1']
    one = cases[0]
    fpm = results[0]['trace'][0]['data']['rows'][0]['observed_shift_fpm']
    assert one['input'].startswith(f'Question: {case["question"]}\nScope: production day {results[0]["production_day"]}, all shifts')
    assert one['narrative'][0].startswith(f'Finding: Shift 1 ran fastest at {fpm:.2f} ft/min') and one['output'] == '\n'.join(one['narrative'])
    assert '(cites r1 row 0 observed_shift_fpm' in one['narrative'][0]                       # citations in short form
    assert one['context'][0].startswith('r1 get_shift_kpis(shift=1) -> {"rows":[{')            # the cited rows first
    assert f'"observed_shift_fpm":{round(fpm, 4)}' in one['context'][0]                        # rounded, still the value cited
    assert 'source_ids' not in ''.join(one['context']) and 'dataset_version' not in ''.join(one['context'])
    assert sum(len(c) for c in one['context']) <= judging.MAX_CONTEXT_CHARS
    again = judging.narrative_cases(case['id'], [case['question']], results, 'scripted plan')[0]
    assert again['digest'] == one['digest']                                                     # the same words, the same digest
    results[0]['narrative'][0]['text'] += ' Changed.'
    assert judging.narrative_cases('x', [case['question']], results, 'x')[0]['digest'] != one['digest']
    # A conversation: one case per turn that wrote a narrative, each told the questions before it.
    turns = judging.narrative_cases('talk', ['First?', 'Second?'], [written(('finding', 'One.', [])), written(('finding', 'Two.', []))], 'x')
    assert [(c['id'], c['turn']) for c in turns] == [('talk:1', 1), ('talk:2', 2)]
    assert 'Earlier questions' not in turns[0]['input'] and 'Earlier questions in this conversation: First?' in turns[1]['input']


def test_an_unavailable_result_and_a_null_field_reach_the_judge_as_unavailable():
    trace = [{'result_id': 'a' * 32 + ':1', 'tool': 'get_trend', 'arguments': {'metric': 'trim_pct', 'weeks': 8}, 'ok': False,
              'error': {'message': 'The window starts before the record store.'}},
             {'result_id': 'a' * 32 + ':2', 'tool': 'get_period_kpis', 'arguments': {'shift': None}, 'ok': True,
              'data': {'shift': None, 'coverage': 'partial', 'missing_weeks': ['2026-07-27'], 'rows': [{'metric': 'trim_pct', 'previous': None, 'current': 3.14159}]}},
             {'result_id': 'a' * 32 + ':3', 'tool': 'render_chart', 'arguments': {}, 'ok': True, 'data': {'rows': []}}]
    lines = judging.evidence_lines(trace)
    assert lines[0] == 'r1 get_trend(metric="trim_pct", weeks=8) -> unavailable: The window starts before the record store.'
    assert '"previous":null' in lines[1] and '"current":3.1416' in lines[1] and '"coverage":"partial"' in lines[1] and '"missing_weeks":["2026-07-27"]' in lines[1]
    assert len(lines) == 2                                                              # a chart is output, not evidence
    result = {'narrative_status': 'available', 'production_day': '2026-09-14', 'selected_shifts': [], 'context': {'scope': 'day', 'period': 'week'},
              'trace': trace, 'narrative': [{'kind': 'limitation', 'text': 'Trim for the previous window is unavailable.', 'citations': []}]}
    (one,) = judging.narrative_cases('t', ['Trim trend?'], [result], 'scripted plan')
    assert one['scope'] == 'production day 2026-09-14, all shifts, week window' and one['narrative'] == ['Limitation: Trim for the previous window is unavailable.']
    assert judging.narrative_cases('t', ['q'], [{**result, 'narrative_status': 'unavailable'}], 'x') == []   # no narrative, nothing to judge


def test_the_runner_and_the_grading_page_name_the_same_narratives_and_say_when_the_target_waits_on_re_recording():
    board = EvalBoard(ROOT / 'evals')
    found = board.narratives(Lock())
    ids = [n['id'] for n in found]
    assert len(ids) == len(set(ids)) and {n['source'] for n in found} <= {'recorded anthropic', 'scripted plan'}
    theirs = {n['id']: n['digest'] for n in found}
    for case_id in SCRIPTED:                                                            # every scripted round is gradable, recorded or not
        case, results = scripted(case_id)
        mine = judging.narrative_cases(case_id, case.get('turns') or [case['question']], results, 'scripted plan')
        assert mine and all(theirs[c['id']] == c['digest'] for c in mine)              # a grade and a judged score meet by id and digest
    # While a live recording is stale (since #91 all of them), fewer than 20 narratives can be graded, and every place
    # that states the target says it waits on re-recording (#71); once they are re-recorded the note goes.
    stale = any((r.get('recording') or {}).get('stale') for c in board.board()['cases'] for r in c['runs'])
    assert (judging.RERECORD_NOTE in board.board()['judge']['target_text']) == stale
    assert (judging.RERECORD_NOTE in board.grading(Lock())['target_text']) == (len(found) < judging.CALIBRATION_TARGET)
    assert judging.target_text() == '20' and judging.target_text(True) == '20 once the live recordings are re-recorded (#71)'


# ---------------------------------------------------------------- calibration

def test_cohen_kappa_against_hand_worked_values():
    assert judging.cohen_kappa([(1, 1), (1, 0), (0, 0), (0, 0)]) == 0.5      # po .75, pe .5
    assert judging.cohen_kappa([(1, 1), (0, 0), (1, 1), (0, 0)]) == 1.0
    assert judging.cohen_kappa([(1, 0), (0, 1), (1, 0), (0, 1)]) == -1.0
    assert judging.cohen_kappa([(1, 1), (1, 0), (0, 1), (0, 0)]) == 0.0      # agreement no better than chance
    assert judging.cohen_kappa([(1, 1)] * 5) is None                         # one class only: undefined, not perfect
    assert judging.cohen_kappa([]) is None


def grade(id_, digest, verdict='good', note='', **lines):
    return {'id': id_, 'digest': digest, 'verdict': verdict, **{k: lines.get(k, True) for k in judging.CRITERIA}, 'note': note}


def scores(**changed):
    return {k: changed.get(k, 1) for k in judging.CRITERIA}


def test_agreement_is_per_line_over_the_same_text_only():
    judged = [{'id': f'n{i}', 'digest': f'd{i}', 'scores': scores(prescribes_nothing=i % 2)} for i in range(4)]
    judged.append({'id': 'n4', 'digest': 'd4', 'scores': scores(grounded=None)})
    judged.append({'id': 'n5', 'digest': 'd5', 'scores': {k: None for k in judging.CRITERIA}})       # a bound stopped the run before it
    grades = [grade('n0', 'd0', 'bad', prescribes_nothing=False), grade('n1', 'd1'), grade('n2', 'd2'),   # n2: judge 0, owner 1
              grade('n3', 'old-text'), grade('n9', 'd9'), grade('n4', 'd4'), grade('n5', 'd5')]
    found = judging.agreement(judged, grades)
    assert (found['graded'], found['judged'], found['matched'], found['unmatched']) == (7, 5, 4, 3)
    assert found['criteria']['prescribes_nothing'] == {'n': 4, 'agree': 3, 'pct': 75.0, 'kappa': 0.5, 'ok': False}
    assert found['criteria']['grounded']['n'] == 3                    # the judge could not score n4's first line: left out of that line only
    assert found['criteria']['comparisons_consistent']['n'] == 4
    assert found['overall']['n'] == 3 and found['overall']['agree'] == 2
    text = judging.agreement_text(found)
    assert 'prescribes_nothing 3/4 75% (kappa 0.50: BELOW 90%)' in text and 'comparisons_consistent 4/4 100%' in text
    assert text.endswith('[4 graded narratives of 20; 3 grades set aside: changed text or not judged]')
    # Before any judged run, a saved grade is waiting, not "not matching": the text says which, and the target its note.
    assert judging.agreement_text(judging.agreement([], [grade('n1', 'd1')])) == \
        '1 owner grades (target 20); no judged run to compare them with, so no agreement measured yet'
    assert judging.agreement_text(judging.agreement(judged, [grade('n3', 'old-text')])) == \
        '1 owner grades (target 20), none of the text the judge scored; no agreement measured yet'
    assert judging.agreement_text(judging.agreement(judged, []), rerecord=True) == \
        '0 owner grades (target 20 once the live recordings are re-recorded (#71)); no agreement measured yet'


def test_a_grade_is_checked_against_the_text_the_page_showed_and_saved_whole(tmp_path):
    shown = {'n1': 'abc', 'n2': 'def'}
    good = grade('n1', 'abc')
    for bad, message in ((dict(good, id='nope'), 'No such'), (dict(good, digest='zzz'), 'changed'), (dict(good, verdict='meh'), 'verdict'),
                         (dict(good, grounded='yes'), 'true'), (dict(good, verdict='bad'), 'Say why'), (dict(good, note='x' * 501), 'at most'),
                         (dict(good, extra=1), 'optional note'), ({k: v for k, v in good.items() if k != 'comparisons_consistent'}, 'optional note'),
                         ([], 'JSON object'), (good, 'Say why')):   # "good/bad with a reason": a good grade carries one too
        with pytest.raises(ValueError, match=message):
            judging.validate_grade(bad, shown)
    first = judging.validate_grade(dict(good, note='  fine,\n  plain  '), shown)
    assert first['note'] == 'fine, plain' and first['graded_at'].endswith('Z') and first['comparisons_consistent'] is True
    judging.save_grade(tmp_path, first)
    judging.save_grade(tmp_path, judging.validate_grade(grade('n2', 'def', 'bad', comparisons_consistent=False, note='above target, but 467 < 500'), shown))
    judging.save_grade(tmp_path, judging.validate_grade(dict(good, verdict='bad', note='on reflection'), shown))   # a regrade replaces
    saved = json.loads((tmp_path / judging.CALIBRATION_FILE).read_text())
    assert [g['id'] for g in saved['grades']] == ['n1', 'n2'] and saved['grades'][0]['verdict'] == 'bad'
    assert saved['grades'][1]['comparisons_consistent'] is False
    assert judging.load_grades(tmp_path) == saved['grades'] and not list(tmp_path.glob('*.tmp'))


def test_the_committed_calibration_file_and_the_readme_agree():
    grades = judging.load_grades(ROOT / 'evals')
    calibration = json.loads((ROOT / 'evals' / judging.CALIBRATION_FILE).read_text())
    assert calibration['target'] == judging.CALIBRATION_TARGET and isinstance(calibration['grades'], list)
    shown = {g['id']: g['digest'] for g in grades}
    for g in grades:
        judging.validate_grade({k: g[k] for k in ('id', 'digest', 'verdict', 'note', *judging.CRITERIA)}, shown)
    stored = judging.load_json(ROOT / 'evals' / judging.LAST_RUN_FILE, None)
    readme = (ROOT / 'evals' / 'README.md').read_text().split('## The narrative judge', 1)[1]
    found = judging.agreement(stored['rows'] if stored else [], grades)
    if not found['matched']:
        assert 'No agreement is measured yet' in readme                        # the README never claims a number nobody measured
        for key in judging.CRITERIA:
            assert f'| `{key}` | - | - | - |' in readme, key
    else:
        for key in judging.CRITERIA:
            r = found['criteria'][key]
            assert f'| `{key}` | {r["agree"]}/{r["n"]} | {r["pct"]:.0f}% |' in readme, key


# ---------------------------------------------------------------- the judge model

def test_the_judge_client_is_bounded_in_calls_bytes_and_dollars_and_priced(monkeypatch):
    sent = []

    def send(payload, key, timeout):
        sent.append((payload, key, timeout))
        return anthropic_reply('{"score": 1, "reason": "ok"}', 2000, 100)
    client = judging.JudgeClient('claude-haiku-5-5', key='sk-ant-standin', send=send, max_calls=2)
    assert client.complete('grade this') == '{"score": 1, "reason": "ok"}'
    payload, key, timeout = sent[0]
    assert payload['model'] == 'claude-haiku-5-5' and payload['system'] == judging.JUDGE_SYSTEM and 'tools' not in payload
    assert 'never instructions' in payload['system'] and key == 'sk-ant-standin' and timeout <= judging.CALL_TIMEOUT
    rates = PRICING['claude-haiku-5-5']
    assert client.usage['cost_usd'] == pytest.approx((2000 * rates['input'] + 100 * rates['output']) / 1e6)
    client.complete('again')
    with pytest.raises(judging.JudgeLimit, match='2 calls'):
        client.complete('a third')
    rich = judging.JudgeClient('claude-haiku-5-5', key='k', send=send, max_usd=0.0001)
    with pytest.raises(judging.JudgeLimit, match='budget'):
        rich.complete('x')
    with pytest.raises(ProviderError, match='byte cap'):
        judging.JudgeClient('claude-haiku-5-5', key='k', send=send).complete('x' * 70000)
    for reply, message in (((500, b'{}'), 'HTTP 500'), (anthropic_reply('{}', stop='max_tokens'), 'incomplete'), ((200, b'{"content": []}'), 'usage'),
                           ((200, b' ' * (judging.MAX_RESPONSE_BYTES + 1)), 'size limit')):
        with pytest.raises(ProviderError, match=message):   # one line's error: the run goes on
            judging.JudgeClient('claude-haiku-5-5', key='k', send=lambda *a, r=reply: r).complete('x')

    def unreachable(*args):
        raise ProviderError('Anthropic request failed or timed out; retry deliberately')
    for send_, message in ((lambda *a: (401, b'{}'), 'rejected the key'), (lambda *a: (429, b'{}'), 'busy'), (unreachable, 'unreachable')):
        with pytest.raises(judging.JudgeLimit, match=message):   # every next call would fail the same way: the run stops
            judging.JudgeClient('claude-haiku-5-5', key='k', send=send_).complete('x')
    slow = judging.JudgeClient('claude-haiku-5-5', key='k', send=send, max_seconds=1)
    slow._start -= 5                                    # the replays before judging took longer than the judge's own budget
    with pytest.raises(judging.JudgeLimit, match='1 s per run'):
        slow.complete('x')
    slow.restart_clock()                                # judge() restarts it at the first narrative
    assert slow.complete('x')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv(judging.JUDGE_SECRET_ENV, raising=False)
    with pytest.raises(judging.JudgeUnavailable, match='ANTHROPIC_API_KEY is not set'):
        judging.JudgeClient('claude-haiku-5-5')
    with pytest.raises(judging.JudgeUnavailable, match='priced model'):
        judging.JudgeClient('claude-opus-9', key='k')
    monkeypatch.setenv(judging.JUDGE_ENV, 'claude-sonnet-5-5')                          # the judge model is a config value
    assert judging.JudgeClient(key='k', send=send).model == 'claude-sonnet-5-5'


def test_the_judge_key_can_be_read_from_secret_manager_by_name_in_a_named_project(monkeypatch):
    import subprocess
    calls = []

    def gcloud(argv, **kwargs):
        calls.append(argv)
        ok = '--secret=judge-anthropic-key' in argv
        return subprocess.CompletedProcess(argv, 0 if ok else 1, stdout='sk-ant-from-secret-manager\n' if ok else '', stderr='')
    monkeypatch.setattr(judging.subprocess, 'run', gcloud)
    for name in ('ANTHROPIC_API_KEY', judging.JUDGE_SECRET_ENV, judging.JUDGE_PROJECT_ENV):
        monkeypatch.delenv(name, raising=False)
    sent = []
    client = judging.JudgeClient('claude-haiku-5-5', secret='judge-anthropic-key',
                                 send=lambda payload, key, timeout: sent.append(key) or anthropic_reply('{"score": 1, "reason": "ok"}'))
    client.complete('x')
    assert sent == ['sk-ant-from-secret-manager'] and calls == [['gcloud', 'secrets', 'versions', 'access', 'latest', '--secret=judge-anthropic-key']]
    calls.clear()                                                                          # the project, when named, is gcloud's --project
    judging.JudgeClient('claude-haiku-5-5', secret='judge-anthropic-key', project='shift-demo-123', send=lambda *a: None)
    monkeypatch.setenv(judging.JUDGE_PROJECT_ENV, 'shift-demo-456')
    monkeypatch.setenv(judging.JUDGE_SECRET_ENV, 'judge-anthropic-key')                  # or both named in the environment
    assert judging.JudgeClient('claude-haiku-5-5', send=lambda *a: None)._key == 'sk-ant-from-secret-manager'
    assert [c[-1] for c in calls] == ['--project=shift-demo-123', '--project=shift-demo-456']
    with pytest.raises(judging.JudgeUnavailable, match='project id'):
        judging.JudgeClient('claude-haiku-5-5', secret='judge-anthropic-key', project='Bad Project; rm')
    with pytest.raises(judging.JudgeUnavailable, match='did not return the secret missing'):
        judging.JudgeClient('claude-haiku-5-5', secret='missing')
    with pytest.raises(judging.JudgeUnavailable, match='secret name'):
        judging.JudgeClient('claude-haiku-5-5', secret='a; rm')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-env')                                  # an environment key wins, gcloud is not run
    calls.clear()
    assert judging.JudgeClient('claude-haiku-5-5', secret='judge-anthropic-key')._key == 'sk-ant-env' and calls == []


def test_an_openai_model_can_judge_through_the_openai_adapter():
    from investigator.openai_provider import MODEL
    seen = []

    def send(payload, key, timeout):
        seen.append(payload)
        return {'status': 'completed', 'usage': {'input_tokens': 500, 'output_tokens': 20},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"score": 0, "reason": "a should"}'}]}]}
    client = judging.JudgeClient(MODEL, key='sk-standin', send=send)
    assert client.complete('grade') == '{"score": 0, "reason": "a should"}' and client.provider == 'openai'
    assert seen[0]['input'][0] == {'role': 'developer', 'content': judging.JUDGE_SYSTEM} and seen[0]['store'] is False


# ---------------------------------------------------------------- judge() in CI, DeepEval stubbed

class FakeTestCase:
    """DeepEval's LLMTestCase, as judge() builds it."""
    def __init__(self, input, actual_output, context):
        self.input, self.actual_output, self.context = input, actual_output, context


class LineMetric:
    """A metric for one rubric line that asks the judge client and reads its JSON reply, the way G-Eval does."""
    def __init__(self, key, client):
        self.key, self.client = key, client

    def measure(self, test, _show_indicator=True):
        reply = json.loads(self.client.complete(f'line: {self.key}\n{test.input}\n---\n{test.actual_output}\n---\n' + '\n'.join(test.context)))
        self.score, self.reason = reply['score'], reply['reason']


def two_narratives():
    """A check line (prescribes_nothing's boundary) and an actual-against-target claim its own figures contradict (line 5)."""
    return (judging.narrative_cases('a', ['Why was shift 2 slow?'], [written(('finding', 'Shift 2 ran at 616.12 ft/min including downtime.', [('observed_shift_fpm', 0)]),
                                                                             ('check', 'Ask the crew how the splices went.', []))], 'scripted plan')
            + judging.narrative_cases('b', ['Was the wet end on target?'], [written(('finding', 'The wet end ran above target at 467.45 ft/min against '
                                                                                   'a target of 500.00 ft/min.', [('actual_fpm', 0), ('target_fpm', 0)]))], 'scripted plan'))


def standin_send(prompts):
    """The judge model as a function of the line and the narrative: a check line fails prescribes_nothing, the false 'above
    target' fails comparisons_consistent, and narrative b's unavailable_not_zero gets an unparseable reply."""
    def send(payload, key, timeout):
        prompt = payload['messages'][0]['content']
        prompts.append(prompt)
        line = next(k for k in judging.CRITERIA if k in prompt.split('\n', 1)[0] or any(step in prompt for step in judging.RUBRIC[judging.CRITERIA.index(k)]['steps']))
        output = prompt.split('---\n')[1] if '---\n' in prompt else prompt.split('Actual Output:\n')[1].split('\nContext:')[0]
        if line == 'prescribes_nothing' and 'Check:' in output:
            return anthropic_reply('{"score": 0, "reason": "It tells the crew what to ask."}', 2000, 50)
        if line == 'comparisons_consistent' and 'above target at 467.45' in output:
            return anthropic_reply('{"score": 0, "reason": "467.45 is below the 500.00 target."}', 2000, 50)
        if line == 'unavailable_not_zero' and 'above target' in output:
            return anthropic_reply('no JSON here', 2000, 50)
        return anthropic_reply('{"score": 1, "reason": "Meets the steps."}', 2000, 50)
    return send


def test_judge_scores_each_line_reports_errors_and_costs_without_deepeval(monkeypatch):
    monkeypatch.setattr(judging, '_deepeval', lambda: (None, None, FakeTestCase, None))
    cases, prompts = two_narratives(), []
    client = judging.JudgeClient('claude-haiku-5-5', key='sk-ant-standin', send=standin_send(prompts))
    rows, summary = judging.judge(cases, client, metrics={k: LineMetric(k, client) for k in judging.CRITERIA})
    assert len(prompts) == 2 * len(judging.CRITERIA)                                  # one call per line per narrative
    assert [r['id'] for r in rows] == ['a:1', 'b:1'] and all(r['digest'] == c['digest'] for r, c in zip(rows, cases))
    assert rows[0]['scores'] == scores(prescribes_nothing=0) and rows[0]['reasons']['prescribes_nothing'] == 'It tells the crew what to ask.'
    assert rows[1]['scores'] == scores(comparisons_consistent=0, unavailable_not_zero=None)
    assert rows[1]['reasons']['comparisons_consistent'] == '467.45 is below the 500.00 target.'
    assert rows[1]['reasons']['unavailable_not_zero'].startswith('judge error')     # one line's error, the rest still scored
    rates = PRICING['claude-haiku-5-5']
    per_call = (2000 * rates['input'] + 50 * rates['output']) / 1e6
    assert rows[0]['calls'] == rows[1]['calls'] == 5 and rows[0]['cost_usd'] == pytest.approx(5 * per_call, abs=1e-6)
    assert summary['calls'] == 10 and summary['cost_usd'] == pytest.approx(10 * per_call, abs=1e-6)
    assert summary['cost_per_case_usd'] == pytest.approx(5 * per_call, abs=1e-6) and summary['metric'] == 'LineMetric'
    assert summary['criteria'] == {'grounded': {'pass': 2, 'fail': 0, 'error': 0}, 'recorded_not_proven': {'pass': 2, 'fail': 0, 'error': 0},
                                   'prescribes_nothing': {'pass': 1, 'fail': 1, 'error': 0}, 'unavailable_not_zero': {'pass': 1, 'fail': 0, 'error': 1},
                                   'comparisons_consistent': {'pass': 1, 'fail': 1, 'error': 0}}
    # A bound reached mid-run leaves the remaining lines unscored and says why; a refused key stops after one call.
    capped = judging.JudgeClient('claude-haiku-5-5', key='k', send=standin_send([]), max_calls=7)
    rows, summary = judging.judge(cases, capped, metrics={k: LineMetric(k, capped) for k in judging.CRITERIA})
    assert all(v is not None for v in rows[0]['scores'].values()) and [rows[1]['scores'][k] for k in judging.CRITERIA][:2] == [1, 1]
    assert [rows[1]['scores'][k] for k in judging.CRITERIA][2:] == [None, None, None] and 'judge limit reached' in rows[1]['reasons']['comparisons_consistent']
    assert summary['judged'] == 2 and summary['calls'] == 7
    refused = []
    locked = judging.JudgeClient('claude-haiku-5-5', key='k', send=lambda *a: refused.append(1) or (401, b'{}'))
    rows, summary = judging.judge(cases * 3, locked, metrics={k: LineMetric(k, locked) for k in judging.CRITERIA})
    assert len(refused) == 1 and summary['judged'] == 0 and all(v is None for r in rows for v in r['scores'].values())


class FakeBase:
    """DeepEval's DeepEvalBaseLLM: the constructor names the model and loads it."""
    def __init__(self, model=None):
        self.name = model
        self.model = self.load_model()


class FakeParams:
    INPUT, ACTUAL_OUTPUT, CONTEXT = 'input', 'actual_output', 'context'


class FakeGEval:
    """G-Eval's contract as judge() uses it: the given steps and the test case go to the model in one prompt, and strict
    mode reads a 0 or 1 back from its JSON."""
    made = []

    def __init__(self, name, evaluation_params, evaluation_steps, model, strict_mode, async_mode, **extra):
        self.kwargs = dict(name=name, evaluation_params=evaluation_params, evaluation_steps=evaluation_steps, model=model,
                           strict_mode=strict_mode, async_mode=async_mode, **extra)
        FakeGEval.made.append(self)

    def measure(self, test, _show_indicator=True):
        prompt = ('Evaluation Steps:\n' + '\n'.join(self.kwargs['evaluation_steps']) + f'\n\nInput:\n{test.input}\n\nActual Output:\n'
                  f'{test.actual_output}\nContext:\n' + '\n'.join(test.context))
        reply = json.loads(self.kwargs['model'].generate(prompt))
        self.score, self.reason = reply['score'], reply['reason']


def test_the_default_metrics_are_one_strict_geval_per_line_on_the_project_client(monkeypatch):
    monkeypatch.setattr(judging, '_deepeval', lambda: (FakeGEval, FakeBase, FakeTestCase, FakeParams))
    FakeGEval.made.clear()
    prompts = []
    client = judging.JudgeClient('claude-haiku-5-5', key='sk-ant-standin', send=standin_send(prompts))
    model = judging.deepeval_model(client)
    assert model.get_model_name() == 'claude-haiku-5-5' and model.load_model() is model
    assert json.loads(model.generate('line: grounded\nq\n---\nFinding: x.\n---\nctx'))['score'] == 1               # through the client
    assert json.loads(asyncio.run(model.a_generate('line: grounded\nq\n---\nFinding: x.\n---\nctx')))['score'] == 1
    assert client.usage['calls'] == 2
    metrics = judging.geval_metrics(model)
    assert list(metrics) == list(judging.CRITERIA)
    for line, metric in zip(judging.RUBRIC, metrics.values()):
        kw = metric.kwargs
        assert kw['name'] == line['name'] and kw['evaluation_steps'] == line['steps'] and kw['model'] is model
        assert kw['strict_mode'] is True and kw['async_mode'] is False and kw['evaluation_params'] == ['input', 'actual_output', 'context']
    # judge() with no metrics given builds exactly those, and scores through them.
    FakeGEval.made.clear()
    rows, summary = judging.judge(two_narratives(), client)
    assert len(FakeGEval.made) == len(judging.CRITERIA) and summary['metric'] == 'FakeGEval'
    assert rows[0]['scores'] == scores(prescribes_nothing=0) and rows[1]['scores'] == scores(comparisons_consistent=0, unavailable_not_zero=None)
    assert all(any(step in p for step in line['steps']) for p, line in zip(prompts[2:7], judging.RUBRIC))   # each prompt carries its own line's steps


# ---------------------------------------------------------------- the runner

def test_judge_refuses_before_any_case_runs_without_deepeval_or_a_key(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(golden, 'run_case_detail', lambda *a, **k: ran.append(a))

    def missing():
        raise judging.JudgeUnavailable("DeepEval is not installed: pip install -e '.[judge]'")
    monkeypatch.setattr(judging, '_deepeval', missing)
    assert golden.main(['--judge', '--only', 'why-slow']) == 2
    assert "pip install -e '.[judge]'" in capsys.readouterr().out
    monkeypatch.setattr(judging, '_deepeval', lambda: None)
    for name in ('ANTHROPIC_API_KEY', judging.JUDGE_ENV, judging.JUDGE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    assert golden.main(['--judge', '--only', 'why-slow']) == 2
    assert 'ANTHROPIC_API_KEY is not set and no Secret Manager secret is named' in capsys.readouterr().out
    for flags in (['--judge-model', 'claude-haiku-5-5'], ['--judge-secret', 'judge-anthropic-key'], ['--judge-project', 'shift-demo-123']):
        assert golden.main(flags + ['--only', 'why-slow']) == 2                        # each goes with --judge
    assert ran == []


def standin_judge(cases, client, metrics=None):
    """judging.judge without a model: a check line fails prescribes_nothing, everything else passes; a call a line."""
    rows = []
    for c in cases:
        check = any(line.startswith('Check:') for line in c['narrative'])
        client.usage['calls'] += len(judging.CRITERIA)
        client.usage['cost_usd'] += 0.002
        rows.append({'id': c['id'], 'run': c['run'], 'turn': c['turn'], 'source': c['source'], 'question': c['question'], 'digest': c['digest'],
                     'scores': scores(prescribes_nothing=0 if check else 1),
                     'reasons': {k: 'a check line' if (k == 'prescribes_nothing' and check) else 'ok' for k in judging.CRITERIA},
                     'calls': len(judging.CRITERIA), 'cost_usd': 0.002})
    return rows, judging.summary(rows, client.model, 'GEval', client.usage)


def test_a_judged_tracked_run_records_cost_per_case_and_agreement(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(judging, '_deepeval', lambda: None)
    monkeypatch.setattr(judging, 'judge', standin_judge)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-standin-not-a-credential')
    monkeypatch.delenv(judging.JUDGE_ENV, raising=False)
    monkeypatch.setattr(judging.JudgeClient, 'complete', lambda self, prompt: pytest.fail('the stand-in judge calls no model'))
    picked = [CASES['analyst-fastest-shift-consistent'], CASES['analyst-orderings-per-shift'], CASES['why-slow']]
    monkeypatch.setattr(golden, 'load_cases', lambda: picked)
    for name in ('HISTORY', 'LAST_RUN', 'JUDGE_LAST_RUN'):
        monkeypatch.setattr(golden, name, tmp_path / getattr(golden, name).name)
    monkeypatch.setattr(golden, 'ROOT', tmp_path)   # the tracked files are printed relative to it; the cases and fixtures stay the repo's
    evals = tmp_path / 'evals'
    evals.mkdir()
    monkeypatch.setattr(golden, 'EVALS', evals)
    case, results = scripted('analyst-fastest-shift-consistent')
    narrative = judging.narrative_cases(case['id'], [case['question']], results, 'x')[0]
    judging.save_grade(evals, judging.validate_grade(grade(narrative['id'], narrative['digest'], 'bad', prescribes_nothing=False, note='a check'),
                                                     {narrative['id']: narrative['digest']}))
    assert golden.main(['--judge', '--track']) == 0
    out = capsys.readouterr().out
    assert 'judge: GEval (claude-haiku-5-5), rubric ' + judging.rubric_digest() + ': 2 narratives, 10 model calls, $0.0040, $0.0020 per judged case' in out
    assert 'analyst-fastest-shift-consistent:1' in out and 'analyst-orderings-per-shift:1' in out and 'scripted plan' in out and ' comp ' in out
    assert 'judge vs owner: grounded 1/1 100% (kappa undefined: ok)' in out and '[1 graded narratives of 20]' in out
    line = json.loads((tmp_path / 'history.jsonl').read_text().splitlines()[-1])
    assert line['judge']['cost_per_case_usd'] == 0.002 and line['judge']['model'] == 'claude-haiku-5-5' and line['judge']['rubric'] == judging.rubric_digest()
    assert line['judge']['criteria']['prescribes_nothing'] == {'pass': 1, 'fail': 1, 'error': 0}
    assert set(line['judge']['criteria']) == set(judging.CRITERIA) == set(line['judge']['agreement']['criteria'])
    assert line['judge']['agreement']['matched'] == 1 and line['judge']['agreement']['criteria']['prescribes_nothing']['agree'] == 1
    assert line['exit'] == 0                                                            # the judge does not gate
    stored = json.loads((tmp_path / judging.LAST_RUN_FILE).read_text())
    assert (stored['ran_at'], stored['commit']) == (line['ran_at'], line['commit']) and len(stored['rows']) == 2
    # The next run without --judge prints the stored judge and the owner agreement, and calls no model.
    assert golden.main(['--only', 'why-slow']) == 0
    assert 'judge, last tracked ' + line['ran_at'] + ' (claude-haiku-5-5, GEval): grounded 2/2' in capsys.readouterr().out
    # A judged run that scored nothing (a refused key, a bound) is printed and never tracked as the judge's result.
    def refused(cases, client, metrics=None):
        rows, summary = standin_judge(cases, client)
        for row in rows:
            row['scores'] = {k: None for k in judging.CRITERIA}
        return rows, judging.summary(rows, client.model, 'GEval', client.usage)
    monkeypatch.setattr(judging, 'judge', refused)
    before = (tmp_path / judging.LAST_RUN_FILE).read_bytes()
    assert golden.main(['--judge', '--track']) == 0
    assert 'no narrative was scored, so this run is not tracked as a judged run' in capsys.readouterr().out
    assert 'judge' not in json.loads((tmp_path / 'history.jsonl').read_text().splitlines()[-1])
    assert (tmp_path / judging.LAST_RUN_FILE).read_bytes() == before


def test_the_eval_table_states_the_target_waits_on_re_recording_while_a_recording_is_stale(monkeypatch, capsys):
    stale = {'id': 'x@anthropic', 'outcome': 'skip', 'note': 'stale recording: dataset d9143ff1d150 vs live 0123; re-record with --live --provider anthropic'}
    assert golden.stale_recordings([stale]) and not golden.stale_recordings([dict(stale, outcome='pass')])
    assert 'target 20 once the live recordings are re-recorded (#71)' in golden.stored_judge_line(True)
    assert 'target 20)' in golden.stored_judge_line(False)


# ---------------------------------------------------------------- DeepEval itself (the optional `judge` extra)

def test_geval_scores_each_line_through_the_project_client():
    pytest.importorskip('deepeval')
    cases, prompts = two_narratives(), []
    client = judging.JudgeClient('claude-haiku-5-5', key='sk-ant-standin', send=standin_send(prompts))
    rows, summary = judging.judge(cases, client)
    assert len(prompts) == 2 * len(judging.CRITERIA)                               # one call per rubric line, the steps given
    for line, prompt in zip(judging.RUBRIC, prompts):
        assert all(step in prompt for step in line['steps'])
        assert cases[0]['narrative'][0] in prompt and 'r1 get_shift_kpis(shift=2)' in prompt and 'Why was shift 2 slow?' in prompt
    assert rows[0]['scores'] == scores(prescribes_nothing=0) and rows[1]['scores'] == scores(comparisons_consistent=0, unavailable_not_zero=None)
    assert rows[1]['reasons']['unavailable_not_zero'].startswith('judge error') and summary['metric'] == 'GEval' and summary['calls'] == 10
    import os
    assert os.environ['DEEPEVAL_TELEMETRY_OPT_OUT'] == '1' and os.environ['DEEPEVAL_DISABLE_DOTENV'] == '1'
    refused = []
    rows, _ = judging.judge(cases * 3, judging.JudgeClient('claude-haiku-5-5', key='k', send=lambda *a: refused.append(1) or (401, b'{}')))
    assert len(refused) == 1 and all(v is None for r in rows for v in r['scores'].values())   # one call, not thirty


# ---------------------------------------------------------------- the grading page: local workbench only

def serve(public=None):
    server = make_server(0, public=public, host='127.0.0.1')
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(server, thread):
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def request(server, method, path, body=None, headers=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def test_the_hosted_demo_has_no_grading_route():
    server, thread = serve(PublicDemo(origins=('https://demo.example',), embed_origins=(), live_model=False))
    try:
        for path in ('/evals/grade', '/grade.js', '/api/evals/narratives'):
            assert request(server, 'GET', path, headers={'Host': 'demo.example'})[0] == 404, path
        status, body = request(server, 'POST', '/api/evals/grade', json.dumps(grade('a', 'b')),
                               {'Content-Type': 'application/json', 'X-Workbench-Token': server.app.token, 'Origin': 'https://demo.example'})
        assert status == 404 and b'local workbench only' in body
        board = json.loads(request(server, 'GET', '/evals.json', headers={'Host': 'demo.example'})[1])
        assert board['judge']['target'] == judging.CALIBRATION_TARGET and 'grades' not in board['judge']   # counts, never the owner's notes
    finally:
        stop(server, thread)


def test_the_grading_page_saves_a_grade_and_counts_grades_as_evals_does(tmp_path, monkeypatch):
    shipped = tmp_path / 'app' / 'evals'
    shutil.copytree(ROOT / 'evals', shipped, ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setenv('INVESTIGATOR_EVALS', str(shipped))
    before = (ROOT / 'evals' / judging.CALIBRATION_FILE).read_bytes()
    server, thread = serve()
    try:
        status, page = request(server, 'GET', '/evals/grade')
        assert status == 200 and b'<script src="/grade.js" defer></script>' in page
        assert request(server, 'GET', '/grade.js')[0] == 200
        listing = json.loads(request(server, 'GET', '/api/evals/narratives')[1])
        assert {n['id'] for n in listing['narratives']} >= {f'{c}:1' for c in SCRIPTED}
        assert (listing['target'], listing['graded'], listing['set_aside']) == (judging.CALIBRATION_TARGET, 0, 0)
        assert listing['target_text'] == judging.target_text(len(listing['narratives']) < judging.CALIBRATION_TARGET)
        assert [r['key'] for r in listing['rubric']] == list(judging.CRITERIA)
        item = listing['narratives'][0]
        assert {'id', 'digest', 'question', 'narrative', 'evidence', 'grade', 'stale'} <= set(item) and 'scores' not in item   # never the judge's verdict
        headers = {'Content-Type': 'application/json', 'X-Workbench-Token': server.app.token}
        body = grade(item['id'], item['digest'], 'bad', comparisons_consistent=False)
        assert request(server, 'POST', '/api/evals/grade', json.dumps(body), {'Content-Type': 'application/json'})[0] == 403     # no token
        status, refused = request(server, 'POST', '/api/evals/grade', json.dumps(body), headers)
        assert status == 400 and b'Say why' in refused
        status, saved = request(server, 'POST', '/api/evals/grade', json.dumps(dict(body, note='Closer to target, but its figures say not.')), headers)
        saved = json.loads(saved)
        assert status == 200 and (saved['graded'], saved['set_aside']) == (1, 0)
        written = json.loads((shipped / judging.CALIBRATION_FILE).read_text())['grades']
        assert [(g['id'], g['verdict'], g['comparisons_consistent']) for g in written] == [(item['id'], 'bad', False)]
        assert json.loads(request(server, 'GET', '/evals.json')[1])['judge']['graded'] == 1                    # the board follows at once
        assert json.loads(request(server, 'GET', '/api/evals/narratives')[1])['narratives'][0]['grade']['note'] == 'Closer to target, but its figures say not.'
    finally:
        stop(server, thread)
    # A grade of text that has since changed (or no longer replays) is counted by both pages alike, and set aside on the grading page.
    judging.save_grade(shipped, {**grade('story-trim-creep@anthropic:2', 'recorded-before-91', note='was fine'), 'graded_at': '2026-11-11T20:00:00Z'})
    board = EvalBoard(shipped)
    counts = board.grading(Lock())
    assert (counts['graded'], counts['set_aside']) == (2, 1) and board.board()['judge']['graded'] == counts['graded']
    assert (ROOT / 'evals' / judging.CALIBRATION_FILE).read_bytes() == before


def test_the_board_shows_a_judged_run_only_when_it_was_tracked(tmp_path):
    shipped = tmp_path / 'app' / 'evals'
    shutil.copytree(ROOT / 'evals', shipped, ignore=shutil.ignore_patterns('__pycache__'))
    newest = json.loads((shipped / 'history.jsonl').read_text().splitlines()[-1])
    row = {'id': 'n1', 'run': 'r', 'turn': 1, 'source': 'recorded anthropic', 'question': 'q', 'digest': 'd',
           'scores': scores(), 'reasons': {k: 'ok' for k in judging.CRITERIA}, 'calls': 5, 'cost_usd': 0.001}
    summary = judging.summary([row], 'claude-haiku-5-5', 'GEval', {'calls': 5, 'cost_usd': 0.001})
    (shipped / judging.LAST_RUN_FILE).write_text(json.dumps({'ran_at': newest['ran_at'], 'commit': newest['commit'], 'summary': summary, 'rows': [row]}))
    board = EvalBoard(shipped)
    judged = board.board()['judge']
    assert judged['last']['cost_per_case_usd'] == 0.001 and judged['last']['rubric_current'] and [r['id'] for r in judged['rows']] == ['n1']
    assert judged['rows'][0]['reasons']['grounded'] == 'ok'                              # the local workbench shows the judge's reasons
    hosted = board.board(public=True)['judge']                                           # the hosted demo shows its scores, not its prose
    assert 'reasons' not in hosted['rows'][0] and hosted['rows'][0]['scores'] == judged['rows'][0]['scores'] and hosted['reasons'] == 'local workbench only'
    assert board.board()['judge']['rows'][0]['reasons']                                  # and the local copy is left whole
    (shipped / judging.LAST_RUN_FILE).write_text(json.dumps({'ran_at': '1999-01-01T00:00Z', 'commit': 'nope', 'summary': summary, 'rows': [row]}))
    assert EvalBoard(shipped).board()['judge']['last'] is None
