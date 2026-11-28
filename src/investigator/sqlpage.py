"""The blocks of `/docs/sql` (issue #190): one question followed from its words to its SQL and its rows, and the tests
behind that SQL, every block produced when the docs are built.

`docs/SQL.md` types no SQL, parameter, plan or result row, and no number. Each `{{name}}` in it is filled here by the code
the app itself runs: the offline router (`agent.route`), the URL grammar (`urls`), the semantic model's validator and
compiler (`semantic.validate`, `semantic.compile_query`), the answer (`agent.Agent.ask`, as the server answers an address)
with its receipts (`datapages.attach_sources`), and one fixed tool (`RecordStore.get_downtime_pareto`). The test blocks are
read from the test files themselves: the hand-written oracle from `tests/test_examples.py`, the value golden's plain-Python
computation from `evals/make_value_goldens.py` with its expected values from `evals/golden_values.json`, the routing and
refusal cases from `evals/golden.json`, and the reading score from `examples.reading_score`.

The page states facts about those blocks, and each one is checked here, so the build fails rather than print a page that
disagrees with the code (`PageError`): the router reads the question as the verified example's plan, the SQL the answer
ran is the statements `compile_query` returns, each piece of the model the page shows is in that SQL, every bound value
is the value its label names, the fixed tool's question is a routing golden that runs that tool over the same week, the
oracle is the test's own and, run here on the worked week (the test runs it in the example's other windows), agrees with
the example as the test compares them, and the value golden's total is the answer's total.

The records are the golden window's (the window ending `evals/golden_values.json`'s `as_of`, as the image builds
`/app/prebuilt-golden`): the process's own store when it holds that window (pytest pins it), else the prebuilt folder
INVESTIGATOR_GOLDEN_PREBUILT names, else generated. The workbench passes its own store, read instead when its window
starts where the golden window starts and ends on or after it: every production day is generated from its own date-keyed
draws, so the golden window's days are the same records in it (tests/test_synth_determinism.py), and the page names the
window it was read from.
"""
import ast
from datetime import date
import json
import os
from pathlib import Path
import re

# The worked question, asked on the golden page (examples.SCORE_DAY, shift SCORE_SHIFT under the SCORE_SCOPE scope), and the
# verified example its reading must equal, in the relative window it names.
QUESTION = 'top 5 maintenance downtime reasons this week'
EXAMPLE = 'top-maintenance-downtimes'
EXAMPLE_WINDOW = 'this_week'
REFUSED_SPLIT = 'customer'        # a split the worked plan cannot take: a stop reaches no order lane, so no customer
# The other source of SQL: a routing golden whose question one fixed tool answers, over the same week.
FIXED_CASE = 'each-reason-minutes-is-the-pareto-by-reason'
FIXED_TOOL = 'get_downtime_pareto'
REFUSAL_CASE = 'semantic-refusal-lists-every-value'
# The value golden of the same week's maintenance minutes, split another way, and the function that computes it.
VALUE_CASE = 'value-semantic-downtime-by-grade-maintenance-week-0914'
VALUE_FUNCTION = 'semantic_downtime_by_grade_case'
SHOWN_IDS = 3                     # record ids a rows table prints of each row's receipt; the receipt link holds them all
ORACLE_TOLERANCE = 0.01           # tests/test_examples.py compares the example with its oracle within this (pytest.approx abs)


class PageError(ValueError):
    """A block of the page could not be rendered, or the page would say something the code does not do."""


# ------------------------------------------------------------------ the store the page reads

_STORES = {}


def golden_window(root):
    from .record_store import dataset_window
    as_of = json.loads((Path(root) / 'evals' / 'golden_values.json').read_text(encoding='utf-8'))['as_of']
    return dataset_window(as_of=as_of)


def golden_store(root):
    """The golden window's store: the process's own when it holds that window, else the image's prebuilt golden folder, else
    generated once per process (about twenty seconds)."""
    from .record_store import RecordStore, dataset_window, default_store
    window = golden_window(root)
    if dataset_window() == window:
        return default_store()
    if window not in _STORES:
        _STORES[window] = RecordStore(window=window, prebuilt=os.environ.get('INVESTIGATOR_GOLDEN_PREBUILT', ''))
    return _STORES[window]


