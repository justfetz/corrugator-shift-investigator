"""The semantic model's parser (issue #144, phase 2 of #117): a question read into a SemanticQuery, or refused.

Three properties hold for every question: every content word of an answered question is one the reading used (the card's
"Read as" line states the reading, and the parser refuses rather than drop a word); an answer leads with the measure the
question asked; a refusal offers at most three questions, each of which the parser reads completely. The generated test
asks several hundred questions built from templates and the model's own vocabulary; the differential tests check that a
question's figure equals the existing tool's number for the same thing.
"""
import json
import re
import time
from pathlib import Path

import pytest

from investigator import agent
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
    # a loss or a reject said of what ran (the coordinator's review of PR #155 at 74cc46f, item 1)
    ('How many sheets did we lose this week', "'lose' asks what was lost"),
    ('Which grade lost the most MSF this week', "'lost' asks what was lost"),
    ('How many feet were rejected this week', "'rejected' names the dry-end rejects"),
    ('How many sheets were rejected this week', "'rejected' names the dry-end rejects"),
    ('Which grade took the most setups this week', "'took' is not a word the semantic model reads"),
    ('Which shift spent the most downtime this week', "'spent' is not a word the semantic model reads"),
    ('Which shift lost the most this week', "'lost' asks what was lost and names no measure"),
    ('Which grades had more setups than 26-C this week', "'than' compares with another value, excludes one"),
    ('Downtime for reasons other than the stacker this week', "'than' compares with another value, excludes one"),
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
               'Daily setups this week', 'Worst shift last week', 'Median downtime yesterday', 'How many sheets did we lose this week',
               'Which grade lost the most MSF this week', 'How many feet were rejected this week', 'Which day lost the most feet this week',
               'Which grade took the most setups this week', 'Which shift rejected the most this week', 'Which grades had more setups than 26-C this week']
    for question in refused:
        r = reading(store, question)
        assert r.suggestions, question
        for suggestion in r.suggestions:
            s = reading(store, suggestion)
            assert s.complete, (question, suggestion, s.refusal)


# ------------------------------------------------------------------ the router's use of it

def test_a_loss_verb_is_read_only_of_a_loss(store):
    """'lost' is read of downtime and the waste lines, 'rejected' of the dry-end rejects; beside what ran it is refused over the hand
    route, which answers what ran (the coordinator's review of PR #155 at 74cc46f, item 1)."""
    for question, measure in (('Which shift lost the most minutes this week', 'downtime_minutes'), ('How much trim did we lose this week', 'trim_sqft'),
                              ('How much scrap was rejected this week', 'reject_sqft'), ('Which grade lost the most downtime this week', 'downtime_minutes'),
                              ('Which shift produced the most MSF this week', 'msf'), ('How much did we lose to warp this week', 'reject_sqft')):
        r = reading(store, question)
        verbs = {'lost', 'lose', 'rejected', 'produced'}
        assert r.complete and r.query.measure == measure and verbs & set(dict(r.used)), (question, r.refusal, r.used)
    for question, why in (('How much MSF was rejected this week?', "'rejected' names the dry-end rejects"),
                          ('Which grade lost the most MSF this week?', "'lost' asks what was lost"),
                          ('Which shift lost the most sheets this week?', "'lost' asks what was lost")):
        with pytest.raises(ValueError, match=re.escape(why)):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)


def test_a_verb_the_parser_does_not_read_keeps_only_a_hand_answer_to_the_other_words(store):
    """"took", "experienced", "running": the hand route stands when it answers the question without the verb (the Pareto for "which
    reason took the most minutes", the review for "how much downtime did we experience"), else the verb is refused, never answered as
    something else (the reviewer's probe of this round: "which grade took the most setups" was the grade totals' MSF)."""
    for question, tool in (('Which reason took the most minutes this week?', 'get_downtime_pareto'),
                           ('How much downtime did we experience this week?', 'get_downtime_review')):
        request, _, _ = answer(store, question)
        assert request['plan'][0]['tool'] == tool, (question, request['plan'][0]['tool'])
    for question, word in (('Which grade took the most setups this week?', 'took'), ('Which shift experienced the most stops this week?', 'experienced'),
                           ('Which shift was running the fastest this week?', 'running'), ('How many minutes did each grade spend down this week?', 'spend')):
        with pytest.raises(ValueError, match=f"'{word}' is not a word the semantic model reads"):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)


def test_a_complete_reading_never_replaces_a_domain_refusal(store):
    """A hand refusal that gives a domain reason stands over a complete reading; one that names a structure or a word its one quantity
    does not carry is the parser's to answer (the coordinator's review of PR #155 at 74cc46f, item 1)."""
    sheets, ranked = reading(store, 'Sheets this week'), reading(store, 'Which shift had the most setups this week')
    assert sheets.complete and ranked.complete
    domain = ValueError("Rejects are recorded as square feet by reason, not as sheets (sheets only per reported sub-reason)")
    assert P.stands(domain, sheets) and not P.router_limit(domain)
    assert P.stands(ValueError('No tool returns the longest or shortest stop, setup or run'), sheets)
    limit = ValueError("'which' asks for a ranking or a comparison, which one quantity does not answer: one quantity is a window's total")
    assert P.router_limit(limit) and not P.stands(limit, ranked)
    # the owner-pending re-points: the semantic model reads the very measure or dimension the reason says is missing
    assert not P.stands(domain, reading(store, 'How many reject sheets yesterday?'))
    with pytest.raises(ValueError, match='recorded as square feet by reason'):
        parse_request('How many sheets were rejected this week?', question_scope('How many sheets were rejected this week?', Context(DAY, 2, scope='shift'),
                                                                                  store.production_days, store), store)
    # a noun the router does not count, read by the model in a longer phrase or as no count, overrules; a count of it does not
    for question, measure in (('Roll changes this week', 'paper_changes'), ('Good board this week', 'good_output_sqft'),
                              ('Stacker boards this week', 'stacker_waste_sqft'), ('How much chop this week?', 'shear_sqft'),
                              ('Gross square feet this week', 'gross_sqft'), ('which customer had the most scrap this week', 'reject_sqft')):
        request, _, _ = answer(store, question)
        assert request['plan'][0]['arguments']['measure'] == measure, (question, request['plan'][0])
    for question, why in (('How many chops this week?', 'not a count'), ('How many boards this week?', 'do not count boards')):
        with pytest.raises(ValueError, match=why):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)


def test_a_waste_answer_leads_with_the_line_asked(store):
    """The waste breakdown keeps "waste", a family or reason shown or compared and the line it opens with (side trim); a line it does not
    lead with is the semantic model's (the coordinator's review of PR #155 at 74cc46f, item 3), and so are a grade split of anything but
    MSF and a comparison the grade totals do not carry."""
    for question, tool in (('How much trim this week?', 'get_waste_breakdown'), ('Waste this week', 'get_waste_breakdown'),
                           ('Show warp this month', 'get_waste_breakdown'), ('Is it bond or warp this month?', 'get_waste_breakdown'),
                           ('How much shear this week?', S.TOOL), ('How much warp this week?', S.TOOL), ('Dry end rejects this week', S.TOOL),
                           ('How much bale this week?', S.TOOL), ('Volume by grade this week', 'get_grade_totals'),
                           ('Lineal feet per grade this week', S.TOOL), ('MSF this week versus last week', S.TOOL),
                           ('Bond this week', S.TOOL), ('Which reject family had the most rejects this week?', S.TOOL),
                           ('What did cracks cost this month?', 'get_waste_breakdown'), ('Elapsed speed by grade this week', S.TOOL),
                           ('Speed by grade this week versus last week', S.TOOL), ('fpm by grade', 'get_grade_totals'),
                           ('Downtime dollars this week', S.TOOL), ("What's trim costing us this month?", 'get_opportunities')):
        request, _, _ = answer(store, question)
        assert request['plan'][0]['tool'] == tool, (question, request['plan'][0]['tool'])

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
# the verbs a quantity is said with (the coordinator's review of PR #155 at 74cc46f, item 1): read where the measure is what they say
# ("which shift lost the most downtime", "how much scrap was rejected"), refused beside any other, never dropped
VERB_TEMPLATES = ('how many {m} did we lose this week', 'which {dim} lost the most {m} this week', 'how much {m} was rejected this week',
                  'which {dim} rejected the most {m} this week', 'how much {m} did we produce this week', 'which {dim} produced the most {m} this week',
                  'how many {m} did we spend this week', 'which {dim} took the most {m} this week', 'how many {m} did setups take this week',
                  'which {dim} was making the most {m} this week', 'how many {m} did we experience this week', 'how many {m} were stopped this week',
                  'which {dim} was running the most {m} this week', 'how much {m} were we losing this week')
