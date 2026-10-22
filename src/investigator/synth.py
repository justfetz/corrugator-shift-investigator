"""Deterministic multi-month synthetic generator for the demo record families.

`generate_months()` lays out every production shift of the requested months
for one corrugator and emits dry-end setups, wet ends, shifts and stops
(see `records.py`, docs/RECORDS.md). The same seed gives byte-identical
JSONL and the same `content_hash`. Simulation settings here are demo
choices, not plant standards; the seeded stories come from `stories.py`,
which no tool or analysis module reads.

Calendar: production days are Monday to Friday (weekends are plant-down and
emit nothing; there is no holiday calendar). Each production day has three
480-minute shifts starting 07:00 and is labelled by its start date.
"""
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import random

from . import records as R
from . import stories
from .fixture import GRADES

CHOP_LENGTH_IN = 34                       # one full-web shear chop
MIN_RUN_SECONDS = 60                      # smallest running time of a record
CREW_SPEED_FACTOR = {"Crew A": 0.97, "Crew B": 0.94, "Crew C": 0.91}
SHIFT_SPEED_FACTOR = {1: 1.0, 2: 1.0, 3: 0.97}
SPEED_NOISE = (0.94, 1.04)
TRAVEL_FT = (2800, 11500)                 # order travel per setup: lo + span*r^1.5 (about 7,000 lineal ft per setup)
SETUPS_PER_WET_END = (2, 2, 3, 3, 4, 4, 5)  # drawn per non-single wet end (about 20,000 lineal ft per paper run)
WEEK_SPEED_FACTOR = (0.95, 1.05)          # one draw per production week: week-to-week volume moves a few percent
ORDER_CHANGE_CHOPS = (12, 20)             # chops when the knife combination changes
CONTINUATION_CHOPS = (7, 12)              # chops when only one knife changes
SPLICERS = tuple(f"Splicer {i}" for i in range(1, 6))
KNIFE_PLACES = ("Upper knife", "Lower knife", "Shear knife", "Slitter/scorer")
PM_WEEKDAY, PM_SHIFT = 3, 1               # Thursday, shift 1
PM_MINUTES = (120, 135)
PM_PLACES = ("Double backer", "Single facer", "Glue dams", "Steam system", "Stacker")
POWER_OUTAGE_MINUTES = (10, 15)           # one per month
MONDAY_MAINTENANCE, AUGUST_MAINTENANCE, MONDAY_OPERATOR = 1.40, 1.08, 1.15
RUN_MINUTES_PER_SHIFT = 460               # budget basis for the stop rates

# Unplanned stop catalogue: reason, candidate places, expected minutes per
# RUN_MINUTES_PER_SHIFT of running time, mean duration in seconds.
MAINTENANCE_STOPS = (
    ("Knife / slitter jam", KNIFE_PLACES, 0.92, 110),
    ("Steam / moisture", ("Steam system", "Double backer"), 0.40, 150),
    ("Glue / starch", ("Glue dams",), 0.28, 120),
    ("Stacker", ("Stacker",), 0.40, 90),
    ("Other", ("Single facer", "Double backer") + SPLICERS, 0.56, 130),
)
OPERATOR_STOPS = (
    ("Order change", ("Upper knife", "Lower knife", "Stacker"), 2.4, 75),
    ("Raw material (roll)", SPLICERS, 1.9, 120),
    ("Missed splice", SPLICERS, 2.0, 90),
    ("Stacker", ("Stacker",), 1.4, 70),
    ("Glue / starch", ("Glue dams",), 0.9, 100),
    ("Other", ("Single facer", "Double backer", "Shear knife"), 0.9, 80),
)
NOTES = {
    "Knife / slitter jam": "Jam cleared at {place}; blade and anvil condition not inspected",
    "Missed splice": "Splice missed at {place}; web break, chops run until the web settled",
    "Raw material (roll)": "Roll change delayed at {place}; roll condition noted by crew",
    "Stacker": "Stacker fault reported at {place}; sheets re-stacked by hand",
    "Order change": "Changeover pause at {place} while the new order was set",
    "Glue / starch": "Glue application issue reported at {place}",
    "Steam / moisture": "Steam or moisture issue reported at {place}",
    "Power outage": "Power interruption; machine restarted after supply returned",
    "Other": "Unclassified stop reported at {place}",
}


