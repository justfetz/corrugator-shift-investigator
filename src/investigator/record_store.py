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
generator's seeded answer keys are never imported here: the effects have to
be found in the records.
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
    "rejects": "setup_id VARCHAR, production_date DATE, shift INTEGER, reason VARCHAR, detail VARCHAR, position INTEGER, sheets INTEGER, sqft DOUBLE",
    "wet_ends": ("wet_end_id VARCHAR, production_date DATE, grade VARCHAR, width_in DOUBLE, started TIMESTAMP, "
                 "ended TIMESTAMP, feet DOUBLE, gross DOUBLE, trim DOUBLE, max_fpm DOUBLE, avg_fpm DOUBLE, setup_count INTEGER"),
    "shifts": ("production_date DATE, shift INTEGER, crew VARCHAR, feet DOUBLE, gross DOUBLE, trim DOUBLE, shear DOUBLE, "
               "rejects DOUBLE, bale DOUBLE, max_fpm DOUBLE, avg_fpm DOUBLE, scheduled_minutes INTEGER, wet_end_count INTEGER"),
    "stops": ("stop_id VARCHAR, production_date DATE, shift INTEGER, setup_id VARCHAR, kind VARCHAR, place VARCHAR, "
              "reason VARCHAR, seconds DOUBLE, started TIMESTAMP, ended TIMESTAMP, note VARCHAR"),
}

# The dashboard tiles: key, label, unit, which direction is better.
# "neutral" means the count is context, not a score (docs/METRICS.md).
KPI_CATALOG = (
    ("volume_msf", "Volume", "MSF", "higher"),
    ("lineal_ft", "Lineal feet", "ft", "higher"),
    ("speed_run_fpm", "Speed without stops", "ft/min", "higher"),
    ("speed_all_fpm", "Speed with stops", "ft/min", "higher"),
    ("downtime_pct", "Downtime", "% of scheduled minutes", "lower"),
    ("downtime_minutes", "Downtime minutes", "min", "lower"),
    ("stops", "Stops", "count", "lower"),
    ("minutes_per_stop", "Minutes per stop", "min/stop", "lower"),
    ("trim_pct", "Side trim", "% of gross", "lower"),
    ("shear_pct", "Shear", "% of gross", "lower"),
    ("dry_end_pct", "Dry-end rejects", "% of gross", "lower"),
    ("bond_pct", "Dry-end: bond", "% of gross", "lower"),
    ("warp_pct", "Dry-end: warp", "% of gross", "lower"),
    ("misalignment_pct", "Dry-end: misalignment", "% of gross", "lower"),
    ("moisture_pct", "Dry-end: moisture", "% of gross", "lower"),
    ("bale_pct", "Bale (scale reading)", "% of gross", "lower"),
    ("unexplained_pct", "Unexplained (bale minus trim, shear and rejects)", "% of gross", "lower"),
    ("maintenance_pm_pct", "Maintenance + PM downtime", "% of scheduled minutes", "lower"),
    ("operator_pct", "Operator downtime", "% of scheduled minutes", "lower"),
    ("setups", "Setup records", "count", "neutral"),
    ("paper_changes", "Paper changes (distinct wet ends)", "count", "neutral"),
    ("lineal_per_setup", "Lineal per setup (dry-end record)", "ft/setup", "higher"),
    ("lineal_per_paper_change", "Lineal per paper change (wet-end record)", "ft/paper change", "higher"),
    ("bale_msf", "Baled waste (scale reading)", "MSF", "lower"),
)
KPI_UNITS = {key: unit for key, _, unit, _ in KPI_CATALOG}
TREND_METRICS = ("trim_pct", "shear_pct", "dry_end_pct", "warp_pct", "bond_pct", "misalignment_pct", "moisture_pct",
                 "speed_run_fpm", "speed_all_fpm", "downtime_pct", "stops", "minutes_per_stop", "msf_per_day",
                 "lineal_per_setup", "lineal_per_paper_change")
