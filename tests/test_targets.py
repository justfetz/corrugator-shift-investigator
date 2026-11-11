"""The owner's targets and flags (issue #101): the bands and their source, every banded headline showing its band,
the dry-end denominator, each grade's speed baseline, the downtime review with its flags and recurrence, "yesterday",
and the two lenses on the cost model's levers. The value goldens recompute the review and the speed lever a second
way (evals/make_value_goldens.py); these tests hold the rules from outside the tools."""
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pytest

from investigator import cost_model, targets
from investigator.agent import Agent, Context, parse_request, question_scope, read_scope_text, route
from investigator.record_store import SPEED_MIN_RUN_MINUTES, default_store
from investigator.urls import canonical_url, page_url, redirect_for, resolve_url

ROOT = Path(__file__).resolve().parent.parent
DAY = '2026-09-14'
OWNER = 'owner, 2026-11-10'


@pytest.fixture(scope='module')
def store():
    return default_store()


def ask(store, question, day=DAY, scope='shift', period='day', shift=2):
    analysis = store.day_analysis(day)
    try:
        context = Context(day, shift, scope=scope, period=period)
        return Agent(analysis, store=store).ask(question, context)
    finally:
        analysis.close()


# ------------------------------------------------------------------ the bands

def test_every_band_is_the_owners_with_its_source_and_the_trim_target_is_three_percent():
    definition = json.loads((ROOT / 'src' / 'investigator' / 'targets.json').read_text())
    assert definition['source'] == OWNER and all(b['source'] == OWNER for b in definition['bands'])
    bands = {b['id']: b for b in definition['bands']}
    assert {k: (b['home_run'], b['target'], b['flag']) for k, b in bands.items()} == {
        'operator': (None, 2.5, 2.5), 'maintenance': (None, 2.5, 2.5), 'op_maint': (None, 5.0, 5.0),
        'trim': (2.75, 3.0, 3.25), 'shear': (0.5, 0.75, 1.0), 'dry_end': (0.75, 1.0, 1.25),
        'peel': (None, 0.5, None), 'core': (None, 0.1, None)}
    assert 'yield after trim and shear' in bands['dry_end']['denominator']
    rates = {r['id']: r for r in json.loads((ROOT / 'src' / 'investigator' / 'rates.json').read_text())['rates']}
    assert rates['trim_target_pct']['default'] == bands['trim']['target'] == 3.0 and rates['trim_target_pct']['source'] == OWNER
    assert targets.TRIM_FLAG_PCT == 3.25   # the per-setup flag the KPI tools count setups above


@pytest.mark.parametrize('measure, value, word', [
    ('trim', 2.75, 'home run'), ('trim', 2.76, 'on target'), ('trim', 3.0, 'on target'), ('trim', 3.1, 'over target'),
    ('trim', 3.25, 'over target'), ('trim', 3.26, 'flag'), ('maintenance', 2.5, 'on target'), ('maintenance', 2.51, 'flag'),
    ('maintenance', 12.5, 'flag'), ('op_maint', 5.0, 'on target'), ('op_maint', 5.01, 'flag'), ('dry_end', 0.7, 'home run'),
    ('dry_end', 1.2, 'over target'), ('dry_end', 1.3, 'flag'), ('peel', 0.6, 'over target'), ('shear', None, None)])
def test_a_band_reads_the_figure_against_home_run_target_and_flag(measure, value, word):
    assert targets.band(measure, value) == word
    if word:
        text = targets.band_text(measure, word)
        assert text.startswith({'home run': 'home run', 'on target': 'on target', 'over target': 'over the', 'flag': 'flag:'}[word])


def test_the_owners_sixty_minute_shift_is_a_flag_five_times_target():
    """The owner's example: 60 maintenance minutes on a 480-minute shift is 12.5%, five times the 2.5% target."""
    pct = 60 / 480 * 100
    assert pct == 12.5 and pct / targets.BANDS['maintenance']['target'] == 5
    assert targets.is_flag('maintenance', pct) and targets.threshold_minutes('maintenance') == 12


