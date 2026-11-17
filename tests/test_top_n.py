"""Top N answers as asked (issue #185): the stop reasons ranked read as the semantic model's ranking, over the year to date too, and a
window word the model cannot read is never answered as another window.

The family test generates the owner's top-N questions (phrasings x kinds x windows x N x the page's scope x a shift named) and checks
every answer's rows against a hand-written query over `stops`, independent of the router, the parser and the compiler: the same
reasons in the same order, the same minutes to 0.1 and the same stop counts. The owner's calls (#172, 2026-11-15): top reasons rank
by downtime minutes, the percent beside them, and five when no number is said.
"""
from datetime import date
import json
import re

import pytest

from investigator import semantic as S
from investigator import semantic_parse as P
from investigator.agent import Agent, Context, parse_request, question_scope, route
from investigator.record_store import default_store
from investigator.urls import canonical_url, resolve_url

DAY = '2026-09-14'
WINDOWS = {   # the window words a question says, and the dates a hand-written query reads for them from Monday 14 September
    '': (DAY, DAY),                              # none: the selected day
    'yesterday': ('2026-09-11', '2026-09-11'),
    'last week': ('2026-09-07', '2026-09-11'),
    'this month': ('2026-09-01', '2026-09-30'),
    'YTD': ('2026-01-01', DAY),
}
KINDS = {'maintenance': 'Maintenance', 'operator': 'Operator', None: None}
NS = (3, 5, None)
# (phrasing, whether it says N): {top} is "top", "top 3" or "top 5"; {kind} the kind's word and a space, or nothing for every kind
PHRASINGS = (('{top} {kind}reasons {window}', True),
             ('what were my {top} {kind}reasons {window}?', True),
             ('{top} {kind}downtime reasons {window}', True),
             ('biggest {kind}problems {window}', False),
             ('worst {kind}reasons {window}', False),
             ('{kind}pareto {window}', False))


@pytest.fixture(scope='module')
def store():
    return default_store()


def oracle(store, window, kind, shift, n, measure='minutes'):
    """The hand-written query: each stop reason's minutes and stops over the window, largest first, ties by name, the first n."""
    start, end = WINDOWS[window]
    where, params = ['production_date BETWEEN ? AND ?'], [start, end]
    if kind:
        where.append('kind = ?')
        params.append(kind)
    if shift:
        where.append('shift = ?')
        params.append(shift)
    order = 'sum(seconds) DESC' if measure == 'minutes' else 'count(*) DESC'
    sql = (f"SELECT reason, sum(seconds) / 60.0 AS minutes, count(*) AS stops FROM stops WHERE {' AND '.join(where)} "
           f"GROUP BY reason ORDER BY {order}, reason LIMIT ?")
    return [(r['reason'], r['minutes'], r['stops']) for r in store._fetch(sql, params + [n], 100)]


def same_rows(got, want):
    """The same reasons in the same order with the same stops, and minutes equal to 0.1 (a sum of each stop's minutes and the
    minutes of the summed seconds differ in the last bits of a float, never by a tenth)."""
    return len(got) == len(want) and all(a[0] == b[0] and a[2] == b[2] and abs(a[1] - b[1]) < 0.05 for a, b in zip(got, want))


def family():
    out = []
    for phrasing, says_n in PHRASINGS:
        for kind_word, kind in KINDS.items():
            for window in WINDOWS:
                for n in (NS if says_n else (None,)):
                    for shift in (None, 1):
                        text = phrasing.format(top=f'top {n}' if n else 'top', kind=f'{kind_word} ' if kind_word else '', window=window)
                        text = text.replace('?', '') + (' on shift 1' if shift else '') + ('?' if '?' in text else '')
                        out.append((re.sub(r'\s+', ' ', text).strip(), kind, window, n or 5, shift))
    return out


