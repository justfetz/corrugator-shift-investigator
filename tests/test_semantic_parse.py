"""The semantic model's parser (issue #144, phase 2 of #117): a question read into a SemanticQuery, or refused.

Three properties hold for every question: every content word of an answered question is one the reading used (the card's
"Read as" line states the reading, and the parser refuses rather than drop a word); an answer leads with the measure the
question asked; a refusal offers at most three questions, each of which the parser reads completely. The generated test
asks several hundred questions built from templates and the model's own vocabulary; the differential tests check that a
question's figure equals the existing tool's number for the same thing.
"""
import re
import time

import pytest

from investigator import semantic as S
from investigator import semantic_parse as P
from investigator.agent import Agent, Context, parse_request, question_scope, route
from investigator.record_store import default_store
from investigator.urls import canonical_url, resolve_url

DAY = '2026-09-14'
WEEK = {'period': 'week', 'anchor': DAY}


@pytest.fixture(scope='module')
def store():
    return default_store()


def reading(store, question, scope='day', shift=2, period='day', day=DAY):
    context = Context(day, shift, scope=scope, period=period)
    return P.read(question, question_scope(question, context, store.production_days, store), store, strict=True)


def answer(store, question, scope='shift', shift=2, period='day', day=DAY):
    """(request, context, result) as the server answers a typed question: the scope it names, the router, the agent."""
    context = Context(day, shift, scope=scope, period=period)
    request, asked = route(question, context, store.production_days, store)
    analysis = store.day_analysis(asked.production_day)
    try:
        return request, asked, Agent(analysis).ask(question, asked, request=request)
    finally:
        analysis.close()


# ------------------------------------------------------------------ what a question reads as

@pytest.mark.parametrize('question, measure, group_by, filters, rank', [
    ('Which shift had the most setups this week?', 'setups', ['shift'], {}, 'most'),
    ('What shift had the fewest setups this week?', 'setups', ['shift'], {}, 'least'),
    ('Which crew had the most stops this week', 'stops', ['crew'], {}, 'most'),
    ('How many stops by grade this week', 'stops', ['grade'], {}, None),
    ('Downtime per shift this week', 'downtime_minutes', ['shift'], {}, None),
    ('Stops by type this week', 'stops', ['kind'], {}, None),
    ('MSF by day this week', 'msf', ['day'], {}, None),
    ('which grade has the most warp', 'reject_sqft', ['grade'], {'reject_family': 'WARP'}, 'most'),
    ('downtime on the double backer', 'downtime_minutes', [], {'place': 'Double backer'}, None),
    ('Most common downtime reason this week', 'stops', ['reason'], {}, 'most'),
    ('Highest downtime day this week', 'downtime_minutes', ['day'], {}, 'most'),
    ('Which shift was the slowest this month', 'speed_run_fpm', ['shift'], {}, 'least'),
    ('How often were we down this week?', 'stops', [], {}, None),
    ('How often did we change paper this week?', 'paper_changes', [], {}, None),
    ('how much scrap did we make this week', 'reject_sqft', [], {}, None),
    ('How many minutes of planned downtime this week?', 'downtime_minutes', [], {'kind': 'Scheduled'}, None),
    ('Minutes per stop at the slitter this week', 'minutes_per_stop', [], {'place': 'Slitter/scorer'}, None),
    ('top 3 stop reasons by downtime this week', 'downtime_minutes', ['reason'], {}, 'most'),
])
def test_a_question_reads_into_the_models_terms(store, question, measure, group_by, filters, rank):
    r = reading(store, question)
    assert r.complete, (question, r.refusal, r.unused)
    assert (r.query.measure, r.query.group_by, r.query.filters, r.rank) == (measure, group_by, filters, rank), question


def test_the_window_and_the_shift_are_the_ones_the_question_names(store):
    assert reading(store, 'stops by shift this week').query.window == WEEK
    assert reading(store, 'stops by shift this month').query.window == {'period': 'month', 'anchor': DAY}
    assert reading(store, 'stops per day in August').query.window == {'period': 'month', 'anchor': '2026-08-01'}
    assert reading(store, 'stops by kind on shift 3 this week').query.shift == 3
    assert reading(store, 'stops by kind this week').query.shift is None              # a number with no shift named: every shift
    assert reading(store, 'stops by kind this week', scope='shift', shift=2).query.shift is None   # question_scope moved it
    assert reading(store, 'top 3 stop reasons by downtime this week').query.limit == 3
    assert reading(store, 'setups this week versus last week').query.compare == 'previous'


