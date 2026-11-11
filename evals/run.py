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
type. Offline cases are deterministic and free, and each one also runs through the
URL path (issue #20): its question goes to `GET /q?...&q=` on a loopback server,
the redirect must land on `expect.url` when the case records one, and the answer
served at that canonical address is scored like the in-process one, within the
same per-case budget on the server's own time (`Server-Timing`). Cases marked `live: true` (and
the `mode: live` cases) also run against a live planner: OpenAI or Anthropic,
chosen by `--provider` or by whichever key is set. Live runs are recorded to
`evals/fixtures/<provider>/<id>.json` (stamped with the dataset hash; a recording made
against another dataset is reported as stale, never scored) and replayed afterwards; a live type passes
by pass rate (`THRESHOLDS`), an offline case passes or fails on its own.

    python3 evals/run.py                              # offline + replay of recorded fixtures
    python3 evals/run.py --live                       # also call the one provider whose key is set
    python3 evals/run.py --live --provider anthropic  # ANTHROPIC_API_KEY; or openai with OPENAI_API_KEY
    python3 evals/run.py --only why-slow              # one case
    python3 evals/run.py --json out.json              # machine-readable results

Exit status is the number of failed offline cases plus the number of live
(provider, type) pairs under their pass-rate threshold, plus, when the run
covers the whole offline set (no --only), one per offline latency percentile
(p50, p95) over its budget. Skips and known defects do not count.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from datetime import datetime, timezone
import subprocess
import math
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from investigator.agent import PERIOD_TOOLS, TOOL_SCHEMAS, Agent, Context  # noqa: E402
from investigator.cost_model import parse_rates  # noqa: E402
from investigator.record_store import default_store  # noqa: E402

GOLDEN = ROOT / 'evals' / 'golden.json'
VALUES = ROOT / 'evals' / 'golden_values.json'
FIXTURES = ROOT / 'evals' / 'fixtures'
DEFAULT_DAY = '2026-09-14'
TYPES = ('routing', 'faithfulness', 'grounding', 'refusal', 'value', 'investigation', 'data_quality')
# Pass rate a live provider must reach per type; offline cases pass or fail one by one.
THRESHOLDS = {'routing': 0.9, 'faithfulness': 1.0, 'grounding': 1.0, 'refusal': 1.0,
              'investigation': 0.8, 'value': 1.0, 'data_quality': 1.0}
# Latency: an offline case fails above `expect.max_ms` (default DEFAULT_MAX_MS; a live or replayed
# run is never timed against it), and a whole-set run fails when the offline p50 or p95 is over
# LATENCY_BUDGET_MS (an --only subset prints its percentiles and is not gated: they are just those cases' times).
# The clock covers the agent's turns only: dataset build, fixture I/O and the checks are outside it.
DEFAULT_MAX_MS = 250
LATENCY_BUDGET_MS = {'p50': 100, 'p95': 250}
TOOL_NAMES = {s['name'] for s in TOOL_SCHEMAS}
# Numbers the prose may state that are rules, not measurements (PROCESS_CONTEXT).
RULE_CONSTANTS = {'2.5', '1', '3.25', '0.4', '480', '12', '0'}
# The answer shape (issue #75), stated here independently of the application: a bold figure, the
# evidence a line cites, a where-to-look question, and the result field each evidence part names.
BOLD = re.compile(r'\*\*([^*]+)\*\*')
EVIDENCE = re.compile(r' \(query (\d+)(?: · (row|detail|weekday|flag) (\d+))?\)')
LOOK_LINK = re.compile(r' → “([^”]+)”$')
EVIDENCE_PARTS = {'row': 'rows', 'detail': 'details', 'weekday': 'weekday_profile', 'flag': 'flags'}
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
RUN_ID = re.compile(r'[0-9a-f]{32}:\d+')
CITATION = re.compile(r'^(?:Shift (\d)|All shifts), (\w+), ([0-9a-f]{32}:\d+), row (\d+)$')
COMPARISON_WITHDRAWN_LINE = 'A comparison was withdrawn because it contradicted its own figures.'


# ---------------------------------------------------------------- cases

def load_cases():
    """golden.json cases followed by the generated value cases."""
    cases = list(json.loads(GOLDEN.read_text())['cases'])
    if VALUES.exists():
        cases.extend(json.loads(VALUES.read_text())['cases'])
    return cases


_VALUES_STATUS = {}


def dataset_version():
    """The live store's content hash; recordings and value goldens are scored only against it."""
    return default_store().content_hash


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
    close_server()


# ---------------------------------------------------------------- the URL path (issue #20)

_SERVER = {}
URL_MS = {}   # case id -> server time of its answers over GET, for the table and the JSON


