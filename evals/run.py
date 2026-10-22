#!/usr/bin/env python3
"""Golden evaluation for the shift investigator.

Seven case types, one score table:

  routing        the planner chose the right tools with the right arguments
  faithfulness   every number in the answer came out of a tool row
  grounding      every finding cites evidence that exists; nothing was dropped
  refusal        out-of-scope, out-of-date, out-of-tool requests are refused
  value          a tool's numbers equal a second, plain computation over the records
                 (generated into golden_values.json by make_value_goldens.py)
  investigation  a multi-turn question finds the seeded story in the evidence,
                 reports its numbers, and asserts no cause the evidence lacks
  data_quality   a corrupt record is flagged, excluded, reported, never averaged in

Every case also passes the global invariants in `invariants()`, whatever its
type. Offline cases are deterministic and free. Cases marked `live: true` (and
the `mode: live` cases) also run against a live planner: OpenAI or Anthropic,
chosen by `--provider` or by whichever key is set. Live runs are recorded to
`evals/fixtures/<provider>/<id>.json` and replayed afterwards; a live type passes
by pass rate (`THRESHOLDS`), an offline case passes or fails on its own.

    python3 evals/run.py                              # offline + replay of recorded fixtures
    python3 evals/run.py --live                       # also call the one provider whose key is set
    python3 evals/run.py --live --provider anthropic  # ANTHROPIC_API_KEY; or openai with OPENAI_API_KEY
    python3 evals/run.py --only why-slow              # one case
    python3 evals/run.py --json out.json              # machine-readable results

Exit status is the number of failed offline cases plus the number of live
(provider, type) pairs under their pass-rate threshold. Skips and known
defects do not count.
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

from investigator.agent import PERIOD_TOOLS, TOOL_SCHEMAS, Agent, Context  # noqa: E402
from investigator.record_store import default_store  # noqa: E402

GOLDEN = ROOT / 'evals' / 'golden.json'
VALUES = ROOT / 'evals' / 'golden_values.json'
FIXTURES = ROOT / 'evals' / 'fixtures'
DEFAULT_DAY = '2026-09-14'
TYPES = ('routing', 'faithfulness', 'grounding', 'refusal', 'value', 'investigation', 'data_quality')
# Pass rate a live provider must reach per type; offline cases pass or fail one by one.
THRESHOLDS = {'routing': 0.9, 'faithfulness': 1.0, 'grounding': 1.0, 'refusal': 1.0,
              'investigation': 0.8, 'value': 1.0, 'data_quality': 1.0}
TOOL_NAMES = {s['name'] for s in TOOL_SCHEMAS}
# Numbers the prose may state that are rules, not measurements (PROCESS_CONTEXT).
RULE_CONSTANTS = {'2.5', '1', '3.25', '0.4', '480', '12', '0'}
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
RUN_ID = re.compile(r'[0-9a-f]{32}:\d+')
CITATION = re.compile(r'^(?:Shift (\d)|All shifts), (\w+), ([0-9a-f]{32}:\d+), row (\d+)$')


# ---------------------------------------------------------------- cases

def load_cases():
    """golden.json cases followed by the generated value cases."""
    cases = list(json.loads(GOLDEN.read_text())['cases'])
    if VALUES.exists():
        cases.extend(json.loads(VALUES.read_text())['cases'])
    return cases


_VALUES_STATUS = {}


def stale_values():
    """None when golden_values.json matches the live store, else the reason."""
    if 'message' not in _VALUES_STATUS:
        if not VALUES.exists():
            _VALUES_STATUS['message'] = 'golden_values.json missing; run python3 evals/make_value_goldens.py'
        else:
            recorded = json.loads(VALUES.read_text()).get('content_hash')
            live = default_store().content_hash
            _VALUES_STATUS['message'] = None if recorded == live else (
                f'golden_values.json was generated for dataset {str(recorded)[:12]}, the live store is {live[:12]}: '
                'stale goldens; run python3 evals/make_value_goldens.py')
    return _VALUES_STATUS['message']


# ---------------------------------------------------------------- datasets

_DATASETS = {}


def dataset(day, corrupt=False):
    """A production day of the record store (the app's data source), built once per run."""
    key = (day, corrupt)
    if key not in _DATASETS:
        _DATASETS[key] = default_store().day_analysis(day, corrupt=corrupt)
    return _DATASETS[key]


def close_datasets():
    for analysis in _DATASETS.values():
        analysis.close()
    _DATASETS.clear()


# ---------------------------------------------------------------- planners

class ScriptedPlanner:
    """Issues exactly one tool call and finishes on it: the value cases go through
    the real agent loop (validation, trace, summary) without a language model."""
    mode = 'scripted single tool call (value golden); no language model'

    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments

    def next_step(self, context):
        if not context['results']:
            return {'tool': self.tool, 'arguments': self.arguments}
        result = context['results'][-1]
        if result['ok']:
            return {'final': [result['result_id']]}
        return {'unavailable': result['error']['message']}


# ---------------------------------------------------------------- checks

def renderings(value):
    """Every way the application formats a numeric field."""
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)) and value < 0:
        return renderings(-value)  # a signed delta prints as "-0.12"; the number token is "0.12"
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
        row_sets = [data.get('rows', [])] + [c['rows'] for c in data.get('components', [])] + [data.get('weekday_profile', [])]
        # Period evidence names its windows (labels carry dates) and the weeks it could not find.
        for window in (data.get('window'), data.get('previous_window')):
            if isinstance(window, dict):
                strings.update(v for v in window.values() if isinstance(v, str))
        strings.update(data.get('missing_weeks', []))
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
            token = token.rstrip(',')  # "523, better": the comma is punctuation, not a thousands separator
            if token not in numbers:
                bad.append(f'{token!r} in {line[:70]!r}')
    return bad


