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

Conversions are the post's: one minute at the required 675 ft/min on a 96 in net width is
675 x 96 / 12 / 1000 = 5.4 MSF. Annualized at the post's volumes: paper by volume (the window's
waste share of gross x 1.8M MSF a year per machine), machine time and contribution on time by time
(the window's minutes per scheduled minute x 1,440 min x 1.8M / 7,000 MSF days a year).
"""
import json
import math
import os
import re
from pathlib import Path

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
            'constants': {'msf_per_minute': MSF_PER_MINUTE, 'annual_minutes': ANNUAL_MINUTES, 'shift_minutes': SHIFT_MINUTES,
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
    'downtime' or 'speed' (minutes of machine time). `gross_sqft` and `minutes` are the window's gross
    area and scheduled minutes, for the per-year figures; without them the per-year fields are null.
    Returns the flat dollar fields a row carries (flat, so the narrative can cite each)."""
    v = rates['values']
    paper, hour, margin = v['paper_usd_msf'], v['machine_usd_hour'], v['margin_usd_msf']
    by_volume = ASSUMPTIONS['annual_msf_per_machine'] / (gross_sqft / 1000) if gross_sqft else None
    by_time = ANNUAL_MINUTES / minutes if minutes else None
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
        paper_year = paper_usd * by_volume if by_volume else None
    elif kind in ('downtime', 'speed'):
        msf, paper_usd, paper_year = None, None, 0.0
        machine_minutes = quantity
        not_made = quantity * MSF_PER_MINUTE
    else:
        raise ValueError(f'Unknown cost kind {kind!r}')
    machine_usd = machine_minutes / 60 * hour
    if kind != 'trim' and kind != 'paper':
        lead = '' if kind in ('downtime', 'speed') else f'{msf:,.3f} MSF / {MSF_PER_MINUTE:g} MSF per min = '
        parts.append(f'{lead}{machine_minutes:,.2f} min x ${rate_text(hour, True)}/hr machine time = {_money(machine_usd)}')
    usd = (paper_usd or 0.0) + machine_usd
    # The run time of waste board is annualized like its paper, by volume; downtime and the speed gap by time.
    factor = by_volume if kind == 'waste' else by_time
    machine_year = 0.0 if machine_usd == 0 else machine_usd * factor if factor else None
    usd_year = paper_year + machine_year if paper_year is not None and machine_year is not None else None
    if margin is None:
        contribution, contribution_year, status = None, None, MARGIN_NOT_SET
        parts.append(f'contribution: {MARGIN_NOT_SET}')
    else:
        contribution, status = not_made * margin, 'set'
        factor = by_volume if kind in ('trim', 'waste', 'paper') else by_time
        contribution_year = contribution * factor if factor else None
        parts.append(f'contribution {not_made:,.3f} MSF not made x ${rate_text(margin)}/MSF = {_money(contribution)} (not added)')
    # The per-year factors, written out so the yearly figure can be redone on paper too.
    volume_text = (f'x {by_volume:,.2f} ({ASSUMPTIONS["annual_msf_per_machine"]:,} MSF a year / {gross_sqft / 1000:,.3f} MSF gross)'
                   if by_volume else None)
    time_text = f'x {by_time:,.2f} ({ANNUAL_MINUTES:,.0f} scheduled min a year / {minutes:,g} min)' if by_time else None
    if usd_year is None:
        parts.append('per year: not computed (no window volume or minutes)')
    elif kind in ('trim', 'paper', 'waste'):
        parts.append(f'per year: {volume_text}')
    else:
        parts.append(f'per year: {time_text}')
    # `price_terms` (issue #31b) is the call itself, rate-free and unrounded: kind, quantity, and the window's gross
    # area and scheduled minutes. The card re-prices a row at a visitor's rates in the browser by making this same
    # call (web/money.js price), so a figure there and the server's figure at the same rates never disagree.
    r2 = lambda x: None if x is None else round(x, 2)
    return {'msf': None if msf is None else round(msf, 3), 'paper_usd': r2(paper_usd),
            'machine_minutes': round(machine_minutes, 2), 'machine_time_usd': r2(machine_usd), 'usd': r2(usd),
            'usd_per_year': None if usd_year is None else round(usd_year, 0),
            'msf_not_made': round(not_made, 3), 'contribution_usd': r2(contribution),
            'contribution_usd_per_year': None if contribution_year is None else round(contribution_year, 0),
            'contribution_status': status, 'usd_basis': '; '.join(parts),
            'share_of_window_pct': None if not (gross_sqft if kind in ('trim', 'waste', 'paper') else minutes) else
                round(quantity / (gross_sqft if kind in ('trim', 'waste', 'paper') else minutes) * 100, 4),
            'price_terms': {'kind': kind, 'quantity': quantity, 'gross_sqft': gross_sqft or None, 'minutes': minutes or None}}


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
WASTE_KINDS = {'trim': 'trim', 'shear': 'waste', 'bond': 'waste', 'warp': 'waste', 'misalignment': 'waste',
               'moisture': 'waste', 'dry_end': 'waste', 'bale': 'paper', 'unexplained': 'paper'}
PRICED_TOOLS = ('get_quality_breakdown', 'get_downtime_breakdown', 'get_waste_breakdown',
                'get_downtime_pareto', 'get_speed_opportunity', 'get_speed_variance')
GAIN = 'not priced: this bucket ran better than the reference (a gain, not a loss)'


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
    """Each speed-variance bucket as machine time (issue #92): its lineal feet over the reference speed
    are the minutes the corrugator would need at that speed to make them, priced like the speed gap.
    A bucket that ran better than the reference is a gain and is not priced, whichever way the gap goes;
    the gap as a whole is priced on the envelope (`gap_usd`) when the window is slower than its reference,
    so the bucket dollars add up to it only when every bucket is a loss."""
    minutes, reference = data['scheduled_minutes'], data['reference_speed_fpm']

    def priced_for(feet):
        if feet is None or not reference or feet <= 0:
            return {**price('speed', None, rates), 'usd_basis': GAIN} if feet is not None and feet < 0 else price('speed', None, rates)
        out = price('speed', feet / reference, rates, None, minutes)
        out['usd_basis'] = f'{feet:,.1f} lineal ft / {reference:,.1f} ft/min reference speed = ' + out['usd_basis']
        return out
    for row in data['rows']:
        row.update(priced_for(row['gap_lineal_ft']))
    gap = priced_for(data['gap_lineal_ft'])
    # the margin's status is the rates', not the gap's: a faster window still prices its loss buckets
    data.update({'gap_machine_minutes': gap['machine_minutes'], 'gap_usd': gap['usd'], 'gap_usd_per_year': gap['usd_per_year'],
                 'gap_contribution_usd': gap['contribution_usd'],
                 'contribution_status': MARGIN_NOT_SET if rates['values']['margin_usd_msf'] is None else 'set'})
    return data


# ------------------------------------------------------------------ the two money tools

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


def bucket_of(row):
    """The bucket a priced row adds to on the card's chips: setups, stops, speed, trim, shear, dry-end."""
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


def trim_over_target(trim_pct, gross_sqft, minutes, rates):
    """Side-trim points over the trim target and their paper dollars a year at the post's volume (the post's
    formula, points / 100 x 1.8M MSF x paper), the arithmetic's trim line. Flat fields beside the totals;
    `trim_over_terms` re-derives them at another target or paper rate in the browser."""
    target = rates['values']['trim_target_pct']
    over = max(0.0, (trim_pct or 0) - target)
    p = price('trim', over / 100 * gross_sqft if gross_sqft else 0.0, rates, gross_sqft, minutes)
    return {'trim_pct_of_gross': None if trim_pct is None else round(trim_pct, 4), 'trim_target_pct': target,
            'trim_points_over': round(over, 4), 'trim_over_usd': p['usd'], 'trim_over_usd_per_year': p['usd_per_year'],
            'trim_over_basis': p['usd_basis'], 'trim_over_terms': {**p['price_terms'], 'trim_pct': trim_pct or 0}}


def tile(metric, parts, rates, what, metrics=None):
    """A KPI tile's own loss priced (issue #31b review): one pricing call per part, each the quantity a tool row of
    the same scope prices (a reject reason, a stop reason, a waste category), so the tile and that row agree to the
    cent. `parts` are price_terms; the tile is their sum, as the rows' totals are. `metric` names the tile: the KPI
    field it reads (get_shift_kpis) or its row's metric (get_period_kpis, compare_periods); `metrics` every tile the
    same loss sits on (the stop tiles: downtime %, downtime minutes and stops are all the window's stops)."""
    priced = [price(p['kind'], p['quantity'], rates, p['gross_sqft'], p['minutes']) for p in parts]
    usd = [r['usd'] for r in priced if r['usd'] is not None]
    years = [r['usd_per_year'] for r in priced]
    return {'metric': metric, 'metrics': list(metrics or [metric]), 'what': what, 'usd': round(_add(usd), 2) if usd else None,
            'usd_per_year': None if not priced or None in years else round(_add(years), 0),
            'machine_minutes': round(_add(r['machine_minutes'] or 0 for r in priced), 2),
            'usd_basis': ' + '.join(r['usd_basis'] for r in priced), 'parts': [r['price_terms'] for r in priced]}


def _part(kind, quantity, gross, minutes):
    return {'kind': kind, 'quantity': quantity, 'gross_sqft': gross or None, 'minutes': minutes or None}


def shift_tiles(analysis, shift, k, rates):
    """The money of a shift's KPI tiles, from the same calls get_shift_dollars makes: the speed gap, side trim, each
    dry-end reason (so the dry-end tile is that tool's Dry-end bucket), and the maintenance and operator minutes.
    Returned on get_shift_dollars, the money answer a card fetches after its first paint, so a KPI answer stays the
    size it was (#33) and a tile and the chip it agrees with come in one response."""
    gross = k.get('gross_sqft')
    if not gross:
        return []
    running = SHIFT_MINUTES - (k['downtime_pct'] or 0) * SHIFT_MINUTES / 100
    required = ASSUMPTIONS['required_fpm']
    kinds = {r['category']: r['down_minutes'] for r in analysis.get_downtime_breakdown(shift, group_by='kind')['rows']}
    rejects = analysis.get_quality_breakdown(shift)['rows']
    return [tile('observed_shift_fpm', [_part('speed', max(0.0, required * running - k['observed_lineal_ft']) / required, gross, SHIFT_MINUTES)],
                 rates, f'the speed gap to {required:g} ft/min'),
            tile('maintenance_pct', [_part('downtime', kinds.get('Maintenance', 0.0), gross, SHIFT_MINUTES)], rates, 'maintenance stops'),
            tile('operator_pct', [_part('downtime', kinds.get('Operator', 0.0), gross, SHIFT_MINUTES)], rates, 'operator stops'),
            tile('dry_end_pct', [_part('waste', r['rejected_sqft'], gross, SHIFT_MINUTES) for r in rejects], rates, 'dry-end rejects'),
            tile('trim_pct', [_part('trim', k['trim_pct'] * gross / 100, gross, SHIFT_MINUTES)], rates, 'side trim')]


# The window tiles priced like get_waste_breakdown's rows (sq ft to 0.1, the gross to 0.1) and the Pareto's (minutes to
# 0.01 per reason), so a tile agrees with those rows on the same page to the cent.
WASTE_TILES = {'trim_pct': 'trim', 'shear_pct': 'shear', 'dry_end_pct': 'dry_end', 'bond_pct': 'bond', 'warp_pct': 'warp',
               'misalignment_pct': 'misalignment', 'moisture_pct': 'moisture', 'bale_pct': 'bale', 'unexplained_pct': 'unexplained'}


def period_tiles(data, reasons, rates):
    """The money of a window's KPI tiles (get_period_kpis, compare_periods): each waste tile its category's area, each
    stop tile the stop reasons' minutes (`reasons`, the Pareto's rows of the same window and shift)."""
    sums = data.get('current_sums') or {}
    if not sums.get('gross_sqft'):
        return []
    gross, minutes = round(sums['gross_sqft'], 1), window_minutes(data)
    out = [tile(metric, [_part(WASTE_KINDS[key], round(sums[key], 1), gross, minutes)], rates, key.replace('_', '-') if key != 'dry_end' else 'dry-end rejects')
           for metric, key in WASTE_TILES.items()]
    stops = [_part('downtime', r['minutes'], gross, minutes) for r in reasons]
    out.append(tile('downtime_pct', stops, rates, 'all stops', ('downtime_pct', 'downtime_minutes', 'stops')))   # one entry, three tiles
    out.append(tile('maintenance_pm_pct', [_part('downtime', round(sums['maintenance_pm_seconds'] / 60, 2), gross, minutes)], rates, 'maintenance and PM stops'))
    out.append(tile('operator_pct', [_part('downtime', round(sums['operator_seconds'] / 60, 2), gross, minutes)], rates, 'operator stops'))
    return out


def _rank(rows, key):
    rows.sort(key=lambda r: (-(r[key] or 0), r['gap'] if 'gap' in r else r['lever']))
    for i, r in enumerate(rows, 1):
        r['rank'] = i
    return rows


def shift_dollars(analysis, shift, rates):
    """`get_shift_dollars`: where the money went in one shift, every gap priced, ranked by dollars.
    Gaps: each downtime reason (minutes), the speed gap to the required 675 ft/min (running time
    only), side trim, shear and each dry-end reason (square feet)."""
    kpis = analysis.get_shift_kpis(shift)
    stops = analysis.get_downtime_breakdown(shift)
    rejects = analysis.get_quality_breakdown(shift)
    k = kpis['rows'][0]
    gross = k['gross_sqft']
    rows = []
    for r in stops['rows']:
        rows.append({'gap': f'Downtime: {r["category"]}', 'kind': 'setup' if r['category'] == 'Order change' else 'downtime',
                     'quantity': round(r['down_minutes'], 2), 'unit': 'min', 'source_ids': r['source_ids'],
                     **price('downtime', r['down_minutes'], rates, gross, SHIFT_MINUTES)})
    if gross:
        running = SHIFT_MINUTES - (k['downtime_pct'] or 0) * SHIFT_MINUTES / 100
        required = ASSUMPTIONS['required_fpm']
        gap_ft = max(0.0, required * running - k['observed_lineal_ft'])
        rows.append({'gap': f'Speed gap to {required:g} ft/min', 'kind': 'speed', 'quantity': round(gap_ft, 1), 'unit': 'lineal ft',
                     'source_ids': k['source_ids'], **price('speed', gap_ft / required, rates, gross, SHIFT_MINUTES)})
        rows.append({'gap': 'Side trim', 'kind': 'trim', 'quantity': round(k['trim_pct'] * gross / 100, 1), 'unit': 'sq ft',
                     'source_ids': k['source_ids'], **price('trim', k['trim_pct'] * gross / 100, rates, gross, SHIFT_MINUTES)})
        rows.append({'gap': 'Shear chops', 'kind': 'waste', 'quantity': round(k['shear_pct'] * gross / 100, 1), 'unit': 'sq ft',
                     'source_ids': k['source_ids'], **price('waste', k['shear_pct'] * gross / 100, rates, gross, SHIFT_MINUTES)})
    for r in rejects['rows']:
        rows.append({'gap': f'Dry-end: {r["reason"]}', 'kind': 'waste', 'quantity': round(r['rejected_sqft'], 1), 'unit': 'sq ft',
                     'source_ids': r['source_ids'], **price('waste', r['rejected_sqft'], rates, gross, SHIFT_MINUTES)})
    data = {'production_day': kpis['production_day'], 'shift': shift, 'dataset_version': kpis['dataset_version'],
            'rows': _rank(rows, 'usd'), 'coverage': kpis['coverage'], 'excluded_records': kpis['excluded_records'],
            'trace': _trace([kpis, stops, rejects]), 'gross_sqft': round(gross or 0, 1), 'scheduled_minutes': SHIFT_MINUTES,
            **_totals(rows), **summary(rows)}
    if gross:
        data.update(trim_over_target(k['trim_pct'], gross, SHIFT_MINUTES, rates))
        if kpis['coverage'] == 'complete':
            data['tile_money'] = shift_tiles(analysis, shift, k, rates)   # the shift's KPI tiles, priced as these rows are
    return _envelope(data, rates)


def day_dollars(analysis, rates):
    """`get_day_dollars`: where the money went across a production day (issue #31b review), the card's all-shifts
    total from the server. Each gap is the same gap of get_shift_dollars summed over the day's complete shifts (an
    incomplete shift is named in `excluded_shifts`, not added) and priced once on the day: the day's gross area and
    480 minutes a complete shift, so its per-year figure is the day's own, not a sum of three shifts' years."""
    parts = [{k: v for k, v in shift_dollars(analysis, shift, rates).items() if k != 'tile_money'} for shift in (1, 2, 3)]
    complete = [d for d in parts if d['coverage'] == 'complete']
    used = complete or parts
    gross_of = lambda d: next((r['price_terms']['gross_sqft'] for r in d['rows'] if r.get('price_terms') and r['price_terms']['gross_sqft']), 0.0)
    gross, minutes = _add(gross_of(d) for d in used), SHIFT_MINUTES * len(used)
    gaps = {}
    for d in used:
        for r in d['rows']:
            g = gaps.setdefault(r['gap'], {'gap': r['gap'], 'kind': r['kind'], 'unit': r['unit'], 'shown': [], 'terms': [], 'shifts': [], 'pricing': None})
            g['shown'].append(r['quantity'])
            g['shifts'].append(d['shift'])
            if r.get('price_terms'):
                g['terms'].append(r['price_terms']['quantity'])
                g['pricing'] = r['price_terms']['kind']
    rows = []
    for g in gaps.values():
        places = 2 if g['unit'] == 'min' else 1
        quantity = _add(g['terms']) if g['terms'] else None
        rows.append({'gap': g['gap'], 'kind': g['kind'], 'quantity': round(_add(g['shown']), places), 'unit': g['unit'], 'shifts': g['shifts'],
                     **price(g['pricing'] or 'downtime', quantity, rates, gross, minutes)})
    trim = _add(r['price_terms']['quantity'] for d in used for r in d['rows'] if r['kind'] == 'trim' and r.get('price_terms'))
    data = {'production_day': parts[0]['production_day'], 'shift': None, 'dataset_version': parts[0]['dataset_version'],
            'rows': _rank(rows, 'usd'), 'coverage': 'complete' if complete else 'partial',
            'excluded_records': [x for d in parts if d['coverage'] != 'complete' for x in d['excluded_records']],
            'excluded_shifts': [d['shift'] for d in parts if d['coverage'] != 'complete'] if complete else [],
            'shifts': [{'shift': d['shift'], 'coverage': d['coverage'], 'total_usd': d['total_usd'], 'total_usd_per_year': d['total_usd_per_year']} for d in parts],
            'trace': {'sql': ';\n'.join(d['trace']['sql'] for d in parts), 'parameters': [d['trace']['parameters'] for d in parts],
                      'elapsed_ms': sum(d['trace']['elapsed_ms'] for d in parts)},
            'gross_sqft': round(gross, 1), 'scheduled_minutes': minutes, **_totals(rows), **summary(rows)}
    if gross:
        data.update(trim_over_target(trim / gross * 100, gross, minutes, rates))
    return _envelope(data, rates)


def opportunities(store, window, shift, rates):
    """`get_opportunities`: where to look for improvement in a window, the levers ranked in $/yr.
    Levers: the speed gap to each cell's best fifth, side-trim points over the trim target, each
    downtime reason (order change is setup time), shear and each dry-end reason. Each names the
    records behind it. It ranks levers and names records; it never prescribes a machine adjustment."""
    waste = store.get_waste_breakdown(window, shift)
    pareto = store.get_downtime_pareto(window, 'reason', 'minutes', shift)
    speed = store.get_speed_opportunity(window, 'width', shift)
    gross = waste['gross_sqft']
    minutes = window_minutes(waste)
    by = {r['category']: r for r in waste['rows']}
    v = rates['values']
    rows = []
    cells = [r for r in speed['rows'] if (r['opportunity_hours'] or 0) > 0]
    rows.append({'lever': 'Speed gap to best-fifth speed', 'kind': 'speed', 'quantity': speed['total_opportunity_hours'], 'unit': 'hours',
                 'records': f'{len(cells)} grade and width cells: ' + ', '.join(c['label'] for c in cells[:5]) + (' and more' if len(cells) > 5 else ''),
                 **price('speed', speed['total_opportunity_hours'] * 60, rates, gross, minutes)})
    trim_pct = by['trim']['pct_of_gross']
    over = max(0.0, (trim_pct or 0) - v['trim_target_pct'])
    over_sqft = over / 100 * gross if gross else 0.0
    rows.append({'lever': f'Side trim over the {v["trim_target_pct"]:g}% target', 'kind': 'trim', 'quantity': round(over, 4), 'unit': 'points',
                 'records': f'side trim {trim_pct}% of {gross:,.1f} sq ft gross' + ('' if over else ', at or under target'),
                 **price('trim', over_sqft, rates, gross, minutes)})
    if rows[-1]['price_terms']:
        rows[-1]['price_terms']['trim_pct'] = trim_pct or 0   # the points over a changed target are re-derived from it (web/money.js)
    for r in pareto['rows']:
        rows.append({'lever': f'Downtime: {r["category"]}' + (' (setup time)' if r['category'] == 'Order change' else ''),
                     'kind': 'setup' if r['category'] == 'Order change' else 'downtime', 'quantity': r['minutes'], 'unit': 'min',
                     'records': f'{r["stops"]} stops recorded as {r["category"]}', **price('downtime', r['minutes'], rates, gross, minutes)})
    for key in ('shear', 'bond', 'warp', 'misalignment', 'moisture'):
        r = by[key]
        if r['sqft']:
            rows.append({'lever': r['label'] + ' (all recorded)', 'kind': 'waste', 'quantity': r['sqft'], 'unit': 'sq ft',
                         'records': f'{r["label"]}: {r["pct_of_gross"]}% of gross', **price('waste', r['sqft'], rates, gross, minutes)})
    coverage = waste['window']['coverage']
    data = {'tool': 'get_opportunities', 'window': waste['window'], 'previous_window': None, 'shift': shift,
            'dataset_version': waste['dataset_version'], 'rows': _rank(rows, 'usd_per_year'),
            'coverage': 'complete' if coverage == 'complete' else coverage, 'excluded_records': [],
            'source_rows': {'waste': waste['source_rows'], 'stops': pareto['source_rows'], 'speed': speed['source_rows']},
            'trace': _trace([waste, pareto, speed]), 'gross_sqft': gross, 'scheduled_minutes': minutes,
            **_totals(rows), **summary(rows),
            'method_note': ('Levers are gaps priced at the rates: the speed gap to each cell\'s best-fifth speed, side-trim points '
                            'over the target, each recorded downtime reason, and all recorded shear and dry-end waste (no benchmark '
                            'for those). Per year at the post\'s volumes. These name where to look and the records behind each '
                            'lever; they do not prescribe a machine adjustment.')}
    return _envelope(data, rates)
