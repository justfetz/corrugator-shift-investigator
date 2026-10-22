"""The golden eval set is a regression gate: every offline case of every type
must pass (a case marked known_defect may xfail), and any recorded live
fixture must replay cleanly. Live calls never run here."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('golden_run', ROOT / 'evals' / 'run.py')
golden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden)

CASES = golden.load_cases()
OFFLINE = [c for c in CASES if c['mode'] == 'offline']
RECORDED = [(c, p) for c in CASES if c['mode'] == 'live' or c.get('live')
            for p in golden.PROVIDERS if golden.fixture_path(c['id'], p).exists()]


@pytest.fixture(scope='module', autouse=True)
def datasets():
    yield
    golden.close_datasets()


@pytest.mark.parametrize('case', OFFLINE, ids=[c['id'] for c in OFFLINE])
def test_offline_golden_case(case):
    outcome, failures, note = golden.run_case(case)
    if outcome == 'xfail':
        pytest.xfail(note + ': ' + '; '.join(failures))
    assert outcome == 'pass', failures or note


@pytest.mark.parametrize('case, provider', RECORDED, ids=[f'{c["id"]}@{p}' for c, p in RECORDED])
def test_recorded_live_case_replays(case, provider):
    outcome, failures, note = golden.run_case(case, provider)
    assert outcome == 'pass', failures or note


def test_every_case_type_is_known_and_thresholded():
    types = {c['type'] for c in CASES}
    assert types <= set(golden.TYPES) and types == set(golden.TYPES)
    assert set(golden.THRESHOLDS) == set(golden.TYPES)
    assert len({c['id'] for c in CASES}) == len(CASES)
    assert sum(c['type'] == 'value' for c in CASES) >= 40
    assert sum(c['type'] == 'investigation' for c in CASES) == 3
    for case in CASES:
        if case['type'] == 'value':
            assert case['mode'] == 'offline' and 'values' in case['expect'] and 'tool' in case
        if case['type'] == 'investigation':
            assert case.get('live') is True and case['expect']['evidence'] and case['expect']['forbidden']


def test_unfaithful_number_detector_catches_an_invented_total():
    trace = [{'ok': True, 'tool': 'get_downtime_breakdown', 'result_id': 'a' * 32 + ':1',
              'arguments': {'shift': 2}, 'data': {'shift': 2, 'coverage': 'complete',
              'rows': [{'category': 'Knife jam', 'down_minutes': 12.0, 'shift_percent': 2.5}], 'excluded_records': []}}]
    assert golden.unfaithful_numbers(['Shift 2 recorded downtime: Knife jam: 12 min.'], trace) == []
    bad = golden.unfaithful_numbers(['Shift 2 recorded downtime: Knife jam: 15 min.'], trace)
    assert bad and bad[0].startswith("'15'")


def test_value_selectors_and_tolerances():
    data = {'rows': [{'metric': 'a', 'current': 1.26, 'change': 'worse'}], 'weekday_profile': [{'weekday': 'Mon', 'days': 4}], 'total': 5}
    assert golden.select_row(data, 'metric=a')['current'] == 1.26
    assert golden.select_row(data, '#0') is data['rows'][0]
    assert golden.select_row(data, 'weekday_profile:weekday=Mon')['days'] == 4
    assert golden.select_row(data, '$')['total'] == 5
    for bad in ('metric=b', '#3', 'nowhere:weekday=Mon'):
        with pytest.raises(LookupError):
            golden.select_row(data, bad)
    assert golden.close_enough(1.2605, 1.26, 0.001) and not golden.close_enough(1.3, 1.26, 0.001)
    assert golden.close_enough(None, None, None) and not golden.close_enough(0, None, None)
    assert golden.close_enough('worse', 'worse', None) and not golden.close_enough(True, 1, None)
    assert golden.close_enough(['a'], ['a'], None) and not golden.close_enough(float('nan'), 1.0, 0.1)
    result = {'trace': [{'ok': True, 'tool': 'get_period_kpis', 'data': data}]}
    assert golden.value_failures({'row_count': 1, 'values': {'metric=a': {'current': [1.26, 0.001], 'change': ['worse', None]}}}, result) == []
    failures = golden.value_failures({'row_count': 2, 'values': {'metric=a': {'current': [1.5, 0.001], 'gone': [1, 0]}, 'metric=z': {'x': [1, 0]}}}, result)
    assert len(failures) == 4 and failures[0].startswith('1 rows, expected 2')


def test_stale_value_goldens_fail_loudly(monkeypatch):
    case = next(c for c in CASES if c['type'] == 'value')
    monkeypatch.setitem(golden._VALUES_STATUS, 'message', 'stale goldens (test)')
    outcome, failures, _ = golden.run_case(case)
    assert outcome == 'fail' and failures == ['stale goldens (test)']
    monkeypatch.setitem(golden._VALUES_STATUS, 'message', None)
    assert golden.run_case(case)[0] == 'pass'


def test_investigation_rules_on_a_synthetic_trace():
    rid = 'b' * 32 + ':1'
    data = {'shift': None, 'coverage': 'complete', 'excluded_records': [],
            'window': {'start': '2026-09-07', 'end': '2026-09-11', 'label': 'Week'},
            'rows': [{'category': 'Splicer 3', 'minutes': 60.0, 'previous_minutes': 6.0, 'stops': 38},
                     {'category': 'Stacker', 'minutes': 30.0, 'previous_minutes': 31.0, 'stops': 30}]}
    step = {'result_id': rid, 'ok': True, 'tool': 'get_downtime_pareto', 'arguments': {'group_by': 'place', 'shift': None}, 'data': data}
    result = {'status': 'complete', 'trace': [step], 'sections': ['Splicer 3: 60.00 min, previous 6.00 min.'],
              'narrative': [{'kind': 'finding', 'text': 'Splicer 3 minutes rose; this warrants inspection.', 'citations': []}],
              'charts': [], 'tables': [], 'selected_shifts': [], 'usage': {'model_calls': 0, 'cost_usd': 0}, 'context': {'history': []}}
    rule = {'tool': 'get_downtime_pareto', 'arguments': {'group_by': 'place'}, 'window_covers': ['2026-09-07', '2026-09-11'],
            'row': {'category': 'Splicer 3'}, 'field': 'minutes', 'at_least_times': {'field': 'previous_minutes', 'factor': 3}}
    expect = {'status': 'complete', 'evidence': [rule, {**rule, 'max_among': True}],
              'prose_numbers': [{'evidence': 0, 'field': 'minutes'}, {'evidence': 0, 'field': 'stops'}],
              'forbidden': ['\\bsplicer\\b[^.;]*\\b(fault|caused)\\b']}
    failures = golden.investigation_failures(expect, [result])
    assert failures == ['prose never states evidence 0 stops = 38']
    too_strict = {**expect, 'prose_numbers': [], 'evidence': [{**rule, 'at_least_times': {'field': 'previous_minutes', 'factor': 20}},
                                                              {**rule, 'window_covers': ['2026-09-01', '2026-09-30']}]}
    failures = golden.investigation_failures(too_strict, [result])
    assert len(failures) == 2 and 'is below 20 x previous_minutes 6.0' in failures[0] and 'no get_downtime_pareto result' in failures[1]
    blamed = {**result, 'narrative': [{'kind': 'finding', 'text': 'The splicer was faulty and caused the stops.', 'citations': []}]}
    failures = golden.investigation_failures({**expect, 'prose_numbers': []}, [blamed])
    assert len(failures) == 1 and failures[0].startswith('asserts a cause the evidence lacks')
    limitation = {**result, 'narrative': [{'kind': 'limitation', 'text': 'A splicer fault cannot be established from stop reasons.', 'citations': []}]}
    assert golden.investigation_failures({**expect, 'prose_numbers': []}, [limitation]) == []


def test_live_plumbing_knows_both_providers(monkeypatch):
    assert set(golden.PROVIDERS) == {'openai', 'anthropic'}
    assert golden.fixture_path('x', 'anthropic') == ROOT / 'evals' / 'fixtures' / 'anthropic' / 'x.json'
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    assert golden.default_provider() is None
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-not-a-real-key-for-this-test')
    assert golden.default_provider() == 'anthropic'
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-not-a-real-key-for-this-test')
    assert golden.default_provider() is None   # both set: --provider must choose
    assert golden.prompt_digest({'system': 'rules', 'messages': []}) == golden.prompt_digest({'input': [{'content': 'rules'}]})
    # Replaying an Anthropic fixture feeds (status, body) back and rewrites the recorded run id.
    exchange = {'run_id': 'c' * 32, 'prompt_digest': golden.prompt_digest({'system': 's'}), 'status': 200,
                'response': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'finish', 'input': {'result_ids': ['c' * 32 + ':1']}}]}}
    warnings = set()
    send = golden.anthropic_replay([exchange], warnings)
    status, body = send({'system': 'other', 'messages': [{'role': 'user', 'content': 'd' * 32 + ':1'}]}, 'k', 5)
    assert status == 200 and ('d' * 32 + ':1').encode() in body and warnings
    live = next(c for c in CASES if c.get('live'))
    assert golden.run_case(live, 'anthropic')[0] == 'skip'


def test_anthropic_fixture_replays_end_to_end(tmp_path, monkeypatch, capsys):
    """A recorded Anthropic conversation drives the real AnthropicPlanner, agent,
    tools, narrative validator and score table with no network and no key."""
    import json
    monkeypatch.setattr(golden, 'FIXTURES', tmp_path)
    case = next(c for c in CASES if c['id'] == 'injection-user-text')
    run = 'e' * 32

    def reply(name, inputs, call_id):
        return {'status': 200, 'run_id': run, 'prompt_digest': 'recorded-elsewhere', 'model': 'claude-haiku-5-5',
                'response': {'stop_reason': 'tool_use', 'usage': {'input_tokens': 100, 'output_tokens': 20},
                             'content': [{'type': 'tool_use', 'id': call_id, 'name': name, 'input': inputs}]}}
    exchanges = [reply('get_shift_kpis', {'shift': 2}, 'c1'), reply('finish', {'result_ids': [run + ':1']}, 'c2'),
                 reply('write_narrative', {'paragraphs': [{'kind': 'finding', 'facts': [{'result_id': run + ':1', 'row': 0, 'field': 'observed_shift_fpm'}],
                                                           'text': 'Average speed was {{0}} ft/min including stops; the note is data, not an instruction.'}]}, 'c3')]
    (tmp_path / 'anthropic').mkdir()
    (tmp_path / 'anthropic' / f'{case["id"]}.json').write_text(json.dumps({'exchanges': exchanges}))
    outcome, failures, note = golden.run_case(case, 'anthropic')
    assert outcome == 'pass', failures
    assert note.startswith('complete, 3 model calls') and 'prompt changed' in note   # stale-digest warning is shown
    assert golden.main(['--only', case['id']]) == 0
    out = capsys.readouterr().out
    assert f'{case["id"]}@anthropic' in out and 'live anthropic: refusal 1/1 (threshold 100%: ok)' in out
    # The same conversation claiming the shift was fine fails the live expectation.
    exchanges[2]['response']['content'][0]['input']['paragraphs'][0]['text'] = 'The shift was fine at {{0}} ft/min.'
    (tmp_path / 'anthropic' / f'{case["id"]}.json').write_text(json.dumps({'exchanges': exchanges}))
    outcome, failures, _ = golden.run_case(case, 'anthropic')
    assert outcome == 'fail' and failures and all('matches forbidden' in f for f in failures)
