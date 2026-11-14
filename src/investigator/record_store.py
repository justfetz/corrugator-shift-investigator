"""The app's data source: the synthetic production record in one in-memory DuckDB.

The dataset is a window, not a file (issue #35): every production day (Mon-Fri)
from 1 January 2025 (`synth.DATASET_FIRST_DAY`, a fixed start, issue #100) to
`as_of`, a rolling end. `INVESTIGATOR_AS_OF` pins `as_of` (tests and the golden
set use 2026-09-30); unset, a store booting from a prebuilt folder takes the
window that folder was built for (the image's window is fixed at image build),
and otherwise `as_of` is yesterday. Every day is generated from its own
date-keyed streams (`synth.generate_day`), so a day's records are the same in
every window that contains it.

`RecordStore` generates the record families once (`synth.generate_days`), loads
them into the record contract's tables (docs/RECORD_CONTRACT.md: setups, the
setup_orders lane view, rejects, wet_ends, shifts, stops, rolls and the customers
and reject_reasons dimensions) and exposes two things:

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
from bisect import bisect_left, bisect_right
from calendar import monthrange
import csv
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import math
import os
import pickle
import tempfile
import threading
import time
from pathlib import Path
from threading import Timer

import duckdb

from . import records as R
from . import targets
from .analysis import DUCKDB_CONFIG, Analysis, load_rows
from .fixture import GRADES
from .synth import generate_days, weekdays_between, window_bounds

DEFAULT_SEED = 2026
DEFAULT_MACHINE = "CORR-1"
AS_OF_ENV = "INVESTIGATOR_AS_OF"         # YYYY-MM-DD: the window's last calendar day; unset = the prebuilt folder's, else yesterday
PREBUILT_ENV = "INVESTIGATOR_PREBUILT"   # folder written by `python -m investigator.prebuilt`; unset = generate at boot
# 2: the folder also carries the derived shift_kpis table (issue #34). 3: the window replaces the months and
# records.pkl holds one pickled blob per production day, unpickled on first use (issue #35: a year of record
# objects costs about 600 ms to unpickle, which is most of the boot budget).
PREBUILT_FORMAT = 3
DAY_CACHE = 64   # production days of record objects a store keeps unpickled at once (the rest stay pickled)
LOAD_CHUNK = 50_000   # rows per insert while loading a generated store, so the JSON payload stays small
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PERIODS = ("day", "week", "month", "fiscal")
# 4-4-5 accounting calendar for the demo: a fiscal year is 52 weeks, four quarters of 4, 4 and 5 weeks
# (twelve periods), and the year anchored here starts on Monday 2026-08-03 (the first Monday of the
# original two months). periods.py (the legacy period report) uses the same `fiscal_period`, so the
# scope bar's "4-4-5" view and the period tools name the same windows. The calendar is defined for
# every date (issue #35): the prior year starts on 2025-08-04, so the rolling window's first days fall
# in its periods, and years further away are named by their first Monday.
FISCAL_START = date(2026, 8, 3)
FISCAL_WEEKS = (4, 4, 5)

CORRUPT_TRIM_SQFT = 99999999.0          # the fixture's bad-trim record, injected on demand
MAX_TREND_WEEKS = 26
ROW_LIMIT = 1000

# The record contract's tables (docs/RECORD_CONTRACT.md). The older working names the tools read stay as
# they were (feet = lineal_ft, gross = gross_sqft, trim = trim_sqft, shear = shear_sqft, rejects = dry-end
# quality reject sq ft, seconds = elapsed, width_in = web_width_in, grade = quality_id); the contract's new
# columns carry its names. `rejects` on a setup is the five dry-end families only: changeover and admin
# reject lines and the lanes' cutoff and stacker waste boards are their own columns and waste lines.
TABLES = {
    "setups": ("setup_id VARCHAR, production_date DATE, shift INTEGER, wet_end_id VARCHAR, quality_id VARCHAR, "
               "target DOUBLE, started TIMESTAMP, ended TIMESTAMP, seconds DOUBLE, stop_seconds DOUBLE, "
               "stops INTEGER, lineal_ft DOUBLE, gross_sqft DOUBLE, trim_sqft DOUBLE, shear_sqft DOUBLE, reject_sqft DOUBLE, "
               "web_width_in DOUBLE, change_code VARCHAR, split_from VARCHAR, run_seconds DOUBLE, scheduled_lineal_ft DOUBLE, "
               "avg_fpm DOUBLE, trim_in DOUBLE, trim_pct DOUBLE, swing_knife_sqft DOUBLE, is_fanfold BOOLEAN, sheet_type VARCHAR, "
               "filler BOOLEAN, lanes INTEGER, changeover_sqft DOUBLE, admin_sqft DOUBLE, cutoff_waste_sqft DOUBLE, "
               "stacker_waste_sqft DOUBLE"),
    "setup_orders": ("setup_id VARCHAR, production_date DATE, shift INTEGER, lane INTEGER, order_number VARCHAR, part_number VARCHAR, "
                     "customer_id VARCHAR, order_quantity INTEGER, sheet_width_in DOUBLE, sheet_length_in DOUBLE, outs INTEGER, "
                     "cutoff_knife INTEGER, nominal_cuts INTEGER, actual_cuts INTEGER, sheet_type VARCHAR, filler BOOLEAN, "
                     "cutoff_waste_boards INTEGER, stacker_waste_boards INTEGER, cutoff_waste_sqft DOUBLE, stacker_waste_sqft DOUBLE, "
                     "stack_height INTEGER, folds INTEGER, sheets_per_pallet INTEGER, pallets INTEGER, perforation_length_in DOUBLE, "
                     "width_share DOUBLE, allocated_trim_sqft DOUBLE, allocated_shear_sqft DOUBLE"),
    "customers": "customer_id VARCHAR, name VARCHAR, destination VARCHAR, ship_to VARCHAR",
    "rejects": ("setup_id VARCHAR, production_date DATE, shift INTEGER, family VARCHAR, category VARCHAR, reason VARCHAR, "
                "liner VARCHAR, side VARCHAR, lane INTEGER, sheets INTEGER, sqft DOUBLE, counted_in VARCHAR"),
    "reject_reasons": ("category VARCHAR, category_label VARCHAR, family VARCHAR, counted_in VARCHAR, controllable VARCHAR, "
                       "driver VARCHAR, reason VARCHAR, liner_positions VARCHAR[], side_positions VARCHAR[], generated BOOLEAN"),
    "wet_ends": ("wet_end_id VARCHAR, production_date DATE, quality_id VARCHAR, web_width_in DOUBLE, started TIMESTAMP, "
                 "ended TIMESTAMP, lineal_ft DOUBLE, gross_sqft DOUBLE, trim_sqft DOUBLE, max_fpm DOUBLE, avg_fpm DOUBLE, setup_count INTEGER"),
    "rolls": ("roll_id VARCHAR, production_date DATE, wet_end_id VARCHAR, stand VARCHAR, paper_grade VARCHAR, width_in DOUBLE, "
              "lineal_ft DOUBLE, peel_sqft DOUBLE, core_sqft DOUBLE, spliced_out BOOLEAN"),
    "shifts": ("production_date DATE, shift INTEGER, crew VARCHAR, lineal_ft DOUBLE, gross_sqft DOUBLE, trim_sqft DOUBLE, shear_sqft DOUBLE, "
               "reject_sqft DOUBLE, bale_sqft DOUBLE, max_fpm DOUBLE, avg_fpm DOUBLE, scheduled_minutes INTEGER, wet_end_count INTEGER, "
               "changeover_sqft DOUBLE, admin_sqft DOUBLE, cutoff_waste_sqft DOUBLE, stacker_waste_sqft DOUBLE"),
    "stops": ("stop_id VARCHAR, production_date DATE, shift INTEGER, setup_id VARCHAR, kind VARCHAR, place VARCHAR, "
              "reason VARCHAR, seconds DOUBLE, started TIMESTAMP, ended TIMESTAMP, comment VARCHAR"),
}
# Tables without a production date (dimensions), skipped by per-day comparisons.
DIMENSION_TABLES = ("customers", "reject_reasons")

# One row per production day x shift x variant (`corrupt` false: the records as generated; true: the
# fixture's bad-trim record injected into the day's first shift-2 setup, as `day_analysis(corrupt=True)`
# does). Raw numerators and denominators only, never a percentage: any window's tile is a ratio of the
# sums of these columns (docs/METRICS.md, "The shift_kpis table", names each column's formula row).
# Built once per store from the contract tables (`build_shift_kpis`): at image build into the prebuilt
# file, at boot when generating. Setups that fail validation are left out of the setup columns and
# counted in `excluded_setups`, exactly as the per-day Analysis excludes them (issue #34).
SHIFT_KPIS = (
    ("production_date", "DATE"), ("shift", "INTEGER"), ("corrupt", "BOOLEAN"), ("crew", "VARCHAR"),
    ("coverage", "VARCHAR"), ("excluded_setups", "INTEGER"),
    ("setups", "INTEGER"), ("paper_changes", "INTEGER"), ("wet_end_ids", "VARCHAR[]"),
    ("lineal_ft", "DOUBLE"), ("target_ft", "DOUBLE"), ("elapsed_seconds", "DOUBLE"), ("setup_stop_seconds", "DOUBLE"),
    ("gross_sqft", "DOUBLE"), ("trim_sqft", "DOUBLE"), ("shear_sqft", "DOUBLE"), ("reject_sqft", "DOUBLE"),
    ("bond_sqft", "DOUBLE"), ("warp_sqft", "DOUBLE"), ("misalignment_sqft", "DOUBLE"), ("moisture_sqft", "DOUBLE"), ("other_sqft", "DOUBLE"),
    ("changeover_sqft", "DOUBLE"), ("admin_sqft", "DOUBLE"), ("cutoff_waste_sqft", "DOUBLE"), ("stacker_waste_sqft", "DOUBLE"),
    ("setups_above_trim_target", "INTEGER"), ("grade_gross_sqft", "MAP(VARCHAR, DOUBLE)"),
    ("scheduled_seconds", "DOUBLE"), ("bale_sqft", "DOUBLE"),
    ("stops", "INTEGER"), ("stop_seconds", "DOUBLE"), ("maintenance_seconds", "DOUBLE"), ("planned_maintenance_seconds", "DOUBLE"),
    ("operator_seconds", "DOUBLE"), ("source_ids", "VARCHAR[]"),
)
SHIFT_TRIM_TARGET_PCT = targets.TRIM_FLAG_PCT   # the owner's per-setup trim flag, 3.25% (targets.json); the daily KPI tool counts the same
_SHIFT_KPIS_SQL = f"""
WITH variants AS (SELECT * FROM (VALUES (false), (true)) v(corrupt)),
valid AS (SELECT v.corrupt, s.* FROM setups s CROSS JOIN variants v
          WHERE NOT EXISTS (SELECT 1 FROM kpi_excluded x WHERE x.setup_id = s.setup_id AND x.corrupt = v.corrupt)),
excluded AS (SELECT x.corrupt, s.production_date, s.shift, count(*) AS n
             FROM kpi_excluded x JOIN setups s ON s.setup_id = x.setup_id GROUP BY ALL),
setup_sums AS (SELECT corrupt, production_date, shift, count(*) AS setups, count(DISTINCT wet_end_id) AS paper_changes,
                      list(DISTINCT wet_end_id ORDER BY wet_end_id) AS wet_end_ids, sum(lineal_ft) AS lineal_ft,
                      sum(target*seconds/60) AS target_ft, sum(seconds) AS elapsed_seconds, sum(stop_seconds) AS setup_stop_seconds,
                      sum(gross_sqft) AS gross_sqft, sum(trim_sqft) AS trim_sqft, sum(shear_sqft) AS shear_sqft, sum(reject_sqft) AS reject_sqft,
                      sum(changeover_sqft) AS changeover_sqft, sum(admin_sqft) AS admin_sqft,
                      sum(cutoff_waste_sqft) AS cutoff_waste_sqft, sum(stacker_waste_sqft) AS stacker_waste_sqft,
                      count(*) FILTER (WHERE trim_sqft/gross_sqft*100 > {SHIFT_TRIM_TARGET_PCT}) AS setups_above_trim_target,
                      list(setup_id ORDER BY setup_id) AS source_ids
               FROM valid GROUP BY ALL),
grade_sums AS (SELECT corrupt, production_date, shift, map(list(quality_id ORDER BY quality_id), list(gross ORDER BY quality_id)) AS grade_gross_sqft
               FROM (SELECT corrupt, production_date, shift, quality_id, sum(gross_sqft) AS gross FROM valid GROUP BY ALL) GROUP BY ALL),
reject_sums AS (SELECT v.corrupt, v.production_date, v.shift,
                       sum(r.sqft) FILTER (WHERE r.family = 'BOND') AS bond_sqft, sum(r.sqft) FILTER (WHERE r.family = 'WARP') AS warp_sqft,
                       sum(r.sqft) FILTER (WHERE r.family = 'MISALIGNMENT') AS misalignment_sqft,
                       sum(r.sqft) FILTER (WHERE r.family = 'MOISTURE') AS moisture_sqft,
                       sum(r.sqft) FILTER (WHERE r.family = 'OTHER') AS other_sqft
                FROM rejects r JOIN valid v ON v.setup_id = r.setup_id GROUP BY ALL),
stop_sums AS (SELECT production_date, shift, count(*) AS stops, sum(seconds) AS stop_seconds,
                     sum(seconds) FILTER (WHERE kind = 'Maintenance') AS maintenance_seconds,
                     sum(seconds) FILTER (WHERE kind = 'Scheduled') AS planned_maintenance_seconds,
                     sum(seconds) FILTER (WHERE kind = 'Operator') AS operator_seconds
              FROM stops GROUP BY ALL)
