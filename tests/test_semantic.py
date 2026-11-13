"""The semantic model and its compiler (issue #117, phase 1).

The differential tests are the heart of it: for every measure of `semantic_model.json`, the compiler's number for a
golden day, shift and window equals the number an existing tool returns for the same thing (downtime minutes by
reason = get_downtime_breakdown and get_downtime_pareto, side trim % = the KPI rows, and so on), to the tool's own
display rounding. A measure no tool returns yet (order changes) is checked against a count of the record objects.
`test_every_measure_has_a_differential_check` fails when a measure is added to the model without one.
"""
import copy
import json
import re
from datetime import date, timedelta

import pytest

from investigator import cost_model, semantic as S, targets
from pathlib import Path

from investigator.agent import PLAN_TOOLS, TOOL_SCHEMAS, Agent, Context, route, scope_violation
from investigator.record_store import TABLES, default_store, fiscal_period
from investigator.urls import canonical_url, page_url, redirect_for, resolve_url

ROOT = Path(__file__).resolve().parents[1]
DAYS = ('2026-09-14', '2026-09-16', '2026-09-17')   # a Monday; the September outage day (shift 3); a planned-maintenance Thursday
WEEK = {'period': 'week', 'anchor': '2026-09-09'}
MONTH = {'period': 'month', 'anchor': '2026-09-14'}
PERIOD_CASES = ((WEEK, None), (WEEK, 2), (MONTH, None), (MONTH, 3))
_DAYS = {}


@pytest.fixture(scope='module')
def store():
    yield default_store()
    for analysis in _DAYS.values():
        analysis.close()
    _DAYS.clear()


def day_analysis(store, day):
    if day not in _DAYS:
        _DAYS[day] = store.day_analysis(day)
    return _DAYS[day]


def run(store, measure, window, **kw):
    return S.run(S.SemanticQuery(measure, window=window, **kw), store)


def rows_by(result, *dims):
    return {tuple(r[d] for d in dims) if len(dims) > 1 else r[dims[0]]: r for r in result['rows']}


def close(got, want, places=None, rel=1e-9):
    """`want` is a tool's value rounded to `places` decimals (None: unrounded, float noise only)."""
    if want is None or got is None:
        return want is None and got is None
    if places is None:
        return abs(got - want) <= abs(want) * rel + 1e-6
    return abs(got - want) <= 0.51 * 10 ** -places + 1e-9


def day_shifts():
    return [(day, shift) for day in DAYS for shift in (1, 2, 3)]


def window_of(day):
    return {'period': 'day', 'anchor': day}


# ------------------------------------------------------------------ differential checks, one family of tools each

def check_shift_kpis(measure, field, filters=None):
    """The daily KPI row (Analysis.get_shift_kpis, unrounded) for every shift of the golden days."""
    def check(store):
        for day, shift in day_shifts():
            want = day_analysis(store, day).get_shift_kpis(shift)['rows'][0][field]
            got = run(store, measure, window_of(day), shift=shift, filters=filters or {})['value']
            assert close(got, want), (measure, field, day, shift, got, want)
    return check


def check_breakdown(measure, field):
    """get_downtime_breakdown by reason, place and kind, unrounded, for every shift of the golden days."""
    def check(store):
        for day, shift in day_shifts():
            for group in ('reason', 'place', 'kind'):
                tool = day_analysis(store, day).get_downtime_breakdown(shift, group_by=group)
                cost_model.attach('get_downtime_breakdown', tool, cost_model.effective())
                got = rows_by(run(store, measure, window_of(day), shift=shift, group_by=[group], limit=200), group)
                assert set(got) == {r['category'] for r in tool['rows']}, (measure, day, shift, group)
                for r in tool['rows']:
                    assert close(got[r['category']]['value'], r[field]), (measure, day, shift, group, r['category'])
    return check


def check_quality(measure, field):
    """get_quality_breakdown by reject family, unrounded (its dollars priced against the shift's gross and 480 minutes)."""
    def check(store):
        for day, shift in day_shifts():
            analysis = day_analysis(store, day)
            tool = analysis.get_quality_breakdown(shift)
            cost_model.attach('get_quality_breakdown', tool, cost_model.effective(), analysis.get_shift_kpis(shift)['rows'][0]['gross_sqft'])
            got = rows_by(run(store, measure, window_of(day), shift=shift, group_by=['reject_family']), 'reject_family')
            assert set(got) == {r['reason'] for r in tool['rows']}
            for r in tool['rows']:
                assert close(got[r['reason']]['value'], r[field], places=2 if field == 'usd' else None), (measure, day, shift, r['reason'])
    return check


def check_wet_end(measure, field):
    """get_wet_end_performance by paper run, unrounded."""
    def check(store):
        for day, shift in day_shifts():
            tool = day_analysis(store, day).get_wet_end_performance(shift)
            got = rows_by(run(store, measure, window_of(day), shift=shift, group_by=['wet_end'], limit=200), 'wet_end')
            assert set(got) == {r['wet_end_id'] for r in tool['rows']}
            for r in tool['rows']:
                assert close(got[r['wet_end_id']]['value'], r[field]), (measure, day, shift, r['wet_end_id'])
    return check


def check_period_kpis(measure, metric, places, filters=None):
    """get_period_kpis' tile (rounded to `places`) over weeks and months, all shifts and one."""
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = next(r for r in store.get_period_kpis(window, compare=False, shift=shift)['rows'] if r['metric'] == metric)
            got = run(store, measure, window, shift=shift, filters=filters or {})['value']
            assert close(got, tool['current'], places), (measure, metric, window, shift, got, tool['current'])
    return check


def check_pareto(measure, field, places):
    """get_downtime_pareto by reason, place and kind (rounded), over weeks and months."""
    def check(store):
        for window, shift in PERIOD_CASES:
            for group in ('reason', 'place', 'kind'):
                tool = store.get_downtime_pareto(window, group, 'minutes', shift)
                if field == 'usd':
                    cost_model.attach('get_downtime_pareto', tool, cost_model.effective())
                got = rows_by(run(store, measure, window, shift=shift, group_by=[group], limit=200), group)
                assert set(got) == {r['category'] for r in tool['rows']}
                for r in tool['rows']:
                    value = got[r['category']]['share_pct' if field == 'share_pct' else 'value']
                    # the Pareto prices its minutes rounded to two decimals: a cent or two apart at most
                    assert close(value, r[field], places) or (field == 'usd' and abs(value - r[field]) <= 0.21), (measure, window, shift, group, r)
    return check


def check_waste(measure, category, field='sqft', places=1, filters=None):
    """get_waste_breakdown's row for one waste category (rounded), over weeks and months."""
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_waste_breakdown(window, shift)
            cost_model.attach('get_waste_breakdown', tool, cost_model.effective())
            row = next(r for r in tool['rows'] if r['category'] == category)
            got = run(store, measure, window, shift=shift, filters=filters or {})['value']
            # the breakdown prices its square feet rounded to one decimal: a cent apart at most
            assert close(got, row[field], places) or (field == 'usd' and abs(got - row[field]) <= 0.02), (measure, category, window, shift, got, row[field])
    return check


def check_waste_envelope(measure, key):
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_waste_breakdown(window, shift)
            assert close(run(store, measure, window, shift=shift)['value'], tool[key], 1), (measure, key, window, shift)
    return check


def check_waste_details(measure, field, places):
    """get_waste_breakdown's details: each reported sub-reason under its family."""
    def check(store):
        for window, shift in PERIOD_CASES:
            details = store.get_waste_breakdown(window, shift)['details']
            got = rows_by(run(store, measure, window, shift=shift, group_by=['reject_family', 'reject_reason'], limit=200), 'reject_family', 'reject_reason')
            assert set(got) == {(d['reason'], d['detail']) for d in details}
            for d in details:
                assert close(got[(d['reason'], d['detail'])]['value'], d[field], places), (measure, window, shift, d)
    return check


def check_grade_totals(measure, field, places, tolerance=None):
    """get_grade_totals by grade (rounded), over weeks and months."""
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_grade_totals(window, shift)
            got = rows_by(run(store, measure, window, shift=shift, group_by=['grade']), 'grade')
            assert set(got) == {r['grade'] for r in tool['rows']}
            for r in tool['rows']:
                value = got[r['grade']]['value']
                ok = abs(value - r[field]) <= tolerance if tolerance is not None else close(value, r[field], places)
                assert ok, (measure, field, window, shift, r['grade'], value, r[field])
    return check


