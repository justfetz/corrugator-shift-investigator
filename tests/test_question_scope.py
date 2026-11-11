"""Issue #105: the question's own shift and window win over the controls, and a misspelt measure word
reads as the measure. "How much donwtime on 1st shift last week?" asked with shift 2 and Day on Mon 14 Sep
is about shift 1 in the week of 7-11 Sep: the router moves the scope rather than refusing it, the canonical
address names that scope, and the analyst plans inside it."""
import json
from pathlib import Path

import pytest

from investigator.agent import (Context, correct_typos, parse_request, question_scope, read_scope_text,
                                relative_window, route)
from investigator.record_store import default_store
from investigator.urls import canonical_url

DAY = '2026-09-14'   # a Monday: yesterday is Friday 11 Sep, across the weekend
DAYS = default_store().production_days


def rail(shift=2, scope='shift', period='day', day=DAY):
    return Context(day, shift, scope=scope, period=period)


def address(question, context):
    request, scoped = route(question, context, DAYS)
    return canonical_url(request, scoped)


# ---------------------------------------------------------------- typo tolerance

@pytest.mark.parametrize('typed, read', [
    ('donwtime', 'downtime'), ('dowtime', 'downtime'), ('downtiem', 'downtime'), ('wast', 'waste'), ('spead', 'speed'),
    ('shaer', 'shear'), ('tirm', 'trim'), ('wapr', 'warp'), ('bnod', 'bond'), ('stpos', 'stops'), ('setpus', 'setups'),
    ('rejcts', 'rejects'), ('quailty', 'quality'), ('mositure', 'moisture')])
def test_a_misspelt_measure_word_reads_as_the_measure(typed, read):
    assert correct_typos(f'how much {typed} this week?') == f'how much {read} this week?'


@pytest.mark.parametrize('word', [
    'steps', 'step', 'shop', 'spend', 'seed', 'bold', 'bound', 'band', 'warm', 'wrap', 'trip', 'quantity', 'reflects',   # real words one edit away
    'trims', 'wasted', 'speeds', 'operators', 'stop', 'reject', 'setup',                                                # a measure word's own forms
    'flurbtime', 'glorp', 'zxqv', 'was', 'the'])                                                                        # nonsense and short words
def test_real_words_and_nonsense_are_left_alone(word):
    assert correct_typos(f'show {word} by reason') == f'show {word} by reason'


def test_no_golden_question_is_rewritten_by_the_typo_rule():
    """The typo rule may only ever fix a misspelling: every question the golden set asks reads as it did."""
    cases = json.loads((Path(__file__).resolve().parents[1] / 'evals' / 'golden.json').read_text())['cases']
    questions = {q for c in cases for q in (c.get('turns') or [c.get('question')]) if q}
    typos = {'How much donwtime on 1st shift last week?', 'How much donwtime?', 'Show dowtime by reason this week',
             'How much wast?', 'What was our spead?'}
    assert typos <= questions
    assert [q for q in questions - typos if correct_typos(q.lower()) != q.lower()] == []


def test_a_nonsense_measure_is_still_refused():
    with pytest.raises(ValueError, match='Offline mode supports'):
        parse_request('How much flurbtime?', rail())


# ---------------------------------------------------------------- the question's own shift

def test_one_named_shift_becomes_the_selected_shift():
    scoped = question_scope('Show shift 1', rail(), DAYS)
    assert (scoped.production_day, scoped.shift, scoped.scope, scoped.period, scoped.read) == (DAY, 1, 'shift', 'day', {'shift': 1})
    assert address('Show shift 1', rail()) == '/corr/1/day/2026-09-14/shift/1?measure=overview'
    assert address('How did shift 2 go compared with last week?', rail(shift=1)) == '/corr/1/day/2026-09-14/shift/2?measure=kpis&window=week'


@pytest.mark.parametrize('question', ['Show shift 2', 'Why was this shift slow?', 'Compare shift 1 with shift 3', 'Compare that with first shift'])
def test_the_selected_shift_comparisons_and_the_all_shift_scope_keep_the_controls(question):
    current = rail()
    assert question_scope(question, current, DAYS) is current            # the selected shift, or a comparison: no move
    day = rail(scope='day')
    assert question_scope('downtime on 1st shift', day, DAYS) is day       # all shifts: a named shift narrows with shifts=, as before
    if 'shift 1 with shift 3' in question or 'first shift' in question:
        with pytest.raises(ValueError, match='Selected shift only'):    # a comparison of other shifts still asks for the day scope
            route(question, current, DAYS)


# ---------------------------------------------------------------- relative windows

