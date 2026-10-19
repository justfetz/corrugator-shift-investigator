#!/usr/bin/env python3
"""Recompute a shift's numbers from the committed CSVs, without the app, and compare.

    python3 tools/verify_day.py 2026-09-14 2

Left column: what the app's tools return (XML -> DuckDB -> parameterized SQL).
Right column: the same metric from data/csv/*.csv with the formula written out here
in plain SQL. If the two columns ever disagree, one of them is wrong and the
dataset is small enough to find out which by hand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from investigator.analysis import Analysis  # noqa: E402

CSV = ROOT / 'data' / 'csv'
SHIFT_SECONDS = 8 * 3600

# Every formula here is the one the README gives in words. Nothing is imported from the app's SQL.
FORMULAS = {
    'observed_shift_fpm':    'sum(feet) / 480',
    'observed_lineal_ft':    'sum(feet)',
    'speed_to_target_pct':   'sum(feet) / sum(target * seconds / 60) * 100',
    'valid_setups':          'count(*)',
    'paper_changes':         'count(distinct wet_end_id)',
    'maintenance_pct':       "(select coalesce(sum(seconds),0) from stop where kind = 'Maintenance') / 28800 * 100",
    'operator_pct':          "(select coalesce(sum(seconds),0) from stop where kind = 'Operator') / 28800 * 100",
    'downtime_pct':          '(select coalesce(sum(seconds),0) from stop) / 28800 * 100',
    'trim_pct':              'sum("trim") / sum(gross) * 100',
    'shear_pct':             'sum(shear) / sum(gross) * 100',
    'dry_end_pct':           'sum(rejects) / sum(gross) * 100',
    'throughput_in':         'sum(gross) / sum(feet) * 12',
}


def from_csv(day, shift):
    db = duckdb.connect()
    # Load the whole CSV, then keep one shift of one day; values are bound, never formatted into SQL.
    db.execute(f"CREATE TABLE all_setups AS SELECT * FROM read_csv_auto('{CSV / 'setups.csv'}')")
    db.execute(f"CREATE TABLE all_stops AS SELECT * FROM read_csv_auto('{CSV / 'stops.csv'}')")
    db.execute('CREATE TABLE setup AS SELECT * FROM all_setups WHERE production_day = ? AND shift = ?', [day, shift])
    db.execute('CREATE TABLE stop AS SELECT * FROM all_stops WHERE production_day = ? AND shift = ?', [day, shift])
    select = ', '.join(f'{formula} AS {name}' for name, formula in FORMULAS.items())
    row = db.execute(f'SELECT {select} FROM setup').fetchone()
    kpis = dict(zip(FORMULAS, row))
    downtime = db.execute('SELECT reason, sum(seconds)/60 FROM stop GROUP BY reason ORDER BY 2 DESC, 1').fetchall()
    wet_end = db.execute('''SELECT wet_end_id, sum(feet)/(sum(seconds)/60) AS actual_fpm, max(target) AS target_fpm
                            FROM setup GROUP BY wet_end_id ORDER BY wet_end_id''').fetchall()
    quality = db.execute('SELECT reject_reason, sum(rejects) FROM setup GROUP BY reject_reason ORDER BY 2 DESC, 1').fetchall()
    db.close()
    return kpis, downtime, wet_end, quality


def from_app(day, shift):
    analysis = Analysis((ROOT / 'data' / f'{day}.xml').read_bytes())
    try:
        kpis = analysis.get_shift_kpis(shift)['rows'][0]
        downtime = [(r['category'], r['down_minutes']) for r in analysis.get_downtime_breakdown(shift)['rows']]
        wet_end = [(r['wet_end_id'], r['actual_fpm'], r['target_fpm']) for r in analysis.get_wet_end_performance(shift)['rows']]
        quality = [(r['reason'], r['rejected_sqft']) for r in analysis.get_quality_breakdown(shift)['rows']]
    finally:
        analysis.close()
    return kpis, downtime, wet_end, quality


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('day', nargs='?', default='2026-09-14')
    parser.add_argument('shift', nargs='?', type=int, default=2, choices=[1, 2, 3])
    args = parser.parse_args(argv)
    app_kpis, app_dt, app_we, app_q = from_app(args.day, args.shift)
    csv_kpis, csv_dt, csv_we, csv_q = from_csv(args.day, args.shift)
    mismatches = 0
    print(f'{args.day} shift {args.shift}: app tool output vs plain SQL over data/csv')
    print(f'{"metric":22}{"app":>14}{"from csv":>14}  formula')
    print('-' * 100)
    for name, formula in FORMULAS.items():
        ok = close(app_kpis[name], csv_kpis[name])
        mismatches += not ok
        print(f'{name:22}{float(app_kpis[name]):>14,.4f}{float(csv_kpis[name]):>14,.4f}  {formula}' + ('' if ok else '   <-- MISMATCH'))
    for label, a, b in (('downtime by reason (min)', app_dt, csv_dt), ('dry-end rejects by reason (sq ft)', app_q, csv_q)):
        same = len(a) == len(b) and all(x[0] == y[0] and close(x[1], y[1]) for x, y in zip(a, b))
        mismatches += not same
        print(f'{label:34}{"match" if same else "MISMATCH":>8}  {len(a)} rows  ' + '; '.join(f'{k}: {v:g}' for k, v in a))
    same = len(app_we) == len(csv_we) and all(x[0] == y[0] and close(x[1], y[1]) and close(x[2], y[2]) for x, y in zip(app_we, csv_we))
    mismatches += not same
    below = sum(x[1] < x[2] for x in app_we)
    print(f'{"wet-end speed vs target":34}{"match" if same else "MISMATCH":>8}  {len(app_we)} runs, {below} below target')
    print('-' * 100)
    print('all numbers agree' if not mismatches else f'{mismatches} disagreement(s)')
    return mismatches


if __name__ == '__main__':
    sys.exit(main())
