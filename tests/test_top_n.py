"""Top N answers as asked (issue #185): the stop reasons ranked read as the semantic model's ranking, over the year to date too, and a
window word the model cannot read is never answered as another window.

The family test generates the owner's top-N questions (phrasings x kinds x windows x N x the page's scope x a shift named) and checks
every answer's rows against a hand-written query over `stops`, independent of the router, the parser and the compiler: the same
reasons in the same order, the same minutes to 0.1 and the same stop counts. The owner's calls (#172, 2026-11-15): top reasons rank
by downtime minutes, the percent beside them, and five when no number is said. The coordinator's review of PR #186 (round 1): N
counts the reasons that rank, planned maintenance and a power outage shown apart beside them (item 5); "worst", "biggest" and "top"
reasons read alike, as the ranking, with no carve-out for the owner's downtime review (item 7).
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
             ('worst {kind}downtime reasons {window}', False),     # item 7: "worst downtime reasons" is the ranking, never the review
             ('biggest {kind}downtime reasons {window}', False),
             ('{top} {kind}stop reasons {window}', True),           # item 6: "stop reasons" rank by downtime minutes too
             ('{kind}pareto {window}', False))
# The reasons a ranking sets apart, shown beside the N it ranks: planned maintenance (schedule-driven) and a power outage (external)
APART = ('Planned maintenance', 'Power outage')


@pytest.fixture(scope='module')
def store():
    return default_store()


def oracle(store, window, kind, shift, n, measure='minutes'):
    """The hand-written query: each stop reason's minutes and stops over the window, largest first, ties by name; the first n reasons
    that rank, with the reasons set apart (APART) that come before the n-th."""
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
           f"GROUP BY reason ORDER BY {order}, reason")
    out, ranked = [], 0
    for r in store._fetch(sql, params, 100):
        if ranked == n:
            break
        out.append((r['reason'], r['minutes'], r['stops']))
        ranked += r['reason'] not in APART
    return out


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
        # every phrasing, "top downtime reasons" over every kind too: the ranking, never the owner's review (item 7)
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
    assert checked == len(family())


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
        # the default said only when no word named the measure ("downtime reasons" names it; "stop reasons" does not: item 6)
        assert ("ranked by downtime minutes (the plant's default)" in read_as) == ('downtime' not in question), (question, read_as)
        # how many the window holds when fewer than N rank, from the hand-written query: the reasons that rank when some are set
        # apart (the reviewer's check of PR #186 round 1), else every reason
        every = oracle(store, window, kind, None, 100)
        ranked = sum(reason not in APART for reason, _, _ in every)
        holds = (f' (the window holds {ranked} that rank)' if ranked < len(every) and ranked < n else
                 f' (the window holds {len(every)})' if len(every) < n else '')
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


# the year to date holds 10 stop reasons, 8 of which rank (planned maintenance and the power outage are set apart): the hand-written
# query says so in test_the_year_holds_eight_reasons_that_rank
@pytest.mark.parametrize('question, said', [('top 30 reasons this year', 'top 30 (the window holds 8 that rank)'),
                                            ('top 40 reasons by stops this year', 'top 40 (the window holds 8 that rank)'),
                                            ('top 3 reasons by stops this year', 'top 3'),
                                            ('top 25 reasons this year', 'top 25 (the window holds 8 that rank)'),   # the default limit
                                            ('top 25 orders by rejects this year', 'top 25'),
                                            ('top 100 reasons this year', 'top 100 (the window holds 8 that rank)'),  # N up to 200
                                            ('top 200 reasons by stops this year', 'top 200 (the window holds 8 that rank)'),
                                            ('top thirty reasons this year', 'top 30 (the window holds 8 that rank)'),   # in number words
                                            ('top twenty-five orders by rejects this year', 'top 25'),
                                            ('top 9 reasons this week', 'top 9 (the window holds 8 that rank)')])
def test_top_n_is_said_whatever_n(store, question, said):
    """A ranking of N rows says "top N" for any N asked (Codex on PR #186), the default limit's 25 too: that N was said rides on the
    lead, and the address keeps top=25 (the review of PR #186, item 3); N is read in words up to MAX_LIMIT (item 4)."""
    request, asked = route(question, Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    analysis = store.day_analysis(asked.production_day)
    try:
        result = Agent(analysis).ask(question, asked, request=request)
    finally:
        analysis.close()
    data = next(s['data'] for s in result['trace'] if s['tool'] == S.TOOL)
    read_as = next(s for s in result['sections'] if s.startswith('Read as'))
    assert read_as.endswith(f'largest first, {said}.'), read_as
    n = int(re.search(r'top (\d+)', said)[1])
    url = canonical_url(request, asked)
    assert f'&top={n}' in url, url
    assert resolve_url(url, store)['request'] == request   # the address carries the N said, the default's 25 too


def test_the_year_holds_eight_reasons_that_rank(store):
    every = oracle(store, 'YTD', None, None, 100)
    assert (len(every), sum(reason not in APART for reason, _, _ in every)) == (10, 8)
    week = [(r['reason'], r['minutes'], r['stops']) for r in store._fetch(
        "SELECT reason, sum(seconds) / 60.0 AS minutes, count(*) AS stops FROM stops WHERE production_date BETWEEN ? AND ? GROUP BY reason",
        ['2026-09-14', '2026-09-18'], 100)]
    assert (len(week), sum(reason not in APART for reason, _, _ in week)) == (10, 8)


@pytest.mark.parametrize('question, said', [('top 0 reasons last week', "'top 0' asks for no rows"),
                                            ('top zero reasons last week', "'top zero' asks for no rows"),
                                            ('top three hundred reasons this year', "'top three hundred' asks for more rows than a ranking lists"),
                                            ('top 201 reasons this year', "'top 201' asks for more rows than a ranking lists"),
                                            ('top 1000 orders by rejects this year', "'top 1000' asks for more rows than a ranking lists")])
def test_top_0_or_more_than_the_model_lists_is_refused(store, question, said):
    """N outside 1 to MAX_LIMIT is refused naming it, under either scope, never clamped to another N (the review of PR #186,
    items 4 and 5: "top 0" answered the top 1, with a headline that named no reason)."""
    for scope in ('day', 'shift'):
        with pytest.raises(ValueError, match=re.escape(said) + f': a ranking lists from 1 to {S.MAX_LIMIT} rows'):
            route(question, Context(DAY, 2, scope=scope, period='day'), store.production_days, store)


@pytest.mark.parametrize('scope', ['day', 'shift'])
def test_top_1_reason_is_the_largest_unplanned_one(store, scope):
    """N counts the reasons that rank, planned maintenance shown apart (the review of PR #186, item 5): "top 1 reason last week" is
    Missed splice, 103.43 min, with planned maintenance's 123.00 min beside it, the hand-written query's rows."""
    request, asked = route('top 1 reason last week', Context(DAY, 2, scope=scope, period='day'), store.production_days, store)
    args = request['plan'][0]['arguments']
    assert (args['measure'], args['group_by'], args['limit']) == ('downtime_minutes', ['reason'], 1)
    data = S.run(args, store)
    got = [(r['reason'], r['value'], r['source_count']) for r in data['rows']]
    assert same_rows(got, oracle(store, 'last week', None, None, 1))
    assert [r['reason'] for r in data['rows']] == ['Planned maintenance', 'Missed splice']
    from investigator.shapes import SemanticAnswer
    assert [data['rows'][i]['reason'] for i in SemanticAnswer.apart(data)] == ['Planned maintenance']


def test_the_rows_set_apart_are_read_beyond_the_limit_only_for_a_ranking_of_stops(store):
    """The query reads the values a ranking of stops sets apart beyond its limit, and keeps N that rank; any other query reads its
    limit (the receipt's LIMIT is the query's own)."""
    week = {'period': 'week', 'anchor': '2026-09-07'}
    stops = S.validate(S.SemanticQuery('downtime_minutes', window=week, group_by=['reason'], limit=3), store)
    assert S.apart_slack(S.model(), stops) == 2   # Planned maintenance and Power outage
    rows = S.run(stops, store)['rows']
    assert sum(not S.set_apart('stop', r['lever'], 'controllable') for r in rows) == 3
    setups = S.validate(S.SemanticQuery('setups', window=week, group_by=['grade'], limit=3), store)
    assert S.apart_slack(S.model(), setups) == 0 and len(S.run(setups, store)['rows']) == 3
    by_day = S.validate(S.SemanticQuery('downtime_minutes', window=week, group_by=['day'], limit=3), store)
    assert S.apart_slack(S.model(), by_day) == 0   # a time split lists its days in order: nothing to set apart


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


@pytest.mark.parametrize('question, measure, limit', [
    ('top stop reasons YTD', 'downtime_minutes', 5),           # item 6: "stop reasons" rank by downtime minutes, like any reasons
    ('top stop reason this week', 'downtime_minutes', S.DEFAULT_LIMIT),
    ('top stop reasons by stops YTD', 'stops', 5),             # ... and only "by stops" or "most stops" counts them
    ('which reason cost us the most this month', 'downtime_usd', S.DEFAULT_LIMIT),   # "cost us the most": the money, priced downtime
    ('which reasons cost the most this week', 'downtime_usd', 5),
])
def test_the_owners_rules_for_stop_reasons_and_cost(store, question, measure, limit):
    """The coordinator's defaults from the owner's 15 Nov calls (the review of PR #186, item 6; the owner can overrule at merge)."""
    for scope in ('day', 'shift'):
        request, asked = route(question, Context(DAY, 2, scope=scope, period='day'), store.production_days, store)
        assert request['intent'] == 'semantic', (question, scope, request['intent'])
        args = request['plan'][0]['arguments']
        assert (args['measure'], args['group_by'], args['limit']) == (measure, ['reason'], limit), (question, args)
        assert resolve_url(canonical_url(request, asked), store)['request'] == request


def test_cost_with_a_measure_said_after_it_is_the_verb(store):
    """"Which reason cost the most minutes": the minutes, the Pareto's own answer (the looks every stop card offers ask it)."""
    request, _ = route('which reason cost the most minutes this week', Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    assert request['intent'] == 'period_pareto' and request['plan'][0]['arguments'].get('measure') != 'count', request


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
    # the review of PR #186, item 1: each answered as another window at c7b61dd
    'top operator reasons Q3', 'top operator reasons in Q2', 'top operator reasons QTD', 'top operator reasons FYTD',
    'top operator reasons H1', 'top operator reasons first half', 'top operator reasons since Monday',
    'top operator reasons since 1 Jan', 'top operator reasons lately', 'top operator reasons annually', 'downtime since June',
    'downtime month-to-date', 'downtime week-to-date', 'downtime rolling 4 weeks', 'downtime trailing 30 days',
    'downtime last fifteen days', 'downtime recently', 'downtime January through September', 'downtime between June and August',
    'downtime from June to August', 'top operator reasons per year',
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


@pytest.mark.parametrize('question, word, grain', [
    ('Downtime last week vs previous', 'previous', 'week'), ('trim last week vs previous', 'previous', 'week'),
    ('speed last month vs previous', 'previous', 'month'), ('msf last month vs previous', 'previous', 'month'),
    ('waste last month vs previous', 'previous', 'month'), ('how did last week compare to previous', 'previous', 'week'),
    ('downtime last week compared to prior', 'prior', 'week'),
])
def test_previous_or_prior_is_the_word_refused_never_the_window_beside_it(store, question, word, grain):
    """The review of PR #186, item 2: "'last week' is not a window the semantic model reads" was false; the word the model cannot
    read is "previous" or "prior", and the refusal names it and how a comparison is asked, which answers."""
    for scope in ('day', 'shift'):
        with pytest.raises(ValueError) as refused:
            route(question, Context(DAY, 2, scope=scope, period='day'), store.production_days, store)
        text = str(refused.value)
        assert text.startswith(f"'{word}' names no window: the semantic model compares a window with the one just before it, asked as "
                               f"'this {grain} compared with the {grain} before'"), text
        assert 'is not a window the semantic model reads' not in text, text
    request, _ = route(f'downtime this {grain} compared with the {grain} before', Context(DAY, 2, scope='day', period='day'),
                       store.production_days, store)
    assert request['intent'] != 'semantic' or request['plan'][0]['arguments']['compare'] == 'previous'


def test_two_windows_of_one_grain_offer_the_one_against_the_one_before(store):
    with pytest.raises(ValueError) as refused:
        route('top maintenance reasons last week and this week', Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    assert str(refused.value).startswith("'last week' and 'this week' are two windows"), str(refused.value)
    offered = re.findall(r"'([^']+)'", str(refused.value).split('Did you mean', 1)[1])
    assert offered == ['top maintenance reasons this week compared with the week before']
    request, _ = route(offered[0], Context(DAY, 2, scope='day', period='day'), store.production_days, store)
    args = request['plan'][0]['arguments']
    assert (request['intent'], args['compare'], args['filters'], args['window']['period']) == ('semantic', 'previous', {'kind': 'Maintenance'}, 'week')


@pytest.mark.parametrize('question, intent', [
    ('How many stops this week and last week?', 'period_quantity'),     # the quantity routes read their own windows
    ('Side trim trend over the last eight weeks', 'period_trend'),
    ('Show downtime by reason on Monday', 'downtime'),                # the selected day said by its weekday
    ('How did this week go versus the previous one?', 'period_compare'),   # the KPI summary reads "the previous one" itself
    ('what is trim costing us annually', None),                        # a year said as a rate of money: the levers' yearly figure
])
def test_a_route_that_reads_its_window_keeps_it(store, question, intent):
    request, _ = route(question, Context(DAY, 2, scope='shift', period='day'), store.production_days, store)
    assert request['intent'] == intent if intent else request['intent'] != 'semantic', (question, request['intent'])


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


# ---- The coordinator's review of PR #186 at 69d8a15 (round 2)

def test_window_words_said_short_are_model_entries():
    """Item 5: "wk", "mo", "Sept", "PTD", "Q-3" and "3Q" are the model's `windows.shorthand`, read by every route as the words they
    shorten; "next week" and "next month" are its `windows.future`, refused. Checked when the model loads."""
    from investigator import agent
    model = S.model()
    assert {'wk': 'week', 'mo': 'month', 'sept': 'september', 'ptd': 'period to date', 'q-3': 'q3', '3q': 'q3'}.items() <= model.shorthand.items()
    assert {'next week', 'next month'} <= set(model.future)
    assert agent.read_shorthand('downtime last wk in sept, ptd, q-3 or 3q') == 'downtime last week in september, period to date, q3 or q3'
    assert agent.read_shorthand('the wks-long run') == 'the wks-long run'   # never inside a hyphenated word
    spec = json.loads(S.MODEL_PATH.read_text())
    spec['windows']['shorthand']['words']['WK'] = 'week'
    with pytest.raises(ValueError, match='said short'):
        S.Model(spec)
    spec = json.loads(S.MODEL_PATH.read_text())
    spec['windows']['future']['words'].append('Next Year')
    with pytest.raises(ValueError, match='after the selected day'):
        S.Model(spec)
    page = S.model_markdown()
    assert '### Said short' in page and '### After the selected day' in page and '"next week"' in page


@pytest.mark.parametrize('lower, read', [
    ('downtime september 2026', 'downtime september'), ('msf in september, 2026', 'msf in september'),
    ('downtime during september of 2026', 'downtime during september'), ('how did december 2025 go?', 'how did december go?'),
    ('compare september 2026 to august 2026', 'compare september to august'),
    ('downtime on 10 september 2026', 'downtime on 10 september 2026'),      # a named date: not this rule's ("not this round")
    ('downtime september 10, 2026', 'downtime september 10, 2026'),
    ('downtime 2025', 'downtime 2025'),                                      # a year alone: refused by the parser, as before
])
def test_a_year_beside_a_month_is_that_month(lower, read):
    """Item 1: the year a month's name reads from the selected day is read away, so every route answers that month as main did."""
    from investigator import agent
    assert agent.month_years(lower, DAY, '2025-01-01') == read


@pytest.mark.parametrize('lower, said', [
    ('downtime august 2025', "^'august 2025' is not the August a month's name reads: that is the latest August on or before the selected day, August 2026"),
    ('downtime october 2026', "^'october 2026' is after the selected day"),
    ('downtime in december 2024', "^'december 2024' is before the records, which hold production days from 2025-01-01"),
])
def test_a_month_of_another_year_is_refused_naming_the_pair(lower, said):
    """Item 1: never another year's month (main answered August 2026 for "downtime August 2025")."""
    from investigator import agent
    with pytest.raises(agent.Standing, match=said):
        agent.month_years(lower, DAY, '2025-01-01')


@pytest.mark.parametrize('question', ['top operator reasons P1', 'top operator reasons in P9', 'downtime P1', 'how many stops in P12',
                                      'downtime P13'])
@pytest.mark.parametrize('scope', ['shift', 'day'])
def test_a_numbered_period_is_the_selected_one_or_refused_naming_it(store, question, scope):
    """Item 3: a 4-4-5 period said by its number answers only when it holds the selected day (P2 on 14 Sep 2026), on every route; the
    hand route ("downtime P1") answered P2 on main too."""
    context = Context(DAY, 2, scope=scope, period='day')
    with pytest.raises(ValueError, match=r"^'p1[0-3]?' is not|^'p[0-9]+' is not"):
        route(question, context, store.production_days, store)
    for kept in ('top operator reasons P2', 'how many stops in P2'):
        request, _ = route(kept, context, store.production_days, store)
        assert request['plan'][0]['arguments']['window']['period'] == 'fiscal'


@pytest.mark.parametrize('question, grain', [('downtime the last week', 'week'), ('msf in the last week', 'week'),
                                             ('top operator reasons over the last week', 'week'),
                                             ('top maintenance reasons the last month', 'month'), ('downtime the last period', 'period')])
def test_the_last_week_is_a_rolling_window_never_compared_with_nothing(store, question, grain):
    """Item 4: "the last week" is the trailing week, refused in the router's own words with the window before offered."""
    context = Context(DAY, 2, scope='shift', period='day')
    with pytest.raises(ValueError) as refused:
        route(question, context, store.production_days, store)
    text = str(refused.value)
    assert text.startswith(f"A rolling window ('the last {grain}') is not one the records keep") and 'compared with no window' not in text
    assert f"last {grain}'" in text


@pytest.mark.parametrize('question', ['top 200 orders by rejects YTD', 'top 200 days by msf YTD', 'top 200 customers by rejects YTD',
                                      'top 40 customers by rejects YTD'])
def test_a_long_ranking_is_bounded_for_the_planner_never_on_the_card(store, question):
    """Item 2: every top N up to MAX_LIMIT answers in full. Each row carries its record ids, so 40 customers or 75 days took the planner
    context past its bound and stopped at context_limit; the planner's view leaves them out, then the rows past PLANNER_ROWS."""
    from investigator import agent
    request, context = route(question, Context(DAY, 2, scope='shift', period='day'), store.production_days, store)
    analysis = store.day_analysis(context.production_day)
    try:
        result = Agent(analysis).ask(question, context, request=request)
    finally:
        analysis.close()
    assert result['status'] == 'complete'
    data = result['trace'][0]['data']
    assert len(data['rows']) == min(request['plan'][0]['arguments']['limit'], data['groups'])
    assert [len(t['rows']) for t in result['tables']] == [len(data['rows'])] and [len(c['rows']) for c in result['charts']] == [len(data['rows'])]
    step = {'result_id': 'r:1', 'tool': 'semantic_query', 'ok': True, 'data': data}
    assert 'source_ids' in data['rows'][0] and 'source_ids' not in agent.planner_view(step, 'ids')['data']['rows'][0]
    cut = agent.planner_view(step, 'rows')['data']
    assert len(cut['rows']) == min(agent.PLANNER_ROWS, len(data['rows'])) and cut.get('rows_left_out', 0) == max(0, len(data['rows']) - agent.PLANNER_ROWS)
    assert agent.planner_view(step) == step   # under the bound the planner sees the result whole, as before
