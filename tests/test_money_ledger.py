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
    assert (start.isoformat(), end.isoformat(), label) == ('2026-08-31', DAY, '4-4-5 P2 to date through Mon 14 Sep')   # its end day (PR #168)
    ptd, usd = window_ledger(store, start, end)
    # the yearly base is the period to date's whole dollars, its cells' added up (the coordinator's review of 82c0994, item 2)
    cells, _, _ = cost_model.ledger_cells(store, start, end)
    whole = cost_model.whole_dollars(cost_model.display_terms(cells, rates()), rates())
    assert [r['usd_shown'] for r in cards[0]['ptd_levers']] == [whole['levers'][k] for k in cost_model.LEVERS]
    # each shift's own yearly figure at its own production days, the machine's theirs added up (the review of 2510d38, item 1)
    days = {sh: len({c['production_day'] for c in cells if c['shift'] == sh and c['setups']}) for sh in (1, 2, 3)}
    assert cards[0]['ptd_days'] == ptd['days'] and cards[0]['ptd_usd_per_year'] == sum(
        cost_model.annual(e['levers'][k], days[e['shift']]) for k in cost_model.LEVERS for e in whole['shifts'])
    for lever in cards[0]['ptd_levers']:
        assert [(b['shift'], b['days'], b['usd_shown']) for b in lever['by_shift']] == [(e['shift'], days[e['shift']], e['levers'][lever['key']])
                                                                                       for e in whole['shifts']]
        assert lever['usd_per_year'] == sum(b['usd_per_year'] for b in lever['by_shift'])
    # shift 1's month: its own period to date, about a third of the machine's, never the machine's year
    machine = cost_model.opportunities(store, {'period': 'month', 'anchor': DAY}, None, rates())
    first = cost_model.opportunities(store, {'period': 'month', 'anchor': DAY}, 1, rates())
    assert first['ptd_scope'] == 'shift 1' and first['total_usd_per_year'] < machine['total_usd_per_year'] / 2
    # 28 September opens P3: under a production week in, the rate reads P2 with it, never one day's
    assert cost_model.ptd_window(store, '2026-09-28')[2] == '4-4-5 P2 and P3 to date through Mon 28 Sep'
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
        if re.search(r'(?<![\d,.])675(?![\d,])', line):   # the speed's own figure, never a dollar figure like $1,675
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


# ---- PR #168's fix round: whole dollars that add up (the owner's top rule), no yearly figure from a few days, the period
# to date naming its end day, side trim by shift, and the KPI operator tile as the ledger prints it

def day_cards(store, day, text=''):
    analysis = store.day_analysis(day)
    try:
        return cost_model.day_dollars(analysis, rates(text)), [cost_model.shift_dollars(analysis, n, rates(text)) for n in (1, 2, 3)]
    finally:
        analysis.close()


def test_largest_remainder_adds_to_its_target_and_never_lifts_a_zero():
    """Floors, then a dollar each to the largest remainders, a tie to the earlier part; a target past what the floors and
    remainders reach moves the largest parts, a $0 part never rises and no part falls below $0."""
    lr = cost_model.largest_remainder
    assert lr([16240, 35060, 14270], 656) == [162, 351, 143]   # floors 655, one more to the largest remainder (.70)
    assert lr([16240, 35060, 14270], 655) == [162, 350, 143]
    assert lr([1050, 1050], 21) == [11, 10]                     # a tie to the earlier part
    assert lr([0, 9999, 1], 101) == [0, 100, 1]                 # $0 stays $0; the larger remainders rise first
    assert lr([0, 10000, 0], 101) == [0, 101, 0]                # past the remainders: the largest part moves, never a $0 one
    assert lr([0, 500, 300], 6) == [0, 4, 2]                    # under the floors: a dollar off the largest, then the next
    assert lr([100, 200], 2) == [1, 1] and lr([0, 0], 0) == [0, 0] and lr([0, 100], 0) == [0, 0]   # never below $0
    assert [cost_model.nearest_dollar(c) for c in (65549, 65550, 65650, 65551, -150)] == [655, 656, 656, 656, -2]   # a tie to even


