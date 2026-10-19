"""Golden-eval checks, importable by the CLI (evals/run.py), the test suite and the
/evals page. Every check returns a named verdict so a reader sees what passed, not
only what failed.

Four case types, one vocabulary:
  routing       the planner chose the right tools with the right arguments
  faithfulness  every number in the answer came out of a tool row
  grounding     every finding cites evidence that exists; nothing was dropped
  refusal       out-of-scope, out-of-date, out-of-tool requests are refused
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

from .agent import TOOL_SCHEMAS, Agent, Context
from .analysis import Analysis
from .fixture import generate

TOOL_NAMES = {s['name'] for s in TOOL_SCHEMAS}
# Numbers the prose may state that are rules, not measurements (PROCESS_CONTEXT).
RULE_CONSTANTS = {'2.5', '1', '3.25', '0.4', '480', '12', '0'}
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
RUN_ID = re.compile(r'[0-9a-f]{32}:\d+')
CITATION = re.compile(r'^Shift (\d), (\w+), ([0-9a-f]{32}:\d+), row (\d+)$')
TYPES = ('routing', 'faithfulness', 'grounding', 'refusal')

GLOBAL_INVARIANTS = [
    ('offline is free', 'an offline run reports zero model calls and $0'),
    ('tools in allow-list', 'every executed tool is one of the declared tool schemas'),
    ('scope respected', 'no tool ran for a shift outside the selected scope'),
    ('chart rows equal source', 'every chart carries exactly the rows of the result it cites'),
    ('numbers from tool rows', 'every numeric token in the prose equals a tool-row value, a row count or a stated rule constant'),
    ('narrative grounded', 'every narrative citation resolves to (result_id, row, field) in the trace; findings cite something'),
    ('tables complete', 'every table shown has the row count of an independent tool call'),
]


# ---------------------------------------------------------------- where the cases live

def evals_dir():
    """Repository evals/ next to src/, or EVALS_DIR, or ./evals in a container."""
    candidates = [os.environ.get('EVALS_DIR'), Path.cwd() / 'evals', Path(__file__).resolve().parents[2] / 'evals']
    for candidate in candidates:
        if candidate and (Path(candidate) / 'golden.json').exists():
            return Path(candidate)
    return None


def load_cases():
    directory = evals_dir()
    if directory is None:
        return []
    return json.loads((directory / 'golden.json').read_text())['cases']


def fixture_path(case_id):
    directory = evals_dir()
    return None if directory is None else directory / 'fixtures' / f'{case_id}.json'


# ---------------------------------------------------------------- datasets

_DATASETS = {}


def dataset(day, corrupt=False):
    from .server import DAYS
    key = (day, corrupt)
    if key not in _DATASETS:
        _DATASETS[key] = Analysis(generate(seed=7 + DAYS.index(day), production_day=day, corrupt=corrupt))
    return _DATASETS[key]


def close_datasets():
    for analysis in _DATASETS.values():
        analysis.close()
    _DATASETS.clear()


# ---------------------------------------------------------------- faithfulness

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


# ---------------------------------------------------------------- verdicts

def _verdict(name, ok, detail=''):
    return {'check': name, 'ok': bool(ok), 'detail': detail}


def invariant_verdicts(case, result, analysis, allowed_shifts):
    """The checks every case must pass, whatever its category, each with a verdict."""
    out = []
    trace = result['trace']
    if case['mode'] == 'offline':
        used = result['usage']['model_calls'] or result['usage']['cost_usd']
        out.append(_verdict('offline is free', not used, 'reported model usage' if used else '0 model calls, $0'))
    unknown = [s['tool'] for s in trace if s['ok'] and s['tool'] not in TOOL_NAMES]
    out.append(_verdict('tools in allow-list', not unknown, f'executed unknown tool {unknown}' if unknown else f'{len(trace)} calls, all declared'))
    escaped = [f'{s["tool"]} shift {s["arguments"]["shift"]}' for s in trace
               if s['ok'] and 'shift' in s['arguments'] and s['arguments']['shift'] not in allowed_shifts]
    evidence_shifts = {s['data']['shift'] for s in trace if s['ok'] and s['tool'] != 'render_chart'}
    outside = sorted(evidence_shifts - set(allowed_shifts))
    out.append(_verdict('scope respected', not escaped and not outside,
                        f'outside scope {allowed_shifts}: {escaped or outside}' if (escaped or outside) else f'shifts {sorted(evidence_shifts)} within {allowed_shifts}'))
    by_id = {s['result_id']: s for s in trace}
    chart_problems = []
    for chart in result['charts']:
        source = by_id.get(chart['source_result_id'])
        if source is None:
            chart_problems.append('chart cites a result not in the trace')
        elif source['tool'] != 'get_shift_overview' and source['data']['rows'] != chart['rows']:
            chart_problems.append(f'chart rows differ from {source["tool"]} rows')
    out.append(_verdict('chart rows equal source', not chart_problems, '; '.join(chart_problems) or f'{len(result["charts"])} charts match their results'))
    bad = unfaithful_numbers(result['sections'], trace)
    for paragraph in result.get('narrative', []):
        bad += [f'narrative {paragraph["kind"]}: {b}' for b in unfaithful_numbers([paragraph['text']], trace)]
    out.append(_verdict('numbers from tool rows', not bad, '; '.join(bad[:3]) if bad else 'every number traces to a tool row'))
    ground = []
    cited = 0
    for paragraph in result.get('narrative', []):
        for citation in paragraph['citations']:
            m = CITATION.match(citation)
            if not m:
                ground.append(f'malformed citation {citation!r}')
                continue
            shift, field, rid, row = int(m[1]), m[2], m[3], int(m[4])
            source = by_id.get(rid)
            if source is None or not source['ok'] or source['data']['shift'] != shift \
                    or row >= len(source['data']['rows']) or field not in source['data']['rows'][row]:
                ground.append(f'citation does not resolve: {citation}')
            else:
                cited += 1
        if paragraph['kind'] != 'limitation' and not paragraph['citations']:
            ground.append(f'{paragraph["kind"]} paragraph cites nothing')
    if result.get('narrative'):
        out.append(_verdict('narrative grounded', not ground, '; '.join(ground[:3]) if ground else f'{cited} citations resolve'))
    table_problems = []
    for table in result['tables']:
        full = getattr(analysis, table['title'])(table['shift'], **({'metric': table['metric']} if 'metric' in table else {}))
        if len(table['rows']) != len(full['rows']):
            table_problems.append(f'{table["title"]} shows {len(table["rows"])} of {len(full["rows"])} rows')
    if result['tables']:
        out.append(_verdict('tables complete', not table_problems, '; '.join(table_problems) or f'{len(result["tables"])} tables complete'))
    return out


def expectation_verdicts(case, result):
    """The checks the case asks for explicitly, each with a verdict."""
    expect = case.get('expect', {})
    trace = result['trace']
    ok_steps = [s for s in trace if s['ok']]
    out = []
    if 'status' in expect:
        out.append(_verdict(f'status is {expect["status"]}', result['status'] == expect['status'], f'status {result["status"]}'))
    if 'status_in' in expect:
        out.append(_verdict(f'status in {expect["status_in"]}', result['status'] in expect['status_in'], f'status {result["status"]}'))
    if 'shifts' in expect:
        out.append(_verdict(f'selected shifts {expect["shifts"]}', result['selected_shifts'] == expect['shifts'], f'selected {result["selected_shifts"]}'))
    for want in expect.get('tools', []):
        args = ', '.join(f'{k}={v}' for k, v in want.get('arguments', {}).items())
        hit = any(s['tool'] == want['tool'] and all(s['arguments'].get(k) == v for k, v in want.get('arguments', {}).items()) for s in ok_steps)
        out.append(_verdict(f'calls {want["tool"]}({args})', hit, 'called' if hit else 'not called'))
    if 'tools_any' in expect:
        hit = any(s['tool'] in expect['tools_any'] for s in ok_steps)
        out.append(_verdict(f'calls one of {expect["tools_any"]}', hit, 'called' if hit else 'none called'))
    if 'tools_only' in expect:
        extra = sorted({s['tool'] for s in ok_steps} - set(expect['tools_only']))
        out.append(_verdict(f'only {expect["tools_only"]}', not extra, f'unexpected {extra}' if extra else 'no other tools'))
    if 'tool_calls' in expect:
        out.append(_verdict(f'{expect["tool_calls"]} tool calls', len(trace) == expect['tool_calls'], f'{len(trace)} calls'))
    if 'charts' in expect:
        want, n = expect['charts'], len(result['charts'])
        if isinstance(want, int):
            out.append(_verdict(f'{want} charts', n == want, f'{n} charts'))
        else:
            out.append(_verdict(f'at least {want.get("min", 0)} charts', n >= want.get('min', 0), f'{n} charts'))
    if 'tables' in expect:
        titles = sorted(t['title'] for t in result['tables'])
        out.append(_verdict(f'tables {sorted(expect["tables"])}', titles == sorted(expect['tables']), f'tables {titles}'))
    if 'coverage' in expect:
        wrong = [s['tool'] for s in ok_steps if s['tool'] != 'render_chart' and s['data']['coverage'] != expect['coverage']]
        out.append(_verdict(f'coverage {expect["coverage"]}', not wrong, f'not {expect["coverage"]}: {wrong}' if wrong else 'all results'))
    if 'sections_match' in expect:
        hit = any(re.search(expect['sections_match'], s, re.I) for s in result['sections'])
        out.append(_verdict(f'answer matches /{expect["sections_match"]}/', hit, 'matched' if hit else 'no section matches'))
    if 'sections_not_match' in expect:
        hits = [s[:60] for s in result['sections'] if re.search(expect['sections_not_match'], s, re.I)]
        out.append(_verdict(f'answer never matches /{expect["sections_not_match"]}/', not hits, hits[0] if hits else 'absent'))
    prose = [p['text'] for p in result.get('narrative', []) if p['kind'] != 'limitation']
    if 'narrative_not_match' in expect:
        hits = [t[:60] for t in prose if re.search(expect['narrative_not_match'], t, re.I)]
        out.append(_verdict(f'narrative never matches /{expect["narrative_not_match"]}/', not hits, hits[0] if hits else 'absent'))
    if 'narrative_status' in expect:
        out.append(_verdict(f'narrative {expect["narrative_status"]}', result.get('narrative_status') == expect['narrative_status'], f'narrative_status {result.get("narrative_status")}'))
    if 'history_length' in expect:
        n = len(result['context']['history'])
        out.append(_verdict(f'history length {expect["history_length"]}', n == expect['history_length'], f'{n}'))
    if 'max_cost_usd' in expect:
        cost = result['usage'].get('cost_usd', 0)
        out.append(_verdict(f'cost at most ${expect["max_cost_usd"]}', cost <= expect['max_cost_usd'], f'${cost:.4f}'))
    if 'rejected_tool_calls' in expect:
        n = sum(not s['ok'] for s in trace)
        out.append(_verdict(f'at least {expect["rejected_tool_calls"]} rejected calls', n >= expect['rejected_tool_calls'], f'{n} rejected'))
    return out


def describe_expectations(case):
    """The case's expectations in plain words, for a reader."""
    expect = case.get('expect', {})
    words = []
    if 'raises' in expect:
        words.append(f'refused before any tool runs; the refusal mentions "{expect["raises"]}"')
    if 'status' in expect:
        words.append(f'status is {expect["status"]}')
    if 'status_in' in expect:
        words.append('status is one of ' + ', '.join(expect['status_in']))
    if 'shifts' in expect:
        words.append('selected shifts are ' + ', '.join(map(str, expect['shifts'])))
    for want in expect.get('tools', []):
        args = ', '.join(f'{k}={json.dumps(v)}' for k, v in want.get('arguments', {}).items())
        words.append(f'calls {want["tool"]}({args})')
    if 'tools_any' in expect:
        words.append('calls at least one of ' + ', '.join(expect['tools_any']))
    if 'tools_only' in expect:
        words.append('calls nothing outside ' + ', '.join(expect['tools_only']))
    if 'tool_calls' in expect:
        words.append(f'exactly {expect["tool_calls"]} tool calls')
    if 'charts' in expect:
        want = expect['charts']
        words.append(f'{want} chart(s)' if isinstance(want, int) else f'at least {want.get("min", 0)} chart(s)')
    if 'tables' in expect:
        words.append('tables shown: ' + ', '.join(expect['tables']))
    if 'coverage' in expect:
        words.append(f'every result has coverage {expect["coverage"]}')
    if 'sections_match' in expect:
        words.append(f'the answer contains a line matching /{expect["sections_match"]}/')
    if 'sections_not_match' in expect:
        words.append(f'no line of the answer matches /{expect["sections_not_match"]}/')
    if 'narrative_not_match' in expect:
        words.append(f'no finding, hypothesis or check matches /{expect["narrative_not_match"]}/')
    if 'narrative_status' in expect:
        words.append(f'the model narrative is {expect["narrative_status"]}')
    if 'history_length' in expect:
        words.append(f'{expect["history_length"]} turns are carried as history')
    if 'max_cost_usd' in expect:
        words.append(f'cost at most ${expect["max_cost_usd"]}')
    if 'rejected_tool_calls' in expect:
        words.append(f'at least {expect["rejected_tool_calls"]} tool call(s) rejected by the application')
    return words


