"""Demo record families shaped like a generic corrugator's own records (record contract v2, issue #100).

Plain, JSON-serialisable dataclasses: the dry-end setup record (one per run setup,
up to three order lanes across the web, the fact table), the wet-end record (one
paper run), the shift record, the stop record (the downtime log), the roll record
(roll stock fed into a paper run) and the customer dimension. Field names and units
mirror the shape a corrugator's MES records them in, so the data reads like plant
records; it describes a generic dry-end setup record, not any one vendor's table,
and claims no wire-format compatibility. docs/RECORD_CONTRACT.md maps every field to
its typical MES field and to the store column; docs/RECORDS.md documents the families.

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
# Paper (web) widths on a 110-inch machine, 90 to 110 inches in 5-inch steps (issue #100). The scheduler
# runs every setup on the narrowest of these at least as wide as its used width plus the minimum trim.
WEB_WIDTHS = (90, 95, 100, 105, 110)
MACHINE_WIDTH_IN = 110
# The type of change that started a setup record, a code with a lookup (owner, 2026-11-11): O a regular
# order change, S a shift split (the setup continues `split_from_setup_id` across a shift boundary).
# Other codes are added here as they appear.
CHANGE_CODES = {"O": "order change", "S": "shift split"}
SHEET_TYPES = ("product", "bottom_sheet", "cap_sheet")
CUTOFF_KNIVES = (1, 2)   # lane N is assigned to cutoff knife N, which discharges to stacker N
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

TAXONOMY_PATH = Path(__file__).with_name("reject_reasons.json")


@lru_cache(maxsize=1)
def taxonomy():
    """The dry-end reject taxonomy (reject_reasons.json): category -> reason, positions as attributes."""
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def categories():
    """{category key: category entry} in the taxonomy's order."""
    return {c["key"]: c for c in taxonomy()["categories"]}


@lru_cache(maxsize=1)
def families():
    """{family code: family entry}: BOND, WARP, MISALIGNMENT, OTHER (dry-end quality), CHANGEOVER, ADMIN."""
    return dict(taxonomy()["families"])


def family_of(category):
    return categories()[category]["family"]


def counted_in(category):
    """Which waste line a category's rejects count in: dry_end, shear or excluded."""
    return families()[family_of(category)]["counted_in"]


# The four families of dry-end quality waste, in tile order: the dry-end tile is exactly their sum.
DRY_END_FAMILIES = ("BOND", "WARP", "MISALIGNMENT", "OTHER")


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
    """One order lane of a dry-end setup: one customer's slice of the web (lanes 1-3, stored embedded on
    the setup record as the MES does; the store's `setup_orders` is the one-row-per-lane view).

    lane: 1..3 across the web. cutoff_allocation: the cutoff knife the lane is assigned to (lane 1 ->
        knife 1 -> stacker 1).
    order_number, part_number, customer_id: the order, its item and its customer (the customer dimension).
    order_quantity: sheets the order asks for, over all its setups (ours: the demand the scheduler fills).
    sheet_width_in, sheet_length_in: the ordered sheet; the length is the cutoff. Widths are in eighths.
    number_of_outs: sheets across the web for this lane (outs live on the lane, never on the customer).
    nominal_cuts: the scheduled cuts of this lane on this record; actual_cuts: as run (the over/under-run
        window is +-10% of nominal). sheets = cuts x outs. lineal = cuts x length / 12.
    sheet_type: product, bottom_sheet or cap_sheet (pallet sheets run for the plant's own use);
        filler: the lane was run to fill a tough trim, not for a customer order.
    cutoff_waste_boards, stacker_waste_boards: the default waste-board counts the record carries, boards
        lost at the lane's cutoff knife and at its stacker; *_sqft converts them with the lane's sheet size.
        Their own waste lines, never dry-end quality waste.
    stack_height_sheets: sheets per stack delivered by the stacker.
    folds, sheets_per_pallet, pallets, perforation_length_in: fanfold delivery only, null otherwise.
    """
    lane: int
    order_number: str
    part_number: str
    customer_id: str
    order_quantity: int
    sheet_width_in: float
    sheet_length_in: int
    number_of_outs: int
    cutoff_allocation: int
    nominal_cuts: int
    actual_cuts: int
    sheet_type: str
    filler: bool
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
        return self.sheet_width_in * self.number_of_outs

    @property
    def nominal_ft(self):
        return self.nominal_cuts * self.sheet_length_in / 12

    @property
    def actual_ft(self):
        return self.actual_cuts * self.sheet_length_in / 12