def invariants(mode, result, analysis, allowed_shifts):
    """Checks every case must pass, whatever its category."""
    failures = []
    trace = result['trace']
    if mode == 'offline' and (result['usage']['model_calls'] or result['usage']['cost_usd']):
        failures.append('offline run reported model usage')
    for step in trace:
        if step['ok'] and step['tool'] not in TOOL_NAMES:
            failures.append(f'executed unknown tool {step["tool"]}')
        if step['ok'] and step['arguments'].get('shift') is not None and step['arguments']['shift'] not in allowed_shifts:
            failures.append(f'{step["tool"]} ran for shift {step["arguments"]["shift"]} outside scope {allowed_shifts}')
    evidence_shifts = {step['data']['shift'] for step in trace if step['ok'] and step['tool'] != 'render_chart'
                       and step['data']['shift'] is not None}  # period tools report None for all shifts
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
            shift, field, rid, row = (int(m[1]) if m[1] else None), m[2], m[3], int(m[4])
            source = by_id.get(rid)
            if source is None or not source['ok'] or source['data']['shift'] != shift \
                    or row >= len(source['data']['rows']) or field not in source['data']['rows'][row]:
                failures.append(f'citation does not resolve: {citation}')
        if paragraph['kind'] != 'limitation' and not paragraph['citations']:
            failures.append(f'{paragraph["kind"]} paragraph cites nothing')
    for table in result['tables']:
        if table['title'] in PERIOD_TOOLS:
            continue  # period tables are the whole store result; chart-row equality covers them above
        shift = table['shift']
        full = getattr(analysis, table['title'])(shift, **({'metric': table['metric']} if 'metric' in table else {}))
        if len(table['rows']) != len(full['rows']):
            failures.append(f'{table["title"]} table shows {len(table["rows"])} of {len(full["rows"])} rows')
    return failures


