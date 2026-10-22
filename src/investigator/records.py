"""Demo record families shaped like a corrugator controller's own record types.

Four plain, JSON-serialisable dataclasses: the dry-end setup record (one per
setup, "P04P"-shaped), the wet-end record ("P04G"-shaped), the shift record
("P04T"-shaped) and the stop record ("P04S"-shaped). Field names and units
mirror the machine's record *shape* so the data reads like plant records.
This is the project's own documented demo schema; it claims no vendor
wire-format compatibility. Field-by-field documentation is docs/RECORDS.md.

Units: feet, inches, square feet, ft/min, whole seconds. Dates are YYYYMMDD
and times HHMMSS in the fixed demo-local clock (no timezone, no DST).
"""
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from typing import get_args, get_origin

SCHEMA = "demo-records/v1"
WEB_WIDTHS = (98, 95, 92, 87)
CHANGE_CODES = ("O", "S", "C")  # order change, shift split, continuation
REJECT_REASONS = ("BOND", "WARP", "MISALIGNMENT")
STOP_KINDS = ("Maintenance", "Operator", "PM")
STOP_PLACES = ("Upper knife", "Lower knife", "Shear knife", "Slitter/scorer",
               "Double backer", "Splicer 1", "Splicer 2", "Splicer 3", "Splicer 4",
               "Splicer 5", "Glue dams", "Stacker", "Single facer", "Steam system",
               "Power")
STOP_REASONS = ("Knife / slitter jam", "Missed splice", "Raw material (roll)",
                "Stacker", "Order change", "Glue / starch", "Steam / moisture",
                "Power outage", "Other")
CREWS = ("Crew A", "Crew B", "Crew C")
SCHEDULED_SHIFT_MINUTES = 480


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
class CustomerBlock:
    """One order on one knife position inside a dry-end setup.

    order_number: sales-order style id, unique per order; the same order may
        appear on consecutive setups (continuation) but once per setup.
    part_number: part / item id carried by the order.
    sheet_width_in, sheet_length_in: ordered sheet size; length is the cutoff.
    number_of_outs: sheets across the web for this order.
    cutoff_allocation: knife position 1..3 (position index + 1).
    nominal_cuts: planned cuts on this knife for this setup record.
    actual_cuts: counted cuts; within +-10% of nominal (over/under-run window).
    hqm_waste_sqft: reject area attributed at the cutoff knife (misalignment).
    asm_waste_sqft: reject area attributed at the stacker (bond, warp).
    stack_height_sheets: sheets per stack delivered by the stacker.
    """
    order_number: str
    part_number: str
    sheet_width_in: int
    sheet_length_in: int
    number_of_outs: int
    cutoff_allocation: int
    nominal_cuts: int
    actual_cuts: int
    hqm_waste_sqft: float
    asm_waste_sqft: float
    stack_height_sheets: int


@dataclass
class RejectEntry:
    """Non-protocol dry-end reject line: counted sheets of one customer block.

    reason: BOND, WARP or MISALIGNMENT (a reported observation, not a cause).
    cutoff_allocation: the knife position whose sheets were rejected.
    sheets: individual sheets; sqft = sheets x width x length / 144.
    """
    reason: str
    cutoff_allocation: int
    sheets: int
    sqft: float