@dataclass
class RejectEntry:
    """One dry-end reject line: counted sheets of one lane, by category and reason (reject_reasons.json).

    category: the taxonomy's category key (bond, warp, cracking, misalignment, handling, paper,
        changeover, print, admin); its family and its waste line follow from the taxonomy.
    reason: the reported reason within the category (a reported observation, not a proven cause).
    liner, side: where on the board, as attributes, never as reasons: liner top / bottom / medium / DB /
        SF, side drive / operator / middle; null when the reason carries no position.
    lane: the lane whose sheets were rejected. sheets: individual sheets; sqft = sheets x width x length / 144.
    """
    category: str
    reason: str
    lane: int
    sheets: int
    sqft: float
    liner: str | None = None
    side: str | None = None

    @property
    def family(self):
        return family_of(self.category)


@dataclass
class DryEndSetupRecord:
    """One dry-end run setup, the fact table. A setup is closed when the order lanes change (O) or
    at a shift boundary (S).

    setup_id: 20-character run-setup id (machine code + start stamp + seq).
    machine_id: the corrugator the record belongs to.
    start_date/start_time, end_date/end_time: YYYYMMDD / HHMMSS, demo-local.
    actual_run_sec: running time, elapsed minus the stops inside the record.
    lineal_ft: all web travel through the machine during the record, as run: order travel (the
        longest lane's actual cuts x length / 12) plus the shear chops. Gross = lineal_ft x web / 12.
    total_nominal_ft: the scheduled lineal feet, the longest lane's nominal cuts x length / 12 (the plan;
        lineal_ft is the actual).
    avg_speed_fpm: lineal_ft / elapsed minutes, elapsed INCLUDING stops.
    web_width_in: paper (web) width, one of WEB_WIDTHS.
    quality_id: the board grade key (fixture.GRADES).
    total_produced_sqft: gross square feet, trim and chops included.
    trim_sqft: side-trim area = order travel x trim inches / 12; trim inches = web - used width, split
        evenly both sides. trim_pct: 100 x trim inches / web width.
    change_code: a key of CHANGE_CODES.
    stop_time_sec, number_of_stops: the setup's summary of the stop log: the sum of seconds and the
        count of the StopRecords naming this setup_id.
    shift_number: 1, 2 or 3. production_date: YYYYMMDD of the production day (07:00 start).
    chop_sqft: full-web shear chops, 34 inches each, part of gross.
    swing_knife_sqft: area cut by the swing knife; null when the machine has none (most machines),
        never zero.
    is_fanfold: the setup ran to a fanfold delivery.
    sheet_type: product when any lane is a product, else the lanes' pallet-sheet type; filler: a lane
        was added to fill a tough trim.
    lanes: 1..3 OrderLane, one order per lane. rejects: the reject lines.
    wet_end_id: the paper run that aggregates this setup.
    split_from_setup_id: for change_code S, the record this one continues.
    """
    setup_id: str
    machine_id: str
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    actual_run_sec: int
    lineal_ft: float
    total_nominal_ft: float
    avg_speed_fpm: float
    web_width_in: int
    quality_id: str
    total_produced_sqft: float
    trim_sqft: float
    trim_pct: float
    change_code: str
    stop_time_sec: int
    number_of_stops: int
    shift_number: int
    chop_sqft: float
    swing_knife_sqft: float | None
    is_fanfold: bool
    sheet_type: str
    filler: bool
    lanes: list[OrderLane]
    production_date: str
    rejects: list[RejectEntry]
    wet_end_id: str
    split_from_setup_id: str | None = None

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
    def chop_ft(self):
        return self.chop_sqft * 12 / self.web_width_in

    @property
    def order_travel_ft(self):
        """Travel that reached the cutoff knives as order sheets: the longest lane."""
        return self.lineal_ft - self.chop_ft

    @property
    def used_width_in(self):
        return sum(lane.sheet_width_in * lane.number_of_outs for lane in self.lanes)

    @property
    def trim_in(self):
        return self.web_width_in - self.used_width_in

    @property
    def running_speed_fpm(self):
        """Speed while the web was moving: lineal over elapsed minus stops."""
        return self.lineal_ft / ((self.elapsed_seconds - self.stop_time_sec) / 60)

    def reject_sqft_in(self, line):
        """Reject area counted in one waste line: dry_end, shear (changeover) or excluded (admin)."""
        return sum(r.sqft for r in self.rejects if counted_in(r.category) == line)

    @property
    def reject_sqft(self):
        """Dry-end quality rejects: the four dry-end families, never changeover or admin."""
        return self.reject_sqft_in("dry_end")

    @property
    def changeover_sqft(self):
        return self.reject_sqft_in("shear")

    @property
    def admin_sqft(self):
        return self.reject_sqft_in("excluded")

    @property
    def cutoff_waste_sqft(self):
        return sum(lane.cutoff_waste_sqft for lane in self.lanes)

    @property
    def stacker_waste_sqft(self):
        return sum(lane.stacker_waste_sqft for lane in self.lanes)