def check_overrun(measure, field, places=None):
    """get_overrun by knife and over all knives, over weeks and months."""
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_overrun(window, shift)
            result = run(store, measure, window, shift=shift, group_by=['lane'])
            got = rows_by(result, 'lane')
            for r in tool['rows']:
                value = result['value'] if r['knife'] == 'all knives' else got[int(r['knife'].split()[1])]['value']
                assert close(value, r[field], places) if places is not None else value == r[field], (measure, field, window, shift, r['knife'])
    return check


def check_review(measure, places):
    """get_downtime_review: every reason under its kind, and the maintenance and operator totals, against scheduled minutes."""
    field = {'downtime_pct': 'pct_of_scheduled', 'downtime_minutes': 'minutes', 'stops': 'stops'}[measure]
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_downtime_review(window, shift)
            for kind in ('Maintenance', 'Operator'):
                got = rows_by(run(store, measure, window, shift=shift, group_by=['reason'], filters={'kind': kind}, limit=200), 'reason')
                for r in [r for r in tool['rows'] if r['kind'] == kind]:
                    assert close(got[r['reason']]['value'], r[field], places), (measure, window, shift, kind, r['reason'])
                total = run(store, measure, window, shift=shift, filters={'kind': kind})
                envelope = tool[f'{kind.lower()}_{ {"downtime_pct": "pct", "downtime_minutes": "minutes", "stops": "stops"}[measure] }']
                assert close(total['value'], envelope, places), (measure, window, shift, kind)
                if measure == 'downtime_pct':
                    assert total['band'] == tool[f'{kind.lower()}_band']   # the owner's band, read the same way
    return check


def check_stops_profile(measure, field, places):
    """get_stops_profile: one row per production day of the window. Its downtime % divides minutes it already rounded to
    two decimals, so its percent may be a rounding step of those minutes apart."""
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_stops_profile(window, shift)
            got = rows_by(run(store, measure, window, shift=shift, group_by=['day']), 'day')
            for r in tool['rows']:
                value = got[r['production_day']]['value'] if r['production_day'] in got else 0
                if field == 'downtime_pct':
                    assert abs(value - r[field]) <= 0.005 / (480 if shift else 1440) * 100 + 0.51e-4, (measure, window, shift, r)
                else:
                    assert close(value, r[field], places) if places is not None else value == r[field], (measure, window, shift, r)
    return check


def check_trend(measure, metric, places):
    """get_trend: the weekly series ending with the anchor's week."""
    def check(store):
        tool = store.get_trend(metric, 6, '2026-09-14')
        for r in tool['rows']:
            got = run(store, measure, {'period': 'week', 'anchor': r['week_start']})['value']
            assert close(got, r['value'], places), (measure, metric, r)
    return check


def check_rollstock(measure, field, places):
    """get_rollstock for one shift: the setups that shift ran on each paper run, by grade and web width."""
    def check(store):
        for window in (WEEK, MONTH):
            for shift in (1, 2):
                tool = store.get_rollstock(window, shift)
                got = rows_by(run(store, measure, window, shift=shift, group_by=['grade', 'web_width'], limit=200), 'grade', 'web_width')
                assert set(got) == {(r['grade'], r['width_in']) for r in tool['rows']}
                for r in tool['rows']:
                    assert close(got[(r['grade'], r['width_in'])]['value'], r[field], places), (measure, window, shift, r['label'])
    return check


def check_scheduled_minutes(store):
    """Scheduled minutes = the cost model's window minutes (production days x 480 a shift) and the review's own."""
    for window, shift in PERIOD_CASES:
        got = run(store, 'scheduled_minutes', window, shift=shift)['value']
        assert got == cost_model.window_minutes(store.get_period_kpis(window, compare=False, shift=shift))
        assert got == store.get_downtime_review(window, shift)['scheduled_minutes']


def check_order_changes(store):
    """No tool on this store counts order changes yet: the setups whose record carries change code O, counted from the
    record objects of each golden day."""
    for day, shift in day_shifts():
        with store._lock:
            records = store._day_records(day)['setups']
        want = sum(1 for s in records if s.shift_number == shift and s.change_code == 'O')
        assert run(store, 'order_changes', window_of(day), shift=shift)['value'] == want


def check_target_ft(store):
    """Feet at grade target over the setup clock = lineal feet / speed to target (the daily KPI row, unrounded)."""
    for day, shift in day_shifts():
        row = day_analysis(store, day).get_shift_kpis(shift)['rows'][0]
        got = run(store, 'target_ft', window_of(day), shift=shift)['value']
        assert close(got, row['observed_lineal_ft'] / row['speed_to_target_pct'] * 100)


def check_running(measure):
    """Running minutes and feet at target over them, from the grade totals' speed without stops (rounded to a tenth)."""
    def check(store):
        for window, shift in PERIOD_CASES:
            for r in store.get_grade_totals(window, shift)['rows']:
                minutes = r['lineal_ft'] / r['speed_run_fpm']
                want = minutes * (r['target_fpm'] if measure == 'target_run_ft' else 1)
                got = run(store, measure, window, shift=shift, filters={'grade': r['grade']})['value']
                assert abs(got - want) <= abs(want) * 2e-4, (measure, window, shift, r['grade'], got, want)
    return check


def check_production_days(store):
    """Production days with a setup record = the days the KPI tiles divide MSF by (their day basis)."""
    for window, shift in PERIOD_CASES:
        assert run(store, 'production_days', window, shift=shift)['value'] == store.get_period_kpis(window, compare=False, shift=shift)['production_days_current']


def check_review_total(measure, key, places):
    """get_downtime_review's envelope: the operator-and-maintenance minutes and percent, and its band."""
    def check(store):
        for window, shift in PERIOD_CASES:
            tool = store.get_downtime_review(window, shift)
            got = run(store, measure, window, shift=shift)
            assert close(got['value'], tool[key], places), (measure, window, shift, got['value'], tool[key])
            if measure == 'op_maint_pct':
                assert got['band'] == tool['op_maint_band']
    return check


def check_maintenance_pm_minutes(store):
    """Maintenance and planned-maintenance minutes = the review's maintenance minutes plus its PM minutes (each rounded)."""
    for window, shift in PERIOD_CASES:
        tool = store.get_downtime_review(window, shift)
        got = run(store, 'maintenance_pm_minutes', window, shift=shift)['value']
        assert abs(got - tool['maintenance_minutes'] - tool['pm_minutes']) <= 0.011, (window, shift)


FAMILIES = {'bond': 'BOND', 'warp': 'WARP', 'misalignment': 'MISALIGNMENT', 'moisture': 'MOISTURE'}