@pytest.mark.parametrize('question, scope, period, field', [
    ('Show downtime by reason', 'shift', 'day', 'op_maint_band_text'),
    ('Show maintenance downtime by reason', 'shift', 'day', 'maintenance_band_text'),
    ('Show dry-end quality waste', 'shift', 'day', 'dry_end_band_text'),
    ('Waste breakdown for the week', 'day', 'day', 'band_text'),
    ('Trend of trim over eight weeks', 'day', 'day', 'band_text'),
    ('What were the top downtime reasons this week?', 'day', 'day', 'op_maint_band_text'),
    ("What's trim costing us this month?", 'day', 'day', 'band_text'),
])
def test_every_banded_headline_shows_its_band_beside_its_figure(store, question, scope, period, field):
    result = ask(store, question, scope=scope, period=period)
    head = next(s for s in result['sections'] if '**' in s and not s.startswith('- '))
    texts = set()
    for step in result['trace']:
        if step['ok'] and step['tool'] != 'render_chart':
            data = step['data']
            texts |= {r.get(field) for r in data['rows']} | {data.get(field)}
    texts.discard(None)
    assert any(t in head for t in texts), (head, texts)
    assert any(w in head for w in ('home run', 'on target', 'over the', 'flag:')), head


# ------------------------------------------------------------------ dry-end over yield

def test_dry_end_waste_is_a_percent_of_the_yield_after_trim_and_shear(store):
    analysis = store.day_analysis(DAY)
    try:
        row = analysis.get_shift_kpis(2)['rows'][0]
    finally:
        analysis.close()
    yield_sqft = row['gross_sqft'] * (1 - (row['trim_pct'] + row['shear_pct']) / 100)
    assert row['yield_sqft'] == pytest.approx(yield_sqft)
    rejects = row['gross_sqft'] - row['estimated_good_sqft'] - row['gross_sqft'] * (row['trim_pct'] + row['shear_pct']) / 100
    assert row['dry_end_pct'] == pytest.approx(rejects / yield_sqft * 100)
    waste = store.get_waste_breakdown({'period': 'month', 'anchor': DAY})
    dry = next(r for r in waste['rows'] if r['category'] == 'dry_end')
    assert dry['pct_of_yield'] == pytest.approx(dry['sqft'] / waste['yield_sqft'] * 100, abs=1e-4)
    assert dry['pct_of_yield'] > dry['pct_of_gross']
    tile = next(r for r in store.get_period_kpis({'period': 'month', 'anchor': DAY})['rows'] if r['metric'] == 'dry_end_pct')
    assert tile['unit'] == '% of yield after trim and shear' and tile['current'] == pytest.approx(dry['pct_of_yield'], abs=1e-4)


# ------------------------------------------------------------------ the speed baseline

def test_each_grades_baseline_is_its_sustained_speed_over_the_trailing_year(store):
    base = store.grade_speed_baselines(DAY)
    first, last = store.trailing_year(DAY)
    assert (base['since'], base['through']) == (first.isoformat(), last.isoformat()) == (store.first_day, DAY)
    rows = store._fetch('SELECT grade, feet, seconds, stop_seconds FROM setups WHERE production_date BETWEEN ? AND ?', [first, last], limit=50000)
    feet, run = defaultdict(float), defaultdict(float)
    for r in rows:
        minutes = (r['seconds'] - r['stop_seconds']) / 60
        if minutes >= SPEED_MIN_RUN_MINUTES:
            feet[r['grade']] += r['feet']
            run[r['grade']] += minutes
    assert {g: v['baseline_fpm'] for g, v in base['grades'].items()} == {g: round(feet[g] / run[g], 1) for g in feet}
    analysis = store.day_analysis(DAY)
    try:
        runs = analysis.get_wet_end_performance(2)
    finally:
        analysis.close()
    assert runs['runs_under_baseline'] == sum(r['under_baseline'] for r in runs['rows']) > 0
    for r in runs['rows']:
        assert r['baseline_fpm'] == base['grades'][r['grade']]['baseline_fpm']
        assert r['running_fpm'] >= r['actual_fpm']   # stops out
        assert r['baseline_gap_ft'] == pytest.approx(max(0.0, r['baseline_fpm'] * r['running_minutes'] - r['lineal_ft']))