def usable(store, root):
    """Whether a given store reads the page as the golden window's does: it starts on the golden window's first day and ends
    on or after its last, so every day the page reads is the same record in it (a window's days never move, issue #35)."""
    if store is None:
        return False
    first, last = golden_window(root)
    return tuple(store.window[:1]) == (first,) and store.window[1] >= last


# ------------------------------------------------------------------ Markdown helpers

def _fence(text, lang=''):
    body = str(text)
    if '```' in body:
        raise PageError('a rendered block holds a code fence')
    return f'```{lang}\n{body}\n```'


def _json(value):
    return _fence(json.dumps(value, indent=1, ensure_ascii=False, default=str), 'json')


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _code(value):
    text = str(value)
    return '`' + text.replace('`', "'") + '`' if text else ''


def _number(value):
    """A value as DuckDB returned it: a float in full (repr), an integer as an integer."""
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _table(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '| ' + ' | '.join('---' for _ in head) + ' |']
    out += ['| ' + ' | '.join(_cell(c) for c in row) + ' |' for row in rows]
    return '\n'.join(out)


def _quote(sections):
    """The card's sections as a block quote, one paragraph each, as the app prints them."""
    lines = []
    for text in sections:
        lines += [f'> {line}' if line.strip() else '>' for line in str(text).split('\n')] + ['>']
    return '\n'.join(lines[:-1])


def _function_source(path, name):
    """The source of one top-level function of a Python file, as written."""
    text = Path(path).read_text(encoding='utf-8')
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return '\n'.join(text.split('\n')[node.lineno - 1:node.end_lineno])
    raise PageError(f'{Path(path).name} has no function {name}: the page shows it')


