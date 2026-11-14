"""The data browser (issue #142): the tables every answer reads, their schema and their rows, at /data.

Datasette-style and read-only. `/data` lists each table the tools read with what it is, its row count in the
window and its first and last day; `/data/<table>` shows every column with its type, its unit and its meaning
from the record contract (a link to the contract row), then 50 rows at a time, newest first, filtered by the
URL's own parameters, so the URL is the state; `/data/<table>.csv` downloads the filtered rows, at most
CSV_LIMIT of them. docs/URLS.md has the grammar.

Every query is one fixed SELECT over an allowlisted table and its allowlisted columns with every visitor value
bound as a parameter, a LIMIT, and the record store's own lock, time cap and row cap (`RecordStore._fetch`, the
bounds every tool runs under). There is no free-form SQL, no system table and no file path, in public mode or on
the workbench. The tables are the store's: the record contract (docs/RECORD_CONTRACT.md) and the derived
`shift_kpis` (docs/METRICS.md). Nothing here writes.

Receipts (`SOURCES`, `attach_sources`): each tool declares the table and filter it read, so an answer's
"query N · row M" links to the rows behind its figure. The server attaches the declaration to every trace entry
as `source` (table, filter, url, and a url per cited row), and the page puts a link beside the receipt.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import csv
import io
import json
import math
import re
import time
from urllib.parse import parse_qsl, quote

from .record_store import SHIFT_KPIS, TABLES as STORE_TABLES
from .records import PLANNED_STOP_REASON, SCHEDULED_STOP_LABEL

PER_PAGE = 50
MAX_PAGE = 200             # deepest page: 10,000 rows in, as deep as the CSV; past it a narrower filter is the way further
CSV_LIMIT = 10_000         # rows in one CSV download of a filter
CSV_CHUNK = 1_000          # rows per read of a CSV: the store's lock is let go between reads, so a download never holds it long
MAX_VALUE = 120            # characters in one filter value
MAX_VALUES = 20            # values of one column filter given more than once (any of them matches)
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')
CONTRACT = '/docs/record-contract'
METRICS = '/docs/metrics'
# The parameters every dated table takes, before its column filters: the production day, a span, a weekday, a shift,
# and the crew on that shift (a table without a crew column of its own reads it from `shifts`). A table that names a
# setup but not its grade (lanes, stops, reject lines) also takes `quality_id` (`grade`), the board grade of that setup,
# and one that names a lane but not its order (reject lines) takes the lane's `order_number` and `customer_id`.
LANE_KEYS = ('order_number', 'customer_id')
SCOPE_PARAMS = ('day', 'from', 'to', 'weekday', 'shift', 'crew')
SINGLE = ('day', 'from', 'to', 'weekday')   # given once; shift takes a list (shift=1,3), a column filter (and crew) may be given again
# A word the issue and a plant use for a column: ?order=SO-... is the lane's order number, ?grade=32-C the board grade.
ALIASES = {'order': 'order_number', 'grade': 'quality_id'}
# The stop kinds as the store spells them; the contract's rule: a filter that names the planned kind `pm` answers as Scheduled.
STOP_KINDS = {'maintenance': 'Maintenance', 'operator': 'Operator', 'scheduled': 'Scheduled', 'pm': 'Scheduled', 'external': 'External'}


@dataclass(frozen=True)
class Table:
    name: str
    label: str
    meaning: str        # one sentence from the record contract
    order: str          # the production order, newest first: the store writes rows in it, so a page reads rowid DESC (a test holds it)
    docs: str           # the docs page whose table documents its columns
    dated: bool = True  # has production_date (the dimensions do not)


# Every table the tools read, in the order /data lists them. Their meanings are the contract's own sentences.
TABLES = {t.name: t for t in (
    Table('setups', 'Dry-end run setups',
          'One row per run setup, the fact table: up to three orders (lanes) across the web, the setup\'s clock, '
          'its feet, square feet and every waste line of its own.',
          'production_date DESC, started DESC', CONTRACT),
    Table('setup_orders', 'Order lanes',
          'One row per order lane of a setup: one customer\'s slice of the web, its sheet size, outs, cuts, '
          'cutoff knife and waste boards.',
          'production_date DESC, setup_id DESC', CONTRACT),
    Table('stops', 'The downtime log',
          'One row per stop: the run setup that holds it, its kind (Maintenance, Operator, Scheduled; a power '
          'outage counts as External), place, reason, seconds and the operator\'s comment.',
          'production_date DESC, started DESC', CONTRACT),
    Table('rejects', 'Reject lines',
          'One row per reject line: one lane\'s rejected sheets by category and reason, with where on the board '
          'as attributes (liner, side).',
          'production_date DESC, setup_id DESC', CONTRACT),
    Table('wet_ends', 'Wet-end paper runs',
          'One row per paper run: consecutive setups on one grade and web width, its totals exact sums of its setups.',
          'production_date DESC, started DESC', CONTRACT),
    Table('rolls', 'Roll stock',
          'One row per roll of paper fed into a paper run: its stand, paper, feet fed, peel and core.',
          'production_date DESC, roll_id DESC', CONTRACT),
    Table('shifts', 'Shifts',
          'One row per 480-minute shift: its crew and the exact sums of its setups, with the bale\'s scale reading.',
          'production_date DESC, shift DESC', CONTRACT),
    Table('shift_kpis', 'Shift KPI sums (derived)',
          'One row per production day, shift and variant of raw numerators and denominators, built once from the '
          'tables above; every KPI tile is a ratio of sums of these columns.',
          'production_date DESC, shift DESC, corrupt ASC', METRICS),
    Table('customers', 'Customers',
          'The customer dimension: forty invented customers and C000, the plant itself, for pallet sheets; no measures.',
          'rowid ASC', CONTRACT, dated=False),
    Table('reject_reasons', 'The reject taxonomy',
          'One row per reason of the reject taxonomy (reject_reasons.json): its category, family, waste line and '
          'the positions it can be recorded at.',
          'rowid ASC', CONTRACT, dated=False),
)}

# ---- the columns: unit and meaning, each from the contract row the link names (tests/test_datapages.py holds the
# list to the store's own columns and to the contract's rows: a column without a documented meaning fails).
D, K = 'date', 'key'
_DAY = (D, 'the production day, 07:00 to 07:00, labelled by its start date')
_SHIFT = ('shift 1–3', 'shift 1 starts 07:00, 2 at 15:00, 3 at 23:00, each 480 scheduled minutes')
_SETUP = (K, 'the run setup: machine code + start stamp + sequence in the shift')
_WET_END = (K, 'the paper run: WE + production date + sequence')
COLUMNS = {
    'setups': {
        'setup_id': _SETUP, 'production_date': _DAY, 'shift': _SHIFT,
        'wet_end_id': (K, 'the paper run the setup belongs to'),
        'quality_id': ('grade', 'the board grade (26-C, 32-C, 55-C, 200-C, 275-BC, 275-EB)'),
        'target': ('ft/min', "the grade's target speed, a configured table"),
        'started': ('timestamp', 'start of the setup, date and time joined'),
        'ended': ('timestamp', 'end of the setup, date and time joined'),
        'seconds': ('s', 'elapsed time, end minus start: running time plus the stops inside the setup'),
        'stop_seconds': ('s', 'stop time of the setup: the sum of its rows in the downtime log'),
        'stops': ('count', 'stop count of the setup: its rows in the downtime log'),
        'lineal_ft': ('ft', "lineal feet as run: the longest lane's actual cuts × its length / 12, plus the shear chops"),
        'gross_sqft': ('sq ft', 'gross square feet made: web × lineal / 12'),
        'trim_sqft': ('sq ft', 'side trim: trim inches × order travel / 12'),
        'shear_sqft': ('sq ft', 'the shear chops: full-web, 34 in each, part of gross'),
        'reject_sqft': ('sq ft', 'dry-end quality reject sq ft: bond, warp, misalignment, moisture, other'),
        'web_width_in': ('in', 'web (paper) width: 90–110 in, 5-in steps, the narrowest that fits the lanes plus the minimum trim'),
        'change_code': ('code', 'O a regular order change, S a shift split continuing split_from'),
        'split_from': (K, 'the setup an S record continues across a shift boundary'),
        'run_seconds': ('s', 'actual run time: elapsed minus the stops inside the setup'),
        'scheduled_lineal_ft': ('ft', "scheduled (nominal) lineal feet: the longest lane's nominal cuts × its length / 12"),
        'avg_fpm': ('ft/min', 'average speed: lineal / elapsed minutes, stops included'),
        'trim_in': ('in', 'side trim width: web width minus the used width, split evenly both sides'),
        'trim_pct': ('%', 'side trim: trim inches / web × 100'),
        'swing_knife_sqft': ('sq ft', 'swing-knife waste; null when the machine has no swing knife (the demo corrugator has none), never zero'),
        'is_fanfold': ('yes/no', 'the setup ran to a fanfold delivery (one lane)'),
        'sheet_type': ('text', 'product, bottom_sheet or cap_sheet (product when any lane is)'),
        'filler': ('yes/no', 'a pallet-sheet lane was added to fill a tough trim'),
        'lanes': ('count', 'order lanes across the web, 1–3'),
        'changeover_sqft': ('sq ft', 'changeover reject lines, counted with shear'),
        'admin_sqft': ('sq ft', 'planning / admin reject entries, reported apart (not production waste)'),
        'cutoff_waste_sqft': ('sq ft', "the lanes' cutoff-knife waste boards"),
        'stacker_waste_sqft': ('sq ft', "the lanes' stacker waste boards"),
    },
    'setup_orders': {
        'setup_id': _SETUP, 'production_date': _DAY, 'shift': _SHIFT,
        'lane': ('lane 1–3', 'lane across the web'),
        'order_number': (K, "SO-YYMMDDnnnn: the production day and the order's sequence in it"),
        'part_number': (K, 'item number: P-nnnnn (PLT-wwll for pallet sheets)'),
        'customer_id': (K, 'the customer, per lane (C000 is the plant)'),
        'order_quantity': ('sheets', 'the sheets the order asks for over all its setups; 0 for a filler lane'),
        'sheet_width_in': ('in', 'sheet width, in eighths of an inch'),
        'sheet_length_in': ('in', 'sheet length: the cutoff'),
        'outs': ('count', 'outs: sheets across the web for this lane'),
        'cutoff_knife': ('knife 1–2', 'the cutoff knife the lane runs on: lane N → knife N → stacker N'),
        'nominal_cuts': ('count', 'scheduled cuts'),
        'actual_cuts': ('count', 'actual cuts, within ±10% of scheduled (the over/under-run window)'),
        'sheet_type': ('text', 'product, or a pallet bottom / cap sheet'),
        'filler': ('yes/no', 'a pallet-sheet lane run as a filler'),
        'cutoff_waste_boards': ('boards', "boards lost at the lane's cutoff knife at an order change (none on an S split)"),
        'stacker_waste_boards': ('boards', "boards lost at the lane's stacker at an order change (none on an S split)"),
        'cutoff_waste_sqft': ('sq ft', "the cutoff-knife boards × the lane's sheet size"),
        'stacker_waste_sqft': ('sq ft', "the stacker boards × the lane's sheet size"),
        'stack_height': ('sheets', 'sheets per stack at the stacker'),
        'folds': ('count', 'fanfold folds; null unless fanfold'),
        'sheets_per_pallet': ('sheets', 'fanfold sheets per pallet; null unless fanfold'),
        'pallets': ('count', 'fanfold pallets; null unless fanfold'),
        'perforation_length_in': ('in', 'fanfold perforation length; null unless fanfold'),
        'width_share': ('ratio', "the lane's outs × sheet width / the setup's used width"),
        'allocated_trim_sqft': ('sq ft', "the setup's side trim × width_share: this customer's part of it"),
        'allocated_shear_sqft': ('sq ft', "the setup's shear chops × width_share: this customer's part of them"),
    },
    'stops': {
        'stop_id': (K, 'the stop: ST + start stamp + sequence'), 'production_date': _DAY, 'shift': _SHIFT,
        'setup_id': (K, 'the run setup that holds the stop'),
        'kind': ('text', 'the category: Maintenance, Operator or Scheduled (planned maintenance); a power outage counts as External'),
        'place': ('text', 'the reason group: Splicer 1–5, Stacker, Upper / Lower / Shear knife, Slitter/scorer, Double backer, ...'),
        'reason': ('text', 'missed splice, knife / slitter jam, stacker, raw material, order change, ...; a Scheduled stop is Planned maintenance'),
        'seconds': ('s', 'the stop\'s duration: ended − started'),
        'started': ('timestamp', 'start of the stop'),
        'ended': ('timestamp', 'end of the stop'),
        'comment': ('text', "the operator's free text, verbatim and unverified; about half are empty"),
    },
    'rejects': {
        'setup_id': _SETUP, 'production_date': _DAY, 'shift': _SHIFT,
        'family': ('text', 'the dry-end tile the category adds to (BOND, WARP, MISALIGNMENT, MOISTURE, OTHER) or CHANGEOVER, ADMIN'),
        'category': ('text', "the taxonomy's category: bond, warp, moisture, cracking, misalignment, handling, paper, changeover, print, admin"),
        'reason': ('text', 'the reported reason within the category: a recorded observation, not a proven cause'),
        'liner': ('text', 'where on the board: liner top, bottom, medium, DB or SF (an attribute, never a reason)'),
        'side': ('text', 'where on the board: side drive, operator or middle (an attribute, never a reason)'),
        'lane': ('lane 1–3', 'whose sheets were rejected'),
        'sheets': ('sheets', 'individual sheets; never multiplied by outs again'),
        'sqft': ('sq ft', 'sheets × sheet width × sheet length / 144'),
        'counted_in': ('text', 'the waste line it counts in: dry_end, shear (changeover) or excluded (planning / admin)'),
    },
    'wet_ends': {
        'wet_end_id': _WET_END, 'production_date': (D, 'the production day the run started'),
        'quality_id': ('grade', "the run's board grade"),
        'web_width_in': ('in', "the run's paper width"),
        'started': ('timestamp', "the run's first setup start"),
        'ended': ('timestamp', "the run's last setup end"),
        'lineal_ft': ('ft', 'lineal feet: the sum of its setups'),
        'gross_sqft': ('sq ft', 'gross square feet: the sum of its setups'),
        'trim_sqft': ('sq ft', 'side trim: the sum of its setups'),
        'max_fpm': ('ft/min', 'peak speed: the highest running speed of its setups'),
        'avg_fpm': ('ft/min', 'average speed: lineal / elapsed minutes, stops included'),
        'setup_count': ('count', 'the setups it holds'),
    },
    'rolls': {
        'roll_id': (K, 'the roll: R + production date + sequence'), 'production_date': (D, "the paper run's production day"),
        'wet_end_id': (K, 'the paper run the roll fed'),
        'stand': ('text', 'the roll stand: top liner, medium, bottom liner (double wall adds a second medium and the single-facer liner)'),
        'paper_grade': ('text', 'the paper on the stand'),
        'width_in': ('in', "the run's web width"),
        'lineal_ft': ('ft', "feet the roll fed into the run (a medium's include its flute take-up)"),
        'peel_sqft': ('sq ft', 'outer wraps stripped at roll prep'),
        'core_sqft': ('sq ft', 'paper left on the core when the roll was spliced out; 0 for the last roll on a stand'),
        'spliced_out': ('yes/no', 'the roll was spliced out (the last roll on a stand is taken down at the paper change)'),
    },
    'shifts': {
        'production_date': _DAY, 'shift': _SHIFT,
        'crew': ('text', 'the crew on shift (a synthetic label, not a person)'),
        'lineal_ft': ('ft', 'lineal feet: the sum of its setups'),
        'gross_sqft': ('sq ft', 'gross square feet: the sum of its setups'),
        'trim_sqft': ('sq ft', 'side trim: the sum of its setups'),
        'shear_sqft': ('sq ft', 'shear chops: the sum of its setups'),
        'reject_sqft': ('sq ft', 'dry-end reject sq ft: the sum of its setups'),
        'bale_sqft': ('sq ft', "the bale's scale reading for the shift"),
        'max_fpm': ('ft/min', 'peak speed: the highest running speed of its setups'),
        'avg_fpm': ('ft/min', 'average speed: lineal over 480 minutes'),
        'scheduled_minutes': ('min', 'scheduled minutes: 480'),
        'wet_end_count': ('count', 'the paper runs the shift touched'),
        'changeover_sqft': ('sq ft', "changeover rejects: the sum of its setups' lines"),
        'admin_sqft': ('sq ft', "planning / admin entries: the sum of its setups' lines"),
        'cutoff_waste_sqft': ('sq ft', "cutoff-knife waste boards: the sum of its setups' lines"),
        'stacker_waste_sqft': ('sq ft', "stacker waste boards: the sum of its setups' lines"),
    },
    'shift_kpis': {
        'production_date': _DAY, 'shift': _SHIFT,
        'corrupt': ('yes/no', "false: the records as generated; true: the data-quality demo's bad-trim record injected"),
        'crew': ('text', "the shift record's crew label"),
        'coverage': ('text', 'complete, or incomplete when a setup of the shift failed validation'),
        'excluded_setups': ('count', 'setups that failed validation, left out of every setup column'),
        'setups': ('count', 'dry-end setups'),
        'paper_changes': ('count', 'distinct wet-end IDs in the shift'),
        'wet_end_ids': ('list', 'the paper runs of the shift; a window counts distinct IDs across its rows'),
        'lineal_ft': ('ft', 'sum of lineal feet'),
        'target_ft': ('ft', 'sum of target speed × elapsed minutes: the grade-weighted target footage'),
        'elapsed_seconds': ('s', 'sum of the setup records\' elapsed seconds'),
        'setup_stop_seconds': ('s', 'stop seconds inside the setup records, subtracted from elapsed for running speed'),
        'gross_sqft': ('sq ft', 'sum of gross square feet: the denominator of every % of gross tile'),
        'trim_sqft': ('sq ft', 'sum of side trim'),
        'shear_sqft': ('sq ft', 'sum of shear chops'),
        'reject_sqft': ('sq ft', 'sum of dry-end reject sq ft, the dry-end families only'),
        'bond_sqft': ('sq ft', 'reject lines of the BOND family'),
        'warp_sqft': ('sq ft', 'reject lines of the WARP family'),
        'misalignment_sqft': ('sq ft', 'reject lines of the MISALIGNMENT family'),
        'moisture_sqft': ('sq ft', 'reject lines of the MOISTURE family'),
        'other_sqft': ('sq ft', 'reject lines of the OTHER family (cracking, handling, paper, print)'),
        'changeover_sqft': ('sq ft', 'changeover reject lines, counted with shear'),
        'admin_sqft': ('sq ft', 'planning / admin reject lines, reported apart'),
        'cutoff_waste_sqft': ('sq ft', "the lanes' cutoff-knife waste boards"),
        'stacker_waste_sqft': ('sq ft', "the lanes' stacker waste boards"),
        'setups_above_trim_target': ('count', 'setups whose own trim is above the per-setup trim flag of their gross'),
        'grade_gross_sqft': ('sq ft by grade', 'gross square feet per board grade; the values add to gross_sqft'),
        'scheduled_seconds': ('s', 'scheduled shift time'),
        'bale_sqft': ('sq ft', "the shift's bale scale reading"),
        'stops': ('count', 'stop records'),
        'stop_seconds': ('s', 'all stop seconds'),
        'maintenance_seconds': ('s', 'unplanned Maintenance stop seconds'),
        'planned_maintenance_seconds': ('s', 'Scheduled (planned maintenance) stop seconds'),
        'operator_seconds': ('s', 'Operator stop seconds'),
        'source_ids': ('list', 'the setup records summed'),
    },
    'customers': {
        'customer_id': (K, "the customer: C + number; C000 is the plant's own pallet sheets"),
        'name': ('text', 'an invented customer name'),
        'destination': ('text', 'the delivery route'),
        'ship_to': (K, 'the ship-to code'),
    },
    'reject_reasons': {
        'category': (K, 'the category key (bond, warp, moisture, cracking, misalignment, handling, paper, changeover, print, admin)'),
        'category_label': ('text', "the category's plant name"),
        'family': ('text', 'the tile it adds to: BOND, WARP, MISALIGNMENT, MOISTURE, OTHER, CHANGEOVER or ADMIN'),
        'counted_in': ('text', 'its waste line: dry_end, shear (counted with shear) or excluded (reported apart)'),
        'controllable': ('text', 'yes, no or partly'),
        'driver': ('text', 'what drives it: temperature, sequencing, handling, ...'),
        'reason': (K, 'the reason within the category'),
        'liner_positions': ('list', 'the liners it can be recorded at; empty when position does not apply'),
        'side_positions': ('list', 'the sides it can be recorded at; empty when position does not apply'),
        'generated': ('yes/no', 'false for bookkeeping entered downstream of the corrugator, never on a dry-end record'),
    },
}


def _types():
    """Each table's columns and DuckDB types, from the store's own definitions (never a system table)."""
    out = {name: [tuple(c.split(' ', 1)) for c in columns.split(', ')] for name, columns in STORE_TABLES.items()}
    out['shift_kpis'] = list(SHIFT_KPIS)
    return out


TYPES = _types()
FILTERABLE = ('VARCHAR', 'INTEGER', 'DOUBLE', 'BOOLEAN')   # equality filters; dates go through day/from/to


def contract_link(table, column=None):
    """The docs row that defines a column (docsite gives each contract row an id `col-<table>-<column>`)."""
    page = TABLES[table].docs
    return f'{page}#col-{table}-{column}' if column else f'{page}#col-{table}'


def columns(table):
    """[{name, type, unit, meaning, contract, filter}] in the store's column order."""
    return [{'name': name, 'type': kind, 'unit': COLUMNS[table][name][0], 'meaning': COLUMNS[table][name][1],
             'contract': contract_link(table, name), 'filter': kind in FILTERABLE and name != 'production_date'}
            for name, kind in TYPES[table]]