def local_server():
    """One loopback workbench per run, started on first use, over the same default store."""
    if 'server' not in _SERVER:
        from investigator.server import make_server
        server = make_server(0)
        server.app.request_limit = 1_000_000   # the harness is not a visitor: the per-minute window is for people
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _SERVER['server'], _SERVER['thread'] = server, thread
    return _SERVER['server']


def close_server():
    if 'server' in _SERVER:
        server, thread = _SERVER.pop('server'), _SERVER.pop('thread')
        server.shutdown()
        thread.join()
        server.server_close()
        server.app.close()


def http_get(path):
    server = local_server()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
    try:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def bare_url(url):
    """A canonical address without its follow-up chain (`after=`): what `expect.url` records."""
    path, query = urlsplit(url).path, urlsplit(url).query
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != 'after']
    return path + ('?' + urlencode(kept, safe=',/?=:-') if kept else '')


def via_url(case, expect, turns, redirect_only=False):
    """Run the turns through the URL path: `GET /q` must redirect to the canonical address (the
    recorded `expect.url` when there is one), and that address must answer as the question would.
    Returns (results or None when a turn was refused, the refusal text, failures, server ms).
    `redirect_only` checks the resolution alone (a live case that records a URL)."""
    failures, results, after, ms, error, location = [], [], [], 0.0, None, None
    period = case.get('period', 'day')
    for text in turns:
        params = [('day', case.get('day', DEFAULT_DAY)), ('shift', case.get('shift', 2)), ('scope', case.get('scope', 'shift')),
                  ('period', 'accounting' if period == 'fiscal' else period)]
        if case.get('corrupt'):
            params.append(('corrupt', '1'))
        params.append(('q', text))
        params.extend(after)
        status, headers, body = http_get('/q?' + urlencode(params))
        if status == 400:
            error = json.loads(body)['error']
            break
        if status != 302 or 'Location' not in headers:
            failures.append(f'GET /q answered {status}, expected a 302 to the canonical URL')
            break
        location = headers['Location']
        if redirect_only:
            continue
        status, headers, body = http_get(location)
        timing = re.search(r'dur=([0-9.]+)', headers.get('Server-Timing', ''))
        ms += float(timing[1]) if timing else 0.0
        if status == 400:
            error = json.loads(body)['error']
            break
        if status != 200:
            failures.append(f'GET {location} answered {status}')
            break
        result = json.loads(body)
        if result.get('url') != location:
            failures.append(f'the answer names {result.get("url")!r} as its address, served at {location!r}')
        results.append(result)
        after.append(('after', bare_url(location)))
    want = expect.get('url')
    if want and (location is None or bare_url(location) != want):
        failures.append(f'q= resolves to {bare_url(location) if location else repr(error)}, expected {want}')
    if error is not None:
        results = None
    return results, error, failures, ms


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


class ScriptedAnalyst:
    """Returns one fixed plan and, when the case scripts one, the narrative lines a model might
    stream: a `plan` case puts both through the real plan check, tool execution and stream
    validator (`Agent.ask_planned`, `NarrativeStream`) without a model. Without `narrative` it
    writes none."""
    planned = True
    mode = 'scripted plan (golden); no language model'

    def __init__(self, plan, narrative=None):
        self._plan = plan
        self._narrative = narrative

    def plan(self, context):
        return json.loads(json.dumps(self._plan))

    def narrate(self, question, evidence, emit=None):
        if self._narrative is None:
            raise ValueError('a scripted plan has no narrative')
        from investigator.narrative import NarrativeStream, evidence_packet
        stream = NarrativeStream(evidence_packet(evidence), emit)
        text = '\n'.join(self._narrative) + '\n'
        for i in range(0, len(text), 9):   # small text deltas, the way a model's stream arrives
            stream.feed(text[i:i + 9])
        return stream.finish()


def run_turn(agent, text, context):
    """One question through the agent: the planned round for an analyst, the loop for everything else."""
    if getattr(agent.planner, 'planned', False):
        return agent.ask_planned(text, context)
    return agent.ask(text, context)


# ---------------------------------------------------------------- checks

def renderings(value):
    """Every way the application formats a numeric field."""
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)) and value < 0:
        return renderings(-value)  # a signed delta prints as "-0.12"; the number token is "0.12"
    if isinstance(value, float):
        value = abs(value)         # -0.0 prints as "-0.00"; its number token is "0.00"
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
            # A refused call's own message (a window outside the dataset range, a shift
            # outside the scope) is tool output the answer may quote verbatim; the numbers
            # inside it are the tool's, not the planner's.
            strings.add(result['error']['message'])
            continue
        data = result['data']
        # Every row set the envelope carries: rows, chart components, the weekday profile, reported
        # sub-reason details, and any other list of records a tool adds.
        row_sets = [data.get('rows', [])] + [c['rows'] for c in data.get('components', [])]
        row_sets += [v for k, v in data.items() if k not in ('rows', 'components', 'excluded_records')
                     and isinstance(v, list) and v and all(isinstance(r, dict) for r in v)]
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
        # Envelope scalars (a window total, a band width, a gross area) are tool output as much as rows are.
        for value in data.values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
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
        text = EVIDENCE.sub(' ', text)   # `(query 2 · row 0)` names a step and a row; it states no measurement
        text = LOOK_LINK.sub(' ', text)  # a where-to-look question is the router's own text
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


