"""Exact over summary (issue #113): a question that asks for one quantity opens with that quantity, in the unit asked, or
is refused with what can be counted; the KPI summary (`measure=kpis`) answers "how did we do", never a count or a unit.

The catch-all audit lives here as AUDIT: the issue's seven rows, the owner's interview phrasings (#24) and the count and
unit phrasings that landed on the KPI summary on main. Each must land on a measure that answers it (`evals/run.py`
states the guard and the headline-unit rule on its own; this file runs them over the audit as well as the goldens)."""
import json
import re
import sys
from pathlib import Path

import pytest

from investigator import agent  # noqa: E402
from investigator.agent import (FUNCTION_WORDS, GRADE_QUANTITIES, LEFTOVER_FAMILIES, QUANTITY_HELP, QUANTITY_METRICS, RANKED_TEXT, SETUP_SPEED, WASTE_KEYS, Agent,
                                Context, catch_all_guard, parse_request, question_scope, quantity_request, route, slug)
from investigator.fixture import GRADES
from investigator.records import STOP_PLACES, STOP_REASONS
from investigator.record_store import default_store
from investigator.urls import canonical_url, page_url, redirect_for, resolve_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'evals'))
from run import FUNCTION_WORDS as RUN_FUNCTION_WORDS, asked_unit_failures, catch_all_failures  # noqa: E402

DAY = '2026-09-14'
GOLDEN = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']

# The audit (issue #113, owner's comment of 2026-11-11), asked with shift 2 and Day on Mon 14 Sep. Before this change the
# first group landed on measure=kpis; the rest opened with another unit (MSF for feet, a stop count for minutes, a
# percent for minutes or stops) or were refused with the generic help. The PR's before/after table lists each one.
AUDIT = [
    'how many setups did we run last week', 'how many paper changes last week', 'how many order changes this week',
    'what was our lineal per setup last week', 'how many minutes of maintenance downtime yesterday', 'how many stops did we have yesterday',
    'how many setups yesterday', 'How many missed splices this week?', 'how many step-ups did we have this week',
    'how many rolls did we use this week', 'how many boards did we lose at the knife this week', 'how many orders did we run this week',
    'how many wet ends this week', 'how many paper changes this month', 'number of setups this month', 'count of stops this week',
    'how many stops this week', 'how many feet did we run last week', 'what was our feet per paper change this week',
    'minutes per stop this week', 'average minutes per stop last month', 'how many minutes did we lose to missed splices this week',
    'how many stops at the stacker this week', 'how many changeovers this week', 'how many order changes last week',
    'how many setups this period', 'how many paper changes on shift 1 last week', 'how many setups did we run on shift 3 last month',
    'How many splices did we miss yesterday?', 'how many rolls did we run yesterday', 'how many sheets did we make this week',
    'how many sheets did we run yesterday', 'how many boards did we lose at the stacker yesterday', 'how many orders did we run yesterday',
    'how many minutes of downtime last week', 'how many minutes of operator downtime this week', 'how many hours of downtime this month',
    'how much downtime in minutes yesterday', 'total downtime minutes this week', 'how many lineal feet did we run this week',
    'how many minutes of steam / moisture this month', ('How did shift 1 go compared with last week on lineal per paper change?', 1),
    'How did we do on setups this month?', 'Compare stops this week to last week', 'How many tons did we ship?',
    # the review of this PR: a reason, a place, a grade, dollars and a setup speed named, each as the audit's owner listed them
    'How did we do on glue this week?', 'how did we do on knife jams this month', 'how did we do on the stacker this week',
    'how did we do on cost this week', 'how fast did we change paper this week', 'how quick were the changeovers this week',
    'how many setups did we run on 32-C last week', 'how many stops on 55-C this week', 'how many minutes of downtime on 26-C last week',
    ('how many setups did each shift run last week', 2, 'day'), 'how many setups by shift last week', 'how many stops per shift this week',
    'how many setups on the single facer this week', 'compare setups this week to last week', 'paper changes last week',
    # the question log's countable words (issue #125's catch-all flag): each one lands on a quantity or is refused
    'how many boxes did we make this week', 'how many grade changes this week', 'how many flute changes this week',
    'how many chops this week', 'how many mins of downtime this week', 'how many hrs of downtime this week', "what's the stop count this week",
    # the re-review: a grade the records do not hold, two grades, double wall, and a count of something unknown
    'how many setups on 44-C last week', 'how many setups did we run on 32-C and 26-C last week', 'how many setups of double wall this week',
    'how many widgets this week',
    # the Codex review of PR 143: order change stops and their minutes are a stop reason; a kind's stop count
    'how many order change stops this week', 'how many minutes of order change stops this week',
    'how many minutes did order changes take this week', 'how many maintenance stops did we have yesterday',
    # the coordinator's review of PR 143: qualified sheets, ratios in another unit, durations, the overview, area and percent
    'how many reject sheets yesterday', 'how many bad sheets this week', 'how many sheets of warp this week', 'how many sheets per order this week',
    'what was our minutes per setup last week', 'what was our msf per setup this week', 'how many minutes did setups take this week',
    'what was our average setup time this week', 'how long did changeovers take this week', 'how long did knife jams take this week',
    'how long were we down on shift 3', 'how many widgets on shift 2', 'how much scrap on shift 2', 'how many square feet of trim this week',
    'how many sq ft of warp this week', 'how much waste in msf this week', 'what percent was shear this week', 'how much trim in msf this week',
    'what percentage was dry-end rejects this week', 'how many setups did this shift run yesterday',
    # the coordinator's re-review of PR 143: a ratio the records do not keep, MSF per day, slow asked as a duration, the shift
    # comparison, hours, and the default scope (no shift named: every shift; no window named: the selected day)
    'Feet per stop this week', 'Splices per setup this week', 'Setups per day this week', 'MSF per hour this week',
    'What was our MSF per day this week?', 'What was our volume per day this week?', 'How long were we slow on shift 2?',
    'How many minutes were we slow this week?', ('How much scrap on shift 1 versus shift 2?', 2, 'day'), 'What percent downtime this week?',
    'How many msf did we run last week?', 'Number of stops by reason this week', 'How much downtime in hours last week?',
    'How many stops did we have?', 'How often did we stop this week?', 'How many minutes of downtime per day this week?',
    # the reviewer's check of that round: "per" meaning "by", the volume and speed of a day, hours of a shift, slow named of
    # something else, and a speed asked in feet per minute
    'Downtime per reason this week', 'Downtime per equipment this week', 'Waste per reason this week', 'Rollstock per width this month',
    'How much did we make per day this week?', 'How many MSF did we make per day this week?', 'Throughput per day this week',
    'Average speed per day this week', 'How many feet per minute did we run this week?',
    'What was our fastest setup time?', 'How many feet did we run per day this week?', 'How long did we run slow this week?',
    # round 4 of the coordinator's review: a ranked shift, a numerator the records do not keep per setup, per minute, an average or
    # longest stop, a grouping or a shift comparison, a breakdown in percent, the stops profile's scope and the downtime of each day
    ('How many minutes of downtime did the slowest shift have this week?', 1), ('How many minutes of downtime did the best shift have this week?', 1),
    ('How many stops did the fastest shift have this week?', 1), ('How many minutes of downtime did the slowest shift have this week?', 2, 'day'),
    'Cost per setup this week', 'Glue per setup this week', 'Starch per setup this week', 'Yield per setup this week',
    'Efficiency per setup this week', 'Loss per setup this week', 'Sheets per minute this week', 'Stops per minute this week',
    'Setups per minute this week', 'Average stop length this week', 'Average stop duration this week', 'Typical stop this week',
    'What was the average stop this week?', 'How long is our average stop?', 'Average setup length this week',
    'How long was the longest stop this week?', 'How many minutes of downtime at each place this week?',
    'How many minutes of downtime for each reason this week?', ('Compare shifts on downtime minutes', 2, 'day'),
    'Square feet per minute this week', 'Waste breakdown in percent this week', 'Stops per day this week', 'How many stops per day this week?',
    'Downtime per day this week', 'How many minutes of downtime per day this week?',
]
# What a refusal of an audited question says: what can be asked instead.
REFUSALS = (QUANTITY_HELP, 'Offline mode supports', SETUP_SPEED, 'one shift or all shifts together', 'Selected shift only', RANKED_TEXT)


def names_three_that_answer(refusal):
    """True when a refusal names at most three questions, never the generic help, and each of them answers (round 6: the
    allow-list's refusals and the day words' too)."""
    asks = re.findall(r"'([^']* this week)'", refusal)
    return 1 <= len(asks) <= 3 and QUANTITY_HELP not in refusal and all(landing(ask)[0] for ask in asks)


def landing(question, scope='shift', shift=2, period='day', day=DAY):
    """(canonical URL, request, context) of a question, or (None, the refusal, None)."""
    store = default_store()
    context = Context(day, shift, scope=scope, period=period)
    try:
        request, asked = route(question, context, store.production_days)
    except ValueError as exc:
        return None, str(exc), None
    return canonical_url(request, asked), request, asked