@pytest.mark.parametrize('day', ['2026-06-10', '2026-09-28', '2026-08-31', '2026-09-30'])
def test_a_day_card_prints_whole_dollars_that_add_up(store, day):
    """The review's item 1 (10 Jun printed $655 over shift lines adding to $656): each shift prints its nearest dollar (the cell
    is the root: the coordinator's review of 82c0994, item 2), the day its shifts' whole dollars added up, the levers too; every
    set of parts adds to its total, and each shift card prints what its line on the day card prints."""
    whole, shifts = day_cards(store, day)
    assert [s['total_usd_shown'] for s in shifts] == [cost_model.nearest_dollar(cost_model.to_cents(s['total_usd'])) for s in shifts]
    assert abs(whole['total_usd_shown'] - whole['total_usd']) <= 0.5 * len(shifts) + 0.01
    assert [s['total_usd_shown'] for s in shifts] == [x['usd_shown'] for x in whole['shifts']]
    assert sum(s['total_usd_shown'] for s in shifts) == whole['total_usd_shown']
    for data in [whole, *shifts]:
        lev = [r for r in data['rows'] if not r.get('apart')]
        at = [r for r in data['rows'] if r.get('apart') == 'at-standard']
        assert sum(r['usd_shown'] for r in lev) == data['total_usd_shown']
        assert sum(r['usd_shown'] for r in at) == data['at_standard_usd_shown']
        assert sum(r['usd_shown'] for r in data['rows']) == data['all_in_usd_shown']
        assert data['total_paper_usd_shown'] + data['total_machine_time_usd_shown'] == data['total_usd_shown']
        assert sum(b['usd_shown'] for b in data['buckets']) == data['total_usd_shown']
        for r in data['rows']:   # each printed figure is its cents' floor or ceiling, a shift's line within a dollar a shift
            assert abs(r['usd_shown'] - r['usd']) < (1 if data['shift'] else 3), (day, r['lever'], r['usd'], r['usd_shown'])
    for k in cost_model.LEVERS:   # the day's lever is its shifts' whole dollars added up
        assert levers(whole)[k]['usd_shown'] == sum(levers(s)[k]['usd_shown'] for s in shifts), (day, k)
    trim = [d['usd_shown'] for d in whole['details']]
    assert trim == [levers(s)['trim']['usd_shown'] for s in shifts] and sum(trim) == levers(whole)['trim']['usd_shown']


def test_a_window_s_shifts_print_the_same_whole_dollars_on_every_card(store):
    """A window card for all shifts adds its shifts' whole dollars, and each shift's own window card prints that share: the
    trim by shift (both lenses), the ownership lens's split against the levers, and the KPI operator and maintenance tiles."""
    window, r = {'period': 'fiscal', 'anchor': DAY}, rates()
    every = {lens: cost_model.opportunities(store, window, None, r, lens) for lens in ('all', 'ownership')}
    mine = {(sh, lens): cost_model.opportunities(store, window, sh, r, lens) for sh in (1, 2, 3) for lens in ('all', 'ownership')}
    assert sum(mine[(sh, 'all')]['total_usd_shown'] for sh in (1, 2, 3)) == every['all']['total_usd_shown']
    for d in every['all']['details']:
        assert d['usd_shown'] == levers(mine[(d['shift'], 'all')])['trim']['usd_shown']
    for d in every['ownership']['details']:
        assert d['usd_shown'] == mine[(d['shift'], 'ownership')]['rows'][0]['usd_shown']
    own, lev = every['ownership']['rows'][0], levers(every['all'])['trim']
    at = next(x for x in every['all']['rows'] if x.get('apart') == 'at-standard' and x['key'] == 'trim')
    assert (own['above_standard_usd_shown'], own['at_standard_usd_shown']) == (lev['usd_shown'], at['usd_shown'])
    assert own['usd_shown'] == own['above_standard_usd_shown'] + own['at_standard_usd_shown']
    o = every['ownership']
    assert o['total_usd_shown'] == sum(x['usd_shown'] for x in o['rows']) == o['at_standard_usd_shown'] + o['above_standard_usd_shown'] + o['no_band_usd_shown']
    assert o['dry_end_usd_shown'] == sum(x['usd_shown'] for x in o['rows'] if x.get('group') == 'dry_end')
    s, terms = cost_model.period_whole(store, window, None, r)
    data = {'current_sums': store.get_period_kpis(window)['current_sums'], 'window': store._window(window), 'shift': None}
    tiles = {t['metric']: t for t in cost_model.period_tiles(data, store.get_downtime_pareto(window, 'reason', 'minutes', None)['rows'], r, (s, terms))}
    rows = {(x['key'], x.get('apart')): x['usd_shown'] for x in every['all']['rows']}
    assert tiles['operator_pct']['usd_shown'] == rows[('operator', None)] + rows[('operator', 'at-standard')]   # item 9: P2's $12,990
    assert tiles['maintenance_pm_pct']['usd_shown'] == rows[('maintenance', None)] + rows[('maintenance', 'at-standard')] + rows.get(('scheduled', 'schedule-driven'), 0)


