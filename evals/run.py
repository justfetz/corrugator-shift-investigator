#!/usr/bin/env python3
"""Golden evaluation for the shift investigator.

Four case types, one score table:

  routing       the planner chose the right tools with the right arguments
  faithfulness  every number in the answer came out of a tool row
  grounding     every finding cites evidence that exists; nothing was dropped
  refusal       out-of-scope, out-of-date, out-of-tool requests are refused

Every case also passes the global invariants in `invariants()`, whatever its
type. Offline cases are deterministic and free. Live cases run the OpenAI
planner; they are replayed from `evals/fixtures/<id>.json` when a recording
exists and skipped otherwise.

    python3 evals/run.py                  # offline + replay recorded live fixtures
    python3 evals/run.py --live           # also call OpenAI (OPENAI_API_KEY) and record fixtures
    python3 evals/run.py --live --provider gemini   # same with Gemini on Vertex AI (GOOGLE_CLOUD_PROJECT
                                          #   + GOOGLE_OAUTH_ACCESS_TOKEN from `gcloud auth print-access-token`)
    python3 evals/run.py --only why-slow  # one case
    python3 evals/run.py --json out.json  # machine-readable results

Exit status is the number of failed cases (0 = all pass; skips do not count).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from investigator.agent import TOOL_SCHEMAS, Agent, Context  # noqa: E402
from investigator.analysis import Analysis  # noqa: E402
from investigator.fixture import generate  # noqa: E402
from investigator.server import DAYS  # noqa: E402

GOLDEN = ROOT / 'evals' / 'golden.json'
FIXTURES = ROOT / 'evals' / 'fixtures'
TOOL_NAMES = {s['name'] for s in TOOL_SCHEMAS}
# Numbers the prose may state that are rules, not measurements (PROCESS_CONTEXT).
RULE_CONSTANTS = {'2.5', '1', '3.25', '0.4', '480', '12', '0'}
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
RUN_ID = re.compile(r'[0-9a-f]{32}:\d+')
CITATION = re.compile(r'^Shift (\d), (\w+), ([0-9a-f]{32}:\d+), row (\d+)$')


# ---------------------------------------------------------------- datasets

_DATASETS = {}


def dataset(day, corrupt=False):
    key = (day, corrupt)
    if key not in _DATASETS:
        _DATASETS[key] = Analysis(generate(seed=7 + DAYS.index(day), production_day=day, corrupt=corrupt))
    return _DATASETS[key]


def close_datasets():
    for analysis in _DATASETS.values():
        analysis.close()
    _DATASETS.clear()


# ---------------------------------------------------------------- checks

def renderings(value):
    """Every way the application formats a numeric field."""
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {str(value), f'{value:,}', f'{value:.1f}', f'{value:.2f}', f'{value:,.1f}', f'{value:,.2f}'}
    if isinstance(value, float) and math.isfinite(value):
        return {f'{value:.0f}', f'{value:.1f}', f'{value:.2f}', f'{value:,.0f}', f'{value:,.1f}',
                f'{value:,.2f}', f'{value:g}'}
    return set()


def evidence_vocabulary(trace):
    """Numbers and strings that tool output makes available to the prose."""
    numbers, strings = set(RULE_CONSTANTS), set()
    for result in trace:
        if not result['ok']:
            continue
        data = result['data']
        row_sets = [data.get('rows', [])] + [c['rows'] for c in data.get('components', [])]
        for rows in row_sets:
            numbers.update(renderings(len(rows)))
            if rows and 'actual_fpm' in rows[0] and 'target_fpm' in rows[0]:
                numbers.update(renderings(sum(r['actual_fpm'] < r['target_fpm'] for r in rows)))
            for row in rows:
                for value in row.values():
                    if isinstance(value, str):
                        strings.add(value)
                    else:
                        numbers.update(renderings(value))
        numbers.update(renderings(len(data.get('excluded_records', []))))
        for record in data.get('excluded_records', []):
            strings.update(str(v) for v in record.values() if isinstance(v, str))
    return numbers, strings


def unfaithful_numbers(lines, trace):
    """Numeric tokens in the prose that no tool row produced."""
    numbers, strings = evidence_vocabulary(trace)
    strings = sorted(strings, key=len, reverse=True)
    bad = []
    for line in lines:
        text = re.sub(r'\s*Evidence: .*$', '', line)
        text = RUN_ID.sub(' ', text)
        text = re.sub(r'\b[Ss]hift [123]\b', ' ', text)
        for s in strings:
            if s:
                text = text.replace(s, ' ')
        for token in NUMBER.findall(text):
            if token not in numbers:
                bad.append(f'{token!r} in {line[:70]!r}')
    return bad


def invariants(case, result, analysis, allowed_shifts):
    """Checks every case must pass, whatever its category."""
    failures = []
    trace = result['trace']
    if case['mode'] == 'offline' and (result['usage']['model_calls'] or result['usage']['cost_usd']):
        failures.append('offline run reported model usage')
    for step in trace:
        if step['ok'] and step['tool'] not in TOOL_NAMES:
            failures.append(f'executed unknown tool {step["tool"]}')
        if step['ok'] and 'shift' in step['arguments'] and step['arguments']['shift'] not in allowed_shifts:
            failures.append(f'{step["tool"]} ran for shift {step["arguments"]["shift"]} outside scope {allowed_shifts}')
    evidence_shifts = {step['data']['shift'] for step in trace if step['ok'] and step['tool'] != 'render_chart'}
    if evidence_shifts - set(allowed_shifts):
        failures.append(f'evidence for shifts {sorted(evidence_shifts - set(allowed_shifts))} outside scope')
    by_id = {step['result_id']: step for step in trace}
    for chart in result['charts']:
        source = by_id.get(chart['source_result_id'])
        if source is None:
            failures.append('chart cites a result that is not in the trace')
            continue
        rows = source['data']['rows'] if source['tool'] != 'get_shift_overview' else None
        if rows is not None and rows != chart['rows']:
            failures.append(f'chart rows differ from {source["tool"]} rows')
    bad = unfaithful_numbers(result['sections'], trace)
    if bad:
        failures.append('numbers not from tool output: ' + '; '.join(bad[:3]))
    for paragraph in result.get('narrative', []):
        bad = unfaithful_numbers([paragraph['text']], trace)
        if bad:
            failures.append(f'narrative {paragraph["kind"]} has numbers not from tool output: ' + '; '.join(bad[:2]))
        for citation in paragraph['citations']:
            m = CITATION.match(citation)
            if not m:
                failures.append(f'malformed citation {citation!r}')
                continue
            shift, field, rid, row = int(m[1]), m[2], m[3], int(m[4])
            source = by_id.get(rid)
            if source is None or not source['ok'] or source['data']['shift'] != shift \
                    or row >= len(source['data']['rows']) or field not in source['data']['rows'][row]:
                failures.append(f'citation does not resolve: {citation}')
        if paragraph['kind'] != 'limitation' and not paragraph['citations']:
            failures.append(f'{paragraph["kind"]} paragraph cites nothing')
    for table in result['tables']:
        shift = table['shift']
        full = getattr(analysis, table['title'])(shift, **({'metric': table['metric']} if 'metric' in table else {}))
        if len(table['rows']) != len(full['rows']):
            failures.append(f'{table["title"]} table shows {len(table["rows"])} of {len(full["rows"])} rows')
    return failures


def expectations(case, result, trace):
    """Checks the case asks for explicitly."""
    expect = case.get('expect', {})
    failures = []
    if 'status' in expect and result['status'] != expect['status']:
        failures.append(f'status {result["status"]!r}, expected {expect["status"]!r}')
    if 'status_in' in expect and result['status'] not in expect['status_in']:
        failures.append(f'status {result["status"]!r} not in {expect["status_in"]}')
    if 'shifts' in expect and result['selected_shifts'] != expect['shifts']:
        failures.append(f'selected shifts {result["selected_shifts"]}, expected {expect["shifts"]}')
    ok_steps = [s for s in trace if s['ok']]
    for want in expect.get('tools', []):
        hit = any(s['tool'] == want['tool'] and all(s['arguments'].get(k) == v for k, v in want.get('arguments', {}).items())
                  for s in ok_steps)
        if not hit:
            failures.append(f'missing tool call {want}')
    if 'tools_any' in expect and not any(s['tool'] in expect['tools_any'] for s in ok_steps):
        failures.append(f'none of {expect["tools_any"]} was called')
    if 'tools_only' in expect:
        extra = {s['tool'] for s in ok_steps} - set(expect['tools_only'])
        if extra:
            failures.append(f'unexpected tools {sorted(extra)}')
    if 'tool_calls' in expect and len(trace) != expect['tool_calls']:
        failures.append(f'{len(trace)} tool calls, expected {expect["tool_calls"]}')
    if 'charts' in expect:
        want = expect['charts']
        n = len(result['charts'])
        if isinstance(want, int) and n != want:
            failures.append(f'{n} charts, expected {want}')
        if isinstance(want, dict) and n < want.get('min', 0):
            failures.append(f'{n} charts, expected at least {want["min"]}')
    if 'tables' in expect:
        titles = sorted(t['title'] for t in result['tables'])
        if titles != sorted(expect['tables']):
            failures.append(f'tables {titles}, expected {sorted(expect["tables"])}')
    if 'coverage' in expect:
        wrong = [s['tool'] for s in ok_steps if s['tool'] != 'render_chart' and s['data']['coverage'] != expect['coverage']]
        if wrong:
            failures.append(f'coverage not {expect["coverage"]} for {wrong}')
    if 'sections_match' in expect and not any(re.search(expect['sections_match'], s, re.I) for s in result['sections']):
        failures.append(f'no section matches /{expect["sections_match"]}/')
    if 'sections_not_match' in expect:
        hits = [s[:60] for s in result['sections'] if re.search(expect['sections_not_match'], s, re.I)]
        if hits:
            failures.append(f'section matches forbidden /{expect["sections_not_match"]}/: {hits[0]!r}')
    prose = [p['text'] for p in result.get('narrative', []) if p['kind'] != 'limitation']
    if 'narrative_not_match' in expect:
        hits = [t[:60] for t in prose if re.search(expect['narrative_not_match'], t, re.I)]
        if hits:
            failures.append(f'narrative matches forbidden /{expect["narrative_not_match"]}/: {hits[0]!r}')
    if 'narrative_status' in expect and result.get('narrative_status') != expect['narrative_status']:
        failures.append(f'narrative_status {result.get("narrative_status")!r}, expected {expect["narrative_status"]!r}')
    if 'history_length' in expect and len(result['context']['history']) != expect['history_length']:
        failures.append(f'history length {len(result["context"]["history"])}, expected {expect["history_length"]}')
    if 'max_cost_usd' in expect and result['usage'].get('cost_usd', 0) > expect['max_cost_usd']:
        failures.append(f'cost ${result["usage"]["cost_usd"]:.4f} above ${expect["max_cost_usd"]}')
    if 'rejected_tool_calls' in expect:
        n = sum(not s['ok'] for s in trace)
        if n < expect['rejected_tool_calls']:
            failures.append(f'{n} rejected tool calls, expected at least {expect["rejected_tool_calls"]}')
    return failures


# ---------------------------------------------------------------- live planner transport

def prompt_digest(payload):
    if 'systemInstruction' in payload:                      # Gemini
        first = payload['systemInstruction']['parts'][0]['text']
    else:                                                   # OpenAI
        first = payload['input'][0]['content'] if payload.get('input') else ''
    return hashlib.sha256(first.encode()).hexdigest()[:12]


def recording_transport(exchanges, provider):
    if provider == 'gemini':
        from investigator import gemini_provider
        project = os.environ['GOOGLE_CLOUD_PROJECT']

        def transport(payload, token, timeout):
            return gemini_provider.transport(payload, token, timeout, project)
    else:
        from investigator.openai_provider import transport

    def send(payload, key, timeout):
        response = transport(payload, key, timeout)
        seen = RUN_ID.search(json.dumps(payload))
        tools = [t['name'] for t in payload['tools']] if 'input' in payload else \
                [d['name'] for d in payload['tools'][0]['functionDeclarations']]
        exchanges.append({'run_id': seen.group(0).split(':')[0] if seen else None,
                          'model': payload.get('model'), 'prompt_digest': prompt_digest(payload),
                          'tools': tools, 'response': response})
        return response
    return send


def replay_transport(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        exchange = pending.pop(0)
        if exchange.get('prompt_digest') != prompt_digest(payload):
            warnings.add('prompt changed since the fixture was recorded; re-record with --live')
        body = json.dumps(exchange['response'])
        current = RUN_ID.search(json.dumps(payload))
        if exchange.get('run_id') and current:
            body = body.replace(exchange['run_id'], current.group(0).split(':')[0])
        return json.loads(body)
    return send


# ---------------------------------------------------------------- running one case

def run_case(case, live, record, provider='openai'):
    """Return (outcome, failures, note) with outcome in pass/fail/skip."""
    day = case.get('day', '2026-09-14')
    scope = case.get('scope', 'shift')
    shift = case.get('shift', 2)
    analysis = dataset(day, case.get('corrupt', False))
    context = Context(day, shift)
    context.scope = scope
    allowed = [shift] if scope == 'shift' else [1, 2, 3]
    turns = case.get('turns') or [case['question']]
    note = ''
    warnings = set()

    if case['mode'] == 'offline':
        def make_agent():
            return Agent(analysis)
    else:
        from investigator.openai_provider import Budget, OpenAIPlanner
        from investigator.gemini_provider import GeminiPlanner
        fixture = FIXTURES / f'{case["id"]}.json'
        if live:
            if provider == 'gemini':
                key = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
                if not key or not os.environ.get('GOOGLE_CLOUD_PROJECT'):
                    return 'skip', [], 'GOOGLE_CLOUD_PROJECT and GOOGLE_OAUTH_ACCESS_TOKEN not set'
            else:
                key = os.environ.get('OPENAI_API_KEY')
                if not key:
                    return 'skip', [], 'OPENAI_API_KEY not set'
            exchanges = []
            send = recording_transport(exchanges, provider)
        elif fixture.exists():
            recorded = json.loads(fixture.read_text())
            provider = recorded.get('provider', 'openai')
            exchanges = recorded['exchanges']
            send = replay_transport(exchanges, warnings)
            key = 'ya29.replay-fixture-not-a-credential' if provider == 'gemini' else 'sk-replay-fixture-not-a-credential'
        else:
            return 'skip', [], 'no fixture; run --live to record'
        ledger = Path(tempfile.mkdtemp()) / 'budget.sqlite3'

        def make_agent():
            if provider == 'gemini':
                planner = GeminiPlanner(os.environ.get('GOOGLE_CLOUD_PROJECT', 'replay-project'), Budget(ledger), send, token=key)
            else:
                planner = OpenAIPlanner(key, Budget(ledger), send)
            return Agent(analysis, planner=planner, max_calls=10, max_seconds=60)

    result = None
    try:
        for text in turns:
            result = make_agent().ask(text, context)
    except ValueError as exc:
        want = case.get('expect', {}).get('raises')
        if want and re.search(want, str(exc), re.I):
            return 'pass', [], f'refused: {str(exc)[:60]}'
        return 'fail', [f'raised {str(exc)[:90]!r}' + (f', expected /{want}/' if want else '')], ''
    if case.get('expect', {}).get('raises'):
        return 'fail', [f'did not raise; status {result["status"]}'], ''

    failures = invariants(case, result, analysis, allowed) + expectations(case, result, result['trace'])
    if case['mode'] == 'live' and live and record:
        FIXTURES.mkdir(exist_ok=True)
        (FIXTURES / f'{case["id"]}.json').write_text(json.dumps(
            {'case': case['id'], 'provider': provider, 'model': result['usage'].get('model'),
             'question': turns[-1], 'recorded_status': result['status'],
             'recorded_cost_usd': result['usage']['cost_usd'], 'exchanges': exchanges}, indent=1))
        note = f'recorded {len(exchanges)} {provider} exchanges, ${result["usage"]["cost_usd"]:.4f}'
    elif case['mode'] == 'live':
        note = f'{result["status"]}, {result["usage"]["model_calls"]} {provider} model calls'
    else:
        note = f'{result["status"]}, {len(result["trace"])} tool calls'
    if warnings:
        note += '; ' + '; '.join(sorted(warnings))
    return ('fail' if failures else 'pass'), failures, note


# ---------------------------------------------------------------- score table

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='run live cases against OpenAI and record fixtures')
    parser.add_argument('--no-record', action='store_true', help='with --live: do not overwrite fixtures')
    parser.add_argument('--provider', choices=['openai', 'gemini'], default='openai', help='with --live: which model to call')
    parser.add_argument('--only', action='append', help='run only this case id (repeatable)')
    parser.add_argument('--json', type=Path, help='write results here')
    args = parser.parse_args(argv)

    cases = json.loads(GOLDEN.read_text())['cases']
    if args.only:
        cases = [c for c in cases if c['id'] in args.only]
    rows = []
    try:
        for case in cases:
            outcome, failures, note = run_case(case, args.live, not args.no_record, args.provider)
            rows.append({'id': case['id'], 'type': case['type'], 'mode': case['mode'],
                         'outcome': outcome, 'failures': failures, 'note': note})
    finally:
        close_datasets()

    width = max(len(r['id']) for r in rows) + 2
    print(f'{"case":{width}}{"type":14}{"mode":9}{"result":8}note')
    print('-' * (width + 31 + 40))
    for r in rows:
        print(f'{r["id"]:{width}}{r["type"]:14}{r["mode"]:9}{r["outcome"]:8}{r["note"]}')
        for failure in r['failures']:
            print(f'{"":{width}}  - {failure}')
    print('-' * (width + 31 + 40))
    totals = Counter()
    for r in rows:
        totals[(r['type'], r['outcome'])] += 1
    summary = []
    for kind in ('routing', 'faithfulness', 'grounding', 'refusal'):
        p, f, s = (totals[(kind, o)] for o in ('pass', 'fail', 'skip'))
        if p + f + s:
            summary.append(f'{kind} {p}/{p + f}' + (f' ({s} skipped)' if s else ''))
    failed = sum(r['outcome'] == 'fail' for r in rows)
    skipped = sum(r['outcome'] == 'skip' for r in rows)
    print('  '.join(summary))
    print(f'{len(rows) - failed - skipped} passed, {failed} failed, {skipped} skipped')
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))
    return failed


if __name__ == '__main__':
    sys.exit(main())
