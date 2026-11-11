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
    assert 40 <= len(cases) <= 90 and len({c['id'] for c in cases}) == len(cases)
    tools = {c['tool'] for c in cases}
    assert tools == {'get_period_kpis', 'compare_periods', 'get_downtime_pareto', 'get_waste_breakdown',
                     'get_stops_profile', 'get_trend', 'get_grade_totals', 'get_overrun', 'get_speed_opportunity', 'get_rollstock',
                     'get_shift_kpis', 'get_downtime_breakdown', 'get_quality_breakdown',
                     'get_shift_dollars', 'get_opportunities', 'get_speed_variance', 'get_day_dollars'}   # get_day_dollars: the #31b review
    dollars = [c for c in cases if c['id'].startswith('value-dollars-')]
    assert len(dollars) >= 6 and any(c.get('rates') for c in dollars)   # issue #31: dollar cases, one with changed rates
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
    # a window before the dataset's first day reads null, never zero (the data's first week, issue #100)
    first = by_id['value-kpi-first-week-no-previous']['expect']['values']
    assert first['metric=volume_msf']['previous'] == [None, None] and first['metric=stops']['delta'] == [None, None]
    august = by_id['value-kpi-august-all']['expect']['values']   # July is inside the year: a full comparison
    assert august['metric=volume_msf']['previous'][0] > 0 and august['metric=stops']['delta'][0] is not None
    assert by_id['value-trend-stops-9w-missing-weeks']['expect']['values']['$']['missing_weeks'] == [['2024-12-16', '2024-12-23'], None]
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
    ('month', '2026-09-14'), ('month', '2026-08-14'), ('week', '2026-09-29'),
    # the data's first days (issue #100): a partial first week, the first month, the month and 4-4-5 period before
    # the data, and the 4-4-5 periods on both sides of 2026-08-03
    ('day', '2025-01-01'), ('week', '2025-01-02'), ('month', '2025-01-15'), ('month', '2024-12-15'),
    ('fiscal', '2025-01-15'), ('fiscal', '2024-12-15'), ('fiscal', '2026-08-02'), ('fiscal', '2026-08-14'), ('fiscal', '2026-09-30')])
def test_generator_calendar_agrees_with_the_store(period, anchor):
    start, end = maker.window(period, anchor)
    assert (start, end) == parse_window({'period': period, 'anchor': anchor})[2:]
    assert maker.previous(period, start, end) == previous_window(period, start, end)
    plain = maker.Plain(default_store_data())
    assert plain.coverage(start, end) == default_store()._describe(period, start, end)['coverage']


_DATA = {}


def default_store_data():
    if 'data' not in _DATA:
        _DATA['data'] = maker.generate()
    return _DATA['data']


def test_the_goldens_the_runner_and_the_tests_name_one_window():
    """The value goldens are generated for the window ending maker.AS_OF; evals/run.py scores the golden
    set against the store of that window; tests/conftest.py pins the default store to it."""
    runner_spec = importlib.util.spec_from_file_location('golden_runner', ROOT / 'evals' / 'run.py')
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    from conftest import GOLDEN_AS_OF
    assert maker.AS_OF == runner.GOLDEN_AS_OF == GOLDEN_AS_OF == COMMITTED['as_of']
    assert COMMITTED['first_day'] == '2025-01-01' and default_store().first_day == '2025-01-01'
    assert runner.golden_store() is default_store()   # the session's store already holds that window: no second store


def test_plain_tiles_are_ratios_of_sums_not_averages_of_averages():
    plain = maker.Plain(default_store_data())
    month = plain.tiles(plain.sums(date(2026, 9, 1), date(2026, 9, 30)))
    days = [d for d in sorted(plain.days) if d.month == 9]
    per_day = [plain.tiles(plain.sums(d, d))['trim_pct'] for d in days]
    assert month['trim_pct'] != pytest.approx(sum(per_day) / len(per_day), abs=1e-6)
    assert month['trim_pct'] == pytest.approx(3.4851, abs=1e-4) and month['stops'] == 669   # issue #100: was 3.2256 and 673 (3.2274 and 617 before #35)