def unprovenanced(lines, trace):
    """Numbers on a cited line that its cited evidence does not hold (issue #75). A line with a `**figure**`
    carries exactly one, and an evidence reference `(query N · row M)` that resolves: step N of the trace, a
    successful tool call, row M of its rows (`detail M`, `weekday M`, `flag M`: of its sub-reason, weekday and flag lists).
    Every number in the figure is a rendering of a value of that row; with no row named, of the result as a
    whole: a count of its rows, a below-target count, or a total its envelope carries. Every other number on
    the line is one of those too, a count of the result's rows, or a stated rule constant: a line never
    states a figure from a row it does not cite."""
    bad = []
    for line in lines:
        figures = BOLD.findall(line)
        if not figures:
            continue
        refs = EVIDENCE.findall(line)
        if len(figures) != 1 or len(refs) != 1:
            bad.append(f'{len(figures)} figures and {len(refs)} evidence references in {line[:70]!r}')
            continue
        step, part, index = refs[0]
        step = int(step)
        source = trace[step - 1] if 0 < step <= len(trace) else None
        if source is None or not source['ok'] or source['tool'] == 'render_chart':
            bad.append(f'query {step} is not a successful tool call: {line[:70]!r}')
            continue
        data = source['data']
        rows = data.get('rows', [])
        if part:
            records = data.get(EVIDENCE_PARTS[part]) or []
            if int(index) >= len(records):
                bad.append(f'query {step} has no {part} {index}: {line[:70]!r}')
                continue
            record = records[int(index)]
            allowed = set().union(*(renderings(v) for v in record.values() if not isinstance(v, (str, list, dict))))
            names = [v for v in record.values() if isinstance(v, str)]
        else:
            allowed = renderings(len(rows))
            if rows and 'actual_fpm' in rows[0] and 'target_fpm' in rows[0]:
                allowed |= renderings(sum(r['actual_fpm'] < r['target_fpm'] for r in rows))
            allowed |= set().union(set(), *(renderings(v) for v in data.values() if isinstance(v, (int, float)) and not isinstance(v, bool)))
            names = [v for v in data.values() if isinstance(v, str)]
        for token in NUMBER.findall(figures[0]):
            if token.rstrip(',') not in allowed:
                bad.append(f'{token!r} is not in query {step} {part or "result"} {index}: {line[:70]!r}')
        beside = allowed | renderings(len(rows)) | set(RULE_CONSTANTS)
        rest = LOOK_LINK.sub(' ', EVIDENCE.sub(' ', line))
        for name in sorted(names, key=len, reverse=True):
            rest = rest.replace(name, ' ')
        rest = re.sub(r'\b[Ss]hift [123]\b', ' ', rest)
        for token in NUMBER.findall(rest):
            if token.rstrip(',') not in beside:
                bad.append(f'{token!r} on a line citing query {step} {part or "result"} {index} is not from it: {line[:70]!r}')
    return bad


