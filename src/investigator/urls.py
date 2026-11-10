"""The URL grammar: every offline page state is one canonical URL (issue #20, the Datasette model).

    /corr/<machine>/<grain>/<date>[/shift/<n>]?measure=<what>&...

The path is the *where*, exactly the controls: the machine, the Period control as a grain (day,
week, month, period = the 4-4-5 accounting period), the production day, and the shift (omitted for
all three shifts). The query is the *what*: `measure` names the plan the offline router would run,
and the few modifiers the router knows (`by`, `kind`, `unit`, `metric`, `weeks`, `window`,
`compare`, `shifts`, `rank`, `want`). A typed question is a shortcut: `parse_request` turns it into
a request and `canonical_url` turns the request into the one URL for it; `resolve_url` is the
inverse, and the same request reaches the same agent, so the URL answers exactly what the question
would have, in the same controls.

`docs/URLS.md` is the public description of the grammar; keep the two in step.
"""
from datetime import date
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .agent import Context, PERIOD_WORDS, TREND_METRICS, asked_for, period_plan

# URL grain <-> the Period control (web and /api/ask) <-> the period tools' name for it.
GRAIN_TO_CONTROL = {'day': 'day', 'week': 'week', 'month': 'month', 'period': 'accounting'}
GRAIN_TO_TOOL = {'day': 'day', 'week': 'week', 'month': 'month', 'period': 'fiscal'}
TOOL_TO_GRAIN = {v: k for k, v in GRAIN_TO_TOOL.items()}
CONTROL_TO_GRAIN = {v: k for k, v in GRAIN_TO_CONTROL.items()}
# The day-shaped intents of the offline router and their measure names. Under the day grain they
# run the daily tools; under a ranged grain they take their period form (agent.period_plan).
INTENT_MEASURES = {'summary': 'summary', 'overview': 'overview', 'downtime': 'downtime', 'speed': 'fpm',
                   'quality': 'quality', 'compare': 'compare', 'matrix': 'setups', 'orders': 'orders',
                   'notes': 'notes', 'ranking': 'rank'}
MEASURE_INTENTS = {v: k for k, v in INTENT_MEASURES.items()}
# One record-store tool each; `kpis` becomes compare_periods when `compare=` names a window.
STORE_MEASURES = {'kpis': 'get_period_kpis', 'pareto': 'get_downtime_pareto', 'waste': 'get_waste_breakdown',
                  'stops': 'get_stops_profile', 'grade': 'get_grade_totals', 'overrun': 'get_overrun',
                  'opportunity': 'get_speed_opportunity', 'trend': 'get_trend', 'rollstock': 'get_rollstock'}
TOOL_MEASURES = {v: k for k, v in STORE_MEASURES.items()}
# Plans the period form of a day-shaped intent runs that are more than one store tool (agent.period_plan),
# by their tools in order. Under a ranged grain `downtime` is the Pareto alone, `quality` the waste breakdown
# alone and `compare` the KPI tiles per shift, so those three resolve as aliases of pareto, waste and kpis.
PERIOD_SHAPES = {('get_period_kpis', 'get_downtime_pareto', 'get_waste_breakdown'): 'overview',
                 ('get_period_kpis', 'get_grade_totals'): 'fpm'}
UNCHARTED = ('setups', 'orders', 'notes', 'rank')   # day plans that draw nothing: `want=chart` changes their status
GROUPS = ('reason', 'place', 'kind')
RANK_METRICS = ('speed', 'downtime', 'quality')
QUERY_FIELDS = ('measure', 'by', 'kind', 'unit', 'metric', 'weeks', 'window', 'compare', 'shifts', 'rank', 'want',
                'corrupt', 'after', 'format')
