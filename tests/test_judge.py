"""The narrative judge of issue #25: a DeepEval G-Eval rubric over the analyst's prose, live only, and the owner's
grades it is calibrated against. Nothing here calls a model: the judge model's transport is a stand-in, and the
tests that need DeepEval itself skip when it is not installed (it is the optional `judge` extra, not in CI)."""
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


@pytest.fixture(scope='module', autouse=True)
def datasets():
    yield
    golden.close_datasets()


def replayed(case_id, provider='anthropic'):
    case = CASES[case_id]
    outcome, failures, _, _, results = golden.run_case_detail(case, provider, live=False, record=False)
    assert outcome == 'pass', failures
    return case, results


def anthropic_reply(text, input_tokens=1000, output_tokens=40, stop='end_turn'):
    return 200, json.dumps({'content': [{'type': 'thinking', 'thinking': 'x'}, {'type': 'text', 'text': text}], 'stop_reason': stop,
                            'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens}}).encode()


# ---------------------------------------------------------------- the rubric

def test_the_rubric_is_four_lines_each_scored_on_its_own_and_documented():
    assert judging.CRITERIA == ('grounded', 'recorded_not_proven', 'prescribes_nothing', 'unavailable_not_zero')
    for line in judging.RUBRIC:
        assert line['name'] and line['criterion'] and len(line['steps']) >= 3
        assert any('Score 1' in step for step in line['steps']) and any('Score 0' in step for step in line['steps'])
    digest = judging.rubric_digest()
    assert len(digest) == 12 and digest == judging.rubric_digest()
    readme = (ROOT / 'evals' / 'README.md').read_text()
    section = readme.split('## The narrative judge', 1)[1]
    for line in judging.RUBRIC:   # the README states every line's name, key and criterion, and how it is run and calibrated
        assert line['name'] in section and f'`{line["key"]}`' in section and line['criterion'] in section
    for needle in ('--judge', 'judge_calibration.json', 'judge_last_run.json', 'kappa', 'cost_per_case_usd', 'DEEPEVAL_JUDGE', '/evals/grade'):
        assert needle in section, needle


# ---------------------------------------------------------------- judge cases

def test_a_replayed_recording_becomes_one_judge_case_with_its_evidence():
    case, results = replayed('live-why-slow')
    cases = judging.narrative_cases('live-why-slow@anthropic', [case['question']], results, 'recorded anthropic')
    assert [c['id'] for c in cases] == ['live-why-slow@anthropic:1']
    one = cases[0]
    assert one['input'].startswith(f'Question: {case["question"]}\nScope: production day 2026-09-14, shift 2')
    assert one['narrative'][0].startswith('Finding: Shift 2 ran at 89.04') and one['output'] == '\n'.join(one['narrative'])
    assert '(cites r1 row 0 speed_to_target_pct; r1 row 0 downtime_pct)' in one['narrative'][0]   # citations in short form
    assert one['context'][0].startswith('r1 get_shift_overview(shift=2) -> {"rows":[{"valid_setups":44')   # the cited rows first
    assert '"speed_to_target_pct":89.0443' in one['context'][0]                                        # rounded, still the value cited
    assert 'source_ids' not in ''.join(one['context']) and 'dataset_version' not in ''.join(one['context'])
    assert sum(len(c) for c in one['context']) <= judging.MAX_CONTEXT_CHARS
    again = judging.narrative_cases('live-why-slow@anthropic', [case['question']], results, 'recorded anthropic')[0]
    assert again['digest'] == one['digest']                                                 # the same words, the same digest
    results[0]['narrative'][0]['text'] += ' Changed.'
    assert judging.narrative_cases('x', [case['question']], results, 'x')[0]['digest'] != one['digest']


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


def test_the_runner_and_the_grading_page_name_the_same_narratives_and_there_are_enough_to_calibrate():
    board = EvalBoard(ROOT / 'evals', golden.dataset_version())
    found = board.narratives(Lock())
    ids = [n['id'] for n in found]
    assert len(ids) == len(set(ids)) >= judging.CALIBRATION_TARGET
    assert {n['source'] for n in found} == {'recorded anthropic', 'scripted plan'}
    case, results = replayed('story-monday-warp')
    mine = judging.narrative_cases('story-monday-warp@anthropic', case['turns'], results, 'recorded anthropic')
    theirs = {n['id']: n['digest'] for n in found}
    assert mine and all(theirs[c['id']] == c['digest'] for c in mine)                  # a grade and a judged score meet by id and digest
    assert [c['turn'] for c in mine] == [1, 2, 3, 4] and mine[2]['input'].count('Earlier questions') == 1


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


def test_agreement_is_per_line_over_the_same_text_only():
    judged = [{'id': f'n{i}', 'digest': f'd{i}', 'scores': {'grounded': 1, 'recorded_not_proven': 1, 'prescribes_nothing': i % 2, 'unavailable_not_zero': 1}}
              for i in range(4)]
    judged.append({'id': 'n4', 'digest': 'd4', 'scores': {'grounded': None, 'recorded_not_proven': 1, 'prescribes_nothing': 1, 'unavailable_not_zero': 1}})
    grades = [grade('n0', 'd0', 'bad', prescribes_nothing=False), grade('n1', 'd1'), grade('n2', 'd2'),   # n2: judge 0, owner 1
              grade('n3', 'old-text'), grade('n9', 'd9'), grade('n4', 'd4')]
    found = judging.agreement(judged, grades)
    assert (found['graded'], found['matched'], found['unmatched']) == (6, 4, 2)
    assert found['criteria']['prescribes_nothing'] == {'n': 4, 'agree': 3, 'pct': 75.0, 'kappa': 0.5, 'ok': False}
    assert found['criteria']['grounded']['n'] == 3                    # the judge could not score n4's first line: left out of that line only
    assert found['overall']['n'] == 3 and found['overall']['agree'] == 2
    text = judging.agreement_text(found)
    assert 'prescribes_nothing 3/4 75% (kappa 0.50: BELOW 90%)' in text and '[4 graded narratives of 20]' in text
    assert judging.agreement_text(judging.agreement([], [])) == 'owner grades 0 of 20; no agreement measured yet'


def test_a_grade_is_checked_against_the_text_the_page_showed_and_saved_whole(tmp_path):
    shown = {'n1': 'abc', 'n2': 'def'}
    good = grade('n1', 'abc')
    for bad, message in ((dict(good, id='nope'), 'No such'), (dict(good, digest='zzz'), 'changed'), (dict(good, verdict='meh'), 'verdict'),
                         (dict(good, grounded='yes'), 'true'), (dict(good, verdict='bad'), 'Say why'), (dict(good, note='x' * 501), 'at most'),
                         (dict(good, extra=1), 'optional note'), ({k: v for k, v in good.items() if k != 'grounded'}, 'optional note'), ([], 'JSON object')):
        with pytest.raises(ValueError, match=message):
            judging.validate_grade(bad, shown)
    first = judging.validate_grade(dict(good, note='  fine,\n  plain  '), shown)
    assert first['note'] == 'fine, plain' and first['graded_at'].endswith('Z')
    judging.save_grade(tmp_path, first)
    judging.save_grade(tmp_path, judging.validate_grade(grade('n2', 'def', 'bad', prescribes_nothing=False, note='told them to adjust'), shown))
    judging.save_grade(tmp_path, judging.validate_grade(dict(good, verdict='bad', note='on reflection'), shown))   # a regrade replaces
    saved = json.loads((tmp_path / judging.CALIBRATION_FILE).read_text())
    assert [g['id'] for g in saved['grades']] == ['n1', 'n2'] and saved['grades'][0]['verdict'] == 'bad'
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
    for reply, message in (((529, b'{}'), 'HTTP 529'), (anthropic_reply('{}', stop='max_tokens'), 'incomplete'), ((200, b'{"content": []}'), 'usage')):
        with pytest.raises(ProviderError, match=message):
            judging.JudgeClient('claude-haiku-5-5', key='k', send=lambda *a, r=reply: r).complete('x')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    with pytest.raises(judging.JudgeUnavailable, match='ANTHROPIC_API_KEY is not set'):
        judging.JudgeClient('claude-haiku-5-5')
    with pytest.raises(judging.JudgeUnavailable, match='priced model'):
        judging.JudgeClient('claude-opus-9', key='k')
    monkeypatch.setenv(judging.JUDGE_ENV, 'claude-sonnet-5-5')                          # the judge model is a config value
    assert judging.JudgeClient(key='k', send=send).model == 'claude-sonnet-5-5'


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
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv(judging.JUDGE_ENV, raising=False)
    assert golden.main(['--judge', '--only', 'why-slow']) == 2
    assert 'ANTHROPIC_API_KEY is not set; the judge model claude-haiku-5-5 needs it' in capsys.readouterr().out
    assert golden.main(['--judge-model', 'claude-haiku-5-5', '--only', 'why-slow']) == 2
    assert ran == []


def standin_judge(cases, client, metrics=None):
    """judging.judge without DeepEval: a check line fails prescribes_nothing, everything else passes; 4 calls a case."""
    rows = []
    for c in cases:
        check = any(line.startswith('Check:') for line in c['narrative'])
        client.usage['calls'] += 4
        client.usage['cost_usd'] += 0.002
        rows.append({'id': c['id'], 'run': c['run'], 'turn': c['turn'], 'source': c['source'], 'question': c['question'], 'digest': c['digest'],
                     'scores': {k: 0 if (k == 'prescribes_nothing' and check) else 1 for k in judging.CRITERIA},
                     'reasons': {k: 'a check line' if (k == 'prescribes_nothing' and check) else 'ok' for k in judging.CRITERIA},
                     'calls': 4, 'cost_usd': 0.002})
    return rows, judging.summary(rows, client.model, 'GEval', client.usage)


def test_a_judged_tracked_run_records_cost_per_case_and_agreement(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(judging, '_deepeval', lambda: None)
    monkeypatch.setattr(judging, 'judge', standin_judge)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-standin-not-a-credential')
    monkeypatch.delenv(judging.JUDGE_ENV, raising=False)
    monkeypatch.setattr(judging.JudgeClient, 'complete', lambda self, prompt: pytest.fail('the stand-in judge calls no model'))
    picked = [CASES['live-why-slow'], CASES['analyst-fastest-shift-consistent'], CASES['why-slow']]
    monkeypatch.setattr(golden, 'load_cases', lambda: picked)
    for name in ('HISTORY', 'LAST_RUN', 'JUDGE_LAST_RUN'):
        monkeypatch.setattr(golden, name, tmp_path / getattr(golden, name).name)
    monkeypatch.setattr(golden, 'ROOT', tmp_path)   # the tracked files are printed relative to it; the cases and fixtures stay the repo's
    evals = tmp_path / 'evals'
    evals.mkdir()
    monkeypatch.setattr(golden, 'EVALS', evals)
    narrative = judging.narrative_cases('live-why-slow@anthropic', [CASES['live-why-slow']['question']], replayed('live-why-slow')[1], 'x')[0]
    judging.save_grade(evals, judging.validate_grade(grade(narrative['id'], narrative['digest'], 'bad', prescribes_nothing=False, note='a check'),
                                                     {narrative['id']: narrative['digest']}))
    assert golden.main(['--judge', '--track']) == 0
    out = capsys.readouterr().out
    assert 'judge: GEval (claude-haiku-5-5), rubric ' + judging.rubric_digest() + ': 2 narratives, 8 model calls, $0.0040, $0.0020 per judged case' in out
    assert 'live-why-slow@anthropic:1' in out and 'analyst-fastest-shift-consistent:1' in out and 'scripted plan' in out
    assert 'judge vs owner: grounded 1/1 100% (kappa undefined: ok)' in out and '[1 graded narratives of 20]' in out
    line = json.loads((tmp_path / 'history.jsonl').read_text().splitlines()[-1])
    assert line['judge']['cost_per_case_usd'] == 0.002 and line['judge']['model'] == 'claude-haiku-5-5' and line['judge']['rubric'] == judging.rubric_digest()
    assert line['judge']['criteria']['prescribes_nothing'] == {'pass': 0, 'fail': 2, 'error': 0}
    assert line['judge']['agreement']['matched'] == 1 and line['judge']['agreement']['criteria']['prescribes_nothing']['agree'] == 1
    assert line['exit'] == 0                                                            # the judge does not gate
    stored = json.loads((tmp_path / judging.LAST_RUN_FILE).read_text())
    assert (stored['ran_at'], stored['commit']) == (line['ran_at'], line['commit']) and len(stored['rows']) == 2
    # The next run without --judge prints the stored judge and the owner agreement, and calls no model.
    assert golden.main(['--only', 'why-slow']) == 0
    assert 'judge, last tracked ' + line['ran_at'] + ' (claude-haiku-5-5, GEval): grounded 2/2' in capsys.readouterr().out


# ---------------------------------------------------------------- DeepEval itself (the optional `judge` extra)

def test_geval_scores_each_line_through_the_project_client():
    pytest.importorskip('deepeval')
    case, results = replayed('live-why-slow')
    cases = judging.narrative_cases('live-why-slow@anthropic', [case['question']], results, 'recorded anthropic')
    prompts = []

    def send(payload, key, timeout):
        prompt = payload['messages'][0]['content']
        prompts.append(prompt)
        if 'tells someone what to do' in prompt:
            return anthropic_reply('{"reason": "It tells the crew what to compare.", "score": 0}', 3000, 60)
        if 'Find in Context every value that is unavailable' in prompt:
            return anthropic_reply('not json at all', 3000, 60)
        return anthropic_reply('```json\n{"reason": "Fine.", "score": 1}\n```', 3000, 60)
    client = judging.JudgeClient('claude-haiku-5-5', key='sk-ant-standin', send=send)
    rows, summary = judging.judge(cases, client)
    assert len(prompts) == 4                                                       # one call per rubric line, the steps given
    for line, prompt in zip(judging.RUBRIC, prompts):
        assert all(step in prompt for step in line['steps'])
        assert cases[0]['narrative'][0] in prompt and 'r1 get_shift_overview(shift=2)' in prompt and case['question'] in prompt
    (row,) = rows
    assert row['scores'] == {'grounded': 1, 'recorded_not_proven': 1, 'prescribes_nothing': 0, 'unavailable_not_zero': None}
    assert row['reasons']['prescribes_nothing'] == 'It tells the crew what to compare.' and row['reasons']['unavailable_not_zero'].startswith('judge error')
    rates = PRICING['claude-haiku-5-5']
    assert summary['cost_usd'] == pytest.approx(4 * (3000 * rates['input'] + 60 * rates['output']) / 1e6, abs=1e-6)
    assert summary['cost_per_case_usd'] == summary['cost_usd'] and summary['metric'] == 'GEval' and summary['calls'] == 4
    assert summary['criteria']['unavailable_not_zero'] == {'pass': 0, 'fail': 0, 'error': 1}
    import os
    assert os.environ['DEEPEVAL_TELEMETRY_OPT_OUT'] == '1' and os.environ['DEEPEVAL_DISABLE_DOTENV'] == '1'
    # A bound reached mid-run leaves the remaining lines unscored and says why, rather than failing the run.
    capped = judging.JudgeClient('claude-haiku-5-5', key='sk-ant-standin', send=send, max_calls=2)
    rows, summary = judging.judge(cases, capped)
    assert [rows[0]['scores'][k] for k in judging.CRITERIA] == [1, 1, None, None] and 'judge limit reached' in rows[0]['reasons']['prescribes_nothing']


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


def test_the_grading_page_saves_a_grade_into_the_evals_folder(tmp_path, monkeypatch):
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
        assert len(listing['narratives']) >= listing['target'] == judging.CALIBRATION_TARGET and listing['graded'] == 0
        item = listing['narratives'][0]
        assert {'id', 'digest', 'question', 'narrative', 'evidence', 'grade', 'stale'} <= set(item) and 'scores' not in item   # never the judge's verdict
        headers = {'Content-Type': 'application/json', 'X-Workbench-Token': server.app.token}
        body = grade(item['id'], item['digest'], 'bad', prescribes_nothing=False)
        assert request(server, 'POST', '/api/evals/grade', json.dumps(body), {'Content-Type': 'application/json'})[0] == 403     # no token
        status, refused = request(server, 'POST', '/api/evals/grade', json.dumps(body), headers)
        assert status == 400 and b'Say why' in refused
        status, saved = request(server, 'POST', '/api/evals/grade', json.dumps(dict(body, note='It tells the crew what to do.')), headers)
        assert status == 200 and json.loads(saved)['graded'] == 1
        written = json.loads((shipped / judging.CALIBRATION_FILE).read_text())['grades']
        assert [(g['id'], g['verdict'], g['prescribes_nothing']) for g in written] == [(item['id'], 'bad', False)]
        assert json.loads(request(server, 'GET', '/evals.json')[1])['judge']['graded'] == 1                    # the board follows at once
        assert json.loads(request(server, 'GET', '/api/evals/narratives')[1])['narratives'][0]['grade']['note'] == 'It tells the crew what to do.'
    finally:
        stop(server, thread)
    assert (ROOT / 'evals' / judging.CALIBRATION_FILE).read_bytes() == before


def test_the_board_shows_a_judged_run_only_when_it_was_tracked(tmp_path):
    shipped = tmp_path / 'app' / 'evals'
    shutil.copytree(ROOT / 'evals', shipped, ignore=shutil.ignore_patterns('__pycache__'))
    newest = json.loads((shipped / 'history.jsonl').read_text().splitlines()[-1])
    row = {'id': 'n1', 'run': 'r', 'turn': 1, 'source': 'recorded anthropic', 'question': 'q', 'digest': 'd',
           'scores': {k: 1 for k in judging.CRITERIA}, 'reasons': {k: 'ok' for k in judging.CRITERIA}, 'calls': 4, 'cost_usd': 0.001}
    summary = judging.summary([row], 'claude-haiku-5-5', 'GEval', {'calls': 4, 'cost_usd': 0.001})
    (shipped / judging.LAST_RUN_FILE).write_text(json.dumps({'ran_at': newest['ran_at'], 'commit': newest['commit'], 'summary': summary, 'rows': [row]}))
    judged = EvalBoard(shipped, golden.dataset_version()).board()['judge']
    assert judged['last']['cost_per_case_usd'] == 0.001 and judged['last']['rubric_current'] and [r['id'] for r in judged['rows']] == ['n1']
    (shipped / judging.LAST_RUN_FILE).write_text(json.dumps({'ran_at': '1999-01-01T00:00Z', 'commit': 'nope', 'summary': summary, 'rows': [row]}))
    assert EvalBoard(shipped, golden.dataset_version()).board()['judge']['last'] is None
