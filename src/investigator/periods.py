"""Deterministic period reports; only validated synthetic records are read.

Every tile, ranking and heatmap cell is a lookup of the store's precomputed
`shift_kpis` rows (one per production day and shift, validated once when the
store is built; issue #34); the recurring-downtime table and the notes read the
raw stop records. The calendar is the store's Monday-Friday production calendar:
weekends are plant-down, never missing data, and a window is complete when every
weekday in it has three validated shifts.
"""
from collections import defaultdict
from datetime import date, timedelta
import time
from uuid import uuid4
from .record_store import FISCAL_START, default_store, fiscal_bounds, fiscal_period, shift_note

ACCOUNTING_445_START = FISCAL_START  # shared with the period tools: one 4-4-5 calendar in the app


def weekdays(start, end):
    """Production (Mon-Fri) dates from start to end inclusive."""
    return [start+timedelta(days=n) for n in range((end-start).days+1) if (start+timedelta(days=n)).weekday() < 5]


def accounting_window(anchor):
    """Return the containing 4-4-5 period and the immediately prior period: (start, end, (prior start,
    prior end), period number 1-12, weeks). The period tools' calendar, so it is defined for every date."""
    start, end, _, number, weeks = fiscal_period(anchor)
    return start, end, fiscal_period(start - timedelta(days=1))[:2], number, weeks


def window(day, period, first, last):
    anchor = date.fromisoformat(day)
    if not first <= anchor <= last or period not in ('week', 'month', 'accounting'):
        raise ValueError('Choose a supported calendar date and period')
    if period == 'week':
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=4)
        previous = (start-timedelta(days=7), end-timedelta(days=7))
        return start, end, previous, 'Production week (Mon-Fri)'
    if period == 'accounting':
        start, end, previous, number, weeks = accounting_window(anchor)
        return start, end, previous, fiscal_bounds(anchor)[2]   # "4-4-5 P2 (4 weeks)"; a prior year's period says so
    start = anchor.replace(day=1)
    next_month = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
    end = next_month-timedelta(days=1)
    old_end = start-timedelta(days=1)
    old_start = old_end.replace(day=1)
    return start, end, (old_start, old_end), 'Calendar month'


SHIFT_SECONDS = 28800   # the daily KPI tool's fixed eight-hour denominator (analysis.get_shift_kpis)
# One window's shift rows: the precomputed numerators and denominators, nothing recomputed per day.
KPI_SQL = ("SELECT * FROM shift_kpis WHERE corrupt = ? AND production_date BETWEEN ? AND ? "
           "AND list_contains(?, shift) ORDER BY production_date, shift")
# The stop records of both windows, in the order the per-day notes tool lists them.
STOP_SQL = ("SELECT production_date, shift, stop_id, kind, place, reason, seconds, comment AS note, "
            "strftime(started, '%Y-%m-%d %H:%M:%S') AS start, strftime(ended, '%Y-%m-%d %H:%M:%S') AS \"end\" "
            "FROM stops WHERE production_date BETWEEN ? AND ? AND list_contains(?, shift) "
            "ORDER BY production_date, shift, start, stop_id")
STOP_ROW_LIMIT = 5000   # two five-week windows of every shift hold about 2,300 stops


def shift_matrix_row(k):
    """The daily KPI tool's row (analysis.get_shift_kpis) from one shift_kpis row: the same
    ratios of the same sums, so the daily matrix reads as it did when every day was rebuilt."""
    n, feet, gross = k['setups'], k['lineal_ft'], k['gross_sqft']
    def ratio(a, b, scale=1):
        return a/b*scale if n and b else None
    return {'valid_setups': n, 'paper_changes': k['paper_changes'],
            'speed_to_target_pct': ratio(feet, k['target_ft'], 100),
            'observed_lineal_ft': feet if n else None, 'observed_shift_fpm': feet/480 if n else None,
            'gross_sqft': gross if n else None,
            'estimated_good_sqft': gross-k['trim_sqft']-k['shear_sqft']-k['reject_sqft']-k['cutoff_waste_sqft']-k['stacker_waste_sqft'] if n else None,
            'throughput_in': ratio(gross, feet, 12),
            'trim_pct': ratio(k['trim_sqft'], gross, 100), 'shear_pct': ratio(k['shear_sqft'], gross, 100),
            'dry_end_pct': ratio(k['reject_sqft'], gross, 100),
            'maintenance_pct': k['maintenance_seconds']/SHIFT_SECONDS*100,
            'operator_pct': k['operator_seconds']/SHIFT_SECONDS*100,
            'pm_pct': k['pm_seconds']/SHIFT_SECONDS*100,
            'downtime_pct': k['stop_seconds']/SHIFT_SECONDS*100,
            'lineal_per_setup': ratio(feet, n), 'lineal_per_wet_end': ratio(feet, k['paper_changes']),
            'setups_above_trim_target': k['setups_above_trim_target'], 'source_ids': list(k['source_ids'])}