def shape_failures(spec, sections):
    """`expect.shape`: one headline (the first section with a bold figure that is not a bullet), finding
    bullets after it with exactly one bold figure each, and a `Where to look` list of question links."""
    failures = []
    heads = [i for i, s in enumerate(sections) if '**' in s and not s.startswith('- ')]
    if len(heads) != 1:
        return [f'{len(heads)} headlines, expected one']
    head = heads[0]
    if 'headline_match' in spec and not re.search(spec['headline_match'], sections[head]):
        failures.append(f'headline {sections[head][:70]!r} does not match /{spec["headline_match"]}/')
    findings = []
    for s in sections[head + 1:]:
        if not s.startswith('- '):
            break
        findings.append(s)
    low, high = spec.get('bullets', [3, 5])
    if not low <= len(findings) <= high or any(len(BOLD.findall(b)) != 1 for b in findings):
        failures.append(f'{len(findings)} finding bullets right under the headline (want {low}-{high}, one bold figure each)')
    if 'Where to look' not in sections:
        return failures + ['no Where to look list']
    looks = []
    for s in sections[sections.index('Where to look') + 1:]:
        if not s.startswith('- '):
            break
        looks.append(s)
    low, high = spec.get('look', [2, 3])
    if not low <= len(looks) <= high or not all(LOOK_LINK.search(s) for s in looks):
        failures.append(f'{len(looks)} where-to-look links (want {low}-{high}, each ending with a question)')
    return failures


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
    bad = unprovenanced(result['sections'], trace)
    if bad:
        failures.append('figures not from their cited row: ' + '; '.join(bad[:3]))
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
            # `shift` may be cited as a field: the validator answers it from the result's metadata.
            row_has_field = row < len(source['data']['rows']) and (field in source['data']['rows'][row] or (field == 'shift' and shift is not None)) if source and source['ok'] else False
            if source is None or not source['ok'] or source['data']['shift'] != shift or not row_has_field:
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
    if 'chart_units' in expect:
        units = [c.get('unit') for c in result['charts']]
        if units != expect['chart_units']:
            failures.append(f'chart units {units}, expected {expect["chart_units"]}')
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
    # sections_match: one pattern, or a list of patterns each of which some section matches
    for pattern in ([expect['sections_match']] if isinstance(expect.get('sections_match'), str) else expect.get('sections_match', [])):
        if not any(re.search(pattern, s, re.I) for s in result['sections']):
            failures.append(f'no section matches /{pattern}/')
    if 'shape' in expect:
        failures.extend(shape_failures(expect['shape'], result['sections']))
    if 'sections_not_match' in expect:
        hits = [s[:60] for s in result['sections'] if re.search(expect['sections_not_match'], s, re.I)]
        if hits:
            failures.append(f'section matches forbidden /{expect["sections_not_match"]}/: {hits[0]!r}')
    prose = [p['text'] for p in result.get('narrative', []) if p['kind'] != 'limitation']
    if 'narrative_not_match' in expect:
        hits = [t[:60] for t in prose if re.search(expect['narrative_not_match'], t, re.I)]
        if hits:
            failures.append(f'narrative matches forbidden /{expect["narrative_not_match"]}/: {hits[0]!r}')
    if 'narrative_match' in expect and not any(re.search(expect['narrative_match'], t, re.I) for t in prose):
        failures.append(f'no interpretation paragraph matches /{expect["narrative_match"]}/')
    if 'narrative_status' in expect and result.get('narrative_status') != expect['narrative_status']:
        failures.append(f'narrative_status {result.get("narrative_status")!r}, expected {expect["narrative_status"]!r}')
    if 'comparisons_withdrawn' in expect:
        # Issue #79: a paragraph whose ordering words contradict its own cited values is withdrawn,
        # counted, and disclosed in one limitation line (the issue's wording, not imported from the app).
        want, got = expect['comparisons_withdrawn'], result['usage'].get('comparisons_withdrawn', 0)
        disclosed = any(p['kind'] == 'limitation' and p['text'] == COMPARISON_WITHDRAWN_LINE for p in result.get('narrative', []))
        if got != want:
            failures.append(f'{got} comparisons withdrawn, expected {want}')
        if disclosed != bool(want):
            failures.append(f'the limitation line {COMPARISON_WITHDRAWN_LINE!r} is ' + ('missing' if want else 'shown with nothing withdrawn'))
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
              'prompt_digest': prompt_digest(payload), 'tools': [t['name'] for t in payload.get('tools', [])], 'response': response}
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


# The analyst (issue #23) makes two streamed calls with no tools. A recording of that shape is
# stamped `shape: "plan"` and stores each call's stream events; it replays through AnthropicAnalyst.
# A recording without the stamp was made by the tool loop and replays through AnthropicPlanner
# until it is re-recorded (`--live --provider anthropic`), which always records the new shape.
ANALYST_SHAPE = 'plan'


def analyst_record(exchanges):
    from investigator.anthropic_provider import stream_transport

    def send(payload, key, timeout):
        status, events = stream_transport(payload, key, timeout)
        seen = []
        exchanges.append(exchange_record(payload, {'events': seen}, status))

        def relay():
            try:
                for event in events:
                    seen.append(event)
                    yield event
            finally:
                events.close()
        return status, relay()
    return send


