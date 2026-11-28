"""The verified examples (issue #187): every example's rows equal a hand-written SQL query over the record tables.

Each oracle below is written from the example's definition, beside it, in plain SQL over the store's own tables (setups,
stops, rejects, rolls, shifts): it never calls the semantic model's compiler. The window is worked out by hand from the page's
day and the 4-4-5 calendar, and the grade baseline from targets.json's words. No expected number is typed anywhere: the
oracle computes it. Each example runs in its own window and in each alternate, at the eval anchor (the golden store as of
2026-09-30, page on 14 Sep 2026) and at a second anchor (page on 15 Oct 2025, where the grade baseline's trailing year is cut
short by the start of the records).
"""
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from investigator import examples, semantic, targets
from investigator.record_store import SPEED_MIN_RUN_MINUTES, default_store, fiscal_bounds

ANCHORS = ('2026-09-14', '2025-10-15')
EXAMPLES = examples.load(store=default_store())   # with the store: every filter value is one the records hold
BY_ID = {e['id']: e for e in EXAMPLES}
CASES = [(e['id'], day, window) for e in EXAMPLES if examples.shape(e) != 'refusal'
         for day in ANCHORS for window in [e['plan']['window'], *e.get('alternates', [])]]


@pytest.fixture(scope='module')
def store():
    return default_store()


def q(store, sql, params):
    return store._fetch(sql, [p.isoformat() if isinstance(p, date) else p for p in params], limit=500)


# ------------------------------------------------------------------ the windows, by hand

def window_dates(name, day):
    """(first day, last day) of a relative window on the page's day, from the 4-4-5 calendar and the weekday."""
    d = date.fromisoformat(day)
    monday = d - timedelta(days=d.weekday())
    p_start, p_end, _ = fiscal_bounds(d)
    if name == 'ytd':
        return date(d.year, 1, 1), d
    if name == 'ptd':
        return p_start, d
    if name == 'period':
        return p_start, p_end
    if name == 'previous_period':
        start, end, _ = fiscal_bounds(p_start - timedelta(days=1))
        return start, end
    if name == 'this_week':
        return monday, monday + timedelta(days=4)
    if name == 'last_week':
        return monday - timedelta(days=7), monday - timedelta(days=3)
    if name == 'this_month':
        first = d.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt - timedelta(days=1)
    raise AssertionError(name)


def trailing_year(store, end):
    """targets.json's baseline window: the 365 days ending on the day asked about (the window's last day), inside the records."""
    last = min(end, store._last)
    return max(last - timedelta(days=364), store._first), last


# The grade baseline, written from targets.json's definition: lineal feet over running minutes of every setup of the grade in
# the trailing year that ran at least SPEED_MIN_RUN_MINUTES, rounded to 0.1 ft/min half to even as the tools' Python round does;
# a grade with none reads its target.
BASELINE = """base AS (
  SELECT quality_id, round_even(sum(lineal_ft) / (sum(seconds - stop_seconds) / 60), 1) AS fpm
  FROM setups WHERE production_date BETWEEN ? AND ? AND seconds - stop_seconds >= ? GROUP BY quality_id),
speed AS (SELECT s.*, coalesce(base.fpm, s.target) AS baseline FROM setups s LEFT JOIN base ON base.quality_id = s.quality_id),
-- targets.json's speed gap: a run (a paper run on its production day and shift, as the money ledger cuts it) under its grade's
-- baseline, the feet it would have made at the baseline in its running minutes less the feet it made; a run over its baseline has none
runs AS (SELECT production_date, shift, wet_end_id, quality_id, greatest(sum(baseline * (seconds - stop_seconds) / 60) - sum(lineal_ft), 0) AS gap
         FROM speed WHERE production_date BETWEEN ? AND ? GROUP BY production_date, shift, wet_end_id, quality_id)"""


def baseline_params(store, start, end):
    """The trailing year and the shortest run of the baseline, then the window of the runs' gaps."""
    first, last = trailing_year(store, end)
    return [first, last, SPEED_MIN_RUN_MINUTES * 60, start, end]


def band_word(band_id, value):
    """The band of a value, read by hand from targets.json's lines (home run, target, flag)."""
    spec = next(b for b in json.loads(Path(targets.__file__).with_name('targets.json').read_text())['bands'] if b['id'] == band_id)
    if spec['home_run'] is not None and value <= spec['home_run']:
        return 'home run'
    if value <= spec['target']:
        return 'on target'
    if spec['flag'] is not None and value > spec['flag']:
        return 'flag'
    return 'over target'


# basis weight, by hand: the leading number of the paper grade ('26 lb liner' is 26 lb per MSF)
WEIGHT = "CAST(split_part(paper_grade, ' ', 1) AS DOUBLE)"


# ------------------------------------------------------------------ the oracles: (key, value, the other fields) per row