@pytest.mark.parametrize('window', [{'period': 'week', 'anchor': '2025-01-13'}, {'period': 'week', 'anchor': '2025-01-01'},
                                    {'period': 'month', 'anchor': DAY}])
@pytest.mark.parametrize('shift', [None, 2])
@pytest.mark.parametrize('text', ['', 'paper:47.5,hour:1450,trim:2.75'])
def test_every_view_of_a_waste_line_prints_one_figure(store, window, shift, text):
    """The reviewer's finding 1 on PR #168's fix round: the week of 13 Jan 2025's KPI tiles printed side trim $62,760 and the
    dry end $22,821 against the ownership lens's $62,761 and $22,822, and the first week's dry-end families $12,530 under a
    $12,529 dry-end line. Every waste tile, the waste breakdown's line and the ownership lens's line print the money ledger's
    whole dollars for it; the families add to the dry-end line, the stop tiles to the ledger's stop lines."""
    r = rates(text)
    s, terms = cost_model.period_whole(store, window, shift, r)
    data = cost_model.attach('get_period_kpis', store.get_period_kpis(window, shift=shift), r)
    tiles = {t['metric']: t for t in cost_model.period_tiles(data, store.get_downtime_pareto(window, 'reason', 'minutes', shift)['rows'], r, (s, terms))}
    waste = cost_model.waste_shown(cost_model.attach('get_waste_breakdown', store.get_waste_breakdown(window, shift), r), terms, r)
    lines = {x['category']: x['usd_shown'] for x in waste['rows']}
    own = {x['key']: x['usd_shown'] for x in cost_model.opportunities(store, window, shift, r, 'ownership')['rows']}
    ledger = {(x['key'], x.get('apart')): x['usd_shown'] for x in cost_model.opportunities(store, window, shift, r)['rows']}
    for key, shown in lines.items():
        if shown is None:
            continue
        if f'{key}_pct' in tiles:
            assert tiles[f'{key}_pct']['usd_shown'] == shown, key
        if key in own:
            assert own[key] == shown, key
    assert sum(lines[k] for k in cost_model.DRY_END_KINDS) == lines['dry_end']
    for key in ('trim', 'shear', 'dry_end'):
        assert lines[key] == ledger[(key, None)] + ledger.get((key, 'at-standard'), 0)
    assert lines['changeover'] == ledger.get(('changeover', 'no-band'), 0)
    assert lines['cutoff_waste'] + lines['stacker_waste'] == ledger.get(('boards', 'no-band'), 0)
    stops = sum(ledger.get(k, 0) for k in [('operator', None), ('operator', 'at-standard'), ('maintenance', None), ('maintenance', 'at-standard'),
                                           ('scheduled', 'schedule-driven'), ('external', 'external')])
    assert tiles['downtime_pct']['usd_shown'] == stops


def test_the_data_quality_toggle_moves_no_shift_it_leaves_complete(store):
    """The reviewer's finding 2 on PR #168's fix round: on 16 Feb 2026 shift 1 is $334.36 either way, yet its card printed $334
    without the data-quality toggle and $335 with it, the day's root having dropped the incomplete shift 2. The root is the
    day's own records in either view. A day card asked only for the incomplete shift prints that shift's own card (finding 5)."""
    day, r = '2026-02-16', rates()
    plain, bad = store.day_analysis(day), store.day_analysis(day, corrupt=True)
    try:
        assert bad.errors and all(e.get('shift') == 2 for e in bad.errors)
        for n in (1, 3):
            a, b = cost_model.shift_dollars(plain, n, r), cost_model.shift_dollars(bad, n, r)
            assert a['total_usd'] == b['total_usd']
            assert a['total_usd_shown'] == b['total_usd_shown'] and [x['usd_shown'] for x in a['rows']] == [x['usd_shown'] for x in b['rows']]
        whole, toggled = cost_model.day_dollars(plain, r), cost_model.day_dollars(bad, r)
        assert toggled['excluded_shifts'] == [2]
        mine = {e['shift']: e['usd_shown'] for e in whole['shifts']}
        assert {e['shift']: e['usd_shown'] for e in toggled['shifts'] if 'usd_shown' in e} == {1: mine[1], 3: mine[3]}
        assert toggled['total_usd_shown'] == mine[1] + mine[3]
        only = cost_model.day_dollars(bad, r, (2,))
        assert only['total_usd_shown'] == cost_model.shift_dollars(bad, 2, r)['total_usd_shown'] > 0
    finally:
        plain.close()
        bad.close()


