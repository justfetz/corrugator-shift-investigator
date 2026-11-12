"""The period report as it was before issue #34: every day rebuilt through `day_analysis`.

Frozen here as the reference the shift_kpis lookup is tested against (tests/test_shift_kpis.py):
every tile, ranking, heatmap cell and recurring-downtime row must agree to 4 decimals. Not
imported by the application.
"""
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
import threading
import time
from uuid import uuid4
from investigator.periods import ACCOUNTING_445_START, weekdays, window
from investigator.record_store import default_store
from investigator.records import stop_label   # how the planned stop reads (owner ruling (c), 2026-11-12)


class LegacyPeriodReports:
    def __init__(self, store=None):
        self.store = store if store is not None else default_store()
        self.first = date.fromisoformat(self.store.first_day)
        self.last = date.fromisoformat(self.store.last_day)
        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()   # the POST path and the URL path (issue #20) share one cache

    def snapshot(self, day, corrupt=False):
        key = (day.isoformat(), corrupt)
        with self.cache_lock:
            if key not in self.cache:
                a = self.store.day_analysis(day.isoformat(), corrupt)
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
        start, end, (old_start, old_end), period_name = window(day, period, self.first, self.last)
        shifts = [shift] if scope == 'shift' else [1,2,3]
        expected = len(weekdays(start, end))          # weekends are plant-down, not missing days
        expected_old = len(weekdays(old_start, old_end))
        current, previous, note_rows, versions = [], [], [], {}
        stops = defaultdict(lambda: {'current_count':0,'previous_count':0,'current_minutes':0.0,'previous_minutes':0.0})
        daily_stops = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'minutes': 0.0}))
        for label, begin, finish, target in [('current',start,end,current),('previous',old_start,old_end,previous)]:
            for d in weekdays(begin, finish):
                if d.isoformat() not in self.store.production_days:
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
            f'Estimated good output {totals["estimated_good_sqft"]:,.0f} sq ft after every recorded waste line (trim, shear, dry-end and changeover rejects, the cutoff-knife and stacker waste boards, planning / admin). Speed {totals["observed_shift_fpm"]:.1f} ft/min; {totals["speed_to_target_pct"]:.1f}% of grade-weighted target. Maintenance {totals["maintenance_pct"]:.2f}%; Operator {totals["operator_pct"]:.2f}%; dry-end waste {totals["dry_end_pct"]:.2f}%.']
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
            sections.append(f'Most recorded downtime: {top["place"]} / {stop_label(top["reason"])}, {top["current_count"]} stops, {top["current_minutes"]:g} min. Repeated symptoms do not establish a root cause.')
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
        daily_trace=self.snapshot(date.fromisoformat(current[0]['day']),corrupt)['shifts'][shifts[0]]['kpis']['trace']
        trace={'result_id':rid,'tool':'get_period_report','arguments':{'day':day,'period':period,'scope':scope,'shift':shift},
               'ok':True,'elapsed_ms':(time.monotonic()-started)*1000,'data':{'range':label,'coverage':'complete' if complete else 'incomplete',
               'rows':[totals],'dataset_versions':versions,'calculation':'Sum raw numerators and denominators from validated daily SQL tools. Fixed 480 minutes per shift. Speed target weighted by elapsed grade time.',
               'daily_sql':daily_trace['sql'],'daily_parameters':daily_trace['parameters']}}   # the per-day KPI query with its bound values (#40)
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
            'limitations':f'Synthetic records. {period_name} uses the displayed dates. Estimated good square feet subtracts every recorded waste line (trim, shear, dry-end and changeover rejects, the cutoff-knife and stacker waste boards, planning / admin) from gross area; it is not a customer-acceptance claim. The demo 4-4-5 year starts {ACCOUNTING_445_START} pending plant confirmation. Notes are observations, not causal proof. Missing periods are not ranked.'}