def filter_names(table):
    """The URL parameters a table page takes, in canonical order."""
    kinds = dict(TYPES[table])
    scope = [p for p in SCOPE_PARAMS if TABLES[table].dated and (p not in ('shift', 'crew') or 'shift' in kinds)
             and not (p == 'crew' and 'crew' in kinds)]
    grade = ['quality_id'] if 'setup_id' in kinds and 'quality_id' not in kinds else []   # the grade of the row's setup
    lane = list(LANE_KEYS) if {'setup_id', 'lane'} <= set(kinds) and 'order_number' not in kinds else []   # the row's lane's order
    return scope + grade + lane + [name for name, kind in TYPES[table] if kind in FILTERABLE and name not in ('shift', 'production_date')]


# ---------------------------------------------------------------- the URL grammar

def _check_text(value, name):
    if not isinstance(value, str) or len(value) > MAX_VALUE or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{name} takes up to {MAX_VALUE} printable characters')
    return value


def _date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_filters(table, query):
    """A query string as a table's filters: {param: typed value} in canonical order, plus the page. ValueError names
    the parameter that is wrong. Every column name comes from the allowlist; values are only ever bound."""
    if table not in TABLES:
        raise ValueError(f'No table {table!r}; the tables are {", ".join(TABLES)}')
    allowed, kinds = filter_names(table), dict(TYPES[table])
    raw, page, seen = {}, 1, set()
    for key, value in parse_qsl(query or '', keep_blank_values=True, max_num_fields=64):
        if key == 'page':
            if 'page' in seen:
                raise ValueError("'page' is given twice")
            seen.add('page')
            if not re.fullmatch(r'[1-9]\d{0,4}', value) or int(value) > MAX_PAGE:
                raise ValueError(f'page is a whole number from 1 to {MAX_PAGE}; past it, narrow the filter')
            page = int(value)
            continue
        if key == 'format' and value == 'json':   # the JSON representation asked for by parameter (the server already chose it)
            continue
        name = ALIASES.get(key, key)
        if name == 'production_date':
            name = 'day'
        if name not in allowed:
            raise ValueError(f'{key[:40]!r} is not a filter of {table}; its filters are {", ".join(allowed)}')
        if name in SINGLE and name in seen:
            raise ValueError(f'{name!r} is given twice')
        seen.add(name)
        if value == '':
            continue
        values = raw.setdefault(name, [])
        if len(values) >= MAX_VALUES:
            raise ValueError(f'{name!r} takes at most {MAX_VALUES} values')
        values.append(_check_text(value, name))
    out = {}
    for name in allowed:
        if name not in raw:
            continue
        typed = [_typed(table, name, kinds, value) for value in raw[name]]
        if name == 'shift':   # shift=1,3 and shift=1&shift=3 alike
            typed = [t for v in typed for t in (v if isinstance(v, tuple) else (v,))]
        typed = list(dict.fromkeys(typed))   # a repeated value once
        out[name] = typed[0] if len(typed) == 1 else sorted(typed, key=_value_text)
    if 'day' in out and ('from' in out or 'to' in out):
        raise ValueError('Give a day, or a span from and to, not both')
    if 'from' in out and 'to' in out and out['from'] > out['to']:
        raise ValueError('from is on or before to')
    return out, page