CHECKS = {
    'setups': [check_shift_kpis('setups', 'valid_setups'), check_period_kpis('setups', 'setups', 0), check_grade_totals('setups', 'setups', 0)],
    'order_changes': [check_order_changes],
    'paper_changes': [check_shift_kpis('paper_changes', 'paper_changes'), check_period_kpis('paper_changes', 'paper_changes', 0),
                      check_grade_totals('paper_changes', 'wet_ends', 0), check_rollstock('paper_changes', 'paper_runs', 0)],
    'lineal_ft': [check_shift_kpis('lineal_ft', 'observed_lineal_ft'), check_period_kpis('lineal_ft', 'lineal_ft', 1),
                  check_grade_totals('lineal_ft', 'lineal_ft', 1), check_wet_end('lineal_ft', 'lineal_ft'), check_rollstock('lineal_ft', 'lineal_ft', 1)],
    'lineal_per_setup': [check_shift_kpis('lineal_per_setup', 'lineal_per_setup'), check_period_kpis('lineal_per_setup', 'lineal_per_setup', 1),
                         check_grade_totals('lineal_per_setup', 'lineal_per_setup', 1), check_trend('lineal_per_setup', 'lineal_per_setup', 1)],
    'lineal_per_paper_change': [check_shift_kpis('lineal_per_paper_change', 'lineal_per_wet_end'),
                                check_period_kpis('lineal_per_paper_change', 'lineal_per_paper_change', 1),
                                check_grade_totals('lineal_per_paper_change', 'lineal_per_wet_end', 1)],
    'gross_sqft': [check_shift_kpis('gross_sqft', 'gross_sqft'), check_waste_envelope('gross_sqft', 'gross_sqft')],
    'msf': [check_period_kpis('msf', 'volume_msf', 1), check_grade_totals('msf', 'volume_msf', 1), check_rollstock('msf', 'msf', 1)],
    'trim_sqft': [check_waste('trim_sqft', 'trim')],
    'trim_pct': [check_shift_kpis('trim_pct', 'trim_pct'), check_period_kpis('trim_pct', 'trim_pct', 4), check_grade_totals('trim_pct', 'trim_pct', 4),
                 check_trend('trim_pct', 'trim_pct', 4)],
    'shear_sqft': [check_waste('shear_sqft', 'shear')],
    'shear_pct': [check_shift_kpis('shear_pct', 'shear_pct'), check_period_kpis('shear_pct', 'shear_pct', 4)],
    'yield_sqft': [check_shift_kpis('yield_sqft', 'yield_sqft'), check_waste_envelope('yield_sqft', 'yield_sqft')],
    'good_output_sqft': [check_shift_kpis('good_output_sqft', 'estimated_good_sqft')],
    'reject_sqft': [check_quality('reject_sqft', 'rejected_sqft'), check_waste('reject_sqft', 'dry_end'),
                    *[check_waste('reject_sqft', family, filters={'reject_family': code}) for family, code in FAMILIES.items()],
                    check_waste_details('reject_sqft', 'sqft', 1)],
    'reject_sheets': [check_waste_details('reject_sheets', 'sheets', 0)],
    'dry_end_pct': [check_shift_kpis('dry_end_pct', 'dry_end_pct'), check_period_kpis('dry_end_pct', 'dry_end_pct', 4),
                    *[check_period_kpis('dry_end_pct', f'{family}_pct', 4, {'reject_family': code}) for family, code in FAMILIES.items()],
                    *[check_waste('dry_end_pct', family, 'pct_of_yield', 4, {'reject_family': code}) for family, code in FAMILIES.items()],
                    check_grade_totals('dry_end_pct', 'dry_end_pct', 4), check_trend('dry_end_pct', 'dry_end_pct', 4)],
    'stops': [check_breakdown('stops', 'stops'), check_period_kpis('stops', 'stops', 0), check_pareto('stops', 'stops', 0),
              check_review('stops', 0), check_stops_profile('stops', 'stops', None), check_trend('stops', 'stops', 0)],
    'downtime_minutes': [check_breakdown('downtime_minutes', 'down_minutes'), check_period_kpis('downtime_minutes', 'downtime_minutes', 1),
                         check_pareto('downtime_minutes', 'minutes', 2), check_pareto('downtime_minutes', 'share_pct', 2),
                         check_review('downtime_minutes', 2), check_stops_profile('downtime_minutes', 'minutes', 2)],
    'minutes_per_stop': [check_breakdown('minutes_per_stop', 'minutes_per_stop'), check_period_kpis('minutes_per_stop', 'minutes_per_stop', 2),
                         check_trend('minutes_per_stop', 'minutes_per_stop', 2)],
    'scheduled_minutes': [check_scheduled_minutes],
    'downtime_pct': [check_shift_kpis('downtime_pct', 'downtime_pct'), check_shift_kpis('downtime_pct', 'maintenance_pct', {'kind': 'Maintenance'}),
                     check_shift_kpis('downtime_pct', 'operator_pct', {'kind': 'Operator'}), check_shift_kpis('downtime_pct', 'pm_pct', {'kind': 'PM'}),
                     check_period_kpis('downtime_pct', 'downtime_pct', 4), check_period_kpis('downtime_pct', 'operator_pct', 4, {'kind': 'Operator'}),
                     check_review('downtime_pct', 4), check_stops_profile('downtime_pct', 'downtime_pct', 4), check_trend('downtime_pct', 'downtime_pct', 4)],
    'elapsed_minutes': [check_wet_end('elapsed_minutes', 'elapsed_minutes')],
    'running_minutes': [check_running('running_minutes')],
    'target_ft': [check_target_ft],
    'target_run_ft': [check_running('target_run_ft')],
    'speed_run_fpm': [check_period_kpis('speed_run_fpm', 'speed_run_fpm', 1), check_grade_totals('speed_run_fpm', 'speed_run_fpm', 1),
                      check_trend('speed_run_fpm', 'speed_run_fpm', 1)],
    'speed_elapsed_fpm': [check_wet_end('speed_elapsed_fpm', 'actual_fpm'), check_grade_totals('speed_elapsed_fpm', 'speed_elapsed_fpm', 1)],
    'speed_all_fpm': [check_period_kpis('speed_all_fpm', 'speed_all_fpm', 1), check_trend('speed_all_fpm', 'speed_all_fpm', 1)],
    'speed_to_target_pct': [check_shift_kpis('speed_to_target_pct', 'speed_to_target_pct'), check_wet_end('speed_to_target_pct', 'speed_to_target_pct')],
    # the grade totals divide a speed already rounded to a tenth by the target, then round to a tenth again
    'run_speed_to_target_pct': [check_grade_totals('run_speed_to_target_pct', 'speed_to_target_pct', 1, tolerance=0.07)],
    'bale_sqft': [check_waste('bale_sqft', 'bale')],
    'sheets': [check_overrun('sheets', 'actual_sheets')],
    'nominal_sheets': [check_overrun('nominal_sheets', 'nominal_sheets')],
    'actual_cuts': [check_overrun('actual_cuts', 'actual_cuts')],
    'nominal_cuts': [check_overrun('nominal_cuts', 'nominal_cuts')],
    'overrun_cuts': [check_overrun('overrun_cuts', 'delta_cuts')],
    'overrun_pct': [check_overrun('overrun_pct', 'overrun_pct', 2)],
    'trim_usd': [check_waste('trim_usd', 'trim', 'usd', 2)],
    'shear_usd': [check_waste('shear_usd', 'shear', 'usd', 2)],
    'dry_end_usd': [check_quality('dry_end_usd', 'usd'), check_waste('dry_end_usd', 'dry_end', 'usd', 2),
                    *[check_waste('dry_end_usd', family, 'usd', 2, {'reject_family': code}) for family, code in FAMILIES.items()]],
    'downtime_usd': [check_breakdown('downtime_usd', 'usd'), check_pareto('downtime_usd', 'usd', 2)],
    'production_days': [check_production_days],
    'msf_per_day': [check_period_kpis('msf_per_day', 'msf_per_day', 1), check_trend('msf_per_day', 'msf_per_day', 1)],
    'setups_above_trim_target': [check_shift_kpis('setups_above_trim_target', 'setups_above_trim_target')],
    'maintenance_pm_minutes': [check_maintenance_pm_minutes],
    'maintenance_pm_pct': [check_period_kpis('maintenance_pm_pct', 'maintenance_pm_pct', 4)],
    'op_maint_minutes': [check_review_total('op_maint_minutes', 'op_maint_minutes', 2)],
    'op_maint_pct': [check_shift_kpis('op_maint_pct', 'op_maint_pct'), check_review_total('op_maint_pct', 'op_maint_pct', 4)],
    'bale_msf': [check_period_kpis('bale_msf', 'bale_msf', 1)],
    'bale_pct': [check_period_kpis('bale_pct', 'bale_pct', 4), check_waste('bale_pct', 'bale', 'pct_of_gross', 4)],
    'unexplained_sqft': [check_waste('unexplained_sqft', 'unexplained')],
    'unexplained_pct': [check_period_kpis('unexplained_pct', 'unexplained_pct', 4), check_waste('unexplained_pct', 'unexplained', 'pct_of_gross', 4)],
    'bale_usd': [check_waste('bale_usd', 'bale', 'usd', 2)],
    'unexplained_usd': [check_waste('unexplained_usd', 'unexplained', 'usd', 2)],
}


def test_every_measure_has_a_differential_check():
    assert set(CHECKS) == set(S.model().measures), sorted(set(CHECKS) ^ set(S.model().measures))


@pytest.mark.parametrize('measure', sorted(CHECKS))
def test_the_compiler_agrees_with_the_tools(store, measure):
    for check in CHECKS[measure]:
        check(store)


# ------------------------------------------------------------------ the model file

