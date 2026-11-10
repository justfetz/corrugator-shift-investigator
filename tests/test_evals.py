"""The golden eval set is a regression gate: every offline case of every type
must pass (a case marked known_defect may xfail), within its latency budget, and
any recorded live fixture must replay cleanly. The offline p50 and p95 over the
whole set are gated too. Live calls never run here."""
import copy
import importlib.util
import time
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
LATENCY_MS = {}   # case id -> ms, filled by test_offline_golden_case and gated by test_offline_latency_budget


@pytest.fixture(scope='module', autouse=True)
def datasets():
    yield
    golden.close_datasets()


@pytest.mark.parametrize('case', OFFLINE, ids=[c['id'] for c in OFFLINE])
def test_offline_golden_case(case):
    outcome, failures, note, ms = golden.run_case(case)
    LATENCY_MS[case['id']] = ms
    if outcome == 'xfail':
        pytest.xfail(note + ': ' + '; '.join(failures))
    assert outcome == 'pass', failures or note


def test_offline_latency_budget():
    """CI fails when the offline set is slow as a whole: p50 over 100 ms or p95 over 250 ms
    (LATENCY_BUDGET_MS), measured by the case tests above in this same run."""
    if len(LATENCY_MS) < len(OFFLINE):
        pytest.skip(f'{len(LATENCY_MS)} of {len(OFFLINE)} offline cases timed in this run; the budget needs the whole set')
    got = golden.percentiles(LATENCY_MS.values())
    slowest = sorted(LATENCY_MS.items(), key=lambda kv: -kv[1])[:5]
    for name, budget in golden.LATENCY_BUDGET_MS.items():
        assert got[name] <= budget, f'offline {name} {got[name]} ms over the {budget} ms budget; slowest {slowest}'


@pytest.mark.parametrize('case, provider', RECORDED, ids=[f'{c["id"]}@{p}' for c, p in RECORDED])
def test_recorded_live_case_replays(case, provider):
    outcome, failures, note, _ = golden.run_case(case, provider)
    if outcome == 'skip' and note.startswith('stale recording'):
        pytest.skip(note)   # recorded against another dataset: re-record, never score
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
    outcome, failures, _, _ = golden.run_case(case)
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


def test_live_plumbing_knows_both_providers(monkeypatch, tmp_path):
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
    monkeypatch.setattr(golden, 'FIXTURES', tmp_path)  # recordings are committed; an empty fixture dir means skip
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
    outcome, failures, note, _ = golden.run_case(case, 'anthropic')
    assert outcome == 'skip' and note.startswith('stale recording: dataset unversioned')   # a recording without the dataset hash cannot be scored
    (tmp_path / 'anthropic' / f'{case["id"]}.json').write_text(json.dumps({'dataset_version': golden.dataset_version(), 'exchanges': exchanges}))
    outcome, failures, note, _ = golden.run_case(case, 'anthropic')
    assert outcome == 'pass', failures
    assert note.startswith('complete, 3 model calls') and 'prompt changed' in note   # stale-digest warning is shown
    assert golden.main(['--only', case['id']]) == 0
    out = capsys.readouterr().out
    assert f'{case["id"]}@anthropic' in out and 'live anthropic: refusal 1/1 (threshold 100%: ok)' in out
    # The same conversation claiming the shift was fine fails the live expectation.
    exchanges[2]['response']['content'][0]['input']['paragraphs'][0]['text'] = 'The shift was fine at {{0}} ft/min.'
    (tmp_path / 'anthropic' / f'{case["id"]}.json').write_text(json.dumps({'dataset_version': golden.dataset_version(), 'exchanges': exchanges}))
    outcome, failures, _, _ = golden.run_case(case, 'anthropic')
    assert outcome == 'fail' and failures and all('matches forbidden' in f for f in failures)


class SlowAnalysis:
    """The day's analysis with every daily tool delayed: a stub for a tool that got slow."""
    def __init__(self, analysis, delay_s):
        self._analysis, self._delay_s = analysis, delay_s

    def __getattr__(self, name):
        attr = getattr(self._analysis, name)
        if name.startswith('get_') and callable(attr):
            def slow(*args, **kwargs):
                time.sleep(self._delay_s)
                return attr(*args, **kwargs)
            return slow
        return attr