def top_maintenance_downtimes(store, a, b):
    total = q(store, "SELECT sum(seconds) / 60 AS m FROM stops WHERE production_date BETWEEN ? AND ? AND kind = 'Maintenance'", [a, b])[0]['m']
    rows = q(store, """SELECT reason, sum(seconds) / 60 AS minutes, count(*) AS stops FROM stops
                       WHERE production_date BETWEEN ? AND ? AND kind = 'Maintenance'
                       GROUP BY reason ORDER BY minutes DESC, reason LIMIT 5""", [a, b])
    return [((r['reason'],), r['minutes'], {'stops': r['stops'], 'minutes_per_stop': r['minutes'] / r['stops'],
                                          'share_pct': r['minutes'] / total * 100}) for r in rows]


def where_is_the_downtime(store, a, b):
    scheduled = q(store, 'SELECT sum(scheduled_minutes) AS m FROM shifts WHERE production_date BETWEEN ? AND ?', [a, b])[0]['m']
    rows = q(store, """SELECT kind, reason, sum(seconds) / 60 AS minutes FROM stops WHERE production_date BETWEEN ? AND ?
                       GROUP BY kind, reason ORDER BY minutes DESC, kind, reason LIMIT 25""", [a, b])
    return [((r['kind'], r['reason']), r['minutes'] / scheduled * 100, {'numerator': r['minutes'], 'denominator': scheduled}) for r in rows]


def speed_loss_contributors(store, a, b):
    rows = q(store, f"""WITH {BASELINE},
        feet AS (SELECT 0 AS part, st.reason AS k, sum(st.seconds / 60 * sp.baseline) AS lost FROM stops st JOIN speed sp ON sp.setup_id = st.setup_id
                 WHERE st.production_date BETWEEN ? AND ? AND st.kind IN ('Maintenance', 'Operator') GROUP BY st.reason
                 UNION ALL
                 SELECT 1, quality_id, sum(gap) FROM runs GROUP BY quality_id)
        SELECT part, k, lost FROM feet ORDER BY lost DESC, part, k LIMIT 3""", baseline_params(store, a, b) + [a, b])
    return [((r['k'],), r['lost'], {'part': r['part']}) for r in rows]


def _grades(store, a, b, ascending):
    rows = q(store, f"""WITH {BASELINE},
        gaps AS (SELECT quality_id, sum(gap) AS recoverable FROM runs GROUP BY quality_id)
        SELECT s.quality_id, sum(lineal_ft) / (sum(seconds - stop_seconds) / 60) AS fpm, sum(lineal_ft) AS feet,
               sum(seconds - stop_seconds) / 60 AS run_min, sum(baseline * (seconds - stop_seconds) / 60) AS base_ft, any_value(g.recoverable) AS recoverable
        FROM speed s JOIN gaps g ON g.quality_id = s.quality_id WHERE production_date BETWEEN ? AND ? GROUP BY s.quality_id
        ORDER BY fpm {'ASC' if ascending else 'DESC'}, s.quality_id LIMIT 5""", baseline_params(store, a, b) + [a, b])
    # the footage recoverable is the grade's runs' gaps, and the gap in ft/min is that footage over all its running minutes
    return [((r['quality_id'],), r['fpm'], {'baseline_speed_fpm': r['base_ft'] / r['run_min'], 'speed_gap_fpm': r['recoverable'] / r['run_min'],
                                           'speed_gap_ft': r['recoverable']}) for r in rows]


def best_running_grades(store, a, b):
    return _grades(store, a, b, ascending=False)


def worst_running_grades(store, a, b):
    return _grades(store, a, b, ascending=True)


def stops_on_worst_running_grades(store, a, b):
    # the five slowest grades by speed without stops (as worst-running-grades ranks them), each led by its stops (the question names
    # stops: exact over summary), then its minutes down, minutes per stop and downtime % of its own setup clock; its speed comes last
    rows = q(store, """WITH g AS (SELECT quality_id, sum(lineal_ft) / (sum(seconds - stop_seconds) / 60) AS fpm, sum(seconds) / 60 AS clock
                                  FROM setups WHERE production_date BETWEEN ? AND ? GROUP BY quality_id),
                       d AS (SELECT s.quality_id, count(*) AS stops, sum(st.seconds) / 60 AS minutes FROM stops st JOIN setups s ON s.setup_id = st.setup_id
                             WHERE st.production_date BETWEEN ? AND ? GROUP BY s.quality_id)
                       SELECT g.quality_id, g.fpm, g.clock, coalesce(d.stops, 0) AS stops, coalesce(d.minutes, 0) AS minutes
                       FROM g LEFT JOIN d ON d.quality_id = g.quality_id ORDER BY g.fpm, g.quality_id LIMIT 5""", [a, b, a, b])
    return [((r['quality_id'],), r['stops'], {'downtime_minutes': r['minutes'],
                                             'minutes_per_stop': r['minutes'] / r['stops'] if r['stops'] else None,
                                             'downtime_clock_pct': r['minutes'] / r['clock'] * 100, 'speed_run_fpm': r['fpm']}) for r in rows]


