"""The data browser (issue #142): /data lists the tables every answer reads, /data/<table> shows a table's schema and
its rows, and an answer's receipts link to the rows behind each figure.

The schema pages are held to the record contract: every column a page shows has a unit and a meaning, and its row in
the contract (docs/RECORD_CONTRACT.md; docs/METRICS.md for the derived shift_kpis) exists and carries the anchor the
page links to. Filters are fuzzed: no value or name reaches the SQL text. Every receipt of every offline golden answer
links to rows, and those rows hold the cited row's own quantity (its sum, count or ratio over them).
"""
import gc
import http.client
import json
import math
import random
import re
import time
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl, quote

import pytest

from investigator import datapages as dp
from investigator import docsite
from investigator.record_store import RecordStore, default_store
from investigator.server import PublicDemo, Workbench, make_server

ROOT = Path(__file__).resolve().parents[1]
REF = re.compile(r' \(query (\d+)(?: · (row|detail|weekday|flag) (\d+))?\)')


@pytest.fixture(scope='module')
def store():
    """The process's shared store, left as this module found it: answering every golden URL unpickles a few dozen days of
    record objects into its day cache, and keeping them would make every later test's garbage collection walk them. On a
    slow CI runner that was enough to push the /evals run-all's slowest case past its 250 ms budget (CI on #151)."""
    shared = default_store()
    days = dict(shared._by_day)
    yield shared
    shared._by_day.clear()
    shared._by_day.update(days)
    gc.collect()


# ------------------------------------------------------------------ the schema is the contract's

def _anchors(source):
    """The row anchors the docs site gives a page: `col-<table>-<column>` for each column a contract table names."""
    by_source = {p['source']: p['url'] for p in docsite.pages()}
    page = next(p for p in docsite.pages() if p['source'] == source)
    _, body, _ = docsite.render_page(page, (ROOT / source).read_text(encoding='utf-8'), by_source)
    return set(re.findall(r'id="(col-[a-z_]+-[a-z_]+)"', body)), body


def test_every_shown_column_has_a_unit_a_meaning_and_its_contract_row(store):
    """A column the browser shows without a documented meaning fails here: the catalog lists exactly the store's
    columns, each with a unit and a meaning, and each column is a row of its table in the contract, whose anchor is
    the link the schema page gives it."""
    anchors, bodies = {}, {}
    for source in ('docs/RECORD_CONTRACT.md', 'docs/METRICS.md'):
        found, body = _anchors(source)
        anchors[source], bodies[source] = found, body
        assert len(re.findall(r'id="(col-[a-z_]+-[a-z_]+)"', body)) == len(found), f'{source}: a row anchor is repeated'
    for table in dp.TABLES:
        live = [r[0] for r in store._db.execute(f'DESCRIBE {table}').fetchall()]
        assert [name for name, _ in dp.TYPES[table]] == live, table
        assert set(dp.COLUMNS[table]) == set(live), (table, set(live) ^ set(dp.COLUMNS[table]))
        source = 'docs/METRICS.md' if dp.TABLES[table].docs == dp.METRICS else 'docs/RECORD_CONTRACT.md'
        for column in dp.columns(table):
            assert column['unit'].strip() and len(column['meaning'].strip()) > 8, (table, column['name'])
            assert column['contract'] == f'{dp.TABLES[table].docs}#col-{table}-{column["name"]}'
            assert f'col-{table}-{column["name"]}' in anchors[source], \
                f'{table}.{column["name"]} has no row in {source}: document it there, in its table, before the browser shows it'
        assert dp.TABLES[table].meaning.endswith('.') and len(dp.TABLES[table].meaning) > 40
        assert f'id="col-{table}"' in bodies[source], f'{table}: its column table in {source} has no anchor'   # the page's contract link


def test_every_tool_declares_the_table_it_read():
    """A new tool must say which rows it read before its receipts can link: SOURCES covers every tool with rows."""
    from investigator.agent import TOOL_SCHEMAS
    tools = {s['name'] for s in TOOL_SCHEMAS} - {'render_chart'}
    assert tools <= set(dp.SOURCES), tools - set(dp.SOURCES)
    for tool, (table, parts) in dp.SOURCES.items():
        assert table in dp.TABLES and set(parts) <= set(dp.PARTS), tool


# ------------------------------------------------------------------ the index and a table page

def test_the_index_lists_every_table_with_its_rows_and_days(store):
    out = dp.index(store)
    names = [t['name'] for t in out['tables']]
    assert names == list(dp.TABLES)
    for t in out['tables']:
        n = store._db.execute(f'SELECT count(*) FROM {t["name"]}').fetchone()[0]
        assert t['rows'] == n > 0 and t['meaning'] and t['url'] == f'/data/{t["name"]}'
        if dp.TABLES[t['name']].dated:
            assert (t['first_day'], t['last_day']) == (store.first_day, store.last_day), t
        else:
            assert t['first_day'] is None and t['last_day'] is None
    assert out['window'] == {'first_day': store.first_day, 'last_day': store.last_day, 'as_of': store.as_of.isoformat()}