def test_the_guard_flags_a_quantity_question_on_the_kpi_summary():
    """The rule in evals/run.py, held to the URLs main resolved the issue's rows to (its table), and to the questions the
    KPI summary is for."""
    for question, url, metric in [('how many setups did we run last week', '/corr/1/week/2026-09-07/shift/2?measure=kpis', 'setups'),
                                  ('how many order changes this week', '/corr/1/day/2026-09-14/shift/2?measure=kpis&window=week', 'order_changes'),
                                  ('what was our lineal per setup last week', '/corr/1/week/2026-09-07/shift/2?measure=kpis', 'lineal_per_setup'),
                                  ('how many minutes of downtime this month', '/corr/1/day/2026-09-14?measure=kpis&window=month', 'downtime_minutes')]:
        assert catch_all_failures(question, url), question
        # the same quantity over every shift, as a question that names no shift is answered (round 4, item 8); its own metric, since
        # the plain-quantity gate (round 5) reads what the metric consumes ("per setup" is the ratio's, not the setup count's)
        assert not catch_all_failures(question, url.replace('measure=kpis', f'measure=quantity&metric={metric}').replace('/shift/2', '')), question
    for question in ('Compare this month to last', 'How did September go versus August?', 'How did shift 3 go this month?', 'Compare all shifts'):
        assert not catch_all_failures(question, '/corr/1/day/2026-09-14?measure=kpis&window=month'), question


@pytest.mark.parametrize('question', AUDIT, ids=lambda q: q if isinstance(q, str) else q[0])
def test_no_audited_question_lands_on_the_kpi_summary_and_each_opens_with_its_unit(question):
    """Every audited question lands on a measure that answers it, or is refused with what can be counted; an answer's
    headline carries the unit asked (run.py's rule), and its address answers the same. A question asked with another
    shift selected names it beside the question (the golden case's controls)."""
    question, shift, scope = (question, 2, 'shift') if isinstance(question, str) else (*question, 'shift')[:3]
    url, request, asked = landing(question, scope=scope, shift=shift)
    if url is None:
        assert any(r in request for r in REFUSALS) or names_three_that_answer(request), (question, request)
        return
    assert not catch_all_failures(question, url), (question, url)
    store = default_store()
    analysis = store.day_analysis(asked.production_day)
    try:
        result = Agent(analysis).ask(question, asked, request=request)
    finally:
        analysis.close()
    assert result['status'] == 'complete', (question, result['sections'][:2])
    assert not asked_unit_failures(question, result['sections']), (question, result['sections'][0])
    resolved = resolve_url(url, store)
    assert page_url(resolved) == url and redirect_for(url, store) is None, url
    assert resolved['request'].get('lead') == request.get('lead') and resolved['request']['plan'] == request['plan'], url


def test_the_seven_rows_of_the_issue_land_on_one_quantity():
    """The issue's table, by row: the measure and the metric each lands on, and the scope it is answered in."""
    # no shift named: every shift together (the coordinator's review of PR 143, item 7, pending the owner)
    rows = {'how many setups did we run last week': '/corr/1/week/2026-09-07?measure=quantity&metric=setups',
            'how many paper changes last week': '/corr/1/week/2026-09-07?measure=quantity&metric=paper_changes',
            'how many order changes this week': '/corr/1/day/2026-09-14?measure=quantity&window=week&metric=order_changes',
            'what was our lineal per setup last week': '/corr/1/week/2026-09-07?measure=quantity&metric=lineal_per_setup',
            'how many minutes of maintenance downtime yesterday': '/corr/1/day/2026-09-11?measure=quantity&metric=maintenance_minutes',
            'how many stops did we have yesterday': '/corr/1/day/2026-09-11?measure=quantity&metric=stops',
            'how many setups yesterday': '/corr/1/day/2026-09-11?measure=quantity&metric=setups'}
    for question, url in rows.items():
        assert landing(question)[0] == url, question


def test_the_router_refuses_a_quantity_it_cannot_place_rather_than_summarize():
    """The last line of the router (issue #113, item 3): a plan that is the KPI summary alone, asked by a question that
    names a countable noun or a unit, is refused with what can be counted until the model reads such questions (#104)."""
    summary = {'intent': 'period_compare', 'plan': [{'tool': 'get_period_kpis', 'arguments': {}}]}
    with pytest.raises(ValueError, match='one quantity at a time'):
        catch_all_guard('how did we do on setups this month', summary)
    catch_all_guard('how did we do this month', summary)   # the summary is what that question asks for
    catch_all_guard('how many setups this month', {**summary, 'intent': 'period_quantity'})
    context = Context(DAY, 2, scope='shift', period='day')
    for question in ('How did we do on glue this week?', 'How did we do on the stacker this week?', 'How did this month go on steam?'):
        with pytest.raises(ValueError, match='one quantity at a time'):
            parse_request(question, context)
    with pytest.raises(ValueError, match="where is the money this week"):
        parse_request('How did we do on cost this week?', context)
    # a countable noun on a summary phrasing is the count it names (the review of this PR), never the KPI summary
    for question, metric in (('How did we do on setups this month?', 'setups'), ('Compare stops this week to last week', 'stops'),
                             ('How did August go on paper changes?', 'paper_changes'), ('How did we do on missed splices this week?', 'stops')):
        assert parse_request(question, context)['lead']['metric'] == metric, question
    assert parse_request('How did August go?', context)['plan'][0]['tool'] == 'get_period_kpis'


@pytest.mark.parametrize('question, refusal', [
    ('How many step-ups did we have this week?', 'no step-ups'), ('How many rolls did we use?', 'do not count rolls'),
    ('How many boards did we lose at the stacker?', 'do not count boards'), ('How many splices did we run?', 'not every splice'),
    ('How many orders did we run?', 'distinct orders'), ('How many customers did we run this week?', 'do not count customers'),
    ('How many hours of downtime this month?', 'in minutes, not hours'), ('How many pallets did we ship?', 'no shipping'),
    ('How many stops on 55-C this week?', 'not by grade'), ('How many order changes on 275-BC this week?', 'not by grade'),
    ('How many setups on the single facer this week?', 'counted for the line'), ('How many sheets at the stacker this week?', 'counted for the line'),
    ('How many reject sheets this week?', 'square feet by reason'), ('How many sheets of warp this week?', 'square feet by reason'),
    ('What was our minutes per setup this week?', 'not a ratio the records keep'), ('How many sheets per order this week?', 'not a ratio the records keep'),
    ('How many minutes did setups take this week?', 'setup matrix'), ('How long are our runs this week?', 'setup matrix'),
    ('How much trim in MSF this week?', 'in square feet'), ('How many square feet did we make this week?', 'Square feet are kept for the waste lines'),
    ('How many widgets on shift 2?', 'one quantity at a time'), ('How much scrap on shift 2?', 'one quantity at a time'),
    ('Feet per stop this week', 'Feet per stop is not a ratio the records keep'), ('Setups per day this week', 'Setups per day is not a ratio'),
    ('Splices per setup this week', 'Splices per setup is not a ratio'), ('MSF per hour this week', 'MSF per hour is not a ratio'),
    ('How long were we slow on shift 2?', 'Slow is a speed'), ('How many minutes were we slow this week?', 'Slow is a speed'),
    ('How much downtime in hours this week?', 'in minutes, not hours'),
    ('How many feet did we run per day this week?', 'Feet per day is not a ratio'), ('How much per day this week?', 'Per day is not a ratio'),
    ('How long did we run slow this week?', 'Slow is a speed'), ('What was our fastest setup time?', 'setup matrix'),
    # round 4 of the coordinator's review
    ('Cost per setup this week', 'Cost per setup is not a ratio'), ('Glue per setup this week', 'Glue per setup is not a ratio'),
    ('Starch per setup this week', 'Starch per setup is not a ratio'), ('Yield per setup this week', 'Yield per setup is not a ratio'),
    ('Efficiency per setup this week', 'Efficiency per setup is not a ratio'), ('Loss per setup this week', 'Loss per setup is not a ratio'),
    ('Sheets per minute this week', 'Sheets per minute is not a ratio'), ('Stops per minute this week', 'Stops per minute is not a ratio'),
    ('Setups per minute this week', 'Setups per minute is not a ratio'), ('Square feet per minute this week', 'Square feet per minute is not a ratio'),
    ('MSF per minute this week', 'MSF per minute is not a ratio'),
    ('Average setup length this week', 'per setup in the setup matrix'), ('How long was the average changeover this week?', 'per setup in the setup matrix'),
    ('Average missed splice stop this week', "each stop reason's minutes per stop are in the downtime review"),
    ('Average maintenance stop this week', 'the downtime review'),
    # the reviewer subagent's probe of round 4: a grouping, an average across days, a percent per day, a share of waste
    ('Average stop length by reason this week', 'the downtime review'), ('Average minutes per stop by reason this week', "the whole window's"),
    ('How long do our missed splices take on average this week?', 'the downtime review'),
    ('Average downtime per day this week', 'No tool returns one average across'), ('Percent downtime per day this week', 'kept in minutes'),
    ('Downtime per reason per day this week', "the whole window's"), ('How many minutes of downtime at each place per day this week?', 'not by day'),
    ('How many minutes did the slowest crew lose this week?', 'A ranked shift'),
    ('Dry-end rejects as a percent of gross this week', 'a percent of the yield'), ('What percentage of waste was dry end this week?', 'not as a share'),
    ('How many setups at each place this week?', "the whole window's"), ('Stops breakdown by reason in percent this week', "the whole window's"),
    ('How long was the longest stop this week?', 'No tool returns the longest'), ('What was our shortest setup this week?', 'No tool returns the longest'),
    ('Which stop was the longest this week?', 'No tool returns the longest'),
    ('How many minutes of downtime did the slowest shift have this week?', 'A ranked shift'),
    ('How many setups did the best shift run this week?', 'A ranked shift'), ('How many stops did the shift with the most downtime have?', 'A ranked shift'),
])
def test_a_count_the_records_do_not_hold_is_refused_with_what_can_be_counted(question, refusal):
    with pytest.raises(ValueError, match=refusal) as caught:
        parse_request(question, Context(DAY, 2, scope='shift', period='day'))
    assert QUANTITY_HELP in str(caught.value) or names_three_that_answer(str(caught.value))   # the day words name three (round 6)