def test_a_share_is_the_values_share_of_the_split(store):
    r = reading(store, 'What percent of stops were at the stacker this week?')
    assert (r.query.measure, r.query.group_by, r.share, r.query.share) == ('stops', ['place'], 'Stacker', 'Stacker')
    r = reading(store, 'What share of stops were maintenance this week?')
    assert (r.query.group_by, r.share) == (['kind'], 'Maintenance')
    r = reading(store, 'How much of our downtime was maintenance this week')   # "how much of" is a share, never an ask
    assert (r.query.measure, r.query.group_by, r.share) == ('downtime_minutes', ['kind'], 'Maintenance')


def test_the_share_is_the_value_after_the_verb_and_a_value_beside_the_measure_narrows_the_whole(store):
    """Codex on PR #155: "what percent of maintenance stops were at the stacker" is the stacker's share of the maintenance stops,
    never maintenance's share of the stacker's; with two values and no verb between them the share asked is not said."""
    r = reading(store, 'What percent of maintenance stops were at the stacker this week?')
    assert (r.query.group_by, r.query.filters, r.share) == (['place'], {'kind': 'Maintenance'}, 'Stacker')
    r = reading(store, 'What percent of stops at the stacker were maintenance this week?')
    assert (r.query.group_by, r.query.filters, r.share) == (['kind'], {'place': 'Stacker'}, 'Maintenance')
    r = reading(store, 'What percent of rejects were warp on 32-C this month?')
    assert (r.query.group_by, r.query.filters, r.share) == (['reject_family'], {'grade': '32-C'}, 'WARP')
    assert not reading(store, 'What percent of maintenance stops at the stacker this week').complete


def test_a_share_of_a_split_leads_with_the_largest_share_and_says_share(store):
    """The review of PR #155, item 5: "percent of stops by kind" led with the count of stops and its Read as dropped the share."""
    for question, by in (('Percent of stops by kind this week', 'kind'), ('Share of stops each day this week', 'day'),
                         ('Percentage of downtime by place this week', 'place')):
        request, asked, result = answer(store, question)
        url = canonical_url(request, asked)
        assert f'by={by}&share=each' in url and request['lead'] == {'shares': True}, (question, url)
        rows = result['trace'][0]['data']['rows']
        top = max(range(len(rows)), key=lambda i: rows[i]['value'])
        head = result['sections'][0]
        assert re.match(rf'^{re.escape(rows[top]["label"])} was the largest share of the [a-z ]+ this week, [0-9.]+%: \*\*', head), head
        assert head.endswith(f'(query 1 · row {top})'), head
        assert next(s for s in result['sections'] if s.startswith('Read as: ')).endswith("each row's share of the total."), question


def test_a_share_leads_with_the_values_own_row_beyond_the_limit_and_without_records(store):
    """The review of PR #155, item 4: the share of a value with no records, or ranked below the rows listed, led with the total."""
    for question, label in (('What percent of stops were at the steam system this week?', 'Steam system'),
                            ('What share of dry end rejects was cracking this month?', 'Cracking board')):
        request, asked, result = answer(store, question)
        data = result['trace'][0]['data']
        row = data['rows'][data['share_row']]
        head = result['sections'][0]
        assert head.startswith(f'{label} was ') and f'(query 1 · row {data["share_row"]})' in head and row['label'] == label, head
        assert f'share={label.replace(" ", "+")}' in canonical_url(request, asked)
    assert '0.00% of the stops this week: **0 stops**' in answer(store, 'What percent of stops were at the steam system this week?')[2]['sections'][0]


@pytest.mark.parametrize('question, why', [
    ('which shift was worst last week', "'worst' does not say which way is better"),
    ('Who had the most stops this week', "'who' could be a shift or a crew"),
    ('Mean time to repair this week', "'mean' asks for a statistic"),
    ('Average stops per day this week', "'average' asks for a statistic"),
    ('square feet of trim and shear this week', 'are two measures'),
    ('How many glue stops this week', "'glue' is a place and a reason"),
    ('How many stops by splicer this week', 'splits by part of a dimension'),
    ('Biggest stop this week', 'A ranking needs what it ranks'),
    ('Stops per 1000 ft this week', 'is not a ratio the semantic model defines'),
    ('how many stops over 10 minutes this week', "is not a word the semantic model reads"),
    ('How many stops each day yesterday', 'needs a window longer than one'),
    ('which shift lost the most speed this week', 'Speed lost is a gap'),
])
def test_a_word_the_model_cannot_place_is_refused_with_at_most_three_questions(store, question, why):
    r = reading(store, question)
    assert not r.complete and why in r.refusal, (question, r.refusal)
    assert len(r.suggestions) <= 3
    text = P.refusal_text(r)
    assert text.count("'") >= 0 and len(re.findall(r"'[^']+'", text.split('Did you mean')[-1])) <= 3