def _typed(table, name, kinds, value):
    """One filter value as its column's type, or a ValueError that names the parameter."""
    if name in ('day', 'from', 'to'):
        parsed = _date(value)
        if parsed is None:
            raise ValueError(f'{name} is a date, YYYY-MM-DD')
        return parsed
    if name == 'weekday':
        match = [w for w in WEEKDAYS if w.lower() == value.lower()[:3] and len(value) >= 3]
        if not match:
            raise ValueError('weekday is one of Mon, Tue, Wed, Thu, Fri')
        return match[0]
    if name == 'shift':   # one shift, or a list of them: shift=1,3 (the shifts a day's money was summed over)
        parts = value.split(',')
        if not parts or any(p not in ('1', '2', '3') for p in parts) or len(set(parts)) != len(parts):
            raise ValueError('shift is 1, 2 or 3, or a list of them such as 1,3')
        return int(parts[0]) if len(parts) == 1 else tuple(sorted(int(p) for p in parts))
    kind = kinds.get(name, 'VARCHAR')   # crew or grade without a column of their own are the shift's or the setup's: text
    if kind == 'INTEGER':
        if not re.fullmatch(r'-?\d{1,9}', value):
            raise ValueError(f'{name} is a whole number')
        return int(value)
    if kind == 'DOUBLE':
        try:
            number = float(value)
        except ValueError:
            number = math.nan
        if not math.isfinite(number) or not re.fullmatch(r'-?\d{1,9}(\.\d{1,6})?', value):
            raise ValueError(f'{name} is a number')
        return number
    if kind == 'BOOLEAN':
        if value.lower() not in ('true', 'false', '1', '0', 'yes', 'no'):
            raise ValueError(f'{name} is true or false')
        return value.lower() in ('true', '1', 'yes')
    if table == 'stops' and name == 'kind':
        return STOP_KINDS.get(value.lower(), value)
    return value


