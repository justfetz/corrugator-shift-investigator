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
FISCAL = {'period': 'fiscal', 'anchor': '2026-09-14'}   # 4-4-5 P2: 2026-08-31 to 2026-09-27, four whole weeks
PERIOD_CASES = ((WEEK, None), (WEEK, 2), (MONTH, None), (MONTH, 3), (FISCAL, None), (FISCAL, 3))
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


PERIOD_KPIS = []   # (measure, metric, places, filters) of every check_period_kpis: the time-split and compare checks reuse them


def check_period_kpis(measure, metric, places, filters=None):
    """get_period_kpis' tile (rounded to `places`) over weeks, months and 4-4-5 periods, all shifts and one."""
    PERIOD_KPIS.append((measure, metric, places, filters or {}))

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


FAMILIES = {'bond': 'BOND', 'warp': 'WARP', 'misalignment': 'MISALIGNMENT', 'moisture': 'MOISTURE', 'other': 'OTHER'}

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
    'changeover_sqft': [check_waste('changeover_sqft', 'changeover')],
    'cutoff_waste_sqft': [check_waste('cutoff_waste_sqft', 'cutoff_waste')],
    'stacker_waste_sqft': [check_waste('stacker_waste_sqft', 'stacker_waste')],
    'admin_sqft': [check_waste('admin_sqft', 'admin')],
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
        assert m['label'] and m['unit'] and m['description'] and model.levers[name] in ('controllable', 'schedule-driven', 'external'), name
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
    mapped = {e['table']: {w for col in [*e['columns'].values(), e.get('where')] if col for w in re.findall(r'[A-Za-z_]\w*', re.sub(r"'[^']*'", '', col))}
              for e in physical.values()}
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
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'trim_pct').update(scale=-100))   # a scale that is not a positive number
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'trim_sqft').update(scale=100))   # a scale on a measure that is no ratio
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'stops').update(lever='maybe'))   # a lever the owner has not named
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'trim_pct').update(lever='schedule-driven'))   # a typed copy of its band's lever
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'trim_sqft').update(lever='controllable'))     # the trim band's feet, another lever
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'dry_end_usd').update(lever='schedule-driven'))   # the dry-end band's dollars
    broken(lambda s: next(d for d in s['dimensions'] if d['name'] == 'kind')['value_levers'].update(External='external'))   # targets.json's, retyped
    broken(lambda s: next(d for d in s['dimensions'] if d['name'] == 'reason').update(external='causes'))   # an external block targets.json lacks
    broken(lambda s: next(m for m in s['measures'] if m['name'] == 'setups_above_trim_target').update(lever='controllable'))   # over the trim flag
    broken(lambda s: next(e for e in s['entities'] if e['name'] == 'reject').update(band='not_a_band'))   # an entity band targets.json lacks
    for where in ("family = 'BOND'; DROP TABLE stops", "kategory = 'dry_end'", "family = 'BOND' OR (sqft > 0", "sqft > 0)", "upper(family) = 'BOND'",
                  # the structure, not only the tokens (the coordinator's re-review of PR #148): each of these loaded, then failed in DuckDB
                  "family = = 'BOND'", "family IN 'BOND'", "family IN ()", "family = 'BOND' AND", "NOT", "()", "family 'BOND'",
                  "family IS 'BOND'", "'BOND' = family", "family = 'BOND' family = 'WARP'", "family IN ('BOND',)", "AND family = 'BOND'",
                  "family = 'BOND' OR OR family = 'WARP'", "family IS NOT", "family NOT = 'BOND'", "(family) = 'BOND'"):
        broken(lambda s, w=where: s['physical']['entities']['reject'].update(where=w))   # a where outside its small grammar
    for where in ("counted_in = 'dry_end'", "family IN ('BOND', 'WARP') AND NOT (sqft <= 0 OR reason IS NULL)",
                  "family NOT IN ('OTHER') OR counted_in IS NOT NULL", "NOT NOT family = 'BOND'", "((family = 'BOND'))", "sqft > 0.5 AND sqft <> sheets"):
        good = copy.deepcopy(spec)
        good['physical']['entities']['reject']['where'] = where
        S.Model(good)   # the grammar's whole reach still loads