# The words a template says that carry no content (agent.FUNCTION_WORDS and the copulas and question words the templates use), kept
# here apart from the parser's FILLER: a content word the parser drops fails the test even when the parser lists it as filler
NON_CONTENT = agent.FUNCTION_WORDS | frozenset(('had', 'were', 'was', 'did', 'do'))


def generated(store):
    model = S.model()
    out = []
    for template in TEMPLATES:
        for measure, mword in MEASURE_WORDS.items():
            for dim, dword in DIM_WORDS.items():
                out.append((template.format(dim=dword, m=mword), measure, dim, None))
    for template in VERB_TEMPLATES:
        for measure, mword in MEASURE_WORDS.items():
            for dim, dword in (DIM_WORDS.items() if '{dim}' in template else [(None, None)]):
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
        content = [t for t in P.tokens_of(question) if t not in NON_CONTENT]
        assert all(t in used for t in content), (question, [t for t in content if t not in used], r.used)
        quantity = {r.query.measure, *S._parts(model, r.query.measure)}
        assert measure in quantity, (question, r.query.measure)
        per_day = dim == 'day' and ' per day' in question and model.measures[r.query.measure].get('denominator') == 'production_days'
        if dim and not per_day:   # "volume per day" is the model's own ratio, volume per production day
            assert dim in r.query.group_by, (question, r.query)
        if value:
            assert r.query.filters.get(value[0]) == value[1] or r.share == value[1], (question, r.query, r.share)
    assert answered > 200 and refused > 0, (answered, refused)


def test_a_generated_verb_question_is_answered_only_as_asked_or_refused(store):
    """Through the router: a question with a verb (VERB_TEMPLATES) is refused, answered by the semantic model reading the verb, or
    answered by a hand route that covers what the question's other words ask (semantic_parse.covers), never by one that answers
    something else (the reviewer's probe of this round: "which grade took the most setups" answered with the grade totals' MSF)."""
    verbs = P.LOSS_VERBS + P.REJECT_VERBS + P.PRODUCE_VERBS + P.UNREAD_VERBS
    checked = 0
    for question, measure, dim, value in generated(store):
        said = [w for w in P.tokens_of(question) if w in verbs]
        if not said:
            continue
        try:
            request, context = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        except ValueError:
            continue
        checked += 1
        if request['intent'] == 'semantic':
            assert said[0] not in P.LOSS_VERBS + P.REJECT_VERBS or request['plan'][0]['arguments']['measure'] in P.LOSS_MEASURES + P.REJECT_MEASURES, question
            continue
        if request['intent'] in P.ALLOW_LISTED:
            continue
        rest = P.read(re.sub(rf"\b{said[0]}\b", ' ', question, count=1), context, store)
        if not rest.complete:
            continue   # the other words are refused on their own ("which customer lost the most downtime"): not the verb's to decide
        assert said[0] not in P.LOSS_VERBS + P.REJECT_VERBS or rest.query.measure in P.LOSS_MEASURES + P.REJECT_MEASURES, (question, request['intent'])
        assert P.covers(request, rest, context, store), (question, request['intent'], rest.query.to_arguments())
    assert checked > 100, checked


# Minutes or time said with a verb, and a loss to a value or a line (the coordinator's review of PR #155 at b955ca4, item 3): a refusal
# that fired before the verb was read let the grade totals answer "which grade took the most minutes" and the waste breakdown's side trim
# "which shift lost the most to warp". Each is refused, read with its verb, or answered by a hand route that covers the other words:
# never one whose other words the parser cannot read either
VERB_TIME_TEMPLATES = ('which {dim} took the most minutes this week', 'which {dim} took the most time this week',
                       'which {dim} spent the most minutes this week', 'which {dim} spent the most time this week',
                       'which {dim} spent the most time down this week', 'which {dim} lost the most time this week',
                       'which {dim} lost the most minutes this week', 'which {dim} produced the most minutes this week',
                       'which {dim} made the most minutes this week', 'which {dim} produced the most time this week',
                       # the reviewer's check of 0d23e4a: hours, the longest, a bare "stopped", and hours ranked with no verb
                       'which {dim} took the most hours this week', 'which {dim} took the longest this week',
                       'which {dim} stopped the most this week', 'which {dim} had the most hours this week',
                       'which {dim} had the most time down this week')
VERB_VALUE_TEMPLATES = ('which {dim} lost the most to {value} this week', 'which {dim} lost the most minutes to {value} this week',
                        'which {dim} lost the most feet to {value} this week', 'how much did we lose to {value} this week')
# what the value after "to" filters by, or the line it names; moisture's minutes are the stop reason Steam / moisture's
LOST_TO = {'warp': ('reject_family', 'WARP'), 'bond': ('reject_family', 'BOND'), 'moisture': ('reject_family', 'MOISTURE', 'Steam / moisture'),
           'misalignment': ('reject_family', 'MISALIGNMENT'), 'the stacker': ('reason', 'Stacker'), 'trim': (None, 'trim_sqft'),
           'side trim': (None, 'trim_sqft'), 'shear': (None, 'shear_sqft'), 'downtime': (None, 'downtime_minutes'),
           # a loss to what ran or to a speed is refused (the reviewer's check of 0d23e4a: side trim, the grade totals, one shift's speed)
           'changeovers': None, 'setups': None, 'speed': None, 'slow running': None}


def verb_questions():
    out = [(t.format(dim=d), ()) for t in VERB_TIME_TEMPLATES for d in DIM_WORDS.values()]
    for template in VERB_VALUE_TEMPLATES:
        for value, said in LOST_TO.items():
            for d in (DIM_WORDS.values() if '{dim}' in template else [None]):
                out.append((template.format(dim=d, value=value), said))
    return out