# ------------------------------------------------------------------ the downtime review

@pytest.mark.parametrize('window, shift', [({'period': 'day', 'anchor': '2026-09-18'}, None), ({'period': 'day', 'anchor': '2026-09-30'}, 1),
                                           ({'period': 'month', 'anchor': DAY}, None), ({'period': 'week', 'anchor': '2026-08-20'}, None)])
def test_the_review_ranks_every_reason_by_its_share_of_scheduled_minutes_and_puts_planned_maintenance_last(store, window, shift):
    data = store.get_downtime_review(window, shift)
    rows = data['rows']
    ranked = [r for r in rows if not r['scheduled']]
    planned = [r for r in rows if r['scheduled']]
    assert rows == ranked + planned and all(r['kind'] == 'PM' and r['rank'] is None for r in planned)
    assert [r['rank'] for r in ranked] == list(range(1, len(ranked) + 1))
    assert [r['pct_of_scheduled'] for r in ranked] == sorted((r['pct_of_scheduled'] for r in ranked), reverse=True)
    for key, kinds in (('maintenance', {'Maintenance'}), ('operator', {'Operator'}), ('op_maint', {'Maintenance', 'Operator'}), ('pm', {'PM'})):
        assert data[f'{key}_minutes'] == pytest.approx(sum(r['minutes'] for r in rows if r['kind'] in kinds), abs=0.05)
    for r in rows:
        assert r['pct_of_scheduled'] == pytest.approx(r['minutes'] / data['scheduled_minutes'] * 100, abs=1e-3)
        assert r['minutes_per_stop'] == pytest.approx(r['minutes'] / r['stops'], abs=0.01)
    assert data['op_maint_band'] == targets.band('op_maint', data['op_maint_pct'])


def test_the_review_keeps_the_previous_window_beside(store):
    """The owner (review of #111): downtime by reason over a named window goes to the review, and the review keeps the
    previous-window comparison the Pareto had: each reason's previous minutes and share, the totals, "versus" in the
    window line, and None (never zero) when that window is outside the records."""
    week = store.get_downtime_review({'period': 'week', 'anchor': DAY}, None)
    assert week['previous_window']['start'] == '2026-09-07' and week['coverage'] == 'complete'
    before = store.get_downtime_review({'period': 'week', 'anchor': '2026-09-07'}, None)   # the previous week, asked itself
    then = {(r['kind'], r['reason']): r for r in before['rows']}
    for r in week['rows']:
        prior = then.get((r['kind'], r['reason']))
        assert (r['previous_minutes'], r['previous_stops'], r['previous_pct_of_scheduled']) == \
            ((prior['minutes'], prior['stops'], prior['pct_of_scheduled']) if prior else (0.0, 0, 0.0)), r['category']
    for key in ('maintenance', 'operator', 'op_maint', 'pm'):
        assert (week[f'previous_{key}_minutes'], week[f'previous_{key}_pct']) == (before[f'{key}_minutes'], before[f'{key}_pct'])
    assert week['previous_scheduled_minutes'] == before['scheduled_minutes']
    first = store.get_downtime_review({'period': 'month', 'anchor': '2026-08-03'}, 2)
    assert first['previous_window']['coverage'] == 'unavailable' and first['window']['coverage'] == 'complete'
    assert all(r['previous_minutes'] is None and r['previous_pct_of_scheduled'] is None for r in first['rows'])
    assert first['previous_op_maint_pct'] is None and first['previous_scheduled_minutes'] is None
    answer = ask(store, 'Show downtime by reason', scope='day', period='week')
    assert any(s.startswith('Week 2026-09-14 to 2026-09-18 (Mon-Fri), all shifts, versus Week 2026-09-07') for s in answer['sections'])
    assert 'The previous window' in answer['sections']
    assert canonical_url(*route('Show downtime by reason', Context(DAY, 2, scope='day', period='week'), store.production_days)) == \
        redirect_for('/corr/1/week/2026-09-14?measure=downtime&compare=prev', store) == '/corr/1/week/2026-09-14?measure=review'


