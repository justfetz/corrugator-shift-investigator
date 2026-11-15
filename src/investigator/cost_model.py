"""The cost model (issue #31): quantities from the records priced at assumed rates.

Every dollar here is a tool quantity (square feet of waste, minutes of downtime, hours of a speed
gap) times a rate from `rates.json`. The defaults are the owner's post; a rate is overridden by
`INVESTIGATOR_RATES` on the server and by `rates=` on a request or URL (`paper:55,hour:1200,margin:40`).
No tool hard-codes a rate, and no model supplies one. The records hold no costs: every figure is
synthetic data at assumed rates, never a real plant's.

Three priced components, kept apart so that changing one rate changes only the dollars that use it:

- paper_usd          waste MSF x paper $/MSF (side trim, shear, dry-end rejects, the unexplained remainder)
- machine_time_usd   minutes of corrugator time x the machine $/hr shadow price (downtime, the speed gap,
                     and the run time shear and rejects consumed at the required rate; side trim is width,
                     not length, and consumes none)
- contribution_usd   board MSF not made x the contribution margin $/MSF; null with
                     contribution_status 'margin not set' until the owner sets a margin, never $0

`usd` is paper plus machine time. Contribution is shown beside it and never added: it prices the same
lost capacity the shadow price does, from the other side.

Conversions are the post's: one minute at the required speed, 675 ft/min at 10% downtime, on a 96 in net width
is 675 x 96 / 12 / 1000 = 5.4 MSF. That is the only use of the required speed in a price; the speed lever is each
grade's trailing-year baseline (issue #164).

The money tools (`get_shift_dollars`, `get_day_dollars`, `get_opportunities`) are one ledger (issue #164, below): every
lever priced as its gap to its band in targets.json, the same cells summed for a shift, a day, a week, a period to date
and a year to date, and a yearly figure only from period to date (`annual`).
"""
import bisect
import json
import math
import os
import re
from datetime import date, timedelta
from pathlib import Path
from . import targets

from . import records as R
from .record_store import WASTE_CATEGORIES, fiscal_bounds

DEFINITION = json.loads((Path(__file__).with_name('rates.json')).read_text())
RATE_SPECS = {r['id']: r for r in DEFINITION['rates']}
SHORT = {r['short']: r['id'] for r in DEFINITION['rates']}
ASSUMPTIONS = {a['id']: a['value'] for a in DEFINITION['assumptions']}
ENV = 'INVESTIGATOR_RATES'
MAX_RATE = 100000

MSF_PER_MINUTE = ASSUMPTIONS['required_fpm'] * ASSUMPTIONS['net_width_in'] / 12 / 1000           # 5.4
DAYS_PER_YEAR = ASSUMPTIONS['annual_msf_per_machine'] / (ASSUMPTIONS['volume_sqft_day'] / 1000)  # 257.14
ANNUAL_MINUTES = ASSUMPTIONS['scheduled_min_day'] * DAYS_PER_YEAR
SHIFT_MINUTES = 480
MARGIN_NOT_SET = 'margin not set'
NOTE = ('Dollars are synthetic records priced at assumed rates (the post\'s defaults unless changed); '
        'they are not a real plant\'s figures.')
# Said once on a card over several production days, and on its tiles (the coordinator's review of 82c0994 on PR #168, item 2):
# its whole dollars are its cells' added up (whole_dollars), which can differ from the window priced at once in cents. The bound
# over the whole record (the review of 2510d38, item 2), 1 Jan 2025 to 13 Nov 2026: at the post's rates every multi-day card's
# figures are within $12.78 of their cents (4-4-5 P2 2026's waste recorded), LONG_BOUND, which the note states and
# tests/test_money_ledger.py holds on the golden record. Other rates can read wider: within $23.69 at six other sets, and a very
# low machine rate floors each cell's sub-dollar lines, $29.74 at hour:1,trim:2 (September 2026's waste recorded);
# docs/COST_MODEL.md has the table. In words, never a $ figure: web/money.js re-prices every $ token of the prose.
LONG_BOUND = 13   # dollars: no multi-day card at the post's rates reads this far off its cents over the whole record
LONG_NOTE = ('Over several days these whole dollars, added shift by shift, can read off the same quantities priced at once in cents, '
             'which the formulas and receipts state: at the post\'s rates by under thirteen dollars over the whole record.')


# ------------------------------------------------------------------ rates

def parse_rates(text):
    """`paper:55,hour:1200,margin:40,trim:3` -> {rate id: value}. Unknown, repeated or out-of-range
    entries are refused with what to change; an empty string is no override."""
    if text is None or text == '':
        return {}
    if not isinstance(text, str) or len(text) > 200:
        raise ValueError('rates is a short list such as paper:55,hour:1200,margin:40')
    out = {}
    for item in text.split(','):
        key, sep, value = item.partition(':')
        if not sep or key not in SHORT:
            raise ValueError('rates takes ' + ', '.join(f'{k}:<number>' for k in SHORT) + ', for example paper:55,margin:40')
        rid = SHORT[key]
        if rid in out:
            raise ValueError(f'Repeated rate {key!r}')
        # At most two decimals, so the rate a link carries is exactly the rate the answer was priced at. ASCII digits
        # only: Python's \d and float() take full-width and other Unicode digits, which web/money.js refuses.
        if not re.fullmatch(r'\d{1,6}(\.\d{1,2})?', value, re.ASCII):
            raise ValueError(f'Rate {key} must be a number with at most two decimals, for example {key}:{RATE_SPECS[rid]["default"] or 40}')
        number = float(value)
        limit = 100 if rid == 'trim_target_pct' else MAX_RATE
        if not math.isfinite(number) or not 0 < number <= limit:
            raise ValueError(f'Rate {key} must be above 0 and at most {limit:g} {RATE_SPECS[rid]["unit"]}')
        out[rid] = number
    return out


def rate_text(value, comma=False):
    """A rate as written: exact to the two decimals `parse_rates` accepts, trailing zeros dropped."""
    text = f'{value:,.2f}' if comma else f'{value:.2f}'
    return text.rstrip('0').rstrip('.')


def format_rates(overrides):
    """The canonical spelling of an override set: the rates.json order, exact numbers."""
    return ','.join(f'{RATE_SPECS[rid]["short"]}:{rate_text(overrides[rid])}' for rid in RATE_SPECS if rid in (overrides or {}))


def effective(overrides=None):
    """The rates in force: the post's defaults, then INVESTIGATOR_RATES, then the request's own.
    Returns {'values': {id: value or None}, 'sources': {id: post | server | yours | ...}}."""
    values = {rid: spec['default'] for rid, spec in RATE_SPECS.items()}
    sources = {rid: spec['source'] for rid, spec in RATE_SPECS.items()}
    try:
        server = parse_rates(os.environ.get(ENV, ''))
    except ValueError as exc:
        raise ValueError(f'{ENV} is invalid: {exc}') from None
    for layer, label in ((server, 'server'), (overrides or {}, 'yours')):
        for rid, value in layer.items():
            values[rid], sources[rid] = value, label
    return {'values': values, 'sources': sources}


def client_config():
    """What the card needs to price in the browser (issue #31b, `/api/config`): each rate with its default, the
    server's value and source (INVESTIGATOR_RATES applied), its `rates=` key and limit; the fixed assumptions and the
    constants they derive; the cost note. No rate is written into web/money.js: it reads them from here."""
    rates = effective()
    return {'rates': [{'id': rid, 'short': spec['short'], 'label': spec['label'], 'unit': spec['unit'], 'default': spec['default'],
                       'value': rates['values'][rid], 'source': rates['sources'][rid], 'default_source': spec['source'],
                       'note': spec['note'], 'max': 100 if rid == 'trim_target_pct' else MAX_RATE} for rid, spec in RATE_SPECS.items()],
            'assumptions': DEFINITION['assumptions'], 'post': DEFINITION['post'],
            'constants': {'msf_per_minute': MSF_PER_MINUTE, 'annual_minutes': ANNUAL_MINUTES, 'shift_minutes': SHIFT_MINUTES, 'days_per_year': DAYS_PER_YEAR,
                          'annual_msf_per_machine': ASSUMPTIONS['annual_msf_per_machine'], 'required_fpm': ASSUMPTIONS['required_fpm'],
                          'net_width_in': ASSUMPTIONS['net_width_in'], 'scheduled_min_day': ASSUMPTIONS['scheduled_min_day'],
                          'min_rate_days': MIN_RATE_DAYS},   # no yearly figure from fewer production days (PR #168)
            'margin_not_set': MARGIN_NOT_SET, 'note': NOTE, 'long_note': LONG_NOTE}


def rate_rows(rates):
    """The rates as rows: what a card's provenance line and the narrative may name."""
    return [{'rate_id': rid, 'label': spec['label'], 'unit': spec['unit'], 'value': rates['values'][rid],
             'status': 'set' if rates['values'][rid] is not None else 'not set', 'source': rates['sources'][rid]}
            for rid, spec in RATE_SPECS.items()]


def _money(value):
    """A figure inside a formula, in cents (the coordinator's review of 82c0994 on PR #168, item 1): the formula states what
    a row's dollars are to the cent, and a card's line prints its whole dollars, so the two never read as two roundings."""
    return f'${value:,.2f}'


def provenance(rates):
    """One line naming every rate and where it came from, e.g. 'paper $55/MSF (post), ...'."""
    v, s = rates['values'], rates['sources']
    margin = (f'margin ${rate_text(v["margin_usd_msf"])}/MSF ({s["margin_usd_msf"]})' if v['margin_usd_msf'] is not None
              else f'contribution {MARGIN_NOT_SET} (assumption: {s["margin_usd_msf"]})')
    return (f'Rates: paper ${rate_text(v["paper_usd_msf"])}/MSF ({s["paper_usd_msf"]}), machine time ${rate_text(v["machine_usd_hour"], True)}/hr '
            f'({s["machine_usd_hour"]}), {margin}, trim target {rate_text(v["trim_target_pct"])}% ({s["trim_target_pct"]}).')


# ------------------------------------------------------------------ pricing

def price(kind, quantity, rates, gross_sqft=None, minutes=None):
    """Price one quantity. kind: 'trim' (sq ft, paper only), 'waste' (sq ft that ran through the machine:
    paper plus the run time it took), 'paper' (sq ft priced as paper only: bale, the unexplained remainder),
    'downtime' or 'speed' (minutes of machine time). `gross_sqft` and `minutes` are the window's gross area and
    scheduled minutes, for the row's share of its window. A price is the window's own dollars only: nothing here is
    annualized (the owner's call 3 on issue #164: a yearly figure comes only from period to date, `annual`).
    Returns the flat dollar fields a row carries (flat, so the narrative can cite each)."""
    v = rates['values']
    paper, hour, margin = v['paper_usd_msf'], v['machine_usd_hour'], v['margin_usd_msf']
    if quantity is None or quantity < 0:
        return {'msf': None, 'paper_usd': None, 'machine_minutes': None, 'machine_time_usd': None, 'usd': None,
                'usd_per_year': None, 'msf_not_made': None, 'contribution_usd': None, 'contribution_usd_per_year': None,
                'contribution_status': 'not priced', 'usd_basis': 'not priced: the quantity is unavailable or a negative remainder',
                'share_of_window_pct': None, 'price_terms': None}
    parts = []
    if kind in ('trim', 'waste', 'paper'):
        msf = quantity / 1000
        paper_usd = msf * paper
        machine_minutes = msf / MSF_PER_MINUTE if kind == 'waste' else 0.0
        not_made = msf if kind == 'waste' else 0.0
        parts.append(f'{quantity:,.1f} sq ft = {msf:,.3f} MSF x ${rate_text(paper)}/MSF paper = {_money(paper_usd)}')
    elif kind in ('downtime', 'speed'):
        msf, paper_usd = None, None
        machine_minutes = quantity
        not_made = quantity * MSF_PER_MINUTE
    else:
        raise ValueError(f'Unknown cost kind {kind!r}')
    machine_usd = machine_minutes / 60 * hour
    if kind != 'trim' and kind != 'paper':
        lead = '' if kind in ('downtime', 'speed') else f'{msf:,.3f} MSF / {MSF_PER_MINUTE:g} MSF per min = '
        parts.append(f'{lead}{machine_minutes:,.2f} min x ${rate_text(hour, True)}/hr machine time = {_money(machine_usd)}')
    # `usd` is the two priced parts as the formula states them, to the cent, added (PR #168, item 1): a waste row's formula
    # ends with that sum, so its line reads paper + machine time = the row's dollars with no cent between them
    usd = round(paper_usd, 2) + round(machine_usd, 2) if kind == 'waste' else (paper_usd or 0.0) + machine_usd
    if kind == 'waste':
        parts.append(f'{_money(paper_usd)} + {_money(machine_usd)} = {_money(round(usd, 2))}')
    if margin is None:
        contribution, status = None, MARGIN_NOT_SET
        parts.append(f'contribution: {MARGIN_NOT_SET}')
    else:
        contribution, status = not_made * margin, 'set'
        parts.append(f'contribution {not_made:,.3f} MSF not made x ${rate_text(margin)}/MSF = {_money(contribution)} (not added)')
    # `price_terms` (issue #31b) is the call itself, rate-free and unrounded: kind, quantity, and the window's gross
    # area and scheduled minutes. The card re-prices a row at a visitor's rates in the browser by making this same
    # call (web/money.js price), so a figure there and the server's figure at the same rates never disagree.
    r2 = lambda x: None if x is None else round(x, 2)
    base = gross_sqft if kind in ('trim', 'waste', 'paper') else minutes
    return {'msf': None if msf is None else round(msf, 3), 'paper_usd': r2(paper_usd),
            'machine_minutes': round(machine_minutes, 2), 'machine_time_usd': r2(machine_usd), 'usd': r2(usd),
            'usd_per_year': None, 'msf_not_made': round(not_made, 3), 'contribution_usd': r2(contribution),
            'contribution_usd_per_year': None, 'contribution_status': status, 'usd_basis': '; '.join(parts),
            'share_of_window_pct': round(quantity / base * 100, 4) if base else None,
            'price_terms': {'kind': kind, 'quantity': quantity, 'gross_sqft': gross_sqft or None, 'minutes': minutes or None}}