def where_is_the_waste(store, a, b):
    s = q(store, 'SELECT sum(gross_sqft) AS gross, sum(trim_sqft) AS trim, sum(shear_sqft) AS shear FROM setups WHERE production_date BETWEEN ? AND ?', [a, b])[0]
    rejects = q(store, "SELECT sum(sqft) AS sqft FROM rejects WHERE counted_in = 'dry_end' AND production_date BETWEEN ? AND ?", [a, b])[0]['sqft'] or 0
    rolls = q(store, f"""SELECT sum({WEIGHT} * peel_sqft) AS peel, sum({WEIGHT} * core_sqft) AS core,
                                sum({WEIGHT} * (lineal_ft * width_in / 12 + peel_sqft + core_sqft)) AS paper
                         FROM rolls WHERE production_date BETWEEN ? AND ?""", [a, b])[0]
    lines = [('trim_pct', s['trim'] / s['gross'] * 100, 'trim'), ('shear_pct', s['shear'] / s['gross'] * 100, 'shear'),
             ('dry_end_pct', rejects / (s['gross'] - s['trim'] - s['shear']) * 100, 'dry_end'),
             ('peel_pct', rolls['peel'] / rolls['paper'] * 100, 'peel'), ('core_pct', rolls['core'] / rolls['paper'] * 100, 'core')]
    return [((m,), v, {'band': band_word(band, v)}) for m, v, band in lines]


def high_trim_by_grade(store, a, b):
    flag = targets.BANDS['trim']['flag']
    rows = q(store, """SELECT quality_id, sum(trim_sqft) / sum(gross_sqft) * 100 AS pct, count(*) FILTER (WHERE trim_sqft / gross_sqft * 100 > ?) AS over
                       FROM setups WHERE production_date BETWEEN ? AND ? GROUP BY quality_id ORDER BY pct DESC, quality_id LIMIT 5""", [flag, a, b])
    return [((r['quality_id'],), r['pct'], {'setups_above_trim_target': r['over'], 'band': band_word('trim', r['pct'])}) for r in rows]


def high_trim_by_setup(store, a, b):
    flag = targets.BANDS['trim']['flag']
    # a grade none of whose setups ran over the flag has no row: there is nothing of it to count
    rows = q(store, """SELECT quality_id, count(*) FILTER (WHERE trim_sqft / gross_sqft * 100 > ?) AS over, count(*) AS setups
                       FROM setups WHERE production_date BETWEEN ? AND ? GROUP BY quality_id HAVING over > 0
                       ORDER BY over DESC, quality_id LIMIT 5""", [flag, a, b])
    return [((r['quality_id'],), r['over'], {'setups': r['setups']}) for r in rows]


def _shear_by(store, a, b, column, order):
    rows = q(store, f"""SELECT {column} AS k, sum(shear_sqft) / sum(gross_sqft) * 100 AS pct FROM setups WHERE production_date BETWEEN ? AND ?
                        GROUP BY {column} ORDER BY {order} LIMIT 25""", [a, b])
    return [((r['k'],), r['pct'], {'band': band_word('shear', r['pct'])}) for r in rows]


def shear_by_grade(store, a, b):
    return _shear_by(store, a, b, 'quality_id', 'pct DESC, k')


def shear_by_shift(store, a, b):
    return _shear_by(store, a, b, 'shift', 'k')


def shear_by_machine(store, a, b):
    s = q(store, 'SELECT sum(shear_sqft) / sum(gross_sqft) * 100 AS pct FROM setups WHERE production_date BETWEEN ? AND ?', [a, b])[0]['pct']
    return [((), s, {'band': band_word('shear', s)})]


def dry_end_top_reasons(store, a, b):
    yield_sqft = q(store, 'SELECT sum(gross_sqft - trim_sqft - shear_sqft) AS y FROM setups WHERE production_date BETWEEN ? AND ?', [a, b])[0]['y']
    rows = q(store, """SELECT reason, sum(sqft) AS sqft FROM rejects WHERE counted_in = 'dry_end' AND production_date BETWEEN ? AND ?
                       GROUP BY reason ORDER BY sqft DESC, reason LIMIT 5""", [a, b])
    return [((r['reason'],), r['sqft'], {'dry_end_pct': r['sqft'] / yield_sqft * 100}) for r in rows]


def peel_and_core(store, a, b):
    r = q(store, f"""SELECT sum({WEIGHT} * (peel_sqft + core_sqft)) AS lost, sum({WEIGHT} * (lineal_ft * width_in / 12 + peel_sqft + core_sqft)) AS paper
                     FROM rolls WHERE production_date BETWEEN ? AND ?""", [a, b])[0]
    return [((), r['lost'] / r['paper'] * 100, {'numerator': r['lost'] / 1000, 'denominator': r['paper'] / 1000})]