def test_a_verb_with_minutes_time_or_a_loss_to_a_value_is_answered_only_as_asked_or_refused(store):
    verbs = P.LOSS_VERBS + P.REJECT_VERBS + P.PRODUCE_VERBS + P.UNREAD_VERBS
    answered = refused = 0
    for question, said in verb_questions():
        verb = next((w for w in P.tokens_of(question) if w in verbs), None)
        try:
            request, context = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        except ValueError as exc:
            refused += 1
            hand = question_scope(question, Context(DAY, 2, scope='shift'), store.production_days, store)
            try:
                agent._parse_request(question, hand)
                stood = False
            except ValueError as own:
                stood = str(own) == str(exc)   # the hand router's own refusal stands ("the records do not count customers")
            if verb in P.UNREAD_VERBS and not stood:
                assert f"'{verb}'" in str(exc), (question, str(exc))   # else the refusal names the verb (docs/ASKING.md)
            continue
        answered += 1
        assert said is not None, (question, request['intent'])   # a loss to what ran or to a speed is refused
        if request['intent'] == 'semantic':
            args = request['plan'][0]['arguments']
            assert verb not in P.LOSS_VERBS or args['measure'] in P.LOSS_MEASURES, (question, args)
            assert verb not in P.UNREAD_VERBS + P.PRODUCE_VERBS, (question, args)
            if said and said[0]:
                assert set(args['filters'].values()) & set(said[1:]), (question, args)   # "lost the most to warp": the warp family's
            elif said:
                assert args['measure'] == said[1], (question, args)                # "lost the most to trim": side trim's
            continue
        if request['intent'] in P.ALLOW_LISTED:
            continue
        # a loss is read with its "to" ("how much did we lose to trim": the breakdown leads with trim); any other verb is not read
        rest = P.read(question if verb is None or verb in P.LOSS_VERBS else re.sub(rf"\b{verb}\b", ' ', question, count=1), context, store)
        assert rest.complete and P.covers(request, rest, context, store), (question, request['intent'])
    assert answered > 20 and refused > 50, (answered, refused)


def test_a_ranking_or_a_comparison_of_chops_or_boards_is_a_count_and_refused(store):
    """A ranking, a comparison or a 'vs' of the plural noun the records keep as square feet asks a count, refused with the records'
    reason as "how many shear chops" is, over any hand route (the coordinator's review of PR #155 at b955ca4, item 1). The waste lines'
    own names, a unit said, a split and the singular are the square feet."""
    templates = ('which {dim} had the most {n} this week', 'which {dim} had the fewest {n} this week', 'top 3 {dim}s by {n} this week',
                 'compare {dim}s on {n} this week', 'most {n} by {dim} this week', 'which {dim} had the most {n}', 'top 3 {dim}s by {n}')
    for noun, why in (('chops', 'keep shear chops as square feet, not a count'), ('shear chops', 'keep shear chops as square feet'),
                      ('stacker boards', 'do not count boards'), ('boards', 'do not count boards')):
        for template in templates:
            for dim in ('shift', 'grade', 'crew', 'day'):
                question = template.format(dim=dim, n=noun)
                with pytest.raises(ValueError, match=why):
                    route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        for question in (f'{noun} on shift 1 versus shift 2 this week', f'{noun} on shift 1 vs shift 2', f'{noun} this week versus last week',
                         f'compare {noun} on 32-C and 26-C this week'):
            with pytest.raises(ValueError, match=why):
                route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    # ... and when another refusal fires first (the reviewer's check of 0d23e4a): a place, a second measure, a word unread
    for question, why in (('Which shift had the most chops at the shear this week?', 'keep shear chops as square feet'),
                          ('Which shift had the most waste boards this week?', 'do not count boards'),
                          ('Which shift had the most reject boards this week?', 'do not count boards'),
                          ('Which shift had the most bad boards this week?', 'do not count boards'),
                          ('How many boards by grade this week?', 'do not count boards')):
        with pytest.raises(ValueError, match=why):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    for suggestion in reading(store, 'Which shift had the most chops this week?').suggestions:
        assert reading(store, suggestion).complete, suggestion   # each question offered reads in its turn
    for question, measure in (('Which shift had the most cutoff waste boards this week?', 'cutoff_waste_sqft'),
                              ('Which shift had the most knife waste boards this week?', 'cutoff_waste_sqft'),
                              ('Which shift had the most stacker waste boards this week?', 'stacker_waste_sqft'),
                              ('Which shift had the most square feet of chops this week?', 'shear_sqft'),
                              ('Which shift had the most chop this week?', 'shear_sqft'), ('Chops by shift this week', 'shear_sqft'),
                              ('Which shift had the most shear this week?', 'shear_sqft')):
        request, _, _ = answer(store, question)
        assert request['plan'][0]['arguments']['measure'] == measure, (question, request['plan'][0])


def test_a_line_said_with_waste_leads_with_that_line(store):
    """"<line> waste" is that line, and a family's waste is the family's rejects: only a bare "waste" is every line, the waste breakdown
    (the coordinator's review of PR #155 at b955ca4, item 2). "Moisture" is the reject family's own name and only a synonym of the stop
    reason Steam / moisture, so it reads as the family, as "warp" does."""
    for question, measure, filters in (('Dry end waste this week', 'reject_sqft', {}), ('Dry-end waste this week', 'reject_sqft', {}),
                                       ('Shear waste this week', 'shear_sqft', {}), ('Chop waste this week', 'shear_sqft', {}),
                                       ('Bale waste this week', 'bale_sqft', {}), ('Reject waste this week', 'reject_sqft', {}),
                                       ('Scrap waste this week', 'reject_sqft', {}), ('Knife waste this week', 'cutoff_waste_sqft', {}),
                                       ('Cutoff knife waste this week', 'cutoff_waste_sqft', {}), ('Admin waste this week', 'admin_sqft', {}),
                                       ('Warp waste this week', 'reject_sqft', {'reject_family': 'WARP'}),
                                       ('Bond waste this week', 'reject_sqft', {'reject_family': 'BOND'}),
                                       ('Misalignment waste this week', 'reject_sqft', {'reject_family': 'MISALIGNMENT'}),
                                       ('How much moisture this week?', 'reject_sqft', {'reject_family': 'MOISTURE'}),
                                       ('Moisture waste this week', 'reject_sqft', {'reject_family': 'MOISTURE'})):
        request, _, _ = answer(store, question)
        args = request['plan'][0]['arguments']
        assert request['plan'][0]['tool'] == S.TOOL and args['measure'] == measure and args['filters'] == filters, (question, request['plan'][0])
    for question in ('Waste this week', 'How much waste this week?', 'Show warp waste this month', 'Show moisture this month',
                     'Trim waste this week', 'Waste by reject family this week',
                     # a word the model does not hold before "waste" leaves every line (the reviewer's re-check of 87f0146)
                     "This week's waste", 'What is our main waste this week?', 'Excess waste this week', "Last month's waste"):
        request, _, _ = answer(store, question)
        assert request['plan'][0]['tool'] == 'get_waste_breakdown', (question, request['plan'][0])
    # the stop reason is still read where a stop is said or counted ("how often moisture": the reviewer's check of 0d23e4a)
    with pytest.raises(ValueError, match='^Offline mode answers one quantity'):   # a count: never the reject reason's square feet
        route('How many glue dam this week?', Context(DAY, 2, scope='shift'), store.production_days, store)
    for question in ('Moisture stops this week', 'How often moisture this week?', 'How many times moisture this week?'):
        request, _, _ = answer(store, question)
        assert request['plan'][0]['arguments']['filters'] == {'reason': 'Steam / moisture'}, (question, request['plan'][0])
    request, _, _ = answer(store, 'Baler waste this week')
    assert request['plan'][0]['arguments']['measure'] == 'bale_sqft'
    for question in ('Paper waste this week', 'Splice waste this week', 'Roll waste this week'):
        with pytest.raises(ValueError, match='cannot narrow it'):   # no line the model holds: never every line led by side trim
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)


def test_boards_alone_are_never_the_grades(store):
    """A bare "boards" is the sheets (the model's synonym), never the board grades said as a set: "which shift had the most cutoff waste
    boards" is the cutoff waste by shift, never by shift and grade (the coordinator's review of PR #155 at b955ca4, item 4)."""
    vocab = P.vocabulary(store)
    assert ('dimension', 'grade') not in vocab[('boards',)] and ('dimension', 'grade') in vocab[('board',)]
    assert vocab[('cutoff', 'waste', 'boards')] == [('measure', 'cutoff_waste_sqft')]
    r = reading(store, 'Which shift had the most cutoff waste boards this week?')
    assert r.complete and r.query.measure == 'cutoff_waste_sqft' and r.query.group_by == ['shift'], r
    request, _, _ = answer(store, 'MSF by board this week')
    assert request['plan'][0]['tool'] == 'get_grade_totals'   # the singular is still the grade