@pytest.mark.parametrize('question, scope, refusal', [
    ('How many setups did each shift run this week?', 'day', 'one shift or all shifts together'),
    ('How many stops per shift this week?', 'shift', 'one shift or all shifts together'),
    ('How many setups between shifts this week?', 'day', 'one shift or all shifts together'),
    ('How many setups did shift 1 and shift 3 run this week?', 'day', 'one shift or all shifts together'),
    ('How many setups across all shifts this week?', 'shift', 'Selected shift only'),
    ('How fast did we change paper this week?', 'shift', 'per setup and per production day'),
    ('How quick were the changeovers this month?', 'day', 'per setup and per production day'),
    ('How much scrap on shift 1 versus shift 2?', 'day', 'one quantity at a time'),
    ('How many minutes of downtime did shift 1 have versus shift 3?', 'day', 'one shift or all shifts together'),
    ('Compare shifts on downtime minutes', 'day', 'one shift or all shifts together'),
    ('Compare the shifts on setups this week', 'shift', 'one shift or all shifts together'),
    ('Shift comparison of stops this week', 'day', 'one shift or all shifts together'),
    ('Compare stops across shifts this week', 'day', 'one shift or all shifts together'),
    ('How do the shifts stack up on setups this week?', 'day', 'one shift or all shifts together'),
    ('Minutes of downtime shift vs shift this week', 'day', 'one shift or all shifts together'),
    ('How many minutes of downtime did the slowest shift have this week?', 'day', 'A ranked shift'),
])
def test_a_count_split_by_shift_or_a_setup_speed_over_a_window_is_refused(question, scope, refusal):
    """A quantity is one shift or all shifts together, never each shift's total read as the answer; how fast a setup went
    is the setup matrix, per shift and per day, never the window's KPI tiles."""
    with pytest.raises(ValueError, match=refusal):
        parse_request(question, Context(DAY, 2, scope=scope, period='day'))
    assert parse_request('How many setups across all shifts this week?', Context(DAY, 2, scope='day', period='day'))['plan'][0]['arguments']['shift'] is None


def test_every_quantity_has_one_canonical_address_in_every_grain():
    """measure=quantity round-trips for every metric, every stop reason and place, under every grain and both scopes;
    a metric, reason or place outside the catalog is refused, and so is reason= on another measure."""
    store = default_store()
    windows = {'day': ('day', DAY), 'week': ('week', DAY), 'month': ('month', DAY), 'period': ('fiscal', DAY)}
    for grain, (period, anchor) in windows.items():
        for scope, shift in (('day', None), ('shift', 2)):
            context = Context(DAY, shift or 2, scope=scope, period=period)
            leads = [{'metric': m} for m in QUANTITY_METRICS if not m.startswith('waste_')] + [{'metric': 'stops', 'reason': r} for r in STOP_REASONS] + \
                    [{'metric': 'downtime_minutes', 'place': p} for p in STOP_PLACES] + \
                    [{'metric': m, 'grade': g} for m in GRADE_QUANTITIES for g in GRADES] + \
                    [{'metric': m, 'waste': w} for m in ('waste_sqft', 'waste_pct') for w in WASTE_KEYS]
            for lead in leads:
                request = quantity_request(lead, {'period': period, 'anchor': anchor}, shift)
                url = canonical_url(request, context)
                resolved = resolve_url(url, store)
                assert page_url(resolved) == url and resolved['request']['lead'] == lead and resolved['request']['plan'] == request['plan'], url
    for url, reason in [(f'/corr/1/week/{DAY}?measure=quantity', 'metric is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=volume_msf', 'metric is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&reason=coffee', 'reason is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&place=canteen', 'place is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=setups&reason=missed-splice', 'takes metric=stops'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&reason=stacker&place=stacker', 'one of reason= or place='),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=setups&shifts=1,3', 'not split by shift'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=stops&grade=32-c', 'A grade takes metric=setups'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=setups&grade=99-c', 'grade is one of'),
                        (f'/corr/1/week/{DAY}?measure=grade&grade=32-c', 'grade= does not apply'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=waste_sqft', 'takes waste='),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=waste_pct&waste=glue', 'waste is one of'),
                        (f'/corr/1/week/{DAY}?measure=quantity&metric=setups&waste=trim', 'takes waste='),
                        (f'/corr/1/week/{DAY}?measure=waste&waste=trim', 'waste= does not apply'),
                        (f'/corr/1/week/{DAY}?measure=kpis&reason=missed-splice', 'reason= does not apply'),
                        (f'/corr/1/week/{DAY}?measure=pareto&place=stacker', 'place= does not apply')]:
        with pytest.raises(ValueError, match=reason):
            resolve_url(url, store)
    assert slug('Knife / slitter jam') == 'knife-slitter-jam' and slug('Raw material (roll)') == 'raw-material-roll' and slug('Slitter/scorer') == 'slitter-scorer'


def test_a_number_with_no_shift_named_answers_every_shift_over_the_window_it_names_or_the_selected_day():
    """The owner's scope ruling (2026-11-13): a question that asks for a number and names no shift answers every shift together,
    whatever the shift control says, counts, minutes, MSF, percentages and speed alike, and an ask word never changes the scope
    ("how much downtime this week" reads what "total downtime this week" reads). A question that names no day or window reads the
    selected day, every quantity and the downtime review alike (it retired #101's previous production day). "This shift" or a named
    shift keeps the shift, and so do the investigations (why, what caused or drove, where it came from), dollars, and a production
    day's per-shift answers (its speed, whose all-shift form is the three shifts compared)."""
    store = default_store()
    context = Context(DAY, 2, scope='shift', period='day')
    for question, day in (('How many setups did we run?', DAY), ('How many stops did we have?', DAY), ('What was our minutes per stop?', DAY),
                          ('How many minutes of maintenance downtime?', DAY), ('How many stops this week?', DAY),
                          ('How many setups yesterday?', '2026-09-11'), ('How many minutes of maintenance downtime yesterday?', '2026-09-11'),
                          ('How many missed splices yesterday?', '2026-09-11'), ('How many square feet of trim this week?', DAY),
                          ('What percent downtime this week?', DAY), ('How many msf did we run last week?', '2026-09-07'),
                          ('Number of stops by reason this week', DAY), ('How much downtime this week?', DAY), ('Total downtime this week', DAY),
                          ('Feet per minute this week', DAY), ('How many feet per minute did we run this week?', DAY),
                          ('Which downtime reason cost the most minutes this month?', DAY), ('How much did we make this week?', DAY),
                          ('How much did we make?', DAY), ('Waste breakdown this week', DAY)):
        moved = question_scope(question, context, store.production_days)
        assert (moved.scope, moved.production_day, moved.read.get('all_shifts'), moved.read.get('default')) == ('day', day, True, None), question
    # a quantity measure takes the default scope with or without an ask word (round 4 of the coordinator's review, item 8)
    for question in ('Stops per day this week', 'How many stops per day this week?', 'Downtime per day this week', 'Feet per setup this week'):
        moved = question_scope(question, context, store.production_days)
        assert (moved.scope, moved.read.get('all_shifts')) == ('day', True), question
    # the owner's downtime review: all shifts, on the selected day (or the day "yesterday" names), never the day before it
    for question, day, default in (('How much downtime?', DAY, 'all shifts on the selected day'), ('What percent downtime?', DAY, 'all shifts on the selected day'),
                                   ('How much downtime yesterday?', '2026-09-11', 'all shifts')):
        moved = question_scope(question, context, store.production_days)
        assert (moved.scope, moved.production_day, moved.read.get('default')) == ('day', day, default), question
    for question in ('How many setups did this shift run yesterday?', 'How many stops on shift 2 yesterday?', 'How many setups did our shift run?',
                     'How many dollars did trim cost this week?', 'Why was downtime high every day this week?', 'What drove downtime this week?',
                     'Where did the downtime come from this week?', 'Why was downtime so high?', 'How many feet per minute did we run?',
                     'How fast did we run?', 'Feet per minute', 'Which downtime reason cost the most minutes this month on this shift?'):
        assert question_scope(question, context, store.production_days).scope == 'shift', question
    assert question_scope('Show setup matrix', context, store.production_days) is context   # not a number: the controls


