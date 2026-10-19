"""Generate an inspectable, deterministic month: one bounded XML file per production day,
a manifest of hashes, and flat CSV extracts of the four tables the SQL runs against.

    python3 -m investigator.archive --output data

The committed data/ directory is exactly this output; the app regenerates the same
bytes from the seed at request time (tests/test_data_archive.py checks the hashes)."""
import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from .fixture import generate
from .analysis import Analysis

TABLES = ('shifts', 'setups', 'stops', 'orders')
ORDER_BY = {'shifts': 'shift', 'setups': 'shift, started, id', 'stops': 'shift, started, id', 'orders': 'shift, record_id, knife'}


def build_archive(output, days=30):
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError('Choose 1-30 days')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    manifest={'synthetic':True,'generator':'v4-quantity-orders','clock':'fixed-demo-local','days':[]}
    for n in range(days):
        day=(date(2026,9,1)+timedelta(days=n)).isoformat()
        data=generate(seed=7+n,production_day=day)
        analysis=Analysis(data)
        try:
            if analysis.errors:raise ValueError(f'Invalid generated day: {day}')
            kpis=[analysis.get_shift_kpis(s)['rows'][0] for s in (1,2,3)]
        finally:analysis.close()
        (output/f'{day}.xml').write_bytes(data)
        _append_csv(output/'csv', data, day, first=(n == 0))
        manifest['days'].append({'production_day':day,'file':f'{day}.xml','seed':7+n,
            'sha256':hashlib.sha256(data).hexdigest(),'shifts':3,
            'setups':sum(row['valid_setups'] for row in kpis),
            'wet_end_runs':sum(row['paper_changes'] for row in kpis)})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def _append_csv(folder, data, day, first):
    """Flat CSVs with a production_day column, one row per table row, deterministic order."""
    folder.mkdir(parents=True, exist_ok=True)
    analysis = Analysis(data)
    try:
        for table in TABLES:
            columns = [r[0] for r in analysis._db.execute(f'DESCRIBE {table}').fetchall()]
            rows = analysis._db.execute(f'SELECT * FROM {table} ORDER BY {ORDER_BY[table]}').fetchall()
            with open(folder/f'{table}.csv', 'w' if first else 'a', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                if first:
                    writer.writerow(['production_day', *columns])
                for row in rows:
                    writer.writerow([day, *[v.isoformat(sep=' ') if hasattr(v, 'isoformat') else v for v in row]])
    finally:
        analysis.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('artifacts/month'))
    parser.add_argument('--days',type=int,default=30)
    args=parser.parse_args()
    manifest=build_archive(args.output,args.days)
    print(f"Validated {len(manifest['days'])} days, {sum(d['shifts'] for d in manifest['days'])} shifts, {sum(d['setups'] for d in manifest['days'])} setups")

if __name__=='__main__':main()