ORACLES = {name.replace('_', '-'): fn for name, fn in globals().items() if callable(fn) and name.replace('_', '-') in BY_ID}


def test_every_example_has_an_oracle():
    assert set(ORACLES) == {e['id'] for e in EXAMPLES if examples.shape(e) != 'refusal'}


def _key(row, example):
    if examples.shape(example) == 'lines':
        return (row['key']['measure'],)
    return tuple(row['key'].values())


def _field(row, field):
    """A row's other field: a measure beside it, the measure that ranked it (`rank`), or a field of its own (a band, a share)."""
    if field in row.get('beside', {}):
        return row['beside'][field]
    if (row.get('rank') or {}).get('measure') == field:
        return row['rank']['value']
    return row.get(field)


@pytest.mark.parametrize('eid, day, window', CASES, ids=[f'{e}@{d}@{w}' for e, d, w in CASES])
def test_example_equals_its_oracle(store, eid, day, window):
    example = BY_ID[eid]
    got = examples.run_example(example, store, day, window)
    a, b = window_dates(window, day)
    assert (got['window']['start'], got['window']['end']) == (a.isoformat(), b.isoformat())
    want = ORACLES[eid](store, a, b)
    assert want, f'{eid}: the oracle found no rows in {a} to {b}, so the example proves nothing there'
    assert [_key(r, example) for r in got['rows']] == [w[0] for w in want]   # the same rows, in the same order
    for row, (key, value, other) in zip(got['rows'], want):
        assert row['value'] == pytest.approx(value, abs=0.01), (key, 'value')
        for field, expected in other.items():
            actual = _field(row, field)
            if expected is None or isinstance(expected, str):
                assert actual == expected, (key, field)
            else:
                assert actual == pytest.approx(expected, abs=0.01), (key, field)


def test_the_customer_credits_example_is_refused_naming_shipments(store):
    got = examples.run_example(BY_ID['customer-credits'], store, ANCHORS[0])
    assert got['refused'] and 'shipments' in got['refused'] and got['rows'] == []


def test_the_by_machine_example_says_the_records_hold_one_corrugator(store):
    got = examples.run_example(BY_ID['shear-by-machine'], store, ANCHORS[0])
    assert any('one corrugator' in s for s in got['says'])
    # and the model refuses a split by machine for that reason, never as a dimension it lacks
    for word in ('machine', 'corrugator'):
        with pytest.raises(semantic.SemanticError, match='one corrugator'):
            semantic.validate({'measure': 'shear_pct', 'group_by': [word], 'window': {'period': 'week', 'anchor': '2026-09-14'}}, store)


def test_the_stops_on_the_worst_running_grades_lead_with_stops(store):
    """The coordinator's review of 91e8b33, item 7: the question names stops, so each row leads with the grade's stops, then its
    minutes down, minutes per stop and downtime % of its own clock, on the worst running grades by speed (CLAUDE.md, exact over
    summary): the same grades as worst-running-grades, slowest first, each with the speed that ranked it."""
    example = BY_ID['stops-on-worst-running-grades']
    got = examples.run_example(example, store, ANCHORS[0])
    worst = examples.run_example(BY_ID['worst-running-grades'], store, ANCHORS[0])
    assert example['plan']['measure'] == 'stops' and example['beside'] == ['downtime_minutes', 'minutes_per_stop', 'downtime_clock_pct']
    assert [r['key'] for r in got['rows']] == [r['key'] for r in worst['rows']] and len(got['rows']) == 5
    assert [r['rank'] for r in got['rows']] == [{'measure': 'speed_run_fpm', 'value': r['value']} for r in worst['rows']]
    assert all(isinstance(r['value'], int) for r in got['rows'])   # the lead figure is a count of stops, never a speed
    assert examples.describe_example(example).startswith('stops (stops) · by board grade · the 5 with the lowest speed without stops')


def test_the_per_run_shortfall_is_never_labelled_as_baseline_less_speed(store):
    """The coordinator's review of 91e8b33, item 6: speed_gap_fpm sums only the runs under their baseline (targets.json's gap) over
    all the running minutes, so it is not the grade baseline less the grade's speed (26-C: 827.80 less 824.01 is not 15.30). Its
    label says what it is, so a plant reader beside the baseline never takes it for the difference."""
    got = examples.run_example(BY_ID['best-running-grades'], store, ANCHORS[0])
    differs = [r for r in got['rows'] if abs(r['beside']['baseline_speed_fpm'] - r['value'] - r['beside']['speed_gap_fpm']) > 0.01]
    assert differs   # on the golden page the two figures differ, so a label that reads as the difference misleads
    label = semantic.model().measures['speed_gap_fpm']['label']
    assert label == 'Shortfall on runs under baseline' and 'gap to grade baseline' not in examples.describe_example(BY_ID['best-running-grades'])