def test_a_table_page_is_the_schema_and_fifty_rows_newest_first(store):
    first = dp.page(store, 'stops', '')
    assert first['per_page'] == 50 and len(first['rows']) == 50 and first['count'] == store._db.execute('SELECT count(*) FROM stops').fetchone()[0]
    starts = [r['started'] for r in first['rows']]
    assert starts == sorted(starts, reverse=True) and first['rows'][0]['production_date'] == store.last_day
    assert first['newer'] is None and first['older'] == '/data/stops?page=2'
    # newest first is the reverse of the order the store wrote the rows in: that order is production order, every table
    for table, t in dp.TABLES.items():
        if not t.dated:
            continue   # a dimension reads in the order of its file
        keys = [k.split()[0] for k in t.order.split(', ')]
        written = store._db.execute(f'SELECT {", ".join(keys)} FROM {table} ORDER BY rowid').fetchall()
        assert written == sorted(written), f'{table}: the rows are not stored in production order, so rowid paging would not be newest first'
    second = dp.page(store, 'stops', 'page=2')
    assert second['rows'][0]['started'] <= starts[-1] and not {r['stop_id'] for r in second['rows']} & {r['stop_id'] for r in first['rows']}
    assert [c['name'] for c in first['columns']] == [name for name, _ in dp.TYPES['stops']]
    assert all('?' in first['sql'] and 'LIMIT ? OFFSET ?' in first['sql'] for _ in [0])
    # filters live in the URL, in one canonical spelling
    day = dp.page(store, 'stops', 'reason=missed%20splice&shift=2&day=2026-09-14')
    assert day['url'] == '/data/stops?day=2026-09-14&shift=2&reason=missed%20splice'
    assert day['rows'] and all(r['production_date'] == '2026-09-14' and r['shift'] == 2 and r['reason'] == 'Missed splice' for r in day['rows'])
    assert day['count'] == len(day['rows'])
    order = store._db.execute("SELECT order_number FROM setup_orders WHERE production_date = '2026-09-14' LIMIT 1").fetchone()[0]
    lanes = dp.page(store, 'setup_orders', f'order={order}')
    assert lanes['rows'] and all(r['order_number'] == order for r in lanes['rows']) and lanes['url'] == f'/data/setup_orders?order_number={order}'
    span = dp.page(store, 'shift_kpis', 'from=2026-09-07&to=2026-09-11&corrupt=false&weekday=mon')
    assert span['count'] == 3 and {r['production_date'] for r in span['rows']} == {'2026-09-07'}
    planned = dp.page(store, 'stops', 'kind=pm&from=2026-09-01&to=2026-09-30')
    assert planned['rows'] and {r['kind'] for r in planned['rows']} == {'Scheduled'}   # the contract: kind=pm answers as Scheduled
    assert dp.page(store, 'customers', '')['count'] == 41 and dp.page(store, 'reject_reasons', 'family=warp')['count'] >= 5
    with pytest.raises(ValueError, match='not a filter'):
        dp.page(store, 'customers', 'day=2026-09-14')   # a dimension has no production day
    # a column filter given more than once matches any of its values; crew and grade reach a table through its shift and setup
    either = dp.page(store, 'stops', 'day=2026-09-14&reason=Stacker&reason=Missed%20splice&reason=Stacker')
    assert either['filters']['reason'] == ['Missed splice', 'Stacker'] and either['url'].endswith('reason=Missed%20splice&reason=Stacker')
    assert {r['reason'] for r in either['rows']} == {'Missed splice', 'Stacker'} and either['count'] == store._db.execute(
        "SELECT count(*) FROM stops WHERE production_date = '2026-09-14' AND reason IN ('Missed splice', 'Stacker')").fetchone()[0]
    crew = dp.page(store, 'stops', 'from=2026-09-01&to=2026-09-30&crew=crew%20b&grade=275-BC')
    want = store._db.execute("SELECT count(*) FROM stops t JOIN shifts c USING (production_date, shift) JOIN setups g USING (setup_id) "
                             "WHERE t.production_date BETWEEN '2026-09-01' AND '2026-09-30' AND c.crew = 'Crew B' AND g.quality_id = '275-BC'").fetchone()[0]
    assert crew['count'] == want > 0 and crew['url'] == '/data/stops?from=2026-09-01&to=2026-09-30&crew=crew%20b&quality_id=275-BC'
    assert dp.page(store, 'stops', 'day=2026-09-14&format=json')['url'] == '/data/stops?day=2026-09-14'   # the JSON asked for by parameter
    for bad, message in (('day=2026-13-01', 'date'), ('shift=4', 'shift'), ('page=0', 'page'), ('page=201', 'narrow the filter'),
                         ('limit=5', 'not a filter'), ('page=2&page=3', 'twice'), ('reason=' + '&reason='.join(map(str, range(21))), 'at most'),
                         ('nope=1', 'not a filter'), ('shift=1&shift=2', 'twice'), ('day=2026-09-14&from=2026-09-01', 'not both'),
                         ('from=2026-09-30&to=2026-09-01', 'on or before'), ('lane=one', 'whole number'), ('weekday=Sat', 'weekday')):
        with pytest.raises(ValueError, match=message):
            dp.page(store, 'setup_orders' if 'lane' in bad else 'stops', bad)


