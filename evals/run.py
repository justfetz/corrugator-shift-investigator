#!/usr/bin/env python3
"""Golden evaluation for the shift investigator: one score table.

The checks live in src/investigator/evals.py, shared with the test suite and the
app's /evals page. This file is the command line and the only place that makes a
live model call.

    python3 evals/run.py                  # offline cases + replay of recorded live fixtures
    python3 evals/run.py --live           # also call OpenAI (OPENAI_API_KEY) and record fixtures
    python3 evals/run.py --live --provider gemini   # same with Gemini on Vertex AI (GOOGLE_CLOUD_PROJECT
                                          #   + GOOGLE_OAUTH_ACCESS_TOKEN from `gcloud auth print-access-token`)
    python3 evals/run.py --only why-slow  # one case
    python3 evals/run.py --json out.json  # machine-readable results

Exit status is the number of failed cases (0 = all pass; skips do not count).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('EVALS_DIR', str(ROOT / 'evals'))

from investigator import evals  # noqa: E402

FIXTURES = ROOT / 'evals' / 'fixtures'


def recording_transport(exchanges, provider):
    """A real transport that also records every exchange for later replay."""
    if provider == 'gemini':
        from investigator import gemini_provider
        project = os.environ['GOOGLE_CLOUD_PROJECT']

        def transport(payload, token, timeout):
            return gemini_provider.transport(payload, token, timeout, project)
    else:
        from investigator.openai_provider import transport

    def send(payload, key, timeout):
        response = transport(payload, key, timeout)
        seen = evals.RUN_ID.search(json.dumps(payload))
        tools = [t['name'] for t in payload['tools']] if 'input' in payload else \
                [d['name'] for d in payload['tools'][0]['functionDeclarations']]
        exchanges.append({'run_id': seen.group(0).split(':')[0] if seen else None,
                          'model': payload.get('model'), 'prompt_digest': evals.prompt_digest(payload),
                          'tools': tools, 'response': response})
        return response
    return send


def run_case(case, live=False, record=True, provider='openai'):
    """CLI wrapper: live recording when asked, otherwise the package's offline/replay path."""
    if case['mode'] != 'live' or not live:
        report = evals.run_case(case)
    else:
        if provider == 'gemini':
            key = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
            if not key or not os.environ.get('GOOGLE_CLOUD_PROJECT'):
                return {'id': case['id'], 'type': case['type'], 'mode': 'live', 'outcome': 'skip', 'checks': [],
                        'note': 'GOOGLE_CLOUD_PROJECT and GOOGLE_OAUTH_ACCESS_TOKEN not set'}
        else:
            key = os.environ.get('OPENAI_API_KEY')
            if not key:
                return {'id': case['id'], 'type': case['type'], 'mode': 'live', 'outcome': 'skip', 'checks': [], 'note': 'OPENAI_API_KEY not set'}
        exchanges = []
        report = evals.run_case(case, send=recording_transport(exchanges, provider), provider=provider, key=key)
        if record and report.get('result') is not None:
            FIXTURES.mkdir(exist_ok=True)
            result = report['result']
            (FIXTURES / f'{case["id"]}.json').write_text(json.dumps(
                {'case': case['id'], 'provider': provider, 'model': result['usage'].get('model'),
                 'question': report['question'], 'recorded_status': result['status'],
                 'recorded_cost_usd': result['usage']['cost_usd'], 'exchanges': exchanges}, indent=1))
            report['note'] = f'recorded {len(exchanges)} {provider} exchanges, ${result["usage"]["cost_usd"]:.4f}'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='run live cases against the model and record fixtures')
    parser.add_argument('--no-record', action='store_true', help='with --live: do not overwrite fixtures')
    parser.add_argument('--provider', choices=['openai', 'gemini'], default='openai', help='with --live: which model to call')
    parser.add_argument('--only', action='append', help='run only this case id (repeatable)')
    parser.add_argument('--json', type=Path, help='write results here')
    args = parser.parse_args(argv)

    cases = evals.load_cases()
    if args.only:
        cases = [c for c in cases if c['id'] in args.only]
    reports = []
    try:
        for case in cases:
            report = run_case(case, args.live, not args.no_record, args.provider)
            report.pop('result', None)
            reports.append(report)
    finally:
        evals.close_datasets()

    width = max(len(r['id']) for r in reports) + 2
    print(f'{"case":{width}}{"type":14}{"mode":9}{"result":8}note')
    print('-' * (width + 31 + 40))
    for r in reports:
        print(f'{r["id"]:{width}}{r["type"]:14}{r["mode"]:9}{r["outcome"]:8}{r["note"]}')
        for failure in evals.failures(r):
            print(f'{"":{width}}  - {failure}')
    print('-' * (width + 31 + 40))
    totals = evals.score(reports)
    summary = []
    for kind in evals.TYPES:
        t = totals.get(kind)
        if t:
            summary.append(f'{kind} {t["pass"]}/{t["pass"] + t["fail"]}' + (f' ({t["skip"]} skipped)' if t['skip'] else ''))
    failed = sum(r['outcome'] == 'fail' for r in reports)
    skipped = sum(r['outcome'] == 'skip' for r in reports)
    print('  '.join(summary))
    print(f'{len(reports) - failed - skipped} passed, {failed} failed, {skipped} skipped')
    if args.json:
        args.json.write_text(json.dumps(reports, indent=1, default=str))
    return failed


if __name__ == '__main__':
    sys.exit(main())
