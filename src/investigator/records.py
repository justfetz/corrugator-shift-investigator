"""Record contract v2: a generic corrugator dry-end setup record as the fact table (issue #100).

Plain, JSON-serialisable dataclasses for the record families of one machine:
the dry-end setup (one row per run setup, with up to three order lanes
embedded as the record carries them), the wet-end paper run, the shift, the
downtime log (one row per stop, linked to its setup), the roll stock each paper
run consumed, and the customer dimension the lanes point at. Field names follow
docs/RECORD_CONTRACT.md, which maps every field to the typical MES field it
stands for (or says why one is not modelled); docs/RECORDS.md documents the
generator's behaviour. This is the project's own documented demo schema: it
describes how corrugators in general record setups and claims no vendor's
wire format.

Units: feet, inches, square feet, ft/min, whole seconds. Dates are YYYYMMDD
and times HHMMSS in the fixed demo-local clock (no timezone, no DST).
"""
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
from typing import get_args, get_origin

SCHEMA = "demo-records/v2"
# Paper widths on a 110-inch machine, 90-110 in 5-inch steps (issue #100). The generator draws a new
# paper run's width by these weights, about a 98-inch average.
WEB_WIDTHS = (110, 105, 100, 95, 90)
WEB_WIDTH_WEIGHTS = (6, 18, 30, 28, 18)
MACHINE_WIDTH_IN = 110
# Change codes, as a code with a lookup: O a regular order change, S a shift change (the setup split at
# the shift boundary, continuing `split_from`), C a continuation (an order carried from the previous
# setup while another lane changed). Others are added here as they appear.
CHANGE_CODES = {"O": "order change", "S": "shift split", "C": "continuation"}
SHEET_TYPES = ("product", "bottom_sheet", "cap_sheet")
LANES = (1, 2, 3)
STOP_KINDS = ("Maintenance", "Operator", "PM")
STOP_PLACES = ("Upper knife", "Lower knife", "Shear knife", "Slitter/scorer",
               "Double backer", "Splicer 1", "Splicer 2", "Splicer 3", "Splicer 4",
               "Splicer 5", "Glue dams", "Stacker", "Single facer", "Steam system",
               "Power")
UNPLANNED_STOP_REASONS = ("Knife / slitter jam", "Missed splice", "Raw material (roll)",
                          "Stacker", "Order change", "Glue / starch", "Steam / moisture",
                          "Power outage", "Other")
PLANNED_STOP_REASON = "Planned maintenance"   # the reason every PM-kind stop carries
STOP_REASONS = UNPLANNED_STOP_REASONS + (PLANNED_STOP_REASON,)
CREWS = ("Crew A", "Crew B", "Crew C")
SCHEDULED_SHIFT_MINUTES = 480
INTERNAL_CUSTOMER = "CU000"   # the plant itself: bottom sheets, cap sheets and trim fillers
ROLL_POSITIONS_SINGLE_WALL = ("SF1 liner", "SF1 medium", "DB liner")
ROLL_POSITIONS_DOUBLE_WALL = ("SF1 liner", "SF1 medium", "SF2 liner", "SF2 medium", "DB liner")
ROLL_ENDS = ("splice", "paper change")
COMMENT_LABEL = "(synthetic note, unverified)"   # every non-empty stop comment ends with it


@lru_cache(maxsize=1)
def reject_taxonomy():
    """The owner-approved reject reasons (reject_reasons.json): category -> reasons, positions."""
    return json.loads(Path(__file__).with_name("reject_reasons.json").read_text(encoding="utf-8"))


def reject_categories():
    """{category key: category entry} in the taxonomy's order."""
    return {c["key"]: c for c in reject_taxonomy()["categories"]}


def reject_reasons(category):
    """{reason: reason entry} of one category."""
    return {r["reason"]: r for r in reject_categories()[category]["reasons"]}


def counted_as(category):
    """Where a category's area is reported: dry_end, changeover or excluded."""
    return reject_categories()[category]["counted_as"]