def owners_review(question, kind, window, n_said, shift):
    """The owner's downtime review keeps "top downtime reasons" over every kind with no number said (golden
    owner-top-downtime-reasons-yesterday, issue #101): the flagship card ranks every reason itself, over the windows it reads."""
    return kind is None and window != 'YTD' and 'downtime reasons' in question and not n_said


def test_the_family_is_several_hundred():
    assert len(family()) >= 200   # each asked with the page on All and on shift 2: over four hundred answers


@pytest.mark.parametrize('scope', ['day', 'shift'])
def test_every_top_n_answer_is_the_hand_written_query(store, scope):
    """Every question of the family, with the page on All and on shift 2: the answer is the semantic query of downtime minutes by
    stop reason, largest first, over the window the question names, for every shift unless it names one; its rows are the
    hand-written query's, reason by reason, minutes to 0.1 and stops; the card leads with the first row a ranking may lead with and
    says "top N"."""
    checked = 0
    for question, kind, window, n, shift in family():
        context = Context(DAY, 2, scope=scope, period='day')
        request, asked = route(question, context, store.production_days, store)
        n_said = bool(re.search(r'\btop [35]\b', question))
        if owners_review(question, kind, window, n_said, shift) and (shift is None or request['intent'] == 'period_review'):
            assert request['intent'] == 'period_review', (question, scope, request['intent'])   # the owner's review keeps it
            continue
        assert request['intent'] == 'semantic', (question, scope, request['intent'])
        args = request['plan'][0]['arguments']
        assert (args['measure'], args['group_by'], args['order'], args['limit']) == ('downtime_minutes', ['reason'], 'value', n), (question, args)
        assert args['filters'] == ({'kind': kind} if kind else {}), (question, args['filters'])
        assert args['shift'] == shift, (question, scope, args['shift'])
        start, end = WINDOWS[window]
        data = S.run(args, store)
        assert (data['window']['start'], data['window']['end']) == (start, end), (question, data['window'])
        got = [(r['reason'], r['value'], r['source_count']) for r in data['rows']]
        minutes = [r['value'] for r in data['rows']]
        assert minutes == sorted(minutes, reverse=True), (question, got)
        assert same_rows(got, oracle(store, window, kind, shift, n)), (question, scope, got)
        checked += 1
    assert checked >= 180


def test_the_card_says_top_n_and_leads_with_the_first_row(store):
    """The card of each family answer (the page on All): its headline names the first reason a ranking may lead with (never
    planned maintenance or an outage) and its minutes; its Read as line says "ranked by downtime minutes (the plant's default)" and
    "top N" (or how many the window holds when fewer); one finding per row, in the query's order."""
    from investigator.shapes import SemanticAnswer
    for question, kind, window, n, shift in family():
        if shift:
            continue
        context = Context(DAY, 2, scope='day', period='day')
        request, asked = route(question, context, store.production_days, store)
        if request['intent'] != 'semantic':
            continue
        analysis = store.day_analysis(asked.production_day)
        try:
            result = Agent(analysis).ask(question, asked, request=request)
        finally:
            analysis.close()
        sections = [re.sub(r'\s*\((?:query|row)[^)]*\)', '', s) for s in result['sections']]
        data = next(s['data'] for s in result['trace'] if s['tool'] == S.TOOL)
        lead = next(r for i, r in enumerate(data['rows']) if i not in SemanticAnswer.apart(data))
        assert sections[0].startswith(f'{lead["label"]} had the most downtime'), (question, sections[0])
        read_as = next(s for s in sections if s.startswith('Read as'))
        # the default said only when no word named the measure ("downtime reasons" names it)
        assert ("ranked by downtime minutes (the plant's default)" in read_as) == ('downtime' not in question), (question, read_as)
        holds = f' (the window holds {data["groups"]})' if data['groups'] < n else ''
        assert f'largest first, top {n}{holds}.' in read_as, (question, read_as)
        # one finding per row: the rows a ranking may lead with in the query's order, then the ones it sets apart
        apart = SemanticAnswer.apart(data)
        order = [r['label'] for i, r in enumerate(data['rows']) if i not in apart] + [r['label'] for i, r in enumerate(data['rows']) if i in apart]
        labels = {r['label'] for r in data['rows']}
        listed = [m[1] for s in sections[1:sections.index(read_as)] for m in [re.match(r'^- (.+?): \*\*', s)] if m and m[1] in labels]
        assert listed == order, (question, listed, order)


