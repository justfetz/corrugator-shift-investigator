"""Deterministic synthetic generator for the record families, seeded by date (record contract v2).

`generate_day(day)` draws one production day's orders (customer, sheet width x
length, quantity, quality ID), schedules them into dry-end setups the way a
corrugator scheduler does (issue #100: pair orders across the two cutoff knives
to fill the web, the shortest lane sets the setup's length, the other lanes run
as far as their pair and their remainder rolls into the next setup), lays the
setups out over the day's three shifts with their stops, and emits the setups,
wet ends, shifts, the downtime log and the roll stock each paper run consumed
(see `records.py`, docs/RECORD_CONTRACT.md, docs/RECORDS.md). The customer
dimension comes from its own seeded stream (`customers()`).

Every random draw of a day comes from streams keyed by the dataset seed and a
date (issue #35): the day's own `Random(f"{seed}:{day}")`, its production week's
speed factor `Random(f"{seed}:week:{monday}")` and its month's power outage
`Random(f"{seed}:outage:{yyyy-mm}")`. Nothing carries from one production day
into the next (the day closes at 07:00 the following morning, and an order still
open then is not continued), so a day regenerates byte-identically whatever
range it is generated in. `generate_days()`, `generate_window()` and
`generate_months()` concatenate days, add the customer dimension and the
`content_hash` of the range. Simulation settings here are demo choices, not
plant standards; the seeded stories come from `stories.py`, which no tool or
analysis module reads.

Calendar: production days are Monday to Friday (weekends are plant-down and
emit nothing; there is no holiday calendar). Each production day has three
480-minute shifts starting 07:00 and is labelled by its start date. The served
dataset runs from DATA_START (1 January 2025) to yesterday, a fixed start and a
rolling end (owner, issue #100); a pinned `as_of` (the golden set) keeps the
rolling year `window_days`: every production day from `as_of` minus
DATASET_SPAN_DAYS to `as_of`.
"""
from calendar import monthrange
from dataclasses import dataclass, field
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
ORDER_TRAVEL_FT = (6000, 18000)           # an order's travel at its typical outs: lo + span*r^1.5 (sets its quantity)
TYPICAL_LANE_IN = 46                      # the typical outs of an order fill about this much of the web
SETUPS_PER_WET_END = (2, 3, 3, 3, 4, 4)  # drawn per non-single wet end (about 21,000 lineal ft per paper run)
CAMPAIGN_WET_ENDS = (1, 2, 2, 3)          # paper runs of one grade in a row: an open order carries across a width change
WEEK_SPEED_FACTOR = (0.95, 1.05)          # one draw per production week: week-to-week volume moves a few percent
ORDER_CHANGE_CHOPS = (12, 20)             # chops when the lanes' order combination changes
CONTINUATION_CHOPS = (10, 16)             # chops when an order continues and another lane changes
SHEET_WIDTH_IN = (12, 60)
SHEET_LENGTH_IN = (20, 92)
MAX_OUTS = 6
TRIM_MIN_IN, TRIM_MAX_IN = 2, 14          # side trim: 1 to 7 inches a side
FILLER_TRIGGER_IN = 4                     # a pairing this far above the allowance tries a plant-use filler lane
REMAINDER_SLACK_IN = 2                    # a grade's last remainder runs alone within this of the allowance
REMAINDER_MAX_TRIM_PCT = 6.0              # ... or alone on its narrowest paper up to this trim; a filler lane only past it
ALLOWANCE_OFFSET_IN = 0.4                 # the crews' allowance sits this far under the target share: pairing adds the rest
MIN_SETUP_FT = 1000                       # the shortest lane of a setup runs at least this far
MIN_REMAINDER_FT = 1000                   # an order is never left with less than this to run (at its outs)
POOL_SIZE = 24                            # open orders of the running grade the scheduler pairs from
FANFOLD_SHARE, BOTTOM_SHEET_SHARE, CAP_SHEET_SHARE = 0.02, 0.02, 0.01
OVERRUN = ((0.85, 0.0, 0.02), (0.12, 0.02, 0.06), (0.03, 0.06, 0.075))   # share, |delta| range: actual travel vs the plan
SPLICERS = tuple(f"Splicer {i}" for i in range(1, 6))
KNIFE_PLACES = ("Upper knife", "Lower knife", "Shear knife", "Slitter/scorer")
PM_WEEKDAY, PM_SHIFT = 3, 1               # Thursday, shift 1
PM_MINUTES = (120, 135)
PM_PLACES = ("Double backer", "Single facer", "Glue dams", "Steam system", "Stacker")
POWER_OUTAGE_MINUTES = (10, 15)           # one per month
MONDAY_MAINTENANCE, AUGUST_MAINTENANCE, MONDAY_OPERATOR = 1.40, 1.08, 1.15
RUN_MINUTES_PER_SHIFT = 460               # budget basis for the stop rates
CREW_EPOCH = date(2026, 8, 3)             # the Monday the crew rotation counts weeks from (the original dataset's first Monday)
DATASET_SPAN_DAYS = 365                   # a pinned window (the golden set): every production day from as_of - 365 days to as_of
DATA_START = date(2025, 1, 1)             # the served data's first day: 1 January 2025 to yesterday (owner, issue #100)
COMMENT_EMPTY_SHARE = 0.40                # downtime log rows whose comment is left empty

# Waste boards per lane the record carries at the cutoff knife and the stacker, by change code.
CUTOFF_BOARDS = {"O": (2, 6), "C": (1, 4), "S": (0, 1)}
STACKER_BOARDS = {"O": (1, 5), "C": (0, 3), "S": (0, 1)}