def excluded_failures(spec, steps):
    """`excluded_records`: a {count, shift, error_match, raw} requirement or a list of them.
    The excluded records of every successful tool call, deduplicated by record id, must
    satisfy the count and each listed requirement (raw values are the record's own)."""
    records = {}
    for step in steps:
        for record in step['data'].get('excluded_records', []):
            records[record['record_id']] = record
    wants = spec if isinstance(spec, list) else [spec]
    failures = []
    for want in wants:
        if 'count' in want and len(records) != want['count']:
            failures.append(f'{len(records)} excluded records, expected {want["count"]}')
        def matches(record):
            return (want.get('record_id') in (None, record['record_id'])
                    and want.get('shift') in (None, record['shift'])
                    and ('error_match' not in want or re.search(want['error_match'], record['error']))
                    and all(record['raw'].get(k) == v for k, v in want.get('raw', {}).items()))
        if any(k in want for k in ('record_id', 'shift', 'error_match', 'raw')) and not any(matches(r) for r in records.values()):
            failures.append(f'no excluded record matches {want}: have {[(r["record_id"], r["error"]) for r in records.values()]}')
    return failures


def expectations(expect, result):
    """Checks the case asks for explicitly."""
    trace = result['trace']
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
    if 'excluded_records' in expect:
        failures.extend(excluded_failures(expect['excluded_records'], [s for s in ok_steps if s['tool'] != 'render_chart']))
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


# ---------------------------------------------------------------- value cases

def select_row(data, selector):
    """`$` the result envelope; `#N` row N; `field=value` the first row whose field
    prints as value; `set:...` the same inside another row set (weekday_profile)."""
    if selector == '$':
        return data
    rows_key, _, match = selector.partition(':') if ':' in selector else ('rows', '', selector)
    rows = data.get(rows_key)
    if not isinstance(rows, list):
        raise LookupError(f'no row set {rows_key!r}')
    if match.startswith('#'):
        index = int(match[1:])
        if index >= len(rows):
            raise LookupError(f'row {index} beyond {len(rows)} rows')
        return rows[index]
    key, _, value = match.partition('=')
    for row in rows:
        if str(row.get(key)) == value:
            return row
    raise LookupError(f'no row with {key}={value!r} among {[str(r.get(key)) for r in rows][:8]}')


def close_enough(got, want, tolerance):
    """[expected, tolerance]: null tolerance (and strings, booleans, lists, null) are exact."""
    if tolerance is None or want is None or isinstance(want, (str, bool, list)):
        if isinstance(want, bool) or isinstance(got, bool):
            return got is want
        return got == want
    if not isinstance(got, (int, float)) or isinstance(got, bool) or not math.isfinite(got):
        return False
    return abs(got - want) <= tolerance


def value_failures(expect, result):
    steps = [s for s in result['trace'] if s['tool'] != 'render_chart']
    if not steps:
        return ['no tool call was made']
    if not steps[0]['ok']:
        return ['tool call failed: ' + steps[0]['error']['message']]
    data = steps[0]['data']
    failures = []
    if 'row_count' in expect and len(data.get('rows', [])) != expect['row_count']:
        failures.append(f'{len(data.get("rows", []))} rows, expected {expect["row_count"]}')
    for selector, fields in expect.get('values', {}).items():
        try:
            row = select_row(data, selector)
        except (LookupError, ValueError) as exc:
            failures.append(f'{selector}: {exc}')
            continue
        for field, (want, tolerance) in fields.items():
            if field not in row:
                failures.append(f'{selector}.{field} missing')
            elif not close_enough(row[field], want, tolerance):
                failures.append(f'{selector}.{field} = {row[field]!r}, expected {want!r}' + (f' ±{tolerance}' if tolerance else ''))
    return failures


# ---------------------------------------------------------------- investigation cases

def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def find_row(rows, wanted):
    return next((r for r in rows if all(str(r.get(k)) == str(v) for k, v in wanted.items())), None)


def rule_candidates(rule, steps):
    """Successful tool results that fit a rule's tool, argument subset, window and row."""
    for step in steps:
        if not step['ok'] or step['tool'] != rule['tool']:
            continue
        if not all(step['arguments'].get(k) == v for k, v in rule.get('arguments', {}).items()):
            continue
        data = step['data']
        if 'window_covers' in rule:
            window = data.get('window') or {}
            lo, hi = rule['window_covers']
            if not window or window.get('start', '9') > lo or window.get('end', '0') < hi:
                continue
        rows = data.get(rule.get('rows', 'rows')) or []
        row = find_row(rows, rule['row']) if 'row' in rule else None
        if 'row' in rule and row is None:
            continue
        yield step, rows, row


