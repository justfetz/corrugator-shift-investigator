"""Deterministic period reports; only validated synthetic daily analysis is read."""
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
import hashlib
import json
import time
from uuid import uuid4
from .analysis import Analysis
from .fixture import generate

FIRST = date(2026, 9, 1)
LAST = date(2026, 9, 30)
ACCOUNTING_445_START = date(2026, 9, 1)  # Editable demo assumption.
ACCOUNTING_WEEKS = (4, 4, 5) * 4


def accounting_window(anchor):
    """Return the containing 4-4-5 period and the immediately prior period."""
    offset = (anchor-ACCOUNTING_445_START).days
    if offset < 0:
        raise ValueError('Date precedes the configured demo accounting year')
    elapsed = 0
    for index, weeks in enumerate(ACCOUNTING_WEEKS):
        days = weeks*7
        if offset < elapsed+days:
            start = ACCOUNTING_445_START+timedelta(days=elapsed)
            end = start+timedelta(days=days-1)
            prior_days = ACCOUNTING_WEEKS[index-1]*7 if index else ACCOUNTING_WEEKS[-1]*7
            old_end = start-timedelta(days=1)
            return start, end, (old_end-timedelta(days=prior_days-1), old_end), index+1, weeks
        elapsed += days
    raise ValueError('Date is outside the configured demo accounting year')


def window(day, period):
    anchor = date.fromisoformat(day)
    if not FIRST <= anchor <= LAST or period not in ('week', 'month', 'accounting'):
        raise ValueError('Choose a supported calendar date and period')
    if period == 'week':
        start, end = anchor - timedelta(days=6), anchor
        previous = (start-timedelta(days=7), start-timedelta(days=1))
        return start, end, previous, 'Trailing 7 days'
    if period == 'accounting':
        start, end, previous, number, weeks = accounting_window(anchor)
        return start, end, previous, f'4-4-5 P{number} ({weeks} weeks)'
    start = anchor.replace(day=1)
    next_month = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
    end = next_month-timedelta(days=1)
    old_end = start-timedelta(days=1)
    old_start = old_end.replace(day=1)
    return start, end, (old_start, old_end), 'Calendar month'


class PeriodReports:
    def __init__(self):
        self.cache = OrderedDict()

    def snapshot(self, day, corrupt=False):
        key = (day.isoformat(), corrupt)
        if key not in self.cache:
            a = Analysis(generate(seed=7+(day-FIRST).days, production_day=day.isoformat(), corrupt=corrupt))
            try:
                self.cache[key] = {'version': a.version, 'shifts': {
                    s: {'kpis': a.get_shift_kpis(s), 'notes': a.get_shift_notes(s)} for s in (1,2,3)}}
            finally:
                a.close()
            if len(self.cache)>60:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def investigate(self, day, shift, scope, period, corrupt=False):
        started = time.monotonic()
        start, end, (old_start, old_end), period_name = window(day, period)
        shifts = [shift] if scope == 'shift' else [1,2,3]
        expected = (end-start).days+1
        expected_old = (old_end-old_start).days+1
        current, previous, note_rows, versions = [], [], [], {}
        stops = defaultdict(lambda: {'current_count':0,'previous_count':0,'current_minutes':0.0,'previous_minutes':0.0})
        daily_stops = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'minutes': 0.0}))
        for label, begin, finish, target in [('current',start,end,current),('previous',old_start,old_end,previous)]:
            for n in range((finish-begin).days+1):
                d = begin+timedelta(days=n)
                if not FIRST <= d <= LAST:
                    continue
                snapshot = self.snapshot(d, corrupt)
                versions[d.isoformat()] = snapshot['version']
                for s in shifts:
                    data = snapshot['shifts'][s]
                    row = dict(data['kpis']['rows'][0], day=d.isoformat(), shift=s, coverage=data['kpis']['coverage'])
                    target.append(row)
                    for note in data['notes']['rows']:
                        if note['record_type']=='Downtime':
                            item=stops[(note['kind'],note['place'],note['reason'])]
                            item[label+'_count']+=1
                            item[label+'_minutes']+=note['down_minutes']
                            if label == 'current':
                                day_item = daily_stops[d.isoformat()][(note['kind'],note['place'],note['reason'])]
                                day_item['count'] += 1
                                day_item['minutes'] += note['down_minutes']
                        if label=='current':
                            note_rows.append(dict(note, day=d.isoformat(), shift=s))
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
            f'Estimated good output {totals["estimated_good_sqft"]:,.0f} sq ft after recorded trim, shear and dry-end rejects. Speed {totals["observed_shift_fpm"]:.1f} ft/min; {totals["speed_to_target_pct"]:.1f}% of grade-weighted target. Maintenance {totals["maintenance_pct"]:.2f}%; Operator {totals["operator_pct"]:.2f}%; dry-end waste {totals["dry_end_pct"]:.2f}%.']
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
        tables=[{'title':'Recurring downtime: current and previous period','rows':recurring,'coverage':'complete' if complete else 'incomplete'},
                {'title':'Shift rankings: 1 = best in this period; no combined score','rows':rankings,'coverage':'complete' if complete else 'incomplete'},
                {'title':'Daily shift matrix','rows':current,'coverage':'complete' if complete else 'incomplete'},
                {'title':'Reported shift and downtime notes (synthetic, unverified)','rows':note_rows,'coverage':'complete' if complete else 'incomplete'}]
        trace={'result_id':rid,'tool':'get_period_report','arguments':{'day':day,'period':period,'scope':scope,'shift':shift},
               'ok':True,'elapsed_ms':(time.monotonic()-started)*1000,'data':{'range':label,'coverage':'complete' if complete else 'incomplete',
               'rows':[totals],'dataset_versions':versions,'calculation':'Sum raw numerators and denominators from validated daily SQL tools. Fixed 480 minutes per shift. Speed target weighted by elapsed grade time.',
               'daily_sql':self.snapshot(max(start,FIRST),corrupt)['shifts'][shifts[0]]['kpis']['trace']['sql']}}
        heatmap_rows=[]
        for n in range(expected):
            value_day = (start+timedelta(days=n)).isoformat()
            rows = [r for r in current if r['day']==value_day]
            if not rows:
                heatmap_rows.append({'day': value_day, 'available': False})
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
            'limitations':f'Synthetic records. {period_name} uses the displayed dates. Estimated good square feet subtracts recorded trim, shear and dry-end rejects from gross area; it is not a customer-acceptance claim. The demo 4-4-5 year starts {ACCOUNTING_445_START} pending plant confirmation. Notes are observations, not causal proof. Missing periods are not ranked.'}