def production_days(months):
    """Monday-Friday dates of the given (year, month) pairs, in order."""
    days = []
    for year, month in months:
        year, month = int(year), int(month)
        for day in range(1, monthrange(year, month)[1] + 1):
            moment = date(year, month, day)
            if moment.weekday() < 5:
                days.append(moment)
    return days


@dataclass
class _Shift:
    day: date
    number: int
    start: datetime
    end: datetime
    crew: str
    contiguous_next: bool = True

    @property
    def date_text(self):
        return self.day.strftime("%Y%m%d")


def _shift_slots(days):
    anchor = days[0] - timedelta(days=days[0].weekday())
    slots = []
    for day in days:
        week = (day - anchor).days // 7
        for number in (1, 2, 3):
            start, end = R.shift_window(day.strftime("%Y%m%d"), number)
            slots.append(_Shift(day, number, start, end, R.CREWS[(number - 1 + week) % 3]))
    for current, following in zip(slots, slots[1:]):
        current.contiguous_next = following.start == current.end
    slots[-1].contiguous_next = False
    return slots


@dataclass
class _Context:
    """Everything the stop and quality draws need to know about a shift."""
    slot: _Shift
    monday: bool
    month: int
    post_rebuild: bool
    splice_story: bool
    trim_target_pct: float
    pm: tuple | None            # (minutes, place, note) for this shift's planned window
    outage: bool
    week_factor: float = 1.0    # the production week's speed draw (WEEK_SPEED_FACTOR)


def _week_factors(days, seed):
    """One speed factor per ISO production week, drawn from its own stream so the week-to-week
    movement does not depend on how many setups the previous week produced."""
    rng = random.Random(f"{seed}:weeks")
    weeks = sorted({d.isocalendar()[:2] for d in days})
    return {week: rng.uniform(*WEEK_SPEED_FACTOR) for week in weeks}


def _context(slot, outage_slots, week_factors=None):
    day = slot.day
    rebuild = stories.SLITTER_REBUILD
    after = max(0, (day - rebuild["pm_date"]).days)
    trim_target = rebuild["baseline_trim_pct"] + rebuild["trim_creep_pp_per_week"] * after / 7
    pm = None
    if day.weekday() == PM_WEEKDAY and slot.number == PM_SHIFT:
        if day == rebuild["pm_date"] and slot.number == rebuild["pm_shift"]:
            pm = (rebuild["pm_minutes"], rebuild["place"], "Planned window: slitter/scorer rebuild")
        else:
            index = (day - date(2026, 1, 1)).days // 7
            pm = (None, PM_PLACES[index % len(PM_PLACES)], "Planned maintenance window: {place}")
    splice = stories.SPLICER3_MISSED_SPLICE
    return _Context(slot, day.weekday() == 0, day.month, day > rebuild["pm_date"],
                    splice["start"] <= day <= splice["end"], trim_target, pm,
                    (slot.day, slot.number) in outage_slots,
                    (week_factors or {}).get(day.isocalendar()[:2], 1.0))


def _poisson(rng, lam):
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _stochastic_round(rng, value):
    return int(math.floor(value + rng.random()))