def _constant(path, name):
    for node in ast.parse(Path(path).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise PageError(f'{Path(path).name} has no constant {name}')


def _case(root, path, case_id):
    cases = json.loads((Path(root) / path).read_text(encoding='utf-8'))['cases']
    found = [c for c in cases if c.get('id') == case_id]
    if len(found) != 1:
        raise PageError(f'{path} holds no case {case_id!r}: the page shows it')
    return found[0]


def _bound(statement, labels):
    """A statement's bound values beside the `?` each fills, in order. `labels` are (the value the page expects, what it is):
    every value must be named, and be the value its name says."""
    if len(labels) != len(statement.params) or statement.sql.count('?') != len(statement.params):
        raise PageError(f'the {statement.name} statement binds {len(statement.params)} values for '
                        f'{statement.sql.count("?")} placeholders, and the page names {len(labels)}')
    for value, (expected, label) in zip(statement.params, labels):
        if value != expected:
            raise PageError(f'the {statement.name} statement binds {value!r} where the page names {label} ({expected!r})')
    rows = []
    for i, (value, (_, label)) in enumerate(zip(statement.params, labels)):
        shown = value.isoformat() if isinstance(value, date) else _number(value)
        rows.append([str(i + 1), _code(shown), label])
    return _table(['Placeholder', 'Bound value', 'What it binds'], rows)


# ------------------------------------------------------------------ the page

def figures(root, store=None):
    """{name: Markdown} for every `{{name}}` of docs/SQL.md. `store` is used when it holds the days the page reads, else the
    golden window's store. Raises PageError (or the code's own error) when a block cannot be rendered or a check fails."""
    from . import agent, datapages, examples, semantic, urls
    from .record_store import parse_window
    from .docsite import RENDERED_SOURCES
    root = Path(root)
    for source in RENDERED_SOURCES:
        if not (root / source).is_file():
            raise PageError(f'{source} is not under {root}: the page reads it')
    mdl = semantic.model()
    day, shift, scope = examples.SCORE_DAY, examples.SCORE_SHIFT, examples.SCORE_SCOPE
    by_id = {e['id']: e for e in examples.load(mdl=mdl)}
    example = by_id[EXAMPLE]
    (want,), _ = examples.queries(example, day, EXAMPLE_WINDOW, mdl)
    _, _, start, end = parse_window(want['window'])
    fixed_case = _case(root, 'evals/golden.json', FIXED_CASE)
    if not usable(store, root):
        store = golden_store(root)
    out = {'page.day': date.fromisoformat(day).strftime('%A %-d %B %Y'), 'page.shift': str(shift),
           'store.first': store._first.isoformat(), 'store.last': store._last.isoformat(),
           'question': _code(QUESTION), 'example.id': _code(EXAMPLE), 'example.question': f'"{example["question"]}"'}

    # 1. The reading: the router's plan, which must be the verified example's.
    context = agent.Context(day, shift, scope=scope, period='day')
    request, read_context = agent.route(QUESTION, context, store.production_days, store)
    plan = request.get('plan') or []
    if request.get('intent') != 'semantic' or len(plan) != 1 or plan[0]['tool'] != semantic.TOOL:
        raise PageError(f'the router no longer reads {QUESTION!r} into one semantic query: {request.get("intent")}')
    arguments = plan[0]['arguments']
    try:   # what the hand router alone makes of the words, as parse_request runs it before the semantic parser reads them
        hand = agent._parse_request(QUESTION, read_context)
        agent.catch_all_guard(agent.correct_typos(QUESTION.lower()), hand)
    except ValueError as exc:
        out['hand.reading'] = f'would refuse it ("{_cell(exc)}")'
    else:
        tools = [s['tool'] for s in hand.get('plan') or []]
        if semantic.TOOL in tools:
            raise PageError('the hand router now answers the worked question itself: the page says the parser does')
        out['hand.reading'] = (f'would answer it with {", ".join(_code(t) for t in tools)}, a review of every stop reason rather than the '
                               'ranking asked,' if tools else f'would answer it as its {_code(hand.get("intent"))} card, not the ranking asked,')
    read = {k: arguments.get(k) for k in want}
    if read != {**want, 'window': read['window']} or semantic.parse_window(read['window'])[2:] != (start, end):
        raise PageError(f'the router reads {QUESTION!r} as {read}, not as the verified example {EXAMPLE} in {EXAMPLE_WINDOW}: {want}')
    out['plan'] = _json(arguments)
    out['window.start'], out['window.end'] = start.isoformat(), end.isoformat()

    # The answer, as the server answers its address: the URL resolved against the store, the agent, the receipts.
    address = urls.canonical_url(request, read_context, machine_id=store.machine_id)
    resolved = urls.resolve_url(address, store)
    resolved['context'].history = resolved['history']
    analysis = store.day_analysis(resolved['day'], corrupt=resolved['corrupt'])
    try:
        result = agent.Agent(analysis).ask(resolved['text'], resolved['context'], request=resolved['request'])
    finally:
        analysis.close()
    datapages.attach_sources(result, resolved['corrupt'])
    steps = [e for e in result['trace'] if e.get('ok') and e['tool'] == semantic.TOOL]
    if result.get('status') != 'complete' or len(steps) != 1:
        raise PageError(f'the answer to {address} did not run one semantic query: {result.get("status")}')
    entry = steps[0]
    data = entry['data']
    out['address'] = f'[{_code(address)}]({address})'
    read_as = [s for s in result['sections'] if s.startswith('Read as:')]
    if len(read_as) != 1:
        raise PageError('the answer has no Read as line')
    out['read_as'] = _quote(read_as)
    if re.search(r'\b(first|top) ', read_as[0]):
        out['read_as.limit'] = 'The line names the limit too.'
    elif data['groups'] <= arguments['limit']:
        out['read_as.limit'] = (f'The line leaves the limit out because it cuts nothing: this week the split has {data["groups"]} rows, no '
                                f'more than the limit, so every row is listed. The limit is in the plan and in the address (`top=`).')
    else:
        raise PageError('the Read as line leaves out a limit that cuts rows')
    kinds = semantic.store_values('kind', store, mdl) or []
    out['kinds'] = ', '.join(_code(k) for k in kinds)
    if valid_kind := arguments['filters'].get('kind'):
        if valid_kind not in kinds:
            raise PageError(f'the records hold no stop kind {valid_kind}')
    out['card'] = _quote([s for s in result['sections'] if not s.startswith('Read as:')][:1])
    out['card.tools'] = ', '.join(_code(e['tool']) for e in result['trace'])
    ref = re.search(r'\(query (\d+) · row (\d+)\)', result['sections'][0])
    step = result['trace'].index(entry) + 1
    if not ref or (int(ref.group(1)), int(ref.group(2))) != (step, 0):
        raise PageError('the first finding does not cite the first row of the semantic query')
    out['card.ref'] = (f'`query {step} · row 0` is the first row of step {step}, the semantic query\'s rows statement. Steps count from 1 '
                       'in the order they ran, and rows from 0 in the order the step returned them.')

    # 2. Validation: the plan as validate returns it, and one plan it refuses.
    valid = semantic.validate(arguments, store)
    refused_plan = {**arguments, 'group_by': [REFUSED_SPLIT]}
    try:
        semantic.validate(refused_plan, store)
    except semantic.SemanticError as exc:
        out['refused.plan'] = _json(refused_plan)
        out['refused.message'] = _quote([str(exc)])
    else:
        raise PageError(f'validate accepted a split by {REFUSED_SPLIT}: the page shows it refused')
    out['refused.split'] = _code(REFUSED_SPLIT)

    # 3. The model entries the compiler reads, and what each becomes in the SQL.
    measure = mdl.measures[valid.measure]
    spec = json.loads(semantic.MODEL_PATH.read_text(encoding='utf-8'))
    entries = {'measure': next(m for m in spec['measures'] if m['name'] == valid.measure),
               'dimensions': [{k: d[k] for k in ('name', 'label', 'type', 'entity', 'expr') if k in d}
                              for d in spec['dimensions'] if d['name'] in (*valid.group_by, *valid.filters)],
               'physical': {measure['entity']: spec['physical']['entities'][measure['entity']]}}
    entries['measure'] = {k: v for k, v in entries['measure'].items() if k not in ('synonyms', 'tool')}
    out['model.measure'] = _json(entries['measure'])
    out['model.dimensions'] = _json(entries['dimensions'])
    out['model.physical'] = _json(entries['physical'])
    out['model.entity'] = _code(measure['entity'])
    out['model.table'] = _code(spec['physical']['entities'][measure['entity']]['table'])
    part = semantic._Part(mdl, measure['entity'], valid.group_by, valid.filters, valid.shift, measure)
    pieces = [('the measure', _code(entries['measure']['expr']) + f', aggregated as {entries["measure"]["agg"]}',
               semantic._aggregate(mdl, valid.measure)),
              ('the table it reads', f'{_code(measure["entity"])} in the physical block', part.source()),
              ('the window', 'every entity\'s `production_date`', part.where[0])]
    pieces += [(f'the split by {dim}', _code(mdl.dimensions[dim]['expr']), sql) for dim, sql in part.dims]
    pieces += [(f'the filter on {dim}', _code(mdl.dimensions[dim]['expr']) + ' equal to a bound value', f'{part._dimension(dim)} = ?')
               for dim in valid.filters]
    pieces.append(('the receipt', f'the entity\'s id, {_code(spec["physical"]["entities"][measure["entity"]]["id"])}', part.ids()))

    # 4. The compiled statements, exactly as compile_query returns them, and the proof that they are what the answer ran.
    fill = bool(valid.group_by) and (mdl.dimensions[valid.group_by[0]]['type'] == 'time' or semantic.store_values(valid.group_by[0], store, mdl) is not None)
    compiled = semantic.compile_query(valid, mdl, zero_fill=fill, bounds=(store._first, store._last))
    ran = data['trace']
    if ran['sql'] != ';\n'.join(s.sql for s in compiled.statements) or ran['parameters'] != [[semantic._plain(p) for p in s.params] for s in compiled.statements]:
        raise PageError('the SQL the answer ran is not the statements compile_query returns for its plan')
    whole = '\n'.join(s.sql for s in compiled.statements)
    for name, _, sql in pieces:
        if sql not in whole:
            raise PageError(f'{name} ({sql}) is not in the compiled SQL')
    out['pieces'] = _table(['Piece', 'In the model', 'In the SQL'], [[n, m, _code(s)] for n, m, s in pieces])
    labels_of = {'groups': 'the rows, one per reason', 'total': 'the total over every row'}
    if [s.name for s in compiled.statements] != list(labels_of):
        raise PageError(f'compile_query returns other statements than the page describes: {[s.name for s in compiled.statements]}')
    blocks = []
    for statement in compiled.statements:
        labels = [(start, 'the window\'s first production day'), (end, 'the window\'s last production day')]
        if valid.shift is not None:
            labels.append((valid.shift, 'the shift'))
        labels += [(value, f'the {dim} filter') for dim, value in valid.filters.items()]
        labels.append((statement.params[-1], 'the rows the statement returns at most') if statement.name == 'groups' else (1, 'one row'))
        blocks.append(f'**{_cell(statement.name)}**: {labels_of.get(statement.name, statement.name)}.\n\n{_fence(statement.sql, "sql")}\n\n'
                      + _bound(statement, labels))
    out['compiled'] = '\n\n'.join(blocks)
    out['compiled.count_words'] = ' and '.join(_code(s.name) for s in compiled.statements)
    out['trace.sql'] = _fence(ran['sql'], 'sql')
    out['trace.parameters'] = _fence(json.dumps(ran['parameters']), 'json')

    # 5. The rows, with their receipts.
    rows_links = (entry.get('source') or {}).get('rows', {}).get('row') or []
    if len(rows_links) != len(data['rows']) or not all(rows_links):
        raise PageError('a row of the answer has no receipt link')
    dim = valid.group_by[0]
    table = []
    for row, link in zip(data['rows'], rows_links):
        ids = ', '.join(_code(i) for i in row['source_ids'][:SHOWN_IDS]) + (', …' if len(row['source_ids']) > SHOWN_IDS else '')
        table.append([row['label'], _code(_number(row['value'])), _code(_number(row['share_pct'])), _code(row['source_count']), ids,
                      f'[the rows]({link})'])
    table.append(['**Total**', _code(_number(data['value'])), '', _code(data['source_count']), '', f'[the rows]({entry["source"]["url"]})'])
    out['rows'] = _table([mdl.dimensions[dim]['label'], f'Value ({measure["unit"]})', 'Share %', '`source_count`',
                          '`source_ids` (the first)', 'Receipt in /data'], table)
    out['rows.first'] = _code(data['rows'][0]['label'])
    out['receipt.cap'] = str(semantic.SOURCE_ID_CAP)
    out['receipt.url'] = f'[{_code(entry["source"]["url"])}]({entry["source"]["url"]})'
    out['receipt.first'] = f'[{_code(rows_links[0])}]({rows_links[0]})'

    # The other source of SQL: a fixed tool, through its routing golden's own question.
    fixed_question = fixed_case['question']
    fixed_request, fixed_context = agent.route(fixed_question, agent.Context(fixed_case.get('day', day), fixed_case.get('shift', shift),
                                                                          scope=fixed_case.get('scope', scope), period=fixed_case.get('period', 'day')),
                                               store.production_days, store)
    calls = fixed_request.get('plan') or []
    if [c['tool'] for c in calls] != [FIXED_TOOL] or not any(t['tool'] == FIXED_TOOL for t in fixed_case['expect'].get('tools', [])):
        raise PageError(f'{FIXED_CASE} is no longer answered by {FIXED_TOOL}')
    fixed_address = urls.canonical_url(fixed_request, fixed_context, machine_id=store.machine_id)
    if fixed_address != fixed_case['expect']['url']:
        raise PageError(f'{FIXED_CASE} resolves to {fixed_address}, not its golden address {fixed_case["expect"]["url"]}')
    call = calls[0]['arguments']
    if parse_window(call['window'])[2:] != (start, end):
        raise PageError(f'{FIXED_CASE} reads another week than the worked question')
    pareto = getattr(store, FIXED_TOOL)(**call)
    fixed_entry = {'ok': True, 'tool': FIXED_TOOL, 'arguments': call, 'data': pareto}
    fixed_source = datapages.source(fixed_entry, fixed_case.get('day', day))
    out['fixed.question'] = f'"{fixed_question}"'
    out['fixed.address'] = f'[{_code(fixed_address)}]({fixed_address})'
    out['fixed.tool'] = _code(FIXED_TOOL)
    out['fixed.call'] = _json({'tool': FIXED_TOOL, 'arguments': call})
    sql = pareto['trace']['sql'].split(';\n')   # the envelope joins a tool's statements as the query box shows them
    if len(set(sql)) != 1 or len(sql) != len(pareto['trace']['parameters']):
        raise PageError(f'{FIXED_TOOL} ran more than one statement text')
    windows = [(pareto['window'], 'the week asked'), (pareto['previous_window'], 'the week before')]
    runs = pareto['trace']['parameters']
    if len(runs) != len(windows) or any(p[:2] != [w['start'], w['end']] for p, (w, _) in zip(runs, windows)):
        raise PageError(f'{FIXED_TOOL} no longer runs once for the week asked and once for the week before')
    links = (fixed_source.get('rows') or {}).get('row') or []
    if len(links) != len(pareto['rows']) or not all(links):
        raise PageError(f'a row of {FIXED_TOOL} has no receipt link')
    out['fixed.sql'] = _fence(sql[0], 'sql')
    out['fixed.parameters'] = _table(['Run', 'Bound values'], [[label, _code(json.dumps(p))] for p, (_, label) in zip(runs, windows)])
    out['fixed.group_by'] = _code(call['group_by'])
    out['fixed.rows'] = _table(['Category', 'Stops', 'Minutes', 'Share %', 'Minutes the week before', 'Receipt in /data'],
                               [[r['category'], _code(r['stops']), _code(_number(r['minutes'])), _code(_number(r['share_pct'])),
                                 _code(_number(r['previous_minutes'])), f'[the rows]({link})'] for r, link in zip(pareto['rows'], links)])
    out['fixed.case'] = _json({k: fixed_case[k] for k in ('id', 'type', 'question') if k in fixed_case}
                              | {'expect': {k: fixed_case['expect'][k] for k in ('url', 'tools') if k in fixed_case['expect']}})
    out['fixed.case_id'] = _code(FIXED_CASE)

    # The tests. Value golden: its case and the plain-Python function that wrote its numbers.
    value_case = _case(root, 'evals/golden_values.json', VALUE_CASE)
    total = value_case['expect']['values'].get('$', {}).get('value')
    if value_case['arguments']['window'] != want['window'] or value_case['arguments']['filters'] != valid.filters or not total \
            or abs(total[0] - data['value']) > total[1] + 1e-9:
        raise PageError(f'{VALUE_CASE} is no longer the worked week\'s maintenance minutes, or its total is not the answer\'s')
    out['value.id'] = _code(VALUE_CASE)
    out['value.arguments'] = _json({'tool': value_case['tool'], 'arguments': value_case['arguments']})
    out['value.expect'] = _table(['Row', 'Field', 'Expected', 'Tolerance'],
                                 [[_code(sel), _code(f), _code(_number(v[0])), _code('exact' if v[1] is None else _number(v[1]))]
                                  for sel, fields in value_case['expect']['values'].items() for f, v in fields.items()])
    out['value.total'] = _code(_number(total[0]))
    out['answer.total'] = _code(_number(data['value']))
    out['value.function'] = _fence(_function_source(root / 'evals/make_value_goldens.py', VALUE_FUNCTION), 'python')
    out['value.function_name'] = _code(VALUE_FUNCTION)

    # Verified examples: the oracle for this example, from the test file.
    oracle = EXAMPLE.replace('-', '_')
    out['oracle'] = _fence(_function_source(root / 'tests/test_examples.py', oracle), 'python')
    out['oracle.name'] = _code(oracle)
    anchors = _constant(root / 'tests/test_examples.py', 'ANCHORS')
    out['oracle.anchors'] = ' and '.join(_code(a) for a in anchors)
    labels = {w['name']: w['label'] for w in mdl.spec['defaults']['relative_windows']}
    out['oracle.windows'] = ', '.join(f'{_code(w)} ({labels.get(w, w).lower()})' for w in [example['plan']['window'], *example.get('alternates', [])])
    stand_in = [w for w in mdl.spec['defaults']['relative_windows'] if w.get('interim') and w['period'] not in semantic.WINDOW_PERIODS]
    out['oracle.stand_in'] = (' '.join(f'The model does not read the {w["label"].lower()} yet, so the example\'s {_code(w["name"])} is asked in '
                                       f'a stand-in window, here and in the test. The model file says: "{_cell(w["description"])}"' for w in stand_in)
                              or 'Every window the example names is one the model reads.')
    # The oracle run here on the worked week, beside the example's own answer, compared as the test compares them.
    namespace = {'date': date}
    exec(compile(_function_source(root / 'tests/test_examples.py', 'q') + '\n\n' + _function_source(root / 'tests/test_examples.py', oracle),
                 'tests/test_examples.py', 'exec'), namespace)
    want_rows = namespace[oracle](store, start, end)
    got = examples.run_example(example, store, day, EXAMPLE_WINDOW, mdl)
    if (got['window']['start'], got['window']['end']) != (start.isoformat(), end.isoformat()) or not want_rows \
            or [tuple(r['key'].values()) for r in got['rows']] != [w[0] for w in want_rows]:
        raise PageError(f'the oracle and the example {EXAMPLE} list other rows for the worked week')
    oracle_rows = []
    for row, (key, value, other) in zip(got['rows'], want_rows):
        fields = {'value': (row['value'], value), **{f: (row.get('beside', {}).get(f, row.get(f)), v) for f, v in other.items()}}
        if any(a is None or abs(a - b) > ORACLE_TOLERANCE for a, b in fields.values()):
            raise PageError(f'the oracle and the example {EXAMPLE} disagree on {key} in the worked week: {fields}')
        oracle_rows.append([row['label']] + [f'{_code(_number(a))} · {_code(_number(b))}' for a, b in fields.values()])
    if [w[0] for w in want_rows] != [(r[dim],) for r in data['rows']] or any(abs(w[1] - r['value']) > ORACLE_TOLERANCE for w, r in zip(want_rows, data['rows'])):
        raise PageError('the oracle\'s rows for the worked week are not the answer\'s rows')
    out['oracle.rows'] = _table([mdl.dimensions[dim]['label'], *[f'{f} (example · oracle)' for f in fields]], oracle_rows)
    out['oracle.beside'] = ', '.join(_code(b) for b in example.get('beside', [])) or 'none'
    out['oracle.tolerance'] = _code(_number(ORACLE_TOLERANCE))
    if f'abs={ORACLE_TOLERANCE}' not in _function_source(root / 'tests/test_examples.py', 'test_example_equals_its_oracle'):
        raise PageError(f'test_example_equals_its_oracle no longer compares within {ORACLE_TOLERANCE}')
    out['oracle.test'] = _code('test_example_equals_its_oracle')
    if 'def test_example_equals_its_oracle' not in (root / 'tests/test_examples.py').read_text(encoding='utf-8'):
        raise PageError('tests/test_examples.py has no test_example_equals_its_oracle')
    out['example.plan'] = _json(example['plan'])

    # Routing and refusal goldens.
    refusal = _case(root, 'evals/golden.json', REFUSAL_CASE)
    out['refusal.case'] = _json({k: refusal[k] for k in ('id', 'type', 'url', 'expect') if k in refusal})
    out['refusal.case_id'] = _code(REFUSAL_CASE)

    # The reading score of the worked example: its owner's words and its rewordings, as the router reads them.
    score = examples.reading_score(store, mdl=mdl, examples=[example])
    out['score.rows'] = _table(['Wording', 'Whose', 'Window', 'Outcome', 'Read as'],
                               [[f'"{r["text"]}"', 'the owner\'s' if r['owner'] else 'a rewording', _code(r['window'] or 'none said'),
                                 r['outcome'], r['read_as']] for r in score['rows']])
    return out


BLOCK = re.compile(r'([ \t]*)\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}[ \t]*')


def check_source(text):
    """Lines of docs/SQL.md that type what the page renders: a code block of its own (fenced, or indented as Markdown's code is),
    or a number anywhere, inline code included (the command name `python3` aside). Every block and number is a `{{name}}`."""
    from .docsite import typed_numbers
    problems = [f'line {n}: a code block typed into the page' for n, line in enumerate(text.split('\n'), 1)
                if re.match(r'^\s*(```|~~~)', line) or (re.match(r'^( {4}|\t)', line) and not BLOCK.fullmatch(line))]
    problems += typed_numbers(re.sub(r'\bpython3\b', 'python', text))
    return problems


def expand(text, values, source='docs/SQL.md'):
    """`text` with each `{{name}}` filled: a figure alone on its line keeps the line's indentation on every line it fills."""
    def known(name):
        if name not in values:
            raise KeyError(f'{source}: no block named {name!r}; sqlpage.figures has {sorted(values)}')
        return values[name]
    from .docsite import FIGURE
    out = []
    for line in text.split('\n'):   # line by line over the source, so a filled block is never read again for figures
        block = BLOCK.fullmatch(line)
        if block:
            out.append('\n'.join(block.group(1) + part if part else part for part in known(block.group(2)).split('\n')))
        else:
            out.append(FIGURE.sub(lambda m: known(m.group(1)), line))
    return '\n'.join(out)


def render(text, root, store=None):
    """docs/SQL.md with every block filled; raises PageError when the source types a block or a number, or a block fails."""
    typed = check_source(text)
    if typed:
        raise PageError(f'docs site: docs/SQL.md renders every SQL block, plan, row and number at build, but these lines type one: {typed}')
    return expand(text, figures(root, store))