def analyst_replay(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        exchange = pending.pop(0)
        return exchange.get('status', 200), iter(json.loads(replay_body(exchange, payload, warnings))['events'])
    return send


def analyst_planner(key, ledger, send):
    from investigator.anthropic_provider import AnthropicAnalyst, Budget
    # Replays run as fast as the events can be read; a live round keeps the product's own ten-second ceiling.
    return AnthropicAnalyst(key, Budget(ledger), send, model=os.environ.get('ANTHROPIC_MODEL') or None)


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
    """(outcome, failures, note, ms): `run_case_detail` without the turn results."""
    return run_case_detail(case, provider, live, record)[:4]


def run_case_detail(case, provider=None, live=False, record=False):
    """`_run_case` plus the agent's result for each turn that ran (the /evals page draws its tool
    trail from them). The temporary budget ledger of a live or replayed run is removed afterwards."""
    scratch = []
    try:
        return _run_case(case, provider, live, record, scratch)
    finally:
        for folder in scratch:
            shutil.rmtree(folder, ignore_errors=True)


def _run_case(case, provider, live, record, scratch):
    """Return (outcome, failures, note, ms) with outcome in pass/fail/skip/xfail and
    ms the wall time of the agent's turns (0 when the case did not run).

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
    results = []
    turns = case.get('turns') or [case.get('question') or f'value golden: {case.get("tool")}']
    mode = 'live' if provider else 'offline'
    note, warnings, exchanges = '', set(), []

    if provider is None:
        if case['type'] == 'value':
            stale = stale_values()
            if stale:
                return 'fail', [stale], '', 0.0, results

            def make_agent(analysis):
                return Agent(analysis, planner=ScriptedPlanner(case['tool'], case['arguments']))
        elif case.get('plan') is not None:
            def make_agent(analysis):
                return Agent(analysis, planner=ScriptedAnalyst(case['plan'], case.get('narrative')))
        else:
            def make_agent(analysis):
                return Agent(analysis)
    else:
        spec = PROVIDERS[provider]
        fixture = fixture_path(case['id'], provider)
        planned = provider == 'anthropic'   # what a live run records; a replay follows the recording's own shape
        if live:
            key = os.environ.get(spec['env'])
            if not key:
                return 'skip', [], f'{spec["env"]} not set', 0.0, results
            send = analyst_record(exchanges) if planned else spec['record'](exchanges)
        elif fixture.exists():
            recording = json.loads(fixture.read_text())
            # A recording replays the model's choices against the live store's data. Made against
            # another dataset (or before recordings were versioned) it cannot be scored: the rows the
            # model cited may no longer exist. That is a stale recording, not a failure.
            recorded_dataset, live_dataset = recording.get('dataset_version'), dataset_version()
            if recorded_dataset != live_dataset:
                return 'skip', [], (f'stale recording: dataset {str(recorded_dataset)[:12] if recorded_dataset else "unversioned"} '
                                    f'vs live {live_dataset[:12]}; re-record with --live --provider {provider}'), 0.0, results
            exchanges = recording['exchanges']
            planned = recording.get('shape') == ANALYST_SHAPE
            send = analyst_replay(exchanges, warnings) if planned else spec['replay'](exchanges, warnings)
            key = spec['replay_key']
        else:
            return 'skip', [], f'no {provider} fixture; run --live --provider {provider} to record', 0.0, results
        scratch.append(tempfile.mkdtemp())
        ledger = Path(scratch[-1]) / 'budget.sqlite3'

        def make_agent(analysis):
            if planned:
                return Agent(analysis, planner=analyst_planner(key, ledger, send), max_calls=2, max_seconds=10)
            return Agent(analysis, planner=spec['planner'](key, ledger, send), max_calls=10, max_seconds=60)

    analysis, ms = None, 0.0
    try:
        analysis = dataset(day, case.get('corrupt', False))
        context = Context(day, shift)
        context.scope = scope
        context.period = case.get('period', 'day')   # the Period control: week, month or fiscal routes to the period tools
        context.rates = parse_rates(case.get('rates'))   # a case's rate overrides, spelled as rates= spells them (issue #31)
        started = time.perf_counter()
        try:
            for text in turns:
                results.append(run_turn(make_agent(analysis), text, context))
        finally:
            ms = (time.perf_counter() - started) * 1000
    except ValueError as exc:
        want = expect.get('raises')
        if want and re.search(want, str(exc), re.I):
            slow = latency_failures(expect, provider, ms) + url_failures(case, expect, turns, provider)
            return ('fail' if slow else 'pass'), slow, f'refused: {str(exc)[:60]}', ms, results
        return 'fail', [f'raised {str(exc)[:90]!r}' + (f', expected /{want}/' if want else '')], '', ms, results
    if expect.get('raises'):
        return 'fail', [f'did not raise; status {results[-1]["status"]}'], '', ms, results

    failures = latency_failures(expect, provider, ms)
    failures.extend(score(case, expect, results, analysis, allowed, mode))
    failures.extend(url_failures(case, expect, turns, provider, analysis, allowed))
    last = results[-1]
    if provider and planned:
        # The analyst is two model calls at most: one plan, one narrative. An answer with a narrative used exactly two.
        calls = [r['usage']['model_calls'] for r in results]
        if any(c > 2 for c in calls) or any(r['narrative_status'] == 'available' and c != 2 for r, c in zip(results, calls)):
            failures.append(f'analyst made {calls} model calls; a round is one plan and one narrative')
    if provider and live and record:
        fixture = fixture_path(case['id'], provider)
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(json.dumps(
            {'case': case['id'], 'provider': provider, 'question': turns[-1], 'recorded_status': last['status'],
             'recorded_cost_usd': last['usage']['cost_usd'], 'dataset_version': dataset_version(),
             **({'shape': ANALYST_SHAPE} if planned else {}), 'exchanges': exchanges}, indent=1))
        note = (f'recorded {len(exchanges)} exchanges, {last["usage"]["model_calls"]} model calls, ${last["usage"]["cost_usd"]:.4f}'
                + (f', first token {last["usage"]["first_token_ms"]:.0f} ms' if 'first_token_ms' in last['usage'] else ''))
    elif provider:
        note = f'{last["status"]}, {last["usage"]["model_calls"]} model calls'
    else:
        note = f'{last["status"]}, {sum(len(r["trace"]) for r in results)} tool calls'
        if case['id'] in URL_MS:
            note += f'; URL {URL_MS[case["id"]]:.0f} ms'
    if warnings:
        note += '; ' + '; '.join(sorted(warnings))
    if failures and case.get('known_defect'):
        return 'xfail', failures, 'known defect: ' + case['known_defect'], ms, results
    if not failures and case.get('known_defect'):
        note += '; known_defect marker is stale: the case passes, remove the marker'
    return ('fail' if failures else 'pass'), failures, note, ms, results


def score(case, expect, results, analysis, allowed, mode):
    """The invariants over every turn and the case's own expectations over the last (or, for an
    investigation, over all of them): the same scoring for the in-process and the URL answers."""
    failures = []
    for result in results:
        failures.extend(invariants(mode, result, analysis, allowed))
    if case['type'] == 'investigation':
        failures.extend(investigation_failures(expect, results))
    else:
        failures.extend(expectations(expect, results[-1]))
        if case['type'] == 'value':
            failures.extend(value_failures(expect, results[-1]))
    return failures


def url_failures(case, expect, turns, provider, analysis=None, allowed=None):
    """The URL path of an offline case: the redirect lands on `expect.url`, a refusal comes back as
    the same text, an answer scores like the in-process one and stays within the case's budget on
    the server's own time. A value case has no question, and an analyst-plan case (`plan`, issue #23)
    is a scripted live round, which has no URL; a live row checks the resolution only."""
    if case['type'] == 'value' or case.get('plan') is not None:
        return []
    if provider:
        if 'url' not in expect:
            return []
        _, _, failures, _ = via_url(case, expect, turns, redirect_only=True)
        return [f'URL: {f}' for f in failures]
    results, error, failures, ms = via_url(case, expect, turns)
    want = expect.get('raises')
    if want:
        if error is None:
            failures.append(f'answered over GET; expected a refusal /{want}/')
        elif not re.search(want, error, re.I):
            failures.append(f'GET refused with {error[:90]!r}, expected /{want}/')
    elif error is not None:
        failures.append(f'GET refused: {error[:90]!r}')
    elif results and not failures:
        URL_MS[case['id']] = ms
        budget = expect.get('max_ms', DEFAULT_MAX_MS)
        if ms > budget:
            failures.append(f'{ms:.0f} ms server time, budget {budget} ms')
        failures.extend(score(case, expect, results, analysis, allowed, 'offline'))
    return [f'URL: {f}' for f in failures]


def latency_failures(expect, provider, ms):
    """The case's own budget, offline only: `expect.max_ms`, default DEFAULT_MAX_MS. A live or replayed
    run is never timed against it: a `live: true` case shares its expect with its live variant, and
    that time is the provider's."""
    if provider:
        return []
    budget = expect.get('max_ms', DEFAULT_MAX_MS)
    if ms > budget:
        return [f'{ms:.0f} ms, budget {budget} ms']
    return []