def test_a_csv_holds_the_lock_no_longer_than_a_page(store, monkeypatch):
    """The store's lock is the tools' lock: a CSV reads its rows a chunk at a time, each read its own short hold, so a
    download never keeps a tool waiting (the #142 review measured 120-185 ms in one read before)."""
    holds, fetch = [], store._fetch

    def timed(sql, params, limit=1000):
        started = time.perf_counter()
        try:
            return fetch(sql, params, limit)
        finally:
            holds.append((time.perf_counter() - started) * 1000)
    monkeypatch.setattr(store, '_fetch', timed)
    for table in ('setups', 'setup_orders', 'rejects'):
        holds.clear()
        body, truncated = dp.csv_rows(store, table, '')
        assert truncated and len(holds) == dp.CSV_LIMIT // dp.CSV_CHUNK + 1, (table, len(holds))
        assert sorted(holds)[len(holds) // 2] < 60, (table, holds)   # each read a few tens of milliseconds at most on a slow runner


def test_the_csv_is_the_filtered_rows_bounded(store):
    body, truncated = dp.csv_rows(store, 'stops', 'day=2026-09-14')
    lines = body.decode().splitlines()
    assert lines[0].split(',') == [name for name, _ in dp.TYPES['stops']] and not truncated
    assert len(lines) - 1 == store._db.execute("SELECT count(*) FROM stops WHERE production_date = '2026-09-14'").fetchone()[0]
    body, truncated = dp.csv_rows(store, 'rejects', '')
    assert truncated and len(body.decode().splitlines()) == dp.CSV_LIMIT + 1


# ------------------------------------------------------------------ the queries are fixed and bounded

NASTY = ["'", '"', "1' OR '1'='1", "1; DROP TABLE stops; --", "x') UNION SELECT * FROM duckdb_tables() --", '%27', '\\',
         'Missed splice\' --', "information_schema.tables", "read_csv('/etc/passwd')", '../../etc/passwd', '%00', '‮',
         '1e309', 'NaN', '-1', '9' * 40, 'true; COPY stops TO \'/tmp/x\'', '2026-09-14\' OR 1=1 --', '*', '$1', '?', ';']


def test_fuzzed_filters_never_reach_the_sql(store):
    """Random and hostile filter names and values: each is refused with a ValueError or bound as a parameter. The SQL
    text is one of the table's fixed shapes (no visitor text in it), and the store is unchanged afterwards."""
    counts = {t: store._db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in dp.TABLES}
    rng = random.Random(142)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 '\";-_/\\()*=%.,:é‮\t"
    shapes = set()
    tried = refused = bound = 0
    valid = {'day': '2026-09-14', 'from': '2026-09-01', 'to': '2026-09-30', 'weekday': 'Mon', 'shift': '2', 'page': '2'}
    for _ in range(800):
        table = rng.choice(list(dp.TABLES))
        names = dp.filter_names(table) + ['select', 'production_date;', 'order', 'grade', "kind'", '1=1', 'information_schema', 'page']
        params = []
        for _ in range(rng.randint(1, 3)):
            name, draw = rng.choice(names), rng.random()
            value = (rng.choice(NASTY) if draw < .45 else valid.get(name, 'Missed splice') if draw < .7
                     else ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 30))))
            params.append(f'{quote(name, safe="")}={quote(value, safe="")}')
        query = '&'.join(params)
        tried += 1
        try:
            out = dp.page(store, table, query)
        except ValueError:
            refused += 1
            continue
        for value in [v for _, v in parse_qsl(query)]:
            if len(value) > 2 and not re.fullmatch(r'[\w.\-]*', value):
                assert value not in out['sql'] and value not in out['count_sql'], (table, query)
                bound_values = {str(p).lower() for values in out['parameters'] for p in values}
                assert value.lower() in bound_values, (table, query)
                bound += value in NASTY
        shape = re.sub(r'EXISTS \(SELECT 1 FROM shifts c WHERE c\.production_date = \w+\.production_date AND c\.shift = \w+\.shift '
                       r'AND lower\(c\.crew\) IN \((?:lower\(\?\)(?:, )?)+\)\)'
                       r'|EXISTS \(SELECT 1 FROM setups g WHERE g\.setup_id = \w+\.setup_id AND lower\(g\.quality_id\) IN \((?:lower\(\?\)(?:, )?)+\)\)'
                       r'|lower\("\w+"\) IN \((?:lower\(\?\)(?:, )?)+\)|"\w+" IN \((?:\?(?:, )?)+\)', 'C', out['sql'])
        shapes.update(re.sub(r'"?\w+"? (=|>=|<=) \?|lower\("\w+"\) = lower\(\?\)|dayofweek\(production_date\) = \?', 'C', one).replace('rowid BETWEEN ? AND ?', 'R')
                      for one in shape.split(';\n'))
    assert refused > 100 and tried - refused > 60 and bound > 20, (tried, refused, bound)   # hostile values were bound, not refused only
    span = r'SELECT min\(r\) AS lo, max\(r\) AS hi, count\(\*\) AS n FROM \(SELECT rowid AS r FROM \w+( WHERE C( AND C)*)? ORDER BY rowid (DESC|ASC) LIMIT \? OFFSET \?\)'
    rows = r'SELECT [\w", ]+ FROM \w+ WHERE (C AND )*R ORDER BY rowid (DESC|ASC)'
    assert all(re.fullmatch(span, s) or re.fullmatch(rows, s) for s in shapes), shapes
    assert counts == {t: store._db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in dp.TABLES}
    with pytest.raises(ValueError, match='No table'):
        dp.page(store, 'duckdb_tables', '')
    with pytest.raises(ValueError, match='No table'):
        dp.page(store, 'information_schema.tables', '')