def test_no_answer_says_the_records_hold_no_basis_weight(store):
    """The coordinator's review of 91e8b33, item 2: peel and core are read by weight, each paper grade at the basis weight the board
    recipes give it, so no answer may say there is no basis weight. The paper-consumed answer says it stays in area; the hand
    router's weight refusal still refuses, naming where the weights come from.

    The coordinator's review of e545dfb, item 1: the semantic model answers peel, core and the paper consumed in pounds at a public
    address (peel_lb, core_lb, paper_consumed_lb), so no text may say that paper by weight, or tons of paper, waits on issue #181:
    only the cost does. The refusal says the router does not read weights and points to m=peel_lb, an address that answers in lb."""
    import re
    from investigator.agent import Context, route
    from investigator.record_store import RecordStore
    weighed = {name: semantic.model().measures[name] for name in ('peel_lb', 'core_lb', 'paper_consumed_lb')}
    assert all(m['unit'] == 'lb' for m in weighed.values())   # the measures the wording points to answer in pounds
    note = store.get_rollstock({'period': 'fiscal', 'anchor': '2026-08-03'})['msf_note']
    for text in (note, RecordStore.get_rollstock.__doc__):
        flat = ' '.join(text.split())
        assert 'no basis weight' not in flat and 'board recipes' in flat and 'area' in flat, flat
        assert 'semantic model reads the paper consumed, the peel and the core by weight' in flat, flat
        assert 'only their cost waits on pricing paper by weight' in flat and not re.search(r'tons of paper', flat), flat
    for question in ('pounds of peel this week', 'how many tons of paper did we use this week'):
        with pytest.raises(ValueError) as refused:
            route(question, Context(ANCHORS[0], 2, scope='shift', period='day'), store.production_days, store)
        message = str(refused.value)
        assert 'no basis weight' not in message and 'no weights' not in message and 'board recipe' in message, message
        assert message.startswith('The router does not read tons or pounds: the semantic model reads peel, core and paper consumed '
                                  'by weight'), message
        assert 'm=peel_lb' in message and 'only their cost waits on issue #181.' in message, message
        assert 'not counted' not in message and not re.search(r'paper by weight[^.;]*wait', message), message


def test_a_compared_window_is_held_at_the_querys_own_baselines(store):
    """The week before is read at the baselines of the week asked about, as get_speed_variance holds both sides: the grade
    baseline is the same number on both sides."""
    got = semantic.run({'measure': 'baseline_speed_fpm', 'group_by': ['grade'], 'compare': 'previous',
                        'window': {'period': 'week', 'anchor': '2026-09-14'}}, store)
    assert got['rows'] and all(r['previous_value'] == pytest.approx(r['value'], abs=1e-9) for r in got['rows'])


def test_the_speed_gap_is_each_runs_and_its_shifts_add_up_to_the_day(store):
    """targets.json's gap is a run's, and a run is cut by shift as the money ledger cuts it (#164: shift figures add up to the day):
    the shifts' rows add up to the window, which is speed_baseline_gap's runs of each shift."""
    window = {'period': 'week', 'anchor': '2026-09-07'}
    whole = semantic.run({'measure': 'speed_gap_ft', 'window': window}, store)
    by_shift = semantic.run({'measure': 'speed_gap_ft', 'group_by': ['shift'], 'window': window}, store)['rows']
    tool = [r for s in (1, 2, 3) for r in store.speed_baseline_gap(window, s)['runs']]
    assert whole['value'] == pytest.approx(sum(r['gap_ft'] for r in tool), abs=0.05 * len(tool) + 0.01)
    assert sum(r['value'] for r in by_shift) == pytest.approx(whole['value'], abs=1e-6) and all(r['value'] >= 0 for r in by_shift)
    assert sum(r['share_pct'] for r in by_shift) == pytest.approx(100)


@pytest.mark.parametrize('window', [{'period': 'ptd', 'anchor': day} for day in ANCHORS] + [{'period': 'fiscal', 'anchor': '2026-09-29'}])
def test_the_grade_baseline_is_the_tools_baseline(store, window):
    """The model's baseline is record_store.grade_speed_baselines, grade by grade, for the trailing year the tools read: the 365 days
    ending on the window's last day, cut to the records (the 4-4-5 period of 29 Sep 2026 runs past the records' last day)."""
    _, _, _, b = semantic.parse_window(window)
    got = semantic.run({'measure': 'baseline_speed_fpm', 'group_by': ['grade'], 'window': window}, store)
    tools = store.grade_speed_baselines(b.isoformat())['grades']
    assert {r['grade']: round(r['value'], 1) for r in got['rows']} == {g: v['baseline_fpm'] for g, v in tools.items() if g in {r['grade'] for r in got['rows']}}


