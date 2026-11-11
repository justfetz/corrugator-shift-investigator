"""The /evals page: the golden set, its last tracked run and the score history, as data.

Everything here is read from the `evals/` folder as shipped (the repository checkout, or the
folder `INVESTIGATOR_EVALS` names in the image): `golden.json`, `golden_values.json`, the
recorded fixtures, `history.jsonl`, `last_run.json` and the narrative judge's two files
(`judge_last_run.json`, `judge_calibration.json`). A case is run by the same `evals/run.py` the
regression gate uses, loaded from that folder; a live case is always *replayed* from its recorded
fixture (`live=False`), so no path from the page reaches a model.

One thing is written, and only by the local workbench: the owner's grades of recorded narratives
(`save_grade`, the /evals/grade page of issue #25) go to `judge_calibration.json`. The hosted demo
has no grading route at all.
"""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

from . import judge as judging

ALL_TTL_SECONDS = 60   # a whole-set run is answered from the previous one for this long


def evals_dir():
    """The folder holding run.py and the golden files, or None when it was not shipped."""
    named = os.environ.get('INVESTIGATOR_EVALS')
    folder = Path(named) if named else Path(__file__).resolve().parents[2] / 'evals'
    return folder if (folder / 'run.py').is_file() and (folder / 'golden.json').is_file() else None


def _words(values):
    values = [str(v) for v in values]
    if not values:
        return 'none'
    return values[0] if len(values) == 1 else ', '.join(values[:-1]) + ' and ' + values[-1]


def _window(window):
    period = {'fiscal': '4-4-5 period', 'day': 'day'}.get(window.get('period'), window.get('period'))
    return f'the {period} of {window.get("anchor")}' if window.get('period') == 'day' else f'the {period} containing {window.get("anchor")}'


def describe_call(tool, arguments):
    """A tool call in plain words: "get_downtime_breakdown for shift 2 by reason"."""
    parts = [tool]
    args = dict(arguments or {})
    if 'metric' in args:
        parts.append(f'of {args.pop("metric")}')
    if 'shift' in args:
        shift = args.pop('shift')
        parts.append('for all shifts' if shift is None else f'for shift {shift}')
    if 'group_by' in args:
        parts.append(f'by {args.pop("group_by")}')
    if 'slice_by' in args:
        parts.append(f'sliced by {args.pop("slice_by")}')
    if 'measure' in args:
        parts.append(f'measured in {args.pop("measure")}')
    kind = args.pop('kind', None)
    if kind is not None:
        parts.append(f'({kind} only)')
    if 'window' in args:
        parts.append('over ' + _window(args.pop('window')))
    if 'current_window' in args:
        parts.append('over ' + _window(args.pop('current_window')))
    if 'previous_window' in args:
        parts.append('against ' + _window(args.pop('previous_window')))
    compare = args.pop('compare', None)
    if compare is True:
        parts.append('against the previous window')
    if 'weeks' in args:
        parts.append(f'over {args.pop("weeks")} weeks')
    if 'anchor' in args:
        parts.append(f'ending at {args.pop("anchor")}')
    parts.extend(f'with {k} = {json.dumps(v)}' for k, v in args.items())
    return ' '.join(parts)


def _excluded(want):
    bits = []
    if 'count' in want:
        bits.append(f'exactly {want["count"]} excluded record' + ('' if want['count'] == 1 else 's'))
    if 'record_id' in want:
        bits.append(f'record {want["record_id"]} is excluded')
    if 'shift' in want:
        bits.append(f'from shift {want["shift"]}')
    if 'error_match' in want:
        bits.append(f'with an error matching /{want["error_match"]}/')
    if want.get('raw'):
        bits.append('keeping its raw values ' + ', '.join(f'{k} = {v}' for k, v in want['raw'].items()))
    return ' '.join(bits)


def _evidence(rule):
    if rule.get('why'):
        return rule['why']
    text = describe_call(rule['tool'], rule.get('arguments'))
    if 'row' in rule:
        text += ' has a row with ' + ', '.join(f'{k} = {v}' for k, v in rule['row'].items())
    return text


