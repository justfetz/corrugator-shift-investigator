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
    assert (r.query.measure, r.query.group_by, r.share) == ('stops', ['place'], 'Stacker')
    r = reading(store, 'What share of stops were maintenance this week?')
    assert (r.query.group_by, r.share) == (['kind'], 'Maintenance')


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
               'Run rate this week', 'Best crew this week', 'Stops per 1000 ft this week', 'How many glue stops this week']
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
                     'Stops by type this week', 'downtime on the double backer'):
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
             '{m} per {dim} this month', 'top 3 {dim}s by {m} this week', 'compare {dim}s on {m} this week')
VALUE_TEMPLATES = ('how many {m} on {value} this week', 'what percent of {m} were {value} this week', '{m} for {value} this month')


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


def test_a_sample_of_generated_answers_reads_as_it_was_asked_on_the_card(store):
    """On the card: the Read as line names the measure, each split and each filter; the headline leads with the measure."""
    for question, measure, dim, value in generated(store)[::23]:
        r = reading(store, question)
        if not r.complete:
            continue
        request, asked, result = answer(store, question, scope='day')
        if request['intent'] != 'semantic':
            continue   # the hand route answers it already ("how many setups on 200-C": one quantity, #113)
        data = result['trace'][0]['data']
        read_as = next(s for s in result['sections'] if s.startswith('Read as: '))
        noun = re.sub(r'\s*\(.*\)$', '', data['label']).lower()
        assert read_as.startswith(f'Read as: {noun}'), (question, read_as)
        for f in data['filters']:
            assert f['text'] in read_as, (question, read_as)
        head = result['sections'][0]
        assert noun in head.lower() or (request.get('lead') or {}).get('share') is not None, (question, head)


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