def test_the_runner_states_the_scope_ruling():
    """evals/run.py fails a number answered for the selected shift when the question names none (the review, the Pareto, the waste
    breakdown, the totals by grade, a window's speed), and passes an investigation or a production day's per-shift speed there."""
    assert catch_all_failures('How much downtime this week?', '/corr/1/day/2026-09-14/shift/2?measure=review&window=week')
    assert catch_all_failures('Feet per minute this week', '/corr/1/day/2026-09-14/shift/2?measure=fpm&window=week')
    assert catch_all_failures('How much did we make?', '/corr/1/day/2026-09-14/shift/2?measure=grade')
    assert catch_all_failures('Which downtime reason cost the most minutes this month?', '/corr/1/day/2026-09-14/shift/2?measure=pareto&window=month')
    for question, url in (('Why was downtime high every day this week?', '/corr/1/day/2026-09-14/shift/2?measure=review&window=week'),
                          ('Where did the downtime come from this week?', '/corr/1/day/2026-09-14/shift/2?measure=pareto&window=week&by=place'),
                          ('How fast did we run?', '/corr/1/day/2026-09-14/shift/2?measure=fpm'),
                          ('How much downtime this week on this shift?', '/corr/1/day/2026-09-14/shift/2?measure=review&window=week')):
        assert not catch_all_failures(question, url), question


def test_the_runner_catches_the_wrong_answers_the_review_found():
    """The rules in evals/run.py, held to the answers the router gave before the coordinator's review of PR 143: each would fail."""
    assert catch_all_failures('How many widgets on shift 2?', '/corr/1/day/2026-09-14/shift/2?measure=overview')
    assert catch_all_failures('How much scrap on shift 2?', '/corr/1/day/2026-09-14/shift/2?measure=overview')
    assert catch_all_failures('How long were we down on shift 3?', '/corr/1/day/2026-09-14/shift/3?measure=overview')
    assert not catch_all_failures('Why was this shift slow?', '/corr/1/day/2026-09-14/shift/2?measure=overview')
    assert not catch_all_failures('Summarize performance, downtime and waste. Show supporting charts.', '/corr/1/day/2026-09-14/shift/2?measure=summary')
    assert asked_unit_failures('What was our minutes per setup last week?', ['**7,212.6 ft per setup** · -401.8 vs 7,614.4 the week before'])
    assert asked_unit_failures('How many square feet of trim this week?', ['**3.21%** of gross · +0.17 points'])
    assert asked_unit_failures('What percent was shear this week?', ['**388,677 sq ft** of Side trim'])
    assert asked_unit_failures('How much waste in MSF this week?', ['**5.04%** of gross'])
    assert not asked_unit_failures('What was our lineal per setup last week?', ['**7,212.6 ft per setup** · -401.8 vs 7,614.4 the week before'])
    # the coordinator's re-review: a ratio answered with its numerator's count, a unit found only after the lead clause, a
    # duration asked as "how long" or in hours, "how often" on the overview, and the shift comparison
    assert asked_unit_failures('Feet per stop this week', ['**137 stops** · -39 vs 176 the week before, better'])
    assert asked_unit_failures('How many minutes did knife jams take?', ['**10 stops** · 17.62 minutes · 4 reasons'])
    assert asked_unit_failures('How long did knife jams take this week?', ['**28 stops** · 50.45 minutes'])
    assert asked_unit_failures('How much downtime in hours last week?', ['**3.94%** of scheduled minutes · on target'])
    assert asked_unit_failures('What was our MSF per day this week?', ['**12,092.1 MSF** · -453.0 vs 12,545.1 MSF, worse'])
    assert not asked_unit_failures('What was our MSF per day this week?', ['**6,967.0 MSF per production day** · -53.8 vs 7,020.8'])
    assert not asked_unit_failures('How long did knife jams take this week?', ['**50.45 minutes** for Knife / slitter jam · 28 stops'])
    assert not asked_unit_failures('Stops per day this week', ['**31 stops** on 2026-09-15 (Tue), the most of 5 production days'])
    assert catch_all_failures('How often did we stop this week?', '/corr/1/day/2026-09-14/shift/2?measure=overview&window=week')
    assert catch_all_failures('How much scrap on shift 1 versus shift 2?', '/corr/1/day/2026-09-14?measure=compare&shifts=1,2')
    assert not catch_all_failures('How much faster was shift 1 than shift 2?', '/corr/1/day/2026-09-14?measure=compare&shifts=1,2')
    assert not catch_all_failures('Compare shift 1 and shift 2 in feet per minute', '/corr/1/day/2026-09-14?measure=compare&shifts=1,2')
    assert asked_unit_failures('How many feet per minute did we run this week?', ['**4,225,692 lineal ft** · -1,000 vs 4,226,692'])
    assert not asked_unit_failures('Were we slow in the first hours of the shift?', ['**87.2%** of grade target'])
    # round 4 of the coordinator's review: each wrong answer it found, and the right one beside it where there is one
    shift_1 = '/corr/1/day/2026-09-14/shift/1?measure=quantity&window=week&metric=downtime_minutes'
    assert catch_all_failures('How many minutes of downtime did the slowest shift have this week?', shift_1)   # item 1
    assert catch_all_failures('How many minutes of downtime did the best shift have this week?', shift_1)
    assert not catch_all_failures('How many minutes of downtime did shift 1 have this week?', shift_1)
    for word in ('Cost', 'Glue', 'Starch', 'Yield', 'Efficiency', 'Loss'):   # item 2: the bare "per setup" rule
        assert asked_unit_failures(f'{word} per setup this week', ['**7,126.0 ft per setup** · +76.2 vs 7,049.8 the week before']), word
    assert not asked_unit_failures('What was our per setup this week?', ['**7,126.0 ft per setup** · +76.2 vs 7,049.8 the week before'])
    assert not asked_unit_failures('Length per setup this week', ['**7,126.0 ft per setup** · +76.2 vs 7,049.8 the week before'])
    assert asked_unit_failures('Sheets per minute this week', ['**3,800,554 sheets** cut across all knives · 3,789,206 nominal'])   # item 3
    assert asked_unit_failures('Stops per minute this week', ['**137 stops** · -39 vs 176 the week before, better'])
    assert asked_unit_failures('Setups per minute this week', ['**580 setups** · -26 vs 606 the week before'])
    for question in ('Average stop length this week', 'Average stop duration this week', 'Typical stop this week',   # item 4
                     'What was the average stop this week?'):
        assert asked_unit_failures(question, ['**137 stops** · -39 vs 176 the week before, better']), question
        assert not asked_unit_failures(question, ['**2.66 minutes per stop** · +0.27 vs 2.39 the week before, worse']), question
    assert asked_unit_failures('How long is our average stop?', ['**41.4 minutes** of downtime · +3.6 vs 37.8 the production day before'])
    assert asked_unit_failures('Average setup length this week', ['**580 setups** · -26 vs 606 the week before'])
    assert asked_unit_failures('How long was the longest stop this week?', ['**364.3 minutes** of downtime · -66.2 vs 430.5 the week before'])
    week = '/corr/1/day/2026-09-14?measure=quantity&window=week&metric=downtime_minutes'
    assert catch_all_failures('How many minutes of downtime at each place this week?', week)   # item 5
    assert catch_all_failures('Compare shifts on downtime minutes', '/corr/1/day/2026-09-14?measure=quantity&metric=downtime_minutes')
    assert not catch_all_failures('How many minutes of downtime at each place this week?', '/corr/1/day/2026-09-14?measure=pareto&window=week&by=place')
    assert asked_unit_failures('Square feet per minute this week', ['**618.2 ft/min** without stops · -26.9 vs 645.1 ft/min'])   # item 6
    assert catch_all_failures('Waste breakdown in percent this week',   # item 7
                              '/corr/1/day/2026-09-14?measure=quantity&window=week&metric=waste_pct&waste=bale')
    assert not catch_all_failures('Waste breakdown in percent this week', '/corr/1/day/2026-09-14?measure=waste&window=week')
    assert catch_all_failures('Stops per day this week', '/corr/1/day/2026-09-14/shift/2?measure=stops&window=week')   # item 8
    assert not catch_all_failures('Stops per day this week', '/corr/1/day/2026-09-14?measure=stops&window=week')
    assert not catch_all_failures('How many stops per day on this shift this week?', '/corr/1/day/2026-09-14/shift/2?measure=stops&window=week')
    assert not asked_unit_failures('Downtime per day this week',   # item 9: the downtime of each production day, in minutes
                                   ['**160.93 minutes** of downtime on 2026-09-17 (Thu), the most of 5 production days · 20 stops'])
    assert asked_unit_failures('Downtime per day this week', ['**36 stops** on 2026-09-15 (Tue), the most of 5 production days · 55.10 min'])
    # the reviewer subagent's probe of round 4: the same wrong answers in other words
    assert catch_all_failures('How many minutes did the slowest crew lose this week?', week)
    assert catch_all_failures('How many stops did the shift with the most downtime have?', '/corr/1/day/2026-09-14?measure=quantity&metric=stops')
    for question in ('How many minutes of downtime in each area this week?', 'How many minutes of downtime at every piece of equipment this week?',
                     'How many minutes of downtime for each stop reason this week?', 'Downtime breakdown in minutes this week',
                     'Average stop length by reason this week', 'Downtime per reason per day this week', 'How many setups at each place this week?',
                     'Shift comparison of stops this week', 'Compare stops across shifts this week', 'How do the shifts stack up on setups this week?',
                     'Minutes of downtime shift vs shift this week', 'Waste lines in percent this week'):
        assert catch_all_failures(question, week), question
    assert not catch_all_failures('How many setups across all shifts this week?', week.replace('downtime_minutes', 'setups'))
    assert asked_unit_failures('Average downtime per day this week',
                               ['**160.93 minutes** of downtime on 2026-09-17 (Thu), the most of 5 production days · 20 stops'])
    assert asked_unit_failures('Percent downtime per day this week',
                               ['**160.93 minutes** of downtime on 2026-09-17 (Thu), the most of 5 production days · 20 stops'])