def annual(usd, days):
    """Dollars a year at a period's rate (the owner's call 3 on issue #164): period-to-date dollars per production day x
    the post's production days a year (DAYS_PER_YEAR, 1.8M MSF / 7,000 MSF a day). Never a shift's or a day's own: the
    money tools call it with period-to-date dollars only. Whole dollars; None without dollars, and None with fewer than
    MIN_RATE_DAYS production days (the coordinator's default on PR #168: one day x 257 is no yearly figure; `year_note`
    says why)."""
    return None if usd is None or not days or days < MIN_RATE_DAYS else round(usd / days * DAYS_PER_YEAR, 0)


def shift_days(cells):
    """[[shift, production days], ...] in shift order: each shift's own production days in `cells` (a period to date's), the
    days its own yearly figure is read over (annual_of)."""
    return [[sh, len({c['production_day'] for c in cells if c['shift'] == sh and c['setups']})] for sh in sorted({c['shift'] for c in cells})]


def annual_of(whole, path, days_of):
    """A yearly figure as the shifts print it (the coordinator's review of 2510d38 on PR #168, item 1): each shift's own, its
    line `path` of the period to date's whole dollars (`whole['shifts']`) at its own production days (`days_of`, shift_days),
    and a card for several shifts those figures added up, so the shift cards' yearly figures add to the all-shifts card's, lever
    by lever and line by line. A card for one shift is its own figure. None when a shift states none (`annual`: fewer than
    MIN_RATE_DAYS production days), or without shifts. web/money.js annualOf is this."""
    group, key = path
    days = {sh: d for sh, d in days_of}
    years = [annual(e[group][key], days.get(e['shift'], 0)) for e in whole['shifts']]
    return None if not years or None in years else round(_add(years), 0)


def trim_points_usd_per_year(points, rates=None, machines=1):
    """Paper dollars a year of `points` of side trim at the post's volume: the post's example,
    0.1 point across two machines = 0.001 x 3.6M MSF x $55 = $198,000."""
    rates = rates or effective()
    return points / 100 * ASSUMPTIONS['annual_msf_per_machine'] * machines * rates['values']['paper_usd_msf']


def _envelope(data, rates):
    data['rates'] = rate_rows(rates)
    data['provenance'] = provenance(rates)
    data['cost_note'] = NOTE
    return data


# Waste breakdown categories and how each is priced; bale is the scale's total, priced as paper.
WASTE_KINDS = {'trim': 'trim', 'shear': 'waste', 'changeover': 'waste', 'cutoff_waste': 'waste', 'stacker_waste': 'waste',
               'bond': 'waste', 'warp': 'waste', 'misalignment': 'waste', 'moisture': 'waste', 'other': 'waste', 'dry_end': 'waste', 'admin': 'waste',
               'bale': 'paper', 'unexplained': 'paper'}
PRICED_TOOLS = ('get_quality_breakdown', 'get_downtime_breakdown', 'get_waste_breakdown',
                'get_downtime_pareto', 'get_speed_opportunity', 'get_speed_variance')
GAIN = 'not priced: this bucket ran better than the reference (a gain, not a loss)'
SCHEDULE_DRIVEN_MIX = 'not priced: the grade mix is schedule-driven, the order book decides which grades run (issue #101)'
# the speed-variance buckets that are not the crew's (the owner, PR #111): why each is left unpriced
NOT_PRICED = {'mix': SCHEDULE_DRIVEN_MIX,
              'planned': 'not priced: planned maintenance is schedule-driven, the maintenance plan sets its stops (PR #111)',
              'external': 'not priced: external stops (a power outage) are outside the plant, not controllable (PR #111)'}


def window_minutes(data):
    """Scheduled minutes of a period tool's window: production days observed x 480 per shift."""
    days = data['window']['production_days_observed']
    return days * (SHIFT_MINUTES if data.get('shift') else 3 * SHIFT_MINUTES) or None


def attach(tool, data, rates, gross_sqft=None):
    """Put the dollar fields beside the quantity on every row of a priced tool's result.
    `gross_sqft` is the shift's gross area for the daily quality breakdown (its annual basis)."""
    if tool not in PRICED_TOOLS:
        return data
    if tool == 'get_speed_variance':
        return _envelope(speed_variance_dollars(data, rates), rates)
    for row in data['rows']:
        if tool == 'get_quality_breakdown':
            row.update(price('waste', row['rejected_sqft'], rates, gross_sqft, SHIFT_MINUTES))
        elif tool == 'get_downtime_breakdown':
            row.update(price('downtime', row['down_minutes'], rates, None, SHIFT_MINUTES))
        elif tool == 'get_waste_breakdown':
            row.update(price(WASTE_KINDS[row['category']], row['sqft'], rates, data['gross_sqft'], window_minutes(data)))
        elif tool == 'get_downtime_pareto':
            row.update(price('downtime', row['minutes'], rates, None, window_minutes(data)))
        elif tool == 'get_speed_opportunity':
            row.update(price('speed', row['opportunity_hours'] * 60, rates, None, window_minutes(data)))
    return _envelope(data, rates)


def speed_variance_dollars(data, rates):
    """Each controllable speed-variance bucket as machine time (issue #92): its lineal feet over the reference
    speed are the minutes the corrugator would need at that speed to make them, priced like the speed gap. A
    bucket that ran better than the reference is a gain and is not priced, whichever way the gap goes. The grade
    mix is schedule-driven (the owner, issue #101: the order book decides which grades run), so it is never
    priced as machine time; the envelope's controllable dollars (`controllable_usd`) are the priced loss buckets,
    stops, setups and running under baseline, and the gap as a whole is not priced, since it holds the mix."""
    minutes, reference = data['scheduled_minutes'], data['reference_speed_fpm']

    def priced_for(feet):
        if feet is None or not reference or feet <= 0:
            return {**price('speed', None, rates), 'usd_basis': GAIN} if feet is not None and feet < 0 else price('speed', None, rates)
        out = price('speed', feet / reference, rates, None, minutes)
        out['usd_basis'] = f'{feet:,.1f} lineal ft / {reference:,.1f} ft/min reference speed = ' + out['usd_basis']
        return out
    for row in data['rows']:
        if row.get('control') != 'controllable':   # the mix and planned stops (schedule-driven), external stops: not priced
            row.update({**price('speed', None, rates), 'usd_basis': NOT_PRICED[row['bucket']]})
        else:
            row.update(priced_for(row['gap_lineal_ft']))
    priced = [r for r in data['rows'] if r.get('control') == 'controllable' and r['usd'] is not None]

    def total(key, places=2):   # left to right (_add), as web/money.js re-derives it at other rates
        values = [r[key] for r in priced if r[key] is not None]
        return round(_add(values), places) if values else None
    # the margin's status is the rates', not the gap's: a faster window still prices its loss buckets
    data.update({'controllable_machine_minutes': total('machine_minutes'), 'controllable_usd': total('usd'),
                 'controllable_usd_per_year': total('usd_per_year', 0) if priced and all(r['usd_per_year'] is not None for r in priced) else None,
                 'controllable_contribution_usd': total('contribution_usd'),
                 'contribution_status': MARGIN_NOT_SET if rates['values']['margin_usd_msf'] is None else 'set'})
    return data


# ------------------------------------------------------------------ what the money tools share

def _trace(parts, started_ms=0.0):
    return {'sql': ';\n'.join(p['trace']['sql'] if isinstance(p['trace']['sql'], str) else ';\n'.join(p['trace']['sql']) for p in parts),
            'parameters': [p['trace']['parameters'] for p in parts],
            'elapsed_ms': sum(p['trace']['elapsed_ms'] for p in parts)}


def _add(values):
    """Floats added left to right, as web/money.js adds them: Python's sum() of floats compensates (3.12+), and a
    total the card re-derives in the browser must be this one to the last bit."""
    total = 0.0
    for value in values:
        total += value
    return total


def _totals(rows):
    def total(key):
        values = [r[key] for r in rows if r[key] is not None]
        return round(_add(values), 2) if values else 0.0
    out = {'total_usd': total('usd'), 'total_paper_usd': total('paper_usd'), 'total_machine_time_usd': total('machine_time_usd')}
    years = [r['usd_per_year'] for r in rows]
    out['total_usd_per_year'] = round(_add(years), 0) if rows and None not in years else None
    margin_set = all(r['contribution_status'] != MARGIN_NOT_SET for r in rows)
    out['total_contribution_usd'] = total('contribution_usd') if margin_set else None
    out['contribution_status'] = 'set' if margin_set else MARGIN_NOT_SET
    return out


PAPER_KINDS = ('trim', 'waste', 'paper')
TIME_KINDS = ('downtime', 'speed')


def bucket_of(row):
    """The chip a priced row adds to on the card: a ledger row's own lever (`bucket`), else by kind and name."""
    if row.get('bucket'):
        return row['bucket']
    kind = (row.get('price_terms') or {}).get('kind') or row.get('kind')
    name = str(row.get('gap') or row.get('lever') or '').lower()
    if row.get('kind') == 'setup':
        return 'Setups'
    if kind in ('downtime', 'speed', 'trim'):
        return {'downtime': 'Stops', 'speed': 'Speed', 'trim': 'Trim'}[kind]
    return 'Shear' if name.startswith('shear') else 'Dry-end' if name.startswith('dry-end') else 'Other waste'


def summary(rows):
    """What the card states about a money tool's rows besides each row (issue #31b review): the total of each bucket
    (the dollar line's chips) and the sums its arithmetic shows: the area priced as paper, the machine minutes of the
    stops, the speed gap and the run time waste took, and the MSF not made. Sums of the rows' own (rounded) figures,
    in the rows' ranked order, so web/money.js re-derives them at other rates to the last digit."""
    buckets = {}
    for r in rows:
        if r.get('usd') is not None:
            b = buckets.setdefault(bucket_of(r), {'usd': [], 'year': []})
            b['usd'].append(r['usd'])
            b['year'].append(r['usd_per_year'])
    out = [{'bucket': name, 'usd': round(_add(b['usd']), 2), 'usd_per_year': None if None in b['year'] else round(_add(b['year']), 0)}
           for name, b in buckets.items()]
    out.sort(key=lambda b: (-b['usd'], b['bucket']))
    priced = [r for r in rows if r.get('price_terms')]
    minutes = lambda kind: round(_add(r['machine_minutes'] for r in priced if r['price_terms']['kind'] == kind), 2)
    area = _add(r['price_terms']['quantity'] for r in priced if r['price_terms']['kind'] in PAPER_KINDS)
    return {'buckets': out, 'area_sqft': round(area, 1), 'area_msf': round(area / 1000, 3),
            'stop_minutes': minutes('downtime'), 'speed_minutes': minutes('speed'), 'waste_run_minutes': minutes('waste'),
            'machine_minutes': round(_add(r['machine_minutes'] for r in priced), 2),
            'msf_not_made': round(_add(r['msf_not_made'] for r in priced), 3)}


def tile(metric, parts, rates, what, metrics=None, measure='all recorded'):
    """A KPI tile's own loss priced (issue #31b review): one pricing call per part, each the quantity a tool row of
    the same scope prices (a reject reason, a stop reason, a waste category), so the tile and that row agree to the
    cent. `parts` are price_terms; the tile is their sum, as the rows' totals are. `metric` names the tile: the KPI
    field it reads (get_shift_kpis) or its row's metric (get_period_kpis, compare_periods); `metrics` every tile the
    same loss sits on (the stop tiles: downtime %, downtime minutes and stops are all the window's stops). A tile is
    the window's own dollars, never annualized (issue #164)."""
    priced = [price(p['kind'], p['quantity'], rates, p['gross_sqft'], p['minutes']) for p in parts]
    usd = [r['usd'] for r in priced if r['usd'] is not None]
    return {'metric': metric, 'metrics': list(metrics or [metric]), 'what': what, 'measure': measure, 'usd': round(_add(usd), 2) if usd else None,
            'usd_per_year': None, 'machine_minutes': round(_add(r['machine_minutes'] or 0 for r in priced), 2),
            'usd_basis': ' + '.join(r['usd_basis'] for r in priced), 'parts': [r['price_terms'] for r in priced]}


def _part(kind, quantity, gross, minutes):
    return {'kind': kind, 'quantity': quantity, 'gross_sqft': gross or None, 'minutes': minutes or None}


# The window tiles priced like get_waste_breakdown's rows (sq ft to 0.1, the gross to 0.1) and the Pareto's (minutes to
# 0.01 per reason), so a tile agrees with those rows on the same page to the cent.
WASTE_TILES = {'trim_pct': 'trim', 'shear_pct': 'shear', 'dry_end_pct': 'dry_end', 'bond_pct': 'bond', 'warp_pct': 'warp',
               'misalignment_pct': 'misalignment', 'moisture_pct': 'moisture', 'other_pct': 'other', 'bale_pct': 'bale', 'unexplained_pct': 'unexplained'}