# ------------------------------------------------------------------ the windows, the defaults and the file's checks

@pytest.mark.parametrize('day', ANCHORS)
@pytest.mark.parametrize('name', ['ptd', 'period', 'previous_period', 'this_week', 'last_week', 'this_month', 'ytd'])
def test_a_relative_window_covers_the_days_worked_out_by_hand(day, name):
    spec, _ = semantic.relative_window(name, day)
    _, _, start, end = semantic.parse_window(spec)
    assert (start, end) == window_dates(name, day)


def test_the_period_to_date_is_never_compared_or_split_by_week(store):
    window = {'period': 'ptd', 'anchor': '2026-09-14'}
    with pytest.raises(semantic.SemanticError, match='period to date has none'):
        semantic.validate({'measure': 'stops', 'window': window, 'compare': 'previous'}, store)
    with pytest.raises(semantic.SemanticError, match='ends on a part'):
        semantic.validate({'measure': 'stops', 'window': window, 'group_by': ['week']}, store)
    assert semantic.validate({'measure': 'stops', 'window': window, 'group_by': ['day']}, store).group_by == ['day']


def test_rolls_carry_no_shift(store):
    with pytest.raises(semantic.SemanticError, match='carries no shift'):
        semantic.validate({'measure': 'peel_pct', 'window': {'period': 'week', 'anchor': '2026-09-14'}, 'shift': 2}, store)


def test_a_measure_the_records_lack_is_refused_naming_what_is_missing():
    for word in ('customer credits', 'customer_credits', 'credits'):
        with pytest.raises(semantic.SemanticError, match='no shipments') as caught:
            semantic.resolve_measure(word)
        assert caught.value.choices == ['reject_sqft']


def test_every_default_says_who_ruled_it_and_when():
    rules = semantic.model().defaults['rules']
    assert rules and all(r['ruled_by'] in semantic.RULERS and date.fromisoformat(r['ruled_on']) for r in rules.values())
    assert rules['speed_loss']['ruled_by'] == 'owner-pending'   # issue #187: the contributors are the owner's to rule


def _spec_with(change):
    spec = json.loads(semantic.MODEL_PATH.read_text())
    change(spec)
    return spec


@pytest.mark.parametrize('change, message', [
    (lambda s: s['defaults']['rules'][0].update(ruled_by='someone'), 'ruled by one of'),
    (lambda s: s['defaults']['rules'][0].update(ruled_on='17 Nov'), 'the day it was ruled'),
    (lambda s: s['defaults']['rules'][0]['applies'].update(window='fortnight'), 'not a relative window'),
    (lambda s: s['defaults']['relative_windows'][1].update(step=-1), 'only holds'),
    (lambda s: s['defaults']['relative_windows'][0].pop('interim', None) or s['defaults']['relative_windows'][0].update(period='quarter'), 'no interim'),
    (lambda s: s['absent']['concepts'][0].update(words=['downtime']), 'already a measure'),
])
def test_the_model_refuses_a_default_it_cannot_hold(change, message):
    with pytest.raises(ValueError, match=message):
        semantic.Model(_spec_with(change))


def _examples_with(tmp_path, change):
    spec = json.loads(examples.EXAMPLES_PATH.read_text())
    change(spec['examples'])
    path = tmp_path / 'examples.json'
    path.write_text(json.dumps(spec))
    return path


@pytest.mark.parametrize('change, message', [
    (lambda e: e[0]['plan'].update(measure='downtime_hours'), 'No measure'),
    (lambda e: e[0]['plan'].update(group_by=['grain']), 'No dimension'),
    (lambda e: e[0]['plan'].update(group_by=['machine']), 'one corrugator'),
    (lambda e: e[0]['plan'].update(window='fortnight'), 'not a relative window'),
    (lambda e: e[0]['plan'].update(limit=3), 'cites top_n'),
    (lambda e: e[0].update(beside=['stops']), 'cites reason_ranking'),
    (lambda e: e[0].update(rules=['no_such_rule']), 'does not hold'),
    (lambda e: e[0]['rewordings'].pop(), 'rewordings'),
    (lambda e: e[1].update(id=e[0]['id']), 'new id'),
    (lambda e: e[0].update(lever='morale'), 'lever'),
])
def test_an_example_the_model_cannot_hold_fails_to_load(tmp_path, change, message):
    with pytest.raises(examples.ExampleError, match=message):
        examples.load(_examples_with(tmp_path, change))


def _ranked(e):
    return next(x for x in e if x['id'] == 'stops-on-worst-running-grades')