def test_a_page_answers_in_tens_of_milliseconds(store):
    """The budget the issue sets, loose enough for a shared CI runner: the median of five reads of each table's first
    page, its deepest page and a filtered page, under 100 ms (on the prebuilt store a page takes a few milliseconds)."""
    deepest = [(t, f'page={dp.MAX_PAGE}') for t in ('setups', 'setup_orders', 'rejects', 'rolls')]
    for table, query in [(t, '') for t in dp.TABLES] + deepest + [('rejects', 'from=2026-09-01&to=2026-09-30&family=warp'),
                                                                 ('stops', 'reason=Missed%20splice&crew=Crew%20A&grade=55-C')]:
        times = []
        for _ in range(5):
            started = time.perf_counter()
            dp.page(store, table, query)
            times.append((time.perf_counter() - started) * 1000)
        assert sorted(times)[2] < 100, (table, query, times)


# ------------------------------------------------------------------ receipts link to the rows behind a figure

def near(got, want, places=None):
    """`want` as the tool rounded it (`places`), else to the decimals it carries: the same floats summed in another order
    may round to the neighbouring last digit, so one unit of it is allowed."""
    if want is None:
        return got is None
    if got is None:
        return False
    if places is None:
        decimals = len(repr(float(want)).split('.')[1]) if 'e' not in repr(float(want)) else 6
        return math.isclose(got, want, rel_tol=1e-6, abs_tol=1e-6) or (decimals <= 4 and abs(got - want) <= 10 ** -decimals + 1e-9)
    return abs(got - want) <= 0.5 * 10 ** -places + 1e-6 * max(1, abs(want))


def sums(rows, *columns):
    return [sum((r[c] or 0) for r in rows) for c in columns]


def tile_sums(rows):
    """shift_kpis rows as the period tools' sums (record_store._sums), so RecordStore._kpis recomputes any tile."""
    t = {c: sum(r[c] or 0 for r in rows) for c in ('lineal_ft', 'gross_sqft', 'trim_sqft', 'shear_sqft', 'reject_sqft', 'elapsed_seconds',
                                                   'setup_stop_seconds', 'scheduled_seconds', 'bale_sqft', 'stop_seconds', 'operator_seconds',
                                                   'bond_sqft', 'warp_sqft', 'misalignment_sqft', 'moisture_sqft', 'other_sqft', 'changeover_sqft',
                                                   'admin_sqft', 'cutoff_waste_sqft', 'stacker_waste_sqft')}
    t.update(shift_records=len(rows), setup_records=sum(r['setups'] for r in rows), stop_count=sum(r['stops'] for r in rows),
             paper_changes=len({w for r in rows for w in r['wet_end_ids']}),
             production_days=len({r['production_date'] for r in rows if r['setups'] > 0}),
             maintenance_pm_seconds=sum(r['maintenance_seconds'] + r['planned_maintenance_seconds'] for r in rows))
    return t


def stop_minutes(rows):
    return sum(r['seconds'] for r in rows) / 60


def also_failure(store, entry, part, index):
    """The rows a step also read hold the rest of what its finding states: a comparison's previous window recomputes the
    previous value; the notes' shift record is the note before the stops (Codex on #151)."""
    tool, data = entry['tool'], entry['data']
    for also in entry['source'].get('also', []):
        rows = dp.resolve(store, also['url'], limit=500_000)
        if also['label'] == 'previous window' and tool in ('get_period_kpis', 'compare_periods') and part == 'row':
            row = data['rows'][index]
            if row['previous'] is not None and not near(RecordStore._kpis(tile_sums(rows))[row['metric']], row['previous']):
                return f'{tool} row {index}: previous {row["metric"]} over {also["url"]}, the tool says {row["previous"]}'
        elif also['label'] == 'previous window' and tool == 'get_rollstock' and part == 'row':
            row = data['rows'][index]
            column = 'lineal_ft'
            got = sum(r[column] for r in rows if r['quality_id'] == row['grade'] and (r.get('web_width_in') or r.get('width_in')) == row['width_in'])
            if row.get('previous_lineal_ft') is not None and not near(got, row['previous_lineal_ft'], 1):
                return f'{tool} row {index}: previous feet {got} over {also["url"]}, the tool says {row["previous_lineal_ft"]}'
        elif also['label'] == 'the shift record':
            if len(rows) != 1 or rows[0]['crew'] not in data['rows'][0]['notes']:
                return f'{tool}: the shift record {rows} does not hold the shift note {data["rows"][0]["notes"]!r}'
        elif not rows and (data.get('previous_window') or {}).get('production_days_observed'):
            return f'{tool}: the previous window {also["url"]} holds no rows'
    return None


def unexplained(rows):
    """The bale minus every recorded waste line, over shift rows (record_store.accounted_sqft)."""
    lines = ('trim_sqft', 'shear_sqft', 'reject_sqft', 'changeover_sqft', 'admin_sqft', 'cutoff_waste_sqft', 'stacker_waste_sqft')
    return sum(r['bale_sqft'] - sum(r[c] for c in lines) for r in rows)


def speed_gap_ft(rows, required):
    """cost_model's speed gap to the required speed, shift by shift: required × running minutes − lineal feet."""
    shifts = {}
    for r in rows:
        shifts.setdefault((r['production_date'], r['shift']), []).append(r)
    return sum(max(0.0, required * (480 - sum(r['stop_seconds'] for r in rs) / 60) - sum(r['lineal_ft'] for r in rs)) for rs in shifts.values())


