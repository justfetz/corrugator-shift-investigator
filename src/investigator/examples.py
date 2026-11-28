"""The verified examples (issue #187): the owner's SLT questions, each with his definition and its exact semantic query.

`examples.json` pairs each question with the semantic query that answers it, built from the defaults block of
`semantic_model.json` (the no-window default, top 5, how reasons rank). Every number an example shows is computed by
`semantic.run` from the record store; this module types none. `tests/test_examples.py` checks each example's rows against a
hand-written SQL query over the record tables, so if the computation behind an example ever changes, the build fails.

An example's plan has one of three shapes:

- a query: one semantic query, with `beside` measures read over the same rows (stops and minutes per stop beside a reason's
  minutes); its rows may be chosen and ordered by another measure, `rank_by` (the stops on the worst running grades: each grade
  leads with its stops, and the grades are the slowest by speed without stops);
- `rank_together`: two queries whose rows are ranked together (speed loss: stop reasons and grades, in feet);
- `lines`: one total per query (where the waste is: trim, shear, dry end, peel, core, each in its own unit);

or it is a `refusal`: a question the records cannot answer, refused naming what is missing.

A plan names its window by a relative window of the defaults block (`ytd`, `last_week`, ...), read on the page's day. Loaded
with the store (`load(store=...)`: the tests, `evals/run.py` and the app at start), every filter value must be one the records
hold, spelled as they hold it.

The reading score (issue #187's baseline for the planner, #172): each example's question in the owner's words and five
rewordings, asked of the offline router (`agent.route`: the scope the words name, then `agent.parse_request`) on the golden
page, and scored a pass when the plan it reads is the example's. A wording asked in a window the model does not read yet (a
relative window with an `interim`; none since PR #186 added the year to date) would be read in a stand-in window: it is
reported apart and never counted as a pass. Reported by `evals/run.py`, never a gate.
"""
import json
from pathlib import Path
import re

from . import semantic

EXAMPLES_PATH = Path(__file__).with_name('examples.json')
LEVERS = ('downtime', 'speed', 'waste')          # the three levers every answer ties back to (CLAUDE.md, "Who this is for")
REVIEWERS = ('owner', 'owner-pending')
REWORDINGS = 5                                   # rewordings per example, beside the owner's own words
QUERY_FIELDS = ('measure', 'group_by', 'filters', 'order', 'limit', 'shift')
SHAPES = ('query', 'rank_together', 'lines', 'refusal')
RANK_FIELDS = ('measure', 'order', 'limit')      # rank_by: the measure that picks and orders a query's rows, its order and how many
STAND_IN = 'stand-in'                            # a wording asked in a window the model reads in a stand-in: never a pass
OUTCOMES = ('pass', 'miss', STAND_IN)
# The page the reading score asks on: the golden default (evals/run.py DEFAULT_DAY, shift 2 under the shift scope)
SCORE_DAY, SCORE_SHIFT, SCORE_SCOPE = '2026-09-14', 2, 'shift'
VALIDATION_DAY = '2026-09-14'    # a day to read relative windows on when an example is checked without a store


class ExampleError(ValueError):
    """An example the model cannot hold: it names a measure, dimension, window or rule the model lacks."""


def shape(example):
    plan = example.get('plan')
    if example.get('refusal'):
        return 'refusal'
    if 'rank_together' in plan:
        return 'rank_together'
    if 'lines' in plan:
        return 'lines'
    return 'query'


def _parts(example):
    """The plan's queries without their window: the one query, or each query of rank_together or lines."""
    plan, kind = example['plan'], shape(example)
    if kind == 'query':
        return [{k: plan[k] for k in QUERY_FIELDS if k in plan}]
    common = {k: plan[k] for k in ('shift',) if k in plan}
    if kind == 'rank_together':
        return [{**part, **common, 'order': plan['order'], 'limit': plan['limit']} for part in plan['rank_together']]
    return [{**part, **common} for part in plan['lines']]


