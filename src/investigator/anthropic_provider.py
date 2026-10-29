"""Anthropic Messages adapter for the hosted demo: no SDK, no retries, no saved keys.

Same Planner.next_step contract as OpenAIPlanner. The server holds the key in public
mode; locally a visitor key arrives through the X-Anthropic-Key header for one request.
"""
from copy import deepcopy
import http.client
import json
import math
import time

from .openai_provider import (Budget, NARRATIVE_INSTRUCTIONS, PLANNER_INSTRUCTIONS,  # noqa: F401 (Budget re-exported)
                              ProviderError, compact_result)

API_HOST = 'api.anthropic.com'
API_PATH = '/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MODEL = 'claude-haiku-5-5'
# USD per million tokens, first-party API list prices read on 2026-10-22. Display estimates
# only: the Anthropic console spend limit is the hard stop. Haiku 5.5 is the <=100K-token
# prompt rate card; this adapter never sends more than 64 KB (well under 20K tokens) per request.
PRICING = {
    'claude-haiku-5-5': {'input': 0.10, 'output': 0.50},
    'claude-sonnet-5-5': {'input': 2.00, 'output': 10.00},
}
MAX_TOKENS = 2048          # planning step; thinking (adaptive, on by default) counts toward this cap
NARRATIVE_MAX_TOKENS = 4096  # the write_narrative call carries paragraphs plus citations; 1024 was cut off every time
# Serialized request ceiling. A four-step investigation over the period tools (wet-end
# performance plus a setup matrix) passed 24 KB on the fourth call, so the cap is 64 KB and
# older tool results are trimmed to a stub first (_fit); only a request that still does not
# fit after trimming is refused.
MAX_REQUEST_BYTES = 64000
MAX_RESPONSE_BYTES = 512000
# Pre-call budget reservation, sized from the request actually being sent rather than a flat
# guard: about 2x the list-price worst case for this request (input estimated at a
# conservative 3 bytes per token, output at the call's max_tokens). Never refunded.
BYTES_PER_TOKEN = 3
RESERVE_MULTIPLIER = 2
RESERVE_FLOOR_MICRO_USD = 500
COMPACTED_NOTE = 'Earlier tool result trimmed to fit the request limit; its evidence stays citable by result_id.'
MAX_CALLS = 10
LIFETIME_SECONDS = 60
SOCKET_TIMEOUT = 20

# Strict mode is OFF for every tool. Two API limits bit on 2026-10-22: at most 20 strict tools per
# request, and a total "compiled grammar" budget that 19 strict schemas with nested window objects
# already exceeded ("The compiled grammar is too large"). The application validates every tool
# call's arguments itself (agent.py refuses a bad shift, window, enum or type and the model gets
# the error back; finish and cannot_answer are checked in next_step), so strict mode was a
# convenience, not the gate. STRICT_TOOLS can name a few small schemas to opt back in.
STRICT_TOOLS = frozenset()
CONTROL_TOOLS = [
    {'name': 'finish', 'description': 'Finish with successful evidence result IDs.', 'strict': False,
     'input_schema': {'type': 'object', 'properties': {'result_ids': {'type': 'array', 'items': {'type': 'string'}}},
                      'required': ['result_ids'], 'additionalProperties': False}},
    {'name': 'cannot_answer', 'description': 'Explain that the requested evidence or capability is unavailable.', 'strict': False,
     'input_schema': {'type': 'object', 'properties': {'reason': {'type': 'string'}}, 'required': ['reason'], 'additionalProperties': False}},
]
TOOL_RULE = ('\nRespond only by calling exactly one tool per turn: an investigation tool, finish, or cannot_answer. '
             'Plain text without a tool call ends the investigation with the evidence gathered so far.')


def resolve_model(name):
    """Validate the configured model; only the two priced models are allowed."""
    if name in (None, ''):
        return DEFAULT_MODEL
    if name not in PRICING:
        raise ProviderError('ANTHROPIC_MODEL must be one of: ' + ', '.join(sorted(PRICING)))
    return name