PARAM_ORDER = ('measure', 'window', 'by', 'kind', 'unit', 'metric', 'weeks', 'compare', 'shifts', 'rank', 'want', 'corrupt', 'after')
MAX_AFTER = 8          # prior views carried as follow-up context; the agent keeps its own max_turns
MAX_URL = 2000
PATH = re.compile(r'^/corr/(?P<machine>[A-Za-z0-9_.-]{1,32})/(?P<grain>day|week|month|period)/'
                  r'(?P<date>\d{4}-\d{2}-\d{2})(?:/shift/(?P<shift>[123]))?(?P<json>\.json)?$')
MEASURE_HELP = ('measure is one of ' + ', '.join([*INTENT_MEASURES.values(), *STORE_MEASURES, 'report']) +
                '; see docs/URLS.md')


def machine_slug(machine_id):
    """`CORR-1` is `/corr/1/`; any other id is used as it is, lower-cased."""
    return re.sub(r'^corr-', '', str(machine_id).lower())


def _month_of(anchor):
    return anchor[:7]


# ------------------------------------------------------------------ request -> URL

def canonical_url(request, context, corrupt=False, machine_id='CORR-1', after=()):
    """The one URL for a parsed offline request in its context (the page URL, no `.json`)."""
    plan = request.get('plan')
    params = []
    day = context.production_day
    control = getattr(context, 'period', 'day')
    grain = TOOL_TO_GRAIN[control]
    scope_day = context.scope == 'day'
    if not plan:
        measure = INTENT_MEASURES[request['intent']]
        if measure in ('overview', 'downtime', 'compare'):
            if request.get('group_by', 'reason') != 'reason':
                params.append(('by', request['group_by']))
            if request.get('kind'):
                params.append(('kind', request['kind'].lower()))
        if measure == 'rank' and list(request.get('ranking_metrics', RANK_METRICS)) != list(RANK_METRICS):
            params.append(('rank', ','.join(request['ranking_metrics'])))
        if scope_day and request['shifts'] != [1, 2, 3]:
            params.append(('shifts', ','.join(str(s) for s in request['shifts'])))
    else:
        tools = tuple(dict.fromkeys(step['tool'] for step in plan))
        first = plan[0]['arguments']
        window = first.get('window') or first.get('current_window')
        shifts = [step['arguments'].get('shift') for step in plan if step['tool'] == tools[0]]
        window_param = None   # the question's own window when it is not the Period control's
        if window is not None and (window['period'], window['anchor']) != (control, day):
            if window['anchor'] == day:
                window_param = TOOL_TO_GRAIN[window['period']]                      # "this week" under Day
            elif window['period'] == 'month' and window['anchor'].endswith('-01'):
                window_param = _month_of(window['anchor'])                          # a named month: "August"
            else:
                raise ValueError(f'No canonical URL for a window anchored on {window["anchor"]} from {day}')
        if tools in PERIOD_SHAPES:
            measure = 'summary' if request.get('intent') == 'period_summary' else PERIOD_SHAPES[tools]
            if measure in ('overview', 'summary'):
                pareto = next(step['arguments'] for step in plan if step['tool'] == 'get_downtime_pareto')
                if pareto['group_by'] != 'reason':
                    params.append(('by', pareto['group_by']))
        elif tools == ('get_grade_totals',) and first.get('measure') == 'speed':
            measure = 'fpm'                                                     # speed grouped by grade
            params.append(('by', 'grade'))
        elif tools == ('get_period_kpis',):
            measure = 'kpis'
            if first.get('compare') is False:
                params.append(('compare', 'none'))
        elif tools == ('compare_periods',):
            measure = 'kpis'
            previous = first['previous_window']
            if previous['period'] != window['period']:
                raise ValueError('No canonical URL for a comparison across window kinds')
            params.append(('compare', _month_of(previous['anchor']) if previous['period'] == 'month' and previous['anchor'].endswith('-01') else previous['anchor']))
        elif len(tools) == 1 and tools[0] in TOOL_MEASURES:
            measure = TOOL_MEASURES[tools[0]]
            if measure == 'pareto':
                if first['group_by'] != 'reason':
                    params.append(('by', first['group_by']))
                if first['measure'] != 'minutes':
                    params.append(('unit', first['measure']))
            elif measure == 'opportunity' and first['slice_by'] != 'width':
                params.append(('by', first['slice_by']))
            elif measure == 'trend':
                params.append(('metric', first['metric']))
                if first.get('weeks', 8) != 8:
                    params.append(('weeks', str(first['weeks'])))
        else:
            raise ValueError(f'No canonical URL for the plan {tools}')
        if window_param:
            if tools in PERIOD_SHAPES and measure not in ('fpm', 'overview'):
                raise ValueError('The period form of a day-shaped question runs over the Period control, never another window')
            params.append(('window', window_param))
        named = [s for s in shifts if s is not None]
        if scope_day and named:
            params.append(('shifts', ','.join(str(s) for s in named)))
    asked = request.get('asked') or []
    wants = [w for w in ('chart', 'profit') if w in asked and (w == 'profit' or measure in UNCHARTED)]
    if wants:
        params.append(('want', ','.join(wants)))
    params.insert(0, ('measure', measure))
    if corrupt:
        params.append(('corrupt', '1'))
    for prior in after:
        params.append(('after', prior))
    params.sort(key=lambda kv: PARAM_ORDER.index(kv[0]))   # one spelling per state: a fixed field order
    path = f'/corr/{machine_slug(machine_id)}/{grain}/{day}' + ('' if scope_day else f'/shift/{context.shift}')
    return path + '?' + urlencode(params, safe=',/?=:-')


