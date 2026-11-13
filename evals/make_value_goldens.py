#!/usr/bin/env python3
"""Generate `evals/golden_values.json`: the expected numbers for the `value` cases.

How the golden set is made. With synthetic data we own the truth, so an
expected answer is never typed by hand and never read back from the tool it
checks. This script computes every expectation a second way: plain Python
sums over the record families that `synth.generate_window()` returns for the
window from the data's first day to AS_OF (the golden window, issues #35, #100; the same helper style as
`tests/test_period_tools.py`), with its own calendar arithmetic for day /
week / month / 4-4-5 windows. The DuckDB path the tools use is
not imported for any number here. The file carries the dataset content hash;
`evals/run.py` refuses to score the value cases when the live store's hash
differs, so a stale golden fails loudly instead of passing quietly.

    python3 evals/make_value_goldens.py            # rewrite evals/golden_values.json
    python3 evals/make_value_goldens.py --check    # exit 1 if the committed file differs

A tolerance rides with every number: it covers the tool's display rounding
(one decimal for feet and MSF, four for percentages, two for minutes) plus
floating-point noise between a DuckDB sum and a Python sum. Integers and
strings are exact. `null` means the tool must also return null (a window
before the dataset's first day is unavailable, never zero).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from calendar import monthrange
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from investigator.fixture import GRADES  # noqa: E402  grade targets: a configured table, not a computation
from investigator.synth import DATASET_FIRST_DAY, generate_window  # noqa: E402  the dataset's configured first day


OUT = ROOT / 'evals' / 'golden_values.json'
# The owner's rule (PR #111, 2026-11-12), read from targets.json itself and not from the module the tools use: a stop
# whose reason is external (a power outage) is counted as its own kind, never as the maintenance the records log it as.
_EXTERNAL = json.loads((ROOT / 'src' / 'investigator' / 'targets.json').read_text())['external']
EXTERNAL_KIND, EXTERNAL_REASONS = _EXTERNAL['kind'], set(_EXTERNAL['reasons'])
# The golden window: every production day from the dataset's first day (1 January 2025, a fixed start, issue #100)
# to AS_OF (evals/run.py GOLDEN_AS_OF).
SEED, AS_OF, MACHINE = 2026, "2026-09-30", "CORR-1"
FIRST_DAY = DATASET_FIRST_DAY.isoformat()   # 2025-01-01, a Wednesday
# The reject taxonomy is configured data, like the grade table: which family and which waste line each category
# counts in. Read from the file itself, not through the application's helpers.
TAXONOMY = json.loads((ROOT / "src" / "investigator" / "reject_reasons.json").read_text(encoding="utf-8"))
FAMILY = {c["key"]: c["family"] for c in TAXONOMY["categories"]}
LINE = {c["key"]: TAXONOMY["families"][c["family"]]["counted_in"] for c in TAXONOMY["categories"]}


def dry_end(setup):
    """A setup's dry-end quality rejects: the lines whose category counts in the dry-end line."""
    return sum(r.sqft for r in setup.rejects if LINE[r.category] == "dry_end")


def line_sqft(setup, line):
    """A setup's reject lines of one waste line: shear (the changeover rejects) or excluded (planning / admin)."""
    return sum(r.sqft for r in setup.rejects if LINE[r.category] == line)


def boards(setup):
    """A setup's cutoff-knife and stacker waste boards, from its lanes."""
    return sum(lane.cutoff_waste_sqft + lane.stacker_waste_sqft for lane in setup.lanes)


def good_of(setup):
    """A setup's good output, the saleable board (owner, 2026-11-12): its gross minus every recorded waste line, from its
    own reject lines and lanes."""
    return (setup.total_produced_sqft - setup.trim_sqft - setup.chop_sqft - dry_end(setup) - line_sqft(setup, "shear")
            - boards(setup) - line_sqft(setup, "excluded"))
CORRUPT_TRIM_SQFT = 99999999.0   # the fixture's bad-trim value the corrupt toggle injects
HEAVY_MAX_MS = 500               # the eight- and nine-week trends run near 100 ms uncontended and crossed the runner's 250 ms
                                 # default under 2x CPU contention; evals/README.md (Latency budget) has the numbers
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
    "warp_pct": ("lower", 4), "misalignment_pct": ("lower", 4), "moisture_pct": ("lower", 4), "other_pct": ("lower", 4), "bale_pct": ("lower", 4),
    "unexplained_pct": ("lower", 4), "maintenance_pm_pct": ("lower", 4), "operator_pct": ("lower", 4),
    "setups": ("neutral", 0), "paper_changes": ("neutral", 0),
    "lineal_per_setup": ("higher", 1), "lineal_per_paper_change": ("higher", 1), "bale_msf": ("lower", 1),
}
TREND_PLACES = {k: p for k, (_, p) in TILES.items()}
WASTE = ("trim", "shear", "changeover", "cutoff_waste", "stacker_waste", "bond", "warp", "misalignment", "moisture", "other",
         "dry_end", "admin", "bale", "unexplained")
DRY_END = ("bond", "warp", "misalignment", "moisture", "other", "dry_end")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
# The recorded waste lines good output is gross minus (owner, 2026-11-12): every line but the bale, a scale reading,
# and its unexplained remainder; the dry-end families are inside dry_end.
GOOD_LINES = ("trim", "shear", "changeover", "cutoff_waste", "stacker_waste", "dry_end", "admin")


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


FISCAL_START = date(2026, 8, 3)   # the demo 4-4-5 year starts on the original two months' first Monday