@dataclass
class WetEndRecord:
    """One paper run: consecutive setups sharing web width and board grade. Totals are exact sums
    over setup_ids.

    wet_end_id: WE + production date + sequence.
    quality_id, web_width_in: constant across its setups.
    start_date/start_time, end_date/end_time, duration (HHMMSS).
    lineal_ft, total_produced_sqft, side_trim_sqft: sums of its setups.
    max_speed_fpm: highest running speed of its setups.
    avg_speed_fpm: lineal_ft / elapsed minutes, stops included.
    setup_ids: the setup records aggregated, in time order.
    shift_numbers: the shift numbers its setups fall in (1..2 entries).
    production_date: production day of its first setup.
    """
    wet_end_id: str
    quality_id: str
    web_width_in: int
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    duration: str
    lineal_ft: float
    total_produced_sqft: float
    side_trim_sqft: float
    max_speed_fpm: float
    avg_speed_fpm: float
    setup_ids: list[str]
    shift_numbers: list[int]
    production_date: str

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)


@dataclass
class ShiftRecord:
    """One 480-minute shift. Totals are exact sums over the setup records carrying this
    production_date and shift_number.

    shift_number: 1 (07:00), 2 (15:00), 3 (23:00). production_date: YYYYMMDD.
    group_name: crew label Crew A / B / C, rotating weekly; never a person.
    start_date/start_time, end_date/end_time, duration (always 080000).
    total_lineal_ft, total_produced_sqft, side_trim_sqft: sums of setups.
    chop_sqft: shear chops. reject_sqft: dry-end quality rejects (the four dry-end families).
    changeover_sqft: changeover rejects (counted with shear). admin_sqft: planning / admin entries.
    cutoff_waste_sqft, stacker_waste_sqft: the lanes' cutoff-knife and stacker waste boards.
    max_speed_fpm: highest running speed of its setups.
    avg_speed_fpm: total_lineal_ft / 480, downtime included.
    wet_end_ids: wet ends with at least one setup in the shift.
    scheduled_minutes: 480.
    bale_sqft: non-protocol scale reading of baled waste = every recorded waste line (trim, chops, all
        reject lines, cutoff and stacker waste boards) + a small unexplained remainder (0.1-0.4% of
        gross), so bale percent reads slightly above the sum of its parts.
    """
    shift_number: int
    production_date: str
    group_name: str
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    duration: str
    total_lineal_ft: float
    total_produced_sqft: float
    side_trim_sqft: float
    chop_sqft: float
    reject_sqft: float
    changeover_sqft: float
    admin_sqft: float
    cutoff_waste_sqft: float
    stacker_waste_sqft: float
    max_speed_fpm: float
    avg_speed_fpm: float
    wet_end_ids: list[str]
    scheduled_minutes: int
    bale_sqft: float

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)

    @property
    def accounted_sqft(self):
        """Every recorded waste line; the bale minus this is the unexplained remainder."""
        return (self.side_trim_sqft + self.chop_sqft + self.reject_sqft + self.changeover_sqft + self.admin_sqft
                + self.cutoff_waste_sqft + self.stacker_waste_sqft)


