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
                          'net_width_in': ASSUMPTIONS['net_width_in'], 'scheduled_min_day': ASSUMPTIONS['scheduled_min_day']},
            'margin_not_set': MARGIN_NOT_SET, 'note': NOTE}


def rate_rows(rates):
    """The rates as rows: what a card's provenance line and the narrative may name."""
    return [{'rate_id': rid, 'label': spec['label'], 'unit': spec['unit'], 'value': rates['values'][rid],
             'status': 'set' if rates['values'][rid] is not None else 'not set', 'source': rates['sources'][rid]}
            for rid, spec in RATE_SPECS.items()]


def _money(value):
    return f'${value:,.0f}' if abs(value) >= 100 else f'${value:,.2f}'


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
    usd = (paper_usd or 0.0) + machine_usd
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
    money tools call it with period-to-date dollars only. Whole dollars; None without dollars or days."""
    return None if usd is None or not days else round(usd / days * DAYS_PER_YEAR, 0)


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


def period_tiles(data, reasons, rates):
    """The money of a window's KPI tiles (get_period_kpis, compare_periods): each waste tile its category's area, each
    stop tile the stop reasons' minutes (`reasons`, the Pareto's rows of the same window and shift). A tile is what its
    figure measures, all recorded: the ownership lens's line for the same window, never a lever's above-standard part."""
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
    out.append(tile('maintenance_pm_pct', [_part('downtime', round(sums['maintenance_pm_seconds'] / 60, 2), gross, minutes)], rates, 'maintenance and planned maintenance (scheduled) stops'))
    out.append(tile('operator_pct', [_part('downtime', round(sums['operator_seconds'] / 60, 2), gross, minutes)], rates, 'operator stops'))
    return out


def _rank(rows, key):
    rows.sort(key=lambda r: (-(r[key] or 0), r['gap'] if 'gap' in r else r['lever']))
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
               'adds $0, and the cost inside the bands is shown apart, at standard, outside the total. A yearly figure is this '
               'period\'s rate, period-to-date dollars per production day x the post\'s production days a year, never a shift\'s or '
               'a day\'s own. These name where to look and whom to ask; they never prescribe a machine adjustment.')

CELL_SQL = ("SELECT production_date, shift, coverage, setups, gross_sqft, trim_sqft, shear_sqft, reject_sqft, bond_sqft, warp_sqft, "
            "misalignment_sqft, moisture_sqft, other_sqft, changeover_sqft, cutoff_waste_sqft, stacker_waste_sqft, admin_sqft, bale_sqft, "
            "scheduled_seconds, stop_seconds, maintenance_seconds, planned_maintenance_seconds, operator_seconds, lineal_ft, source_ids "
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


def ptd_window(store, through):
    """The period to date a yearly figure is read from: the 4-4-5 period containing `through` (the window's last day, or
    the card's day), from its first day inside the record store to `through`. In a period's first week, when it holds
    fewer than MIN_RATE_DAYS production days, the period before it is read with it ("P3 and P4 to date"), so a yearly
    figure is never one day's or one shift's. Returns (start, end, label)."""
    day = min(date.fromisoformat(str(through)[:10]), store._last)
    start, _, label = fiscal_bounds(day)
    name = label.split(' (')[0]
    if len([d for d in store.production_days if start.isoformat() <= d <= day.isoformat()]) < MIN_RATE_DAYS:
        before, _, prior = fiscal_bounds(start - timedelta(days=1))
        if start > store._first:
            name = f'{prior.split(" (")[0]} and {name.split(" ", 1)[1] if name.startswith("4-4-5 ") else name}'
            start = before
    return max(start, store._first), day, f'{name} to date'


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


def _lever_row(key, s, rates, scope, stops, ptd=None):
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
        row.update(_yearly(key, ptd, rates))
    return row


def _yearly(key, ptd, rates):
    """A lever's dollars a year at this period's rate: its period-to-date dollars per production day x the production days
    a year (`annual`); `year_terms` re-derive it at a visitor's rates in the browser."""
    kind, qty = LEVER_KIND[key], ptd['over'][key]
    p = price(kind, qty, rates)
    terms = {'kind': kind, 'quantity': qty, 'days': ptd['days'], **({'trim_cells': ptd['trim_cells']} if key == 'trim' else {})}
    contribution = None if p['contribution_usd'] is None else annual(p['contribution_usd'], ptd['days'])
    return {'usd_per_year': annual(p['usd'], ptd['days']), 'contribution_usd_per_year': contribution, 'year_terms': terms}


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
    rest.sort(key=lambda r: (group(r), -(r['usd'] or 0), r['lever']))
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


def ptd_ledger(store, through, shift, rates):
    """The period to date of `through` for `shift` (None: all shifts): its ledger, the dollars above standard of each lever
    and in all, and the same at this period's rate a year. `ptd_terms` re-derive it at a visitor's rates in the browser."""
    start, end, label = ptd_window(store, through)
    cells, _, trace = ledger_cells(store, start, end, shift)
    s = ledger_sums(cells, rates)
    levers = []
    for key in LEVERS:
        p = price(LEVER_KIND[key], s['over'][key], rates)
        levers.append({'key': key, 'lever': lever_label(key, rates), 'usd': p['usd'], 'usd_per_year': annual(p['usd'], s['days'])})
    years = [r['usd_per_year'] for r in levers]
    out = {'ptd_label': label, 'ptd_start': start.isoformat(), 'ptd_through': end.isoformat(),
           'ptd_days': s['days'], 'ptd_scope': f'shift {shift}' if shift else 'all shifts',
           'ptd_usd': round(_add(r['usd'] for r in levers), 2),
           'ptd_usd_per_year': None if None in years else round(_add(years), 0),
           'ptd_levers': levers,
           'ptd_terms': {'days': s['days'], 'levers': [{'key': k, 'kind': LEVER_KIND[k], 'quantity': s['over'][k],
                                                        **({'trim_cells': s['trim_cells']} if k == 'trim' else {})} for k in LEVERS]}}
    return s, out, trace


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
    """The ledger of the shifts `shifts` of the analysis's day: their cells, rows, totals, and the machine's period to date."""
    store, corrupt = _cards_store(analysis)
    exclude = tuple(e['record_id'] for e in analysis.errors) if corrupt else ()
    cells, stops, trace = ledger_cells(store, analysis.day, analysis.day, None, corrupt, exclude)
    cells = [c for c in cells if c['shift'] in shifts]
    return store, cells, stops, trace


def shift_tiles(cell, s, rates):
    """The money of a shift's KPI tiles, from the cell the card prices: the speed tile is the speed lever (the grade
    baseline, the one speed a price uses); side trim, the dry-end families, and the maintenance and operator minutes are
    all recorded, as the ownership lens's lines of the same shift. Returned on get_shift_dollars, the money answer a card
    fetches after its first paint, so a KPI answer stays the size it was (#33)."""
    gross, minutes = cell['gross'], cell['scheduled']
    return [tile('observed_shift_fpm', [_part('speed', s['over']['speed'], gross, minutes)], rates, 'the speed under the grade baseline',
                 measure='under the grade baseline'),
            tile('maintenance_pct', [_part('downtime', cell['maintenance'], gross, minutes)], rates, 'maintenance stops, all recorded'),
            tile('operator_pct', [_part('downtime', cell['operator'], gross, minutes)], rates, 'operator stops, all recorded'),
            tile('dry_end_pct', [_part('waste', cell[k], gross, minutes) for k in DRY_END_KINDS], rates, 'dry-end rejects, all recorded'),
            tile('trim_pct', [_part('trim', cell['trim'], gross, minutes)], rates, 'side trim, all recorded')]


def shift_dollars(analysis, shift, rates):
    """`get_shift_dollars`: one shift on the ledger (issue #164). Its levers, each its gap to its band, ranked by dollars;
    the cost at standard, planned maintenance, external stops and the waste with no band apart, outside the total; the
    machine's period to date and its rate a year beside it. A shift is never annualized on its own."""
    store, cells, stops, trace = _card(analysis, (shift,), rates)
    rows, s = [], ledger_sums(cells, rates)
    if cells:
        rows = rank_ledger([_lever_row(k, s, rates, f'shift {shift}', stops) for k in LEVERS] + _apart_rows(s, rates))
    _, ptd, ptd_trace = ptd_ledger(store, analysis.day, None, rates)
    cell = cells[0] if cells else None
    coverage = cell['coverage'] if cell else 'unavailable'
    excluded = [e for e in analysis.errors if e.get('shift') == shift]
    data = {'production_day': analysis.day, 'shift': shift, 'dataset_version': analysis.version, 'rows': rows,
            'coverage': 'complete' if coverage == 'complete' else 'partial' if cell else 'unavailable', 'excluded_records': excluded,
            'trace': {'sql': ';\n'.join(trace['sql'] + ptd_trace['sql']), 'parameters': trace['parameters'] + ptd_trace['parameters'],
                      'elapsed_ms': 0.0},
            'gross_sqft': s['gross'], 'scheduled_minutes': s['scheduled'], 'source_ids': cell['source_ids'] if cell else [],
            **ledger_totals(rows), **ptd, **_speeds(s), **_baseline_span(store, analysis.day, analysis.day), 'method_note': LEDGER_NOTE}
    if cell and cell['coverage'] == 'complete' and cell['gross']:
        data['tile_money'] = shift_tiles(cell, s, rates)
    return _envelope(data, rates)


def day_dollars(analysis, rates, shifts=(1, 2, 3)):
    """`get_day_dollars`: a production day on the ledger (issue #31b review, #164), the card's total for the day's shifts
    (all three, or the ones a question named). The same cells as each shift's get_shift_dollars, those that are complete
    (an incomplete one is named in `excluded_shifts`, not added), summed: so the day's levers are its shifts' added up, and
    its period to date and rate a year are the machine's, the figures every shift's card states."""
    asked = sorted(set(shifts or (1, 2, 3)))
    store, cells, stops, trace = _card(analysis, asked, rates)
    complete = [c for c in cells if c['coverage'] == 'complete']
    used = complete or cells
    s = ledger_sums(used, rates)
    rows = rank_ledger([_lever_row(k, s, rates, 'the day', stops) for k in LEVERS] + _apart_rows(s, rates)) if used else []
    _, ptd, ptd_trace = ptd_ledger(store, analysis.day, None, rates)
    data = {'production_day': analysis.day, 'shift': None, 'dataset_version': analysis.version, 'rows': rows,
            'coverage': 'complete' if complete else 'partial',
            'excluded_records': [e for e in analysis.errors if e.get('shift') in [c['shift'] for c in cells if c['coverage'] != 'complete']],
            'excluded_shifts': [c['shift'] for c in cells if c['coverage'] != 'complete'] if complete else [],
            'shifts': [{'shift': c['shift'], 'coverage': c['coverage']} for c in cells],
            'trace': {'sql': ';\n'.join(trace['sql'] + ptd_trace['sql']), 'parameters': trace['parameters'] + ptd_trace['parameters'],
                      'elapsed_ms': 0.0},
            'gross_sqft': s['gross'], 'scheduled_minutes': s['scheduled'], **ledger_totals(rows), **ptd, **_speeds(s),
            **_baseline_span(store, analysis.day, analysis.day), 'method_note': LEDGER_NOTE}
    return _envelope(data, rates)


def _ownership_rows(s, ptd, rates):
    """The ownership lens's lines: the total cost of every waste line, side trim first, each banded line split into its cost
    at standard and above standard (the lever's own figure), and a yearly figure at this period's rate."""
    gross, minutes = s['gross'], s['scheduled']
    lines, plines = s['lines'], ptd['lines']

    def pct(key):
        if key in DRY_END_KINDS:
            return {'pct_of_yield': _pct(lines[key], lines['yield'], 4)}, f'{_waste_label(key)}: {_pct(lines[key], lines["yield"], 4)}% of the yield after trim and shear'
        return {'pct_of_gross': _pct(lines[key], gross, 4)}, f'{_waste_label(key)}: {_pct(lines[key], gross, 4)}% of gross'

    def yearly(kind, key):
        p = price(kind, plines[key], rates)
        contribution = None if p['contribution_usd'] is None else annual(p['contribution_usd'], ptd['days'])
        return {'usd_per_year': annual(p['usd'], ptd['days']), 'contribution_usd_per_year': contribution,
                'year_terms': {'kind': kind, 'quantity': plines[key], 'days': ptd['days']}}

    def split(row, key):
        above = price(LEVER_KIND[key], s['over'][key], rates, gross, minutes)
        row['above_standard_usd'] = above['usd']
        row['at_standard_usd'] = round(row['usd'] - above['usd'], 2)
        row['above_terms'] = {'kind': LEVER_KIND[key], 'quantity': s['over'][key], **({'trim_cells': s['trim_cells']} if key == 'trim' else {})}
        return row
    trim_pct = _pct(lines['trim'], gross, 4)
    trim = _control({'lever': TOTAL_TRIM, 'key': 'trim', 'kind': 'trim', 'quantity': lines['trim'], 'unit': 'sq ft', 'pct_of_gross': trim_pct,
                     'records': f'side trim {trim_pct}% of {gross:,.1f} sq ft gross', **price('trim', lines['trim'], rates, gross, minutes)}, 'trim')
    targets.mark(trim, 'trim', trim_pct)
    trim.update(yearly('trim', 'trim'))
    rows = [split(trim, 'trim')]
    for key in ('shear', 'changeover', 'cutoff_waste', 'stacker_waste', 'admin', *DRY_END_KINDS, 'unexplained'):
        if not lines[key] or lines[key] <= 0:
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
    by_usd = lambda r: (-(r['usd'] or 0), r['lever'])
    for members in groups.values():
        members.sort(key=by_usd)
    size = {k: _add(r['usd'] or 0 for r in members) for k, members in groups.items()}
    out, group_rank = [], None
    for rank, key in enumerate(sorted(groups, key=lambda k: (-size[k], k)), 2):
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
            'dry_end_above_terms': {'kind': 'waste', 'quantity': s['over']['dry_end']}}


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
    through = current['end'] if current['coverage'] != 'unavailable' else store.last_day
    ptd_s, ptd, ptd_trace = ptd_ledger(store, through, shift, rates)
    scope = f'shift {shift}' if shift else 'all shifts'
    group = {}
    if lens == 'ownership':
        rows = _ownership_rows(s, ptd_s, rates)
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
    else:
        rows = rank_ledger([_lever_row(k, s, rates, scope, stops, ptd_s) for k in LEVERS] + _apart_rows(s, rates), lens)
        totals = ledger_totals(rows)
    controllable = [r for r in rows if r.get('control') == 'controllable' and not r.get('apart') and lens != 'ownership']
    coverage = current['coverage']
    notes = {'all': LEDGER_NOTE,
             'supervisor': 'The supervisor lens: the controllable levers ranked, each its gap to its band; side trim is schedule-driven and '
                           'listed after with the rows shown apart. ' + LEDGER_NOTE,
             'ownership': ('The ownership lens: the total cost of the waste recorded, side trim first, then the rest by dollars, each line '
                           'of the waste breakdown priced once, so the total is the waste breakdown\'s. A banded line is split at its band: '
                           'at standard, the cost of the order mix and the process; above standard, the lever\'s own figure. The dry-end '
                           'waste is the tag scrap: bond, warp, misalignment, moisture and other, one subtotal with each kind beneath it. A '
                           'yearly figure is this period\'s rate, period-to-date dollars per production day x the post\'s production days a year.')}[lens]
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
            'runs_under_baseline': s['runs_under'], **ptd, **_speeds(s),
            **_baseline_span(store, current['start'], current['end'] if coverage != 'unavailable' else through),
            'method_note': notes}
    return _envelope(data, rates)