def describe_expect(expect):
    """A case's `expect` block as sentences a visitor can read; one per check the runner makes."""
    lines = []
    if 'raises' in expect:
        lines.append(f'Refused before any tool runs, with a reason matching /{expect["raises"]}/')
    if 'url' in expect:
        lines.append(f'Typed into /q, the question resolves to the address {expect["url"]} (issue #20)')
    if 'status' in expect:
        lines.append(f'Ends with status "{expect["status"]}"')
    if 'status_in' in expect:
        lines.append('Ends with status ' + _words(f'"{s}"' for s in expect['status_in']).replace(' and ', ' or '))
    if 'shifts' in expect:
        lines.append('Covers shift' + ('' if len(expect['shifts']) == 1 else 's') + ' ' + _words(expect['shifts'])
                     if expect['shifts'] else 'Selects no shift (a period answer covers all shifts)')
    for want in expect.get('tools', []):
        lines.append('Calls ' + describe_call(want['tool'], want.get('arguments')))
    if 'tools_any' in expect:
        lines.append('Calls at least one of ' + _words(expect['tools_any']).replace(' and ', ' or '))
    if 'tools_only' in expect:
        lines.append('Calls no tool other than ' + _words(expect['tools_only']))
    if 'tool_calls' in expect:
        lines.append(f'Makes exactly {expect["tool_calls"]} tool call' + ('' if expect['tool_calls'] == 1 else 's'))
    if 'rejected_tool_calls' in expect:
        lines.append(f'At least {expect["rejected_tool_calls"]} tool call is refused by the application')
    if 'charts' in expect:
        want = expect['charts']
        lines.append(f'Draws at least {want.get("min", 0)} chart(s)' if isinstance(want, dict)
                     else ('Draws no chart' if want == 0 else f'Draws exactly {want} chart' + ('' if want == 1 else 's')))
    if 'chart_units' in expect:
        lines.append('Charts in ' + _words(expect['chart_units']) + ', in that order')
    if 'tables' in expect:
        lines.append('Shows the table' + ('' if len(expect['tables']) == 1 else 's') + ' ' + _words(expect['tables'])
                     if expect['tables'] else 'Shows no table')
    if 'coverage' in expect:
        lines.append(f'Every tool result reports coverage "{expect["coverage"]}"')
    if 'row_count' in expect:
        lines.append(f'The tool returns exactly {expect["row_count"]} row' + ('' if expect['row_count'] == 1 else 's'))
    if 'values' in expect:
        fields = sum(len(v) for v in expect['values'].values())
        lines.append(f'{fields} value' + ('' if fields == 1 else 's') + f' across {len(expect["values"])} row'
                     + ('' if len(expect['values']) == 1 else 's') + ' equal a second, plain computation over the records (within the stated tolerance)')
    if 'excluded_records' in expect:
        wants = expect['excluded_records'] if isinstance(expect['excluded_records'], list) else [expect['excluded_records']]
        lines.extend('Data quality: ' + _excluded(w) for w in wants)
    for rule in expect.get('evidence', []):
        lines.append('Evidence found: ' + _evidence(rule))
    if expect.get('prose_numbers'):
        lines.append('The answer states the found numbers: ' + _words(
            f'{n["field"]} of evidence {n["evidence"] + 1}' for n in expect['prose_numbers']))
    for pattern in expect.get('forbidden', []):
        lines.append(f'Never asserts a cause the evidence lacks: /{pattern}/')
    for pattern in [expect['sections_match']] if isinstance(expect.get('sections_match'), str) else expect.get('sections_match', []):
        lines.append(f'An evidence line matches /{pattern}/')
    if 'shape' in expect:
        low, high = expect['shape'].get('bullets', [3, 5])
        look_low, look_high = expect['shape'].get('look', [2, 3])
        lines.append(f'Answer shape: one headline' + (f' matching /{expect["shape"]["headline_match"]}/' if 'headline_match' in expect['shape'] else '')
                     + f', {low}-{high} finding bullets with one bold figure each, and {look_low}-{look_high} where-to-look questions (issue #75)')
    if 'sections_not_match' in expect:
        lines.append(f'No evidence line matches /{expect["sections_not_match"]}/')
    if 'narrative_status' in expect:
        lines.append(f'The model\'s interpretation is "{expect["narrative_status"]}"')
    if 'narrative_not_match' in expect:
        lines.append(f'The interpretation never matches /{expect["narrative_not_match"]}/')
    if 'narrative_match' in expect:
        lines.append(f'An interpretation paragraph matches /{expect["narrative_match"]}/')
    if 'comparisons_withdrawn' in expect:
        n = expect['comparisons_withdrawn']
        lines.append(f'{n} paragraph' + (' is' if n == 1 else 's are') + ' withdrawn because the ordering it asserts contradicts the values it cites, '
                     'and one limitation line says so (issue #79)' if n else
                     'No comparison is withdrawn: every ordering the interpretation asserts agrees with the values it cites (issue #79)')
    if 'history_length' in expect:
        lines.append(f'Carries {expect["history_length"]} questions of history to the next turn')
    if 'max_cost_usd' in expect:
        lines.append(f'Costs at most ${expect["max_cost_usd"]} when a model is used')
    if 'max_ms' in expect:
        lines.append(f'Answers offline within {expect["max_ms"]} ms')
    return lines