def test_a_flag_fires_exactly_when_a_shift_is_over_its_band_and_names_who_to_ask(store):
    stops = store._fetch("SELECT production_date, shift, kind, sum(seconds) / 60 AS minutes FROM stops "
                         "WHERE kind IN ('Maintenance', 'Operator') GROUP BY ALL", [])
    over = {(r['production_date'].isoformat(), r['shift'], r['kind']) for r in stops if r['minutes'] / 480 * 100 > 2.5}
    seen = set()
    for day in store.production_days:
        flags, count = store.downtime_flags(day, day)
        assert count == len(flags)   # every flag is returned, each with its recurrence (review of #111)
        for f in flags:
            seen.add((f['production_day'], f['shift'], f['kind']))
            assert f['band'] == 'flag' and f['pct_of_shift'] > 2.5 and f['supervisor'].startswith(f'the shift {f["shift"]} supervisor ({f["crew"]}')
            assert 0 <= f['times_over_band'] <= f['shifts_counted'] and 0 <= f['weeks_in_top_five'] <= f['weeks_counted']
            assert f['recurrence_through'] == day and f['recurrence_scope'] == 'all shifts'
    assert seen == over and ('2026-09-18', 2, 'Maintenance') in seen


def test_every_flag_of_a_month_carries_its_recurrence_and_is_listed(store):
    """Review of #111: recurrence for every flag, not only the first five. A month of all shifts fires many; each one
    carries its recurrence, the same as asking for its own day, and the answer lists every one."""
    month = store.get_downtime_review({'period': 'month', 'anchor': DAY}, None)
    assert month['flag_count'] == len(month['flags']) > 5
    keys = ('recurrence_since', 'shifts_counted', 'times_over_band', 'weeks_counted', 'weeks_in_top_five')
    for f in month['flags']:
        alone, _ = store.downtime_flags(f['production_day'], f['production_day'])
        same = next(x for x in alone if (x['shift'], x['kind']) == (f['shift'], f['kind']))
        assert [f[k] for k in keys] == [same[k] for k in keys]
    sections = ask(store, 'Show downtime by reason', scope='day', period='month')['sections']
    flagged = [s for s in sections if s.startswith('- Shift ') and ' of the shift in ' in s]
    assert len(flagged) == month['flag_count'] and 'More flags' in sections
    assert f'{month["flag_count"]} flags fired in this window, the largest first, each with its recurrence.' in sections


def test_the_prompt_reads_the_bands_from_targets_json(monkeypatch):
    """Review of #111: the analyst's process context states the owner's bands as targets.json holds them; nothing typed."""
    from investigator import agent
    assert targets.prompt_text() in agent.PROCESS_CONTEXT
    assert 'trim 2.75/3/3.25% of gross' in targets.prompt_text() and 'separately 2.5% of scheduled minutes (5% together)' in targets.prompt_text()
    monkeypatch.setitem(targets.BANDS['trim'], 'flag', 3.5)
    monkeypatch.setitem(targets.BANDS['dry_end'], 'target', 0.9)
    assert 'trim 2.75/3/3.5% of gross' in targets.prompt_text() and 'dry-end waste 0.75/0.9/1.25% of the yield' in targets.prompt_text()


def test_a_shift_scoped_flag_counts_that_shifts_own_year(store):
    plant, _ = store.downtime_flags('2026-09-18', '2026-09-18')
    own, _ = store.downtime_flags('2026-09-18', '2026-09-18', 2)
    mine = next(f for f in plant if f['shift'] == 2 and f['kind'] == 'Maintenance')
    assert own[0]['reason'] == mine['reason'] == 'Power outage'
    assert own[0]['shifts_counted'] * 3 == mine['shifts_counted'] and own[0]['recurrence_scope'] == 'shift 2'
    analysis = store.day_analysis('2026-09-18')
    try:
        daily = analysis.get_downtime_breakdown(2, kind='Maintenance')
    finally:
        analysis.close()
    assert daily['flags'] == own and daily['rows'][0]['category'] == 'Power outage'


# ------------------------------------------------------------------ "yesterday" and the all-shift default