def queries(example, day, window=None, mdl=None):
    """(the example's semantic queries read on the page's `day`, the relative window they were read in): each query with its
    window. `window` is a relative window that replaces the plan's (an alternate, or the window a rewording names)."""
    mdl = mdl or semantic.model()
    if shape(example) == 'refusal':
        return [], None
    name = window or example['plan']['window']
    spec, read_as = semantic.relative_window(name, day, mdl)
    out = []
    for part in _parts(example):
        query = {'group_by': [], 'filters': {}, 'shift': None, **part, 'window': spec}
        out.append(query)
    return out, read_as


def _ranking(example, query):
    """The query that picks and orders a rank_by example's rows: the lead query's split, filters, shift and window, read in the
    rank_by measure with its order and limit (the slowest five grades by speed without stops)."""
    rank = example['plan']['rank_by']
    return {**query, 'measure': rank['measure'], 'order': rank['order'], 'limit': rank['limit']}


def _check_values(valid, store, mdl, where):
    """Every filter value of a checked query is one the store holds, spelled as the store holds it: a misspelled value, or one the
    records hold in another case, fails the load (the coordinator's review of PR #189, item 4)."""
    for dim, value in valid.filters.items():
        try:
            held = semantic.resolve_value(dim, value, store, mdl)
        except semantic.SemanticError as exc:
            raise ExampleError(f'{where}: {exc}') from None
        if held != value:
            raise ExampleError(f'{where}: names {mdl.dimensions[dim]["label"].lower()} {value!r}, and the records hold it as {held!r}')


