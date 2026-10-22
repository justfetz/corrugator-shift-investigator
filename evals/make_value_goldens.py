#!/usr/bin/env python3
"""Generate `evals/golden_values.json`: the expected numbers for the `value` cases.

How the golden set is made. With synthetic data we own the truth, so an
expected answer is never typed by hand and never read back from the tool it
checks. This script computes every expectation a second way: plain Python
sums over the record families that `synth.generate_months()` returns (the
same helper style as `tests/test_period_tools.py`), with its own calendar
arithmetic for day / week / month windows. The DuckDB path the tools use is
not imported for any number here. The file carries the dataset content hash;
`evals/run.py` refuses to score the value cases when the live store's hash
differs, so a stale golden fails loudly instead of passing quietly.

    python3 evals/make_value_goldens.py            # rewrite evals/golden_values.json
    python3 evals/make_value_goldens.py --check    # exit 1 if the committed file differs

A tolerance rides with every number: it covers the tool's display rounding
(one decimal for feet and MSF, four for percentages, two for minutes) plus
floating-point noise between a DuckDB sum and a Python sum. Integers and
strings are exact. `null` means the tool must also return null (a July
window is unavailable, never zero).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from investigator.fixture import GRADES  # noqa: E402  grade targets: a configured table, not a computation
from investigator.synth import generate_months  # noqa: E402

OUT = ROOT / 'evals' / 'golden_values.json'
SEED, MONTHS, MACHINE = 2026, (("2026", 8), ("2026", 9)), "CORR-1"
CORRUPT_TRIM_SQFT = 99999999.0   # the fixture's bad-trim value the corrupt toggle injects
SCHEDULED_SECONDS = 28800

# The dashboard tiles: key -> (better direction, display places). The direction is
# the product rule from docs/METRICS.md; the places are the tool's display rounding
# and set the tolerance. Both are checked against the application's catalog in
# `check_catalog()` so the two lists cannot drift apart silently.
TILES = {
    "volume_msf": ("higher", 1), "lineal_ft": ("higher", 1), "msf_per_day": ("higher", 1), "speed_run_fpm": ("higher", 1),
    "speed_all_fpm": ("higher", 1), "downtime_pct": ("lower", 4), "downtime_minutes": ("lower", 1),
    "stops": ("lower", 0), "minutes_per_stop": ("lower", 2), "trim_pct": ("lower", 4),
    "shear_pct": ("lower", 4), "dry_end_pct": ("lower", 4), "bond_pct": ("lower", 4),
    "warp_pct": ("lower", 4), "misalignment_pct": ("lower", 4), "moisture_pct": ("lower", 4), "bale_pct": ("lower", 4),
    "unexplained_pct": ("lower", 4), "maintenance_pm_pct": ("lower", 4), "operator_pct": ("lower", 4),
    "setups": ("neutral", 0), "paper_changes": ("neutral", 0),
    "lineal_per_setup": ("higher", 1), "lineal_per_paper_change": ("higher", 1), "bale_msf": ("lower", 1),
}
TREND_PLACES = {k: p for k, (_, p) in TILES.items()}
WASTE = ("trim", "shear", "bond", "warp", "misalignment", "moisture", "dry_end", "bale", "unexplained")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


# ---------------------------------------------------------------- calendar by hand

def weekdays(start, end):
    days, moment = [], start
    while moment <= end:
        if moment.weekday() < 5:
            days.append(moment)
        moment += timedelta(days=1)
    return days


def month_bounds(moment):
    first = moment.replace(day=1)
    return first, first.replace(day=monthrange(first.year, first.month)[1])


FISCAL_START = date(2026, 8, 3)   # the demo 4-4-5 year starts on the dataset's first Monday


def fiscal_bounds(moment):
    """Own 4-4-5 arithmetic: periods of 4, 4, 5 weeks forward from FISCAL_START (5, 4, 4 backward)."""
    pattern = (4, 4, 5)
    if moment >= FISCAL_START:
        start, i = FISCAL_START, 0
        while True:
            end = start + timedelta(days=pattern[i % 3] * 7 - 1)
            if moment <= end:
                return start, end
            start, i = end + timedelta(days=1), i + 1
    end, i = FISCAL_START - timedelta(days=1), -1
    while True:
        start = end - timedelta(days=pattern[i % 3] * 7 - 1)
        if moment >= start:
            return start, end
        end, i = start - timedelta(days=1), i - 1


def window(period, anchor):
    moment = date.fromisoformat(anchor)
    if period == "day":
        return moment, moment
    if period == "week":
        start = moment - timedelta(days=moment.weekday())
        return start, start + timedelta(days=4)
    if period == "fiscal":
        return fiscal_bounds(moment)
    return month_bounds(moment)


def previous(period, start, end):
    if period == "day":
        moment = start - timedelta(days=1)
        while moment.weekday() >= 5:
            moment -= timedelta(days=1)
        return moment, moment
    if period == "week":
        return start - timedelta(days=7), end - timedelta(days=7)
    if period == "fiscal":
        return fiscal_bounds(start - timedelta(days=1))
    return month_bounds(start - timedelta(days=1))


# ---------------------------------------------------------------- plain sums

class Plain:
    """Plain Python sums over the record families; nothing here queries a database."""

    def __init__(self, data):
        self.data = data
        self.days = {date(int(s.production_date[:4]), int(s.production_date[4:6]), int(s.production_date[6:]))
                     for s in data["shifts"]}

    def coverage(self, start, end):
        expected = weekdays(start, end)
        observed = [d for d in expected if d in self.days]
        if observed and len(observed) == len(expected):
            return "complete"
        return "partial" if observed else "unavailable"

    def between(self, family, start, end, shift=None):
        lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        return [r for r in self.data[family] if lo <= r.production_date <= hi and (shift is None or r.shift_number == shift)]

    def sums(self, start, end, shift=None):
        setups = self.between("dry_end_setups", start, end, shift)
        shifts = self.between("shifts", start, end, shift)
        stops = self.between("stops", start, end, shift)
        reasons, kinds = defaultdict(float), defaultdict(float)
        for s in setups:
            for r in s.rejects:
                reasons[r.reason] += r.sqft
        for st in stops:
            kinds[st.kind] += st.duration_sec
        return {
            "setups": len(setups), "paper_changes": len({s.wet_end_id for s in setups}),
            "feet": sum(s.lineal_ft for s in setups), "gross": sum(s.total_produced_sqft for s in setups),
            "trim": sum(s.trim_sqft for s in setups), "shear": sum(s.chop_sqft for s in setups),
            "rejects": sum(s.kqf_sqft for s in setups),
            "running_minutes": sum(s.elapsed_seconds - s.stop_time_sec for s in setups) / 60,
            "production_days": len({s.production_date for s in setups}),
            "shift_records": len(shifts), "scheduled_seconds": sum(sh.scheduled_minutes for sh in shifts) * 60,
            "bale": sum(sh.bale_sqft for sh in shifts),
            "stop_count": len(stops), "stop_seconds": sum(st.duration_sec for st in stops),
            "maintenance_pm_seconds": kinds["Maintenance"] + kinds["PM"], "operator_seconds": kinds["Operator"],
            "bond": reasons["BOND"], "warp": reasons["WARP"], "misalignment": reasons["MISALIGNMENT"], "moisture": reasons["MOISTURE"],
        }

    @staticmethod
    def tiles(t):
        """Ratios of sums, last. None where the window has no records."""
        def ratio(n, d, scale=1.0):
            return None if not d else n / d * scale
        empty = not t["setups"]
        gross, scheduled = t["gross"], t["scheduled_seconds"]
        return {
            "volume_msf": None if empty else gross / 1000,
            "lineal_ft": None if empty else t["feet"],
            "speed_run_fpm": ratio(t["feet"], t["running_minutes"]),
            "speed_all_fpm": ratio(t["feet"], scheduled / 60),
            "downtime_pct": ratio(t["stop_seconds"], scheduled, 100),
            "downtime_minutes": None if not t["shift_records"] else t["stop_seconds"] / 60,
            "stops": None if not t["shift_records"] else t["stop_count"],
            "minutes_per_stop": ratio(t["stop_seconds"] / 60, t["stop_count"]),
            "trim_pct": ratio(t["trim"], gross, 100), "shear_pct": ratio(t["shear"], gross, 100),
            "dry_end_pct": ratio(t["rejects"], gross, 100), "bond_pct": ratio(t["bond"], gross, 100),
            "warp_pct": ratio(t["warp"], gross, 100), "misalignment_pct": ratio(t["misalignment"], gross, 100),
            "moisture_pct": ratio(t["moisture"], gross, 100),
            "bale_pct": ratio(t["bale"], gross, 100),
            "unexplained_pct": ratio(t["bale"] - t["trim"] - t["shear"] - t["rejects"], gross, 100),
            "maintenance_pm_pct": ratio(t["maintenance_pm_seconds"], scheduled, 100),
            "operator_pct": ratio(t["operator_seconds"], scheduled, 100),
            "setups": None if not t["shift_records"] else t["setups"],
            "paper_changes": None if not t["shift_records"] else t["paper_changes"],
            "lineal_per_setup": ratio(t["feet"], t["setups"]),
            "lineal_per_paper_change": ratio(t["feet"], t["paper_changes"]),
            "bale_msf": None if not t["shift_records"] else t["bale"] / 1000,
            "msf_per_day": ratio(gross / 1000, t["production_days"]),
        }


# ---------------------------------------------------------------- expectation helpers

def num(value, places):
    """[expected, tolerance] for a value the tool displays with `places` decimals."""
    if value is None:
        return [None, None]
    if places == 0:
        return [int(round(value)), 0]
    return [round(value, 6), round(0.51 * 10 ** -places, 8)]


def exact(value):
    return [value, None]


def approx(value, rel=1e-6):
    """Unrounded daily-tool values: relative tolerance for float noise only."""
    if value is None:
        return [None, None]
    return [round(value, 9), round(abs(value) * rel + 1e-6, 9)]


def case(id, tool, arguments, expect, *, day="2026-09-14", corrupt=False, note=""):
    entry = {"id": id, "type": "value", "mode": "offline", "day": day, "tool": tool, "arguments": arguments}
    if corrupt:
        entry["corrupt"] = True
    if note:
        entry["note"] = note
    entry["expect"] = expect
    return entry


# ---------------------------------------------------------------- the period tools, by hand

def kpi_values(plain, cur, prev, shift):
    now = plain.tiles(plain.sums(*cur, shift))
    before = plain.tiles(plain.sums(*prev, shift)) if prev else None
    comparable = bool(prev) and plain.coverage(*cur) == "complete" and plain.coverage(*prev) == "complete"
    values = {}
    for key, (better, places) in TILES.items():
        row = {"current": num(now[key], places), "better": exact(better)}
        if prev:
            row["previous"] = num(before[key], places)
        if comparable and now[key] is not None and before[key] is not None:
            delta = now[key] - before[key]
            row["delta"] = [round(delta, 6), round(1.02 * 10 ** -places, 8)] if places else [int(round(delta)), 0]
            shown = round(round(now[key], places) - round(before[key], places), 4)   # the tile reconciles on screen
            if better == "neutral":
                change = "neutral"
            elif shown == 0:
                change = "same"
            else:
                change = "better" if (shown < 0) == (better == "lower") else "worse"
            row["change"] = exact(change)
        else:
            row["delta"] = exact(None)
            row["change"] = exact(None)
        values[f"metric={key}"] = row
    now_days, prev_days = len([d for d in weekdays(*cur) if d in plain.days]), (len([d for d in weekdays(*prev) if d in plain.days]) if prev else None)
    values["$"] = {"comparison": exact("complete" if comparable else "withheld: a window is incomplete or unavailable"),
                   "production_days_current": exact(now_days), "production_days_previous": exact(prev_days),
                   "equal_production_days": exact(None if prev_days is None else now_days == prev_days)}
    coverage = "complete" if plain.coverage(*cur) == "complete" and (not prev or plain.coverage(*prev) == "complete") else \
        ("unavailable" if plain.coverage(*cur) == "unavailable" else "partial")
    return values, coverage


def kpi_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    prev = previous(period, *cur)
    values, coverage = kpi_values(plain, cur, prev, shift)
    return case(id, "get_period_kpis", {"window": {"period": period, "anchor": anchor}, "compare": True, "shift": shift},
                {"coverage": coverage, "row_count": len(TILES), "values": values}, day=day, note=note)


def compare_case(plain, id, current, base, shift=None, *, day="2026-09-14", note=""):
    cur, prev = window(*current), window(*base)
    values, coverage = kpi_values(plain, cur, prev, shift)
    return case(id, "compare_periods", {"current_window": {"period": current[0], "anchor": current[1]},
                                        "previous_window": {"period": base[0], "anchor": base[1]}, "shift": shift},
                {"coverage": coverage, "row_count": len(TILES), "values": values}, day=day, note=note)


def pareto_case(plain, id, period, anchor, group_by, measure, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    prev = previous(period, *cur)
    def grouped(span):
        minutes, counts = defaultdict(float), defaultdict(int)
        for st in plain.between("stops", *span, shift):
            key = getattr(st, group_by)
            minutes[key] += st.duration_sec / 60
            counts[key] += 1
        return minutes, counts
    minutes, counts = grouped(cur)
    prev_minutes, prev_counts = grouped(prev)
    prev_available = plain.coverage(*prev) != "unavailable"
    measure_of = minutes if measure == "minutes" else counts
    order = sorted(minutes, key=lambda c: (-measure_of[c], c))
    total = sum(measure_of.values())
    running, values = 0.0, {}
    for category in order:
        running += measure_of[category]
        values[f"category={category}"] = {
            "stops": exact(counts[category]), "minutes": num(minutes[category], 2),
            "share_pct": num(measure_of[category] / total * 100, 2), "cumulative_pct": num(running / total * 100, 2),
            "previous_stops": exact(prev_counts.get(category, 0) if prev_available else None),
            "previous_minutes": num(prev_minutes.get(category, 0.0) if prev_available else None, 2)}
    values["#0"] = {"category": exact(order[0])}
    values["$"] = {"total_minutes": num(sum(minutes.values()), 2), "total_stops": exact(sum(counts.values())),
                   "group_by": exact(group_by), "measure": exact(measure)}
    coverage = "complete" if plain.coverage(*cur) == "complete" and plain.coverage(*prev) == "complete" else "partial"
    return case(id, "get_downtime_pareto", {"window": {"period": period, "anchor": anchor}, "group_by": group_by,
                                            "measure": measure, "shift": shift},
                {"coverage": coverage, "row_count": len(order), "values": values}, day=day, note=note)


def waste_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    prev = previous(period, *cur)
    now, before = plain.sums(*cur, shift), plain.sums(*prev, shift)
    prev_available = plain.coverage(*prev) != "unavailable"
    def areas(t):
        return {"trim": t["trim"], "shear": t["shear"], "bond": t["bond"], "warp": t["warp"],
                "misalignment": t["misalignment"], "moisture": t["moisture"], "dry_end": t["rejects"], "bale": t["bale"],
                "unexplained": t["bale"] - t["trim"] - t["shear"] - t["rejects"]}
    a, b = areas(now), areas(before)
    values = {}
    for key in WASTE:
        pct = a[key] / now["gross"] * 100
        prior = b[key] / before["gross"] * 100 if prev_available and before["gross"] else None
        values[f"category={key}"] = {
            "sqft": num(a[key], 1), "pct_of_gross": num(pct, 4),
            "previous_sqft": num(b[key], 1) if prev_available else exact(None),
            "previous_pct_of_gross": num(prior, 4),
            "delta_pct_points": [round(pct - prior, 6), 1.02e-4] if prior is not None else exact(None)}
    values["$"] = {"gross_sqft": num(now["gross"], 1), "previous_gross_sqft": num(before["gross"], 1) if prev_available else exact(None)}
    coverage = "complete" if plain.coverage(*cur) == "complete" and plain.coverage(*prev) == "complete" else "partial"
    return case(id, "get_waste_breakdown", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": coverage, "row_count": len(WASTE), "values": values}, day=day, note=note)


def profile_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    days = sorted({sh.production_date for sh in plain.between("shifts", *cur, shift)})
    scheduled = defaultdict(int)
    for sh in plain.between("shifts", *cur, shift):
        scheduled[sh.production_date] += sh.scheduled_minutes
    per_day = defaultdict(lambda: [0, 0.0])
    for st in plain.between("stops", *cur, shift):
        per_day[st.production_date][0] += 1
        per_day[st.production_date][1] += st.duration_sec / 60
    values, profile = {}, {name: [0, 0, 0.0] for name in WEEKDAYS}
    for text in days:
        d = date(int(text[:4]), int(text[4:6]), int(text[6:]))
        count, minutes = per_day[text]
        values[f"production_day={d.isoformat()}"] = {
            "weekday": exact(WEEKDAYS[d.weekday()]), "stops": exact(count), "minutes": num(minutes, 2),
            "minutes_per_stop": [round(minutes / count, 6), 0.011] if count else exact(None),
            # the tool divides the displayed (2-dp) minutes so the row reconciles on screen
            "downtime_pct": [round(minutes / scheduled[text] * 100, 6), round(0.005 / scheduled[text] * 100 + 0.51e-4, 8)]}
        bucket = profile[WEEKDAYS[d.weekday()]]
        bucket[0] += 1
        bucket[1] += count
        bucket[2] += minutes
    for name, (n_days, count, minutes) in profile.items():
        values[f"weekday_profile:weekday={name}"] = {
            "days": exact(n_days), "stops": exact(count), "minutes": [round(minutes, 6), 0.03],
            "stops_per_day": [round(count / n_days, 6), 0.006] if n_days else exact(None),
            "minutes_per_day": [round(minutes / n_days, 6), 0.03] if n_days else exact(None)}
    return case(id, "get_stops_profile", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": plain.coverage(*cur), "row_count": len(days), "values": values}, day=day, note=note)


def grade_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    prev = previous(period, *cur)
    available = plain.coverage(*prev) != "unavailable"
    by_grade, before = defaultdict(list), defaultdict(float)
    for s in plain.between("dry_end_setups", *cur, shift):
        by_grade[s.quality_id].append(s)
    for s in plain.between("dry_end_setups", *prev, shift):
        before[s.quality_id] += s.total_produced_sqft
    total = sum(s.total_produced_sqft for group in by_grade.values() for s in group)
    values = {"$": {"total_volume_msf": num(total / 1000, 1)}}
    for grade, mine in by_grade.items():
        gross, feet = sum(s.total_produced_sqft for s in mine), sum(s.lineal_ft for s in mine)
        running = sum(s.elapsed_seconds - s.stop_time_sec for s in mine) / 60
        elapsed = sum(s.elapsed_seconds for s in mine) / 60
        run_fpm = feet / running
        values[f"grade={grade}"] = {
            "target_fpm": exact(float(GRADES[grade])), "setups": exact(len(mine)), "wet_ends": exact(len({s.wet_end_id for s in mine})),
            "lineal_ft": num(feet, 1), "volume_msf": num(gross / 1000, 1), "share_of_volume_pct": num(gross / total * 100, 4),
            "speed_run_fpm": num(run_fpm, 1), "speed_elapsed_fpm": num(feet / elapsed, 1),
            # the tool divides the displayed (1-dp) speed by the target
            "speed_to_target_pct": [round(run_fpm / GRADES[grade] * 100, 6), round(0.05 / GRADES[grade] * 100 + 0.051, 6)],
            "setup_stops": exact(sum(s.number_of_stops for s in mine)), "setup_stop_minutes": num(sum(s.stop_time_sec for s in mine) / 60, 1),
            "downtime_pct_of_elapsed": num(sum(s.stop_time_sec for s in mine) / sum(s.elapsed_seconds for s in mine) * 100, 4),
            "lineal_per_setup": num(feet / len(mine), 1), "lineal_per_wet_end": num(feet / len({s.wet_end_id for s in mine}), 1),
            "trim_pct": num(sum(s.trim_sqft for s in mine) / gross * 100, 4), "dry_end_pct": num(sum(s.kqf_sqft for s in mine) / gross * 100, 4),
            "previous_volume_msf": num(before[grade] / 1000, 1) if available else exact(None),
            "delta_volume_msf": [round(gross / 1000 - round(before[grade] / 1000, 1), 6), 0.11] if available else exact(None)}
    coverage = "complete" if plain.coverage(*cur) == "complete" and plain.coverage(*prev) == "complete" else "partial"
    return case(id, "get_grade_totals", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": coverage, "row_count": len(by_grade), "values": values}, day=day, note=note)


def overrun_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    lines = defaultdict(list)
    for s in plain.between("dry_end_setups", *cur, shift):
        for c in s.customers:
            lines[c.cutoff_allocation].append(c)
    lines["all knives"] = [c for k in sorted(k for k in lines if k != "all knives") for c in lines[k]]
    values = {"$": {"band_pct": exact(2.0), "window_pct": exact(10.0)}}
    for key, mine in lines.items():
        label = key if key == "all knives" else f"knife {key}"
        nominal, actual = sum(c.nominal_cuts for c in mine), sum(c.actual_cuts for c in mine)
        over = sum(1 for c in mine if (c.actual_cuts - c.nominal_cuts) / c.nominal_cuts * 100 > 2)
        under = sum(1 for c in mine if (c.actual_cuts - c.nominal_cuts) / c.nominal_cuts * 100 < -2)
        values[f"knife={label}"] = {
            "lines": exact(len(mine)), "nominal_cuts": exact(nominal), "actual_cuts": exact(actual), "delta_cuts": exact(actual - nominal),
            "overrun_pct": num((actual - nominal) / nominal * 100, 2),
            "nominal_sheets": exact(sum(c.nominal_cuts * c.number_of_outs for c in mine)),
            "actual_sheets": exact(sum(c.actual_cuts * c.number_of_outs for c in mine)),
            "over_sheets": exact(sum((c.actual_cuts - c.nominal_cuts) * c.number_of_outs for c in mine if c.actual_cuts > c.nominal_cuts)),
            "under_sheets": exact(sum((c.nominal_cuts - c.actual_cuts) * c.number_of_outs for c in mine if c.actual_cuts < c.nominal_cuts)),
            "lines_inside_band_pct": num((len(mine) - over - under) / len(mine) * 100, 1),
            "lines_over_pct": num(over / len(mine) * 100, 1), "lines_under_pct": num(under / len(mine) * 100, 1)}
    return case(id, "get_overrun", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": plain.coverage(*cur), "row_count": len(lines), "values": values}, day=day, note=note)


def trend_case(plain, id, metric, weeks, anchor, shift=None, *, day="2026-09-14", note=""):
    last_start, last_end = window("week", anchor)
    values, missing, rows = {}, [], 0
    for back in range(weeks - 1, -1, -1):
        start, end = last_start - timedelta(days=7 * back), last_end - timedelta(days=7 * back)
        coverage = plain.coverage(start, end)
        if coverage == "unavailable":
            missing.append(start.isoformat())
            continue
        rows += 1
        value = plain.tiles(plain.sums(start, end, shift))[metric]
        values[f"week_start={start.isoformat()}"] = {
            "week_end": exact(end.isoformat()), "value": num(value, TREND_PLACES[metric]),
            "production_days": exact(len([d for d in weekdays(start, end) if d in plain.days])),
            "partial": exact(coverage != "complete")}
    values["$"] = {"missing_weeks": exact(missing), "weeks_requested": exact(weeks), "metric": exact(metric)}
    all_complete = rows and not missing and all(v["partial"][0] is False for k, v in values.items() if k != "$")
    return case(id, "get_trend", {"metric": metric, "weeks": weeks, "anchor": anchor, "shift": shift},
                {"coverage": "complete" if all_complete else "partial", "row_count": rows, "values": values}, day=day, note=note)


# ---------------------------------------------------------------- the daily tools, by hand

def day_setups(plain, day, shift, corrupt=False):
    span = (date.fromisoformat(day),) * 2
    setups = sorted(plain.between("dry_end_setups", *span, shift), key=lambda s: (s.start, s.setup_id))
    excluded = []
    if corrupt and shift == 2:
        # The corrupt toggle overwrites trim on the first setup of shift 2 with an impossible
        # area; validation refuses it and the record is excluded with its raw values.
        bad = setups.pop(0)
        excluded.append({"record_id": bad.setup_id, "shift": 2, "error_match": "Waste/gross",
                         "raw": {"trim_sqft": CORRUPT_TRIM_SQFT, "lineal_ft": bad.lineal_ft,
                                 "total_produced_sqft": bad.total_produced_sqft}})
    return setups, excluded


def day_kpi_case(plain, id, day, shift, corrupt=False, note=""):
    setups, excluded = day_setups(plain, day, shift, corrupt)
    stops = plain.between("stops", date.fromisoformat(day), date.fromisoformat(day), shift)
    feet = sum(s.lineal_ft for s in setups)
    gross = sum(s.total_produced_sqft for s in setups)
    trim, shear, rejects = (sum(getattr(s, f) for s in setups) for f in ("trim_sqft", "chop_sqft", "kqf_sqft"))
    kinds = defaultdict(float)
    for st in stops:
        kinds[st.kind] += st.duration_sec
    wet_ends = len({s.wet_end_id for s in setups})
    row = {
        "valid_setups": exact(len(setups)), "paper_changes": exact(wet_ends),
        "speed_to_target_pct": approx(feet / sum(GRADES[s.quality_id] * s.elapsed_seconds / 60 for s in setups) * 100),
        "observed_lineal_ft": approx(feet), "observed_shift_fpm": approx(feet / 480),
        "gross_sqft": approx(gross), "estimated_good_sqft": approx(sum(s.total_produced_sqft - s.trim_sqft - s.chop_sqft - s.kqf_sqft for s in setups)),
        "throughput_in": approx(gross / feet * 12),
        "trim_pct": approx(trim / gross * 100), "shear_pct": approx(shear / gross * 100), "dry_end_pct": approx(rejects / gross * 100),
        "maintenance_pct": approx(kinds["Maintenance"] / SCHEDULED_SECONDS * 100),
        "operator_pct": approx(kinds["Operator"] / SCHEDULED_SECONDS * 100),
        "pm_pct": approx(kinds["PM"] / SCHEDULED_SECONDS * 100),
        "downtime_pct": approx(sum(kinds.values()) / SCHEDULED_SECONDS * 100),
        "lineal_per_setup": approx(feet / len(setups)), "lineal_per_wet_end": approx(feet / wet_ends),
        "setups_above_trim_target": exact(sum(1 for s in setups if s.trim_sqft / s.total_produced_sqft * 100 > 3.25)),
    }
    expect = {"coverage": "incomplete" if excluded else "complete", "row_count": 1, "values": {"#0": row}}
    if excluded:
        expect["excluded_records"] = excluded
    return case(id, "get_shift_kpis", {"shift": shift}, expect, day=day, corrupt=corrupt, note=note)


def day_downtime_case(plain, id, day, shift, group_by="reason", kind=None, note=""):
    minutes = defaultdict(float)
    for st in plain.between("stops", date.fromisoformat(day), date.fromisoformat(day), shift):
        if kind is None or st.kind == kind:
            minutes[getattr(st, group_by)] += st.duration_sec / 60
    order = sorted(minutes, key=lambda c: (-minutes[c], c))
    values = {f"category={c}": {"down_minutes": approx(minutes[c]), "shift_percent": approx(minutes[c] * 60 / SCHEDULED_SECONDS * 100)}
              for c in order}
    if order:
        values["#0"] = {"category": exact(order[0])}
    return case(id, "get_downtime_breakdown", {"shift": shift, "group_by": group_by, "kind": kind},
                {"coverage": "complete", "row_count": len(order), "values": values}, day=day, note=note)


def day_quality_case(plain, id, day, shift, note=""):
    setups, _ = day_setups(plain, day, shift)
    area = defaultdict(float)
    for s in setups:
        for r in s.rejects:
            area[r.reason] += r.sqft
    order = sorted(area, key=lambda c: (-area[c], c))
    values = {f"reason={r}": {"rejected_sqft": approx(area[r])} for r in order}
    values["#0"] = {"reason": exact(order[0])}
    return case(id, "get_quality_breakdown", {"shift": shift},
                {"coverage": "complete", "row_count": len(order), "values": values}, day=day, note=note)


# ---------------------------------------------------------------- the case list

def build(data=None):
    data = data or generate_months(seed=SEED, months=MONTHS, machine_id=MACHINE)
    plain = Plain(data)
    cases = []
    # Every KPI tile for September and August, all shifts and per shift.
    cases.append(kpi_case(plain, "value-kpi-september-all", "month", "2026-09-14",
                          note="September against August: every tile, delta and change label."))
    cases.append(kpi_case(plain, "value-kpi-august-all", "month", "2026-08-14",
                          note="August's previous window is July, which is outside the dataset: previous, delta and change are null, never zero."))
    for shift in (1, 2, 3):
        cases.append(kpi_case(plain, f"value-kpi-september-shift-{shift}", "month", "2026-09-14", shift))
        cases.append(kpi_case(plain, f"value-kpi-august-shift-{shift}", "month", "2026-08-14", shift))
    # One production week per shift (and all shifts) and one production day per shift.
    cases.append(kpi_case(plain, "value-kpi-week-0914-all", "week", "2026-09-16"))
    for shift in (1, 2, 3):
        cases.append(kpi_case(plain, f"value-kpi-week-0914-shift-{shift}", "week", "2026-09-16", shift))
        cases.append(kpi_case(plain, f"value-kpi-day-0914-shift-{shift}", "day", "2026-09-14", shift,
                              note="A Monday: the previous production day is the Friday before."))
    # 4-4-5 periods: P2 (31 Aug - 27 Sep) against P1 (3 - 30 Aug), equal 20-day windows; P1 against the prior year's P13.
    cases.append(kpi_case(plain, "value-kpi-fiscal-p2-vs-p1", "fiscal", "2026-09-14",
                          note="Two complete 4-week periods: equal production days, raw totals comparable."))
    cases.append(kpi_case(plain, "value-kpi-fiscal-p1-no-previous", "fiscal", "2026-08-14",
                          note="P1's predecessor is the prior year's 5-week P13, before the dataset: previous null, deltas withheld."))
    cases.append(kpi_case(plain, "value-kpi-first-week-no-previous", "week", "2026-08-05",
                          note="The first week has an unavailable previous window: deltas withheld, current tiles present."))
    cases.append(kpi_case(plain, "value-kpi-last-week-partial", "week", "2026-09-29",
                          note="The week of 28 September has three production days: coverage partial, deltas withheld."))
    cases.append(compare_case(plain, "value-compare-september-vs-august", ("month", "2026-09-01"), ("month", "2026-08-01")))
    cases.append(compare_case(plain, "value-compare-day-0914-vs-0803-shift-1", ("day", "2026-09-14"), ("day", "2026-08-03"), 1))
    # The downtime Pareto for the story week (7-11 September), by reason and by place, minutes and count.
    for group_by in ("reason", "place", "kind"):
        for measure in ("minutes", "count"):
            if group_by == "kind" and measure == "count":
                continue
            cases.append(pareto_case(plain, f"value-pareto-week-0907-{group_by}-{measure}", "week", "2026-09-09", group_by, measure,
                                     note="The missed-splice week: Splicer 3 minutes against the week before."))
    cases.append(pareto_case(plain, "value-pareto-september-place-minutes-shift-2", "month", "2026-09-14", "place", "minutes", 2))
    # The waste breakdown for both months and the story week.
    cases.append(waste_case(plain, "value-waste-september", "month", "2026-09-14"))
    cases.append(waste_case(plain, "value-waste-august", "month", "2026-08-14",
                            note="July is unavailable: previous columns and deltas are null."))
    cases.append(waste_case(plain, "value-waste-week-0907", "week", "2026-09-09"))
    # Stops profile: the month with the Monday effect and one week for one shift.
    cases.append(profile_case(plain, "value-stops-profile-august", "month", "2026-08-14"))
    cases.append(profile_case(plain, "value-stops-profile-september", "month", "2026-09-14"))
    cases.append(profile_case(plain, "value-stops-profile-week-0914-shift-1", "week", "2026-09-14", 1))
    # Totals by grade: both months (August's previous is July: null), one week for one shift.
    cases.append(grade_case(plain, "value-grade-totals-september", "month", "2026-09-14"))
    cases.append(grade_case(plain, "value-grade-totals-august", "month", "2026-08-14",
                            note="July is unavailable: previous and delta volume are null, never zero."))
    cases.append(grade_case(plain, "value-grade-totals-week-0914-shift-2", "week", "2026-09-14", 2))
    # Over/under-run by knife: the story week for one shift, and September for all shifts.
    cases.append(overrun_case(plain, "value-overrun-week-0914-shift-2", "week", "2026-09-14", 2))
    cases.append(overrun_case(plain, "value-overrun-september", "month", "2026-09-14"))
    # Trends for trim and warp, plus a series that starts before the dataset.
    cases.append(trend_case(plain, "value-trend-trim-8w", "trim_pct", 8, "2026-09-30", day="2026-09-30"))
    cases.append(trend_case(plain, "value-trend-warp-8w", "warp_pct", 8, "2026-09-14"))
    cases.append(trend_case(plain, "value-trend-stops-9w-missing-weeks", "stops", 9, "2026-09-14",
                            note="Nine weeks back reaches July: two weeks are listed as missing, never as zero."))
    cases.append(trend_case(plain, "value-trend-msf-per-day-4w-shift-3", "msf_per_day", 4, "2026-09-14", 3))
    # The daily tools for three production days: the first day, a mid-September Monday, the last day.
    for day in ("2026-08-03", "2026-09-14", "2026-09-30"):
        tag = day[5:].replace("-", "")
        for shift in (1, 2, 3):
            cases.append(day_kpi_case(plain, f"value-day-kpis-{tag}-shift-{shift}", day, shift))
        cases.append(day_downtime_case(plain, f"value-day-downtime-{tag}-reason-shift-2", day, 2, "reason"))
        cases.append(day_downtime_case(plain, f"value-day-downtime-{tag}-kind-shift-1", day, 1, "kind"))
        cases.append(day_downtime_case(plain, f"value-day-downtime-{tag}-maintenance-place-shift-3", day, 3, "place", "Maintenance"))
        cases.append(day_quality_case(plain, f"value-day-quality-{tag}-shift-3", day, 3))
    # Data quality: the corrupt trim record is excluded, reported with its raw value, and never averaged in.
    cases.append(day_kpi_case(plain, "value-dq-corrupt-trim-excluded", "2026-09-14", 2, corrupt=True,
                              note="Expected values are sums over the remaining valid setups: the 99,999,999 sq ft trim is reported in excluded_records, not averaged in."))
    cases.append(day_kpi_case(plain, "value-dq-corrupt-other-shift-complete", "2026-09-14", 1, corrupt=True,
                              note="The bad record sits in shift 2; shift 1 stays complete and unchanged."))
    assert len({c["id"] for c in cases}) == len(cases)
    return {
        "about": "GENERATED by evals/make_value_goldens.py from plain Python sums over synth.generate_months(); "
                 "do not edit by hand. Each value is [expected, tolerance]; tolerance null means exact. "
                 "evals/run.py refuses these cases when content_hash differs from the live store.",
        "generator": "evals/make_value_goldens.py",
        "seed": SEED, "months": [list(m) for m in MONTHS], "machine_id": MACHINE,
        "content_hash": data["content_hash"],
        "cases": cases,
    }


def check_catalog():
    """The tile list here must equal the application's catalog (names and better direction only)."""
    from investigator.record_store import KPI_CATALOG, TREND_METRICS, WASTE_CATEGORIES
    catalog = {key: better for key, _, _, better in KPI_CATALOG}
    mine = {key: better for key, (better, _) in TILES.items()}
    if catalog != mine:
        raise SystemExit(f"TILES drifted from KPI_CATALOG: {sorted(set(catalog.items()) ^ set(mine.items()))}")
    if not set(TREND_METRICS) <= set(TREND_PLACES) or tuple(k for k, _ in WASTE_CATEGORIES) != WASTE:
        raise SystemExit("trend metrics or waste categories drifted from the application")


def render(golden):
    return json.dumps(golden, indent=1) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='compare with the committed file instead of writing')
    args = parser.parse_args(argv)
    check_catalog()
    golden = build()
    text = render(golden)
    values = sum(len(v) for c in golden["cases"] for v in c["expect"]["values"].values())
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is stale: run python3 evals/make_value_goldens.py")
            return 1
        print(f"{OUT.relative_to(ROOT)} is current: {len(golden['cases'])} value cases, {values} expectations, hash {golden['content_hash'][:12]}")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(golden['cases'])} value cases, {values} expectations, "
          f"dataset {golden['content_hash'][:12]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