def test_every_suggestion_is_a_question_the_parser_answers(store):
    """A "did you mean" never offers a question that is refused in its turn."""
    refused = ['which shift was worst last week', 'Who had the most stops this week', 'Mean time to repair this week',
               'Biggest stop this week', 'How many stops by splicer this week', 'Average downtime per shift this week',
               'Run rate this week', 'Best crew this week', 'Stops per 1000 ft this week', 'How many glue stops this week',
               'Which shift had the most stops on shift 2 this week', 'What portion of downtime was the stacker this week',
               'Daily setups this week', 'Worst shift last week', 'Median downtime yesterday']
    for question in refused:
        r = reading(store, question)
        assert r.suggestions, question
        for suggestion in r.suggestions:
            s = reading(store, suggestion)
            assert s.complete, (question, suggestion, s.refusal)


# ------------------------------------------------------------------ the router's use of it

def test_the_flagship_investigations_keep_their_routes(store):
    """Why, the review, the Pareto, the waste breakdown, the money, the setup matrix and the speed variance answer as before."""
    for question, tool in (('Why was this shift slow?', None), ('Show setup matrix', None),
                           ('Downtime by reason this week', 'get_downtime_review'),
                           ('Which downtime reason cost the most minutes this week?', 'get_downtime_pareto'),
                           ('Is it bond or warp this month?', 'get_waste_breakdown'),
                           ('Where is the money this month?', 'get_opportunities'),
                           ('What drove speed this week?', 'get_speed_variance'),
                           ('How many stops this week?', 'get_period_kpis')):
        request = parse_request(question, Context(DAY, 2, scope='day'), store)
        assert request['intent'] != 'semantic', question
        if tool:
            assert request['plan'][0]['tool'] == tool, question


def test_a_ranked_question_names_the_winner_and_keeps_the_ranking_in_its_address(store):
    request, asked, result = answer(store, 'Which shift had the most setups this week?')
    url = canonical_url(request, asked)
    assert url.startswith('/corr/1/day/2026-09-14?measure=semantic&m=setups&window=week&by=shift&rank=most'), url
    head = result['sections'][0]
    rows = result['trace'][0]['data']['rows']
    assert head.startswith(f'{rows[0]["label"]} had the most setups this week: **{rows[0]["value"]:,} setups**') and head.endswith('(query 1 · row 0)'), head
    assert rows == sorted(rows, key=lambda r: -r['value'])
    resolved = resolve_url(url, store)
    assert resolved['request']['plan'] == request['plan'] and resolved['request'].get('lead') == request.get('lead')


def test_the_read_as_line_is_the_same_for_the_question_and_its_address(store):
    """The card states what the semantic query read; the address alone reproduces it, so the two cards are one."""
    for question in ('Which shift had the most setups this week?', 'What percent of stops were at the stacker this week?',
                     'Stops by type this week', 'downtime on the double backer', 'Percent of stops by kind this week',
                     'What percent of stops were at the steam system this week?', 'Which place had the fewest stops this week?'):
        request, asked, result = answer(store, question)
        url = canonical_url(request, asked)
        resolved = resolve_url(url, store)
        analysis = store.day_analysis(resolved['day'])
        try:
            again = Agent(analysis).ask(resolved['text'], resolved['context'], request=resolved['request'])
        finally:
            analysis.close()
        read_as = [s for s in result['sections'] if s.startswith('Read as: ')]
        assert read_as and read_as == [s for s in again['sections'] if s.startswith('Read as: ')], question
        assert result['sections'][0] == again['sections'][0], question


def test_a_tie_names_every_winner_and_a_fewest_counts_the_values_with_no_records(store):
    """The review of PR #155, items 2 and 3: Crew B alone was named while Crew C had as many stops at the stacker; Power (one
    stop) was named the fewest while the steam system had none."""
    head = answer(store, 'Which crew had the most stops at the stacker this month?')[2]['sections'][0]
    assert re.match(r'^Crew [ABC] and Crew [ABC] tied for the most stops, stop place Stacker this month: \*\*51 stops\*\*', head), head
    head = answer(store, 'Which place had the fewest stops this week?')[2]['sections'][0]
    assert head.startswith('Steam system had the fewest stops this week: **0 stops**'), head
    from investigator.shapes import SemanticAnswer
    assert [SemanticAnswer.names(list(x)) for x in ('A', 'AB', 'ABC', 'ABCD')] == ['A', 'A and B', 'A, B and C', 'A, B, C and others']