DRY_END_CATEGORIES = tuple(k for k, c in reject_categories().items() if c["counted_as"] == "dry_end")
# The dry-end families the KPI tiles split dry-end waste into, and the only categories the demo
# generator emits, so the four add to the dry-end total.
GENERATED_REJECT_CATEGORIES = ("bond", "warp", "misalignment", "cracking")
LINERS = tuple(reject_taxonomy()["positions"]["liner"])
SIDES = tuple(reject_taxonomy()["positions"]["side"])


def stamp(moment):
    """Split a datetime into the record pair (YYYYMMDD, HHMMSS)."""
    return moment.strftime("%Y%m%d"), moment.strftime("%H%M%S")


def unstamp(date_text, time_text):
    """Rebuild a datetime from a (YYYYMMDD, HHMMSS) pair."""
    return datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")


def hhmmss(seconds):
    """Whole seconds as HHMMSS; hours may exceed 23 for long spans."""
    seconds = int(seconds)
    return f"{seconds // 3600:02d}{seconds % 3600 // 60:02d}{seconds % 60:02d}"


def hhmmss_seconds(text):
    return int(text[:-4]) * 3600 + int(text[-4:-2]) * 60 + int(text[-2:])


@dataclass
class OrderLane:
    """One order lane (1-3) across the web of a dry-end setup: one customer's slice.

    lane: 1..3, across the web. cutoff_knife: the cutoff knife the lane is assigned to (lane 1 ->
        knife 1 -> stacker 1); the machine has two cutoff knives.
    order_number, part_number, customer_id (-> customers). order_quantity: sheets the order asks for.
    outs: sheets across the web for this lane. sheet_width_in x sheet_length_in: the sheet; length
        is the cutoff.
    nominal_cuts: scheduled cuts on this setup; actual_cuts: counted cuts. sheets = cuts x outs.
    cutoff_waste_boards, stacker_waste_boards: boards lost at the lane's cutoff knife and at its
        stacker, the default counts the record carries; *_sqft is boards x sheet area. They are their
        own waste lines, never dry-end quality waste.
    stack_height_sheets: sheets per stack delivered by the stacker.
    folds, sheets_per_pallet, pallets, perforation_length_in: fanfold lanes only; null otherwise.
    """
    lane: int
    cutoff_knife: int
    order_number: str
    part_number: str
    customer_id: str
    order_quantity: int
    outs: int
    sheet_width_in: int
    sheet_length_in: int
    nominal_cuts: int
    actual_cuts: int
    cutoff_waste_boards: int
    stacker_waste_boards: int
    cutoff_waste_sqft: float
    stacker_waste_sqft: float
    stack_height_sheets: int
    folds: int | None = None
    sheets_per_pallet: int | None = None
    pallets: int | None = None
    perforation_length_in: int | None = None

    @property
    def sheet_sqft(self):
        return self.sheet_width_in * self.sheet_length_in / 144

    @property
    def used_width_in(self):
        return self.outs * self.sheet_width_in

    @property
    def nominal_sheets(self):
        return self.nominal_cuts * self.outs

    @property
    def actual_sheets(self):
        return self.actual_cuts * self.outs

    @property
    def nominal_lineal_ft(self):
        return self.nominal_cuts * self.sheet_length_in / 12

    @property
    def actual_lineal_ft(self):
        return self.actual_cuts * self.sheet_length_in / 12


@dataclass
class RejectEntry:
    """One dry-end reject line: counted sheets of one lane, as category -> reason with positions.

    lane: the lane whose sheets were rejected. category, reason: reject_reasons.json (a reported
        observation, not a proven cause). liner (top / bottom / medium / DB / SF) and side (drive /
        operator / middle) are positions, null where the reason takes none: "bottom liner" is an
        identity, not a cause.
    sheets: individual sheets; sqft = sheets x sheet width x sheet length / 144.
    """
    lane: int
    category: str
    reason: str
    liner: str | None
    side: str | None
    sheets: int
    sqft: float