def test_the_model_file_is_complete():
    model = S.model()
    for name, m in model.measures.items():
        assert m['label'] and m['unit'] and m['description'] and m['lever'] in ('controllable', 'schedule-driven', 'external'), name
        assert m.get('tool'), f'{name}: name the tool that serves it today, or say none does'
        # the answer prints the definition: it may state a rule constant (480 a shift, / 12) and nothing a tool did not return
        assert set(re.findall(r'\d+(?:\.\d+)?', m['description'])) <= {'480', '12'}, name
    issue = ('day', 'shift', 'week', 'month', 'period', 'grade', 'flute', 'wet_end', 'place', 'kind', 'reason', 'reject_family',
             'reject_reason', 'order', 'customer')
    assert set(issue) <= set(model.dimensions)
    for band in ('trim', 'shear', 'dry_end'):   # every band of a measure the store carries is on a measure
        assert any(m.get('band') == band for m in model.measures.values())
    assert {b for m in model.measures.values() for b in (m.get('band_by') or {}).get('bands', {}).values()} == {'maintenance', 'operator'}


def test_synonyms_mean_one_thing():
    model = S.model()
    for items in (model.measures, model.dimensions):
        seen = {}
        for name, item in items.items():
            for word in [name, *item.get('synonyms', [])]:
                assert seen.setdefault(word.lower(), name) == name, f'{word!r} names both {seen[word.lower()]} and {name}'
    # a value synonym stands for at most one value the store holds
    store = default_store()
    for dim, spec in model.dimensions.items():
        if spec['type'] == 'time' or not model.mapped(spec['entity'] if spec['entity'] != '*' else 'setup', dim):
            continue
        values = S.store_values(dim, store)
        words = {}
        for canonical, synonyms in (spec.get('value_synonyms') or {}).items():
            for word in synonyms:
                words.setdefault(word.lower(), set()).add(canonical)
        for word, canonicals in words.items():
            held = [c for c in canonicals if values is None or any(S._same(c, v) for v in values)]
            assert len(held) <= 1, (dim, word, held)


def test_physical_names_live_in_the_one_mapping_block():
    """Every other block names logical columns as {placeholders}; the compiled SQL reads only the columns the
    physical block maps, so a renamed store column changes that block and nothing else."""
    spec = json.loads(S.MODEL_PATH.read_text())
    physical = spec['physical']['entities']
    # the columns the block names: its column mappings and its entities' row restrictions (`where`, quoted literals aside)
    mapped = {e['table']: {w for col in [*e['columns'].values(), re.sub(r"'[^']*'", '', e.get('where') or '')] if col
                           for w in re.findall(r'[A-Za-z_]\w*', col)} - {'AND', 'IN'} for e in physical.values()}
    for block in ('dimensions', 'measures'):
        for item in spec[block]:
            for key in ('expr',):
                if key in item:
                    bare = re.findall(r'[A-Za-z_]\w*', re.sub(r'\{[a-z_]+\}', '', re.sub(r"'[^']*'", '', item[key])))
                    assert set(bare) <= S.FUNCTIONS, (item['name'], bare)
    model = S.model()
    aliases = re.compile(r'\b(b|j\d)\.([A-Za-z_]\w*)')
    for name in model.measures:
        query = S.validate(S.SemanticQuery(name, window=WEEK, group_by=['shift']))
        for statement in S.compile_query(query).statements:
            tables = {table for table, _ in re.findall(r'(?:FROM|JOIN) ([a-z_]+) (b|j\d)\b', statement.sql)}
            assert tables <= {e['table'] for e in physical.values()}
            readable = set().union(*(mapped[table] for table in tables))
            for alias, column in aliases.findall(statement.sql):
                assert column in readable, (name, alias, column)
    # and the block names only columns the store creates
    store_columns = {table: {part.split()[0] for part in spec_.split(',')} for table, spec_ in TABLES.items()}
    for table, columns in mapped.items():
        assert columns - S.FUNCTIONS <= store_columns[table], (table, columns - store_columns[table])


def test_a_broken_model_file_is_refused():
    spec = json.loads(S.MODEL_PATH.read_text())

    def broken(edit):
        bad = copy.deepcopy(spec)
        edit(bad)
        with pytest.raises(ValueError, match='semantic model'):
            S.Model(bad)
    broken(lambda s: s['measures'][3].update(expr='{lineal_feet}'))                         # a placeholder the entity does not map
    broken(lambda s: s['measures'][3].update(expr='feet'))                                  # a physical name outside the mapping block
    broken(lambda s: s['measures'][9].update(band='not_a_band'))                            # a band targets.json does not hold
    broken(lambda s: s['physical']['entities']['setup']['columns'].update(lineal_ft='lineal_feet'))   # a column the store does not create
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'trim_pct').update(numerator='lineal_per_setup'))   # a ratio of a ratio
    broken(lambda s: s['joins'][0].update(relationship='one_to_many'))                      # a join that would fan out


# ------------------------------------------------------------------ validate: refusals name the nearest choices

@pytest.mark.parametrize('query, message, choices', [
    (dict(measure='scrapp'), r"^No measure 'scrapp' in the semantic model; the nearest: reject_sqft", ['reject_sqft']),
    (dict(measure='stops', group_by=['plcae']), r"^No dimension 'plcae' in the semantic model; the nearest: place", ['place']),
    (dict(measure='lineal_ft', group_by=['order']), r'recorded per run setup, which carries no order; it can be split by: day, ', ['day']),
    (dict(measure='speed_all_fpm', group_by=['grade']), r'read against scheduled minutes, which carry no grade', ['day']),
    (dict(measure='speed_all_fpm', filters={'place': 'Stacker'}), r'carries no place; it can be filtered by: shift, crew$', ['shift', 'crew']),
    (dict(measure='stops', filters={'place': 'dbl backer'}), r"^No stop place 'dbl backer' in the records; the nearest: Double backer$", ['Double backer']),
    (dict(measure='stops', filters={'reason': 'zzz'}), r"^No stop reason 'zzz' in the records; the values are: ", None),
    (dict(measure='stops', filters={'day': '2026-09-14'}), r'day is a time dimension: the window sets the time', None),
    (dict(measure='stops', group_by=['place', 'reason', 'kind']), r'group_by lists at most two dimensions', None),
    (dict(measure='stops', limit=0), r'limit is a whole number of rows from 1 to', None),
    (dict(measure='stops', compare='yesterday'), r'compare is previous', None),
    (dict(measure='stops', shift=4), r'shift is 1, 2 or 3', None),
    (dict(measure='stops', window={'period': 'month', 'anchor': '2024-01-01'}), r'outside the dataset range', None),
    (dict(measure='stops', window={'period': 'day', 'anchor': '2026-09-13'}), r'is a Sunday: production days are Monday to Friday', None),
    (dict(measure='stops', window=None), r'needs a window', None),
    (dict(measure='wet_end_id_oops', group_by=[]), r'No measure', None),
    (dict(measure='stops', filters={'wet_end': 'WE2026091401'}), r"^No paper run \(wet end\) 'WE2026091401' in the records; the nearest: WE20260914", None),
])
def test_validate_refuses_with_the_nearest_choices(query, message, choices):
    store = default_store()
    query = {'window': WEEK, **query}
    with pytest.raises(S.SemanticError, match=message) as refused:
        S.validate(S.SemanticQuery(**query), store)
    if choices:
        assert refused.value.choices[:len(choices)] == choices


def test_a_column_the_store_does_not_carry_is_refused_with_that_reason():
    """A null in the physical block: the store does not carry the column (v1 carried no customer), and a dimension that reads
    it is refused with that reason, as a split and as a filter. Record contract v2 carries the customer: sheets split by it."""
    spec = json.loads(S.MODEL_PATH.read_text())
    spec['physical']['entities']['lane']['columns']['customer_id'] = None
    mdl = S.Model(spec)
    for query in (S.SemanticQuery('sheets', window=WEEK, group_by=['customer']), S.SemanticQuery('sheets', window=WEEK, filters={'customer': 'C001'})):
        with pytest.raises(S.SemanticError, match='carry no customer yet'):
            S.validate(query, mdl=mdl)
    store = default_store()
    result = S.run(S.SemanticQuery('sheets', window=WEEK, group_by=['customer'], limit=200), store)
    want = store._fetch('SELECT customer_id, sum(actual_cuts * outs) AS v FROM setup_orders WHERE production_date BETWEEN ? AND ? GROUP BY ALL',
                        [date(2026, 9, 7), date(2026, 9, 11)], limit=200)
    assert {r['customer']: r['value'] for r in result['rows']} == {r['customer_id']: r['v'] for r in want}


