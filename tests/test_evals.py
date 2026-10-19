"""The golden eval set is a regression gate: offline cases must all pass, and
any recorded live fixture must replay cleanly. Live calls never run here."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('golden_run', ROOT / 'evals' / 'run.py')
golden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden)

CASES = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
OFFLINE = [c for c in CASES if c['mode'] == 'offline']
RECORDED = [c for c in CASES if c['mode'] == 'live' and (ROOT / 'evals' / 'fixtures' / f'{c["id"]}.json').exists()]


@pytest.fixture(scope='module', autouse=True)
def datasets():
    yield
    golden.close_datasets()


@pytest.mark.parametrize('case', OFFLINE, ids=[c['id'] for c in OFFLINE])
def test_offline_golden_case(case):
    outcome, failures, note = golden.run_case(case, live=False, record=False)
    assert outcome == 'pass', failures or note


@pytest.mark.parametrize('case', RECORDED, ids=[c['id'] for c in RECORDED])
def test_recorded_live_case_replays(case):
    outcome, failures, note = golden.run_case(case, live=False, record=False)
    assert outcome == 'pass', failures or note


def test_every_case_type_is_known():
    assert {c['type'] for c in CASES} <= {'routing', 'faithfulness', 'grounding', 'refusal'}
    assert len({c['id'] for c in CASES}) == len(CASES)


def test_unfaithful_number_detector_catches_an_invented_total():
    trace = [{'ok': True, 'tool': 'get_downtime_breakdown', 'result_id': 'a' * 32 + ':1',
              'arguments': {'shift': 2}, 'data': {'shift': 2, 'coverage': 'complete',
              'rows': [{'category': 'Knife jam', 'down_minutes': 12.0, 'shift_percent': 2.5}], 'excluded_records': []}}]
    assert golden.unfaithful_numbers(['Shift 2 recorded downtime: Knife jam: 12 min.'], trace) == []
    bad = golden.unfaithful_numbers(['Shift 2 recorded downtime: Knife jam: 15 min.'], trace)
    assert bad and bad[0].startswith("'15'")