def scope_url(day, control_period, scope, shift, machine_id='CORR-1'):
    """The bare page URL of a scope (no question): where the controls sit."""
    grain = CONTROL_TO_GRAIN[control_period]
    return f'/corr/{machine_slug(machine_id)}/{grain}/{day}' + ('' if scope == 'day' else f'/shift/{shift}')


# ------------------------------------------------------------------ URL -> request

def split_url(url):
    """(path, query string) of a URL or path; the fragment and any host are dropped."""
    parts = urlsplit(url)
    return parts.path, parts.query


def _query(query):
    """The query as a dict of single values plus the `after` list; unknown or repeated fields are refused."""
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query))
    out, after = {}, []
    for key, value in pairs:
        if key not in QUERY_FIELDS:
            raise ValueError(f'Unknown query field {key!r}; the fields are ' + ', '.join(QUERY_FIELDS))
        if key == 'after':
            after.append(value)
            continue
        if key in out:
            raise ValueError(f'Repeated query field {key!r}')
        out[key] = value
    if len(after) > MAX_AFTER:
        raise ValueError(f'At most {MAX_AFTER} prior views can be carried as follow-up context')
    return out, after


def _shift_list(value, scope, shift):
    """`shifts=2,1` under the all-shifts scope; nothing (or the selected shift) under one shift."""
    if value is None:
        return []
    items = value.split(',')
    if not items or any(item not in ('1', '2', '3') for item in items) or len(set(items)) != len(items):
        raise ValueError('shifts lists distinct shifts from 1, 2 and 3, for example shifts=2,1')
    shifts = [int(item) for item in items]
    if scope == 'shift' and shifts != [shift]:
        raise ValueError('Selected shift only: choose Production-day comparison or change the shift control')
    return shifts