def test_synonyms_resolve_to_the_models_names_and_the_stores_values():
    store = default_store()
    query = S.validate(S.SemanticQuery('scrap', group_by=['defect'], filters={'paper run': 'we20260914016', 'flute': 'single wall'}, window=WEEK), store)
    assert (query.measure, query.group_by, query.filters) == ('reject_sqft', ['reject_reason'], {'wet_end': 'WE20260914016', 'flute': 'C'})
    query = S.validate(S.SemanticQuery('downtime', filters={'place': 'DB', 'kind': 'pm', 'reason': 'starch'}, window=WEEK), store)
    assert query.filters == {'place': 'Double backer', 'kind': 'Scheduled', 'reason': 'Glue / starch'}
    for word in ('planned', 'planned maintenance', 'Scheduled', 'PM'):   # the planned kind (v1 named it PM), never Maintenance
        assert S.validate(S.SemanticQuery('downtime', filters={'kind': word}, window=WEEK), store).filters == {'kind': 'Scheduled'}, word
    query = S.validate(S.SemanticQuery('lineal_ft', filters={'web_width': '100', 'shift': '2'}, window=WEEK), store)
    assert query.filters == {'web_width': 100.0} and query.shift == 2
    assert S.validate(S.SemanticQuery('sheets', filters={'lane': '2'}, window=WEEK), store).filters == {'lane': 2}
    assert S.validate(S.SemanticQuery('stops', group_by=['shift'], window=WEEK)).order == 'dimension'   # a shift reads in its own order
    assert S.validate(S.SemanticQuery('stops', group_by=['reason'], window=WEEK)).order == 'value'      # a reason, largest first


# ------------------------------------------------------------------ compile: parameterized, allowlisted, bounded

def test_every_value_is_a_bound_parameter_and_every_statement_has_a_limit():
    hostile = "x' OR 1=1; DROP TABLE stops; --"
    query = S.validate(S.SemanticQuery('downtime_usd', group_by=['grade'], filters={'place': hostile, 'kind': 'Operator'}, window=WEEK, shift=2,
                                       compare='previous'))
    compiled = S.compile_query(query)
    assert [s.name for s in compiled.statements] == ['groups', 'total', 'basis gross_sqft', 'basis scheduled_minutes']
    assert 'LEFT JOIN' in compiled.statements[0].sql and 'coalesce(prev.value, 0) AS previous_value' in compiled.statements[0].sql
    for statement in compiled.statements:
        assert hostile not in statement.sql and 'Operator' not in statement.sql and '2026-09' not in statement.sql
        assert statement.sql.rstrip().endswith('LIMIT ?') or statement.sql.rstrip().endswith('LIMIT 1')
        assert statement.sql.count('?') == len(statement.params)
        assert ';' not in statement.sql
    assert hostile in compiled.statements[0].params and 'Operator' in compiled.statements[0].params
    with pytest.raises(S.SemanticError, match='No stop place'):
        S.validate(query, default_store())   # and against the store the value is refused before anything compiles


def test_the_receipt_reruns_and_names_the_records_behind_each_row(store):
    result = run(store, 'downtime_minutes', WEEK, group_by=['reason'], filters={'kind': 'Maintenance'})
    statements = result['trace']['sql'].split(';\n')
    assert len(statements) == len(result['trace']['parameters']) == 2
    again = store._fetch(statements[0], result['trace']['parameters'][0])
    assert [r['value'] for r in again] == [r['value'] for r in result['rows']]
    for row in result['rows']:
        assert row['source_ids'] == sorted(row['source_ids']) and len(row['source_ids']) <= S.SOURCE_ID_CAP
        assert row['source_count'] >= len(row['source_ids'])
        listed = store._fetch('SELECT DISTINCT reason, kind FROM stops WHERE stop_id IN (SELECT unnest(?))', [row['source_ids']])
        assert listed == [{'reason': row['reason'], 'kind': 'Maintenance'}]
    big = run(store, 'setups', MONTH)
    assert big['source_count'] > S.SOURCE_ID_CAP and big['value'] == big['source_count']


def test_the_4_4_5_period_in_sql_is_the_calendars():
    store = default_store()
    days = [date.fromisoformat(d) for d in store.production_days] + [date(2020, 1, 6), date(2031, 12, 31), date(2026, 8, 2), date(2026, 8, 3)]
    sql = f"SELECT d, {S._fiscal_start('d')} AS start FROM (SELECT unnest(?::DATE[]) AS d)"
    rows = store._fetch(sql, [[d.isoformat() for d in days]], limit=len(days))
    assert {r['d']: r['start'] for r in rows} == {d: fiscal_period(d)[0] for d in days}


def test_time_grains_and_the_previous_window(store):
    weeks = run(store, 'stops', MONTH, group_by=['week'])
    assert all(date.fromisoformat(r['week']).weekday() == 0 for r in weeks['rows'])
    months = run(store, 'stops', {'period': 'fiscal', 'anchor': '2026-09-14'}, group_by=['month'])
    assert [r['month'] for r in months['rows']] == ['2026-08-01', '2026-09-01']
    compared = run(store, 'stops', WEEK, group_by=['kind'], compare='previous')
    before = rows_by(run(store, 'stops', {'period': 'week', 'anchor': '2026-09-02'}, group_by=['kind']), 'kind')
    assert compared['previous_window']['start'] == '2026-08-31'
    for row in compared['rows']:   # a slice the previous window recorded nothing for is a recorded zero, as in the current one
        assert row['previous_value'] == (before[row['kind']]['value'] if row['kind'] in before else 0)
    assert compared['delta'] == compared['value'] - compared['previous_value'] and compared['comparison'] == 'complete'
    partial = run(store, 'stops', {'period': 'week', 'anchor': '2025-01-06'}, compare='previous')   # the records start Wednesday 1 January 2025
    assert partial['previous_window']['coverage'] == 'partial'
    assert partial['previous_value'] is not None and partial['delta'] is None and partial['comparison'].startswith('withheld')
    outside = run(store, 'stops', {'period': 'day', 'anchor': '2025-01-01'}, group_by=['kind'], compare='previous')
    assert outside['previous_window']['coverage'] == 'unavailable' and outside['previous_value'] is None and outside['delta'] is None
    assert all(r['previous_value'] is None for r in outside['rows'])   # outside the records: never the zero an empty sum reads


def test_a_slice_with_no_records_is_a_recorded_zero_where_the_denominator_says_so(store):
    """Downtime % by day lists every production day of the window (the scheduled minutes are its rows); a count by a
    dimension of its own records lists only what was recorded."""
    result = run(store, 'downtime_pct', WEEK, group_by=['day'], filters={'kind': 'External'})
    assert len(result['rows']) == 5 and all(r['value'] >= 0 for r in result['rows'])
    assert all(r['value'] == 0 and r['source_count'] == 0 for r in result['rows'] if r['numerator'] == 0)


def test_the_compiler_answers_in_tens_of_milliseconds(store):
    """Datasette-fast (CLAUDE.md): a generous bound here, since CI runners are slower (#139); the PR records the measured times."""
    import time
    run(store, 'stops', WEEK)   # the first query pays DuckDB's warm-up
    for measure, kw in (('downtime_minutes', {'group_by': ['grade'], 'filters': {'kind': 'Maintenance'}}),
                        ('dry_end_pct', {'group_by': ['reject_family']}), ('reject_sqft', {'group_by': ['order'], 'shift': 2}),
                        ('trim_usd', {'group_by': ['grade']}), ('speed_all_fpm', {'group_by': ['shift'], 'compare': 'previous'})):
        started = time.perf_counter()
        run(store, measure, MONTH, **kw)
        assert (time.perf_counter() - started) * 1000 < 500, measure


# ------------------------------------------------------------------ the URL and the answer

