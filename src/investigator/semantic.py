"""The semantic model's compiler (issue #117, phase 1): a structured request becomes parameterized DuckDB SQL.

A question is a `SemanticQuery`: one measure, up to two group-by dimensions, filters on dimensions, a window
(the period tools' {period, anchor}), a shift, and an optional comparison with the previous window of the same
kind. `validate` checks it against `semantic_model.json` and, given the record store, against the values the
store holds; anything unknown is refused with the nearest valid choices. `compile` turns a valid query into SQL
over allowlisted tables and columns: every table, column and expression comes from the model file (the
`physical` block names them), every value the request carries is a bound parameter, and every statement has a
LIMIT. `run` executes it on the store under the store's two-second interrupt and returns the result in the shape
the tools return: rows, the envelope's window, shift and coverage, and the receipt, which is the trace (the
compiled SQL with its parameters, as every tool's trace) plus each row's `source_ids` (the records it summed,
the first SOURCE_ID_CAP of them, sorted) and `source_count`.

Every number a result carries is computed by DuckDB from the store; this module types none and rounds none
(the answer shape formats them). A dollar figure is a DuckDB quantity priced once by `cost_model.price`, the
same call the tools make, at the named, assumed rates.

Phase 1 is additive: the offline router does not read the model yet (phase 2 wires it, after #144), and the
analyst's tool list does not include `semantic_query`. The URL `measure=semantic&m=…&by=…&<dimension>=…`
(urls.py, docs/URLS.md) reaches it, and `/docs/semantic-model` is rendered from the model file.
"""
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import difflib
import json
import math
from pathlib import Path
import re
import time

from . import cost_model, targets

MODEL_PATH = Path(__file__).with_name('semantic_model.json')
TOOL = 'semantic_query'      # the tool name a plan and a trace carry; not on the analyst's allowlist in phase 1
MAX_GROUP_BY = 2
DEFAULT_LIMIT = 25
MAX_LIMIT = 200
SOURCE_ID_CAP = 50           # record ids a row's receipt lists; source_count is always the full count
ORDERS = ('value', 'value_asc', 'dimension')
COMPARES = ('previous',)
AGGREGATIONS = ('sum', 'count', 'count_distinct', 'ratio', 'priced')
SIMPLE = ('sum', 'count', 'count_distinct')
ADDITIVE = ('sum', 'count')  # a share of the total is read only for these
TIME_GRAINS = ('day', 'week', 'month', 'fiscal')
PRICE_KINDS = ('trim', 'waste', 'paper', 'downtime', 'speed')
# The window a priced figure is annualized against, as the tools price theirs: the window's gross area and its
# scheduled minutes, both measures of the model.
PRICE_BASIS = ('gross_sqft', 'scheduled_minutes')
# SQL words a model expression may use outside its {placeholders}: function names, nothing else.
FUNCTIONS = {'split_part', 'concat_ws'}
PREVIOUS_WORDS = {'day': 'the production day before', 'week': 'the week before', 'month': 'the month before',
                  'fiscal': 'the period before'}
TIME_PLURALS = {'day': 'days', 'week': 'weeks', 'month': 'months', 'fiscal': '4-4-5 periods'}
LEVERS = ('controllable', 'schedule-driven', 'external')


class SemanticError(ValueError):
    """A request the model refuses; `choices` are the nearest valid names or values."""

    def __init__(self, message, choices=()):
        super().__init__(message)
        self.choices = list(choices)


# ------------------------------------------------------------------ the model file

_TOKEN = re.compile(r"'[^']*'|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\s+|[-+*/(),]")
_PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')
# An entity's `where`, a small grammar: conditions joined by AND, each `column <op> literal` or `column IN (literal, ...)`,
# a literal being text in single quotes (no quote inside it) or a number. Nothing else parses: no OR, no function, no
# parenthesis, no second column. Its literals become bound parameters when it compiles.
_WHERE_TOKEN = re.compile(r"\s*(?:(?P<text>'[^']*')|(?P<number>-?\d+(?:\.\d+)?)(?![\w.])|(?P<op><>|!=|<=|>=|=|<|>)|(?P<punct>[(),])"
                          r"|(?P<word>[A-Za-z_]\w*))")
WHERE_OPS = ('=', '<>', '!=', '<=', '>=', '<', '>')


def parse_where(expr, table_columns, where='where'):
    """[(column, operator, [literal, ...])] of an entity's `where` (the operator `IN` for a list), or ValueError naming
    what is outside the grammar. `table_columns` are the columns a condition may name (its table's)."""
    def refuse(why):
        raise ValueError(f'semantic model: {where} {why}; a where is column <op> literal or column IN (literals), joined by AND')
    if not isinstance(expr, str) or not expr.strip():
        refuse('is empty')
    tokens, pos = [], 0
    while pos < len(expr):
        match = _WHERE_TOKEN.match(expr, pos)
        if not match or match.end() == pos:
            if expr[pos:].strip():
                refuse(f'cannot read {expr[pos:pos + 12]!r}')
            break
        kind = match.lastgroup
        text = match[kind]
        if kind == 'text':
            tokens.append(('literal', text[1:-1]))
        elif kind == 'number':
            tokens.append(('literal', float(text) if '.' in text else int(text)))
        elif kind == 'word' and text.upper() in ('AND', 'IN'):
            tokens.append((text.upper(), text))
        else:
            tokens.append((kind, text))
        pos = match.end()
    conditions, i = [], 0

    def take(*kinds):
        nonlocal i
        if i >= len(tokens) or tokens[i][0] not in kinds:
            refuse('ends early' if i >= len(tokens) else f'has {tokens[i][1]!r} where it needs {" or ".join(kinds)}')
        i += 1
        return tokens[i - 1]
    while True:
        column = take('word')[1]
        if column not in table_columns:
            refuse(f'names {column}, which is not a column of its table')
        if i < len(tokens) and tokens[i][0] == 'IN':
            i += 1
            if i >= len(tokens) or tokens[i] != ('punct', '('):
                refuse('needs ( after IN')
            i += 1
            values = [take('literal')[1]]
            while i < len(tokens) and tokens[i] == ('punct', ','):
                i += 1
                values.append(take('literal')[1])
            if take('punct')[1] != ')':
                refuse('needs ) to close its IN list')
            conditions.append((column, 'IN', values))
        else:
            op = take('op')[1]
            conditions.append((column, op, [take('literal')[1]]))
        if i == len(tokens):
            return conditions
        take('AND')


def where_sql(conditions, alias):
    """(SQL, parameters) of parsed `where` conditions over `alias`: every literal a bound parameter."""
    parts, params = [], []
    for column, op, values in conditions:
        if op == 'IN':
            parts.append(f"{alias}.{column} IN ({', '.join('?' for _ in values)})")
        else:
            parts.append(f'{alias}.{column} {op} ?')
        params.extend(values)
    return ' AND '.join(parts), params


def _physical_columns():
    """{table: set of columns} of the record store's own schema (record_store.TABLES): a mapping can only
    name a column the store creates."""
    from .record_store import TABLES
    return {table: {part.strip().split()[0] for part in spec.split(',')} for table, spec in TABLES.items()}


