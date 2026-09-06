"""Run deterministic tools and inspect evidence without an LLM."""
import argparse
import json
from pathlib import Path
from .analysis import Analysis
from .fixture import generate
from .chart import downtime_svg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data = generate()
    (args.output / 'production.xml').write_bytes(data)
    analysis = Analysis(data)
    try:
        report = {'synthetic': True, 'status': 'deterministic tools; model not connected',
                  'shifts': [{'kpis': analysis.get_shift_kpis(s),
                              'downtime': analysis.get_downtime_breakdown(s)} for s in (1,2,3)]}
        (args.output / 'tool-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        for result in report['shifts']:
            kpi = result['kpis']
            (args.output / f"shift-{kpi['shift']}-downtime.svg").write_text(downtime_svg(result['downtime']), encoding='utf-8')
            print(f"Shift {kpi['shift']}: {kpi['rows'][0]['observed_shift_fpm']:.1f} ft/min; {kpi['coverage']}")
        print(f'XML, tool evidence and SVG charts written to {args.output}')
    finally:
        analysis.close()

if __name__ == '__main__':
    main()