def fiscal_bounds(moment):
    """Own 4-4-5 arithmetic: 52-week years from FISCAL_START (52 weeks back for the prior year), each
    four quarters of 4, 4 and 5 weeks."""
    weeks_in = (moment - FISCAL_START).days // 7
    start = FISCAL_START + timedelta(weeks=52 * (weeks_in // 52))
    for weeks in (4, 4, 5) * 4:
        end = start + timedelta(days=weeks * 7 - 1)
        if moment <= end:
            return start, end
        start = end + timedelta(days=1)
    raise AssertionError("twelve periods cover the year")


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
        # every stop is read under the kind the owner counts it as (EXTERNAL_KIND for an external reason)
        self.data = {**data, "stops": [replace(st, kind=EXTERNAL_KIND) if st.reason in EXTERNAL_REASONS else st for st in data["stops"]]}
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
        reasons, lines, kinds = defaultdict(float), defaultdict(float), defaultdict(float)
        for s in setups:
            for r in s.rejects:
                reasons[FAMILY[r.category]] += r.sqft
                lines[LINE[r.category]] += r.sqft
        for st in stops:
            kinds[st.kind] += st.duration_sec
        return {
            "setups": len(setups), "paper_changes": len({s.wet_end_id for s in setups}),
            "feet": sum(s.lineal_ft for s in setups), "gross": sum(s.total_produced_sqft for s in setups),
            "trim": sum(s.trim_sqft for s in setups), "shear": sum(s.chop_sqft for s in setups),
            "rejects": lines["dry_end"], "changeover": lines["shear"], "admin": lines["excluded"],
            "cutoff_waste": sum(lane.cutoff_waste_sqft for s in setups for lane in s.lanes),
            "stacker_waste": sum(lane.stacker_waste_sqft for s in setups for lane in s.lanes),
            "running_minutes": sum(s.elapsed_seconds - s.stop_time_sec for s in setups) / 60,
            "production_days": len({s.production_date for s in setups}),
            "shift_records": len(shifts), "scheduled_seconds": sum(sh.scheduled_minutes for sh in shifts) * 60,
            "bale": sum(sh.bale_sqft for sh in shifts),
            "stop_count": len(stops), "stop_seconds": sum(st.duration_sec for st in stops),
            "maintenance_pm_seconds": kinds["Maintenance"] + kinds["Scheduled"], "operator_seconds": kinds["Operator"],
            "bond": reasons["BOND"], "warp": reasons["WARP"], "misalignment": reasons["MISALIGNMENT"],
            "moisture": reasons["MOISTURE"], "other": reasons["OTHER"],
        }

    @staticmethod
    def accounted(t):
        """Every recorded waste line: trim, shear, changeover, dry-end, admin, cutoff and stacker waste boards."""
        return t["trim"] + t["shear"] + t["changeover"] + t["rejects"] + t["admin"] + t["cutoff_waste"] + t["stacker_waste"]

    @staticmethod
    def tiles(t):
        """Ratios of sums, last. None where the window has no records."""
        def ratio(n, d, scale=1.0):
            return None if not d else n / d * scale
        empty = not t["setups"]
        gross, scheduled = t["gross"], t["scheduled_seconds"]
        net = gross - t["trim"] - t["shear"]   # dry-end waste is a percent of the yield after trim and shear (issue #101)
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
            "dry_end_pct": ratio(t["rejects"], net, 100), "bond_pct": ratio(t["bond"], net, 100),
            "warp_pct": ratio(t["warp"], net, 100), "misalignment_pct": ratio(t["misalignment"], net, 100),
            "moisture_pct": ratio(t["moisture"], net, 100),
            "other_pct": ratio(t["other"], net, 100),
            "bale_pct": ratio(t["bale"], gross, 100),
            "unexplained_pct": ratio(t["bale"] - Plain.accounted(t), gross, 100),
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


def case(id, tool, arguments, expect, *, day="2026-09-14", corrupt=False, note="", max_ms=None, rates=None):
    entry = {"id": id, "type": "value", "mode": "offline", "day": day, "tool": tool, "arguments": arguments}
    if corrupt:
        entry["corrupt"] = True
    if rates:
        entry["rates"] = rates   # the visitor's rate overrides, as rates= spells them (context.rates)
    if note:
        entry["note"] = note
    if max_ms is not None:
        expect = {**expect, "max_ms": max_ms}   # the case's own latency budget where the runner's default is too tight
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


def tile_parts_sum(parts, rates):
    """A tile's dollars by hand: one pricing call per part, summed, as the tool rows of the same scope are."""
    priced = [money(kind, q, gross, minutes, rates, quantum) for kind, q, gross, minutes, quantum in parts]
    usd = [e["usd"] for e in priced if e["usd"][0] is not None]
    if not usd:
        return {"usd": exact(None)}
    return {"usd": [round(sum(u[0] for u in usd), 6), sum(u[1] for u in usd)],
            "usd_per_year": exact(None) if len(usd) < len(priced) else
                [round(sum(e["usd_per_year"][0] for e in priced), 3), sum(e["usd_per_year"][1] for e in priced)]}


WINDOW_TILE_SUMS = {"trim_pct": ("trim", "trim"), "shear_pct": ("shear", "waste"), "dry_end_pct": ("rejects", "waste"),
                    "bond_pct": ("bond", "waste"), "warp_pct": ("warp", "waste"), "misalignment_pct": ("misalignment", "waste"),
                    "moisture_pct": ("moisture", "waste"), "other_pct": ("other", "waste"), "bale_pct": ("bale", "paper")}


def window_tile_values(plain, span, shift, rates_text=None):
    """The dollars on a window's KPI tiles by hand (the #31b review: from the tool, not the tile's rounded value): each
    waste tile its category's area to 0.1 sq ft on the window's gross, the stop tiles each stop reason's minutes to
    0.01 (the Pareto's rows), maintenance and PM and operator their minutes, on the window's scheduled minutes."""
    rates = rates_of(rates_text)
    t = plain.sums(*span, shift)
    if not t["gross"]:
        return {}
    gross, minutes = round(t["gross"], 1), window_minutes(plain, span, shift)
    one = lambda kind, q: tile_parts_sum([(kind, q, gross, minutes, 0)], rates)
    values = {f"tile_money:metric={m}": one(kind, round(t[key], 1)) for m, (key, kind) in WINDOW_TILE_SUMS.items()}
    # the dry-end tile is its reject reasons' rows, as the waste breakdown and the levers price them (and the chip sums)
    values["tile_money:metric=dry_end_pct"] = tile_parts_sum([("waste", round(t[r], 1), gross, minutes, 0)
                                                              for r in ("bond", "warp", "misalignment", "moisture", "other")], rates)
    values["tile_money:metric=unexplained_pct"] = one("paper", round(t["bale"] - Plain.accounted(t), 1))
    reasons = defaultdict(float)
    for st in plain.between("stops", *span, shift):
        reasons[st.reason] += st.duration_sec / 60
    stops = tile_parts_sum([("downtime", round(m, 2), gross, minutes, 0) for m in reasons.values()], rates)   # whole seconds / 60 never ties at 0.005
    values["tile_money:metric=downtime_pct"] = {**stops, "metrics": exact(["downtime_pct", "downtime_minutes", "stops"])}   # one entry, three tiles
    values["tile_money:metric=maintenance_pm_pct"] = one("downtime", round(t["maintenance_pm_seconds"] / 60, 2))
    values["tile_money:metric=operator_pct"] = one("downtime", round(t["operator_seconds"] / 60, 2))
    return values


def kpi_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    prev = previous(period, *cur)
    values, coverage = kpi_values(plain, cur, prev, shift)
    values.update(window_tile_values(plain, cur, shift))
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


def waste_areas(t):
    """Each waste line's area (WASTE order): the dry-end families add to dry_end; the unexplained remainder is
    the bale minus every recorded line."""
    return {"trim": t["trim"], "shear": t["shear"], "changeover": t["changeover"], "cutoff_waste": t["cutoff_waste"],
            "stacker_waste": t["stacker_waste"], "bond": t["bond"], "warp": t["warp"], "misalignment": t["misalignment"],
            "moisture": t["moisture"], "other": t["other"], "dry_end": t["rejects"], "admin": t["admin"], "bale": t["bale"],
            "unexplained": t["bale"] - Plain.accounted(t)}


def waste_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    prev = previous(period, *cur)
    now, before = plain.sums(*cur, shift), plain.sums(*prev, shift)
    prev_available = plain.coverage(*prev) != "unavailable"
    def areas(t):
        return waste_areas(t)
    a, b = areas(now), areas(before)
    net, prior_net = (t["gross"] - t["trim"] - t["shear"] for t in (now, before))
    values = {}
    for key in WASTE:
        pct = a[key] / now["gross"] * 100
        prior = b[key] / before["gross"] * 100 if prev_available and before["gross"] else None
        values[f"category={key}"] = {
            "sqft": num(a[key], 1), "pct_of_gross": num(pct, 4),
            "previous_sqft": num(b[key], 1) if prev_available else exact(None),
            "previous_pct_of_gross": num(prior, 4),
            "delta_pct_points": [round(pct - prior, 6), 1.02e-4] if prior is not None else exact(None)}
        if key in DRY_END:   # dry-end waste is read against the yield after trim and shear too (issue #101)
            values[f"category={key}"]["pct_of_yield"] = num(a[key] / net * 100, 4)
            values[f"category={key}"]["previous_pct_of_yield"] = num(b[key] / prior_net * 100, 4) if prev_available and prior_net else exact(None)
    good = now["gross"] - sum(a[k] for k in GOOD_LINES)
    # Two roads to good output: the window's line totals off its gross, and each setup's own good output summed.
    by_setup = sum(good_of(s) for s in plain.between("dry_end_setups", *cur, shift))
    if abs(by_setup - good) > 0.01:
        raise AssertionError(f"{id}: good output by setup {by_setup} is not gross minus the window's lines {good}")
    values["$"] = {"gross_sqft": num(now["gross"], 1), "previous_gross_sqft": num(before["gross"], 1) if prev_available else exact(None),
                   "good_output_sqft": num(good, 1), "good_output_pct": num(good / now["gross"] * 100, 4),
                   "yield_sqft": num(net, 1)}
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
            "trim_pct": num(sum(s.trim_sqft for s in mine) / gross * 100, 4),
            "dry_end_pct": num(sum(dry_end(s) for s in mine) / sum(s.total_produced_sqft - s.trim_sqft - s.chop_sqft for s in mine) * 100, 4),
            "previous_volume_msf": num(before[grade] / 1000, 1) if available else exact(None),
            "delta_volume_msf": [round(gross / 1000 - round(before[grade] / 1000, 1), 6), 0.11] if available else exact(None)}
    coverage = "complete" if plain.coverage(*cur) == "complete" and plain.coverage(*prev) == "complete" else "partial"
    return case(id, "get_grade_totals", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": coverage, "row_count": len(by_grade), "values": values}, day=day, note=note)


def rollstock_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    """Paper consumed per grade and web width. All shifts: the wet-end records by the production day
    they started on. One shift: the setups that shift ran, grouped by their run's grade and width, with
    distinct wet ends as the run count."""
    cur = window(period, anchor)
    prev = previous(period, *cur)
    available = plain.coverage(*prev) != "unavailable"

    def cells(span):
        out = defaultdict(lambda: {"runs": set(), "feet": 0.0, "gross": 0.0})
        if shift is None:
            for w in plain.between("wet_ends", *span):
                cell = out[(w.quality_id, w.web_width_in)]
                cell["runs"].add(w.wet_end_id)
                cell["feet"] += w.lineal_ft
                cell["gross"] += w.total_produced_sqft
        else:
            for s in plain.between("dry_end_setups", *span, shift):
                cell = out[(s.quality_id, s.web_width_in)]
                cell["runs"].add(s.wet_end_id)
                cell["feet"] += s.lineal_ft
                cell["gross"] += s.total_produced_sqft
        return out
    now, before = cells(cur), cells(prev)
    total_feet = sum(c["feet"] for c in now.values())
    total_gross = sum(c["gross"] for c in now.values())
    total_runs = sum(len(c["runs"]) for c in now.values())
    prev_feet = sum(c["feet"] for c in before.values())
    prev_gross = sum(c["gross"] for c in before.values())
    prev_runs = sum(len(c["runs"]) for c in before.values())
    values = {"$": {"total_lineal_ft": num(total_feet, 1), "total_msf": num(total_gross / 1000, 1), "paper_runs": exact(total_runs),
                    "lineal_per_run": num(total_feet / total_runs, 1),
                    "previous_total_lineal_ft": num(prev_feet, 1) if available else exact(None),
                    "previous_total_msf": num(prev_gross / 1000, 1) if available else exact(None),
                    "previous_paper_runs": exact(prev_runs) if available else exact(None),
                    # the tool subtracts the two displayed (1-dp) totals
                    "delta_lineal_ft": [round(total_feet - prev_feet, 6), 0.11] if available else exact(None),
                    "delta_msf": [round((total_gross - prev_gross) / 1000, 6), 0.11] if available else exact(None)}}
    for (grade, width), cell in now.items():
        prior = before.get((grade, width), {"runs": set(), "feet": 0.0, "gross": 0.0})
        feet, gross, runs = cell["feet"], cell["gross"], len(cell["runs"])
        values[f"label={grade} {width:g} in"] = {
            "grade": exact(grade), "width_in": num(float(width), 1), "paper_runs": exact(runs),
            "lineal_ft": num(feet, 1), "msf": num(gross / 1000, 1), "share_of_feet_pct": num(feet / total_feet * 100, 4),
            "lineal_per_run": num(feet / runs, 1),
            "previous_paper_runs": exact(len(prior["runs"])) if available else exact(None),
            "previous_lineal_ft": num(prior["feet"], 1) if available else exact(None),
            "previous_msf": num(prior["gross"] / 1000, 1) if available else exact(None),
            "delta_lineal_ft": [round(feet - prior["feet"], 6), 0.11] if available else exact(None),
            "delta_msf": [round((gross - prior["gross"]) / 1000, 6), 0.11] if available else exact(None)}
    coverage = "complete" if plain.coverage(*cur) == "complete" and plain.coverage(*prev) == "complete" else "partial"
    return case(id, "get_rollstock", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": coverage, "row_count": len(now), "values": values}, day=day, note=note)


def overrun_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    lines = defaultdict(list)
    for s in plain.between("dry_end_setups", *cur, shift):
        for c in s.lanes:
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


def _ntile_first(values_desc, buckets=5):
    """How many of n values (already sorted descending) land in DuckDB's first ntile bucket:
    the first n % buckets buckets hold one extra row."""
    n = len(values_desc)
    return n // buckets + (1 if n % buckets else 0)


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def speed_case(plain, id, period, anchor, slice_by="width", shift=None, *, day="2026-09-14", note=""):
    cur = window(period, anchor)
    crews = {(sh.production_date, sh.shift_number): sh.group_name for sh in plain.between("shifts", *cur, shift)}
    cells, short = defaultdict(list), 0
    for s in plain.between("dry_end_setups", *cur, shift):
        run_min = (s.elapsed_seconds - s.stop_time_sec) / 60
        if run_min < 2.0:
            short += 1
            continue
        key = (s.quality_id, f"{s.web_width_in:g} in" if slice_by == "width" else crews[(s.production_date, s.shift_number)])
        cells[key].append((s.lineal_ft / run_min, s.lineal_ft, run_min, dry_end(s), s.total_produced_sqft - s.trim_sqft - s.chop_sqft))
    values, total, thin = {}, 0.0, 0
    for (grade, slc), rows in cells.items():
        rows.sort(key=lambda r: -r[0])
        feet, run = sum(r[1] for r in rows), sum(r[2] for r in rows)
        typical = feet / run
        entry = {"setups": exact(len(rows)), "lineal_ft": num(feet, 1), "run_minutes": num(run, 1),
                 "typical_fpm": num(typical, 1), "median_setup_fpm": num(_median([r[0] for r in rows]), 1),
                 "typical_pct_of_target": [round(typical / GRADES[grade] * 100, 6), round(0.05 / GRADES[grade] * 100 + 0.051, 6)],
                 "target_fpm": exact(float(GRADES[grade]))}
        if len(rows) >= 10:
            k = _ntile_first([r[0] for r in rows])
            best_rows, rest_rows = rows[:k], rows[k:]
            best = sum(r[1] for r in best_rows) / sum(r[2] for r in best_rows)
            best_pct = sum(r[3] for r in best_rows) / sum(r[4] for r in best_rows) * 100
            rest_pct = sum(r[3] for r in rest_rows) / sum(r[4] for r in rest_rows) * 100
            hours = (run - feet / best) / 60
            total += hours
            entry.update({"best_fifth_fpm": num(best, 1), "gap_fpm": [round(best - typical, 6), 0.11],
                          "opportunity_hours": num(hours, 2), "best_fifth_dry_end_pct": num(best_pct, 4),
                          "rest_dry_end_pct": num(rest_pct, 4), "reject_penalty_points": [round(best_pct - rest_pct, 6), 1.02e-4],
                          "status": exact("ok")})
            values[f"label={grade} · {slc}"] = entry
        else:
            thin += 1   # listed in thin_cells, never a row
    values["$"] = {"excluded_short_setups": exact(short), "min_run_minutes": exact(2.0), "min_setups": exact(10),
                   "total_opportunity_hours": [round(total, 6), 0.02 * max(1, len(cells))]}
    return case(id, "get_speed_opportunity", {"window": {"period": period, "anchor": anchor}, "slice_by": slice_by, "shift": shift},
                {"coverage": plain.coverage(*cur), "row_count": len(cells) - thin, "values": values}, day=day, note=note)


def trend_case(plain, id, metric, weeks, anchor, shift=None, *, day="2026-09-14", note="", max_ms=None):
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
                {"coverage": "complete" if all_complete else "partial", "row_count": rows, "values": values},
                day=day, note=note, max_ms=max_ms)


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
    trim, shear, rejects = (sum(s.trim_sqft for s in setups), sum(s.chop_sqft for s in setups), sum(dry_end(s) for s in setups))
    kinds = defaultdict(float)
    for st in stops:
        kinds[st.kind] += st.duration_sec
    net = gross - trim - shear   # dry-end waste's denominator: the yield after trim and shear (issue #101)
    wet_ends = len({s.wet_end_id for s in setups})
    row = {
        "valid_setups": exact(len(setups)), "paper_changes": exact(wet_ends),
        "speed_to_target_pct": approx(feet / sum(GRADES[s.quality_id] * s.elapsed_seconds / 60 for s in setups) * 100),
        "observed_lineal_ft": approx(feet), "observed_shift_fpm": approx(feet / 480),
        "gross_sqft": approx(gross), "estimated_good_sqft": approx(sum(good_of(s) for s in setups)),
        "trim_sqft": approx(trim), "shear_sqft": approx(shear), "reject_sqft": approx(rejects),
        "changeover_sqft": approx(sum(line_sqft(s, "shear") for s in setups)), "waste_board_sqft": approx(sum(boards(s) for s in setups)),
        "admin_sqft": approx(sum(line_sqft(s, "excluded") for s in setups)),
        "throughput_in": approx(gross / feet * 12),
        "trim_pct": approx(trim / gross * 100), "shear_pct": approx(shear / gross * 100), "dry_end_pct": approx(rejects / net * 100),
        "yield_sqft": approx(net), "op_maint_pct": approx((kinds["Maintenance"] + kinds["Operator"]) / SCHEDULED_SECONDS * 100),
        "maintenance_pct": approx(kinds["Maintenance"] / SCHEDULED_SECONDS * 100),
        "operator_pct": approx(kinds["Operator"] / SCHEDULED_SECONDS * 100),
        "pm_pct": approx(kinds["Scheduled"] / SCHEDULED_SECONDS * 100),
        "downtime_pct": approx(sum(kinds.values()) / SCHEDULED_SECONDS * 100),
        "lineal_per_setup": approx(feet / len(setups)), "lineal_per_wet_end": approx(feet / wet_ends),
        "setups_above_trim_target": exact(sum(1 for s in setups if s.trim_sqft / s.total_produced_sqft * 100 > 3.25)),
    }
    lines = trim + shear + rejects + sum(line_sqft(s, "shear") + boards(s) + line_sqft(s, "excluded") for s in setups)
    if setups and abs(gross - lines - sum(good_of(s) for s in setups)) > 0.01:   # the shift's line totals against each setup's own
        raise AssertionError(f"{id}: good output by setup is not gross minus the shift's lines")
    expect = {"coverage": "incomplete" if excluded else "complete", "row_count": 1, "values": {"#0": row}}
    if excluded:
        expect["excluded_records"] = excluded
    return case(id, "get_shift_kpis", {"shift": shift}, expect, day=day, corrupt=corrupt, note=note)


def day_downtime_case(plain, id, day, shift, group_by="reason", kind=None, note=""):
    minutes, stops, planned = defaultdict(float), defaultdict(int), defaultdict(lambda: True)
    for st in plain.between("stops", date.fromisoformat(day), date.fromisoformat(day), shift):
        if kind is None or st.kind == kind:
            minutes[getattr(st, group_by)] += st.duration_sec / 60
            stops[getattr(st, group_by)] += 1
            planned[getattr(st, group_by)] &= st.kind == "Scheduled"
    order = sorted(minutes, key=lambda c: (planned[c], -minutes[c], c))   # planned maintenance is scheduled time: last (issue #101)
    values = {f"category={c}": {"down_minutes": approx(minutes[c]), "shift_percent": approx(minutes[c] * 60 / SCHEDULED_SECONDS * 100),
                                "stops": exact(stops[c]), "minutes_per_stop": approx(minutes[c] / stops[c]), "scheduled": exact(planned[c])}
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
            if LINE[r.category] == "dry_end":
                area[FAMILY[r.category]] += r.sqft
    order = sorted(area, key=lambda c: (-area[c], c))
    values = {f"reason={r}": {"rejected_sqft": approx(area[r])} for r in order}
    values["#0"] = {"reason": exact(order[0])}
    return case(id, "get_quality_breakdown", {"shift": shift},
                {"coverage": "complete", "row_count": len(order), "values": values}, day=day, note=note)


# ---------------------------------------------------------------- dollars (issue #31), by hand
# The post's numbers, typed here on purpose: this second computation never reads the application's
# cost model for a number (`check_rates()` confirms the two agree on the defaults).
PAPER, HOUR, TRIM_TARGET = 55.0, 1200.0, 3.0   # the trim target is the owner's (issue #101); 3.25% is the flag
MSF_PER_MIN = 675 * 96 / 12 / 1000          # one minute at 675 ft/min on 96 in net = 5.4 MSF
ANNUAL_MSF = 3_600_000 / 2                   # the post's 3.6M MSF a year across two machines, per machine
ANNUAL_MIN = 1440 * ANNUAL_MSF / 7000        # 1,440 scheduled minutes on each of the post's production days
SQFT_KINDS = ("trim", "waste", "paper")
WASTE_PRICING = {"trim": "trim", "bale": "paper", "unexplained": "paper"}   # every other category is waste board


def rates_of(text):
    """'paper:66,hour:1500,margin:40' -> (paper, hour, margin, trim target), the defaults filled in."""
    found = dict(item.split(":") for item in text.split(",")) if text else {}
    return (float(found.get("paper", PAPER)), float(found.get("hour", HOUR)),
            float(found["margin"]) if "margin" in found else None, float(found.get("trim", TRIM_TARGET)))


def money(kind, qty, gross, minutes, rates, quantum=0.0):
    """The dollar fields of one priced quantity, each [expected, tolerance]. `quantum` is the display
    rounding of the quantity the tool priced (0.1 sq ft, 0.01 min): one step of it is allowed."""
    paper, hour, margin, _ = rates
    if qty < 0:
        return {"usd": exact(None), "contribution_status": exact("not priced")}
    if kind in SQFT_KINDS:
        msf = qty / 1000
        paper_usd = msf * paper
        run_min = msf / MSF_PER_MIN if kind == "waste" else 0.0
        not_made = msf if kind == "waste" else 0.0
        per_unit = paper / 1000 + (hour / 60 / MSF_PER_MIN / 1000 if kind == "waste" else 0) + (margin or 0) / 1000
    else:
        paper_usd, run_min, not_made = 0.0, qty, qty * MSF_PER_MIN
        per_unit = hour / 60 + (margin or 0) * MSF_PER_MIN
    machine = run_min / 60 * hour
    by_volume = ANNUAL_MSF / (gross / 1000) if gross else None
    by_time = ANNUAL_MIN / minutes
    machine_factor = by_volume if kind == "waste" else by_time
    tol = round(0.011 + 1.01 * quantum * per_unit, 6)
    year = paper_usd * (by_volume or 0) + machine * machine_factor
    year_tol = round(tol * max(by_volume or 0, by_time) + 1.01, 3)
    out = {"paper_usd": [round(paper_usd, 6), tol] if kind in SQFT_KINDS else exact(None),
           "machine_time_usd": [round(machine, 6), tol], "usd": [round(paper_usd + machine, 6), tol],
           "usd_per_year": [round(year, 3), year_tol], "msf_not_made": [round(not_made, 6), 0.0011 + quantum * MSF_PER_MIN]}
    if margin is None:
        out.update({"contribution_usd": exact(None), "contribution_usd_per_year": exact(None), "contribution_status": exact("margin not set")})
    else:
        factor = by_volume if kind in SQFT_KINDS else by_time
        out.update({"contribution_usd": [round(not_made * margin, 6), tol],
                    "contribution_usd_per_year": [round(not_made * margin * factor, 3), year_tol], "contribution_status": exact("set")})
    return out


def window_minutes(plain, span, shift):
    days = {sh.production_date for sh in plain.between("shifts", *span, shift)}
    return len(days) * (480 if shift else 1440)


def shift_gaps(plain, day, shift):
    """get_shift_dollars' gaps by hand, before pricing: {gap: (pricing kind, quantity)} and the shift's gross area.
    Each downtime reason, the speed gap to 675 ft/min over running time, side trim, shear and each dry-end reason."""
    span = (date.fromisoformat(day),) * 2
    setups, _ = day_setups(plain, day, shift)
    stops = plain.between("stops", *span, shift)
    gross = sum(s.total_produced_sqft for s in setups)
    feet = sum(s.lineal_ft for s in setups)
    reasons, rejects = defaultdict(float), defaultdict(float)
    for st in stops:
        reasons[st.reason] += st.duration_sec / 60
    for s in setups:
        for r in s.rejects:
            if LINE[r.category] == "dry_end":
                rejects[FAMILY[r.category]] += r.sqft
    gaps = {f"Downtime: {PLANNED_GAP if r == 'Planned maintenance' else r}": ("downtime", m) for r, m in reasons.items()}
    running = 480 - sum(st.duration_sec for st in stops) / 60
    gaps["Speed gap to 675 ft/min"] = ("speed", max(0.0, 675 * running - feet) / 675)
    gaps["Side trim"] = ("trim", sum(s.trim_sqft for s in setups))
    gaps["Shear chops"] = ("waste", sum(s.chop_sqft for s in setups))
    for gap, area in (("Changeover rejects (counted with shear)", sum(line_sqft(s, "shear") for s in setups)),
                      ("Cutoff-knife and stacker waste boards", sum(boards(s) for s in setups))):
        if round(area, 1):
            gaps[gap] = ("waste", area)
    gaps.update({f"Dry-end: {r}": ("waste", a) for r, a in rejects.items()})
    return gaps, gross


def gap_bucket(gap):
    """The chip a gap adds to on the card (cost_model.bucket_of): order changes are setups, other stops are stops."""
    if gap.startswith("Downtime: Order change"):
        return "Setups"
    for prefix, name in (("Downtime", "Stops"), ("Speed", "Speed"), ("Side trim", "Trim"), ("Shear", "Shear"), ("Dry-end", "Dry-end")):
        if gap.startswith(prefix):
            return name
    return "Other waste"


def money_summary(gaps, priced, gross, minutes, rates, trim_sqft=None):
    """The card's summary of a money answer by hand (issue #31b review): each chip's bucket total, the arithmetic's
    sums (the area priced as paper, the minutes of the stops, the speed gap and the run time waste took, the MSF not
    made) and side trim's points over the target with their paper dollars a year at the post's volume (points / 100
    x 1.8M MSF x paper). Each figure the server states is held here, so none of them is the browser's own."""
    values = {}
    buckets = defaultdict(list)
    for g, e in priced.items():
        buckets[gap_bucket(g)].append(e)
    for b, es in buckets.items():
        values[f"buckets:bucket={b}"] = {"usd": [round(sum(e["usd"][0] for e in es), 6), 0.011 * len(es)],
                                         "usd_per_year": [round(sum(e["usd_per_year"][0] for e in es), 3), 1.01 * len(es)]}
    values["buckets:#0"] = {"bucket": exact(max(buckets, key=lambda b: sum(e["usd"][0] for e in buckets[b])))}
    of = lambda kind: [q for k, q in gaps.values() if k == kind]
    area = sum(q for k, q in gaps.values() if k in SQFT_KINDS)
    waste_min = sum(q / 1000 / MSF_PER_MIN for q in of("waste"))
    machine = sum(of("downtime")) + sum(of("speed")) + waste_min
    n = len(gaps)
    values["$"] = {"area_sqft": num(area, 1), "area_msf": num(area / 1000, 3), "stop_minutes": [round(sum(of("downtime")), 6), 0.006 * len(of("downtime"))],
                   "speed_minutes": [round(sum(of("speed")), 6), 0.006], "waste_run_minutes": [round(waste_min, 6), 0.006 * len(of("waste"))],
                   "machine_minutes": [round(machine, 6), 0.006 * n],
                   "msf_not_made": [round(sum(of("downtime")) * MSF_PER_MIN + sum(of("speed")) * MSF_PER_MIN + sum(of("waste")) / 1000, 6), 0.0011 * n]}
    if trim_sqft is not None:   # the arithmetic's trim line (get_opportunities has it as a lever row instead)
        paper, _, _, target = rates
        pct = trim_sqft / gross * 100
        over = max(0.0, pct - target)
        values["$"].update({"trim_pct_of_gross": num(pct, 4), "trim_target_pct": exact(target), "trim_points_over": [round(over, 6), 0.0001],
                            "trim_over_usd": [round(over / 100 * gross / 1000 * paper, 6), 0.011],
                            "trim_over_usd_per_year": [round(over / 100 * ANNUAL_MSF * paper, 3), 1.01]})
    return values


# The owner's ruling on the money cards (PR #111): planned maintenance (the reason every Scheduled stop carries) is
# schedule-driven and an external reason (a power outage, targets.json) external; both are on the card, apart from the
# ranked losses. The planned stop reads as its reason and its kind both, "Planned maintenance (scheduled)", wherever it
# shows (owner ruling (c) on PR #123), the money card's gap included.
PLANNED_GAP = "Planned maintenance (scheduled)"
GAP_APART = {f"Downtime: {PLANNED_GAP}": "schedule-driven", **{f"Downtime: {r}": "external" for r in EXTERNAL_REASONS}}


def apart_values(values, priced):
    """Each gap's place on the card (`apart`; a gap apart has no rank) and the all-in total with the gaps apart."""
    for g in priced:
        values[f"rows:gap={g}"]["apart"] = exact(GAP_APART.get(g))
        if g in GAP_APART:
            values[f"rows:gap={g}"]["rank"] = exact(None)
    n = len(priced)
    return {"apart_gaps": exact(sum(g in GAP_APART for g in priced)),
            "all_in_usd": [round(sum(e["usd"][0] for e in priced.values()), 6), 0.011 * n],
            "all_in_usd_per_year": [round(sum(e["usd_per_year"][0] for e in priced.values()), 3), 1.01 * n]}


def shift_dollars_case(plain, id, day, shift, rates_text=None, note=""):
    """get_shift_dollars by hand: each gap of shift_gaps priced on the shift's gross and 480 minutes; the recorded losses
    ranked by dollars and totalled, with the card's summary of them (money_summary); planned maintenance and an outage
    apart, unranked, in the all-in total only (the owner, PR #111)."""
    rates = rates_of(rates_text)
    gaps, gross = shift_gaps(plain, day, shift)
    everything = {g: money(kind, q, gross, 480, rates) for g, (kind, q) in gaps.items()}
    values = {f"rows:gap={g}": e for g, e in everything.items()}
    extra = apart_values(values, everything)
    priced = {g: e for g, e in everything.items() if g not in GAP_APART}
    gaps = {g: v for g, v in gaps.items() if g not in GAP_APART}
    top = max(priced, key=lambda g: (priced[g]["usd"][0], [-ord(c) for c in g]))
    values["#0"] = {"gap": exact(top)}
    n = len(priced)
    summary = money_summary(gaps, priced, gross, 480, rates, gaps["Side trim"][1])
    values.update({k: v for k, v in summary.items() if k != "$"})
    # The dollars on the shift's KPI tiles (#31b review): the speed gap, side trim and each dry-end reason as these rows
    # price them (so the dry-end tile is the Dry-end chip), and the maintenance and operator stops' minutes.
    kinds = defaultdict(float)
    for st in plain.between("stops", *(date.fromisoformat(day),) * 2, shift):
        kinds[st.kind] += st.duration_sec / 60
    tiles = {"observed_shift_fpm": [gaps["Speed gap to 675 ft/min"]], "maintenance_pct": [("downtime", kinds["Maintenance"])],
             "operator_pct": [("downtime", kinds["Operator"])], "trim_pct": [gaps["Side trim"]],
             "dry_end_pct": [q for g, q in gaps.items() if g.startswith("Dry-end")]}
    values.update({f"tile_money:metric={k}": tile_parts_sum([(kind, q, gross, 480, 0) for kind, q in v], rates) for k, v in tiles.items()})
    values["$"] = {"total_usd": [round(sum(e["usd"][0] for e in priced.values()), 6), 0.011 * n],
                   "total_usd_per_year": [round(sum(e["usd_per_year"][0] for e in priced.values()), 3), 1.01 * n],
                   "total_contribution_usd": exact(None) if rates[2] is None else
                       [round(sum(e["contribution_usd"][0] for e in priced.values()), 6), 0.011 * n],
                   "contribution_status": exact("margin not set" if rates[2] is None else "set"), **summary["$"], **extra}
    return case(id, "get_shift_dollars", {"shift": shift}, {"coverage": "complete", "row_count": len(everything), "values": values},
                day=day, note=note, rates=rates_text)


def day_dollars_case(plain, id, day, rates_text=None, shifts=(1, 2, 3), corrupt=False, note=""):
    """get_day_dollars by hand (issue #31b review): the card's total for the day's shifts (all three, or the ones a
    question named). Each gap of shift_gaps summed over those shifts that are complete, rounded as the window tools
    round (0.1 sq ft, 0.01 min), and priced once, on their gross area (to 0.1) and 480 minutes each, so its dollars a
    year are the day's own; with the card's summary over those rows. On corrupt data shift 2 holds a refused record,
    so it is named in excluded_shifts and left out, and the day is priced over the other shifts' 480 minutes each."""
    rates = rates_of(rates_text)
    bad = {2} if corrupt and 2 in shifts else set()
    counted = [n for n in shifts if n not in bad]
    gaps, gross = {}, 0.0
    for shift in counted:
        mine, g = shift_gaps(plain, day, shift)
        gross += g
        for gap, (kind, q) in mine.items():
            gaps[gap] = (kind, gaps.get(gap, (kind, 0.0))[1] + q)
    gaps = {g: (kind, round(q, 2 if kind in ("downtime", "speed") else 1)) for g, (kind, q) in gaps.items()}
    gross, minutes = round(gross, 1), 480 * len(counted)
    everything = {g: money(kind, q, gross, minutes, rates) for g, (kind, q) in gaps.items()}
    values = {f"rows:gap={g}": e for g, e in everything.items()}
    extra = apart_values(values, everything)   # planned maintenance and an outage apart (the owner, PR #111)
    priced = {g: e for g, e in everything.items() if g not in GAP_APART}
    gaps = {g: v for g, v in gaps.items() if g not in GAP_APART}
    values["#0"] = {"gap": exact(max(priced, key=lambda g: (priced[g]["usd"][0], [-ord(c) for c in g])))}
    for shift in shifts:   # each shift named with its coverage; its own total is its get_shift_dollars step
        values[f"shifts:shift={shift}"] = {"coverage": exact("incomplete" if shift in bad else "complete")}
    n = len(priced)
    summary = money_summary(gaps, priced, gross, minutes, rates, gaps["Side trim"][1])
    values.update({k: v for k, v in summary.items() if k != "$"})
    values["$"] = {"shift": exact(None), "excluded_shifts": exact(sorted(bad)), "scheduled_minutes": exact(minutes), "gross_sqft": num(gross, 1),
                   "total_usd": [round(sum(e["usd"][0] for e in priced.values()), 6), 0.011 * n],
                   "total_usd_per_year": [round(sum(e["usd_per_year"][0] for e in priced.values()), 3), 1.01 * n],
                   "contribution_status": exact("margin not set" if rates[2] is None else "set"), **summary["$"], **extra}
    expect = {"coverage": "complete", "row_count": len(everything), "values": values}
    if bad:
        expect["excluded_records"] = day_setups(plain, day, 2, corrupt=True)[1]
    arguments = {"shifts": list(shifts)}
    return case(id, "get_day_dollars", arguments, expect, day=day, corrupt=corrupt, note=note, rates=rates_text)


def waste_dollars_case(plain, id, period, anchor, shift=None, rates_text=None, *, day="2026-09-14", note=""):
    """get_waste_breakdown's dollar fields: each category's displayed square feet priced."""
    rates = rates_of(rates_text)
    span = window(period, anchor)
    t = plain.sums(*span, shift)
    areas = waste_areas(t)
    minutes = window_minutes(plain, span, shift)
    values = {f"category={k}": money(WASTE_PRICING.get(k, "waste"), round(a, 1), t["gross"], minutes, rates, 0.1) for k, a in areas.items()}
    return case(id, "get_waste_breakdown", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"row_count": len(WASTE), "values": values}, day=day, note=note, rates=rates_text)


def pareto_dollars_case(plain, id, period, anchor, shift=None, rates_text=None, *, day="2026-09-14", note=""):
    """get_downtime_pareto's dollar fields: each reason's displayed minutes at the machine rate."""
    rates = rates_of(rates_text)
    span = window(period, anchor)
    minutes = defaultdict(float)
    for st in plain.between("stops", *span, shift):
        minutes[st.reason] += st.duration_sec / 60
    scheduled = window_minutes(plain, span, shift)
    values = {f"category={r}": money("downtime", round(m, 2), None, scheduled, rates, 0.01) for r, m in minutes.items()}
    return case(id, "get_downtime_pareto", {"window": {"period": period, "anchor": anchor}, "group_by": "reason", "measure": "minutes", "shift": shift},
                {"row_count": len(minutes), "values": values}, day=day, note=note, rates=rates_text)


def opportunities_case(plain, id, period, anchor, shift=None, rates_text=None, *, day="2026-09-14", note="", lens=None):
    """get_opportunities by hand: the speed gap to each cell's best fifth (the speed case's own hours),
    trim points over the target, each downtime reason and the recorded shear and dry-end waste, priced
    and ranked by dollars a year."""
    rates = rates_of(rates_text)
    span = window(period, anchor)
    t = plain.sums(*span, shift)
    gross = round(t["gross"], 1)
    scheduled = window_minutes(plain, span, shift)
    gap_minutes, _ = baseline_gap(plain, span, shift)
    speed_lever = "Speed under each grade's trailing-year speed"
    levers = {speed_lever: money("speed", round(gap_minutes, 2), gross, scheduled, rates, 0.01)}
    terms = {speed_lever: ("speed", round(gap_minutes, 2))}
    trim_pct = round(t["trim"] / t["gross"] * 100, 4)
    over = max(0.0, trim_pct - rates[3])
    levers[f"Side trim over the {rates[3]:g}% target"] = money("trim", over / 100 * gross, gross, scheduled, rates, 1e-4 / 100 * gross)
    terms[f"Side trim over the {rates[3]:g}% target"] = ("trim", over / 100 * gross)
    minutes, stops = defaultdict(float), defaultdict(int)
    for st in plain.between("stops", *span, shift):
        minutes[st.reason] += st.duration_sec / 60
        stops[st.reason] += 1
    for r, m in minutes.items():
        lever = f"Downtime: {r}" + (" (setup time)" if r == "Order change" else " (scheduled time)" if r == "Planned maintenance" else
                                    " (external)" if r in EXTERNAL_REASONS else "")
        levers[lever] = money("downtime", round(m, 2), gross, scheduled, rates, 0.01)
        terms[lever] = ("downtime", round(m, 2))
    labels = {"shear": "Shear chops", "changeover": "Changeover rejects (counted with shear)", "bond": "Dry-end: bond",
              "warp": "Dry-end: warp", "misalignment": "Dry-end: misalignment", "moisture": "Dry-end: moisture",
              "other": "Dry-end: other (cracking, handling, paper, print)"}
    for key, label in labels.items():
        if round(t[key], 1):
            levers[f"{label} (all recorded)"] = money("waste", round(t[key], 1), gross, scheduled, rates, 0.1)
            terms[f"{label} (all recorded)"] = ("waste", round(t[key], 1))
    control = {k: "schedule-driven" if k.startswith("Side trim") or k.endswith("(scheduled time)") else
               "external" if k.endswith("(external)") else "controllable" for k in levers}
    values = {f"rows:lever={k}": {**e, "control": exact(control[k])} for k, e in levers.items()}
    summary = money_summary(terms, levers, gross, scheduled, rates)   # the panel's chips and the arithmetic's sums (#31b review)
    values.update({k: v for k, v in summary.items() if k != "$"})
    values["#0"] = {"lever": exact(max(levers, key=lambda k: levers[k]["usd_per_year"][0]))}
    if lens == "supervisor":   # the controllable levers ranked by dollars a year; the schedule-driven ones carry no rank
        mine = sorted((k for k in levers if control[k] == "controllable"), key=lambda k: -levers[k]["usd_per_year"][0])
        values["#0"] = {"lever": exact(mine[0]), "rank": exact(1)}
        for k in levers:
            values[f"rows:lever={k}"]["rank"] = exact(mine.index(k) + 1 if k in mine else None)
        controllable = {"controllable_levers": exact(len(mine)),
                        "controllable_usd_per_year": [round(sum(levers[k]["usd_per_year"][0] for k in mine), 3), sum(levers[k]["usd_per_year"][1] for k in mine)]}
    values["$"] = {"total_usd_per_year": [round(sum(e["usd_per_year"][0] for e in levers.values()), 3),
                                          sum(e["usd_per_year"][1] for e in levers.values())],
                   "contribution_status": exact("margin not set" if rates[2] is None else "set"), **summary["$"]}
    if lens == "supervisor":
        values["$"].update(controllable)
    arguments = {"window": {"period": period, "anchor": anchor}, "shift": shift, **({"lens": lens} if lens else {})}
    return case(id, "get_opportunities", arguments,
                {"coverage": plain.coverage(*span), "row_count": len(levers), "values": values}, day=day, note=note, rates=rates_text)


def waste_total(plain, span, shift, rates):
    """The waste breakdown's total in dollars, by hand: every line it lists priced as the breakdown prices it
    (waste_dollars_case), added up, but the dry-end total (the sum of its five families, already counted) and the bale
    (the scale reading the lines and its unexplained remainder add to); the remainder counts when the bale is over the
    recorded lines. Returns (usd, usd tolerance, usd a year, its tolerance)."""
    t = plain.sums(*span, shift)
    minutes = window_minutes(plain, span, shift)
    lines = {k: a for k, a in waste_areas(t).items() if k not in ("dry_end", "bale") and (k != "unexplained" or round(a, 1) > 0)}
    priced = [money(WASTE_PRICING.get(k, "waste"), round(a, 1), t["gross"], minutes, rates, 0.1) for k, a in lines.items() if round(a, 1) > 0]
    return (sum(e["usd"][0] for e in priced), sum(e["usd"][1] for e in priced),
            sum(e["usd_per_year"][0] for e in priced), sum(e["usd_per_year"][1] for e in priced))


def ownership_total_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    """The ownership lens's total equals the waste breakdown's total for the same window (the coordinator's re-review of
    PR #123, owner ruling (d): each loss priced exactly once). The expected total is the breakdown's lines priced by hand;
    it is written only when it agrees with the ownership lens's levers summed by hand (ownership_case), the second road."""
    rates = rates_of(None)
    span = window(period, anchor)
    usd, usd_tol, year, year_tol = waste_total(plain, span, shift, rates)
    lens = ownership_case(plain, id, period, anchor, shift, day=day)["expect"]["values"]["$"]
    if abs(lens["total_usd"][0] - usd) > 0.02 or abs(lens["total_usd_per_year"][0] - year) > 2:
        raise AssertionError(f"{id}: the ownership levers add to {lens['total_usd'][0]}, the waste breakdown's lines to {usd}")
    values = {"$": {"total_usd": [round(usd, 6), round(usd_tol, 6)], "total_usd_per_year": [round(year, 3), round(year_tol, 3)]}}
    return case(id, "get_opportunities", {"window": {"period": period, "anchor": anchor}, "shift": shift, "lens": "ownership"},
                {"coverage": plain.coverage(*span), "values": values}, day=day, note=note)


def ownership_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    """get_opportunities with lens=ownership by hand: the total cost of each waste the records hold, side trim first
    (all of it, priced as paper), then by dollars a year shear, the changeover rejects, the cutoff-knife and stacker
    waste boards, planning / admin, the dry-end reasons (paper plus the run time they took) as one group, the tag scrap,
    with its subtotal (the owner, PR #111), and the unexplained remainder (paper); the total of them all. Every waste
    line of the waste breakdown is a lever, each priced once (owner ruling (d) on PR #123; the coordinator's re-review)."""
    rates = rates_of(None)
    span = window(period, anchor)
    t = plain.sums(*span, shift)
    gross = round(t["gross"], 1)
    scheduled = window_minutes(plain, span, shift)
    trim = "Side trim (all recorded)"
    levers = {trim: (money("trim", round(t["trim"], 1), gross, scheduled, rates, 0.1), "schedule-driven")}
    for key, label in (("shear", "Shear chops (all recorded)"), ("changeover", "Changeover rejects (counted with shear) (all recorded)"),
                       ("cutoff_waste", "Cutoff-knife waste boards (all recorded)"), ("stacker_waste", "Stacker waste boards (all recorded)"),
                       ("admin", "Planning / admin (not production waste) (all recorded)"),
                       ("bond", "Dry-end: bond (all recorded)"), ("warp", "Dry-end: warp (all recorded)"),
                       ("misalignment", "Dry-end: misalignment (all recorded)"), ("moisture", "Dry-end: moisture (all recorded)"),
                       ("other", "Dry-end: other (cracking, handling, paper, print) (all recorded)")):
        if round(t[key], 1) > 0:   # planning / admin is bookkeeping, not production waste (reject_reasons.json: controllable "no")
            levers[label] = (money("waste", round(t[key], 1), gross, scheduled, rates, 0.1),
                             "not production waste" if key == "admin" else "controllable")
    unexplained = round(t["bale"] - Plain.accounted(t), 1)   # the bale minus every recorded waste line (issue #100)
    if unexplained > 0:
        levers["Unexplained (bale minus every recorded waste line)"] = (money("paper", unexplained, gross, scheduled, rates, 0.1), "unattributed")
    # the dry-end families are one group, the tag scrap (the owner, PR #111): after trim, the group (by the sum of its
    # kinds) and each other lever by dollars a year; the group holds the rank, its kinds beneath it hold none
    dry = [k for k in levers if k.startswith("Dry-end: ")]
    groups = {k: [k] for k in levers if k != trim and k not in dry}
    if dry:
        groups["dry_end"] = sorted(dry, key=lambda k: -levers[k][0]["usd_per_year"][0])
    year = lambda keys: sum(levers[k][0]["usd_per_year"][0] for k in keys)
    ranked = sorted(groups, key=lambda g: -year(groups[g]))
    rank_of = {trim: 1, **{k: (None if g == "dry_end" else i) for i, g in enumerate(ranked, 2) for k in groups[g]}}
    values = {f"rows:lever={k}": {**levers[k][0], "control": exact(levers[k][1]), "rank": exact(rank_of[k])} for k in levers}
    order = [trim] + [k for g in ranked for k in groups[g]]
    for i, k in enumerate(order):
        values[f"#{i}"] = {"lever": exact(k)}
    # a dry-end family is read against the yield after trim and shear (issue #101), shear against gross
    net = t["gross"] - t["trim"] - t["shear"]
    for key, label in (("bond", "bond"), ("warp", "warp"), ("misalignment", "misalignment"), ("moisture", "moisture"),
                       ("other", "other (cracking, handling, paper, print)")):
        if f"rows:lever=Dry-end: {label} (all recorded)" in values:
            values[f"rows:lever=Dry-end: {label} (all recorded)"]["pct_of_yield"] = num(t[key] / net * 100, 4)
    if "rows:lever=Shear chops (all recorded)" in values:
        values["rows:lever=Shear chops (all recorded)"]["pct_of_gross"] = num(t["shear"] / t["gross"] * 100, 4)
    values["#0"] = {"lever": exact(trim), "pct_of_gross": num(t["trim"] / t["gross"] * 100, 4)}
    values["$"] = {"total_usd_per_year": [round(sum(e["usd_per_year"][0] for e, _ in levers.values()), 3), sum(e["usd_per_year"][1] for e, _ in levers.values())],
                   "total_usd": [round(sum(e["usd"][0] for e, _ in levers.values()), 6), sum(e["usd"][1] for e, _ in levers.values())]}
    if dry:   # the subtotal: its kinds' dollars added up; its share and band are the dry-end waste's over the yield
        mine = groups["dry_end"]
        values["$"].update({"dry_end_rank": exact(ranked.index("dry_end") + 2), "dry_end_kinds": exact(len(mine)),
                            "dry_end_label": exact("Dry-end waste (tag scrap)"),
                            "dry_end_usd_per_year": [round(year(mine), 3), sum(levers[k][0]["usd_per_year"][1] for k in mine) + 1],
                            "dry_end_usd": [round(sum(levers[k][0]["usd"][0] for k in mine), 6), sum(levers[k][0]["usd"][1] for k in mine) + 0.01],
                            "dry_end_sqft": num(t["rejects"], 1), "dry_end_pct_of_yield": num(t["rejects"] / net * 100, 4)})
    return case(id, "get_opportunities", {"window": {"period": period, "anchor": anchor}, "shift": shift, "lens": "ownership"},
                {"coverage": plain.coverage(*span), "row_count": len(levers), "values": values}, day=day, note=note)


def grade_baselines(plain, through):
    """Each grade's sustained speed over the trailing year ending on `through` (targets.json, speed), by hand:
    feet over running minutes of its setups with at least two running minutes, rounded as the tool shows it.
    Returns (baselines, since, last)."""
    last = min(through, max(plain.days))
    since = max(last - timedelta(days=364), min(plain.days))
    feet, run = defaultdict(float), defaultdict(float)
    for s in plain.between("dry_end_setups", since, last):
        minutes = (s.elapsed_seconds - s.stop_time_sec) / 60
        if minutes >= 2.0:
            feet[s.quality_id] += s.lineal_ft
            run[s.quality_id] += minutes
    return {g: round(feet[g] / run[g], 1) for g in feet if run[g]}, since, last


def baseline_gap(plain, span, shift):
    """The speed under each grade's baseline (issue #101), by hand: each grade's sustained speed over the trailing
    year ending on the window's last day (feet over running minutes of its setups with at least two running minutes,
    rounded as the tool shows it), then every wet-end run of the window short of it, in minutes at that speed."""
    baseline = grade_baselines(plain, span[1])[0]
    runs = defaultdict(lambda: [0.0, 0.0])
    grades = {}
    for s in plain.between("dry_end_setups", *span, shift):
        key = (s.production_date, s.wet_end_id)
        runs[key][0] += s.lineal_ft
        runs[key][1] += (s.elapsed_seconds - s.stop_time_sec) / 60
        grades[key] = s.quality_id
    total, under = 0.0, 0
    for key, (f, m) in runs.items():
        base = baseline.get(grades[key])
        if base and m:
            gap = max(0.0, base * m - f)
            total += gap / base
            under += gap > 0
    return total, under


def ymd(text):
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def review_case(plain, id, period, anchor, shift=None, *, day="2026-09-14", note=""):
    """get_downtime_review by hand (issue #101): scheduled minutes from the shift records; each kind and reason's stops,
    minutes, percent of scheduled minutes and minutes per stop, planned maintenance last and unranked; maintenance and
    operator totals; and each flagged shift (maintenance or operator over 2.5% of its scheduled minutes) with the
    reason that led it and that reason's recurrence over the trailing year ending on the flagged day. The previous
    window of the same kind beside: each reason's and each total's minutes and percent of that window's scheduled
    minutes, zero for a reason it lacks and None when the window is outside the records."""
    span = window(period, anchor)
    prev = previous(period, *span)
    prev_available = plain.coverage(*prev) != "unavailable"
    shifts = plain.between("shifts", *span, shift)
    scheduled = sum(sh.scheduled_minutes for sh in shifts)
    prev_scheduled = sum(sh.scheduled_minutes for sh in plain.between("shifts", *prev, shift)) if prev_available else None
    stops = plain.between("stops", *span, shift)
    minutes, count = defaultdict(float), defaultdict(int)
    for st in stops:
        minutes[(st.kind, st.reason)] += st.duration_sec / 60
        count[(st.kind, st.reason)] += 1
    prev_minutes, prev_count = defaultdict(float), defaultdict(int)
    for st in (plain.between("stops", *prev, shift) if prev_available else []):
        prev_minutes[(st.kind, st.reason)] += st.duration_sec / 60
        prev_count[(st.kind, st.reason)] += 1
    then = lambda value: value if prev_available else None
    label = {k: f"{k[1]} · {'scheduled' if k[0] == 'Scheduled' else k[0].lower()}" for k in minutes}
    # ranked: maintenance and operator only; planned maintenance (scheduled time) and external downtime are apart
    ranked = sorted((k for k in minutes if k[0] in ("Maintenance", "Operator")), key=lambda k: (-round(minutes[k], 2), label[k]))
    values = {}
    for k, m in minutes.items():
        values[f"category={label[k]}"] = {"stops": exact(count[k]), "minutes": num(m, 2), "pct_of_scheduled": num(m / scheduled * 100, 4),
                                          "minutes_per_stop": num(m / count[k], 2), "scheduled": exact(k[0] == "Scheduled"),
                                          "external": exact(k[0] == EXTERNAL_KIND),
                                          "rank": exact(ranked.index(k) + 1 if k in ranked else None),
                                          "previous_stops": exact(then(prev_count.get(k, 0))),
                                          "previous_minutes": num(then(prev_minutes.get(k, 0.0)), 2),
                                          "previous_pct_of_scheduled": num(then(prev_minutes.get(k, 0.0) / prev_scheduled * 100 if prev_available else None), 4)}
    envelope = {"scheduled_minutes": exact(scheduled), "previous_scheduled_minutes": exact(prev_scheduled)}
    for key, kinds in (("maintenance", ("Maintenance",)), ("operator", ("Operator",)), ("op_maint", ("Maintenance", "Operator")), ("pm", ("Scheduled",)),
                       ("external", (EXTERNAL_KIND,))):
        m = sum(v for k, v in minutes.items() if k[0] in kinds)
        before = sum(v for k, v in prev_minutes.items() if k[0] in kinds)
        envelope.update({f"{key}_minutes": num(m, 2), f"{key}_stops": exact(sum(c for k, c in count.items() if k[0] in kinds)),
                         f"{key}_pct": num(m / scheduled * 100, 4),
                         f"previous_{key}_minutes": num(then(before), 2),
                         f"previous_{key}_pct": num(before / prev_scheduled * 100 if prev_available else None, 4)})
    # the flags: a shift whose maintenance or operator minutes are over 2.5% of its scheduled minutes
    first_day, last_day = min(plain.days), max(plain.days)
    flags = []
    for sh in shifts:
        mine = [st for st in stops if st.production_date == sh.production_date and st.shift_number == sh.shift_number]
        for kind in ("Maintenance", "Operator"):
            by_reason = defaultdict(float)
            for st in mine:
                if st.kind == kind:
                    by_reason[st.reason] += st.duration_sec / 60
            total = sum(by_reason.values())
            if total / sh.scheduled_minutes * 100 > 2.5:
                lead = max(by_reason, key=lambda r: (by_reason[r], r))
                flags.append({"day": ymd(sh.production_date), "shift": sh.shift_number, "crew": sh.group_name, "kind": kind,
                              "minutes": total, "pct": total / sh.scheduled_minutes * 100, "reason": lead, "reason_minutes": by_reason[lead]})
    flags.sort(key=lambda f: (-round(f["pct"], 4), f["day"], f["shift"], f["kind"]))
    envelope["flag_count"] = exact(len(flags))
    for i, f in enumerate(flags):   # every flag carries its recurrence (review of #111)
        since, through = max(f["day"] - timedelta(days=364), first_day), min(f["day"], last_day)
        year_shifts = plain.between("shifts", since, through, shift)
        year_stops = [st for st in plain.between("stops", since, through, shift) if st.kind in ("Maintenance", "Operator")]
        on_shift = defaultdict(float)
        weekly = defaultdict(lambda: defaultdict(float))
        for st in year_stops:
            on_shift[(st.production_date, st.shift_number, st.kind, st.reason)] += st.duration_sec / 60
            moment = ymd(st.production_date)
            weekly[moment - timedelta(days=moment.weekday())][(st.kind, st.reason)] += st.duration_sec / 60
        times = sum(1 for sh in year_shifts if on_shift[(sh.production_date, sh.shift_number, f["kind"], f["reason"])] > 2.5 / 100 * sh.scheduled_minutes)
        top = sum(1 for reasons in weekly.values() if (f["kind"], f["reason"]) in sorted(reasons, key=lambda k: (-reasons[k], k))[:5])
        values[f"flags:#{i}"] = {"production_day": exact(f["day"].isoformat()), "shift": exact(f["shift"]), "crew": exact(f["crew"]),
                                 "kind": exact(f["kind"]), "reason": exact(f["reason"]), "band": exact("flag"),
                                 "minutes": num(f["minutes"], 2), "pct_of_shift": num(f["pct"], 4), "reason_minutes": num(f["reason_minutes"], 2),
                                 "recurrence_since": exact(since.isoformat()), "shifts_counted": exact(len(year_shifts)),
                                 "times_over_band": exact(times), "weeks_in_top_five": exact(top), "weeks_counted": exact(len(weekly))}
    values["$"] = envelope
    coverage = plain.coverage(*span)
    if coverage != "unavailable":
        coverage = "complete" if coverage == "complete" and plain.coverage(*prev) == "complete" else "partial"
    return case(id, "get_downtime_review", {"window": {"period": period, "anchor": anchor}, "shift": shift},
                {"coverage": coverage, "row_count": len(minutes), "values": values}, day=day, note=note)


def speedvar_side(plain, span, shift, speed):
    """One side of a speed variance by hand: scheduled minutes, the speed with stops, the grade baselines
    (`speed`, the owner's standard of issue #101) at the side's own mix, and per scheduled minute the feet
    below the baseline lost to each stop reason, to order changes (per grade) and to running under the
    baseline (per grade). Stops are matched to their setup record by id here, in Python, to price each
    stopped minute at that setup's grade baseline."""
    setups = plain.between("dry_end_setups", *span, shift)
    minutes = sum(sh.scheduled_minutes for sh in plain.between("shifts", *span, shift))
    target_of = {s.setup_id: speed[s.quality_id] for s in setups}
    grade_of = {s.setup_id: s.quality_id for s in setups}
    stops, setup_loss = defaultdict(lambda: [0.0, 0.0, 0]), defaultdict(float)   # reason -> [feet lost, minutes, count]
    planned, external = defaultdict(lambda: [0.0, 0.0, 0]), defaultdict(lambda: [0.0, 0.0, 0])   # the two buckets apart (PR #111)
    for st in plain.between("stops", *span, shift):
        lost = st.duration_sec / 60 * target_of[st.setup_id]
        if st.reason == "Order change":
            setup_loss[grade_of[st.setup_id]] += lost
        elif st.kind in ("Scheduled", EXTERNAL_KIND):
            cell = (planned if st.kind == "Scheduled" else external)[st.reason]
            cell[0] += lost
            cell[1] += st.duration_sec / 60
            cell[2] += 1
        else:
            cell = stops[st.reason]
            cell[0] += lost
            cell[1] += st.duration_sec / 60
            cell[2] += 1
    below, clock = defaultdict(float), defaultdict(float)
    for s in setups:
        base = speed[s.quality_id]
        below[s.quality_id] += base * (s.elapsed_seconds - s.stop_time_sec) / 60 - s.lineal_ft
        clock[s.quality_id] += s.elapsed_seconds / 60
    return {"minutes": minutes, "speed": sum(s.lineal_ft for s in setups) / minutes,
            "target": sum(speed[g] * m for g, m in clock.items()) / minutes,
            "share": {g: m / minutes for g, m in clock.items()},
            "stops": {r: c[0] / minutes for r, c in stops.items()}, "stop_cells": dict(stops),
            "planned": {r: c[0] / minutes for r, c in planned.items()}, "external": {r: c[0] / minutes for r, c in external.items()},
            "apart_cells": {**{("planned", r): c for r, c in planned.items()}, **{("external", r): c for r, c in external.items()}},
            "setups": {g: v / minutes for g, v in setup_loss.items()}, "below": {g: v / minutes for g, v in below.items()}}


def speedvar_case(plain, id, period, anchor, shift=None, against="baseline", against_shift=None, *, day="2026-09-14", note=""):
    """get_speed_variance by hand (issue #92), every grade held at its trailing-year baseline (issue #101). The gap
    is the reference speed minus this window's speed with stops, taken directly; each bucket is computed on its own
    definition (mix: the reference's grade baselines at its mix minus this window's; each loss: this side's feet
    below the baseline per scheduled minute minus the reference's), so the tool's claim that they add up is checked
    by the sums here, not assumed. The grade mix is schedule-driven and never priced; the controllable buckets that
    are losses are priced as machine time and summed."""
    span = window(period, anchor)
    speed, since, last = grade_baselines(plain, span[1])
    c = speedvar_side(plain, span, shift, speed)
    if against == "baseline":
        clock = defaultdict(float)
        for s in plain.between("dry_end_setups", since, last):
            clock[s.quality_id] += s.elapsed_seconds / 60
        total = sum(clock.values())
        standard = sum(speed[g] * m for g, m in clock.items()) / total
        r = {"speed": standard, "target": standard, "share": {g: m / total for g, m in clock.items()}, "stops": {}, "planned": {}, "external": {},
             "setups": {}, "below": {}}
    else:
        r = speedvar_side(plain, previous(period, *span) if against == "previous" else span,
                          shift if against == "previous" else against_shift, speed)
    gap = r["speed"] - c["speed"]
    minus = lambda key: {k: c[key].get(k, 0.0) - r[key].get(k, 0.0) for k in set(c[key]) | set(r[key])}
    items = {"stops": minus("stops"), "planned": minus("planned"), "external": minus("external"), "setups": minus("setups"),
             "below_baseline": minus("below"),
             "mix": {g: (r["share"].get(g, 0.0) - c["share"].get(g, 0.0)) * (speed[g] - r["target"]) for g in set(c["share"]) | set(r["share"])}}
    buckets = {key: sum(c[key].values()) - sum(r[key].values()) for key in ("stops", "planned", "external", "setups")}
    buckets.update({"below_baseline": sum(c["below"].values()) - sum(r["below"].values()), "mix": r["target"] - c["target"]})
    # planned maintenance and external stops are a bucket only when either side holds one (the owner, PR #111)
    buckets = {k: v for k, v in buckets.items() if k not in ("planned", "external") or items[k]}
    assert abs(sum(buckets.values()) - gap) < 1e-6, "the buckets add up to the gap, taken directly"
    rates = rates_of(None)
    reference = round(r["speed"], 1)
    values = {"$": {"against": exact(against), "speed_all_fpm": num(c["speed"], 1), "reference_speed_fpm": num(r["speed"], 1),
                    "baseline_speed_fpm": num(c["target"], 1), "reference_baseline_speed_fpm": num(r["target"], 1),
                    "baseline_since": exact(since.isoformat()), "baseline_through": exact(last.isoformat()),
                    "gap_fpm": num(gap, 2), "gap_lineal_ft": num(gap * c["minutes"], 1), "scheduled_minutes": num(c["minutes"], 1),
                    "direction": exact("below" if gap > 0 else "above")}}
    controllable = []
    for key, value in buckets.items():
        way = 1 if value >= 0 else -1
        leading = sorted(((v, k) for k, v in items[key].items() if v * way > 0), key=lambda vk: (-vk[0] * way, vk[1]))
        feet = round(value * c["minutes"], 1)
        row = {"gap_fpm": num(value, 2), "gap_lineal_ft": num(value * c["minutes"], 1), "share_of_gap_pct": num(value / gap * 100, 1),
               "top_item": exact(leading[0][1] if leading else None), "top_item_fpm": num(leading[0][0], 2) if leading else exact(None),
               "items": exact(len(items[key]))}
        # the grade mix and planned maintenance are schedule-driven, external stops are external: never priced
        control = "schedule-driven" if key in ("mix", "planned") else "external" if key == "external" else "controllable"
        row["control"] = exact(control)
        if feet > 0 and control == "controllable":   # each controllable loss bucket priced as machine time: its lineal feet at the reference speed
            priced = money("speed", feet / reference, None, c["minutes"], rates, 0.06 * feet / reference ** 2 + 0.06 / reference)
            row.update({k: priced[k] for k in ("machine_time_usd", "usd", "usd_per_year", "contribution_status")})
            controllable.append(priced["usd"][0])
        else:
            row.update({"usd": exact(None), "contribution_status": exact("not priced")})
        values[f"bucket={key}"] = row
        values["$"][f"{key}_share_pct"] = num(value / gap * 100, 1)
    # the controllable dollars: the priced loss buckets, never the grade mix, planned or external stops (each rounded to the cent, as the tool sums them)
    values["$"]["controllable_usd"] = [round(sum(controllable), 2), 0.02 * len(controllable) + 0.01] if controllable else exact(None)
    top_stop = max(c["stop_cells"], key=lambda k: c["stop_cells"][k][0])
    lost, mins, count = c["stop_cells"][top_stop]
    values[f"details:item={top_stop}"] = {"bucket": exact("stops"), "gap_fpm": num(lost / c["minutes"] - r["stops"].get(top_stop, 0.0), 2),
                                          "minutes": num(mins, 2), "stops": exact(count)}
    # planned maintenance and an outage, each in its own bucket: this side's minutes and stops pinned (coordinator re-review)
    for (key, reason), (lost, mins, count) in sorted(c["apart_cells"].items()):
        values[f"details:item={reason}"] = {"bucket": exact(key), "gap_fpm": num(lost / c["minutes"] - r[key].get(reason, 0.0), 2),
                                            "minutes": num(mins, 2), "stops": exact(count)}
    arguments = {"window": {"period": period, "anchor": anchor}, "shift": shift, "against": against}
    if against == "shift":
        arguments["against_shift"] = against_shift
    coverage = plain.coverage(*span)
    if against == "previous":
        coverage = "complete" if coverage == "complete" and plain.coverage(*previous(period, *span)) == "complete" else "partial"
    return case(id, "get_speed_variance", arguments, {"coverage": coverage, "row_count": len(buckets), "values": values}, day=day, note=note)


# ---------------------------------------------------------------- the case list

def generate():
    """The golden window's record families."""
    return generate_window(date.fromisoformat(AS_OF), seed=SEED, machine_id=MACHINE)


def client_case():
    """get_shift_dollars at other rates held to the client formula (issue #31b): the figures web/money.js makes,
    in the browser, of the same shift's quantities at the post's rates (evals/client_money.json, written by
    evals/make_client_money.py). Every field is exact: the card and the server must not disagree by a cent."""
    doc = json.loads((ROOT / "evals" / "client_money.json").read_text())
    out = doc["output"]
    values = {f"rows:gap={r['gap']}": {k: exact(v) for k, v in r.items() if k != "gap"} for r in out["rows"]}
    values["$"] = {k: exact(v) for k, v in out.items() if k != "rows"}
    return case("value-dollars-shift-0914-shift-2-client", "get_shift_dollars", {"shift": doc["shift"]},
                {"coverage": "complete", "row_count": len(out["rows"]), "values": values}, day=doc["day"], rates=doc["rates"],
                note="The card re-prices this shift in the browser at paper $60, machine time $1,350.50, a $35 margin and a 3.1% "
                     "trim target; the server's own answer at those rates must give the same figures, field for field.")


def build(data=None):
    data = data or generate()
    plain = Plain(data)
    cases = []
    # Every KPI tile for September and August, all shifts and per shift.
    cases.append(kpi_case(plain, "value-kpi-september-all", "month", "2026-09-14",
                          note="September against August: every tile, delta and change label."))
    cases.append(kpi_case(plain, "value-kpi-august-all", "month", "2026-08-14",
                          note="August against July, both complete inside the year: every tile, delta and change label."))
    for shift in (1, 2, 3):
        cases.append(kpi_case(plain, f"value-kpi-september-shift-{shift}", "month", "2026-09-14", shift))
        cases.append(kpi_case(plain, f"value-kpi-august-shift-{shift}", "month", "2026-08-14", shift))
    # One production week per shift (and all shifts) and one production day per shift.
    cases.append(kpi_case(plain, "value-kpi-week-0914-all", "week", "2026-09-16"))
    for shift in (1, 2, 3):
        cases.append(kpi_case(plain, f"value-kpi-week-0914-shift-{shift}", "week", "2026-09-16", shift))
        cases.append(kpi_case(plain, f"value-kpi-day-0914-shift-{shift}", "day", "2026-09-14", shift,
                              note="A Monday: the previous production day is the Friday before."))
    # 4-4-5 periods: P2 (31 Aug - 27 Sep) against P1 (3 - 30 Aug), equal 20-day windows; P1 against the prior year's P12.
    cases.append(kpi_case(plain, "value-kpi-fiscal-p2-vs-p1", "fiscal", "2026-09-14",
                          note="Two complete 4-week periods: equal production days, raw totals comparable."))
    cases.append(kpi_case(plain, "value-kpi-fiscal-p1-vs-prior-p12", "fiscal", "2026-08-14",
                          note="P1 (4 weeks) against the prior year's 5-week P12 (29 Jun - 2 Aug): both complete, unequal production days (20 vs 25)."))
    cases.append(kpi_case(plain, "value-kpi-fiscal-first-period-partial", "fiscal", "2025-01-15", day="2025-01-15",
                          note="The data opens on Wednesday 1 Jan 2025 (issue #100), inside P6 of the fiscal year starting 2024-08-05 (30 Dec 2024 - 2 Feb 2025): current partial, previous P5 unavailable, deltas withheld."))
    cases.append(kpi_case(plain, "value-kpi-first-week-no-previous", "week", "2025-01-02", day="2025-01-02",
                          note="The data's first week (30 Dec 2024 - 3 Jan 2025) holds three production days and its previous week is before the dataset: coverage partial, previous null, deltas withheld."))
    cases.append(kpi_case(plain, "value-kpi-second-week-previous-partial", "week", "2025-01-08", day="2025-01-08",
                          note="The data's second week (6 - 10 Jan 2025) is complete; its previous week holds only the data's first three days: previous values shown, partial, deltas withheld (the path value-kpi-first-month-previous-partial held before issue #100)."))
    cases.append(kpi_case(plain, "value-kpi-first-month-previous-unavailable", "month", "2025-01-15", day="2025-01-15",
                          note="Renamed from value-kpi-first-month-previous-partial (issue #100), an intended rename, not a removal (owner ruling (a), 2026-11-12): the data now opens on the first of a month, so the first month has no previous month and no month is partial at the start; value-kpi-second-week-previous-partial keeps the partial-previous path. January 2025 is complete; December 2024 is before the dataset: previous unavailable, deltas withheld."))
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
                            note="August against July: both complete, previous columns and deltas present."))
    cases.append(waste_case(plain, "value-waste-first-week-no-previous", "week", "2025-01-02", day="2025-01-02",
                            note="The window's first week: its previous week is before the dataset, so previous columns and deltas are null."))
    cases.append(waste_case(plain, "value-waste-week-0907", "week", "2026-09-09"))
    # Stops profile: the month with the Monday effect and one week for one shift.
    cases.append(profile_case(plain, "value-stops-profile-august", "month", "2026-08-14"))
    cases.append(profile_case(plain, "value-stops-profile-september", "month", "2026-09-14"))
    cases.append(profile_case(plain, "value-stops-profile-week-0914-shift-1", "week", "2026-09-14", 1))
    # Totals by grade: both months, the window's first week (previous unavailable: null), one week for one shift.
    cases.append(grade_case(plain, "value-grade-totals-september", "month", "2026-09-14"))
    cases.append(grade_case(plain, "value-grade-totals-august", "month", "2026-08-14",
                            note="August against July: previous and delta volume present."))
    cases.append(grade_case(plain, "value-grade-totals-first-week-no-previous", "week", "2025-01-02", day="2025-01-02",
                            note="The previous week is before the dataset: previous and delta volume are null, never zero."))
    cases.append(grade_case(plain, "value-grade-totals-week-0914-shift-2", "week", "2026-09-14", 2))
    # Rollstock (paper consumed) from the wet-end records: both months, the window's first week (previous
    # unavailable: null), and one week for one shift, where the feet are the setups that shift ran on each paper run.
    cases.append(rollstock_case(plain, "value-rollstock-september", "month", "2026-09-14",
                                note="Whole paper runs by the production day they started on; MSF is feet x width, no basis weight."))
    cases.append(rollstock_case(plain, "value-rollstock-august", "month", "2026-08-14",
                                note="August against July: previous feet, MSF, runs and deltas present."))
    cases.append(rollstock_case(plain, "value-rollstock-first-week-no-previous", "week", "2025-01-02", day="2025-01-02",
                                note="The previous week is before the dataset: previous feet, MSF, runs and deltas are null, never zero."))
    cases.append(rollstock_case(plain, "value-rollstock-week-0914-shift-2", "week", "2026-09-14", 2,
                                note="One shift: the setups it ran on each run; a run spanning two shifts counts in both."))
    # Over/under-run by knife: the story week for one shift, and September for all shifts.
    cases.append(overrun_case(plain, "value-overrun-week-0914-shift-2", "week", "2026-09-14", 2))
    cases.append(overrun_case(plain, "value-overrun-september", "month", "2026-09-14"))
    # Speed opportunity: September by grade x width, the story week by grade x crew for one shift.
    cases.append(speed_case(plain, "value-speed-opportunity-september-width", "month", "2026-09-14",
                            note="Best fifth = the fastest fifth of each cell's setups by speed without stops (ntile 5, ratio of sums); setups under two running minutes excluded and counted."))
    cases.append(speed_case(plain, "value-speed-opportunity-week-0907-crew-shift-2", "week", "2026-09-09", "crew", 2))
    # Trends for trim and warp, plus a series that starts before the dataset. The eight- and nine-week
    # series carry the heavy-case budget; the four-week one runs well inside the default.
    cases.append(trend_case(plain, "value-trend-trim-8w", "trim_pct", 8, "2026-09-30", day="2026-09-30", max_ms=HEAVY_MAX_MS))
    cases.append(trend_case(plain, "value-trend-warp-8w", "warp_pct", 8, "2026-09-14", max_ms=HEAVY_MAX_MS))
    cases.append(trend_case(plain, "value-trend-stops-9w-missing-weeks", "stops", 9, "2025-02-12", day="2025-02-12", max_ms=HEAVY_MAX_MS,
                            note="Nine weeks back from 12 Feb 2025 reaches before the data: the weeks of 16 and 23 Dec 2024 are listed as missing, never as zero, and the week of 30 Dec is partial."))
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
    # Ruling (d), owner 2026-11-12: good output is gross minus every recorded waste line, so the lines and good output
    # add back to gross, for a shift (the daily KPI row) and for a month (the waste breakdown).
    cases.append(day_kpi_case(plain, "value-good-output-adds-back-shift", "2026-09-14", 2,
                              note="Owner ruling (d), 2026-11-12: good output is the saleable board, gross minus every recorded waste "
                                   "line. trim_sqft + shear_sqft + reject_sqft + changeover_sqft + waste_board_sqft + admin_sqft + "
                                   "estimated_good_sqft = gross_sqft for the shift; each line is computed here from the reject lines and "
                                   "the lanes, independently of the tool."))
    cases.append(waste_case(plain, "value-good-output-adds-back-month", "month", "2026-09-14",
                            note="Owner ruling (d), 2026-11-12: over September, the waste breakdown's recorded lines (trim, shear, "
                                 "changeover, cutoff-knife and stacker boards, dry-end, planning / admin) plus good_output_sqft add "
                                 "back to gross_sqft; the generator computes good output both as gross minus the month's line "
                                 "totals and as each setup's own good output summed, and writes only when they agree. The bale and its unexplained "
                                 "remainder are a scale reading, not a line."))
    cases.append(day_kpi_case(plain, "value-dq-corrupt-trim-excluded", "2026-09-14", 2, corrupt=True,
                              note="Expected values are sums over the remaining valid setups: the 99,999,999 sq ft trim is reported in excluded_records, not averaged in."))
    cases.append(day_kpi_case(plain, "value-dq-corrupt-other-shift-complete", "2026-09-14", 1, corrupt=True,
                              note="The bad record sits in shift 2; shift 1 stays complete and unchanged."))
    # Dollars (issue #31): the quantities priced at the post's rates, a second time by hand.
    cases.append(shift_dollars_case(plain, "value-dollars-shift-0914-shift-2", "2026-09-14", 2,
                                    note="Where the money went in shift 2: every gap priced at the post's rates; contribution reads 'margin not set', never $0."))
    cases.append(shift_dollars_case(plain, "value-dollars-shift-0930-shift-1", "2026-09-30", 1))
    cases.append(shift_dollars_case(plain, "value-dollars-shift-0917-shift-1-planned-apart", "2026-09-17", 1,
                                    note="Thursday's first shift holds the planned maintenance window (133 minutes on the v2 records): on the card, apart from "
                                         "the ranked losses, and in the all-in total only (the owner, PR #111)."))
    cases.append(day_dollars_case(plain, "value-dollars-day-0916-outage-apart", "2026-09-16",
                                  note="The day of September's power outage (third shift): the outage is apart, unranked, in the all-in total only."))
    cases.append(shift_dollars_case(plain, "value-dollars-shift-0914-shift-2-rates", "2026-09-14", 2, "paper:66,hour:1500,margin:40",
                                    note="The same shift with paper x1.2, machine time x1.25 and a $40/MSF margin: each dollar scales with its own rate only."))
    cases.append(day_dollars_case(plain, "value-dollars-day-0914", "2026-09-14",
                                  note="The card's all-shifts total for the day: each gap summed over the three shifts and priced once on the day, "
                                       "with each shift's own total beside it (issue #31b review: the server's figure, not the browser's)."))
    cases.append(day_dollars_case(plain, "value-dollars-day-0914-rates", "2026-09-14", "paper:60,hour:1350.5,margin:35,trim:3.1"))
    cases.append(day_dollars_case(plain, "value-dollars-day-0914-shifts-1-3", "2026-09-14", shifts=(1, 3),
                                  note="A question about shifts 1 and 3: the day total covers those two shifts and their 960 minutes, never shift 2."))
    cases.append(day_dollars_case(plain, "value-dollars-day-0914-corrupt", "2026-09-14", corrupt=True,
                                  note="Shift 2 holds the refused trim record: it is named in excluded_shifts and left out, and the day is priced "
                                       "over shifts 1 and 3 alone (their gross and 960 minutes), with the excluded record reported."))
    cases.append(waste_dollars_case(plain, "value-dollars-waste-september", "month", "2026-09-14",
                                    note="What waste genuinely costs: each bucket's MSF x paper, plus the run time shear and rejects took."))
    cases.append(pareto_dollars_case(plain, "value-dollars-pareto-week-0907-shift-2", "week", "2026-09-09", 2))
    cases.append(opportunities_case(plain, "value-dollars-opportunities-september", "month", "2026-09-14",
                                    note="Where to look: the levers ranked by dollars a year at the post's volumes."))
    cases.append(opportunities_case(plain, "value-dollars-opportunities-week-0914-shift-2-margin", "week", "2026-09-14", 2, "margin:40,trim:3",
                                    note="A $40/MSF margin and a 3% trim target: contribution is priced beside, never added, and trim points over 3% become a lever."))
    # the owner's downtime answer (issue #101): a day of all shifts, the flagged maintenance shift, a month with many flags
    cases.append(review_case(plain, "value-review-day-0930", "day", "2026-09-30", day="2026-09-30",
                             note="The golden year's last production day, all shifts: a flagged operator shift (third shift, glue / starch) and its recurrence over the year."))
    cases.append(review_case(plain, "value-review-day-0504-shift-1", "day", "2026-05-04", 1, day="2026-05-04",
                             note="First shift on the day of a power outage (14 minutes on the v2 records): the outage is external (the owner, PR #111), so it is listed apart and never in the shift's maintenance, which is on target; the flag that fires is the shift's operator downtime."))
    cases.append(review_case(plain, "value-review-september", "month", "2026-09-14",
                             note="A month: every one of its many flags, each with its own trailing year."))
    cases.append(review_case(plain, "value-review-day-0928", "day", "2026-09-28", day="2026-09-28",
                             note="One operator flag on the v2 records (first shift, led by order changes), whose leading reason was over its band on its own once more in the year, so recurrence is not zero."))
    cases.append(review_case(plain, "value-review-day-0907", "day", "2026-09-07", day="2026-09-07",
                             note="Two operator flags (shifts 1 and 3), each led by missed splices that were over the band on their own once more in the year (yesterday-review-from-tuesday asks for this day; issue #100's records)."))
    cases.append(review_case(plain, "value-review-first-month-shift-2", "month", "2025-01-15", 2, day="2025-01-15",
                             note="The data's first month, January 2025 (issue #100: the records start on 1 January 2025; anchored on September 2025, the old rolling year's first month, before): the previous month is outside the records, so every previous figure is None, never zero."))
    # the owner's two lenses on the same levers (issue #101), totals recomputed here, not with the cost model
    cases.append(opportunities_case(plain, "value-dollars-supervisor-september", "month", "2026-09-14", lens="supervisor",
                                    note="The supervisor lens: the controllable losses ranked, schedule-driven trim and planned maintenance unranked."))
    cases.append(ownership_case(plain, "value-dollars-ownership-september", "month", "2026-09-14",
                                note="The ownership lens: the total cost of waste, side trim first. Every line of the waste breakdown is "
                                     "a lever, each priced once (owner ruling (d) on PR #123): the changeover rejects, the cutoff-knife "
                                     "and stacker waste boards and planning / admin (not production waste) included."))
    cases.append(ownership_total_case(plain, "value-ownership-total-is-the-waste-breakdown-september", "month", "2026-09-14",
                                      note="The coordinator's re-review of PR #123 (owner ruling (d)): the ownership lens's total equals "
                                           "the waste breakdown's total for September, every line it lists priced once, but the dry-end "
                                           "total (its five families are lines already) and the bale (the scale reading the lines and the "
                                           "unexplained remainder add to). The lens priced the cutoff-knife and stacker boards and planning "
                                           "/ admin zero times before."))
    cases.append(ownership_total_case(plain, "value-ownership-total-is-the-waste-breakdown-week-0914-shift-2", "week", "2026-09-14", 2,
                                      note="The same identity for one shift over the story week."))
    # Speed variance (issue #92): the gap split into stops, setups, below-target running and grade mix, by hand,
    # against target (the usual grade mix), against the previous week, and one shift against another.
    cases.append(speedvar_case(plain, "value-speedvar-september-baseline", "month", "2026-09-14",
                               note="Against each grade's trailing-year baseline at the usual grade mix (the share of the setup clock over the same year; issue #101): the buckets add up to the gap taken directly, and the grade mix is schedule-driven, never priced."))
    cases.append(speedvar_case(plain, "value-speedvar-week-0914-shift-2-previous", "week", "2026-09-14", 2, "previous",
                               note="Against the previous week: each loss is this week's feet below the grade baselines per scheduled minute minus the week before's; an offset bucket is not priced."))
    cases.append(speedvar_case(plain, "value-speedvar-week-0914-shift-3-vs-shift-1", "week", "2026-09-14", 3, "shift", 1,
                               note="Shift 3 against shift 1 over the same week: shift 1's Thursday planned maintenance makes stops an offset; the heavier grade mix of shift 3 is part of its gap."))
    cases.append(client_case())
    assert len({c["id"] for c in cases}) == len(cases)
    return {
        "about": "GENERATED by evals/make_value_goldens.py from plain Python sums over synth.generate_window() for the window "
                 "from first_day to as_of; do not edit by hand. Each value is [expected, tolerance]; tolerance null means exact. "
                 "evals/run.py scores them against the store of that window and refuses them when content_hash differs.",
        "generator": "evals/make_value_goldens.py",
        "seed": SEED, "as_of": AS_OF, "first_day": FIRST_DAY, "machine_id": MACHINE,
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


def check_rates():
    """The post's numbers typed here must equal the application's defaults (rates.json)."""
    defaults = json.loads((ROOT / "src" / "investigator" / "rates.json").read_text())
    rates = {r["short"]: r["default"] for r in defaults["rates"]}
    facts = {a["id"]: a["value"] for a in defaults["assumptions"]}
    mine = {"paper": PAPER, "hour": HOUR, "margin": None, "trim": TRIM_TARGET}
    if rates != mine or facts["annual_msf_per_machine"] != ANNUAL_MSF or facts["required_fpm"] * facts["net_width_in"] / 12 / 1000 != MSF_PER_MIN:
        raise SystemExit(f"rates.json drifted from the post's numbers here: {rates} vs {mine}")


def render(golden):
    return json.dumps(golden, indent=1) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='compare with the committed file instead of writing')
    args = parser.parse_args(argv)
    check_catalog()
    check_rates()
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