def test_no_yearly_figure_from_fewer_than_five_production_days(store):
    """The review's item 2: on the records' first day the card stated $403,694 a year, one day x 257.14, under a period that
    began before the records. With fewer than five production days there is no yearly figure and the card says why; the
    period is labelled from the records' first day."""
    first = store.production_days[0]
    assert first == '2025-01-01'
    for day in store.production_days[:6]:
        start, end, label = cost_model.ptd_window(store, day)
        days = len([d for d in store.production_days if start.isoformat() <= d <= day])
        assert start.isoformat() == first and 'from the first day of the records, Wed 1 Jan 2025' in label, label
        whole, _ = day_cards(store, day)
        if days < cost_model.MIN_RATE_DAYS:
            assert whole['ptd_usd_per_year'] is None and all(r['usd_per_year'] is None for r in whole['ptd_levers'])
            assert whole['ptd_year_note'] == f'no yearly figure: {days} production day{"" if days == 1 else "s"} to date, fewer than 5'
        else:
            assert whole['ptd_usd_per_year'] is not None and whole['ptd_year_note'] is None
    assert cost_model.annual(1000.0, 4) is None and cost_model.annual(1000.0, 5) == round(1000.0 / 5 * cost_model.DAYS_PER_YEAR)


def test_the_period_to_date_names_its_end_day(store):
    """The coordinator's default on PR #168: "4-4-5 P2 to date through Mon 14 Sep", so a day and its week never state two
    yearly figures under one label."""
    assert cost_model.ptd_window(store, DAY)[2] == '4-4-5 P2 to date through Mon 14 Sep'
    assert cost_model.ptd_window(store, '2026-09-18')[2] == '4-4-5 P2 to date through Fri 18 Sep'


def test_no_money_note_carries_a_dollar_figure():
    """A note no row prices (the method, the speeds, whole dollars) never carries a $ figure: web/money.js re-prices every
    $ token of the prose, so "adds $0" in the method note would read as a lever's dollars at another rate (the sweep of PR
    #168 found it)."""
    from investigator import shapes
    for note in (cost_model.LEDGER_NOTE, cost_model.SPEEDS_NOTE, shapes.WHOLE_NOTE, shapes.TRIM_BY_SHIFT):
        assert '$' not in note, note


def test_side_trim_by_shift_is_explanatory_and_shows_inside_the_band(store):
    """The owner's word on PR #168: each shift's side trim % of gross and its setups above the per-setup trim flag, beside its
    dollars, even on a day inside the band (10 Jun ran 2.98%), labelled schedule-driven."""
    whole, shifts = day_cards(store, '2026-06-10')
    assert [d['shift'] for d in whole['details']] == [1, 2, 3]
    for d, s in zip(whole['details'], shifts):
        cell = levers(s)['trim']
        assert d['part'] == 'above' and d['control'] == 'schedule-driven' and d['trim_flag_pct'] == targets.TRIM_FLAG_PCT
        assert d['trim_pct_of_gross'] is not None and d['setups'] >= d['setups_above_trim_flag'] >= 0
        assert d['usd'] == cell['usd'] and d['quantity'] == cell['quantity'] and d['shifts_over'] == cell['shifts_over']
    analysis = store.day_analysis('2026-06-10')
    try:
        assert cost_model.shift_dollars(analysis, 2, rates()).get('details') is None   # one shift: its own lever line says it
    finally:
        analysis.close()


# ---- the coordinator's review of 82c0994 on PR #168: the cell is the whole-dollar root, so nested windows add up as printed

NESTED = ('2026-07-01', '2026-09-30')   # July to September (the golden window ends 30 Sep)


def printed_lines(data):
    """A ledger card's printed figures, keyed so two cards of nested windows can be added: its total, each lever, each row at
    standard and apart, everything recorded and priced, paper and machine time."""
    out = {('total',): data['total_usd_shown'], ('at',): data['at_standard_usd_shown'], ('all_in',): data['all_in_usd_shown'],
           ('paper',): data['total_paper_usd_shown'], ('machine',): data['total_machine_time_usd_shown']}
    out.update({(r['key'], r.get('apart') or 'lever'): r['usd_shown'] for r in data['rows']})
    return out