def period_tiles(data, reasons, rates, ledger=None):
    """The money of a window's KPI tiles (get_period_kpis, compare_periods): each waste tile its category's area, each
    stop tile the stop reasons' minutes (`reasons`, the Pareto's rows of the same window and shift). A tile is what its
    figure measures, all recorded: the ownership lens's line for the same window, never a lever's above-standard part.
    The operator and maintenance tiles are the money ledger's (`ledger`, period_whole: its sums and display terms; the
    review of PR #168, item 9): its minutes, each shift's rounded as the ledger rounds them. Every tile prints the money
    ledger's whole dollars (tile_shown, SHOWN_OF): the lever's line and its line at standard (and planned maintenance)
    added up, a waste tile its line's, so a tile, the money card and the waste breakdown print one figure."""
    sums = data.get('current_sums') or {}
    if not sums.get('gross_sqft'):
        return []
    gross, minutes = round(sums['gross_sqft'], 1), window_minutes(data)
    reasons_of = {'dry_end': ('bond', 'warp', 'misalignment', 'moisture', 'other')}   # the dry-end tile is its families' rows, as the chip is
    out = [tile(metric, [_part(WASTE_KINDS[k], round(sums[k], 1), gross, minutes) for k in reasons_of.get(key, (key,))], rates,
                key.replace('_', '-') if key != 'dry_end' else 'dry-end rejects')
           for metric, key in WASTE_TILES.items()]
    stops = [_part('downtime', r['minutes'], gross, minutes) for r in reasons]
    out.append(tile('downtime_pct', stops, rates, 'all stops', ('downtime_pct', 'downtime_minutes', 'stops')))   # one entry, three tiles
    s, whole = (ledger[0], whole_dollars(ledger[1], rates)) if ledger else (None, None)
    pm = ([_part('downtime', s['all']['maintenance'], gross, minutes), _part('downtime', s['minutes']['planned'], gross, minutes)] if s else
          [_part('downtime', round(sums['maintenance_pm_seconds'] / 60, 2), gross, minutes)])
    out.append(tile('maintenance_pm_pct', pm, rates, 'maintenance and planned maintenance (scheduled) stops'))
    op = s['all']['operator'] if s else round(sums['operator_seconds'] / 60, 2)
    out.append(tile('operator_pct', [_part('downtime', op, gross, minutes)], rates, 'operator stops'))
    for t in out:
        tile_shown(t, whole)
        t['production_days'] = data['window']['production_days_observed']   # several: its card says LONG_NOTE on the tile
    return out


def period_whole(store, window, shift, rates):
    """The money ledger of a period tool's window and shift, for its tiles (period_tiles): the scope's sums and the
    display terms its whole dollars are its cells' of (the cell is the root: the review of 82c0994 on PR #168, item 2)."""
    current = store._window(window)
    cells, _, _ = ledger_cells(store, current['start'], current['end'], shift)
    return ledger_sums(cells, rates), display_terms(cells, rates)


def waste_shown(data, terms, rates):
    """get_waste_breakdown's printed whole dollars (the review of PR #168): each line the money ledger's for the same window
    and shift (whole_dollars' `lines`), the figure the ownership lens and the KPI tiles print, so the dry-end families add to the
    dry-end line and every view prints one figure; `display_terms` re-derive them at a visitor's rates (web/money.js)."""
    lines = whole_dollars(terms, rates)['lines']
    for r in data['rows']:
        r['usd_shown'] = None if r.get('usd') is None else lines[r['category']]
    data['display_terms'] = terms
    return data


# A daily breakdown row's whole dollars (the review of PR #168's fix round, finding 4): the shift's dry-end families on the
# quality breakdown are its dry-end kinds' lines, and the stops by kind its stop lines, as the shift's money card and tiles
# print them; a row by reason or place has no line of its own on the ledger and prints its nearest dollar.
DAILY_SHOWN = {'get_quality_breakdown': lambda r: [['lines', r['reason'].lower()]] if str(r.get('reason', '')).lower() in DRY_END_KINDS else None,
               'get_downtime_breakdown': lambda r: {'Operator': [['levers', 'operator'], ['at', 'operator']],
                                                     'Maintenance': [['levers', 'maintenance'], ['at', 'maintenance']],
                                                     'Scheduled': [['apart', 'scheduled']],
                                                     targets.EXTERNAL_KIND: [['apart', 'external']]}.get(r.get('category'))}


def daily_shown(analysis, tool, data, shift, rates, group_by=None):
    """A shift's quality breakdown, or its downtime by kind, in the whole dollars the shift's money card prints (its cell's,
    whole_dollars): each row's `shown_of` and `usd_shown`, and the card's `display_terms` for the browser."""
    if tool not in DAILY_SHOWN or (tool == 'get_downtime_breakdown' and group_by != 'kind') or getattr(analysis, 'store', None) is None:
        return data
    _, every, _, _ = _card(analysis, (1, 2, 3), rates)
    cells = [c for c in every if c['shift'] == shift]
    if not cells:
        return data
    terms = display_terms(cells, rates)
    whole = whole_dollars(terms, rates)
    for r in data['rows']:
        path = DAILY_SHOWN[tool](r)
        if path and r.get('usd') is not None:
            r['shown_of'], r['usd_shown'] = path, sum(whole[g][k] for g, k in path)
    data['display_terms'] = terms
    return data


def _rank(rows, key):
    """Ranked by the whole dollars the card prints (`usd_shown`, PR #168) when a row carries them, then by `key`, then by
    name: a list never prints a larger figure below a smaller one. web/money.js rank is this."""
    shown = lambda r: r.get('usd_shown') if r.get('usd_shown') is not None else (r[key] or 0)
    rows.sort(key=lambda r: (-shown(r), -(r[key] or 0), r['gap'] if 'gap' in r else r['lever']))
    for i, r in enumerate(rows, 1):
        r['rank'] = i
    return rows


LENSES = ('all', 'supervisor', 'ownership')


def family_control(family):
    """A reject family's control label from the taxonomy (reject_reasons.json): its categories' drivers in the file's
    order, a category that is only partly controllable saying so with its own note. A family none of whose categories is
    controllable is not production waste (planning / admin). The coordinator's re-review of PR #123: every dry-end family
    read "temperatures" before, the slitter and knife, the board surface, handling, paper and print included."""
    found = [c for c in R.categories().values() if c['family'] == family]
    if all(c['controllable'] == 'no' for c in found):
        return 'not production waste', '; '.join(c['driver'] for c in found)
    return 'controllable', '; '.join(c['driver'] + (f', partly controllable ({c["control_note"]})' if c['controllable'] == 'partly' else
                                                    ' (not controllable)' if c['controllable'] == 'no' else '') for c in found)


# The owner's two lenses (issue #101, targets.json): which levers a supervisor controls, and why. Each reject family (the
# five dry-end families, the changeover rejects, planning / admin) takes its label from the taxonomy; the cutoff-knife and
# stacker boards are lost at each order change, so sequencing, as the changeover rejects are.
# Side trim's control is targets.json's trim band lever (schedule-driven), read here once for every money view.
CONTROL = {'speed': ('controllable', 'speed under the grade baseline'), 'trim': (targets.BANDS['trim']['lever'], 'the order mix and its web widths'),
           'downtime': ('controllable', 'maintenance and operator downtime'), 'setup': ('controllable', 'operator downtime at order changes'),
           'scheduled': ('schedule-driven', 'planned maintenance is scheduled time'), 'shear': ('controllable', 'sequencing'),
           **{R.families()[code]['key']: family_control(code) for code in R.families()},
           'cutoff_waste': ('controllable', 'sequencing: the boards each order change loses at the cutoff knife'),
           'stacker_waste': ('controllable', 'sequencing: the boards each order change loses at the stacker'),
           'unexplained': ('unattributed', 'the scale reading less the recorded waste'),
           'external': ('external', 'outside the plant, not controllable'),   # a power outage (targets.json; the owner on PR #111)
           'dry_end_lever': ('controllable', '; '.join(f'{R.families()[code]["key"]}: {family_control(code)[1]}' for code in R.DRY_END_FAMILIES)),
           'operator': ('controllable', 'operator downtime, the order changes included'),
           'maintenance': ('controllable', 'unplanned maintenance: breakdowns and faults')}
TOTAL_TRIM = 'Side trim (all recorded)'
# The ownership lens's dry-end subtotal (the owner, PR #111): bond, warp, misalignment, moisture and other are dry-end waste,
# the tag scrap, priced and ranked as one line with each kind kept beneath it, since the detail is what supervisors go after.
DRY_END_GROUP, DRY_END_LABEL = 'dry_end', 'Dry-end waste (tag scrap)'
DRY_END_KINDS = tuple(R.families()[code]['key'] for code in R.DRY_END_FAMILIES)   # bond, warp, misalignment, moisture, other


def _control(row, key):
    row['control'], row['control_note'] = CONTROL[key]
    return row


# ------------------------------------------------------------------ the one money ledger (issue #164)
#
# The owner's calls on issue #164 (2026-11-14): every lever is its gap to its band in targets.json, so trim, shear and dry
# end inside their bands price at $0 and their cost at standard is context, shown apart; the speed lever is each grade's
# trailing-year baseline, and the 675 ft/min required speed is shown beside it, labelled, never as the same definition; a
# yearly figure comes only from period to date; each band names an owner role ("owner to set" until the owner sets it).
#
# One ledger prices every money view. Its cell is one shift of one production day (a row of the derived shift_kpis
# table): each lever's quantity in a cell is the cell's gap to the band, rounded (0.1 sq ft, 0.01 min), and a window's is
# the sum of its cells'. The band is the shift's standard, so a shift over it is an opportunity even when the window's
# average is inside it, and a shift, a day, a week, a period to date and a year to date are the same sums: a shift's
# dollars add to its day, and one window reads the same figure in every view. Planned maintenance, external stops, the
# waste at standard and the waste with no band are priced and shown apart, outside the total.

LEVERS = ('speed', 'trim', 'shear', 'dry_end', 'operator', 'maintenance')
BANDED = ('trim', 'shear', 'dry_end', 'operator', 'maintenance')   # the levers with a cost at standard
LEVER_KIND = {'speed': 'speed', 'trim': 'trim', 'shear': 'waste', 'dry_end': 'waste', 'operator': 'downtime', 'maintenance': 'downtime'}
LEVER_BUCKET = {'speed': 'Speed', 'trim': 'Trim', 'shear': 'Shear', 'dry_end': 'Dry-end', 'operator': 'Operator stops', 'maintenance': 'Maintenance stops'}
LEVER_CONTROL = {'speed': 'speed', 'trim': 'trim', 'shear': 'shear', 'dry_end': 'dry_end_lever', 'operator': 'operator', 'maintenance': 'maintenance'}
AREA_LEVERS = ('trim', 'shear', 'dry_end')
TIME_LEVERS = ('operator', 'maintenance')
APART = {'at-standard': 0, 'schedule-driven': 1, 'external': 2, 'no-band': 3}   # the groups shown apart, in this order
APART_LABEL = {'at-standard': 'At standard, not in the total', 'schedule-driven': 'Schedule-driven, shown apart',
               'external': 'External, not controllable', 'no-band': 'No band yet, shown apart'}
APART_NOTE = {'at-standard': 'inside its band: the cost of the order mix and the process, not an opportunity',
              'schedule-driven': 'planned maintenance is scheduled time', 'external': 'outside the plant, not controllable',
              'no-band': 'targets.json sets no band for it yet, so it is not a lever'}
PLANNED_LABEL = 'Downtime: Planned maintenance (scheduled)'
EXTERNAL_LABEL = f'Downtime: {" / ".join(sorted(targets.EXTERNAL_REASONS))} (external)'
NO_BAND = (('changeover', 'Changeover rejects (counted with shear)'), ('boards', 'Cutoff-knife and stacker waste boards'))
REQUIRED_FPM = ASSUMPTIONS['required_fpm']
# The downtime the required speed allows, read from its own words in rates.json ("Required run speed at 10% downtime"), so the
# money answers' speeds note never types a copy of it.
REQUIRED_DOWNTIME_PCT = json.loads(re.search(r'at ([\d.]+)% downtime', next(a['label'] for a in DEFINITION['assumptions']
                                                                           if a['id'] == 'required_fpm')).group(1))
SPEEDS_NOTE = ('Two speeds, never one definition: the speed lever is each grade\'s trailing-year baseline (targets.json), and '
               'the required speed, {required:g} ft/min at {downtime:g}% downtime (rates.json, the post), converts machine minutes to MSF '
               'not made and is no lever.')
LEDGER_NOTE = ('Each lever is its gap to its band in targets.json, summed over the shifts of the window: a shift inside its band '
               'adds nothing, and the cost inside the bands is shown apart, at standard, outside the total. A yearly figure is this '
               'period\'s rate, period-to-date dollars per production day x the post\'s production days a year, never a shift\'s or '
               'a day\'s own. For all shifts it is each shift\'s period-to-date rate added up, so the shifts\' yearly figures add to it. '
               'These name where to look and whom to ask; they never prescribe a machine adjustment.')

CELL_SQL = ("SELECT production_date, shift, coverage, setups, gross_sqft, trim_sqft, shear_sqft, reject_sqft, bond_sqft, warp_sqft, "
            "misalignment_sqft, moisture_sqft, other_sqft, changeover_sqft, cutoff_waste_sqft, stacker_waste_sqft, admin_sqft, bale_sqft, "
            "scheduled_seconds, stop_seconds, maintenance_seconds, planned_maintenance_seconds, operator_seconds, lineal_ft, source_ids, "
            "setups_above_trim_target "
            "FROM shift_kpis WHERE corrupt = ? AND production_date BETWEEN ? AND ?{clause} ORDER BY production_date, shift")
# Each wet-end run's part in a shift, so a run that spans two shifts is measured in each against its grade's baseline
# through its own day (the trailing year ending on the day, targets.json speed): a shift's speed gap never moves with
# the window it is read in.
RUN_SQL = ("SELECT production_date, shift, wet_end_id, quality_id AS grade, sum(lineal_ft) AS feet, sum(seconds - stop_seconds) / 60 AS run_min "
           "FROM setups WHERE production_date BETWEEN ? AND ?{clause}{exclude} GROUP BY ALL ORDER BY production_date, shift, wet_end_id, grade")