def test_yesterday_moves_to_the_previous_production_day_and_answers_the_review_there(store):
    """"Yesterday" is the production day before the selected one, and the question moves the controls to it (issue #105);
    the owner's default downtime answer for that day, all shifts, is the review."""
    for period in ('day', 'month'):
        request, context = route('What were the top downtime reasons yesterday?', Context('2026-09-29', 2, scope='day', period=period),
                                 store.production_days)
        assert context.production_day == '2026-09-28' and request['plan'] == [
            {'tool': 'get_downtime_review', 'arguments': {'window': {'period': 'day', 'anchor': '2026-09-28'}, 'shift': None}}]
        assert canonical_url(request, context) == '/corr/1/day/2026-09-28?measure=review'
    request, context = route('What went wrong yesterday?', Context('2026-09-29', 2, scope='day'), store.production_days)
    assert request['plan'][0]['arguments'] == {'window': {'period': 'day', 'anchor': '2026-09-28'}, 'shift': None, 'lens': 'supervisor'}
    with pytest.raises(ValueError, match='ask about yesterday on its own'):
        route('Compare with yesterday.', Context('2026-09-29', 2), store.production_days)


def test_a_production_days_downtime_over_all_shifts_is_the_review(store):
    day = Context('2026-09-18', 2, scope='day')
    assert canonical_url(parse_request('Show downtime by reason', day), day) == '/corr/1/day/2026-09-18?measure=review'
    shift = Context('2026-09-18', 2, scope='shift')
    assert canonical_url(parse_request('Show downtime by reason', shift), shift) == '/corr/1/day/2026-09-18/shift/2?measure=downtime'
    # the address keeps main's meaning, each shift's priced downtime (a money question's answer, review of #111); the
    # review is measure=review
    assert redirect_for('/corr/1/day/2026-09-18?measure=downtime', store) is None
    assert [s['tool'] for s in resolve_url('/corr/1/day/2026-09-18?measure=downtime', store)['request'].get('plan') or []] != ['get_downtime_review']
    assert redirect_for('/corr/1/day/2026-09-18?measure=downtime&kind=maintenance', store) is None   # a filter keeps the per-shift plan
    for url in ('/corr/1/day/2026-09-18?measure=review', '/corr/1/week/2026-09-18/shift/2?measure=review'):
        assert page_url(resolve_url(url, store)) == url