def _check_example(example, mdl, defaults, seen, store=None):
    eid = example.get('id')
    where = f'example {eid}'
    if not eid or not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', eid) or eid in seen:
        raise ExampleError(f'{where}: an example needs a new id of lower-case words joined by hyphens')
    for key in ('question', 'definition', 'lever', 'reviewed_by'):
        if not isinstance(example.get(key), str) or not example[key].strip():
            raise ExampleError(f'{where}: an example needs its {key}')
    if example['lever'] not in LEVERS:
        raise ExampleError(f'{where}: the lever is one of ' + ', '.join(LEVERS))
    if example['reviewed_by'] not in REVIEWERS:
        raise ExampleError(f'{where}: reviewed_by is owner or owner-pending')
    for rule in example.get('rules', []):
        if rule not in defaults['rules']:
            raise ExampleError(f'{where}: names default {rule}, which the semantic model does not hold')
    kind = shape(example) if example.get('refusal') or isinstance(example.get('plan'), dict) else None
    if kind is None:
        raise ExampleError(f'{where}: an example has a plan or a refusal')
    if kind == 'refusal':
        concept = example['refusal'].get('absent')
        if concept not in {c['name'] for c in mdl.absent.values()}:
            raise ExampleError(f'{where}: refuses for {concept}, which the semantic model does not list as absent')
    else:
        plan = example['plan']
        if plan.get('window') not in defaults['windows']:
            raise ExampleError(f'{where}: the plan names window {plan.get("window")}, which is not a relative window of the model')
        for name in example.get('alternates', []):
            if name not in defaults['windows']:
                raise ExampleError(f'{where}: the alternate {name} is not a relative window of the model')
        if kind == 'query':
            unknown = set(plan) - {*QUERY_FIELDS, 'window', 'rank_by'}
        else:
            unknown = set(plan) - {kind, 'window', 'shift', 'order', 'limit'}
        if unknown:
            raise ExampleError(f'{where}: the plan has fields the model does not read: ' + ', '.join(sorted(unknown)))
        if kind != 'query' and example.get('beside'):
            raise ExampleError(f'{where}: only a single query reads measures beside it')
        parts, _ = queries(example, VALIDATION_DAY, mdl=mdl)
        if kind != 'query' and len(parts) < 2:
            raise ExampleError(f'{where}: {kind} puts two queries or more together')
        valid = []
        for query in parts:
            try:
                valid.append(semantic.validate(query, None, mdl))
            except semantic.SemanticError as exc:
                raise ExampleError(f'{where}: {exc}') from None
        ranked = None
        if 'rank_by' in plan:
            rank = plan['rank_by']
            if not isinstance(rank, dict) or set(rank) != set(RANK_FIELDS):
                raise ExampleError(f'{where}: rank_by names its ' + ', '.join(RANK_FIELDS))
            if 'order' in plan or 'limit' in plan:
                raise ExampleError(f'{where}: rank_by holds the order and the limit of the rows, so the plan names neither')
            if not plan.get('group_by'):
                raise ExampleError(f'{where}: rank_by ranks the rows of a split, and the plan names none')
            if rank['order'] not in ('value', 'value_asc'):
                raise ExampleError(f'{where}: rank_by orders by value, largest first (value) or smallest first (value_asc)')
            try:
                ranked = semantic.validate(_ranking(example, parts[0]), None, mdl)
            except semantic.SemanticError as exc:
                raise ExampleError(f'{where}: rank_by {rank["measure"]}: {exc}') from None
        if store is not None:
            for query in valid:
                _check_values(query, store, mdl, where)
        if kind == 'rank_together' and len({mdl.measures[q.measure]['unit'] for q in valid}) != 1:
            raise ExampleError(f'{where}: rank_together ranks queries of one unit')
        if kind == 'lines' and any(q.group_by for q in valid):
            raise ExampleError(f'{where}: a line is one total, never a split')
        for name in example.get('beside', []):
            try:
                semantic.validate({**parts[0], 'measure': name, 'order': 'dimension', 'limit': semantic.MAX_LIMIT}, None, mdl)
            except semantic.SemanticError as exc:
                raise ExampleError(f'{where}: beside {name}: {exc}') from None
        _check_rules(example, valid, defaults, where, limited=[ranked] if ranked else valid)
    rewordings = example.get('rewordings', [])
    if len(rewordings) != REWORDINGS:
        raise ExampleError(f'{where}: an example carries {REWORDINGS} rewordings beside the owner\'s words')
    texts = {example['question'].strip().lower()}
    for wording in rewordings:
        if not isinstance(wording.get('text'), str) or wording['text'].strip().lower() in texts:
            raise ExampleError(f'{where}: a rewording needs its own text')
        texts.add(wording['text'].strip().lower())
        if wording.get('reviewed_by') not in REVIEWERS:
            raise ExampleError(f'{where}: a rewording is reviewed by owner or owner-pending')
        if 'window' in wording and wording['window'] not in defaults['windows']:
            raise ExampleError(f'{where}: the rewording names window {wording["window"]}, which is not a relative window of the model')


def _check_rules(example, valid, defaults, where, limited=None):
    """The defaults an example cites hold in its plan: the build fails when the two drift apart. `limited`: the queries whose limit
    is the plan's (the rank_by query of a ranked example, whose lead query lists every row it ranks)."""
    rules = defaults['rules']
    plan = example['plan']
    for rid in example.get('rules', []):
        applies = rules[rid].get('applies') or {}
        if 'window' in applies and plan['window'] != applies['window']:
            raise ExampleError(f'{where}: cites {rid}, whose window is {applies["window"]}, but reads {plan["window"]}')
        if 'alternates' in applies and example.get('alternates') != applies['alternates']:
            raise ExampleError(f'{where}: cites {rid}, whose alternates are ' + ', '.join(applies['alternates']))
        if 'limit' in applies and any(q.limit != applies['limit'] for q in (limited or valid)):
            raise ExampleError(f'{where}: cites {rid}, which lists {applies["limit"]}')
        if 'dimension' in applies and (valid[0].group_by[:1] != [applies['dimension']] or valid[0].measure != applies['measure']
                                       or example.get('beside', []) != applies.get('beside', [])):
            raise ExampleError(f'{where}: cites {rid}, which ranks {applies["dimension"]} by {applies["measure"]} with '
                               + ', '.join(applies.get('beside', [])) + ' beside')
        if 'measures' in applies and [q.measure for q in valid] != applies['measures']:
            raise ExampleError(f'{where}: cites {rid}, which reads ' + ', '.join(applies['measures']))
        if 'measure' in applies and 'dimension' not in applies and valid[0].measure != applies['measure']:
            raise ExampleError(f'{where}: cites {rid}, which reads {applies["measure"]}')
        if 'absent' in applies and (example.get('refusal') or {}).get('absent') != applies['absent']:
            raise ExampleError(f'{where}: cites {rid}, which refuses for {applies["absent"]}')


