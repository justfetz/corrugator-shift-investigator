"""Issue #164: one money ledger. Every lever is its gap to its band in targets.json, the same cells add up from a shift to a
day, a week, a period to date and a year to date, a yearly figure comes only from period to date, each band names an owner
role, and a money answer never names a speed without saying which one."""
import re
from datetime import date, timedelta

import pytest

from investigator import cost_model, targets
from investigator.agent import Agent, Context
from investigator.record_store import default_store, fiscal_bounds

DAY = '2026-09-14'          # a Monday in 4-4-5 P2
WEEK = ('2026-09-14', '2026-09-18')


@pytest.fixture(scope='module')
def store():
    return default_store()


def rates(text=''):
    return cost_model.effective(cost_model.parse_rates(text))


def levers(data):
    return {r['key']: r for r in data['rows'] if not r.get('apart')}


def window_ledger(store, start, end, shift=None, text=''):
    """The ledger of any span, from the same cells every money tool reads: (quantities above the bands, dollars of each lever)."""
    cells, _, _ = cost_model.ledger_cells(store, start, end, shift)
    s = cost_model.ledger_sums(cells, rates(text))
    usd = {k: cost_model.price(cost_model.LEVER_KIND[k], s['over'][k], rates(text))['usd'] for k in cost_model.LEVERS}
    return s, usd


def close(total, parts, slack=0.005):
    """A total priced once and the sum of its parts priced each on their own agree to the cent, but for each figure's rounding."""
    return abs(total - sum(parts)) <= slack * (len(parts) + 1) + 1e-9


@pytest.mark.parametrize('text', ['', 'paper:60,hour:1350.5,margin:35,trim:3.1'])
def test_shift_day_week_period_and_year_to_date_add_up(store, text):
    """The owner's top rule: no money jumping around. A shift's dollars add to its day, the days to the week, the weeks to the
    period to date, the periods to the year to date: each lever's quantity above its band exactly (the same cells' gaps), each
    lever's dollars and the totals to the cent but for rounding."""
    r = rates(text)
    week_days = [d for d in store.production_days if WEEK[0] <= d <= WEEK[1]]
    days = []
    for day in week_days:
        analysis = store.day_analysis(day)
        try:
            shifts = [cost_model.shift_dollars(analysis, n, r) for n in (1, 2, 3)]
            whole = cost_model.day_dollars(analysis, r)
        finally:
            analysis.close()
        for key in cost_model.LEVERS:   # each lever's quantity above its band: the shifts' gaps, added
            assert levers(whole)[key]['quantity'] == round(sum(levers(s)[key]['quantity'] for s in shifts), 2), (day, key)
            assert close(levers(whole)[key]['usd'], [levers(s)[key]['usd'] for s in shifts]), (day, key)
        assert close(whole['total_usd'], [s['total_usd'] for s in shifts]), day
        assert close(whole['at_standard_usd'], [s['at_standard_usd'] for s in shifts], 0.01), day
        days.append(whole)
    week = cost_model.opportunities(store, {'period': 'week', 'anchor': DAY}, None, r)
    for key in cost_model.LEVERS:
        assert levers(week)[key]['quantity'] == round(sum(levers(d)[key]['quantity'] for d in days), 2), key
        assert close(levers(week)[key]['usd'], [levers(d)[key]['usd'] for d in days]), key
    assert close(week['total_usd'], [d['total_usd'] for d in days])
    # the period to date the cards state is the 4-4-5 period's own ledger: its weeks add to it, and its days to it
    start, end, _ = cost_model.ptd_window(store, WEEK[1])
    ptd, ptd_usd = window_ledger(store, start, end, text=text)
    weeks, monday = [], start
    while monday <= end:
        weeks.append(window_ledger(store, monday, min(monday + timedelta(days=4), end), text=text))
        monday += timedelta(days=7)
    for key in cost_model.LEVERS:
        assert ptd['over'][key] == round(sum(w[0]['over'][key] for w in weeks), 2), key
        assert close(ptd_usd[key], [w[1][key] for w in weeks]), key
    assert close(days[-1]['ptd_usd'], [sum(w[1].values()) for w in weeks], 0.04)
    # and the year to date is its periods' ledgers added up
    year_start, periods = date(2026, 8, 3), []   # the demo 4-4-5 year (record_store.FISCAL_START)
    moment = year_start
    while moment <= date.fromisoformat(WEEK[1]):
        p_start, p_end, _ = fiscal_bounds(moment)
        periods.append(window_ledger(store, p_start, min(p_end, date.fromisoformat(WEEK[1])), text=text))
        moment = p_end + timedelta(days=1)
    ytd, ytd_usd = window_ledger(store, year_start, WEEK[1], text=text)
    assert len(periods) >= 2
    for key in cost_model.LEVERS:
        assert ytd['over'][key] == round(sum(p[0]['over'][key] for p in periods), 2), key
        assert close(ytd_usd[key], [p[1][key] for p in periods]), key