def resolve_url(url, store, max_turns=4, depth=0):
    """Validate a canonical URL against the store and return the state it names:

        {day, control_period, tool_period, scope, shift, corrupt, request, report, text, history, format, json}

    `request` is the offline router's request (None for `measure=report`), `text` the address the
    agent records as the question, `history` the entries rebuilt from `after=`."""
    if len(url) > MAX_URL:
        raise ValueError(f'URL longer than {MAX_URL} characters')
    path, query = split_url(url)
    match = PATH.match(path)
    if not match:
        raise ValueError('Not a question URL: /corr/<machine>/<day|week|month|period>/<YYYY-MM-DD>[/shift/<1|2|3>]?measure=...')
    if match['machine'] != machine_slug(store.machine_id):
        raise ValueError(f'No machine {match["machine"]!r}: the record store holds {machine_slug(store.machine_id)}')
    grain = match['grain']
    day = store.check_day(match['date'])   # a weekend or out-of-range date is refused with the calendar reason
    scope = 'shift' if match['shift'] else 'day'
    shift = int(match['shift']) if match['shift'] else 2
    fields, after = _query(query)
    corrupt = fields.get('corrupt', '0')
    if corrupt not in ('0', '1'):
        raise ValueError('corrupt is 1 (inject the bad trim record) or absent')
    corrupt = corrupt == '1'
    fmt = fields.get('format')
    if match['json']:
        if fmt not in (None, 'json'):
            raise ValueError('A .json address does not take format=')
        fmt = 'json'
    if fmt not in (None, 'json', 'pdf'):
        raise ValueError('format is json or pdf')
    measure = fields.get('measure')
    if measure is None:
        raise ValueError('Add measure= to ask this scope a question; ' + MEASURE_HELP)
    tool_period = GRAIN_TO_TOOL[grain]
    context = Context(day, shift, period=tool_period)
    context.scope = scope
    window = _window(fields.get('window'), tool_period, day)
    if measure == 'report':
        if grain == 'day':
            raise ValueError('The period report needs Week, Month or 4-4-5; a day is investigated with a question')
        request = None
    else:
        request = _request(measure, fields, context, window)
        request['asked'] = _wants(fields.get('want'))
    for key in ('by', 'kind', 'unit', 'metric', 'weeks', 'window', 'compare', 'rank'):
        if key in fields and key not in _used(measure, fields):
            raise ValueError(f'{key}= does not apply to measure={measure}')
    history = []
    if after:
        if depth:
            raise ValueError('A prior view carries no after= of its own')
        for prior in after:
            earlier = resolve_url(prior, store, max_turns, depth + 1)
            if earlier['day'] != day or earlier['corrupt'] != corrupt:
                raise ValueError('after= names a view of another production day or dataset; follow-ups share the day')
            if earlier['report']:
                continue   # the period report is not a question: it never enters the history on the session path either
            history.append({'question': prior, 'shifts': earlier['request']['shifts'], 'intent': earlier['request']['intent']})
        history = history[-max_turns:]
    resolved = {'day': day, 'control_period': GRAIN_TO_CONTROL[grain], 'tool_period': tool_period, 'scope': scope,
                'shift': shift, 'corrupt': corrupt, 'request': request, 'report': measure == 'report',
                'history': history, 'format': fmt, 'json': bool(match['json']) or fmt == 'json', 'after': after,
                'context': context, 'machine_id': store.machine_id}
    # The agent records the view's own address as the question; the page URL adds the follow-up chain.
    resolved['text'] = page_url(resolved, with_after=False)
    return resolved


def _used(measure, fields):
    """The modifier fields a measure reads, so a stray one is refused rather than ignored."""
    if measure == 'overview':
        return {'by', 'kind', 'window'}
    if measure in ('downtime', 'compare'):
        return {'by', 'kind'}
    if measure == 'rank':
        return {'rank'}
    if measure == 'fpm':
        return {'by', 'window'}
    if measure == 'kpis':
        return {'window', 'compare'}
    if measure == 'pareto':
        return {'window', 'by', 'unit'}
    if measure == 'opportunity':
        return {'window', 'by'}
    if measure == 'trend':
        return {'metric', 'weeks'}
    if measure in STORE_MEASURES:
        return {'window'}
    return set()