def load(path=None, mdl=None, store=None):
    """The examples of `examples.json`, each checked against the semantic model: a plan naming a measure, dimension, window or
    default the model lacks fails here, so it fails the build. With the `store` (the tests, evals/run.py and the app at start),
    each filter value is checked against the values the records hold too: a misspelled one fails the same way."""
    mdl = mdl or semantic.model()
    spec = json.loads(Path(path or EXAMPLES_PATH).read_text())
    seen = set()
    for example in spec['examples']:
        _check_example(example, mdl, mdl.defaults, seen, store)
        seen.add(example['id'])
    return spec['examples']


def about(path=None):
    return json.loads(Path(path or EXAMPLES_PATH).read_text())['about']


# ------------------------------------------------------------------ running an example

def _key(query, row):
    return tuple(row.get(dim) for dim in query['group_by'])


def _row(row, query, part=None, share=False):
    out = {'key': {dim: row.get(dim) for dim in query['group_by']}, 'label': row['label'], 'value': row['value']}
    for field in ('numerator', 'denominator', 'band', *(('share_pct',) if share else ())):
        if row.get(field) is not None:
            out[field] = row[field]
    if part is not None:
        out['part'], out['measure'] = part, query['measure']
    return out


def run_example(example, store, day, window=None, mdl=None):
    """The example's answer on the page's `day`: {'rows', 'window', 'read_as', 'shape', 'says'}. Every value is semantic.run's,
    computed by DuckDB from the store; a refusal is {'refused': the message}."""
    mdl = mdl or semantic.model()
    kind = shape(example)
    says = [mdl.defaults['rules'][r]['applies']['says'] for r in example.get('rules', [])
            if 'says' in (mdl.defaults['rules'][r].get('applies') or {})]
    if kind == 'refusal':
        concept = next(c for c in mdl.absent.values() if c['name'] == example['refusal']['absent'])
        try:
            semantic.run({'measure': concept['name'], 'window': semantic.relative_window('this_week', day, mdl)[0]}, store, mdl=mdl)
        except semantic.SemanticError as exc:
            return {'shape': kind, 'refused': str(exc), 'needs': concept['needs'], 'rows': []}
        raise ExampleError(f'example {example["id"]}: the semantic model answered a question it should refuse')
    parts, read_as = queries(example, day, window, mdl)
    rank = example['plan'].get('rank_by') if kind == 'query' else None
    if rank:
        # the rows are the ranking's (the slowest grades, slowest first); each leads with the plan's own measure for that row
        ranking = semantic.run(_ranking(example, parts[0]), store, mdl=mdl)
        results = [semantic.run({**parts[0], 'order': 'dimension', 'limit': semantic.MAX_LIMIT}, store, mdl=mdl)]
    else:
        ranking, results = None, [semantic.run(q, store, mdl=mdl) for q in parts]
    out = {'shape': kind, 'window': results[0]['window'], 'read_as': read_as, 'says': says,
           'queries': [r['query'] for r in ([ranking] if ranking else []) + results]}
    if kind == 'query':
        query, result = parts[0], results[0]
        share = any((mdl.defaults['rules'][r].get('applies') or {}).get('share') for r in example.get('rules', []))
        if rank:
            if result['groups'] > len(result['rows']):
                raise ExampleError(f'example {example["id"]}: {result["groups"]} rows, more than one request lists')
            held = {_key(query, r): r for r in result['rows']}
            empty = 0 if mdl.measures[query['measure']]['agg'] in semantic.ADDITIVE else None
            rows = []
            for r in ranking['rows']:
                hit = held.get(_key(query, r))
                row = _row(hit, query) if hit else {'key': {dim: r.get(dim) for dim in query['group_by']}, 'label': r['label'], 'value': empty}
                row['rank'] = {'measure': rank['measure'], 'value': r['value']}
                rows.append(row)
        else:
            if query['group_by'] and query.get('limit') == semantic.DEFAULT_LIMIT and result['groups'] > len(result['rows']):
                # a split that says no number lists every row (describe: "every row"): one the limit would cut is refused, never shown short
                raise ExampleError(f'example {example["id"]}: {result["groups"]} rows, more than one request lists')
            rows = [_row(r, query, share=share) for r in result['rows']]
        for name in example.get('beside', []):
            beside = semantic.run({**query, 'measure': name, 'order': 'dimension', 'limit': semantic.MAX_LIMIT}, store, mdl=mdl)
            if beside['groups'] > len(beside['rows']):
                raise ExampleError(f'example {example["id"]}: {name} beside has more rows than one request lists')
            values = {_key(query, r): r['value'] for r in beside['rows']}
            # a slice the beside measure recorded nothing for is a recorded zero for a sum or a count, and no value for a ratio
            empty = 0 if mdl.measures[name]['agg'] in semantic.ADDITIVE else None
            for row in rows:
                row.setdefault('beside', {})[name] = values.get(_key(query, row['key']), empty)
        if not query['group_by']:
            rows = [{'key': {}, 'label': 'Total', 'value': result['value'],
                     **{f: result[f] for f in ('numerator', 'denominator', 'band') if result.get(f) is not None}}]
        out['rows'] = rows
    elif kind == 'rank_together':
        rows = []
        for i, (query, result) in enumerate(zip(parts, results)):
            dim = query['group_by'][0]
            for r in result['rows']:
                row = _row(r, query, part=i)
                row['label'] = f'{mdl.dimensions[dim]["label"]}: {r["label"]}'
                rows.append(row)
        ascending = example['plan']['order'] == 'value_asc'
        rows.sort(key=lambda r: ((r['value'] if ascending else -r['value']) if r['value'] is not None else float('inf'), r['part'], r['label']))
        out['rows'] = rows[:example['plan']['limit']]
    else:
        out['rows'] = [{'key': {'measure': q['measure']}, 'label': mdl.measures[q['measure']]['label'], 'unit': mdl.measures[q['measure']]['unit'],
                        'value': r['value'], **{f: r[f] for f in ('numerator', 'denominator', 'band') if r.get(f) is not None}}
                       for q, r in zip(parts, results)]
    return out