def rule_holds(rule, rows, row):
    """None when the rule holds for this result, else why not."""
    field = rule.get('field')
    value = row.get(field) if row is not None else None
    if field and not is_number(value):
        return f'{field} is {value!r}'
    if 'at_least_times' in rule:
        other, factor = row.get(rule['at_least_times']['field']), rule['at_least_times'].get('factor', 1)
        if not is_number(other):
            return f'{rule["at_least_times"]["field"]} is {other!r}'
        if value < factor * other:
            return f'{field} {value} is below {factor} x {rule["at_least_times"]["field"]} {other}'
    if 'greater_than' in rule:
        other = row.get(rule['greater_than']['field'])
        if not is_number(other) or not value > other:
            return f'{field} {value} is not above {rule["greater_than"]["field"]} {other!r}'
    if 'change_from' in rule:
        spec = rule['change_from']
        base = find_row(rows, spec['row'])
        if base is None or not is_number(base.get(field)):
            return f'no base row {spec["row"]} with {field}'
        delta = value - base[field]
        if 'min' in spec and delta < spec['min']:
            return f'{field} changed {delta:+.4f} from {spec["row"]}, less than {spec["min"]}'
        if 'max' in spec and delta > spec['max']:
            return f'{field} changed {delta:+.4f} from {spec["row"]}, more than {spec["max"]}'
    if rule.get('max_among'):
        others = [r.get(field) for r in rows if r is not row and is_number(r.get(field))]
        if not others or not all(value > o for o in others):
            return f'{field} {value} is not the maximum of {sorted(others, reverse=True)[:3]}...'
    if 'min_value' in rule and value < rule['min_value']:
        return f'{field} {value} below {rule["min_value"]}'
    if 'max_value' in rule and value > rule['max_value']:
        return f'{field} {value} above {rule["max_value"]}'
    return None


def investigation_failures(expect, results):
    """Evidence rules over every turn, the found numbers in the prose, forbidden claims absent."""
    failures = []
    for index, result in enumerate(results, 1):
        if 'status' in expect and result['status'] != expect['status']:
            failures.append(f'turn {index} status {result["status"]!r}, expected {expect["status"]!r}')
        if 'status_in' in expect and result['status'] not in expect['status_in']:
            failures.append(f'turn {index} status {result["status"]!r} not in {expect["status_in"]}')
    steps = [s for r in results for s in r['trace']]
    sections = [line for r in results for line in r['sections']]
    narrative = [p for r in results for p in r.get('narrative', [])]
    prose = sections + [p['text'] for p in narrative]
    matched = []
    for index, rule in enumerate(expect.get('evidence', [])):
        reasons, hit = [], None
        for step, rows, row in rule_candidates(rule, steps):
            reason = rule_holds(rule, rows, row)
            if reason is None:
                hit = row if row is not None else {}
                break
            reasons.append(reason)
        matched.append(hit)
        if hit is None:
            label = rule.get('why') or f'{rule["tool"]} {rule.get("row", "")}'.strip()
            failures.append(f'evidence {index} ({label}): ' + ('; '.join(reasons[:2]) if reasons else
                            f'no {rule["tool"]} result matches {rule.get("arguments", {})} {rule.get("window_covers", "")}'))
    tokens = {t.rstrip(',') for t in NUMBER.findall(' '.join(prose))}
    for want in expect.get('prose_numbers', []):
        row = matched[want['evidence']] if want['evidence'] < len(matched) else None
        if not row:
            continue  # the evidence failure above already says why
        value = row.get(want['field'])
        if not renderings(value) & tokens:
            failures.append(f'prose never states evidence {want["evidence"]} {want["field"]} = {value!r}')
    claims = sections + [p['text'] for p in narrative if p['kind'] != 'limitation']
    for pattern in expect.get('forbidden', []):
        hits = [t[:80] for t in claims if re.search(pattern, t, re.I)]
        if hits:
            failures.append(f'asserts a cause the evidence lacks /{pattern[:50]}/: {hits[0]!r}')
    generic = {k: v for k, v in expect.items() if k not in ('status', 'status_in', 'evidence', 'prose_numbers', 'forbidden')}
    failures.extend(expectations(generic, results[-1]))
    return failures