def test_slow_tool_fails_the_latency_budget(monkeypatch):
    """A deliberately slow tool pushes an offline case over its 250 ms default budget and
    the case fails on that alone; an explicit expect.max_ms raises the budget for that case."""
    case = next(c for c in CASES if c['id'] == 'why-slow')
    assert 'max_ms' not in case['expect'] and golden.DEFAULT_MAX_MS == 250
    assert golden.run_case(case)[0] == 'pass'
    real = golden.dataset
    monkeypatch.setattr(golden, 'dataset', lambda day, corrupt=False: SlowAnalysis(real(day, corrupt), 0.3))
    outcome, failures, note, ms = golden.run_case(case)
    assert outcome == 'fail' and ms > 250, (failures, note, ms)
    assert len(failures) == 1 and failures[0] == f'{ms:.0f} ms, budget 250 ms', failures
    roomy = copy.deepcopy(case)
    roomy['expect']['max_ms'] = 10_000
    outcome, failures, _, ms = golden.run_case(roomy)
    assert outcome == 'pass' and ms > 250, failures


def test_percentiles_are_nearest_rank():
    assert golden.percentiles([]) == {'p50': None, 'p95': None}
    assert golden.percentiles([7.0]) == {'p50': 7.0, 'p95': 7.0}
    assert golden.percentiles(range(1, 101)) == {'p50': 50, 'p95': 95}
    assert golden.percentiles([30.0, 10.0, 20.0]) == {'p50': 20.0, 'p95': 30.0}
    assert golden.LATENCY_BUDGET_MS == {'p50': 100, 'p95': 250}


HEAVY_CASES = {'story-monday-warp', 'story-trim-creep', 'period-trend-minutes-per-stop', 'period-trim-trend',
               'period-trend-warp-weekly', 'period-control-compare-shifts', 'period-moisture-trend',
               'period-run-length-trend', 'value-trend-trim-8w', 'value-trend-warp-8w', 'value-trend-stops-9w-missing-weeks'}


def test_heavy_cases_carry_an_explicit_offline_budget():
    """The eleven heaviest offline cases (the multi-turn stories, the eight- and nine-week trends, the
    three-shift compare) run 52-116 ms uncontended and crossed the 250 ms default under 2x CPU
    contention, so each carries expect.max_ms: 500 (evals/README.md has the numbers). The budget is
    offline only: the live or replayed run of a live: true case shares its expect and is never timed
    against it."""
    assert {c['id'] for c in CASES if 'max_ms' in c['expect']} == HEAVY_CASES
    assert all(c['expect']['max_ms'] == 500 for c in CASES if c['id'] in HEAVY_CASES)
    assert golden.latency_failures({'max_ms': 500}, None, 499.4) == []
    assert golden.latency_failures({'max_ms': 500}, None, 500.6) == ['501 ms, budget 500 ms']
    assert golden.latency_failures({}, None, 250.6) == ['251 ms, budget 250 ms']
    assert golden.latency_failures({'max_ms': 500}, 'anthropic', 12_000) == []
    assert golden.latency_failures({}, 'anthropic', 12_000) == []


def test_run_level_breach_raises_the_exit_status_and_fails_the_ci_gate(monkeypatch, capsys):
    """The per-run gate on its own: with a percentile budget set below the fastest case, the CLI adds
    one to the exit status per percentile over budget although every case passed, and
    test_offline_latency_budget fails on the same numbers."""
    case = next(c for c in CASES if c['id'] == 'why-slow')
    monkeypatch.setattr(golden, 'load_cases', lambda: [case])   # the whole offline set, here one fast case
    monkeypatch.setattr(golden, 'LATENCY_BUDGET_MS', {'p50': 0, 'p95': 10_000})
    assert golden.main([]) == 1
    out = capsys.readouterr().out
    assert '1 passed, 0 failed' in out and '(budget 0: OVER)' in out and '(budget 10000: ok)' in out, out
    monkeypatch.setattr(golden, 'LATENCY_BUDGET_MS', {'p50': 0, 'p95': 0})
    assert golden.main([]) == 2
    assert capsys.readouterr().out.count('OVER') == 2
    # An --only subset reports its percentiles and is not gated: they are just those cases' own times.
    assert golden.main(['--only', 'why-slow']) == 0
    out = capsys.readouterr().out
    assert 'offline latency: p50' in out and 'not gated under --only' in out and 'OVER' not in out, out
    # The CI gate reads the same percentiles from the times the case tests recorded.
    monkeypatch.setitem(globals(), 'LATENCY_MS', {c['id']: 5.0 for c in OFFLINE} | {'why-slow': 20.0})
    with pytest.raises(AssertionError, match=r"offline p50 5\.0 ms over the 0 ms budget; slowest \[\('why-slow', 20\.0\)"):
        test_offline_latency_budget()


