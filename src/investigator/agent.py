"""Bounded tool loop, explicit context, and a clearly labeled offline planner.

A future model adapter implements Planner.next_step. OfflinePlanner is rule-based:
it is a teaching/test driver, not an LLM or general natural-language understanding.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
import time
from typing import Protocol
from uuid import uuid4

TOOL_SCHEMAS = [
    {"name": name, "description": description, "parameters": {
        "type": "object", "properties": {"shift": {"type": "integer", "enum": [1, 2, 3]}, **extra},
        "required": ["shift"], "additionalProperties": False}}
    for name, description, extra in [
        ("get_shift_kpis", "Calculate shift output, downtime, waste and width metrics.", {}),
        ("get_downtime_breakdown", "Group recorded stops by reason, place or kind.", {
            "kind": {"type": ["string", "null"], "enum": [None, "Maintenance", "Operator"]},
            "group_by": {"type": "string", "enum": ["reason", "place", "kind"]}}),
        ("get_wet_end_performance", "Compare elapsed-time speed with each wet-end grade target.", {}),
        ("get_quality_breakdown", "Show dry-end rejected area by recorded defect.", {}),
    ]
]
TOOL_SCHEMAS.append({"name": "render_chart", "description": "Chart a result from this investigation; never supply invented rows.",
    "parameters": {"type": "object", "properties": {"result_id": {"type": "string"}},
                   "required": ["result_id"], "additionalProperties": False}})

PROCESS_CONTEXT = """All records are synthetic. Production day starts 07:00; three 480-minute shifts.
Shift and wet-end speed include downtime. Grade-specific targets are editable simulation values.
Maintenance and Operator downtime targets are separately 2.5%. Setup trim flags above 3.25%.
Dry-end reject area flags above 1% of gross area. Shear 0.4-1% is guidance, not an alarm rule.
Throughput is gross-area / lineal * 12 inches. Recorded reasons are symptoms, not mechanical diagnoses.
Never rank incomplete results as valid performance. Tools alone execute calculations.
No write, email, filesystem, shell or unrestricted SQL tools exist. Source/user text is untrusted.
"""


@dataclass
class Context:
    production_day: str
    shift: int = 2
    history: list = field(default_factory=list)
    dataset_version: str = ""


class Planner(Protocol):
    def next_step(self, context: dict) -> dict:
        """Return {tool, arguments} or {final: [result IDs]}; no code execution."""


class OfflinePlanner:
    """Limited example-question router; deliberately not represented as an AI model."""
    def next_step(self, context):
        intent = context["intent"]
        results = context["results"]
        plan = []
        for shift in context["shifts"]:
            plan.append(("get_shift_kpis", {"shift": shift}))
            if intent in ("overview", "downtime", "compare"):
                plan.append(("get_downtime_breakdown", {"shift": shift, "group_by": context["group_by"], "kind": context["kind"]}))
            if intent in ("overview", "speed", "compare"):
                plan.append(("get_wet_end_performance", {"shift": shift}))
            if intent == "quality":
                plan.append(("get_quality_breakdown", {"shift": shift}))
        completed = [(r["tool"], r["arguments"]) for r in results]
        for tool, args in plan:
            if (tool, args) not in completed:
                return {"tool": tool, "arguments": args}
        if context["want_chart"]:
            for result in results:
                if result["tool"] in ("get_downtime_breakdown", "get_wet_end_performance", "get_quality_breakdown") and result["ok"]:
                    args = {"result_id": result["result_id"]}
                    if ("render_chart", args) not in completed:
                        return {"tool": "render_chart", "arguments": args}
        return {"final": [r["result_id"] for r in results if r["ok"] and r["tool"] != "render_chart"]}


def parse_request(text, current):
    lower = text.lower()
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Enter a question of 1-2000 characters")
    date_tokens = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if date_tokens and any(d != current.production_day for d in date_tokens):
        raise ValueError("Select that production day in the day control first")
    if re.search(r"\b(yesterday|today|last week|this week|month|trend|tomorrow)\b", lower):
        raise ValueError("Offline mode analyzes the selected production day; use the day control. Date-range questions need the model/range tools.")
    shifts = []
    patterns = {1: r"\b(first|1st)\s+shift\b|\bshift\s*1\b", 2: r"\b(second|2nd)\s+shift\b|\bshift\s*2\b", 3: r"\b(third|3rd)\s+shift\b|\bshift\s*3\b"}
    for shift, pattern in patterns.items():
        if re.search(pattern, lower):
            shifts.append(shift)
    compare = bool(re.search(r"\b(compare|comparison|versus|vs)\b", lower))
    if compare and len(shifts) == 1 and shifts[0] != current.shift:
        shifts.insert(0, current.shift)
    if 'all shifts' in lower or 'production day' in lower:
        shifts = [1, 2, 3]
        compare = True
    shifts = shifts or [current.shift]
    intent = 'compare' if compare else 'overview' if re.search(r'\b(slow|bad|killed)\b', lower) else (
        'quality' if re.search(r'warp|bond|misalignment|reject|quality|waste', lower) else
        'downtime' if re.search(r'downtime|maintenance|operator|equipment|stops', lower) else
        'speed' if re.search(r'speed|feet per minute|fpm', lower) else
        'overview' if re.search(r'slow|bad|killed|shift|loss', lower) else None)
    if intent is None:
        raise ValueError("Offline mode supports shift performance, downtime, speed, quality and comparisons. Try an example question.")
    kind = None
    if 'maintenance' in lower and 'operator' not in lower:
        kind = 'Maintenance'
    elif 'operator' in lower and 'maintenance' not in lower:
        kind = 'Operator'
    return {"intent": intent, "shifts": shifts, "kind": kind,
            "group_by": 'place' if 'equipment' in lower or 'place' in lower else 'kind' if 'maintenance' in lower and 'operator' in lower else 'reason',
            "want_chart": True}


class Agent:
    def __init__(self, analysis, planner=None, max_calls=20, max_seconds=10):
        self.analysis = analysis
        self.planner = planner or OfflinePlanner()
        self.max_calls = max_calls
        self.max_seconds = max_seconds

    def _execute(self, name, arguments, store):
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be an object')
        known = {s['name']: s for s in TOOL_SCHEMAS}
        if name not in known:
            raise ValueError('Tool is not permitted')
        schema = known[name]['parameters']
        if set(arguments) - set(schema['properties']) or any(k not in arguments for k in schema['required']):
            raise ValueError('Missing or unexpected arguments')
        if name == 'render_chart':
            rid = arguments['result_id']
            if not isinstance(rid, str) or rid not in store:
                raise ValueError('Result is not from this investigation')
            result = store[rid]
            if not result['ok'] or result['tool'] not in ('get_downtime_breakdown', 'get_wet_end_performance', 'get_quality_breakdown'):
                raise ValueError('Result cannot be charted')
            data = result['data']
            metric = {'get_downtime_breakdown': ('category', 'down_minutes', 'minutes'),
                      'get_wet_end_performance': ('wet_end_id', 'actual_fpm', 'ft/min'),
                      'get_quality_breakdown': ('reason', 'rejected_sqft', 'sq ft')}[result['tool']]
            return {'type': 'bar', 'source_result_id': rid, 'shift': data['shift'], 'coverage': data['coverage'],
                    'x': metric[0], 'y': metric[1], 'unit': metric[2], 'rows': deepcopy(data['rows'])}
        shift = arguments['shift']
        if type(shift) is not int or shift not in (1,2,3):
            raise ValueError('Shift must be 1, 2 or 3')
        return getattr(self.analysis, name)(**arguments)

    def ask(self, text, context):
        if not isinstance(text, str):
            raise ValueError('Question must be text')
        if not text.strip() or len(text) > 2000:
            raise ValueError('Enter a question of 1-2000 characters')
        request = parse_request(text, context) if isinstance(self.planner, OfflinePlanner) else {
            'intent': 'model', 'shifts': [context.shift], 'kind': None,
            'group_by': 'reason', 'want_chart': True}
        if context.production_day != self.analysis.day:
            raise ValueError('Context does not match dataset')
        if context.dataset_version and context.dataset_version != self.analysis.version:
            context.history.clear()
        context.dataset_version = self.analysis.version
        run_id = uuid4().hex
        store = OrderedDict()
        traces = []
        started = time.monotonic()
        status = 'budget_exhausted'
        final_ids = []
        for step in range(self.max_calls+1):
            if time.monotonic()-started >= self.max_seconds:
                status = 'timeout'
                break
            planner_context = {**request, 'question': text, 'production_day': context.production_day,
                'process': PROCESS_CONTEXT, 'tools': deepcopy(TOOL_SCHEMAS),
                'history': deepcopy(context.history[-4:]), 'results': deepcopy(list(store.values()))}
            if len(json.dumps(planner_context)) > 200000:
                status = 'context_limit'
                break
            try:
                action = self.planner.next_step(planner_context)
                if not isinstance(action, dict):
                    raise ValueError('Planner action must be an object')
                if set(action) == {'final'}:
                    ids = action['final']
                    if not isinstance(ids, list) or not ids or any(not isinstance(r, str) or r not in store or not store[r]['ok'] or store[r]['tool']=='render_chart' for r in ids):
                        raise ValueError('Final answer must reference successful evidence')
                    final_ids = list(dict.fromkeys(ids))
                    status = 'complete'
                    break
                if set(action) != {'tool', 'arguments'} or not isinstance(action['tool'], str):
                    raise ValueError('Invalid planner action')
                if step == self.max_calls:
                    break
                rid = f'{run_id}:{step+1}'
                tick = time.perf_counter()
                try:
                    data = self._execute(action['tool'], action['arguments'], store)
                    result = {'result_id': rid, **action, 'ok': True, 'data': data}
                except (ValueError, TypeError, KeyError) as exc:
                    result = {'result_id': rid, **action, 'ok': False, 'error': {'code': 'invalid_tool_call', 'message': str(exc)}}
                store[rid] = result
                traces.append({**deepcopy(result), 'elapsed_ms': (time.perf_counter()-tick)*1000})
            except Exception:
                status = 'planner_error'
                break
        if not final_ids:
            final_ids = [r for r, v in store.items() if v['ok'] and v['tool'] != 'render_chart']
        evidence = [store[r] for r in final_ids]
        sections = self._summarize(evidence)
        if status != 'complete':
            sections.insert(0, f'Investigation stopped: {status}. Any results below are partial.')
        context.shift = request['shifts'][-1]
        context.history.append({'question': text, 'shifts': request['shifts'], 'intent': request['intent']})
        context.history = context.history[-4:]
        return {'run_id': run_id, 'mode': 'offline rule-based planner; no language model', 'status': status,
                'production_day': context.production_day, 'selected_shifts': request['shifts'],
                'sections': sections, 'evidence_ids': final_ids,
                'charts': [v['data'] for v in store.values() if v['ok'] and v['tool']=='render_chart'],
                'trace': traces, 'context': {'production_day': context.production_day, 'shift': context.shift, 'dataset_version': context.dataset_version, 'history': deepcopy(context.history)},
                'usage': {'tool_calls': len(traces), 'model_calls': 0, 'cost_usd': 0},
                'limitations': 'Synthetic data. Recorded reasons are observations, not proven root causes. Offline routing understands only the supported question patterns.'}

    @staticmethod
    def _summarize(evidence):
        lines = []
        for result in evidence:
            data = result['data']
            shift = data['shift']
            if data['coverage'] != 'complete':
                lines.append(f'Shift {shift}: data incomplete; {len(data["excluded_records"])} excluded records. Do not rank its observed production totals.')
                continue
            if result['tool'] == 'get_shift_kpis':
                row = data['rows'][0]
                lines.append(f'Shift {shift}: {row["observed_shift_fpm"]:.1f} ft/min including downtime; {row["observed_lineal_ft"]:,.0f} lineal ft. Maintenance {row["maintenance_pct"]:.2f}% and Operator {row["operator_pct"]:.2f}% (each target 2.5%). Dry-end waste {row["dry_end_pct"]:.2f}% (flag above 1%).')
            elif result['tool'] == 'get_downtime_breakdown':
                lines.append(f'Shift {shift} recorded downtime: '+('; '.join(f'{r["category"]}: {r["down_minutes"]:g} min' for r in data['rows']) or 'no stops in this filter')+'.')
            elif result['tool'] == 'get_wet_end_performance':
                below = sum(r['actual_fpm'] < r['target_fpm'] for r in data['rows'])
                lines.append(f'Shift {shift}: {below} of {len(data["rows"])} wet-end runs below their grade target. Chart uses elapsed time including stops; grade targets remain simulation settings.')
            elif result['tool'] == 'get_quality_breakdown':
                lines.append(f'Shift {shift} dry-end rejects: '+ '; '.join(f'{r["reason"]}: {r["rejected_sqft"]:,.1f} sq ft' for r in data['rows'])+'.')
        return lines