def owned_lines(data):
    """The ownership lens's printed figures: each line, the tag scrap, its splits, all the waste, paper and machine time."""
    out = {(r['key'],): r['usd_shown'] for r in data['rows']}
    out.update({(k,): data.get(f'{k}_usd_shown') or 0 for k in ('total', 'above_standard', 'at_standard', 'no_band', 'dry_end',
                                                             'total_paper', 'total_machine_time')})
    return out


def added(cards, lines):
    out = {}
    for c in cards:
        for k, v in lines(c).items():
            out[k] = out.get(k, 0) + v
    return out


@pytest.mark.parametrize('text', ['', 'paper:47.5,hour:1450,margin:40,trim:2.75'])
def test_displayed_days_add_to_their_weeks_months_periods_and_periods_to_date(store, text):
    """The coordinator's review of 82c0994 on PR #168, items 2 and 3: the week of 7 Sep's five day cards printed $12,546 and
    the week $12,547; September's days $66,790 and the month $66,792, shift 3's $22,935 and $22,939; the day cards' "part of $X"
    chain broke on 17 steps; 1 Jan 2025's ownership lens printed $22,540 over its shifts and $22,541 for the day. The cell, one
    shift of one day, is the whole-dollar root, so over July to September, at the post's rates and at others: each day card is
    its shift cards, and every week, month, 4-4-5 period and period to date, for all shifts and for each shift, prints its days'
    (or its shift cards') printed figures added up, line by line; the ownership lens's lines and the KPI tiles add up day by
    day and shift by shift."""
    r = rates(text)
    cache, owned, tiled = {}, {}, {}

    def tiles_of(window, shift):
        s, terms = cost_model.period_whole(store, window, shift, r)
        data = cost_model.attach('get_period_kpis', store.get_period_kpis(window, shift=shift), r)
        pareto = store.get_downtime_pareto(window, 'reason', 'minutes', shift)['rows']
        return {t['metric']: t['usd_shown'] for t in cost_model.period_tiles(data, pareto, r, (s, terms))}

    def own_day(d):   # a day's ownership lens and KPI tiles, all shifts
        if d not in owned:
            owned[d] = cost_model.opportunities(store, {'period': 'day', 'anchor': d}, None, r, 'ownership')
            tiled[d] = tiles_of({'period': 'day', 'anchor': d}, None)
        return owned[d], tiled[d]

    def day(d):
        if d not in cache:
            analysis = store.day_analysis(d)
            try:
                cache[d] = (cost_model.day_dollars(analysis, r), {n: cost_model.shift_dollars(analysis, n, r) for n in (1, 2, 3)})
            finally:
                analysis.close()
        return cache[d]

    days = [d for d in store.production_days if NESTED[0] <= d <= NESTED[1]]
    for d in days:   # a day card is its shift cards, line by line, and so is its period to date
        whole, shifts = day(d)
        assert printed_lines(whole) == added(shifts.values(), printed_lines), d
        assert [e['usd_shown'] for e in whole['shifts']] == [shifts[n]['total_usd_shown'] for n in (1, 2, 3)], d
    windows = []
    monday = date.fromisoformat(NESTED[0]) - timedelta(days=date.fromisoformat(NESTED[0]).weekday())
    while monday.isoformat() <= NESTED[1]:
        windows.append({'period': 'week', 'anchor': monday.isoformat()})
        monday += timedelta(days=7)
    windows += [{'period': 'month', 'anchor': m} for m in ('2026-07-01', '2026-08-03', '2026-09-01')]
    moment = date.fromisoformat(NESTED[0])
    while moment.isoformat() <= NESTED[1]:
        windows.append({'period': 'fiscal', 'anchor': moment.isoformat()})
        moment = fiscal_bounds(moment)[1] + timedelta(days=1)
    checked = 0
    for window in windows:
        w = store._window(window)
        inside = [d for d in store.production_days if w['start'] <= d <= w['end']]
        for shift in (None, 1, 2, 3):
            card = cost_model.opportunities(store, window, shift, r)
            parts = [day(d)[0] if shift is None else day(d)[1][shift] for d in inside]
            got, want = printed_lines(card), added(parts, printed_lines)
            assert {k: v for k, v in got.items() if k[0] != 'lever'} == want, (window, shift)   # every line, the totals too
            assert card['ptd_usd_shown'] == sum(day(d)[0]['total_usd_shown'] if shift is None else day(d)[1][shift]['total_usd_shown']
                                                for d in store.production_days if card['ptd_start'] <= d <= card['ptd_through']), (window, shift)
            assert [x['usd_shown'] for x in card['ptd_levers']] == [sum(levers(day(d)[0] if shift is None else day(d)[1][shift])[k]['usd_shown']
                                                                         for d in store.production_days if card['ptd_start'] <= d <= card['ptd_through'])
                                                                     for k in cost_model.LEVERS], (window, shift)
            checked += 1
        # the ownership lens and the KPI tiles (item 3): the shifts add to all shifts, and the days to the window, every line
        own = {sh: cost_model.opportunities(store, window, sh, r, 'ownership') for sh in (None, 1, 2, 3)}
        assert owned_lines(own[None]) == added([own[sh] for sh in (1, 2, 3)], owned_lines), window
        assert owned_lines(own[None]) == added([own_day(d)[0] for d in inside], owned_lines), window
        tiles = {sh: tiles_of(window, sh) for sh in (None, 1, 2, 3)}
        assert tiles[None] == {m: sum(tiles[sh][m] for sh in (1, 2, 3)) for m in tiles[None]}, window
        assert tiles[None] == {m: sum(own_day(d)[1][m] for d in inside) for m in tiles[None]}, window
    assert checked == 4 * len(windows) >= 4 * 20
    # the "part of $X" chain: a day's period to date is the day before's and the day, in one period to date
    for before, d in zip(days, days[1:]):
        a, b = day(before)[0], day(d)[0]
        if a['ptd_start'] == b['ptd_start']:
            assert b['ptd_usd_shown'] == a['ptd_usd_shown'] + b['total_usd_shown'], d
    # 1 Jan 2025's ownership lens: its shifts add to the day, every line (item 3)
    first = {'period': 'day', 'anchor': '2025-01-01'}
    own = {sh: cost_model.opportunities(store, first, sh, r, 'ownership') for sh in (None, 1, 2, 3)}
    assert owned_lines(own[None]) == added([own[sh] for sh in (1, 2, 3)], owned_lines)