def test_a_verb_a_refusal_fired_before_is_still_refused_or_covered(store):
    """The verb's cue is set even when another refusal fires first (the coordinator's review of PR #155 at b955ca4, item 3), so route
    refuses naming it rather than let a hand route answer something else; a hand route that answers the other words keeps them."""
    for question, why in (('Which grade took the most minutes this week?', "'took' is not a word"),
                          ('Which grade spent the most time down this week?', "'spent' is not a word"),
                          ('Which grade took the most time this week?', "'took' is not a word"),
                          ('Which grade produced the most minutes this week?', "'produced' is read only of what ran"),
                          ('Which grade lost the most time this week?', 'names no measure'),
                          ('Which shift lost the most feet to warp this week?', 'not recorded by reject family')):
        with pytest.raises(ValueError, match=why):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    for question, tool in (('Which reason took the most minutes this week?', 'get_downtime_pareto'),
                           ('Which place took the most minutes this week?', 'get_downtime_pareto'),
                           ('how fast are we running between changeovers', None), ('Which grade were we running the most this month', 'get_grade_totals'),
                           ('What did slow running cost us this week?', 'get_opportunities')):
        request, _, _ = answer(store, question)
        plan = request.get('plan') or []
        assert (plan[0]['tool'] if plan else None) == tool or tool is None and request['intent'] != 'semantic', (question, request['intent'], plan[:1])


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
    top = re.search(r'\b(top|bottom) (\d+)\b', lower)
    if top and int(top[2]) < data['groups'] and f'{top[1]} {top[2]}' not in read_as:   # issue #185: a ranking of N says "top N"
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
        try:
            request, asked, result = answer(store, question, scope='day')
        except ValueError as exc:
            # a domain reason the hand router gave stands over a complete reading ("what percent of scrap were Bond": the waste lines
            # are kept as a percent of gross; the review of PR #155 at 74cc46f, item 1), never a structure or a word it does not read
            assert not P.router_limit(exc), (question, str(exc)[:120])
            continue
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


# ------------------------------------------------------------------ #159's refusals stand (the coordinator on PR #155, 2026-11-14)
GOLDEN_CASES = json.loads((Path(__file__).resolve().parent.parent / 'evals' / 'golden.json').read_text())['cases']
# Every refusal golden #159 (issue #153) added: words the gate consumed but no route applied. Each id starts "unapplied-".
UNAPPLIED_REFUSALS = [c for c in GOLDEN_CASES if c['id'].startswith('unapplied-') and 'raises' in c['expect']]


def golden_context(case):
    return Context(case.get('day', DAY), case.get('shift', 2), scope=case.get('scope', 'shift'), period=case.get('period', 'day'))


@pytest.mark.parametrize('case', UNAPPLIED_REFUSALS, ids=[c['id'] for c in UNAPPLIED_REFUSALS])
def test_every_refusal_of_issue_153_stands_through_the_full_route_with_the_parser_on(store, case):
    """Each refusal golden of #159 through the server's whole path: question_scope, the hand router, then the semantic model's parser
    on a store it reads (the parser on, never the single-day fixture's store=False). The refusal is the golden's, word for word:
    two windows, "during the day" beside another window, a hedge, a plan, a capacity or "so far", the line running, "this shift" under
    All, who logged a stop, "this day" or "the period" under a ranged Period control, a comparison the answer does not carry."""
    with pytest.raises(ValueError) as refused:
        route(case['question'], golden_context(case), store.production_days, store)
    assert re.search(case['expect']['raises'], str(refused.value), re.I), (case['question'], str(refused.value))


def test_the_parser_reads_some_of_them_completely_and_the_hand_refusal_stands(store):
    """The test above is not vacuous: the parser reads some of #159's refused questions completely ("how many planned stops by reason
    this week" is a filtered split it could answer), and only agent.Standing keeps the hand router's refusal. The rest it refuses on its
    own: two windows, "this shift" under All and "logged by" are refused by the parser too, so no hand route is needed to stop them."""
    assert len(UNAPPLIED_REFUSALS) > 50
    complete = []
    for case in UNAPPLIED_REFUSALS:
        moved = question_scope(case['question'], golden_context(case), store.production_days, store)
        if P.read(case['question'], moved, store, strict=True).complete:
            complete.append(case['id'])
            with pytest.raises(agent.Standing):
                agent._parse_request(case['question'], moved)
    assert 'unapplied-planned-stops-by-reason-refused' in complete and 'unapplied-sheets-versus-last-week-refused' in complete, complete
    for question, scope, period, why in (
            ('Which shift had the most stops this week versus last month?', 'shift', 'day', 'windows of different lengths'),
            ('Which shift had the most stops in August versus last month?', 'shift', 'day', "the month before the selected day's"),
            ('Which grade had the most setups last week versus the week before?', 'shift', 'day', 'two windows before the selected one'),
            ('Which shift had the most stops logged by operators this week?', 'shift', 'day', 'who logged the stops'),
            ('Setups by grade this shift this week', 'day', 'day', 'no shift is selected'),
            ('Which shift had the most stops for the period?', 'shift', 'week', 'could be the 4-4-5 accounting period'),
            ('Which shift had the most stops this day?', 'shift', 'week', 'names the selected production day')):
        context = Context(DAY, 2, scope=scope, period=period)
        r = P.read(question, question_scope(question, context, store.production_days, store), store, strict=True)
        assert not r.complete and why in r.refusal, (question, r.refusal)
        for suggestion in r.suggestions:
            s = reading(store, suggestion, period=period)
            assert s.complete, (question, suggestion, s.refusal)
        with pytest.raises(ValueError, match=re.escape(why)):
            route(question, context, store.production_days, store)


def test_the_parser_reads_the_routers_request_phrases_and_no_moved_function_word(store):
    """#159 took can, could, would, will, need, want, to, go, up, went and far out of agent.FUNCTION_WORDS (a plan, a capacity, the line
    running or a span to date), and the parser's filler is built from that table. A request said around the question is the router's own
    phrase (REQUEST_LEAD, MODAL_YOU, DID_HAVE_TO, UP_PARTICLE) and is read; outside one each of those words is unread, never dropped."""
    moved = ('can', 'could', 'would', 'will', 'need', 'want', 'to', 'go', 'up', 'went', 'far')
    assert not set(moved) & agent.FUNCTION_WORDS and not set(moved) & P.FILLER
    for question in ('Could you tell me which shift had the most stops this week?', 'I want to know which grade had the most setups this month',
                     'Which shift racked up the most stops this week?', 'Can you show me which shift had the most stops this week?',
                     'Compare shift 1 to shift 2 on stops this week'):
        assert reading(store, question).complete, question
    for question in ('Which shift can run the most setups this week?', 'Which grade will run the most setups this week?',
                     'Which shift had the most stops so far this week?', 'Which shift was up the most this week?',
                     'Which crew needs the most setups this week?', 'Which shift had the most setups to go this week?',
                     'Which shift went down the most this week?', 'Which shift is going to have the most setups this week?',
                     'Which grade are we going to run the most setups on this week?',
                     'Compare setups we have to run on 32-C to 26-C this week', 'Compare setups to be run on 32-C to 26-C this week'):
        assert not reading(store, question).complete, question