class Model:
    """The loaded model, checked against itself, against targets.json and against the store's schema."""

    def __init__(self, spec):
        self.spec = spec
        physical = spec['physical']['entities']
        self.entities = {e['name']: {**e, **physical[e['name']]} for e in spec['entities']}
        self.joins = {(j['from'], j['to']): j for j in spec['joins']}
        self.dimensions = {d['name']: d for d in spec['dimensions']}
        self.measures = {m['name']: m for m in spec['measures']}
        self.wheres = {}
        self._check()
        self.measure_words = self._words(self.measures)
        self.dimension_words = self._words(self.dimensions)

    @staticmethod
    def _words(items):
        words = {}
        for name, item in items.items():
            for word in [name, item['label'], *item.get('synonyms', [])]:
                words.setdefault(word.lower(), name)
        return words

    def _check(self):
        columns = _physical_columns()
        if set(self.entities) != set(self.spec['physical']['entities']):
            raise ValueError('semantic model: every entity needs one physical mapping, and every mapping an entity')
        for name, entity in self.entities.items():
            if entity['table'] not in columns:
                raise ValueError(f'semantic model: entity {name} maps to {entity["table"]}, which the store does not create')
            for logical, expr in entity['columns'].items():
                if expr is not None:
                    self._physical_tokens(expr, columns[entity['table']], f'{name}.{logical}')
            if entity.get('where') is not None:
                self.wheres[name] = parse_where(entity['where'], columns[entity['table']], f'{name} where')
            if entity['id'] is not None and entity['id'] not in entity['columns']:
                raise ValueError(f'semantic model: entity {name} names id {entity["id"]}, which it does not map')
        for (src, dst), join in self.joins.items():
            if src not in self.entities or dst not in self.entities or join['relationship'] != 'many_to_one':
                raise ValueError(f'semantic model: join {src} -> {dst} must join two entities, many to one')
            if dst in self.wheres:
                # a dimension read through a join to restricted rows would read the rows it leaves out as a value of nothing
                raise ValueError(f'semantic model: entity {dst} is joined from {src}, so it holds no where')
            for left, right in join['on']:
                if left not in self.entities[src]['columns'] or right not in self.entities[dst]['columns']:
                    raise ValueError(f'semantic model: join {src} -> {dst} names a column neither side maps')
        for name, dim in self.dimensions.items():
            if dim['type'] == 'time' and dim['grain'] not in TIME_GRAINS:
                raise ValueError(f'semantic model: dimension {name} has an unknown grain')
            owners = list(self.entities) if dim['entity'] == '*' else [dim['entity']]
            for owner in owners:
                self._template(dim['expr'], owner, f'dimension {name}')
            if any(lever not in LEVERS for lever in (dim.get('value_levers') or {}).values()):
                raise ValueError(f'semantic model: dimension {name} names a value lever outside {", ".join(LEVERS)}')
            if dim.get('recurs', True) not in (True, False):
                raise ValueError(f'semantic model: dimension {name} recurs is true or false')
        for name, entity in self.entities.items():
            lever_by = entity.get('lever_by')
            if lever_by is not None and (self.dimensions.get(lever_by, {}).get('entity') != name or not self.dimensions[lever_by].get('value_levers')):
                raise ValueError(f'semantic model: entity {name} takes its lever from {lever_by}, which must be its own dimension with value levers')
        for name, m in self.measures.items():
            if m['agg'] not in AGGREGATIONS or not m.get('unit') or not m.get('description'):
                raise ValueError(f'semantic model: measure {name} needs a known aggregation, a unit and a definition')
            if m['agg'] in SIMPLE:
                if m['entity'] not in self.entities:
                    raise ValueError(f'semantic model: measure {name} names an unknown entity')
                if m['agg'] != 'count':
                    self._template(m['expr'], m['entity'], f'measure {name}')
                for column, value in m.get('filter', {}).items():
                    if column not in self.entities[m['entity']]['columns'] or not all(isinstance(v, str) for v in (value if isinstance(value, list) else [value])):
                        raise ValueError(f'semantic model: measure {name} filters on an unmapped column or a value that is not text')
                if m.get('above_band'):
                    rule = m['above_band']
                    self._template(rule['expr'], m['entity'], f'measure {name} above_band')
                    if rule['band'] not in targets.BANDS or targets.BANDS[rule['band']].get(rule['line']) is None:
                        raise ValueError(f'semantic model: measure {name} reads a band line targets.json does not hold')
            elif m['agg'] == 'ratio':
                for part in (m['numerator'], m['denominator']):
                    if self.measures.get(part, {}).get('agg') not in SIMPLE:
                        raise ValueError(f'semantic model: ratio {name} needs two simple measures, not {part}')
                scale = m.get('scale', 1)   # compiled into the SQL as a number: a positive, finite int or float, nothing else
                if type(scale) not in (int, float) or not math.isfinite(scale) or not scale > 0:
                    raise ValueError(f'semantic model: ratio {name} needs a positive number as its scale, not {scale!r}')
            else:
                if self.measures.get(m['quantity'], {}).get('agg') not in SIMPLE or m['price_kind'] not in PRICE_KINDS:
                    raise ValueError(f'semantic model: priced {name} needs a simple quantity and a cost_model kind')
            if m.get('lever') not in LEVERS:
                raise ValueError(f'semantic model: measure {name} needs a lever: {", ".join(LEVERS)}')
            if m.get('band') and m['band'] not in targets.BANDS:
                raise ValueError(f'semantic model: measure {name} names band {m["band"]}, which targets.json does not hold')
            for band in (m.get('band_by') or {}).get('bands', {}).values():
                if band not in targets.BANDS:
                    raise ValueError(f'semantic model: measure {name} names band {band}, which targets.json does not hold')
        for basis in PRICE_BASIS:
            if self.measures.get(basis, {}).get('agg') != 'sum':
                raise ValueError(f'semantic model: the pricing basis needs the sum measure {basis}')

    @staticmethod
    def _physical_tokens(expr, table_columns, where):
        for token in _TOKEN.findall(expr) if ''.join(_TOKEN.findall(expr)) == expr else [None]:
            if token is None:
                raise ValueError(f'semantic model: {where} is not a plain column expression')
            if re.fullmatch(r'[A-Za-z_]\w*', token) and token not in table_columns and token.lower() not in FUNCTIONS:
                raise ValueError(f'semantic model: {where} names {token}, which is not a column of its table')

    def _template(self, expr, entity, where):
        """Check a logical template: placeholders the entity maps, and nothing but functions, numbers,
        operators and quoted literals outside them."""
        for logical in _PLACEHOLDER.findall(expr):
            if logical not in self.entities[entity]['columns']:
                raise ValueError(f'semantic model: {where} reads {{{logical}}}, which entity {entity} does not map')
        rest = _PLACEHOLDER.sub('0', expr)
        if ''.join(_TOKEN.findall(rest)) != rest:
            raise ValueError(f'semantic model: {where} is not a plain expression')
        for token in _TOKEN.findall(rest):
            if re.fullmatch(r'[A-Za-z_]\w*', token) and token.lower() not in FUNCTIONS:
                raise ValueError(f'semantic model: {where} names {token} outside a placeholder')

    # ---- what a request may combine
    def measure_entities(self, name):
        """(the entity a measure's rows come from, the entity of its denominator or None)."""
        m = self.measures[name]
        if m['agg'] == 'ratio':
            return self.measures[m['numerator']]['entity'], self.measures[m['denominator']]['entity']
        if m['agg'] == 'priced':
            return self.measures[m['quantity']]['entity'], None
        return m['entity'], None

    def reach(self, entity, dimension):
        """The entity that provides `dimension` to rows of `entity` (itself or one many-to-one join), or None."""
        owner = self.dimensions[dimension]['entity']
        if owner in ('*', entity):
            return entity
        return owner if (entity, owner) in self.joins else None

    def mapped(self, entity, dimension):
        """False when a dimension reads a column the store does not carry yet (a null in the physical block)."""
        owner = self.reach(entity, dimension) or entity
        return all(self.entities[owner]['columns'].get(c) is not None for c in _PLACEHOLDER.findall(self.dimensions[dimension]['expr']))


_MODEL = {}


def model():
    """The model of `semantic_model.json`, loaded and checked once."""
    if 'model' not in _MODEL:
        _MODEL['model'] = Model(json.loads(MODEL_PATH.read_text()))
    return _MODEL['model']


# ------------------------------------------------------------------ the request