# ------------------------------------------------------------------ validate: refusals name the nearest choices

@pytest.mark.parametrize('query, message, choices', [
    (dict(measure='scrapp'), r"^No measure 'scrapp' in the semantic model; the nearest: reject_sqft", ['reject_sqft']),
    (dict(measure='stops', group_by=['plcae']), r"^No dimension 'plcae' in the semantic model; the nearest: place", ['place']),
    (dict(measure='lineal_ft', group_by=['order']), r'recorded per run setup, which carries no order; it can be split by: day, ', ['day']),
    (dict(measure='speed_all_fpm', group_by=['grade']), r'read against scheduled minutes, which carry no grade', ['day']),
    (dict(measure='speed_all_fpm', filters={'place': 'Stacker'}), r'carries no place; it can be filtered by: shift, crew$', ['shift', 'crew']),
    (dict(measure='stops', filters={'place': 'dbl backer'}), r"^No stop place 'dbl backer' in the records; the nearest: Double backer$", ['Double backer']),
    (dict(measure='stops', filters={'reason': 'zzz'}), r"^No stop reason 'zzz' in the records; the values are: Glue / starch, .*, Steam / moisture$", None),
    (dict(measure='stops', group_by=['day'], compare='previous'), r'^compare=prev puts the previous window beside each row of the same slice; a split by day', None),
    (dict(measure='msf', group_by=['grade', 'week'], compare='previous'), r'a split by week has no row in common', None),
    (dict(measure='stops', filters={'day': '2026-09-14'}), r'day is a time dimension: the window sets the time', None),
    (dict(measure='sheets', filters={'customer': 'C9999'}), r"^No customer 'C9999' in the records; the values are: C000, C001, .*, C039, C040$", None),
    (dict(measure='op_maint_pct', filters={'kind': 'External'}),
     r"^Downtime \(operator and maintenance\) \(op_maint_pct\) counts only stop kind Operator or Maintenance: stop kind External is outside it, "
     r"and would read as a recorded zero\. Filter by one of those, or ask downtime_pct for every stop kind\.$", ['Operator', 'Maintenance']),
    (dict(measure='maintenance_pm_minutes', filters={'kind': 'operator'}),
     r"^Maintenance and planned maintenance \(maintenance_pm_minutes\) counts only stop kind Maintenance or Scheduled: stop kind Operator is "
     r"outside it, and would read as a recorded zero\. Filter by one of those, or ask downtime_minutes for every stop kind\.$", ['Maintenance', 'Scheduled']),
    (dict(measure='order_changes', filters={'kind': 'Operator'}), r'recorded per run setup, which carries no kind', None),
    (dict(measure='lineal_ft', group_by=['customer']), r'recorded per run setup, which carries no customer', None),
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


def test_synonyms_resolve_to_the_models_names_and_the_stores_values():
    store = default_store()
    query = S.validate(S.SemanticQuery('scrap', group_by=['defect'], filters={'paper run': 'we20260914016', 'flute': 'single wall'}, window=WEEK), store)
    assert (query.measure, query.group_by, query.filters) == ('reject_sqft', ['reject_reason'], {'wet_end': 'WE20260914016', 'flute': 'C'})
    query = S.validate(S.SemanticQuery('downtime', filters={'place': 'DB', 'kind': 'pm', 'reason': 'starch'}, window=WEEK), store)
    assert query.filters == {'place': 'Double backer', 'kind': 'Scheduled', 'reason': 'Glue / starch'}
    for word in ('planned', 'planned maintenance', 'Scheduled', 'PM'):   # the planned kind under either contract's name, never Maintenance
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
    lines = run(store, 'reject_sqft', window_of('2026-09-14'), group_by=['order'], shift=2, limit=200)   # a reject's id is its line's
    small = [r for r in lines['rows'] if r['source_count'] <= S.SOURCE_ID_CAP]
    assert small and all(len(r['source_ids']) == r['source_count'] and all(i.count('|') == 3 for i in r['source_ids']) for r in small)
    lanes = run(store, 'sheets', window_of('2026-09-14'), group_by=['lane'], shift=2)   # and a lane's: setup id and lane
    assert all(i.endswith(f"|{r['lane']}") for r in lanes['rows'] for i in r['source_ids'])
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
    partial = run(store, 'stops', {'period': 'week', 'anchor': '2025-01-06'}, compare='previous')   # the records' first week: three production days
    assert partial['previous_value'] is not None and partial['delta'] is None and partial['comparison'].startswith('withheld')
    outside = run(store, 'stops', {'period': 'day', 'anchor': store.first_day}, group_by=['kind'], compare='previous')
    assert outside['previous_window']['coverage'] == 'unavailable' and outside['previous_value'] is None and outside['delta'] is None
    assert all(r['previous_value'] is None for r in outside['rows'])   # outside the records: never the zero an empty sum reads


def test_a_slice_with_no_records_is_a_recorded_zero_where_the_denominator_says_so(store):
    """Downtime % by day lists every production day of the window (the scheduled minutes are its rows); a count by a
    dimension of its own records lists only what was recorded."""
    result = run(store, 'downtime_pct', WEEK, group_by=['day'], filters={'kind': 'External'})
    assert len(result['rows']) == 5 and all(r['value'] >= 0 for r in result['rows'])
    assert all(r['value'] == 0 and r['source_count'] == 0 for r in result['rows'] if r['numerator'] == 0)


def test_the_compiler_answers_in_tens_of_milliseconds(store, latency_budget):
    """Datasette-fast (CLAUDE.md): a generous bound here, scaled on a CI runner by latency_budget (#139); the PR records the measured times."""
    import time
    run(store, 'stops', WEEK)   # the first query pays DuckDB's warm-up
    for measure, kw in (('downtime_minutes', {'group_by': ['grade'], 'filters': {'kind': 'Maintenance'}}),
                        ('dry_end_pct', {'group_by': ['reject_family']}), ('reject_sqft', {'group_by': ['order'], 'shift': 2}),
                        ('trim_usd', {'group_by': ['grade']}), ('speed_all_fpm', {'group_by': ['shift'], 'compare': 'previous'})):
        started = time.perf_counter()
        run(store, measure, MONTH, **kw)
        assert (time.perf_counter() - started) * 1000 < latency_budget(500), measure


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
    assert 'Scheduled' not in [r['label'] for r in data['rows']] and 'Planned maintenance (scheduled)' in [r['label'] for r in data['rows']]
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
    assert '| lane | `setup_orders` | `customer_id` | `customer_id` |' in text and 'not carried yet' not in text   # contract v2 carries the customer
    assert "| reject | `rejects` | `line_id` |" in text and "counted_in = 'dry_end'" in text
    html = docsite.build_site()['/docs/semantic-model'].decode()
    assert '<h1' in html and 'The semantic model' in html and 'downtime_minutes' in html


# ------------------------------------------------------------------ the review of the branch (issue #117)

def test_a_band_read_from_a_filter_stands_beside_the_total_and_the_slices_that_are_its_ratio(store):
    """Maintenance by grade: each grade against all the scheduled minutes, so no band beside a grade; by shift, each shift's
    maintenance minutes against its own scheduled minutes, the ratio the band reads, so each shift carries it."""
    grades = run(store, 'downtime_pct', WEEK, group_by=['grade'], filters={'kind': 'Maintenance'})
    assert grades['band'] == targets.band('maintenance', grades['value']) and all('band' not in r for r in grades['rows'])
    shifts = run(store, 'downtime_pct', WEEK, group_by=['shift'], filters={'kind': 'Maintenance'})
    assert all(r['band'] == targets.band('maintenance', r['value']) for r in shifts['rows'])


def test_a_measure_with_a_band_reads_its_lever_from_targets_json(monkeypatch):
    """One source for a measure's lever (the coordinator on PR #148): a banded measure types no lever and has its band's
    from targets.json; a measure over the same quantity (trim feet and trim dollars beside the trim band) has the same one,
    so the model refuses to load when the two drift apart; the External kind and the power outage read targets.json's
    external block."""
    model, spec = S.model(), json.loads(S.MODEL_PATH.read_text())
    for name, m in model.measures.items():
        bands = [m['band']] if m.get('band') else list((m.get('band_by') or {}).get('bands', {}).values())
        if bands:
            assert 'lever' not in m, name
            assert {model.levers[name]} == {targets.BANDS[b]['lever'] for b in bands}, name
    assert model.levers['setups_above_trim_target'] == targets.BANDS['trim']['lever'] and 'lever' not in model.measures['setups_above_trim_target']
    triples = model.same_quantity()
    pairs = {(source.split(',')[0], other) for source, _, other in triples}
    assert {('trim_pct', 'trim_sqft'), ('trim_pct', 'trim_usd'), ('shear_pct', 'shear_sqft'), ('dry_end_pct', 'reject_sqft'),
            ('dry_end_pct', 'dry_end_usd'), ('op_maint_pct', 'op_maint_minutes'), ('downtime_pct', 'downtime_minutes'),
            ('the dry_end band', 'reject_sheets')} <= pairs
    assert not {other for _, _, other in triples} & {'maintenance_pm_minutes', 'maintenance_pm_pct', 'yield_sqft', 'lineal_ft'}   # other records
    assert all(model.levers[other] == lever for _, lever, other in triples)
    # the owner moves trim's control in targets.json: the typed trim feet and dollars now contradict it, and the model says so
    monkeypatch.setitem(targets.BANDS['trim'], 'lever', 'controllable')
    with pytest.raises(ValueError, match=r'measure trim_sqft has lever schedule-driven, but it measures the quantity of trim_pct, whose lever in targets.json is controllable'):
        S.Model(copy.deepcopy(spec))
    monkeypatch.undo()
    # side trim in another unit is the same quantity, and so are the reject lines counted in sheets
    alias = copy.deepcopy(spec)
    alias['measures'].append({**next(m for m in spec['measures'] if m['name'] == 'trim_sqft'), 'name': 'trim_msf', 'expr': '{trim_sqft} / 1000',
                              'lever': 'controllable', 'synonyms': []})
    with pytest.raises(ValueError, match=r'measure trim_msf has lever controllable, but it measures the quantity of trim_pct'):
        S.Model(alias)
    sheets = copy.deepcopy(spec)
    next(m for m in sheets['measures'] if m['name'] == 'reject_sheets').update(lever='schedule-driven')
    with pytest.raises(ValueError, match=r'measure reject_sheets has lever schedule-driven, but it measures the quantity of the dry_end band, which reads every reject row'):
        S.Model(sheets)
    assert model.value_lever('kind', targets.EXTERNAL_KIND) == targets.EXTERNAL['lever']
    assert all(model.value_lever('reason', r) == targets.EXTERNAL['lever'] for r in targets.EXTERNAL_REASONS)
    assert model.value_lever('kind', 'Maintenance') is None
    text = S.model_markdown(S.MODEL_PATH.read_text())
    assert "| schedule-driven (its band's, targets.json) |" in text and '| `kind` | External | external | targets.json, external |' in text


def test_a_slice_with_a_lever_of_its_own_says_it(store):
    """Planned maintenance is schedule-driven and a power outage external (the owner on PR #111), as a slice or a filter."""
    outage_week = {'period': 'week', 'anchor': '2026-09-14'}   # the week of September's power outage
    kinds = rows_by(run(store, 'downtime_minutes', outage_week, group_by=['kind']), 'kind')
    assert {k: r['lever'] for k, r in kinds.items()} == {'Maintenance': 'controllable', 'Operator': 'controllable', 'Scheduled': 'schedule-driven', 'External': 'external'}
    assert run(store, 'downtime_minutes', WEEK, filters={'kind': 'Scheduled'})['lever'] == 'schedule-driven'
    assert run(store, 'downtime_pct', WEEK, filters={'reason': 'Power outage'})['lever'] == 'external'
    assert run(store, 'maintenance_pm_pct', WEEK)['lever'] == 'schedule-driven' and run(store, 'stops', WEEK)['lever'] == 'controllable'
    # a stop kind read against a band of its own takes that band's lever from targets.json
    assert run(store, 'downtime_pct', WEEK, filters={'kind': 'Operator'})['lever'] == targets.BANDS['operator']['lever']
    assert run(store, 'trim_usd', WEEK)['lever'] == run(store, 'trim_pct', WEEK)['lever'] == targets.BANDS['trim']['lever']


def test_an_entity_where_is_a_small_checked_grammar_and_its_literals_are_bound(store):
    """The physical block can keep an entity to some of its rows (record contract v2 keeps the rejects to the dry-end lines):
    columns of its table, comparisons, literals, AND, OR, IN, NOT, IS NULL. The literals are bound, and the condition holds."""
    spec = json.loads(S.MODEL_PATH.read_text())
    spec['physical']['entities']['reject']['where'] = "family IN ('BOND', 'WARP') AND sqft > 0 AND NOT reason IS NULL"
    model = S.Model(spec)
    query = S.validate(S.SemanticQuery('reject_sqft', group_by=['reject_family'], window=WEEK), store, model)
    statement = S.compile_query(query, model).statements[0]
    assert "'BOND'" not in statement.sql and 'b.family IN (?, ?) AND b.sqft > ? AND NOT b.reason IS NULL' in statement.sql
    assert statement.params[2:5] == ['BOND', 'WARP', 0] and statement.sql.count('?') == len(statement.params)
    kept = S.run(query, store, mdl=model)
    whole = rows_by(run(store, 'reject_sqft', WEEK, group_by=['reject_family']), 'reject_family')
    assert {r['reject_family'] for r in kept['rows']} == {'BOND', 'WARP'}
    assert all(close(r['value'], whole[r['reject_family']]['value']) for r in kept['rows'])
    assert close(kept['value'], whole['BOND']['value'] + whole['WARP']['value'])
    assert S.store_values('reject_family', store, model) == ['BOND', 'WARP']   # a filter's values are the kept rows' too
    with pytest.raises(S.SemanticError, match="the values are: BOND, WARP$"):
        S.validate(S.SemanticQuery('reject_sqft', filters={'reject_family': 'MOISTURE'}, window=WEEK), store, model)


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


def _a_value(store, measure, dim):
    """One value of `dim` the measure's records hold in September: the first of the store's list the measure accepts, or
    for a dimension too long to list (orders, paper runs) the first row of a split by it."""
    values = S.store_values(dim, store)
    if values is None:
        return run(store, measure, MONTH, group_by=[dim], limit=1)['rows'][0][dim]
    for v in values:
        try:
            if run(store, measure, MONTH, filters={dim: v})['source_count']:
                return v
        except S.SemanticError:
            continue   # outside the measure's own filter (the External kind of operator and maintenance minutes): refused


def test_a_share_a_filter_cuts_from_the_whole_denominator_carries_no_band_and_says_so(store):
    """The coordinator's re-review of PR #148: a filter the denominator cannot reach (a grade on downtime %, a customer or a
    reject family on dry-end %) cuts the numerator only, so the figure is the slice's part of the window's ratio, never the
    slice's own rate. It carries no band, says it is read against all the window's scheduled minutes or yield, and its
    denominator is the whole window's, for every banded ratio and every such filter. band_by's own dimension (the stop
    kind) is the ratio its band reads and keeps it."""
    model = S.model()
    pairs = 0
    for name, m in model.measures.items():
        if m['agg'] != 'ratio' or not m.get('denominator_total'):
            continue
        den = model.measures[m['denominator']]['entity']
        own = (m.get('band_by') or {}).get('dimension')
        whole = run(store, name, MONTH)
        rows, _ = model.measure_entities(name)
        for dim in model.dimensions:
            if dim == own or model.dimensions[dim]['type'] == 'time' or model.reach(rows, dim) is None or model.reach(den, dim) is not None:
                continue
            value = _a_value(store, model.measures[m['numerator']]['name'], dim)
            filters = {dim: value, **({own: 'Maintenance'} if own else {})}   # with the kind, the reading a band would have
            got = run(store, name, MONTH, filters=filters)
            pairs += 1
            assert 'band' not in got and 'band_text' not in got, (name, filters, got.get('band_text'))
            assert got['denominator'] == whole['denominator'], (name, filters)   # all the window's scheduled minutes or yield
            assert got['denominator_text'] == "all the window's " + m['unit'].split(' of ', 1)[1], (name, filters)
            assert got['cut'] == [dim] and got['band_withheld'] is bool(m.get('band') or own), (name, filters)
    assert pairs >= 15   # the re-review counted fifteen measure-and-filter pairs that carried a band
    # downtime % of one grade: its part of all the month's scheduled minutes, which the answer says, not 26-C's own clock
    grade = run(store, 'downtime_pct', MONTH, filters={'grade': '26-C'})
    minutes = run(store, 'downtime_minutes', MONTH, filters={'grade': '26-C'})['value']
    assert close(grade['value'], minutes / run(store, 'scheduled_minutes', MONTH)['value'] * 100)
    # under one shift the denominator is that shift's minutes, and the words say so
    assert run(store, 'downtime_pct', MONTH, shift=3, filters={'grade': '26-C'})['denominator_text'] == "all the shift's scheduled minutes in the window"
    # the stop kind alone is band_by's own: its band, against the scheduled minutes, as the downtime review reads it
    kind = run(store, 'downtime_pct', MONTH, filters={'kind': 'Maintenance'})
    assert kind['band'] == targets.band('maintenance', kind['value']) and 'denominator_text' not in kind and kind['cut'] == []


def test_a_row_cut_from_the_whole_denominator_carries_no_band_even_beside_band_bys_own_dimension(store):
    """by=kind,grade: the kind is band_by's own, but the grade cuts each row's stops and never the scheduled minutes, so no
    row carries the kind's band; by=shift with a grade filter: each shift's part of that shift's minutes."""
    rows = run(store, 'downtime_pct', MONTH, group_by=['kind', 'grade'], limit=200)['rows']
    assert rows and all('band' not in r and r['denominator_text'] == "all the window's scheduled minutes" for r in rows)
    shifts = run(store, 'downtime_pct', MONTH, group_by=['shift'], filters={'grade': '26-C', 'kind': 'Maintenance'})
    assert 'band' not in shifts and shifts['denominator_text'] == "all the window's scheduled minutes"
    assert all('band' not in r and r['denominator_text'] == "all the shift's scheduled minutes in the window" for r in shifts['rows'])
    families = run(store, 'dry_end_pct', MONTH, group_by=['grade', 'reject_family'], limit=200)
    assert all('band' not in r and r['denominator_text'] == "all the board grade's yield in the window" for r in families['rows'])
    # a week the window cuts short (September's first, from Monday 31 August) is the window's part of it, and says so
    weeks = run(store, 'downtime_pct', MONTH, group_by=['week'], filters={'grade': '26-C'})
    assert {r['denominator_text'] for r in weeks['rows']} == {"all the production week's scheduled minutes in the window"}
    assert families['band'] == targets.band('dry_end', families['value']) and 'denominator_text' not in families


def test_the_answer_says_a_cut_share_is_read_against_the_whole_window(store):
    """A grade or a customer has a clock or a yield of its own, so the note says the figure is not that rate; a reject family
    has none, so its part of all the yield is all the rate it has, and the note does not say otherwise."""
    for url, head, note in (
            ('/corr/1/month/2026-09-14?measure=semantic&m=downtime_pct&grade=26-C',
             "Downtime, board grade (quality id) 26-C this month: **{v}%** of all the window's scheduled minutes (query 1)",
             "Board grade (quality id) 26-C cuts the downtime only, never the scheduled minutes: the figure is the slice's part of the whole, not its own rate."),
            ('/corr/1/month/2026-09-14?measure=semantic&m=dry_end_pct&customer=C002',
             "Dry-end rejects, customer C002 this month: **{v}%** of all the window's yield (query 1)",
             "Customer C002 cuts the dry-end rejects only, never the yield after trim and shear: the figure is the slice's part of the whole, "
             "not its own rate, so it carries no band."),
            ('/corr/1/month/2026-09-14?measure=semantic&m=dry_end_pct&reject_family=BOND',
             "Dry-end rejects, reject family Bond this month: **{v}%** of all the window's yield (query 1)",
             "Reject family Bond cuts the dry-end rejects only, never the yield after trim and shear: the figure is the slice's part of the whole, "
             "so it carries no band."),
            ('/corr/1/month/2026-09-14/shift/3?measure=semantic&m=op_maint_pct&kind=Maintenance',
             "Downtime (operator and maintenance), stop kind Maintenance, shift 3 this month: **{v}%** of all the shift's scheduled minutes in the window (query 1)",
             "Stop kind Maintenance cuts the operator and maintenance downtime only, never the scheduled minutes: the figure is the slice's part of the "
             "whole, so it carries no band.")):
        resolved = resolve_url(url, store)
        analysis = store.day_analysis(resolved['day'])
        try:
            answer = Agent(analysis).ask(resolved['text'], resolved['context'], request=resolved['request'])
        finally:
            analysis.close()
        data = answer['trace'][0]['data']
        assert answer['sections'][0] == head.format(v=f'{data["value"]:.2f}') and note in answer['sections']


TIME_SPLITS = ((FISCAL, 'week', None), (FISCAL, 'week', 3), (MONTH, 'week', None), (MONTH, 'month', None), (MONTH, 'month', 3),
               (FISCAL, 'period', None), (FISCAL, 'period', 3))


def test_a_time_split_agrees_with_the_period_tools_window_by_window(store):
    """The coordinator's re-review of PR #148: each row of by=week, by=month and by=period is the tile get_period_kpis returns
    for that week, month or 4-4-5 period, for every measure a tile checks. A row the window cuts short (September's first and
    last weeks) is a part of its week, outside any tile, and is skipped; every whole week, month or period is compared."""
    for window, grain, shift in TIME_SPLITS:
        bounds = store._window(window)
        for measure, metric, places, filters in PERIOD_KPIS:
            result = run(store, measure, window, shift=shift, group_by=[grain], filters=filters, limit=200)
            whole = 0
            for row in result['rows']:
                spec = {'period': 'fiscal' if grain == 'period' else grain, 'anchor': row[grain]}
                inner = store._window(spec)
                if inner['start'] < bounds['start'] or inner['end'] > bounds['end']:
                    continue
                tool = next(r for r in store.get_period_kpis(spec, compare=False, shift=shift)['rows'] if r['metric'] == metric)
                assert close(row['value'], tool['current'], places), (measure, metric, window, grain, shift, row[grain], row['value'], tool['current'])
                whole += 1
            assert whole >= (3 if grain == 'week' else 1), (measure, window, grain, shift)


def test_compare_prev_agrees_with_the_period_tools_comparison(store):
    """compare=prev beside get_period_kpis(compare=True): the current and previous values, the change, the previous window and
    whether the change is stated, for every measure a tile checks, over P2 (against P1), a week and a month, all shifts and one."""
    for window, shift in ((FISCAL, None), (FISCAL, 3), (WEEK, None), (MONTH, 2)):
        tool = store.get_period_kpis(window, compare=True, shift=shift)
        tiles = {r['metric']: r for r in tool['rows']}
        for measure, metric, places, filters in PERIOD_KPIS:
            got = run(store, measure, window, shift=shift, filters=filters, compare='previous')
            tile = tiles[metric]
            assert got['previous_window']['start'] == tool['previous_window']['start'] and got['previous_window']['end'] == tool['previous_window']['end']
            assert got['comparison'] == tool['comparison'] == 'complete', (measure, window, shift)
            assert close(got['value'], tile['current'], places) and close(got['previous_value'], tile['previous'], places), (measure, metric, window, shift)
            # the tile's change is of its two rounded values: a rounding step apart at most
            assert abs(got['delta'] - tile['delta']) <= 1.01 * 10 ** -places + 1e-9, (measure, metric, window, shift, got['delta'], tile['delta'])
            if 'band' in tile:
                assert got.get('band') == tile['band'], (measure, metric, window, shift)


def test_a_duckdb_error_is_a_refusal_never_a_server_error(store):
    """A where that passes the grammar but fails in DuckDB (a text column compared with a number) is refused with the reason,
    as a SemanticError the agent records as a refused call, never an exception that would be a 500: from the query, and
    from a filter's value lookup in validate (the review of PR #148)."""
    for where in ('family > 5', 'family = sqft'):
        spec = json.loads(S.MODEL_PATH.read_text())
        spec['physical']['entities']['reject']['where'] = where
        model = S.Model(spec)
        S._VALUES.clear()
        for query in (S.SemanticQuery('reject_sqft', window=WEEK), S.SemanticQuery('reject_sqft', window=WEEK, filters={'reject_family': 'BOND'}),
                      S.SemanticQuery('reject_sqft', window=WEEK, filters={'reject_reason': 'zzz'})):
            with pytest.raises(S.SemanticError, match=r'^The semantic model could not run this request in DuckDB'):
                S.run(query, store, mdl=model)
    S._VALUES.clear()


def test_rows_that_do_not_add_up_to_the_total_are_refused(store, monkeypatch):
    """The review of PR #148 caught DuckDB 1.5.1's join filter pushdown losing a LEFT JOIN's matches under CPU contention: every
    day of a dry-end split read 0.00% beside a 0.41% total. Every connection now runs with that optimizer off, and a result
    whose listed rows of an additive quantity do not add up to its total is refused, never shown."""
    from investigator.analysis import DUCKDB_CONFIG
    assert DUCKDB_CONFIG['disabled_optimizers'] == 'join_filter_pushdown'
    assert store._fetch("SELECT current_setting('disabled_optimizers') AS v", [])[0]['v'] == 'join_filter_pushdown'
    analysis = store.day_analysis('2026-09-14')
    try:
        assert analysis._db.execute("SELECT current_setting('disabled_optimizers')").fetchone()[0] == 'join_filter_pushdown'
    finally:
        analysis.close()
    days = run(store, 'dry_end_pct', MONTH, group_by=['day'], filters={'reject_family': 'BOND'})
    assert close(sum(r['numerator'] for r in days['rows']), days['numerator']) and all(r['numerator'] > 0 for r in days['rows'])
    real = store._fetch

    def lossy(sql, params, limit=1000):   # the join's matches lost: each day's numerator read null, filled as a zero
        rows = real(sql, params, limit)
        return [{**r, 'numerator': 0, 'value': 0} if 'd0' in r and 'numerator' in r else r for r in rows]
    monkeypatch.setattr(store, '_fetch', lossy)
    with pytest.raises(S.SemanticError, match='do not add up to its total'):
        run(store, 'dry_end_pct', MONTH, group_by=['day'], filters={'reject_family': 'BOND'})
    with pytest.raises(S.SemanticError, match='do not add up to its total'):
        run(store, 'downtime_pct', MONTH, group_by=['grade'])
    run(store, 'downtime_pct', MONTH, group_by=['grade'], limit=2)   # a limited list is not the whole: not checked


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