def _value_text(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        return f'{value:g}' if value == int(value) and abs(value) < 1e15 else repr(value)
    return str(value)


def table_url(table, filters=None, page=1, suffix=''):
    """The canonical address of a table page: its filters in canonical order, the page when past the first."""
    order = filter_names(table)
    filters = {k: v for k, v in (filters or {}).items() if v is not None}
    unknown = set(filters) - set(order)
    if unknown:
        raise ValueError(f'not a filter of {table}: {sorted(unknown)}')
    parts = []
    for k in order:
        if k not in filters:
            continue
        values = sorted(dict.fromkeys(filters[k]), key=_value_text) if isinstance(filters[k], (list, tuple)) else [filters[k]]
        if k == 'shift':   # one parameter: shift=1,3
            parts.append(f'shift={",".join(str(v) for v in values)}')
        else:
            parts.extend(f'{k}={quote(_value_text(v), safe="-_.~:")}' for v in values)
    if page > 1:
        parts.append(f'page={page}')
    return f'/data/{table}{suffix}' + ('?' + '&'.join(parts) if parts else '')


def _where(table, filters):
    """The WHERE clause and its bound values. Column names are the allowlist's; values are parameters only."""
    clauses, params = [], []
    kinds = dict(TYPES[table])
    for name, value in filters.items():
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        if name in SINGLE and len(values) != 1:
            raise ValueError(f'{name} takes one value')
        if name == 'day':
            clauses.append('production_date = ?')
        elif name == 'from':
            clauses.append('production_date >= ?')
        elif name == 'to':
            clauses.append('production_date <= ?')
        elif name == 'weekday':
            clauses.append('dayofweek(production_date) = ?')   # DuckDB: Sunday 0, Monday 1 ... Friday 5
            values = [WEEKDAYS.index(values[0]) + 1]
        elif name == 'crew' and 'crew' not in kinds and 'shift' in kinds:   # the crew on the row's shift, from the shift record
            marks = ', '.join('lower(?)' for _ in values)
            clauses.append(f'EXISTS (SELECT 1 FROM shifts c WHERE c.production_date = {table}.production_date '
                           f'AND c.shift = {table}.shift AND lower(c.crew) IN ({marks}))')
        elif name == 'quality_id' and 'quality_id' not in kinds and 'setup_id' in kinds:   # the board grade of the row's setup
            marks = ', '.join('lower(?)' for _ in values)
            clauses.append(f'EXISTS (SELECT 1 FROM setups g WHERE g.setup_id = {table}.setup_id AND lower(g.quality_id) IN ({marks}))')
        elif name in LANE_KEYS and name not in kinds and {'setup_id', 'lane'} <= set(kinds):   # the order or customer of the row's lane
            marks = ', '.join('lower(?)' for _ in values)
            clauses.append(f'EXISTS (SELECT 1 FROM setup_orders o WHERE o.setup_id = {table}.setup_id AND o.lane = {table}.lane '
                           f'AND lower(o.{name}) IN ({marks}))')
        elif kinds.get(name) == 'VARCHAR':
            clauses.append(f'lower("{name}") = lower(?)' if len(values) == 1 else f'lower("{name}") IN ({", ".join("lower(?)" for _ in values)})')
        elif name in kinds:
            clauses.append(f'"{name}" = ?' if len(values) == 1 else f'"{name}" IN ({", ".join("?" for _ in values)})')
        else:   # parse_filters never yields one; a guard all the same
            raise ValueError(f'not a column of {table}: {name}')
        params.extend(values)
    return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', params


def plain(value):
    """A DuckDB value as JSON: dates ISO, timestamps 'YYYY-MM-DD HH:MM:SS', lists and maps as JSON, no NaN."""
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {'key', 'value'} and isinstance(value['key'], list):   # an older DuckDB's MAP
            return {str(k): plain(v) for k, v in zip(value['key'], value['value'])}
        return {str(k): plain(v) for k, v in value.items()}
    return value


# Newest first is the reverse of the order the store wrote the rows in, which is production order (a test holds it), so
# a page is two reads that never sort a wide row: the rowids of its rows (one integer column, top-N over the filter),
# then the rows of that rowid range that pass the filter, which are exactly the page. A dimension reads in file order.
def _direction(table):
    return 'DESC' if TABLES[table].dated else 'ASC'


def _span_sql(table, filters, limit, offset=0):
    where, params = _where(table, filters)
    sql = (f'SELECT min(r) AS lo, max(r) AS hi, count(*) AS n FROM (SELECT rowid AS r FROM {table}{where} '
           f'ORDER BY rowid {_direction(table)} LIMIT ? OFFSET ?)')
    return sql, params + [limit, offset]


def _rows_sql(table, filters, lo, hi):
    names = ', '.join(f'"{name}"' for name, _ in TYPES[table])
    where, params = _where(table, filters)
    where = f'{where} AND rowid BETWEEN ? AND ?' if where else ' WHERE rowid BETWEEN ? AND ?'
    return f'SELECT {names} FROM {table}{where} ORDER BY rowid {_direction(table)}', params + [lo, hi]


def _read(store, table, filters, limit, offset=0):
    """(rows, the queries that ran with their values): `limit` rows of the filter from `offset`, in page order."""
    span_sql, span_params = _span_sql(table, filters, limit, offset)
    span = store._fetch(span_sql, span_params, limit=1)[0]
    ran = [(span_sql, span_params)]
    if not span['n']:
        return [], ran
    rows_sql, rows_params = _rows_sql(table, filters, span['lo'], span['hi'])
    rows = store._fetch(rows_sql, rows_params, limit=limit)
    return rows, ran + [(rows_sql, rows_params)]


def _select(table, filters, limit, offset=0):
    """One plain read of a filter in page order (what a receipt link holds, for the tests)."""
    names = ', '.join(f'"{name}"' for name, _ in TYPES[table])
    where, params = _where(table, filters)
    sql = f'SELECT {names} FROM {table}{where} ORDER BY rowid {_direction(table)} LIMIT ? OFFSET ?'
    return sql, params + [limit, offset]


# ---------------------------------------------------------------- the pages

_INDEX = {}


def index(store):
    """GET /data.json: every table with its meaning, its rows in the window and its first and last day. The data never
    changes while a server runs, so the counts are read once per store."""
    key = (id(store), store.content_hash)
    if key not in _INDEX:
        started = time.perf_counter()
        tables = []
        for t in TABLES.values():
            span = 'min(production_date) AS first, max(production_date) AS last' if t.dated else 'NULL AS first, NULL AS last'
            row = store._fetch(f'SELECT count(*) AS n, {span} FROM {t.name}', [], limit=1)[0]
            n, first, last = row['n'], row['first'], row['last']
            tables.append({'name': t.name, 'label': t.label, 'meaning': t.meaning, 'rows': n,
                           'first_day': plain(first), 'last_day': plain(last), 'columns': len(TYPES[t.name]),
                           'url': f'/data/{t.name}', 'contract': contract_link(t.name)})
        _INDEX.clear()
        _INDEX[key] = {'tables': tables, 'window': {'first_day': store.first_day, 'last_day': store.last_day,
                                                    'as_of': store.as_of.isoformat()},
                       'dataset_hash': store.content_hash[:12], 'per_page': PER_PAGE, 'csv_limit': CSV_LIMIT,
                       'built_ms': round((time.perf_counter() - started) * 1000, 1)}
    return {**_INDEX[key], 'elapsed_ms': 0.0}


def page(store, table, query):
    """GET /data/<table>.json?<filters>: the schema and one page of rows, newest first, with the SQL that ran."""
    started = time.perf_counter()
    filters, number = parse_filters(table, query)
    rows, ran = _read(store, table, filters, PER_PAGE, (number - 1) * PER_PAGE)
    where, bound = _where(table, filters)
    count_sql = f'SELECT count(*) AS n FROM {table}{where}'
    count = store._fetch(count_sql, bound, limit=1)[0]['n']
    pages = max(1, math.ceil(count / PER_PAGE))
    t = TABLES[table]
    return {'table': table, 'label': t.label, 'meaning': t.meaning, 'contract': contract_link(table),
            'columns': columns(table), 'filters': filters, 'filterable': filter_names(table), 'aliases': ALIASES,
            'rows': [{k: plain(v) for k, v in r.items()} for r in rows], 'count': count, 'page': number, 'pages': pages,
            'per_page': PER_PAGE, 'max_page': MAX_PAGE, 'url': table_url(table, filters, number),
            'newer': table_url(table, filters, number - 1) if number > 1 else None,
            'older': table_url(table, filters, number + 1) if number < pages and number < MAX_PAGE else None,
            'csv': table_url(table, filters, suffix='.csv'), 'csv_limit': CSV_LIMIT,
            'sql': ';\n'.join(q for q, _ in ran), 'parameters': [[plain(p) for p in values] for _, values in ran], 'count_sql': count_sql, 'count_parameters': [plain(p) for p in bound],
            'window': {'first_day': store.first_day, 'last_day': store.last_day},
            'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}


def csv_rows(store, table, query):
    """GET /data/<table>.csv?<filters>: the filtered rows, newest first, at most CSV_LIMIT. Returns (bytes, truncated).
    One read of the filter's rowids (a single integer column), then the rows CSV_CHUNK at a time by rowid range: each read
    its own short hold of the store's lock, so a download never keeps the tools waiting longer than a page does."""
    filters, _ = parse_filters(table, query)
    where, params = _where(table, filters)
    ids = [r['r'] for r in store._fetch(f'SELECT rowid AS r FROM {table}{where} ORDER BY rowid {_direction(table)} LIMIT ?',
                                        params + [CSV_LIMIT + 1], limit=CSV_LIMIT + 1)]
    truncated = len(ids) > CSV_LIMIT
    rows = []
    for at in range(0, min(len(ids), CSV_LIMIT), CSV_CHUNK):
        chunk = ids[at:min(at + CSV_CHUNK, CSV_LIMIT)]
        sql, bound = _rows_sql(table, filters, min(chunk), max(chunk))
        rows.extend(store._fetch(sql, bound, limit=len(chunk)))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    names = [name for name, _ in TYPES[table]]
    writer.writerow(names)
    for row in rows[:CSV_LIMIT]:
        writer.writerow([_csv_cell(row[name]) for name in names])
    return out.getvalue().encode('utf-8'), truncated


def _csv_cell(value):
    value = plain(value)
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, dict)):
        value = json.dumps(value, separators=(',', ':'))
    text = str(value)
    # A spreadsheet runs a cell that starts with = + - @ as a formula: the synthetic text never should, so quote it out.
    return "'" + text if text[:1] in ('=', '+', '-', '@') and not re.fullmatch(r'-?\d+(\.\d+)?(e-?\d+)?', text) else text