@dataclass
class SemanticQuery:
    """measure x group_by x filters x window (issue #117). `window` is {period: day|week|month|fiscal, anchor:
    YYYY-MM-DD}; `shift` 1-3 or None for all shifts (a `shift` filter is the same thing); `compare` None or
    'previous' (the previous window of the same kind beside every row); `order` value (largest first),
    value_asc or dimension (the dimension's own order; the default for a time, shift or lane dimension);
    `limit` the rows returned, 1-MAX_LIMIT (the total is always over every row)."""
    measure: str
    group_by: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    window: dict = None
    compare: str = None
    order: str = None
    limit: int = DEFAULT_LIMIT
    shift: int = None

    def to_arguments(self):
        """The tool call's arguments: plain JSON, in a fixed order (a plan and a trace carry them)."""
        return {'measure': self.measure, 'window': dict(self.window) if self.window else None, 'group_by': list(self.group_by),
                'filters': dict(self.filters), 'shift': self.shift, 'compare': self.compare, 'order': self.order, 'limit': self.limit}

    @classmethod
    def from_arguments(cls, arguments):
        if not isinstance(arguments, dict):
            raise SemanticError('A semantic query is an object: measure, window, group_by, filters, shift, compare, order, limit')
        unknown = set(arguments) - {'measure', 'window', 'group_by', 'filters', 'shift', 'compare', 'order', 'limit'}
        if unknown:
            raise SemanticError(f'Unknown semantic query fields: {", ".join(sorted(unknown))}')
        values = {k: arguments[k] for k in arguments if arguments[k] is not None or k in ('compare', 'order', 'shift', 'window')}
        values.setdefault('group_by', [])
        values.setdefault('filters', {})
        values.setdefault('limit', DEFAULT_LIMIT)
        if 'measure' not in values:
            raise SemanticError('A semantic query names a measure')
        return cls(**values)


def _nearest(word, words, n=5):
    """The nearest valid words to `word` (difflib's ratio), then any that contain it; at most `n`."""
    word = str(word).lower()
    close = difflib.get_close_matches(word, list(words), n=n, cutoff=0.6)
    close += [w for w in words if word and word in w and w not in close]
    return close[:n]


def _choices(word, words, n=5):
    """Canonical names near `word` among {word: canonical name}, in order, without repeats."""
    return list(dict.fromkeys(words[w] for w in _nearest(word, words, n * 3)))[:n]


def resolve_measure(name, mdl=None):
    mdl = mdl or model()
    if not isinstance(name, str) or not name.strip():
        raise SemanticError('Name a measure with m=, for example m=downtime_minutes; every measure is on /docs/semantic-model')
    found = mdl.measure_words.get(name.strip().lower())
    if found:
        return found
    choices = _choices(name, mdl.measure_words)
    raise SemanticError(f'No measure {name!r} in the semantic model' + (f'; the nearest: {", ".join(choices)}' if choices else '') +
                        '. Every measure is on /docs/semantic-model.', choices)


def resolve_dimension(name, mdl=None):
    mdl = mdl or model()
    found = mdl.dimension_words.get(str(name).strip().lower()) if isinstance(name, str) else None
    if found:
        return found
    choices = _choices(name, mdl.dimension_words)
    raise SemanticError(f'No dimension {name!r} in the semantic model' + (f'; the nearest: {", ".join(choices)}' if choices else '') +
                        '. Every dimension is on /docs/semantic-model.', choices)


def _usable(mdl, measure, dimension, as_filter):
    """None when `dimension` can slice (or filter) `measure`, else why not, with the dimensions that can."""
    m = mdl.measures[measure]
    rows, denominator = mdl.measure_entities(measure)
    entity = mdl.entities[rows]
    if mdl.reach(rows, dimension) is None:
        able = [d for d in mdl.dimensions if mdl.reach(rows, d) and mdl.mapped(rows, d) and _denominator_ok(mdl, m, denominator, d)
                and not (as_filter and mdl.dimensions[d]['type'] == 'time')]
        return (f'{m["label"]} ({measure}) is recorded per {entity["label"].lower()}, which carries no {dimension}; '
                f'it can be {"filtered" if as_filter else "split"} by: ' + ', '.join(able), able)
    if not mdl.mapped(rows, dimension):
        return (f'The records in this store carry no {dimension} yet, so {m["label"].lower()} cannot be '
                f'{"filtered" if as_filter else "split"} by it', [])
    if not _denominator_ok(mdl, m, denominator, dimension):
        able = [d for d in mdl.dimensions if mdl.reach(rows, d) and mdl.mapped(rows, d) and _denominator_ok(mdl, m, denominator, d)
                and not (as_filter and mdl.dimensions[d]['type'] == 'time')]
        return (f'{m["label"]} ({measure}) is read against {mdl.measures[m["denominator"]]["label"].lower()}, which carry no '
                f'{dimension}; it can be {"filtered" if as_filter else "split"} by: ' + ', '.join(able), able)
    return None


def _denominator_ok(mdl, m, denominator, dimension):
    return denominator is None or m.get('denominator_total') or mdl.reach(denominator, dimension) is not None


def _window_bounds(window):
    from .record_store import parse_window
    period, anchor, start, end = parse_window(window)
    return period, anchor, start, end


def validate(query, store=None, mdl=None):
    """The query with every name resolved (synonyms to the model's names, filter values to the store's own
    values when `store` is given), or SemanticError naming what is wrong and the nearest valid choices."""
    mdl = mdl or model()
    if isinstance(query, dict):
        query = SemanticQuery.from_arguments(query)
    if not isinstance(query, SemanticQuery):
        raise SemanticError('Expected a SemanticQuery')
    measure = resolve_measure(query.measure, mdl)
    group_by = query.group_by
    if isinstance(group_by, str):
        group_by = [g for g in group_by.split(',') if g]
    if not isinstance(group_by, (list, tuple)) or len(group_by) > MAX_GROUP_BY:
        raise SemanticError(f'group_by lists at most {WORDS[MAX_GROUP_BY]} dimensions')
    by = []
    for name in group_by:
        dim = resolve_dimension(name, mdl)
        if dim in by:
            raise SemanticError(f'group_by names {dim} twice')
        problem = _usable(mdl, measure, dim, as_filter=False)
        if problem:
            raise SemanticError(*problem)
        by.append(dim)
    if not isinstance(query.filters, dict):
        raise SemanticError('filters maps a dimension to one value, for example {"place": "Double backer"}')
    shift = query.shift
    filters = {}
    for name, value in query.filters.items():
        dim = resolve_dimension(name, mdl)
        if dim == 'shift':
            shift = _shift(value) if shift is None or _shift(value) == shift else _conflict(shift, value)
            continue
        if mdl.dimensions[dim]['type'] == 'time':
            raise SemanticError(f'{dim} is a time dimension: the window sets the time (the grain and the day of the address), never a filter')
        problem = _usable(mdl, measure, dim, as_filter=True)
        if problem:
            raise SemanticError(*problem)
        if dim in filters:
            raise SemanticError(f'filters name {dim} twice')
        filters[dim] = resolve_value(dim, value, store, mdl) if store is not None else value
    shift = _shift(shift)
    if query.window is None:
        raise SemanticError('A semantic query needs a window: {"period": "day|week|month|fiscal", "anchor": "YYYY-MM-DD"}')
    try:   # a window outside the dataset, or a weekend day, is refused with the calendar's reason
        store._window(query.window) if store is not None else _window_bounds(query.window)
    except SemanticError:
        raise
    except ValueError as exc:
        raise SemanticError(str(exc)) from None
    compare = {'prev': 'previous'}.get(query.compare, query.compare)
    if compare is not None and compare not in COMPARES:
        raise SemanticError('compare is previous (the previous window of the same kind) or none')
    for dim in by if compare else []:
        # the window before holds none of this window's days, weeks or months, nor its paper runs or orders (each belongs to
        # one production day): a row would be set against nothing (the card once read "previous 0 stops" on every day), so a
        # comparison splits by categories that recur only
        spec = mdl.dimensions[dim]
        if spec['type'] == 'time':
            raise SemanticError(f"A comparison with the previous window cannot split by {dim}: the window before holds none of this "
                                f"window's {TIME_PLURALS[spec['grain']]}, so every row would be set against nothing. Split by a category "
                                f"(stop kind, grade, shift), or ask the previous window at its own address.")
        if spec.get('recurs', True) is False:
            raise SemanticError(f"A comparison with the previous window cannot split by {dim}: each {spec['label'].lower()} belongs to "
                                f"one production day, so the window before holds none of this window's, and every row would be set "
                                f"against nothing. Split by a category that recurs (stop kind, grade, shift), or ask without compare.")
    for dim, value in filters.items() if compare else []:
        if mdl.dimensions[dim].get('recurs', True) is False:
            raise SemanticError(f"A comparison with the previous window cannot filter by {dim}: {mdl.dimensions[dim]['label'].lower()} "
                                f"{value} belongs to one production day, so the window before holds none of it. Ask without compare.")
    order = query.order
    if order is None:
        first = by[0] if by else None
        order = 'dimension' if first and (mdl.dimensions[first]['type'] == 'time' or first in ('shift', 'lane')) else 'value'
    if order not in ORDERS:
        raise SemanticError('order is value (largest first), value_asc or dimension')
    limit = query.limit
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise SemanticError(f'limit is a whole number of rows from 1 to {MAX_LIMIT}')
    return SemanticQuery(measure=measure, group_by=by, filters=filters, window={'period': query.window['period'], 'anchor': query.window['anchor']},
                         compare=compare, order=order, limit=limit, shift=shift)