def yearly_figures(data):
    """A money window card's printed yearly figures, keyed so the shift cards of a window can be added: each lever's and each
    ownership line's (`usd_per_year`), and the totals a card states a year (all the levers, the controllable ones, the tag
    scrap, the period to date)."""
    out = {('row', r['key']): r['usd_per_year'] for r in data['rows'] if r.get('usd_per_year') is not None}
    out.update({('card', k): data[k] for k in ('total_usd_per_year', 'controllable_usd_per_year', 'dry_end_usd_per_year', 'ptd_usd_per_year')
                if data.get(k) is not None})
    return out


@pytest.mark.parametrize('text', ['', 'paper:47.5,hour:1450,margin:40,trim:2.75'])
def test_the_shifts_yearly_figures_add_to_the_all_shifts_card(store, text):
    """The coordinator's review of 2510d38 on PR #168, item 1: the week of 7 Sep's shift cards said $287,307 + $217,646 +
    $229,629 = $734,582 a year and the all-shifts card $734,580; the levers missed in 14 of 22 windows, and the ownership lens's
    all the waste recorded on 57 of 70 day cards and 20 of 22 windows. Each scope rounded its own period to date to the dollar
    once. The all-shifts yearly figure of each lever and each line is the shifts' own printed yearly figures added up, so over
    July to September, every day, week, month and 4-4-5 period, both lenses, the shift cards' yearly figures add to the
    all-shifts card's, figure by figure, and every figure the all-shifts card states a year has its three shifts' beside it."""
    r = rates(text)
    windows = [{'period': 'day', 'anchor': d} for d in store.production_days if NESTED[0] <= d <= NESTED[1]]
    monday = date.fromisoformat(NESTED[0]) - timedelta(days=date.fromisoformat(NESTED[0]).weekday())
    while monday.isoformat() <= NESTED[1]:
        windows.append({'period': 'week', 'anchor': monday.isoformat()})
        monday += timedelta(days=7)
    windows += [{'period': 'month', 'anchor': m} for m in ('2026-07-01', '2026-08-03', '2026-09-01')]
    moment = date.fromisoformat(NESTED[0])
    while moment.isoformat() <= NESTED[1]:
        windows.append({'period': 'fiscal', 'anchor': moment.isoformat()})
        moment = fiscal_bounds(moment)[1] + timedelta(days=1)
    checked = 0
    for window in windows:
        for lens in ('all', 'ownership'):
            cards = {sh: cost_model.opportunities(store, window, sh, r, lens) for sh in (None, 1, 2, 3)}
            every, mine = yearly_figures(cards[None]), [yearly_figures(cards[sh]) for sh in (1, 2, 3)]
            assert every and all(set(m) == set(every) for m in mine), (window, lens)
            assert every == {k: sum(m[k] for m in mine) for k in every}, (window, lens)
            # and on every card the total a year is its rows' added up, the rows each its shifts'
            for figures in (every, *mine):
                assert figures[('card', 'total_usd_per_year')] == sum(v for k, v in figures.items() if k[0] == 'row'), (window, lens)
            checked += len(every)
    assert len(windows) >= 66 + 20 and checked > 2000