SELECT sh.production_date, sh.shift, v.corrupt, sh.crew,
       CASE WHEN coalesce(e.n, 0) > 0 THEN 'incomplete' ELSE 'complete' END, coalesce(e.n, 0),
       coalesce(ss.setups, 0), coalesce(ss.paper_changes, 0), coalesce(ss.wet_end_ids, []),
       coalesce(ss.lineal_ft, 0), coalesce(ss.target_ft, 0), coalesce(ss.elapsed_seconds, 0), coalesce(ss.setup_stop_seconds, 0),
       coalesce(ss.gross_sqft, 0), coalesce(ss.trim_sqft, 0), coalesce(ss.shear_sqft, 0), coalesce(ss.reject_sqft, 0),
       coalesce(rs.bond_sqft, 0), coalesce(rs.warp_sqft, 0), coalesce(rs.misalignment_sqft, 0), coalesce(rs.moisture_sqft, 0), coalesce(rs.other_sqft, 0),
       coalesce(ss.changeover_sqft, 0), coalesce(ss.admin_sqft, 0), coalesce(ss.cutoff_waste_sqft, 0), coalesce(ss.stacker_waste_sqft, 0),
       coalesce(ss.setups_above_trim_target, 0), coalesce(gs.grade_gross_sqft, map()),
       sh.scheduled_minutes * 60.0, sh.bale_sqft,
       coalesce(st.stops, 0), coalesce(st.stop_seconds, 0), coalesce(st.maintenance_seconds, 0), coalesce(st.planned_maintenance_seconds, 0),
       coalesce(st.operator_seconds, 0), coalesce(ss.source_ids, [])
FROM shifts sh CROSS JOIN variants v
LEFT JOIN setup_sums ss ON ss.corrupt = v.corrupt AND ss.production_date = sh.production_date AND ss.shift = sh.shift
LEFT JOIN grade_sums gs ON gs.corrupt = v.corrupt AND gs.production_date = sh.production_date AND gs.shift = sh.shift
LEFT JOIN reject_sums rs ON rs.corrupt = v.corrupt AND rs.production_date = sh.production_date AND rs.shift = sh.shift
LEFT JOIN excluded e ON e.corrupt = v.corrupt AND e.production_date = sh.production_date AND e.shift = sh.shift
LEFT JOIN stop_sums st ON st.production_date = sh.production_date AND st.shift = sh.shift
ORDER BY sh.production_date, sh.shift, v.corrupt"""


# Dry-end waste is a percent of the yield after trim and shear (gross minus side trim minus shear), the owner's
# denominator (issue #101, targets.json); side trim, shear, bale and the unexplained remainder stay percents of gross.
YIELD_UNIT = "% of yield after trim and shear"
# The dashboard tiles: key, label, unit, which direction is better.
# "neutral" means the count is context, not a score (docs/METRICS.md).
KPI_CATALOG = (
    ("volume_msf", "Volume", "MSF", "higher"),
    ("lineal_ft", "Lineal feet", "ft", "higher"),
    ("msf_per_day", "Volume per production day", "MSF/day", "higher"),
    ("speed_run_fpm", "Speed without stops", "ft/min", "higher"),
    ("speed_all_fpm", "Speed with stops", "ft/min", "higher"),
    ("downtime_pct", "Downtime", "% of scheduled minutes", "lower"),
    ("downtime_minutes", "Downtime minutes", "min", "lower"),
    ("stops", "Stops", "count", "lower"),
    ("minutes_per_stop", "Minutes per stop", "min/stop", "lower"),
    ("trim_pct", "Side trim", "% of gross", "lower"),
    ("shear_pct", "Shear", "% of gross", "lower"),
    ("dry_end_pct", "Dry-end rejects", YIELD_UNIT, "lower"),
    ("bond_pct", "Dry-end: bond", YIELD_UNIT, "lower"),
    ("warp_pct", "Dry-end: warp", YIELD_UNIT, "lower"),
    ("misalignment_pct", "Dry-end: misalignment", YIELD_UNIT, "lower"),
    ("moisture_pct", "Dry-end: moisture", YIELD_UNIT, "lower"),
    ("other_pct", "Dry-end: other (cracking, handling, paper, print)", YIELD_UNIT, "lower"),
    ("bale_pct", "Bale (scale reading)", "% of gross", "lower"),
    ("unexplained_pct", "Unexplained (bale minus every recorded waste line)", "% of gross", "lower"),
    ("maintenance_pm_pct", "Maintenance + planned maintenance (scheduled) downtime", "% of scheduled minutes", "lower"),
    ("operator_pct", "Operator downtime", "% of scheduled minutes", "lower"),
    ("setups", "Setup records", "count", "neutral"),
    ("paper_changes", "Paper changes (distinct wet ends)", "count", "neutral"),
    ("lineal_per_setup", "Lineal per setup (dry-end record)", "ft/setup", "higher"),
    ("lineal_per_paper_change", "Lineal per paper change (wet-end record)", "ft/paper change", "higher"),
    ("bale_msf", "Baled waste (scale reading)", "MSF", "lower"),
)
KPI_UNITS = {key: unit for key, _, unit, _ in KPI_CATALOG}
TREND_METRICS = ("trim_pct", "shear_pct", "dry_end_pct", "warp_pct", "bond_pct", "misalignment_pct", "moisture_pct", "other_pct",
                 "speed_run_fpm", "speed_all_fpm", "downtime_pct", "stops", "minutes_per_stop", "msf_per_day",
                 "lineal_per_setup", "lineal_per_paper_change")
TREND_UNITS = {**{m: KPI_UNITS[m] for m in TREND_METRICS if m in KPI_UNITS}, "msf_per_day": "MSF per production day"}
# The waste lines of a window (issue #100): the dry-end quality families add up to dry_end; changeover rejects
# count with shear, the lanes' cutoff-knife and stacker waste boards are their own lines, planning / admin entries
# are reported apart; the unexplained remainder is the bale minus every one of them.
WASTE_CATEGORIES = (("trim", "Side trim"), ("shear", "Shear chops"), ("changeover", "Changeover rejects (counted with shear)"),
                    ("cutoff_waste", "Cutoff-knife waste boards"), ("stacker_waste", "Stacker waste boards"),
                    ("bond", "Dry-end: bond"), ("warp", "Dry-end: warp"), ("misalignment", "Dry-end: misalignment"),
                    ("moisture", "Dry-end: moisture"), ("other", "Dry-end: other (cracking, handling, paper, print)"),
                    ("dry_end", "Dry-end rejects (all dry-end families)"), ("admin", "Planning / admin (not production waste)"),
                    ("bale", "Bale (scale reading)"), ("unexplained", "Unexplained (bale minus every recorded waste line)"))
DRY_END_CATEGORIES = ("bond", "warp", "misalignment", "moisture", "other", "dry_end")
WASTE_BANDS = {"trim": "trim", "shear": "shear", "dry_end": "dry_end"}   # waste category -> its band in targets.json
_STOP_GROUPS = ("reason", "place", "kind")
_STOP_MEASURES = ("minutes", "count")
# Over/under-run: actual cuts against nominal cuts per knife. The band is the demo's
# "inside the cut range" width; the plant's acceptance range is an open owner input
# (docs/METRICS.md). The record window is the +-10% the generator never leaves.
OVERRUN_BAND_PCT = 2.0
OVERRUN_WINDOW_PCT = 10.0
# Speed opportunity: setups shorter than this running time never reach steady speed and are left
# out (counted, never silently); a cell needs this many setups before a best-fifth is meaningful.
SPEED_MIN_RUN_MINUTES = 2.0
SPEED_MIN_SETUPS = 10
_SPEED_SLICES = ("width", "crew")
# Speed variance (issue #92): the gap between a reference speed and the window's speed with stops, split
# into buckets that add up to it. The setup bucket is the order-change stop, the changeover the cost model
# prices as setup time too; planned maintenance and external stops (a power outage) are a bucket each (PR #111);
# every other recorded stop, unplanned maintenance and operator, is the stops bucket.
SPEEDVAR_AGAINST = ("baseline", "previous", "shift")
SPEEDVAR_ALIASES = {"target": "baseline"}   # the reference was each grade's target before the owner's baseline (issue #101)
SPEEDVAR_SETUP_REASON = "Order change"
# Stops are the unplanned maintenance and operator stops; planned maintenance (schedule-driven) and external stops
# (a power outage) are buckets of their own, listed only when either side holds one (the owner, PR #111).
SPEEDVAR_BUCKETS = (("stops", "Stops"), ("planned", "Planned maintenance"), ("external", "External stops"), ("setups", "Setups"),
                    ("below_baseline", "Below baseline"), ("mix", "Grade mix"))
SPEEDVAR_OPTIONAL = ("planned", "external")
# Each bucket's lever (issue #101; PR #111): the grade mix and planned maintenance are schedule-driven, a power outage
# is external, and the rest is the crew's.
SPEEDVAR_CONTROL = {"mix": ("schedule-driven", "the order mix decides which grades run"),
                    "planned": ("schedule-driven", "the maintenance plan sets planned stops"),
                    "external": ("external", "outside the plant, not controllable")}
SPEEDVAR_CREW = ("controllable", "stops, changeovers and running speed on the floor")
SPEEDVAR_SHARE_MIN_FPM = 0.05   # a gap smaller than this has no meaningful shares: they are left null


def shift_note(crew):
    """The one note a shift record carries: its crew label (the record families have no free text)."""
    return f"{crew} on duty (synthetic crew label, not a person). The record families carry no free-text shift note."


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


def fiscal_period(moment):
    """(start, end, year, number, weeks) of the 4-4-5 period containing `moment`. A fiscal year is
    52 weeks: four quarters of 4, 4 and 5 weeks, so twelve periods. Year 0 starts on FISCAL_START,
    year -1 (the prior year) 52 weeks earlier; `number` is 1-12."""
    year, week = divmod((moment - FISCAL_START).days // 7, 52)
    year_start, first_week = FISCAL_START + timedelta(weeks=52 * year), 0
    for number in range(12):
        weeks = FISCAL_WEEKS[number % 3]
        if week < first_week + weeks:
            start = year_start + timedelta(weeks=first_week)
            return start, start + timedelta(days=weeks * 7 - 1), year, number + 1, weeks
        first_week += weeks
    raise AssertionError("twelve periods cover 52 weeks")


def fiscal_bounds(moment):
    """(start, end, label) of the 4-4-5 period containing `moment`: P1-P12 of the year anchored at
    FISCAL_START, of the prior year, or of the fiscal year named by its first Monday."""
    start, end, year, number, weeks = fiscal_period(moment)
    if year == 0:
        return start, end, f"4-4-5 P{number} ({weeks} weeks)"
    if year == -1:
        return start, end, f"4-4-5 P{number} of the prior year ({weeks} weeks)"
    return start, end, f"4-4-5 P{number} of the year starting {(FISCAL_START + timedelta(weeks=52 * year)).isoformat()} ({weeks} weeks)"


def _tile_sums(t):
    """The raw numerators a window's KPI tiles are priced from (issue #31b, cost_model.period_tiles): the areas and
    the stop seconds behind each ratio, unrounded. Kept off every model's packet (narrative.PACKET_DROP is per row;
    openai_provider.compact_result drops this key)."""
    return {"gross_sqft": t["gross_sqft"], "trim": t["trim_sqft"], "shear": t["shear_sqft"], "dry_end": t["reject_sqft"],
            "bond": t["bond_sqft"], "warp": t["warp_sqft"], "misalignment": t["misalignment_sqft"], "moisture": t["moisture_sqft"],
            "other": t["other_sqft"], "changeover": t["changeover_sqft"], "admin": t["admin_sqft"], "cutoff_waste": t["cutoff_waste_sqft"],
            "stacker_waste": t["stacker_waste_sqft"], "bale": t["bale_sqft"], "unexplained": t["bale_sqft"] - accounted_sqft(t),
            "maintenance_pm_seconds": t["maintenance_pm_seconds"], "operator_seconds": t["operator_seconds"]}


def accounted_sqft(t):
    """Every recorded waste line of a window's sums: what the bale is compared with."""
    return (t["trim_sqft"] + t["shear_sqft"] + t["reject_sqft"] + t["changeover_sqft"] + t["admin_sqft"]
            + t["cutoff_waste_sqft"] + t["stacker_waste_sqft"])


def parse_window(spec, first_day=None, last_day=None):
    """Validate {"period": day|week|month, "anchor": "YYYY-MM-DD"} and return
    (period, anchor, start, end). A day window needs a weekday; when bounds
    are given the resolved window must overlap the store's calendar range."""
    if not isinstance(spec, dict) or set(spec) != {"period", "anchor"}:
        raise ValueError('A window is {"period": "day|week|month|fiscal", "anchor": "YYYY-MM-DD"}')
    period, anchor = spec["period"], spec["anchor"]
    if period not in PERIODS:
        raise ValueError("Window period must be day, week, month or fiscal (4-4-5)")
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
    elif period == "fiscal":
        start, end, _ = fiscal_bounds(moment)
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
    if period == "fiscal":
        return fiscal_bounds(start - timedelta(days=1))[:2]
    return _month_bounds(start - timedelta(days=1))


def window_label(period, start, end):
    if period == "day":
        return f"{start.isoformat()} ({WEEKDAYS[start.weekday()]})"
    if period == "week":
        return f"Week {start.isoformat()} to {end.isoformat()} (Mon-Fri)"
    if period == "fiscal":
        return f"{fiscal_bounds(start)[2]} {start.isoformat()} to {end.isoformat()}"
    return f"{start.strftime('%B %Y')} ({start.isoformat()} to {end.isoformat()})"


def _parse_as_of(value, source):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{source} must be a YYYY-MM-DD date, not {value!r}") from None