def line_checks(table, row, rows, data):
    """A priced line's quantity over its rows, each line by what it prices: minutes of stops, square feet of reject
    lines, waste boards of lanes, side trim and shear of setups, the points over the trim target, the speed gap, the runs
    under their baseline, the bale's unexplained remainder."""
    label, quantity = row.get('lever') or row.get('gap'), row['quantity']
    if table == 'stops':
        return [('minutes', stop_minutes(rows), quantity, 2)]
    if table == 'rejects':
        return [('sq ft', sum(r['sqft'] for r in rows), quantity, 1)]
    if table == 'setup_orders':
        cutoff, stacker = sums(rows, 'cutoff_waste_sqft', 'stacker_waste_sqft')
        got = cutoff if label.startswith('Cutoff-knife waste') else stacker if label.startswith('Stacker waste') else cutoff + stacker   # or both: 'Cutoff-knife and stacker waste boards'
        return [('waste boards sq ft', got, quantity, 1)]
    if table == 'shifts':
        return [('unexplained sq ft', unexplained(rows), quantity, 1)]
    over = re.match(r'Side trim over the ([\d.]+)% target', label)
    if over:
        trim, gross = sums(rows, 'trim_sqft', 'gross_sqft')
        return [('points over the trim target', max(0.0, trim / gross * 100 - float(over.group(1))), quantity, 4)]
    if label.startswith('Side trim'):
        return [('side trim sq ft', sum(r['trim_sqft'] for r in rows), quantity, 1)]
    if label.startswith('Shear'):
        return [('shear sq ft', sum(r['shear_sqft'] for r in rows), quantity, 1)]
    required = re.match(r'Speed gap to ([\d.]+) ft/min', label)
    if required:
        return [('speed gap ft', speed_gap_ft(rows, float(required.group(1))), quantity, 1)]
    if label.startswith('Speed under'):
        return [('wet-end runs', len({r['wet_end_id'] for r in rows}), data['source_rows']['runs'], None)]
    return []


def speedvar_checks(part, row, rows, data, url):
    """A speed-variance receipt: the explained side's stops or setups (the minutes and stops the tool reports for it);
    a bucket or item that lost nothing there opens the reference's rows, which must then hold what it names."""
    if part == 'row' and dp._reference(row) or part == 'detail' and dp._reference(row):
        item = row.get('item')
        if row['bucket'] in ('below_baseline', 'mix'):
            return [('the grade on the reference side', all(r['quality_id'] == item for r in rows) if item else bool(rows), True, None)]
        return [('rows on the reference side', bool(rows), True, None)]
    if part is None or row['bucket'] in ('below_baseline', 'mix') and part == 'row':
        return [('setups of the window', len(rows), data['source_rows']['current']['setups'], None)]
    if part == 'detail' and row['bucket'] in ('below_baseline', 'mix'):
        return [('setups of the grade', all(r['quality_id'] == row['item'] for r in rows) and bool(rows), True, None)]
    items = [d for d in data['details'] if d['bucket'] == row['bucket']] if part == 'row' else [row]
    # a bucket's minutes are its items' minutes, each rounded to two places: their sum is within half a cent per item
    return [('stops', len(rows), sum(d['stops'] for d in items), None),
            ('minutes', stop_minutes(rows), sum(d['minutes'] for d in items), ('abs', 0.005 * len(items) + 1e-6))]