def resolve(store, url, limit=CSV_LIMIT):
    """Every row a /data address selects (not one page): what a receipt link holds, for the tests and the CSV."""
    path, _, query = url.partition('?')
    table = path.removeprefix('/data/')
    filters, _ = parse_filters(table, query)
    sql, params = _select(table, filters, limit + 1)
    rows = store._fetch(sql, params, limit=limit + 1)
    if len(rows) > limit:
        raise ValueError(f'{url} selects more than {limit} rows')
    return [{k: plain(v) for k, v in r.items()} for r in rows]


# ---------------------------------------------------------------- receipts: the table and filter each tool read

def _span(start, end):
    return {'day': start} if start == end else {'from': start, 'to': end}


def _scope(entry, production_day):
    """The rows a result read, before its own grouping: its window (or the production day) and its shift."""
    data, args = entry.get('data') or {}, entry.get('arguments') or {}
    window = data.get('window') if isinstance(data, dict) else None
    out = _span(window['start'], window['end']) if isinstance(window, dict) and window.get('start') else {'day': data.get('production_day') or production_day}
    shift = data.get('shift', args.get('shift'))
    shifts = data.get('shifts')
    if shift is None and isinstance(shifts, list) and shifts:   # the day's money: the complete shifts it summed (#151 review)
        excluded = {e.get('shift') if isinstance(e, dict) else e for e in data.get('excluded_shifts') or []}
        summed = sorted({x.get('shift') if isinstance(x, dict) else x for x in shifts} - excluded)
        if summed and len(summed) < 3 and all(x in (1, 2, 3) for x in summed):
            shift = summed[0] if len(summed) == 1 else summed
    if type(shift) is int and shift in (1, 2, 3) or isinstance(shift, list):
        out['shift'] = shift
    if entry.get('tool') == 'get_speed_variance' and data.get('against') == 'shift' and data.get('against_shift') in (1, 2, 3) \
            and type(shift) is int:   # a shift held against another reads both: the gap is between them (#151 review)
        out['shift'] = sorted({shift, data['against_shift']})
    previous = data.get('previous_window') if isinstance(data, dict) else None
    if entry.get('tool') == 'get_speed_variance' and data.get('against') == 'previous' and isinstance(window, dict) \
            and isinstance(previous, dict) and previous.get('start') and previous['start'] < window['start']:
        # a window held against the one before it reads both, and they are next to each other (record_store.previous_window):
        # one span holds the gap and every bucket and detail of it, each the difference of the two windows (#151 re-review)
        out.pop('day', None)
        out.update({'from': previous['start'], 'to': window['end']})
    kind = args.get('kind') if entry.get('tool') == 'get_downtime_breakdown' else None
    if isinstance(kind, str) and kind:   # the breakdown read one stop kind (PM answers as Scheduled, the contract's rule)
        out['kind'] = STOP_KINDS.get(kind.lower(), kind)
    return out


# How a stop reason reads on a card beside the money (cost_model's lever and gap names) against how the store records it.
LABEL_SUFFIX = re.compile(r' \((?:setup time|scheduled time|external)\)$')


def _stop_value(field, value):
    """A shown stop kind or reason as the store records it: the planned stop reads "Planned maintenance (scheduled)"
    wherever it is shown (records.stop_label), the store keeps kind Scheduled and reason Planned maintenance."""
    value = LABEL_SUFFIX.sub('', str(value))
    if value == SCHEDULED_STOP_LABEL:
        return 'Scheduled' if field == 'kind' else PLANNED_STOP_REASON
    return value


def _stop_group(entry):
    group = (entry.get('data') or {}).get('group_by') or (entry.get('arguments') or {}).get('group_by') or 'reason'
    return group if group in ('reason', 'place', 'kind') else 'reason'