TREND_UNITS = {**{m: KPI_UNITS[m] for m in TREND_METRICS if m in KPI_UNITS}, "msf_per_day": "MSF per production day"}
WASTE_CATEGORIES = (("trim", "Side trim"), ("shear", "Shear chops"), ("bond", "Dry-end: bond"),
                    ("warp", "Dry-end: warp"), ("misalignment", "Dry-end: misalignment"), ("moisture", "Dry-end: moisture"),
                    ("dry_end", "Dry-end rejects (all reasons)"), ("bale", "Bale (scale reading)"),
                    ("unexplained", "Unexplained (bale minus accounted waste)"))
_STOP_GROUPS = ("reason", "place", "kind")
_STOP_MEASURES = ("minutes", "count")
# Over/under-run: actual cuts against nominal cuts per knife. The band is the demo's
# "inside the cut range" width; the plant's acceptance range is an open owner input
# (docs/METRICS.md). The record window is the +-10% the generator never leaves.
OVERRUN_BAND_PCT = 2.0
OVERRUN_WINDOW_PCT = 10.0


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
                rejects.append([s.setup_id, day, s.shift_number, r.reason, r.detail, r.cutoff_allocation, r.sheets, r.sqft])
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
                    reject_rows.append([s.setup_id, s.shift_number, r.reason, r.detail, float(r.sheets), r.sqft])
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

    def _window(self, spec):
        period, anchor, start, end = parse_window(spec, self._first, self._last)
        return self._describe(period, start, end, anchor)

    def _describe(self, period, start, end, anchor=None):
        expected = _weekdays(start, end)
        observed = [d for d in expected if d.isoformat() in self._days]
        coverage = "complete" if observed and len(observed) == len(expected) else "partial" if observed else "unavailable"
        return {"period": period, "anchor": (anchor or start).isoformat(), "start": start.isoformat(),
                "end": end.isoformat(), "label": window_label(period, start, end),
                "production_days_expected": len(expected), "production_days_observed": len(observed),
                "coverage": coverage}

    def _previous(self, window):
        period = window["period"]
        start, end = previous_window(period, date.fromisoformat(window["start"]), date.fromisoformat(window["end"]))
        return self._describe(period, start, end)

    @staticmethod
    def _shift_clause(shift):
        if shift is None:
            return "", []
        if type(shift) is not int or shift not in (1, 2, 3):
            raise ValueError("Shift must be 1, 2, 3 or null for all shifts")
        return " AND shift = ?", [shift]

    def _sums(self, window, shift, trace):
        """Raw numerators and denominators of one window; KPIs are ratios of these."""
        clause, extra = self._shift_clause(shift)
        params = [window["start"], window["end"], *extra]
        queries = {
            "setups": "SELECT count(*) AS setup_records, count(DISTINCT wet_end_id) AS paper_changes, "
                      "coalesce(sum(feet),0) AS lineal_ft, coalesce(sum(gross),0) AS gross_sqft, "
                      "coalesce(sum(trim),0) AS trim_sqft, coalesce(sum(shear),0) AS shear_sqft, "
                      "coalesce(sum(rejects),0) AS reject_sqft, coalesce(sum(seconds),0) AS elapsed_seconds, "
                      "coalesce(sum(stop_seconds),0) AS setup_stop_seconds, count(DISTINCT production_date) AS production_days "
                      f"FROM setups WHERE production_date BETWEEN ? AND ?{clause}",
            "shifts": "SELECT count(*) AS shift_records, coalesce(sum(scheduled_minutes),0)*60 AS scheduled_seconds, "
                      f"coalesce(sum(bale),0) AS bale_sqft FROM shifts WHERE production_date BETWEEN ? AND ?{clause}",
            "stops": f"SELECT kind, count(*) AS stops, sum(seconds) AS seconds FROM stops WHERE production_date BETWEEN ? AND ?{clause} GROUP BY kind",
            "rejects": f"SELECT reason, sum(sqft) AS sqft FROM rejects WHERE production_date BETWEEN ? AND ?{clause} GROUP BY reason",
            "details": f"SELECT reason, detail, sum(sqft) AS sqft, sum(sheets) AS sheets FROM rejects WHERE production_date BETWEEN ? AND ?{clause} GROUP BY reason, detail ORDER BY sum(sqft) DESC, reason, detail",
        }
        sums = {}
        for name, sql in queries.items():
            rows = self._fetch(sql, params)
            trace["sql"].append(sql)
            trace["parameters"].append(params)
            sums[name] = rows
        setup, shifts = sums["setups"][0], sums["shifts"][0]
        by_kind = {r["kind"]: r for r in sums["stops"]}
        by_reason = {r["reason"]: r["sqft"] for r in sums["rejects"]}
        total = {**setup, **shifts,
                 "stop_count": sum(r["stops"] for r in sums["stops"]),
                 "stop_seconds": sum(r["seconds"] for r in sums["stops"]),
                 "maintenance_pm_seconds": sum(r["seconds"] for k, r in by_kind.items() if k in ("Maintenance", "PM")),
                 "operator_seconds": by_kind.get("Operator", {}).get("seconds", 0) or 0,
                 "bond_sqft": by_reason.get("BOND", 0.0), "warp_sqft": by_reason.get("WARP", 0.0),
                 "misalignment_sqft": by_reason.get("MISALIGNMENT", 0.0), "moisture_sqft": by_reason.get("MOISTURE", 0.0),
                 "details": sums["details"],
                 "source_rows": {"setups": setup["setup_records"], "shifts": shifts["shift_records"],
                                 "stops": sum(r["stops"] for r in sums["stops"]), "rejects": len(sums["rejects"])}}
        return total

    @staticmethod
    def _kpis(t):
        gross, scheduled = t["gross_sqft"], t["scheduled_seconds"]
        running_minutes = (t["elapsed_seconds"] - t["setup_stop_seconds"]) / 60
        accounted = t["trim_sqft"] + t["shear_sqft"] + t["reject_sqft"]
        return {
            "volume_msf": _round(gross / 1000, 1) if t["setup_records"] else None,
            "lineal_ft": _round(t["lineal_ft"], 1) if t["setup_records"] else None,
            "speed_run_fpm": _ratio(t["lineal_ft"], running_minutes, places=1),
            "speed_all_fpm": _ratio(t["lineal_ft"], scheduled / 60, places=1),
            "downtime_pct": _ratio(t["stop_seconds"], scheduled, 100),
            "downtime_minutes": _round(t["stop_seconds"] / 60, 1) if t["shift_records"] else None,
            "stops": t["stop_count"] if t["shift_records"] else None,
            "minutes_per_stop": _ratio(t["stop_seconds"] / 60, t["stop_count"], places=2),
            "trim_pct": _ratio(t["trim_sqft"], gross, 100),
            "shear_pct": _ratio(t["shear_sqft"], gross, 100),
            "dry_end_pct": _ratio(t["reject_sqft"], gross, 100),
            "bond_pct": _ratio(t["bond_sqft"], gross, 100),
            "warp_pct": _ratio(t["warp_sqft"], gross, 100),
            "misalignment_pct": _ratio(t["misalignment_sqft"], gross, 100),
            "moisture_pct": _ratio(t["moisture_sqft"], gross, 100),
            "bale_pct": _ratio(t["bale_sqft"], gross, 100),
            "unexplained_pct": _ratio(t["bale_sqft"] - accounted, gross, 100),
            "maintenance_pm_pct": _ratio(t["maintenance_pm_seconds"], scheduled, 100),
            "operator_pct": _ratio(t["operator_seconds"], scheduled, 100),
            "setups": t["setup_records"] if t["shift_records"] else None,
            "paper_changes": t["paper_changes"] if t["shift_records"] else None,
            "lineal_per_setup": _ratio(t["lineal_ft"], t["setup_records"], places=1),
            "lineal_per_paper_change": _ratio(t["lineal_ft"], t["paper_changes"], places=1),
            "bale_msf": _round(t["bale_sqft"] / 1000, 1) if t["shift_records"] else None,
            "msf_per_day": _ratio(gross / 1000, t["production_days"], places=1),
        }

    @staticmethod
    def _kpi_rows(current, previous, comparable):
        rows = []
        for key, label, unit, better in KPI_CATALOG:
            now, before = current[key], previous[key] if previous else None
            delta = round(now - before, 4) if comparable and now is not None and before is not None else None
            if delta is None or better == "neutral":
                change = None if delta is None else "neutral"
            elif delta == 0:
                change = "same"
            else:
                change = "better" if (delta < 0) == (better == "lower") else "worse"
            rows.append({"metric": key, "label": label, "unit": unit, "current": now, "previous": before,
                         "delta": delta, "better": better, "change": change})
        return rows

    def _envelope(self, tool, window, previous, shift, rows, trace, started, source_rows, **extra):
        windows = [window] + ([previous] if previous else [])
        coverage = "complete" if all(w["coverage"] == "complete" for w in windows) else \
            "unavailable" if window["coverage"] == "unavailable" else "partial"
        trace["elapsed_ms"] = (time.perf_counter() - started) * 1000
        trace["sql"] = ";\n".join(trace["sql"])
        return {"tool": tool, "window": window, "previous_window": previous, "shift": shift,
                "dataset_version": self.content_hash, "rows": rows, "coverage": coverage,
                "excluded_records": [], "source_rows": source_rows, "trace": trace, **extra}

    # ------------------------------------------------------------ period tools

    def get_period_kpis(self, window, compare=True, shift=None):
        """The dashboard tiles for one window, with the previous window of the
        same kind alongside when compare is true. Ratios of sums throughout."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        if type(compare) is not bool:
            raise ValueError("compare must be true or false")
        current = self._window(window)
        previous = self._previous(current) if compare else None
        now = self._sums(current, shift, trace)
        before = self._sums(previous, shift, trace) if previous else None
        comparable = bool(previous) and current["coverage"] == "complete" and previous["coverage"] == "complete"
        rows = self._kpi_rows(self._kpis(now), self._kpis(before) if before else None, comparable)
        source = {"current": now["source_rows"], "previous": before["source_rows"] if before else None}
        return self._envelope("get_period_kpis", current, previous, shift, rows, trace, started, source,
                              comparison="complete" if comparable else "withheld: a window is incomplete or unavailable")

    def compare_periods(self, current_window, previous_window, shift=None):
        """The same KPI table for two arbitrary windows (month vs month, week vs
        week, day vs day); the second window is the comparison base."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current, previous = self._window(current_window), self._window(previous_window)
        now, before = self._sums(current, shift, trace), self._sums(previous, shift, trace)
        comparable = current["coverage"] == "complete" and previous["coverage"] == "complete"
        rows = self._kpi_rows(self._kpis(now), self._kpis(before), comparable)
        source = {"current": now["source_rows"], "previous": before["source_rows"]}
        return self._envelope("compare_periods", current, previous, shift, rows, trace, started, source,
                              comparison="complete" if comparable else "withheld: a window is incomplete or unavailable")

    def get_downtime_pareto(self, window, group_by="reason", measure="minutes", shift=None):
        """Stops of the window grouped by reason, place or kind, ordered by the
        measure, with share and cumulative share and the previous window beside."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        if group_by not in _STOP_GROUPS or measure not in _STOP_MEASURES:
            raise ValueError("group_by must be reason, place or kind; measure must be minutes or count")
        current = self._window(window)
        previous = self._previous(current)
        clause, extra = self._shift_clause(shift)
        # group_by is strictly allowlisted; every visitor value is bound.
        sql = (f"SELECT {group_by} AS category, count(*) AS stops, sum(seconds)/60 AS minutes "
               f"FROM stops WHERE production_date BETWEEN ? AND ?{clause} GROUP BY {group_by}")
        grouped = {}
        for label, w in (("current", current), ("previous", previous)):
            params = [w["start"], w["end"], *extra]
            grouped[label] = {r["category"]: r for r in self._fetch(sql, params)}
            trace["sql"].append(sql)
            trace["parameters"].append(params)
        key = "minutes" if measure == "minutes" else "stops"
        rows = sorted(grouped["current"].values(), key=lambda r: (-r[key], r["category"]))
        total = sum(r[key] for r in rows)
        running, out = 0.0, []
        for r in rows:
            running += r[key]
            before = grouped["previous"].get(r["category"])
            out.append({"category": r["category"], "stops": r["stops"], "minutes": round(r["minutes"], 2),
                        "share_pct": _ratio(r[key], total, 100, 2), "cumulative_pct": _ratio(running, total, 100, 2),
                        "previous_stops": before["stops"] if before else (0 if previous["coverage"] != "unavailable" else None),
                        "previous_minutes": round(before["minutes"], 2) if before else (0.0 if previous["coverage"] != "unavailable" else None)})
        source = {"stops": sum(r["stops"] for r in rows), "previous_stops": sum(r["stops"] for r in grouped["previous"].values())}
        return self._envelope("get_downtime_pareto", current, previous, shift, out, trace, started, source,
                              group_by=group_by, measure=measure, total_minutes=round(sum(r["minutes"] for r in rows), 2),
                              total_stops=sum(r["stops"] for r in rows))

    def get_waste_breakdown(self, window, shift=None):
        """Trim, shear, bond, warp, misalignment, bale and the unexplained
        remainder as square feet and percent of gross, previous window alongside."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        previous = self._previous(current)
        now, before = self._sums(current, shift, trace), self._sums(previous, shift, trace)

        def areas(t):
            accounted = t["trim_sqft"] + t["shear_sqft"] + t["reject_sqft"]
            return {"trim": t["trim_sqft"], "shear": t["shear_sqft"], "bond": t["bond_sqft"], "warp": t["warp_sqft"],
                    "misalignment": t["misalignment_sqft"], "moisture": t["moisture_sqft"], "dry_end": t["reject_sqft"], "bale": t["bale_sqft"],
                    "unexplained": t["bale_sqft"] - accounted}
        a, b = areas(now), areas(before)
        available = previous["coverage"] != "unavailable"
        rows = []
        for key, label in WASTE_CATEGORIES:
            pct, prior = _ratio(a[key], now["gross_sqft"], 100), _ratio(b[key], before["gross_sqft"], 100) if available else None
            rows.append({"category": key, "label": label, "sqft": round(a[key], 1), "pct_of_gross": pct,
                         "previous_sqft": round(b[key], 1) if available else None, "previous_pct_of_gross": prior,
                         "delta_pct_points": round(pct - prior, 4) if pct is not None and prior is not None else None})
        source = {"current": now["source_rows"], "previous": before["source_rows"]}
        details = [{"reason": d["reason"], "detail": d["detail"], "sqft": round(d["sqft"], 1), "sheets": d["sheets"],
                    "pct_of_gross": _ratio(d["sqft"], now["gross_sqft"], 100),
                    "share_of_dry_end_pct": _ratio(d["sqft"], now["reject_sqft"], 100, places=1)} for d in now["details"]]
        return self._envelope("get_waste_breakdown", current, previous, shift, rows, trace, started, source,
                              gross_sqft=round(now["gross_sqft"], 1), previous_gross_sqft=round(before["gross_sqft"], 1) if available else None,
                              details=details)

    def get_stops_profile(self, window, shift=None):
        """Stops, minutes and minutes per stop for every production day of the
        window, plus the window's Monday-to-Friday weekday profile."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        clause, extra = self._shift_clause(shift)
        params = [current["start"], current["end"], *extra]
        queries = {
            "shifts": "SELECT production_date, sum(scheduled_minutes) AS scheduled_minutes FROM shifts "
                      f"WHERE production_date BETWEEN ? AND ?{clause} GROUP BY production_date ORDER BY production_date",
            "stops": "SELECT production_date, count(*) AS stops, sum(seconds)/60 AS minutes FROM stops "
                     f"WHERE production_date BETWEEN ? AND ?{clause} GROUP BY production_date",
        }
        fetched = {}
        for name, sql in queries.items():
            fetched[name] = self._fetch(sql, params)
            trace["sql"].append(sql)
            trace["parameters"].append(params)
        stops_by_day = {r["production_date"]: r for r in fetched["stops"]}
        daily, profile = [], {name: {"weekday": name, "days": 0, "stops": 0, "minutes": 0.0} for name in WEEKDAYS[:5]}
        for r in fetched["shifts"]:
            day = r["production_date"]
            name = WEEKDAYS[day.weekday()]
            stopped = stops_by_day.get(day, {"stops": 0, "minutes": 0.0})
            minutes = round(stopped["minutes"], 2)
            daily.append({"production_day": day.isoformat(), "weekday": name, "stops": stopped["stops"], "minutes": minutes,
                          "minutes_per_stop": _ratio(minutes, stopped["stops"], places=2),
                          "downtime_pct": _ratio(minutes, r["scheduled_minutes"], 100)})
            bucket = profile[name]
            bucket["days"] += 1
            bucket["stops"] += stopped["stops"]
            bucket["minutes"] += minutes
        weekday_rows = []
        for bucket in profile.values():
            weekday_rows.append({**bucket, "minutes": round(bucket["minutes"], 2),
                                 "stops_per_day": _ratio(bucket["stops"], bucket["days"], places=2),
                                 "minutes_per_day": _ratio(bucket["minutes"], bucket["days"], places=2),
                                 "minutes_per_stop": _ratio(bucket["minutes"], bucket["stops"], places=2)})
        source = {"shifts": len(fetched["shifts"]), "stops": sum(r["stops"] for r in daily)}
        return self._envelope("get_stops_profile", current, None, shift, daily, trace, started, source,
                              weekday_profile=weekday_rows)

    def get_grade_totals(self, window, shift=None):
        """Volume, lineal feet, speed and waste by board grade for one window,
        previous window's volume alongside. Ratios of sums within each grade.

        Grade-level speed can only use the setup records' own clocks: without
        stops = feet / (elapsed - setup-allocated stop seconds), elapsed = feet /
        elapsed seconds. Scheduled shift minutes carry no grade, so the tile
        table's "speed with stops" has no per-grade equivalent."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        previous = self._previous(current)
        clause, extra = self._shift_clause(shift)
        sql = ("SELECT grade, max(target) AS target, count(*) AS setups, count(DISTINCT wet_end_id) AS wet_ends, "
               "sum(feet) AS feet, sum(gross) AS gross, sum(seconds) AS seconds, sum(stop_seconds) AS stop_seconds, "
               "sum(stops) AS stops, sum(trim) AS trim, sum(rejects) AS rejects "
               f"FROM setups WHERE production_date BETWEEN ? AND ?{clause} GROUP BY grade ORDER BY gross DESC, grade")
        fetched = {}
        for name, win in (("current", current), ("previous", previous)):
            params = [win["start"], win["end"], *extra]
            fetched[name] = self._fetch(sql, params)
            trace["sql"].append(sql)
            trace["parameters"].append(params)
        available = previous["coverage"] != "unavailable"
        before = {r["grade"]: r for r in fetched["previous"]}
        total_gross = sum(r["gross"] for r in fetched["current"])
        rows = []
        for r in fetched["current"]:
            prior = before.get(r["grade"]) if available else None
            prior_msf = round(prior["gross"] / 1000, 1) if prior else (0.0 if available else None)
            run_fpm = _ratio(r["feet"], (r["seconds"] - r["stop_seconds"]) / 60, places=1)
            rows.append({"grade": r["grade"], "target_fpm": r["target"], "setups": r["setups"], "wet_ends": r["wet_ends"],
                         "lineal_ft": round(r["feet"], 1), "volume_msf": round(r["gross"] / 1000, 1),
                         "share_of_volume_pct": _ratio(r["gross"], total_gross, 100),
                         "speed_run_fpm": run_fpm, "speed_elapsed_fpm": _ratio(r["feet"], r["seconds"] / 60, places=1),
                         "speed_to_target_pct": _ratio(run_fpm, r["target"], 100, places=1),
                         "setup_stops": r["stops"], "setup_stop_minutes": round(r["stop_seconds"] / 60, 1),
                         "downtime_pct_of_elapsed": _ratio(r["stop_seconds"], r["seconds"], 100),
                         "lineal_per_setup": _ratio(r["feet"], r["setups"], places=1),
                         "lineal_per_wet_end": _ratio(r["feet"], r["wet_ends"], places=1),
                         "trim_pct": _ratio(r["trim"], r["gross"], 100), "dry_end_pct": _ratio(r["rejects"], r["gross"], 100),
                         "previous_volume_msf": prior_msf,
                         "delta_volume_msf": round(r["gross"] / 1000 - prior_msf, 1) if prior_msf is not None else None})
        source = {"current": {"setups": sum(r["setups"] for r in fetched["current"])},
                  "previous": {"setups": sum(r["setups"] for r in fetched["previous"])} if available else None}
        return self._envelope("get_grade_totals", current, previous, shift, rows, trace, started, source,
                              total_volume_msf=round(total_gross / 1000, 1),
                              speed_note="Grade speeds come from the setup records' own elapsed clocks; "
                                         "scheduled shift minutes carry no grade, so there is no per-grade speed with stops.")

    def get_overrun(self, window, shift=None):
        """Over/under-run by knife: actual cuts against nominal cuts for every
        customer line of the window, as cuts, sheets (cuts x outs) and percent of
        nominal, plus the share of lines inside the +-band, over it and under it.
        The last row is all knives together."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        clause, extra = self._shift_clause(shift)
        sql = ("SELECT position AS knife, count(*) AS lines, sum(nominal_cuts) AS nominal_cuts, sum(actual_cuts) AS actual_cuts, "
               "sum(nominal_cuts*outs) AS nominal_sheets, sum(actual_cuts*outs) AS actual_sheets, "
               "sum(CASE WHEN actual_cuts > nominal_cuts THEN (actual_cuts-nominal_cuts)*outs ELSE 0 END) AS over_sheets, "
               "sum(CASE WHEN actual_cuts < nominal_cuts THEN (nominal_cuts-actual_cuts)*outs ELSE 0 END) AS under_sheets, "
               "sum(CASE WHEN (actual_cuts-nominal_cuts)*100.0/nominal_cuts > ? THEN 1 ELSE 0 END) AS lines_over, "
               "sum(CASE WHEN (actual_cuts-nominal_cuts)*100.0/nominal_cuts < -? THEN 1 ELSE 0 END) AS lines_under "
               f"FROM customers WHERE production_date BETWEEN ? AND ?{clause} AND nominal_cuts > 0 GROUP BY position ORDER BY position")
        # bound order: the two band values sit first in the SQL text, then the window and shift
        params = [OVERRUN_BAND_PCT, OVERRUN_BAND_PCT, current["start"], current["end"], *extra]
        fetched = self._fetch(sql, params)
        trace["sql"].append(sql)
        trace["parameters"].append(params)

        def row(label, r):
            inside = r["lines"] - r["lines_over"] - r["lines_under"]
            return {"knife": label, "lines": r["lines"], "nominal_cuts": r["nominal_cuts"], "actual_cuts": r["actual_cuts"],
                    "delta_cuts": r["actual_cuts"] - r["nominal_cuts"],
                    "overrun_pct": _ratio(r["actual_cuts"] - r["nominal_cuts"], r["nominal_cuts"], 100, places=2),
                    "nominal_sheets": r["nominal_sheets"], "actual_sheets": r["actual_sheets"],
                    "over_sheets": r["over_sheets"], "under_sheets": r["under_sheets"],
                    "lines_inside_band_pct": _ratio(inside, r["lines"], 100, places=1),
                    "lines_over_pct": _ratio(r["lines_over"], r["lines"], 100, places=1),
                    "lines_under_pct": _ratio(r["lines_under"], r["lines"], 100, places=1)}
        rows = [row(f"knife {r['knife']}", r) for r in fetched]
        if fetched:
            total = {k: sum(r[k] for r in fetched) for k in fetched[0] if k != "knife"}
            rows.append(row("all knives", total))
        source = {"customer_lines": sum(r["lines"] for r in fetched)}
        return self._envelope("get_overrun", current, None, shift, rows, trace, started, source,
                              band_pct=OVERRUN_BAND_PCT, window_pct=OVERRUN_WINDOW_PCT,
                              band_note=f"Inside the band means actual cuts within +-{OVERRUN_BAND_PCT:g}% of nominal on that line; "
                                        "the band is a demo setting, the plant's acceptance range is an open owner input. "
                                        "Positive is overrun, negative is underrun.")

    def get_trend(self, metric, weeks=8, anchor=None, shift=None):
        """A weekly series (Mon-Fri production weeks) of one allow-listed metric,
        ending with the week that contains the anchor."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        if metric not in TREND_METRICS:
            raise ValueError("metric must be one of: " + ", ".join(TREND_METRICS))
        if type(weeks) is not int or not 1 <= weeks <= MAX_TREND_WEEKS:
            raise ValueError(f"weeks must be an integer from 1 to {MAX_TREND_WEEKS}")
        _, anchor_day, last_start, last_end = parse_window({"period": "week", "anchor": anchor or self.last_day}, self._first, self._last)
        rows, missing = [], []
        for back in range(weeks - 1, -1, -1):
            w = self._describe("week", last_start - timedelta(days=7 * back), last_end - timedelta(days=7 * back))
            if w["coverage"] == "unavailable":
                missing.append(w["start"])
                continue
            value = self._kpis(self._sums(w, shift, trace))[metric]
            rows.append({"week_start": w["start"], "week_end": w["end"], "production_days": w["production_days_observed"],
                         "value": value, "unit": TREND_UNITS[metric], "partial": w["coverage"] != "complete"})
        window = {"period": "week", "anchor": anchor_day.isoformat(), "start": (last_start - timedelta(days=7 * (weeks - 1))).isoformat(),
                  "end": last_end.isoformat(), "label": f"{weeks} production weeks ending {last_end.isoformat()}",
                  "production_days_expected": 5 * weeks, "production_days_observed": sum(r["production_days"] for r in rows),
                  "coverage": "complete" if rows and not missing and not any(r["partial"] for r in rows) else "partial" if rows else "unavailable"}
        source = {"weeks": len(rows)}
        return self._envelope("get_trend", window, None, shift, rows, trace, started, source,
                              metric=metric, unit=TREND_UNITS[metric], weeks_requested=weeks, missing_weeks=missing)


_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def default_store():
    """The process-wide store over the default two months, built on first use."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = RecordStore()
        return _DEFAULT
