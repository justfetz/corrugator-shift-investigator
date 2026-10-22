"""The app's data source: two synthetic months in one in-memory DuckDB.

`RecordStore` generates the record families once (`synth.generate_months`,
about two seconds), loads them into fixed tables (setups, customers, rejects,
wet_ends, shifts, stops) and exposes two things:

* `day_analysis(production_day, corrupt=False)` builds the SAME per-day tables
  the existing `Analysis` tools read (shifts, setups, stops, orders, rejects),
  so the nine daily tools, the offline planner, the PDF reports and the period
  reports keep their semantics unchanged while reading the record families.
* the period tools (`get_period_kpis`, `get_downtime_pareto`,
  `get_waste_breakdown`, `get_stops_profile`, `get_trend`, `compare_periods`)
  over the whole store, every one a fixed read query with bound values.

Every percentage is a ratio of summed numerators and denominators over the
window, never an average of per-shift percentages (docs/METRICS.md). The
seeded story answer keys (`stories.py`) are never imported here.
"""
from calendar import monthrange
from datetime import date, timedelta
import hashlib
import math
import threading
import time
from threading import Timer

import duckdb

from . import records as R
from .analysis import Analysis, load_rows
from .fixture import GRADES
from .synth import generate_months, production_days

DEFAULT_SEED = 2026
DEFAULT_MONTHS = (("2026", 8), ("2026", 9))
DEFAULT_MACHINE = "CORR-1"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PERIODS = ("day", "week", "month")
CORRUPT_TRIM_SQFT = 99999999.0          # the fixture's bad-trim record, injected on demand
MAX_TREND_WEEKS = 26
ROW_LIMIT = 1000

TABLES = {
    "setups": ("setup_id VARCHAR, production_date DATE, shift INTEGER, wet_end_id VARCHAR, grade VARCHAR, "
               "target DOUBLE, started TIMESTAMP, ended TIMESTAMP, seconds DOUBLE, stop_seconds DOUBLE, "
               "stops INTEGER, feet DOUBLE, gross DOUBLE, trim DOUBLE, shear DOUBLE, rejects DOUBLE, "
               "width_in DOUBLE, change_code VARCHAR, split_from VARCHAR"),
    "customers": ("setup_id VARCHAR, production_date DATE, shift INTEGER, order_number VARCHAR, part_number VARCHAR, "
                  "sheet_width_in DOUBLE, sheet_length_in DOUBLE, outs INTEGER, position INTEGER, nominal_cuts INTEGER, "
                  "actual_cuts INTEGER, hqm_waste_sqft DOUBLE, asm_waste_sqft DOUBLE, stack_height INTEGER"),
    "rejects": "setup_id VARCHAR, production_date DATE, shift INTEGER, reason VARCHAR, position INTEGER, sheets INTEGER, sqft DOUBLE",
    "wet_ends": ("wet_end_id VARCHAR, production_date DATE, grade VARCHAR, width_in DOUBLE, started TIMESTAMP, "
                 "ended TIMESTAMP, feet DOUBLE, gross DOUBLE, trim DOUBLE, max_fpm DOUBLE, avg_fpm DOUBLE, setup_count INTEGER"),
    "shifts": ("production_date DATE, shift INTEGER, crew VARCHAR, feet DOUBLE, gross DOUBLE, trim DOUBLE, shear DOUBLE, "
               "rejects DOUBLE, bale DOUBLE, max_fpm DOUBLE, avg_fpm DOUBLE, scheduled_minutes INTEGER, wet_end_count INTEGER"),
    "stops": ("stop_id VARCHAR, production_date DATE, shift INTEGER, setup_id VARCHAR, kind VARCHAR, place VARCHAR, "
              "reason VARCHAR, seconds DOUBLE, started TIMESTAMP, ended TIMESTAMP, note VARCHAR"),
}

def _round(value, places):
    return None if value is None else round(value, places)


def _ratio(numerator, denominator, scale=1.0, places=4):
    if denominator in (None, 0) or numerator is None:
        return None
    return round(numerator / denominator * scale, places)


def _weekdays(start, end):
    """Production (Mon-Fri) dates from start to end inclusive."""
    days, moment = [], start
    while moment <= end:
        if moment.weekday() < 5:
            days.append(moment)
        moment += timedelta(days=1)
    return days