def test_per_is_a_ratio_only_when_it_divides_and_slow_or_hours_only_when_asked():
    """The reviewer's check of the re-review's fix: "per" meaning "by" keeps its grouping, a speed per day or in feet per minute is
    the speed answer, what we made per day is MSF per production day; hours named as a span and slow named of something else
    are not refused; a shift comparison asked in feet per minute stays the comparison, which opens with it."""
    store = default_store()
    for question, measure in (('Downtime per reason this week', 'measure=review'), ('Downtime per equipment this week', 'by=place'),
                              ('Downtime per kind this week', 'by=kind'), ('Dry-end rejects per reason this week', 'measure=waste'),
                              ('Rollstock per width this month', 'measure=rollstock'), ('Speed per run this week', 'measure=fpm'),
                              ('Average speed per day this week', 'measure=fpm'), ('How many feet per minute did we run this week?', 'measure=fpm'),
                              ('How much did we make per production day this month?', 'metric=msf_per_day'),
                              ('Production per day this week', 'metric=msf_per_day'),
                              ('What was speed in the first hours of the week?', 'measure=fpm'),
                              ('How many minutes of downtime did shift 3 have this week?', 'metric=downtime_minutes'),
                              # round 4 of the coordinator's review: an average stop, a time per stop, the downtime of each day
                              ('Average stop length this week', 'metric=minutes_per_stop'), ('Average time per stop this week', 'metric=minutes_per_stop'),
                              ('How long is our average stop?', 'metric=minutes_per_stop'), ('What was our per stop this week?', 'metric=minutes_per_stop'),
                              ('Downtime per day this week', 'metric=downtime_per_day'), ('How much downtime per day in September?', 'metric=downtime_per_day'),
                              ('How many stops at each place this week?', 'by=place&unit=count'), ('Waste breakdown in percent this week', 'measure=waste'),
                              ('Dry-end rejects by reason in percent this week', 'measure=waste'), ('Lineal feet per minute this week', 'measure=fpm'),
                              ('How many feet per setup did we run this week?', 'metric=lineal_per_setup'),
                              ('How many minutes of downtime in each area this week?', 'by=place'), ('Stops by kind this week', 'by=kind&unit=count'),
                              ('How many minutes of downtime for each stop reason this week?', 'measure=pareto&window=week'),
                              ('Downtime breakdown in minutes this week', 'measure=pareto&window=week'), ('Waste lines in percent this week', 'measure=waste'),
                              ('Feet per min this week', 'measure=fpm'), ('ft per minute this week', 'measure=fpm')):
        url = landing(question)[0]
        assert url and measure in url, (question, url)
    url = landing('Compare shift 1 and shift 2 in feet per minute', scope='day')[0]
    assert url and 'measure=compare' in url, url


def test_order_changes_are_counted_beside_the_tiles_and_never_zero_for_a_missing_window():
    """The KPI tool counts the setups an order change started (change code O) with the previous window's; a window the
    records do not hold is unavailable (None), never zero. The value goldens recount them from the raw records."""
    store = default_store()
    week = store.get_period_kpis({'period': 'week', 'anchor': '2026-09-07'}, shift=2)
    setups = next(r for r in week['rows'] if r['metric'] == 'setups')
    assert 0 < week['order_changes'] <= setups['current'] and week['order_changes_delta'] == week['order_changes'] - week['previous_order_changes']
    first = store.get_period_kpis({'period': 'week', 'anchor': store.production_days[0]})
    assert first['previous_order_changes'] is None and first['order_changes_delta'] is None


def test_a_count_or_unit_golden_states_its_headline():
    """Every golden routing case whose question asks for a count or a unit pins the headline's unit (`shape.headline_match`
    or a headline in `sections_match`), so the headline-unit rule is held case by case as well as by run.py."""
    asks = re.compile(r'\b(how many|number of|count of)\b|\bminutes per stop\b|\bper (set-?up|paper change)\b', re.I)
    for case in GOLDEN:
        question = (case.get('turns') or [case.get('question') or ''])[-1]
        if case['type'] != 'routing' or case.get('mode') != 'offline' or not asks.search(question) or 'trend' in question.lower():
            continue
        expect = case['expect']
        patterns = [expect.get('shape', {}).get('headline_match', '')] + (
            [expect['sections_match']] if isinstance(expect.get('sections_match'), str) else expect.get('sections_match', []))
        assert any(p.startswith('^\\*\\*') for p in patterns), case['id']


# ---- Round 6 of the coordinator's review of PR 143: the allow-list. A quantity answers only when every word of the question is
# consumed (its measure, its filter, the window, the shift) or is one of a closed table of function words, the same table in the
# router and the runner. This file states it once more, so a word added to either side fails here until it is added to all three.
# Issue #153 took out every word that names a plan, a capacity, what is left, the line running or a span to date ("can", "could",
# "would", "will", "need", "want", "to", "go", "went", "up", "so", "far"): a word sits here only if it carries no meaning a route could
# ignore.
FUNCTION_WORDS_TABLE = sorted((
    'a', 'an', 'the', 'this', 'that', 'these', 'those', 'any', 'all', 'some', 'our', 'my', 'your', 'its', 'their',
    'i', 'me', 'we', 'us', 'you', 'it', 'they', 'there', "what's", 'whats', "it's", "there's", "we've",
    'what', 'how', 'many', 'much', 'number', 'count', 'total', 'totals', 'overall', 'altogether', 'combined', 'amount',
    'is', 'are', 'was', 'were', 'be', 'been', 'do', 'does', 'did', 'have', 'has', 'had',
    'run', 'ran', 'make', 'made', 'get', 'got', 'see', 'saw', 'log', 'logged', 'record', 'recorded', 'rack', 'racked',
    'happen', 'happened', 'occur', 'occurred', 'show', 'tell', 'give', 'done', 'complete', 'completed',
    'know', 'of', 'in', 'on', 'at', 'for', 'from', 'please', 'line', 'corrugator', 'machine', 'plant'))
WEEK_SETUPS = '/corr/1/day/2026-09-14?measure=quantity&window=week&metric=setups'


def test_the_function_words_are_one_closed_table_in_the_router_and_the_runner():
    """One table in the router (agent.FUNCTION_WORDS) and one in the runner (run.FUNCTION_WORDS), each this file's; no word of it is
    one a refusal names as asking a different question (agent.LEFTOVER_FAMILIES)."""
    assert sorted(FUNCTION_WORDS) == FUNCTION_WORDS_TABLE and sorted(RUN_FUNCTION_WORDS) == FUNCTION_WORDS_TABLE
    assert not set(FUNCTION_WORDS) & {word for words in LEFTOVER_FAMILIES.values() for word in words}


@pytest.mark.parametrize('word', FUNCTION_WORDS_TABLE)
def test_a_function_word_never_takes_a_plain_quantity_off_its_route(word):
    """Each function word, put into a plain count, leaves it the count: the router answers it and the runner passes it."""
    question = f"how many setups {word} this week"
    url, request, _ = landing(question)
    assert url and 'metric=setups' in url, (question, url or request)
    assert not catch_all_failures(question, url), (question, url)


# a word that needs an object to read as a question ("by type", "across reasons", "per MSF", "over 10")
OBJECT = {'by': 'by type', 'across': 'across reasons', 'per': 'per MSF', 'over': 'over 10', 'under': 'under 10', 'than': 'more than 10',
          'within': 'within an hour', 'between': 'between stops', 'during': 'during stops', 'before': 'before noon', 'after': 'after lunch'}
OUTSIDE = sorted({word for words in LEFTOVER_FAMILIES.values() for word in words} | {'unexpected', 'new', 'weird', 'blue', 'quickly', 'customer'})


@pytest.mark.parametrize('word', OUTSIDE)
def test_every_word_outside_the_table_is_refused_by_the_router_and_failed_by_the_runner(word):
    """Any word the route does not consume, put into a plain count (every word a refusal names a family for, and words of no family),
    takes the count off the quantity route (refused, or another route answers it), and the runner fails the count's own URL for it."""
    question = f"how many setups {OBJECT.get(word, word)} this week"
    url, refusal, _ = landing(question)
    assert url is None or 'measure=quantity' not in url, (question, url)
    assert catch_all_failures(question, WEEK_SETUPS), question