# ------------------------------------------------------------------ the reading in words

def describe(query, mdl=None, rank=None):
    """A semantic query in words: `Downtime (min) · stop kind Maintenance · by stop reason · largest first · top 5 · all shifts`;
    with `rank` (an example's rank_by), its rows are the ranking's: `stops (stops) · by board grade · the 5 with the lowest speed
    without stops (ft/min), lowest first · all shifts`."""
    mdl = mdl or semantic.model()
    m = mdl.measures[query['measure']]
    parts = [f'{m["label"].lower()} ({m["unit"]})']
    for dim, value in (query.get('filters') or {}).items():
        parts.append(f'{mdl.dimensions[dim]["label"].lower()} {value}')
    if query.get('group_by') and rank:
        parts.append('by ' + ' and '.join(semantic._noun(mdl, d) for d in query['group_by']))
        by, way = mdl.measures[rank['measure']], 'lowest' if rank['order'] == 'value_asc' else 'highest'
        parts.append(f'the {rank["limit"]} with the {way} {by["label"].lower()} ({by["unit"]}), {way} first')
    elif query.get('group_by'):
        parts.append('by ' + ' and '.join(semantic._noun(mdl, d) for d in query['group_by']))
        order = query.get('order') or 'value'
        parts.append({'value': 'largest first', 'value_asc': 'smallest first', 'dimension': 'in order'}[order])
        parts.append(f'top {query["limit"]}' if query.get('limit') and query['limit'] != semantic.DEFAULT_LIMIT else 'every row')
    parts.append(f'shift {query["shift"]}' if query.get('shift') else 'all shifts')
    return ' · '.join(parts)