class PeriodReports:
    def __init__(self, store=None):
        self.store = store if store is not None else default_store()
        self.first = date.fromisoformat(self.store.first_day)
        self.last = date.fromisoformat(self.store.last_day)

    def investigate(self, day, shift, scope, period, corrupt=False):
        started = time.monotonic()
        if type(corrupt) is not bool:
            raise ValueError('Invalid data-quality option')
        start, end, (old_start, old_end), period_name = window(day, period, self.first, self.last)
        shifts = [shift] if scope == 'shift' else [1,2,3]
        if any(type(s) is not int or s not in (1, 2, 3) for s in shifts):
            raise ValueError('Shift must be 1, 2 or 3')
        expected = len(weekdays(start, end))          # weekends are plant-down, not missing days
        expected_old = len(weekdays(old_start, old_end))
        current, previous, note_rows, versions = [], [], [], {}
        stops = defaultdict(lambda: {'current_count':0,'previous_count':0,'current_minutes':0.0,'previous_minutes':0.0})
        daily_stops = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'minutes': 0.0}))
        kpi_parameters = []
        for label, begin, finish, target in [('current',start,end,current),('previous',old_start,old_end,previous)]:
            params = [corrupt, begin.isoformat(), finish.isoformat(), shifts]
            kpi_parameters.append(params)
            for k in self.store._fetch(KPI_SQL, params):
                d = k['production_date'].isoformat()
                versions[d] = self.store.day_version(d, corrupt)
                target.append(dict(shift_matrix_row(k), day=d, shift=k['shift'], coverage=k['coverage']))
                if label == 'current':
                    note_rows.append({'record_type': 'Shift', 'record_id': f"Shift {k['shift']}", 'start': '', 'end': '',
                                      'kind': '', 'place': '', 'reason': '', 'down_minutes': None, 'notes': shift_note(k['crew']),
                                      'source_ids': [f"shift-{k['shift']}"], 'day': d, 'shift': k['shift']})
        stop_parameters = [old_start.isoformat(), end.isoformat(), shifts]
        shift_notes = {(n['day'], n['shift']): i for i, n in enumerate(note_rows)}
        downtime_notes = defaultdict(list)
        for st in self.store._fetch(STOP_SQL, stop_parameters, limit=STOP_ROW_LIMIT):
            moment = st['production_date']
            label = 'current' if start <= moment <= end else 'previous' if old_start <= moment <= old_end else None
            if label is None:
                continue
            d, minutes, key = moment.isoformat(), st['seconds']/60, (st['kind'], st['place'], st['reason'])
            item = stops[key]
            item[label+'_count'] += 1
            item[label+'_minutes'] += minutes
            if label == 'current':
                day_item = daily_stops[d][key]
                day_item['count'] += 1
                day_item['minutes'] += minutes
                downtime_notes[(d, st['shift'])].append({'record_type': 'Downtime', 'record_id': st['stop_id'],
                    'start': st['start'], 'end': st['end'], 'kind': st['kind'], 'place': st['place'], 'reason': st['reason'],
                    'down_minutes': minutes, 'notes': st['note'], 'source_ids': [st['stop_id']], 'day': d, 'shift': st['shift']})
        # Each shift's note first, then its stops by start: the per-day notes tool's order.
        note_rows = [row for note in note_rows for row in (note, *downtime_notes[(note['day'], note['shift'])])]
        if not current:
            raise ValueError(f'No production days between {start} and {end}: the dataset covers {self.first} to {self.last}')
        complete = len(current)==expected*len(shifts) and all(r['coverage']=='complete' for r in current)
        old_complete = len(previous)==expected_old*len(shifts) and all(r['coverage']=='complete' for r in previous)
        def aggregate(rows):
            if not rows: return None
            gross=sum(r['gross_sqft'] for r in rows)
            feet=sum(r['observed_lineal_ft'] for r in rows)
            target_feet=sum(r['observed_lineal_ft']/(r['speed_to_target_pct']/100) for r in rows)
            return {'observed_shift_fpm':feet/(480*len(rows)), 'observed_lineal_ft':feet,
                'gross_sqft':gross, 'estimated_good_sqft':sum(r['estimated_good_sqft'] for r in rows),
                'speed_to_target_pct':feet/target_feet*100, 'valid_setups':sum(r['valid_setups'] for r in rows),
                'paper_changes':sum(r['paper_changes'] for r in rows),
                'maintenance_pct':sum(r['maintenance_pct'] for r in rows)/len(rows),
                'operator_pct':sum(r['operator_pct'] for r in rows)/len(rows),
                'downtime_pct':sum(r['downtime_pct'] for r in rows)/len(rows),
                **{k:sum(r[k]*r['gross_sqft'] for r in rows)/gross for k in ('dry_end_pct','trim_pct','shear_pct')},
                'throughput_in':gross/feet*12,
                'lineal_per_setup':feet/sum(r['valid_setups'] for r in rows)}
        totals=aggregate(current); old=aggregate(previous)
        label=f'{start.isoformat()} to {end.isoformat()}'
        rid=uuid4().hex
        sections=[f'Synthetic {period_name} report: {label}. '+('Selected shift '+str(shift)+'.' if scope=='shift' else 'All three shifts.'),
            f'{len(current)} available shift records. {totals["valid_setups"]} valid dry-end setups; {totals["paper_changes"]} paper changes (wet-end runs).',
            f'Estimated good output {round(totals["estimated_good_sqft"], 1):,.0f} sq ft after recorded trim, shear, dry-end rejects and waste boards. Speed {totals["observed_shift_fpm"]:.1f} ft/min; {totals["speed_to_target_pct"]:.1f}% of grade-weighted target. Maintenance {totals["maintenance_pct"]:.2f}%; Operator {totals["operator_pct"]:.2f}%; dry-end waste {totals["dry_end_pct"]:.2f}%.']
        if not complete: sections.append('Current period is incomplete: missing days or excluded records. Observed totals only; rankings withheld.')
        if complete and old_complete:
            sections.append(f'Previous period {old_start} to {old_end}: speed change {totals["observed_shift_fpm"]-old["observed_shift_fpm"]:+.1f} ft/min; maintenance change {totals["maintenance_pct"]-old["maintenance_pct"]:+.2f} percentage points.')
        else: sections.append(f'Full comparison unavailable for {old_start} to {old_end}; missing/invalid records are not zero production.')
        recurring=[]
        for (kind,place,reason), counts in sorted(stops.items(),key=lambda kv:(-kv[1]['current_minutes'],kv[0])):
            recurring.append({'kind':kind,'place':place,'reason':reason,**counts,
                'minutes_change':counts['current_minutes']-counts['previous_minutes'] if complete and old_complete else None})
        if not old_complete:
            for row in recurring:
                row['previous_count'] = None
                row['previous_minutes'] = None
        if recurring and complete:
            top=recurring[0]
            sections.append(f'Most recorded downtime: {top["place"]} / {top["reason"]}, {top["current_count"]} stops, {top["current_minutes"]:g} min. Repeated symptoms do not establish a root cause.')
        rankings=[]
        if complete:
            for s in shifts:
                rankings.append(dict(shift=s, **aggregate([r for r in current if r['shift']==s])))
            for metric,field,reverse in [('speed','speed_to_target_pct',True),('maintenance','maintenance_pct',False),('waste','dry_end_pct',False)]:
                values=sorted({r[field] for r in rankings},reverse=reverse)
                for r in rankings:r[metric+'_rank']=values.index(r[field])+1
        tables=[{'title':'Recurring downtime: current and previous period','rows':recurring,'coverage':'complete' if complete else 'incomplete','source_result_id':rid},
                {'title':'Shift rankings: 1 = best in this period; no combined score','rows':rankings,'coverage':'complete' if complete else 'incomplete','source_result_id':rid},
                {'title':'Daily shift matrix','rows':current,'coverage':'complete' if complete else 'incomplete','source_result_id':rid},
                {'title':'Reported shift and downtime notes (synthetic, unverified)','rows':note_rows,'coverage':'complete' if complete else 'incomplete','source_result_id':rid}]
        trace={'result_id':rid,'tool':'get_period_report','arguments':{'day':day,'period':period,'scope':scope,'shift':shift},
               'ok':True,'elapsed_ms':(time.monotonic()-started)*1000,'data':{'range':label,'coverage':'complete' if complete else 'incomplete',
               'rows':[totals],'dataset_versions':versions,'calculation':'Sum raw numerators and denominators of the precomputed shift_kpis rows (one per validated production day and shift). Fixed 480 minutes per shift. Speed target weighted by elapsed grade time.',
               'trace':{'sql':';\n'.join([KPI_SQL, KPI_SQL, STOP_SQL]),'parameters':[*kpi_parameters, stop_parameters]}}}   # the lookups with their bound values (#40)
        heatmap_rows=[]
        for n in range((end-start).days+1):
            moment = start+timedelta(days=n)
            value_day = moment.isoformat()
            rows = [r for r in current if r['day']==value_day]
            if not rows:
                # Weekends are plant-down by calendar; other gaps are missing records.
                heatmap_rows.append({'day': value_day, 'available': False, 'plant_down': moment.weekday() >= 5})
                continue
            summary = aggregate(rows)
            hitters = daily_stops[value_day]
            top_key, top_value = max(hitters.items(), key=lambda item: item[1]['minutes']) if hitters else ((None,None,None), {'count':0,'minutes':0})
            heatmap_rows.append({'day':value_day, 'available':all(r['coverage']=='complete' for r in rows),
                'estimated_good_sqft':summary['estimated_good_sqft'], 'gross_sqft':summary['gross_sqft'],
                'speed_to_target_pct':summary['speed_to_target_pct'], 'downtime_pct':summary['downtime_pct'],
                'maintenance_pct':summary['maintenance_pct'], 'operator_pct':summary['operator_pct'],
                'dry_end_pct':summary['dry_end_pct'], 'trim_pct':summary['trim_pct'], 'shear_pct':summary['shear_pct'],
                'paper_changes':summary['paper_changes'], 'valid_setups':summary['valid_setups'],
                'heavy_hitter':f'{top_key[1]} / {top_key[2]}' if top_key[1] else 'No recorded downtime',
                'heavy_hitter_minutes':top_value['minutes'], 'heavy_hitter_stops':top_value['count']})
        heatmap_scope = f'Shift {shift} only (8 hours per date)' if scope == 'shift' else 'All three shifts (24 hours per date)'
        charts=[{'title':f'{period_name}: estimated good square feet by production date — {heatmap_scope}',
            'type':'calendar_heatmap','unit':'estimated good sq ft','x':'day','y':'estimated_good_sqft',
            'rows':heatmap_rows,'coverage':'complete' if complete else 'incomplete','source_result_id':rid}]
        for s in shifts:
            rows=[{'day':r['day'],'actual_fpm':r['observed_shift_fpm'],'target_fpm':r['observed_shift_fpm']/(r['speed_to_target_pct']/100),'speed_to_target_pct':r['speed_to_target_pct']} for r in current if r['shift']==s]
            charts.append({'title':f'Shift {s}: daily speed / weighted target', 'type':'bar','shift':s,'unit':'ft/min','x':'day','y':'actual_fpm','rows':rows,'coverage':'complete' if complete else 'incomplete','source_result_id':rid})
        return {'run_id':rid,'mode':'offline deterministic period report; no model calls','status':'complete' if complete else 'partial',
            'production_day':day,'period_label':label,'selected_shifts':shifts,'sections':sections,'tables':tables,'charts':charts,'trace':[trace],
            'evidence_ids':[rid],'context':{'production_day':day,'shift':shift,'scope':scope,'history':[]},
            'usage':{'tool_calls':1,'model_calls':0,'cost_usd':0},
            'limitations':f'Synthetic records. {period_name} uses the displayed dates. Estimated good square feet subtracts recorded trim, shear, dry-end rejects and waste boards from gross area; it is not a customer-acceptance claim. The demo 4-4-5 year starts {ACCOUNTING_445_START} pending plant confirmation. Notes are observations, not causal proof. Missing periods are not ranked.'}