@pytest.mark.parametrize('case', [c for c in GOLDEN if c['id'].startswith(('gate-', 'allow-', 'unapplied-')) and c['type'] == 'refusal'],
                         ids=lambda c: c['id'])
def test_a_gate_refusal_names_what_it_answers_in_at_most_three_questions_that_answer(case):
    """A refusal of rounds 5 and 6, the day words' too, names what the route answers instead, at most three questions and never the
    generic help, and each of them is answered."""
    url, refusal, _ = landing(case['question'], shift=case.get('shift', 2), scope=case.get('scope', 'shift'), period=case.get('period', 'day'))
    assert url is None and case['expect']['raises'] in refusal, (case['question'], url, refusal)
    asks = re.findall(r"'([^']* this week)'", refusal)
    assert 1 <= len(asks) <= 3 and QUANTITY_HELP not in refusal, refusal
    for ask in asks:
        assert landing(ask)[0], (ask, landing(ask)[1])


def test_the_runner_catches_round_6s_wrong_answers():
    """evals/run.py, held to the answers ff8ad77 gave the round-6 review's questions: each fails on its own."""
    week = '/corr/1/day/2026-09-14?measure=quantity&window=week&metric='
    profile = '/corr/1/day/2026-09-14?measure=stops&window=week'
    for question, url in (('How many stops under 2 minutes this week?', week + 'stops'), ('How many brief stops this week?', week + 'stops'),
                          ('How many stops before noon this week?', week + 'stops'), ('Stop rate this week', week + 'stops'),
                          ('Stop distribution this week', week + 'stops'), ('What proportion of stops were at the stacker this week?', week + 'stops&place=stacker'),
                          ('90th percentile stop length this week', week + 'downtime_minutes'), ('Minimum stops in a day this week', profile),
                          ('What was the fewest stops in a day this week?', profile), ('How many long stops each day this week?', profile),
                          ('Share of stops each day this week', profile), ('How many stops at the slitter this week?', week + 'stops'),
                          ('How many planned stops this week?', week + 'stops'), ('How many unplanned stops this week?', week + 'stops'),
                          ('How many minutes of planned downtime this week?', week + 'downtime_minutes'),
                          ('Minutes per stop at the dry end this week', week + 'minutes_per_stop'), ('Minutes per stop for crew A this week', week + 'minutes_per_stop'),
                          ('Downtime per day at the dry end this week', week + 'downtime_per_day'),
                          ('Why was downtime high every day this week?', week + 'downtime_per_day'),
                          ('How many stops for order changes this week?', week + 'order_changes'), ('How many stops at the wet end this week?', week + 'paper_changes'),
                          ('How many stops during setups this week?', week + 'setups'), ('Ratio of stops to setups this week', week + 'setups'),
                          ('How many stops in the last 7 days?', '/corr/1/day/2026-09-14?measure=quantity&metric=stops')):
        assert catch_all_failures(question, url), (question, url)
    assert asked_unit_failures('How many stops for order changes this week?', ['**571 order changes** · -26 vs 597 the week before'])
    assert not asked_unit_failures('How many stops for order changes this week?', ['**19 stops** for Order change · 26.87 minutes'])
    # the reviewer's probe of the draft: windows, shifts and "last" read but never applied, and the right answers the runner must pass
    for question, url in (('How long did the last stop last?', '/corr/1/day/2026-09-14?measure=quantity&metric=downtime_minutes'),
                          ('How many minutes of downtime the week before last?', week + 'downtime_minutes'),
                          ('How many stops on the same day last week?', '/corr/1/week/2026-09-07?measure=quantity&metric=stops'),
                          ('How many stops in the last week of August?', '/corr/1/day/2026-09-14?measure=quantity&window=2026-08&metric=stops'),
                          ('How many stops on shift one this week?', week + 'stops'), ('How many stops month to date?', '/corr/1/day/2026-09-14?measure=quantity&window=month&metric=stops'),
                          ('How many stops per day did we have on Monday this week?', profile),
                          ('On average, how many minutes were we stopped this week?', week + 'minutes_per_stop')):
        assert catch_all_failures(question, url), (question, url)
    for question, url in (('How many setpus last week?', '/corr/1/week/2026-09-07?measure=quantity&metric=setups'),   # a typo the router reads
                          ('How many times did we stop this week?', week + 'stops'), ('How many stops this week and last week?', week + 'stops'),
                          ('How many stops did the stacker cause this week?', week + 'stops&reason=stacker'),
                          ("What's behind the downtime this week?", '/corr/1/day/2026-09-14/shift/2?measure=review&window=week'),
                          ('What is driving the downtime this week?', '/corr/1/day/2026-09-14/shift/2?measure=review&window=week')):
        assert not catch_all_failures(question, url), (question, url)
    assert not asked_unit_failures('Why were there so many stops per day this week?', ['**3.32%** of scheduled minutes in maintenance and operator downtime'])


@pytest.mark.parametrize('question, scope, metric', [
    ('How long did knife jams take this week?', 'shift', 'downtime_minutes'), ('Compare stops this week to last week', 'shift', 'stops'),
    ('How many stops this week versus last week?', 'shift', 'stops'), ('How many stops at splicer 3 this week?', 'shift', 'stops'),
    ('How many stops on the upper knife this week?', 'shift', 'stops'), ('How many stops caused by the stacker this week?', 'shift', 'stops'),
    ('How many setups across all shifts this week?', 'day', 'setups'), ('Average minutes per stop last month', 'shift', 'minutes_per_stop'),
    ('How long is our average stop?', 'shift', 'minutes_per_stop'), ('Average stop length this week', 'shift', 'minutes_per_stop'),
    ('Average run length this week', 'shift', 'lineal_per_setup'), ('What percent was shear this week?', 'shift', 'waste_pct'),
    ('How many stops did shift 2 have compared with last week?', 'shift', 'stops'), ('Average MSF per day this week', 'shift', 'msf_per_day'),
    ('Downtime by day this week', 'shift', 'downtime_per_day'), ('Daily downtime this week', 'shift', 'downtime_per_day'),
    ('How much downtime did we have per stop this week?', 'shift', 'minutes_per_stop'),
    ('How many minutes do we lose per stop this week?', 'shift', 'minutes_per_stop'),
    ('How long are we down per stop this week?', 'shift', 'minutes_per_stop'),
    ('How did shift 1 go compared with last week on lineal per paper change?', 'shift', 'lineal_per_paper_change'),
])
def test_what_the_route_consumes_is_never_a_qualifier_left(question, scope, metric):
    """The ask word, the ratio's per and average, the filter's own words ("knife jams", "splicer 3", "caused by the stacker"), the
    window, a window compared with the one before and all shifts are the route's: the quantity answers, and the runner agrees."""
    url, request, _ = landing(question, scope=scope, shift=1 if 'shift 1' in question else 2)
    assert url and f'metric={metric}' in url, (question, url or request)
    assert not catch_all_failures(question, url), (question, url)


@pytest.mark.parametrize('noun', ['count', 'frequency', 'rate', 'interval', 'cost', 'place', 'location', 'reason'])
def test_an_average_stop_is_its_length_and_nothing_else(noun):
    """Round 5, item 2: "average stop count / frequency / rate / interval / cost / place / location / reason" is not a length, so
    not minutes per stop; the runner fails minutes per stop for it."""
    question = f'Average stop {noun} this week'
    url, refusal, _ = landing(question)
    assert url is None and "'average' asks for a statistic" in refusal, (question, url, refusal)
    assert catch_all_failures(question, '/corr/1/day/2026-09-14?measure=quantity&window=week&metric=minutes_per_stop'), question
    for length in ('Average stop length this week', 'Average stop time this week', 'Average length of a stop this week',
                   'What was our average stop on shift 2 this week?'):
        assert landing(length)[0].endswith('metric=minutes_per_stop'), length


@pytest.mark.parametrize('question, metric', [
    ('Stacker minutes per stop this week', 'minutes_per_stop'), ('Minutes per stop at splicer 3 this week', 'minutes_per_stop'),
    ('Knife jam minutes per stop this week', 'minutes_per_stop'), ('Maintenance downtime per day this week', 'downtime_per_day'),
    ('Operator downtime per day this week', 'downtime_per_day'), ('Downtime per day at the stacker this week', 'downtime_per_day'),
    ('Feet per setup at the stacker this week', 'lineal_per_setup'),
])
def test_a_ratio_kept_for_all_stops_is_never_asked_of_one_reason_place_or_kind(question, metric):
    """Round 5, item 2: minutes per stop and each production day's downtime are kept for all stops together; asked of one stop
    reason, place or kind they are refused, never answered with every stop's figure, and the runner fails that figure."""
    url, refusal, _ = landing(question)
    assert url is None and 'is kept for' in refusal, (question, url, refusal)
    assert catch_all_failures(question, f'/corr/1/day/2026-09-14?measure=quantity&window=week&metric={metric}'), question