def percentiles(values):
    """Nearest-rank p50 and p95 of the case times, in ms; None when there are no values."""
    ordered = sorted(values)
    if not ordered:
        return {'p50': None, 'p95': None}
    return {name: round(ordered[max(math.ceil(q * len(ordered)), 1) - 1], 1) for name, q in (('p50', 0.5), ('p95', 0.95))}


# ---------------------------------------------------------------- score table

def plan_rows(cases, live, provider):
    """(case, provider or None) pairs: every offline case once, plus a live variant per
    provider for `mode: live` cases and cases marked `live: true`. An unrecorded live variant
    shows as a skipped `@live` row, except for a scripted analyst case (`plan`): its offline
    row already puts the round through the real checks, and its live variant runs on demand."""
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
                if not recorded and case.get('plan') is None:
                    plan.append((case, '?'))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='run the live variants against a provider and record fixtures')
    parser.add_argument('--provider', choices=sorted(PROVIDERS), help='openai or anthropic; default: the one whose key is set')
    parser.add_argument('--no-record', action='store_true', help='with --live: do not overwrite fixtures')
    parser.add_argument('--only', action='append', help='run only this case id (repeatable)')
    parser.add_argument('--json', type=Path, help='write results here')
    parser.add_argument('--track', action='store_true', help='append one line per run to evals/history.jsonl (commit, dataset, model, pass rate per type, cost)')
    args = parser.parse_args(argv)

    if args.track and args.only:
        # history.jsonl and last_run.json describe whole-set runs: the /evals page and the main page's badge
        # read the newest line as the score of the set, so a subset must never be appended.
        print('--track records a whole-set run; it cannot be combined with --only')
        return 2
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
                outcome, failures, note, ms, usage = 'skip', [], 'no fixture; run --live to record', 0.0, {}
                case_provider = None
                label = f'{case["id"]}@live'
            else:
                outcome, failures, note, ms, turn_results = run_case_detail(case, case_provider, args.live, not args.no_record)
                label = case['id'] + (f'@{case_provider}' if case_provider else '')
                usage = turn_results[-1]['usage'] if turn_results else {}
            rows.append({'id': label, 'case': case['id'], 'type': case['type'], 'provider': case_provider,
                         'mode': 'live' if case_provider or label.endswith('@live') else 'offline',
                         'outcome': outcome, 'failures': failures, 'note': note, 'ms': round(ms, 1),
                         'url_ms': round(URL_MS[case['id']], 1) if not case_provider and case['id'] in URL_MS else None,
                         'comparisons_withdrawn': usage.get('comparisons_withdrawn')})   # None: no narrative was written
            if args.live and case_provider and outcome != 'skip':   # a real provider answered: its clock is worth keeping
                rows[-1].update(called=True, model_calls=usage.get('model_calls'), first_token_ms=usage.get('first_token_ms'))
    finally:
        close_datasets()

    width = max(len(r['id']) for r in rows) + 2
    print(f'{"case":{width}}{"type":15}{"mode":9}{"result":8}{"ms":>6}  note')
    print('-' * (width + 40 + 40))
    for r in rows:
        print(f'{r["id"]:{width}}{r["type"]:15}{r["mode"]:9}{r["outcome"]:8}{r["ms"]:6.0f}  {r["note"]}')
        for failure in r['failures'][:6]:
            print(f'{"":{width}}  - {failure}')
        if len(r['failures']) > 6:
            print(f'{"":{width}}  - ... {len(r["failures"]) - 6} more')
    print('-' * (width + 40 + 40))
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
        stale = sum(1 for r in live_rows if r['provider'] == name and r['outcome'] == 'skip' and str(r.get('note', '')).startswith('stale recording'))
        if stale:
            parts.append(f'{stale} stale recording(s): re-record with --live --provider {name}')
        if parts:
            print(f'live {name}: ' + '  '.join(parts))
    url_latency = percentiles(URL_MS[r['case']] for r in rows if r['case'] in URL_MS and not r['provider'])
    if url_latency['p50'] is not None:
        print(f'URL path: {sum(1 for r in rows if r["case"] in URL_MS and not r["provider"])} offline cases answered over GET; '
              + '  '.join(f'server {k} {url_latency[k]:.0f} ms' for k in ('p50', 'p95')) + ' (each case within its own budget)')
    latency = offline_latency(rows)
    if latency['p50'] is not None and args.only:   # a subset's percentiles are those cases' own times: reported, not gated
        print('offline latency: ' + '  '.join(f'{k} {latency[k]:.0f} ms' for k in ('p50', 'p95')) + ' (not gated under --only)')
    elif latency['p50'] is not None:
        verdicts = [f'{k} {latency[k]:.0f} ms (budget {LATENCY_BUDGET_MS[k]}: {"ok" if latency[k] <= LATENCY_BUDGET_MS[k] else "OVER"})'
                    for k in ('p50', 'p95')]
        breaches += sum(latency[k] > LATENCY_BUDGET_MS[k] for k in ('p50', 'p95'))
        print('offline latency: ' + '  '.join(verdicts))
    timed = live_latency(rows)
    if timed:
        print('live latency: ' + '  '.join(f'{k} {v:.0f} ms' for k, v in timed.items() if isinstance(v, (int, float)) and k != 'model_calls_max')
              + f'  model calls per case: at most {timed["model_calls_max"]}')
    withdrawn = comparisons_withdrawn(rows)
    if withdrawn:
        print('comparisons withdrawn: ' + '  '.join(f'{label} {n} in {k} narrative{"" if k == 1 else "s"}' for label, (n, k) in withdrawn.items()))
    failed = sum(r['outcome'] == 'fail' and not r['provider'] for r in rows)
    skipped = sum(r['outcome'] == 'skip' for r in rows)
    xfailed = sum(r['outcome'] == 'xfail' for r in rows)
    live_failed = sum(r['outcome'] == 'fail' and bool(r['provider']) for r in rows)
    print(f'{len(rows) - failed - live_failed - skipped - xfailed} passed, {failed} failed, {skipped} skipped'
          + (f', {xfailed} known defect' if xfailed else '') + (f', {live_failed} live failed' if live_failed else ''))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))
    if args.track:
        line = history_line(rows, failed + breaches)
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open('a') as out:
            out.write(json.dumps(line, separators=(',', ':')) + '\n')
        LAST_RUN.write_text(json.dumps(last_run(rows, line), indent=1) + '\n')
        print(f'tracked in {HISTORY.relative_to(ROOT)} (rows in {LAST_RUN.relative_to(ROOT)}): ' + ', '.join(f'{k} {v["pass"]}/{v["pass"] + v["fail"]}' for k, v in line['offline'].items())
              + (f'; latency p50 {line["latency_ms"]["p50"]:.0f} ms, p95 {line["latency_ms"]["p95"]:.0f} ms' if line['latency_ms']['p50'] is not None else '')
              + ''.join(f'; {p} ({v["model"]}): ' + ', '.join(f'{k} {c["pass"]}/{c["pass"] + c["fail"]}' for k, c in v['types'].items()) for p, v in line['live'].items()))
    return failed + breaches