def test_a_standing_refusal_and_who_logged_reach_no_further_than_issue_153(store):
    """The reviewer's probe of this round: beside the Pareto grouped a stop reason, place or kind stands ("planned stops by reason"), and
    so does a comparison, which #159 pins; a second split is the parser's. "Logged by" is refused over a hand route only
    when a stop is counted and the words are a verb: "maintenance report", "the operator notes" keep the hand router's answer."""
    for question in ('How many stops by reason by shift this week?', 'Minutes of downtime by place for each shift this week',
                     'Stops by kind by shift this week'):
        request, _ = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        assert request['intent'] == 'semantic', question
    for question in ('How many stops by reason this week compared with last week?', 'How many stops by kind this month versus last month?'):
        with pytest.raises(ValueError, match='does not carry beside it'):   # #159 pins it (tests/test_quantity_questions.py)
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    for question in ('Maintenance report this week', 'Show the operator notes for this week', 'What did maintenance report this week?',
                     'Downtime by reason, maintenance notes this week'):
        context = Context(DAY, 2, scope='shift')
        moved = question_scope(question, context, store.production_days, store)
        hand = parse_request(question, moved, False)   # store False: the hand router alone
        request, _ = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        assert request['intent'] == hand['intent'] and request.get('plan') == hand.get('plan'), question
    for question in ('Which shift had the most stops logged by operators this week?', 'Stops the maintenance crew logged this week'):
        with pytest.raises(ValueError, match='who logged the stops'):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)


def test_a_bare_dimension_before_the_measure_beside_a_ranking_is_no_second_split(store):
    """A dimension said bare before the measure ("board stops", "wet end rejects", "cutoff knife rejects"), with no value and no split
    word of its own, beside a ranking of another dimension, is no split by both: refused over every hand route with the ranking alone
    and the split by both, each of which reads (the coordinator's review of PR #155 at 7b46463, item 1: never "Shift 1 · 275-BC")."""
    templates = ('which {o} had the most {d} {m} this week', 'which {o} had the fewest {d} {m} this week', 'top 3 {o}s by {d} {m} this week',
                 'the {o} with the most {d} {m} this week', 'which {o} had the most {d} {m}')
    for other in ('shift', 'crew', 'day'):
        for dim in ('board', 'grade', 'quality', 'wet end', 'width', 'flute', 'lane', 'job', 'cutoff knife'):
            for measure in ('stops', 'setups', 'rejects', 'waste', 'trim', 'scrap', 'shear'):
                for template in templates:
                    question = template.format(o=other, d=dim, m=measure)
                    if other == 'day' and not question.endswith('this week'):
                        continue
                    try:
                        request, _ = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
                    except ValueError:
                        continue
                    groups = (request.get('plan') or [{}])[0].get('arguments', {}).get('group_by') or []
                    assert not (isinstance(groups, list) and len(groups) > 1), (question, groups)
    for question in ('Which shift had the most board stops this week?', 'Which crew had the most board waste this week?',
                     'Which grade had the most crew stops this week?', 'Top 3 shifts by board stops this week'):
        r = reading(store, question)
        assert 'modifier' in r.cues and len(r.suggestions) == 2, (question, r.refusal)
    # every question a refusal offers answers, and the split by both answers as that split (the reviewer's check of 2ec98b5: "stops by
    # shift and order", "side trim by shift and customer" were offered); with no split to offer, the model's reason or the ranking alone
    for other in ('shift', 'crew', 'grade'):
        for dim in ('board', 'wet end', 'width', 'flute', 'lane', 'job', 'crew', 'cutoff knife', 'reason', 'place', 'order', 'customer'):
            for measure in ('stops', 'setups', 'rejects', 'trim', 'downtime', 'MSF'):
                question = f'which {other} had the most {dim} {measure} this week'
                r = reading(store, question)
                if 'modifier' not in r.cues:
                    continue
                for suggestion in r.suggestions:
                    request, _ = route(suggestion, Context(DAY, 2, scope='shift'), store.production_days, store)
                    if ' and ' in suggestion:
                        assert request['plan'][0]['arguments']['group_by'] == [P.tokens_of(other)[0], request['plan'][0]['arguments']['group_by'][1]], (question, suggestion)
                if len(r.suggestions) < 2:
                    assert re.search(r"(is not recorded by|so it is no split by) .*: ask the ranking$", r.refusal), (question, r.refusal)
    with pytest.raises(ValueError, match=r"and stops is not recorded by order: ask the ranking\. Did you mean 'which shift had the most stops this week'\?$"):
        route('Which shift had the most order stops this week?', Context(DAY, 2, scope='shift'), store.production_days, store)
    # a split word, a value, a plural or the same dimension keep their reading
    for question, groups in (('Which shift had the most stops by grade this week?', ['shift', 'grade']),
                             ('Which shift had the most grade 26-C stops this week?', ['shift']),
                             ('Which grade had the most board stops this week?', ['grade']),
                             ('Most common stop reason by shift this week', ['reason', 'shift']),
                             ('Which grade had the most stops this week?', ['grade'])):
        r = reading(store, question)
        assert r.complete and r.query.group_by == groups, (question, r.refusal)


def test_two_dimensions_joined_by_and_are_one_split(store):
    """"By shift and grade", "which shift and grade": one split by both, as "by shift by grade" is (the split the refusal above
    offers); never "'grade' is not a word the semantic model reads", the reason Pareto or the waste breakdown."""
    for question, measure, groups in (('Stops by shift and grade this week', 'stops', ['shift', 'grade']),
                                      ('Which shift and grade had the most stops this week?', 'stops', ['shift', 'grade']),
                                      ('Dry-end rejects by shift and grade this week', 'reject_sqft', ['shift', 'grade']),
                                      ('Setups by grade and crew this week', 'setups', ['grade', 'crew']),
                                      ('Downtime by reason and shift this week', 'downtime_minutes', ['reason', 'shift'])):
        request, _ = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        args = request['plan'][0]['arguments']
        assert (args['measure'], args['group_by']) == (measure, groups), (question, args)
    for question in ('Stops by shift or grade this week', 'Which shift or grade had the most stops this week?'):
        # "or" asks one or the other, never the split by both
        try:
            request, _ = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        except ValueError:
            continue
        groups = (request.get('plan') or [{}])[0].get('arguments', {}).get('group_by') or []
        assert not (isinstance(groups, list) and len(groups) > 1), (question, groups)


def test_a_line_said_with_waste_is_refused_over_every_hand_route(store):
    """"How much paper waste", "how much paper did we waste": no line the model holds, refused over the rollstock answer as over the
    waste breakdown (the coordinator's review of PR #155 at 7b46463, item 2), and the refusal names the stop reason, never "reason":
    the dry-end rejects are recorded by reject reason (item 3). A line's own name keeps its line."""
    for question, value in (('How much paper waste this week?', 'paper'), ('How much paper did we waste this week?', 'paper'),
                            ('Paper waste this week', 'paper'), ('Splice waste this week', 'splice'), ('How much paper was wasted this week?', 'paper'),
                            ('How much paper did shift 1 waste this week?', 'paper'), ('How much paper we wasted this week', 'paper'),
                            ('Which shift had the most knife rejects this week?', 'knife')):
        with pytest.raises(ValueError, match=f"^Dry-end rejects is not recorded by stop reason, so '{value}' cannot narrow it"):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    for question, measure in (('How much trim did we waste this week?', None), ('How much paper did we use this week?', None),
                              ('How much did we waste this week?', None), ('How much shear did we waste this week?', 'shear_sqft'),
                              ('How much stacker did we waste this week?', 'stacker_waste_sqft'),
                              ('What changeover did we waste this week?', 'changeover_sqft'),
                              ('How much shear did crew A waste this week?', 'shear_sqft'), ('How much shear was wasted this week?', 'shear_sqft')):
        # "waste" said as the verb of a line is that line's waste, as "shear waste" is; a bare "how much did we waste" is every line
        request, _ = route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        got = request['plan'][0]['arguments'].get('measure') if request['intent'] == 'semantic' else None
        assert got == measure, (question, request['intent'], got)