def figure_failure(entry, part, index, url, rows):
    """None when the rows behind a receipt hold the cited row's quantity, else what differs. The bold figure itself is a
    field of that row (evals/run.py's faithfulness and grounding checks), so the rows hold the figure. Every receipt
    must have a check here: one without is a failure, never counted as verified."""
    if not rows:
        return f'{url} holds no rows'
    tool, data = entry['tool'], entry['data']
    row = data[dp.PARTS[part]][index] if part else None
    table = url.split('?')[0].removeprefix('/data/')
    checks = []   # (what, got over the rows, what the tool says, decimals it rounded to or None)
    if tool in ('get_downtime_pareto', 'get_downtime_breakdown') and part == 'row':
        minutes = row.get('minutes', row.get('down_minutes'))
        checks = [('stops', len(rows), row['stops'], None), ('minutes', stop_minutes(rows), minutes, 2 if 'minutes' in row else None)]
    elif tool in ('get_downtime_review', 'get_downtime_breakdown') and part in ('row', 'flag'):
        checks = [('stops', len(rows), row['stops'], None), ('minutes', stop_minutes(rows), row['minutes'], 2)]
    elif tool == 'get_downtime_review':
        maint_op = sum(r['maintenance_seconds'] + r['operator_seconds'] for r in rows) / 60
        checks = [('maintenance + operator minutes', maint_op, data['op_maint_minutes'], 2),
                  ('scheduled minutes', sum(r['scheduled_seconds'] for r in rows) / 60, data['scheduled_minutes'], 0)]
    elif tool == 'get_grade_totals' and part == 'row':
        checks = [('setups', len(rows), row['setups'], None), ('MSF', sum(r['gross_sqft'] for r in rows) / 1000, row['volume_msf'], 1)]
    elif tool == 'get_grade_totals':
        checks = [('MSF', sum(r['gross_sqft'] for r in rows) / 1000, data['total_volume_msf'], 1)]
    elif tool in ('get_opportunities', 'get_shift_dollars', 'get_day_dollars') and part == 'row':
        checks = line_checks(table, row, rows, data)
    elif tool in ('get_shift_dollars', 'get_day_dollars', 'get_opportunities', 'get_waste_breakdown') and part is None:
        checks = [('gross sq ft', sum(r['gross_sqft'] for r in rows), data['gross_sqft'], 1)]
    elif tool == 'get_overrun':
        nominal, actual = sums(rows, 'nominal_cuts', 'actual_cuts')
        checks = [('lines', len(rows), row['lines'], None), ('nominal cuts', nominal, row['nominal_cuts'], None), ('actual cuts', actual, row['actual_cuts'], None)]
    elif tool in ('get_period_kpis', 'compare_periods'):
        checks = [(row['metric'], RecordStore._kpis(tile_sums(rows))[row['metric']], row['current'], None)]
    elif tool == 'get_trend':
        checks = [(data['metric'], RecordStore._kpis(tile_sums(rows))[data['metric']], row['value'], None)]
    elif tool == 'get_quality_breakdown':
        checks = [('sq ft', sum(r['sqft'] for r in rows), row['rejected_sqft'], None)]
    elif tool == 'get_rankings':
        if data['metric'] == 'speed':
            got = sum(r['lineal_ft'] for r in rows) / sum(r['target'] * r['seconds'] / 60 for r in rows) * 100
        else:
            got = stop_minutes(rows) if data['metric'] == 'downtime' else sum(r['sqft'] for r in rows)
        checks = [(data['metric'], got, row['value'], None)]
    elif tool == 'get_rollstock' and part == 'row':
        checks = [('feet', sum(r['lineal_ft'] for r in rows), row['lineal_ft'], 1)] + ([('paper runs', len(rows), row['paper_runs'], None)] if table == 'wet_ends' else [])
    elif tool == 'get_rollstock':
        checks = [('feet', sum(r['lineal_ft'] for r in rows), data['total_lineal_ft'], 1)]
    elif tool == 'get_setup_matrix':
        checks = [('the setup', [r['setup_id'] for r in rows], [row['setup_id']], None), ('feet', rows[0]['lineal_ft'], row['lineal_ft'], None)]
    elif tool in ('get_shift_kpis', 'get_shift_overview'):
        r = data['rows'][0]
        checks = [('one shift row', len(rows), 1, None), ('gross sq ft', rows[0]['gross_sqft'], r['gross_sqft'], None),
                  ('lineal ft', rows[0]['lineal_ft'], r['observed_lineal_ft'], None), ('setups', rows[0]['setups'], r['valid_setups'], None)]
    elif tool == 'get_shift_notes':
        checks = [('stops, each a note after the shift\'s own', len(rows), len(data['rows']) - 1, None)]
    elif tool == 'get_speed_opportunity':
        target = row['setups'] if part == 'row' else data['source_rows']['setups_included']
        checks = [('setups past the minimum run time', len([r for r in rows if r['run_seconds'] >= data['min_run_minutes'] * 60]), target, None)]
    elif tool == 'get_speed_variance':
        checks = speedvar_checks(part, row, rows, data, url)
    elif tool == 'get_stops_profile':
        checks = [('stops', len(rows), row['stops'], None)] + ([('minutes', stop_minutes(rows), row['minutes'], 2)] if part == 'row' else [])
    elif tool == 'get_waste_breakdown' and part == 'detail':
        checks = [('sq ft', sum(r['sqft'] for r in rows), row['sqft'], 1), ('sheets', sum(r['sheets'] for r in rows), row['sheets'], None)]
    elif tool == 'get_waste_breakdown' and part == 'row':
        if row['category'] == 'unexplained':
            checks = [('unexplained sq ft', unexplained(rows), row['sqft'], 1)]
        else:
            column = {'setups': f'{row["category"]}_sqft', 'rejects': 'sqft', 'setup_orders': f'{row["category"]}_sqft', 'shifts': 'bale_sqft'}[table]
            checks = [(row['category'], sum(r[column] for r in rows), row['sqft'], 1)]
    elif tool == 'get_wet_end_performance' and part == 'row':
        checks = [('setups', len(rows), len(row['source_ids']), None), ('feet', sum(r['lineal_ft'] for r in rows), row['lineal_ft'], None)]
    elif tool == 'get_wet_end_performance':
        checks = [('paper runs', len({r['wet_end_id'] for r in rows}), len(data['rows']), None)]
    elif tool == 'get_order_matrix' and part is None:
        checks = [('lanes', len(rows), len(data['rows']), None)]
    elif tool == 'get_order_matrix':
        checks = [('one lane', len(rows), 1, None), ('actual cuts', rows[0]['actual_cuts'], row['cuts'], None)]
    if not checks:
        return f'{tool} {part} {index}: no check of its rows ({url})'
    for what, got, want, places in checks:
        if isinstance(places, tuple):   # ('abs', tolerance): a sum of values the tool rounded one by one
            same = abs(got - want) <= places[1]
        else:
            same = got == want if isinstance(want, (list, bool)) or places is None and isinstance(want, int) else near(got, want, places)
        if not same:
            return f'{tool} {part} {index}: {what} {got!r} over {url}, the tool says {want!r}'
    return None


