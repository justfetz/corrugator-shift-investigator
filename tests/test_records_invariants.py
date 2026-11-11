"""Physics and accounting invariants of the two-month synthetic record set (record contract v2, issue #100).

Every test walks every record of both months produced by the default seed. The
setup identities are the owner's (issue #100): the lanes' outs x sheet width
plus the trim make the web; a lane's travel is its cuts x sheet length; the
lanes of a setup end within one knife rotation of each other; the setup's
scheduled lineal is its longest lane; an order is complete when the sheets
scheduled over its setups reach its quantity; gross is web x lineal and trim is
trim inches x order travel. `lineal_ft` counts every foot of web, order sheets
and shear chops alike, so the cut checks are made on the order travel (lineal
minus the chop travel).
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import re
import pytest
from investigator import records as R
from investigator import stories
from investigator.fixture import GRADES
from investigator.synth import (CHOP_LENGTH_IN, CUSTOMER_COUNT, FLUTES, TAKE_UP, TRIM_MAX_IN, TRIM_MIN_IN, generate_days,
                                generate_months, knife_story_on, production_days, trim_target_pct, weekdays_between)

SRC = Path(__file__).resolve().parents[1] / "src" / "investigator"


@pytest.fixture(scope="module")
def data():
    return generate_months()


@pytest.fixture(scope="module")
def setups(data):
    return data["dry_end_setups"]


@pytest.fixture(scope="module")
def by_shift(data):
    groups = defaultdict(list)
    for setup in data["dry_end_setups"]:
        groups[(setup.production_date, setup.shift)].append(setup)
    return groups


def month_of(record):
    return record.production_date[:6]


def weekday(record):
    return date(int(record.production_date[:4]), int(record.production_date[4:6]),
                int(record.production_date[6:])).weekday()


def ratio(numerator, denominator):
    return 100 * numerator / denominator


def shift_boundaries(data):
    return {sh.end for sh in data["shifts"]}


def test_calendar_is_three_shifts_per_weekday_of_both_months(data):
    days = production_days((("2026", 8), ("2026", 9)))
    assert len(days) == 21 + 22 and all(day.weekday() < 5 for day in days)
    shifts = data["shifts"]
    assert len(shifts) == 3 * len(days)
    assert [(s.production_date, s.shift) for s in shifts] == [(d.strftime("%Y%m%d"), n) for d in days for n in (1, 2, 3)]
    for shift in shifts:
        start, end = R.shift_window(shift.production_date, shift.shift)
        assert (shift.start, shift.end) == (start, end)
        assert start.hour == {1: 7, 2: 15, 3: 23}[shift.shift]
        assert shift.duration == "080000" and shift.scheduled_minutes == 480
        assert shift.group_name in R.CREWS


def test_lanes_fit_the_web_less_the_trim_for_every_setup(setups):
    """Acceptance (issue #100): the lanes' outs x sheet width fit inside the web width minus the trim, at least
    the minimum trim of 1 inch a side and at most 7; the trim the record carries agrees, both as trim_pct of the
    web and as trim_sqft over the order travel."""
    for setup in setups:
        assert setup.web_width_in in R.WEB_WIDTHS and setup.quality_id in GRADES
        assert 1 <= len(setup.lanes) <= 3
        used = sum(lane.outs * lane.sheet_width_in for lane in setup.lanes)
        assert used <= setup.web_width_in - TRIM_MIN_IN, setup.setup_id
        assert setup.trim_pct * setup.web_width_in / 100 == pytest.approx(setup.web_width_in - used, abs=0.01)
        assert setup.trim_sqft * 12 / setup.order_travel_ft == pytest.approx(setup.web_width_in - used, abs=0.05)
        assert used + setup.trim_in == setup.web_width_in
        assert TRIM_MIN_IN <= setup.trim_in <= TRIM_MAX_IN and 1 <= setup.trim_in / 2 <= 7   # per side, web centered
        for number, lane in enumerate(setup.lanes, 1):
            assert lane.lane == number and lane.cutoff_knife == number
            assert 12 <= lane.sheet_width_in <= 60 and 20 <= lane.sheet_length_in <= 92
            assert lane.outs >= 1 and lane.stack_height_sheets > 0


def test_setup_identities(data, setups):
    """The owner's six identities (issue #100), on every setup of both months."""
    boundaries = shift_boundaries(data)
    clamped = 0
    for s in setups:
        # 1. used + trim = web; trim_pct = trim / web
        used = sum(l.outs * l.sheet_width_in for l in s.lanes)
        assert s.web_width_in - used == s.trim_in and s.trim_pct == pytest.approx(round(100 * s.trim_in / s.web_width_in, 2))
        # 2. lane travel = cuts x length / 12, sheets = cuts x outs
        for l in s.lanes:
            assert l.nominal_lineal_ft == l.nominal_cuts * l.sheet_length_in / 12 and l.nominal_sheets == l.nominal_cuts * l.outs
        # 3. the lanes end within one knife rotation of each other: exactly on the plan, and on the
        #    actual cuts each lane is within one of its own sheet lengths of the order travel
        longest = max(l.sheet_length_in for l in s.lanes) / 12
        nominal = [l.nominal_lineal_ft for l in s.lanes]
        assert max(nominal) - min(nominal) < longest, s.setup_id
        for l in s.lanes:
            behind = s.order_travel_ft - l.actual_lineal_ft
            if not -1e-6 <= behind < l.sheet_length_in / 12 + 0.01:
                clamped += 1   # the actual cuts were held inside the +-10% window of the plan
        # 4. scheduled lineal = the longest lane's plan; actual lineal = order travel + the chops
        assert s.scheduled_lineal_ft == pytest.approx(max(nominal), abs=0.06)
        assert s.lineal_ft == pytest.approx(s.order_travel_ft + s.shear_ft, abs=1e-6)
        # 6. gross = web x lineal; trim = trim inches x order travel
        assert s.gross_sqft == pytest.approx(s.lineal_ft * s.web_width_in / 12, abs=0.06)
        assert s.trim_sqft == pytest.approx(s.order_travel_ft * s.trim_in / 12, abs=0.06)
    lanes = sum(len(s.lanes) for s in setups)
    assert clamped <= 0.001 * lanes, (clamped, lanes)
    # 5. an order is complete when the sheets scheduled over its setups reach its quantity, unless its
    #    last setup was cut at a shift end (the day closes at 07:00, or a sliver under a minute was dropped)
    sheets, last, quantity = defaultdict(int), {}, {}
    for s in setups:
        for l in s.lanes:
            sheets[l.order_number] += l.nominal_sheets
            last[l.order_number] = s
            quantity[l.order_number] = l.order_quantity
    short = [o for o in sheets if sheets[o] < quantity[o]]
    assert all(last[o].end in boundaries for o in short), [o for o in short if last[o].end not in boundaries][:5]
    assert len(short) <= 0.05 * len(sheets)
    assert sum(sheets[o] >= quantity[o] for o in sheets) / len(sheets) > 0.95


def test_lineal_consistency_and_overrun_window(setups):
    within_two_percent = total = 0
    for setup in setups:
        assert setup.order_travel_ft > 0
        for lane in setup.lanes:
            assert lane.nominal_cuts >= 1
            assert 0.9 * lane.nominal_cuts <= lane.actual_cuts <= 1.1 * lane.nominal_cuts
            within_two_percent += abs(lane.actual_cuts - lane.nominal_cuts) <= 0.02 * lane.nominal_cuts
            total += 1
    assert within_two_percent / total > 0.75


def test_setup_area_accounting(setups):
    taxonomy = R.reject_categories()
    for setup in setups:
        width = setup.web_width_in
        chops = setup.shear_sqft * 144 / (CHOP_LENGTH_IN * width)
        assert chops == pytest.approx(round(chops), abs=0.01) and chops >= 0
        assert setup.swing_knife_sqft is None   # CORR-1 has no swing knife: null, never zero
        waste = setup.trim_sqft + setup.shear_sqft + setup.reject_sqft + setup.cutoff_waste_sqft + setup.stacker_waste_sqft
        assert waste < setup.gross_sqft
        assert setup.reject_sqft == pytest.approx(sum(e.sqft for e in setup.rejects if R.counted_as(e.category) == "dry_end"), abs=0.06)
        assert setup.cutoff_waste_sqft == pytest.approx(sum(l.cutoff_waste_sqft for l in setup.lanes), abs=0.06)
        assert setup.stacker_waste_sqft == pytest.approx(sum(l.stacker_waste_sqft for l in setup.lanes), abs=0.06)
        lost = defaultdict(int)
        for entry in setup.rejects:
            lane = setup.lanes[entry.lane - 1]
            assert entry.category in R.GENERATED_REJECT_CATEGORIES and entry.sheets > 0
            reason = R.reject_reasons(entry.category)[entry.reason]
            assert (entry.liner in reason["liner"]) if reason["liner"] else entry.liner is None
            assert (entry.side in reason["side"]) if reason["side"] else entry.side is None
            if reason.get("doublewall_only"):
                assert len(FLUTES[setup.quality_id]) == 2
            assert entry.sqft == pytest.approx(entry.sheets * lane.sheet_sqft, abs=0.06)
            lost[entry.lane] += entry.sheets
        for lane in setup.lanes:
            assert lane.cutoff_waste_sqft == pytest.approx(lane.cutoff_waste_boards * lane.sheet_sqft, abs=0.06)
            assert lane.stacker_waste_sqft == pytest.approx(lane.stacker_waste_boards * lane.sheet_sqft, abs=0.06)
            assert lost[lane.lane] + lane.cutoff_waste_boards + lane.stacker_waste_boards <= lane.actual_sheets
            assert lane.customer_id == R.INTERNAL_CUSTOMER or re.fullmatch(r"CU\d{3}", lane.customer_id)
            fanfold = (lane.folds, lane.sheets_per_pallet, lane.pallets, lane.perforation_length_in)
            if setup.is_fanfold:
                assert None not in fanfold and lane.folds == lane.actual_cuts and lane.perforation_length_in == lane.sheet_length_in
            else:
                assert fanfold == (None, None, None, None)
        assert setup.avg_fpm == pytest.approx(setup.lineal_ft / (setup.elapsed_seconds / 60), abs=0.06)
        assert setup.run_seconds == setup.elapsed_seconds - setup.stop_seconds
        assert setup.sheet_type in R.SHEET_TYPES
        assert not setup.filler or setup.lanes[-1].customer_id == R.INTERNAL_CUSTOMER   # a filler lane is plant use
        if setup.sheet_type != "product":
            assert setup.lanes[0].customer_id == R.INTERNAL_CUSTOMER
        assert len(setup.setup_id) == 20 and setup.setup_id.startswith("CORR" + setup.start_date + setup.start_time)
    assert set(taxonomy) >= set(R.GENERATED_REJECT_CATEGORIES)
    assert {e.category for s in setups for e in s.rejects} == set(R.GENERATED_REJECT_CATEGORIES)
    assert any(s.filler for s in setups) and any(s.is_fanfold for s in setups)
    assert {s.sheet_type for s in setups} == set(R.SHEET_TYPES)


def test_one_order_per_lane_and_change_codes(setups):
    """O: no order shared with the previous setup. C: an order carried from the previous setup (possibly
    on another paper width of the same grade, at other outs). S: the shift split, continuing split_from."""
    assert len({s.setup_id for s in setups}) == len(setups)
    last_seen = {}
    for index, setup in enumerate(setups):
        orders = [l.order_number for l in setup.lanes]
        assert len(set(orders)) == len(orders)           # one order per lane
        assert setup.change_code in R.CHANGE_CODES
        for order in orders:                              # orders are contiguous in time
            assert last_seen.get(order, index - 1) == index - 1
            last_seen[order] = index
        previous = setups[index - 1] if index else None
        shared = set(orders) & {l.order_number for l in previous.lanes} if previous else set()
        if setup.change_code == "O":
            assert not shared and setup.split_from is None
        elif setup.change_code == "C":
            assert shared and setup.split_from is None
            assert setup.quality_id == previous.quality_id
        else:
            assert setup.split_from == previous.setup_id
            assert setup.wet_end_id == previous.wet_end_id
            assert [(l.order_number, l.sheet_width_in, l.sheet_length_in, l.outs) for l in setup.lanes] == \
                [(l.order_number, l.sheet_width_in, l.sheet_length_in, l.outs) for l in previous.lanes]
            assert previous.end == setup.start and setup.start == R.shift_window(setup.production_date, setup.shift)[0]
            assert (previous.production_date, previous.shift) != (setup.production_date, setup.shift)
    assert {s.change_code for s in setups} == set(R.CHANGE_CODES)


def test_wet_end_is_the_exact_aggregate_of_its_setups(data, setups):
    index = {s.setup_id: s for s in setups}
    order = {s.setup_id: i for i, s in enumerate(setups)}
    covered = []
    for wet in data["wet_ends"]:
        members = [index[i] for i in wet.setup_ids]
        covered.extend(wet.setup_ids)
        positions = [order[i] for i in wet.setup_ids]
        assert positions == list(range(positions[0], positions[0] + len(positions)))
        assert all(m.wet_end_id == wet.wet_end_id for m in members)
        assert {m.web_width_in for m in members} == {wet.web_width_in}
        assert {m.quality_id for m in members} == {wet.quality_id}
        slack = 0.06 * len(members)
        assert wet.lineal_ft == pytest.approx(sum(m.lineal_ft for m in members), abs=slack)
        assert wet.gross_sqft == pytest.approx(sum(m.gross_sqft for m in members), abs=slack)
        assert wet.trim_sqft == pytest.approx(sum(m.trim_sqft for m in members), abs=slack)
        assert wet.start == members[0].start and wet.end == members[-1].end
        assert R.hhmmss_seconds(wet.duration) == int((wet.end - wet.start).total_seconds())
        shifts = []
        for m in members:
            if m.shift not in shifts:
                shifts.append(m.shift)
        assert wet.shifts == shifts and 1 <= len(shifts) <= 2
        assert wet.max_fpm == pytest.approx(max(m.running_fpm for m in members), abs=0.06)
        assert wet.avg_fpm == pytest.approx(wet.lineal_ft / ((wet.end - wet.start).total_seconds() / 60), abs=0.06)
        assert wet.production_date == members[0].production_date
        if len(shifts) == 2:   # the boundary falls on a member start: an S split or an exact end
            boundary = R.shift_window(members[-1].production_date, members[-1].shift)[0]
            assert any(m.start == boundary for m in members)
    assert sorted(covered) == sorted(index) and len(covered) == len(index)
    assert any(len(w.shifts) == 2 for w in data["wet_ends"])


def test_shift_is_the_exact_aggregate_of_its_setups(data, by_shift):
    for shift in data["shifts"]:
        members = by_shift[(shift.production_date, shift.shift)]
        slack = 0.06 * len(members)
        for name in ("lineal_ft", "gross_sqft", "trim_sqft", "shear_sqft", "reject_sqft", "cutoff_waste_sqft", "stacker_waste_sqft"):
            assert getattr(shift, name) == pytest.approx(sum(getattr(m, name) for m in members), abs=slack), name
        assert shift.avg_fpm == pytest.approx(shift.lineal_ft / 480, abs=0.06)
        assert shift.max_fpm == pytest.approx(max(m.running_fpm for m in members), abs=0.06)
        wet_ends = []
        for m in members:
            if m.wet_end_id not in wet_ends:
                wet_ends.append(m.wet_end_id)
        assert shift.wet_end_ids == wet_ends
        parts = shift.trim_sqft + shift.shear_sqft + shift.reject_sqft + shift.cutoff_waste_sqft + shift.stacker_waste_sqft
        remainder = ratio(shift.bale_sqft - parts, shift.gross_sqft)
        assert 0.1 <= remainder <= 0.4


def test_time_partition_and_the_downtime_log(data, by_shift):
    """Setups tile the shifts; every stop row sits inside its setup, and for every setup the sum of its
    stop rows' seconds equals its stop_seconds and their count its stops (owner, issue #100)."""
    for shift in data["shifts"]:
        members = by_shift[(shift.production_date, shift.shift)]
        cursor = shift.start
        for setup in members:
            assert setup.start == cursor and setup.end > setup.start
            cursor = setup.end
        assert cursor == shift.end
    index = {s.setup_id: s for s in data["dry_end_setups"]}
    inside = defaultdict(list)
    previous_end = None
    for stop in data["stops"]:
        setup = index[stop.setup_id]
        assert stop.seconds == int((stop.end - stop.start).total_seconds()) > 0
        assert setup.start <= stop.start and stop.end <= setup.end
        assert (stop.production_date, stop.shift) == (setup.production_date, setup.shift)
        assert stop.kind in R.STOP_KINDS and stop.place in R.STOP_PLACES and stop.reason in R.STOP_REASONS
        assert stop.comment == "" or stop.comment.endswith(R.COMMENT_LABEL)
        if stop.kind == "PM" or stop.reason == "Power outage":
            assert stop.comment
        assert previous_end is None or stop.start >= previous_end    # never overlap
        previous_end = stop.end
        inside[stop.setup_id].append(stop)
    for setup in data["dry_end_setups"]:
        stops = inside.get(setup.setup_id, [])
        assert setup.stops == len(stops)
        assert setup.stop_seconds == sum(s.seconds for s in stops)
        assert setup.run_seconds >= 60
    empty = sum(not s.comment for s in data["stops"]) / len(data["stops"])
    assert 0.25 <= empty <= 0.5, empty   # a realistic share of the log is left without a comment


def test_downtime_budget_per_month(data):
    scheduled = defaultdict(int)
    for shift in data["shifts"]:
        scheduled[month_of(shift)] += shift.scheduled_minutes
    minutes = defaultdict(float)
    for stop in data["stops"]:
        minutes[(month_of(stop), stop.kind)] += stop.seconds / 60
    for month in ("202608", "202609"):
        planned = ratio(minutes[(month, "Maintenance")] + minutes[(month, "PM")], scheduled[month])
        assert 2.0 <= planned <= 3.0, (month, planned)
        operator = ratio(minutes[(month, "Operator")], scheduled[month])
        assert 1.0 <= operator <= 3.0, (month, operator)
    pm = [s for s in data["stops"] if s.kind == "PM"]
    thursdays = [d for d in production_days((("2026", 8), ("2026", 9))) if d.weekday() == 3]
    assert [s.production_date for s in pm] == [d.strftime("%Y%m%d") for d in thursdays]
    assert all(s.shift == 1 and 120 <= s.seconds / 60 <= 240 for s in pm)


def test_waste_bands_per_month(data):
    gross, trim, chop, boards, rejects = (defaultdict(float) for _ in range(5))
    for setup in data["dry_end_setups"]:
        month = month_of(setup)
        gross[month] += setup.gross_sqft
        trim[month] += setup.trim_sqft
        chop[month] += setup.shear_sqft
        boards[month] += setup.cutoff_waste_sqft + setup.stacker_waste_sqft
        for entry in setup.rejects:
            rejects[(month, entry.category)] += entry.sqft
    for month in ("202608", "202609"):
        assert 2.5 <= ratio(trim[month], gross[month]) <= 3.5
        assert 0.4 <= ratio(chop[month], gross[month]) <= 1.0
        assert 0.1 <= ratio(boards[month], gross[month]) <= 0.4   # waste boards at the knives and stackers
        bond, warp, mis, cracking = (ratio(rejects[(month, c)], gross[month]) for c in R.GENERATED_REJECT_CATEGORIES)
        assert 0.9 <= bond + warp + mis + cracking <= 1.3   # "dry-end waste should be about 1%"
        assert 0.3 <= bond <= 0.5 and 0.25 <= warp <= 0.55 and 0.15 <= mis <= 0.25 and 0.07 <= cracking <= 0.13
        # Reported reasons follow the stories' mix: up warp leads warp, delamination leads bond.
        by_reason = defaultdict(float)
        for setup in data["dry_end_setups"]:
            if month_of(setup) == month:
                for entry in setup.rejects:
                    by_reason[(entry.category, entry.reason)] += entry.sqft
        warps = [by_reason[("warp", r)] for r in ("Up warp", "Down warp", "Twist warp", "S warp", "End to end warp")]
        assert warps == sorted(warps, reverse=True) and warps[-1] > 0
        bonds = {r: v for (c, r), v in by_reason.items() if c == "bond"}
        assert max(bonds, key=bonds.get) == "Delamination"
    for shift in data["shifts"]:
        assert shift.bale_sqft > shift.trim_sqft + shift.shear_sqft + shift.reject_sqft


def test_output_per_production_day(data):
    per_day = defaultdict(float)
    for shift in data["shifts"]:
        per_day[shift.production_date] += shift.gross_sqft
        # about 2.4M a shift at a 98-inch average web; a fast crew on 105-110 in paper with few stops reaches 3.1M (issue #100:
        # paper widths are 90-110 in, up to 110 on the 110-inch machine; the cap was 3.0M with widths up to 106)
        assert 1_200_000 < shift.gross_sqft < 3_200_000
    assert all(6_000_000 <= gross <= 8_500_000 for gross in per_day.values()), (min(per_day.values()), max(per_day.values()))
    setups_per_day = defaultdict(int)
    for setup in data["dry_end_setups"]:
        setups_per_day[setup.production_date] += 1
    assert 90 <= min(setups_per_day.values()) and max(setups_per_day.values()) <= 160   # about 7,000 lineal ft per setup
    feet = sum(s.lineal_ft for s in data["dry_end_setups"])
    assert 6_500 <= feet / len(data["dry_end_setups"]) <= 7_800
    assert 18_000 <= feet / len(data["wet_ends"]) <= 23_000   # about 20,000 lineal ft per paper run
    gross = sum(s.gross_sqft for s in data["dry_end_setups"])
    assert 97.0 <= gross / feet * 12 <= 99.5   # a 98-inch average web on a 110-inch machine
    weeks = defaultdict(float)
    for day_text, total in per_day.items():
        weeks[date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:])).isocalendar()[1]] += total
    full = [weeks[w] for w in sorted(weeks)][:-1]   # the last week of September is short
    moves = [abs(b - a) / a for a, b in zip(full, full[1:])]
    assert max(moves) >= 0.04   # week-to-week volume moves a few percent, not a flat line