# ---------------------------------------------------------------- live providers

def prompt_digest(payload):
    """OpenAI: the developer message; Anthropic: the system prompt."""
    if payload.get('input'):
        first = payload['input'][0]['content']
    else:
        first = payload.get('system') or ''
    return hashlib.sha256(first.encode()).hexdigest()[:12]


def exchange_record(payload, response, status=None):
    seen = RUN_ID.search(json.dumps(payload))
    record = {'run_id': seen.group(0).split(':')[0] if seen else None, 'model': payload['model'],
              'prompt_digest': prompt_digest(payload), 'tools': [t['name'] for t in payload['tools']], 'response': response}
    if status is not None:
        record['status'] = status
    return record


def replay_body(exchange, payload, warnings):
    if exchange.get('prompt_digest') != prompt_digest(payload):
        warnings.add('prompt changed since the fixture was recorded; re-record with --live')
    body = json.dumps(exchange['response'])
    current = RUN_ID.search(json.dumps(payload))
    if exchange.get('run_id') and current:
        body = body.replace(exchange['run_id'], current.group(0).split(':')[0])
    return body


def openai_record(exchanges):
    from investigator.openai_provider import transport

    def send(payload, key, timeout):
        response = transport(payload, key, timeout)
        exchanges.append(exchange_record(payload, response))
        return response
    return send