HISTORY = ROOT / 'evals' / 'history.jsonl'


LAST_RUN = ROOT / 'evals' / 'last_run.json'


def last_run(rows, line):
    """The rows behind the newest history line: id, case, type, provider, outcome and ms per row.
    `ran_at` and `commit` are that line's, so a reader can tell the two files belong together; the
    /evals page shows each case's result from here without running anything."""
    return {'ran_at': line['ran_at'], 'commit': line['commit'], 'dataset': line['dataset'],
            'rows': [{k: r[k] for k in ('id', 'case', 'type', 'provider', 'mode', 'outcome', 'ms', 'note')} for r in rows]}


def offline_latency(rows):
    """p50 and p95 of the offline cases that ran (skips did not run, so they carry no time)."""
    return percentiles(r['ms'] for r in rows if not r['provider'] and r['outcome'] != 'skip')


def live_latency(rows):
    """Wall time of the rounds a real provider answered in this run (`--live`): p50 and p95 of the whole
    round, the median time to the first narrative token, and the most model calls any case made.
    None when no provider was called; replays are never timed."""
    called = [r for r in rows if r.get('called')]
    if not called:
        return None
    out = dict(percentiles(r['ms'] for r in called))
    first = [r['first_token_ms'] for r in called if isinstance(r.get('first_token_ms'), (int, float))]
    if first:
        out['first_token_p50'] = percentiles(first)['p50']
    out['model_calls_max'] = max((r.get('model_calls') or 0) for r in called)
    return out