def test_customers_are_a_dimension_every_lane_points_at(data):
    customers = {c.customer_id: c for c in data["customers"]}
    assert len(customers) == CUSTOMER_COUNT + 1 and R.INTERNAL_CUSTOMER in customers
    assert set(R.to_dict(data["customers"][0])) == {"customer_id", "name", "segment", "destination", "ship_to"}   # no measures
    assert all(re.fullmatch(r"Customer \d{3} \(.+\)", c.name) for cid, c in customers.items() if cid != R.INTERNAL_CUSTOMER)
    used = {l.customer_id for s in data["dry_end_setups"] for l in s.lanes}
    assert used <= set(customers) and len(used) > CUSTOMER_COUNT // 2


def test_roll_stock_feeds_every_paper_run_with_peel_and_core(data):
    rolls = defaultdict(list)
    for roll in data["rolls"]:
        rolls[roll.wet_end_id].append(roll)
    assert set(rolls) == {w.wet_end_id for w in data["wet_ends"]}
    for wet in data["wet_ends"]:
        flutes = FLUTES[wet.quality_id]
        positions = R.ROLL_POSITIONS_SINGLE_WALL if len(flutes) == 1 else R.ROLL_POSITIONS_DOUBLE_WALL
        mine = rolls[wet.wet_end_id]
        assert {r.position for r in mine} == set(positions)
        for position in positions:
            fed = [r for r in mine if r.position == position]
            flute = flutes[0] if position.startswith("SF1") else flutes[-1]
            expected = wet.lineal_ft * (TAKE_UP[flute] if position.endswith("medium") else 1.0)
            assert sum(r.lineal_ft for r in fed) == pytest.approx(expected, abs=0.1 * len(fed))
            assert [r.ended_at for r in fed] == ["splice"] * (len(fed) - 1) + ["paper change"]
        for r in mine:
            assert r.width_in == wet.web_width_in and r.production_date == wet.production_date
            assert r.consumed_sqft == pytest.approx(r.lineal_ft * r.width_in / 12, abs=0.06)
            assert r.peel_sqft > 0 and (r.core_sqft > 0) == (r.ended_at == "splice")
    consumed = sum(r.consumed_sqft for r in data["rolls"])
    assert 0.3 <= ratio(sum(r.peel_sqft for r in data["rolls"]), consumed) <= 0.7      # peel ~0.5% (issue #101)
    assert 0.05 <= ratio(sum(r.core_sqft for r in data["rolls"]), consumed) <= 0.2     # core ~0.1%