def describe_example(example, mdl=None):
    """The example's reading in words, its window named by the relative window it reads."""
    mdl = mdl or semantic.model()
    kind = shape(example)
    if kind == 'refusal':
        concept = next(c for c in mdl.absent.values() if c['name'] == example['refusal']['absent'])
        return f'refused: the records hold no {concept["needs"]}'
    parts, _ = queries(example, VALIDATION_DAY, mdl=mdl)
    spec = mdl.defaults['windows'][example['plan']['window']]
    window = spec['label'].lower()
    if spec['period'] not in semantic.WINDOW_PERIODS:
        window += f' (read as {mdl.defaults["windows"][spec["interim"]]["label"].lower()} until the model reads the {window})'
    if kind == 'query':
        text = describe(parts[0], mdl, rank=example['plan'].get('rank_by'))
        if example.get('beside'):
            text += ' · beside: ' + ', '.join(f'{mdl.measures[b]["label"].lower()} ({mdl.measures[b]["unit"]})' for b in example['beside'])
    elif kind == 'rank_together':
        each = [f'{mdl.measures[q["measure"]]["label"].lower()} by {semantic._noun(mdl, q["group_by"][0])}' for q in parts]
        text = (' and '.join(each) + f' ({mdl.measures[parts[0]["measure"]]["unit"]}), ranked together · '
                f'{"largest" if example["plan"]["order"] == "value" else "smallest"} first · top {example["plan"]["limit"]} · all shifts')
    else:
        text = 'one line each: ' + ', '.join(f'{mdl.measures[q["measure"]]["label"].lower()} ({mdl.measures[q["measure"]]["unit"]})' for q in parts)
    return f'{text} · {window}'


# ------------------------------------------------------------------ the reading score

def _normal(query, store, mdl):
    """A query as the model reads it (`semantic.validate` with the store: names and values resolved, defaults filled), with its
    window as the dates it covers, so two readings of one window compare equal whatever day anchors them. With no split there is
    one row, so its order and its limit say nothing and are left out."""
    q = semantic.validate(query, store, mdl)
    period, _, start, end = semantic.parse_window(q.window)
    args = q.to_arguments()
    args['window'] = (period, start.isoformat(), end.isoformat())
    if not q.group_by:
        args.pop('order', None)
        args.pop('limit', None)
    return args


def _read_as(request):
    """What the offline router made of a question, in a few words."""
    plan = request.get('plan') or []
    if request.get('intent') == 'semantic' and len(plan) == 1:
        return 'semantic: ' + describe(plan[0]['arguments'])
    tools = ', '.join(step['tool'] for step in plan) or 'the daily tools'
    return f'{request.get("intent")} answer ({tools})'