def _exchange(exchange):
    """One recorded model response, reduced to what it chose: tool calls, text, tokens."""
    response = exchange.get('response') or {}
    calls, text = [], []
    for block in response.get('content') or []:           # Anthropic messages
        if block.get('type') == 'tool_use':
            calls.append({'tool': block.get('name'), 'arguments': block.get('input')})
        elif block.get('type') == 'text' and block.get('text'):
            text.append(block['text'])
    for item in response.get('output') or []:             # OpenAI responses
        if item.get('type') == 'function_call':
            try:
                arguments = json.loads(item.get('arguments') or '{}')
            except ValueError:
                arguments = item.get('arguments')
            calls.append({'tool': item.get('name'), 'arguments': arguments})
    usage = response.get('usage') or {}
    return {'model': exchange.get('model'), 'http_status': exchange.get('status', 200), 'calls': calls, 'text': ' '.join(text)[:2000],
            'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens')}


class EvalBoard:
    """The golden set as the page shows it, plus offline / replayed runs of its cases."""

    def __init__(self, folder, dataset_hash=None):
        """`dataset_hash` defaults to the hash the value goldens were generated for: the golden set is
        scored against the window ending run.GOLDEN_AS_OF, never against the serving store's window
        (issue #35), so that is the dataset a recording must match to be replayed."""
        self.folder = Path(folder)
        spec = importlib.util.spec_from_file_location('investigator_golden_run', self.folder / 'run.py')
        self.runner = importlib.util.module_from_spec(spec)
        bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # loading run.py must not leave a __pycache__ in evals/
        try:
            spec.loader.exec_module(self.runner)
        finally:
            sys.dont_write_bytecode = bytecode
        self.dataset_hash = dataset_hash if dataset_hash is not None else self.runner.golden_hash()
        self.cases = {case['id']: case for case in self.runner.load_cases()}
        self.plan = [(case, provider) for case, provider in self.runner.plan_rows(list(self.cases.values()), False, None)]
        self.all_lock = threading.Lock()
        self._all = None            # (monotonic time, payload) of the last whole-set run
        self._narratives = None     # the recorded and scripted narratives the owner grades, replayed once per process
        self._board = self._build()

    # ------------------------------------------------------------ static data

    @staticmethod
    def row_id(case, provider):
        return case['id'] + ('' if provider is None else '@live' if provider == '?' else f'@{provider}')

    def _history(self):
        path, out = self.folder / 'history.jsonl', []
        if not path.is_file():
            return out
        for raw in path.read_text().splitlines():
            if not raw.strip():
                continue
            line = json.loads(raw)
            tallies = list(line.get('offline', {}).values()) + [t for p in line.get('live', {}).values() for t in p.get('types', {}).values()]
            passed, failed = sum(t['pass'] for t in tallies), sum(t['fail'] for t in tallies)
            total = line.get('cases') or sum(t['pass'] + t['fail'] + t['skip'] for t in tallies)
            latency = line.get('latency_ms') or {}
            out.append({'ran_at': line.get('ran_at'), 'commit': line.get('commit'), 'dataset': line.get('dataset'),
                        'passed': passed, 'failed': failed, 'skipped': total - passed - failed, 'total': total,
                        'pass_rate_pct': round(100 * passed / total, 1) if total else None,
                        'p50_ms': latency.get('p50'), 'p95_ms': latency.get('p95'),
                        'recorded_cost_usd': round(sum(p.get('recorded_cost_usd') or 0 for p in line.get('live', {}).values()), 5),
                        'models': sorted({p['model'] for p in line.get('live', {}).values() if p.get('model')}), 'exit': line.get('exit'),
                        'judge_cost_per_case_usd': (line.get('judge') or {}).get('cost_per_case_usd')})
        return out

    def _judge(self, history, rerecord=False):
        """The narrative judge as the page shows it (issue #25): the rubric, the newest judged run's scores when
        `judge_last_run.json` belongs to a tracked line, and its agreement with the owner's grades. The grades' own
        reasons stay in the file; the page gets their count (every saved grade, as the grading page counts them) and
        the agreement. `rerecord`: a live recording is stale, so the target of 20 waits on re-recording (#71)."""
        stored = judging.load_json(self.folder / judging.LAST_RUN_FILE, None)
        tracked = {(h['ran_at'], h['commit']) for h in history}
        if stored and (stored.get('ran_at'), stored.get('commit')) not in tracked:
            stored = None   # a judged run nobody tracked in history.jsonl is not shown as the judge's result
        grades = judging.load_grades(self.folder)
        rows = stored['rows'] if stored else []
        return {'rubric': [{k: c[k] for k in ('key', 'name', 'criterion', 'steps')} for c in judging.RUBRIC],
                'rubric_digest': judging.rubric_digest(), 'target': judging.CALIBRATION_TARGET, 'target_text': judging.target_text(rerecord),
                'bar_pct': judging.AGREEMENT_BAR_PCT,
                'last': ({'ran_at': stored['ran_at'], 'commit': stored['commit'], **stored['summary'],
                          'rubric_current': stored['summary'].get('rubric') == judging.rubric_digest()} if stored else None),
                'rows': [{k: r[k] for k in ('id', 'run', 'turn', 'source', 'question', 'scores', 'reasons', 'cost_usd')} for r in rows],
                'graded': len(grades), 'agreement': judging.agreement(rows, grades)}

    def _last_rows(self, newest):
        """Per-row results of the newest history line, or {} when last_run.json is not that run's."""
        path = self.folder / 'last_run.json'
        if not newest or not path.is_file():
            return {}
        recorded = json.loads(path.read_text())
        if (recorded.get('ran_at'), recorded.get('commit')) != (newest['ran_at'], newest['commit']):
            return {}
        return {row['id']: {'outcome': row['outcome'], 'ms': row['ms'], 'note': row.get('note', '')} for row in recorded.get('rows', [])}

    def _fixture(self, case, provider):
        path = self.runner.fixture_path(case['id'], provider)
        if not path.is_file():
            return None
        recording = json.loads(path.read_text())
        exchanges = recording.get('exchanges', [])
        return {'provider': provider, 'models': sorted({e['model'] for e in exchanges if e.get('model')}),
                'recorded_status': recording.get('recorded_status'), 'recorded_cost_usd': recording.get('recorded_cost_usd'),
                'model_responses': len(exchanges), 'stale': recording.get('dataset_version') != self.dataset_hash,
                'exchanges': [_exchange(e) for e in exchanges]}

    def _case(self, case, last):
        value = case['type'] == 'value'
        turns = case.get('turns') or ([case['question']] if case.get('question') else [])
        runs = []
        for planned, provider in self.plan:
            if planned is not case:
                continue
            rid = self.row_id(case, provider)
            run = {'id': rid, 'mode': 'offline' if provider is None else 'live', 'provider': None if provider in (None, '?') else provider,
                   'last': last.get(rid)}
            if provider not in (None, '?'):
                fixture = self._fixture(case, provider)
                run['recording'] = {k: v for k, v in fixture.items() if k != 'exchanges'} if fixture else None
            runs.append(run)
        out = {'id': case['id'], 'type': case['type'], 'mode': case['mode'], 'also_live': bool(case.get('live')),
               'question': turns[-1] if turns else None, 'turns': turns,
               'call': describe_call(case['tool'], case['arguments']) if value else None,
               'day': case.get('day', self.runner.DEFAULT_DAY), 'scope': 'day' if value else case.get('scope', 'shift'),
               'shift': case.get('shift', 2), 'period': case.get('period', 'day'), 'corrupt': bool(case.get('corrupt')),
               'note': case.get('note'), 'known_defect': case.get('known_defect'),
               'expected': describe_expect(case.get('expect', {})), 'runs': runs}
        if 'expect_live' in case:
            out['expected_live'] = describe_expect(case['expect_live'])
        return out

    def _build(self):
        history = self._history()
        newest = history[-1] if history else None
        last = self._last_rows(newest)
        cases = [self._case(case, last) for case in self.cases.values()]
        # A stale recording replays nothing, so the judge's calibration target waits on re-recording (#71).
        self._rerecord = any((run.get('recording') or {}).get('stale') for case in cases for run in case['runs'])
        by_type = {}
        for case in cases:
            by_type[case['type']] = by_type.get(case['type'], 0) + 1
        badge = None
        if newest:
            badge = f'{newest["passed"]}/{newest["total"]} golden' + (f' · {newest["p50_ms"]:.0f} ms p50' if newest['p50_ms'] is not None else '')
        return {'about': 'The golden set of the Corrugator Shift Investigator: every case, what it expects, its result in the last tracked '
                         'run, and the score history. Synthetic data. A live case is replayed from its recorded fixture; nothing here calls a model.',
                'dataset_hash': self.dataset_hash, 'golden_as_of': self.runner.GOLDEN_AS_OF, 'badge': badge,
                'counts': {'cases': len(cases), 'runs': len(self.plan), 'by_type': by_type,
                           'recordings': sum(1 for c in cases for r in c['runs'] if r.get('recording'))},
                'thresholds': {'live_pass_rate': self.runner.THRESHOLDS, 'offline_latency_ms': self.runner.LATENCY_BUDGET_MS,
                               'case_max_ms': self.runner.DEFAULT_MAX_MS},
                'last_run': newest, 'history': history, 'types': list(self.runner.TYPES), 'cases': cases,
                'judge': self._judge(history, self._rerecord)}

    def board(self, public=False):
        """The page's data. The hosted demo's copy leaves out the judge's reasons: they are model prose that no check
        of this project reads, so they stay on the owner's local workbench; the scores and the agreement are shown."""
        if not public:
            return self._board
        judge = dict(self._board['judge'], rows=[{k: v for k, v in r.items() if k != 'reasons'} for r in self._board['judge']['rows']],
                     reasons='local workbench only')
        return dict(self._board, judge=judge)

    @property
    def summary(self):
        """What /api/config carries for the main page's badge."""
        newest = self._board['last_run']
        if not newest:
            return {'badge': None}
        return {'badge': self._board['badge'], 'passed': newest['passed'], 'total': newest['total'], 'p50_ms': newest['p50_ms'],
                'ran_at': newest['ran_at'], 'commit': newest['commit']}

    # ------------------------------------------------------------ running

    def _resolve(self, row):
        """(case, provider) for a row id such as "why-slow" or "live-why-slow@anthropic"."""
        if not isinstance(row, str):
            raise ValueError('Name a golden case')
        for case, provider in self.plan:
            if self.row_id(case, provider) == row:
                return case, provider
        raise ValueError('No such golden case')

    def _run(self, case, provider):
        """One row, offline or replayed. `live=False` always: a recording is replayed, a model is never called."""
        if provider == '?':
            return 'skip', [], 'no recorded fixture; this page never calls a model', 0.0, []
        return self.runner.run_case_detail(case, provider, live=False, record=False)

    def run_one(self, row, lock):
        case, provider = self._resolve(row)
        with lock:
            try:
                outcome, failures, note, ms, results = self._run(case, provider)
            finally:
                self.runner.close_datasets()
        turns = case.get('turns') or [case.get('question') or f'value golden: {case.get("tool")}']
        out = {'id': row, 'case': case['id'], 'type': case['type'], 'mode': 'offline' if provider is None else 'live',
               'provider': None if provider in (None, '?') else provider, 'outcome': outcome, 'failures': failures, 'note': note,
               'ms': round(ms, 1), 'ran_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'model_called': False,
               'turns': [{'question': turns[i] if i < len(turns) else None, 'status': r['status'], 'mode': r['mode'], 'sections': r['sections'],
                          'narrative': r.get('narrative', []), 'usage': r['usage'], 'trace': r['trace']} for i, r in enumerate(results)]}
        if out['provider']:
            out['recording'] = self._fixture(case, provider)
        return out

    def run_all(self, lock):
        """Every row once, the lock taken per case so questions from other visitors interleave.
        A second request inside ALL_TTL_SECONDS gets the previous run (marked `cached`)."""
        with self.all_lock:
            if self._all and time.monotonic() - self._all[0] < ALL_TTL_SECONDS:
                return {**self._all[1], 'cached': True}
            rows = []
            try:
                for case, provider in self.plan:
                    with lock:
                        outcome, failures, note, ms, _ = self._run(case, provider)
                    rows.append({'id': self.row_id(case, provider), 'case': case['id'], 'type': case['type'],
                                 'provider': None if provider in (None, '?') else provider, 'outcome': outcome,
                                 'failures': failures, 'note': note, 'ms': round(ms, 1)})
            finally:
                with lock:
                    self.runner.close_datasets()
            count = lambda outcome: sum(r['outcome'] == outcome for r in rows)
            payload = {'ran_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'total': len(rows), 'passed': count('pass'),
                       'failed': count('fail'), 'skipped': count('skip') + count('xfail'),
                       'latency_ms': self.runner.offline_latency(rows), 'model_called': False, 'cached': False, 'rows': rows}
            self._all = (time.monotonic(), payload)
            return payload

    # ------------------------------------------------------------ grading (local workbench only, issue #25)

    def narratives(self, lock):
        """Every narrative the golden set's runs write without a model: each recorded live run replayed, and each
        scripted analyst plan that carries a narrative. The same ids and digests `evals/run.py --judge` scores,
        so a grade and a judged score of the same text meet in `judging.agreement`. Built once per process."""
        if self._narratives is None:
            found = []
            for case, provider in self.plan:
                if provider == '?' or (provider is None and not (case.get('plan') is not None and case.get('narrative'))):
                    continue
                with lock:
                    try:
                        _, _, _, _, results = self.runner.run_case_detail(case, provider, live=False, record=False)
                    finally:
                        self.runner.close_datasets()
                turns = case.get('turns') or [case.get('question') or '']
                found.extend(judging.narrative_cases(self.row_id(case, provider), turns, results,
                                                     'scripted plan' if provider is None else f'recorded {provider}'))
            self._narratives = found
        return self._narratives

    def grading(self, lock):
        """The grading page's data: each narrative with what the owner already said about it. The judge's own
        scores are left out on purpose, so the owner's grades are not anchored on them."""
        grades = {g.get('id'): g for g in judging.load_grades(self.folder)}
        items = []
        for n in self.narratives(lock):
            grade = grades.get(n['id'])
            items.append({**{k: n[k] for k in ('id', 'run', 'turn', 'source', 'question', 'scope', 'narrative', 'digest')},
                          'evidence': n['context'], 'grade': grade, 'stale': bool(grade) and grade.get('digest') != n['digest']})
        return {'rubric': [{k: c[k] for k in ('key', 'name', 'criterion', 'steps')} for c in judging.RUBRIC],
                **self._grade_counts(grades.values(), {n['id']: n['digest'] for n in self.narratives(lock)}),
                'narratives': items, 'file': 'evals/' + judging.CALIBRATION_FILE}

    def _grade_counts(self, grades, digests):
        """`graded` is every saved grade, the number /evals shows too; `set_aside` the ones not of a narrative's current
        text (changed, or not replayable while the recordings are stale), which agreement leaves out."""
        grades = list(grades)
        current = sum(1 for g in grades if digests.get(g.get('id')) == g.get('digest'))
        return {'graded': len(grades), 'set_aside': len(grades) - current, 'target': judging.CALIBRATION_TARGET,
                'target_text': judging.target_text(len(digests) < judging.CALIBRATION_TARGET)}

    def save_grade(self, payload, lock):
        """Validate one grade against the narrative the page showed and write it to judge_calibration.json."""
        digests = {n['id']: n['digest'] for n in self.narratives(lock)}
        grade = judging.validate_grade(payload, digests)
        with self.all_lock:   # one writer at a time; the file is replaced whole
            grades = judging.save_grade(self.folder, grade)
            self._board['judge'] = self._judge(self._board['history'], self._rerecord)   # /evals counts the new grade at once
        return {'saved': grade, **self._grade_counts(grades, digests)}