@pytest.mark.parametrize('change, message', [
    (lambda e: _ranked(e)['plan']['rank_by'].update(measure='speed_hours'), 'No measure'),
    (lambda e: _ranked(e)['plan']['rank_by'].update(order='dimension'), 'rank_by orders by value'),
    (lambda e: _ranked(e)['plan']['rank_by'].update(limit=3), 'cites top_n'),
    (lambda e: _ranked(e)['plan'].update(limit=5), 'rank_by holds the order and the limit'),
    (lambda e: _ranked(e)['plan'].update(group_by=[]), 'rank_by ranks the rows of a split'),
])
def test_a_ranked_example_the_model_cannot_hold_fails_to_load(tmp_path, change, message):
    with pytest.raises(examples.ExampleError, match=message):
        examples.load(_examples_with(tmp_path, change))


@pytest.mark.parametrize('value, message', [('Maintenence', "No stop kind 'Maintenence' in the records"),
                                            ('maintenance', "the records hold it as 'Maintenance'")])
def test_an_example_naming_a_value_the_records_lack_fails_to_load(tmp_path, store, value, message):
    """The coordinator's review of 91e8b33, item 4: a filter value is checked against the store's own values when the examples load
    with the store (the tests, evals/run.py and the app at start), so a misspelled value fails the build, never renders."""
    path = _examples_with(tmp_path, lambda e: e[0]['plan']['filters'].update(kind=value))
    with pytest.raises(examples.ExampleError, match=message):
        examples.load(path, store=store)


def test_the_app_refuses_to_start_on_an_example_the_records_cannot_hold(tmp_path, store, monkeypatch):
    from investigator import server
    monkeypatch.setattr(examples, 'EXAMPLES_PATH', _examples_with(tmp_path, lambda e: e[0]['plan']['filters'].update(kind='Maintenence')))
    with pytest.raises(examples.ExampleError, match="No stop kind 'Maintenence'"):
        server.Workbench(store=store)


# ------------------------------------------------------------------ the reading score

def test_the_reading_score_reads_every_wording_and_is_never_a_gate(store):
    score = examples.reading_score(store)
    assert score['of'] == len(EXAMPLES) * (examples.REWORDINGS + 1) == len(score['rows'])
    assert score['owner_of'] == len(EXAMPLES) and 0 <= score['read'] <= score['of']
    assert {r['outcome'] for r in score['rows']} <= set(examples.OUTCOMES) and all(r['read_as'] for r in score['rows'])
    assert score['stand_in'] == sum(r['outcome'] == examples.STAND_IN for r in score['rows'])
    assert examples.score_line(score).startswith(f'verified examples read correctly: {score["read"]} of {score["of"]}')
    assert f'{score["stand_in"]} asked in a stand-in window, never counted as a pass' in examples.score_line(score)


def _stand_in_model():
    """The model with its year to date read in a stand-in window, the 4-4-5 period that holds the page day, as it was until PR #186
    added the year to date: the year to date is given a period the model does not read (a quarter), with the period as its
    interim."""
    spec = json.loads(semantic.MODEL_PATH.read_text())
    next(w for w in spec['defaults']['relative_windows'] if w['name'] == 'ytd').update(period='quarter', interim='period')
    return semantic.Model(spec)


def test_a_wording_read_in_a_stand_in_window_never_scores_a_pass(store):
    """The coordinator's review of 91e8b33, item 1: a wording asked in the year to date while the model reads it in its stand-in (the
    4-4-5 period) is not read as the example's plan, whatever the router makes of it: the stand-in ranks reasons otherwise. Such a
    row is reported apart, as a stand-in, and never counted as a pass, whether the wording names the window or leaves it unsaid."""
    mdl = _stand_in_model()
    example, text = BY_ID['top-maintenance-downtimes'], 'top 5 maintenance downtime reasons by minutes this period'
    for window in ('ytd', None):
        assert examples.score_wording(example, text, window, store, mdl=mdl)['outcome'] == examples.STAND_IN, window
    assert examples.score_wording(example, text, 'period', store, mdl=mdl)['outcome'] == 'pass'   # its own window is read on its plan
    score = examples.reading_score(store, mdl=mdl, examples=[example])
    stand_ins = [r for r in score['rows'] if r['outcome'] == examples.STAND_IN]
    assert stand_ins and all(r['window'] == 'ytd' for r in stand_ins) and score['stand_in'] == len(stand_ins)
    assert score['read'] == sum(r['outcome'] == 'pass' for r in score['rows']) and not any(r['window'] == 'ytd' and r['outcome'] == 'pass' for r in score['rows'])
    assert f'{len(stand_ins)} asked in a stand-in window, never counted as a pass' in examples.score_line(score)