def test_the_shift_before_a_ranking_ranks_the_shifts(store):
    """"The shift with the most stops" ranks the shifts; "the shift" is the selected one only where nothing ranks it (this round's
    probe: "the shift with the most board rejects" was read as the selected shift's grades ranked, "200-C ..., shift 2")."""
    for question, measure in (('The shift with the most stops this week', 'stops'), ('The shift that had the most downtime this week', 'downtime_minutes')):
        r = reading(store, question)
        assert r.complete and r.query.measure == measure and r.query.group_by == ['shift'] and r.rank == 'most', (question, r.refusal)
    assert 'modifier' in reading(store, 'The shift with the most board rejects this week').cues
    for question in ('Stops on the shift this week', 'My shift with the most stops this week'):
        r = reading(store, question, scope='shift')   # "my shift" is the selected one, ranked or not
        assert 'shift' not in (r.query.group_by if r.query else []) and (r.query is None or r.query.shift == 2), (question, r.refusal)


def test_boards_said_as_a_dimension_offer_the_grade(store):
    """"Which boards had the most downtime", "downtime by boards", "which boards were slowest": another measure is named, so "boards" is
    said as a dimension, which it is not; the grade is offered. A count of the boards keeps the records' reason, and "how many boards
    per setup" main's ratio reason (the coordinator's review of PR #155 at 7b46463, item 4)."""
    for question, first in (('Which boards had the most downtime this week?', 'which grade had the most minutes of downtime this week'),
                            ('Which boards had the most rejects this week?', 'which grade had the most dry-end rejects this week'),
                            ('Which boards were slowest this week?', 'which grade was the slowest this week'),
                            ('Which boards had the most stops this week?', 'which grade had the most stops this week'),
                            ('Downtime by boards this week', 'minutes of downtime by grade this week')):
        with pytest.raises(ValueError, match=rf"^'boards' is not a dimension the semantic model reads.*Did you mean '{first}'"):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        r = reading(store, question)
        for suggestion in r.suggestions:
            assert reading(store, suggestion).complete, (question, suggestion)
    for question, why in (('How many boards per setup this week?', '^Boards per setup is not a ratio the records keep'),
                          ('How many boards per day this week?', '^Boards per day is not a ratio the records keep'),
                          ('Compare boards on downtime this week', "^'boards' is not a dimension"),
                          ('Which boards took the most minutes this week?', "^'boards' is not a dimension.*'which grade had the most run minutes"),
                          ('How much rollstock waste this week?', "^'rollstock' is not a word")):
        with pytest.raises(ValueError, match=why):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    for question in ('Which shift had the most boards this week?', 'How many boards by grade this week?', 'How many boards per grade this week?',
                     'How many stacker boards per grade this week?'):
        with pytest.raises(ValueError, match='do not count boards'):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)


def test_a_duration_asked_suggests_the_models_minutes(store):
    """"Which grade took / ran the most minutes" is offered the run minutes by grade, and "which stop reason took the most time" the
    Pareto's minutes of downtime: the model's own readings of a duration, never stops or setups (the coordinator's review of PR #155 at
    7b46463, item 5)."""
    for question, first in (('Which grade took the most minutes this week?', 'which grade had the most run minutes this week'),
                            ('Which grade ran the most minutes this week?', 'which grade had the most run minutes this week'),
                            ('Which grade had the most hours this week?', 'which grade had the most run minutes this week'),
                            ('Which stop reason took the most time this week?', 'which reason had the most minutes of downtime this week'),
                            ('Which grade took the most setup time this week?', 'which grade had the most setup minutes this week')):
        with pytest.raises(ValueError, match=f"Did you mean '{first}'"):
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
    request, _ = route('Which grade had the most run minutes this week?', Context(DAY, 2, scope='shift'), store.production_days, store)
    assert request['plan'][0]['arguments']['measure'] == 'running_minutes'
    request, _ = route('Which reason had the most minutes of downtime this week?', Context(DAY, 2, scope='shift'), store.production_days, store)
    assert request['plan'][0]['tool'] == 'get_downtime_pareto'


def _offered(text):
    """The questions a refusal offers: the quoted ones after "Did you mean"."""
    return re.findall(r"'([^']+)'", text.split('Did you mean', 1)[1]) if 'Did you mean' in text else []


def test_every_question_a_refusal_offers_is_answered_as_asked(store):
    """Each question these refusals offer is answered as that question, not just read completely: the semantic query of the measure
    it names, split and ranked as it says, or the grades' speed card for a speed by grade or the slowest grade, whose headline is the
    grade slowest against its target (issue #75). At 97b81f4, 29 refusals offered 'which grade was the fastest this week', which the card answered with the slowest grade,
    and the boards refusal offered 'bale by grade', which the grade totals answered (the coordinator's review of PR #155 at 97b81f4,
    items 1, 2 and 4)."""
    for question in ('Which grade had the most wet end speed this week?', 'Which boards were fastest this week?',
                     'Which boards were slowest this week?', 'Which boards had the most downtime this week?',
                     'Which boards had the most rejects this week?', 'Which boards had the most stops this week?', 'Downtime by boards this week',
                     'Which boards had the most bale this week?', 'Which boards had the most unexplained waste this week?',
                     'Which boards had the most scheduled minutes this week?', 'Bale by boards this week', 'Which boards had the most shear this week?',
                     'Shear by boards this week', 'Which boards had the most order changes this week?', 'Crew rejects by shift this week',
                     'Which shift had the most crew rejects this week?', 'Which boards took the most minutes this week?',
                     'Which boards had the most shear cost this week?', 'Which boards had the most order change stops this week?',
                     'Which boards had the most shear chops, priced this week?', 'Which boards had the highest line speed this week?',
                     'Which boards had the highest bale % this week?', 'Bale MSF by boards this week', 'Shear % by boards this week',
                     'Crew trim % by shift this week'):
        with pytest.raises(ValueError) as refused:
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        offered = _offered(str(refused.value))
        assert offered, (question, str(refused.value))
        for suggestion in offered:
            r = reading(store, suggestion)
            assert r.complete, (question, suggestion, r.refusal)
            request, _ = route(suggestion, Context(DAY, 2, scope='shift'), store.production_days, store)
            if request['intent'] == 'semantic':
                args = request['plan'][0]['arguments']
                assert (args['measure'], args['group_by'], (request.get('lead') or {}).get('rank')) == \
                    (r.query.measure, r.query.group_by, r.rank), (question, suggestion)
            elif request['intent'] in P.ALLOW_LISTED:
                # #113's quantity routes: one window's total, every word their own ("minutes of downtime this week")
                assert not r.query.group_by and r.rank is None, (question, suggestion, request['intent'])
            else:
                assert request['intent'] in ('period_speed', 'speed') and r.query.measure in P.SPEED_FAMILY and r.rank in (None, 'least'), \
                    (question, suggestion, request['intent'], r.rank)


def test_a_measure_with_no_grade_is_never_offered_by_grade(store):
    """"Which boards had the most bale" offers the shifts and gives the model's reason: the bale, the unexplained waste and the scheduled
    minutes are recorded per shift, which carries no grade, so "bale by grade" (the grade totals' MSF) is never offered (the coordinator's
    review of PR #155 at 97b81f4, item 2)."""
    for question, word in (('Which boards had the most bale this week?', 'bale'), ('Bale by boards this week', 'bale'),
                           ('Which boards had the most unexplained waste this week?', 'unexplained waste'),
                           ('Which boards had the most scheduled minutes this week?', 'scheduled minutes')):
        with pytest.raises(ValueError) as refused:
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        text = str(refused.value)
        assert f'{word} is recorded per shift, which carries no grade' in text, text
        assert _offered(text) and not any('grade' in s for s in _offered(text)), text