def _line(label, kind=None):
    """The table and filter behind a priced or waste line, by the line's own words: a stop reason, a reject family,
    the waste boards, side trim, shear, speed, the bale."""
    label = str(label or '')
    if label.startswith('Downtime: '):
        return 'stops', {'reason': _stop_value('reason', label.removeprefix('Downtime: '))}
    family = re.match(r'Dry-end: (\w+)', label)
    if family:
        return 'rejects', {'family': family.group(1).upper()}
    if label.startswith('Changeover'):
        return 'rejects', {'family': 'CHANGEOVER'}
    if label.startswith('Planning / admin'):
        return 'rejects', {'family': 'ADMIN'}
    if 'waste boards' in label:
        return 'setup_orders', {}
    if label.startswith('Unexplained') or label.startswith('Bale'):
        return 'shifts', {}
    return 'setups', {}


WASTE_LINES = {'trim': ('setups', {}), 'shear': ('setups', {}), 'changeover': ('rejects', {'family': 'CHANGEOVER'}),
               'cutoff_waste': ('setup_orders', {}), 'stacker_waste': ('setup_orders', {}),
               'bond': ('rejects', {'family': 'BOND'}), 'warp': ('rejects', {'family': 'WARP'}),
               'misalignment': ('rejects', {'family': 'MISALIGNMENT'}), 'moisture': ('rejects', {'family': 'MOISTURE'}),
               'other': ('rejects', {'family': 'OTHER'}), 'dry_end': ('rejects', {'counted_in': 'dry_end'}),
               'admin': ('rejects', {'family': 'ADMIN'}), 'bale': ('shifts', {}), 'unexplained': ('shifts', {})}
# The stop buckets of a speed variance (record_store.SPEEDVAR_BUCKETS): setups are the order-change stops, planned and
# external stops their kinds; the stops bucket is every other stop reason, the reasons its details list.
SPEEDVAR_STOPS = {'setups': {'reason': 'Order change'}, 'planned': {'kind': 'Scheduled'}, 'external': {'kind': 'External'}}


def _knife(row):
    match = re.fullmatch(r'knife (\d)', str(row.get('knife', '')))
    return {'cutoff_knife': int(match.group(1))} if match else {}


def _place_reason(item):
    place, sep, reason = str(item).partition(' / ')
    return {'place': place, 'reason': _stop_value('reason', reason)} if sep else {'reason': _stop_value('reason', item)}


# A bucket or detail of a speed variance is the difference of its two sides, and the result's scope already holds both:
# the shift it is held against (_scope widens shift to the pair) or the window before (_scope widens the span to both).
def _speedvar_row(row, entry):
    if row.get('bucket') == 'stops':   # the unplanned stops: the reasons the bucket's details list, any of them
        reasons = sorted({_stop_value('reason', d.get('item')) for d in (entry.get('data') or {}).get('details') or []
                          if d.get('bucket') == 'stops' and d.get('item')})
        return 'stops', {'reason': reasons or None}
    if row.get('bucket') in SPEEDVAR_STOPS:
        return 'stops', dict(SPEEDVAR_STOPS[row['bucket']])
    return 'setups', {}


def _speedvar_detail(row, entry):
    if row.get('bucket') == 'setups':   # the order-change stops on the setups of one grade
        return 'stops', {'reason': 'Order change', 'quality_id': row.get('item')}
    if row.get('bucket') == 'stops' or row.get('bucket') in SPEEDVAR_STOPS:
        return 'stops', {**SPEEDVAR_STOPS.get(row['bucket'], {}), 'reason': _stop_value('reason', row.get('item'))}
    return 'setups', {'quality_id': row.get('item')}


def _ranking(row, entry):
    metric = (entry.get('data') or {}).get('metric') or (entry.get('arguments') or {}).get('metric')
    if metric == 'downtime':
        return 'stops', _place_reason(row.get('item'))
    if metric == 'quality':
        return 'rejects', {'family': row.get('item'), 'counted_in': 'dry_end'}
    return 'setups', {'wet_end_id': row.get('item')}


def _speed_cell(row, entry):
    out = {'quality_id': row.get('grade')}
    width = re.fullmatch(r'(\d+) in', str(row.get('slice', '')))
    if row.get('slice_by') == 'width' and width:
        out['web_width_in'] = float(width.group(1))
    elif row.get('slice_by') == 'crew' and row.get('slice'):
        out['crew'] = row['slice']   # the crew on each setup's shift
    return 'setups', out


def _review_row(row, entry):
    return 'stops', {k: _stop_value(k, row[k]) for k in ('kind', 'reason') if row.get(k)}


def _flag(row, entry):
    return 'stops', {'day': row.get('production_day'), 'from': None, 'to': None, 'shift': row.get('shift'),
                     'kind': _stop_value('kind', row.get('kind'))}


# tool -> (the table the result as a whole read, {part: row -> (table, filter over the scope)}). A row's filter is merged
# over the result's scope; a None value drops a scope key (a day row of a month drops the month's span).
SOURCES = {
    # daily tools: the production day and the shift
    'get_shift_kpis': ('shift_kpis', {'row': lambda r, e: ('shift_kpis', {})}),
    'get_shift_overview': ('shift_kpis', {'row': lambda r, e: ('shift_kpis', {})}),
    'get_downtime_breakdown': ('stops', {'row': lambda r, e: ('stops', {_stop_group(e): _stop_value(_stop_group(e), r.get('category'))}),
                                         'flag': _flag}),
    'get_wet_end_performance': ('setups', {'row': lambda r, e: ('setups', {'wet_end_id': r.get('wet_end_id')})}),
    'get_quality_breakdown': ('rejects', {'row': lambda r, e: ('rejects', {'family': r.get('reason'), 'counted_in': 'dry_end'}),
                                          'detail': lambda r, e: ('rejects', {'family': r.get('reason'), 'reason': r.get('detail'),
                                                                              'counted_in': 'dry_end'})}),
    'get_setup_matrix': ('setups', {'row': lambda r, e: ('setups', {'setup_id': r.get('setup_id')})}),
    'get_order_matrix': ('setup_orders', {'row': lambda r, e: ('setup_orders', {'setup_id': r.get('record_id'), 'order_number': r.get('order_id'), **_knife(r)})}),
    'get_shift_notes': ('stops', {}),
    'get_rankings': ('setups', {'row': _ranking}),
    'get_shift_dollars': ('shift_kpis', {'row': lambda r, e: _line(r.get('gap'), r.get('kind'))}),
    'get_day_dollars': ('shift_kpis', {'row': lambda r, e: _line(r.get('gap'), r.get('kind'))}),
    # period tools: the window and the shift
    'get_period_kpis': ('shift_kpis', {'row': lambda r, e: ('shift_kpis', {})}),
    'compare_periods': ('shift_kpis', {'row': lambda r, e: ('shift_kpis', {})}),
    'get_downtime_pareto': ('stops', {'row': lambda r, e: ('stops', {_stop_group(e): _stop_value(_stop_group(e), r.get('category'))})}),
    'get_waste_breakdown': ('shift_kpis', {'row': lambda r, e: WASTE_LINES.get(r.get('category'), ('shift_kpis', {})),
                                           'detail': lambda r, e: ('rejects', {'family': r.get('reason'), 'reason': r.get('detail'),
                                                                               'counted_in': 'dry_end'})}),
    'get_stops_profile': ('stops', {'row': lambda r, e: ('stops', {'day': r.get('production_day'), 'from': None, 'to': None}),
                                    'weekday': lambda r, e: ('stops', {'weekday': r.get('weekday')})}),
    'get_grade_totals': ('setups', {'row': lambda r, e: ('setups', {'quality_id': r.get('grade')})}),
    'get_rollstock': ('setups', {'row': lambda r, e: ('setups', {'quality_id': r.get('grade'), 'web_width_in': r.get('width_in')})}),
    'get_overrun': ('setup_orders', {'row': lambda r, e: ('setup_orders', _knife(r))}),
    'get_speed_opportunity': ('setups', {'row': _speed_cell}),
    'get_opportunities': ('shift_kpis', {'row': lambda r, e: _line(r.get('lever'), r.get('kind'))}),
    'get_downtime_review': ('shift_kpis', {'row': _review_row, 'flag': _flag}),
    'get_speed_variance': ('shift_kpis', {'row': _speedvar_row, 'detail': _speedvar_detail}),
    'get_trend': ('shift_kpis', {'row': lambda r, e: ('shift_kpis', {'from': r.get('week_start'), 'to': r.get('week_end'), 'day': None})}),
}
PARTS = {'row': 'rows', 'detail': 'details', 'flag': 'flags', 'weekday': 'weekday_profile'}
SEMANTIC_TOOL = 'semantic_query'   # semantic.TOOL: its table is its measure's entity's (_semantic_source)
# All-shift rollstock reads the paper runs themselves, one row per run (a shift's reads the setups it ran on each run).
ALL_SHIFT_TABLE = {'get_rollstock': ('wet_ends', {'row': lambda r, e: ('wet_ends', {'quality_id': r.get('grade'), 'web_width_in': r.get('width_in')})})}