def test_reject_taxonomy_is_data_category_reason_position():
    taxonomy = R.reject_taxonomy()
    assert "owner" in taxonomy["source"] and "2026-11-11" in taxonomy["source"]
    assert taxonomy["positions"] == {"liner": ["top", "bottom", "medium", "DB", "SF"], "side": ["drive", "operator", "middle"]}
    categories = R.reject_categories()
    assert list(categories) == ["bond", "warp", "cracking", "misalignment", "handling", "paper", "changeover", "print", "planning"]
    for key, category in categories.items():
        assert category["counted_as"] in ("dry_end", "changeover", "excluded")
        assert category["controllable"] in ("yes", "partly", "no", "unstated")
        names = [r["reason"] for r in category["reasons"]]
        assert len(names) == len(set(names)) and names
        for reason in category["reasons"]:
            assert set(reason["liner"]) <= set(R.LINERS) and set(reason["side"]) <= set(R.SIDES)
            # positions are attributes, never reasons: no reason is named after a liner or a side
            assert not re.fullmatch(r"(top|bottom|DB|SF) liner|(drive|operator) side", reason["reason"], re.I)
    assert categories["changeover"]["counted_as"] == "changeover" and categories["planning"]["counted_as"] == "excluded"
    assert categories["paper"]["controllable"] == "partly"
    assert set(R.GENERATED_REJECT_CATEGORIES) <= set(R.DRY_END_CATEGORIES)
    for category in R.GENERATED_REJECT_CATEGORIES:   # the generator's mixes name only taxonomy reasons
        assert set(stories.WEEKEND_WARP["reason_weights"][category]) <= set(R.reject_reasons(category))