STOP_SQL = ("SELECT shift, kind, reason, count(*) AS stops, sum(seconds) / 60 AS minutes FROM stops WHERE production_date BETWEEN ? AND ?{clause} "
            "GROUP BY ALL ORDER BY shift, kind, reason")
AREAS = ('gross', 'trim', 'shear', 'dry_end', 'bond', 'warp', 'misalignment', 'moisture', 'other', 'changeover', 'cutoff_waste',
         'stacker_waste', 'admin', 'bale')
MINUTES = ('scheduled', 'maintenance', 'operator', 'planned', 'external')


def band_target(key, rates):
    """A lever's standard: the trim target is a rate (rates.json, the visitor may change it); the others are targets.json's."""
    if key == 'speed':
        return None   # the speed lever's standard is each grade's own baseline, not one band
    return rates['values']['trim_target_pct'] if key == 'trim' else targets.BANDS[key]['target']


def cell_gaps(c, rates):
    """One cell's quantity above its band, per lever (rounded: 0.1 sq ft, 0.01 min); web/money.js does the trim line the
    same way, from the same rounded cell, at another target."""
    b = targets.BANDS
    yield_sqft = c['gross'] - c['trim'] - c['shear']   # the dry-end band's denominator: the yield after trim and shear
    return {'speed': c['speed_short_min'],
            'trim': round(max(0.0, c['trim'] - band_target('trim', rates) / 100 * c['gross']), 1),
            'shear': round(max(0.0, c['shear'] - b['shear']['target'] / 100 * c['gross']), 1),
            'dry_end': round(max(0.0, c['dry_end'] - b['dry_end']['target'] / 100 * yield_sqft), 1),
            'operator': round(max(0.0, c['operator'] - b['operator']['target'] / 100 * c['scheduled']), 2),
            'maintenance': round(max(0.0, c['maintenance'] - b['maintenance']['target'] / 100 * c['scheduled']), 2)}


def ledger_cells(store, start, end, shift=None, corrupt=False, exclude=()):
    """The ledger's cells over [start, end]: one per shift of each production day (the shift_kpis row; `corrupt` its
    data-quality variant), its areas to 0.1 sq ft and minutes to 0.01, and its speed gap: each wet-end run's part in the
    shift against its grade's baseline through its own day. `exclude` are setup ids the day's own view left out.
    Returns (cells, stop reasons, the trace). Cells are rate-free, so they are kept on the store per window; nothing that
    reads them changes them."""
    key = (str(start)[:10], str(end)[:10], shift, bool(corrupt), tuple(exclude))
    cache = store.__dict__.setdefault('_ledger_cells', {})
    if key in cache:
        return cache[key]
    if len(cache) > 512:
        cache.clear()
    cache[key] = _ledger_cells(store, start, end, shift, corrupt, exclude)
    return cache[key]


def _ledger_cells(store, start, end, shift, corrupt, exclude):
    clause, extra = store._shift_clause(shift)
    trace = {'sql': [], 'parameters': []}

    def fetch(sql, params, limit=50000):
        trace['sql'].append(sql)
        trace['parameters'].append(params)
        return store._fetch(sql, params, limit=limit)
    start, end = str(start)[:10], str(end)[:10]
    rows = fetch(CELL_SQL.format(clause=clause), [corrupt, start, end, *extra])
    skip = f" AND setup_id NOT IN ({', '.join('?' * len(exclude))})" if exclude else ''
    runs = fetch(RUN_SQL.format(clause=clause, exclude=skip), [start, end, *extra, *exclude])
    stops = fetch(STOP_SQL.format(clause=clause), [start, end, *extra])
    cells = {}
    for r in rows:
        minute = lambda s: round(s / 60, 2)
        c = {'production_day': r['production_date'].isoformat(), 'shift': r['shift'], 'coverage': r['coverage'], 'setups': r['setups'],
             'gross': round(r['gross_sqft'], 1), 'trim': round(r['trim_sqft'], 1), 'shear': round(r['shear_sqft'], 1),
             'dry_end': round(r['reject_sqft'], 1), 'bond': round(r['bond_sqft'], 1), 'warp': round(r['warp_sqft'], 1),
             'misalignment': round(r['misalignment_sqft'], 1), 'moisture': round(r['moisture_sqft'], 1), 'other': round(r['other_sqft'], 1),
             'changeover': round(r['changeover_sqft'], 1), 'cutoff_waste': round(r['cutoff_waste_sqft'], 1),
             'stacker_waste': round(r['stacker_waste_sqft'], 1), 'admin': round(r['admin_sqft'], 1), 'bale': round(r['bale_sqft'], 1),
             'scheduled': minute(r['scheduled_seconds']), 'maintenance': minute(r['maintenance_seconds']), 'operator': minute(r['operator_seconds']),
             'planned': minute(r['planned_maintenance_seconds']),
             'external': minute(r['stop_seconds'] - r['maintenance_seconds'] - r['planned_maintenance_seconds'] - r['operator_seconds']),
             'lineal_ft': round(r['lineal_ft'], 1), 'source_ids': list(r['source_ids'] or []),
             'trim_flag_setups': r['setups_above_trim_target'] or 0,   # the setups above the per-setup trim flag (trim by shift, PR #168)
             'speed_short_min': 0.0, 'runs': 0, 'runs_under': 0, 'grade_short': {}}
        cells[(r['production_date'], r['shift'])] = c
    baselines = {}
    for r in runs:
        c = cells.get((r['production_date'], r['shift']))
        if c is None or not r['run_min'] or r['run_min'] <= 0:
            continue
        day = r['production_date']
        if day not in baselines:
            baselines[day] = store.grade_speed_baselines(day)
        base = baselines[day]['grades'].get(r['grade'])
        if not base:
            continue
        gap_ft = max(0.0, base['baseline_fpm'] * r['run_min'] - r['feet'])
        c['speed_short_min'] += gap_ft / base['baseline_fpm']
        c['grade_short'][r['grade']] = c['grade_short'].get(r['grade'], 0.0) + gap_ft / base['baseline_fpm']
        c['runs'] += 1
        c['runs_under'] += gap_ft > 0
    for c in cells.values():
        c['speed_short_min'] = round(c['speed_short_min'], 2)
    if baselines:
        first = next(iter(baselines.values()))
        trace['sql'].append(first['sql'])
        trace['parameters'].append(first['parameters'])
    return list(cells.values()), stops, trace


def ledger_sums(cells, rates):
    """A window's ledger from its cells: each lever's quantity above its band (the cells' gaps added left to right), all
    recorded and at standard, the shifts over the band, every waste line, the downtime apart, the runs under their
    baseline, and the trim cells the card re-derives the trim line from at another target."""
    gaps = [cell_gaps(c, rates) for c in cells]
    total = lambda key, places=1: round(_add(c[key] for c in cells), places)
    out = {'cells': len(cells), 'days': len({c['production_day'] for c in cells if c['setups']}), 'shift_set': {c['shift'] for c in cells},
           'gross': total('gross'), 'scheduled': total('scheduled', 2),
           'over': {k: round(_add(g[k] for g in gaps), 2 if k in ('speed', 'operator', 'maintenance') else 1) for k in LEVERS},
           'over_cells': {k: sum(g[k] > 0 for g in gaps) for k in LEVERS},
           'all': {k: total(k, 2 if k in ('operator', 'maintenance') else 1) for k in BANDED},
           'lines': {k: total(k) for k in AREAS}, 'minutes': {k: total(k, 2) for k in MINUTES},
           'lineal_ft': total('lineal_ft'), 'runs': sum(c['runs'] for c in cells), 'runs_under': sum(c['runs_under'] for c in cells),
           'trim_cells': [[c['trim'], c['gross']] for c in cells], 'source_ids': [i for c in cells for i in c['source_ids']][:50]}
    out['at'] = {k: round(out['all'][k] - out['over'][k], 2 if k in ('operator', 'maintenance') else 1) for k in BANDED}
    lines = out['lines']
    lines['boards'] = round(lines['cutoff_waste'] + lines['stacker_waste'], 1)
    accounted = _add(lines[k] for k in ('trim', 'shear', 'dry_end', 'changeover', 'admin', 'cutoff_waste', 'stacker_waste'))
    lines['unexplained'] = round(lines['bale'] - accounted, 1)
    lines['yield'] = round(lines['gross'] - lines['trim'] - lines['shear'], 1)
    grades = {}
    for c in cells:
        for g, m in c['grade_short'].items():
            grades[g] = grades.get(g, 0.0) + m
    out['slowest_grade'] = max(sorted(grades), key=lambda g: grades[g]) if grades and max(grades.values()) > 0 else None
    return out


MIN_RATE_DAYS = 5   # a production week: a period to date shorter than this reads its rate with the period before it


def day_text(day, year=False):
    """A day as a label names it: "Mon 14 Sep" (with the year, "Wed 1 Jan 2025")."""
    return f'{day:%a} {day.day} {day:%b}' + (f' {day.year}' if year else '')


def ptd_window(store, through):
    """The period to date a yearly figure is read from: the 4-4-5 period containing `through` (the window's last day, or
    the card's day), from its first day inside the record store to `through`. In a period's first week, when it holds
    fewer than MIN_RATE_DAYS production days, the period before it is read with it ("P3 and P4 to date"), so a yearly
    figure is never one day's or one shift's. The label carries its end day ("4-4-5 P2 to date through Mon 14 Sep", the
    coordinator's default on PR #168), so a day card and its week never state two yearly figures under one label; a period
    that began before the records is labelled from their first day. The end day is the last production day on or before
    `through` (the coordinator's review of 82c0994, item 4: a month ending on a Saturday or a period on a Sunday named its
    calendar end, so one period to date read "through Fri 25 Sep" on a day card and "through Sun 27 Sep" on its period's).
    Returns (start, end, label)."""
    day = min(date.fromisoformat(str(through)[:10]), store._last)
    days = store.production_days
    at = bisect.bisect_right(days, day.isoformat())
    if at:
        day = date.fromisoformat(days[at - 1])
    start, _, label = fiscal_bounds(day)
    name = label.split(' (')[0]
    if len([d for d in store.production_days if start.isoformat() <= d <= day.isoformat()]) < MIN_RATE_DAYS:
        before, _, prior = fiscal_bounds(start - timedelta(days=1))
        if start > store._first:
            name = f'{prior.split(" (")[0]} and {name.split(" ", 1)[1] if name.startswith("4-4-5 ") else name}'
            start = before
    if start < store._first:
        return store._first, day, f'{name} to date from the first day of the records, {day_text(store._first, True)}, through {day_text(day)}'
    return start, day, f'{name} to date through {day_text(day)}'


def year_note(days):
    """Why a period to date states no yearly figure (the coordinator's default on PR #168): with fewer than MIN_RATE_DAYS
    production days, one day's or a few days' rate would be scaled to a year. None when it states one."""
    if days >= MIN_RATE_DAYS:
        return None
    return f'no yearly figure: {days} production day{"" if days == 1 else "s"} to date, fewer than {MIN_RATE_DAYS}'


def trim_cells_over(cells, target):
    """The trim line's quantity above the target from the trim cells ([trim, gross] each, rounded): each cell's gap
    rounded to 0.1 sq ft, added left to right (web/money.js trimOver, the same arithmetic)."""
    return round(_add(round(max(0.0, trim - target / 100 * gross), 1) for trim, gross in cells), 1)


def _pct(numerator, denominator, places=2):
    return round(numerator / denominator * 100, places) if denominator else None


def _pct_text(numerator, denominator):
    """A share as a lever's words print it: two decimals (web/money.js pctText)."""
    value = _pct(numerator, denominator)
    return 'unavailable' if value is None else f'{value:.2f}'


def lever_label(key, rates):
    if key == 'speed':
        return 'Speed under the grade baseline'
    target = band_target(key, rates)
    return {'trim': f'Side trim over the {target:g}% target',
            'shear': f'Shear over the {target:g}% target', 'dry_end': f'Dry end over the {target:g}% target',
            'operator': f'Operator downtime over the {target:g}% band', 'maintenance': f'Maintenance downtime over the {target:g}% band'}[key]


def at_label(key, rates):
    target = band_target(key, rates)
    return {'trim': f'Side trim at standard (up to {target:g}% of gross)', 'shear': f'Shear at standard (up to {target:g}% of gross)',
            'dry_end': f'Dry end at standard (up to {target:g}% of the yield)', 'operator': f'Operator downtime within its {target:g}% band',
            'maintenance': f'Maintenance downtime within its {target:g}% band'}[key]


def lever_records(key, s, rates, scope):
    """What a lever's figure is read against, in words (a string field: the narrative quotes it, never recomputes it). The
    shifts over the band lead: the band is each shift's standard, so a window whose own share is inside it still prices the
    shifts that were over it, and the window's share follows."""
    target = band_target(key, rates) or 0
    over, cells = s['over_cells'][key], s['cells']
    shifts = f'{over} of {cells} shift{"" if cells == 1 else "s"} over the {target:g}% {"band" if key in TIME_LEVERS else "target"}'
    lines, minutes = s['lines'], s['minutes']
    if key == 'speed':
        return f'{s["runs_under"]} of {s["runs"]} wet-end runs under their grade baseline'
    if key == 'trim':
        return f'{shifts}, side trim {_pct_text(lines["trim"], lines["gross"])}% of gross'
    if key == 'shear':
        return f'{shifts}, shear {_pct_text(lines["shear"], lines["gross"])}% of gross'
    if key == 'dry_end':
        return f'{shifts}, dry end {_pct_text(lines["dry_end"], lines["yield"])}% of the yield after trim and shear'
    return f'{shifts}, {key} downtime {_pct_text(minutes[key], s["scheduled"])}% of scheduled minutes'