def test_a_word_that_is_also_a_dimension_the_measure_is_recorded_by_is_a_modifier(store):
    """"Crew rejects by shift": no value of "crew" (the Operator kind's synonym) narrows the dry-end rejects, but they are recorded by
    crew, so the word said bare before them beside the shifts split is _modifier's case, never "not recorded by stop kind" (the
    coordinator's review of PR #155 at 97b81f4, item 5). With nothing else split or ranked, and a measure "crew" does narrow, it stays
    as it was."""
    for question in ('Crew rejects by shift this week', 'Which shift had the most crew rejects this week?', 'Crew waste by shift this week'):
        r = reading(store, question)
        assert 'modifier' in r.cues and r.refusal.startswith("'crew' is said before"), (question, r.refusal)
        assert 'stop kind' not in r.refusal, (question, r.refusal)
    r = reading(store, 'Crew stops by shift this week')
    assert r.complete and r.query.filters == {'kind': 'Operator'} and r.query.group_by == ['shift'], r.refusal


def test_edge_trim_waste_is_side_trim(store):
    """"Edge trim waste" is a synonym of side trim, as "side trim waste" is, so "how much edge trim did we waste" reads as side trim,
    never two measures (the coordinator's review of PR #155 at 97b81f4, item 3)."""
    for question in ('How much edge trim did we waste this week?', 'How much edge trim was wasted this week?', 'Edge trim waste this week',
                     'How much edge trim waste this week?'):
        r = reading(store, question)
        assert r.complete and r.query.measure == 'trim_sqft', (question, r.refusal)


def test_a_suggestion_names_the_measure_the_question_asked(store):
    """Each question a refusal offers reads as the measure the refused question named, its unit included: the speed with stops is
    never offered as "which shift was the fastest" (the speed without stops), a percent or the bale in MSF never as the square feet
    its label shares ("bale %" is "bale %", never "bale"), and the priced shear as "shear cost" (the reviewer's check of this round)."""
    for question, measure in (('Which boards had the highest line speed this week?', 'speed_all_fpm'),
                              ('Which boards had the lowest speed with stops this week?', 'speed_all_fpm'),
                              ('Which boards had the most elapsed speed this week?', 'speed_elapsed_fpm'),
                              ('Which grade had the most wet end speed this week?', 'speed_run_fpm'),
                              ('Which boards had the highest bale % this week?', 'bale_pct'), ('Bale MSF by boards this week', 'bale_msf'),
                              ('Which boards had the highest unexplained % this week?', 'unexplained_pct'), ('Shear % by boards this week', 'shear_pct'),
                              ('Crew trim % by shift this week', 'trim_pct'), ('Which boards had the most shear cost this week?', 'shear_usd'),
                              ('Which boards had the most bale this week?', 'bale_sqft')):
        with pytest.raises(ValueError) as refused:
            route(question, Context(DAY, 2, scope='shift'), store.production_days, store)
        offered = _offered(str(refused.value))
        assert offered, (question, str(refused.value))
        for suggestion in offered:
            r = reading(store, suggestion)
            assert r.complete and r.query.measure == measure, (question, suggestion, r.query.measure if r.query else r.refusal)


def test_the_modifier_and_the_boards_keep_their_own_reasons_where_they_do_not_apply(store):
    """_modifier's reason needs another split the measure is recorded by: "crew rejects by reason" is never told to ask a split the
    model refuses. A value said with its cue is a value ("compare boards at the shear on shift 1 versus shift 2" compares a count of
    boards: the records' reason), never a measure kept for _boards_dimension (the reviewer's check of this round)."""
    r = reading(store, 'Crew rejects by reason this week')
    assert 'modifier' not in r.cues and not r.complete, r.refusal
    with pytest.raises(ValueError, match='^The records do not count boards'):
        route('Compare boards at the shear on shift 1 versus shift 2 this week', Context(DAY, 2, scope='shift'), store.production_days, store)


# ------------------------------------------------------------------ the questions a refusal offers are answered as offered

def test_the_words_a_suggestion_says_read_back(store):
    """The production days are offered as "days run", the speed against grade target as "attainment" and the running one as "running
    attainment": each reads back as its measure over the window asked. "Production days" read as the volume split by day, and "speed
    against grade target … last week" left the window on this week (the coordinator's review of PR #155 at 2c28fd3, item 2)."""
    for measure, word in (('production_days', 'days run'), ('speed_to_target_pct', 'attainment'), ('run_speed_to_target_pct', 'running attainment')):
        assert P._measure_word(S.model(), measure) == word
        for question, window in ((f'which grade had the most {word} last week', {'period': 'week', 'anchor': '2026-09-07'}),
                                 (f'{word} by grade this week', WEEK)):
            r = reading(store, question)
            assert r.complete and r.query.measure == measure and r.query.window == window, (question, r.refusal, r.query)


def test_a_question_offered_is_kept_only_when_the_router_answers_it_as_offered(store):
    """_answers_as_offered routes a question offered from the refusal's own controls: the semantic query of its measure, split, ranking,
    filters and window, or a hand route that opens with the measure. "Order changes yesterday" is the orders card's order fragments,
    "setup minutes yesterday" the day's setups, "rejected sheets yesterday" the dry-end percent of yield, "maintenance + pm downtime this
    week" the review's maintenance and operator downtime: never offered (the coordinator's review of PR #155 at 2c28fd3, item 1)."""
    mdl = S.model()
    for question, dropped, kept in (('Order changes by boards yesterday', 'order changes yesterday', 'order changes by grade yesterday'),
                                    ('Setup minutes by boards yesterday', 'setup minutes yesterday', 'setup minutes by grade yesterday'),
                                    ('Rejected sheets by boards yesterday', 'rejected sheets yesterday', 'rejected sheets by grade yesterday'),
                                    ('Maintenance + PM downtime by boards this week', 'maintenance + pm downtime this week',
                                     'maintenance + pm downtime by grade this week')):
        r = reading(store, question, scope='shift')
        assert [str(s) for s in r.suggestions] == [kept], (question, r.suggestions)
        assert dropped in [str(s) for s in r.dropped], (question, r.dropped)
        measure = r.suggestions[0].measure
        assert not P._answers_as_offered(P._ask(mdl, r, measure), r) and P._answers_as_offered(P._ask(mdl, r, measure, 'grade'), r)
    # a question offered keeps the values the refusal read, its named shift and its window; with none kept, the model's own reach
    r = reading(store, 'Which boards had the most operator stops this week?', scope='shift')
    assert [s.filters for s in r.suggestions] == [{'kind': 'Operator'}] * 2, r.suggestions
    r = reading(store, 'Speed against grade target by boards yesterday', scope='shift')
    assert [str(s) for s in r.suggestions] == ['attainment by grade yesterday', 'attainment yesterday'], r.suggestions
    r = reading(store, 'Which boards had the most glue stops this week?', scope='shift')
    assert not r.suggestions and r.refusal.endswith('The semantic model keeps stops by shift, crew, grade, flute, paper run, web width, '
                                                    'stop kind, stop place and stop reason'), r.refusal


OFFER_WINDOWS = ('this week', 'last week', 'yesterday', 'this month', 'last month')
OFFER_PAIRS = (('grade', 'shift'), ('crew', 'shift'), ('wet end', 'grade'), ('shift', 'grade'))
OFFER_LINES = ('paper', 'splice', 'glue', 'stacker', 'knife', 'warp', 'bond', 'moisture', 'order change', 'slitter', 'double backer', 'splicer',
               'crew', 'grade', 'roll', 'baler', 'steam', 'jam', 'maintenance')