@dataclass
class DryEndSetupRecord:
    """One dry-end setup ("P04P"-shaped). A setup is closed when either knife's
    order combination changes (O / C) or at a shift boundary (S).

    setup_id: 20-character machine-style id (machine code + start stamp + seq).
    machine_id: the corrugator the record belongs to.
    start_date/start_time, end_date/end_time: YYYYMMDD / HHMMSS, demo-local.
    lineal_ft: all web travel through the machine during the record, order
        sheets and shear chops alike. Gross area is lineal_ft x web / 12.
    avg_speed_fpm: lineal_ft / elapsed minutes, elapsed INCLUDING stops.
    web_width_in: paper (web) width, one of 98, 95, 92, 87.
    quality_id: board grade (fixture.GRADES key).
    total_nominal_ft: planned order travel = nominal_cuts x cutoff / 12 of the
        guide customer (cutoff_allocation 1); knives share travel, so this is
        a per-setup figure, never a sum over customers.
    total_produced_sqft: gross square feet, trim and chops included.
    trim_sqft: side-trim area = order travel x trim inches / 12.
    trim_pct: 100 x trim_sqft / total_produced_sqft.
    change_code: O = order change started the record, S = shift split (this
        record continues split_from_setup_id across a shift boundary),
        C = continuation (knife 1's order continued, another knife changed).
    stop_time_sec, number_of_stops: the stops that fell inside the record.
    shift_number: 1, 2 or 3. production_date: YYYYMMDD of the production day
        (07:00 start) that the shift belongs to.
    chop_sqft: full-web shear chops, 34 inches each, part of gross.
    kqf_sqft: demo definition - total dry-end reject area (sum of rejects).
    customers: 1..3 CustomerBlock, one order per knife position.
    rejects: non-protocol reject lines per reason and knife position.
    wet_end_id: the wet-end record that aggregates this setup.
    split_from_setup_id: for change_code S, the record this one continues.
    """
    setup_id: str
    machine_id: str
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    lineal_ft: float
    avg_speed_fpm: float
    web_width_in: int
    quality_id: str
    total_nominal_ft: float
    total_produced_sqft: float
    trim_sqft: float
    trim_pct: float
    change_code: str
    stop_time_sec: int
    number_of_stops: int
    shift_number: int
    chop_sqft: float
    kqf_sqft: float
    customers: list[CustomerBlock]
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
        """Travel that reached the cutoff knives as order sheets."""
        return self.lineal_ft - self.chop_ft

    @property
    def trim_in(self):
        return self.web_width_in - sum(c.sheet_width_in * c.number_of_outs for c in self.customers)

    @property
    def running_speed_fpm(self):
        """Speed while the web was moving: lineal over elapsed minus stops."""
        return self.lineal_ft / ((self.elapsed_seconds - self.stop_time_sec) / 60)

    @property
    def reject_sqft(self):
        return sum(r.sqft for r in self.rejects)


@dataclass
class WetEndRecord:
    """One paper run ("P04G"-shaped): consecutive setups sharing web width and
    board grade. Totals are exact sums over setup_ids.

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
    """One 480-minute shift ("P04T"-shaped). Totals are exact sums over the
    setup records carrying this production_date and shift_number.

    shift_number: 1 (07:00), 2 (15:00), 3 (23:00). production_date: YYYYMMDD.
    group_name: crew label Crew A / B / C, rotating weekly; never a person.
    start_date/start_time, end_date/end_time, duration (always 080000).
    total_lineal_ft, total_produced_sqft, side_trim_sqft: sums of setups.
    chop_sqft, reject_sqft: sums of setups (shear and dry-end rejects).
    max_speed_fpm: highest running speed of its setups.
    avg_speed_fpm: total_lineal_ft / 480, downtime included.
    wet_end_ids: wet ends with at least one setup in the shift.
    scheduled_minutes: 480.
    bale_sqft: non-protocol scale reading of baled waste = trim + chops +
        rejects + a small unexplained remainder (0.1-0.4% of gross), so bale
        percent reads slightly above the sum of its parts.
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


@dataclass
class StopRecord:
    """One machine stop ("P04S"-shaped), always inside exactly one setup.

    stop_id: ST + start stamp. start_date/start_time, end_date/end_time.
    duration_sec: whole seconds = end - start.
    kind: Maintenance, Operator or PM (planned maintenance window).
    place: equipment location from STOP_PLACES.
    reason: one of the nine STOP_REASONS; a recorded symptom, not a cause.
    shift_number, production_date: the shift the stop fell in.
    setup_id: the dry-end setup record that contains the stop.
    user_info: synthetic operator note, labelled unverified.
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
    user_info: str

    @property
    def start(self):
        return unstamp(self.start_date, self.start_time)

    @property
    def end(self):
        return unstamp(self.end_date, self.end_time)


FAMILIES = {"dry_end_setups": DryEndSetupRecord, "wet_ends": WetEndRecord,
            "shifts": ShiftRecord, "stops": StopRecord}


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


def shift_window(production_date, shift_number):
    """(start, end) datetimes of a shift in the production day starting 07:00."""
    origin = datetime.strptime(production_date, "%Y%m%d") + timedelta(hours=7)
    start = origin + timedelta(hours=8 * (shift_number - 1))
    return start, start + timedelta(minutes=SCHEDULED_SHIFT_MINUTES)