def _month_bounds(moment):
    start = moment.replace(day=1)
    return start, start.replace(day=monthrange(start.year, start.month)[1])


def parse_window(spec, first_day=None, last_day=None):
    """Validate {"period": day|week|month, "anchor": "YYYY-MM-DD"} and return
    (period, anchor, start, end). A day window needs a weekday; when bounds
    are given the resolved window must overlap the store's calendar range."""
    if not isinstance(spec, dict) or set(spec) != {"period", "anchor"}:
        raise ValueError('A window is {"period": "day|week|month", "anchor": "YYYY-MM-DD"}')
    period, anchor = spec["period"], spec["anchor"]
    if period not in PERIODS:
        raise ValueError("Window period must be day, week or month")
    if not isinstance(anchor, str):
        raise ValueError("Window anchor must be a YYYY-MM-DD date")
    try:
        moment = date.fromisoformat(anchor)
    except ValueError:
        raise ValueError("Window anchor must be a YYYY-MM-DD date") from None
    if period == "day":
        if moment.weekday() >= 5:
            raise ValueError(f"{anchor} is a {WEEKDAY_NAMES[moment.weekday()]}: production days are Monday to Friday")
        start = end = moment
    elif period == "week":
        start = moment - timedelta(days=moment.weekday())
        end = start + timedelta(days=4)
    else:
        start, end = _month_bounds(moment)
    if first_day and last_day and (end < first_day or start > last_day):
        raise ValueError(f"The {period} window {start} to {end} is outside the dataset range {first_day} to {last_day}")
    return period, moment, start, end


def previous_window(period, start, end):
    """The window of the same kind immediately before (start, end)."""
    if period == "day":
        moment = start - timedelta(days=1)
        while moment.weekday() >= 5:
            moment -= timedelta(days=1)
        return moment, moment
    if period == "week":
        return start - timedelta(days=7), end - timedelta(days=7)
    return _month_bounds(start - timedelta(days=1))


def window_label(period, start, end):
    if period == "day":
        return f"{start.isoformat()} ({WEEKDAYS[start.weekday()]})"
    if period == "week":
        return f"Week {start.isoformat()} to {end.isoformat()} (Mon-Fri)"
    return f"{start.strftime('%B %Y')} ({start.isoformat()} to {end.isoformat()})"