def _fit_new(rng, occupied, count):
    """Return `count` (width, outs) pairs occupying exactly `occupied` inches."""
    if count == 1:
        for outs in rng.sample((1, 2, 3, 4), 4):
            if occupied % outs == 0 and 12 <= occupied // outs <= 60:
                return [(occupied // outs, outs)]
        return None
    for _ in range(80):
        first_w, first_o, second_o = rng.randint(12, 60), rng.randint(1, 3), rng.randint(1, 3)
        rest = occupied - first_w * first_o
        if rest >= 12 * second_o and rest % second_o == 0 and rest // second_o <= 60:
            return [(first_w, first_o), (rest // second_o, second_o)]
    return None


def _fit_setup(rng, width, trim_target_pct, fixed, count):
    """Choose trim inches near the target and fit `count` new orders beside
    the fixed (continuing) order. Returns (trim_in, pairs) or None."""
    wanted = _stochastic_round(rng, trim_target_pct * width / 100)
    wanted = min(14, max(2, wanted))
    taken = fixed[0] * fixed[1] if fixed else 0
    for delta in (0, 1, -1, 2, -2, 3, -3):
        trim_in = wanted + delta
        if not 2 <= trim_in <= 14:
            continue
        occupied = width - trim_in - taken
        if occupied < 12 * count:
            continue
        pairs = _fit_new(rng, occupied, count)
        if pairs:
            return trim_in, pairs
    return None


class _Planner:
    """Generates wet ends and the setups inside them, ahead of any clock."""

    def __init__(self, rng):
        self.rng = rng
        self.order_seq = 820000
        self.wet = None
        self.wet_count = 0
        self.wet_seq = {}
        self.grade_queue = []

    def abandon(self):
        """Drop the current wet end (used at a non-contiguous shift boundary)."""
        self.wet = None

    def _order(self, width_in, outs, quality):
        self.order_seq += 1
        thick = quality.endswith(("BC", "EB"))
        return {"order_number": f"SO-{self.order_seq}",
                "part_number": f"P-{self.rng.randint(10000, 99999)}",
                "w": width_in, "outs": outs, "L": self.rng.randint(20, 92),
                "stack": self.rng.randint(80, 160) if thick else self.rng.randint(150, 300)}

    def _grade(self):
        if not self.grade_queue:
            self.grade_queue = list(GRADES)
            self.rng.shuffle(self.grade_queue)
        return self.grade_queue.pop()

    def _new_wet_end(self, ctx):
        rng = self.rng
        width = rng.choices(R.WEB_WIDTHS, weights=R.WEB_WIDTH_WEIGHTS)[0]
        quality = self._grade()
        single = self.wet_count % 5 == 4
        self.wet_count += 1
        seq = self.wet_seq.get(ctx.slot.date_text, 0) + 1
        self.wet_seq[ctx.slot.date_text] = seq
        wet = {"id": f"WE{ctx.slot.date_text}{seq:03d}", "quality": quality, "width": width,
               "single": single, "left": 1 if single else rng.choice(SETUPS_PER_WET_END),
               "index": 0, "knife1": None}
        if not single:
            while True:
                w1, o1 = rng.randint(12, 60), rng.randint(1, 3)
                if w1 * o1 <= width - 26:
                    break
            wet["knife1"] = self._order(w1, o1, quality)
        self.wet = wet

    def next_setup(self, ctx):
        rng = self.rng
        if self.wet is None or self.wet["left"] == 0:
            self._new_wet_end(ctx)
        wet = self.wet
        wet["left"] -= 1
        quality, width = wet["quality"], wet["width"]
        if wet["single"]:
            trim_in, pairs = _fit_setup(rng, width, ctx.trim_target_pct, None, 1)
            specs, code = [self._order(*pairs[0], quality)], "O"
        else:
            keep = wet["index"] > 0 and rng.random() >= 0.3
            count = 2 if rng.random() < 0.15 else 1
            fixed = wet["knife1"]
            fit = _fit_setup(rng, width, ctx.trim_target_pct, (fixed["w"], fixed["outs"]), count)
            if fit is None:
                fit = _fit_setup(rng, width, ctx.trim_target_pct, (fixed["w"], fixed["outs"]), 1)
            if fit is None:  # the continuing order no longer fits beside anything: replace it
                keep = False
                fit = _fit_setup(rng, width, ctx.trim_target_pct, None, 2) or \
                    _fit_setup(rng, width, ctx.trim_target_pct, None, 1)
                if len(fit[1]) == 1:
                    specs = [self._order(*fit[1][0], quality)]
                    wet["knife1"] = specs[0]
                else:
                    specs = [self._order(*pair, quality) for pair in fit[1]]
                    wet["knife1"] = specs[0]
            else:
                if not keep:
                    wet["knife1"] = self._order(fixed["w"], fixed["outs"], quality)
                specs = [wet["knife1"]] + [self._order(*pair, quality) for pair in fit[1]]
            trim_in = fit[0]
            code = "C" if keep else "O"
        wet["index"] += 1
        chops = rng.randint(*(CONTINUATION_CHOPS if code == "C" else ORDER_CHANGE_CHOPS))
        travel = TRAVEL_FT[0] + TRAVEL_FT[1] * rng.random() ** 1.5
        fpm = (GRADES[quality] * CREW_SPEED_FACTOR[ctx.slot.crew] * ctx.week_factor
               * SHIFT_SPEED_FACTOR[ctx.slot.number] * rng.uniform(*SPEED_NOISE))
        run = max(MIN_RUN_SECONDS, round((travel + chops * CHOP_LENGTH_IN / 12) / fpm * 60))
        return {"quality": quality, "width": width, "trim_in": trim_in, "specs": specs,
                "code": code, "chops": chops, "fpm": fpm, "run": run, "wet_end_id": wet["id"]}


def _stop(kind, reason, place, seconds, chops=0, note=None):
    return {"kind": kind, "reason": reason, "place": place, "seconds": int(seconds),
            "chops": chops, "note": (note or NOTES[reason]).format(place=place)}


def _draw_stops(rng, ctx, run_seconds):
    """Unplanned stops for a setup with `run_seconds` of running time."""
    rebuild, splice = stories.SLITTER_REBUILD, stories.SPLICER3_MISSED_SPLICE
    run_minutes = run_seconds / 60
    stops = []
    catalogue = [("Maintenance", row) for row in MAINTENANCE_STOPS] + \
                [("Operator", row) for row in OPERATOR_STOPS]
    if ctx.splice_story:
        extra = splice["minutes_multiplier"] - 1
        catalogue.append(("Operator", ("Missed splice", (splice["place"],), 2.0 * extra, 90)))
    for kind, (reason, places, minutes, mean_seconds) in catalogue:
        scale = 1.0
        if kind == "Maintenance":
            scale *= (MONDAY_MAINTENANCE if ctx.monday else 1) * (AUGUST_MAINTENANCE if ctx.month == 8 else 1)
            if reason == "Knife / slitter jam" and ctx.post_rebuild:
                scale *= rebuild["knife_slitter_minutes_multiplier"]
        else:
            scale *= MONDAY_OPERATOR if ctx.monday else 1
        lam = minutes * scale / RUN_MINUTES_PER_SHIFT * run_minutes / (mean_seconds / 60)
        for _ in range(_poisson(rng, lam)):
            seconds = max(20, round(mean_seconds * rng.uniform(0.5, 1.5)))
            if reason == "Knife / slitter jam":
                share = rebuild["slitter_place_share_after" if ctx.post_rebuild else "slitter_place_share_before"]
                place = rebuild["place"] if rng.random() < share else rng.choice(
                    [p for p in places if p != rebuild["place"]])
            else:
                place = rng.choice(places)
            chops = rng.randint(*splice["web_break_chops"]) if reason == "Missed splice" else 0
            stops.append((rng.uniform(0, run_seconds) if reason != "Order change" else 0.0,
                          _stop(kind, reason, place, seconds, chops)))
    return stops


def _insert_stop(segments, stop, after_run):
    """Insert a stop segment once `after_run` seconds of running time have passed."""
    seen = 0.0
    for index, segment in enumerate(segments):
        if segment[0] != "run":
            continue
        if seen + segment[1] >= after_run:
            head = int(round(after_run - seen))
            head = min(max(head, 0), segment[1])
            segments[index:index + 1] = [("run", head), ("stop", stop["seconds"], stop),
                                         ("run", segment[1] - head)]
            return
        seen += segment[1]
    segments.append(("stop", stop["seconds"], stop))


def _segments(rng, plan, ctx):
    segments = [("run", plan["run"])]
    for offset, stop in _draw_stops(rng, ctx, plan["run"]):
        _insert_stop(segments, stop, offset)
    return segments


def _run_seconds(segments):
    return sum(s[1] for s in segments if s[0] == "run")


def _split(segments, boundary):
    """Split segments at `boundary` seconds. A stop that would straddle the
    boundary moves whole into the second part; the first part runs instead."""
    first, second, seen = [], [], 0
    for segment in segments:
        if seen >= boundary:
            second.append(segment)
        elif seen + segment[1] <= boundary:
            first.append(segment)
            seen += segment[1]
        elif segment[0] == "run":
            first.append(("run", boundary - seen))
            second.append(("run", seen + segment[1] - boundary))
            seen = boundary
        else:
            first.append(("run", boundary - seen))
            second.append(segment)
            seen = boundary
    return first, second


@dataclass
class _Frame:
    plan: dict
    segments: list
    start: datetime
    slot: _Shift
    setup_id: str
    base_chops: int
    split_from: str | None = None

    @property
    def code(self):
        return "S" if self.split_from else self.plan["code"]


def _machine_code(machine_id):
    text = "".join(ch for ch in machine_id if ch.isalnum()).upper()
    return (text + "0000")[:4]


def _layout(rng, slots, planner, machine_code, outage_slots, week_factors=None):
    frames, pending, per_slot = [], None, {}
    for slot in slots:
        ctx = _context(slot, outage_slots, week_factors)
        cursor = slot.start
        while cursor < slot.end:
            if pending:
                plan, segments, split_from, base_chops = pending
                pending = None
            else:
                plan = planner.next_setup(ctx)
                segments, split_from, base_chops = _segments(rng, plan, ctx), None, plan["chops"]
            if cursor == slot.start:
                run = _run_seconds(segments)
                if ctx.pm:
                    minutes, place, note = ctx.pm
                    minutes = minutes or rng.randint(*PM_MINUTES)
                    _insert_stop(segments, _stop("PM", "Planned maintenance", place, minutes * 60, 0, note),
                                 min(run, rng.randint(300, 900)))
                if ctx.outage:
                    _insert_stop(segments, _stop("Maintenance", "Power outage", "Power",
                                                 rng.randint(*POWER_OUTAGE_MINUTES) * 60),
                                 min(run, rng.randint(600, 20000)))
            boundary = int((slot.end - cursor).total_seconds())
            total = sum(s[1] for s in segments)

            def emit(parts, split=split_from, chops=base_chops):
                seq = per_slot.get(id(slot), 0) + 1
                per_slot[id(slot)] = seq
                day_text, time_text = R.stamp(cursor)
                frame = _Frame(plan, parts, cursor, slot,
                               f"{machine_code}{day_text}{time_text}{seq % 100:02d}", chops, split)
                frames.append(frame)
                return frame

            if total <= boundary:
                emit(segments)
                cursor += timedelta(seconds=total)
                continue
            first, second = _split(segments, boundary)
            if _run_seconds(second) < MIN_RUN_SECONDS:
                emit(first)                       # remainder too small: close here
            elif _run_seconds(first) < MIN_RUN_SECONDS and frames and frames[-1].slot is slot:
                frames[-1].segments.append(("run", boundary))   # stretch the prior record
                if slot.contiguous_next:
                    pending = (plan, segments, split_from, base_chops)
            else:
                frame = emit(first)
                if slot.contiguous_next:
                    pending = (plan, second, frame.setup_id, 0)
            cursor = slot.end
        if not slot.contiguous_next:
            pending = None
            planner.abandon()
    return frames


def _reject_detail(rng, reason, weights):
    """One reported sub-reason per reject line, drawn by the family's weights (deterministic per rng)."""
    options = R.REJECT_DETAILS[reason]
    if len(options) == 1:
        return options[0]
    mix = weights[reason]
    return rng.choices(options, weights=[mix[o] for o in options])[0]


def _actual_cuts(rng, nominal):
    draw = rng.random()
    if draw < 0.85:
        delta = rng.uniform(-0.02, 0.02)
    elif draw < 0.97:
        delta = rng.choice((-1, 1)) * rng.uniform(0.02, 0.08)
    else:
        delta = rng.choice((-1, 1)) * rng.uniform(0.08, 0.099)
    actual = round(nominal * (1 + delta))
    return min(max(actual, math.ceil(nominal * 0.9)), math.floor(nominal * 1.1))


def _setup_record(rng, frame, ctx, machine_id):
    plan, width = frame.plan, frame.plan["width"]
    elapsed = sum(s[1] for s in frame.segments)
    run = _run_seconds(frame.segments)
    stop_segments = [s for s in frame.segments if s[0] == "stop"]
    end = frame.start + timedelta(seconds=elapsed)
    lineal = round(run * plan["fpm"] / 60, 1)
    chops = frame.base_chops + sum(s[2]["chops"] for s in stop_segments)
    chop_sqft = round(chops * CHOP_LENGTH_IN / 12 * width / 12, 1)
    order_travel = lineal - chop_sqft * 12 / width
    gross = round(lineal * width / 12, 1)
    trim = round(order_travel * plan["trim_in"] / 12, 1)
    occupied = width - plan["trim_in"]
    warp = stories.WEEKEND_WARP
    rates = {"BOND": warp["bond_rate"],
             "WARP": warp["warp_rate_by_month"].get(ctx.month, warp["warp_rate_by_month"][9])
             * (warp["monday_multiplier"] if ctx.monday else 1),
             "MISALIGNMENT": warp["misalignment_rate"], "MOISTURE": warp["moisture_rate"]}
    detail_weights = {"WARP": warp["warp_detail_weights"], "BOND": warp["bond_detail_weights"]}
    customers, rejects = [], []
    for position, spec in enumerate(plan["specs"], 1):
        nominal = max(1, round(order_travel * 12 / spec["L"]))
        actual = _actual_cuts(rng, nominal)
        area, share = spec["w"] * spec["L"] / 144, spec["w"] * spec["outs"] / occupied
        waste = {"hqm": 0.0, "asm": 0.0}
        for reason in R.REJECT_REASONS:
            target = gross * rates[reason] * rng.uniform(0.5, 1.5) * share
            sheets = min(_stochastic_round(rng, target / area), actual * spec["outs"])
            if sheets:
                sqft = round(sheets * area, 1)
                rejects.append(R.RejectEntry(reason, position, sheets, sqft, _reject_detail(rng, reason, detail_weights)))
                waste["hqm" if reason == "MISALIGNMENT" else "asm"] += sqft
        customers.append(R.CustomerBlock(spec["order_number"], spec["part_number"], spec["w"],
                                         spec["L"], spec["outs"], position, nominal, actual,
                                         round(waste["hqm"], 1), round(waste["asm"], 1), spec["stack"]))
    start_date, start_time = R.stamp(frame.start)
    end_date, end_time = R.stamp(end)
    record = R.DryEndSetupRecord(
        setup_id=frame.setup_id, machine_id=machine_id, start_date=start_date, start_time=start_time,
        end_date=end_date, end_time=end_time, lineal_ft=lineal,
        avg_speed_fpm=round(lineal / (elapsed / 60), 1), web_width_in=width,
        quality_id=plan["quality"],
        total_nominal_ft=round(customers[0].nominal_cuts * customers[0].sheet_length_in / 12, 1),
        total_produced_sqft=gross, trim_sqft=trim, trim_pct=round(trim / gross * 100, 2),
        change_code=frame.code, stop_time_sec=sum(s[1] for s in stop_segments),
        number_of_stops=len(stop_segments), shift_number=frame.slot.number, chop_sqft=chop_sqft,
        kqf_sqft=round(sum(r.sqft for r in rejects), 1), customers=customers,
        production_date=frame.slot.date_text, rejects=rejects, wet_end_id=plan["wet_end_id"],
        split_from_setup_id=frame.split_from)
    stops, moment = [], frame.start
    for segment in frame.segments:
        if segment[0] == "stop":
            payload = segment[2]
            finish = moment + timedelta(seconds=segment[1])
            stops.append(R.StopRecord(
                stop_id="ST" + "".join(R.stamp(moment)), start_date=R.stamp(moment)[0],
                start_time=R.stamp(moment)[1], end_date=R.stamp(finish)[0], end_time=R.stamp(finish)[1],
                duration_sec=segment[1], kind=payload["kind"], place=payload["place"],
                reason=payload["reason"], shift_number=frame.slot.number,
                production_date=frame.slot.date_text, setup_id=frame.setup_id,
                user_info=payload["note"] + " (synthetic note, unverified)"))
        moment += timedelta(seconds=segment[1])
    return record, stops


def _wet_end_records(setups):
    groups = {}
    for setup in setups:
        groups.setdefault(setup.wet_end_id, []).append(setup)
    records = []
    for wet_end_id, members in groups.items():
        start, end = members[0].start, members[-1].end
        lineal = round(sum(s.lineal_ft for s in members), 1)
        shifts = []
        for s in members:
            if s.shift_number not in shifts:
                shifts.append(s.shift_number)
        records.append(R.WetEndRecord(
            wet_end_id=wet_end_id, quality_id=members[0].quality_id, web_width_in=members[0].web_width_in,
            start_date=R.stamp(start)[0], start_time=R.stamp(start)[1], end_date=R.stamp(end)[0],
            end_time=R.stamp(end)[1], duration=R.hhmmss((end - start).total_seconds()),
            lineal_ft=lineal, total_produced_sqft=round(sum(s.total_produced_sqft for s in members), 1),
            side_trim_sqft=round(sum(s.trim_sqft for s in members), 1),
            max_speed_fpm=round(max(s.running_speed_fpm for s in members), 1),
            avg_speed_fpm=round(lineal / ((end - start).total_seconds() / 60), 1),
            setup_ids=[s.setup_id for s in members], shift_numbers=shifts,
            production_date=members[0].production_date))
    return records


def _shift_records(rng, slots, setups):
    by_slot = {}
    for setup in setups:
        by_slot.setdefault((setup.production_date, setup.shift_number), []).append(setup)
    records = []
    for slot in slots:
        members = by_slot[(slot.date_text, slot.number)]
        lineal = round(sum(s.lineal_ft for s in members), 1)
        gross = round(sum(s.total_produced_sqft for s in members), 1)
        trim = round(sum(s.trim_sqft for s in members), 1)
        chop = round(sum(s.chop_sqft for s in members), 1)
        reject = round(sum(s.reject_sqft for s in members), 1)
        wet_end_ids = []
        for s in members:
            if s.wet_end_id not in wet_end_ids:
                wet_end_ids.append(s.wet_end_id)
        records.append(R.ShiftRecord(
            shift_number=slot.number, production_date=slot.date_text, group_name=slot.crew,
            start_date=R.stamp(slot.start)[0], start_time=R.stamp(slot.start)[1],
            end_date=R.stamp(slot.end)[0], end_time=R.stamp(slot.end)[1],
            duration=R.hhmmss(R.SCHEDULED_SHIFT_MINUTES * 60), total_lineal_ft=lineal,
            total_produced_sqft=gross, side_trim_sqft=trim, chop_sqft=chop, reject_sqft=reject,
            max_speed_fpm=round(max(s.running_speed_fpm for s in members), 1),
            avg_speed_fpm=round(lineal / R.SCHEDULED_SHIFT_MINUTES, 1), wet_end_ids=wet_end_ids,
            scheduled_minutes=R.SCHEDULED_SHIFT_MINUTES,
            bale_sqft=round(trim + chop + reject + gross * rng.uniform(0.001, 0.004), 1)))
    return records


def generate_months(seed=2026, months=(("2026", 8), ("2026", 9)), machine_id="CORR-1"):
    """Generate every record family for the production days of `months`.

    Returns {"dry_end_setups": [...], "wet_ends": [...], "shifts": [...],
    "stops": [...], "content_hash": sha256 hex, "manifest": {...}} where the
    lists hold the dataclasses from `records.py` in time order.
    """
    months = tuple((str(y), int(m)) for y, m in months)
    rng = random.Random(seed)
    slots = _shift_slots(production_days(months))
    outage_slots = {rng.choice([(s.day, s.number) for s in slots
                                if (str(s.day.year), s.day.month) == month]) for month in months}
    week_factors = _week_factors([s.day for s in slots], seed)
    frames = _layout(rng, slots, _Planner(rng), _machine_code(machine_id), outage_slots, week_factors)
    setups, stops = [], []
    for frame in frames:
        record, stop_records = _setup_record(rng, frame, _context(frame.slot, outage_slots, week_factors), machine_id)
        setups.append(record)
        stops.extend(stop_records)
    data = {"dry_end_setups": setups, "wet_ends": _wet_end_records(setups),
            "shifts": _shift_records(rng, slots, setups), "stops": stops}
    data["content_hash"] = content_hash(data)
    data["manifest"] = {"schema": R.SCHEMA, "seed": seed, "months": [list(m) for m in months],
                        "machine_id": machine_id, "content_hash": data["content_hash"],
                        "counts": {name: len(data[name]) for name in R.FAMILIES},
                        "files": {name: f"{name}.jsonl" for name in R.FAMILIES}}
    return data


def jsonl_bytes(records):
    lines = [json.dumps(R.to_dict(r) if not isinstance(r, dict) else r, sort_keys=True,
                        separators=(",", ":")) for r in records]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def content_hash(data):
    digest = hashlib.sha256()
    for name in R.FAMILIES:
        digest.update(name.encode())
        digest.update(jsonl_bytes(data[name]))
    return digest.hexdigest()


def write_jsonl(data, directory):
    """Write one JSONL file per family plus manifest.json into `directory`."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in R.FAMILIES:
        (directory / f"{name}.jsonl").write_bytes(jsonl_bytes(data[name]))
    (directory / "manifest.json").write_text(json.dumps(data["manifest"], indent=2, sort_keys=True) + "\n")
    return directory


def read_jsonl(directory):
    """Read the families back as dataclasses; the manifest hash must match."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    data = {}
    for name, cls in R.FAMILIES.items():
        text = (directory / f"{name}.jsonl").read_text(encoding="utf-8")
        data[name] = [R.from_dict(cls, json.loads(line)) for line in text.splitlines() if line]
    data["content_hash"] = content_hash(data)
    if data["content_hash"] != manifest["content_hash"]:
        raise ValueError("Record files do not match their manifest content hash")
    data["manifest"] = manifest
    return data