def stop_reasons(stops, shifts):
    """The window's stop reasons on the cells' own shifts, by kind, the most minutes first: what `look_at` names."""
    out = {}
    for r in stops:
        if r['shift'] in shifts:
            k = (r['kind'], r['reason'])
            out[k] = out.get(k, 0.0) + r['minutes']
    return [{'kind': k, 'reason': reason, 'minutes': m} for (k, reason), m in sorted(out.items(), key=lambda x: (-x[1], x[0]))]


def look_at(key, s, stops):
    """Where to look and what to ask about, never a machine adjustment (issue #164, item 5)."""
    if key == 'speed':
        return (f'the {s["slowest_grade"]} runs first, the grade furthest under its baseline' if s['slowest_grade'] else
                'the runs under their grade baseline, by grade')
    if key == 'trim':
        return 'the order mix and web widths on the shifts over target'
    if key == 'shear':
        return 'the order and grade changes behind the chops (the sequence)'
    if key == 'dry_end':
        family = max(DRY_END_KINDS, key=lambda k: (s['lines'][k], k))
        return f'{family} rejects by grade, the largest dry-end family'
    kind = 'Operator' if key == 'operator' else 'Maintenance'
    lead = next((r for r in stop_reasons(stops, s['shift_set']) if r['kind'] == kind and r['minutes'] > 0), None)
    return f'the {lead["reason"]} stops, the largest {key} reason' if lead else f'the {key} stops by reason'


def _lever_row(key, s, rates, scope, stops, ptd=None, ptd_whole=None):
    kind = LEVER_KIND[key]
    qty = s['over'][key]
    row = {'lever': lever_label(key, rates), 'key': key, 'kind': kind, 'bucket': LEVER_BUCKET[key], 'quantity': qty,
           'unit': 'min' if kind in TIME_KINDS else 'sq ft', 'band_target': band_target(key, rates),
           'shifts_over': s['over_cells'][key], 'shifts': s['cells'], 'records': lever_records(key, s, rates, scope),
           'owner': targets.owner(key), 'look_at': look_at(key, s, stops), 'apart': None}
    _control(row, LEVER_CONTROL[key])
    row.update(price(kind, qty, rates, s['gross'], s['scheduled']))
    if key == 'trim':
        row['price_terms']['trim_cells'] = s['trim_cells']   # the card re-derives the line at another target (web/money.js)
    if ptd is not None:
        row.update(_yearly(key, ptd, rates, ptd_whole))
    return row


def _yearly(key, ptd, rates, ptd_whole):
    """A lever's dollars a year at this period's rate: each shift's period-to-date whole dollars (its cells' added up, the
    review of 82c0994, item 2) per its production day x the production days a year, the shifts' added up (annual_of, the review
    of 2510d38, item 1); `year_terms` re-derive it at a visitor's rates in the browser, `of` naming its line of the period to
    date's whole dollars (`ptd_terms.whole`) and `shift_days` each shift's days."""
    kind, qty = LEVER_KIND[key], ptd['over'][key]
    p = price(kind, qty, rates)
    terms = {'kind': kind, 'quantity': qty, 'days': ptd['days'], 'shift_days': ptd['shift_days'], 'of': ['levers', key],
             **({'trim_cells': ptd['trim_cells']} if key == 'trim' else {})}
    contribution = None if p['contribution_usd'] is None else annual(p['contribution_usd'], ptd['days'])
    return {'usd_per_year': annual_of(ptd_whole, terms['of'], ptd['shift_days']), 'contribution_usd_per_year': contribution, 'year_terms': terms}


def at_standard(kind, total, over, rates, gross, minutes):
    """The cost inside a band: all recorded less above standard, each priced on its own quantity, so a lever (above),
    its line at standard and the ownership lens's total (all recorded) are the same three figures in every view.
    `price_terms` carry both quantities (`part` 'at'); web/money.js re-derives it the same way."""
    whole, above = price(kind, total, rates, gross, minutes), price(kind, over, rates, gross, minutes)
    diff = lambda k, places=2: None if whole[k] is None else round(whole[k] - (above[k] or 0), places)
    at_qty = round(total - over, 2 if kind in TIME_KINDS else 1)
    return {'msf': diff('msf', 3), 'paper_usd': diff('paper_usd'), 'machine_minutes': diff('machine_minutes'),
            'machine_time_usd': diff('machine_time_usd'), 'usd': diff('usd'), 'usd_per_year': None, 'msf_not_made': diff('msf_not_made', 3),
            'contribution_usd': diff('contribution_usd'), 'contribution_usd_per_year': None, 'contribution_status': whole['contribution_status'],
            'all_recorded_usd': whole['usd'], 'above_standard_usd': above['usd'],
            'usd_basis': f'{_money(whole["usd"])} all recorded less {_money(above["usd"])} above standard = {_money(diff("usd"))} at standard; ' +
                         (f'contribution: {MARGIN_NOT_SET}' if whole['contribution_usd'] is None else f'contribution {_money(diff("contribution_usd"))} (not added)'),
            'share_of_window_pct': None if not (gross if kind in PAPER_KINDS else minutes) else
                round(at_qty / (gross if kind in PAPER_KINDS else minutes) * 100, 4),
            'price_terms': {'kind': kind, 'part': 'at', 'quantity': at_qty, 'total': total, 'over': over,
                            'gross_sqft': gross or None, 'minutes': minutes or None}}


def _apart_rows(s, rates):
    """The rows shown apart, outside the total: each band's cost at standard, planned maintenance, external stops and the
    waste no band covers yet."""
    rows = []
    for key in BANDED:
        kind = LEVER_KIND[key]
        if not s['all'][key]:
            continue
        row = {'lever': at_label(key, rates), 'key': key, 'kind': kind, 'bucket': LEVER_BUCKET[key], 'quantity': s['at'][key],
               'unit': 'min' if kind in TIME_KINDS else 'sq ft', 'apart': 'at-standard', 'apart_note': APART_NOTE['at-standard']}
        row.update(at_standard(kind, s['all'][key], s['over'][key], rates, s['gross'], s['scheduled']))
        if key == 'trim':
            row['price_terms']['trim_cells'] = s['trim_cells']
        rows.append(row)
    for apart, label, qty, control in (('schedule-driven', PLANNED_LABEL, s['minutes']['planned'], 'scheduled'),
                                       ('external', EXTERNAL_LABEL, s['minutes']['external'], 'external')):
        if qty:
            rows.append(_control({'lever': label, 'key': control, 'kind': 'downtime', 'bucket': 'Stops', 'quantity': qty, 'unit': 'min',
                                  'apart': apart, 'apart_note': APART_NOTE[apart], **price('downtime', qty, rates, s['gross'], s['scheduled'])}, control))
    for key, label in NO_BAND:
        qty = s['lines'][key]
        if qty:
            rows.append({'lever': label, 'key': key, 'kind': 'waste', 'bucket': 'Other waste', 'quantity': qty, 'unit': 'sq ft',
                         'apart': 'no-band', 'apart_note': APART_NOTE['no-band'], **price('waste', qty, rates, s['gross'], s['scheduled'])})
    return rows


def rank_ledger(rows, lens='all'):
    """The levers ranked by dollars (under the supervisor lens the controllable ones only); the rest after them with no
    rank, by group (APART: at standard, schedule-driven, external, no band; the supervisor lens's schedule-driven trim lever
    with the schedule-driven group) and dollars. web/money.js ranks them the same way."""
    def group(r):
        return APART[r['apart']] if r.get('apart') else APART['schedule-driven'] if r['control'] == 'schedule-driven' else APART['external']
    ranked = [r for r in rows if not r.get('apart') and (lens != 'supervisor' or r['control'] == 'controllable')]
    rest = [r for r in rows if r not in ranked]
    _rank(ranked, 'usd')
    rest.sort(key=lambda r: (group(r), -(r.get('usd_shown') if r.get('usd_shown') is not None else r['usd'] or 0), -(r['usd'] or 0), r['lever']))
    for r in rest:
        r['rank'] = None
    return ranked + rest


def ledger_totals(rows):
    """A ledger's totals: the levers above standard (`total_*`, and the chips and arithmetic of them), the cost at standard,
    and the all-in total with every row shown apart, each added left to right in row order."""
    levers = [r for r in rows if not r.get('apart')]
    at = [r['usd'] for r in rows if r.get('apart') == 'at-standard' and r['usd'] is not None]
    usd = [r['usd'] for r in rows if r['usd'] is not None]
    return {**_totals(levers), **summary(levers), 'levers': len(levers), 'apart_gaps': len(rows) - len(levers),
            'at_standard_usd': round(_add(at), 2) if at else 0.0, 'all_in_usd': round(_add(usd), 2) if usd else 0.0}


# ------------------------------------------------------------------ whole dollars that add up (the owner's top rule, PR #168)
#
# A card prints whole dollars, and every set of parts it prints adds up to the figure it prints, in those whole dollars,
# in every view and across nested windows (the coordinator's review of 82c0994, item 2: the cell is the root). The root is
# the ledger's cell, one shift of one production day (the owner's own ledger rule, "the cell is one shift; a day is the sum of
# its shifts", applied to whole dollars):
#
# - each cell's levers above standard print their nearest dollar together (a tie to the even dollar), and each lever its
#   share by the largest-remainder method: its floor, then a dollar each to the levers with the largest remainders until the
#   shares add up; its rows at standard the same way, as a root of their own; each row shown apart its nearest dollar; the two
#   lines of the waste boards share the boards row's; the five dry-end kinds share the dry-end line's (the lever's and its row
#   at standard's); planning / admin, the bale and the unexplained remainder print their nearest dollar; paper and machine
#   time, and the contribution, share the cell's figure the same way;
# - a shift's card, a day's, a week's, a month's, a 4-4-5 period's, a period to date's, each of their lines and tiles, and the
#   base of a yearly figure are their cells' whole dollars added up.
#
# So a cell prints the same whole dollars on every card that holds it, a day is its shifts, a week its days, a period to date
# its days, and every card's lines add up to its figure. A cell's lever total and its total at standard are within half a
# dollar of their cents, each of their lines within a dollar; a figure added from several roots (the ownership lens's waste
# recorded adds about twelve a cell) carries each one's rounding, and a window carries every cell's, so a long window can read
# several dollars off the same quantities priced at once in cents (its card says so once, LONG_NOTE). The cents (`usd`) stay
# what the arithmetic and the receipts recompute; `*_shown` is what a card prints. web/money.js allocates the same way from
# `display_terms`, at a visitor's rates.

APART_KEYS = ('scheduled', 'external', 'changeover', 'boards')   # the rows shown apart, each its cell's nearest dollar
APART_KIND = {'scheduled': 'downtime', 'external': 'downtime', 'changeover': 'waste', 'boards': 'waste',
              'cutoff_waste': 'waste', 'stacker_waste': 'waste'}
BOARDS = ('cutoff_waste', 'stacker_waste')   # the ownership lens's two lines of the waste boards row
# the waste lines whose whole dollars no lever or row apart holds: the dry-end kinds share the dry-end lever's and its row at
# standard's, the rest print their nearest dollar; each priced as the waste breakdown prices it (WASTE_KINDS)
LINE_TERMS = (*DRY_END_KINDS, 'admin', 'bale', 'unexplained')
# the ownership lens's lines (its total is theirs added up): side trim, shear, the changeover rejects, the waste boards' two
# lines, planning / admin, the dry-end kinds and the unexplained remainder, never the bale (the scale's reading of them all)
OWNER_LINES = ('trim', 'shear', 'changeover', 'cutoff_waste', 'stacker_waste', 'admin', *DRY_END_KINDS, 'unexplained')


def to_cents(usd):
    """A priced figure (two places) as integer cents."""
    return 0 if usd is None else int(round(usd * 100))


def nearest_dollar(cents):
    """Whole dollars of integer cents, a tie to the even dollar (Python's round, and shapes.money's f'{x:,.0f}')."""
    dollars, rest = divmod(cents, 100)
    return dollars + (1 if rest > 50 or (rest == 50 and dollars % 2) else 0)