def test_one_window_reads_the_same_figure_in_every_view(store):
    """A shift's card and the levers of the same shift and day are one figure; the levers and what trim costs agree."""
    analysis = store.day_analysis(DAY)
    try:
        card = cost_model.shift_dollars(analysis, 2, rates())
    finally:
        analysis.close()
    window = cost_model.opportunities(store, {'period': 'day', 'anchor': DAY}, 2, rates())
    assert {k: (r['quantity'], r['usd']) for k, r in levers(card).items()} == {k: (r['quantity'], r['usd']) for k, r in levers(window).items()}
    assert card['total_usd'] == window['total_usd'] and card['at_standard_usd'] == window['at_standard_usd']
    owner = cost_model.opportunities(store, {'period': 'day', 'anchor': DAY}, 2, rates(), 'ownership')
    assert owner['rows'][0]['above_standard_usd'] == levers(window)['trim']['usd']


def test_a_lever_inside_its_band_is_zero_and_its_cost_is_at_standard(store):
    """The owner's call 1: trim, shear and dry end inside their bands show $0 opportunity; their cost is context, at standard.
    Second shift on 2026-09-09 ran side trim under 3% and shear under 0.75% (money-on-target-trim-not-first)."""
    analysis = store.day_analysis('2026-09-09')
    try:
        card = cost_model.shift_dollars(analysis, 2, rates())
    finally:
        analysis.close()
    trim, shear = levers(card)['trim'], levers(card)['shear']
    assert trim['usd'] == 0 and trim['quantity'] == 0 and shear['usd'] == 0 and trim['rank'] != 1
    at = {r['key']: r for r in card['rows'] if r.get('apart') == 'at-standard'}
    for key in ('trim', 'shear', 'dry_end'):   # all recorded = at standard + above standard, to the cent
        assert at[key]['all_recorded_usd'] == round(at[key]['usd'] + levers(card)[key]['usd'], 2)
    assert card['total_usd'] == round(sum(r['usd'] for r in levers(card).values()), 2)
    # a lower target moves trim above its band in the same records: the band, not the record, decides the lever
    lower = cost_model.shift_dollars(store.day_analysis('2026-09-09'), 2, rates('trim:2.5'))
    assert levers(lower)['trim']['usd'] > 0 and levers(lower)['trim']['lever'] == 'Side trim over the 2.5% target'


def test_a_yearly_figure_comes_only_from_period_to_date(store):
    """The owner's call 3: never annualize a shift or a day. Every shift of a day states the same yearly figure, the machine's
    period to date at this period's rate; a window's yearly figure is its own scope's period to date, a shift's never scaled
    to the machine; a period's first week reads its rate with the period before it."""
    analysis = store.day_analysis(DAY)
    try:
        cards = [cost_model.shift_dollars(analysis, n, rates()) for n in (1, 2, 3)] + [cost_model.day_dollars(analysis, rates())]
    finally:
        analysis.close()
    assert len({c['ptd_usd_per_year'] for c in cards}) == 1 and all(c['total_usd_per_year'] is None for c in cards)
    assert all(r['usd_per_year'] is None for c in cards for r in c['rows'])
    start, end, label = cost_model.ptd_window(store, DAY)
    assert (start.isoformat(), end.isoformat(), label) == ('2026-08-31', DAY, '4-4-5 P2 to date')
    ptd, usd = window_ledger(store, start, end)
    assert cards[0]['ptd_days'] == ptd['days'] and cards[0]['ptd_usd_per_year'] == sum(
        cost_model.annual(usd[k], ptd['days']) for k in cost_model.LEVERS)
    # shift 1's month: its own period to date, about a third of the machine's, never the machine's year
    machine = cost_model.opportunities(store, {'period': 'month', 'anchor': DAY}, None, rates())
    first = cost_model.opportunities(store, {'period': 'month', 'anchor': DAY}, 1, rates())
    assert first['ptd_scope'] == 'shift 1' and first['total_usd_per_year'] < machine['total_usd_per_year'] / 2
    # 28 September opens P3: under a production week in, the rate reads P2 with it, never one day's
    assert cost_model.ptd_window(store, '2026-09-28')[2] == '4-4-5 P2 and P3 to date'
    assert cost_model.ptd_window(store, '2026-09-28')[0] == date(2026, 8, 31)


