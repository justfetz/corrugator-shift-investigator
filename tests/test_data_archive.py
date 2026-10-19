"""The committed data/ directory is the generator's output, byte for byte, and the
plain-SQL verifier agrees with the app's tools."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from investigator.fixture import generate
from investigator.server import DAYS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
MANIFEST = json.loads((DATA / 'manifest.json').read_text())


def test_manifest_covers_every_demo_day():
    assert [d['production_day'] for d in MANIFEST['days']] == DAYS
    assert MANIFEST['synthetic'] is True


@pytest.mark.parametrize('entry', MANIFEST['days'][::7], ids=lambda e: e['production_day'])
def test_committed_xml_matches_the_generator(entry):
    data = (DATA / entry['file']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry['sha256']
    assert generate(seed=entry['seed'], production_day=entry['production_day']) == data
    assert entry['seed'] == 7 + DAYS.index(entry['production_day'])


def test_csv_row_counts_match_manifest():
    with open(DATA / 'csv' / 'setups.csv', newline='') as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == sum(d['setups'] for d in MANIFEST['days'])
    assert {r['production_day'] for r in rows} == set(DAYS)
    assert set(rows[0]) >= {'production_day', 'shift', 'wet_end_id', 'grade', 'target', 'seconds', 'feet', 'gross', 'trim', 'shear', 'rejects', 'reject_reason'}
    with open(DATA / 'csv' / 'shifts.csv', newline='') as handle:
        assert sum(1 for _ in csv.DictReader(handle)) == 90


@pytest.mark.parametrize('day,shift', [('2026-09-14', 2), ('2026-09-01', 1), ('2026-09-29', 3)])
def test_plain_sql_over_csv_agrees_with_the_app(day, shift):
    spec = importlib.util.spec_from_file_location('verify_day', ROOT / 'tools' / 'verify_day.py')
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    assert verify.main([day, str(shift)]) == 0