# The period tools' sums as they were before issue #34: every window re-aggregated from the raw
# setups, shifts, stops and rejects tables. Called with a RecordStore as `self`.
def legacy_sums(self, window, shift, trace):
    """Raw numerators and denominators of one window; KPIs are ratios of these."""
    clause, extra = self._shift_clause(shift)
    params = [window["start"], window["end"], *extra]
    queries = {
        "setups": "SELECT count(*) AS setup_records, count(DISTINCT wet_end_id) AS paper_changes, "
                  "coalesce(sum(lineal_ft),0) AS lineal_ft, coalesce(sum(gross_sqft),0) AS gross_sqft, "
                  "coalesce(sum(trim_sqft),0) AS trim_sqft, coalesce(sum(shear_sqft),0) AS shear_sqft, "
                  "coalesce(sum(reject_sqft),0) AS reject_sqft, coalesce(sum(seconds),0) AS elapsed_seconds, "
                  "coalesce(sum(stop_seconds),0) AS setup_stop_seconds, count(DISTINCT production_date) AS production_days, "
                  "coalesce(sum(changeover_sqft),0) AS changeover_sqft, coalesce(sum(admin_sqft),0) AS admin_sqft, "
                  "coalesce(sum(cutoff_waste_sqft),0) AS cutoff_waste_sqft, coalesce(sum(stacker_waste_sqft),0) AS stacker_waste_sqft "
                  f"FROM setups WHERE production_date BETWEEN ? AND ?{clause}",
        "shifts": "SELECT count(*) AS shift_records, coalesce(sum(scheduled_minutes),0)*60 AS scheduled_seconds, "
                  f"coalesce(sum(bale_sqft),0) AS bale_sqft FROM shifts WHERE production_date BETWEEN ? AND ?{clause}",
        "stops": f"SELECT kind, count(*) AS stops, sum(seconds) AS seconds FROM stops WHERE production_date BETWEEN ? AND ?{clause} GROUP BY kind",
        # record contract v2 (issue #100): the dry-end families of the reject lines, by family and reported reason
        "rejects": f"SELECT family AS reason, sum(sqft) AS sqft FROM rejects WHERE counted_in = 'dry_end' AND production_date BETWEEN ? AND ?{clause} GROUP BY family",
        "details": ("SELECT family AS reason, reason AS detail, sum(sqft) AS sqft, sum(sheets) AS sheets FROM rejects "
                    f"WHERE counted_in = 'dry_end' AND production_date BETWEEN ? AND ?{clause} GROUP BY family, reason ORDER BY 3 DESC, 1, 2"),
    }
    sums = {}
    for name, sql in queries.items():
        rows = self._fetch(sql, params)
        trace["sql"].append(sql)
        trace["parameters"].append(params)
        sums[name] = rows
    setup, shifts = sums["setups"][0], sums["shifts"][0]
    by_kind = {r["kind"]: r for r in sums["stops"]}
    by_reason = {r["reason"]: r["sqft"] for r in sums["rejects"]}
    total = {**setup, **shifts,
             "stop_count": sum(r["stops"] for r in sums["stops"]),
             "stop_seconds": sum(r["seconds"] for r in sums["stops"]),
             "maintenance_pm_seconds": sum(r["seconds"] for k, r in by_kind.items() if k in ("Maintenance", "Scheduled")),
             "operator_seconds": by_kind.get("Operator", {}).get("seconds", 0) or 0,
             "bond_sqft": by_reason.get("BOND", 0.0), "warp_sqft": by_reason.get("WARP", 0.0),
             "misalignment_sqft": by_reason.get("MISALIGNMENT", 0.0), "moisture_sqft": by_reason.get("MOISTURE", 0.0),
             "other_sqft": by_reason.get("OTHER", 0.0),
             "details": sums["details"],
             "source_rows": {"setups": setup["setup_records"], "shifts": shifts["shift_records"],
                             "stops": sum(r["stops"] for r in sums["stops"]), "rejects": len(sums["rejects"])}}
    return total