def test_the_runner_catches_round_5s_wrong_answers_and_passes_the_right_ones():
    """The rules in evals/run.py, held to the answers a7c9b44 gave the round-5 review's questions (each fails), and to the right
    answers its runner failed (each passes)."""
    week = '/corr/1/day/2026-09-14?measure=quantity&window=week&metric='
    for question, metric in (('Which shift had the most setups this week?', 'setups'), ('How did the shifts compare on stops this week?', 'stops'),
                             ('Stops by type this week', 'stops'), ('Stops each day this week', 'stops'), ('How many long stops this week?', 'stops'),
                             ('Stops per 1000 ft this week', 'stops'), ('Stacker minutes per stop this week', 'minutes_per_stop'),
                             ('Maintenance downtime per day this week', 'downtime_per_day'), ('Average stop cost this week', 'minutes_per_stop'),
                             ('Average stop reason this week', 'minutes_per_stop'), ('Median stop length this week', 'downtime_minutes'),
                             ('What was the most common stop this week?', 'stops'), ('How many stops over 10 minutes this week?', 'stops'),
                             ('Daily setups this week', 'setups'), ('What share of stops were maintenance this week?', 'maintenance_stops')):
        assert catch_all_failures(question, week + metric), question
    assert catch_all_failures('What percent of stops were at the stacker this week?', week + 'stops&place=stacker')
    assert catch_all_failures('Minutes per stop at splicer 3 this week', week + 'minutes_per_stop')
    assert asked_unit_failures('How many feet a minute did we run this week?', ['**4,196,531 lineal ft** · -142,225 vs 4,338,756 the week before'])
    assert not asked_unit_failures('How many feet a minute did we run this week?', ['**613.1 ft/min** without stops · -26.9 vs 640.0 ft/min'])
    minutes = ['**2.71 minutes per stop** · +0.47 vs 2.24 the week before, worse']
    for question in ('How much downtime did we have per stop this week?', 'How many minutes do we lose per stop this week?',
                     'How long are we down per stop this week?'):   # item 3 (A4): right answers the runner failed
        assert not asked_unit_failures(question, minutes) and not catch_all_failures(question, week + 'minutes_per_stop'), question
    feet = ['**7,273.0 ft per setup** · +113.3 vs 7,159.7 the week before, better']
    assert not asked_unit_failures('Average run length this week', feet)
    assert not catch_all_failures('Average run length this week', week + 'lineal_per_setup')
    assert asked_unit_failures('Average run length this week', ['**577 setups** · -29 vs 606 the week before'])



# The reviewer subagent's probe of round 5: what the gate consumes keeps answering, the day words never drop a filter or turn an
# average or a grouping into the busiest day or the mean, and the stops profile is every stop's.
@pytest.mark.parametrize('question, metric', [
    ('How many minutes per stop on average this week?', 'minutes_per_stop'), ('On average, how many minutes per stop this week?', 'minutes_per_stop'),
    ('How many minutes of downtime per stop did we average this week?', 'minutes_per_stop'), ('Feet per setup on average this week', 'lineal_per_setup'),
    ('On average how much did we make per day this week?', 'msf_per_day'), ('Daily average MSF this week', 'msf_per_day'),
    ("What's the total number of stops across the line this week?", 'stops'), ('How many stops across the corrugator this week?', 'stops'),
    ('How many stops across August?', 'stops'), ('How many stops this week by the stacker?', 'stops'),
    ('How many stops triggered by the stacker this week?', 'stops'), ('How many minutes were lost by the stacker this week?', 'downtime_minutes'),
    ('How many stops by operators this week?', 'operator_stops'), ('How many minutes was the line down all week long?', 'downtime_minutes'),
    ('How many stops of any type this week?', 'stops'), ('How long did the average stop take this week?', 'minutes_per_stop'),
    ("Each day's downtime this week", 'downtime_per_day'),
])
def test_a_plain_quantity_in_other_words_still_answers(question, metric):
    url, request, _ = landing(question)
    assert url and f'metric={metric}' in url, (question, url or request)
    assert not catch_all_failures(question, url), (question, url)


@pytest.mark.parametrize('question, refusal', [
    ('How many stops at the stacker each day this week?', 'kept for all stops together'), ('Stacker stops by day this week', 'kept for all stops together'),
    ('Daily operator stops this week', 'kept for all stops together'), ('Stops per day at the stacker this week', 'kept for all stops together'),
    ('Maintenance stops per day this week', 'kept for all stops together'), ('How many stops a day on average this week?', 'No tool returns one average'),
    ('Average stops per day this week', 'No tool returns one average'), ('Typical number of stops per day this week', 'No tool returns one average'),
    ('Minutes per stop each day this week', "'per' asks for a grouping"), ('Daily minutes per stop this week', "'per' asks for a grouping"),
    ('Minutes per stop at the slitter this week', 'is kept for all stops together'),
    ('Minutes per stop for scheduled stops this week', 'is kept for all stops together'),
    ('Minutes per stop for order changes this week', 'is kept for all stops together'),
])
def test_the_day_words_never_drop_a_filter_or_answer_an_average_with_the_busiest_day(question, refusal):
    url, text, _ = landing(question)
    assert url is None and refusal in text, (question, url, text)


def test_a_grouping_by_day_of_anything_but_stops_or_downtime_is_never_the_per_day_mean():
    """"MSF by day" asks for each day's MSF, a grouping, never MSF per production day (the mean); stops and downtime by day are the
    stops profile, which lists each day. And the runner fails the stops profile for one reason, place or kind."""
    for question in ('MSF by day this week', 'MSF each day this week', 'How much did we make each day this week?', 'Volume by day this week'):
        url = landing(question)[0]
        assert url is None or 'metric=msf_per_day' not in url, (question, url)
        assert catch_all_failures(question, '/corr/1/day/2026-09-14?measure=quantity&window=week&metric=msf_per_day'), question
    assert catch_all_failures('Stops per day at the stacker this week', '/corr/1/day/2026-09-14?measure=stops&window=week')
    assert not catch_all_failures('Stops per day this week', '/corr/1/day/2026-09-14?measure=stops&window=week')


# ---- Issue #153, the hotfix after PR 143: words the gate consumed but no route applied. A word may sit in the function-word table only
# if it carries no meaning a route could ignore; a word that names a scope, a comparison, a state, a plan or a time span is consumed
# only by the code that applies it.
FILTER_WORDS = '|'.join(pattern for pattern, _ in agent.PLACE_WORDS + agent.REASON_WORDS)
# The phrases a route reads, a function word inside one included ("this week", "how many", "at the stacker", "the number of", "a day",
# "how did shift 1 go", "make per day"): deleting a word of one changes the phrase, not a function word's meaning
READ_PHRASES = (*agent.CONSUMED_ALWAYS, *(pattern for words in agent.METRIC_WORDS.values() for pattern in words), agent.COUNT_ASK,
                agent.DURATION_ASK, agent.FEET_ASK, r"\bhow\s+(many|much)\b", r"\b(the\s+)?(number|count|total)\s+of\b", agent.HOW_DID_GO,
                agent.PER_WORD, r"\ba\s+day\b", r"\b(make|made|produced?|run|ran)\b(?=.*\bper\b)",
                rf"\b((at|on|by|to|from|for|of|due\s+to|caused\s+by)\s+)?(the\s+)?({FILTER_WORDS})", r"\b(on|of|for)\s+\d{2,3}-?(c|bc|eb)\b",
                agent.WINDOW_COMPARE, agent.COMPARED_TO_SAID, agent.PREVIOUS_WINDOW)


def free_function_words(question):
    """The function words of `question` outside every phrase a route reads, as regex matches on its lower case."""
    lower = question.lower()
    spans = [m.span() for pattern in READ_PHRASES for m in re.finditer(pattern, lower) if m.end() > m.start()]
    return [t for t in re.finditer(r"[a-z0-9']+", lower)
            if t.group(0) in FUNCTION_WORDS and not any(start <= t.start() and t.end() <= end for start, end in spans)]


QUANTITY_GOLDENS = [c for c in GOLDEN if c['type'] == 'routing' and c.get('mode') == 'offline' and c.get('question')
                    and re.search(r'measure=(quantity|stops)\b', c['expect'].get('url') or '') and free_function_words(c['question'])]


def test_deleting_a_function_word_from_a_golden_question_changes_nothing():
    """Issue #153: each function word a golden quantity question holds (outside the phrases a route reads), deleted, leaves the question
    on the same address: a function word carries no meaning, so no route reads it either. Twenty golden questions at least."""
    assert len(QUANTITY_GOLDENS) >= 20, len(QUANTITY_GOLDENS)
    deleted = 0
    for case in QUANTITY_GOLDENS:
        controls = dict(scope=case.get('scope', 'shift'), shift=case.get('shift', 2), period=case.get('period', 'day'), day=case.get('day', DAY))
        question, url = case['question'], case['expect']['url']
        assert landing(question, **controls)[0] == url, case['id']
        for word in free_function_words(question):
            shorter = re.sub(r'\s+', ' ', question[:word.start()] + question[word.end():]).strip()
            assert landing(shorter, **controls)[0] == url, (case['id'], word.group(0), shorter, landing(shorter, **controls)[1])
            deleted += 1
    assert deleted >= 40, deleted