def test_planned_maintenance_and_external_downtime_never_lead_a_ranking_of_stops(store):
    """The review of PR #155, item 7 (the owner, docs/METRICS.md): a ranking of stop reasons or kinds never leads with planned
    maintenance or external downtime. Its second review: the line names the rows it sets apart ("leaving out Planned maintenance and
    Power outage"), never "the unplanned" (a power outage is unplanned), and lists them after the ranking with their own figures, so
    the claim is true over every row it does not name; the runner holds it to that."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
    from run import ranked_failures
    for question in ('Top 3 downtime reasons this week', 'Least common stop reason this week', 'Which reason had the fewest stops this month?',
                     'Which kind had the fewest stops this week?', 'Most common downtime reason'):
        request, asked, result = answer(store, question)
        head, rows = result['sections'][0], result['trace'][0]['data']['rows']
        apart = sorted(r['label'] for r in rows if r['lever'] in ('schedule-driven', 'external'))
        assert 'unplanned' not in head and not re.match(r'^(Planned maintenance|Power outage|External)', head), head
        assert (f', leaving out {" and ".join(apart)}: **' in head) if apart else ('leaving out' not in head), (head, apart)
        assert not ranked_failures([head], result['trace']), (question, ranked_failures([head], result['trace']))
        if apart:
            sections = result['sections']
            at = sections.index('Left out of the ranking (planned maintenance and external downtime never lead)')
            assert sorted(re.match(r'^- (.+?): \*\*', b)[1] for b in sections[at + 1:at + 1 + len(apart)]) == apart, sections[at:at + 4]
    # the wording the review caught fails the runner: "the unplanned" leaves out a power outage it never names
    request, asked, result = answer(store, 'Least common stop reason this week')
    head = result['sections'][0]
    false = re.sub(r' this week, leaving out [^:]+:', ' of the unplanned stop reasons this week:', head)
    assert false != head and any('Power outage' in f for f in ranked_failures([false], result['trace'])), false


def test_two_values_compared_lead_with_both_and_keep_them_in_the_address(store):
    """The second review of PR #155, item 4: "scrap on shift 1 versus shift 2" and "setups on 32-C and 26-C" led with the total of
    every shift or grade. The card names both (the larger one's figure, cited), the two rows first, the others after them; the
    address carries them (vs=), and the runner fails a comparison its rows contradict."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
    from run import ranked_failures
    for question, scope, part, pattern in (
            ('How much scrap on shift 1 versus shift 2?', 'day', '&by=shift&vs=1,2', r'^Shift [12] had more dry-end rejects than Shift [12]: \*\*'),
            ('How many setups did we run on 32-C and 26-C last week?', 'shift', '&by=grade&vs=32-C,26-C', r'^(32-C|26-C) had more setups than (32-C|26-C) last week: \*\*'),
            ('Compare shift 1 and shift 3 on downtime this week', 'shift', '&by=shift&vs=1,3', r'^Shift [13] had more downtime than Shift [13] this week: \*\*')):
        request, asked, result = answer(store, question, scope=scope)
        url = canonical_url(request, asked)
        assert url.endswith(part), url
        head, rows = result['sections'][0], result['trace'][0]['data']['rows']
        assert re.match(pattern, head) and not ranked_failures([head], result['trace']), head
        bullets = [s for s in result['sections'] if s.startswith('- ') and '(query 1 · row ' in s]
        named = {r['label'] for r in rows if r['label'] in head}
        assert len(named) == 2 and {re.match(r'^- (.+?): ', b)[1] for b in bullets[:2]} == named, (head, bullets[:3])
        resolved = resolve_url(url, store)
        assert resolved['request'].get('lead') == request.get('lead'), (resolved['request'].get('lead'), request.get('lead'))
        swapped = re.sub(r'^(.+?) had more (.+?) than (.+?)( this week| last week)?: ', r'\3 had more \2 than \1\4: ', head)
        assert ranked_failures([swapped], result['trace']), swapped
    with pytest.raises(ValueError, match='vs= names two values'):
        resolve_url('/corr/1/day/2026-09-14?measure=semantic&m=stops&window=week&by=shift&vs=1', store)


def test_a_percent_of_the_shift_or_of_the_scheduled_time_is_never_a_share_of_the_downtime(store):
    """The second review of PR #155, items 2 and 3: "what percent of the shift was maintenance downtime" answered maintenance's
    share of the downtime, and "what percentage of scheduled time was operator downtime" was refused as two measures. A percent of
    the shift or of the scheduled time is downtime as a percent of the scheduled minutes: the hand router's answers (main's) stand."""
    for question, scope, url in (
            ('What percent of the shift was maintenance downtime?', 'shift', '/corr/1/day/2026-09-14/shift/2?measure=downtime&kind=maintenance'),
            ('What percent of the shift was operator downtime?', 'shift', '/corr/1/day/2026-09-14/shift/2?measure=downtime&kind=operator'),
            ('What percentage of scheduled time was operator downtime this week?', 'shift', '/corr/1/day/2026-09-14?measure=review&window=week'),
            ('Maintenance downtime as a percent of scheduled minutes this week', 'shift', '/corr/1/day/2026-09-14?measure=review&window=week'),
            ('What percent of the shift were we down?', 'shift', '/corr/1/day/2026-09-14/shift/2?measure=semantic&m=downtime_pct')):
        request, asked = route(question, Context(DAY, 2, scope=scope), store.production_days, store)
        assert canonical_url(request, asked) == url, (question, canonical_url(request, asked))
    r = reading(store, 'What percent of the shift was maintenance downtime?', scope='shift')
    assert (r.query.measure, r.query.filters, r.query.shift, r.share) == ('downtime_pct', {'kind': 'Maintenance'}, 2, None)
    assert reading(store, 'What percent of stops were at the stacker this week?').share == 'Stacker'   # a share of a split stays one


def test_a_moved_window_keeps_its_own_words_on_the_card_and_its_dates_in_the_read_as_line(store):
    """The review of PR #155, item 6: "last week" and "last month" read as "this week" and "this month"."""
    request, asked, result = answer(store, 'Which shift was the slowest last week?')
    assert ' last week: **' in result['sections'][0], result['sections'][0]
    assert 'Week 2026-09-07 to 2026-09-11 (Mon-Fri)' in next(s for s in result['sections'] if s.startswith('Read as: '))
    request, asked, result = answer(store, 'Setups by day last month')
    assert result['sections'][0].startswith('Setups last month: **')
    assert 'August 2026 (2026-08-01 to 2026-08-31)' in next(s for s in result['sections'] if s.startswith('Read as: '))
    r = reading(store, 'Worst shift last week')
    assert r.suggestions and all(s.endswith('last week') for s in r.suggestions), r.suggestions


def test_a_named_shift_beside_a_split_by_shift_is_refused_never_dropped(store):
    """The review of PR #155, item 8: "on shift 2" was dropped for the split by shift."""
    with pytest.raises(ValueError, match=r"'shift 2' names one shift beside a split by shift.*Did you mean"):
        route('Which shift had the most stops on shift 2 this week?', Context(DAY, 2, scope='shift'), store.production_days, store)
    assert reading(store, 'compare shift 1 and shift 2 stops this week').query.group_by == ['shift']   # two shifts compared still split


def test_per_day_of_setups_and_per_order_are_refused_as_ratios_never_split(store):
    """The review of PR #155, item 9: "daily setups" and "sheets per order" are ratios the records do not keep (the hand router's
    refusal, as on main); the stops and the downtime of each production day are split by day."""
    for question, why in (('Daily setups this week', 'Setups per day is not a ratio'), ('Setups per day this week', 'Setups per day is not a ratio'),
                          ('How many feet did we run per day this week?', 'Feet per day is not a ratio'),
                          ('How many sheets per order this week?', 'Sheets per order is not a ratio')):
        with pytest.raises(ValueError, match=why):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    assert 'per day is not a ratio the semantic model defines' in reading(store, 'Daily setups this week').refusal
    assert reading(store, 'Daily operator stops this week').query.group_by == ['day']
    assert reading(store, 'MSF per day by shift this week').query.measure == 'msf_per_day'


def test_bare_stops_and_the_most_common_reason_answer_every_shift(store):
    """The review of PR #155, item 1: "stops", "stops yesterday" and "most common downtime reason" answered shift 2's breakdown
    under the shift control; the owner's downtime default answers every shift (the review for bare stops, as on main; the count of
    stops by reason, every shift, for "most common")."""
    for question, url in (('Stops', '/corr/1/day/2026-09-14?measure=review'), ('Show me the stops', '/corr/1/day/2026-09-14?measure=review'),
                          ('Stops yesterday', '/corr/1/day/2026-09-11?measure=review'),
                          ('Most common downtime reason', '/corr/1/day/2026-09-14?measure=semantic&m=stops&by=reason&rank=most')):
        request, asked = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        assert canonical_url(request, asked) == url, question


def test_a_superlative_with_no_measure_or_a_statistic_is_refused_over_a_summary_or_the_review(store):
    for question, why in (('Worst shift', "'worst' does not say which way is better"), ('Which shift was best', "'best' does not say"),
                          ('Best crew', "'best' does not say"), ('Median downtime this week', "'median' asks for a statistic"),
                          ('What was our average downtime this week', "'average' asks for a statistic"),
                          ('What portion of downtime was the stacker this week', "'stacker' is a place and a reason.*Did you mean 'what percent of")):
        with pytest.raises(ValueError, match=why):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    assert "'which crew had the most" in P.refusal_text(reading(store, 'Best crew'))


def test_a_hand_refusal_the_model_cannot_read_keeps_its_own_text(store):
    """When neither reads the question, the hand router's refusal is the one shown (its tested wording stands)."""
    with pytest.raises(ValueError, match="asks for a subset"):
        parse_request('How many long stops this week?', Context(DAY, 2, scope='day'), store)


def test_questions_still_answer_in_tens_of_milliseconds(store):
    questions = ['Which shift had the most setups this week?', 'Stops by type this week', 'What percent of stops were at the stacker this week?',
                 'which grade has the most warp', 'Downtime per shift this week']
    for q in questions:
        reading(store, q)   # warm the vocabulary
    started = time.perf_counter()
    for q in questions * 4:
        route(q, Context(DAY, 2, scope='shift'), store.production_days, store)
    per_question = (time.perf_counter() - started) * 1000 / (len(questions) * 4)
    assert per_question < 50, per_question


# ------------------------------------------------------------------ generated: templates x the model's vocabulary

MEASURE_WORDS = {'stops': 'stops', 'downtime_minutes': 'downtime', 'setups': 'setups', 'msf': 'volume', 'reject_sqft': 'scrap',
                 'paper_changes': 'paper changes', 'trim_sqft': 'side trim', 'lineal_ft': 'lineal feet', 'order_changes': 'order changes'}
DIM_WORDS = {'shift': 'shift', 'crew': 'crew', 'grade': 'grade', 'day': 'day', 'reason': 'stop reason', 'place': 'place', 'kind': 'kind',
             'flute': 'flute', 'customer': 'customer'}
TEMPLATES = ('which {dim} had the most {m} this week', 'which {dim} had the fewest {m} this week', '{m} by {dim} this week',
             '{m} per {dim} this month', 'top 3 {dim}s by {m} this week', 'compare {dim}s on {m} this week',
             # the review of PR #155: moved windows, a share of each row, a named shift beside a split
             '{m} by {dim} last week', 'which {dim} had the most {m} last month', 'percent of {m} by {dim} this week',
             '{m} by {dim} yesterday', '{m} by {dim} on shift 3 this week')
VALUE_TEMPLATES = ('how many {m} on {value} this week', 'what percent of {m} were {value} this week', '{m} for {value} this month',
                   'what percent of {m} were {value} last week')


def generated(store):
    model = S.model()
    out = []
    for template in TEMPLATES:
        for measure, mword in MEASURE_WORDS.items():
            for dim, dword in DIM_WORDS.items():
                out.append((template.format(dim=dword, m=mword), measure, dim, None))
    for template in VALUE_TEMPLATES:
        for measure, mword in MEASURE_WORDS.items():
            for dim in ('grade', 'kind', 'crew', 'reason', 'reject_family'):
                for value in (S.store_values(dim, store) or [])[:2]:
                    label = (model.dimensions[dim].get('value_labels') or {}).get(value, value)
                    out.append((template.format(m=mword, value=label), measure, None, (dim, value)))
    return out


def test_the_generated_questions_are_several_hundred():
    assert len(generated(default_store())) >= 300


def test_every_generated_question_uses_every_content_word_or_is_refused(store):
    """Each answer's reading used every content word of the question (the Read as line states it), leads with the measure
    the question asked (the same quantity in the unit asked), splits by the dimension it named and filters by the value it
    named; a question the model cannot say is refused, never answered as something else."""
    model = S.model()
    answered = refused = 0
    for question, measure, dim, value in generated(store):
        r = reading(store, question)
        if not r.complete:
            refused += 1
            assert r.refusal and len(r.suggestions) <= 3, question
            continue
        answered += 1
        used = {t for words, _ in r.used for t in P.tokens_of(words)}
        content = [t for t in P.tokens_of(question) if t not in P.FILLER]
        assert all(t in used for t in content), (question, [t for t in content if t not in used], r.used)
        quantity = {r.query.measure, *S._parts(model, r.query.measure)}
        assert measure in quantity, (question, r.query.measure)
        per_day = dim == 'day' and ' per day' in question and model.measures[r.query.measure].get('denominator') == 'production_days'
        if dim and not per_day:   # "volume per day" is the model's own ratio, volume per production day
            assert dim in r.query.group_by, (question, r.query)
        if value:
            assert r.query.filters.get(value[0]) == value[1] or r.share == value[1], (question, r.query, r.share)
    assert answered > 200 and refused > 0, (answered, refused)


def expected_window(store, question):
    """The window label a question's own words name, worked out apart from the parser: the selected day's week or month, the one
    before ("last week", "last month"), the production day before ("yesterday"), or the selected day itself."""
    from datetime import date, timedelta
    day = date.fromisoformat(DAY)
    if 'last week' in question:
        return store._window({'period': 'week', 'anchor': (day - timedelta(days=7)).isoformat()})['label']
    if 'last month' in question:
        return store._window({'period': 'month', 'anchor': '2026-08-03'})['label']
    if 'yesterday' in question:
        return store._window({'period': 'day', 'anchor': max(d for d in store.production_days if d < DAY)})['label']
    for words, period in (('this week', 'week'), ('this month', 'month')):
        if words in question:
            return store._window({'period': period, 'anchor': DAY})['label']
    return store._window({'period': 'day', 'anchor': DAY})['label']


def read_as_failures(store, question, data, read_as, lead):
    """What a Read as line misdescribes or drops of the question's content words (the review of PR #155, item 8): the measure, the
    window by its dates (the words "this week", "last week", "yesterday" checked against the window they name), each split,
    each filter, the shift, the ranking's direction, "top N" and a share."""
    from investigator.shapes import GROUP_NOUNS
    lower = question.lower()
    bad = []
    noun = re.sub(r'\s*\(.*\)$', '', data['label']).lower()
    if not read_as.startswith(f'Read as: {noun}'):
        bad.append('measure')
    if f', {expected_window(store, lower)},' not in read_as:
        bad.append(f'window {expected_window(store, lower)!r}')
    for dim in data['group_by']:
        if GROUP_NOUNS.get(dim, dim.replace('_', ' ')) not in read_as.split(' by ', 1)[-1]:
            bad.append(f'split {dim}')
    bad += [f'filter {f["text"]}' for f in data['filters'] if f['text'] not in read_as]
    shift = re.search(r'\bshift ([123])\b', lower)
    if (f'shift {shift[1]}' if shift and 'shift' not in data['group_by'] else 'all shifts') not in read_as:
        bad.append('shift')
    if re.search(r'\b(most|top|highest|biggest|largest|fastest)\b', lower) and 'largest first' not in read_as:
        bad.append('most')
    if re.search(r'\b(fewest|least|lowest|smallest|slowest|bottom)\b', lower) and 'smallest first' not in read_as:
        bad.append('fewest')
    top = re.search(r'\btop (\d+)\b', lower)
    if top and int(top[1]) < data['groups'] and f'the first {top[1]}' not in read_as:
        bad.append('top N')
    if re.search(r'\b(percent(?:age)?|share|proportion|fraction|portion)\s+of\b', lower) and \
            not re.search(r"the share of |each row's share of the total", read_as):
        bad.append('share')
    return bad


def test_every_generated_answer_reads_as_it_was_asked_on_the_card(store):
    """On the card: the Read as line names the measure, the window by its dates (the question's own window words checked against
    them), each split, each filter, the shift, the direction of a ranking, "top N" and a share; the headline leads with the measure
    (the review of PR #155, item 8: Read as dropped share and percent, said "this week" for last week, and dropped a named shift)."""
    checked = 0
    for question, measure, dim, value in generated(store):
        r = reading(store, question)
        if not r.complete:
            continue
        request, asked, result = answer(store, question, scope='day')
        if request['intent'] != 'semantic':
            continue   # the hand route answers it already ("how many setups on 200-C": one quantity, #113)
        data = result['trace'][0]['data']
        read_as = next(s for s in result['sections'] if s.startswith('Read as: '))
        assert not read_as_failures(store, question, data, read_as, request.get('lead') or {}), (question, read_as,
                                                                                                read_as_failures(store, question, data, read_as, {}))
        head = result['sections'][0]
        noun = re.sub(r'\s*\(.*\)$', '', data['label']).lower()
        shared = request['plan'][0]['arguments'].get('share') is not None or (request.get('lead') or {}).get('shares')
        assert noun in head.lower() or shared, (question, head)
        checked += 1
    assert checked > 150, checked


# ------------------------------------------------------------------ numbers from the tools (#148's differential tests, through the parser)

def test_a_parsed_questions_figure_is_the_tools_number(store):
    """Through the parser, the semantic figure equals the period tools' own number for the same thing."""
    kpis = {r['metric']: r['current'] for r in store.get_period_kpis(WEEK, compare=False, shift=None)['rows']}
    for question, field in (('How often were we down this week?', 'stops'), ('How often did we change paper this week?', 'paper_changes'),
                            ('setups by shift this week', 'setups')):
        r = reading(store, question)
        result = S.run(r.query, store)
        assert result['value'] == kpis[field], (question, result['value'], kpis[field])
    pareto = {row['category']: row for row in store.get_downtime_pareto(WEEK, 'reason', 'count', None)['rows']}
    r = reading(store, 'Most common downtime reason this week')
    rows = S.run(r.query, store)['rows']
    assert rows[0]['reason'] == max(pareto, key=lambda c: (pareto[c]['stops'], c)) or rows[0]['value'] == max(p['stops'] for p in pareto.values())
    for row in rows:
        assert row['value'] == pareto[row['reason']]['stops'], row


def test_the_runner_states_the_ranking_and_the_share_on_the_address(store):
    """evals/run.py's own statement (issue #144): a ranked question's address keeps rank=, a share of one value keeps share=; each
    golden that ranks or asks a share passes it, and the unranked or share-less address fails."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
    from run import catch_all_failures
    for question in ('Which shift had the most setups this week?', 'What percent of stops were at the stacker this week?'):
        request, asked = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        url = canonical_url(request, asked)
        assert not catch_all_failures(question, url), (question, url)
        bare = re.sub(r'&(?:rank|share)=[^&]+', '', url)
        assert catch_all_failures(question, bare), (question, bare)


def test_the_runner_fails_the_selected_shifts_breakdown_a_tie_named_alone_and_a_fewest_that_is_not(store):
    """evals/run.py (the review of PR #155): a downtime question that names no shift, no day, no dollars and investigates nothing
    fails on the selected shift's breakdown (items 1); a ranked headline that names one winner of a tie, or a fewest that is not the
    fewest, fails (items 2 and 3); "per order" and "per day" of a count are ratios again (item 9: the loosening reverted)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
    from run import catch_all_failures, named_per, ranked_failures
    breakdown = '/corr/1/day/2026-09-14/shift/2?measure=downtime'
    for question in ('Stops', 'Show me the stops', 'Most common downtime reason'):
        assert catch_all_failures(question, breakdown), question
    for question in ('Downtime on shift two', 'What did downtime cost?', 'Downtime on Monday', 'Why so much downtime?', 'Downtime this day'):
        assert not catch_all_failures(question, breakdown), question
    assert not catch_all_failures('Stops', breakdown + '&by=place')
    _, _, result = answer(store, 'Which crew had the most stops at the stacker this month?')
    head, trace = result['sections'][0], result['trace']
    assert not ranked_failures([head], trace)
    rows = trace[0]['data']['rows']
    alone = re.sub(r'^Crew [ABC] and (Crew [ABC]) tied for', r'\1 had', head)
    assert ranked_failures([alone], trace) and 'named alone' in ranked_failures([alone], trace)[0]
    _, _, result = answer(store, 'Which place had the fewest stops this week?')
    rows, trace = result['trace'][0]['data']['rows'], result['trace']
    power = next(i for i, r in enumerate(rows) if r['label'] == 'Power')
    wrong = f'Power had the fewest stops this week: **1 stop** (query 1 · row {power})'
    assert not ranked_failures([result['sections'][0]], trace) and 'is named the fewest' in ranked_failures([wrong], trace)[0]
    assert named_per('How many sheets per order this week?') == r'\bsheets(?: per |/)order\b'
    assert named_per('Setups per day this week') == r'\bsetups(?: per |/)(?:production )?day\b'


def test_a_window_the_hand_route_would_answer_as_the_selected_day_is_refused(store):
    """Round 5 of the coordinator's notes on #144: "Tuesday", "last Monday", "this year", "in 2025", "over the weekend", "the day
    before", "the previous day" and a rolling window answered the selected day. The selected day's own weekday ("on Monday" on a
    Monday) and a weekday profile ("do Mondays stand out") keep their answers."""
    for question, said in (('Stops on Tuesday', 'tuesday'), ('Stops last Monday', 'last monday'), ('Stops this year', 'this year'),
                           ('Stops in 2025', 'in 2025'), ('Stops over the weekend', 'weekend'), ('Stops the day before', 'day before'),
                           ('Stops the previous day', 'previous day'), ('Downtime past two weeks', 'past two weeks'),
                           ('Which downtime reason cost the most minutes this year?', 'this year')):
        with pytest.raises(ValueError, match=f"'{said}' is not a window"):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    for question in ('Show downtime by reason on Monday', 'Stops per day this month: do Mondays stand out?'):
        route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