def test_trim_creep_ends_on_31_october_and_trim_returns_to_its_pre_story_level():
    """Owner, PR #91: the slitter rebuild's trim creep (0.1 point a week from the 20 August rebuild)
    ends on 31 October 2026. From 1 November the crews aim at the pre-story target again, so a window
    past October does not keep growing."""
    rebuild = stories.SLITTER_REBUILD
    assert rebuild["trim_creep_end"] == date(2026, 10, 31)
    baseline, creep = rebuild["baseline_trim_pct"], rebuild["trim_creep_pp_per_week"]
    assert trim_target_pct(date(2026, 8, 19)) == trim_target_pct(rebuild["pm_date"]) == baseline
    assert trim_target_pct(date(2026, 10, 30)) == pytest.approx(baseline + creep * 71 / 7)   # the creep's last production day
    assert trim_target_pct(date(2026, 10, 31)) == pytest.approx(baseline + creep * 72 / 7)
    assert all(trim_target_pct(day) == baseline for day in (date(2026, 11, 1), date(2026, 11, 2), date(2027, 3, 1), date(2027, 7, 30)))

    def trim_pct(first, last):   # side trim as % of gross, from the generated records
        setups = generate_days(weekdays_between(first, last))["dry_end_setups"]
        return ratio(sum(s.trim_sqft for s in setups), sum(s.gross_sqft for s in setups))
    before = trim_pct(date(2026, 7, 20), date(2026, 8, 14))          # four weeks before the rebuild (2.80%)
    late_october = trim_pct(date(2026, 10, 19), date(2026, 10, 30))  # the creep's last two weeks (3.74%)
    november = trim_pct(date(2026, 11, 2), date(2026, 11, 27))       # the four weeks after it ends (2.83%)
    next_july = trim_pct(date(2027, 7, 5), date(2027, 7, 16))        # far past the story (2.78%)
    assert late_october - before >= 0.6, (before, late_october)
    assert abs(november - before) <= 0.15 and abs(next_july - before) <= 0.15, (before, november, next_july)