WEEK = '/corr/1/day/2026-09-14?measure=quantity&window=week&metric='
# Each word that left the table (a plan or capacity, the line running, a span to date) or a shift said with no shift selected, added to a
# plain question in its own sense, with the address main (3cefa4d) gave the question, which the runner fails
ADDED = [
    ('need', 'How many setups do we need this week?', 'shift', WEEK + 'setups'),
    ('want', 'How many setups do we want this week?', 'shift', WEEK + 'setups'),
    ('can', 'How many setups can we run this week?', 'shift', WEEK + 'setups'),
    ('could', 'How many setups could we have run this week?', 'shift', WEEK + 'setups'),
    ('would', 'How many stops would we have this week?', 'shift', WEEK + 'stops'),
    ('will', 'How many setups will we run this week?', 'shift', WEEK + 'setups'),
    ('should', 'How many setups should we run this week?', 'shift', WEEK + 'setups'),
    ('to', 'How many sheets to make this week?', 'shift', WEEK + 'sheets'),
    ('go', 'How many setups to go this week?', 'shift', WEEK + 'setups'),
    ('left', 'How many setups are left this week?', 'shift', WEEK + 'setups'),
    ('up', 'How many minutes were we up this week?', 'shift', WEEK + 'downtime_minutes'),
    ('on', 'How long was the machine on this week?', 'shift', WEEK + 'downtime_minutes'),
    ('on', 'How long has the line been on this week?', 'shift', WEEK + 'downtime_minutes'),
    ('go', 'How long did the line go this week?', 'shift', WEEK + 'downtime_minutes'),
    ('going', 'How long was the line going this week?', 'shift', WEEK + 'downtime_minutes'),
    ('running', 'How long were we running this week?', 'shift', WEEK + 'downtime_minutes'),
    ('machine time', 'How many minutes of machine time this week?', 'shift', WEEK + 'downtime_minutes'),
    ('run time', 'Run time per stop this week', 'shift', WEEK + 'minutes_per_stop'),
    ('uptime', 'How many minutes of uptime this week?', 'shift', WEEK + 'downtime_minutes'),
    ('so far', 'How many stops so far this week?', 'shift', WEEK + 'stops'),
    ('so far', 'How many setups so far this month?', 'shift', '/corr/1/day/2026-09-14?measure=quantity&window=month&metric=setups'),
    ('until now', 'How many stops until now this week?', 'shift', WEEK + 'stops'),
    ('this shift', 'How many stops this shift this week?', 'day', WEEK + 'stops'),
    ('my shift', 'How many stops on my shift this week?', 'day', WEEK + 'stops'),
    ('our shift', 'How many minutes of downtime on our shift this week?', 'day', WEEK + 'downtime_minutes'),
]


# "On" stays in the table as the preposition it is everywhere ("on shift 1", "on 32-C", "on the stacker", "how did we do on setups"):
# its state sense, the line on, is a duration of running, refused by the duration gate before a word is read (agent.duration_gate)
PREPOSITIONS_KEPT = {'on'}


@pytest.mark.parametrize('word, question, scope, main_url', ADDED, ids=[f'{w}: {q}' for w, q, _, _ in ADDED])
def test_a_scope_plan_state_or_span_word_added_to_a_plain_question_refuses(word, question, scope, main_url):
    """Issue #153 (rows 1, 4, 5 and 8): the word is refused by name, never read as the plain quantity it was added to, and the runner
    fails the plain quantity's address for it. A single word of them is out of the function-word table, "on" the preposition aside."""
    url, refusal, _ = landing(question, scope=scope)
    assert url is None and word.split()[-1] in refusal, (question, url, refusal)
    assert ' ' in word or word in PREPOSITIONS_KEPT or word not in FUNCTION_WORDS, word   # a phrase ("so far", "run time") is refused as one
    assert catch_all_failures(question, main_url), (question, main_url)


def test_the_words_that_left_the_table_are_named_by_the_refusals():
    """Each word that left the table belongs to a family a refusal names (a plan, the line running, a span to date), never "a word one
    quantity does not read", and none is a function word."""
    for family, words in (('plan', agent.PLAN_WORDS), ('state', agent.STATE_WORDS), ('span', agent.SPAN_WORDS)):
        assert set(words) <= set(LEFTOVER_FAMILIES[family]) and not set(words) & FUNCTION_WORDS, family
    for word in ('can', 'could', 'would', 'will', 'need', 'want', 'to', 'go', 'went', 'up', 'so', 'far'):
        assert word not in FUNCTION_WORDS and any(word in members for members in LEFTOVER_FAMILIES.values()), word


def test_the_runner_catches_the_hotfix_rows_wrong_answers():
    """evals/run.py, held to the addresses main (3cefa4d) gave the issue's rows: each fails on its own; and the right answers pass."""
    pareto = '/corr/1/day/2026-09-14?measure=pareto&window=week'
    for question, url in (
            ('How many stops last week versus the week before?', WEEK + 'stops'),                          # row 2
            ('How many stops this week versus last month?', '/corr/1/day/2026-09-14?measure=quantity&window=month&metric=stops'),
            ('How many setups this week versus last month?', '/corr/1/day/2026-09-14?measure=quantity&window=month&metric=setups'),
            ('How many stops in August versus last month?', '/corr/1/day/2026-09-14?measure=quantity&window=2026-08&metric=stops'),
            ('How many brief stops by reason this week?', pareto + '&unit=count'),                         # row 3
            ('How many stops under 2 minutes at each place this week?', pareto + '&by=place'),
            ('How many planned stops by reason this week?', pareto + '&unit=count'),
            ('How many long stops at each place this week?', pareto + '&by=place&unit=count'),
            ('How many slitter stops by reason this week?', pareto + '&unit=count'),
            ('How many stops at night by reason this week?', pareto + '&unit=count'),
            ('How many sheets this week versus last week?', WEEK + 'sheets'),                               # row 6
            ('Stops per day this week versus last week', '/corr/1/day/2026-09-14?measure=stops&window=week'),
            ('Downtime per day this week compared with last week', WEEK + 'downtime_per_day'),
            ('How many setups on 32-C this week versus last week?', WEEK + 'setups&grade=32-C'),
            ('How many stops and why this week?', '/corr/1/day/2026-09-14/shift/2?measure=pareto&window=week&unit=count'),   # row 7
            ('How many stops did the operators log this week?', WEEK + 'operator_stops'),                  # row 9
            ('How many stops were logged by the operator this week?', WEEK + 'operator_stops')):
        assert catch_all_failures(question, url), (question, url)
    for question, url in (('How many stops during the week?', WEEK + 'stops'), ('How many stops over the week?', WEEK + 'stops'),   # row 10
                          ('How many stops across the week?', WEEK + 'stops'), ('I need the number of stops this week', WEEK + 'stops'),
                          ('Can you tell me how many setups we ran this week?', WEEK + 'setups'),
                          ('How many stops did we rack up this week?', WEEK + 'stops'),
                          ('Compare stops this week to last week', WEEK + 'stops'), ('How many stops this week and last week?', WEEK + 'stops'),
                          ('How many minutes went to maintenance this week?', WEEK + 'maintenance_minutes'),
                          ('How many operator stops were logged this week?', WEEK + 'operator_stops'),
                          ('How many stops in August versus the month before?', '/corr/1/day/2026-09-14?measure=quantity&window=2026-08&metric=stops'),
                          ('How many stops by reason this week?', pareto + '&unit=count'),
                          ('How many minutes of downtime at each place this week?', pareto + '&by=place'),
                          ('How many stops this shift this week?', '/corr/1/day/2026-09-14/shift/2?measure=quantity&window=week&metric=stops')):
        assert not catch_all_failures(question, url), (question, url, catch_all_failures(question, url))


@pytest.mark.parametrize('question, metric', [
    ('How many stops during the week?', 'stops'), ('How many stops across the week?', 'stops'), ('How many setups over the month?', 'setups'),
    ('Can you tell me how many setups we ran this week?', 'setups'), ('I want to know how many stops this week', 'stops'),
    ('Could you show me the number of stops this week?', 'stops'), ('How many stops did we rack up this week?', 'stops'),
    ('How many minutes went to maintenance this week?', 'maintenance_minutes'), ('How much time did we lose this week?', 'downtime_minutes'),
    ('How long were we down this week?', 'downtime_minutes'), ('How long did knife jams take this week?', 'downtime_minutes'),
    ('How many minutes of maintenance this week?', 'maintenance_minutes'), ('How much time per stop this week?', 'minutes_per_stop'),
    ('How many operator stops were logged this week?', 'operator_stops'), ('How many stops were caused by operators this week?', 'operator_stops'),
    ('How many stops this week compared to last week?', 'stops'), ('How many stops last week and the week before?', 'stops'),
    ('How many stops in August versus the month before?', 'stops'), ('How many stops in September versus last month?', 'stops'),
])
def test_what_the_hotfix_reads_still_answers(question, metric):
    """The words the hotfix reads where they are applied keep answering: a window after a preposition, a request said first, a particle,
    "went to" a filter, a duration that names downtime, a comparison with the window just before the answered one."""
    url, request, _ = landing(question)
    assert url and f'metric={metric}' in url, (question, url or request)
    assert not catch_all_failures(question, url), (question, url)