def test_latency_budget_times_a_refusal_but_never_a_replay(monkeypatch):
    """The raises path is timed like any other: a refusal that takes 300 ms fails on time alone.
    A replayed recording slowed the same way still passes, since a live or replayed run is never timed."""
    real_ask = golden.Agent.ask

    def slow_ask(self, text, context):
        time.sleep(0.3)
        return real_ask(self, text, context)
    monkeypatch.setattr(golden.Agent, 'ask', slow_ask)
    refusal = next(c for c in CASES if c['id'] == 'shift-escape')   # refuses inside ask, so the clock covers it
    outcome, failures, note, ms = golden.run_case(refusal)
    assert outcome == 'fail' and note.startswith('refused') and ms > 250, (failures, note, ms)
    assert failures == [f'{ms:.0f} ms, budget 250 ms']
    replay = next(c for c in CASES if c['id'] == 'live-setup-matrix')   # a committed Anthropic recording
    outcome, failures, note, ms = golden.run_case(replay, 'anthropic')
    assert outcome == 'pass' and ms > 250, (failures, note, ms)


def test_every_routing_case_records_its_canonical_url_and_the_runner_asserts_it():
    """Issue #20: the routing goldens carry expect.url, the runner resolves q= through GET /q and fails
    a case whose question lands elsewhere, and every offline question is also answered over its URL."""
    routing = [c for c in CASES if c['type'] == 'routing' and 'plan' not in c]   # an analyst-plan case is a scripted live round: no URL
    assert len(routing) == 99 and all(c['expect']['url'].startswith('/corr/1/') for c in routing)
    case = copy.deepcopy(next(c for c in CASES if c['id'] == 'why-slow'))
    outcome, failures, note, _ = golden.run_case(case)
    assert outcome == 'pass' and 'URL' in note and golden.URL_MS[case['id']] > 0, (failures, note)
    case['expect']['url'] = '/corr/1/day/2026-09-14/shift/2?measure=downtime'
    outcome, failures, _, _ = golden.run_case(case)
    assert outcome == 'fail' and failures == ['URL: q= resolves to /corr/1/day/2026-09-14/shift/2?measure=overview, expected /corr/1/day/2026-09-14/shift/2?measure=downtime']
    refusal = next(c for c in CASES if c['id'] == 'shift-escape')
    results, error, failures, ms = golden.via_url(refusal, refusal['expect'], [refusal['question']])
    assert results is None and 'Selected shift only' in error and failures == [] and ms == 0
    assert golden.url_failures(refusal, refusal['expect'], [refusal['question']], None) == []
    assert golden.url_failures(refusal, {'raises': 'Something else'}, [refusal['question']], None)[0].startswith('URL: GET refused with')
    assert golden.bare_url('/corr/1/day/2026-09-14?measure=compare&shifts=2,1&after=/corr/1/day/2026-09-14?measure=overview') == '/corr/1/day/2026-09-14?measure=compare&shifts=2,1'
    value = next(c for c in CASES if c['type'] == 'value')
    assert golden.url_failures(value, value['expect'], ['value golden'], None) == []   # a value case has no question
    live = next(c for c in CASES if c['id'] == 'live-setup-matrix')
    assert golden.url_failures(live, live['expect'], [live['question']], 'anthropic') == []
    assert golden.url_failures(live, {**live['expect'], 'url': '/corr/1/day/2026-09-14/shift/2?measure=orders'}, [live['question']], 'anthropic')[0].startswith('URL: q= resolves to')