class RecordStore:
    def __init__(self, seed=DEFAULT_SEED, months=DEFAULT_MONTHS, machine_id=DEFAULT_MACHINE):
        started = time.perf_counter()
        data = generate_months(seed=seed, months=months, machine_id=machine_id)
        self.content_hash = data["content_hash"]
        self.manifest = data["manifest"]
        self.seed, self.months, self.machine_id = seed, tuple(months), machine_id
        self._days = {d.isoformat(): d for d in production_days(months)}
        self.production_days = list(self._days)
        self.first_day, self.last_day = self.production_days[0], self.production_days[-1]
        self._first, self._last = self._days[self.first_day], self._days[self.last_day]
        self._by_day = {key: {"setups": [], "stops": [], "shifts": []} for key in self._days}
        self._lock = threading.RLock()
        self._db = duckdb.connect(":memory:", config={"threads": 2})
        for table, columns in TABLES.items():
            self._db.execute(f"CREATE TABLE {table} ({columns})")
        self._load(data)
        self._db.execute("SET enable_external_access=false")
        self.boot_seconds = time.perf_counter() - started

    # ------------------------------------------------------------ loading

    @staticmethod
    def _iso(text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"

    def _load(self, data):
        setups, customers, rejects, wet_ends, shifts, stops = [], [], [], [], [], []
        for s in data["dry_end_setups"]:
            day = self._iso(s.production_date)
            self._by_day[day]["setups"].append(s)
            setups.append([s.setup_id, day, s.shift_number, s.wet_end_id, s.quality_id, GRADES[s.quality_id],
                           s.start, s.end, float(s.elapsed_seconds), float(s.stop_time_sec), s.number_of_stops,
                           s.lineal_ft, s.total_produced_sqft, s.trim_sqft, s.chop_sqft, s.kqf_sqft,
                           float(s.web_width_in), s.change_code, s.split_from_setup_id])
            for c in s.customers:
                customers.append([s.setup_id, day, s.shift_number, c.order_number, c.part_number, float(c.sheet_width_in),
                                  float(c.sheet_length_in), c.number_of_outs, c.cutoff_allocation, c.nominal_cuts,
                                  c.actual_cuts, c.hqm_waste_sqft, c.asm_waste_sqft, c.stack_height_sheets])
            for r in s.rejects:
                rejects.append([s.setup_id, day, s.shift_number, r.reason, r.cutoff_allocation, r.sheets, r.sqft])
        for w in data["wet_ends"]:
            wet_ends.append([w.wet_end_id, self._iso(w.production_date), w.quality_id, float(w.web_width_in), w.start, w.end,
                             w.lineal_ft, w.total_produced_sqft, w.side_trim_sqft, w.max_speed_fpm, w.avg_speed_fpm,
                             len(w.setup_ids)])
        for sh in data["shifts"]:
            day = self._iso(sh.production_date)
            self._by_day[day]["shifts"].append(sh)
            shifts.append([day, sh.shift_number, sh.group_name, sh.total_lineal_ft, sh.total_produced_sqft, sh.side_trim_sqft,
                           sh.chop_sqft, sh.reject_sqft, sh.bale_sqft, sh.max_speed_fpm, sh.avg_speed_fpm,
                           sh.scheduled_minutes, len(sh.wet_end_ids)])
        for st in data["stops"]:
            day = self._iso(st.production_date)
            self._by_day[day]["stops"].append(st)
            stops.append([st.stop_id, day, st.shift_number, st.setup_id, st.kind, st.place, st.reason,
                          float(st.duration_sec), st.start, st.end, st.user_info])
        for table, rows in (("setups", setups), ("customers", customers), ("rejects", rejects),
                            ("wet_ends", wet_ends), ("shifts", shifts), ("stops", stops)):
            load_rows(self._db, table, rows)
        self.counts = {"setups": len(setups), "customers": len(customers), "rejects": len(rejects),
                       "wet_ends": len(wet_ends), "shifts": len(shifts), "stops": len(stops)}

    def close(self):
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------ per-day tables

    def check_day(self, production_day):
        """Return the ISO production day or raise a clear ValueError."""
        if not isinstance(production_day, str):
            raise ValueError("Select a production day as YYYY-MM-DD")
        try:
            moment = date.fromisoformat(production_day)
        except ValueError:
            raise ValueError("Select a production day as YYYY-MM-DD") from None
        if moment.weekday() >= 5:
            raise ValueError(f"{production_day} is a {WEEKDAY_NAMES[moment.weekday()]}: production days are Monday to Friday "
                             f"between {self.first_day} and {self.last_day}")
        if production_day not in self._days:
            raise ValueError(f"{production_day} is outside the dataset: production days run Monday to Friday "
                             f"from {self.first_day} to {self.last_day}")
        return production_day

    @staticmethod
    def _validate_setup(s, trim):
        quantities = {"lineal_ft": s.lineal_ft, "total_produced_sqft": s.total_produced_sqft, "trim_sqft": trim,
                      "chop_sqft": s.chop_sqft, "kqf_sqft": s.kqf_sqft}
        for name, value in quantities.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {name}")
        gross, feet, width = s.total_produced_sqft, s.lineal_ft, s.web_width_in
        if width not in R.WEB_WIDTHS or feet <= 0 or gross <= 0:
            raise ValueError("Invalid gross geometry")
        if s.quality_id not in GRADES:
            raise ValueError("Invalid demo grade target")
        if not math.isclose(gross, feet * width / 12, abs_tol=0.06) or trim + s.chop_sqft + s.kqf_sqft > gross:
            raise ValueError("Waste/gross area out of bounds")
        elapsed = s.elapsed_seconds
        if elapsed <= 0 or s.stop_time_sec < 0 or s.stop_time_sec >= elapsed:
            raise ValueError("Invalid record interval")
        if not 1 <= len(s.customers) <= 3 or [c.cutoff_allocation for c in s.customers] != list(range(1, len(s.customers) + 1)):
            raise ValueError("Expected one to three knife positions")
        if sum(c.sheet_width_in * c.number_of_outs for c in s.customers) > width:
            raise ValueError("Invalid centered web placement")
        if not math.isclose(s.kqf_sqft, sum(r.sqft for r in s.rejects), abs_tol=0.06):
            raise ValueError("Reject lines do not reconcile")

    def day_analysis(self, production_day, corrupt=False):
        """The per-day Analysis over the record families. `corrupt` injects the
        fixture's bad-trim record into the first setup of shift 2, which is then
        excluded with its raw values and makes shift 2 coverage incomplete."""
        day = self.check_day(production_day)
        if type(corrupt) is not bool:
            raise ValueError("Invalid data-quality option")
        with self._lock:
            families = self._by_day[day]
            setups = sorted(families["setups"], key=lambda s: (s.start, s.setup_id))
            target = next((s for s in setups if s.shift_number == 2), None) if corrupt else None
            errors, setup_rows, order_rows, reject_rows = [], [], [], []
            for s in setups:
                trim = CORRUPT_TRIM_SQFT if s is target else s.trim_sqft
                try:
                    self._validate_setup(s, trim)
                except ValueError as exc:
                    raw = {k: v for k, v in R.to_dict(s).items() if k not in ("customers", "rejects")}
                    raw["trim_sqft"] = trim
                    errors.append({"record_id": s.setup_id, "shift": s.shift_number, "error": str(exc), "raw": raw})
                    continue
                setup_rows.append([s.setup_id, s.shift_number, s.wet_end_id, s.quality_id, float(GRADES[s.quality_id]),
                                   float(s.elapsed_seconds), s.lineal_ft, s.total_produced_sqft, trim, s.chop_sqft,
                                   s.kqf_sqft, s.start, s.end, float(s.web_width_in)])
                for c in s.customers:
                    # The record families carry no order quantity ledger (docs/RECORDS.md):
                    # requested and remaining sheets are null, planned/produced are this record's.
                    order_rows.append([s.setup_id, s.shift_number, s.split_from_setup_id or s.setup_id, c.order_number,
                                       s.quality_id, f"knife {c.cutoff_allocation}", float(c.sheet_width_in),
                                       float(c.sheet_length_in), c.number_of_outs, c.actual_cuts, None,
                                       c.nominal_cuts * c.number_of_outs, c.actual_cuts * c.number_of_outs, None, None])
                for r in s.rejects:
                    reject_rows.append([s.setup_id, s.shift_number, r.reason, float(r.sheets), r.sqft])
            shift_rows = [[sh.shift_number, float(sh.scheduled_minutes * 60),
                           f"{sh.group_name} on duty (synthetic crew label, not a person). "
                           "The record families carry no free-text shift note."]
                          for sh in sorted(families["shifts"], key=lambda sh: sh.shift_number)]
            stop_rows = [[st.stop_id, st.shift_number, st.kind, st.place, st.reason, float(st.duration_sec),
                          st.start, st.end, st.user_info] for st in sorted(families["stops"], key=lambda st: st.start)]
        version = hashlib.sha256(f"{self.content_hash}|{day}|{'corrupt' if corrupt else 'clean'}".encode()).hexdigest()
        return Analysis.from_rows(day, version, shift_rows, setup_rows, stop_rows, order_rows, reject_rows,
                                  errors=errors, store=self)

    # ------------------------------------------------------------ fixed reads

    def _fetch(self, sql, params):
        """Run one fixed read with bound values under the same bounds as Analysis."""
        with self._lock:
            timer = Timer(2.0, self._db.interrupt)
            timer.daemon = True
            timer.start()
            try:
                result = self._db.execute(sql, params)
                columns = [c[0] for c in result.description]
                fetched = result.fetchmany(ROW_LIMIT + 1)
                if len(fetched) > ROW_LIMIT:
                    raise ValueError("Result exceeds row limit")
                return [dict(zip(columns, row)) for row in fetched]
            except duckdb.InterruptException as exc:
                raise ValueError("Query exceeded two-second limit") from exc
            finally:
                timer.cancel()
                timer.join()

_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def default_store():
    """The process-wide store over the default two months, built on first use."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = RecordStore()
        return _DEFAULT