# Roll stock: paper per web position by board grade, flute take-up of a medium, roll lengths.
FLUTES = {"26-C": ("C",), "32-C": ("C",), "55-C": ("C",), "200-C": ("C",), "275-BC": ("B", "C"), "275-EB": ("E", "B")}
PAPERS = {"26-C": ("26# liner", "23# medium"), "32-C": ("35# liner", "26# medium"), "55-C": ("69# liner", "33# medium"),
          "200-C": ("42# liner", "26# medium"), "275-BC": ("42# liner", "26# medium"), "275-EB": ("42# liner", "23# medium")}
TAKE_UP = {"B": 1.36, "C": 1.43, "E": 1.24}
ROLL_FT = {"liner": (14000, 30000), "medium": (20000, 42000)}
PEEL_SHARE = (0.0015, 0.0045)              # outer wraps stripped at prep, of the roll's length
CORE_SHARE = (0.0012, 0.0036)            # paper left on the core at a splice, of the roll's length

CUSTOMER_COUNT = 48
CUSTOMER_SEGMENTS = ("produce", "beverage", "dairy", "meat and poultry", "e-commerce", "industrial parts",
                     "furniture", "household goods")
CUSTOMER_REGIONS = ("North", "South", "East", "West", "Central")

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
# Plausible free-text comments a crew types, by reason (one is drawn; a share stays empty).
COMMENTS = {
    "Knife / slitter jam": ("Jam cleared at {place}; blade and anvil condition not inspected",
                            "Board wrapped at {place}, cleared and restarted", "Slitter jammed at {place}, blades backed off and reset"),
    "Missed splice": ("Splice missed at {place}; web break, chops run until the web settled",
                      "Operator missed splice at {place}, not paying attention", "Splice tape did not hold at {place}"),
    "Raw material (roll)": ("Roll change delayed at {place}; roll condition noted by crew",
                            "Bad roll at {place}, outer wraps damaged", "Roll out of round at {place}, slowed to splice"),
    "Stacker": ("Stacker fault reported at {place}; sheets re-stacked by hand", "Stacker boards fell over at {place}",
                "Jam at {place}, cleared by hand"),
    "Order change": ("Changeover pause at {place} while the new order was set", "Waited on scores at {place} for the new order"),
    "Glue / starch": ("Glue application issue reported at {place}", "Starch running thin at {place}, adjusted gap"),
    "Steam / moisture": ("Steam or moisture issue reported at {place}", "Steam pressure dropped at {place}"),
    "Power outage": ("Power interruption; machine restarted after supply returned",),
    "Other": ("Unclassified stop reported at {place}", "Stopped at {place}, cause not recorded"),
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


def window_bounds(as_of, span_days=DATASET_SPAN_DAYS):
    """(first, last) calendar dates of the rolling window that ends on `as_of` (inclusive)."""
    return as_of - timedelta(days=span_days), as_of


def window_days(as_of, span_days=DATASET_SPAN_DAYS):
    """Every production day of the rolling window ending on `as_of`: Mon-Fri from as_of - span_days to as_of."""
    return weekdays_between(*window_bounds(as_of, span_days))


# ------------------------------------------------------------------ the setup math (issue #100)

def narrowest_web(used_in, min_trim_in, widths=R.WEB_WIDTHS):
    """The narrowest paper width that leaves at least `min_trim_in` of side trim, or None."""
    fitting = [w for w in widths if w - used_in >= min_trim_in]
    return min(fitting) if fitting else None


def trim_pct(used_in, web_in):
    """Side trim as a percentage of the web: (web - used) / web x 100."""
    return 100 * (web_in - used_in) / web_in


def lane_travel_in(remaining_sheets, outs, length_in):
    """Inches of web a lane needs to make `remaining_sheets` at `outs` across: ceil(sheets / outs) cuts."""
    return math.ceil(remaining_sheets / outs) * length_in


def plan_lanes(lanes):
    """The cuts of one setup's lanes [(remaining_sheets, outs, length_in), ...]: the lane needing the
    least travel sets the length (its cuts = ceil(remaining / outs)); every other lane runs only as far
    as that, floor(guide inches / its length), so all lanes end within one knife rotation.
    Returns (guide index, [cuts per lane])."""
    travels = [lane_travel_in(*lane) for lane in lanes]
    guide = min(range(len(lanes)), key=lambda i: (travels[i], i))
    inches = travels[guide]
    return guide, [math.ceil(lanes[i][0] / lanes[i][1]) if i == guide else inches // lanes[i][2] for i in range(len(lanes))]


def best_outs(first_width, partners, web_in, min_trim_in, max_outs=MAX_OUTS):
    """The fill of one web: lane-1 outs, and per partner width its outs, that use the most width while
    leaving at least `min_trim_in`. Returns [(trim_in, (outs1, partner index or None, outs2)), ...]
    in search order (lane-1 outs ascending; alone, then each partner)."""
    options = []
    for outs1 in range(1, max_outs + 1):
        used1 = outs1 * first_width
        if used1 + min_trim_in > web_in:
            break
        options.append((web_in - used1, (outs1, None, 0)))
        room = web_in - min_trim_in - used1
        for index, width in enumerate(partners):
            outs2 = min(max_outs, room // width)
            if outs2 >= 1:
                options.append((web_in - used1 - outs2 * width, (outs1, index, outs2)))
    return options


# ------------------------------------------------------------------ calendar and context

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
    """Everything the stop and quality draws need to know about a shift."""
    slot: _Shift
    monday: bool
    month: int
    knife_story: bool           # the slitter rebuild's jam effect is on (after the PM date, through its end)
    splice_story: bool
    trim_target_pct: float
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


def trim_target_pct(day):
    """The side-trim allowance the crews aim for on `day`: the baseline, plus the slitter rebuild's creep
    of 0.1 point a week from its PM date through `trim_creep_end` (31 October 2026); the baseline again
    after that, so a window past October does not keep growing (owner, PR #91)."""
    rebuild = stories.SLITTER_REBUILD
    if day > rebuild["trim_creep_end"]:
        return rebuild["baseline_trim_pct"]
    after = max(0, (day - rebuild["pm_date"]).days)
    return rebuild["baseline_trim_pct"] + rebuild["trim_creep_pp_per_week"] * after / 7


def knife_story_on(day):
    """The rebuild's knife / slitter jam effect: after the PM date through `knife_slitter_effect_end`
    (31 October 2026, with the trim creep; issue #100)."""
    rebuild = stories.SLITTER_REBUILD
    return rebuild["pm_date"] < day <= rebuild["knife_slitter_effect_end"]


def _context(slot, outage_slots, week_factors=None):
    day = slot.day
    rebuild = stories.SLITTER_REBUILD
    pm = None
    if day.weekday() == PM_WEEKDAY and slot.number == PM_SHIFT:
        if day == rebuild["pm_date"] and slot.number == rebuild["pm_shift"]:
            pm = (rebuild["pm_minutes"], rebuild["place"], "Planned window: slitter/scorer rebuild")
        else:
            index = (day - date(2026, 1, 1)).days // 7
            pm = (None, PM_PLACES[index % len(PM_PLACES)], "Planned maintenance window: {place}")
    splice = stories.SPLICER3_MISSED_SPLICE
    return _Context(slot, day.weekday() == 0, day.month, knife_story_on(day),
                    splice["start"] <= day <= splice["end"], trim_target_pct(day), pm,
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


# ------------------------------------------------------------------ customers

@lru_cache(maxsize=16)
def _customer_table(seed):
    rng = random.Random(f"{seed}:customers")
    rows = [R.CustomerRecord(R.INTERNAL_CUSTOMER, "Plant use (internal)", "internal", "On site", f"{R.INTERNAL_CUSTOMER}-ST1")]
    for n in range(1, CUSTOMER_COUNT + 1):
        cid = f"CU{n:03d}"
        segment = rng.choice(CUSTOMER_SEGMENTS)
        rows.append(R.CustomerRecord(cid, f"Customer {n:03d} ({segment})", segment,
                                     f"{rng.choice(CUSTOMER_REGIONS)} region, {rng.randint(15, 400)} mi",
                                     f"{cid}-ST{rng.randint(1, 3)}"))
    return tuple(rows)


def customers(seed=2026):
    """The customer dimension: synthetic labels, never real companies; CU000 is the plant itself."""
    return list(_customer_table(seed))


@lru_cache(maxsize=16)
def _customer_weights(seed):
    ids = [c.customer_id for c in _customer_table(seed)[1:]]
    return ids, [1 / n ** 0.7 for n in range(1, len(ids) + 1)]


# ------------------------------------------------------------------ the scheduler

class _Planner:
    """Draws the day's orders and schedules them into setups inside paper runs (wet ends), ahead of any
    clock. Orders are drawn per board grade into a pool when the running grade needs them; a setup
    pairs the oldest open order (or the order carried from the previous setup) with the partner that
    fills the web best; the remainder of a lane that did not finish is carried into the next setup."""

    def __init__(self, rng, order_prefix, seed):
        self.rng = rng
        self.order_prefix = order_prefix   # the production day (YYMMDD): order numbers stay unique across days
        self.seed = seed
        self.order_seq = 0
        self.wet = None
        self.wet_count = 0
        self.wet_seq = {}
        self.grade_queue = []
        self.pools = {}
        self.carry = None                  # the order whose remainder must run next
        self.campaign_left = 0             # more paper runs of the running grade before the next grade
        self.previous_orders = ()

    def abandon(self):
        """Drop the current wet end and any carried order (at the end of the production day)."""
        self.wet = None
        self.carry = None
        self.previous_orders = ()

    def _number(self):
        self.order_seq += 1
        if self.order_seq > 9999:
            raise ValueError("more than 9,999 orders in one production day")
        return f"SO-{self.order_prefix}{self.order_seq:04d}"

    def _draw_order(self, quality):
        rng = self.rng
        width, length = rng.randint(*SHEET_WIDTH_IN), rng.randint(*SHEET_LENGTH_IN)
        kind = rng.random()
        fanfold = kind < FANFOLD_SHARE
        sheet_type = "bottom_sheet" if FANFOLD_SHARE <= kind < FANFOLD_SHARE + BOTTOM_SHEET_SHARE else \
            "cap_sheet" if FANFOLD_SHARE + BOTTOM_SHEET_SHARE <= kind < FANFOLD_SHARE + BOTTOM_SHEET_SHARE + CAP_SHEET_SHARE else "product"
        ids, weights = _customer_weights(self.seed)
        customer = R.INTERNAL_CUSTOMER if sheet_type != "product" else rng.choices(ids, weights=weights)[0]
        typical = min(MAX_OUTS, max(1, round(TYPICAL_LANE_IN / width)))
        travel = ORDER_TRAVEL_FT[0] + ORDER_TRAVEL_FT[1] * rng.random() ** 1.5
        quantity = max(100, math.ceil(travel * 12 / length * typical / 10) * 10)
        thick = quality.endswith(("BC", "EB"))
        return {"order_number": self._number(), "part_number": f"P-{rng.randint(10000, 99999)}", "customer_id": customer,
                "w": width, "L": length, "quantity": quantity, "remaining": quantity, "sheet_type": sheet_type,
                "fanfold": fanfold, "filler": False,
                "stack": rng.randint(80, 160) if thick else rng.randint(150, 300)}

    def _pool(self, quality):
        pool = self.pools.setdefault(quality, [])
        while sum(not o["fanfold"] for o in pool) < POOL_SIZE:
            pool.append(self._draw_order(quality))
        return pool

    def _grade(self):
        if not self.grade_queue:
            self.grade_queue = list(GRADES)
            self.rng.shuffle(self.grade_queue)
        return self.grade_queue.pop()

    def _new_wet_end(self, ctx, quality=None, width=None, left=None):
        """Open a paper run: of a new grade (a campaign of CAMPAIGN_WET_ENDS runs, every fifth one a
        one-order run), or, given `quality`, another paper width of the running grade."""
        rng = self.rng
        width = width or rng.choices(R.WEB_WIDTHS, weights=R.WEB_WIDTH_WEIGHTS)[0]
        single = self.wet_count % 5 == 4 and left is None and quality is None
        if quality is None:
            quality = self._grade()
            self.campaign_left = 0 if single else rng.choice(CAMPAIGN_WET_ENDS) - 1
        self.wet_count += 1
        seq = self.wet_seq.get(ctx.slot.date_text, 0) + 1
        self.wet_seq[ctx.slot.date_text] = seq
        if left is None:
            left = 1 if single else rng.choice(SETUPS_PER_WET_END)
        self.wet = {"id": f"WE{ctx.slot.date_text}{seq:03d}", "quality": quality, "width": width,
                    "single": single, "left": left}

    def _allowance(self, ctx, width):
        """The trim the crew leaves on this setup: the day's target share of the web, in whole inches."""
        wanted = ctx.trim_target_pct * width / 100 - ALLOWANCE_OFFSET_IN
        return min(TRIM_MAX_IN - 4, max(TRIM_MIN_IN, _stochastic_round(self.rng, wanted)))

    @staticmethod
    def _valid(lanes):
        """(guide, cuts) of lanes [(order, outs)] when every lane runs at least one cut, the shortest
        runs at least MIN_SETUP_FT, and no order is left with a sliver (under MIN_REMAINDER_FT); else None."""
        guide, cuts = plan_lanes([(o["remaining"], k, o["L"]) for o, k in lanes])
        if cuts[guide] * lanes[guide][0]["L"] < MIN_SETUP_FT * 12:
            return None
        for i, ((order, outs), c) in enumerate(zip(lanes, cuts)):
            if c < 1:
                return None
            left = order["remaining"] - c * outs
            if i != guide and not order["filler"] and 0 < left and lane_travel_in(left, outs, order["L"]) < MIN_REMAINDER_FT * 12:
                return None
        return guide, cuts

    def _fit(self, first, partners, width, allowance, pairs=True):
        """The best (trim, lanes, guide, cuts) on `width`: the fewest trim inches above the allowance."""
        widths = [p["w"] for p in partners] if pairs and not first["fanfold"] else []
        best = None
        for trim, (outs1, index, outs2) in best_outs(first["w"], widths, width, allowance):
            if trim > TRIM_MAX_IN or (best is not None and trim >= best[0]):
                continue
            lanes = [(first, outs1)] + ([(partners[index], outs2)] if index is not None else [])
            checked = self._valid(lanes)
            if checked:
                best = (trim, lanes, *checked)
        return best

    def _filler(self, first, width, allowance, probe=False):
        """Fill a tough trim with a lane of plant-use bottom sheets sized to the room left (`probe`:
        only the trim it would leave, drawing nothing)."""
        best = None
        for outs1 in range(1, MAX_OUTS + 1):
            room = width - allowance - outs1 * first["w"]
            if room < SHEET_WIDTH_IN[0]:
                break
            outs = math.ceil(room / SHEET_WIDTH_IN[1])
            sheet = room // outs
            trim = width - outs1 * first["w"] - outs * sheet
            if best is not None and trim >= best[0]:
                continue
            placeholder = {"w": sheet, "L": SHEET_LENGTH_IN[1], "remaining": 10 ** 9, "filler": True}
            checked = self._valid([(first, outs1), (placeholder, outs)])
            if checked:
                best = (trim, outs1, sheet, outs)
        if best is None or probe:
            return None if best is None else best[0]
        trim, outs1, sheet, outs = best
        filler = {"order_number": self._number(), "part_number": f"P-{self.rng.randint(10000, 99999)}",
                  "customer_id": R.INTERNAL_CUSTOMER, "w": sheet, "L": self.rng.randint(40, 56), "quantity": 0,
                  "remaining": 10 ** 9, "sheet_type": "bottom_sheet", "fanfold": False, "filler": True,
                  "stack": self.rng.randint(150, 300)}
        lanes = [(first, outs1), (filler, outs)]
        return (trim, lanes, *self._valid(lanes))

    def _narrowest_paper(self, order):
        """(web, outs) for one order alone on the paper the scheduler would pick: the narrowest web that
        leaves the minimum trim, at the outs with the smallest trim percentage; None when nothing fits."""
        choices = []
        for outs in range(1, MAX_OUTS + 1):
            web = narrowest_web(outs * order["w"], TRIM_MIN_IN)
            if web is not None and web - outs * order["w"] <= TRIM_MAX_IN and self._valid([(order, outs)]):
                choices.append((trim_pct(outs * order["w"], web), outs, web))
        return min(choices)[1:][::-1] if choices else None

    def _alone_on(self, ctx, order, web, outs):
        return self._fit(order, [], web, min(self._allowance(ctx, web), web - outs * order["w"]), pairs=False)

    def _remainder_setup(self, ctx):
        """The carried order at the end of its grade's campaign, finished in one more setup (often one
        knife, more outs): alone on the current paper when the trim is close to the allowance; else alone
        on the narrowest paper that fits it, or beside a plant-use filler lane, whichever trims less."""
        carry, wet = self.carry, self.wet
        allowance = self._allowance(ctx, wet["width"])
        alone = self._fit(carry, [], wet["width"], allowance, pairs=False)
        if alone and alone[0] <= allowance + REMAINDER_SLACK_IN:
            return alone
        options = []
        paper = self._narrowest_paper(carry)
        if paper is not None:
            web, outs = paper
            options.append((trim_pct(outs * carry["w"], web), 0, paper))
        if alone:
            options.append((100 * alone[0] / wet["width"], 1, None))
        if not options or min(options)[0] > REMAINDER_MAX_TRIM_PCT:
            filler_trim = self._filler(carry, wet["width"], allowance, probe=True)
            if filler_trim is not None:
                options.append((100 * filler_trim / wet["width"], 2, None))
        if not options:
            return None
        _, choice, paper = min(options)
        if choice == 0:
            web, outs = paper
            self._new_wet_end(ctx, quality=wet["quality"], width=web, left=1)
            return self._alone_on(ctx, carry, web, outs)
        return alone if choice == 1 else self._filler(carry, wet["width"], allowance)

    def _single_setup(self, ctx):
        """A one-order paper run: the order (a fanfold one first) alone on the paper picked for it."""
        pool = self._pool(self.wet["quality"])
        papers = [(not o["fanfold"], index, o, self._narrowest_paper(o)) for index, o in enumerate(pool)]
        fitting = sorted((p for p in papers if p[3]), key=lambda p: p[:2])
        if not fitting:
            return None
        first, (web, outs) = fitting[0][2], fitting[0][3]
        self.wet["width"] = web
        return self._alone_on(ctx, first, web, outs)

    def next_setup(self, ctx):
        rng = self.rng
        fit = None
        if self.wet is None or self.wet["left"] == 0:
            if self.carry is not None and self.campaign_left > 0:   # the grade runs on, on another paper width
                self.campaign_left -= 1
                self._new_wet_end(ctx, quality=self.wet["quality"])
            elif self.carry is not None:                             # the campaign's last order finishes
                fit = self._remainder_setup(ctx)
            else:
                if self.campaign_left > 0:
                    self.campaign_left -= 1
                    self._new_wet_end(ctx, quality=self.wet["quality"])
                else:
                    self._new_wet_end(ctx)
                if self.wet["single"]:
                    fit = self._single_setup(ctx)
        wet = self.wet
        wet["left"] = max(0, wet["left"] - 1)
        quality, width = wet["quality"], wet["width"]
        pool = self._pool(quality)
        if fit is None:
            first = self.carry if self.carry is not None else next(o for o in pool if not o["fanfold"])
            pool[:] = [o for o in pool if o is not first]
            partners = [o for o in pool if not o["fanfold"]]
            allowance = self._allowance(ctx, width)
            fit = self._fit(first, partners, width, allowance)
            if not first["fanfold"] and (fit is None or fit[0] > allowance + FILLER_TRIGGER_IN):
                filled = self._filler(first, width, allowance)
                if filled and (fit is None or filled[0] < fit[0]):
                    fit = filled
            if fit is None:
                raise AssertionError(f"no setup fits order {first['order_number']} on {width} in")
        trim_in, lanes, guide, cuts = fit
        carried = {id(o) for o, _ in lanes} & {id(o) for o in self.previous_orders}
        code = "C" if carried else "O"
        self.carry = None
        for (order, outs), c in zip(lanes, cuts):
            pool[:] = [o for o in pool if o is not order]
            if order["filler"]:
                order["quantity"] = c * outs
                order["remaining"] = 0
                continue
            order["remaining"] -= c * outs
            if order["remaining"] > 0:
                assert self.carry is None, "two lanes left open"
                self.carry = order
        self.previous_orders = tuple(o for o, _ in lanes)
        draw = rng.random()
        for share, low, high in OVERRUN:
            if draw < share:
                break
            draw -= share
        delta = rng.choice((-1, 1)) * rng.uniform(low, high)
        nominal_in = cuts[guide] * lanes[guide][0]["L"]
        travel_ft = nominal_in * (1 + delta) / 12
        chops = rng.randint(*(CONTINUATION_CHOPS if code == "C" else ORDER_CHANGE_CHOPS))
        fpm = (GRADES[quality] * CREW_SPEED_FACTOR[ctx.slot.crew] * ctx.week_factor
               * SHIFT_SPEED_FACTOR[ctx.slot.number] * rng.uniform(*SPEED_NOISE))
        run = max(MIN_RUN_SECONDS, round((travel_ft + chops * CHOP_LENGTH_IN / 12) / fpm * 60))
        first_order = lanes[0][0]
        return {"quality": quality, "width": width, "trim_in": trim_in, "code": code, "chops": chops, "fpm": fpm,
                "run": run, "wet_end_id": wet["id"], "travel_in": travel_ft * 12, "guide": guide,
                "lanes": [{"order": o, "outs": k, "cuts": c} for (o, k), c in zip(lanes, cuts)],
                "fanfold": first_order["fanfold"], "sheet_type": first_order["sheet_type"],
                "filler": any(o["filler"] for o, _ in lanes), "frames": [], "truncated": False, "stretched": False}

    def credit(self, plan, parts):
        """A setup cut short at a shift boundary (its last seconds dropped): give the unmade cuts back to
        the lane that was carrying on, so its order still completes in the next setup."""
        for index, lane in enumerate(plan["lanes"]):
            order = lane["order"]
            if index == plan["guide"] or order["filler"]:
                continue
            made = sum(p[index] for p in parts)
            missing = (lane["cuts"] - made) * lane["outs"]
            if missing > 0:
                order["remaining"] += missing
                if self.carry is None or self.carry is order:
                    self.carry = order


# ------------------------------------------------------------------ stops and the time layout

def _comment(rng, reason, place, note=None):
    if note is None and rng.random() < COMMENT_EMPTY_SHARE:
        return ""
    text = note or rng.choice(COMMENTS[reason])
    return f"{text.format(place=place)} {R.COMMENT_LABEL}"


def _stop(rng, kind, reason, place, seconds, chops=0, note=None):
    return {"kind": kind, "reason": reason, "place": place, "seconds": int(seconds),
            "chops": chops, "comment": _comment(rng, reason, place, note)}


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
            if reason == "Knife / slitter jam" and ctx.knife_story:
                scale *= rebuild["knife_slitter_minutes_multiplier"]
        else:
            scale *= MONDAY_OPERATOR if ctx.monday else 1
        lam = minutes * scale / RUN_MINUTES_PER_SHIFT * run_minutes / (mean_seconds / 60)
        for _ in range(_poisson(rng, lam)):
            seconds = max(20, round(mean_seconds * rng.uniform(0.5, 1.5)))
            if reason == "Knife / slitter jam":
                share = rebuild["slitter_place_share_after" if ctx.knife_story else "slitter_place_share_before"]
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

    @property
    def code(self):
        return "S" if self.split_from else self.plan["code"]


def _frame_travel(frame):
    """(lineal_ft, chops, shear_sqft, order travel in inches) of one record from its running time."""
    plan, width = frame.plan, frame.plan["width"]
    lineal = round(_run_seconds(frame.segments) * plan["fpm"] / 60, 1)
    chops = frame.base_chops + sum(s[2]["chops"] for s in frame.segments if s[0] == "stop")
    shear = round(chops * CHOP_LENGTH_IN / 12 * width / 12, 1)
    return lineal, chops, shear, (lineal - shear * 12 / width) * 12


def _nominal_parts(plan):
    """The scheduled cuts per lane of each record a setup plan became. A plan realized in one record
    keeps its cuts; one split at a shift boundary, cut short or stretched shares them by the travel
    each record made, cumulatively, so the parts add up to the plan and every part's lanes still end
    within one knife rotation (the guide's cuts are shared; each other lane runs as far as its pair)."""
    frames = plan["frames"]
    lanes, guide = plan["lanes"], plan["guide"]
    if len(frames) == 1 and not plan["truncated"] and not plan["stretched"]:
        return [[lane["cuts"] for lane in lanes]]
    g0, length = lanes[guide]["cuts"], lanes[guide]["order"]["L"]
    total, cum, before, parts = plan["travel_in"], 0.0, 0, []
    for index, frame in enumerate(frames):
        cum += _frame_travel(frame)[3]
        last = index == len(frames) - 1 and not plan["truncated"] and not plan["stretched"]
        now = g0 if last else max(before, round(g0 * cum / total))
        part = []
        for i, lane in enumerate(lanes):
            if i == guide:
                part.append(now - before)
            else:
                part.append(now * length // lane["order"]["L"] - before * length // lane["order"]["L"])
        parts.append(part)
        before = now
    return parts


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
                    _insert_stop(segments, _stop(rng, "PM", "Planned maintenance", place, minutes * 60, 0, note),
                                 min(run, rng.randint(300, 900)))
                if ctx.outage:
                    _insert_stop(segments, _stop(rng, "Maintenance", "Power outage", "Power",
                                                 rng.randint(*POWER_OUTAGE_MINUTES) * 60,
                                                 note=COMMENTS["Power outage"][0]),
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
                plan["frames"].append(frame)
                return frame

            if total <= boundary:
                emit(segments)
                cursor += timedelta(seconds=total)
                continue
            first, second = _split(segments, boundary)
            if _run_seconds(second) < MIN_RUN_SECONDS:
                emit(first)                       # remainder too small: close here
                plan["truncated"] = True
                if slot.contiguous_next:
                    planner.credit(plan, _nominal_parts(plan))
            elif _run_seconds(first) < MIN_RUN_SECONDS and frames and frames[-1].slot is slot:
                frames[-1].segments.append(("run", boundary))   # stretch the prior record
                frames[-1].plan["stretched"] = True
                if slot.contiguous_next:
                    pending = (plan, segments, split_from, base_chops)
            else:
                frame = emit(first)
                if slot.contiguous_next:
                    pending = (plan, second, frame.setup_id, 0)
                else:
                    plan["truncated"] = True
            cursor = slot.end
        if not slot.contiguous_next:
            pending = None
            planner.abandon()
    return frames


# ------------------------------------------------------------------ records

def _weighted(rng, weights, allowed=None):
    names = [n for n in weights if allowed is None or n in allowed]
    return rng.choices(names, weights=[weights[n] for n in names])[0]


def _reject_line(rng, lane, category, sheets, area, doublewall):
    entries = R.reject_reasons(category)
    allowed = [r for r, e in entries.items() if doublewall or not e.get("doublewall_only")]
    reason = _weighted(rng, stories.WEEKEND_WARP["reason_weights"][category], allowed)
    entry = entries[reason]
    liner = rng.choice(entry["liner"]) if entry["liner"] else None
    side = rng.choice(entry["side"]) if entry["side"] else None
    return R.RejectEntry(lane, category, reason, liner, side, sheets, round(sheets * area, 1))


def _setup_record(rng, frame, parts, ctx, machine_id):
    plan, width = frame.plan, frame.plan["width"]
    elapsed = sum(s[1] for s in frame.segments)
    run = _run_seconds(frame.segments)
    stop_segments = [s for s in frame.segments if s[0] == "stop"]
    end = frame.start + timedelta(seconds=elapsed)
    lineal, chops, shear_sqft, travel_in = _frame_travel(frame)
    gross = round(lineal * width / 12, 1)
    trim = round(travel_in / 12 * plan["trim_in"] / 12, 1)
    used = width - plan["trim_in"]
    warp = stories.WEEKEND_WARP
    rates = {"bond": warp["bond_rate"],
             "warp": warp["warp_rate_by_month"].get(ctx.month, warp["warp_rate_by_month"][9])
             * (warp["monday_multiplier"] if ctx.monday else 1),
             "misalignment": warp["misalignment_rate"], "cracking": warp["cracking_rate"]}
    doublewall = len(FLUTES[plan["quality"]]) == 2
    code = frame.code
    lanes, rejects = [], []
    for number, (spec, nominal) in enumerate(zip(plan["lanes"], parts), 1):
        order, outs, length = spec["order"], spec["outs"], spec["order"]["L"]
        actual = int(travel_in // length)
        if nominal > 0:
            actual = min(max(actual, math.ceil(nominal * 0.9)), math.floor(nominal * 1.1))
        area, share = order["w"] * length / 144, order["w"] * outs / used
        cutoff_boards = min(rng.randint(*CUTOFF_BOARDS[code]), actual * outs)
        stacker_boards = min(rng.randint(*STACKER_BOARDS[code]), actual * outs - cutoff_boards)
        room = actual * outs - cutoff_boards - stacker_boards
        for category in R.GENERATED_REJECT_CATEGORIES:
            target = gross * rates[category] * rng.uniform(0.5, 1.5) * share
            sheets = min(_stochastic_round(rng, target / area), room)
            if sheets:
                room -= sheets
                rejects.append(_reject_line(rng, number, category, sheets, area, doublewall))
        fanfold = {}
        if order["fanfold"]:
            per_pallet = rng.randint(300, 700)
            fanfold = {"folds": actual, "sheets_per_pallet": per_pallet,
                       "pallets": math.ceil(actual * outs / per_pallet), "perforation_length_in": length}
        lanes.append(R.OrderLane(
            lane=number, cutoff_knife=number, order_number=order["order_number"], part_number=order["part_number"],
            customer_id=order["customer_id"], order_quantity=order["quantity"], outs=outs, sheet_width_in=order["w"],
            sheet_length_in=length, nominal_cuts=nominal, actual_cuts=actual,
            cutoff_waste_boards=cutoff_boards, stacker_waste_boards=stacker_boards,
            cutoff_waste_sqft=round(cutoff_boards * area, 1), stacker_waste_sqft=round(stacker_boards * area, 1),
            stack_height_sheets=order["stack"], **fanfold))
    start_date, start_time = R.stamp(frame.start)
    end_date, end_time = R.stamp(end)
    record = R.DryEndSetupRecord(
        setup_id=frame.setup_id, machine_id=machine_id, production_date=frame.slot.date_text, shift=frame.slot.number,
        start_date=start_date, start_time=start_time, end_date=end_date, end_time=end_time, run_seconds=run,
        lineal_ft=lineal, scheduled_lineal_ft=round(max(l.nominal_lineal_ft for l in lanes), 1),
        avg_fpm=round(lineal / (elapsed / 60), 1), web_width_in=width, quality_id=plan["quality"],
        gross_sqft=gross, trim_sqft=trim, trim_pct=round(trim_pct(used, width), 2), shear_sqft=shear_sqft,
        swing_knife_sqft=None, reject_sqft=round(sum(r.sqft for r in rejects if R.counted_as(r.category) == "dry_end"), 1),
        cutoff_waste_sqft=round(sum(l.cutoff_waste_sqft for l in lanes), 1),
        stacker_waste_sqft=round(sum(l.stacker_waste_sqft for l in lanes), 1),
        change_code=code, stop_seconds=sum(s[1] for s in stop_segments), stops=len(stop_segments),
        is_fanfold=plan["fanfold"], sheet_type=plan["sheet_type"], filler=plan["filler"],
        wet_end_id=plan["wet_end_id"], lanes=lanes, rejects=rejects, split_from=frame.split_from)
    stops, moment = [], frame.start
    for segment in frame.segments:
        if segment[0] == "stop":
            payload = segment[2]
            finish = moment + timedelta(seconds=segment[1])
            stops.append(R.StopRecord(
                stop_id="ST" + "".join(R.stamp(moment)), setup_id=frame.setup_id, production_date=frame.slot.date_text,
                shift=frame.slot.number, start_date=R.stamp(moment)[0], start_time=R.stamp(moment)[1],
                end_date=R.stamp(finish)[0], end_time=R.stamp(finish)[1], seconds=segment[1], kind=payload["kind"],
                place=payload["place"], reason=payload["reason"], comment=payload["comment"]))
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
            if s.shift not in shifts:
                shifts.append(s.shift)
        records.append(R.WetEndRecord(
            wet_end_id=wet_end_id, production_date=members[0].production_date, quality_id=members[0].quality_id,
            web_width_in=members[0].web_width_in, start_date=R.stamp(start)[0], start_time=R.stamp(start)[1],
            end_date=R.stamp(end)[0], end_time=R.stamp(end)[1], duration=R.hhmmss((end - start).total_seconds()),
            lineal_ft=lineal, gross_sqft=round(sum(s.gross_sqft for s in members), 1),
            trim_sqft=round(sum(s.trim_sqft for s in members), 1),
            max_fpm=round(max(s.running_fpm for s in members), 1),
            avg_fpm=round(lineal / ((end - start).total_seconds() / 60), 1),
            setup_ids=[s.setup_id for s in members], shifts=shifts))
    return records


def _shift_records(rng, slots, setups):
    by_slot = {}
    for setup in setups:
        by_slot.setdefault((setup.production_date, setup.shift), []).append(setup)
    records = []
    for slot in slots:
        members = by_slot[(slot.date_text, slot.number)]
        sums = {name: round(sum(getattr(s, name) for s in members), 1)
                for name in ("lineal_ft", "gross_sqft", "trim_sqft", "shear_sqft", "reject_sqft", "cutoff_waste_sqft", "stacker_waste_sqft")}
        wet_end_ids = []
        for s in members:
            if s.wet_end_id not in wet_end_ids:
                wet_end_ids.append(s.wet_end_id)
        parts = sum(sums[name] for name in ("trim_sqft", "shear_sqft", "reject_sqft", "cutoff_waste_sqft", "stacker_waste_sqft"))
        records.append(R.ShiftRecord(
            production_date=slot.date_text, shift=slot.number, group_name=slot.crew,
            start_date=R.stamp(slot.start)[0], start_time=R.stamp(slot.start)[1],
            end_date=R.stamp(slot.end)[0], end_time=R.stamp(slot.end)[1],
            duration=R.hhmmss(R.SCHEDULED_SHIFT_MINUTES * 60), **sums,
            max_fpm=round(max(s.running_fpm for s in members), 1),
            avg_fpm=round(sums["lineal_ft"] / R.SCHEDULED_SHIFT_MINUTES, 1), wet_end_ids=wet_end_ids,
            scheduled_minutes=R.SCHEDULED_SHIFT_MINUTES,
            bale_sqft=round(parts + sums["gross_sqft"] * rng.uniform(0.001, 0.004), 1)))
    return records


def _roll_records(rng, wet_ends):
    """The rolls each paper run consumed at each web position. A paper change starts fresh rolls of the
    new width; a roll that runs out is spliced (leaving paper on its core), and the roll on the stand at
    the end of the run is pulled with its butt returned to stock (no core waste)."""
    rolls = []
    for wet in wet_ends:
        flutes = FLUTES[wet.quality_id]
        positions = R.ROLL_POSITIONS_SINGLE_WALL if len(flutes) == 1 else R.ROLL_POSITIONS_DOUBLE_WALL
        liner_paper, medium_paper = PAPERS[wet.quality_id]
        for index, position in enumerate(positions, 1):
            medium = position.endswith("medium")
            flute = flutes[0] if position.startswith("SF1") else flutes[-1]
            need = wet.lineal_ft * (TAKE_UP[flute] if medium else 1.0)
            number = 0
            while need > 0.05:
                number += 1
                length = rng.uniform(*ROLL_FT["medium" if medium else "liner"])
                used = round(min(length, need), 1)
                spliced = length <= need
                need -= min(length, need)
                rolls.append(R.RollRecord(
                    roll_id=f"RL{wet.wet_end_id[2:]}{index}{number:02d}", wet_end_id=wet.wet_end_id,
                    production_date=wet.production_date, position=position,
                    paper_grade=medium_paper if medium else liner_paper, width_in=wet.web_width_in,
                    lineal_ft=round(used, 1), consumed_sqft=round(used * wet.web_width_in / 12, 1),
                    peel_sqft=round(length * rng.uniform(*PEEL_SHARE) * wet.web_width_in / 12, 1),
                    core_sqft=round(length * rng.uniform(*CORE_SHARE) * wet.web_width_in / 12, 1) if spliced else 0.0,
                    ended_at="splice" if spliced else "paper change"))
    return rolls


def generate_day(day, seed=2026, machine_id="CORR-1"):
    """Every record family of one production day (a Monday-Friday `date`), from streams keyed by the
    seed and dates only: the same day is byte-identical in any range that contains it (issue #35).

    Returns {"dry_end_setups": [...], "wet_ends": [...], "shifts": [...], "stops": [...], "rolls": [...]}
    with the dataclasses from `records.py` in time order. The customer dimension is `customers(seed)`.
    """
    if day.weekday() >= 5:
        raise ValueError(f"{day} is a weekend day: production days are Monday to Friday")
    rng = random.Random(f"{seed}:{day.isoformat()}")
    slots = _day_slots(day)
    outage_slots = {outage_slot(seed, day.year, day.month)}
    week_factors = {day.isocalendar()[:2]: week_factor(seed, day - timedelta(days=day.weekday()))}
    frames = _layout(rng, slots, _Planner(rng, day.strftime("%y%m%d"), seed), _machine_code(machine_id), outage_slots, week_factors)
    parts = {}
    for frame in frames:
        plan = frame.plan
        if id(plan) not in parts:
            parts[id(plan)] = iter(_nominal_parts(plan))
    setups, stops = [], []
    for frame in frames:
        record, stop_records = _setup_record(rng, frame, next(parts[id(frame.plan)]),
                                             _context(frame.slot, outage_slots, week_factors), machine_id)
        setups.append(record)
        stops.extend(stop_records)
    wet_ends = _wet_end_records(setups)
    shifts = _shift_records(rng, slots, setups)
    return {"dry_end_setups": setups, "wet_ends": wet_ends, "shifts": shifts, "stops": stops,
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
    data["customers"] = customers(seed)
    data["content_hash"] = content_hash(data)
    data["manifest"] = {"schema": R.SCHEMA, "seed": seed, "first_day": days[0].isoformat(), "last_day": days[-1].isoformat(),
                        "production_days": len(days), "machine_id": machine_id, "content_hash": data["content_hash"],
                        "counts": {name: len(data[name]) for name in R.ALL_FAMILIES},
                        "files": {name: f"{name}.jsonl" for name in R.ALL_FAMILIES}}
    return data


def generate_window(as_of, seed=2026, machine_id="CORR-1", span_days=DATASET_SPAN_DAYS):
    """The rolling window ending on `as_of`: every production day from as_of - span_days to as_of."""
    return generate_days(window_days(as_of, span_days), seed=seed, machine_id=machine_id)


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
    for name in R.ALL_FAMILIES:
        digest.update(name.encode())
        digest.update(jsonl_bytes(data[name]))
    return digest.hexdigest()


def write_jsonl(data, directory):
    """Write one JSONL file per family and dimension plus manifest.json into `directory`."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in R.ALL_FAMILIES:
        (directory / f"{name}.jsonl").write_bytes(jsonl_bytes(data[name]))
    (directory / "manifest.json").write_text(json.dumps(data["manifest"], indent=2, sort_keys=True) + "\n")
    return directory


def read_jsonl(directory):
    """Read the families back as dataclasses; the manifest hash must match."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    data = {}
    for name, cls in R.ALL_FAMILIES.items():
        text = (directory / f"{name}.jsonl").read_text(encoding="utf-8")
        data[name] = [R.from_dict(cls, json.loads(line)) for line in text.splitlines() if line]
    data["content_hash"] = content_hash(data)
    if data["content_hash"] != manifest["content_hash"]:
        raise ValueError("Record files do not match their manifest content hash")
    data["manifest"] = manifest
    return data
