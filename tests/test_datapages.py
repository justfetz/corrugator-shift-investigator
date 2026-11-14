"""The data browser (issue #142): /data lists the tables every answer reads, /data/<table> shows a table's schema and
its rows, and an answer's receipts link to the rows behind each figure.

The schema pages are held to the record contract: every column a page shows has a unit and a meaning, and its row in
the contract (docs/RECORD_CONTRACT.md; docs/METRICS.md for the derived shift_kpis) exists and carries the anchor the
page links to. Filters are fuzzed: no value or name reaches the SQL text. Every receipt of every offline golden answer
links to rows, and the section's bold figure, recomputed from those rows with the receipt's own filter, is the figure
printed (the #151 review).
"""
import gc
import http.client
import json
import math
import random
import re
import sys
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
    two = dp.page(store, 'stops', 'day=2026-09-14&shift=3,1')   # the shifts a day's money was summed over (#151 review)
    assert two['url'] == '/data/stops?day=2026-09-14&shift=1,3' and {r['shift'] for r in two['rows']} == {1, 3}
    assert dp.page(store, 'stops', 'day=2026-09-14&shift=1&shift=3')['url'] == two['url']
    for bad, message in (('day=2026-13-01', 'date'), ('shift=4', 'shift'), ('page=0', 'page'), ('page=201', 'narrow the filter'),
                         ('limit=5', 'not a filter'), ('page=2&page=3', 'twice'), ('reason=' + '&reason='.join(map(str, range(21))), 'at most'),
                         ('nope=1', 'not a filter'), ('day=2026-09-14&day=2026-09-15', 'twice'), ('shift=1,1', 'list of them'), ('shift=0,2', 'list of them'), ('day=2026-09-14&from=2026-09-01', 'not both'),
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
#
# Every section of every offline golden answer that cites a step carries a receipt (result['receipts']): the rows behind its
# own figure. The checker below recomputes the section's **bold figure** from those rows with the receipt's own filter, the
# way the tool states it (a sum, a count, a ratio of sums, a count over a range, "N of M"), and compares it with the figure
# as printed. A dollar figure is a quantity recomputed from the rows and priced by the cost model at the answer's rates; a
# figure held against a baseline takes each grade's baseline from the trailing year's setups the step also declares. A
# section with no check here fails: nothing counts as verified unless its figure was recomputed.

from datetime import date as _date, timedelta as _timedelta

from investigator import cost_model, targets

BOLD = re.compile(r'\*\*([^*]+)\*\*')
NUMBER = re.compile(r'([+-]?)\$?(\d[\d,]*(?:\.\d+)?)')


class Unchecked(Exception):
    pass


def printed(section):
    """The bold figure of a section: (text, value, decimals)."""
    text = BOLD.search(section).group(1)
    m = NUMBER.search(text)
    value = float(m.group(2).replace(',', '')) * (-1 if m.group(1) == '-' else 1)
    decimals = len(m.group(2).split('.')[1]) if '.' in m.group(2) else 0
    return text, value, decimals


def stop_minutes(rows):
    return sum(r['seconds'] for r in rows) / 60


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


LINES = ('trim_sqft', 'shear_sqft', 'reject_sqft', 'changeover_sqft', 'admin_sqft', 'cutoff_waste_sqft', 'stacker_waste_sqft')


def unexplained(rows):
    """The bale minus every recorded waste line (record_store.accounted_sqft), over shift or shift_kpis rows."""
    return sum(r['bale_sqft'] - sum(r[c] for c in LINES) for r in rows)


def good_output(rows):
    return sum(r['gross_sqft'] - sum(r[c] for c in LINES) for r in rows)


def baselines(year_rows):
    """Each grade's sustained speed over the trailing year (record_store.grade_speed_baselines): lineal ft over running
    minutes of the setups with at least SPEED_MIN_RUN_MINUTES of running time, rounded to 0.1 ft/min."""
    from investigator.record_store import SPEED_MIN_RUN_MINUTES
    feet, run = {}, {}
    for r in year_rows:
        minutes = (r['seconds'] - r['stop_seconds']) / 60
        if minutes >= SPEED_MIN_RUN_MINUTES:
            feet[r['quality_id']] = feet.get(r['quality_id'], 0) + r['lineal_ft']
            run[r['quality_id']] = run.get(r['quality_id'], 0) + minutes
    return {g: round(feet[g] / run[g], 1) for g in feet if run[g]}


def runs_below_target(setup_rows):
    """Wet-end runs whose elapsed-time speed is under their grade target (analysis.get_wet_end_performance)."""
    runs = {}
    for r in setup_rows:
        feet, target_ft = runs.get(r['wet_end_id'], (0.0, 0.0))
        runs[r['wet_end_id']] = (feet + r['lineal_ft'], target_ft + r['target'] * r['seconds'] / 60)
    return sum(feet < target_ft for feet, target_ft in runs.values()), len(runs)


def best_fifth_hours(cell):
    """One speed-opportunity cell (record_store.get_speed_opportunity): the setups past the minimum run time, the fastest
    fifth by speed without stops (ntile 5), and the running minutes now less the minutes the same feet take at its speed."""
    from investigator.record_store import SPEED_MIN_RUN_MINUTES, SPEED_MIN_SETUPS
    base = [(r['lineal_ft'], (r['seconds'] - r['stop_seconds']) / 60) for r in cell if r['seconds'] - r['stop_seconds'] >= SPEED_MIN_RUN_MINUTES * 60]
    if len(base) < SPEED_MIN_SETUPS:
        return None
    base.sort(key=lambda x: -x[0] / x[1])
    first = len(base) // 5 + (1 if len(base) % 5 else 0)
    best = sum(f for f, _ in base[:first]) / sum(m for _, m in base[:first])
    return (sum(m for _, m in base) - sum(f for f, _ in base) / best) / 60


class Figures:
    """Recomputes the bold figure of a receipted section from the rows its receipt opens."""

    def __init__(self, store):
        self.store, self.cache, self.grades, self.crews = store, {}, None, None

    def rows(self, url):
        if url not in self.cache:
            self.cache[url] = dp.resolve(self.store, url, limit=500_000)
        return self.cache[url]

    def grade_of(self, setup_id):
        if self.grades is None:
            self.grades = dict(self.store._db.execute('SELECT setup_id, quality_id FROM setups').fetchall())
        return self.grades[setup_id]

    def crew_of(self, row):
        if self.crews is None:
            self.crews = {(d.isoformat(), s): c for d, s, c in self.store._db.execute('SELECT production_date, shift, crew FROM shifts').fetchall()}
        return self.crews[(row['production_date'], row['shift'])]

    @staticmethod
    def also(entry, label):
        return next((a['url'] for a in entry['source'].get('also', []) if a['label'] == label), None)

    def whole(self, entry):
        return self.rows(entry['source']['url'])

    @staticmethod
    def shifted(url, **change):
        """The same address with some filters changed (None drops one): the other side of a comparison."""
        table = url.split('?')[0].removeprefix('/data/')
        filters, _ = dp.parse_filters(table, url.partition('?')[2])
        for k, v in change.items():
            if v is None:
                filters.pop(k, None)
            else:
                filters[k] = v
        return dp.table_url(table, filters)

    # ---- dollars: a quantity from the rows, priced by the cost model at the answer's rates
    def priced(self, entry, row, quantity, field):
        terms = row.get('price_terms') or {}
        whole = self.whole(entry)
        gross = sum(r['gross_sqft'] for r in whole) if whole and 'gross_sqft' in whole[0] else terms.get('gross_sqft')
        minutes = (sum(r['scheduled_seconds'] for r in whole) / 60 if whole and 'scheduled_seconds' in whole[0] else terms.get('minutes'))
        if entry['tool'] in ('get_shift_dollars', 'get_day_dollars'):
            minutes = 480 * len({(r['production_date'], r['shift']) for r in whole})
        kind = terms.get('kind') or {'trim': 'trim', 'waste': 'waste', 'paper': 'paper'}.get(row.get('kind'), 'downtime')
        return cost_model.price(kind, quantity, self.rates, gross, minutes)[field]

    def line_quantity(self, entry, row, url):
        quantity = self._line_quantity(entry, row, url)
        if entry['tool'] == 'get_opportunities' and quantity is not None and not str(row.get('lever', '')).startswith('Side trim over'):
            quantity = round(quantity, 2 if row.get('unit') == 'min' else 1)   # the levers price their rows' rounded quantities
        return quantity

    def _line_quantity(self, entry, row, url):
        """The quantity a priced line prices, from its own rows: stop minutes, reject sq ft, waste-board sq ft, side trim
        and shear sq ft, the sq ft over the trim target, the speed gap's minutes, the minutes under the grade baselines,
        the bale's unexplained remainder."""
        rows = self.rows(url)
        table = url.split('?')[0].removeprefix('/data/')
        label = row.get('lever') or row.get('gap')
        if table == 'stops':
            return stop_minutes(rows)
        if table == 'rejects':
            return sum(r['sqft'] for r in rows)
        if table == 'setup_orders':
            cutoff, stacker = (sum(r[c] for r in rows) for c in ('cutoff_waste_sqft', 'stacker_waste_sqft'))
            return cutoff if label.startswith('Cutoff-knife waste') else stacker if label.startswith('Stacker waste') else cutoff + stacker
        if table == 'shifts':
            return unexplained(rows)
        over = re.match(r'Side trim over the ([\d.]+)% target', label)
        if over:   # the points over the target on the window's trim percent (to four places, as the tool rounds it)
            trim, gross = (sum(r[c] for r in rows) for c in ('trim_sqft', 'gross_sqft'))
            return max(0.0, round(trim / gross * 100, 4) - float(over.group(1))) / 100 * gross
        if label.startswith('Side trim'):
            return sum(r['trim_sqft'] for r in rows)
        if label.startswith('Shear'):
            return sum(r['shear_sqft'] for r in rows)
        required = re.match(r'Speed gap to ([\d.]+) ft/min', label)
        if required:
            speed, shifts = float(required.group(1)), {}
            for r in rows:
                shifts.setdefault((r['production_date'], r['shift']), []).append(r)
            gap_ft = sum(max(0.0, speed * (480 - sum(r['stop_seconds'] for r in rs) / 60) - sum(r['lineal_ft'] for r in rs)) for rs in shifts.values())
            return gap_ft / speed
        if label.startswith('Speed under'):
            base, runs = baselines(self.rows(self.also(entry, 'the trailing year'))), {}
            for r in rows:
                key = (r['production_date'], r['wet_end_id'], r['quality_id'])
                feet, run = runs.get(key, (0.0, 0.0))
                runs[key] = (feet + r['lineal_ft'], run + (r['seconds'] - r['stop_seconds']) / 60)
            return sum(max(0.0, base[g] * run - feet) / base[g] for (_, _, g), (feet, run) in runs.items() if g in base and run)
        raise Unchecked(f'no quantity for the line {label!r}')

    def lever_usd(self, entry, j, field):
        row = entry['data']['rows'][j]
        return self.priced(entry, row, self.line_quantity(entry, row, entry['source']['rows']['row'][j]), field)

    # ---- speed variance: each side from its own rows, losses at each grade's trailing-year baseline
    def speedvar(self, entry, part, index, receipt_rows, url):
        data = entry['data']
        base = baselines(self.rows(self.also(entry, 'the trailing year')))
        speed_of = lambda g: base.get(g)
        cur_shift, against = data.get('shift'), data['against']
        whole = self.whole(entry)
        previous = self.also(entry, 'previous window')

        def side(rows, which):
            if against == 'shift' and cur_shift is not None:
                keep = cur_shift if which == 'current' else data['against_shift']
                return [r for r in rows if r['shift'] == keep]
            return rows

        def minutes(which):
            rows = side(whole, which) if which == 'current' or against == 'shift' else self.rows(previous)
            return sum(r['scheduled_seconds'] for r in rows) / 60

        def other_rows(url):   # the reference side's rows of the same table: the other shift is in them, the previous window is not
            return side(self.rows(url), 'reference') if against == 'shift' else self.rows(self.shifted(url, day=None, **dict(
                zip(('from', 'to'), (data['previous_window']['start'], data['previous_window']['end']))))) if against == 'previous' else []

        def loss(stop_rows, t):
            return sum(r['seconds'] / 60 * speed_of(self.grade_of(r['setup_id'])) for r in stop_rows) / t if t else 0.0

        if part is None:   # the headline: reference speed with stops minus this window's
            current = sum(r['lineal_ft'] for r in side(whole, 'current')) / minutes('current')
            if against == 'baseline':
                year = self.rows(self.also(entry, 'the trailing year'))
                elapsed = {}
                for r in year:
                    elapsed[r['quality_id']] = elapsed.get(r['quality_id'], 0) + r['seconds'] / 60
                reference = sum(speed_of(g) * m for g, m in elapsed.items()) / sum(elapsed.values())
            else:
                rows = side(whole, 'reference') if against == 'shift' else self.rows(previous)
                reference = sum(r['lineal_ft'] for r in rows) / (sum(r['scheduled_seconds'] for r in rows) / 60)
            return abs(reference - current)
        row = data['rows' if part == 'row' else 'details'][index]
        bucket = row['bucket']
        t_cur, t_ref = minutes('current'), (minutes('reference') if against != 'baseline' else None)
        if bucket in ('stops', 'planned', 'external', 'setups'):
            if dp._reference(row) and against == 'previous':   # lost only in the previous window: the receipt opens that window
                return abs(loss(receipt_rows, t_ref))
            return abs(loss(side(receipt_rows, 'current'), t_cur) - (loss(other_rows(url), t_ref) if against != 'baseline' else 0.0))
        if bucket == 'below_baseline':
            def below(rows, t):
                feet, run = {}, {}
                for r in rows:
                    feet[r['quality_id']] = feet.get(r['quality_id'], 0) + r['lineal_ft']
                    run[r['quality_id']] = run.get(r['quality_id'], 0) + (r['seconds'] - r['stop_seconds']) / 60
                return sum(speed_of(g) * run[g] - feet[g] for g in feet) / t if t else 0.0
            if dp._reference(row) and against == 'previous':
                return abs(below(receipt_rows, t_ref))
            return abs(below(side(receipt_rows, 'current'), t_cur) - (below(other_rows(url), t_ref) if against != 'baseline' else 0.0))
        if bucket == 'mix':
            def share(rows, t):
                out = {}
                for r in rows:
                    out[r['quality_id']] = out.get(r['quality_id'], 0) + r['seconds'] / 60 / t
                return out
            now = share(side(receipt_rows, 'current'), t_cur) if not (dp._reference(row) and against == 'previous') else {}
            if against == 'baseline':
                year = self.rows(self.also(entry, 'the trailing year'))
                ref = share(year, sum(r['seconds'] for r in year) / 60)
            else:
                ref = share(other_rows(url) if not (dp._reference(row) and against == 'previous') else receipt_rows, t_ref)
            if part == 'row':
                return abs(sum(speed_of(g) * s for g, s in ref.items()) - sum(speed_of(g) * s for g, s in now.items()))
            reference_base = sum(speed_of(g) * s for g, s in ref.items())   # a grade's part of the mix: its share change at its distance from the reference
            g = row['item']
            return abs((ref.get(g, 0.0) - now.get(g, 0.0)) * (speed_of(g) - reference_base))
        raise Unchecked(f'speed variance bucket {bucket}')

    # ---- every tool's figures
    def expected(self, result, entry, part, index, section, receipt):
        """(value, exact) of the section's bold figure, recomputed from the receipt's rows."""
        tool, data = entry['tool'], entry['data']
        url, of = receipt['url'], receipt['of']
        rows = self.rows(url)
        text, value, decimals = printed(section)
        after = section[section.index('**' + text + '**') + len(text) + 4:]
        row = data[dp.PARTS[part]][index] if part else None
        if of == 'recurrence':   # shifts on which the reason alone was over its band, over the trailing year
            band = targets.BANDS[{'Maintenance': 'maintenance', 'Operator': 'operator'}[row['kind']]]['flag'] / 100 * 480
            minutes = {}
            for r in rows:
                minutes[(r['production_date'], r['shift'])] = minutes.get((r['production_date'], r['shift']), 0) + r['seconds'] / 60
            return sum(m > band for m in minutes.values()), True
        if tool in ('get_downtime_breakdown', 'get_downtime_review') and part == 'flag':
            return stop_minutes(rows) / 480 * 100, False
        if tool == 'get_downtime_breakdown':
            return (stop_minutes(rows) / 480 * 100 if after.startswith(' of the shift') else stop_minutes(rows)), False
        if tool == 'get_downtime_pareto':
            return (len(rows), True) if 'stop' in text else (stop_minutes(rows), False)
        if tool == 'get_downtime_review' and part is None:
            return sum(r['maintenance_seconds'] + r['operator_seconds'] for r in rows) / sum(r['scheduled_seconds'] for r in rows) * 100, False
        if tool == 'get_downtime_review':
            if text.endswith('min'):
                return stop_minutes(rows), False
            return stop_minutes(rows) / (sum(r['scheduled_seconds'] for r in self.whole(entry)) / 60) * 100, False
        if tool in ('get_period_kpis', 'compare_periods'):
            return RecordStore._kpis(tile_sums(rows))[row['metric']], False
        if tool == 'get_trend':
            return RecordStore._kpis(tile_sums(rows))[data['metric']], False
        if tool == 'get_grade_totals':
            if 'grades observed' in after:
                return len({r['quality_id'] for r in rows}), True
            if text.endswith('ft/min'):
                return sum(r['lineal_ft'] for r in rows) / (sum(r['seconds'] - r['stop_seconds'] for r in rows) / 60), False
            return sum(r['gross_sqft'] for r in rows) / 1000, False
        if tool == 'get_opportunities':
            field = 'usd_per_year'
            if part == 'row':
                return self.lever_usd(entry, index, field), False
            levers = range(len(data['rows']))
            if 'controllable losses' in after:
                levers = [j for j in levers if data['rows'][j].get('control') == 'controllable']
            elif 'Dry-end waste (tag scrap)' in section:
                levers = [j for j in levers if str(data['rows'][j]['lever']).startswith('Dry-end:')]
            return sum(self.lever_usd(entry, j, field) for j in levers), False
        if tool in ('get_shift_dollars', 'get_day_dollars'):
            if part == 'row':
                return self.priced(entry, row, self.line_quantity(entry, row, url), 'usd'), False
            return sum(self.lever_usd(entry, j, 'usd') for j, r in enumerate(data['rows']) if not r.get('apart')), False
        if tool == 'get_overrun':
            nominal, actual = (sum(r[c] for r in rows) for c in ('nominal_cuts', 'actual_cuts'))
            return (actual - nominal) / nominal * 100, False
        if tool == 'get_quality_breakdown':
            return sum(r['sqft'] for r in rows), False
        if tool == 'get_rankings':
            if data['metric'] == 'speed':
                return sum(r['lineal_ft'] for r in rows) / sum(r['target'] * r['seconds'] / 60 for r in rows) * 100, False
            return (stop_minutes(rows) if data['metric'] == 'downtime' else sum(r['sqft'] for r in rows)), False
        if tool == 'get_rollstock':
            return sum(r['gross_sqft'] for r in rows) / 1000, False
        if tool == 'get_setup_matrix':
            (s,) = rows
            if 'grade target' in after:
                return s['lineal_ft'] / (s['target'] * s['seconds'] / 60) * 100, False
            if 'allocated stops' in after:
                return s['stop_seconds'] / 60, False
            if 'rejected' in after:
                return s['reject_sqft'], False
        if tool in ('get_shift_kpis', 'get_shift_overview'):
            lead = re.sub(r'^- (?:Shift \d · )?', '', section)   # an all-shifts answer leads each line with its shift
            if receipt['table'] == 'stops':
                return stop_minutes(rows), False
            if receipt['table'] == 'rejects':
                return sum(r['sqft'] for r in rows), False
            if receipt['table'] == 'setups':
                return runs_below_target(rows)[0], True
            (k,) = rows
            if 'lineal ft' in text or lead.startswith('Lineal feet'):
                return k['lineal_ft'], False
            if text.endswith('ft/min'):
                return k['lineal_ft'] / 480, False
            if 'of yield rejected at the dry end' in after:
                return k['reject_sqft'] / (k['gross_sqft'] - k['trim_sqft'] - k['shear_sqft']) * 100, False
            if 'dry-end setups' in after:
                return k['setups'], True
            if 'trim flag' in section:   # the setups whose own trim is over the flag: a column of the shift's row
                return k['setups_above_trim_target'], True
            if 'maintenance and operator downtime' in after:
                return (k['maintenance_seconds'] + k['operator_seconds']) / 288, False
            if 'maintenance downtime' in after:
                return k['maintenance_seconds'] / 288, False
            if 'operator downtime' in after or lead.startswith('Operator downtime'):
                return k['operator_seconds'] / 288, False
            if 'grade target' in after:
                return k['lineal_ft'] / k['target_ft'] * 100, False
            if text.endswith('ft/min'):
                return k['lineal_ft'] / 480, False
            if lead.startswith('Downtime:'):
                return k['stop_seconds'] / 288, False
            if lead.startswith('Dry-end waste'):
                return k['reject_sqft'] / (k['gross_sqft'] - k['trim_sqft'] - k['shear_sqft']) * 100, False
            if lead.startswith('Good output'):
                return good_output(rows), False
            if 'lineal ft' in text or lead.startswith('Lineal feet'):
                return k['lineal_ft'], False
        if tool == 'get_shift_notes':
            return len(rows) + len(self.rows(self.also(entry, 'the shift record'))), True
        if tool == 'get_speed_opportunity':
            if part == 'row':
                return best_fifth_hours(rows), False
            cells = {}
            for r in rows:
                key = (r['quality_id'], r['web_width_in'] if data['rows'][0]['slice_by'] == 'width' else self.crew_of(r))
                cells.setdefault(key, []).append(r)
            return sum(h for h in (best_fifth_hours(c) for c in cells.values()) if h is not None), False
        if tool == 'get_speed_variance':
            return self.speedvar(entry, part, index, rows, url), False
        if tool == 'get_stops_profile':
            if part == 'weekday':
                window = data['window']
                first, last = _date.fromisoformat(window['start']), _date.fromisoformat(window['end'])
                days = sum(1 for n in range((last - first).days + 1) if (first + _timedelta(n)).strftime('%a') == row['weekday'])
                return len(rows) / days, False
            return len(rows), True
        if tool == 'get_waste_breakdown':
            whole = self.whole(entry)
            gross = sum(r['gross_sqft'] for r in whole)
            if part is None:
                return good_output(rows), False
            if part == 'detail':   # the tool rounds a reason's share of dry-end to one place and prints two (see the PR thread)
                return sum(r['sqft'] for r in rows) / sum(r['reject_sqft'] for r in whole) * 100, 1
            table = receipt['table']
            column = {'setups': f'{row["category"]}_sqft', 'rejects': 'sqft', 'setup_orders': f'{row["category"]}_sqft', 'shifts': 'bale_sqft'}[table]
            amount = unexplained(rows) if row['category'] == 'unexplained' else sum(r[column] for r in rows)
            if after.startswith(' of the yield') or after.startswith(' of yield'):
                return amount / (gross - sum(r['trim_sqft'] + r['shear_sqft'] for r in whole)) * 100, False
            return (amount / gross * 100 if '%' in text else amount), False
        if tool == 'get_wet_end_performance':
            if part is None and 'trailing-year running speed' in section:   # runs under their grade's baseline, running speed
                base, runs = baselines(self.rows(self.also(entry, 'the trailing year'))), {}
                for r in rows:
                    feet, run = runs.get(r['wet_end_id'], (0.0, 0.0, None))[:2]
                    runs[r['wet_end_id']] = (feet + r['lineal_ft'], run + (r['seconds'] - r['stop_seconds']) / 60, r['quality_id'])
                return sum(1 for feet, run, g in runs.values() if g in base and run and feet / run < base[g]), True
            if part is None:
                return runs_below_target(rows)[0], True
            return sum(r['lineal_ft'] for r in rows) / sum(r['target'] * r['seconds'] / 60 for r in rows) * 100, False
        if tool == 'get_order_matrix' and part is None:
            return len(rows), True
        raise Unchecked(f'{tool} {part}: no figure check for {section[:80]!r}')

    # ---- "N of M": the other numbers of a count, recomputed too
    OF = re.compile(r'\*\*[^*]+\*\* of ([\d,]+)\b')
    RUNS_UNDER = re.compile(r'([\d,]+) of ([\d,]+) wet-end runs under their grade baseline')

    def counts(self, entry, part, index, section, receipt, rows):
        """[(what, recomputed, printed)] for the numbers beside a figure that count something: the M after a bold N
        ("**14** of 14", "**1** of 783 shifts since …", "**2,563,521 sq ft** of 2,694,377 gross") and a lever's "43 of 69
        wet-end runs under their grade baseline". A section with such a number and no rule for it is unchecked."""
        out = []
        m = self.OF.search(section)
        if m:
            out.append(('of', self._of(entry, index, section, receipt, rows), float(m.group(1).replace(',', ''))))
        m = self.RUNS_UNDER.search(section)
        if m:
            under, total = self._runs_under(entry, rows)
            out += [('runs under', under, float(m.group(1).replace(',', ''))), ('runs', total, float(m.group(2).replace(',', '')))]
        return out

    def _of(self, entry, index, section, receipt, rows):
        if receipt['of'] == 'recurrence':   # the shift records of the trailing year the count covers (record_store._recurrence)
            flag = entry['data']['flags'][index]
            span = {k: v for k, v in dp._recurrence_filter(flag).items() if k in ('from', 'to', 'shift')}
            return len(self.rows(dp.table_url('shifts', span)))
        if receipt['table'] == 'setups' and ('grade target' in section or 'running speed' in section):
            return len({r['wet_end_id'] for r in rows})   # the wet-end runs of those setups
        if receipt['table'] == 'shift_kpis' and len(rows) == 1:
            if 'trim flag' in section:
                return rows[0]['setups']
            if 'Good output' in section:
                return rows[0]['gross_sqft']
        raise Unchecked(f'{entry["tool"]}: no check for the M of {section[:80]!r}')

    def _runs_under(self, entry, rows):
        """A speed lever's wet-end runs (a run's setups on one day, run and grade) under their grade's trailing-year
        running speed, and all of them."""
        base, runs = baselines(self.rows(self.also(entry, 'the trailing year'))), {}
        for r in rows:
            key = (r['production_date'], r['wet_end_id'], r['quality_id'])
            feet, run = runs.get(key, (0.0, 0.0))
            runs[key] = (feet + r['lineal_ft'], run + (r['seconds'] - r['stop_seconds']) / 60)
        return sum(1 for (_, _, g), (feet, run) in runs.items() if g in base and run and feet / run < base[g]), len(runs)

    def check(self, result, i, section):
        receipt = result['receipts'][i]
        m = dp.REF.search(section)
        entry = result['trace'][int(m.group(1)) - 1]
        part, index = m.group(2), int(m.group(3)) if m.group(3) else None
        if not receipt or not receipt['url'].startswith('/data/'):
            return f'no receipt for {section[:90]!r}'
        rates = entry.get('data', {}).get('rates') if isinstance(entry.get('data'), dict) else None
        self.rates = rates if isinstance(rates, dict) and 'values' in rates else cost_model.effective()   # the answer's own rates
        try:
            want, exact = self.expected(result, entry, part, index, section, receipt)
        except Unchecked as exc:
            return f'unchecked: {exc}'
        text, value, decimals = printed(section)
        if exact is not True and exact is not False:   # the tool rounded to `exact` places and printed more
            decimals, exact = exact, False
        # A count is exact. A measure may be one unit of its last printed digit off: the tools round their rows' quantities
        # before they sum or divide them, and the check recomputes from the unrounded rows.
        tolerance = 10 ** -decimals * (0.5 if exact else 1.0) + 1e-9 * abs(value)
        if want is None or abs(round(want, 6) - value) > tolerance:
            return f'**{text}** but {want!r} over {receipt["url"]} ({entry["tool"]} {part} {index}): {section[:110]!r}'
        try:
            for what, want, got in self.counts(entry, part, index, section, receipt, self.rows(receipt['url'])):
                if abs(want - got) > 0.5:
                    return f'{what} {got:g} but {want!r} over {receipt["url"]} ({entry["tool"]} {part} {index}): {section[:110]!r}'
        except Unchecked as exc:
            return f'unchecked: {exc}'
        return None


# The data-quality demo's day (an excluded record in shift 2): a daily tool links the corrupt variant of shift_kpis, a
# period tool the records as generated (the #142 review). The golden set's own corrupt cases ask by POST, not by URL.
CORRUPT_URLS = ('/corr/1/day/2026-09-14/shift/2?measure=overview&corrupt=1', '/corr/1/day/2026-09-14/shift/2?measure=kpis&corrupt=1',
                '/corr/1/day/2026-09-14/shift/2?measure=waste&corrupt=1', '/corr/1/day/2026-09-14/shift/2?measure=dollars&corrupt=1',
                '/corr/1/day/2026-09-14?measure=dollars&corrupt=1', '/corr/1/day/2026-09-14/shift/2?measure=downtime&corrupt=1')


def golden_urls():
    cases = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
    return sorted({c['expect']['url'] for c in cases if c.get('mode', 'offline') == 'offline' and (c.get('expect') or {}).get('url')}) + list(CORRUPT_URLS)


def golden_case_answers():
    """(case id, answer) of every offline golden case with no address of its own that answers (a question asked by
    POST, a follow-up's turns, an analyst's scripted plan): run as evals/run.py runs it, receipts attached as the
    server attaches them. The days it opens are closed again, so later tests do not carry them (CI on #151)."""
    sys.path.insert(0, str(ROOT / 'evals'))
    import run as golden_run
    cases = json.loads((ROOT / 'evals' / 'golden.json').read_text())['cases']
    opened = set(golden_run._DATASETS)
    try:
        for case in cases:
            if case.get('mode', 'offline') != 'offline' or (case.get('expect') or {}).get('url') or (case.get('expect') or {}).get('raises'):
                continue
            for result in golden_run.run_case_detail(case)[4]:
                if isinstance(result, dict) and isinstance(result.get('sections'), list):
                    yield case['id'], dp.attach_sources(result, case.get('corrupt', False))
    finally:
        for key in set(golden_run._DATASETS) - opened:
            golden_run._DATASETS.pop(key).close()


def receipt_failures(store, urls, answers=()):
    """(failures, receipts checked, tools seen) over the answers of these addresses, and of these (name, answer) pairs."""
    app, figures = Workbench(store=store), Figures(store)
    failures, checked, tools = [], 0, set()

    def answered():
        for url in urls:
            try:
                yield url, app.answer_url(url)[0]
            except ValueError:
                continue   # a refusal case: no answer, no receipt
        yield from answers
    try:
        for name, result in answered():
            assert len(result['receipts']) == len(result['sections']), name
            for i, section in enumerate(result['sections']):
                if not dp.REF.search(section):
                    assert result['receipts'][i] is None, (name, section)
                    continue
                problem = figures.check(result, i, section)
                if problem:
                    failures.append(f'{name}: {problem}')
                checked += 1
                tools.add(result['trace'][int(dp.REF.search(section).group(1)) - 1]['tool'])
    finally:
        app.close()
    return failures, checked, tools


# The coordinator's five reproductions (#151 review): each linked to rows that did not hold its figure before this round.
REPRODUCTIONS = ('/corr/1/day/2026-09-11?measure=review',                                   # 1. a previous-window finding
                 '/corr/1/day/2026-09-14?measure=rollstock&window=month',                   # 1. "Previous window: N MSF"
                 '/corr/1/day/2026-01-05?measure=review',                                   # 2. a recurrence count
                 '/corr/1/day/2026-09-14/shift/2?measure=summary',                          # 3. the shift summary's findings
                 '/corr/1/day/2026-03-09?measure=speedvar&window=week&against=shift&shifts=3,1',   # 4. a shift-comparison gap
                 '/corr/1/day/2026-09-14?measure=dollars&shifts=1,3')                       # 5. a day's money over some shifts


def test_the_five_reproductions_link_to_rows_that_hold_their_figures(store):
    """Each linked to rows that did not hold its bold figure (the coordinator's review of #151); now each figure is
    recomputed from the rows its receipt opens."""
    failures, checked, _ = receipt_failures(store, REPRODUCTIONS)
    assert not failures, '\n'.join(failures)
    assert checked > 40
    app = Workbench(store=store)
    try:
        money, _ = app.answer_url('/corr/1/day/2026-09-14?measure=dollars&shifts=1,3')
        day = next(e for e in money['trace'] if e['tool'] == 'get_day_dollars')
        assert day['source']['url'] == '/data/shift_kpis?day=2026-09-14&shift=1,3&corrupt=false'
        # No section cites the day's total, so it is recomputed here: each lever's quantity from the rows it declares,
        # priced at the answer's rates, summed over the two shifts (the whole day's three gave about $25k, not $16,774).
        figures = Figures(store)
        figures.rates = cost_model.effective()
        assert {(r['production_date'], r['shift']) for r in figures.rows(day['source']['url'])} == {('2026-09-14', 1), ('2026-09-14', 3)}
        total = sum(figures.lever_usd(day, j, 'usd') for j, r in enumerate(day['data']['rows']) if not r.get('apart'))
        assert abs(total - day['data']['total_usd']) < 1, (total, day['data']['total_usd'])
        gap, _ = app.answer_url('/corr/1/day/2026-03-09?measure=speedvar&window=week&against=shift&shifts=3,1')
        assert gap['receipts'][0]['url'] == '/data/shift_kpis?from=2026-03-09&to=2026-03-13&shift=1,3&corrupt=false'
        review, _ = app.answer_url('/corr/1/day/2026-09-11?measure=review')
        previous = next(r for r in review['receipts'] if r and r['of'] == 'previous window')   # the bullet under "The previous window"
        assert previous['of'] == 'previous window' and 'day=2026-09-10' in previous['url']
    finally:
        app.close()


def test_a_label_reads_only_the_bullets_of_its_own_group(store):
    """A bullet under "The previous window" opens that window's rows; another step's bullets that follow the group with
    nothing between (shapes.PeriodAnswer appends each step's summary line after the lead's) are about their own window,
    whatever label is above them (the reviewer on #151)."""
    app = Workbench(store=store)
    try:
        review, _ = app.answer_url('/corr/1/day/2026-09-11?measure=review')
        kpis, _ = app.answer_url('/corr/1/day/2026-09-11?measure=kpis')
    finally:
        app.close()
    at = review['sections'].index('The previous window')
    n, line = len(review['trace']) + 1, dp.REF.sub('', kpis['sections'][0])   # "**7,425.2 MSF** · +356.2 vs 7,069.0 MSF, better"
    composed = {'production_day': '2026-09-11', 'trace': [*review['trace'], kpis['trace'][0]],
                'sections': [*review['sections'][:at + 2], f'- All shifts: {line} (query {n} · row 0)', f'- All shifts: {line} (query {n})']}
    got = dp.receipts(composed)
    assert got[at + 1]['of'] == 'previous window' and 'day=2026-09-10' in got[at + 1]['url'], got[at + 1]
    assert [r['of'] for r in got[-2:]] == ['row', 'result'] and all('day=2026-09-11' in r['url'] for r in got[-2:]), got[-2:]


def test_receipts_on_golden_answers_link_to_rows_that_hold_the_bold_figure(store):
    """Every section of every offline golden answer that cites a step, by its address or not: its receipt opens rows, and
    the section's bold figure, recomputed from those rows with the receipt's filter, is the figure printed; so are the
    other numbers of a count ("N of M")."""
    failures, checked, tools = receipt_failures(store, golden_urls(), golden_case_answers())
    assert not failures, f'{len(failures)} of {checked}:\n' + '\n'.join(failures[:25])
    assert checked > 1000 and len(tools) >= 20, (checked, sorted(tools))


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
    # the shift summary (a receipt per section that is not its step's) and a lens's levers (re-ranked at a visitor's rates)
    app = Workbench(store=store)
    try:
        for name in ('summary', 'money_supervisor'):
            saved = json.loads((FIXTURES / f'{name}.json').read_text())
            fresh = json.loads(json.dumps(app.answer_url(saved['url'])[0]))
            refresh = (f"INVESTIGATOR_AS_OF=2026-09-30 python3 -c \"import json;from investigator.server import Workbench;"
                       f"r,_=Workbench().answer_url('{saved['url']}');r.pop('report_markdown',None);"
                       f"json.dump(r,open('tests/ui_fixtures/{name}.json','w'),separators=(',',':'),sort_keys=True)\"")
            assert (saved['sections'], saved['receipts']) == (fresh['sections'], fresh['receipts']), f'refresh with: {refresh}'
            assert [e.get('source') for e in saved['trace']] == [e.get('source') for e in fresh['trace']], f'refresh with: {refresh}'
    finally:
        app.close()


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