def test_the_url_is_the_query():
    store = default_store()
    url = '/corr/1/week/2026-09-14?measure=semantic&m=downtime_minutes&by=grade,shift&kind=Maintenance&compare=prev'
    resolved = resolve_url(url, store)
    step = resolved['request']['plan'][0]
    assert step['tool'] == S.TOOL and step['arguments'] == {
        'measure': 'downtime_minutes', 'window': {'period': 'week', 'anchor': '2026-09-14'}, 'group_by': ['grade', 'shift'],
        'filters': {'kind': 'Maintenance'}, 'shift': None, 'compare': 'previous', 'order': 'value', 'limit': S.DEFAULT_LIMIT}
    assert page_url(resolved) == url and redirect_for(url, store) is None
    assert canonical_url(resolved['request'], resolved['context']) == url
    # hand-typed spellings redirect to the model's own names, values and field order
    for typed, canonical in (
            ('/corr/1/week/2026-09-14?measure=semantic&kind=maint&m=downtime&by=quality', '/corr/1/week/2026-09-14?measure=semantic&m=downtime_minutes&by=grade&kind=Maintenance'),
            ('/corr/1/day/2026-09-14?measure=semantic&m=rejects&place=x&shifts=2', None),
            ('/corr/1/day/2026-09-14/shift/2?measure=semantic&m=stops&reason=glue&window=week', '/corr/1/day/2026-09-14/shift/2?measure=semantic&m=stops&window=week&reason=Glue+/+starch'),
            ('/corr/1/month/2026-09-14?measure=semantic&m=lineal_ft&web_width=100.0', '/corr/1/month/2026-09-14?measure=semantic&m=lineal_ft&web_width=100')):
        assert redirect_for(typed, store) == canonical, typed
    for bad, message in (('/corr/1/week/2026-09-14?measure=semantic', 'Name a measure with m='),
                         ('/corr/1/week/2026-09-14?measure=semantic&m=stops&shifts=1,3', 'runs for one shift'),
                         ('/corr/1/week/2026-09-14?measure=semantic&m=stops&compare=2026-08', 'compare=prev'),
                         ('/corr/1/week/2026-09-14?measure=kpis&m=stops', 'm= does not apply to measure=kpis'),
                         ('/corr/1/week/2026-09-14?measure=semantic&m=stops&unit=count', 'unit= does not apply to measure=semantic'),
                         ('/corr/1/week/2026-09-14?measure=semantic&m=stops&place=Stacker&place=Power', 'Repeated query field')):
        with pytest.raises(ValueError, match=message):
            resolve_url(bad, store)


def test_the_answer_leads_with_the_asked_measure_and_every_figure_cites_its_row(store):
    resolved = resolve_url('/corr/1/week/2026-09-14?measure=semantic&m=downtime_pct&by=kind', store)
    analysis = store.day_analysis(resolved['day'])
    try:
        answer = Agent(analysis).ask(resolved['text'], resolved['context'], request=resolved['request'])
    finally:
        analysis.close()
    data = answer['trace'][0]['data']
    head = answer['sections'][0]
    assert head == f'Downtime this week: **{data["value"]:.2f}%** of scheduled minutes (query 1)'
    for i, row in enumerate(data['rows']):
        line = answer['sections'][1 + i]
        assert line.startswith(f'- {row["label"]}: **{row["value"]:.2f}%** of scheduled minutes') and line.endswith(f'(query 1 · row {i})')
    maintenance = next(i for i, r in enumerate(data['rows']) if r['kind'] == 'Maintenance')
    assert data['rows'][maintenance]['band_text'] in answer['sections'][1 + maintenance]   # the owner's maintenance band, from targets.json
    assert 'PM' not in [r['label'] for r in data['rows']] and 'Planned maintenance (scheduled)' in [r['label'] for r in data['rows']]
    assert answer['tables'][0]['title'] == S.TOOL and answer['charts'][0]['rows'] == data['rows']
    looks = answer['sections'][answer['sections'].index('Where to look') + 1:]
    assert [line.rsplit('“', 1)[1] for line in looks] == ['Which downtime reason cost the most minutes this week?”', 'Stops per day this week”']


# ------------------------------------------------------------------ the docs page

def test_the_docs_page_is_rendered_from_the_model_file():
    from investigator import docsite
    page = next(p for p in docsite.pages() if p['slug'] == 'semantic-model')
    assert page['source'] == 'src/investigator/semantic_model.json'
    text = S.model_markdown(S.MODEL_PATH.read_text())
    model = S.model()
    for name, m in model.measures.items():
        assert f'`{name}`' in text and m['description'].replace('|', '\\|') in text
    for name in model.dimensions:
        assert f'| `{name}` |' in text
    assert 'home run 2.75% / target 3% / flag 3.25% of gross' in text   # the trim band, read from targets.json
    assert 'Maintenance: target 2.5% / flag 2.5% of scheduled minutes' in text
    assert '| db, double-backer, hot plates | place Double backer |' in text
    assert '| lane | `setup_orders` | `customer_id` | `customer_id` |' in text and 'not carried yet' not in text   # record contract v2 carries it
    assert "| reject | `line_id` | where `counted_in = 'dry_end'` |" in text   # the dry-end reject lines only
    html = docsite.build_site()['/docs/semantic-model'].decode()
    assert '<h1' in html and 'The semantic model' in html and 'downtime_minutes' in html


# ------------------------------------------------------------------ the review of the branch (issue #117)

def test_a_slice_read_against_the_whole_denominator_carries_no_band(store):
    """The owner's dry-end band is the total's: a reject family read against all the yield is a share of it, unbanded (as
    the waste breakdown's family rows are); a grade, whose yield splits the same way, is its own ratio and carries it."""
    families = run(store, 'dry_end_pct', WEEK, group_by=['reject_family'])
    assert families['band'] and all('band' not in r for r in families['rows'])
    grades = run(store, 'dry_end_pct', WEEK, group_by=['grade'])
    assert all(r['band'] == targets.band('dry_end', r['value']) for r in grades['rows'])
    kinds = run(store, 'downtime_pct', WEEK, group_by=['kind'])   # band_by: each kind its own band, as the review reads it
    assert {r['kind']: r.get('band') for r in kinds['rows'] if r['kind'] in ('Maintenance', 'Operator')} == \
        {r['kind']: targets.band(r['kind'].lower(), r['value']) for r in kinds['rows'] if r['kind'] in ('Maintenance', 'Operator')}


def test_no_model_planner_can_run_the_semantic_model(store):
    """Phase 1 offers the compiler to no model: a model that writes its name is refused like any unknown tool, in every
    scope; its address and a scripted value case may run it."""
    class Model:
        mode = 'a model planner'
        usage = {'model_calls': 1, 'cost_usd': 0}

        def next_step(self, context):
            if not context['results']:
                return {'tool': S.TOOL, 'arguments': {'measure': 'stops', 'window': WEEK, 'shift': 2}}
            return {'final': [r['result_id'] for r in context['results'] if r['ok']]} if any(r['ok'] for r in context['results']) else {'unavailable': 'no'}
    analysis = store.day_analysis('2026-09-14')
    try:
        context = Context('2026-09-14', 2)
        result = Agent(analysis, planner=Model()).ask('stops this week', context)
        assert result['trace'][0]['ok'] is False and result['trace'][0]['error']['message'] == 'Tool is not permitted'
        with pytest.raises(ValueError, match='Tool is not permitted'):
            Agent(analysis)._execute(S.TOOL, {'measure': 'stops', 'window': WEEK}, {})
        assert S.TOOL not in {s['name'] for s in TOOL_SCHEMAS} and S.TOOL not in PLAN_TOOLS
    finally:
        analysis.close()


def test_the_selected_shift_scope_holds_for_the_semantic_model(store):
    """Under one shift the compiler reads that shift only: all shifts or another shift is refused before it runs."""
    for arguments in ({'measure': 'stops', 'window': WEEK, 'shift': None}, {'measure': 'stops', 'window': WEEK, 'shift': 3}):
        assert scope_violation(S.TOOL, arguments, [2], 'shift', 'day', '2026-09-14')
    assert scope_violation(S.TOOL, {'measure': 'stops', 'window': WEEK, 'shift': 2}, [2], 'shift', 'week', '2026-09-14') is None
    with pytest.raises(S.SemanticError, match='contradicts'):
        S.validate(S.SemanticQuery('stops', window=WEEK, shift=2, filters={'shift': 3}))