def test_every_band_names_an_owner_role_and_the_owner_sets_it(store, monkeypatch):
    """The owner's call 4: each band in targets.json carries an owner role, "owner to set" until he sets it."""
    assert all('owner' in b for b in targets.DEFINITION['bands']) and 'owner' in targets.DEFINITION['speed']
    analysis = store.day_analysis(DAY)
    try:
        card = cost_model.shift_dollars(analysis, 2, rates())
        assert {r['owner'] for r in levers(card).values()} == {targets.OWNER_TO_SET}
        monkeypatch.setitem(targets.BANDS['dry_end'], 'owner', 'wet-end lead')
        assert levers(cost_model.shift_dollars(analysis, 2, rates()))['dry_end']['owner'] == 'wet-end lead'
    finally:
        analysis.close()
    sections = Agent(store.day_analysis(DAY)).ask('Where did the money go this shift?', Context(DAY, 2, scope='shift'))['sections']
    assert any(' · owner role: wet-end lead · look at: ' in s for s in sections if s.startswith('- ') and 'Dry end over' in s)


# Every money view: a shift, a day, two shifts, a week, a month, a 4-4-5 period, each lens, a shift's window.
MONEY_ANSWERS = (('Where did the money go this shift?', 'shift', 'day', 2), ('Where did the money go?', 'day', 'day', 2),
                 ('Where is the money this week?', 'day', 'day', 2), ('Where is the money this month?', 'shift', 'day', 1),
                 ('Where is the money?', 'day', 'fiscal', 2), ("Where's my biggest opportunity this month?", 'day', 'day', 2),
                 ("What's trim costing us this month?", 'day', 'day', 2), ('What did slow running cost us this week?', 'shift', 'day', 2),
                 ('What did waste cost this shift?', 'shift', 'day', 3))
NAMED = re.compile(r'grade baseline|grade\'s trailing-year baseline|required (run )?speed', re.I)
SPEED = re.compile(r'ft/min|\bspeed\b', re.I)


@pytest.mark.parametrize('question, scope, period, shift', MONEY_ANSWERS)
def test_a_money_answer_never_names_a_speed_without_saying_which(store, question, scope, period, shift):
    """The owner's call 2: keep both speeds, never as one definition. Every line of a money answer that names a speed (a
    figure in ft/min, or the word) says which: each grade's baseline (the speed lever) or the required speed at 10% downtime
    (rates.json, the conversion); and the 675 ft/min appears only as the required speed."""
    result = Agent(store.day_analysis(DAY)).ask(question, Context(DAY, shift, scope=scope, period=period))
    money = [s for s in result['trace'] if s['ok'] and s['tool'] in ('get_shift_dollars', 'get_day_dollars', 'get_opportunities')]
    assert money, question
    for line in result['sections']:
        if SPEED.search(line) and '→ “' not in line:   # a where-to-look link is a question, not a figure
            assert NAMED.search(line), line
        if '675' in line:
            assert re.search(r'required speed, 675 ft/min', line), line
    for step in money:
        for row in step['data']['rows']:
            for field in ('lever', 'records', 'look_at', 'usd_basis'):
                text = str(row.get(field) or '')
                assert not re.search(r'ft/min', text) or NAMED.search(text), (field, text)
                assert not re.search(r'speed gap to', text, re.I), (field, text)


def test_the_named_speed_check_fails_on_a_second_definition():
    """The check above is not vacuous: the old shift card's speed line, the gap to 675 ft/min, names no definition."""
    old = '- 10. Speed gap to 675 ft/min: **$145** · $111,577/yr (query 1 · row 9)'
    assert SPEED.search(old) and not NAMED.search(old)
    new = '- 2. Speed under the grade baseline: **$863** · 25 of 40 wet-end runs under their grade baseline (query 4 · row 0)'
    assert NAMED.search(new)