def test_a_plan_with_no_split_is_read_whatever_its_order_and_limit(store):
    """The coordinator's review of 91e8b33, item 5: with no split there is one row, so its order and its limit say nothing; a reading
    that differs only in them is the example's plan. With a split, the limit is part of the plan (the test below)."""
    window = {'period': 'week', 'anchor': '2026-09-07'}
    plain = examples._normal({'measure': 'peel_core_pct', 'window': window}, store, semantic.model())
    for extra in ({'limit': 5}, {'order': 'value_asc'}, {'limit': 1, 'order': 'dimension'}):
        assert examples._normal({'measure': 'peel_core_pct', 'window': window, **extra}, store, semantic.model()) == plain, extra
    split = {'measure': 'shear_pct', 'group_by': ['grade'], 'window': window}
    assert examples._normal({**split, 'limit': 5}, store, semantic.model()) != examples._normal(split, store, semantic.model())


def test_a_refusal_passes_only_when_it_is_the_customer_credits_rule(store, monkeypatch):
    """The coordinator's review of 91e8b33, item 5: the customer-credits example passes only on the rule's own refusal (the absent
    concept's), never on another refusal that happens to name shipments."""
    from investigator import agent
    example = BY_ID['customer-credits']
    rule = next(c for c in semantic.model().absent.values() if c['name'] == example['refusal']['absent'])

    def refusing(message):
        def route(*args, **kwargs):
            raise ValueError(message)
        return route
    monkeypatch.setattr(agent, 'route', refusing('The records hold no shipping: shipments are not recorded.'))
    assert examples.score_wording(example, 'customer credits this week', None, store)['outcome'] == 'miss'
    monkeypatch.setattr(agent, 'route', refusing(rule['refusal'] + ' Ask the dry-end rejects by reason.'))
    assert examples.score_wording(example, 'customer credits this week', None, store)['outcome'] == 'pass'


def _runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location('golden_run_reading', Path(__file__).resolve().parent.parent / 'evals' / 'run.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def test_the_reading_score_is_tracked_printed_and_shown_on_evals(tmp_path, capsys):
    """evals/run.py keeps the score in last_run.json beside the newest history line, prints the line and every wording it missed,
    and /evals shows the score of that run only: a last_run.json of another run shows none."""
    from investigator.evalboard import EvalBoard
    runner = _runner()
    score = {'read': 1, 'of': 3, 'owner_read': 0, 'owner_of': 1, 'examples': 1, 'stand_in': 1, 'page': {'day': '2026-09-14', 'shift': 2, 'scope': 'shift'},
             'rows': [{'example': 'top-maintenance-downtimes', 'text': 'What are our top maintenance downtimes?', 'owner': True, 'window': 'last_week',
                       'outcome': 'miss', 'read_as': 'downtime answer (the daily tools)'},
                      {'example': 'top-maintenance-downtimes', 'text': 'top 5 maintenance downtime reasons last week', 'owner': False, 'window': 'last_week',
                       'outcome': 'pass', 'read_as': 'semantic: …'},
                      {'example': 'top-maintenance-downtimes', 'text': 'top 5 maintenance downtime reasons this year', 'owner': False, 'window': 'ytd',
                       'outcome': 'stand-in', 'read_as': 'semantic: …'}]}
    line = {'ran_at': '2026-11-17T20:00Z', 'commit': 'abc1234', 'dataset': 'c9754a00c4e2'}
    tracked = runner.last_run([], line, score)
    assert tracked['reading'] == score and 'reading' not in runner.last_run([], line)
    runner.print_reading(score)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == examples.score_line(score) and '1 asked in a stand-in window, never counted as a pass' in out[0]
    assert out[1:] == ["  - top-maintenance-downtimes (the owner's words): 'What are our top maintenance downtimes?' read as downtime answer (the daily tools)",
                       '  stand-in window, reported apart (never a pass):',
                       "  - top-maintenance-downtimes (rewording, ytd): 'top 5 maintenance downtime reasons this year' read as semantic: …"]
    board = EvalBoard.__new__(EvalBoard)
    board.folder = tmp_path
    (tmp_path / 'last_run.json').write_text(json.dumps(tracked))
    shown = board._reading({'ran_at': line['ran_at'], 'commit': line['commit']})
    assert (shown['read'], shown['of'], shown['owner_read'], shown['stand_in']) == (1, 3, 0, 1)
    assert [m['text'] for m in shown['misses']] == [score['rows'][0]['text']] and [m['text'] for m in shown['stand_ins']] == [score['rows'][2]['text']]
    assert board._reading({'ran_at': '2026-11-16T20:00Z', 'commit': line['commit']}) is None   # another run's file shows nothing


def test_a_wording_passes_only_on_the_examples_own_plan(store):
    example = BY_ID['high-trim-by-grade']
    # the plan itself, said in the model's words, passes; the same split with every row listed is another plan
    assert examples.score_wording(example, 'top 5 grades by side trim % in the previous period', 'previous_period', store)['outcome'] == 'pass'
    assert examples.score_wording(example, 'side trim % by grade in the previous period', 'previous_period', store)['outcome'] == 'miss'
    assert examples.score_wording(example, 'top 5 grades by side trim % last week', 'previous_period', store)['outcome'] == 'miss'