def reserve_micro_usd(model, request_bytes, max_tokens):
    """Micro-dollars to reserve before a call of this size; floor keeps tiny calls from rounding to nothing."""
    rates = PRICING[model]
    estimate = (request_bytes/BYTES_PER_TOKEN)*rates['input'] + max_tokens*rates['output']
    return max(RESERVE_FLOOR_MICRO_USD, int(math.ceil(RESERVE_MULTIPLIER*estimate)))


def compacted_stub(content):
    """The few fields a trimmed tool result keeps so the model can still cite it by id."""
    try:
        result = json.loads(content)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    stub = {key: result[key] for key in ('result_id', 'tool', 'ok') if key in result}
    data = result.get('data')
    if isinstance(data, dict) and isinstance(data.get('rows'), list):
        stub['row_count'] = len(data['rows'])
    stub['note'] = COMPACTED_NOTE
    return json.dumps(stub)


def anthropic_tools(schemas):
    """Map the application's tool schemas (OpenAI-style parameters) to Anthropic input_schema."""
    tools = []
    for schema in schemas:
        parameters = _strip_null_enum_members(deepcopy(schema['parameters']))
        parameters['required'] = list(parameters['properties'])
        tools.append({'name': schema['name'], 'description': schema['description'], 'input_schema': parameters,
                      'strict': schema['name'] in STRICT_TOOLS})
    tools.extend(deepcopy(CONTROL_TOOLS))
    return tools


def _strip_null_enum_members(node):
    """Rewrite nullable properties into the form the Messages API's strict validator accepts.

    The application's schemas write nullable filters as
    ``{"type": ["string", "null"], "enum": [None, "Maintenance", ...]}``. The
    strict validator rejects a list-valued ``type`` whenever an ``enum`` is
    present ("Enum value 'Maintenance' does not match declared type
    '['string', 'null']'"), and rejects ``None`` as an enum member. Every
    nullable property therefore becomes
    ``{"anyOf": [{"type": "string", "enum": [...]}, {"type": "null"}]}``
    with its description kept. The model can still send ``null`` and the
    application validates the value on its own side exactly as before.
    """
    if isinstance(node, dict):
        kinds = node.get('type')
        if isinstance(kinds, list) and 'null' in kinds:
            concrete = [k for k in kinds if k != 'null']
            branches = []
            for kind in concrete:
                branch = {'type': kind}
                if isinstance(node.get('enum'), list):
                    members = [v for v in node['enum'] if v is not None]
                    if kind == 'integer':
                        members = [v for v in members if isinstance(v, int) and not isinstance(v, bool)]
                    elif kind == 'string':
                        members = [v for v in members if isinstance(v, str)]
                    branch['enum'] = members
                branches.append(branch)
            branches.append({'type': 'null'})
            rebuilt = {k: v for k, v in node.items() if k not in ('type', 'enum')}
            rebuilt['anyOf'] = branches
            node.clear(); node.update(rebuilt)
        elif isinstance(node.get('enum'), list):
            node['enum'] = [v for v in node['enum'] if v is not None]
        for value in list(node.values()):
            _strip_null_enum_members(value)
    elif isinstance(node, list):
        for value in node:
            _strip_null_enum_members(value)
    return node