def score_wording(example, text, window, store, day=SCORE_DAY, shift=SCORE_SHIFT, scope=SCORE_SCOPE, mdl=None):
    """One wording read by the offline router on the page (`day`, `shift`, `scope`): {'outcome': 'pass', 'miss' or 'stand-in',
    'read_as'}. `window` is the relative window the wording names, or None for the example's own.

    A wording asked in a window the model does not read yet (a relative window with an `interim`) can only be compared with the
    example's plan read in its stand-in window, which is another question (the stand-in ranks reasons otherwise): it is a
    stand-in, reported apart and never a pass, whatever the router makes of it. A refusal example passes only on its own rule's
    refusal (the absent concept's words), never on another refusal that happens to name what is missing."""
    from .agent import Context, route
    mdl = mdl or semantic.model()
    kind = shape(example)
    context = Context(day, shift, scope=scope, period='day')
    try:
        request, _ = route(text, context, store.production_days, store)
        refused, read_as = None, _read_as(request)
    except ValueError as exc:
        request, refused = None, str(exc)
        read_as = 'refused: ' + refused[:160]
    if kind == 'refusal':
        rule = next(c for c in mdl.absent.values() if c['name'] == example['refusal']['absent'])
        return {'outcome': 'pass' if refused is not None and rule['refusal'] in refused else 'miss', 'read_as': read_as}
    expected, read_in = queries(example, day, window, mdl)
    if read_in != (window or example['plan']['window']):
        return {'outcome': STAND_IN, 'read_as': read_as}
    if refused is not None:
        return {'outcome': 'miss', 'read_as': read_as}
    if kind != 'query' or example['plan'].get('rank_by'):
        # the offline router reads one semantic query; this example puts several together (or ranks its rows by another measure)
        return {'outcome': 'miss', 'read_as': read_as}
    plan = request.get('plan') or []
    if request.get('intent') != 'semantic' or len(plan) != 1 or plan[0]['tool'] != semantic.TOOL:
        return {'outcome': 'miss', 'read_as': read_as}
    try:
        same = _normal(plan[0]['arguments'], store, mdl) == _normal(expected[0], store, mdl)
    except semantic.SemanticError as exc:
        return {'outcome': 'miss', 'read_as': f'{read_as} ({exc})'}
    return {'outcome': 'pass' if same else 'miss', 'read_as': read_as}


def reading_score(store, day=SCORE_DAY, shift=SCORE_SHIFT, scope=SCORE_SCOPE, mdl=None, examples=None):
    """How many of the examples' wordings the offline router reads as the example's plan: the owner's words and each rewording,
    asked on the golden page. {'read', 'of', 'owner_read', 'owner_of', 'examples', 'stand_in', 'page', 'rows': one per wording,
    each with its outcome}. A wording asked in a stand-in window is counted in `of` and in `stand_in`, never in `read`. Reported,
    never a gate."""
    mdl = mdl or semantic.model()
    rows = []
    for example in examples or load(mdl=mdl, store=store):
        wordings = [{'text': example['question'], 'reviewed_by': 'owner', 'owner': True}] + list(example['rewordings'])
        for wording in wordings:
            window = wording.get('window')
            result = score_wording(example, wording['text'], window, store, day, shift, scope, mdl)
            asked = window or (example['plan']['window'] if shape(example) != 'refusal' else None)
            rows.append({'example': example['id'], 'text': wording['text'], 'owner': bool(wording.get('owner')), 'window': asked, **result})
    owner = [r for r in rows if r['owner']]
    return {'read': sum(r['outcome'] == 'pass' for r in rows), 'of': len(rows),
            'owner_read': sum(r['outcome'] == 'pass' for r in owner), 'owner_of': len(owner),
            'examples': len({r['example'] for r in rows}), 'stand_in': sum(r['outcome'] == STAND_IN for r in rows),
            'page': {'day': day, 'shift': shift, 'scope': scope}, 'rows': rows}


def score_line(score):
    """`verified examples read correctly: X of N (the owner's own words: a of b); s asked in a stand-in window, never counted as a
    pass`: the line evals/run.py prints."""
    return (f'verified examples read correctly: {score["read"]} of {score["of"]} wordings of {score["examples"]} examples '
            f'(the owner\'s own words: {score["owner_read"]} of {score["owner_of"]}); {score.get("stand_in", 0)} asked in a stand-in '
            'window, never counted as a pass; reported, not a gate (issue #187)')