@pytest.mark.parametrize('question, day, period', [
    ('how much downtime yesterday', '2026-09-11', 'day'),       # the production day before Monday is Friday
    ('downtime last week', '2026-09-07', 'week'),               # the first production day of the week before
    ('downtime previous week', '2026-09-07', 'week'),
    ('downtime prior week', '2026-09-07', 'week'),
    ('downtime last month', '2026-08-03', 'month'),             # August, the month before September
    ('downtime last period', '2026-08-03', 'fiscal'),           # P1 (3-30 Aug) is the 4-4-5 period before P2 (31 Aug-27 Sep)
])
def test_a_relative_window_moves_the_day_and_the_period_control(question, day, period):
    scoped = question_scope(question, rail(), DAYS)
    assert (scoped.production_day, scoped.period, scoped.shift, scoped.scope) == (day, period, 2, 'shift')
    assert scoped.read['day'] == day and scoped.read['period'] == period


def test_the_issue_questions_resolve_to_the_scope_they_name():
    for question in ('How much donwtime on 1st shift last week?', 'downtime 1st shift last week', 'first shift downtime last week'):
        assert address(question, rail()) == '/corr/1/week/2026-09-07/shift/1?measure=pareto', question
    assert address('how much downtime did third shift have yesterday', rail(shift=1, period='week')) == '/corr/1/day/2026-09-11/shift/3?measure=downtime'
    assert address('How much downtime on 1st shift last week?', rail(scope='day')) == '/corr/1/week/2026-09-07?measure=pareto&shifts=1'
    assert address('What grades ran slowest last week?', rail(scope='day')) == '/corr/1/week/2026-09-07?measure=fpm&by=grade'
    assert address('downtime last week', rail(period='week', day='2026-09-16')) == '/corr/1/week/2026-09-07/shift/2?measure=pareto'


@pytest.mark.parametrize('question', [
    'what is our speed vs last week', 'How did shift 2 go compared with last week?', 'Compare last week', 'Compare with yesterday.',
    'downtime this week compared with last week', 'downtime last week and this week', 'downtime last week in September',
    'downtime yesterday vs today', 'downtime last week and last month'])
def test_a_comparison_base_or_another_window_keeps_the_controls(question):
    assert relative_window(question.lower()) is None
    assert question_scope(question, rail(), DAYS).read == {}


def test_a_window_before_the_records_is_refused_with_the_calendar_reason():
    for question, day in (('downtime last week', '2026-08-05'), ('downtime yesterday', '2026-08-03'), ('downtime last month', '2026-08-20')):
        with pytest.raises(ValueError, match='before the records, which hold production days from 2026-08-03 to 2026-09-30'):
            question_scope(question, rail(day=day), DAYS)


def test_follow_up_history_goes_with_the_question_on_the_same_day_only():
    current = rail()
    current.history = [{'question': 'Why was this shift slow?', 'shifts': [2], 'intent': 'overview'}]
    same_day = question_scope('Show shift 1', current, DAYS)
    assert same_day.history == current.history and same_day.history is not current.history
    assert question_scope('downtime last week', current, DAYS).history == []


def test_the_router_never_reads_last_week_as_the_selected_week():
    """Only route() can move the scope; parse_request alone refuses the words rather than answer this week for them."""
    with pytest.raises(ValueError, match='names a window off the selected production day'):
        parse_request('What grades ran slowest last week?', rail(scope='day'))
    with pytest.raises(ValueError, match='day control'):   # a comparison with yesterday is still refused, as before
        parse_request('Compare with yesterday.', rail(scope='day'))


def test_downtime_over_a_named_window_is_the_pareto():
    for question, window in (('how much downtime this week', {'period': 'week', 'anchor': DAY}),
                             ('how much downtime in August', {'period': 'month', 'anchor': '2026-08-01'})):
        [step] = parse_request(question, rail())['plan']
        assert step == {'tool': 'get_downtime_pareto', 'arguments': {'window': window, 'group_by': 'reason', 'measure': 'minutes', 'shift': 2}}
    assert [s['tool'] for s in parse_request('why were we slow on stops this week', rail())['plan']] == \
        ['get_period_kpis', 'get_downtime_pareto', 'get_waste_breakdown']   # a diagnosis stays the overview


def test_the_planner_is_told_the_scope_only_when_the_question_moved_it():
    assert read_scope_text(rail()) == '' and read_scope_text(question_scope('Why was this shift slow?', rail(), DAYS)) == ''
    note = read_scope_text(question_scope('How much donwtime on 1st shift last week?', rail(), DAYS))
    assert 'it names shift 1' in note and '"last week" is the Mon-Fri production week containing 2026-09-07' in note
    assert 'do not move the window again' in note