def comparisons_withdrawn(rows):
    """Issue #79: paragraphs the stream validator withdrew because their ordering words ("fastest",
    "below", "rose") contradicted the values they cite, summed per kind of run, with the number of
    narratives written: {'offline': (n, narratives), 'replayed anthropic': ..., 'live anthropic': ...}."""
    out = {}
    for r in rows:
        if r.get('comparisons_withdrawn') is None:
            continue
        label = 'offline' if not r['provider'] else f'{"live" if r.get("called") else "replayed"} {r["provider"]}'
        n, k = out.get(label, (0, 0))
        out[label] = (n + r['comparisons_withdrawn'], k + 1)
    return out


def history_line(rows, exit_status):
    """One record per run: enough to tell whether a change in the planner, the prompt or the
    data moved the score, and whether it moved the clock. Live rows are scored against the
    recordings' model; `latency_ms` is the offline p50 / p95."""
    def tally(subset):
        out = {}
        for r in subset:
            counts = out.setdefault(r['type'], {'pass': 0, 'fail': 0, 'skip': 0})
            counts[r['outcome'] if r['outcome'] in counts else 'skip'] += 1   # xfail counts as a skip
        return out
    try:
        commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    live = {}
    for provider in sorted({r['provider'] for r in rows if r['provider']}):
        models, cost = set(), 0.0
        for r in rows:
            if r['provider'] != provider:
                continue
            path = fixture_path(r['case'], provider)
            if path.exists():
                recording = json.loads(path.read_text())
                cost += float(recording.get('recorded_cost_usd') or 0)
                models.update(e.get('model') for e in recording.get('exchanges', []) if e.get('model'))
        live[provider] = {'model': '/'.join(sorted(models)) or None, 'recorded_cost_usd': round(cost, 5),
                          'types': tally(r for r in rows if r['provider'] == provider)}
    return {'ran_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), 'commit': commit, 'dataset': dataset_version()[:12],
            'cases': len(rows), 'offline': tally(r for r in rows if not r['provider']), 'latency_ms': offline_latency(rows),
            'live': live, **({'live_ms': live_latency(rows)} if live_latency(rows) else {}), 'exit': exit_status}


if __name__ == '__main__':
    sys.exit(main())