# ---- the semantic model's receipts (issue #117): the records each part of its measure reads, as the model's parts read
# them (semantic._Part): the entity's table over the window and shift, the entity's own rows (a reject line counted at the
# dry end), the measure's own rows (operator and maintenance stops), and every filter and row dimension that reaches the
# entity, by its own column or by one many-to-one join the browser's grammar has (a setup's grade, a shift's crew, a
# lane's order and customer). A ratio's denominator is declared beside it (`denominator`): its own entity's records, cut
# only by the dimensions that reach them, as the model reads it (a grade cuts the downtime, never the scheduled minutes).
# A part with no filter in the grammar (a flute, a record over a band) has no link rather than a link to other rows.
class _Unlinkable(ValueError):
    pass


def _semantic_bucket(dim, value):
    """A time bucket's own days: a week, a month or a 4-4-5 period from its first day."""
    from .record_store import fiscal_bounds
    start = date.fromisoformat(str(value)[:10])
    grain = dim['grain']
    if grain == 'week':
        return start, start + timedelta(days=4)
    if grain == 'month':
        return start, (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return fiscal_bounds(start)[:2]


def _semantic_filters(mdl, entity, dims, measure):
    """{filter: value} of the browser's grammar for the records of `entity` that `measure` reads, cut by `dims`
    ({dimension: value}, the query's filters and a row's own); a time bucket as {'within': (first, last)}."""
    spec, table = mdl.entities[entity], mdl.entities[entity]['table']
    allowed, out = filter_names(table), {}
    if spec.get('where'):   # the entity's own rows: one column against one literal is all the model's wheres say today
        match = re.fullmatch(r"(\w+) = '([^']*)'", spec['where'].strip())
        if not match:
            raise _Unlinkable(f'{entity} keeps its rows by {spec["where"]}')
        out[match.group(1)] = match.group(2)
    if measure.get('above_band'):
        raise _Unlinkable('the records over a band')
    for logical, value in (measure.get('filter') or {}).items():
        out[spec['columns'][logical]] = value
    for name, value in dims.items():
        dim, owner = mdl.dimensions[name], mdl.reach(entity, name)
        if owner is None:
            continue   # it never reaches these records: the part stays the whole (a denominator's minutes for a grade)
        if dim.get('grain') == 'day':
            out['day'] = value
            continue
        if dim.get('grain'):
            out['within'] = _semantic_bucket(dim, value)
            continue
        logical = re.fullmatch(r'\{(\w+)\}', dim['expr'])
        if logical and owner != entity:   # a column the join itself matches on is the entity's own (a reject line's lane)
            keys = {right: left for left, right in mdl.joins[(entity, owner)]['on']}
            if logical.group(1) in keys:
                owner, logical = entity, re.fullmatch(r'(\w+)', keys[logical.group(1)])
        column = logical and mdl.entities[owner]['columns'].get(logical.group(1))
        if not column or not re.fullmatch(r'\w+', column):
            raise _Unlinkable(f'{name} is an expression')
        joined = {('setup', 'quality_id'): 'quality_id', ('shift', 'crew'): 'crew', ('lane', 'order_number'): 'order_number',
                  ('lane', 'customer_id'): 'customer_id'}.get((owner, column))
        out[column if owner == entity else joined] = value
    for key in out:
        if key not in allowed and key != 'within':
            raise _Unlinkable(f'{table} has no filter {key}')
    return out


def _semantic_url(mdl, entity, dims, measure, scope):
    filters = {**scope, **_semantic_filters(mdl, entity, dims, measure)}
    within = filters.pop('within', None)
    if within:   # a bucket inside the window: the days both hold
        first = max(within[0].isoformat(), filters.pop('from', None) or filters.get('day') or '0000-00-00')
        last = min(within[1].isoformat(), filters.pop('to', None) or filters.get('day') or '9999-99-99')
        filters.pop('day', None)
        filters.update(_span(first, last))
    elif 'day' in filters:
        filters.pop('from', None), filters.pop('to', None)
    table = mdl.entities[entity]['table']
    return table, filters, table_url(table, filters)


def _semantic_source(entry, production_day):
    """The source of a semantic answer: its measure's records (a ratio's numerator, a priced measure's quantity) for the
    total and for each row, and a ratio's denominator for each."""
    from . import semantic
    data, mdl = entry['data'], semantic.model()
    m = mdl.measures.get(data.get('measure'))
    if not m:
        return None
    reads = m['numerator'] if m['agg'] == 'ratio' else m['quantity'] if m['agg'] == 'priced' else data['measure']
    scope = _scope(entry, production_day)
    filters = {f['dimension']: f['value'] for f in data.get('filters') or []}
    group_by = data.get('group_by') or []

    def link(name, dims):
        measure = mdl.measures[name]
        try:
            return _semantic_url(mdl, measure['entity'], {**filters, **dims}, measure, scope)
        except (_Unlinkable, ValueError, TypeError, KeyError):
            return None, None, None
    table, whole, url = link(reads, {})
    if not url:
        return None
    # rows_exact: a row whose records the grammar cannot filter has no receipt, never the whole result's rows
    out = {'table': table, 'filter': whole, 'url': url, 'rows_exact': True,
           'rows': {'row': [link(reads, {d: r.get(d) for d in group_by})[2] for r in data.get('rows') or []]}}
    if m['agg'] == 'ratio':
        d_table, d_whole, d_url = link(m['denominator'], {})
        if d_url:
            out['denominator'] = {'table': d_table, 'url': d_url,
                                  'rows': [link(m['denominator'], {d: r.get(d) for d in group_by})[2] for r in data.get('rows') or []]}
            out['also'] = [{'label': 'the denominator', 'table': d_table, 'filter': d_whole, 'url': d_url}]
    previous = data.get('previous_window')
    if isinstance(previous, dict) and previous.get('start') and previous.get('coverage') != 'unavailable':
        before = {**{k: v for k, v in whole.items() if k not in ('day', 'from', 'to')}, **_span(previous['start'], previous['end'])}
        out.setdefault('also', []).append({'label': 'previous window', 'table': table, 'filter': before, 'url': table_url(table, before)})
    return out


def _merge(table, scope, extra, corrupt):
    filters = dict(scope)
    for k, v in extra.items():
        if v is None:
            filters.pop(k, None)
        else:
            filters[k] = v
    if table == 'shift_kpis':
        filters['corrupt'] = bool(corrupt) and 'day' in filters
    allowed = filter_names(table)
    return {k: v for k, v in filters.items() if k in allowed}


def source(entry, production_day, corrupt=False):
    """The table and filter a trace entry read, as a link and one link per cited row; None for a step with no rows of
    its own (render_chart) or a failed one."""
    if entry.get('ok') and entry.get('tool') == SEMANTIC_TOOL and isinstance(entry.get('data'), dict):
        return _semantic_source(entry, production_day)
    if not entry.get('ok') or entry.get('tool') not in SOURCES or not isinstance(entry.get('data'), dict):
        return None
    scope = _scope(entry, production_day)
    # The data-quality demo's variant is a daily tool's: a period tool reads the records as generated (corrupt=false).
    corrupt = bool(corrupt) and not isinstance(entry['data'].get('window'), dict)
    table, parts = SOURCES[entry['tool']]
    if entry['tool'] in ALL_SHIFT_TABLE and 'shift' not in scope:
        table, parts = ALL_SHIFT_TABLE[entry['tool']]
    whole = _merge(table, scope, {}, corrupt)
    out = {'table': table, 'filter': whole, 'url': table_url(table, whole)}
    links = {}
    for part, fn in parts.items():
        rows = entry['data'].get(PARTS[part])
        if not isinstance(rows, list) or not rows:
            continue
        urls = []
        for row in rows:
            try:
                row_table, extra = fn(row, entry)
                urls.append(table_url(row_table, _merge(row_table, scope, extra, corrupt)))
            except (ValueError, TypeError, AttributeError):
                urls.append(None)   # a row without a key the grammar takes is linked to the result's own rows
        links[part] = urls
    if links:
        out['rows'] = links
    also = []
    # A comparison also read the window it compares with: the same rows over the previous window (Codex on #151).
    previous = entry['data'].get('previous_window')
    if isinstance(previous, dict) and previous.get('start') and previous.get('coverage') != 'unavailable':
        before = _merge(table, {**{k: v for k, v in scope.items() if k not in ('day', 'from', 'to')},
                                **_span(previous['start'], previous['end'])}, {}, corrupt)
        also.append({'label': 'previous window', 'table': table, 'filter': before, 'url': table_url(table, before)})
    # A speed against each grade's baseline also read the trailing year the baselines come from (its sustained speed,
    # targets.json) and, against the baseline, the usual grade mix of that year (#151 review).
    since, through = entry['data'].get('baseline_since'), entry['data'].get('baseline_through')
    if since and through and entry['tool'] in ('get_speed_variance', 'get_opportunities', 'get_wet_end_performance'):
        year = {'from': since, 'to': through}
        also.append({'label': 'the trailing year', 'table': 'setups', 'filter': year, 'url': table_url('setups', year)})
    # The notes are the shift's own note (its crew, from the shift record) and then its stops' comments (Codex on #151).
    if entry['tool'] == 'get_shift_notes':
        shift = _merge('shifts', scope, {}, corrupt)
        also.append({'label': 'the shift record', 'table': 'shifts', 'filter': shift, 'url': table_url('shifts', shift)})
    if also:
        out['also'] = also
    return out


def attach_sources(result, corrupt=False):
    """Every trace entry of an answer declares the table and filter it read (`source`), and every section that cites a
    step gets the address of the rows behind its own figure (`receipts`, one per section, None for a section citing none)."""
    day = result.get('production_day')
    for entry in result.get('trace') or []:
        if isinstance(entry, dict):
            declared = source(entry, day, corrupt)
            if declared:
                entry['source'] = declared
    if isinstance(result.get('sections'), list):
        result['receipts'] = receipts(result, corrupt)
    return result


# ---------------------------------------------------------------- receipts: the rows behind each section's own figure

# `(query 2 · row 0)` at the end of a section (shapes.REF): the step and the row of its result a figure came from.
REF = re.compile(r' \(query (\d+)(?: · (row|detail|weekday|flag) (\d+))?\)$')
# A finding about the window a comparison held this one against: "The previous window **3.41%**", "Previous window: **…".
PREVIOUS = re.compile(r'(?i)\bprevious window\b[^*]*\*\*')
# A flag's recurrence: "over the band on its own on **1** of 783 shifts since 2025-09-12": a count over the trailing year.
RECURRENCE = re.compile(r'over the band on its own on \*\*')


def _recurrence_filter(flag):
    """The stops behind a flag's recurrence count: its reason in its kind over the trailing year the count covers, on the
    flagged shift's own history or the plant's (`recurrence_scope`)."""
    out = {'from': flag['recurrence_since'], 'to': flag['recurrence_through'], 'kind': _stop_value('kind', flag['kind']),
           'reason': _stop_value('reason', flag['reason'])}
    scope = re.fullmatch(r'shift (\d)', str(flag.get('recurrence_scope', '')))
    if scope:
        out['shift'] = int(scope.group(1))
    return out


def _overview_line(row, section):
    """A shift summary finding's own rows: the largest downtime reason's stops, the largest dry-end reject family's lines,
    the setups whose runs are counted against their grade target; every other figure is the shift's KPI row."""
    if 'downtime reason' in section or 'the only downtime' in section:
        return 'stops', {'reason': _stop_value('reason', row.get('largest_downtime_reason'))}
    if 'dry-end reject reason' in section:
        return 'rejects', {'family': row.get('largest_reject_reason'), 'counted_in': 'dry_end'}
    if 'Wet-end runs below' in section:
        return 'setups', {}
    return 'shift_kpis', {}


def _quantity_rows(entry, part, section):
    """The rows behind a count or unit figure (a quantity answer, #113) that its step's own rows do not hold: a stop kind's
    minutes or stops (the downtime review's rows are the shifts' sums, which carry no stop count by kind), and the window's
    order changes (the KPI tiles carry no order change; they are the setups an order change started, change code O)."""
    bold = BOLD.search(section)
    if part is not None or not bold:
        return None
    figure, tool = bold.group(1), entry.get('tool')
    if tool == 'get_downtime_review' and re.search(r'\b(minutes|stops)$', figure):
        kinds = {k for k in ('maintenance', 'operator') if re.search(rf'\b{k}\b', section, re.I)}
        if len(kinds) == 1:   # one kind named (its own lead, or the other kind beside it), never "maintenance and operator"
            return 'stops', {'kind': kinds.pop().capitalize()}
    if tool in ('get_period_kpis', 'compare_periods') and figure.endswith(' order changes'):
        return 'setups', {'change_code': 'O'}
    return None


BOLD = re.compile(r'\*\*([^*]+)\*\*')


def receipt(entry, part, index, section, production_day, corrupt=False):
    """The rows behind one section's figure: {'url', 'table', 'of'}. Usually the cited row's own rows (`source.rows`) or
    the step's (`source.url`); a figure about the previous window opens that window's rows, a recurrence count the reason's
    stops over its trailing year, and a shift summary finding the table its figure is read from (the #151 review)."""
    src = entry.get('source') if isinstance(entry, dict) else None
    if not src:
        return None
    data = entry.get('data') or {}
    if PREVIOUS.search(section):
        previous = next((a for a in src.get('also', []) if a.get('label') == 'previous window'), None)
        if previous:
            return {'url': previous['url'], 'table': previous['table'], 'of': 'previous window'}
    if part == 'flag' and RECURRENCE.search(section):
        flags = data.get('flags') or []
        if index is not None and 0 <= index < len(flags) and flags[index].get('recurrence_since'):
            return {'url': table_url('stops', _recurrence_filter(flags[index])), 'table': 'stops', 'of': 'recurrence'}
    if entry.get('tool') == 'get_shift_overview' and part == 'row' and data.get('rows'):
        table, extra = _overview_line(data['rows'][0], section)
        filters = _merge(table, _scope(entry, production_day), extra, corrupt)
        return {'url': table_url(table, filters), 'table': table, 'of': 'row'}
    quantity = _quantity_rows(entry, part, section)
    if quantity:   # a quantity answer's figure (#113) that its step's own rows do not hold
        table, extra = quantity
        filters = _merge(table, _scope(entry, production_day), extra, corrupt)
        return {'url': table_url(table, filters), 'table': table, 'of': 'quantity'}
    url = receipt_url(entry, part, index)
    if not url:
        return None
    return {'url': url, 'table': url.split('?')[0].removeprefix('/data/'), 'of': 'row' if part else 'result'}


def _is_label(section):
    """A label line of the answer shape (shapes.py): short, no figure, no digits, no final punctuation; it introduces
    the bullets under it ("The previous window", "Flags and who to ask")."""
    return (isinstance(section, str) and not section.startswith('- ') and '**' not in section and len(section) <= 48
            and not re.search(r'\d|[.:;!?]$', section))


def receipts(result, corrupt=False):
    """One receipt per section of an answer, in section order: None for a section that cites no step. A bullet is read
    with the label above it, so a finding under "The previous window" opens that window's rows; but only a bullet of the
    label's own group: one citing the whole result of the step the lines above the label cite. Another step's bullets can
    follow a group with nothing between (shapes.PeriodAnswer appends them), and they are not about its window (#151)."""
    trace, day, out = result.get('trace') or [], result.get('production_day'), []
    label, label_step, last_step = '', 0, 0
    for section in result.get('sections') or []:
        if _is_label(section):
            label, label_step = section, last_step
        elif not (isinstance(section, str) and section.startswith('- ')):
            label = ''
        m = REF.search(section) if isinstance(section, str) else None
        step = int(m.group(1)) if m else 0
        if not m or not 1 <= step <= len(trace):
            out.append(None)
            continue
        group = label and section.startswith('- ') and step == label_step and not m.group(2)
        context = f'{label}: {section}' if group else section
        out.append(receipt(trace[step - 1], m.group(2), int(m.group(3)) if m.group(3) else None, context, day, corrupt))
        last_step = step
    return out


def receipt_url(entry, part=None, row=None):
    """The address a receipt "(query N · part M)" links to: the cited row's own rows, else the result's."""
    src = entry.get('source') or {}
    if part and row is not None:
        urls = (src.get('rows') or {}).get(part) or []
        if 0 <= row < len(urls) and urls[row]:
            return urls[row]
        if src.get('rows_exact'):
            return None
    return src.get('url')