# ---------------------------------------------------------------- replay

def prompt_digest(payload):
    if 'systemInstruction' in payload:                      # Gemini
        first = payload['systemInstruction']['parts'][0]['text']
    else:                                                   # OpenAI
        first = payload['input'][0]['content'] if payload.get('input') else ''
    return hashlib.sha256(first.encode()).hexdigest()[:12]


def replay_transport(exchanges, warnings):
    """Feed recorded model responses back through the planner's transport hook."""
    pending = list(exchanges)

    def send(payload, key, timeout):
        from .openai_provider import ProviderError
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

def run_case(case, send=None, provider='openai', key=None):
    """Run one case offline, or a live case through `send` (a recorded replay or a real
    transport supplied by the CLI). Returns a result dict with verdicts, never raises."""
    day = case.get('day', '2026-09-14')
    scope = case.get('scope', 'shift')
    shift = case.get('shift', 2)
    analysis = dataset(day, case.get('corrupt', False))
    context = Context(day, shift)
    context.scope = scope
    allowed = [shift] if scope == 'shift' else [1, 2, 3]
    turns = case.get('turns') or [case['question']]
    warnings = set()
    report = {'id': case['id'], 'type': case['type'], 'mode': case['mode'], 'question': turns[-1],
              'outcome': 'skip', 'checks': [], 'note': '', 'answer': None}

    if case['mode'] == 'offline':
        def make_agent():
            return Agent(analysis)
    else:
        if send is None:
            path = fixture_path(case['id'])
            if path is None or not path.exists():
                report['note'] = 'no recording yet; run evals/run.py --live to record'
                return report
            recorded = json.loads(path.read_text())
            provider = recorded.get('provider', 'openai')
            send = replay_transport(recorded['exchanges'], warnings)
            key = 'ya29.replay-fixture-not-a-credential' if provider == 'gemini' else 'sk-replay-fixture-not-a-credential'
            report['note'] = f'replayed {provider} recording'
        from .openai_provider import Budget, OpenAIPlanner
        ledger = Path(tempfile.mkdtemp()) / 'budget.sqlite3'

        def make_agent():
            if provider == 'gemini':
                from .gemini_provider import GeminiPlanner
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
        hit = bool(want and re.search(want, str(exc), re.I))
        report['checks'] = [_verdict(f'refused with "{want}"' if want else 'does not raise', hit, str(exc)[:120])]
        report['outcome'] = 'pass' if hit else 'fail'
        report['note'] = f'refused: {str(exc)[:80]}'
        return report
    if case.get('expect', {}).get('raises'):
        report['checks'] = [_verdict(f'refused with "{case["expect"]["raises"]}"', False, f'did not raise; status {result["status"]}')]
        report['outcome'] = 'fail'
        return report

    report['checks'] = invariant_verdicts(case, result, analysis, allowed) + expectation_verdicts(case, result)
    report['outcome'] = 'fail' if any(not c['ok'] for c in report['checks']) else 'pass'
    report['answer'] = {'status': result['status'], 'sections': result['sections'], 'narrative': result.get('narrative', []),
                        'trace': [{'step': i + 1, 'tool': t['tool'], 'arguments': t['arguments'], 'ok': t['ok'],
                                   'rows': len(t['data'].get('rows', [])) if t['ok'] and isinstance(t.get('data'), dict) else None,
                                   'elapsed_ms': round(t['elapsed_ms'], 1)} for i, t in enumerate(result['trace'])],
                        'charts': len(result['charts']), 'tables': [t['title'] for t in result['tables']],
                        'usage': result['usage']}
    report['result'] = result
    base = (f'{result["status"]}, {result["usage"]["model_calls"]} {provider} model calls' if case['mode'] == 'live'
            else f'{result["status"]}, {len(result["trace"])} tool calls')
    report['note'] = (report['note'] + '; ' if report['note'] else '') + base + ('; ' + '; '.join(sorted(warnings)) if warnings else '')
    return report


def failures(report):
    return [f'{c["check"]}: {c["detail"]}' for c in report['checks'] if not c['ok']]


def score(reports):
    """Totals per type for the score line."""
    totals = {}
    for r in reports:
        t = totals.setdefault(r['type'], {'pass': 0, 'fail': 0, 'skip': 0})
        t[r['outcome']] += 1
    return totals