def test_a_shift_with_none_of_a_line_states_its_yearly_figure_beside_zero_dollars(store):
    """The reviewer's gap on the round on 2510d38: on 29 Jan 2025 shift 2 recorded no planning / admin, so its ownership card
    left the line out, and the $15,637 a year its period to date holds with it, which the all-shifts card added ($5,680,409 a
    year of all the waste against shift cards adding to $5,664,772). Every shift-day of the golden record that holds none of an
    ownership line its period to date holds: the shift's card shows the line at $0 beside its yearly figure, and the day's
    shift cards add to the day's, line by line and in total, at two rate sets."""
    cells, _, _ = cost_model.ledger_cells(store, store.production_days[0], store.production_days[-1])
    keys = ('shear', 'changeover', 'cutoff_waste', 'stacker_waste', 'admin', *cost_model.DRY_END_KINDS, 'unexplained')
    empty = sorted({(c['production_day'], c['shift'], k) for c in cells for k in keys
                    if cost_model.ledger_sums([c], rates())['lines'][k] == 0})
    assert ('2025-01-29', 2, 'admin') in empty and len(empty) >= 3
    for text in ('', 'paper:47.5,hour:1450,margin:40,trim:2.75'):
        r = rates(text)
        for day, shift, key in empty:
            window = {'period': 'day', 'anchor': day}
            cards = {sh: cost_model.opportunities(store, window, sh, r, 'ownership') for sh in (None, 1, 2, 3)}
            line = next(x for x in cards[shift]['rows'] if x['key'] == key)
            assert line['usd'] == 0 and line['usd_shown'] == 0 and line['usd_per_year'] > 0, (day, shift, key)
            every, mine = yearly_figures(cards[None]), [yearly_figures(cards[sh]) for sh in (1, 2, 3)]
            assert all(set(m) == set(every) for m in mine) and every == {k: sum(m[k] for m in mine) for k in every}, (day, key, text)
            assert owned_lines(cards[None]) == added([cards[sh] for sh in (1, 2, 3)], owned_lines), (day, key, text)


def test_a_long_window_reads_within_the_stated_bound_of_its_cents(store):
    """The coordinator's review of 2510d38, item 2: the long-window note states its bound over the whole record, at the post's
    rates (cost_model.LONG_BOUND, "under thirteen dollars"): every week, month and 4-4-5 period of the golden record, for all
    shifts and each shift, both lenses, prints each total, split and line within it of the same quantities priced at once in
    cents, and so does each period to date."""
    r, widest = rates(), (0.0, None)
    first, last = date.fromisoformat(store.production_days[0]), date.fromisoformat(store.production_days[-1])
    windows, monday = [], first - timedelta(days=first.weekday())
    while monday <= last:
        windows.append({'period': 'week', 'anchor': monday.isoformat()})
        monday += timedelta(days=7)
    month = first.replace(day=1)
    while month <= last:
        windows.append({'period': 'month', 'anchor': month.isoformat()})
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    moment = first
    while moment <= last:
        windows.append({'period': 'fiscal', 'anchor': moment.isoformat()})
        moment = fiscal_bounds(moment)[1] + timedelta(days=1)
    pairs = [('total_usd', 'total_usd_shown'), ('at_standard_usd', 'at_standard_usd_shown'), ('all_in_usd', 'all_in_usd_shown'),
             ('ptd_usd', 'ptd_usd_shown'), ('above_standard_usd', 'above_standard_usd_shown'), ('no_band_usd', 'no_band_usd_shown'),
             ('dry_end_usd', 'dry_end_usd_shown')]
    for window in windows:
        for shift in (None, 1, 2, 3):
            for lens in ('all', 'ownership'):
                card = cost_model.opportunities(store, window, shift, r, lens)
                figures = [(card[a], card[b], a) for a, b in pairs if card.get(a) is not None and card.get(b) is not None]
                figures += [(x['usd'], x['usd_shown'], x['lever']) for x in card['rows'] if x.get('usd') is not None]
                for cents, shown, what in figures:
                    widest = max(widest, (abs(shown - cents), (window, shift, lens, what)))
    assert len(windows) > 120 and widest[0] < cost_model.LONG_BOUND, widest
    assert widest[0] > cost_model.LONG_BOUND - 1, widest   # the bound is the record's own, not a loose one