# The data-quality demo's day (an excluded record in shift 2): a daily tool links the corrupt variant of shift_kpis, a
# period tool the records as generated (the #142 review). The golden set's own corrupt cases ask by POST, not by URL.
CORRUPT_URLS = ('/corr/1/day/2026-09-14/shift/2?measure=overview&corrupt=1', '/corr/1/day/2026-09-14/shift/2?measure=kpis&corrupt=1',
                '/corr/1/day/2026-09-14/shift/2?measure=waste&corrupt=1', '/corr/1/day/2026-09-14/shift/2?measure=dollars&corrupt=1',
                '/corr/1/day/2026-09-14?measure=dollars&corrupt=1', '/corr/1/day/2026-09-14/shift/2?measure=downtime&corrupt=1')


def golden_urls():
    cases = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
    return sorted({c['expect']['url'] for c in cases if c.get('mode', 'offline') == 'offline' and (c.get('expect') or {}).get('url')}) + list(CORRUPT_URLS)


def test_receipts_on_golden_answers_link_to_rows_that_hold_the_cited_figure(store):
    """Every "(query N · row M)" of every offline golden answer: the step declares its table and filter, the link
    resolves to rows, and those rows hold the cited row's quantity (summed, counted or as the tool's ratio of sums)."""
    app = Workbench(store=store)
    failures, receipts, tools = [], 0, set()
    for url in golden_urls():
        try:
            result, _ = app.answer_url(url)
        except ValueError:
            continue   # a refusal case: no answer, no receipt
        for section in result['sections']:
            m = REF.search(section)
            if not m:
                continue
            entry = result['trace'][int(m.group(1)) - 1]
            part, index = m.group(2), int(m.group(3)) if m.group(3) else None
            assert entry.get('source'), (url, entry['tool'])
            link = dp.receipt_url(entry, part, index)
            assert link and link.startswith('/data/'), (url, section)
            assert dp.table_url(link.split('?')[0].removeprefix('/data/'), dp.parse_filters(link.split('?')[0].removeprefix('/data/'), link.partition('?')[2])[0]) == link, link
            rows = dp.resolve(store, link, limit=500_000)
            problem = figure_failure(entry, part, index, link, rows) or also_failure(store, entry, part, index)
            if problem:
                failures.append(f'{url}: {problem}')
            receipts += 1
            tools.add(entry['tool'])
    app.close()
    assert not failures, '\n'.join(failures[:20])
    assert receipts > 1000 and len(tools) >= 20, (receipts, sorted(tools))


def test_a_receipt_link_survives_a_corrupt_day_and_names_its_variant(store):
    app = Workbench(store=store)
    result, _ = app.answer_url('/corr/1/day/2026-09-14/shift/2?measure=overview&corrupt=1')
    entry = next(e for e in result['trace'] if e['tool'] == 'get_shift_kpis')
    assert entry['source']['url'] == '/data/shift_kpis?day=2026-09-14&shift=2&corrupt=true'
    rows = dp.resolve(store, entry['source']['url'])
    assert len(rows) == 1 and rows[0]['coverage'] == 'incomplete'
    app.close()


# ------------------------------------------------------------------ the routes