def largest_remainder(parts, target):
    """Whole dollars of `parts` (integer cents, in a fixed order) that add up to `target` whole dollars: each part's floor,
    then a dollar more to the parts with the largest remainders, a tie to the earlier part. When the floors and remainders
    cannot reach the target (a whole priced at once differs from its parts by a cent or so), the largest parts move, a $0
    part never rises and no part falls below $0. web/money.js largestRemainder is this, line for line."""
    out = [c // 100 for c in parts]
    gap = target - sum(out)
    fraction = [i for i in sorted(range(len(parts)), key=lambda i: (-(parts[i] % 100), i)) if parts[i] % 100]
    for i in fraction[:max(gap, 0)]:
        out[i] += 1
    gap -= min(max(gap, 0), len(fraction))
    largest = sorted(range(len(parts)), key=lambda i: (-parts[i], i))
    rising = [i for i in largest if parts[i] > 0] or largest[:1]
    j = 0
    while gap > 0 and rising:
        out[rising[j % len(rising)]] += 1
        gap, j = gap - 1, j + 1
    j = 0
    while gap < 0 and any(out[i] > 0 for i in largest):
        i = largest[j % len(largest)]
        if out[i] > 0:
            out[i], gap = out[i] - 1, gap + 1
        j += 1
    return out


def whole_terms(s, shift=None):
    """The rate-free quantities a cell's whole dollars are priced from (`display_terms`): each lever's gap, each band's
    quantity recorded, each row shown apart, the two lines of the waste boards, the waste lines no lever holds, and the trim
    cell (the trim lever at a visitor's target; so the trim lever's own gap is not in them, and the terms are the same at any
    rates)."""
    return {'shift': shift, 'levers': {k: s['over'][k] for k in LEVERS if k != 'trim'}, 'all': {k: s['all'][k] for k in BANDED},
            'apart': {'scheduled': s['minutes']['planned'], 'external': s['minutes']['external'], 'changeover': s['lines']['changeover'],
                      'boards': s['lines']['boards'], **{k: s['lines'][k] for k in BOARDS}},
            'lines': {k: s['lines'][k] for k in LINE_TERMS}, 'trim_cells': s['trim_cells']}


def _priced(kind, quantity, rates):
    """(cents, paper cents, machine cents, contribution cents) of one quantity, as `price` rounds them (its arithmetic, step for
    step, without the formula text: a card's whole dollars price every cell's every line, thousands of calls); a negative or
    missing quantity prices at 0. tests/test_money_ledger.py holds it to `price`; web/money.js pricedCents is this."""
    if quantity is None or quantity < 0:
        return 0, 0, 0, 0
    v = rates['values']
    if kind in PAPER_KINDS:
        msf = quantity / 1000
        paper = msf * v['paper_usd_msf']
        minutes = msf / MSF_PER_MINUTE if kind == 'waste' else 0.0
        not_made = msf if kind == 'waste' else 0.0
    elif kind in TIME_KINDS:
        paper, minutes, not_made = None, quantity, quantity * MSF_PER_MINUTE
    else:
        raise ValueError(f'Unknown cost kind {kind!r}')
    machine = minutes / 60 * v['machine_usd_hour']
    usd = round(paper, 2) + round(machine, 2) if kind == 'waste' else (paper or 0.0) + machine
    contribution = None if v['margin_usd_msf'] is None else not_made * v['margin_usd_msf']
    cents = lambda x: 0 if x is None else int(round(round(x, 2) * 100))
    return cents(usd), cents(paper), cents(machine), cents(contribution)


def _cents(kind, quantity, rates):
    return _priced(kind, quantity, rates)[0]


def terms_cents(t, rates):
    """One cell's terms priced: integer cents of each lever above its band, each band's cost at standard (all recorded less
    above standard, as at_standard), each row shown apart, the waste boards' two lines, every waste line no lever holds, and
    the paper, machine-time and contribution cents of the levers and of the ownership lens's lines."""
    over = {**t['levers'], 'trim': trim_cells_over(t['trim_cells'], rates['values']['trim_target_pct'])}
    above = {k: _priced(LEVER_KIND[k], over[k], rates) for k in LEVERS}
    recorded = {k: _priced(LEVER_KIND[k], t['all'][k], rates) for k in BANDED}
    plain = {k: _priced(WASTE_KINDS[k], t['lines'][k], rates) for k in LINE_TERMS}
    boards = {k: _priced(APART_KIND[k], t['apart'][k], rates) for k in BOARDS}
    changeover = _priced('waste', t['apart']['changeover'], rates)
    owner = [recorded['trim'], recorded['shear'], changeover, boards['cutoff_waste'], boards['stacker_waste'], plain['admin'],
             *(plain[k] for k in DRY_END_KINDS), plain['unexplained']]
    part = lambda rows, i: sum(r[i] for r in rows)
    return {'levers': [above[k][0] for k in LEVERS],
            'at': [recorded[k][0] - above[k][0] for k in BANDED],
            'apart': [_cents(APART_KIND[k], t['apart'][k], rates) for k in APART_KEYS],
            'boards': [boards[k][0] for k in BOARDS],
            'lines': {k: plain[k][0] for k in LINE_TERMS},
            'split': [part(above.values(), 1), part(above.values(), 2)], 'contribution': part(above.values(), 3),
            'owner_split': [part(owner, 1), part(owner, 2)], 'owner_contribution': part(owner, 3)}


def cell_whole(t, rates):
    """One cell's whole dollars (the root, above): its levers sharing their nearest dollar, its rows at standard theirs, each
    row apart and each plain waste line its own, the boards' and the dry-end kinds' lines sharing their row's and line's."""
    p = terms_cents(t, rates)
    total, at_total = nearest_dollar(sum(p['levers'])), nearest_dollar(sum(p['at']))
    e = {'shift': t['shift'], 'total': total, 'levers': dict(zip(LEVERS, largest_remainder(p['levers'], total))),
         'at_total': at_total, 'at': dict(zip(BANDED, largest_remainder(p['at'], at_total))),
         'apart': {k: nearest_dollar(c) for k, c in zip(APART_KEYS, p['apart'])}}
    e['boards'] = dict(zip(BOARDS, largest_remainder(p['boards'], e['apart']['boards'])))
    lines = {k: e['levers'][k] + e['at'][k] for k in AREA_LEVERS}
    lines.update(zip(DRY_END_KINDS, largest_remainder([p['lines'][k] for k in DRY_END_KINDS], lines['dry_end'])))
    lines.update({'changeover': e['apart']['changeover'], **e['boards']})
    lines.update({k: nearest_dollar(p['lines'][k]) for k in ('admin', 'bale', 'unexplained')})
    e['lines'] = lines
    e['paper'], e['machine'] = largest_remainder(p['split'], total)
    e['contribution'] = nearest_dollar(p['contribution'])
    e['owner'] = sum(lines[k] for k in OWNER_LINES)
    e['owner_paper'], e['owner_machine'] = largest_remainder(p['owner_split'], e['owner'])
    e['owner_contribution'] = nearest_dollar(p['owner_contribution'])
    return e


_CELL_WHOLE = {}   # (rates, a cell's terms) -> its whole dollars (read only: _added makes new dicts)
WHOLE_SCALARS = ('total', 'at_total', 'paper', 'machine', 'contribution', 'owner', 'owner_paper', 'owner_machine', 'owner_contribution')
WHOLE_GROUPS = ('levers', 'at', 'apart', 'boards', 'lines')


def _added(cells, shift=None):
    """Cells' whole dollars added up: every figure and every line of them."""
    out = {'shift': shift, **{k: sum(e[k] for e in cells) for k in WHOLE_SCALARS}}
    for g in WHOLE_GROUPS:
        keys = cells[0][g] if cells else {'levers': LEVERS, 'at': BANDED, 'apart': APART_KEYS, 'boards': BOARDS,
                                          'lines': (*AREA_LEVERS, *DRY_END_KINDS, 'changeover', *BOARDS, 'admin', 'bale', 'unexplained')}[g]
        out[g] = {k: sum(e[g][k] for e in cells) for k in keys}
    out['all_in'] = out['total'] + out['at_total'] + sum(out['apart'].values())
    return out


def whole_dollars(terms, rates):
    """The whole dollars a card prints (see above): `terms` is display_terms, the cells the card adds up. Returns the card's
    figure (`total`), its levers, rows at standard and their total, rows apart, everything recorded and priced (`all_in`),
    every waste line, paper and machine time, the contribution, the ownership lens's total and split, and each shift's own
    (`shifts`, the shift's cells added up, in shift order)."""
    v = rates['values']
    key = (v['paper_usd_msf'], v['machine_usd_hour'], v['margin_usd_msf'], v['trim_target_pct'])
    if len(_CELL_WHOLE) > 50000:
        _CELL_WHOLE.clear()
    cells = []
    for row, t in zip(terms['cells'], cell_terms(terms)):   # a cell's whole dollars are its own: a day card and its three shift
        k = (key, tuple(row))                                # cards read one period to date, so each cell is priced once
        if k not in _CELL_WHOLE:
            _CELL_WHOLE[k] = cell_whole(t, rates)
        cells.append(_CELL_WHOLE[k])
    by = {}
    for e in cells:
        by.setdefault(e['shift'], []).append(e)
    out = _added(cells)
    out['shifts'] = [_added(by[sh], sh) for sh in sorted(by)]
    return out


# A card's cells ride as rows of numbers under one list of field names, so a month's terms stay small on the wire (a cell
# is 27 numbers: the shift, each lever's gap, each band's quantity recorded, each row apart and waste line, the trim cell).
CELL_FIELDS = ('shift', *(k for k in LEVERS if k != 'trim'), *(f'all_{k}' for k in BANDED), *(f'apart_{k}' for k in (*APART_KEYS, *BOARDS)),
               *LINE_TERMS, 'trim', 'gross')


_TERMS_ROW = {}   # id(cell) -> (the cell, its row): cells are kept per window on the store and never change (ledger_cells)


def display_terms(cells, rates):
    """`display_terms` of a card: each of the cells it adds up, its rate-free terms (whole_terms of the cell's own ledger),
    one row of CELL_FIELDS each."""
    if len(_TERMS_ROW) > 50000:
        _TERMS_ROW.clear()
    rows = []
    for c in cells:
        held = _TERMS_ROW.get(id(c))
        if held is None or held[0] is not c:
            t = whole_terms(ledger_sums([c], rates), c['shift'])   # rate-free: the trim lever's own gap is not in them
            (trim, gross), = t['trim_cells']
            held = _TERMS_ROW[id(c)] = (c, [t['shift'], *(t['levers'][k] for k in LEVERS if k != 'trim'), *(t['all'][k] for k in BANDED),
                                            *(t['apart'][k] for k in (*APART_KEYS, *BOARDS)), *(t['lines'][k] for k in LINE_TERMS), trim, gross])
        rows.append(list(held[1]))
    return {'fields': list(CELL_FIELDS), 'cells': rows}


def cell_terms(terms):
    """display_terms' rows as each cell's terms (whole_terms' shape): what terms_cents prices. web/money.js cellTerms is this."""
    out = []
    for row in terms['cells']:
        v = dict(zip(terms['fields'], row))
        out.append({'shift': v['shift'], 'levers': {k: v[k] for k in LEVERS if k != 'trim'}, 'all': {k: v[f'all_{k}'] for k in BANDED},
                    'apart': {k: v[f'apart_{k}'] for k in (*APART_KEYS, *BOARDS)}, 'lines': {k: v[k] for k in LINE_TERMS},
                    'trim_cells': [[v['trim'], v['gross']]]})
    return out


def split_shown(parts, target):
    """A printed figure's parts (cents, in a fixed order) as whole dollars that add up to it."""
    return largest_remainder([to_cents(p) for p in parts], target)


def row_shown(row, whole):
    """The whole dollars a ledger row prints: a lever's, a row at standard's, a row shown apart's (cost_model.APART_KEYS)."""
    if not row.get('apart'):
        return whole['levers'][row['key']]
    if row['apart'] == 'at-standard':
        return whole['at'][row['key']]
    return whole['apart'][row['key']]


def attach_shown(data, rows, whole):
    """A ledger's printed whole dollars beside its cents: each row's `usd_shown`, each chip's, the card's figure, its cost
    at standard and everything recorded, paper and machine time (its cells' shares, so they add up to its figure), and the
    contribution beside it."""
    for r in rows:
        r['usd_shown'] = row_shown(r, whole)
    data['total_usd_shown'] = whole['total']
    data['at_standard_usd_shown'] = whole['at_total']
    data['all_in_usd_shown'] = whole['all_in']
    data['total_paper_usd_shown'], data['total_machine_time_usd_shown'] = whole['paper'], whole['machine']
    data['total_contribution_usd_shown'] = None if data.get('total_contribution_usd') is None else whole['contribution']
    for b in data.get('buckets') or []:
        b['usd_shown'] = sum(r['usd_shown'] for r in rows if not r.get('apart') and r.get('bucket') == b['bucket'])
    return data


def ptd_ledger(store, through, shift, rates):
    """The period to date of `through` for `shift` (None: all shifts): its ledger, the dollars above standard of each lever
    and in all, and the same at this period's rate a year. With fewer than MIN_RATE_DAYS production days there is no
    yearly figure, and `ptd_year_note` says why. `ptd_usd_shown` is the whole dollars a card prints, its cells' added up,
    and each lever's yearly figure is read from its own printed whole dollars (the coordinator's review of 82c0994, item 2:
    the yearly base is the sum of its cells' whole dollars). `ptd_terms` re-derive them at a visitor's rates. Returns (its
    ledger, the fields a card carries, the trace, its whole dollars)."""
    start, end, label = ptd_window(store, through)
    every, _, trace = ledger_cells(store, start, end, None)
    cells = [c for c in every if shift is None or c['shift'] == shift]
    s = ledger_sums(cells, rates)
    s['shift_days'] = shift_days(cells)
    days = dict(s['shift_days'])
    terms = display_terms(cells, rates)
    whole = whole_dollars(terms, rates)
    levers = []
    for key in LEVERS:
        p = price(LEVER_KIND[key], s['over'][key], rates)
        # each shift's own yearly figure, and the scope's theirs added up (the coordinator's review of 2510d38, item 1)
        by_shift = [{'shift': e['shift'], 'days': days[e['shift']], 'usd_shown': e['levers'][key],
                     'usd_per_year': annual(e['levers'][key], days[e['shift']])} for e in whole['shifts']]
        levers.append({'key': key, 'lever': lever_label(key, rates), 'usd': p['usd'], 'usd_shown': whole['levers'][key],
                       'usd_per_year': annual_of(whole, ['levers', key], s['shift_days']), 'by_shift': by_shift})
    years = [r['usd_per_year'] for r in levers]
    out = {'ptd_label': label, 'ptd_start': start.isoformat(), 'ptd_through': end.isoformat(),
           'ptd_days': s['days'], 'ptd_scope': f'shift {shift}' if shift else 'all shifts',
           'ptd_usd': round(_add(r['usd'] for r in levers), 2),
           'ptd_usd_shown': whole['total'],
           'ptd_usd_per_year': None if None in years else round(_add(years), 0),
           'ptd_year_note': year_note(s['days']),
           'ptd_levers': levers,
           'ptd_terms': {'days': s['days'], 'shift_days': s['shift_days'], 'levers': [{'key': k, 'kind': LEVER_KIND[k], 'quantity': s['over'][k],
                                                        **({'trim_cells': s['trim_cells']} if k == 'trim' else {})} for k in LEVERS],
                         'whole': terms}}
    return s, out, trace, whole


def _speeds(s, shift_card=True):
    """The two speeds a money answer names (the owner's call 2): the required speed with its source, and the window's
    running speed with stops; each labelled, neither the speed lever's measure."""
    return {'required_fpm': REQUIRED_FPM, 'required_downtime_pct': REQUIRED_DOWNTIME_PCT,
            'observed_fpm_with_stops': round(s['lineal_ft'] / s['scheduled'], 1) if s['scheduled'] else None,
            'speeds_note': SPEEDS_NOTE.format(required=REQUIRED_FPM, downtime=REQUIRED_DOWNTIME_PCT)}


def _baseline_span(store, start, end):
    """The setups the speed lever's baselines are read from: each day's trailing year (targets.json speed), from the
    first day's to the last day's, so a receipt opens every setup a baseline of the window took."""
    return {'baseline_since': store.trailing_year(start)[0].isoformat(), 'baseline_through': store.trailing_year(end)[1].isoformat()}


def _cards_store(analysis):
    store = getattr(analysis, 'store', None)
    if store is None:
        raise ValueError('The money ledger reads the record store; this dataset is a single-day fixture')
    corrupt = bool(analysis.errors) and analysis.version == store.day_version(analysis.day, True)
    return store, corrupt


def _card(analysis, shifts, rates):
    """The ledger of the shifts `shifts` of the analysis's day: their cells, the stop reasons and the trace. Each cell is its
    own whole-dollar root, so the data-quality toggle never moves a shift it does not touch (the review of PR #168: it
    excludes a record on one shift, and the others print what they print without it)."""
    store, corrupt = _cards_store(analysis)
    exclude = tuple(e['record_id'] for e in analysis.errors) if corrupt else ()
    cells, stops, trace = ledger_cells(store, analysis.day, analysis.day, None, corrupt, exclude)
    cells = [c for c in cells if c['shift'] in shifts]
    return store, cells, stops, trace


# A tile's printed whole dollars, as the ledger prints them (PR #168): the lever's whole dollars, and for a tile of all
# recorded its rows at standard (and apart) of the same scope, added up; a waste tile its line's (whole_dollars' `lines`), the figure
# the ownership lens and the waste breakdown print; web/money.js reads `shown_of` the same way.
_STOPS = [['levers', 'operator'], ['at', 'operator'], ['levers', 'maintenance'], ['at', 'maintenance']]
SHOWN_OF = {'observed_shift_fpm': [['levers', 'speed']], 'maintenance_pct': [['levers', 'maintenance'], ['at', 'maintenance']],
            'operator_pct': [['levers', 'operator'], ['at', 'operator']], 'dry_end_pct': [['levers', 'dry_end'], ['at', 'dry_end']],
            'trim_pct': [['levers', 'trim'], ['at', 'trim']], 'shear_pct': [['levers', 'shear'], ['at', 'shear']],
            'maintenance_pm_pct': [['levers', 'maintenance'], ['at', 'maintenance'], ['apart', 'scheduled']],
            'downtime_pct': [*_STOPS, ['apart', 'scheduled'], ['apart', 'external']],   # every stop: the four kinds the ledger holds
            **{f'{k}_pct': [['lines', k]] for k in (*DRY_END_KINDS, 'bale', 'unexplained')}}


def tile_shown(t, whole):
    """A tile's `usd_shown`: the ledger's printed whole dollars it is (SHOWN_OF), else its own nearest dollar."""
    if t['metric'] in SHOWN_OF and whole is not None and t['usd'] is not None:
        t['shown_of'] = SHOWN_OF[t['metric']]
        t['usd_shown'] = sum(whole[group][key] for group, key in t['shown_of'])
    else:
        t['usd_shown'] = None if t['usd'] is None else nearest_dollar(to_cents(t['usd']))
    return t


def shift_tiles(cell, s, rates, whole=None):
    """The money of a shift's KPI tiles, from the cell the card prices: the speed tile is the speed lever (the grade
    baseline, the one speed a price uses); side trim, the dry-end families, and the maintenance and operator minutes are
    all recorded, as the ownership lens's lines of the same shift. Returned on get_shift_dollars, the money answer a card
    fetches after its first paint, so a KPI answer stays the size it was (#33). Each prints the ledger's whole dollars
    (tile_shown): the lever's, and its rows at standard, as the card's own lines print them."""
    gross, minutes = cell['gross'], cell['scheduled']
    tiles = [tile('observed_shift_fpm', [_part('speed', s['over']['speed'], gross, minutes)], rates, 'the speed under the grade baseline',
                  measure='under the grade baseline'),
             tile('maintenance_pct', [_part('downtime', cell['maintenance'], gross, minutes)], rates, 'maintenance stops, all recorded'),
             tile('operator_pct', [_part('downtime', cell['operator'], gross, minutes)], rates, 'operator stops, all recorded'),
             tile('dry_end_pct', [_part('waste', cell[k], gross, minutes) for k in DRY_END_KINDS], rates, 'dry-end rejects, all recorded'),
             tile('trim_pct', [_part('trim', cell['trim'], gross, minutes)], rates, 'side trim, all recorded')]
    return [tile_shown(t, whole) for t in tiles]


def trim_by_shift(cells, whole, rates, lens='all'):
    """Side trim by shift (the owner's word on PR #168): wherever a card for several shifts shows the trim lever, or the
    ownership lens's trim line, each shift's line, its dollars (above the target; under the ownership lens all recorded)
    with its side trim % of gross and its setups above the per-setup trim flag. Explanatory and schedule-driven: the order
    mix and web widths set it, never a crew's doing. `details` rows, each its shift's whole dollars as the card adds them."""
    if len(whole['shifts']) < 2:
        return []
    by, out = {}, []
    for c in cells:
        by.setdefault(c['shift'], []).append(c)
    for e in whole['shifts']:
        mine = by.get(e['shift'], [])
        s = ledger_sums(mine, rates)
        owner = lens == 'ownership'
        qty = s['all']['trim'] if owner else s['over']['trim']
        p = price('trim', qty, rates, s['gross'], s['scheduled'])
        out.append({'shift': e['shift'], 'key': 'trim', 'part': 'all' if owner else 'above',
                    'lever': TOTAL_TRIM if owner else lever_label('trim', rates), 'quantity': qty, 'usd': p['usd'],
                    'usd_shown': e['levers']['trim'] + (e['at']['trim'] if owner else 0),
                    'trim_pct_of_gross': _pct(s['lines']['trim'], s['lines']['gross']), 'band_target': band_target('trim', rates),
                    'setups': sum(c['setups'] for c in mine), 'setups_above_trim_flag': sum(c['trim_flag_setups'] for c in mine),
                    'trim_flag_pct': targets.TRIM_FLAG_PCT, 'shifts_over': s['over_cells']['trim'], 'shifts': s['cells'],
                    'control': targets.BANDS['trim']['lever'], 'price_terms': {**p['price_terms'], 'trim_cells': s['trim_cells'],
                                                                              'part': 'all' if owner else 'above'}})
    return out


def shift_dollars(analysis, shift, rates):
    """`get_shift_dollars`: one shift on the ledger (issue #164). Its levers, each its gap to its band, ranked by dollars;
    the cost at standard, planned maintenance, external stops and the waste with no band apart, outside the total; the
    machine's period to date and its rate a year beside it. A shift is never annualized on its own. It prints its cell's
    whole dollars (whole_dollars: the cell is the root), the figure the day card's line for it and every window holding it add."""
    store, every, stops, trace = _card(analysis, (1, 2, 3), rates)
    cells = [c for c in every if c['shift'] == shift]
    rows, s = [], ledger_sums(cells, rates)
    terms = display_terms(cells, rates)
    whole = whole_dollars(terms, rates)
    if cells:
        rows = [_lever_row(k, s, rates, f'shift {shift}', stops) for k in LEVERS] + _apart_rows(s, rates)
        for r in rows:
            r['usd_shown'] = row_shown(r, whole)
        rows = rank_ledger(rows)
    _, ptd, ptd_trace, _ = ptd_ledger(store, analysis.day, None, rates)
    cell = cells[0] if cells else None
    coverage = cell['coverage'] if cell else 'unavailable'
    excluded = [e for e in analysis.errors if e.get('shift') == shift]
    data = {'production_day': analysis.day, 'shift': shift, 'dataset_version': analysis.version, 'rows': rows,
            'coverage': 'complete' if coverage == 'complete' else 'partial' if cell else 'unavailable', 'excluded_records': excluded,
            'trace': {'sql': ';\n'.join(trace['sql'] + ptd_trace['sql']), 'parameters': trace['parameters'] + ptd_trace['parameters'],
                      'elapsed_ms': 0.0},
            'gross_sqft': s['gross'], 'scheduled_minutes': s['scheduled'], 'source_ids': cell['source_ids'] if cell else [],
            **ledger_totals(rows), **ptd, **_speeds(s), **_baseline_span(store, analysis.day, analysis.day), 'method_note': LEDGER_NOTE}
    attach_shown(data, rows, whole)
    data['display_terms'] = terms
    if cell and cell['coverage'] == 'complete' and cell['gross']:
        data['tile_money'] = shift_tiles(cell, s, rates, whole)
    return _envelope(data, rates)


def day_dollars(analysis, rates, shifts=(1, 2, 3)):
    """`get_day_dollars`: a production day on the ledger (issue #31b review, #164), the card's total for the day's shifts
    (all three, or the ones a question named). The same cells as each shift's get_shift_dollars, those that are complete
    (an incomplete one is named in `excluded_shifts`, not added), summed: so the day's levers are its shifts' added up, and
    its period to date and rate a year are the machine's, the figures every shift's card states. Its whole dollars are
    its shifts' (whole_dollars), and with several shifts its side trim is listed by shift (`details`)."""
    asked = sorted(set(shifts or (1, 2, 3)))
    store, every, stops, trace = _card(analysis, (1, 2, 3), rates)
    cells = [c for c in every if c['shift'] in asked]
    complete = [c for c in cells if c['coverage'] == 'complete']
    used = complete or cells
    s = ledger_sums(used, rates)
    terms = display_terms(used, rates)
    whole = whole_dollars(terms, rates)
    rows = []
    if used:
        rows = [_lever_row(k, s, rates, 'the day', stops) for k in LEVERS] + _apart_rows(s, rates)
        for r in rows:
            r['usd_shown'] = row_shown(r, whole)
        rows = rank_ledger(rows)
    _, ptd, ptd_trace, _ = ptd_ledger(store, analysis.day, None, rates)
    shown = {e['shift']: e['total'] for e in whole['shifts']}
    data = {'production_day': analysis.day, 'shift': None, 'dataset_version': analysis.version, 'rows': rows,
            'coverage': 'complete' if complete else 'partial',
            'excluded_records': [e for e in analysis.errors if e.get('shift') in [c['shift'] for c in cells if c['coverage'] != 'complete']],
            'excluded_shifts': [c['shift'] for c in cells if c['coverage'] != 'complete'] if complete else [],
            'shifts': [{'shift': c['shift'], 'coverage': c['coverage'], **({'usd_shown': shown[c['shift']]} if c['shift'] in shown else {})}
                       for c in cells],
            'trace': {'sql': ';\n'.join(trace['sql'] + ptd_trace['sql']), 'parameters': trace['parameters'] + ptd_trace['parameters'],
                      'elapsed_ms': 0.0},
            'gross_sqft': s['gross'], 'scheduled_minutes': s['scheduled'], **ledger_totals(rows), **ptd, **_speeds(s),
            **_baseline_span(store, analysis.day, analysis.day), 'method_note': LEDGER_NOTE}
    attach_shown(data, rows, whole)
    data['display_terms'] = terms
    data['details'] = trim_by_shift(used, whole, rates)
    return _envelope(data, rates)


def _ownership_rows(s, ptd, rates, ptd_whole):
    """The ownership lens's lines: the total cost of every waste line, side trim first, each banded line split into its cost
    at standard and above standard (the lever's own figure), and a yearly figure at this period's rate."""
    gross, minutes = s['gross'], s['scheduled']
    lines, plines = s['lines'], ptd['lines']

    def pct(key):
        if key in DRY_END_KINDS:
            return {'pct_of_yield': _pct(lines[key], lines['yield'], 4)}, f'{_waste_label(key)}: {_pct(lines[key], lines["yield"], 4)}% of the yield after trim and shear'
        return {'pct_of_gross': _pct(lines[key], gross, 4)}, f'{_waste_label(key)}: {_pct(lines[key], gross, 4)}% of gross'

    def yearly(kind, key):   # from the period to date's whole dollars of the line, its cells' added up (82c0994, item 2),
        p = price(kind, plines[key], rates)   # each shift's at its own days and the shifts' added up (2510d38, item 1)
        contribution = None if p['contribution_usd'] is None else annual(p['contribution_usd'], ptd['days'])
        return {'usd_per_year': annual_of(ptd_whole, ['lines', key], ptd['shift_days']), 'contribution_usd_per_year': contribution,
                'year_terms': {'kind': kind, 'quantity': plines[key], 'days': ptd['days'], 'shift_days': ptd['shift_days'], 'of': ['lines', key]}}

    def split(row, key):
        above = price(LEVER_KIND[key], s['over'][key], rates, gross, minutes)
        row['above_standard_usd'] = above['usd']
        row['at_standard_usd'] = round(row['usd'] - above['usd'], 2)
        row['above_terms'] = {'kind': LEVER_KIND[key], 'quantity': s['over'][key], **({'trim_cells': s['trim_cells']} if key == 'trim' else {})}
        # the shifts over the band beside the split (the review of PR #168, item 4): the band is each shift's standard, so
        # a window inside it can still hold shifts above it, and the split says how many, as a lever's line does
        row['band_target'], row['shifts_over'], row['shifts'] = band_target(key, rates), s['over_cells'][key], s['cells']
        row['over_records'] = over_records(row['shifts_over'], row['shifts'], row['band_target'])
        return row
    trim_pct = _pct(lines['trim'], gross, 4)
    trim = _control({'lever': TOTAL_TRIM, 'key': 'trim', 'kind': 'trim', 'quantity': lines['trim'], 'unit': 'sq ft', 'pct_of_gross': trim_pct,
                     'records': f'side trim {trim_pct}% of {gross:,.1f} sq ft gross', **price('trim', lines['trim'], rates, gross, minutes)}, 'trim')
    targets.mark(trim, 'trim', trim_pct)
    trim.update(yearly('trim', 'trim'))
    rows = [split(trim, 'trim')]
    for key in ('shear', 'changeover', 'cutoff_waste', 'stacker_waste', 'admin', *DRY_END_KINDS, 'unexplained'):
        # a line the window holds, or one its period to date holds and the window does not (it prints $0 beside its yearly
        # figure): every line with a yearly figure is on every card that adds it, so a shift's card states its own share of
        # the all-shifts card's (the reviewer's gap on 2510d38's round: 29 Jan 2025's shift 2 recorded no planning / admin,
        # and its card left out the $15,637 a year its period to date holds, which the all-shifts card added). A negative
        # remainder is not priced and not shown.
        if lines[key] < 0 or (not lines[key] and not plines[key] > 0):
            continue
        kind = 'paper' if key == 'unexplained' else 'waste'
        share, records = pct(key)
        row = _control({'lever': _waste_label(key) + (' (all recorded)' if key != 'unexplained' else ''), 'key': key, 'kind': kind,
                        'quantity': lines[key], 'unit': 'sq ft', **share, **({'group': DRY_END_GROUP} if key in DRY_END_KINDS else {}),
                        'records': records, **price(kind, lines[key], rates, gross, minutes)}, key)
        row.update(yearly(kind, key))
        rows.append(split(row, 'shear') if key == 'shear' else row)
    return rows


WASTE_LABELS = dict(WASTE_CATEGORIES)


def _waste_label(key):
    return WASTE_LABELS[key]


def owner_order(rows):
    """The ownership lens's lines after side trim: the dry-end group and each other line ranked by dollars (the group by
    the sum of its rows, added left to right as web/money.js does), the group's rows beneath it by dollars. A line's rank
    is its place among the groups (trim is 1); a row inside the group carries no rank of its own. Returns the rows in that
    order and the group's rank (None without one)."""
    groups = {}
    for r in rows:
        groups.setdefault(r.get('group') or r['lever'], []).append(r)
    shown = lambda r: r.get('usd_shown') if r.get('usd_shown') is not None else (r['usd'] or 0)
    by_usd = lambda r: (-shown(r), -(r['usd'] or 0), r['lever'])
    for members in groups.values():
        members.sort(key=by_usd)
    # by the whole dollars printed (PR #168), then the cents: the group by its rows' printed dollars added up
    size = {k: (sum(shown(r) for r in members), _add(r['usd'] or 0 for r in members)) for k, members in groups.items()}
    out, group_rank = [], None
    for rank, key in enumerate(sorted(groups, key=lambda k: (-size[k][0], -size[k][1], k)), 2):
        for r in groups[key]:
            r['rank'] = None if r.get('group') else rank
        if groups[key][0].get('group'):
            group_rank = rank
        out += groups[key]
    return out, group_rank


def dry_end_subtotal(rows, rank, s, rates):
    """The dry-end subtotal (the owner, PR #111): its kinds' dollars added left to right, split at its band: above
    standard is the dry-end lever's own figure, at standard the rest."""
    mine = [r for r in rows if r.get('group') == DRY_END_GROUP]
    years = [r['usd_per_year'] for r in mine]
    usd = round(_add(r['usd'] or 0 for r in mine), 2)
    above = price('waste', s['over']['dry_end'], rates, s['gross'], s['scheduled'])['usd']
    return {'dry_end_rank': rank, 'dry_end_kinds': len(mine), 'dry_end_usd': usd,
            'dry_end_usd_per_year': None if any(v is None for v in years) else round(_add(v or 0 for v in years), 0),
            'dry_end_above_standard_usd': above, 'dry_end_at_standard_usd': round(usd - above, 2),
            'dry_end_above_terms': {'kind': 'waste', 'quantity': s['over']['dry_end']},
            'dry_end_band_target': band_target('dry_end', rates), 'dry_end_shifts_over': s['over_cells']['dry_end'],
            'dry_end_shifts': s['cells'], 'dry_end_over_records': over_records(s['over_cells']['dry_end'], s['cells'], band_target('dry_end', rates))}


def over_records(over, cells, target):
    """A split's shifts over its band, in words (the review of PR #168, item 4): "2 of 3 shifts over the 3% target"."""
    return f'{over} of {cells} shift{"" if cells == 1 else "s"} over the {target:g}% target'


def ownership_shown(rows, whole):
    """The ownership lens's printed whole dollars (PR #168): each line the figure every view of it prints (whole_dollars' `lines`):
    side trim and shear their levers' whole dollars above standard and their rows' at standard added up, so the two lenses
    print one figure; the tag scrap the dry-end lever's and its row at standard's, its kinds sharing it; the waste boards'
    two lines the boards row's; the changeover rejects their row's; planning / admin and the unexplained remainder their
    nearest dollar, as the waste breakdown and the KPI tiles print them. Returns the tag scrap's (above, at)."""
    for r in rows:
        k = r['key']
        if k in ('trim', 'shear'):
            r['above_standard_usd_shown'], r['at_standard_usd_shown'] = whole['levers'][k], whole['at'][k]
        r['usd_shown'] = whole['lines'][k]
    return whole['levers']['dry_end'], whole['at']['dry_end']


def ownership_totals_shown(data, rows, dry, whole):
    """The ownership lens's printed totals: all the waste is its lines' whole dollars added up, at standard and above
    standard the split lines' parts added up, with no band yet the rest, and paper and machine time its cells' shares of
    their lines (so they add up to it in every window), the contribution beside it."""
    split = [r for r in rows if r.get('above_standard_usd_shown') is not None]
    group = data.get('dry_end_rank') is not None
    data['total_usd_shown'] = sum(r['usd_shown'] for r in rows)
    data['above_standard_usd_shown'] = sum(r['above_standard_usd_shown'] for r in split) + (dry[0] if group else 0)
    data['at_standard_usd_shown'] = sum(r['at_standard_usd_shown'] for r in split) + (dry[1] if group else 0)
    data['no_band_usd_shown'] = data['total_usd_shown'] - data['above_standard_usd_shown'] - data['at_standard_usd_shown']
    if group:
        data['dry_end_usd_shown'] = sum(r['usd_shown'] for r in rows if r.get('group') == DRY_END_GROUP)
        data['dry_end_above_standard_usd_shown'], data['dry_end_at_standard_usd_shown'] = dry
    if whole['owner'] == data['total_usd_shown']:
        data['total_paper_usd_shown'], data['total_machine_time_usd_shown'] = whole['owner_paper'], whole['owner_machine']
    else:   # a line the window leaves out (a negative remainder): the card's own split, still adding up to its figure
        data['total_paper_usd_shown'], data['total_machine_time_usd_shown'] = split_shown(
            [data.get('total_paper_usd') or 0, data.get('total_machine_time_usd') or 0], data['total_usd_shown'])
    data['total_contribution_usd_shown'] = None if data.get('total_contribution_usd') is None else whole['owner_contribution']
    for b in data.get('buckets') or []:
        b['usd_shown'] = sum(r['usd_shown'] for r in rows if bucket_of(r) == b['bucket'])
    return data


def opportunities(store, window, shift, rates, lens='all'):
    """`get_opportunities`: a window on the ledger (issue #164), its levers ranked by dollars, each its gap to its band,
    marked controllable or schedule-driven (issue #101), with its owner role and where to look; the cost at standard,
    planned maintenance, external stops and the waste no band covers shown apart. A yearly figure is the scope's period
    to date at this period's rate (the 4-4-5 period containing the window's last day), never the window's own. It ranks
    levers and names records; it never prescribes a machine adjustment.

    `lens` picks the owner's view of the same numbers: `all` ranks every lever; `supervisor` ranks the controllable levers
    and lists side trim (schedule-driven) after, unranked, with the rows shown apart; `ownership` is the total cost of
    waste, side trim first (all of it), then every other line of the waste breakdown by dollars, the dry-end families as
    one subtotal, each banded line split into its cost at standard and above standard (the levers' own figures), so the
    lens's total is the waste breakdown's for the same window."""
    if lens not in LENSES:
        raise ValueError('lens is all, supervisor or ownership')
    current = store._window(window)
    cells, stops, trace = ledger_cells(store, current['start'], current['end'], shift)
    s = ledger_sums(cells, rates)
    # the whole dollars it prints are its cells' added up (whole_dollars: the cell is the root, PR #168)
    terms = display_terms(cells, rates)
    whole = whole_dollars(terms, rates)
    through = current['end'] if current['coverage'] != 'unavailable' else store.last_day
    ptd_s, ptd, ptd_trace, ptd_whole = ptd_ledger(store, through, shift, rates)
    scope = f'shift {shift}' if shift else 'all shifts'
    group = {}
    if lens == 'ownership':
        rows = _ownership_rows(s, ptd_s, rates, ptd_whole)
        dry = ownership_shown(rows, whole)
        rows[0]['rank'] = 1
        rest, group_rank = owner_order(rows[1:])
        rows = rows[:1] + rest
        if group_rank is not None:
            group = {**dry_end_subtotal(rows, group_rank, s, rates), 'dry_end_label': DRY_END_LABEL, 'dry_end_sqft': s['lines']['dry_end'],
                     'dry_end_pct_of_yield': _pct(s['lines']['dry_end'], s['lines']['yield'], 4)}
            targets.mark(group, 'dry_end', group['dry_end_pct_of_yield'])
            group['dry_end_band'], group['dry_end_band_text'] = group.pop('band'), group.pop('band_text')
        above = [r['above_standard_usd'] for r in rows if r.get('above_standard_usd') is not None] + (
            [group['dry_end_above_standard_usd']] if group else [])
        totals = {**_totals(rows), **summary(rows), 'above_standard_usd': round(_add(above), 2),
                  'at_standard_usd': round(_add(r['at_standard_usd'] for r in rows if r.get('at_standard_usd') is not None) +
                                           (group['dry_end_at_standard_usd'] if group else 0.0), 2)}
        totals['no_band_usd'] = round(totals['total_usd'] - totals['above_standard_usd'] - totals['at_standard_usd'], 2)
        totals = ownership_totals_shown({**totals, **group}, rows, dry, whole)
    else:
        rows = [_lever_row(k, s, rates, scope, stops, ptd_s, ptd_whole) for k in LEVERS] + _apart_rows(s, rates)
        for r in rows:
            r['usd_shown'] = row_shown(r, whole)
        rows = rank_ledger(rows, lens)
        totals = attach_shown(ledger_totals(rows), rows, whole)
    controllable = [r for r in rows if r.get('control') == 'controllable' and not r.get('apart') and lens != 'ownership']
    coverage = current['coverage']
    notes = {'all': LEDGER_NOTE,
             'supervisor': 'The supervisor lens: the controllable levers ranked, each its gap to its band; side trim is schedule-driven and '
                           'listed after with the rows shown apart. ' + LEDGER_NOTE,
             'ownership': ('The ownership lens: the total cost of the waste recorded, side trim first, then the rest by dollars, each line '
                           'of the waste breakdown priced once, so the total is the waste breakdown\'s. A banded line is split at its band: '
                           'at standard, the cost of the order mix and the process; above standard, the lever\'s own figure. The dry-end '
                           'waste is the tag scrap: bond, warp, misalignment, moisture and other, one subtotal with each kind beneath it. A '
                           'yearly figure is this period\'s rate, period-to-date dollars per production day x the post\'s production days a year. '
                           'For all shifts it is each shift\'s period-to-date rate added up, so the shifts\' yearly figures add to it; a line '
                           'the period to date holds still shows when the window holds none of it.')}[lens]
    data = {'tool': 'get_opportunities', 'window': current, 'previous_window': None, 'shift': shift, 'lens': lens,
            'dataset_version': store.content_hash, 'rows': rows, 'coverage': 'complete' if coverage == 'complete' else coverage,
            'excluded_records': [], 'source_rows': {'shifts': s['cells'], 'runs': s['runs']},
            'trace': {'sql': ';\n'.join(trace['sql'] + ptd_trace['sql']), 'parameters': trace['parameters'] + ptd_trace['parameters'],
                      'elapsed_ms': 0.0},
            'gross_sqft': s['gross'], 'scheduled_minutes': s['scheduled'], **totals, **group,
            # the controllable levers in their ranked order, added left to right (_add) as web/money.js re-derives them
            'controllable_levers': len(controllable), 'controllable_usd': round(_add(r['usd'] or 0 for r in controllable), 2),
            'controllable_usd_per_year': round(_add(r['usd_per_year'] or 0 for r in controllable), 0)
                if all(r['usd_per_year'] is not None for r in controllable) else None,
            'controllable_usd_shown': sum(r['usd_shown'] for r in controllable),
            'runs_under_baseline': s['runs_under'], **ptd, **_speeds(s),
            **_baseline_span(store, current['start'], current['end'] if coverage != 'unavailable' else through),
            'method_note': notes, 'display_terms': terms,
            'details': trim_by_shift(cells, whole, rates, 'ownership' if lens == 'ownership' else 'all')}
    return _envelope(data, rates)