def transport(payload, key, timeout):
    """POST to the fixed Messages endpoint; return (status, body bytes). Never raises with secrets."""
    connection = http.client.HTTPSConnection(API_HOST, timeout=timeout)
    try:
        connection.request('POST', API_PATH, body=json.dumps(payload).encode(),
                           headers={'x-api-key': key, 'anthropic-version': API_VERSION, 'content-type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read(MAX_RESPONSE_BYTES+1)
    except OSError:
        # Never expose headers, keys or exception details from the socket layer.
        raise ProviderError('Anthropic request failed or timed out; retry deliberately') from None
    finally:
        connection.close()


class AnthropicPlanner:
    def __init__(self, key, budget, send=None, model=None):
        if (not isinstance(key, str) or not key.startswith('sk-ant-') or not 20 <= len(key) <= 512
                or not key.isascii() or any(c.isspace() for c in key)):
            raise ProviderError('Enter a valid Anthropic API key through the local key field or server environment')
        self.model = resolve_model(model)
        self.mode = f'Anthropic ({self.model}) investigation and narrative; calculated evidence'
        self._key = key
        self._budget = budget
        self._send = send or transport
        self._messages = []
        self._system = None
        self._pending = None
        self._compacted = set()  # indices into _messages whose tool_result is already a stub
        self._start = time.monotonic()
        self.usage = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0, 'reserved_usd': 0.0, 'model': self.model}

    def close(self):
        self._key = None
        self._messages.clear()
        self._compacted.clear()
        self._pending = None

    def next_step(self, context):
        self._check_limits()
        if not self._messages:
            self._system = context['process'] + PLANNER_INSTRUCTIONS + TOOL_RULE
            allowed = context.get('allowed_shifts') or context['shifts']
            selected = context['shifts'][0] if context.get('scope', 'shift') == 'shift' or len(allowed) == 1 else None
            opening = {'question': context['question'], 'production_day': context['production_day'],
                       'scope': 'selected shift only' if selected is not None else 'production day: all three shifts (pass shift null unless the user names one)',
                       'selected_shift': selected, 'allowed_shifts': allowed, 'history': context['history']}
            if context.get('period', 'day') != 'day':   # a week, month or 4-4-5 window selected: the period tools' default window
                opening['selected_window'] = {'period': context['period'], 'anchor': context['production_day']}
            self._messages = [{'role': 'user', 'content': json.dumps(opening)}]
        elif self._pending:
            compact = compact_result(context['results'][-1])
            self._messages.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': self._pending, 'content': json.dumps(compact)}]})
            self._pending = None
        payload = {'model': self.model, 'max_tokens': MAX_TOKENS, 'system': self._system,
                   'tools': anthropic_tools(context['tools']),
                   'tool_choice': {'type': 'auto', 'disable_parallel_tool_use': True},
                   'output_config': {'effort': 'low'}, 'messages': self._messages}
        response, call = self._request(payload)
        if call is None:
            # Text-only reply: finish with the successful evidence gathered so far, if any.
            ids = [r['result_id'] for r in context['results'] if r['ok'] and r['tool'] != 'render_chart']
            if ids:
                return {'final': ids}
            return {'unavailable': 'The model ended without requesting evidence.'}
        args = call['input']
        if call['name'] == 'finish':
            if set(args) != {'result_ids'} or not isinstance(args['result_ids'], list):
                raise ProviderError('Invalid finish arguments')
            # Models routinely list the chart they just rendered among the evidence; charts are
            # output, not evidence, so drop them (and anything unknown or failed) rather than
            # failing the whole investigation on a bookkeeping slip.
            usable = {r['result_id'] for r in context['results'] if r['ok'] and r['tool'] != 'render_chart'}
            ids = [r for r in args['result_ids'] if isinstance(r, str) and r in usable]
            if ids:
                return {'final': list(dict.fromkeys(ids))}
            return {'unavailable': 'The model finished without usable evidence.'}
        if call['name'] == 'cannot_answer':
            return {'unavailable': 'The requested information or action is outside the available tools. Try another question or select the relevant production day.'}
        # Echo the assistant turn unchanged (thinking blocks included) so the tool result can follow it.
        self._messages.append({'role': 'assistant', 'content': response['content']})
        self._pending = call['id']
        return {'tool': call['name'], 'arguments': args}

    def summarize(self, question, evidence):
        from .narrative import NARRATIVE_SCHEMA, evidence_packet, render_narrative
        packet = evidence_packet(evidence)
        payload = {'model': self.model, 'max_tokens': NARRATIVE_MAX_TOKENS,
                   'system': NARRATIVE_INSTRUCTIONS + '\nRespond only by calling write_narrative.',
                   'tools': [{'name': 'write_narrative', 'description': 'Return a concise evidence-linked interpretation.',
                              'strict': True, 'input_schema': deepcopy(NARRATIVE_SCHEMA)}],
                   'tool_choice': {'type': 'auto', 'disable_parallel_tool_use': True},
                   'output_config': {'effort': 'low'},
                   'messages': [{'role': 'user', 'content': json.dumps({'question': question, 'evidence': packet})}]}
        _, call = self._request(payload)
        if call is None or call['name'] != 'write_narrative':
            raise ProviderError('Unexpected narrative response')
        return render_narrative(call['input'], packet)

    def _check_limits(self):
        remaining = LIFETIME_SECONDS-(time.monotonic()-self._start)
        if remaining <= 0 or self.usage['model_calls'] >= MAX_CALLS:
            raise ProviderError('Live investigation model-call/time limit reached')
        return remaining

    def _fit(self, payload):
        """Trim the oldest tool results in the running history until the request fits; return its size.

        The result the model is about to read (the last tool_result) is never trimmed, and a
        stub keeps result_id/tool/ok so finish can still cite the evidence. Trimming is
        permanent for the rest of the investigation: a later request would only be larger.
        """
        size = len(json.dumps(payload).encode())
        if size <= MAX_REQUEST_BYTES or payload.get('messages') is not self._messages:
            return size
        result_turns = [i for i, m in enumerate(self._messages)
                        if m['role'] == 'user' and isinstance(m['content'], list)
                        and m['content'][0].get('type') == 'tool_result']
        for index in result_turns[:-1]:
            if index in self._compacted:
                continue
            block = self._messages[index]['content'][0]
            block['content'] = compacted_stub(block['content'])
            self._compacted.add(index)
            size = len(json.dumps(payload).encode())
            if size <= MAX_REQUEST_BYTES:
                break
        return size

    def _request(self, payload):
        remaining = self._check_limits()
        size = self._fit(payload)
        if size > MAX_REQUEST_BYTES:
            raise ProviderError('Model context byte limit reached')
        reserve = reserve_micro_usd(self.model, size, payload['max_tokens'])
        self._budget.reserve(reserve)  # Commit before the network call; never refund uncertain usage.
        self.usage['reserved_usd'] += reserve/1_000_000
        self.usage['model_calls'] += 1
        status, body = self._send(payload, self._key, min(SOCKET_TIMEOUT, remaining))
        if status in (401, 403):
            raise ProviderError('The model provider rejected the key (misconfigured key); no automatic retry')
        if status in (429, 529):
            raise ProviderError('The model is busy; retry in a minute')
        if status != 200:
            raise ProviderError(f'Upstream model error (HTTP {status}); no automatic retry')
        if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_RESPONSE_BYTES:
            raise ProviderError('Anthropic response exceeds size limit')
        try:
            response = json.loads(body)
        except ValueError:
            raise ProviderError('Anthropic response was not valid JSON') from None
        if not isinstance(response, dict):
            raise ProviderError('Anthropic response was not an object')
        usage = response.get('usage') or {}
        for name in ('input_tokens', 'output_tokens'):
            value = usage.get(name)
            if type(value) is not int or value < 0:
                raise ProviderError('Missing or invalid provider usage; reservation retained')
            self.usage[name] += value
        rates = PRICING[self.model]
        self.usage['cost_usd'] = (self.usage['input_tokens']*rates['input']+self.usage['output_tokens']*rates['output'])/1_000_000
        stop = response.get('stop_reason')
        content = response.get('content')
        if not isinstance(content, list):
            raise ProviderError('Anthropic response has no content')
        if stop == 'refusal':
            raise ProviderError('The model declined this request; no action executed')
        if stop not in ('tool_use', 'end_turn'):
            raise ProviderError('Model response incomplete; no action executed')
        calls = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_use']
        if len(calls) > 1:
            raise ProviderError('Expected at most one model tool call')
        if not calls:
            if stop == 'tool_use':
                raise ProviderError('Model reported a tool call without one')
            return response, None
        call = calls[0]
        if not isinstance(call.get('id'), str) or not isinstance(call.get('name'), str) or not isinstance(call.get('input'), dict):
            raise ProviderError('Invalid model tool call')
        return response, call