def test_knife_slitter_jam_effect_ends_with_the_trim_creep_on_31_october():
    """Issue #100 (carried over from #91's review): the rebuild's knife / slitter jam x3 ends on
    31 October 2026 together with the trim creep; from November the jams run at their pre-rebuild rate
    and place mix again."""
    rebuild = stories.SLITTER_REBUILD
    assert rebuild["knife_slitter_effect_end"] == rebuild["trim_creep_end"] == date(2026, 10, 31)
    assert not knife_story_on(rebuild["pm_date"]) and knife_story_on(date(2026, 8, 21)) and knife_story_on(date(2026, 10, 30))
    assert not knife_story_on(date(2026, 11, 2)) and not knife_story_on(date(2027, 3, 1))

    def jams(first, last):   # knife / slitter jams per shift and the slitter's share of them
        data = generate_days(weekdays_between(first, last))
        mine = [s for s in data["stops"] if s.reason == "Knife / slitter jam"]
        return len(mine) / len(data["shifts"]), sum(s.place == rebuild["place"] for s in mine) / len(mine)
    before, during, after = jams(date(2026, 6, 1), date(2026, 8, 14)), jams(date(2026, 9, 1), date(2026, 10, 30)), \
        jams(date(2026, 11, 2), date(2027, 1, 15))
    assert during[0] > 1.5 * before[0] and during[1] > 0.35
    assert after[0] < 1.3 * before[0] and after[1] < 0.3, (before, during, after)