WORDS = {1: 'one', 2: 'two', 3: 'three'}


def _shift(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in ('1', '2', '3'):
        return int(value.strip())
    if type(value) is int and value in (1, 2, 3):
        return value
    raise SemanticError('shift is 1, 2 or 3, or none for all shifts')


def _conflict(shift, value):
    raise SemanticError(f'The query is for shift {shift}; a shift filter of {value} contradicts it')


# ---- dimension values: the store's own
_VALUES = {}
LOOKUP_ROWS = 500   # a dimension with more distinct values than this is checked value by value, never listed


def _value_sql(mdl, dim):
    """(sql listing the dimension's distinct values over the whole store, its parameters) for a categorical dimension:
    the values of its entity's rows, the ones its `where` keeps."""
    spec = mdl.dimensions[dim]
    owner = list(mdl.entities)[0] if spec['entity'] == '*' else spec['entity']
    entity = mdl.entities[owner]
    expr = _render(mdl, spec['expr'], owner, 'b')
    where, params = where_sql(mdl.wheres[owner], 'b') if owner in mdl.wheres else ('', [])
    return f"SELECT DISTINCT {expr} AS v FROM {entity['table']} b" + (f' WHERE {where}' if where else ''), params


def store_values(dim, store, mdl=None):
    """The dimension's distinct values in the store (None for a dimension with more than LOOKUP_ROWS)."""
    mdl = mdl or model()
    sql, params = _value_sql(mdl, dim)
    key = (id(store), getattr(store, 'content_hash', None), sql, tuple(params))
    if key not in _VALUES:
        rows = store._fetch(f'SELECT v FROM ({sql}) WHERE v IS NOT NULL ORDER BY v LIMIT {LOOKUP_ROWS + 1}', params, limit=LOOKUP_ROWS + 1)
        values = [r['v'] for r in rows]
        _VALUES[key] = None if len(values) > LOOKUP_ROWS else values
    return _VALUES[key]


def _same(value, candidate):
    if isinstance(candidate, str):
        return isinstance(value, str) and value.strip().lower() == candidate.lower()
    try:
        return float(str(value).strip()) == float(candidate)
    except (TypeError, ValueError):
        return False


def resolve_value(dim, value, store, mdl=None):
    """The store's own value a filter names: the value itself (in any case), or the value one of its synonyms
    stands for; else SemanticError with the nearest values the store holds."""
    mdl = mdl or model()
    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        raise SemanticError(f'{dim} takes one value')
    spec = mdl.dimensions[dim]
    # a word may stand for several values (planned maintenance is PM in this store, Scheduled in record contract v2):
    # it resolves to the one the store holds
    synonyms = {}
    for canonical, words in (spec.get('value_synonyms') or {}).items():
        for word in words:
            synonyms.setdefault(word.lower(), []).append(canonical)
    values = store_values(dim, store, mdl)
    if values is not None:
        for candidate in values:
            if _same(value, candidate):
                return candidate
        for canonical in synonyms.get(str(value).strip().lower(), []):
            for candidate in values:
                if _same(canonical, candidate):
                    return candidate
        shown = {str(v).lower(): v for v in values}
        nearest = [shown[w] for w in _nearest(value, shown)]
        if nearest:
            raise SemanticError(f'No {spec["label"].lower()} {value!r} in the records; the nearest: ' + ', '.join(str(c) for c in nearest), nearest)
        # no near match: every value the records hold, however many (a split lists only a window's first rows, so the refusal
        # is the one place that names them all)
        raise SemanticError(f'No {spec["label"].lower()} {value!r} in the records; the values are: ' + ', '.join(str(c) for c in values), values)
    # a dimension with many values (paper runs, orders): checked one by one, the nearest by similarity
    sql, params = _value_sql(mdl, dim)
    hit = store._fetch(f'SELECT v FROM ({sql}) WHERE lower(CAST(v AS VARCHAR)) = lower(?) LIMIT 1', params + [str(value).strip()], limit=1)
    if hit:
        return hit[0]['v']
    near = store._fetch(f'SELECT v FROM ({sql}) WHERE v IS NOT NULL ORDER BY jaro_winkler_similarity(lower(CAST(v AS VARCHAR)), lower(?)) DESC, v LIMIT 5',
                        params + [str(value).strip()], limit=5)
    choices = [r['v'] for r in near]
    raise SemanticError(f'No {spec["label"].lower()} {value!r} in the records; the nearest: ' + ', '.join(str(c) for c in choices), choices)


# ------------------------------------------------------------------ compiling

def _physical(expr, alias, entity_name, mdl=None):
    """A physical expression with each of its table's columns qualified by `alias`."""
    mdl = mdl or model()
    columns = _physical_columns()[mdl.entities[entity_name]['table']]
    out = []
    for token in _TOKEN.findall(expr):
        out.append(f'{alias}.{token}' if token in columns else token)
    return ''.join(out)


def _column(mdl, entity_name, logical, alias):
    expr = mdl.entities[entity_name]['columns'][logical]
    if expr is None:
        raise SemanticError(f'The records in this store carry no {logical} yet')
    sql = _physical(expr, alias, entity_name, mdl)
    return sql if re.fullmatch(r'[A-Za-z_]\w*\.[A-Za-z_]\w*', sql) else f'({sql})'


def _render(mdl, template, entity_name, alias):
    return _PLACEHOLDER.sub(lambda m: _column(mdl, entity_name, m[1], alias), template)


def _fiscal_start(expr):
    """The first day of the 4-4-5 period that contains the DATE `expr` (record_store.fiscal_period, in SQL):
    52-week years from FISCAL_START, each four quarters of FISCAL_WEEKS."""
    from .record_store import FISCAL_START, FISCAL_WEEKS
    quarter = sum(FISCAL_WEEKS)
    weeks = f"CAST(floor(datediff('day', DATE '{FISCAL_START.isoformat()}', {expr}) / 7) AS INTEGER)"
    in_year = f'(({weeks} % 52) + 52) % 52'
    in_quarter = f'({in_year} % {quarter})'
    offsets, edges, first = [], [], 0
    for weeks_in in FISCAL_WEEKS[:-1]:
        edges.append(first + weeks_in)
        offsets.append(first)
        first += weeks_in
    case = 'CASE ' + ' '.join(f'WHEN {in_quarter} < {edge} THEN {offset}' for edge, offset in zip(edges, offsets)) + f' ELSE {first} END'
    start_week = f'({weeks} - {in_year} + ({in_year} - {in_quarter}) + {case})'
    return f"(DATE '{FISCAL_START.isoformat()}' + 7 * {start_week})"


def _dimension_sql(mdl, dim, entity_name, alias):
    spec = mdl.dimensions[dim]
    expr = _render(mdl, spec['expr'], entity_name, alias)
    grain = spec.get('grain')
    if grain == 'week':
        return f"CAST(date_trunc('week', {expr}) AS DATE)"
    if grain == 'month':
        return f"CAST(date_trunc('month', {expr}) AS DATE)"
    if grain == 'fiscal':
        return _fiscal_start(expr)
    return expr


@dataclass
class Statement:
    name: str
    sql: str
    params: list


class _Part:
    """One aggregate over one entity: its FROM, joins and WHERE for a window, a shift and the filters that reach it."""

    def __init__(self, mdl, entity_name, dims, filters, shift, measure=None):
        self.mdl, self.entity_name, self.entity = mdl, entity_name, mdl.entities[entity_name]
        self.aliases, self.joins = {entity_name: 'b'}, []
        self.dims = [(d, self._dimension(d)) for d in dims]
        self.where = []
        date_col = _column(mdl, entity_name, 'production_date', 'b')
        self.where.append(f'{date_col} BETWEEN ? AND ?')
        # the parameters after the window's two dates, in the order their placeholders appear
        self.filter_params = []
        if shift is not None:
            self.where.append(f"{_column(mdl, entity_name, 'shift', 'b')} = ?")
            self.filter_params.append(shift)
        if entity_name in mdl.wheres:   # the entity's own rows (a reject line counted in the dry end), its literals bound
            sql, params = where_sql(mdl.wheres[entity_name], 'b')
            self.where.append(f'({sql})')
            self.filter_params.extend(params)
        for dim, value in filters.items():
            if mdl.reach(entity_name, dim) is None:
                continue   # a slice of the numerator only (denominator_total): the denominator stays the whole
            self.where.append(f'{self._dimension(dim)} = ?')
            self.filter_params.append(value)
        measure = measure or {}
        for column, value in measure.get('filter', {}).items():   # the measure's own rows: one value, or any of a list
            values = value if isinstance(value, list) else [value]
            self.where.append(f"{_column(mdl, entity_name, column, 'b')} IN ({', '.join('?' for _ in values)})")
            self.filter_params.extend(values)
        if measure.get('above_band'):   # the records over one of the owner's lines (targets.json), the line a bound value
            rule = measure['above_band']
            self.where.append(f"{_render(mdl, rule['expr'], entity_name, 'b')} > ?")
            self.filter_params.append(targets.BANDS[rule['band']][rule['line']])

    def _dimension(self, dim):
        owner = self.mdl.reach(self.entity_name, dim)
        if owner not in self.aliases:
            alias = f'j{len(self.aliases)}'
            self.aliases[owner] = alias
            join = self.mdl.joins[(self.entity_name, owner)]
            on = ' AND '.join(f"{_column(self.mdl, self.entity_name, left, 'b')} = {_column(self.mdl, owner, right, alias)}"
                              for left, right in join['on'])
            self.joins.append(f"LEFT JOIN {self.mdl.entities[owner]['table']} {alias} ON {on}")
        return _dimension_sql(self.mdl, dim, owner, self.aliases[owner])

    def source(self):
        return f"{self.entity['table']} b" + ''.join(f' {j}' for j in self.joins)

    def bind(self, start, end):
        return [start, end] + list(self.filter_params)

    def ids(self):
        if self.entity['id'] is None:
            return 'CAST([] AS VARCHAR[])'
        key = _column(self.mdl, self.entity_name, self.entity['id'], 'b')
        return f'list(DISTINCT {key} ORDER BY {key})[1:{SOURCE_ID_CAP}]'

    def count_ids(self):
        return 'count(*)'

    def levers(self):
        """SQL listing the distinct values of the dimension this entity takes its lever from (a stop's kind), or None."""
        dim = self.entity.get('lever_by')
        return f'list(DISTINCT {_dimension_sql(self.mdl, dim, self.entity_name, "b")})' if dim else None


def _aggregate(mdl, name, alias='b', entity_name=None):
    m = mdl.measures[name]
    entity_name = entity_name or m['entity']
    if m['agg'] == 'count':
        return 'count(*)'
    expr = _render(mdl, m['expr'], entity_name, alias)
    if m['agg'] == 'count_distinct':
        return f'count(DISTINCT {expr})'
    # a sum over no records is a recorded zero inside the dataset, as a count is (no stop is no downtime); a window
    # outside the dataset is refused, and an unavailable previous window reads null in `run`, never zero
    return f'coalesce(sum({expr}), 0)'


class Compiled:
    """The statements one validated query runs, in order, and how to read their rows."""

    def __init__(self, query, mdl):
        self.query, self.mdl = query, mdl
        self.measure = mdl.measures[query.measure]
        _, _, start, end = _window_bounds(query.window)
        self.window = (start, end)
        self.previous = None
        if query.compare == 'previous':
            from .record_store import previous_window
            self.previous = previous_window(query.window['period'], start, end)
        self.statements = []
        if query.group_by:
            self.statements.append(Statement('groups', *self._sql(query.group_by, grouped=True)))
        self.statements.append(Statement('total', *self._sql([], grouped=False)))
        if self.measure['agg'] == 'priced':
            for basis in PRICE_BASIS:
                part = _Part(mdl, mdl.measures[basis]['entity'], [], {}, query.shift)
                sql = f'SELECT {_aggregate(mdl, basis)} AS value FROM {part.source()} WHERE {" AND ".join(part.where)} LIMIT 1'
                self.statements.append(Statement(f'basis {basis}', sql, part.bind(*self.window)))

    def _simple(self, name, dims, span, grouped):
        m = self.mdl.measures[name]
        part = _Part(self.mdl, m['entity'], dims, self.query.filters, self.query.shift, m)
        cols = [f'{sql} AS d{i}' for i, (_, sql) in enumerate(part.dims)]
        share = ', value / nullif(sum(value) OVER (), 0) * 100 AS share_pct' if grouped and m['agg'] in ADDITIVE else ''
        levers = [f'{part.levers()} AS lever_keys'] if part.levers() else []
        inner = (f"SELECT {', '.join(cols + [f'{_aggregate(self.mdl, name)} AS value', 'count(*) AS source_count', f'{part.ids()} AS source_ids'] + levers)} "
                 f"FROM {part.source()} WHERE {' AND '.join(part.where)}" + (' GROUP BY ALL' if cols else ''))
        return inner, share, part.bind(*span)

    def _body(self, dims, span, grouped):
        """The rows of one window: the dimensions d0, d1, the value (a priced measure's quantity), a ratio's numerator
        and denominator, the share of an additive total, the records behind each row, and the number of rows."""
        q, m = self.query, self.measure
        if m['agg'] in SIMPLE or m['agg'] == 'priced':
            name = m['quantity'] if m['agg'] == 'priced' else q.measure
            inner, share, params = self._simple(name, dims, span, grouped)
            return f'SELECT *{share}, count(*) OVER () AS groups FROM ({inner}) t', params
        return self._ratio(dims, span, grouped)

    def _sql(self, dims, grouped):
        """One statement: the window's rows, and with compare the previous window's value beside each row (and the
        change of the total), joined in DuckDB on the row's dimensions; ordered and limited."""
        keys = [f'd{i}' for i in range(len(dims))]
        limit = self.query.limit if grouped else 1
        sql, params = self._body(dims, self.window, grouped)
        if self.previous is None:
            return f'SELECT * FROM ({sql}) cur' + self._order(keys) + ' LIMIT ?', params + [limit]
        before, before_params = self._body(dims, self.previous, grouped)
        on = ' AND '.join(f'cur.{k} IS NOT DISTINCT FROM prev.{k}' for k in keys) or 'true'
        change = '' if grouped else ', cur.value - prev.value AS delta'
        # a slice the previous window recorded nothing for is a recorded zero for a sum or a count (an unavailable previous
        # window reads null in `run`); a ratio of nothing has no value
        before_value = 'prev.value' if self.measure['agg'] == 'ratio' else 'coalesce(prev.value, 0)'
        sql = (f'SELECT cur.*, {before_value} AS previous_value{change} FROM ({sql}) cur LEFT JOIN ({before}) prev ON {on}'
               + self._order(keys) + ' LIMIT ?')
        return sql, params + before_params + [limit]

    def _ratio(self, dims, span, grouped):
        q, m, mdl = self.query, self.measure, self.mdl
        num, den = mdl.measures[m['numerator']], mdl.measures[m['denominator']]
        scale = m.get('scale', 1)
        n_part = _Part(mdl, num['entity'], dims, q.filters, q.shift, num)
        keys = [f'd{i}' for i in range(len(dims))]
        n_cols = [f'{sql} AS d{i}' for i, (_, sql) in enumerate(n_part.dims)]
        group = ' GROUP BY ALL' if n_cols else ''
        numerator, denominator = _aggregate(mdl, m['numerator']), _aggregate(mdl, m['denominator'])
        where = ' AND '.join(n_part.where)
        restricted = any(part.get(k) for part in (num, den) for k in ('filter', 'above_band'))
        levers = [f'{n_part.levers()} AS lever_keys'] if n_part.levers() else []   # the numerator's records name the lever
        if num['entity'] == den['entity'] and not restricted:
            select = ', '.join(n_cols + [f'{numerator} AS numerator', f'{denominator} AS denominator',
                                         'count(*) AS source_count', f'{n_part.ids()} AS source_ids'] + levers)
            sql = (f'SELECT *, numerator / nullif(denominator, 0) * {scale} AS value, count(*) OVER () AS groups FROM ('
                   f'SELECT {select} FROM {n_part.source()} WHERE {where}{group}) t')
            return sql, n_part.bind(*span)
        shared = [d for d in dims if mdl.reach(den['entity'], d) is not None]
        d_part = _Part(mdl, den['entity'], shared, q.filters, q.shift, den)
        d_cols = [f'{sql} AS d{dims.index(d)}' for d, sql in d_part.dims]
        n_select = ', '.join(n_cols + [f'{numerator} AS numerator', 'count(*) AS source_count', f'{n_part.ids()} AS source_ids'] + levers)
        n_levers = ', n.lever_keys AS lever_keys' if levers else ''
        n_sql = f'SELECT {n_select} FROM {n_part.source()} WHERE {where}{group}'
        d_select = ', '.join(d_cols + [f'{denominator} AS denominator'])
        d_sql = f"SELECT {d_select} FROM {d_part.source()} WHERE {' AND '.join(d_part.where)}" + (' GROUP BY ALL' if d_cols else '')
        on = [f'n.d{dims.index(d)} = d.d{dims.index(d)}' for d in shared]
        if dims and len(shared) == len(dims):
            # every slice is a slice of the denominator too (a day, a shift): the denominator's slices are the rows, and a
            # slice with no numerator records is a recorded zero (a day without a stop is no downtime, not a missing day)
            fill = 'coalesce(n.numerator, 0)' if num['agg'] in ADDITIVE else 'n.numerator'
            sql = (f"SELECT {', '.join([f'd.{k} AS {k}' for k in keys])}, {fill} AS numerator, d.denominator, "
                   f"{fill} / nullif(d.denominator, 0) * {scale} AS value, coalesce(n.source_count, 0) AS source_count, "
                   f"coalesce(n.source_ids, CAST([] AS VARCHAR[])) AS source_ids{n_levers}, count(*) OVER () AS groups "
                   f"FROM ({d_sql}) d LEFT JOIN ({n_sql}) n ON {' AND '.join(on)}")
            return sql, d_part.bind(*span) + n_part.bind(*span)
        join = f"LEFT JOIN ({d_sql}) d ON {' AND '.join(on)}" if on else f'CROSS JOIN ({d_sql}) d'
        sql = (f"SELECT {', '.join([f'n.{k} AS {k}' for k in keys] + ['n.numerator', 'd.denominator'])}, "
               f"n.numerator / nullif(d.denominator, 0) * {scale} AS value, n.source_count, n.source_ids{n_levers}, count(*) OVER () AS groups "
               f"FROM ({n_sql}) n {join}")
        return sql, n_part.bind(*span) + d_part.bind(*span)

    def _order(self, keys):
        order = self.query.order
        by_keys = ', '.join(f'{k} ASC NULLS LAST' for k in keys)
        if not keys:
            return ''
        if order == 'dimension':
            return f' ORDER BY {by_keys}'
        direction = 'DESC' if order == 'value' else 'ASC'
        return f' ORDER BY value {direction} NULLS LAST, {by_keys}'


def compile_query(query, mdl=None):
    """The statements a validated query runs (`validate` first: compile trusts its names)."""
    mdl = mdl or model()
    return Compiled(query, mdl)


# ------------------------------------------------------------------ running

def _plain(value):
    """A DuckDB value as JSON carries it: a date as YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, date):
        return value.isoformat()
    return value


def _label(mdl, dim, value):
    from .record_store import WEEKDAYS, fiscal_bounds
    if value is None:
        return 'Not recorded'
    spec = mdl.dimensions[dim]
    if spec.get('grain') == 'day':
        return f'{value.isoformat()} ({WEEKDAYS[value.weekday()]})'
    if spec.get('grain') == 'week':
        return f'Week of {value.isoformat()}'
    if spec.get('grain') == 'month':
        return value.strftime('%B %Y')
    if spec.get('grain') == 'fiscal':
        return fiscal_bounds(value)[2] + f' from {value.isoformat()}'
    if str(value) in (spec.get('value_labels') or {}):
        return spec['value_labels'][str(value)]
    if dim == 'shift':
        return f'Shift {value}'
    if dim == 'lane':
        return f'Lane {value}'
    if dim == 'web_width':
        return f'{value:g} in'
    return str(value)


def _band_of(mdl, query, dims_values):
    """The targets.json band a value of the measure is read against, or None: the measure's own band, or the one its
    band_by dimension's value names (the row's, else the filter's). A band is for a ratio of its own: a value sliced, by
    its row or by a filter, on a dimension its denominator does not carry is a share of the window's ratio (a reject family
    against all the yield, a grade's maintenance minutes against all the scheduled minutes), so it carries none, unless
    that dimension is the one that names the band (each stop kind its own). docs/AGENT_CONTRACT.md, the semantic shape."""
    m = mdl.measures[query.measure]
    band, names = m.get('band'), None
    rule = m.get('band_by')
    if band is None and rule:
        names = rule['dimension']
        value = dims_values.get(names, query.filters.get(names))
        band = rule['bands'].get(value) if isinstance(value, str) else None
    if band is None:
        return None
    if m['agg'] == 'ratio':
        denominator = mdl.measures[m['denominator']]['entity']
        if any(dim != names and mdl.reach(denominator, dim) is None for dim in (*dims_values, *query.filters)):
            return None
    return band


def _lever(mdl, query, dims_values, keys):
    """The lever of a value: the one lever every record it sums shares, read from the dimension its entity takes its lever
    from (a stop's kind: planned maintenance schedule-driven, a power outage external, the owner on PR #111 and
    targets.json; unplanned maintenance and operator controllable), whatever dimension slices it (a reason, a place). Its
    records' kinds are `keys`, listed by DuckDB. With records of several levers it is the measure's own; with none, the
    lever its row's or filter's value names, else the measure's."""
    rows = mdl.entities[mdl.measure_entities(query.measure)[0]]
    levers = mdl.dimensions[rows['lever_by']]['value_levers'] if rows.get('lever_by') else {}
    found = {levers.get(k) for k in keys or []}
    if len(found) == 1 and None not in found:
        return found.pop()
    if not keys:
        for values in (dims_values, query.filters):
            for dim, value in values.items():
                lever = (mdl.dimensions[dim].get('value_levers') or {}).get(value) if isinstance(value, str) else None
                if lever:
                    return lever
    return mdl.measures[query.measure]['lever']


def _scope_text(mdl, query):
    """The filters as words, one top-level string the answer prints: `at Double backer, stop kind Maintenance`."""
    parts = []
    for dim, value in query.filters.items():
        parts.append(f'{mdl.dimensions[dim]["label"].lower()} {_label(mdl, dim, value)}')
    return ', '.join(parts)


def run(query, store, rates=None, mdl=None):
    """Validate `query` against the model and the store, compile it, execute it, and return the result
    envelope: rows (one per group), the total, the previous window's beside them when asked, the receipt
    (trace: every statement's SQL and parameters; each row's source_ids and source_count), the window and its
    coverage, and for a priced measure the cost model's price fields and its rates."""
    mdl = mdl or model()
    started = time.perf_counter()
    query = validate(query, store, mdl)
    compiled = compile_query(query, mdl)
    m = compiled.measure
    window = store._window(query.window)
    previous = store._previous(window) if query.compare else None
    trace = {'sql': [], 'parameters': []}
    fetched = {}
    for statement in compiled.statements:
        fetched[statement.name] = store._fetch(statement.sql, statement.params, limit=MAX_LIMIT + 1)
        trace['sql'].append(statement.sql)
        trace['parameters'].append([_plain(p) for p in statement.params])
    rates = cost_model.effective(rates) if m['agg'] == 'priced' and (rates is None or 'values' not in rates) else rates
    basis = {b: (fetched[f'basis {b}'][0]['value'] if fetched.get(f'basis {b}') else None) for b in PRICE_BASIS} if m['agg'] == 'priced' else {}
    total_row = fetched['total'][0] if fetched['total'] and window['coverage'] != 'unavailable' else {}
    # a previous window outside the dataset is unavailable: its figures are null, never the zero its empty sums read
    available = previous is not None and previous['coverage'] != 'unavailable'
    rows = []
    for r in fetched.get('groups', []):
        values = {dim: r[f'd{i}'] for i, dim in enumerate(query.group_by)}
        row = {dim: _plain(v) for dim, v in values.items()}
        row['label'] = ' · '.join(_label(mdl, dim, v) for dim, v in values.items())
        row.update(_reading(mdl, query, r, rates, basis, values))
        if query.compare:
            row['previous_value'] = _previous(mdl, query, r, rates, basis) if available else None
        row['source_count'] = r['source_count']
        row['source_ids'] = list(r['source_ids'] or [])
        rows.append(row)
    groups = fetched['groups'][0]['groups'] if fetched.get('groups') else 0
    total = _reading(mdl, query, total_row, rates, basis, {}) if total_row else {'value': None}
    data = {'tool': TOOL, 'measure': query.measure, 'label': m['label'], 'unit': m['unit'], 'agg': m['agg'],
            'definition': m['description'], 'entity': mdl.measure_entities(query.measure)[0], 'group_by': list(query.group_by),
            'filters': [{'dimension': d, 'label': mdl.dimensions[d]['label'], 'value': _plain(v), 'text': _label(mdl, d, v)}
                        for d, v in query.filters.items()],
            'filter_text': _scope_text(mdl, query) or None, 'query': query.to_arguments(),
            'window': window, 'previous_window': previous, 'shift': query.shift,
            'dataset_version': store.content_hash, 'coverage': window['coverage'] if not previous else
            ('complete' if window['coverage'] == previous['coverage'] == 'complete' else 'unavailable' if window['coverage'] == 'unavailable' else 'partial'),
            'excluded_records': [], 'rows': rows, 'groups': groups, 'rows_shown': len(rows)}
    for key in ('value', 'numerator', 'denominator', 'quantity'):
        if key in total:
            data[key] = total[key]
    data['source_count'] = total_row.get('source_count', 0) if total_row else 0
    if m['agg'] == 'priced':
        data['quantity_unit'] = mdl.measures[m['quantity']]['unit']
        data['totals'] = [{'label': 'Total', **{k: v for k, v in total.items() if k != 'value'}, 'value': total.get('value')}]
    for key in ('band', 'band_text'):
        if key in total:
            data[key] = total[key]
    data['lever'] = total.get('lever') or _lever(mdl, query, {}, None)
    if query.compare:
        data['previous_value'] = _previous(mdl, query, total_row, rates, basis) if available and total_row else None
        # the change is DuckDB's (cur.value - prev.value), stated only when both windows are complete, as the tools do (a
        # partial window against a whole one is not like for like); a priced measure states both windows' dollars and no change
        comparable = window['coverage'] == 'complete' and previous['coverage'] == 'complete'
        data['comparison'] = 'complete' if comparable else 'withheld: a window is incomplete or unavailable'
        data['delta'] = total_row.get('delta') if comparable and total_row and m['agg'] != 'priced' else None
    data['source_rows'] = {'records': data['source_count'], 'groups': groups}
    trace['sql'] = ';\n'.join(trace['sql'])
    trace['elapsed_ms'] = (time.perf_counter() - started) * 1000
    data['trace'] = trace
    if m['agg'] == 'priced':
        cost_model._envelope(data, rates)
    return data


def _reading(mdl, query, r, rates, basis, dims_values):
    """One row's value fields: the value, and for a ratio its numerator and denominator, for a priced measure its
    quantity and the cost model's price fields, for an additive measure its share, and its band when it has one."""
    m = mdl.measures[query.measure]
    out = {}
    if m['agg'] == 'priced':
        quantity = r.get('value')
        price = cost_model.price(m['price_kind'], quantity, rates, basis.get('gross_sqft'), basis.get('scheduled_minutes'))
        out.update(quantity=quantity, value=price['usd'], **{k: v for k, v in price.items() if k != 'usd'}, usd=price['usd'])
    elif m['agg'] == 'ratio':
        out.update(value=r.get('value'), numerator=r.get('numerator'), denominator=r.get('denominator'))
    else:
        out['value'] = r.get('value')
    if 'share_pct' in r and m['agg'] != 'priced':
        out['share_pct'] = r['share_pct']
    band = _band_of(mdl, query, dims_values)
    if band:
        targets.mark(out, band, out['value'])
    out['lever'] = _lever(mdl, query, dims_values, r.get('lever_keys'))
    return out


def _previous(mdl, query, r, rates, basis):
    """The previous window's value beside a row: DuckDB's, or for a priced measure its quantity priced by the cost model."""
    value = r.get('previous_value')
    if value is None or mdl.measures[query.measure]['agg'] != 'priced':
        return value
    m = mdl.measures[query.measure]
    return cost_model.price(m['price_kind'], value, rates, basis.get('gross_sqft'), basis.get('scheduled_minutes'))['usd']


def execute(store, arguments, rates=None):
    """The tool's entry point (agent._execute): a plan's arguments in, the result envelope out."""
    if store is None:
        raise ValueError('The semantic model reads the record store; this dataset is a single-day fixture')
    return run(SemanticQuery.from_arguments(arguments), store, rates)


# ------------------------------------------------------------------ the docs page

def _band_words(band_id):
    spec = targets.BANDS[band_id]
    parts = [f'{name} {spec[key]:g}%' for name, key in (('home run', 'home_run'), ('target', 'target'), ('flag', 'flag')) if spec[key] is not None]
    return ' / '.join(parts) + f' {spec["unit"].replace("% ", "")}'


def _cell(text):
    return str(text).replace('|', '\\|').replace('\n', ' ')


def model_markdown(text=None):
    """`/docs/semantic-model`, rendered from the model file's text (docsite builds it at image build): every
    measure with its definition, unit, aggregation and band; every dimension; the synonyms; the entities, joins
    and the physical mapping."""
    spec = json.loads(text) if isinstance(text, str) else (text or json.loads(MODEL_PATH.read_text()))
    measures = {m['name']: m for m in spec['measures']}
    dims = spec['dimensions']
    out = ['# The semantic model', '',
           'Generated from `src/investigator/semantic_model.json` when the docs are built: '
           'edit the file, not this page.', '',
           spec['about'], '', spec['standard'], '',
           '## Asking the model', '',
           'A request names one measure, up to two dimensions to split it by, filters on dimensions, a window and a shift. '
           '`semantic.validate` refuses an unknown measure, dimension or filter value with the nearest valid choices; '
           '`semantic.compile_query` writes parameterized DuckDB SQL over the tables and columns the physical block names, '
           'with a LIMIT on every statement; `semantic.run` executes it under the store\'s two-second interrupt and returns '
           'rows plus a receipt: the SQL with its parameters, and the ids of the records behind each row. A comparison with the '
           'previous window splits by categories that recur: never by a time dimension, a paper run or an order, whose rows the '
           'window before cannot hold.', '',
           'The address of a request ([the URL grammar](../../docs/URLS.md)):', '',
           '    /corr/1/<grain>/<day>[/shift/<n>]?measure=semantic&m=<measure>&by=<dimension>[,<dimension>]&<dimension>=<value>',
           '',
           'for example `/corr/1/week/2026-09-14?measure=semantic&m=downtime_minutes&by=grade&kind=maintenance`: unplanned '
           'maintenance minutes by board grade over that week. A synonym is read as the name it stands for, and the address '
           'redirects to the model\'s own names.', '',
           '## Measures', '',
           'A ratio is a ratio of sums over the window, never an average of ratios. A priced measure is its quantity priced once '
           'by the cost model at the assumed rates ([cost model](../../docs/COST_MODEL.md)); the records hold no costs.', '',
           '| Measure | Definition | Unit | Aggregation | Band (targets.json) | Lever | Tool today |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for m in spec['measures']:
        if m['agg'] == 'ratio':
            agg = f'ratio: `{m["numerator"]}` / `{m["denominator"]}`' + (f' x {m["scale"]}' if m.get('scale', 1) != 1 else '')
        elif m['agg'] == 'priced':
            agg = f'priced: `{m["quantity"]}` as {m["price_kind"]}'
        else:
            agg = f'count of {m["entity"]} rows' if m['agg'] == 'count' else f'{m["agg"].replace("_", " ")} of `{m["expr"]}` over {m["entity"]} rows'
            conditions = [f'{k} is {" or ".join(v) if isinstance(v, list) else v}' for k, v in m.get('filter', {}).items()]
            if m.get('above_band'):
                rule = m['above_band']
                conditions.append(f'`{rule["expr"]}` over the {rule["band"]} {rule["line"]} ({targets.BANDS[rule["band"]][rule["line"]]:g}%)')
            agg += ''.join(f' where {c}' for c in conditions[:1]) + ''.join(f' and {c}' for c in conditions[1:])
        band = _band_words(m['band']) if m.get('band') else ''
        if m.get('band_by'):
            band = '; '.join(f'{value}: {_band_words(b)}' for value, b in m['band_by']['bands'].items())
        out.append(f'| **{m["label"]}** `{m["name"]}` | {_cell(m["description"])} | {_cell(m["unit"])} | {_cell(agg)} | {_cell(band) or "none"} | '
                   f'{m.get("lever", "")} | {_cell(m.get("tool", ""))} |')
    out += ['', '## Dimensions', '',
            'A dimension slices a measure when the measure\'s rows carry it, directly or through one many-to-one join '
            '(a stop belongs to one setup, so stops split by grade; a setup carries several order lanes, so setup feet never '
            'split by order). Time dimensions come from the window\'s grain, never a filter.', '',
            '| Dimension | Label | Recorded on | Definition |', '| --- | --- | --- | --- |']
    for d in dims:
        entity = 'every record' if d['entity'] == '*' else d['entity']
        out.append(f'| `{d["name"]}` | {_cell(d["label"])} | {entity} | {_cell(d["description"])} |')
    out += ['', '## Synonyms', '',
            'The plant\'s own words for a measure, a dimension or a dimension\'s value. Phase 2 of issue #117 has the offline '
            'router and the analyst read these instead of word lists of their own.', '',
            '| Word | Means |', '| --- | --- |']
    for m in spec['measures']:
        if m.get('synonyms'):
            out.append(f'| {_cell(", ".join(m["synonyms"]))} | measure `{m["name"]}` |')
    for d in dims:
        if d.get('synonyms'):
            out.append(f'| {_cell(", ".join(d["synonyms"]))} | dimension `{d["name"]}` |')
        for value, words in (d.get('value_synonyms') or {}).items():
            out.append(f'| {_cell(", ".join(words))} | {d["name"]} {_cell(value)} |')
    out += ['', '## Levers of a slice', '',
            'A value carries the lever every record it sums shares; a stop\'s lever is its kind\'s, so a reason or place takes the '
            'lever of its stops (Planned maintenance is schedule-driven, the place Power and the reason Power outage are external: '
            'the owner, PR #111; `targets.json`). Records of several levers carry the measure\'s own. The answer names a row\'s lever '
            'when it differs from the total\'s.', '',
            '| Dimension | Value | Lever |', '| --- | --- | --- |']
    for d in dims:
        for value, lever in (d.get('value_levers') or {}).items():
            out.append(f'| `{d["name"]}` | {_cell(value)} | {lever} |')
    out += ['', '## Entities and joins', '', '| Entity | Label | Key | Definition |', '| --- | --- | --- | --- |']
    for e in spec['entities']:
        out.append(f'| `{e["name"]}` | {_cell(e["label"])} | {_cell(e["key"])} | {_cell(e["description"])} |')
    out += ['', '| From | To | On | Relationship |', '| --- | --- | --- | --- |']
    for j in spec['joins']:
        out.append(f'| {j["from"]} | {j["to"]} | {", ".join(f"{a} = {b}" for a, b in j["on"])} | {j["relationship"].replace("_", " ")} |')
    physical = spec['physical']
    out += ['', '## The physical mapping', '', physical['about'], '', f'Record contract: `{physical["contract"]}`.', '',
            '| Entity | Table | Logical column | Store column |', '| --- | --- | --- | --- |']
    for name, entity in physical['entities'].items():
        for logical, column in entity['columns'].items():
            out.append(f'| {name} | `{entity["table"]}` | `{logical}` | ' + (f'`{_cell(column)}`' if column is not None else 'not carried yet') + ' |')
    out += ['', 'Each entity\'s receipt id (the column a row\'s `source_ids` lists, one per record summed) and the rows it reads:', '',
            '| Entity | Receipt id | Rows |', '| --- | --- | --- |']
    for name, entity in physical['entities'].items():
        out.append(f'| {name} | `{entity["id"]}` | ' + (f'where `{_cell(entity["where"])}`' if entity.get('where') else 'every row') + ' |')
    return '\n'.join(out) + '\n'