def test_a_long_window_states_its_cents_apart_once():
    """The coordinator's review of 82c0994, item 2: a long window's whole dollars, added cell by cell, can differ from its cents
    by a few dollars; a card over several production days says so once, and a shift's or a day's card does not."""
    from investigator import shapes
    assert '$' not in shapes.LONG_NOTE and 'under thirteen dollars' in shapes.LONG_NOTE and cost_model.LONG_BOUND == 13
    assert shapes.long_note({'window': {'production_days_observed': 5}}) == [shapes.LONG_NOTE]
    assert shapes.long_note({'window': {'production_days_observed': 1}}) == [] and shapes.long_note({}) == []


def test_the_formula_line_states_its_cents_and_what_its_row_prints():
    """The review's item 1: 13 Jul shift 2 printed "Side trim over the 3% target: **$512**" over a formula ending "= $513". The
    formula is in cents, a waste row's ends with its paper and machine time added, and the line says what the row prints when
    that is not the cents' nearest dollar."""
    from investigator import shapes
    trim = cost_model.price('trim', 9318.6, rates())
    assert trim['usd_basis'] == '9,318.6 sq ft = 9.319 MSF x $55/MSF paper = $512.52; contribution: margin not set'
    assert shapes.formula_line('Side trim over the 3% target', {**trim, 'usd_shown': 512}).endswith(
        '= $512.52; contribution: margin not set. It prints $512 (whole dollars that add up).')
    assert shapes.formula_line('Side trim over the 3% target', {**trim, 'usd_shown': 513}).endswith('margin not set.')
    waste = cost_model.price('waste', 11218.7, rates())
    assert waste['usd'] == round(waste['paper_usd'] + waste['machine_time_usd'], 2)
    assert waste['usd_basis'].endswith(f'; ${waste["paper_usd"]:,.2f} + ${waste["machine_time_usd"]:,.2f} = ${waste["usd"]:,.2f}; '
                                       'contribution: margin not set')


def test_the_period_to_date_ends_on_its_last_production_day(store):
    """The review's item 4: P2 to date read "through Fri 25 Sep" on the day card and "through Sun 27 Sep" on the period card,
    and February 2026's one period to date three ways. The label names the last production day on or before `through`."""
    assert cost_model.ptd_window(store, '2026-09-27')[1:] == (date(2026, 9, 25), '4-4-5 P2 to date through Fri 25 Sep')
    labels = {cost_model.ptd_window(store, d)[2] for d in ('2026-02-27', '2026-02-28', '2026-03-01')}
    assert labels == {'4-4-5 P7 of the prior year to date through Fri 27 Feb'}
    period = cost_model.opportunities(store, {'period': 'fiscal', 'anchor': '2026-09-14'}, None, rates())
    assert (period['ptd_through'], period['ptd_label']) == ('2026-09-25', '4-4-5 P2 to date through Fri 25 Sep')


@pytest.mark.parametrize('text', ['', 'paper:47.5,hour:1450,margin:40,trim:2.75', 'paper:99999.99,margin:0.01', 'hour:1'])
def test_the_whole_dollars_fast_cents_are_the_price_calls_own(text):
    """cost_model._priced (and web/money.js priced4) is price's arithmetic without its formula text, for the thousands of calls
    a card's cells make: the same cents as price, field for field, on every kind and on quantities that round at a half cent."""
    import random
    r, rng = rates(text), random.Random(168)
    for kind in ('trim', 'waste', 'paper', 'downtime', 'speed'):
        for q in [0.0, 0.1, 1.0, 9318.6, 11218.7, 3882.2, *(round(rng.uniform(0, 300000), rng.choice((1, 2))) for _ in range(300))]:
            p = cost_model.price(kind, q, r)
            assert cost_model._priced(kind, q, r) == tuple(cost_model.to_cents(p[k]) for k in ('usd', 'paper_usd', 'machine_time_usd', 'contribution_usd')), (kind, q)
    assert cost_model._priced('waste', -1.0, r) == (0, 0, 0, 0) and cost_model._priced('trim', None, r) == (0, 0, 0, 0)