@dataclass
class DryEndSetupRecord:
    """One dry-end run setup: the fact table. A setup closes when the lanes' order combination
    changes (O / C) or at a shift boundary (S).

    setup_id: 20-character run-setup id (machine code + start stamp + sequence in the shift).
    machine_id. production_date: YYYYMMDD of the production day (07:00 start); shift: 1, 2 or 3.
    start_date/start_time, end_date/end_time: YYYYMMDD / HHMMSS, demo-local.
    run_seconds: running time (elapsed minus stop_seconds).
    lineal_ft: actual web travel through the machine, order sheets and shear chops alike; gross is
        lineal_ft x web / 12. scheduled_lineal_ft: the plan, the longest lane's nominal travel.
    avg_fpm: lineal_ft / elapsed minutes, elapsed INCLUDING stops.
    web_width_in: paper width, one of WEB_WIDTHS. quality_id: the board grade key (fixture.GRADES).
    gross_sqft. trim_sqft: side trim = order travel x trim inches / 12; trim_pct = 100 x trim inches
        / web width, the setup's geometric trim percentage.
    shear_sqft: full-web shear chops (34 inches each), part of gross.
    swing_knife_sqft: swing-knife area; null when the machine has no swing knife (never zero).
    reject_sqft: dry-end quality rejects, the sum of the reject lines counted as dry-end.
    cutoff_waste_sqft, stacker_waste_sqft: the lanes' waste boards at the knives and stackers.
    change_code: CHANGE_CODES. stop_seconds, stops: the downtime log rows of this setup.
    is_fanfold: the setup runs fanfold. sheet_type: product, bottom_sheet or cap_sheet (the guide
        lane's); filler: a lane of plant-use sheets was added to fill a tough trim.
    wet_end_id: the paper run. split_from: for change_code S, the setup this one continues.
    lanes: 1..3 OrderLane. rejects: RejectEntry lines.
    """
    setup_id: str
    machine_id: str
    production_date: str
    shift: int
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    run_seconds: int
    lineal_ft: float
    scheduled_lineal_ft: float
    avg_fpm: float
    web_width_in: int
    quality_id: str
    gross_sqft: float
    trim_sqft: float
    trim_pct: float
    shear_sqft: float
    swing_knife_sqft: float | None
    reject_sqft: float
    cutoff_waste_sqft: float
    stacker_waste_sqft: float
    change_code: str
    stop_seconds: int
    stops: int
    is_fanfold: bool
    sheet_type: str
    filler: bool
    wet_end_id: str
    lanes: list[OrderLane]
    rejects: list[RejectEntry]
    split_from: str | None = None

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)

    @property
    def elapsed_seconds(self):
        return int((self.end - self.start).total_seconds())

    @property
    def shear_ft(self):
        return self.shear_sqft * 12 / self.web_width_in

    @property
    def order_travel_ft(self):
        """Travel that reached the cutoff knives as order sheets."""
        return self.lineal_ft - self.shear_ft

    @property
    def used_width_in(self):
        return sum(lane.used_width_in for lane in self.lanes)

    @property
    def trim_in(self):
        return self.web_width_in - self.used_width_in

    @property
    def running_fpm(self):
        """Speed while the web was moving: lineal over running minutes."""
        return self.lineal_ft / (self.run_seconds / 60)


@dataclass
class WetEndRecord:
    """One paper run: consecutive setups sharing web width and board grade. Totals are exact sums
    over setup_ids.

    wet_end_id: WE + production date + sequence. quality_id, web_width_in: constant across its setups.
    start_date/start_time, end_date/end_time, duration (HHMMSS).
    lineal_ft, gross_sqft, trim_sqft: sums of its setups. max_fpm: the highest running speed of its
    setups; avg_fpm: lineal_ft / elapsed minutes, stops included. setup_ids: in time order.
    shifts: the shifts its setups fall in (1..2 entries). production_date: of its first setup.
    """
    wet_end_id: str
    production_date: str
    quality_id: str
    web_width_in: int
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    duration: str
    lineal_ft: float
    gross_sqft: float
    trim_sqft: float
    max_fpm: float
    avg_fpm: float
    setup_ids: list[str]
    shifts: list[int]

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)


