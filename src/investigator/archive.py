"""Generate an inspectable, deterministic month as separate bounded XML days."""
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from .fixture import generate
from .analysis import Analysis


def build_archive(output, days=30):
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError('Choose 1-30 days')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    manifest={'synthetic':True,'generator':'v2-notes-quality-recurrence','clock':'fixed-demo-local','days':[]}
    for n in range(days):
        day=(date(2026,9,1)+timedelta(days=n)).isoformat()
        data=generate(seed=7+n,production_day=day)
        analysis=Analysis(data)
        try:
            if analysis.errors:raise ValueError(f'Invalid generated day: {day}')
            counts=[analysis.get_shift_kpis(s)['rows'][0]['valid_setups'] for s in (1,2,3)]
        finally:analysis.close()
        (output/f'{day}.xml').write_bytes(data)
        manifest['days'].append({'production_day':day,'file':f'{day}.xml','seed':7+n,
            'sha256':hashlib.sha256(data).hexdigest(),'shifts':3,'setups':sum(counts),'wet_end_runs':90})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('artifacts/month'))
    parser.add_argument('--days',type=int,default=30)
    args=parser.parse_args()
    manifest=build_archive(args.output,args.days)
    print(f"Validated {len(manifest['days'])} days, {sum(d['shifts'] for d in manifest['days'])} shifts, {sum(d['setups'] for d in manifest['days'])} setups")

if __name__=='__main__':main()
