"""Deterministic synthetic generator for the demo record families, seeded by date (record contract v2).

`generate_day(day)` lays out the three shifts of one production day for one
corrugator. It draws the day's orders (customer, sheet width x length, quantity,
quality ID) and SCHEDULES them into dry-end setups the way a corrugator scheduler
does (issue #100, the owner's worked example): it pairs orders across the two
cutoff knives, picks the outs that fill the web, runs every setup on the
narrowest paper at least as wide as its used width plus the minimum trim, lets
the lane that finishes first set the setup's length (the other lane runs as far
as its pair, within one knife rotation), and rolls the remainder into a next
setup. Trim therefore comes out of the pairing. It then emits the setups with
their order lanes and reject lines, the wet ends (paper runs), the rolls that fed
them, the shifts and the downtime log (see `records.py`, docs/RECORD_CONTRACT.md).

Every random draw of a day comes from streams keyed by the dataset seed and a
date (issue #35): the day's own `Random(f"{seed}:{day}")`, its production week's
speed factor `Random(f"{seed}:week:{monday}")` and its month's power outage
`Random(f"{seed}:outage:{yyyy-mm}")`. Nothing carries from one production day into
the next (the day closes at 07:00 the following morning; orders still open then
are left open), so a day regenerates byte-identically whatever range it is
generated in. `generate_days()`, `generate_window()` and `generate_months()`
concatenate days, add the customer dimension and the `content_hash` of the range.
Simulation settings here are demo choices, not plant standards; the seeded stories
come from `stories.py`, which no tool or analysis module reads.

Calendar: production days are Monday to Friday (weekends are plant-down and
emit nothing; there is no holiday calendar). Each production day has three
480-minute shifts starting 07:00 and is labelled by its start date. The
dataset starts on DATASET_FIRST_DAY (1 January 2025, owner 2026-11-11) and ends
on `as_of`: a fixed start and a rolling end (`window_days`).
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
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
WEEK_SPEED_FACTOR = (0.95, 1.05)          # one draw per production week: week-to-week volume moves a few percent
ORDER_CHANGE_CHOPS = (12, 20)             # chops when the knife combination changes
CONTINUATION_CHOPS = (7, 12)              # chops when one lane's order continues on its knife at the same outs
SPLICERS = tuple(f"Splicer {i}" for i in range(1, 6))
KNIFE_PLACES = ("Upper knife", "Lower knife", "Shear knife", "Slitter/scorer")
PM_WEEKDAY, PM_SHIFT = 3, 1               # Thursday, shift 1
PM_MINUTES = (120, 135)
PM_PLACES = ("Double backer", "Single facer", "Glue dams", "Steam system", "Stacker")
POWER_OUTAGE_MINUTES = (10, 15)           # one per month
MONDAY_MAINTENANCE, AUGUST_MAINTENANCE, MONDAY_OPERATOR = 1.40, 1.08, 1.15
RUN_MINUTES_PER_SHIFT = 460               # budget basis for the stop rates
CREW_EPOCH = date(2026, 8, 3)             # the Monday the crew rotation counts weeks from (the original dataset's first Monday)
DATASET_FIRST_DAY = date(2025, 1, 1)      # the data starts here (owner, 2026-11-11): a fixed start, a rolling end

# The scheduler (issue #100). Sheet widths are drawn in eighths of an inch, lengths (the cutoff) in inches.
MIN_TRIM_IN = 2.0                         # the scheduler's minimum side trim, both sides together (1 in a side)
MAX_OUTS = 8
POOL_SIZE = 6                             # open orders of the running grade the scheduler pairs from
PAPER_CHANGE_PENALTY = 0.03              # a setup leaves the running paper only when that saves this much trim (a fraction of web)
FILLER_PENALTY = 0.03                     # a pallet-sheet filler lane is added only when it saves this much trim
MIN_TAIL_FT = 1000                        # a lane that would finish within this many feet of its pair finishes on this setup
ORDER_SQFT = (12000, 105000)               # an order's area: lo + span * r^1.5 sq ft, then rounded to 50 sheets
GRADE_BLOCK_SETUPS = (4, 14)              # setups the scheduler runs on one grade before it changes grade
FANFOLD_SHARE = 0.012                     # orders delivered fanfold (single wall only), each run on its own setup
FILLER_SIZES = ((40, 48), (48, 40), (42, 48), (44, 44))   # pallet bottom and cap sheets, width x length
LANE_WASTE_BOARDS = {"cutoff": (1, 2), "stacker": (1, 4)}   # per out and per lane at an order change
CHANGEOVER_RATE = 0.0015                  # changeover rejects on an order change, a fraction of gross
ADMIN_RATE = 0.0004                       # planning / admin entries, a fraction of gross
# Rejects are episodic: the chance a lane of a setup records a category at all. When it does, the line is
# the category's rate over that chance, so the expected area is the rate (simulation choices). Warp, the seeded
# story's category, is on every lane, so its weekly August and Monday steps stand clear of the draw's noise.
REJECT_CHANCE = {"bond": 0.6, "warp": 1.0, "misalignment": 0.5, "moisture": 0.3, "cracking": 0.15, "handling": 0.15, "paper": 0.12,
                 "print": 0.08, "changeover": 0.7, "admin": 0.08}
STOP_COMMENT_EMPTY = {"Order change": 0.80, "Planned maintenance": 0.30}   # share of stops with no comment; default below
STOP_COMMENT_EMPTY_DEFAULT = 0.45
# The swing knife: most machines have none, and the demo corrugator is one of them, so every setup's
# swing_knife_sqft is null, never zero (owner, 2026-11-11).
# The paper on each roll stand, by board grade, with the medium's flute take-up factor.
CONSTRUCTION = {
    "26-C": (("Top liner", "26 lb liner", 1.0), ("Medium", "23 lb medium", 1.43), ("Bottom liner", "26 lb liner", 1.0)),
    "32-C": (("Top liner", "33 lb liner", 1.0), ("Medium", "26 lb medium", 1.43), ("Bottom liner", "33 lb liner", 1.0)),
    "55-C": (("Top liner", "56 lb liner", 1.0), ("Medium", "33 lb medium", 1.43), ("Bottom liner", "56 lb liner", 1.0)),
    "200-C": (("Top liner", "42 lb liner", 1.0), ("Medium", "26 lb medium", 1.43), ("Bottom liner", "42 lb liner", 1.0)),
    "275-BC": (("Top liner", "42 lb liner", 1.0), ("B medium", "26 lb medium", 1.32), ("SF liner", "33 lb liner", 1.0),
               ("C medium", "26 lb medium", 1.43), ("Bottom liner", "42 lb liner", 1.0)),
    "275-EB": (("Top liner", "42 lb liner", 1.0), ("E medium", "23 lb medium", 1.25), ("SF liner", "33 lb liner", 1.0),
               ("B medium", "26 lb medium", 1.32), ("Bottom liner", "42 lb liner", 1.0)),
}
DOUBLE_WALL = frozenset(("275-BC", "275-EB"))
ROLL_FT = (12000, 24000)                  # usable paper on a roll
ROLL_PEEL_FT = (15, 80)                   # outer wraps stripped at roll prep
ROLL_CORE_FT = (60, 200)                  # paper left on the core when a roll is spliced out

# The customer dimension: invented names, no measures. C000 is the plant itself.
_NAMES = ("Northfield", "Cedar Ridge", "Bluewater", "Prairie Gold", "Summit Lane", "Harbor Point", "Lakeshore", "Redwood Hollow",
          "Granite Falls", "Riverbend", "Maple Court", "Ironwood", "Silver Creek", "Westgate", "Oakmont", "Pinecrest",
          "Fairview", "Stonebridge", "Hillcrest", "Brookside")
_TRADES = ("Foods", "Produce", "Packaging", "Supply", "Brewing", "Dairy", "Hardware", "Distribution")
_ROUTES = ("Local", "North route", "South route", "East route", "West route")
CUSTOMERS = tuple(
    [R.CustomerRecord("C000", "Plant stock (pallet sheets)", "In plant", "ST-000-1")]
    + [R.CustomerRecord(f"C{i:03d}", f"{_NAMES[(i * 7) % len(_NAMES)]} {_TRADES[(i * 3) % len(_TRADES)]}",
                        _ROUTES[i % len(_ROUTES)], f"ST-{i:03d}-{1 + i % 3}") for i in range(1, 41)])
ORDER_CUSTOMERS = tuple(c.customer_id for c in CUSTOMERS[1:])
ORDER_CUSTOMER_WEIGHTS = tuple(1 / (1 + i / 6) for i in range(len(ORDER_CUSTOMERS)))   # a few big customers, a long tail

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
# What an operator might type into the stop log's comment, by reason. Quoted verbatim by the tools, never
# read as a number; a share of every reason's stops carries no comment at all (STOP_COMMENT_EMPTY).
COMMENTS = {
    "Knife / slitter jam": ("boards jammed at the {place}, cleared and restarted", "{place} jammed again",
                            "had to change a slitter blade", "blades dull, jam at the {place}", "cleared jam, scores looked off after"),
    "Missed splice": ("operator missed splice, not paying attention", "splice didn't take at {place}, tail too short",
                      "missed splice on {place}, web broke at the double backer", "bad splice tape on {place}",
                      "roll ran out before the splice"),
    "Raw material (roll)": ("roll had a bad wrap, stripped extra at {place}", "waited on a roll from the warehouse",
                            "core crushed, could not mount at {place}", "wet edge on the roll at {place}", "dropped roll, flat spot"),
    "Stacker": ("stacker boards fell over", "stack fell over at the stacker", "lift table fault at the stacker",
                "boards jammed in the stacker", "had to restack by hand"),
    "Order change": ("waited on the next order's tickets", "scores set wrong, reset", "slitter heads slow to position"),
    "Glue / starch": ("glue dam leaking", "starch viscosity off, adjusted", "reset the glue roll gap", "glue pan low"),
    "Steam / moisture": ("steam pressure dropped", "condensate trap stuck at the {place}", "preheater temperature low"),
    "Power outage": ("power bump, machine restarted", "lost power, restarted after supply returned"),
    "Other": ("maintenance fault at the {place}", "e-stop pulled at the {place}", "belt tracking off", "{place} fault, reset"),
    "Planned maintenance": ("planned maintenance on the {place}", "planned work: {place}", "{place} planned maintenance, ran long"),
}
REJECT_MIX = {   # reason weights inside the categories without a story (simulation choices)
    "moisture": {"Wet board": 0.40, "Wet edges": 0.25, "Low moisture / brittle board": 0.20, "Steam or condensate spots": 0.15},
    "cracking": {"Cracking board": 0.45, "Wrinkles": 0.40, "Brake line": 0.15},
    "misalignment": {"Misalign": 0.30, "Web walking": 0.12, "Wrong scores": 0.08, "Light scores": 0.08, "Score cutting": 0.07,
                     "Bad slit cut": 0.08, "Knife not cutting to spec": 0.06, "Frozen slitters": 0.03, "Knife overheating": 0.02,
                     "No change in slitters": 0.03, "Slitter off line": 0.03, "Moon outs": 0.02, "Wrong slat cuts": 0.02,
                     "Wrong cut outs": 0.03, "Wrong dimensions / width": 0.03},
    "handling": {"Bander crush": 0.20, "Bander damage": 0.15, "Forklift damage": 0.25, "Stacker jam": 0.20,
                 "Stacker in discharge": 0.08, "Fanfold jam": 0.02, "Manual discharge": 0.10},
    "paper": {"Wrinkles in paper from mill": 0.30, "Marks in paper / dropped roll": 0.25, "Wrong paper": 0.08, "Wrong medium": 0.06,
              "Wrong DB/SF liner": 0.05, "Wrong flute size": 0.04, "Liner inside out": 0.06, "No coating": 0.08, "Wrong color": 0.08},
    "print": {"Print wrinkle": 0.40, "Print / no print": 0.30, "Streaks in print": 0.30},
    "changeover": {"Order change": 0.50, "Automatic order change": 0.20, "Cut off": 0.18, "Small butt": 0.12},
    "changeover_paper": {"Paper change": 0.45, "Early paper change": 0.15, "Waste from a paper or flute change": 0.40},
    "admin": {"Order fill": 0.18, "Over scheduled": 0.22, "Used as dunnage": 0.08, "Re-arrange outs": 0.08, "Cap sheets": 0.06,
              "Not enough trim": 0.08, "Out of trim": 0.06, "Operator": 0.08, "Equipment entered as a reason": 0.04,
              "NULL (no reason entered)": 0.06, "Blank (no reason entered)": 0.06},
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


def weekdays_between(first, last):
    """Monday-Friday dates from `first` to `last` inclusive, in order."""
    return [first + timedelta(days=n) for n in range((last - first).days + 1) if (first + timedelta(days=n)).weekday() < 5]


def window_bounds(as_of, first_day=None):
    """(first, last) calendar dates of the dataset that ends on `as_of` (inclusive): from DATASET_FIRST_DAY,
    or `first_day` when given (tests use short windows)."""
    first = first_day or DATASET_FIRST_DAY
    if as_of < first:
        raise ValueError(f"The dataset starts on {first.isoformat()}: as_of {as_of.isoformat()} is before it")
    return first, as_of


def window_days(as_of, first_day=None):
    """Every production day of the dataset ending on `as_of`: Mon-Fri from DATASET_FIRST_DAY (or `first_day`) to as_of."""
    return weekdays_between(*window_bounds(as_of, first_day))


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


def _day_slots(day):
    """The three shifts of one production day. Crews rotate weekly, counted from CREW_EPOCH (floor
    division continues the rotation before it). Shifts 1 to 2 and 2 to 3 are contiguous; shift 3 is
    not continued into the next production day, so no record or paper run crosses a day boundary."""
    week = (day - CREW_EPOCH).days // 7
    slots = []
    for number in (1, 2, 3):
        start, end = R.shift_window(day.strftime("%Y%m%d"), number)
        slots.append(_Shift(day, number, start, end, R.CREWS[(number - 1 + week) % 3]))
    for current, following in zip(slots, slots[1:]):
        current.contiguous_next = following.start == current.end
    slots[-1].contiguous_next = False
    return slots


@dataclass
class _Context:
    """Everything the scheduler, the stop and the quality draws need to know about a shift."""
    slot: _Shift
    monday: bool
    month: int
    post_rebuild: bool          # the knife / slitter jam story is on (after the rebuild, through its end)
    splice_story: bool
    min_trim_in: float
    pm: tuple | None            # (minutes, place, note) for this shift's planned window
    outage: bool
    week_factor: float = 1.0    # the production week's speed draw (WEEK_SPEED_FACTOR)


@lru_cache(maxsize=4096)
def week_factor(seed, monday):
    """The production week's speed factor, from the week's own stream keyed by its Monday, so the
    week-to-week movement depends on neither the previous week nor the range being generated."""
    return random.Random(f"{seed}:week:{monday.isoformat()}").uniform(*WEEK_SPEED_FACTOR)


@lru_cache(maxsize=1024)
def outage_slot(seed, year, month):
    """The month's one power outage: a (day, shift) drawn from the month's own stream over every
    production shift of the whole month, whichever days of it a range includes."""
    slots = [(d, n) for d in production_days(((year, month),)) for n in (1, 2, 3)]
    return random.Random(f"{seed}:outage:{year:04d}-{month:02d}").choice(slots)


def min_trim_in(day):
    """The scheduler's minimum side trim on `day`, both sides together: the baseline, plus the slitter
    rebuild's creep of a tenth of an inch a week from its PM date through `trim_creep_end` (31 October
    2026); the baseline again after that, so a window past October does not keep growing (owner, PR #91)."""
    rebuild = stories.SLITTER_REBUILD
    baseline = rebuild["baseline_min_trim_in"]
    if day > rebuild["trim_creep_end"]:
        return baseline
    after = max(0, (day - rebuild["pm_date"]).days)
    return baseline + rebuild["min_trim_creep_in_per_week"] * after / 7


def knife_slitter_story(day):
    """True on the days the rebuild's knife / slitter jam effect runs: after the PM date through its end."""
    rebuild = stories.SLITTER_REBUILD
    return rebuild["pm_date"] < day <= rebuild["knife_slitter_end"]


def _context(slot, outage_slots, week_factors=None):
    day = slot.day
    rebuild = stories.SLITTER_REBUILD
    pm = None
    if day.weekday() == PM_WEEKDAY and slot.number == PM_SHIFT:
        if day == rebuild["pm_date"] and slot.number == rebuild["pm_shift"]:
            pm = (rebuild["pm_minutes"], rebuild["place"], "Planned window: slitter/scorer rebuild")
        else:
            index = (day - date(2026, 1, 1)).days // 7
            pm = (None, PM_PLACES[index % len(PM_PLACES)], None)
    splice = stories.SPLICER3_MISSED_SPLICE
    return _Context(slot, day.weekday() == 0, day.month, knife_slitter_story(day),
                    splice["start"] <= day <= splice["end"], min_trim_in(day), pm,
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


# ------------------------------------------------------------------ the scheduler's arithmetic

def narrowest_web(used_in, min_trim=MIN_TRIM_IN, widths=R.WEB_WIDTHS):
    """The narrowest paper at least as wide as the used width plus the minimum trim, or None."""
    for width in sorted(widths):
        if width >= used_in + min_trim - 1e-9:
            return width
    return None


def trim_pct(web_in, used_in):
    """Side trim as a percent of the web: (web - used) / web x 100 (identity 1)."""
    return (web_in - used_in) / web_in * 100


def fill_outs(sheet_width_in, room_in, max_outs=MAX_OUTS):
    """The most outs of one sheet width that fit in `room_in` inches (0 when none fits)."""
    return max(0, min(max_outs, int(math.floor(room_in / sheet_width_in + 1e-9))))


def lane_run(lanes, min_tail_ft=MIN_TAIL_FT):
    """The scheduled cuts of each lane of one setup (identities 2-4).

    `lanes` is [(remaining sheets or None for a filler that runs as far as its pair, outs, sheet length in)].
    The lane that finishes first sets the length: its cuts are ceil(remaining / outs). A lane that would
    finish within `min_tail_ft` of it finishes on this setup too, and the longer of those sets the
    length. Every other lane runs as far as its pair: floor(lineal x 12 / its length) cuts, so the lanes
    end within one knife rotation. Returns (cuts per lane, index of the lane that set the length)."""
    finish = [math.ceil(rem / outs) * length / 12 if rem is not None else math.inf for rem, outs, length in lanes]
    first = min(finish)
    if first == math.inf:
        raise ValueError("a setup needs one lane with a quantity to finish")
    definer = max((i for i, ft in enumerate(finish) if ft - first < min_tail_ft), key=lambda i: (finish[i], -i))
    rem, outs, length = lanes[definer]
    travel_in = math.ceil(rem / outs) * length
    cuts = [travel_in // lane_length if i != definer else math.ceil(rem / outs) for i, (_, _, lane_length) in enumerate(lanes)]
    return cuts, definer


class _Planner:
    """Schedules the day's orders into setups and paper runs, ahead of any clock."""

    def __init__(self, rng, order_prefix):
        self.rng = rng
        self.order_prefix = order_prefix   # the production day (YYMMDD): order numbers stay unique across days
        self.order_seq = 0
        self.pools = {grade: [] for grade in GRADES}
        self.grade = None
        self.block_left = 0
        self.carry = None                  # (order, lane index, outs) of the order the next setup continues
        self.wet = None                    # {"id", "quality", "width"}
        self.wet_seq = 0
        self.grade_queue = []

    def abandon(self):
        """Drop the open paper run and continuation (at the end of the production day)."""
        self.wet, self.carry, self.grade, self.block_left = None, None, None, 0

    def reopen(self, plan, lead_cuts):
        """A setup closed at a shift boundary before its last cuts ran (the rest was under a minute of running):
        give each order back the sheets it was scheduled but did not run, so its remainder rolls into a later
        setup like any other (identity 5). An order that is already the continuation keeps its place; one that
        had finished goes back to the front of its grade's open orders."""
        d = plan["definer"]
        length, total, nominal = plan["lanes"][d][0]["L"], plan["actual"][d], plan["nominal"][d]
        ran = _nominal_at(nominal, lead_cuts, total)
        for i, (order, outs) in enumerate(plan["lanes"]):
            if order["filler"]:
                continue
            unrun = plan["nominal"][i] - (ran if i == d else ran * length // order["L"])
            if unrun <= 0:
                continue
            was_open = order["remaining"] > 0
            order["remaining"] += unrun * outs
            if not was_open and order["remaining"] > 0 and not (self.carry and self.carry[0] is order):
                self.pools[plan["quality"]].insert(0, order)

    def _order(self, quality):
        rng = self.rng
        self.order_seq += 1
        if self.order_seq > 9999:
            raise ValueError("more than 9,999 orders in one production day")
        number = f"SO-{self.order_prefix}{self.order_seq:04d}"
        thick = quality in DOUBLE_WALL
        customer = rng.choices(ORDER_CUSTOMERS, weights=ORDER_CUSTOMER_WEIGHTS)[0]
        if not thick and rng.random() < FANFOLD_SHARE:
            width, length = 30 + rng.randint(0, 25 * 8) / 8, rng.randint(20, 36)
            quantity = rng.randint(30, 120) * 100
            return {"order_number": number, "part_number": f"P-{rng.randint(10000, 99999)}", "customer_id": customer,
                    "w": width, "L": length, "quantity": quantity, "remaining": quantity, "fanfold": True,
                    "sheets_per_pallet": rng.choice((500, 600, 750, 1000)), "stack": 0, "type": "product", "filler": False}
        width, length = 12 + rng.randint(0, 48 * 8) / 8, rng.randint(20, 92)
        target = ORDER_SQFT[0] + ORDER_SQFT[1] * rng.random() ** 1.5
        quantity = max(250, int(round(target / (width * length / 144) / 50)) * 50)
        return {"order_number": number, "part_number": f"P-{rng.randint(10000, 99999)}", "customer_id": customer,
                "w": width, "L": length, "quantity": quantity, "remaining": quantity, "fanfold": False,
                "stack": rng.randint(80, 160) if thick else rng.randint(150, 300), "type": "product", "filler": False}

    @staticmethod
    def _filler(size):
        """A pallet-sheet lane candidate (bottom or cap sheets for the plant's own use) that fills a tough
        trim; it draws its number, type and stack only when the scheduler picks it (`_take_filler`)."""
        width, length = size
        return {"order_number": None, "part_number": f"PLT-{width}{length}", "customer_id": "C000", "w": float(width),
                "L": length, "quantity": None, "remaining": None, "fanfold": False, "stack": 0, "type": None, "filler": True}

    def _take_filler(self, filler):
        self.order_seq += 1
        if self.order_seq > 9999:
            raise ValueError("more than 9,999 orders in one production day")
        filler.update(order_number=f"SO-{self.order_prefix}{self.order_seq:04d}",
                      type="bottom_sheet" if self.rng.random() < 0.6 else "cap_sheet", stack=self.rng.randint(150, 300))

    def _next_grade(self):
        if not self.grade_queue:
            self.grade_queue = list(GRADES)
            self.rng.shuffle(self.grade_queue)
        return self.grade_queue.pop()

    def _choose(self, lead, partners, min_trim, current_web):
        """The lanes and web for a setup led by `lead`: every outs of the lead alone, and beside each
        partner (each filler size too) at the outs that fill each paper width, scored by trim as a fraction
        of the narrowest web the lanes fit on (`narrowest_web`), plus a penalty for leaving the running
        paper and for a filler lane. Ties go to the wider used width, then to the first found."""
        webs = R.WEB_WIDTHS
        low, step, top = min(webs), webs[1] - webs[0], max(webs)
        w1 = lead["w"]
        best, best_score, best_used = None, None, None
        for o1 in range(1, fill_outs(w1, top - min_trim) + 1):
            u1 = w1 * o1
            options = [(u1, None, 0)]
            if not lead["fanfold"]:
                for partner in partners:
                    w2, last = partner["w"], 0
                    for web in webs:
                        o2 = int((web - min_trim - u1) / w2 + 1e-9)
                        if o2 > MAX_OUTS:
                            o2 = MAX_OUTS
                        if o2 > last:
                            last = o2
                            options.append((u1 + w2 * o2, partner, o2))
            for used, partner, o2 in options:
                need = used + min_trim
                if need > top + 1e-9:
                    continue
                web = low if need <= low else low + step * math.ceil((need - low) / step - 1e-9)
                score = (web - used) / web
                if current_web is not None and web != current_web:
                    score += PAPER_CHANGE_PENALTY
                if partner is not None and partner["filler"]:
                    score += FILLER_PENALTY
                score = round(score, 9)
                if best is None or score < best_score or (score == best_score and used > best_used):
                    best, best_score, best_used = (web, ((lead, o1),) if partner is None else ((lead, o1), (partner, o2))), score, used
        return best

    def next_setup(self, ctx):
        rng = self.rng
        if self.grade is None or self.block_left <= 0:
            if self.carry is not None:     # the open order waits in its grade's pool for that grade's next block
                self.pools[self.grade].insert(0, self.carry[0])
                self.carry = None
            self.grade, self.block_left = self._next_grade(), rng.randint(*GRADE_BLOCK_SETUPS)
            self.wet = None
        self.block_left -= 1
        quality, pool = self.grade, self.pools[self.grade]
        if self.carry is not None:
            lead, carry_lane, carry_outs = self.carry
        else:
            if not pool:
                pool.append(self._order(quality))
            lead, carry_lane, carry_outs = pool.pop(0), None, None
        while len(pool) < POOL_SIZE:
            pool.append(self._order(quality))
        partners = [o for o in pool if not o["fanfold"]]
        fillers = [self._filler(size) for size in FILLER_SIZES] if not lead["fanfold"] else []
        current_web = self.wet["width"] if self.wet else None
        web, chosen = self._choose(lead, partners + fillers, ctx.min_trim_in, current_web)
        for order, _ in chosen:
            if order["filler"]:
                self._take_filler(order)
        lanes = list(chosen)
        if carry_lane == 1 and len(lanes) == 2:      # the continuing order keeps its knife
            lanes = [lanes[1], lanes[0]]
        for order, _ in lanes:
            if not order["filler"] and order is not lead:
                pool.remove(order)
        cuts, definer = lane_run([(o["remaining"], outs, o["L"]) for o, outs in lanes])
        new_wet = self.wet is None or self.wet["width"] != web
        if new_wet:
            self.wet_seq += 1
            self.wet = {"id": f"WE{ctx.slot.date_text}{self.wet_seq:03d}", "quality": quality, "width": web}
        continued = (carry_lane is not None and not new_wet
                     and any(o is lead and i == carry_lane and outs == carry_outs for i, (o, outs) in enumerate(lanes)))
        self.carry = None
        for index, ((order, outs), lane_cuts) in enumerate(zip(lanes, cuts)):
            if order["filler"]:
                continue
            order["remaining"] -= lane_cuts * outs
            if order["remaining"] > 0:
                self.carry = (order, index, outs)
        chops = rng.randint(*(CONTINUATION_CHOPS if continued else ORDER_CHANGE_CHOPS))
        fpm = (GRADES[quality] * CREW_SPEED_FACTOR[ctx.slot.crew] * ctx.week_factor
               * SHIFT_SPEED_FACTOR[ctx.slot.number] * rng.uniform(*SPEED_NOISE))
        lead_length = lanes[definer][0]["L"]
        actual = _actual_cuts(rng, cuts[definer])
        actual_cuts = [actual if i == definer else actual * lead_length // o["L"] for i, (o, _) in enumerate(lanes)]
        lineal = actual * lead_length / 12 + chops * CHOP_LENGTH_IN / 12
        run = max(MIN_RUN_SECONDS, round(lineal / fpm * 60))
        return {"quality": quality, "width": web, "lanes": lanes, "nominal": cuts, "actual": actual_cuts, "definer": definer,
                "code": "O", "chops": chops, "fpm": fpm, "run": run, "wet_end_id": self.wet["id"], "new_wet": new_wet,
                "fanfold": bool(lanes[0][0]["fanfold"]), "cursor": 0}


def _stop(rng, kind, reason, place, seconds, chops=0, note=None):
    empty = STOP_COMMENT_EMPTY.get(reason, STOP_COMMENT_EMPTY_DEFAULT)
    if note is not None:
        comment = note
    elif rng.random() < empty:
        comment = ""
    else:
        comment = rng.choice(COMMENTS[reason]).format(place=place.lower() if place != "Power" else "supply")
    return {"kind": kind, "reason": reason, "place": place, "seconds": int(seconds), "chops": chops, "comment": comment}


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
                          _stop(rng, kind, reason, place, seconds, chops)))
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
    final: bool = False        # the plan's last record: it takes every remaining cut
    cuts: tuple = (0, 0)       # (from, to): the plan's lead-lane actual cuts this record runs (`_lead_cuts`)

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
                    _insert_stop(segments, _stop(rng, "Scheduled", "Planned maintenance", place, minutes * 60, 0, note),
                                 min(run, rng.randint(300, 900)))
                if ctx.outage:
                    _insert_stop(segments, _stop(rng, "Maintenance", "Power outage", "Power",
                                                 rng.randint(*POWER_OUTAGE_MINUTES) * 60),
                                 min(run, rng.randint(600, 20000)))
            boundary = int((slot.end - cursor).total_seconds())
            total = sum(s[1] for s in segments)

            def emit(parts, split=split_from, chops=base_chops, final=False):
                seq = per_slot.get(id(slot), 0) + 1
                per_slot[id(slot)] = seq
                day_text, time_text = R.stamp(cursor)
                frame = _Frame(plan, parts, cursor, slot,
                               f"{machine_code}{day_text}{time_text}{seq % 100:02d}", chops, split, final,
                               _lead_cuts(plan, parts, chops, final))
                frames.append(frame)
                return frame

            if total <= boundary:
                emit(segments, final=True)
                cursor += timedelta(seconds=total)
                continue
            first, second = _split(segments, boundary)
            if _run_seconds(second) < MIN_RUN_SECONDS:
                frame = emit(first)               # remainder too small: close here; the cuts not run go back to their orders
                planner.reopen(plan, frame.cuts[1])
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


def _actual_cuts(rng, nominal):
    """Cuts as run against the scheduled cuts: the over/under-run window, most within +-2%, all within +-10%."""
    draw = rng.random()
    if draw < 0.85:
        delta = rng.uniform(-0.02, 0.02)
    elif draw < 0.97:
        delta = rng.choice((-1, 1)) * rng.uniform(0.02, 0.08)
    else:
        delta = rng.choice((-1, 1)) * rng.uniform(0.08, 0.099)
    actual = round(nominal * (1 + delta))
    return max(1, min(max(actual, math.ceil(nominal * 0.9)), math.floor(nominal * 1.1)))


@lru_cache(maxsize=None)
def _cumulative(items):
    """(names, cumulative weights) of a tuple of (name, weight) pairs, for rng.choices."""
    names, cum, total = [], [], 0.0
    for name, weight in items:
        total += weight
        names.append(name)
        cum.append(total)
    return names, cum


def _weighted(rng, weights):
    names, cum = _cumulative(tuple(weights.items()))
    return rng.choices(names, cum_weights=cum)[0]


@lru_cache(maxsize=None)
def _positions(category, reason, double_wall):
    """The liner and side options the taxonomy allows a reason (DB and SF liners on double wall only)."""
    entry = next(r for r in R.categories()[category]["reasons"] if r["reason"] == reason)
    if entry.get("double_wall_only") and not double_wall:
        raise ValueError(f"{reason!r} is a double-wall reason")
    liners = entry.get("liner") or ()
    if not double_wall:   # a single-wall board has no DB or SF liner, so it never records one
        liners = tuple(x for x in liners if x not in ("DB", "SF"))
    return tuple(liners), tuple(entry.get("side") or ())


@lru_cache(maxsize=None)
def _single_wall_mix(category, mix):
    """A category's reason mix on a single-wall board: without the reasons only a double-wall board can have
    (`double_wall_only` in the taxonomy: between the flutes, a wrong DB / SF liner)."""
    double = {r["reason"] for r in R.categories()[category]["reasons"] if r.get("double_wall_only")}
    return {k: v for k, v in mix if k not in double}


def _position(rng, category, reason, quality):
    """The liner and side a reject line records, as the taxonomy allows for its reason."""
    liners, sides = _positions(category, reason, quality in DOUBLE_WALL)
    return (rng.choice(liners) if liners else None), (rng.choice(sides) if sides else None)


def _nominal_at(nominal, lead_cuts, total):
    """The scheduled lead-lane cuts that go with `lead_cuts` of `total` actual ones (all of them at the end)."""
    return nominal if lead_cuts == total else int(round(nominal * lead_cuts / total))


def _lead_cuts(plan, segments, base_chops, final):
    """The plan's lead-lane actual cuts a record runs, as (from, to), allocated when the record is laid out: by
    its running time less its shear chops (the plan's last record takes what is left), so a setup closed at a
    shift boundary knows exactly which cuts did not run."""
    d = plan["definer"]
    length, total = plan["lanes"][d][0]["L"], plan["actual"][d]
    start = plan["cursor"]
    if final:
        end = total
    else:
        chops = base_chops + sum(s[2]["chops"] for s in segments if s[0] == "stop")
        travel = max(0.0, plan["fpm"] * _run_seconds(segments) / 60 - chops * CHOP_LENGTH_IN / 12)
        end = min(total, start + int(round(travel * 12 / length)))
    plan["cursor"] = end
    return start, end


def _frame_cuts(plan, frame):
    """The cuts of one record: the lane that set the length runs the cuts its layout allocated (`_lead_cuts`);
    the other lanes follow its travel by floor, so every record's lanes end within one knife rotation and the
    records of a plan add up to it. Returns (nominal cuts, actual cuts, scheduled travel ft, actual travel ft),
    the travels being the record's longest lane."""
    d = plan["definer"]
    lanes = plan["lanes"]
    lead_length = lanes[d][0]["L"]
    total_actual, total_nominal = plan["actual"][d], plan["nominal"][d]
    a0, a1 = frame.cuts
    n0, n1 = _nominal_at(total_nominal, a0, total_actual), _nominal_at(total_nominal, a1, total_actual)
    actual, nominal = [], []
    for i, (order, _) in enumerate(lanes):
        if i == d:
            actual.append(a1 - a0)
            nominal.append(n1 - n0)
        else:
            actual.append(a1 * lead_length // order["L"] - a0 * lead_length // order["L"])
            nominal.append(n1 * lead_length // order["L"] - n0 * lead_length // order["L"])
    # The record's travel is its longest lane (identity 4): in a split record a lane that follows by floor can
    # run up to one of its own cuts past the lane that set the length.
    lengths = [order["L"] for order, _ in lanes]
    return (nominal, actual, max(c * L for c, L in zip(nominal, lengths)) / 12,
            max(c * L for c, L in zip(actual, lengths)) / 12)


def _setup_record(rng, frame, ctx, machine_id):
    plan, width = frame.plan, frame.plan["width"]
    elapsed = sum(s[1] for s in frame.segments)
    run = _run_seconds(frame.segments)
    stop_segments = [s for s in frame.segments if s[0] == "stop"]
    end = frame.start + timedelta(seconds=elapsed)
    chops = frame.base_chops + sum(s[2]["chops"] for s in stop_segments)
    chop_ft = chops * CHOP_LENGTH_IN / 12
    nominal, actual, scheduled_ft, order_travel = _frame_cuts(plan, frame)
    lineal = round(order_travel + chop_ft, 1)
    chop_sqft = round(chop_ft * width / 12, 1)
    gross = round(lineal * width / 12, 1)
    used = sum(order["w"] * outs for order, outs in plan["lanes"])
    trim_in = width - used
    trim = round(order_travel * trim_in / 12, 1)
    warp = stories.WEEKEND_WARP
    quality = plan["quality"]
    rates = {"bond": warp["bond_rate"],
             "warp": warp["warp_rate_by_month"].get(ctx.month, warp["warp_rate_by_month"][9])
             * (warp["monday_multiplier"] if ctx.monday else 1),
             "misalignment": warp["misalignment_rate"], "moisture": warp["moisture_rate"], **warp["other_rates"]}
    mixes = {"bond": warp["bond_reason_weights"], "warp": warp["warp_reason_weights"],
             **{k: REJECT_MIX[k] for k in ("moisture", "cracking", "misalignment", "handling", "paper", "print")}}
    if quality not in DOUBLE_WALL:
        mixes = {k: _single_wall_mix(k, tuple(mix.items())) for k, mix in mixes.items()}
    order_change = frame.code == "O"
    lanes, rejects = [], []
    for index, ((order, outs), lane_nominal, lane_actual) in enumerate(zip(plan["lanes"], nominal, actual), 1):
        area = order["w"] * order["L"] / 144
        share = order["w"] * outs / used
        sheets_run = lane_actual * outs
        taken = 0

        def reject(category, mix, target):
            nonlocal taken
            chance = REJECT_CHANCE[category]
            if rng.random() >= chance:
                return
            sheets = min(_stochastic_round(rng, target / chance / area), sheets_run - taken)
            if sheets > 0:
                taken += sheets
                reason = _weighted(rng, mix)
                liner, side = _position(rng, category, reason, quality)
                rejects.append(R.RejectEntry(category, reason, index, sheets, round(sheets * area, 1), liner, side))

        for category, rate in rates.items():
            reject(category, mixes[category], gross * rate * rng.uniform(0.5, 1.5) * share)
        if order_change:
            paper_change = plan["new_wet"] and frame.split_from is None and rng.random() < 0.5
            reject("changeover", REJECT_MIX["changeover_paper" if paper_change else "changeover"],
                   gross * CHANGEOVER_RATE * rng.uniform(0.5, 1.5) * share)
        reject("admin", REJECT_MIX["admin"], gross * ADMIN_RATE * rng.uniform(0.0, 2.0) * share)
        if order_change:
            cutoff_boards = rng.randint(*LANE_WASTE_BOARDS["cutoff"]) * outs
            stacker_boards = rng.randint(*LANE_WASTE_BOARDS["stacker"])
        else:
            cutoff_boards = stacker_boards = 0
        fanfold = order["fanfold"]
        lanes.append(R.OrderLane(
            lane=index, order_number=order["order_number"], part_number=order["part_number"], customer_id=order["customer_id"],
            order_quantity=order["quantity"] if order["quantity"] is not None else 0, sheet_width_in=order["w"],
            sheet_length_in=order["L"], number_of_outs=outs, cutoff_allocation=index, nominal_cuts=lane_nominal,
            actual_cuts=lane_actual, sheet_type=order["type"], filler=order["filler"],
            cutoff_waste_boards=cutoff_boards, stacker_waste_boards=stacker_boards,
            cutoff_waste_sqft=round(cutoff_boards * area, 1), stacker_waste_sqft=round(stacker_boards * area, 1),
            stack_height_sheets=order["stack"],
            folds=lane_actual if fanfold else None,
            sheets_per_pallet=order["sheets_per_pallet"] if fanfold else None,
            pallets=math.ceil(lane_actual * outs / order["sheets_per_pallet"]) if fanfold else None,
            perforation_length_in=order["L"] if fanfold else None))
    types = {lane.sheet_type for lane in lanes}
    start_date, start_time = R.stamp(frame.start)
    end_date, end_time = R.stamp(end)
    record = R.DryEndSetupRecord(
        setup_id=frame.setup_id, machine_id=machine_id, start_date=start_date, start_time=start_time,
        end_date=end_date, end_time=end_time, actual_run_sec=run, lineal_ft=lineal,
        total_nominal_ft=round(scheduled_ft, 1),
        avg_speed_fpm=round(lineal / (elapsed / 60), 1), web_width_in=width, quality_id=quality,
        total_produced_sqft=gross, trim_sqft=trim, trim_pct=round(trim_in / width * 100, 2),
        change_code=frame.code, stop_time_sec=sum(s[1] for s in stop_segments),
        number_of_stops=len(stop_segments), shift_number=frame.slot.number, chop_sqft=chop_sqft,
        swing_knife_sqft=None, is_fanfold=plan["fanfold"],
        sheet_type="product" if "product" in types else sorted(types)[0], filler=any(lane.filler for lane in lanes),
        lanes=lanes, production_date=frame.slot.date_text, rejects=rejects, wet_end_id=plan["wet_end_id"],
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
                production_date=frame.slot.date_text, setup_id=frame.setup_id, comment=payload["comment"]))
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


def _roll_records(rng, wet_ends):
    """The rolls each paper run consumed, stand by stand: a roll is mounted after its outer wraps are
    peeled, spliced out when it runs down (leaving its core paper), and the last roll on a stand is taken
    down at the paper change and returned to stock (no core waste)."""
    rolls, seq = [], 0
    for wet in wet_ends:
        for stand, paper, take_up in CONSTRUCTION[wet.quality_id]:
            feed = wet.lineal_ft * take_up
            while feed > 0:
                seq += 1
                usable = rng.uniform(*ROLL_FT)
                peel = round(rng.uniform(*ROLL_PEEL_FT) * wet.web_width_in / 12, 1)
                spliced = feed >= usable
                fed = usable if spliced else feed
                core = round(rng.uniform(*ROLL_CORE_FT) * wet.web_width_in / 12, 1) if spliced else 0.0
                rolls.append(R.RollRecord(roll_id=f"R{wet.production_date}{seq:04d}", wet_end_id=wet.wet_end_id, stand=stand,
                                          paper_grade=paper, width_in=wet.web_width_in, lineal_ft=round(fed, 1),
                                          peel_sqft=peel, core_sqft=core, spliced_out=spliced, production_date=wet.production_date))
                feed -= fed
    return rolls


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
        changeover = round(sum(s.changeover_sqft for s in members), 1)
        admin = round(sum(s.admin_sqft for s in members), 1)
        cutoff = round(sum(s.cutoff_waste_sqft for s in members), 1)
        stacker = round(sum(s.stacker_waste_sqft for s in members), 1)
        wet_end_ids = []
        for s in members:
            if s.wet_end_id not in wet_end_ids:
                wet_end_ids.append(s.wet_end_id)
        accounted = trim + chop + reject + changeover + admin + cutoff + stacker
        records.append(R.ShiftRecord(
            shift_number=slot.number, production_date=slot.date_text, group_name=slot.crew,
            start_date=R.stamp(slot.start)[0], start_time=R.stamp(slot.start)[1],
            end_date=R.stamp(slot.end)[0], end_time=R.stamp(slot.end)[1],
            duration=R.hhmmss(R.SCHEDULED_SHIFT_MINUTES * 60), total_lineal_ft=lineal,
            total_produced_sqft=gross, side_trim_sqft=trim, chop_sqft=chop, reject_sqft=reject,
            changeover_sqft=changeover, admin_sqft=admin, cutoff_waste_sqft=cutoff, stacker_waste_sqft=stacker,
            max_speed_fpm=round(max(s.running_speed_fpm for s in members), 1),
            avg_speed_fpm=round(lineal / R.SCHEDULED_SHIFT_MINUTES, 1), wet_end_ids=wet_end_ids,
            scheduled_minutes=R.SCHEDULED_SHIFT_MINUTES,
            bale_sqft=round(accounted + gross * rng.uniform(0.001, 0.004), 1)))
    return records


def generate_day(day, seed=2026, machine_id="CORR-1"):
    """Every record family of one production day (a Monday-Friday `date`), from streams keyed by the
    seed and dates only: the same day is byte-identical in any range that contains it (issue #35).

    Returns {"dry_end_setups": [...], "wet_ends": [...], "shifts": [...], "stops": [...], "rolls": [...]}
    with the dataclasses from `records.py` in time order.
    """
    if day.weekday() >= 5:
        raise ValueError(f"{day} is a weekend day: production days are Monday to Friday")
    rng = random.Random(f"{seed}:{day.isoformat()}")
    slots = _day_slots(day)
    outage_slots = {outage_slot(seed, day.year, day.month)}
    week_factors = {day.isocalendar()[:2]: week_factor(seed, day - timedelta(days=day.weekday()))}
    frames = _layout(rng, slots, _Planner(rng, day.strftime("%y%m%d")), _machine_code(machine_id), outage_slots, week_factors)
    setups, stops = [], []
    for frame in frames:
        record, stop_records = _setup_record(rng, frame, _context(frame.slot, outage_slots, week_factors), machine_id)
        setups.append(record)
        stops.extend(stop_records)
    wet_ends = _wet_end_records(setups)
    return {"dry_end_setups": setups, "wet_ends": wet_ends, "shifts": _shift_records(rng, slots, setups), "stops": stops,
            "rolls": _roll_records(rng, wet_ends)}


def generate_days(days, seed=2026, machine_id="CORR-1"):
    """Every record family of the given production days (ascending dates), one day at a time, plus the
    customer dimension.

    Returns {"dry_end_setups": [...], "wet_ends": [...], "shifts": [...], "stops": [...], "rolls": [...],
    "customers": [...], "content_hash": sha256 hex of the range, "manifest": {...}}.
    """
    days = list(days)
    if not days or any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError("generate_days needs at least one production day, in ascending order")
    data = {name: [] for name in R.FAMILIES}
    for day in days:
        part = generate_day(day, seed=seed, machine_id=machine_id)
        for name in R.FAMILIES:
            data[name].extend(part[name])
    data["customers"] = list(CUSTOMERS)
    data["content_hash"] = content_hash(data)
    names = (*R.FAMILIES, *R.DIMENSIONS)
    data["manifest"] = {"schema": R.SCHEMA, "seed": seed, "first_day": days[0].isoformat(), "last_day": days[-1].isoformat(),
                        "production_days": len(days), "machine_id": machine_id, "content_hash": data["content_hash"],
                        "counts": {name: len(data[name]) for name in names},
                        "files": {name: f"{name}.jsonl" for name in names}}
    return data


def generate_window(as_of, seed=2026, machine_id="CORR-1", first_day=None):
    """The dataset ending on `as_of`: every production day from DATASET_FIRST_DAY (or `first_day`) to as_of."""
    return generate_days(window_days(as_of, first_day), seed=seed, machine_id=machine_id)


def generate_months(seed=2026, months=(("2026", 8), ("2026", 9)), machine_id="CORR-1"):
    """Every record family of the production days of `months` (the days are the same as in any
    other range that contains them). Kept for tests and the record docs; the app reads a window."""
    data = generate_days(production_days(tuple((str(y), int(m)) for y, m in months)), seed=seed, machine_id=machine_id)
    data["manifest"]["months"] = [[str(y), int(m)] for y, m in months]
    return data


def jsonl_bytes(records):
    lines = [json.dumps(R.to_dict(r) if not isinstance(r, dict) else r, sort_keys=True,
                        separators=(",", ":")) for r in records]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def content_hash(data):
    digest = hashlib.sha256()
    for name in (*R.FAMILIES, *R.DIMENSIONS):
        digest.update(name.encode())
        digest.update(jsonl_bytes(data[name]))
    return digest.hexdigest()


def write_jsonl(data, directory):
    """Write one JSONL file per family and dimension plus manifest.json into `directory`."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*R.FAMILIES, *R.DIMENSIONS):
        (directory / f"{name}.jsonl").write_bytes(jsonl_bytes(data[name]))
    (directory / "manifest.json").write_text(json.dumps(data["manifest"], indent=2, sort_keys=True) + "\n")
    return directory


def read_jsonl(directory):
    """Read the families and dimensions back as dataclasses; the manifest hash must match."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    data = {}
    for name, cls in {**R.FAMILIES, **R.DIMENSIONS}.items():
        text = (directory / f"{name}.jsonl").read_text(encoding="utf-8")
        data[name] = [R.from_dict(cls, json.loads(line)) for line in text.splitlines() if line]
    data["content_hash"] = content_hash(data)
    if data["content_hash"] != manifest["content_hash"]:
        raise ValueError("Record files do not match their manifest content hash")
    data["manifest"] = manifest
    return data