URL_CASES = [c for c in json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases'] if c.get('url') and c['type'] == 'routing']


@pytest.mark.parametrize('case', URL_CASES, ids=lambda c: c['id'])
def test_every_semantic_answer_reads_short_and_its_looks_route_in_scope(store, case):
    """The answer-shape rules test_answer_shapes.py holds every golden question to, for the cases that ask by address: no
    section over about forty words or three semicolon clauses, and every where-to-look link a question the offline router
    answers in the card's own scope."""
    from test_answer_shapes import MAX_CLAUSES, MAX_WORDS, tool_text
    resolved = resolve_url(case['url'], store)
    analysis = store.day_analysis(resolved['day'])
    try:
        answer = Agent(analysis).ask(resolved['text'], resolved['context'], request=resolved['request'])
    finally:
        analysis.close()
    quoted = sorted(tool_text(answer['trace']), key=len, reverse=True)
    for section in answer['sections']:
        text = section[2:] if section.startswith('- ') else section
        for string in quoted:
            text = text.replace(string, ' ')
        assert len(text.split()) <= MAX_WORDS and len(text.split(';')) <= MAX_CLAUSES, section
        look = re.search(r' → “([^”]+)”$', section)
        if look:
            context = resolved['context']
            _, asked = route(look[1], context, store.production_days)
            assert asked is context, (look[1], asked.read)


# ------------------------------------------------------------------ the coordinator's review of a5af68d (issue #117)

@pytest.mark.parametrize('dim', ['day', 'week', 'month', 'period'])
def test_a_comparison_never_splits_by_time(dim):
    """Item 1: the previous window holds none of this window's days, weeks, months or periods, so a comparison split by
    one set every row against nothing (the card read "previous 0 stops" on every day). It is refused, with why."""
    window = {'period': 'fiscal', 'anchor': '2026-09-14'} if dim in ('month', 'period') else MONTH
    for group_by in ([dim], ['kind', dim]):
        with pytest.raises(S.SemanticError, match=f'^A comparison with the previous window cannot split by {dim}: '):
            S.validate(S.SemanticQuery('stops', window=window, group_by=group_by, compare='previous'))
        assert S.validate(S.SemanticQuery('stops', window=window, group_by=group_by)).group_by == group_by   # without compare, it splits
    for group_by in (['shift'], ['kind'], ['crew', 'shift']):   # a category is in both windows: it still compares
        assert S.validate(S.SemanticQuery('stops', window=WEEK, group_by=group_by, compare='previous')).compare == 'previous'
    with pytest.raises(ValueError, match='^A comparison with the previous window cannot split by day'):
        resolve_url('/corr/1/week/2026-09-14?measure=semantic&m=stops&by=day&compare=prev', default_store())


@pytest.mark.parametrize('dim', ['wet_end', 'order'])
def test_a_comparison_never_splits_or_filters_by_a_one_day_id(store, dim):
    """Item 1, the reviewer's check: a paper run and an order belong to one production day (their ids carry it), so the
    window before holds none of this window's, and a comparison split by one read "previous 0" on every row. Refused, as a
    split by time is, and so is a filter on one."""
    measure = 'lineal_ft' if dim == 'wet_end' else 'sheets'
    assert S.model().dimensions[dim].get('recurs') is False
    with pytest.raises(S.SemanticError, match=f'^A comparison with the previous window cannot split by {dim}: '):
        S.validate(S.SemanticQuery(measure, window=WEEK, group_by=[dim], compare='previous'), store)
    value = run(store, measure, window_of('2026-09-16'), group_by=[dim])['rows'][0][dim]
    with pytest.raises(S.SemanticError, match=f'^A comparison with the previous window cannot filter by {dim}: '):
        S.validate(S.SemanticQuery(measure, window=window_of('2026-09-16'), filters={dim: value}, compare='previous'), store)
    assert run(store, measure, window_of('2026-09-16'), filters={dim: value})['value'] > 0   # without compare, it answers
    for recurring in ('crew', 'grade', 'lane', 'customer', 'kind', 'reject_family'):   # their values come back every window
        assert S.model().dimensions[recurring].get('recurs', True) is True


def _with_where(entity, where):
    spec = json.loads(S.MODEL_PATH.read_text())
    spec['physical']['entities'][entity]['where'] = where
    return S.Model(spec)


def test_an_entity_where_holds_a_checked_condition(store):
    """Item 2: an entity's physical `where` holds a small grammar, checked when the model loads: a column of its table, a
    comparison (= <> != < <= > >=) and a literal, or a column IN a list of literals, joined by AND. The literals are bound
    parameters, never SQL text, and the rows the compiler reads are exactly the rows the condition keeps."""
    mdl = _with_where('stop', "kind IN ('Maintenance', 'Operator') AND seconds >= 60 AND place <> 'Power'")
    week = {'period': 'week', 'anchor': '2026-09-14'}
    result = S.run(S.SemanticQuery('downtime_minutes', window=week, group_by=['kind']), store, mdl=mdl)
    want = store._fetch("SELECT kind, sum(seconds) / 60 AS value, count(*) AS n FROM stops WHERE production_date BETWEEN ? AND ? "
                        "AND kind IN ('Maintenance', 'Operator') AND seconds >= 60 AND place <> 'Power' GROUP BY kind",
                        [date(2026, 9, 14), date(2026, 9, 18)], limit=10)
    got = {r['kind']: r for r in result['rows']}
    assert set(got) == {r['kind'] for r in want} == {'Maintenance', 'Operator'}
    assert all(close(got[r['kind']]['value'], r['value']) and got[r['kind']]['source_count'] == r['n'] for r in want)
    assert close(result['value'], sum(r['value'] for r in want))
    for sql, params in zip(result['trace']['sql'].split(';\n'), result['trace']['parameters']):
        assert "'Maintenance'" not in sql and "'Power'" not in sql and '>= 60' not in sql
        assert sql.count('?') == len(params) and {'Maintenance', 'Operator', 60, 'Power'} <= set(params)
    # the values a filter may name are the restricted rows' own
    assert S.store_values('kind', store, mdl) == ['Maintenance', 'Operator']
    with pytest.raises(S.SemanticError, match="No stop kind 'External' in the records"):
        S.validate(S.SemanticQuery('stops', window=week, filters={'kind': 'External'}), store, mdl)
    assert _with_where('stop', "seconds > 0.5").entities['stop']['where'] == "seconds > 0.5"
    assert _with_where('stop', "kind = 'Operator'") is not None and _with_where('stop', "kind != 'Operator'") is not None


@pytest.mark.parametrize('where', [
    "kind = 'PM' OR 1 = 1",              # OR: not in the grammar
    "kind = 'PM'; DROP TABLE stops",     # a second statement
    "nope = 1",                          # a column the table does not have
    "kind LIKE 'P%'",                    # an operator outside the grammar
    "kind = place",                      # a column against a column: a literal is required
    "kind IN ()",                        # an empty list
    "kind = 'it''s'",                    # a quote inside a literal
    "lower(kind) = 'pm'",                # a function
    "kind = ",                           # no literal
    "AND kind = 'PM'",                   # a leading AND
    "kind = 'PM' AND",                   # a trailing AND
    "kind IN ('PM', seconds)",           # a column inside the list
    "(kind = 'PM')",                     # parentheses
    "kind = 'PM' -- comment",            # a comment
])
def test_an_entity_where_outside_the_grammar_is_refused(where):
    with pytest.raises(ValueError, match='semantic model: stop where'):
        _with_where('stop', where)


def test_an_entity_reached_by_a_join_holds_no_where():
    """A join to a restricted entity would read its excluded rows as a dimension of nothing; the model refuses one."""
    with pytest.raises(ValueError, match='semantic model: .*setup.* joined'):
        _with_where('setup', 'seconds > 0')


def test_a_band_from_a_filter_never_marks_a_slice_of_the_whole(store):
    """Item 3: the owner's band reads a ratio of its own. A grade's maintenance minutes over all the week's scheduled minutes
    is a share of the maintenance %, so it carries no band, though the band came from the kind filter; the week's maintenance
    % and each day's are ratios of their own and carry it (docs/AGENT_CONTRACT.md, the semantic shape)."""
    grades = run(store, 'downtime_pct', WEEK, group_by=['grade'], filters={'kind': 'Maintenance'})
    assert grades['rows'] and all(r.get('band') is None for r in grades['rows'])
    assert grades['band'] == targets.band('maintenance', grades['value'])
    days = run(store, 'downtime_pct', WEEK, group_by=['day'], filters={'kind': 'Maintenance'})
    assert all(r['band'] == targets.band('maintenance', r['value']) for r in days['rows'])
    assert run(store, 'downtime_pct', WEEK, filters={'kind': 'Maintenance', 'place': 'Stacker'}).get('band') is None
    assert run(store, 'dry_end_pct', WEEK, filters={'reject_family': 'BOND'}).get('band') is None   # a family against all the yield
    grade = run(store, 'dry_end_pct', WEEK, filters={'grade': '32-C'})   # a grade's yield is its own: its own ratio
    assert grade['band'] == targets.band('dry_end', grade['value'])


def test_a_refusal_lists_every_value(monkeypatch):
    """Item 4: "the values are" lists every value the records hold, however many (a split would list only DEFAULT_LIMIT
    rows of one window, so the refusal is the one place that names them all)."""
    store = default_store()
    places = S.store_values('place', store)
    with pytest.raises(S.SemanticError) as refused:
        S.validate(S.SemanticQuery('stops', window=WEEK, filters={'place': 'zzz'}), store)
    assert str(refused.value) == "No stop place 'zzz' in the records; the values are: " + ', '.join(places)
    assert refused.value.choices == places
    many = [f'Q{i:02}' for i in range(S.DEFAULT_LIMIT + 31)]
    monkeypatch.setattr(S, 'store_values', lambda dim, store, mdl=None: many)
    with pytest.raises(S.SemanticError) as refused:
        S.validate(S.SemanticQuery('stops', window=WEEK, filters={'place': 'zzz'}), store)
    assert str(refused.value) == "No stop place 'zzz' in the records; the values are: " + ', '.join(many)
    assert refused.value.choices == many


@pytest.mark.parametrize('measure, by', [('reject_sqft', ['order']), ('reject_sqft', ['order', 'reject_family']), ('dry_end_pct', ['order']),
                                         ('sheets', ['order']), ('overrun_pct', ['order'])])
def test_a_line_receipt_lists_the_lines_it_sums(store, measure, by):
    """Item 5: a reject line and an order lane are keyed by their own identity (setup, lane, and for a reject line its family
    and reason), so a row's source_ids are the lines it summed, one id each, and source_count counts the same lines."""
    mdl = S.model()
    result = run(store, measure, window_of('2026-09-14'), shift=2, group_by=by, limit=200)
    entity = mdl.measure_entities(measure)[0]
    table = mdl.entities[entity]['table']
    key = S._column(mdl, entity, mdl.entities[entity]['id'], 'b')
    assert result['rows']
    lines = store._fetch(f"SELECT count(*) AS n, count(DISTINCT {key}) AS ids FROM {table} b WHERE b.production_date = ? AND b.shift = 2",
                         [date(2026, 9, 14)])
    assert lines[0]['n'] == lines[0]['ids'] > 0   # one id per line of the table
    for row in result['rows']:
        assert row['source_count'] <= S.SOURCE_ID_CAP, 'an order on one shift has fewer lines than the cap'
        assert len(row['source_ids']) == len(set(row['source_ids'])) == row['source_count'], (measure, row['label'])
        counted = store._fetch(f'SELECT count(*) AS n FROM {table} b WHERE {key} IN (SELECT unnest(?))', [row['source_ids']])
        assert counted[0]['n'] == row['source_count']
    if measure == 'reject_sqft':   # the ids name exactly the lines whose square feet the row sums
        for row in result['rows']:
            summed = store._fetch(f'SELECT sum(b.sqft) AS v FROM {table} b WHERE {key} IN (SELECT unnest(?))', [row['source_ids']])
            assert close(summed[0]['v'], row['value'])


def test_the_lever_is_the_slices(store):
    """Item 6: planned maintenance is schedule-driven and a power outage external (the owner on PR #111; targets.json): a
    measure filtered by stop kind or reason takes that value's lever, each row of a split by one names its own, and the
    maintenance-and-planned measures are schedule-driven."""
    mdl = S.model()
    assert mdl.measures['maintenance_pm_minutes']['lever'] == mdl.measures['maintenance_pm_pct']['lever'] == 'schedule-driven'
    planned = S.resolve_value('kind', 'planned', store)
    week = {'period': 'week', 'anchor': '2026-09-14'}   # the September outage and a planned-maintenance Thursday
    kinds = run(store, 'downtime_minutes', week, group_by=['kind'])
    assert kinds['lever'] == 'controllable'
    assert {r['kind']: r['lever'] for r in kinds['rows']} == {'Maintenance': 'controllable', 'Operator': 'controllable',
                                                              planned: 'schedule-driven', targets.EXTERNAL_KIND: 'external'}
    reasons = run(store, 'stops', week, group_by=['reason'], limit=200)
    assert {'Planned maintenance', *targets.EXTERNAL_REASONS} <= {r['reason'] for r in reasons['rows']}
    for row in reasons['rows']:
        want = 'external' if row['reason'] in targets.EXTERNAL_REASONS else 'schedule-driven' if row['reason'] == 'Planned maintenance' else 'controllable'
        assert row['lever'] == want, row['reason']
    assert run(store, 'downtime_minutes', WEEK, filters={'kind': 'planned'})['lever'] == 'schedule-driven'
    assert run(store, 'downtime_pct', week, filters={'reason': 'Power outage'})['lever'] == 'external'
    assert run(store, 'stops', WEEK, filters={'kind': 'Operator'})['lever'] == 'controllable'
    assert run(store, 'trim_pct', WEEK)['lever'] == 'schedule-driven'
    # the reviewer's check: a row's lever is the lever of the stop kinds it sums, whatever dimension slices it
    assert run(store, 'maintenance_pm_minutes', week, filters={'kind': 'Maintenance'})['lever'] == 'controllable'
    assert run(store, 'maintenance_pm_minutes', week)['lever'] == 'schedule-driven'   # breakdowns and the plan together: the model's
    for row in run(store, 'maintenance_pm_minutes', week, group_by=['reason'], limit=200)['rows']:
        assert row['lever'] == ('schedule-driven' if row['reason'] == 'Planned maintenance' else 'controllable'), row['reason']
    assert run(store, 'downtime_minutes', week, filters={'place': 'Power'})['lever'] == 'external'
    places = rows_by(run(store, 'downtime_minutes', week, group_by=['place'], limit=200), 'place')
    assert places['Power']['lever'] == 'external' and places['Stacker']['lever'] == 'controllable'
    zero = run(store, 'downtime_pct', WEEK, group_by=['day'], filters={'kind': 'External'})   # a day with no External stop
    assert zero['rows'] and all(r['lever'] == 'external' for r in zero['rows'])
    # the model's kind levers are the owner's (targets.json): Maintenance and Operator controllable, External external
    kind_levers = mdl.dimensions['kind']['value_levers']
    assert kind_levers[targets.EXTERNAL_KIND] == targets.EXTERNAL['lever'] == 'external'
    assert kind_levers['Maintenance'] == targets.BANDS['maintenance']['lever'] and kind_levers['Operator'] == targets.BANDS['operator']['lever']


def test_a_scale_is_a_positive_number():
    """Item 7: a ratio's scale is checked when the model loads, as every other field is."""
    spec = json.loads(S.MODEL_PATH.read_text())
    for scale in ('100', 0, -100, True, None, float('inf'), float('nan')):
        bad = copy.deepcopy(spec)
        next(m for m in bad['measures'] if m['name'] == 'trim_pct')['scale'] = scale
        with pytest.raises(ValueError, match='semantic model: ratio trim_pct'):
            S.Model(bad)


def test_a_change_of_a_percentage_is_in_points(store):
    """Item 7: a percent measure's change reads in points (`+0.12 points vs 3.11% of gross`), never a bare number."""
    resolved = resolve_url('/corr/1/week/2026-09-14?measure=semantic&m=trim_pct&compare=prev', store)
    analysis = store.day_analysis(resolved['day'])
    try:
        answer = Agent(analysis).ask(resolved['text'], resolved['context'], request=resolved['request'])
    finally:
        analysis.close()
    data = answer['trace'][0]['data']
    assert f' · {data["delta"]:+.2f} points vs {data["previous_value"]:.2f}% of gross the week before' in answer['sections'][0]