def _offer_phrasings(boards_only=False):
    """The generated set of the coordinator's review of PR #155 at 2c28fd3, item 3, as (question, the measure it names or None, its
    window words): the boards, a dimension said singular beside a split of another, and a line's waste, for every measure's label and
    the word a question offered says it in, across five windows. The measure is the model's own reading of the word, else the measure
    it was generated from ("minutes of downtime")."""
    mdl = S.model()
    words = {}
    for name, m in mdl.measures.items():
        for w in (P._strip_parens(m['label']).lower(), P._measure_word(mdl, name)):
            words.setdefault(w, mdl.measure_words.get(w) or mdl.measure_words.get(w.lower()) or name)
    named = [(f'which boards had the most {w}', m) for w, m in words.items()] + [(f'{w} by boards', m) for w, m in words.items()]
    named += [('which boards were fastest', 'speed_run_fpm'), ('which boards were slowest', 'speed_run_fpm'), ('which boards took the most minutes', None)]
    # a measure per production day: its ratio to the days where the model keeps one (the MSF per day), else none, so nothing offered
    # may name the measure itself in its place (the reviewer subagent's re-check of e51adf7: 'stops per production day')
    named += [(f'which boards had the most {w} per production day', P._ratio(mdl, m, 'production_days') or 'no ratio')
              for w, m in (('stops', 'stops'), ('setups', 'setups'), ('minutes of downtime', 'downtime_minutes'), ('MSF', 'msf'), ('side trim', 'trim_sqft'))]
    if not boards_only:
        named += [(f'which {other} had the most {said} {w}', m) for w, m in words.items() for said, other in OFFER_PAIRS]
        named += [(f'grade {w} by shift', m) for w, m in words.items()]
        named += [(q, None) for v in OFFER_LINES for q in (f'{v} waste', f'how much {v} waste', f'which shift had the most {v} waste', f'{v} waste by grade')]
    return [(f'{q} {w}', m, w) for q, m in named for w in OFFER_WINDOWS]


def _window_of(store, words, day):
    """The production days the window words name from the controls' day, by the calendar alone."""
    from investigator.record_store import parse_window
    grain = 'day' if words == 'yesterday' else words.split()[-1]
    anchor = agent.previous_window_day(grain, day, store.production_days) if words.split()[0] in ('last', 'yesterday') else day
    return tuple(str(d) for d in parse_window({'period': grain, 'anchor': anchor})[2:])


_FIGURE = re.compile(r'\*\*[+$]?(-?[\d,]+(?:\.\d+)?)')


def _figures(store, request, context, text):
    """The bold figures of the answer to `request`, in order (the first is the one the answer opens with)."""
    analysis = store.day_analysis(context.production_day)
    try:
        result = Agent(analysis).ask(text, context, request=request)
    finally:
        analysis.close()
    return [float(m[1].replace(',', '')) for line in result['sections'] for m in [_FIGURE.search(line)] if m]


def _same(a, b):
    return abs(a - b) <= max(0.051, abs(b) * 0.001)


def _as_asked(store, text, refusal, measure, words, controls):
    """None when the router answers the question `text` that a refusal of a generated question offered, asked next from the visitor's
    own controls, as that question: over the window the generated question's words name (a split by day refused over one day may take
    the week that holds it), naming no value the generated question did not, of the measure it names when the refusal is the boards'
    or a modifier's (they offer the same measure split another way), and, sent to a hand route, opening with the figure the semantic
    model gives for it (any row's for a split it does not rank; the grades' speed card's slowest grade is its own, issue #75). This
    check is the test's own: the question's words and the calendar, never the parser's spec of what it offered or its hand-route table."""
    from investigator.record_store import parse_window
    want = _window_of(store, words, controls.production_day)
    try:
        request, asked = route(text, controls, store.production_days, store)
    except ValueError as exc:
        return f'refused: {str(exc)[:80]}'
    own = P.read(text, asked, store)
    if not own.complete:
        return f'its own reading refuses: {own.refusal[:80]}'
    q = own.query
    if request['intent'] == 'semantic':
        args, lead = request['plan'][0]['arguments'], request.get('lead') or {}
        if (args['measure'], args['group_by'], lead.get('rank'), args['filters']) != (q.measure, q.group_by, own.rank, q.filters):
            return f"planned {args['measure'], args['group_by'], lead.get('rank'), args['filters']}, read {q.measure, q.group_by, own.rank, q.filters}"
        got = tuple(str(d) for d in parse_window(args['window'])[2:])
    else:
        hand = P._hand_window(request, asked)
        got = tuple(str(d) for d in parse_window({'period': hand[0], 'anchor': hand[1]})[2:]) if hand else None
    longer = refusal.startswith('A split by') and got is not None and got[0] <= want[0] and want[1] <= got[1]
    if got != want and not longer:
        return f'window {got}, the question asked {want}'
    if q.filters or q.share is not None:
        return f'names values the question did not: {q.filters}'
    if measure and (refusal.startswith("'boards' is not a dimension") or ' is said before ' in refusal) and q.measure != measure:
        return f'measure {q.measure}, the question asked {measure}'
    if request['intent'] == 'semantic':
        return None
    hand, semantic = _figures(store, request, asked, text), _figures(store, P.request_of(own), asked, text)
    if hand and semantic and _same(hand[0], semantic[0]):
        return None
    card = request['intent'] == 'period_speed' and (q.measure, q.group_by, own.rank) == ('speed_run_fpm', ['grade'], 'least')
    if hand and semantic and (q.group_by and not own.rank or card) and any(_same(hand[0], x) for x in semantic[1:]):
        return None
    return f"{request['intent']} opens with {hand[:1]}, the semantic model with {semantic[:1]}"


@pytest.mark.parametrize('day, period, boards_only', [(DAY, 'day', False), ('2026-09-30', 'day', True), ('2026-09-16', 'week', True)])
def test_every_question_a_generated_refusal_offers_is_answered_as_asked(store, day, period, boards_only):
    """The class, not the instances (the coordinator's review of PR #155 at 2c28fd3, item 3): every question that any refusal of the
    generated set offers is one the parser checked (an Ask), and passes this test's own check when a visitor asks it next in the same
    controls: on a day whose week and month are complete, on the last day of the records, whose are not (the grades' speed card
    then opens with the grades observed, unranked), and under the Week control (an unmoved 'yesterday' is still yesterday). 2c28fd3 offered about fifty that answered another measure, another window or a
    refusal ('which grade had the most production days': the volume; 'order changes yesterday': the order fragments)."""
    controls = Context(day, 2, scope='shift', period=period)
    verdicts, bad, offered_any = {}, [], 0
    for question, measure, words in _offer_phrasings(boards_only):
        try:
            route(question, controls, store.production_days, store)
            continue
        except ValueError as exc:
            text, offered = str(exc), _offered(str(exc))
        if not offered:
            continue
        offered_any += 1
        checked = {str(s) for s in P.read(question, question_scope(question, controls, store.production_days, store), store).suggestions}
        for suggestion in offered:
            if suggestion not in checked:
                bad.append((question, suggestion, 'offered without the check'))
                continue
            key = (suggestion, text.split('.')[0], measure, words)
            if key not in verdicts:
                verdicts[key] = _as_asked(store, suggestion, text, measure, words, controls)
            if verdicts[key]:
                bad.append((question, suggestion, verdicts[key]))
    assert offered_any > (100 if boards_only else 1000) and len(verdicts) > (100 if boards_only else 500), (offered_any, len(verdicts))
    assert not bad, bad[:20]
