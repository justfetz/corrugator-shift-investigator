"""The golden eval set is a regression gate: offline cases must all pass, and
any recorded live fixture must replay cleanly. Live calls never run here."""
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault('EVALS_DIR', str(ROOT / 'evals'))
from investigator import evals  # noqa: E402

CASES = evals.load_cases()
OFFLINE = [c for c in CASES if c['mode'] == 'offline']
RECORDED = [c for c in CASES if c['mode'] == 'live' and (ROOT / 'evals' / 'fixtures' / f'{c["id"]}.json').exists()]


@pytest.fixture(scope='module', autouse=True)
def datasets():
    yield
    evals.close_datasets()


@pytest.mark.parametrize('case', OFFLINE, ids=[c['id'] for c in OFFLINE])
def test_offline_golden_case(case):
    report = evals.run_case(case)
    assert report['outcome'] == 'pass', evals.failures(report) or report['note']


@pytest.mark.parametrize('case', RECORDED, ids=[c['id'] for c in RECORDED])
def test_recorded_live_case_replays(case):
    report = evals.run_case(case)
    assert report['outcome'] == 'pass', evals.failures(report) or report['note']


def test_every_case_type_is_known_and_described():
    assert {c['type'] for c in CASES} <= set(evals.TYPES)
    assert len({c['id'] for c in CASES}) == len(CASES)
    for case in CASES:
        assert evals.describe_expectations(case), case['id']


def test_unfaithful_number_detector_catches_an_invented_total():
    trace = [{'ok': True, 'tool': 'get_downtime_breakdown', 'result_id': 'a' * 32 + ':1',
              'arguments': {'shift': 2}, 'data': {'shift': 2, 'coverage': 'complete',
              'rows': [{'category': 'Knife jam', 'down_minutes': 12.0, 'shift_percent': 2.5}], 'excluded_records': []}}]
    assert evals.unfaithful_numbers(['Shift 2 recorded downtime: Knife jam: 12 min.'], trace) == []
    bad = evals.unfaithful_numbers(['Shift 2 recorded downtime: Knife jam: 15 min.'], trace)
    assert bad and bad[0].startswith("'15'")


def test_every_check_is_reported_not_only_failures():
    report = evals.run_case(next(c for c in OFFLINE if c['id'] == 'why-slow'))
    names = [c['check'] for c in report['checks']]
    assert 'offline is free' in names and 'tools in allow-list' in names and 'numbers from tool rows' in names
    assert all(c['ok'] for c in report['checks'])
    assert report['answer']['trace'][0]['tool'] == 'get_shift_kpis'