def request(server, method, path, body=None, headers=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


@pytest.fixture
def served(store):
    servers = []

    def start(public=None):
        server = make_server(0, public=public, host='127.0.0.1' if public else None, store=store)
        Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server
    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
        server.app.close()


def test_the_routes_are_get_only_read_only_and_keep_the_origin_rules(served):
    local = served()
    status, headers, body = request(local, 'GET', '/data')
    assert status == 200 and headers['Content-Type'].startswith('text/html') and b'/data.js' in body
    status, _, body = request(local, 'GET', '/data/stops')
    assert status == 200 and b'/data.js' in body
    assert request(local, 'GET', '/data/nope')[0] == 404 and request(local, 'GET', '/data/nope.json')[0] == 404
    status, headers, body = request(local, 'GET', '/data.json')
    assert status == 200 and [t['name'] for t in json.loads(body)['tables']] == list(dp.TABLES)
    assert 'Access-Control-Allow-Origin' not in headers and 'Server-Timing' in headers
    status, headers, body = request(local, 'GET', '/data/stops.json?day=2026-09-14&shift=2')
    page = json.loads(body)
    assert status == 200 and page['count'] == len(page['rows']) > 0 and page['url'] == '/data/stops?day=2026-09-14&shift=2'
    assert request(local, 'GET', '/data/stops?day=2026-09-14', headers={'Accept': 'application/json'})[0] == 200
    status, headers, body = request(local, 'GET', '/data/stops.csv?day=2026-09-14&shift=2')
    assert status == 200 and headers['Content-Type'].startswith('text/csv') and headers['X-Rows-Truncated'] == 'false'
    assert 'attachment; filename="stops.csv"' == headers['Content-Disposition'] and body.decode().count('\n') == page['count'] + 1
    status, _, body = request(local, 'GET', "/data/stops.json?reason=x'%20OR%201=1")
    assert status == 200 and json.loads(body)['count'] == 0   # a value is a value
    assert request(local, 'GET', '/data/stops.json?shift=9')[0] == 400
    status, _, body = request(local, 'GET', '/data/stops.json?limit=5')   # a visitor's word "limit" is a 400 like any other, not the store's cap
    assert status == 400 and 'not a filter' in json.loads(body)['error']
    assert request(local, 'GET', '/data/stops?day=2026-09-14&format=json')[0] == 200
    assert request(local, 'GET', '/data/stops.xml')[0] == 404
    assert request(local, 'GET', '/data.json', headers={'Host': 'evil.example'})[0] == 403   # the workbench's own host only
    for method in ('POST', 'PUT', 'DELETE'):
        status, _, _ = request(local, method, '/data/stops.json', body=b'{}', headers={'Content-Type': 'application/json', 'Content-Length': '2'})
        assert status in (403, 501), (method, status)
    public = served(PublicDemo(origins=('https://demo.example',), embed_origins=(), live_model=False))
    status, headers, body = request(public, 'GET', '/data/stops.json?day=2026-09-14', headers={'Host': 'demo.example'})
    assert status == 200 and 'Access-Control-Allow-Origin' not in headers and "frame-ancestors 'none'" in headers['Content-Security-Policy']
    status, _, _ = request(public, 'POST', '/data/stops.json', body=b'{}', headers={'Origin': 'https://demo.example', 'Content-Type': 'application/json',
                                                                                  'Content-Length': '2'})
    assert status == 403


def test_the_data_reads_have_their_own_windows(served):
    """Container-wide, per client address, and for CSVs: one visitor cannot take the container's window, nor hold the
    store with downloads; the page itself is a static file, never counted."""
    from investigator.server import DATA_CSV_PER_MIN, DATA_READS_PER_IP, DATA_READS_PER_MIN
    server = served()
    now = time.monotonic()
    server.app.csv_requests.extend([now] * DATA_CSV_PER_MIN)
    assert request(server, 'GET', '/data/stops.csv?day=2026-09-14')[0] == 429
    assert request(server, 'GET', '/data/stops.json?day=2026-09-14')[0] == 200   # a page is not a download
    server.app.data_windows['127.0.0.1'] = __import__('collections').deque([now] * DATA_READS_PER_IP)
    status, headers, _ = request(server, 'GET', '/data/stops.json')
    assert status == 429 and int(headers['Retry-After']) >= 1
    server.app.data_windows.clear()
    server.app.data_requests.extend([now] * DATA_READS_PER_MIN)
    assert request(server, 'GET', '/data/stops.json')[0] == 429
    assert request(server, 'GET', '/data')[0] == 200 and request(server, 'GET', '/data/stops')[0] == 200
    assert DATA_READS_PER_IP < DATA_READS_PER_MIN


# ------------------------------------------------------------------ the UI test's payloads and the contract anchors

FIXTURES = ROOT / 'tests' / 'ui_fixtures'
DATA_REFRESH = ("INVESTIGATOR_AS_OF=2026-09-30 python3 -c \"import json;from investigator.record_store import default_store;"
                "from investigator import datapages as dp;s=default_store();i=dp.index(s);[i.pop(k) for k in ('built_ms','elapsed_ms')];"
                "json.dump(i,open('tests/ui_fixtures/data_index.json','w'),separators=(',',':'),sort_keys=True);"
                "p=dp.page(s,'stops','day=2026-09-14&shift=2');p.pop('elapsed_ms');"
                "json.dump(p,open('tests/ui_fixtures/data_stops.json','w'),separators=(',',':'),sort_keys=True)\"")


def test_the_ui_data_fixtures_are_the_servers_answers(store):
    """tests/test_ui.cjs renders /data and /data/stops from these files; they must be what the server answers."""
    index = json.loads(json.dumps(dp.index(store)))
    index.pop('built_ms'), index.pop('elapsed_ms')
    assert json.loads((FIXTURES / 'data_index.json').read_text()) == index, f'refresh with: {DATA_REFRESH}'
    page = json.loads(json.dumps(dp.page(store, 'stops', 'day=2026-09-14&shift=2')))
    page.pop('elapsed_ms')
    assert json.loads((FIXTURES / 'data_stops.json').read_text()) == page, f'refresh with: {DATA_REFRESH}'
    why_slow = json.loads((FIXTURES / 'why_slow.json').read_text())
    assert [e.get('source', {}).get('table') for e in why_slow['trace']] == ['shift_kpis', 'stops', 'setups', 'stops', None, None]


def test_the_docs_renderer_anchors_a_column_table_and_nothing_else():
    """Issue #142: a column table (a name in backticks in its first header cell, or the one name in the heading above
    it) gives each column its first cell names an id `col-<table>-<column>`; any other table is left as it was."""
    page = docsite.Renderer().render('## Order lanes (`setup_orders`)\n\n| Ours | Notes |\n| --- | --- |\n'
                                     '| `lane` | across the web |\n| `nominal_cuts`, `actual_cuts` | cuts |\n| plain | no anchor |\n\n'
                                     '## Two (`a`) and (`b`)\n\n| Line | What |\n| --- | --- |\n| `trim` | side trim |\n\n'
                                     '| Ours (`shifts`) | Notes |\n| --- | --- |\n| `crew` | the crew |\n| `lane` | repeated name, own table |\n')
    assert '<div class="table-scroll" id="col-setup_orders"' in page and '<tr id="col-setup_orders-lane">' in page
    assert '<tr id="col-setup_orders-nominal_cuts"><td><span id="col-setup_orders-actual_cuts"></span><code>nominal_cuts</code>' in page
    assert '<tr><td>plain</td>' in page and 'col-a' not in page and 'col-b' not in page and '<tr><td><code>trim</code>' in page
    assert '<tr id="col-shifts-crew">' in page and '<tr id="col-shifts-lane">' in page
    plain = docsite.Renderer().render('| Model | Cost |\n| --- | --- |\n| `gpt-small` | $1 |\n')
    assert ' id=' not in plain