@pytest.mark.parametrize('question, measure, limit', [
    ('top reasons by stops last week', 'stops', 5),            # a measure named keeps it: by stops, never the default minutes
    ('top 3 reasons by stops last week', 'stops', 3),
    ('top 5 reasons by downtime % last week', 'downtime_pct', 5),
    ('which reasons cost the most money last week', 'downtime_usd', 5),   # the unit said of the default: the downtime's dollars
    ('most common downtime reasons last week', 'stops', 5),     # "most common" is a count
])
def test_a_named_measure_or_unit_ranks_by_it(store, question, measure, limit):
    """... and its address answers the same query: m= is left out only for the default measure itself."""
    request, asked = route(question, Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    args = request['plan'][0]['arguments']
    assert (request['intent'], args['measure'], args['group_by'], args['limit']) == ('semantic', measure, ['reason'], limit), args
    url = canonical_url(request, asked)
    assert ('&m=' in url) == (not (request.get('lead') or {}).get('default'))
    resolved = resolve_url(url, store)['request']
    assert resolved['plan'] == request['plan'] and resolved.get('lead') == request.get('lead'), url


@pytest.mark.parametrize('question, said', [('top 30 reasons this year', 'top 30 (the window holds {groups})'),
                                            ('top 40 reasons by stops this year', 'top 40 (the window holds {groups})'),
                                            ('top 3 reasons by stops this year', 'top 3')])
def test_top_n_is_said_whatever_n(store, question, said):
    """A ranking of N rows says "top N" for any N asked, the default limit's 25 aside (Codex on PR #186)."""
    request, asked = route(question, Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    analysis = store.day_analysis(asked.production_day)
    try:
        result = Agent(analysis).ask(question, asked, request=request)
    finally:
        analysis.close()
    data = next(s['data'] for s in result['trace'] if s['tool'] == S.TOOL)
    read_as = next(s for s in result['sections'] if s.startswith('Read as'))
    assert read_as.endswith(f'largest first, {said.format(groups=data["groups"])}.'), read_as


@pytest.mark.parametrize('question, measure', [('top maintenance reasons last week', 'downtime_minutes'),
                                               ('top maintenance reasons by percent last week', 'downtime_pct'),
                                               ('top reasons in dollars last week', 'downtime_usd')])
def test_the_default_leaves_m_out_of_the_address_only_when_it_is_the_measure(store, question, measure):
    request, asked = route(question, Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    url = canonical_url(request, asked)
    assert request['plan'][0]['arguments']['measure'] == measure
    assert ('&m=' not in url) == (measure == 'downtime_minutes'), url
    assert resolve_url(url, store)['request']['plan'] == request['plan']


def test_the_year_to_date_splits_by_day_but_never_by_a_longer_grain(store):
    args = {'measure': 'stops', 'window': {'period': 'ytd', 'anchor': DAY}}
    assert S.run({**args, 'group_by': ['day']}, store)['groups'] == 183   # every production day, 1 Jan to 14 Sep
    for grain in ('week', 'month', 'period'):
        with pytest.raises(S.SemanticError, match='ends on a part'):
            S.validate({**args, 'group_by': [grain]}, store)


def test_stops_by_reason_match_the_hand_written_query(store):
    request, _ = route('top reasons by stops last week', Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    data = S.run(request['plan'][0]['arguments'], store)
    got = [(r['reason'], r['value'], r['source_count']) for r in data['rows']]
    want = [(reason, stops, stops) for reason, _, stops in oracle(store, 'last week', None, None, 5, measure='stops')]
    assert got == want


@pytest.mark.parametrize('question', [
    'which maintenance reason had the most minutes last week',   # "reason" after a value of another dimension (issue #185, item 1)
    'which operator reason had the most downtime this month',
    'what maintenance reason had the most minutes last week',
])
def test_one_reason_asked_after_a_kind_is_the_kind_split_by_reason(store, question):
    request, _ = route(question, Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    args = request['plan'][0]['arguments']
    assert request['intent'] == 'semantic' and args['group_by'] == ['reason'] and args['filters'].get('kind') in ('Maintenance', 'Operator')
    assert args['limit'] == S.DEFAULT_LIMIT   # one reason asked: every row, the card names the first


@pytest.mark.parametrize('question', [
    'why were the top maintenance reasons so high last week',
    'why was the stacker the top reason this week',
    'what caused the top downtime reasons this month',
    'why did maintenance problems go up last week',
])
def test_a_why_question_about_reasons_stays_an_investigation(store, question):
    """"Reason" and "problem" are the stop reason's words, never a why: the investigations (WHY_ASK) stay the hand route's."""
    context = Context(DAY, 2, scope='day', period='day')
    try:
        request, _ = route(question, context, store.production_days, store)
    except ValueError:
        return
    assert request['intent'] != 'semantic', question


@pytest.mark.parametrize('question', ['what was the reason for the stops last week', 'what is the reason for the stacker stops this week'])
def test_the_reason_for_is_never_a_split(store, question):
    """"The reason for" asks a cause, which no answer reads: refused as on main, never every reason ranked."""
    with pytest.raises(ValueError):
        route(question, Context(DAY, 2, scope='shift', period='day'), store.production_days, store)


# ------------------------------------------------------------------ the year to date

@pytest.mark.parametrize('words', ['YTD', 'year to date', 'year-to-date', 'this year', 'so far this year', 'this year so far',
                                   'for the year to date'])
def test_the_year_to_date_words(store, words):
    """Each of the model's year-to-date words reads the window 1 January to the selected day (issue #185, item 3)."""
    request, _ = route(f'top operator reasons {words}', Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    assert request['intent'] == 'semantic'
    assert request['plan'][0]['arguments']['window'] == {'period': 'ytd', 'anchor': DAY}


def test_the_year_to_date_window(store):
    assert S.parse_window({'period': 'ytd', 'anchor': DAY})[2:] == (date(2026, 1, 1), date(2026, 9, 14))
    window = S.window_of(store, {'period': 'ytd', 'anchor': DAY})
    assert window['label'] == 'Year to date, 1 Jan–14 Sep 2026 (2026-01-01 to 2026-09-14)'
    assert (window['start'], window['end'], window['coverage']) == ('2026-01-01', DAY, 'complete')
    assert window['production_days_expected'] == window['production_days_observed']
    with pytest.raises(ValueError, match='outside the dataset range'):
        S.window_of(store, {'period': 'ytd', 'anchor': '2024-06-03'})
    with pytest.raises(S.SemanticError, match='the year to date has none'):
        S.validate(S.SemanticQuery('stops', window={'period': 'ytd', 'anchor': DAY}, compare='previous'), store)


def test_the_year_to_date_is_the_semantic_models_alone(store):
    """The reports and the period tools read no year to date (their year period is issue #106): record_store refuses it, and an
    address asks it only of measure=semantic."""
    from investigator.record_store import parse_window
    with pytest.raises(ValueError):
        parse_window({'period': 'ytd', 'anchor': DAY})
    with pytest.raises(ValueError, match='measure=semantic'):
        resolve_url(f'/corr/1/day/{DAY}?measure=pareto&window=ytd', store)
    url = f'/corr/1/day/{DAY}?measure=semantic&m=stops&window=ytd'
    resolved = resolve_url(url, store)
    assert resolved['request']['plan'][0]['arguments']['window'] == {'period': 'ytd', 'anchor': DAY}
    assert canonical_url(resolved['request'], resolved['context']) == url


def test_a_ytd_answer_reads_the_year_and_is_its_own_address(store):
    context = Context(DAY, 2, scope='shift', period='day')
    request, asked = route('how much money have missed splices cost me this year', context, store.production_days, store)
    args = request['plan'][0]['arguments']
    assert (args['measure'], args['filters'], args['window']) == ('downtime_usd', {'reason': 'Missed splice'}, {'period': 'ytd', 'anchor': DAY})
    data = S.run(args, store)
    minutes = oracle(store, 'YTD', None, None, 20)
    splices = next(m for reason, m, _ in minutes if reason == 'Missed splice')
    assert abs(data['quantity'] - splices) < 0.05   # the minutes priced are the records' own
    assert canonical_url(request, asked) == f'/corr/1/day/{DAY}?measure=semantic&m=downtime_usd&window=ytd&reason=Missed+splice'


# ------------------------------------------------------------------ a window word the model cannot read

@pytest.mark.parametrize('question', [
    'what were my top operator downtime reasons month to date',
    'top maintenance reasons this quarter',
    'top downtime reasons over the last 30 days',
    'top reasons last year',
])
def test_an_unread_window_is_refused_never_answered_as_another(store, question):
    """Change 4: the hand route answered the selected day (main: the day's review for "month to date"); the parser's reason and the
    questions it offers, each read completely over a window the model reads, are the refusal."""
    for scope in ('day', 'shift'):
        with pytest.raises(ValueError, match='is not a window the semantic model reads') as refused:
            route(question, Context(DAY, 2, scope=scope, period='day'), store.production_days, store)
        offered = re.findall(r"'([^']+)'", str(refused.value).split('Did you mean', 1)[1]) if 'Did you mean' in str(refused.value) else []
        for ask in offered:
            request, _ = route(ask, Context(DAY, 2, scope=scope, period='day'), store.production_days, store)
            assert request['intent'] == 'semantic', ask


@pytest.mark.parametrize('question, intent', [
    ('How many stops this week and last week?', 'period_quantity'),     # the quantity routes read their own windows
    ('Side trim trend over the last eight weeks', 'period_trend'),
    ('Show downtime by reason on Monday', 'downtime'),                # the selected day said by its weekday
])
def test_a_route_that_reads_its_window_keeps_it(store, question, intent):
    request, _ = route(question, Context(DAY, 2, scope='shift', period='day'), store.production_days, store)
    assert request['intent'] == intent


# ------------------------------------------------------------------ the model's entries

def test_the_default_ranking_is_a_model_entry():
    """The reasons' default ranking and the year to date are entries of semantic_model.json, checked when it loads."""
    model = S.model()
    assert model.dimensions['reason']['rank_by'] == {**model.dimensions['reason']['rank_by'], 'measure': 'downtime_minutes', 'top': 5}
    assert {'reason', 'reasons', 'problem'} <= set(model.dimensions['reason']['synonyms'])
    assert list(model.windows) == list(S.WINDOW_PERIODS)
    spec = json.loads(S.MODEL_PATH.read_text())
    reason = next(d for d in spec['dimensions'] if d['name'] == 'reason')
    reason['rank_by']['measure'] = 'no_such_measure'
    with pytest.raises(ValueError, match='ranks by default'):
        S.Model(spec)
    spec = json.loads(S.MODEL_PATH.read_text())
    spec['windows']['periods'].pop()
    with pytest.raises(ValueError, match='windows block'):
        S.Model(spec)


def test_the_semantic_model_page_lists_the_default_ranking_and_the_year_to_date():
    page = S.model_markdown()
    assert '## Windows' in page and 'Year to date' in page and 'so far this year' in page
    assert 'ranked by downtime minutes' in page.lower() or 'downtime_minutes' in page.split('## Default rankings', 1)[-1]