def _prebuilt_window(folder):
    """The (first, last) window a prebuilt folder's manifest names, or None."""
    try:
        first, last = json.loads((Path(folder) / "manifest.json").read_text())["window"]
        return date.fromisoformat(first), date.fromisoformat(last)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def dataset_window(as_of=None, prebuilt=None, today=None):
    """The (first, last) calendar dates of the dataset: from synth.DATASET_FIRST_DAY (1 January 2025) to
    `as_of`, a fixed start and a rolling end. `as_of` is resolved in this order: the argument; INVESTIGATOR_AS_OF; the window of the
    prebuilt folder (the argument, else INVESTIGATOR_PREBUILT), so a container keeps the window its
    image was built with however long it runs; else yesterday (`today` minus one day), the last
    production day whose three shifts have all started."""
    if as_of is not None:
        return window_bounds(as_of if isinstance(as_of, date) else _parse_as_of(as_of, "as_of"))
    value = os.environ.get(AS_OF_ENV, "").strip()
    if value:
        return window_bounds(_parse_as_of(value, AS_OF_ENV))
    folder = prebuilt_folder(prebuilt)
    window = _prebuilt_window(folder) if folder else None
    if window is not None:
        return window
    return window_bounds((today or date.today()) - timedelta(days=1))


class RecordStore:
    def __init__(self, seed=DEFAULT_SEED, window=None, machine_id=DEFAULT_MACHINE, prebuilt=None):
        """The rolling window of synthetic production in an in-memory DuckDB plus the record objects by day.

        `window` is a (first, last) pair of calendar dates; None resolves it with
        `dataset_window()` (from 1 January 2025 to INVESTIGATOR_AS_OF, else the prebuilt
        folder's window, else yesterday).

        `prebuilt` (or the INVESTIGATOR_PREBUILT folder) holds what `build_prebuilt`
        wrote for exactly these seed, window, machine and generator source: the record
        objects and the contract's tables. Loading it takes a fraction of generating
        (issue #19: generation plus the JSON inserts cost about two seconds per
        process start, paid by every visitor after Cloud Run scaled to zero).
        Anything missing, mismatched or unreadable falls back to generating, so the
        folder is an accelerator, never a source of truth; `boot_source` says which
        path ran. The folder is trusted like the code (its pickle executes what it
        reads): it comes from our own image build or from the operator, never from a
        visitor."""
        started = time.perf_counter()
        first, last = window if window is not None else dataset_window(prebuilt=prebuilt)
        if not isinstance(first, date) or not isinstance(last, date) or last < first:
            raise ValueError("A store window is a (first, last) pair of dates, first on or before last")
        self.seed, self.window, self.machine_id = seed, (first, last), machine_id
        self.as_of = last
        self._days = {d.isoformat(): d for d in weekdays_between(first, last)}
        if not self._days:
            raise ValueError(f"The window {first} to {last} holds no production day (Monday to Friday)")
        self.production_days = list(self._days)
        self.first_day, self.last_day = self.production_days[0], self.production_days[-1]
        self._first, self._last = self._days[self.first_day], self._days[self.last_day]
        self._by_day = {}            # production day -> its record objects: the DAY_CACHE most recently read
        self._day_blobs = {}         # production day -> its pickled record objects, unpickled on first use
        self._lock = threading.RLock()
        self._baselines = {}   # (first, last) of a trailing year -> each grade's sustained speed (grade_speed_baselines)
        self._db = duckdb.connect(":memory:", config=DUCKDB_CONFIG)   # join filter pushdown off: analysis.DUCKDB_CONFIG
        for table, columns in TABLES.items():
            self._db.execute(f"CREATE TABLE {table} ({columns})")
        self._db.execute(f"CREATE TABLE shift_kpis ({_SHIFT_KPIS_COLUMNS})")
        folder = prebuilt_folder(prebuilt)
        loaded = read_prebuilt(folder, seed, self.window, machine_id) if folder else None
        data = None
        if loaded is not None and self._load_prebuilt_tables(folder, loaded[1]):
            self.boot_source = "prebuilt"
            data = loaded[0]
            self._day_blobs = data["days"]
        if data is None:
            self.boot_source = "generated"
            data = generate_days(list(self._days.values()), seed=seed, machine_id=machine_id)
            self._load(data)
        self.content_hash = data["content_hash"]
        self.manifest = data["manifest"]
        self._db.execute("SET enable_external_access=false")
        self.boot_seconds = time.perf_counter() - started

    # ------------------------------------------------------------ loading

    @staticmethod
    def _iso(text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"

    @classmethod
    def _day_blobs_of(cls, data):
        """The record objects the per-day Analysis reads, one pickled blob per ISO production day.
        A year of record objects is about 330 MB in memory and 25 MB pickled, and a question reads
        one day at a time; prebuilt and generated stores hold the same blobs."""
        days = {}
        for family, key in (("dry_end_setups", "setups"), ("shifts", "shifts"), ("stops", "stops")):
            for record in data[family]:
                days.setdefault(cls._iso(record.production_date), {"setups": [], "stops": [], "shifts": []})[key].append(record)
        return {day: pickle.dumps(families, protocol=pickle.HIGHEST_PROTOCOL) for day, families in days.items()}

    def _day_records(self, day):
        """One production day's record objects, unpickled on first use; the DAY_CACHE most recently
        read stay unpickled. Call under self._lock."""
        families = self._by_day.pop(day, None)
        if families is None:
            blob = self._day_blobs.get(day)
            families = pickle.loads(blob) if blob is not None else {"setups": [], "stops": [], "shifts": []}
        self._by_day[day] = families   # most recent last
        while len(self._by_day) > DAY_CACHE:
            del self._by_day[next(iter(self._by_day))]
        return families

    def _load_prebuilt_tables(self, folder, expected_counts):
        """Copy the contract tables and the derived shift_kpis out of the prebuilt file. False (and
        empty tables) when the file is unreadable, its schema no longer matches TABLES or
        SHIFT_KPIS, or its counts differ from the manifest: the caller then generates. Never raises."""
        # ATTACH takes no bound parameter; the path is ours (env or argument), quoted as a literal.
        literal = str(Path(folder) / "store.duckdb").replace("'", "''")
        attached = False
        try:
            self._db.execute(f"ATTACH '{literal}' AS prebuilt (READ_ONLY)")
            attached = True
            for table in (*TABLES, "shift_kpis"):
                self._db.execute(f"INSERT INTO {table} SELECT * FROM prebuilt.{table}")
            counts = {table: self._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (*TABLES, "shift_kpis")}
            if counts != expected_counts:
                raise ValueError(f"prebuilt counts {counts} differ from the manifest {expected_counts}")
            self.counts = {table: counts[table] for table in TABLES}
            return True
        except Exception:
            for table in (*TABLES, "shift_kpis"):
                self._db.execute(f"DELETE FROM {table}")
            return False
        finally:
            if attached:
                self._db.execute("DETACH prebuilt")

    def _load(self, data):
        self._day_blobs = self._day_blobs_of(data)
        tables = self._rows(data)
        for table, rows in tables.items():
            _load_chunked(self._db, table, rows)
        build_shift_kpis(self._db, data)
        self.counts = {table: len(rows) for table, rows in tables.items()}

    @classmethod
    def _rows(cls, data):
        """The contract's tables as positional rows, in TABLES order."""
        tables = {table: [] for table in TABLES}
        setups, lanes, rejects = tables["setups"], tables["setup_orders"], tables["rejects"]
        for s in data["dry_end_setups"]:
            day = cls._iso(s.production_date)
            setups.append([s.setup_id, day, s.shift_number, s.wet_end_id, s.quality_id, GRADES[s.quality_id],
                           s.start, s.end, float(s.elapsed_seconds), float(s.stop_time_sec), s.number_of_stops,
                           s.lineal_ft, s.total_produced_sqft, s.trim_sqft, s.chop_sqft, round(s.reject_sqft, 1),
                           float(s.web_width_in), s.change_code, s.split_from_setup_id, float(s.actual_run_sec),
                           s.total_nominal_ft, s.avg_speed_fpm, s.trim_in, s.trim_pct, s.swing_knife_sqft, s.is_fanfold,
                           s.sheet_type, s.filler, len(s.lanes), round(s.changeover_sqft, 1), round(s.admin_sqft, 1),
                           round(s.cutoff_waste_sqft, 1), round(s.stacker_waste_sqft, 1)])
            used = s.used_width_in
            for c in s.lanes:
                # identity 6: the setup's own waste (side trim and shear chops) allocated to each customer by its lane's
                # share of the used width; the reject lines and waste boards are already the lane's own
                share = c.used_width_in / used
                lanes.append([s.setup_id, day, s.shift_number, c.lane, c.order_number, c.part_number, c.customer_id,
                              c.order_quantity, float(c.sheet_width_in), float(c.sheet_length_in), c.number_of_outs,
                              c.cutoff_allocation, c.nominal_cuts, c.actual_cuts, c.sheet_type, c.filler, c.cutoff_waste_boards,
                              c.stacker_waste_boards, c.cutoff_waste_sqft, c.stacker_waste_sqft, c.stack_height_sheets,
                              c.folds, c.sheets_per_pallet, c.pallets,
                              None if c.perforation_length_in is None else float(c.perforation_length_in),
                              share, s.trim_sqft * share, s.chop_sqft * share])
            for r in s.rejects:
                rejects.append([s.setup_id, day, s.shift_number, r.family, r.category, r.reason, r.liner, r.side, r.lane,
                                r.sheets, r.sqft, R.counted_in(r.category)])
        for c in data.get("customers", ()):
            tables["customers"].append([c.customer_id, c.name, c.destination, c.ship_to])
        for category in R.taxonomy()["categories"]:
            for reason in category["reasons"]:
                tables["reject_reasons"].append([category["key"], category["label"], category["family"],
                                                 R.counted_in(category["key"]), category["controllable"], category["driver"],
                                                 reason["reason"], list(reason.get("liner", [])), list(reason.get("side", [])),
                                                 reason.get("generated", True)])
        for w in data["wet_ends"]:
            tables["wet_ends"].append([w.wet_end_id, cls._iso(w.production_date), w.quality_id, float(w.web_width_in), w.start, w.end,
                                       w.lineal_ft, w.total_produced_sqft, w.side_trim_sqft, w.max_speed_fpm, w.avg_speed_fpm,
                                       len(w.setup_ids)])
        for r in data["rolls"]:
            tables["rolls"].append([r.roll_id, cls._iso(r.production_date), r.wet_end_id, r.stand, r.paper_grade, float(r.width_in),
                                    r.lineal_ft, r.peel_sqft, r.core_sqft, r.spliced_out])
        for sh in data["shifts"]:
            day = cls._iso(sh.production_date)
            tables["shifts"].append([day, sh.shift_number, sh.group_name, sh.total_lineal_ft, sh.total_produced_sqft, sh.side_trim_sqft,
                                     sh.chop_sqft, sh.reject_sqft, sh.bale_sqft, sh.max_speed_fpm, sh.avg_speed_fpm,
                                     sh.scheduled_minutes, len(sh.wet_end_ids), sh.changeover_sqft, sh.admin_sqft,
                                     sh.cutoff_waste_sqft, sh.stacker_waste_sqft])
        for st in data["stops"]:
            day = cls._iso(st.production_date)
            # a power outage is External, not maintenance (targets.json, the owner on PR #111); the records keep their kind
            tables["stops"].append([st.stop_id, day, st.shift_number, st.setup_id, targets.stop_kind(st.kind, st.reason), st.place,
                                    st.reason, float(st.duration_sec), st.start, st.end, st.comment])
        return tables

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
        rejects = s.reject_sqft + s.changeover_sqft + s.admin_sqft + s.cutoff_waste_sqft + s.stacker_waste_sqft
        quantities = {"lineal_ft": s.lineal_ft, "total_produced_sqft": s.total_produced_sqft, "trim_sqft": trim,
                      "chop_sqft": s.chop_sqft, "reject_sqft": rejects}
        for name, value in quantities.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {name}")
        gross, feet, width = s.total_produced_sqft, s.lineal_ft, s.web_width_in
        if width not in R.WEB_WIDTHS or feet <= 0 or gross <= 0:
            raise ValueError("Invalid gross geometry")
        if s.quality_id not in GRADES:
            raise ValueError("Invalid demo grade target")
        if not math.isclose(gross, feet * width / 12, abs_tol=0.06) or trim + s.chop_sqft + rejects > gross:
            raise ValueError("Waste/gross area out of bounds")
        elapsed = s.elapsed_seconds
        if elapsed <= 0 or s.stop_time_sec < 0 or s.stop_time_sec >= elapsed:
            raise ValueError("Invalid record interval")
        if not 1 <= len(s.lanes) <= 3 or [c.lane for c in s.lanes] != list(range(1, len(s.lanes) + 1)):
            raise ValueError("Expected one to three order lanes")
        if s.used_width_in > width:
            raise ValueError("Invalid centered web placement")
        lanes = {c.lane: c for c in s.lanes}
        if any(r.lane not in lanes or not math.isclose(r.sqft, r.sheets * lanes[r.lane].sheet_sqft, abs_tol=0.06) for r in s.rejects):
            raise ValueError("Reject lines do not reconcile")

    def day_analysis(self, production_day, corrupt=False):
        """The per-day Analysis over the record families. `corrupt` injects the
        fixture's bad-trim record into the first setup of shift 2, which is then
        excluded with its raw values and makes shift 2 coverage incomplete."""
        day = self.check_day(production_day)
        if type(corrupt) is not bool:
            raise ValueError("Invalid data-quality option")
        with self._lock:
            families = self._day_records(day)
            setups = sorted(families["setups"], key=lambda s: (s.start, s.setup_id))
            target = _corrupt_target(setups) if corrupt else None
            errors, setup_rows, order_rows, reject_rows = [], [], [], []
            for s in setups:
                trim = CORRUPT_TRIM_SQFT if s is target else s.trim_sqft
                try:
                    self._validate_setup(s, trim)
                except ValueError as exc:
                    raw = {k: v for k, v in R.to_dict(s).items() if k not in ("lanes", "rejects")}
                    raw["trim_sqft"] = trim
                    errors.append({"record_id": s.setup_id, "shift": s.shift_number, "error": str(exc), "raw": raw})
                    continue
                setup_rows.append([s.setup_id, s.shift_number, s.wet_end_id, s.quality_id, float(GRADES[s.quality_id]),
                                   float(s.elapsed_seconds), s.lineal_ft, s.total_produced_sqft, trim, s.chop_sqft,
                                   round(s.reject_sqft, 1), s.start, s.end, float(s.web_width_in), round(s.changeover_sqft, 1),
                                   round(s.cutoff_waste_sqft + s.stacker_waste_sqft, 1), round(s.admin_sqft, 1)])
                for c in s.lanes:
                    # The record families carry no order quantity ledger (docs/RECORDS.md):
                    # requested and remaining sheets are null, planned/produced are this record's.
                    order_rows.append([s.setup_id, s.shift_number, s.split_from_setup_id or s.setup_id, c.order_number,
                                       s.quality_id, f"knife {c.cutoff_allocation}", float(c.sheet_width_in),
                                       float(c.sheet_length_in), c.number_of_outs, c.actual_cuts, None,
                                       c.nominal_cuts * c.number_of_outs, c.actual_cuts * c.number_of_outs, None, None])
                for r in s.rejects:   # the dry-end families only; the daily `reason` is the family, `detail` the reported reason
                    if R.counted_in(r.category) == "dry_end":
                        reject_rows.append([s.setup_id, s.shift_number, r.family, r.reason, float(r.sheets), r.sqft])
            shift_rows = [[sh.shift_number, float(sh.scheduled_minutes * 60), shift_note(sh.group_name)]
                          for sh in sorted(families["shifts"], key=lambda sh: sh.shift_number)]
            stop_rows = [[st.stop_id, st.shift_number, targets.stop_kind(st.kind, st.reason), st.place, st.reason, float(st.duration_sec),
                          st.start, st.end, st.comment] for st in sorted(families["stops"], key=lambda st: st.start)]
        return Analysis.from_rows(day, self.day_version(day, corrupt), shift_rows, setup_rows, stop_rows, order_rows, reject_rows,
                                  errors=errors, store=self)

    def day_version(self, production_day, corrupt=False):
        """The dataset version of one production day's records, clean or with the bad-trim record."""
        return hashlib.sha256(f"{self.content_hash}|{production_day}|{'corrupt' if corrupt else 'clean'}".encode()).hexdigest()

    # ------------------------------------------------------------ fixed reads

    def _fetch(self, sql, params, limit=ROW_LIMIT):
        """Run one fixed read with bound values under the same bounds as Analysis."""
        with self._lock:
            timer = Timer(2.0, self._db.interrupt)
            timer.daemon = True
            timer.start()
            try:
                result = self._db.execute(sql, params)
                columns = [c[0] for c in result.description]
                fetched = result.fetchmany(limit + 1)
                if len(fetched) > limit:
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
        """Raw numerators and denominators of one window; KPIs are ratios of these.
        The tiles are sums of the precomputed `shift_kpis` rows (issue #34); only the
        reject sub-reasons (`details`) are read from the raw reject lines."""
        clause, extra = self._shift_clause(shift)
        params = [window["start"], window["end"], *extra]
        queries = {
            "kpis": "SELECT count(*) AS shift_records, coalesce(sum(setups),0) AS setup_records, "
                    "coalesce(list_unique(flatten(list(wet_end_ids))),0) AS paper_changes, "
                    "coalesce(sum(lineal_ft),0) AS lineal_ft, coalesce(sum(gross_sqft),0) AS gross_sqft, "
                    "coalesce(sum(trim_sqft),0) AS trim_sqft, coalesce(sum(shear_sqft),0) AS shear_sqft, "
                    "coalesce(sum(reject_sqft),0) AS reject_sqft, coalesce(sum(elapsed_seconds),0) AS elapsed_seconds, "
                    "coalesce(sum(setup_stop_seconds),0) AS setup_stop_seconds, "
                    "count(DISTINCT production_date) FILTER (WHERE setups > 0) AS production_days, "
                    "coalesce(sum(scheduled_seconds),0) AS scheduled_seconds, coalesce(sum(bale_sqft),0) AS bale_sqft, "
                    "coalesce(sum(stops),0) AS stop_count, coalesce(sum(stop_seconds),0) AS stop_seconds, "
                    "coalesce(sum(maintenance_seconds + planned_maintenance_seconds),0) AS maintenance_pm_seconds, "
                    "coalesce(sum(operator_seconds),0) AS operator_seconds, "
                    "coalesce(sum(bond_sqft),0) AS bond_sqft, coalesce(sum(warp_sqft),0) AS warp_sqft, "
                    "coalesce(sum(misalignment_sqft),0) AS misalignment_sqft, coalesce(sum(moisture_sqft),0) AS moisture_sqft, "
                    "coalesce(sum(other_sqft),0) AS other_sqft, "
                    "coalesce(sum(changeover_sqft),0) AS changeover_sqft, coalesce(sum(admin_sqft),0) AS admin_sqft, "
                    "coalesce(sum(cutoff_waste_sqft),0) AS cutoff_waste_sqft, coalesce(sum(stacker_waste_sqft),0) AS stacker_waste_sqft "
                    f"FROM shift_kpis WHERE NOT corrupt AND production_date BETWEEN ? AND ?{clause}",
            # the dry-end families' reported reasons: `reason` is the family, `detail` the reported reason
            "details": ("SELECT family AS reason, reason AS detail, sum(sqft) AS sqft, sum(sheets) AS sheets FROM rejects "
                        f"WHERE counted_in = 'dry_end' AND production_date BETWEEN ? AND ?{clause} "
                        "GROUP BY family, reason ORDER BY 3 DESC, 1, 2"),
        }
        sums = {}
        for name, sql in queries.items():
            rows = self._fetch(sql, params)
            trace["sql"].append(sql)
            trace["parameters"].append(params)
            sums[name] = rows
        total = {**sums["kpis"][0], "details": sums["details"]}
        total["source_rows"] = {"setups": total["setup_records"], "shifts": total["shift_records"],
                                "stops": total["stop_count"], "rejects": len({d["reason"] for d in sums["details"]})}
        return total

    @staticmethod
    def _kpis(t):
        gross, scheduled = t["gross_sqft"], t["scheduled_seconds"]
        net = gross - t["trim_sqft"] - t["shear_sqft"]   # the yield after trim and shear: dry-end waste's denominator
        running_minutes = (t["elapsed_seconds"] - t["setup_stop_seconds"]) / 60
        accounted = accounted_sqft(t)
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
            "dry_end_pct": _ratio(t["reject_sqft"], net, 100),
            "bond_pct": _ratio(t["bond_sqft"], net, 100),
            "warp_pct": _ratio(t["warp_sqft"], net, 100),
            "misalignment_pct": _ratio(t["misalignment_sqft"], net, 100),
            "moisture_pct": _ratio(t["moisture_sqft"], net, 100),
            "other_pct": _ratio(t["other_sqft"], net, 100),
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
            row = {"metric": key, "label": label, "unit": unit, "current": now, "previous": before,
                   "delta": delta, "better": better, "change": change}
            if key in targets.KPI_BANDS:   # the owner's band on the current value (issue #101)
                targets.mark(row, targets.KPI_BANDS[key], now)
            rows.append(row)
        return rows

    @staticmethod
    def _day_basis(current, previous):
        """How many production days each window holds. Unequal counts (a 21-day August against a
        22-day September, a 5-week 4-4-5 period against a 4-week one) make raw totals incomparable;
        the per-day and ratio tiles stay comparable, and the note says so."""
        now = current["production_days_observed"]
        before = previous["production_days_observed"] if previous else None
        equal = None if before is None else now == before
        if equal is None:
            note = f"{now} production days; no comparison window."
        elif equal:
            note = f"Both windows hold {now} production days; raw totals are comparable."
        else:
            note = (f"Unequal production days: {now} vs {before}. Compare volume per production day (MSF/day) and the "
                    "ratio tiles (speed, downtime %, waste %, minutes per stop); raw volume and lineal totals are not like for like.")
        return {"production_days_current": now, "production_days_previous": before, "equal_production_days": equal, "day_basis_note": note}

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
                              comparison="complete" if comparable else "withheld: a window is incomplete or unavailable",
                              current_sums=_tile_sums(now), **self._day_basis(current, previous))

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
                              comparison="complete" if comparable else "withheld: a window is incomplete or unavailable",
                              current_sums=_tile_sums(now), **self._day_basis(current, previous))

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
        """Every waste line of a window as square feet and percent of gross, previous window alongside:
        trim; shear and the changeover rejects counted with it; the cutoff-knife and stacker waste boards;
        the five dry-end families (bond, warp, misalignment, moisture, other) and their total; planning / admin
        entries apart; the bale and its unexplained remainder (WASTE_CATEGORIES)."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        previous = self._previous(current)
        now, before = self._sums(current, shift, trace), self._sums(previous, shift, trace)

        def areas(t):
            return {"trim": t["trim_sqft"], "shear": t["shear_sqft"], "changeover": t["changeover_sqft"],
                    "cutoff_waste": t["cutoff_waste_sqft"], "stacker_waste": t["stacker_waste_sqft"], "bond": t["bond_sqft"],
                    "warp": t["warp_sqft"], "misalignment": t["misalignment_sqft"], "moisture": t["moisture_sqft"], "other": t["other_sqft"],
                    "dry_end": t["reject_sqft"],
                    "admin": t["admin_sqft"], "bale": t["bale_sqft"], "unexplained": t["bale_sqft"] - accounted_sqft(t)}
        a, b = areas(now), areas(before)
        available = previous["coverage"] != "unavailable"
        rows = []
        net, prior_net = (t["gross_sqft"] - t["trim_sqft"] - t["shear_sqft"] for t in (now, before))
        for key, label in WASTE_CATEGORIES:
            pct, prior = _ratio(a[key], now["gross_sqft"], 100), _ratio(b[key], before["gross_sqft"], 100) if available else None
            row = {"category": key, "label": label, "sqft": round(a[key], 1), "pct_of_gross": pct,
                   "previous_sqft": round(b[key], 1) if available else None, "previous_pct_of_gross": prior,
                   "delta_pct_points": round(pct - prior, 4) if pct is not None and prior is not None else None}
            if key in DRY_END_CATEGORIES:   # dry-end waste is also read against the yield after trim and shear (issue #101)
                row["pct_of_yield"] = _ratio(a[key], net, 100)
                row["previous_pct_of_yield"] = _ratio(b[key], prior_net, 100) if available else None
            if key in WASTE_BANDS:
                targets.mark(row, WASTE_BANDS[key], row["pct_of_yield"] if key == "dry_end" else pct)
            rows.append(row)
        source = {"current": now["source_rows"], "previous": before["source_rows"]}
        details = [{"reason": d["reason"], "detail": d["detail"], "sqft": round(d["sqft"], 1), "sheets": d["sheets"],
                    "pct_of_gross": _ratio(d["sqft"], now["gross_sqft"], 100),
                    "share_of_dry_end_pct": _ratio(d["sqft"], now["reject_sqft"], 100, places=1)} for d in now["details"]]
        # Good output is the saleable board: gross minus every recorded waste line (owner, 2026-11-12), so the
        # lines and it add back to gross; the bale and its unexplained remainder are a scale reading, not a line.
        lines = ("trim", "shear", "changeover", "cutoff_waste", "stacker_waste", "dry_end", "admin")
        good = round(now["gross_sqft"] - sum(a[k] for k in lines), 1)
        return self._envelope("get_waste_breakdown", current, previous, shift, rows, trace, started, source,
                              gross_sqft=round(now["gross_sqft"], 1), previous_gross_sqft=round(before["gross_sqft"], 1) if available else None,
                              good_output_sqft=good, good_output_pct=_ratio(good, now["gross_sqft"], 100),
                              yield_sqft=round(net, 1), details=details)

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

    def get_grade_totals(self, window, shift=None, measure="volume"):
        """Volume, lineal feet, speed and waste by board grade for one window,
        previous window's volume alongside. Ratios of sums within each grade.

        Grade-level speed can only use the setup records' own clocks: without
        stops = feet / (elapsed - setup-allocated stop seconds), elapsed = feet /
        elapsed seconds. Scheduled shift minutes carry no grade, so the tile
        table's "speed with stops" has no per-grade equivalent.

        `measure` chooses what the answer leads with and charts, never the rows:
        volume (MSF beside the previous window) or speed (ft/min without stops
        against each grade's target)."""
        if measure not in ("volume", "speed"):
            raise ValueError("measure is volume or speed")
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        previous = self._previous(current)
        clause, extra = self._shift_clause(shift)
        sql = ("SELECT quality_id AS grade, max(target) AS target, count(*) AS setups, count(DISTINCT wet_end_id) AS wet_ends, "
               "sum(lineal_ft) AS feet, sum(gross_sqft) AS gross, sum(seconds) AS seconds, sum(stop_seconds) AS stop_seconds, "
               "sum(stops) AS stops, sum(trim_sqft) AS trim, sum(shear_sqft) AS shear, sum(reject_sqft) AS rejects "
               f"FROM setups WHERE production_date BETWEEN ? AND ?{clause} GROUP BY quality_id ORDER BY gross DESC, grade")
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
                         "trim_pct": _ratio(r["trim"], r["gross"], 100), "dry_end_pct": _ratio(r["rejects"], r["gross"] - r["trim"] - r["shear"], 100),
                         "previous_volume_msf": prior_msf,
                         "delta_volume_msf": round(r["gross"] / 1000 - prior_msf, 1) if prior_msf is not None else None})
        source = {"current": {"setups": sum(r["setups"] for r in fetched["current"])},
                  "previous": {"setups": sum(r["setups"] for r in fetched["previous"])} if available else None}
        return self._envelope("get_grade_totals", current, previous, shift, rows, trace, started, source,
                              total_volume_msf=round(total_gross / 1000, 1), measure=measure,
                              speed_note="Grade speeds come from the setup records' own elapsed clocks; "
                                         "scheduled shift minutes carry no grade, so there is no per-grade speed with stops.")

    def get_rollstock(self, window, shift=None):
        """Paper consumed in one window, from the wet-end (paper run) records: lineal feet and
        MSF per board grade and web width, paper runs and feet per run, the previous window of
        the same kind alongside. MSF is the paper footprint, lineal feet x web width, the same
        area the board records carry; the records hold no basis weight, so there are no tons.

        All shifts: whole paper runs, each on the production day its first setup started. One
        shift: the setups that shift ran on each run, so the feet are the setup feet of that
        shift and a run that spans two shifts is counted in both."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        previous = self._previous(current)
        _, extra = self._shift_clause(shift)
        if shift is None:
            sql = ("SELECT quality_id AS grade, web_width_in AS width_in, count(*) AS paper_runs, coalesce(sum(lineal_ft),0) AS feet, "
                   "coalesce(sum(gross_sqft),0) AS gross FROM wet_ends WHERE production_date BETWEEN ? AND ? GROUP BY quality_id, web_width_in")
        else:
            sql = ("SELECT w.quality_id AS grade, w.web_width_in AS width_in, count(DISTINCT s.wet_end_id) AS paper_runs, "
                   "coalesce(sum(s.lineal_ft),0) AS feet, coalesce(sum(s.gross_sqft),0) AS gross "
                   "FROM setups s JOIN wet_ends w ON w.wet_end_id = s.wet_end_id "
                   "WHERE s.production_date BETWEEN ? AND ? AND s.shift = ? GROUP BY w.quality_id, w.web_width_in")
        fetched = {}
        for name, win in (("current", current), ("previous", previous)):
            params = [win["start"], win["end"], *extra]
            fetched[name] = {(r["grade"], r["width_in"]): r for r in self._fetch(sql, params)}
            trace["sql"].append(sql)
            trace["parameters"].append(params)
        available = previous["coverage"] != "unavailable"

        def totals(cells):
            return {"paper_runs": sum(r["paper_runs"] for r in cells.values()),
                    "feet": sum(r["feet"] for r in cells.values()), "gross": sum(r["gross"] for r in cells.values())}
        now, before = totals(fetched["current"]), totals(fetched["previous"])
        ordered = sorted(fetched["current"].values(), key=lambda r: (-r["feet"], r["grade"], r["width_in"]))
        rows = []
        for r in ordered:
            prior = fetched["previous"].get((r["grade"], r["width_in"])) if available else None
            prior_feet = round(prior["feet"], 1) if prior else (0.0 if available else None)
            prior_msf = round(prior["gross"] / 1000, 1) if prior else (0.0 if available else None)
            feet, msf = round(r["feet"], 1), round(r["gross"] / 1000, 1)
            rows.append({"grade": r["grade"], "width_in": r["width_in"], "label": f"{r['grade']} {r['width_in']:g} in",
                         "paper_runs": r["paper_runs"], "lineal_ft": feet, "msf": msf,
                         "share_of_feet_pct": _ratio(r["feet"], now["feet"], 100),
                         "lineal_per_run": _ratio(r["feet"], r["paper_runs"], places=1),
                         "previous_paper_runs": prior["paper_runs"] if prior else (0 if available else None),
                         "previous_lineal_ft": prior_feet, "previous_msf": prior_msf,
                         "delta_lineal_ft": round(feet - prior_feet, 1) if prior_feet is not None else None,
                         "delta_msf": round(msf - prior_msf, 1) if prior_msf is not None else None})
        total_feet, total_msf = round(now["feet"], 1), round(now["gross"] / 1000, 1)
        prev_feet = round(before["feet"], 1) if available else None
        prev_msf = round(before["gross"] / 1000, 1) if available else None
        source = {"current": {"wet_ends": now["paper_runs"]}, "previous": {"wet_ends": before["paper_runs"]} if available else None}
        attribution = ("Whole paper runs, each on the production day its first setup started." if shift is None else
                       f"The setups shift {shift} ran on each paper run: a run that spans two shifts is counted in both, "
                       "and its feet are split by the setups each shift ran.")
        return self._envelope("get_rollstock", current, previous, shift, rows, trace, started, source,
                              total_lineal_ft=total_feet, total_msf=total_msf, paper_runs=now["paper_runs"],
                              lineal_per_run=_ratio(now["feet"], now["paper_runs"], places=1),
                              previous_total_lineal_ft=prev_feet, previous_total_msf=prev_msf,
                              previous_paper_runs=before["paper_runs"] if available else None,
                              delta_lineal_ft=round(total_feet - prev_feet, 1) if prev_feet is not None else None,
                              delta_msf=round(total_msf - prev_msf, 1) if prev_msf is not None else None,
                              attribution_note=attribution,
                              msf_note="MSF is the paper footprint, lineal feet times web width in thousands of square feet; the records carry no basis weight, so tons and cost are not computed.")

    def get_overrun(self, window, shift=None):
        """Over/under-run by knife: actual cuts against nominal cuts for every
        customer line of the window, as cuts, sheets (cuts x outs) and percent of
        nominal, plus the share of lines inside the +-band, over it and under it.
        The last row is all knives together."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        clause, extra = self._shift_clause(shift)
        sql = ("SELECT cutoff_knife AS knife, count(*) AS lines, sum(nominal_cuts) AS nominal_cuts, sum(actual_cuts) AS actual_cuts, "
               "sum(nominal_cuts*outs) AS nominal_sheets, sum(actual_cuts*outs) AS actual_sheets, "
               "sum(CASE WHEN actual_cuts > nominal_cuts THEN (actual_cuts-nominal_cuts)*outs ELSE 0 END) AS over_sheets, "
               "sum(CASE WHEN actual_cuts < nominal_cuts THEN (nominal_cuts-actual_cuts)*outs ELSE 0 END) AS under_sheets, "
               "sum(CASE WHEN (actual_cuts-nominal_cuts)*100.0/nominal_cuts > ? THEN 1 ELSE 0 END) AS lines_over, "
               "sum(CASE WHEN (actual_cuts-nominal_cuts)*100.0/nominal_cuts < -? THEN 1 ELSE 0 END) AS lines_under "
               f"FROM setup_orders WHERE production_date BETWEEN ? AND ?{clause} AND nominal_cuts > 0 GROUP BY cutoff_knife ORDER BY cutoff_knife")
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

    def get_speed_opportunity(self, window, slice_by="width", shift=None):
        """Best-demonstrated versus typical running speed, by grade and web width (or crew).

        For every setup of the window with at least SPEED_MIN_RUN_MINUTES of running time:
        speed without stops = feet / (elapsed - setup-allocated stop seconds). Per cell:
        typical = ratio of sums over all included setups; median = the median setup speed;
        best fifth = ratio of sums over the fastest fifth of setups (ntile 5 by speed);
        opportunity hours = running minutes now minus the minutes the same feet would take at
        the best-fifth speed, / 60. Beside it, the dry-end reject percent of that fastest fifth
        against the rest: a gain that costs bond or warp shows as a positive reject penalty.
        Peak (max) speeds are never used. Records carry no temperatures, so the heat limit on
        heavy grades and doublewall stays an open owner input; the envelope says so."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        if slice_by not in _SPEED_SLICES:
            raise ValueError("slice_by must be width or crew")
        current = self._window(window)
        clause, extra = self._shift_clause(shift)
        slice_sql = "s.web_width_in" if slice_by == "width" else "sh.crew"
        sql = (f"WITH base AS (SELECT s.quality_id AS grade, {slice_sql} AS slice, s.target, s.lineal_ft AS feet, "
               "s.gross_sqft - s.trim_sqft - s.shear_sqft AS net, s.reject_sqft AS rejects, "
               "(s.seconds - s.stop_seconds) / 60 AS run_min, s.lineal_ft / ((s.seconds - s.stop_seconds) / 60) AS fpm "
               "FROM setups s JOIN shifts sh ON sh.production_date = s.production_date AND sh.shift = s.shift "
               f"WHERE s.production_date BETWEEN ? AND ?{clause.replace(' shift', ' s.shift')} AND s.seconds - s.stop_seconds >= ? * 60), "
               "ranked AS (SELECT *, ntile(5) OVER (PARTITION BY grade, slice ORDER BY fpm DESC) AS fifth FROM base) "
               "SELECT grade, slice, max(target) AS target, count(*) AS setups, sum(feet) AS feet, sum(run_min) AS run_min, "
               "quantile_cont(fpm, 0.5) AS median_fpm, "
               "sum(CASE WHEN fifth = 1 THEN feet END) AS best_feet, sum(CASE WHEN fifth = 1 THEN run_min END) AS best_run_min, "
               "sum(CASE WHEN fifth = 1 THEN rejects END) AS best_rejects, sum(CASE WHEN fifth = 1 THEN net END) AS best_net, "
               "sum(CASE WHEN fifth > 1 THEN rejects END) AS rest_rejects, sum(CASE WHEN fifth > 1 THEN net END) AS rest_net "
               "FROM ranked GROUP BY grade, slice ORDER BY grade, slice")
        params = [current["start"], current["end"], *extra, SPEED_MIN_RUN_MINUTES]
        fetched = self._fetch(sql, params)
        trace["sql"].append(sql)
        trace["parameters"].append(params)
        short_sql = (f"SELECT count(*) AS short FROM setups WHERE production_date BETWEEN ? AND ?{clause} AND seconds - stop_seconds < ? * 60")
        short = self._fetch(short_sql, params)[0]["short"]
        trace["sql"].append(short_sql)
        trace["parameters"].append(params)
        rows = []
        for r in fetched:
            enough = r["setups"] >= SPEED_MIN_SETUPS
            typical = r["feet"] / r["run_min"]
            best = r["best_feet"] / r["best_run_min"] if enough and r["best_run_min"] else None
            best_pct = _ratio(r["best_rejects"], r["best_net"], 100) if enough else None   # dry-end over yield (issue #101)
            rest_pct = _ratio(r["rest_rejects"], r["rest_net"], 100) if enough else None
            rows.append({"grade": r["grade"], "slice_by": slice_by,
                         "slice": (f"{r['slice']:g} in" if slice_by == "width" else r["slice"]),
                         "label": f"{r['grade']} · " + (f"{r['slice']:g} in" if slice_by == "width" else r["slice"]),
                         "target_fpm": r["target"], "setups": r["setups"], "lineal_ft": round(r["feet"], 1),
                         "run_minutes": round(r["run_min"], 1),
                         "typical_fpm": round(typical, 1), "median_setup_fpm": round(float(r["median_fpm"]), 1),
                         "best_fifth_fpm": round(best, 1) if best else None,
                         "gap_fpm": round(best - typical, 1) if best else None,
                         "typical_pct_of_target": _ratio(typical, r["target"], 100, places=1),
                         "best_fifth_pct_of_target": _ratio(best, r["target"], 100, places=1) if best else None,
                         "opportunity_hours": round((r["run_min"] - r["feet"] / best) / 60, 2) if best else None,
                         "best_fifth_dry_end_pct": best_pct, "rest_dry_end_pct": rest_pct,
                         "reject_penalty_points": round(best_pct - rest_pct, 4) if best_pct is not None and rest_pct is not None else None,
                         "status": "ok" if enough else f"insufficient: fewer than {SPEED_MIN_SETUPS} setups"})
        # Cells too thin for a best fifth are listed beside the rows, never charted or ranked.
        thin = [{"label": x["label"], "grade": x["grade"], "slice": x["slice"], "setups": x["setups"]} for x in rows if x["status"] != "ok"]
        rows = [x for x in rows if x["status"] == "ok"]
        rows.sort(key=lambda x: (-x["opportunity_hours"], x["grade"], x["slice"]))
        total = round(sum(x["opportunity_hours"] for x in rows), 2)
        source = {"setups_included": sum(r["setups"] for r in fetched), "setups_excluded_short": short}
        return self._envelope("get_speed_opportunity", current, None, shift, rows, trace, started, source,
                              min_run_minutes=SPEED_MIN_RUN_MINUTES, min_setups=SPEED_MIN_SETUPS,
                              excluded_short_setups=short, total_opportunity_hours=total, thin_cells=thin,
                              method_note=("Best fifth = the fastest fifth of the cell's setups by speed without stops, as a ratio of sums; "
                                           "opportunity hours = running minutes now minus the minutes the same feet take at the best-fifth speed. "
                                           "Peak speeds are never used. A positive reject penalty means the fast setups rejected more than the rest: "
                                           "that part of the gap is a limit, not an opportunity."),
                              temperature_note=("Records carry no temperatures: the heat-transfer limit on heavy grades and doublewall (bond needs its "
                                                "temperature range) is an open owner input and is not modelled here."))

    def _speed_profile(self, window, shift, trace, baselines):
        """The raw sums one side of a speed variance needs: scheduled minutes, feet, and per grade
        its baseline (each grade's sustained speed over the trailing year, `baselines`; the owner's
        speed standard, issue #101), setup clock and running time; per stop reason and grade the
        stopped minutes and the feet the grade's baseline would have made in them. Stops are joined
        to their setup record, whose own clock already holds them, so a stop counts once and at its
        own grade's baseline. A grade with no sustained run in the trailing year falls back to its
        target and is listed (`fallback`); every grade the records hold has one."""
        clause, extra = self._shift_clause(shift)
        params = [window["start"], window["end"], *extra]
        queries = {
            "scheduled": f"SELECT count(*) AS shifts, coalesce(sum(scheduled_minutes), 0) AS minutes FROM shifts WHERE production_date BETWEEN ? AND ?{clause}",
            "grades": ("SELECT quality_id AS grade, max(target) AS target, count(*) AS setups, sum(lineal_ft) AS feet, sum(seconds) / 60 AS elapsed_min, "
                       "sum(seconds - stop_seconds) / 60 AS run_min "
                       f"FROM setups WHERE production_date BETWEEN ? AND ?{clause} GROUP BY quality_id ORDER BY quality_id"),
            "stops": ("SELECT st.kind AS kind, st.reason AS reason, s.quality_id AS grade, count(*) AS stops, sum(st.seconds) / 60 AS minutes "
                      "FROM stops st JOIN setups s ON s.setup_id = st.setup_id "
                      f"WHERE s.production_date BETWEEN ? AND ?{clause.replace(' shift', ' s.shift')} "
                      "GROUP BY st.kind, st.reason, s.quality_id ORDER BY st.kind, st.reason, s.quality_id"),
        }
        fetched = {}
        for name, sql in queries.items():
            fetched[name] = self._fetch(sql, params)
            trace["sql"].append(sql)
            trace["parameters"].append(params)
        minutes = float(fetched["scheduled"][0]["minutes"])
        grades = {r["grade"]: r for r in fetched["grades"]}
        for g, r in grades.items():
            base = baselines["grades"].get(g)
            r["speed"] = base["baseline_fpm"] if base else r["target"]
        stops = [{**r, "base_ft": r["minutes"] * grades[r["grade"]]["speed"]} for r in fetched["stops"]]
        return {"minutes": minutes, "shifts": fetched["scheduled"][0]["shifts"], "feet": sum(r["feet"] for r in grades.values()),
                "grades": grades, "stops": stops, "setups": sum(r["setups"] for r in grades.values()),
                "fallback": sorted(g for g in grades if g not in baselines["grades"])}

    def _standard_profile(self, trace, baselines):
        """The baseline side of a speed variance: each grade's baseline at the usual grade mix, the share of
        the setup clock every grade took over the same trailing year (all shifts). A window that ran more of a
        slow grade than usual is below this speed even if every grade ran at its baseline: that part of the gap
        is the mix, which the order book decides (schedule-driven, issue #101)."""
        sql = ("SELECT quality_id AS grade, max(target) AS target, sum(seconds) / 60 AS elapsed_min FROM setups "
               "WHERE production_date BETWEEN ? AND ? GROUP BY quality_id ORDER BY quality_id")
        params = [baselines["since"], baselines["through"]]
        rows = self._fetch(sql, params)
        trace["sql"].append(sql)
        trace["parameters"].append(params)
        for r in rows:
            base = baselines["grades"].get(r["grade"])
            r["speed"] = base["baseline_fpm"] if base else r["target"]
        total = sum(r["elapsed_min"] for r in rows)
        return {"total": total, "grades": {r["grade"]: r for r in rows}}

    @staticmethod
    def _speed_side(p):
        """One side's speeds and losses per scheduled minute (ft/min): the speed with stops, the speed the
        grade baselines give at its own mix, and the feet below that baseline lost to each stop reason, to
        order changes (by grade) and to running under the grade's baseline (by grade). By construction
        baseline speed - stops - setups - running under baseline = speed with stops, exactly, for any
        per-grade speed: each setup's clock is its running time plus its stops."""
        t = p["minutes"]
        grades = p["grades"]
        stops, planned, external, setups = {}, {}, {}, {}
        for r in p["stops"]:
            if r["reason"] == SPEEDVAR_SETUP_REASON:
                cell = setups.setdefault(r["grade"], {"minutes": 0.0, "stops": 0, "loss": 0.0})
            else:   # by kind: planned maintenance and external stops apart from the unplanned ones (PR #111)
                bucket = planned if r["kind"] == "Scheduled" else external if r["kind"] == targets.EXTERNAL_KIND else stops
                cell = bucket.setdefault(r["reason"], {"minutes": 0.0, "stops": 0, "loss": 0.0})
            cell["minutes"] += r["minutes"]
            cell["stops"] += r["stops"]
            cell["loss"] += r["base_ft"] / t
        return {"speed": p["feet"] / t, "base": sum(g["speed"] * g["elapsed_min"] for g in grades.values()) / t,
                "share": {k: g["elapsed_min"] / t for k, g in grades.items()},
                "speeds": {k: g["speed"] for k, g in grades.items()},
                "stops": stops, "planned": planned, "external": external, "setups": setups,
                "below": {k: {"loss": (g["speed"] * g["run_min"] - g["feet"]) / t, "run_min": g["run_min"], "feet": g["feet"]}
                          for k, g in grades.items()}}

    def get_speed_variance(self, window, shift=None, against="baseline", against_shift=None):
        """Why speed differed from a reference, in buckets that add up to the gap (issue #92): stops, setups,
        running under baseline and the grade mix, and planned maintenance and external stops when either side has one.

        Every grade is held at its baseline, the owner's speed standard (issue #101, targets.json): its
        sustained average speed over the trailing year ending on the window's last day, never its target
        or its maximum. Speed is the KPI tile's speed with stops: lineal feet / scheduled minutes. For
        each side, per scheduled minute: baseline speed (each grade's baseline x its setup clock) - stops
        (minutes stopped x the stopped setup's grade baseline, every unplanned reason but order change; planned
        maintenance and external stops the same way, each its own bucket) - setups
        (order-change stops, the same way) - running under baseline (baseline x running minutes - feet,
        per grade) = speed with stops, exactly, because each setup record's clock is its running time
        plus its stops.

        `against`: baseline = each grade's baseline at the usual mix (each grade's share of the setup
        clock over the same trailing year), with no losses; previous = the same window kind just before,
        same shift filter; shift = `against_shift` over the same window (`shift` is then the one
        explained). Both sides are held at the same baselines. The gap = reference speed - this speed =
        mix (reference baseline speed - this baseline speed; schedule-driven: the order book decides it)
        + each loss bucket (this loss - the reference's), in ft/min and as lineal feet over this window's
        scheduled minutes. `details` splits every bucket by stop reason or grade and adds up to it."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        against = SPEEDVAR_ALIASES.get(against, against)
        if against not in SPEEDVAR_AGAINST:
            raise ValueError("against is baseline, previous or shift")
        if against == "shift":
            if type(shift) is not int or type(against_shift) is not int or shift not in (1, 2, 3) or against_shift not in (1, 2, 3):
                raise ValueError("against=shift compares one shift with another: give shift and against_shift (1, 2 or 3)")
            if shift == against_shift:
                raise ValueError("against_shift must be another shift than shift")
        elif against_shift is not None:
            raise ValueError("against_shift is only for against=shift")
        current = self._window(window)
        if current["coverage"] == "unavailable":
            raise ValueError(f"No production records in {current['label']}: there is no speed to decompose")
        baselines = self.grade_speed_baselines(current["end"])
        trace["sql"].append(baselines["sql"])
        trace["parameters"].append(baselines["parameters"])
        now = self._speed_profile(current, shift, trace, baselines)
        if not now["minutes"] or not now["setups"]:
            raise ValueError(f"No production records in {current['label']}{f' for shift {shift}' if shift else ''}: there is no speed to decompose")
        c = self._speed_side(now)
        previous, fallback = None, set(now["fallback"])
        if against == "baseline":
            standard = self._standard_profile(trace, baselines)
            grades = standard["grades"]
            fallback |= {g for g in grades if g not in baselines["grades"]}
            reference_base = sum(g["speed"] * g["elapsed_min"] for g in grades.values()) / standard["total"]
            r = {"speed": reference_base, "base": reference_base, "share": {k: g["elapsed_min"] / standard["total"] for k, g in grades.items()},
                 "speeds": {k: g["speed"] for k, g in grades.items()}, "stops": {}, "planned": {}, "external": {}, "setups": {}, "below": {}}
            reference_minutes = None
            reference_label = "baseline: each grade's trailing-year sustained speed at the usual grade mix"
        else:
            if against == "previous":
                previous = self._previous(current)
                if previous["coverage"] == "unavailable":
                    raise ValueError(f"The previous window, {previous['label']}, is outside the record store: ask against the baseline instead")
                base = self._speed_profile(previous, shift, trace, baselines)
                reference_label = f"the previous window, {previous['label']}"
            else:
                base = self._speed_profile(current, against_shift, trace, baselines)
                reference_label = f"shift {against_shift}"
            if not base["minutes"] or not base["setups"]:
                raise ValueError(f"No production records for the reference, {reference_label}: there is nothing to compare with")
            r = self._speed_side(base)
            fallback |= set(base["fallback"])
            reference_minutes = base["minutes"]
        minutes = now["minutes"]
        gap = r["speed"] - c["speed"]
        speeds = {**r["speeds"], **c["speeds"]}

        def item(bucket, name, value, **extra):
            return {"bucket": bucket, "item": name, "gap_fpm": value, **extra}
        details = {"stops": [], "planned": [], "external": [], "setups": [], "below_baseline": [], "mix": []}
        for key in ("stops", "planned", "external"):
            for reason in sorted(set(c[key]) | set(r[key])):
                cur, ref = c[key].get(reason, {}), r[key].get(reason, {})
                details[key].append(item(key, reason, cur.get("loss", 0.0) - ref.get("loss", 0.0),
                                         current_loss_fpm=cur.get("loss", 0.0), reference_loss_fpm=ref.get("loss", 0.0),
                                         minutes=round(cur.get("minutes", 0.0), 2), stops=cur.get("stops", 0)))
        for grade in sorted(set(c["setups"]) | set(r["setups"])):
            cur, ref = c["setups"].get(grade, {}), r["setups"].get(grade, {})
            details["setups"].append(item("setups", grade, cur.get("loss", 0.0) - ref.get("loss", 0.0),
                                          current_loss_fpm=cur.get("loss", 0.0), reference_loss_fpm=ref.get("loss", 0.0),
                                          minutes=round(cur.get("minutes", 0.0), 2), stops=cur.get("stops", 0)))
        for grade in sorted(set(c["below"]) | set(r["below"])):
            cur, ref = c["below"].get(grade), r["below"].get(grade)
            run_fpm = cur["feet"] / cur["run_min"] if cur and cur["run_min"] else None
            details["below_baseline"].append(item("below_baseline", grade, (cur["loss"] if cur else 0.0) - (ref["loss"] if ref else 0.0),
                                                  current_loss_fpm=cur["loss"] if cur else 0.0, reference_loss_fpm=ref["loss"] if ref else 0.0,
                                                  speed_run_fpm=_round(run_fpm, 1), baseline_fpm=speeds[grade],
                                                  speed_to_baseline_pct=_ratio(run_fpm, speeds[grade], 100, places=1)))
        for grade in sorted(set(c["share"]) | set(r["share"])):
            now_share, ref_share = c["share"].get(grade, 0.0), r["share"].get(grade, 0.0)
            details["mix"].append(item("mix", grade, (ref_share - now_share) * (speeds[grade] - r["base"]),
                                       time_share_pct=round(now_share * 100, 2), reference_time_share_pct=round(ref_share * 100, 2),
                                       baseline_fpm=speeds[grade]))
        totals = {"stops": sum(x["gap_fpm"] for x in details["stops"]), "planned": sum(x["gap_fpm"] for x in details["planned"]),
                  "external": sum(x["gap_fpm"] for x in details["external"]), "setups": sum(x["gap_fpm"] for x in details["setups"]),
                  "below_baseline": sum(x["gap_fpm"] for x in details["below_baseline"]), "mix": r["base"] - c["base"]}
        sharable = abs(gap) >= SPEEDVAR_SHARE_MIN_FPM
        direction = 1 if gap >= 0 else -1

        def finish(entry):
            value = entry["gap_fpm"]
            entry["gap_fpm"] = round(value, 2)
            entry["gap_lineal_ft"] = round(value * minutes, 1)
            entry["share_of_gap_pct"] = round(value / gap * 100, 1) if sharable else None
            for key in ("current_loss_fpm", "reference_loss_fpm"):
                if key in entry:
                    entry[key] = round(entry[key], 2)
            return entry
        rows = []
        for key, label in SPEEDVAR_BUCKETS:
            if key in SPEEDVAR_OPTIONAL and not details[key]:
                continue   # no planned or external stop on either side: no bucket
            items = sorted(details[key], key=lambda x: (x["gap_fpm"] * direction < 0, -abs(x["gap_fpm"]), x["item"]))
            details[key] = items
            # the bucket's top item pulls the way the bucket does: the largest loss of a loss, the largest offset of an offset
            way = 1 if totals[key] >= 0 else -1
            leading = sorted((x for x in items if x["gap_fpm"] * way > 0), key=lambda x: (-x["gap_fpm"] * way, x["item"]))
            top = leading[0] if leading else None
            # a loss bucket carries each side's loss rate; the mix is a difference of baseline speeds, in the envelope
            losses = {} if key == "mix" else {"current_loss_fpm": sum(x["current_loss_fpm"] for x in items),
                                              "reference_loss_fpm": sum(x["reference_loss_fpm"] for x in items)}
            # the grade mix is the order book's and planned stops the maintenance plan's (schedule-driven, issue #101 and
            # PR #111), a power outage is outside the plant (external); the other losses are the crew's
            control = SPEEDVAR_CONTROL.get(key, SPEEDVAR_CREW)
            rows.append({"bucket": key, "label": label, "gap_fpm": totals[key], **losses,
                         "items": len(items), "top_item": top["item"] if top else None,
                         "top_item_fpm": round(top["gap_fpm"], 2) if top else None,
                         "control": control[0], "control_note": control[1]})
        # the buckets that make the gap first, largest first; then the offsets, largest first
        rows.sort(key=lambda x: (x["gap_fpm"] * direction < 0, -abs(x["gap_fpm"])))
        order = [x["bucket"] for x in rows]
        rows = [finish(x) for x in rows]
        flat = [finish(x) for key in order for x in details[key]]
        shares = {f"{x['bucket']}_share_pct": x["share_of_gap_pct"] for x in rows}
        source = {"current": {"setups": now["setups"], "shifts": now["shifts"]},
                  "reference": {"setups": base["setups"], "shifts": base["shifts"]} if against != "baseline" else {"store_grades": len(r["share"])}}
        note = ("Speed is lineal feet over scheduled minutes (the KPI tile's speed with stops). Each grade is held at its baseline, "
                "its sustained average speed over the trailing year, never its target or its maximum. Each loss is feet below the "
                "baseline per scheduled minute: stopped minutes at the stopped setup's baseline (order changes are the setup bucket), "
                "and running time at the baseline minus the feet made. The grade mix is the reference's baseline speed minus this "
                "window's: the same baselines weighted by each grade's share of the setup clock. The order mix decides it, so it is "
                "schedule-driven. Planned maintenance stops are a bucket of their own, schedule-driven; external stops (a power outage) "
                "are another, outside the plant and not controllable; the unplanned stops, setups and running under baseline are controllable. "
                "The buckets add up to the gap, and each bucket's details add up to it. The records carry one running speed per setup, so there is no separate ramp after a changeover.")
        reference_note = (f"Against the baseline, the reference is each grade's baseline weighted by its share of the setup clock over the "
                          f"same trailing year, all shifts: {r['base']:,.1f} ft/min. This window "
                          f"ran {c['speed']:,.1f} ft/min with stops; its grade baselines at its own mix make {c['base']:,.1f} ft/min."
                          if against == "baseline" else
                          f"Against {reference_label}: {r['speed']:,.1f} ft/min with stops, its grade baselines at its mix {r['base']:,.1f} ft/min; "
                          f"this window {c['speed']:,.1f} and {c['base']:,.1f} ft/min. Both are held at the same baselines, those of the "
                          "trailing year ending on this window's last day. Losses are per scheduled minute of each window, so windows of "
                          "different lengths compare like for like.")
        if fallback:
            reference_note += (" No sustained run in the trailing year for " + ", ".join(sorted(fallback)) +
                               ", so the grade target stands in for its baseline.")
        return self._envelope("get_speed_variance", current, previous, shift, rows, trace, started, source,
                              against=against, against_shift=against_shift, reference_label=reference_label,
                              speed_all_fpm=round(c["speed"], 1), reference_speed_fpm=round(r["speed"], 1),
                              baseline_speed_fpm=round(c["base"], 1), reference_baseline_speed_fpm=round(r["base"], 1),
                              baseline_since=baselines["since"], baseline_through=baselines["through"],
                              baseline_fallback=sorted(fallback),
                              gap_fpm=round(gap, 2), gap_lineal_ft=round(gap * minutes, 1),
                              direction="below" if gap > 0 else "above" if gap < 0 else "at",
                              scheduled_minutes=round(minutes, 1),
                              reference_scheduled_minutes=None if reference_minutes is None else round(reference_minutes, 1),
                              **shares, details=flat, method_note=note, reference_note=reference_note)

    # ------------------------------------------------------------ the owner's standards (issue #101)

    BASELINE_SQL = ("SELECT quality_id AS grade, count(*) AS setups, sum(lineal_ft) AS feet, sum(seconds - stop_seconds) / 60 AS run_min "
                    "FROM setups WHERE production_date BETWEEN ? AND ? AND seconds - stop_seconds >= ? * 60 "
                    "GROUP BY quality_id ORDER BY quality_id")

    def trailing_year(self, through):
        """(first, last) production dates of the trailing year ending on `through`: the 365 days ending on it,
        inside the record store (which starts on 1 January 2025, issue #100: a day in 2025 has a shorter trailing year)."""
        last = min(date.fromisoformat(str(through)[:10]), self._last)
        return max(last - timedelta(days=364), self._first), last

    def grade_speed_baselines(self, through):
        """Each grade's sustained average speed over the trailing year ending on `through` (targets.json, speed):
        lineal ft over running minutes (stops out) of every setup of the grade with at least
        SPEED_MIN_RUN_MINUTES of running time (shorter ones never reach steady speed), a ratio of sums.
        Never the maximum speed, which is a ramp-up point. Computed once per trailing year and kept."""
        first, last = self.trailing_year(through)
        cached = self._baselines.get((first, last))
        if cached is None:
            params = [first.isoformat(), last.isoformat(), SPEED_MIN_RUN_MINUTES]
            rows = self._fetch(self.BASELINE_SQL, params)
            cached = {"since": first.isoformat(), "through": last.isoformat(), "sql": self.BASELINE_SQL, "parameters": params,
                      "grades": {r["grade"]: {"baseline_fpm": round(r["feet"] / r["run_min"], 1), "setups": r["setups"],
                                              "run_minutes": round(r["run_min"], 1)} for r in rows if r["run_min"]}}
            self._baselines[(first, last)] = cached
        return cached

    def speed_baseline_gap(self, window, shift=None):
        """The speed under each grade's baseline over a window (issue #101): every wet-end run (paper run) of the
        window against its grade's sustained speed over the trailing year ending on the window's last day. A run
        under it has a gap: the feet it would have made at the baseline in its running minutes, less the feet it
        made; the minutes are that gap at the baseline speed. Returns the runs, totals and the trace parts."""
        current = self._window(window)
        clause, extra = self._shift_clause(shift)
        sql = ("SELECT production_date, wet_end_id, quality_id AS grade, sum(lineal_ft) AS feet, sum(seconds - stop_seconds) / 60 AS run_min "
               f"FROM setups WHERE production_date BETWEEN ? AND ?{clause} GROUP BY production_date, wet_end_id, quality_id "
               "ORDER BY production_date, wet_end_id")
        params = [current["start"], current["end"], *extra]
        runs = self._fetch(sql, params, limit=20000)
        baselines = self.grade_speed_baselines(current["end"] if current["coverage"] != "unavailable" else self.last_day)
        out, minutes, under = [], 0.0, 0
        for r in runs:
            base = baselines["grades"].get(r["grade"])
            if not base or not r["run_min"]:
                continue
            gap_ft = max(0.0, base["baseline_fpm"] * r["run_min"] - r["feet"])
            minutes += gap_ft / base["baseline_fpm"]
            under += gap_ft > 0
            out.append({"wet_end_id": r["wet_end_id"], "grade": r["grade"], "running_fpm": round(r["feet"] / r["run_min"], 1),
                        "baseline_fpm": base["baseline_fpm"], "gap_ft": round(gap_ft, 1)})
        return {"window": current, "runs": out, "runs_under": under, "gap_minutes": round(minutes, 2),
                "since": baselines["since"], "through": baselines["through"],
                "trace": {"sql": [sql, baselines["sql"]], "parameters": [params, baselines["parameters"]]}}

    # Downtime kinds the owner's bands rank (targets.json); the Scheduled kind is planned maintenance, scheduled time.
    RANKED_KINDS = ("Maintenance", "Operator")
    KIND_BANDS = {"Maintenance": "maintenance", "Operator": "operator"}

    def downtime_flags(self, start, end, shift=None, trace=None):
        """Every shift of [start, end] (one shift, or all) whose maintenance or operator downtime is over its band
        (2.5% of the shift's scheduled minutes), the largest first, with its crew (the supervisor slot), the reason
        that led it and that reason's recurrence over the trailing year ending on the flagged day (targets.json,
        recurrence): the shifts on which the same reason, in the same kind, was over the band on its own, and the
        Mon-Fri weeks in which it was among the five reasons with the most unplanned minutes. A shift-scoped call
        counts that shift's own history; an all-shift call the plant's. Returns (flags, count)."""
        clause, extra = self._shift_clause(shift)
        kinds = "', '".join(self.RANKED_KINDS)
        shift_sql = (f"SELECT production_date, shift, crew, scheduled_minutes FROM shifts WHERE production_date BETWEEN ? AND ?{clause} "
                     "ORDER BY production_date, shift")
        stop_sql = (f"SELECT production_date, shift, kind, reason, count(*) AS stops, sum(seconds) / 60 AS minutes FROM stops "
                    f"WHERE production_date BETWEEN ? AND ?{clause} AND kind IN ('{kinds}') GROUP BY ALL")
        params = [start, end, *extra]
        shifts = self._fetch(shift_sql, params, limit=20000)   # the records' whole span (from 1 January 2025) holds over 1,000 shifts
        stops = self._fetch(stop_sql, params, limit=20000)
        for sql in (shift_sql, stop_sql):
            if trace is not None:
                trace["sql"].append(sql)
                trace["parameters"].append(params)
        by_shift = defaultdict(list)
        for r in stops:
            by_shift[(r["production_date"], r["shift"])].append(r)
        fired = []
        for sh in shifts:
            key, scheduled = (sh["production_date"], sh["shift"]), sh["scheduled_minutes"]
            for kind in self.RANKED_KINDS:
                mine = [r for r in by_shift[key] if r["kind"] == kind]
                minutes = sum(r["minutes"] for r in mine)
                pct = _ratio(minutes, scheduled, 100)
                measure = self.KIND_BANDS[kind]
                if not targets.is_flag(measure, pct):
                    continue
                lead = max(mine, key=lambda r: (r["minutes"], r["reason"]))
                fired.append({"production_day": sh["production_date"].isoformat(), "shift": sh["shift"], "crew": sh["crew"],
                              "supervisor": f"the shift {sh['shift']} supervisor ({sh['crew']} on duty)",
                              "kind": kind, "measure": f"{kind.lower()} downtime", "minutes": round(minutes, 2),
                              "stops": sum(r["stops"] for r in mine), "scheduled_minutes": scheduled, "pct_of_shift": pct,
                              **{k: v for k, v in targets.annotate({"v": pct}, {"v": (measure, "x")}).items() if k != "v"},
                              "reason": lead["reason"], "reason_minutes": round(lead["minutes"], 2), "reason_stops": lead["stops"]})
        for f in fired:
            f["band"], f["band_text"] = f.pop("x_band"), f.pop("x_band_text")
        fired.sort(key=lambda f: (-f["pct_of_shift"], f["production_day"], f["shift"], f["kind"]))
        if fired:   # every flag carries its recurrence (review of #111), however many fire
            self._recurrence(fired, shift, trace)
        return fired, len(fired)

    def _recurrence(self, flags, shift, trace):
        """Fill each flag's recurrence fields (see downtime_flags) from one read of the trailing years they span.
        Each flag is a few lookups, not a pass over the rows, so a window with hundreds of flags (a year once the
        rolling year of issue #91 lands) stays fast: the dates are sorted once and each flag's year is a bisect;
        the shifts on which a reason was over its band are a sorted list per reason; and the weekly top fives of
        whole weeks are computed once, only the two edge weeks of a flag's year computed on their own."""
        years = {f["production_day"]: self.trailing_year(f["production_day"]) for f in flags}
        first, last = min(y[0] for y in years.values()), max(y[1] for y in years.values())
        clause, extra = self._shift_clause(shift)
        kinds = "', '".join(self.RANKED_KINDS)
        sql = (f"SELECT s.production_date, s.shift, s.scheduled_minutes, st.kind, st.reason, coalesce(sum(st.seconds), 0) / 60 AS minutes "
               f"FROM shifts s LEFT JOIN stops st ON st.production_date = s.production_date AND st.shift = s.shift AND st.kind IN ('{kinds}') "
               f"WHERE s.production_date BETWEEN ? AND ?{clause.replace(' shift', ' s.shift')} GROUP BY ALL")
        params = [first.isoformat(), last.isoformat(), *extra]
        rows = self._fetch(sql, params, limit=50000)
        if trace is not None:
            trace["sql"].append(sql)
            trace["parameters"].append(params)
        days = sorted({r["production_date"] for r in rows})
        shifts_on = defaultdict(set)
        over = defaultdict(list)                                        # (kind, reason) -> the dates of the shifts it was over its band
        minutes_on = defaultdict(lambda: defaultdict(float))            # date -> (kind, reason) -> unplanned minutes
        for r in rows:
            shifts_on[r["production_date"]].add(r["shift"])
            if r["kind"] is None:
                continue
            minutes_on[r["production_date"]][(r["kind"], r["reason"])] += r["minutes"]
            if r["minutes"] > targets.BANDS[self.KIND_BANDS[r["kind"]]]["flag"] / 100 * r["scheduled_minutes"]:
                over[(r["kind"], r["reason"])].append(r["production_date"])
        for dates in over.values():
            dates.sort()
        counted = [0]
        for d in days:
            counted.append(counted[-1] + len(shifts_on[d]))
        week_days = defaultdict(list)
        for d in days:
            week_days[d - timedelta(days=d.weekday())].append(d)
        whole = {}

        def top_five(day_list):
            """The five reasons with the most unplanned minutes over the days, or None when they hold no stop."""
            sums = defaultdict(float)
            for d in day_list:
                for k, m in minutes_on[d].items():
                    sums[k] += m
            return set(sorted(sums, key=lambda k: (-sums[k], k))[:5]) if sums else None
        for f in flags:
            since, through = years[f["production_day"]]
            lo, hi = bisect_left(days, since), bisect_right(days, through)
            key = (f["kind"], f["reason"])
            dates = over.get(key, [])
            weeks = in_top = 0
            for monday in sorted({d - timedelta(days=d.weekday()) for d in (days[lo], days[hi - 1])} |
                                 {m for m in week_days if since <= m and m + timedelta(days=4) <= through}) if hi > lo else []:
                inside = [d for d in week_days[monday] if since <= d <= through]
                if inside == week_days[monday]:
                    if monday not in whole:
                        whole[monday] = top_five(inside)
                    top = whole[monday]
                else:
                    top = top_five(inside)
                if top is not None:
                    weeks += 1
                    in_top += key in top
            f.update({"recurrence_since": since.isoformat(), "recurrence_through": through.isoformat(),
                      "shifts_counted": counted[hi] - counted[lo],
                      "times_over_band": bisect_right(dates, through) - bisect_left(dates, since),
                      "weeks_counted": weeks, "weeks_in_top_five": in_top,
                      "recurrence_scope": f"shift {shift}" if shift else "all shifts"})

    def get_downtime_review(self, window, shift=None):
        """The owner's downtime answer (issue #101, targets.json): maintenance and operator downtime first, each
        and together against its band as a percent of scheduled minutes (480 a shift, 1,440 a production day);
        every recorded reason, by kind, ranked by its percent of scheduled minutes with its stop count and minutes
        per stop; external downtime (a power outage, targets.json) and planned maintenance (the Scheduled kind) listed
        apart and not ranked, neither in the maintenance or operator figure; and the flags: each shift
        over a band, with its supervisor slot and the leading reason's recurrence over the trailing year. The previous
        window of the same kind is beside, as in the Pareto: each reason's and each total's minutes and percent of
        that window's scheduled minutes (zero for a reason it did not record; None when that window is outside the
        records, never zero)."""
        started, trace = time.perf_counter(), {"sql": [], "parameters": []}
        current = self._window(window)
        previous = self._previous(current)
        available = previous["coverage"] != "unavailable"
        clause, extra = self._shift_clause(shift)
        sched_sql = f"SELECT count(*) AS shifts, coalesce(sum(scheduled_minutes), 0) AS scheduled FROM shifts WHERE production_date BETWEEN ? AND ?{clause}"
        stop_sql = (f"SELECT kind, reason, count(*) AS stops, sum(seconds) / 60 AS minutes FROM stops "
                    f"WHERE production_date BETWEEN ? AND ?{clause} GROUP BY kind, reason")
        fetched = {}
        for label, w in (("current", current), ("previous", previous if available else None)):
            if w is None:
                continue
            params = [w["start"], w["end"], *extra]
            fetched[label] = (self._fetch(sched_sql, params)[0], self._fetch(stop_sql, params))
            for sql in (sched_sql, stop_sql):
                trace["sql"].append(sql)
                trace["parameters"].append(params)
        sched, grouped = fetched["current"]
        prior_sched, prior_grouped = fetched.get("previous", (None, []))
        prior = {(r["kind"], r["reason"]): r for r in prior_grouped}
        scheduled = sched["scheduled"]
        prior_scheduled = prior_sched["scheduled"] if prior_sched else None
        rows = []
        for r in grouped:
            # ranked: maintenance and operator; apart: external (a power outage, outside the plant) and the Scheduled kind (planned maintenance)
            external = r["kind"] == targets.EXTERNAL_KIND
            planned = r["kind"] not in self.RANKED_KINDS and not external
            before = prior.get((r["kind"], r["reason"]))
            prior_minutes = (before["minutes"] if before else 0.0) if available else None
            row = {"kind": r["kind"], "reason": r["reason"],
                   "category": f"{r['reason']} · {'scheduled' if planned else r['kind'].lower()}",
                   "stops": r["stops"], "minutes": round(r["minutes"], 2), "scheduled_minutes": scheduled,
                   "pct_of_scheduled": _ratio(r["minutes"], scheduled, 100),
                   "minutes_per_stop": _ratio(r["minutes"], r["stops"], places=2), "scheduled": planned, "external": external,
                   "control": "external" if external else "schedule-driven" if planned else "controllable",
                   "previous_stops": (before["stops"] if before else 0) if available else None,
                   "previous_minutes": round(prior_minutes, 2) if available else None,
                   "previous_pct_of_scheduled": _ratio(prior_minutes, prior_scheduled, 100) if available else None}
            if planned or external:
                row.update(rank=None, band=None, band_text=None)
            else:
                targets.mark(row, self.KIND_BANDS[r["kind"]], row["pct_of_scheduled"])
            rows.append(row)
        rows.sort(key=lambda r: (r["scheduled"] or r["external"], r["scheduled"], -r["minutes"], r["category"]))
        for i, r in enumerate([r for r in rows if not (r["scheduled"] or r["external"])], 1):
            r["rank"] = i
        totals = {}
        for key, kinds in (("maintenance", ("Maintenance",)), ("operator", ("Operator",)), ("op_maint", self.RANKED_KINDS), ("pm", ("Scheduled",)),
                           ("external", (targets.EXTERNAL_KIND,))):
            mine = [r for r in grouped if r["kind"] in kinds]
            minutes = sum(r["minutes"] for r in mine)
            before = sum(r["minutes"] for r in prior_grouped if r["kind"] in kinds) if available else None
            totals.update({f"{key}_minutes": round(minutes, 2), f"{key}_stops": sum(r["stops"] for r in mine),
                           f"{key}_pct": _ratio(minutes, scheduled, 100),
                           f"previous_{key}_minutes": round(before, 2) if available else None,
                           f"previous_{key}_pct": _ratio(before, prior_scheduled, 100) if available else None})
            if key not in ("pm", "external"):
                targets.annotate(totals, {f"{key}_pct": (key, key)})
        flags, count = self.downtime_flags(current["start"], current["end"], shift, trace)
        source = {"shifts": sched["shifts"], "stops": sum(r["stops"] for r in grouped),
                  "previous_shifts": prior_sched["shifts"] if prior_sched else None,
                  "previous_stops": sum(r["stops"] for r in prior_grouped) if available else None}
        return self._envelope("get_downtime_review", current, previous, shift, rows, trace, started, source,
                              scheduled_minutes=scheduled, previous_scheduled_minutes=prior_scheduled, **totals, flags=flags, flag_count=count,
                              method_note=("Each reason is ranked by its minutes over the scheduled minutes, 480 a shift. External "
                                           "downtime (a power outage, outside the plant) and planned maintenance (scheduled time) are "
                                           "listed apart, never ranked or counted against a band. A flag is a shift whose "
                                           "maintenance or operator downtime is over its band. Its recurrence counts, over the trailing "
                                           "year, the shifts on which the same reason was over the band on its own, and the weeks it was "
                                           "among the five reasons with the most unplanned minutes."))

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
            row = {"week_start": w["start"], "week_end": w["end"], "production_days": w["production_days_observed"],
                   "value": value, "unit": TREND_UNITS[metric], "partial": w["coverage"] != "complete"}
            if metric in targets.KPI_BANDS:
                targets.mark(row, targets.KPI_BANDS[metric], value)
            rows.append(row)
        window = {"period": "week", "anchor": anchor_day.isoformat(), "start": (last_start - timedelta(days=7 * (weeks - 1))).isoformat(),
                  "end": last_end.isoformat(), "label": f"{weeks} production weeks ending {last_end.isoformat()}",
                  "production_days_expected": 5 * weeks, "production_days_observed": sum(r["production_days"] for r in rows),
                  "coverage": "complete" if rows and not missing and not any(r["partial"] for r in rows) else "partial" if rows else "unavailable"}
        source = {"weeks": len(rows)}
        return self._envelope("get_trend", window, None, shift, rows, trace, started, source,
                              metric=metric, unit=TREND_UNITS[metric], weeks_requested=weeks, missing_weeks=missing)


_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


# ------------------------------------------------------------------ shift_kpis

_SHIFT_KPIS_COLUMNS = ", ".join(f"{name} {kind}" for name, kind in SHIFT_KPIS)


def _csv_value(value):
    if value is None:
        return "\\N"
    if value is True or value is False:
        return "true" if value else "false"
    return value


def _load_chunked(db, table, rows):
    """Insert a contract table's positional rows. A table of plain columns goes through one CSV file that
    DuckDB's own reader parses (several times faster than the JSON payload once the record held 21 months
    of setups, lanes and reject lines; floats are written by repr, so every value round-trips exactly, and
    an empty string stays empty while None is NULL); a table with list columns uses the JSON loader."""
    schema = db.execute(f"DESCRIBE {table}").fetchall()
    if any(kind.endswith("[]") for _, kind, *_ in schema):
        for at in range(0, len(rows), LOAD_CHUNK):
            load_rows(db, table, rows[at:at + LOAD_CHUNK])
        return
    if not rows:
        return
    handle, path = tempfile.mkstemp(prefix=f"investigator-{table}-", suffix=".csv")
    try:
        with os.fdopen(handle, "w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerows([_csv_value(v) for v in row] for row in rows)
        columns = ", ".join(f"'{name}': '{kind}'" for name, kind, *_ in schema)
        literal = path.replace("'", "''")
        db.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{literal}', header=false, columns={{{columns}}}, "
                   "nullstr='\\N', quote='\"', escape='\"', allow_quoted_nulls=false, auto_detect=false)")
    finally:
        os.unlink(path)


def _corrupt_target(setups):
    """The setup the data-quality demo corrupts: the day's first shift-2 setup by start time."""
    return next((s for s in sorted(setups, key=lambda s: (s.start, s.setup_id)) if s.shift_number == 2), None)


def build_shift_kpis(db, data):
    """Fill `shift_kpis` in `db` (which holds the contract tables of `data`) and return its row count.
    Validation runs once here, in Python, with the per-day Analysis's own rules
    (`RecordStore._validate_setup`); the invalid setup ids of each variant go into a
    temporary table the aggregate leaves out."""
    by_day = {}
    for s in data["dry_end_setups"]:
        by_day.setdefault(s.production_date, []).append(s)
    excluded = []
    for setups in by_day.values():
        target = _corrupt_target(setups)
        for s in setups:
            # A setup validates the same in both variants unless it is the corrupt variant's target.
            try:
                RecordStore._validate_setup(s, s.trim_sqft)
                clean = True
            except ValueError:
                clean = False
            corrupt_ok = clean
            if s is target:
                try:
                    RecordStore._validate_setup(s, CORRUPT_TRIM_SQFT)
                    corrupt_ok = True
                except ValueError:
                    corrupt_ok = False
            excluded.extend([s.setup_id, variant] for variant, ok in ((False, clean), (True, corrupt_ok)) if not ok)
    db.execute(f"CREATE TABLE IF NOT EXISTS shift_kpis ({_SHIFT_KPIS_COLUMNS})")
    db.execute("CREATE TEMP TABLE kpi_excluded (setup_id VARCHAR, corrupt BOOLEAN)")
    # One thread: a parallel sum adds in whatever order the threads finish, and the prebuilt
    # file and a generated store must hold bit-identical sums.
    threads = db.execute("SELECT current_setting('threads')").fetchone()[0]
    try:
        if excluded:
            db.executemany("INSERT INTO kpi_excluded VALUES (?, ?)", excluded)
        db.execute("SET threads = 1")
        db.execute("INSERT INTO shift_kpis " + _SHIFT_KPIS_SQL)
    finally:
        db.execute(f"SET threads = {int(threads)}")
        db.execute("DROP TABLE kpi_excluded")
    return db.execute("SELECT count(*) FROM shift_kpis").fetchone()[0]


# ------------------------------------------------------------------ prebuilt store

def generator_fingerprint():
    """A hash of every Python source file in this package. A prebuilt folder is only
    trusted while the code that would regenerate it (the generator, its seeded answer
    keys, the record dataclasses the pickle lays out, the reject taxonomy, the grade table,
    TABLES and the row builder here) is byte-identical to the code that built it. Hashing the whole
    package rather than a named list means a new module can never slip past the check;
    the image builds and reads with the same code, and a stale folder on a developer
    machine is simply ignored."""
    here = Path(__file__).parent
    digest = hashlib.sha256()
    # the reject taxonomy shapes the records too, and targets.json: which stops count as External is the owner's
    # rule there, and the stops table carries it
    for path in [*sorted(here.glob("*.py")), R.TAXONOMY_PATH, here / "targets.json"]:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prebuilt_folder(explicit=None):
    """The folder to try: the argument, else INVESTIGATOR_PREBUILT, else none."""
    value = explicit if explicit is not None else os.environ.get(PREBUILT_ENV, "")
    return Path(value) if value else None


def build_prebuilt(folder, seed=DEFAULT_SEED, window=None, machine_id=DEFAULT_MACHINE):
    """Generate the window once and write what a RecordStore needs to boot without
    generating: the record objects (`records.pkl`, one pickled blob per production day,
    unpickled when a day is first read) and the contract tables plus shift_kpis in DuckDB's
    own file format (`store.duckdb`, attached read-only at boot and copied in), plus a
    manifest naming the inputs and the generator fingerprint. `window` None resolves
    like a store's (INVESTIGATOR_AS_OF, else yesterday; never an existing folder's).
    Run at image build time; see Dockerfile."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    first, last = window if window is not None else dataset_window(prebuilt="")
    data = generate_days(weekdays_between(first, last), seed=seed, machine_id=machine_id)
    rows = RecordStore._rows(data)
    store = folder / "store.duckdb"
    for stale in (store, folder / "store.duckdb.wal"):
        if stale.exists():
            stale.unlink()
    db = duckdb.connect(str(store), config=DUCKDB_CONFIG)
    try:
        for table, columns in TABLES.items():
            db.execute(f"CREATE TABLE {table} ({columns})")
            _load_chunked(db, table, rows[table])
        kpi_rows = build_shift_kpis(db, data)
        db.execute("CHECKPOINT")
    finally:
        db.close()
    records = {"content_hash": data["content_hash"], "manifest": data["manifest"], "days": RecordStore._day_blobs_of(data)}
    (folder / "records.pkl").write_bytes(pickle.dumps(records, protocol=pickle.HIGHEST_PROTOCOL))
    manifest = {"format": PREBUILT_FORMAT, "seed": seed, "window": [first.isoformat(), last.isoformat()], "machine_id": machine_id,
                "first_day": data["manifest"]["first_day"], "last_day": data["manifest"]["last_day"],
                "production_days": data["manifest"]["production_days"],
                "generator_fingerprint": generator_fingerprint(), "content_hash": data["content_hash"],
                "counts": {table: len(r) for table, r in rows.items()}, "shift_kpis_rows": kpi_rows}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def read_prebuilt(folder, seed, window, machine_id):
    """(record objects, manifest counts) from a prebuilt folder, or None when anything
    about it does not match these inputs and this generator source. Never raises: a
    bad folder means generating, not failing to start. The folder is trusted like the
    code: it is written by our own image build, or named by the operator through
    INVESTIGATOR_PREBUILT. Never point that variable at an uploaded or shared folder,
    because `records.pkl` is loaded with pickle, which executes what it reads."""
    folder = Path(folder)
    try:
        manifest = json.loads((folder / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    expected = {"format": PREBUILT_FORMAT, "seed": seed, "window": [window[0].isoformat(), window[1].isoformat()],
                "machine_id": machine_id, "generator_fingerprint": generator_fingerprint()}
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
        return None
    if not (folder / "store.duckdb").is_file():
        return None
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(TABLES) or type(manifest.get("shift_kpis_rows")) is not int:
        return None
    try:
        # pickle executes what it reads: this file came from our own build (see the docstring).
        data = pickle.loads((folder / "records.pkl").read_bytes())
    except Exception:   # a truncated or foreign file: generate instead
        return None
    if not isinstance(data, dict) or data.get("content_hash") != manifest.get("content_hash") or not isinstance(data.get("days"), dict) \
            or any(not isinstance(blob, bytes) for blob in data["days"].values()):
        return None
    return data, {**counts, "shift_kpis": manifest["shift_kpis_rows"]}


def default_store():
    """The process-wide store over the rolling window (`dataset_window()`), built on first use."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = RecordStore()
        return _DEFAULT