def openai_replay(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        return json.loads(replay_body(pending.pop(0), payload, warnings))
    return send


def anthropic_record(exchanges):
    from investigator.anthropic_provider import transport

    def send(payload, key, timeout):
        status, body = transport(payload, key, timeout)
        try:
            response = json.loads(body)
        except ValueError:
            response = {'unparsed_body': body.decode('utf-8', 'replace')[:2000]}
        exchanges.append(exchange_record(payload, response, status))
        return status, body
    return send


def anthropic_replay(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        exchange = pending.pop(0)
        return exchange.get('status', 200), replay_body(exchange, payload, warnings).encode()
    return send


def openai_planner(key, ledger, send):
    from investigator.openai_provider import Budget, OpenAIPlanner
    return OpenAIPlanner(key, Budget(ledger), send)


def anthropic_planner(key, ledger, send):
    from investigator.anthropic_provider import AnthropicPlanner, Budget
    return AnthropicPlanner(key, Budget(ledger), send, model=os.environ.get('ANTHROPIC_MODEL') or None)


PROVIDERS = {
    'openai': {'env': 'OPENAI_API_KEY', 'planner': openai_planner, 'record': openai_record,
               'replay': openai_replay, 'replay_key': 'sk-replay-fixture-not-a-credential'},
    'anthropic': {'env': 'ANTHROPIC_API_KEY', 'planner': anthropic_planner, 'record': anthropic_record,
                  'replay': anthropic_replay, 'replay_key': 'sk-ant-replay-fixture-not-a-credential'},
}


def fixture_path(case_id, provider):
    return FIXTURES / provider / f'{case_id}.json'


def default_provider():
    """The one provider whose key is set; None when none or both are."""
    keyed = [name for name, spec in PROVIDERS.items() if os.environ.get(spec['env'])]
    return keyed[0] if len(keyed) == 1 else None


# ---------------------------------------------------------------- running one case

def run_case(case, provider=None, live=False, record=False):
    """Return (outcome, failures, note) with outcome in pass/fail/skip/xfail.

    provider None runs the case offline (OfflinePlanner, or ScriptedPlanner for a
    value case). A provider name runs it live when `live` is set, else replays the
    recorded fixture for that provider."""
    day = case.get('day', DEFAULT_DAY)
    scope = case.get('scope', 'shift')
    shift = case.get('shift', 2)
    if case['type'] == 'value':
        scope, shift = 'day', case['arguments'].get('shift') or 2
    expect = case.get('expect', {})
    if provider and 'expect_live' in case:
        expect = case['expect_live']
    allowed = [shift] if scope == 'shift' else [1, 2, 3]
    turns = case.get('turns') or [case.get('question') or f'value golden: {case.get("tool")}']
    mode = 'live' if provider else 'offline'
    note, warnings, exchanges = '', set(), []

    if provider is None:
        if case['type'] == 'value':
            stale = stale_values()
            if stale:
                return 'fail', [stale], ''

            def make_agent(analysis):
                return Agent(analysis, planner=ScriptedPlanner(case['tool'], case['arguments']))
        else:
            def make_agent(analysis):
                return Agent(analysis)
    else:
        spec = PROVIDERS[provider]
        fixture = fixture_path(case['id'], provider)
        if live:
            key = os.environ.get(spec['env'])
            if not key:
                return 'skip', [], f'{spec["env"]} not set'
            send = spec['record'](exchanges)
        elif fixture.exists():
            exchanges = json.loads(fixture.read_text())['exchanges']
            send = spec['replay'](exchanges, warnings)
            key = spec['replay_key']
        else:
            return 'skip', [], f'no {provider} fixture; run --live --provider {provider} to record'
        ledger = Path(tempfile.mkdtemp()) / 'budget.sqlite3'

        def make_agent(analysis):
            return Agent(analysis, planner=spec['planner'](key, ledger, send), max_calls=10, max_seconds=60)

    results, analysis = [], None
    try:
        analysis = dataset(day, case.get('corrupt', False))
        context = Context(day, shift)
        context.scope = scope
        for text in turns:
            results.append(make_agent(analysis).ask(text, context))
    except ValueError as exc:
        want = expect.get('raises')
        if want and re.search(want, str(exc), re.I):
            return 'pass', [], f'refused: {str(exc)[:60]}'
        return 'fail', [f'raised {str(exc)[:90]!r}' + (f', expected /{want}/' if want else '')], ''
    if expect.get('raises'):
        return 'fail', [f'did not raise; status {results[-1]["status"]}'], ''

    failures = []
    for result in results:
        failures.extend(invariants(mode, result, analysis, allowed))
    if case['type'] == 'investigation':
        failures.extend(investigation_failures(expect, results))
    else:
        failures.extend(expectations(expect, results[-1]))
        if case['type'] == 'value':
            failures.extend(value_failures(expect, results[-1]))
    last = results[-1]
    if provider and live and record:
        fixture = fixture_path(case['id'], provider)
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(json.dumps(
            {'case': case['id'], 'provider': provider, 'question': turns[-1], 'recorded_status': last['status'],
             'recorded_cost_usd': last['usage']['cost_usd'], 'exchanges': exchanges}, indent=1))
        note = f'recorded {len(exchanges)} exchanges, ${last["usage"]["cost_usd"]:.4f}'
    elif provider:
        note = f'{last["status"]}, {last["usage"]["model_calls"]} model calls'
    else:
        note = f'{last["status"]}, {sum(len(r["trace"]) for r in results)} tool calls'
    if warnings:
        note += '; ' + '; '.join(sorted(warnings))
    if failures and case.get('known_defect'):
        return 'xfail', failures, 'known defect: ' + case['known_defect']
    if not failures and case.get('known_defect'):
        note += '; known_defect marker is stale: the case passes, remove the marker'
    return ('fail' if failures else 'pass'), failures, note


# ---------------------------------------------------------------- score table

def plan_rows(cases, live, provider):
    """(case, provider or None) pairs: every offline case once, plus a live variant per
    provider for `mode: live` cases and cases marked `live: true`."""
    plan = []
    for case in cases:
        if case['mode'] == 'offline':
            plan.append((case, None))
        if case['mode'] == 'live' or case.get('live'):
            if live:
                plan.append((case, provider))
            else:
                recorded = [p for p in PROVIDERS if fixture_path(case['id'], p).exists()]
                plan.extend((case, p) for p in recorded)
                if not recorded:
                    plan.append((case, '?'))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='run the live variants against a provider and record fixtures')
    parser.add_argument('--provider', choices=sorted(PROVIDERS), help='openai or anthropic; default: the one whose key is set')
    parser.add_argument('--no-record', action='store_true', help='with --live: do not overwrite fixtures')
    parser.add_argument('--only', action='append', help='run only this case id (repeatable)')
    parser.add_argument('--json', type=Path, help='write results here')
    args = parser.parse_args(argv)

    provider = args.provider or default_provider()
    if args.live and provider is None:
        print('--live needs exactly one of ' + ' / '.join(s['env'] for s in PROVIDERS.values()) + ' set, or --provider')
        return 2
    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c['id'] in args.only]
    rows = []
    try:
        for case, case_provider in plan_rows(cases, args.live, provider):
            if case_provider == '?':
                outcome, failures, note = 'skip', [], 'no fixture; run --live to record'
                case_provider = None
                label = f'{case["id"]}@live'
            else:
                outcome, failures, note = run_case(case, case_provider, args.live, not args.no_record)
                label = case['id'] + (f'@{case_provider}' if case_provider else '')
            rows.append({'id': label, 'case': case['id'], 'type': case['type'], 'provider': case_provider,
                         'mode': 'live' if case_provider or label.endswith('@live') else 'offline',
                         'outcome': outcome, 'failures': failures, 'note': note})
    finally:
        close_datasets()

    width = max(len(r['id']) for r in rows) + 2
    print(f'{"case":{width}}{"type":15}{"mode":9}{"result":8}note')
    print('-' * (width + 32 + 40))
    for r in rows:
        print(f'{r["id"]:{width}}{r["type"]:15}{r["mode"]:9}{r["outcome"]:8}{r["note"]}')
        for failure in r['failures'][:6]:
            print(f'{"":{width}}  - {failure}')
        if len(r['failures']) > 6:
            print(f'{"":{width}}  - ... {len(r["failures"]) - 6} more')
    print('-' * (width + 32 + 40))
    totals = Counter((r['type'], r['mode'], r['outcome']) for r in rows)
    summary = []
    for kind in TYPES:
        p, f, s, x = (sum(totals[(kind, m, o)] for m in ('offline', 'live')) for o in ('pass', 'fail', 'skip', 'xfail'))
        if p + f + s + x:
            summary.append(f'{kind} {p}/{p + f}' + (f' ({s} skipped)' if s else '') + (f' ({x} known defect)' if x else ''))
    print('  '.join(summary))
    breaches = 0
    live_rows = [r for r in rows if r['provider']]
    for name in sorted({r['provider'] for r in live_rows}):
        parts = []
        for kind in TYPES:
            p = sum(1 for r in live_rows if r['provider'] == name and r['type'] == kind and r['outcome'] == 'pass')
            f = sum(1 for r in live_rows if r['provider'] == name and r['type'] == kind and r['outcome'] == 'fail')
            if p + f:
                rate = p / (p + f)
                ok = rate >= THRESHOLDS[kind]
                breaches += not ok
                parts.append(f'{kind} {p}/{p + f} (threshold {THRESHOLDS[kind]:.0%}: {"ok" if ok else "BELOW"})')
        if parts:
            print(f'live {name}: ' + '  '.join(parts))
    failed = sum(r['outcome'] == 'fail' and not r['provider'] for r in rows)
    skipped = sum(r['outcome'] == 'skip' for r in rows)
    xfailed = sum(r['outcome'] == 'xfail' for r in rows)
    live_failed = sum(r['outcome'] == 'fail' and bool(r['provider']) for r in rows)
    print(f'{len(rows) - failed - live_failed - skipped - xfailed} passed, {failed} failed, {skipped} skipped'
          + (f', {xfailed} known defect' if xfailed else '') + (f', {live_failed} live failed' if live_failed else ''))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))
    return failed + breaches


if __name__ == '__main__':
    sys.exit(main())
