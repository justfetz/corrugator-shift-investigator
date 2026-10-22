"""The value goldens are generated, never typed: the committed file must be
exactly what `evals/make_value_goldens.py` produces from plain sums over the
record families for the live store, and its calendar arithmetic must agree
with the store's independently."""
import importlib.util
import json
from datetime import date
from pathlib import Path
import pytest
from investigator.record_store import KPI_CATALOG, TREND_METRICS, default_store, parse_window, previous_window

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('make_value_goldens', ROOT / 'evals' / 'make_value_goldens.py')
maker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maker)

COMMITTED = json.loads((ROOT / 'evals' / 'golden_values.json').read_text())


def test_committed_file_is_what_the_generator_produces():
    maker.check_catalog()
    fresh = json.loads(maker.render(maker.build()))
    assert fresh['content_hash'] == default_store().content_hash == COMMITTED['content_hash']
    assert fresh == COMMITTED, 'run python3 evals/make_value_goldens.py and commit the result'


def test_value_cases_cover_the_tiles_and_tools_the_brief_names():
    cases = COMMITTED['cases']
    assert 40 <= len(cases) <= 70 and len({c['id'] for c in cases}) == len(cases)
    tools = {c['tool'] for c in cases}
    assert tools == {'get_period_kpis', 'compare_periods', 'get_downtime_pareto', 'get_waste_breakdown',
                     'get_stops_profile', 'get_trend', 'get_shift_kpis', 'get_downtime_breakdown', 'get_quality_breakdown'}
    tiles = {key for key, *_ in KPI_CATALOG}
    for case in cases:
        if case['tool'] in ('get_period_kpis', 'compare_periods'):
            assert {s.split('=')[1] for s in case['expect']['values'] if s.startswith('metric=')} == tiles
        for fields in case['expect']['values'].values():
            for field, pair in fields.items():
                assert isinstance(pair, list) and len(pair) == 2, (case['id'], field)
                expected, tolerance = pair
                assert tolerance is None or (isinstance(tolerance, (int, float)) and tolerance >= 0)
                if expected is None:
                    assert tolerance is None
    by_id = {c['id']: c for c in cases}
    august = by_id['value-kpi-august-all']['expect']['values']
    assert august['metric=volume_msf']['previous'] == [None, None] and august['metric=stops']['delta'] == [None, None]
    assert by_id['value-trend-stops-9w-missing-weeks']['expect']['values']['$']['missing_weeks'] == [['2026-07-20', '2026-07-27'], None]
    corrupt = by_id['value-dq-corrupt-trim-excluded']
    assert corrupt['corrupt'] is True and corrupt['expect']['coverage'] == 'incomplete'
    [excluded] = corrupt['expect']['excluded_records']
    assert excluded['raw']['trim_sqft'] == 99999999.0 and excluded['record_id'].startswith('CORR20260914')
    clean = by_id['value-day-kpis-0914-shift-2']['expect']['values']['#0']
    assert corrupt['expect']['values']['#0']['valid_setups'][0] == clean['valid_setups'][0] - 1
    assert {m for m in TREND_METRICS} <= set(maker.TREND_PLACES)
    assert all(c['type'] == 'value' and c['mode'] == 'offline' for c in cases)


@pytest.mark.parametrize('period, anchor', [
    ('day', '2026-09-14'), ('day', '2026-08-03'), ('week', '2026-09-16'), ('week', '2026-09-19'), ('week', '2026-08-05'),
    ('month', '2026-09-14'), ('month', '2026-08-14'), ('week', '2026-09-29')])
def test_generator_calendar_agrees_with_the_store(period, anchor):
    start, end = maker.window(period, anchor)
    assert (start, end) == parse_window({'period': period, 'anchor': anchor})[2:]
    assert maker.previous(period, start, end) == previous_window(period, start, end)
    plain = maker.Plain(default_store_data())
    assert plain.coverage(start, end) == default_store()._describe(period, start, end)['coverage']


_DATA = {}


def default_store_data():
    if 'data' not in _DATA:
        _DATA['data'] = maker.generate_months(seed=maker.SEED, months=maker.MONTHS, machine_id=maker.MACHINE)
    return _DATA['data']


def test_plain_tiles_are_ratios_of_sums_not_averages_of_averages():
    plain = maker.Plain(default_store_data())
    month = plain.tiles(plain.sums(date(2026, 9, 1), date(2026, 9, 30)))
    days = [d for d in sorted(plain.days) if d.month == 9]
    per_day = [plain.tiles(plain.sums(d, d))['trim_pct'] for d in days]
    assert month['trim_pct'] != pytest.approx(sum(per_day) / len(per_day), abs=1e-6)
    assert month['trim_pct'] == pytest.approx(3.1819, abs=1e-4) and month['stops'] == 558