def _window(value, tool_period, day):
    """The window a store measure reads: the Period control's by default; `window=week|month|period`
    names another grain on the same day, `window=YYYY-MM` a named month (anchored on its first day,
    inside the dataset or not: the tool answers with its own reason)."""
    if value is None:
        return {'period': tool_period, 'anchor': day}
    if value in GRAIN_TO_TOOL and value != 'day':
        return {'period': GRAIN_TO_TOOL[value], 'anchor': day}
    if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        return {'period': 'month', 'anchor': value + '-01'}
    raise ValueError('window is week, month, period (the 4-4-5 period containing the day) or a month as YYYY-MM')


def _wants(value):
    if value is None:
        return []
    items = value.split(',')
    if any(item not in ('chart', 'profit') for item in items) or len(set(items)) != len(items):
        raise ValueError('want lists chart and/or profit')
    return [w for w in ('chart', 'profit') if w in items]


def _request(measure, fields, context, window):
    scope, shift = context.scope, context.shift
    shifts = _shift_list(fields.get('shifts'), scope, shift)
    if measure in MEASURE_INTENTS:
        intent = MEASURE_INTENTS[measure]
        group_by, kind = 'reason', None
        if measure in ('overview', 'downtime', 'compare'):
            group_by = fields.get('by', 'reason')
            if group_by not in GROUPS:
                raise ValueError('by is reason, place or kind')
            kind = fields.get('kind')
            if kind is not None:
                if kind not in ('maintenance', 'operator'):
                    raise ValueError('kind is maintenance or operator')
                kind = kind.capitalize()
        metrics = list(RANK_METRICS)
        if measure == 'rank' and 'rank' in fields:
            metrics = fields['rank'].split(',')
            if any(m not in RANK_METRICS for m in metrics) or len(set(metrics)) != len(metrics):
                raise ValueError('rank lists speed, downtime and/or quality')
        if measure == 'fpm' and 'by' in fields:
            return _speed_by_grade(fields['by'], shifts, context, window)
        if context.period != 'day' or (measure in ('fpm', 'overview') and 'window' in fields):
            if kind is not None:
                raise ValueError('kind= narrows the day overview and downtime; the period form groups the downtime Pareto with by=kind instead')
            return period_plan(intent, window, shifts, group_by, context)
        return {'intent': intent, 'shifts': shifts or ([1, 2, 3] if scope == 'day' else [shift]), 'kind': kind,
                'group_by': group_by, 'want_chart': True, 'ranking_metrics': metrics}
    if measure not in STORE_MEASURES:
        raise ValueError(f'Unknown measure {measure!r}; ' + MEASURE_HELP)
    if scope == 'shift':
        per_shift = [shift]
    elif measure == 'kpis':
        per_shift = shifts or [None]
    elif len(shifts) > 1:
        raise ValueError(f'measure={measure} runs for one shift (shifts=3) or all shifts; it is not split by shift')
    else:
        per_shift = shifts or [None]
    plan = []
    for s in per_shift:
        if measure == 'kpis':
            compare = fields.get('compare')
            if compare in (None, 'prev'):
                plan.append(('get_period_kpis', {'window': window, 'compare': True, 'shift': s}))
            elif compare == 'none':
                plan.append(('get_period_kpis', {'window': window, 'compare': False, 'shift': s}))
            else:
                plan.append(('compare_periods', {'current_window': window, 'previous_window': {'period': window['period'], 'anchor': _compare_anchor(compare, window['period'])}, 'shift': s}))
        elif measure == 'pareto':
            group_by, unit = fields.get('by', 'reason'), fields.get('unit', 'minutes')
            if group_by not in GROUPS:
                raise ValueError('by is reason, place or kind')
            if unit not in ('minutes', 'count'):
                raise ValueError('unit is minutes or count')
            plan.append(('get_downtime_pareto', {'window': window, 'group_by': group_by, 'measure': unit, 'shift': s}))
        elif measure == 'opportunity':
            slice_by = fields.get('by', 'width')
            if slice_by not in ('width', 'crew'):
                raise ValueError('by is width or crew for the speed opportunity')
            plan.append(('get_speed_opportunity', {'window': window, 'slice_by': slice_by, 'shift': s}))
        elif measure == 'trend':
            metric = fields.get('metric')
            if metric not in TREND_METRICS:
                raise ValueError('metric is one of ' + ', '.join(TREND_METRICS))
            weeks = fields.get('weeks', '8')
            if not weeks.isdigit() or not 1 <= int(weeks) <= 26:
                raise ValueError('Trends cover 1 to 26 production weeks')
            plan.append(('get_trend', {'metric': metric, 'weeks': int(weeks), 'anchor': context.production_day, 'shift': s}))
        else:
            plan.append((STORE_MEASURES[measure], {'window': window, 'shift': s}))
    intent = {'kpis': 'period_compare', 'pareto': 'period_pareto', 'waste': 'period_waste', 'stops': 'period_stops',
              'grade': 'period_grade', 'overrun': 'period_overrun', 'opportunity': 'period_speed_opportunity',
              'trend': 'period_trend', 'rollstock': 'period_rollstock'}[measure]
    return {'intent': intent, 'shifts': [s for s in per_shift if s], 'kind': None, 'group_by': 'reason', 'want_chart': True,
            'ranking_metrics': [], 'plan': [{'tool': tool, 'arguments': arguments} for tool, arguments in plan]}