@dataclass
class StopRecord:
    """One row of the downtime log, always inside exactly one setup.

    stop_id: ST + start stamp. start_date/start_time, end_date/end_time.
    duration_sec: whole seconds = end - start.
    kind: the stop category: Maintenance, Operator or PM (the scheduled planned-maintenance window).
    place: the reason group, an equipment location from STOP_PLACES.
    reason: one of STOP_REASONS - the nine unplanned reasons, or "Planned maintenance" for every
        PM-kind stop; a recorded symptom, not a cause.
    shift_number, production_date: the shift the stop fell in.
    setup_id: the dry-end setup record that contains the stop.
    comment: the operator's free text, verbatim; often empty ("").
    """
    stop_id: str
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    duration_sec: int
    kind: str
    place: str
    reason: str
    shift_number: int
    production_date: str
    setup_id: str
    comment: str

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)


@dataclass
class RollRecord:
    """One roll of paper fed into a paper run (wet end) on one roll stand.

    roll_id: R + production date + sequence. wet_end_id: the paper run it fed.
    stand: the roll stand (top liner, medium, bottom liner; a double-wall grade adds a second medium
        and the single-facer liner). paper_grade: the paper on the roll. width_in: the run's web width.
    lineal_ft: feet of paper the roll fed into the run (a medium's feet include its flute take-up).
    peel_sqft: outer wraps stripped at roll prep. core_sqft: paper left on the core when the roll was
        spliced out; 0 when the roll was taken down at the paper change and returned to stock.
    spliced_out: the roll ran down to its core inside the run.
    production_date: YYYYMMDD of the run's production day.
    """
    roll_id: str
    wet_end_id: str
    stand: str
    paper_grade: str
    width_in: int
    lineal_ft: float
    peel_sqft: float
    core_sqft: float
    spliced_out: bool
    production_date: str


@dataclass
class CustomerRecord:
    """The customer dimension: who an order lane's sheets are for. No measures (outs and sheet sizes
    live on the order lane). C000 is the plant itself (pallet bottom and cap sheets)."""
    customer_id: str
    name: str
    destination: str
    ship_to: str


# Per-day families (every record carries a production_date) and the dimensions beside them.
FAMILIES = {"dry_end_setups": DryEndSetupRecord, "wet_ends": WetEndRecord,
            "shifts": ShiftRecord, "stops": StopRecord, "rolls": RollRecord}
DIMENSIONS = {"customers": CustomerRecord}


@lru_cache(maxsize=None)
def _layout(cls):
    """(field names, {name: nested dataclass} for list-of-dataclass fields) of a record class."""
    nested = {}
    for item in fields(cls):
        if get_origin(item.type) is list and is_dataclass(get_args(item.type)[0]):
            nested[item.name] = get_args(item.type)[0]
    return tuple(item.name for item in fields(cls)), nested


def to_dict(record):
    """A record as a plain dict, nested blocks included: the same result as dataclasses.asdict for these
    flat records (plain values and lists of blocks), without its deep copy, which dominated hashing a year."""
    names, nested = _layout(type(record))
    out = {name: getattr(record, name) for name in names}
    for name in nested:
        out[name] = [to_dict(item) for item in out[name]]
    for name in names:
        if name not in nested and isinstance(out[name], list):
            out[name] = list(out[name])
    return out


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


def shift_window(production_date, shift_number):
    """(start, end) datetimes of a shift in the production day starting 07:00."""
    origin = datetime.strptime(production_date, "%Y%m%d") + timedelta(hours=7)
    start = origin + timedelta(hours=8 * (shift_number - 1))
    return start, start + timedelta(minutes=SCHEDULED_SHIFT_MINUTES)