@dataclass
class ShiftRecord:
    """One 480-minute shift. Totals are exact sums over the setups carrying this production_date
    and shift.

    production_date, shift: 1 (07:00), 2 (15:00), 3 (23:00). group_name: crew label Crew A / B / C,
    rotating weekly; never a person. start_date/start_time, end_date/end_time, duration (080000).
    lineal_ft, gross_sqft, trim_sqft, shear_sqft, reject_sqft, cutoff_waste_sqft, stacker_waste_sqft:
    sums of setups. max_fpm: the highest running speed of its setups; avg_fpm: lineal_ft / 480,
    downtime included. wet_end_ids: wet ends with a setup in the shift. scheduled_minutes: 480.
    bale_sqft: scale reading of baled waste = trim + shear + rejects + waste boards + a small
    unexplained remainder (0.1-0.4% of gross), so bale percent reads slightly above its parts.
    """
    production_date: str
    shift: int
    group_name: str
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    duration: str
    lineal_ft: float
    gross_sqft: float
    trim_sqft: float
    shear_sqft: float
    reject_sqft: float
    cutoff_waste_sqft: float
    stacker_waste_sqft: float
    max_fpm: float
    avg_fpm: float
    wet_end_ids: list[str]
    scheduled_minutes: int
    bale_sqft: float

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)


@dataclass
class StopRecord:
    """One row of the downtime log, always inside exactly one setup.

    stop_id: ST + start stamp. setup_id: the run setup it belongs to. production_date, shift.
    start_date/start_time, end_date/end_time; seconds = end - start.
    kind: the category - Maintenance, Operator or PM (the scheduled planned-maintenance window).
    place: where (the reason group): STOP_PLACES. reason: what, STOP_REASONS - a recorded symptom,
        not a cause. comment: free text, often empty; a non-empty one is synthetic and ends with
        COMMENT_LABEL. Comments are quoted verbatim, never read as numbers.
    """
    stop_id: str
    setup_id: str
    production_date: str
    shift: int
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    seconds: int
    kind: str
    place: str
    reason: str
    comment: str

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)


@dataclass
class RollRecord:
    """One roll of paper a wet-end run consumed at one web position.

    roll_id. wet_end_id: the paper run it fed; production_date: of that run. position: the web it
    fed (SF1 liner, SF1 medium, SF2 liner, SF2 medium, DB liner). paper_grade, width_in.
    lineal_ft, consumed_sqft: paper fed into the machine (a medium includes its flute take-up).
    peel_sqft: outer wraps stripped at roll prep. core_sqft: paper left on the core at a splice
    (zero when the roll was pulled at the paper change: the butt goes back to stock).
    ended_at: splice or paper change.
    """
    roll_id: str
    wet_end_id: str
    production_date: str
    position: str
    paper_grade: str
    width_in: int
    lineal_ft: float
    consumed_sqft: float
    peel_sqft: float
    core_sqft: float
    ended_at: str


@dataclass
class CustomerRecord:
    """The customer dimension: who an order lane's sheets are for. No measures.

    customer_id (CU000 is the plant itself). name: synthetic label, never a real company.
    segment, destination, ship_to.
    """
    customer_id: str
    name: str
    segment: str
    destination: str
    ship_to: str


# Record families keyed by production day, then the dimensions (not keyed by day).
FAMILIES = {"dry_end_setups": DryEndSetupRecord, "wet_ends": WetEndRecord,
            "shifts": ShiftRecord, "stops": StopRecord, "rolls": RollRecord}
DIMENSIONS = {"customers": CustomerRecord}
ALL_FAMILIES = {**FAMILIES, **DIMENSIONS}


def to_dict(record):
    return asdict(record)


def from_dict(cls, data):
    """Rebuild a record (and nested blocks) from a plain dict."""
    values = {}
    for item in fields(cls):
        value = data[item.name] if item.name in data else item.default
        kind = item.type
        if get_origin(kind) is list and is_dataclass(get_args(kind)[0]):
            value = [from_dict(get_args(kind)[0], v) for v in value]
        values[item.name] = value
    return cls(**values)


def shift_window(production_date, shift):
    """(start, end) datetimes of a shift in the production day starting 07:00."""
    origin = datetime.strptime(production_date, "%Y%m%d") + timedelta(hours=7)
    start = origin + timedelta(hours=8 * (shift - 1))
    return start, start + timedelta(minutes=SCHEDULED_SHIFT_MINUTES)