def test_story_signals_are_visible_in_the_records(data):
    rebuild, splice, warp = stories.SLITTER_REBUILD, stories.SPLICER3_MISSED_SPLICE, stories.WEEKEND_WARP
    rebuild_text = rebuild["pm_date"].strftime("%Y%m%d")
    pm = [s for s in data["stops"] if s.kind == "PM" and s.production_date == rebuild_text]
    assert len(pm) == 1 and pm[0].place == rebuild["place"] and pm[0].seconds == rebuild["pm_minutes"] * 60
    # Story 1: trim creeps up and knife/slitter stops rise after the rebuild.
    before = [s for s in data["dry_end_setups"] if s.production_date < rebuild_text]
    late = [s for s in data["dry_end_setups"] if s.production_date >= "20260921"]
    trim_before = ratio(sum(s.trim_sqft for s in before), sum(s.gross_sqft for s in before))
    trim_late = ratio(sum(s.trim_sqft for s in late), sum(s.gross_sqft for s in late))
    assert 0.35 <= trim_late - trim_before <= 0.8
    shifts_before = sum(s.production_date < rebuild_text for s in data["shifts"])
    jams = [s for s in data["stops"] if s.reason == "Knife / slitter jam"]
    rate_before = sum(s.production_date < rebuild_text for s in jams) / shifts_before
    rate_after = sum(s.production_date > rebuild_text for s in jams) / (len(data["shifts"]) - shifts_before - 3)
    assert rate_after > 1.5 * rate_before
    slitter_after = [s for s in jams if s.production_date > rebuild_text]
    assert sum(s.place == rebuild["place"] for s in slitter_after) / len(slitter_after) > 0.35
    # Story 2: missed-splice minutes on Splicer 3 roughly triple in the story week.
    week = {(splice["start"] + timedelta(days=i)).strftime("%Y%m%d") for i in range(7)}
    missed = [s for s in data["stops"] if s.reason == "Missed splice"]
    story = [s for s in missed if s.production_date in week]
    rest = [s for s in missed if s.production_date not in week]
    story_days = len({s.production_date for s in data["shifts"] if s.production_date in week})
    rest_days = len({s.production_date for s in data["shifts"]}) - story_days
    per_day_story = sum(s.seconds for s in story) / 60 / story_days
    per_day_rest = sum(s.seconds for s in rest) / 60 / rest_days
    assert per_day_story > 2 * per_day_rest
    assert sum(s.seconds for s in story if s.place == splice["place"]) > 0.5 * sum(s.seconds for s in story)
    assert len(story) / story_days > 1.5 * len(rest) / rest_days
    chop = lambda rows: ratio(sum(s.shear_sqft for s in rows), sum(s.gross_sqft for s in rows))
    assert chop([s for s in data["dry_end_setups"] if s.production_date in week]) > \
        chop([s for s in data["dry_end_setups"] if s.production_date not in week])
    # Story 3: warp is higher on Mondays and in August.
    def warp_pct(rows):
        return ratio(sum(e.sqft for s in rows for e in s.rejects if e.category == "warp"),
                     sum(s.gross_sqft for s in rows))
    mondays = [s for s in data["dry_end_setups"] if weekday(s) == 0]
    others = [s for s in data["dry_end_setups"] if weekday(s) != 0]
    assert warp_pct(mondays) > 1.2 * warp_pct(others)
    august = [s for s in others if month_of(s) == "202608"]
    september = [s for s in others if month_of(s) == "202609"]
    assert warp_pct(august) > 1.2 * warp_pct(september)
    assert warp["warp_rate_by_month"][8] > warp["warp_rate_by_month"][9]


def test_answer_keys_are_not_imported_by_any_tool_layer():
    readers = []
    for path in sorted(SRC.glob("*.py")):
        if "stories" in path.read_text(encoding="utf-8") and path.name != "stories.py":
            readers.append(path.name)
    assert readers == ["synth.py"]