def _speed_by_grade(by, shifts, context, window):
    """`measure=fpm&by=grade`: speed without stops against each grade's target over the window
    (the Period control's, or `window=`), one shift or all shifts."""
    if by != 'grade':
        raise ValueError('by is grade for measure=fpm (speed by grade)')
    if context.scope == 'shift':
        shift = context.shift
    elif len(shifts) > 1:
        raise ValueError('measure=fpm&by=grade runs for one shift (shifts=3) or all shifts; it is not split by shift')
    else:
        shift = shifts[0] if shifts else None
    return {'intent': 'period_speed', 'shifts': [shift] if shift else [], 'kind': None, 'group_by': 'reason', 'want_chart': True,
            'ranking_metrics': [], 'plan': [{'tool': 'get_grade_totals', 'arguments': {'window': window, 'shift': shift, 'measure': 'speed'}}]}


def _compare_anchor(value, period):
    """`compare=2026-08` (a month) or `compare=2026-09-07` (a date): the comparison window's anchor."""
    if re.fullmatch(r'\d{4}-\d{2}', value):
        if period != 'month':
            raise ValueError(f'compare takes a date (YYYY-MM-DD) for a {PERIOD_WORDS[period]}')
        return value + '-01'
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError('compare is prev, none, a month (YYYY-MM) or a date (YYYY-MM-DD)') from None


def page_url(resolved, with_after=True):
    """The page address of a resolved state: the canonical form, `corrupt` kept, the `after=` chain optional."""
    after = resolved['after'] if with_after else ()
    if resolved['report']:
        base = scope_url(resolved['day'], resolved['control_period'], resolved['scope'], resolved['shift'], resolved['machine_id'])
        query = [('measure', 'report')] + ([('corrupt', '1')] if resolved['corrupt'] else []) + [('after', a) for a in after]
        return base + '?' + urlencode(query, safe=',/?=:-')
    return canonical_url(resolved['request'], resolved['context'], resolved['corrupt'], resolved['machine_id'], after)


def question_url(day, shift, scope, control_period, corrupt=False, question='', after=(), machine_id='CORR-1'):
    """The `/q` shortcut for a typed question in a scope; the server answers it with a redirect."""
    params = [('day', day), ('shift', str(shift)), ('scope', scope), ('period', control_period)]
    if corrupt:
        params.append(('corrupt', '1'))
    params.append(('q', question))
    params.extend(('after', a) for a in after)
    return '/q?' + urlencode(params, quote_via=quote, safe='')