def test_downtime_with_no_scope_is_the_previous_production_day_all_shifts(store):
    """The owner's default (review of #111): a downtime question that names no shift and no window answers the previous
    production day, all shifts, whatever the shift control says; shift detail only when a shift is named (or a flag
    pulls one in). A filter, a grouping or another Period control is asked of the controls as before. An address is
    explicit, so the default moves questions only, never a URL."""
    days = store.production_days

    def address(question, day='2026-09-14', scope='shift', period='day'):
        request, context = route(question, Context(day, 2, scope=scope, period=period), days)
        return canonical_url(request, context)
    for question in ('Show downtime by reason', 'How much downtime?', 'What were the top downtime reasons?',
                     'What were the top downtime reasons yesterday?'):
        for scope in ('shift', 'day'):
            assert address(question, scope=scope) == '/corr/1/day/2026-09-11?measure=review', (question, scope)
    assert address('Show downtime by reason', day='2026-09-21') == '/corr/1/day/2026-09-18?measure=review'
    assert address('Show downtime by reason', day=days[0]) == f'/corr/1/day/{days[0]}?measure=review'   # no earlier day: stays
    first = question_scope('Show downtime by reason', Context(days[0], 2), days)   # and the analyst is told the truth about it
    assert "the previous production day" not in read_scope_text(first) and 'the first in the records' in read_scope_text(first)
    for question, url in (('Show downtime by reason for this shift', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          # review of #111: a shift named in words, the selected day named, a money question, another grouping
                          ('Show downtime for shift two', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by reason for the previous shift', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by reason on Monday', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by reason for this day', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by reason for the selected day', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('What did downtime cost?', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime in dollars', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by kind', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by reason for shift 2', '/corr/1/day/2026-09-14/shift/2?measure=downtime'),
                          ('Show downtime by reason for shift 1', '/corr/1/day/2026-09-14/shift/1?measure=downtime'),
                          ('Show maintenance downtime by reason', '/corr/1/day/2026-09-14/shift/2?measure=downtime&kind=maintenance'),
                          ('Which equipment caused the most downtime?', '/corr/1/day/2026-09-14/shift/2?measure=downtime&by=place'),
                          ('Show downtime by reason this week', '/corr/1/day/2026-09-14/shift/2?measure=review&window=week'),
                          ('How much downtime last week?', '/corr/1/week/2026-09-07/shift/2?measure=review'),
                          ('Why was this shift slow?', '/corr/1/day/2026-09-14/shift/2?measure=overview')):
        assert address(question) == url, question
    assert address('Show downtime by reason', period='week') == '/corr/1/week/2026-09-14/shift/2?measure=review'
    assert address('What were the top downtime reasons yesterday?', period='week') == '/corr/1/day/2026-09-11?measure=review'   # yesterday is Day
    assert address('What were the top downtime reasons for all shifts?') == '/corr/1/day/2026-09-11?measure=review'   # "all shifts" names no one shift
    assert redirect_for('/corr/1/day/2026-09-14/shift/2?measure=downtime', store) is None
    # the analyst is told which day the default chose, and why
    scoped = question_scope('Show downtime by reason', Context('2026-09-14', 2), days)
    assert (scoped.production_day, scoped.scope, scoped.period) == ('2026-09-11', 'day', 'day')
    assert "the owner's default applies: the previous production day, all shifts, 2026-09-11" in read_scope_text(scoped)


# ------------------------------------------------------------------ the two lenses

@pytest.mark.parametrize('lens', cost_model.LENSES)
def test_every_lever_is_marked_controllable_or_schedule_driven(store, lens):
    data = cost_model.opportunities(store, {'period': 'month', 'anchor': DAY}, None, cost_model.effective(), lens)
    marks = {r['lever']: r['control'] for r in data['rows']}
    allowed = {'controllable', 'schedule-driven'} | ({'unattributed'} if lens == 'ownership' else set())
    assert set(marks.values()) <= allowed and all(r['control_note'] for r in data['rows'])
    trim = next(r for r in data['rows'] if r['lever'].startswith('Side trim'))
    assert trim['control'] == 'schedule-driven'
    if lens != 'ownership':
        assert marks['Downtime: Planned maintenance (scheduled time)'] == 'schedule-driven'
        assert marks["Speed under each grade's trailing-year speed"] == 'controllable'
        assert all(v == 'controllable' for k, v in marks.items() if k.startswith(('Shear', 'Dry-end')) or
                   (k.startswith('Downtime') and 'Planned maintenance' not in k))
    assert data['controllable_levers'] == sum(v == 'controllable' for v in marks.values())


def test_the_supervisor_lens_ranks_the_controllable_losses_and_the_ownership_lens_starts_with_trim(store):
    window, rates = {'period': 'month', 'anchor': DAY}, cost_model.effective()
    every = cost_model.opportunities(store, window, None, rates)
    supervisor = cost_model.opportunities(store, window, None, rates, 'supervisor')
    ownership = cost_model.opportunities(store, window, None, rates, 'ownership')
    assert sorted(r['lever'] for r in every['rows']) == sorted(r['lever'] for r in supervisor['rows'])   # one set of numbers
    assert {r['lever']: r['usd_per_year'] for r in every['rows']} == {r['lever']: r['usd_per_year'] for r in supervisor['rows']}
    ranked = [r for r in supervisor['rows'] if r['rank'] is not None]
    assert all(r['control'] == 'controllable' for r in ranked) and supervisor['rows'][:len(ranked)] == ranked
    assert [r['usd_per_year'] for r in ranked] == sorted((r['usd_per_year'] for r in ranked), reverse=True)
    assert supervisor['controllable_usd_per_year'] == pytest.approx(sum(r['usd_per_year'] for r in ranked), abs=1)
    first = ownership['rows'][0]
    assert first['lever'] == cost_model.TOTAL_TRIM and first['rank'] == 1 and first['band'] == targets.band('trim', first['pct_of_gross'])
    waste = store.get_waste_breakdown(window)
    trim = next(r for r in waste['rows'] if r['category'] == 'trim')
    assert first['quantity'] == trim['sqft'] and first['usd'] == cost_model.price('trim', trim['sqft'], rates, waste['gross_sqft'], 3 * 480 * 22)['usd']
    shear = next(r for r in ownership['rows'] if r['lever'].startswith('Shear'))
    assert shear['usd_per_year'] == next(r for r in every['rows'] if r['lever'].startswith('Shear'))['usd_per_year']


@pytest.mark.parametrize('question, scope, lens, window', [
    ('What does trim cost?', 'shift', 'ownership', 'day'), ("What's trim costing us this month?", 'day', 'ownership', 'month'),
    ("Where's my biggest opportunity?", 'shift', 'supervisor', 'day'), ('What is our biggest opportunity this week?', 'day', 'supervisor', 'week'),
    ('What went wrong this week?', 'day', 'supervisor', 'week'), ('Where is the money this month?', 'day', None, 'month')])
def test_the_question_picks_the_lens(store, question, scope, lens, window):
    context = Context(DAY, 2, scope=scope)
    request = parse_request(question, context)
    [step] = request['plan']
    assert step['tool'] == 'get_opportunities' and step['arguments'].get('lens') == lens and step['arguments']['window']['period'] == window
    url = canonical_url(request, context)
    assert (f'lens={lens}' in url) == bool(lens) and resolve_url(url, store)['request']['plan'] == request['plan']


def test_neighbouring_money_questions_keep_their_answers(store):
    context = Context(DAY, 2, scope='shift')
    assert parse_request('What did waste cost this shift?', context)['intent'] == 'dollars'
    assert parse_request('Where is the speed opportunity this month?', context)['intent'] == 'period_speed_opportunity'
    assert parse_request('What does warp cost?', context)['intent'] == 'quality'
    with pytest.raises(ValueError, match='lens is supervisor'):
        resolve_url('/corr/1/day/2026-09-14?measure=money&lens=owner', store)
    with pytest.raises(ValueError, match='lens= does not apply'):
        resolve_url('/corr/1/day/2026-09-14?measure=waste&lens=ownership', store)


def test_recurrence_counts_a_reason_that_crossed_its_band_on_its_own(store):
    """Every shift of the trailing year on which the reason alone was over its band counts; here the flag's own shift is not one
    of them (the jam alone was 1.94% of it) and 2026-08-25 shift 3 is. The value golden value-review-day-0928 pins it too."""
    flags, _ = store.downtime_flags('2026-09-28', '2026-09-28')
    jam = next(f for f in flags if f['reason'] == 'Knife / slitter jam')
    assert jam['times_over_band'] >= 1 and jam['shift'] == 3
    over = store._fetch("SELECT production_date, shift, sum(seconds) / 60 AS minutes FROM stops WHERE kind = 'Maintenance' "
                        "AND reason = 'Knife / slitter jam' AND production_date BETWEEN ? AND ? GROUP BY ALL HAVING sum(seconds) / 60 > 12",
                        [jam['recurrence_since'], '2026-09-28'])
    assert jam['times_over_band'] == len(over) == 1 and over[0]['production_date'].isoformat() == '2026-08-25' and over[0]['shift'] == 3


def test_a_lens_declines_a_day_whose_own_view_excludes_a_record_in_its_scope(store):
    """The data-quality demo's bad trim record exists on the day's view only; the lenses read the store, so on that day
    they decline for the shift that holds it (shift 2) and answer for the others (Codex on PR #111)."""
    for shift, status in ((2, 'unavailable'), (1, 'complete')):
        analysis = store.day_analysis(DAY, corrupt=True)
        try:
            result = Agent(analysis, store=store).ask('What does trim cost?', Context(DAY, shift, scope='shift'))
        finally:
            analysis.close()
        assert result['status'] == status, shift
    assert any('holds an excluded record on shift 2' in s for s in ask(store, 'What does trim cost?', shift=2)['sections']) is False
