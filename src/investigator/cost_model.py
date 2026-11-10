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
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f'Rate {key} must be a number, for example {key}:{RATE_SPECS[rid]["default"] or 40}') from None
        limit = 100 if rid == 'trim_target_pct' else MAX_RATE
        if not math.isfinite(number) or not 0 < number <= limit:
            raise ValueError(f'Rate {key} must be above 0 and at most {limit:g} {RATE_SPECS[rid]["unit"]}')
        out[rid] = number
    return out


def format_rates(overrides):
    """The canonical spelling of an override set: the rates.json order, `%g` numbers."""
    return ','.join(f'{RATE_SPECS[rid]["short"]}:{overrides[rid]:g}' for rid in RATE_SPECS if rid in (overrides or {}))


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
    margin = (f'margin ${v["margin_usd_msf"]:g}/MSF ({s["margin_usd_msf"]})' if v['margin_usd_msf'] is not None
              else f'contribution {MARGIN_NOT_SET} (assumption: {s["margin_usd_msf"]})')
    return (f'Rates: paper ${v["paper_usd_msf"]:g}/MSF ({s["paper_usd_msf"]}), machine time ${v["machine_usd_hour"]:,g}/hr '
            f'({s["machine_usd_hour"]}), {margin}, trim target {v["trim_target_pct"]:g}% ({s["trim_target_pct"]}).')


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
                'contribution_status': 'not priced', 'usd_basis': 'not priced: the quantity is unavailable or a negative remainder'}
    parts = []
    if kind in ('trim', 'waste', 'paper'):
        msf = quantity / 1000
        paper_usd = msf * paper
        machine_minutes = msf / MSF_PER_MINUTE if kind == 'waste' else 0.0
        not_made = msf if kind == 'waste' else 0.0
        parts.append(f'{quantity:,.1f} sq ft = {msf:,.3f} MSF x ${paper:g}/MSF paper = {_money(paper_usd)}')
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
        parts.append(f'{lead}{machine_minutes:,.2f} min x ${hour:,g}/hr machine time = {_money(machine_usd)}')
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
        parts.append(f'contribution {not_made:,.3f} MSF not made x ${margin:g}/MSF = {_money(contribution)} (not added)')
    r2 = lambda x: None if x is None else round(x, 2)
    return {'msf': None if msf is None else round(msf, 3), 'paper_usd': r2(paper_usd),
            'machine_minutes': round(machine_minutes, 2), 'machine_time_usd': r2(machine_usd), 'usd': r2(usd),
            'usd_per_year': None if usd_year is None else round(usd_year, 0),
            'msf_not_made': round(not_made, 3), 'contribution_usd': r2(contribution),
            'contribution_usd_per_year': None if contribution_year is None else round(contribution_year, 0),
            'contribution_status': status, 'usd_basis': '; '.join(parts)}


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
                'get_downtime_pareto', 'get_speed_opportunity')


def window_minutes(data):
    """Scheduled minutes of a period tool's window: production days observed x 480 per shift."""
    days = data['window']['production_days_observed']
    return days * (SHIFT_MINUTES if data.get('shift') else 3 * SHIFT_MINUTES) or None


def attach(tool, data, rates, gross_sqft=None):
    """Put the dollar fields beside the quantity on every row of a priced tool's result.
    `gross_sqft` is the shift's gross area for the daily quality breakdown (its annual basis)."""
    if tool not in PRICED_TOOLS:
        return data
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


# ------------------------------------------------------------------ the two money tools

def _trace(parts, started_ms=0.0):
    return {'sql': ';\n'.join(p['trace']['sql'] if isinstance(p['trace']['sql'], str) else ';\n'.join(p['trace']['sql']) for p in parts),
            'parameters': [p['trace']['parameters'] for p in parts],
            'elapsed_ms': sum(p['trace']['elapsed_ms'] for p in parts)}


def _totals(rows):
    def total(key):
        values = [r[key] for r in rows if r[key] is not None]
        return round(sum(values), 2) if values else 0.0
    out = {'total_usd': total('usd'), 'total_paper_usd': total('paper_usd'), 'total_machine_time_usd': total('machine_time_usd')}
    years = [r['usd_per_year'] for r in rows]
    out['total_usd_per_year'] = round(sum(years), 0) if rows and None not in years else None
    margin_set = all(r['contribution_status'] != MARGIN_NOT_SET for r in rows)
    out['total_contribution_usd'] = total('contribution_usd') if margin_set else None
    out['contribution_status'] = 'set' if margin_set else MARGIN_NOT_SET
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
            **_totals(rows)}
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
            **_totals(rows),
            'method_note': ('Levers are gaps priced at the rates: the speed gap to each cell\'s best-fifth speed, side-trim points '
                            'over the target, each recorded downtime reason, and all recorded shear and dry-end waste (no benchmark '
                            'for those). Per year at the post\'s volumes. These name where to look and the records behind each '
                            'lever; they do not prescribe a machine adjustment.')}
    return _envelope(data, rates)
