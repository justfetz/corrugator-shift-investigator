"""Physics and accounting invariants of the two-month synthetic record set (record contract v2, issue #100).

Every test walks every record of both months produced by the default seed.
The lane checks compare cuts with ORDER travel (lineal_ft minus the shear-chop
travel): lineal_ft counts every foot of web, and ten 34-inch chops can never
fit inside one 20-inch cutoff, so gross = lineal x width / 12 is kept exact and
the knife identities are made on the travel that reached the knives.
tests/test_record_contract.py holds the scheduler's worked example and the
contract's own tests.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import pytest
from investigator import records as R
from investigator import stories
from investigator.fixture import GRADES
from investigator.synth import (CHOP_LENGTH_IN, FILLER_SIZES, MAX_OUTS, generate_days, generate_months, knife_slitter_story,
                                min_trim_in, narrowest_web, production_days, weekdays_between)

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
        groups[(setup.production_date, setup.shift_number)].append(setup)
    return groups


def month_of(record):
    return record.production_date[:6]


def weekday(record):
    return date(int(record.production_date[:4]), int(record.production_date[4:6]),
                int(record.production_date[6:])).weekday()


def ratio(numerator, denominator):
    return 100 * numerator / denominator


def test_calendar_is_three_shifts_per_weekday_of_both_months(data):
    days = production_days((("2026", 8), ("2026", 9)))
    assert len(days) == 21 + 22 and all(day.weekday() < 5 for day in days)
    shifts = data["shifts"]
    assert len(shifts) == 3 * len(days)
    assert [(s.production_date, s.shift_number) for s in shifts] == \
        [(d.strftime("%Y%m%d"), n) for d in days for n in (1, 2, 3)]
    for shift in shifts:
        start, end = R.shift_window(shift.production_date, shift.shift_number)
        assert (shift.start, shift.end) == (start, end)
        assert start.hour == {1: 7, 2: 15, 3: 23}[shift.shift_number]
        assert shift.duration == "080000" and shift.scheduled_minutes == 480
        assert shift.group_name in R.CREWS


def day_of(record):
    return date(int(record.production_date[:4]), int(record.production_date[4:6]), int(record.production_date[6:]))


def test_web_geometry_is_centered_on_the_narrowest_paper(setups):
    """Identity 1 (issue #100): used width = sum of outs x sheet width over the lanes; trim = web - used, split
    evenly both sides; trim_pct = trim / web; and the web is the narrowest paper at least as wide as the used
    width plus the day's minimum trim."""
    for setup in setups:
        assert setup.web_width_in in R.WEB_WIDTHS and setup.quality_id in GRADES
        assert 1 <= len(setup.lanes) <= 3 and [lane.lane for lane in setup.lanes] == list(range(1, len(setup.lanes) + 1))
        used = sum(lane.sheet_width_in * lane.number_of_outs for lane in setup.lanes)
        assert used + setup.trim_in == setup.web_width_in
        assert setup.trim_in >= min_trim_in(day_of(setup)) - 1e-9
        assert setup.web_width_in == narrowest_web(used, min_trim_in(day_of(setup)))
        assert setup.trim_pct == pytest.approx(setup.trim_in / setup.web_width_in * 100, abs=0.006)
        for lane in setup.lanes:
            assert lane.cutoff_allocation == lane.lane and lane.cutoff_allocation in R.CUTOFF_KNIVES
            assert (lane.sheet_width_in * 8).is_integer()                      # widths in eighths of an inch
            if lane.filler:
                assert (int(lane.sheet_width_in), lane.sheet_length_in) in FILLER_SIZES
            else:
                assert 12 <= lane.sheet_width_in <= 60 and 20 <= lane.sheet_length_in <= 92
            assert 1 <= lane.number_of_outs <= MAX_OUTS and lane.stack_height_sheets >= 0


def test_lanes_end_within_one_knife_rotation_and_the_longest_lane_is_the_setup(setups):
    """Identities 2-4: a lane's lineal is cuts x length / 12 and its sheets cuts x outs; the lanes of a setup end
    within one knife rotation (as scheduled and as run); the setup's order travel is its longest lane as run
    (lineal minus the shear chops) and its scheduled lineal the longest lane as scheduled. Actual cuts stay in
    the +-10% over/under-run window, most within +-2%."""
    within_two_percent = total = 0
    for setup in setups:
        rotation = max(lane.sheet_length_in for lane in setup.lanes) / 12
        actual = [lane.actual_ft for lane in setup.lanes]
        nominal = [lane.nominal_ft for lane in setup.lanes]
        assert max(actual) - min(actual) < rotation + 1e-9 and max(nominal) - min(nominal) < rotation + 1e-9
        assert setup.order_travel_ft == pytest.approx(max(actual), abs=0.1)
        assert setup.total_nominal_ft == pytest.approx(max(nominal), abs=0.06)
        for lane in setup.lanes:
            if lane.nominal_cuts >= 50:      # a short split part's cut counts round too coarsely for a percentage window
                assert 0.9 * lane.nominal_cuts - 1 <= lane.actual_cuts <= 1.1 * lane.nominal_cuts + 1
                within_two_percent += abs(lane.actual_cuts - lane.nominal_cuts) <= 0.02 * lane.nominal_cuts + 1
                total += 1
    assert within_two_percent / total > 0.75


def test_setup_area_accounting(setups):
    """Identity 6 and the waste lines: gross = web x lineal; trim = trim inches x order travel; every reject line
    is sheets x the lane's sheet size and sits in the taxonomy (category, reason, allowed positions); the lanes'
    cutoff-knife and stacker waste boards convert with the lane's sheet size; nothing exceeds the gross."""
    categories = R.categories()
    for setup in setups:
        width = setup.web_width_in
        assert setup.total_produced_sqft == pytest.approx(setup.lineal_ft * width / 12, abs=0.1)
        chops = setup.chop_sqft * 144 / (CHOP_LENGTH_IN * width)
        assert chops == pytest.approx(round(chops), abs=0.01) and chops >= 0
        assert setup.trim_sqft == pytest.approx(setup.order_travel_ft * setup.trim_in / 12, abs=0.1)
        assert setup.swing_knife_sqft is None            # the demo machine has no swing knife: null, never zero
        lanes = {lane.lane: lane for lane in setup.lanes}
        taken = defaultdict(int)
        for entry in setup.rejects:
            lane = lanes[entry.lane]
            category = categories[entry.category]
            reason = next(r for r in category["reasons"] if r["reason"] == entry.reason)
            assert reason.get("generated", True) and entry.sheets > 0
            assert entry.liner in (reason.get("liner") or [None]) and entry.side in (reason.get("side") or [None])
            assert entry.sqft == pytest.approx(entry.sheets * lane.sheet_sqft, abs=0.06)
            taken[entry.lane] += entry.sheets
        for lane in setup.lanes:
            assert taken[lane.lane] <= lane.actual_cuts * lane.number_of_outs
            assert lane.cutoff_waste_sqft == pytest.approx(lane.cutoff_waste_boards * lane.sheet_sqft, abs=0.06)
            assert lane.stacker_waste_sqft == pytest.approx(lane.stacker_waste_boards * lane.sheet_sqft, abs=0.06)
            if setup.change_code == "S":
                assert lane.cutoff_waste_boards == lane.stacker_waste_boards == 0
        waste = (setup.trim_sqft + setup.chop_sqft + setup.reject_sqft + setup.changeover_sqft + setup.admin_sqft
                 + setup.cutoff_waste_sqft + setup.stacker_waste_sqft)
        assert waste < setup.total_produced_sqft
        assert setup.avg_speed_fpm == pytest.approx(setup.lineal_ft / (setup.elapsed_seconds / 60), abs=0.06)
        assert setup.actual_run_sec == setup.elapsed_seconds - setup.stop_time_sec
        assert len(setup.setup_id) == 20 and setup.setup_id.startswith("CORR" + setup.start_date + setup.start_time)


def test_one_order_per_lane_and_change_codes(setups):
    """O starts a new lane combination; S continues the same lanes across a shift boundary. An order may
    leave the schedule and come back later in its day (the scheduler moved to another grade); it keeps its
    sheet, its grade and its customer."""
    assert len({s.setup_id for s in setups}) == len(setups)
    assert set(R.CHANGE_CODES) == {"O", "S"}
    seen = {}
    for index, setup in enumerate(setups):
        orders = [lane.order_number for lane in setup.lanes]
        assert len(set(orders)) == len(orders)            # one order per lane
        assert setup.change_code in R.CHANGE_CODES
        for lane in setup.lanes:
            key = (lane.sheet_width_in, lane.sheet_length_in, lane.customer_id, lane.order_quantity, setup.quality_id, setup.production_date)
            assert seen.setdefault(lane.order_number, key) == key
        previous = setups[index - 1] if index else None
        if setup.change_code == "O":
            assert setup.split_from_setup_id is None
        else:
            assert setup.split_from_setup_id == previous.setup_id
            assert setup.wet_end_id == previous.wet_end_id
            assert [(c.order_number, c.sheet_width_in, c.sheet_length_in, c.number_of_outs) for c in setup.lanes] == \
                [(c.order_number, c.sheet_width_in, c.sheet_length_in, c.number_of_outs) for c in previous.lanes]
            assert previous.end == setup.start and setup.start == R.shift_window(setup.production_date, setup.shift_number)[0]
            assert (previous.production_date, previous.shift_number) != (setup.production_date, setup.shift_number)
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
        assert wet.total_produced_sqft == pytest.approx(sum(m.total_produced_sqft for m in members), abs=slack)
        assert wet.side_trim_sqft == pytest.approx(sum(m.trim_sqft for m in members), abs=slack)
        assert wet.start == members[0].start and wet.end == members[-1].end
        assert R.hhmmss_seconds(wet.duration) == int((wet.end - wet.start).total_seconds())
        shifts = []
        for m in members:
            if m.shift_number not in shifts:
                shifts.append(m.shift_number)
        assert wet.shift_numbers == shifts and 1 <= len(shifts) <= 2
        assert wet.max_speed_fpm == pytest.approx(max(m.running_speed_fpm for m in members), abs=0.06)
        assert wet.avg_speed_fpm == pytest.approx(wet.lineal_ft / ((wet.end - wet.start).total_seconds() / 60), abs=0.06)
        assert wet.production_date == members[0].production_date
        if len(shifts) == 2:   # the boundary falls on a member start: an S split or an exact end
            boundary = R.shift_window(members[-1].production_date, members[-1].shift_number)[0]
            assert any(m.start == boundary for m in members)
    assert sorted(covered) == sorted(index) and len(covered) == len(index)
    assert any(len(w.shift_numbers) == 2 for w in data["wet_ends"])


def test_shift_is_the_exact_aggregate_of_its_setups(data, by_shift):
    for shift in data["shifts"]:
        members = by_shift[(shift.production_date, shift.shift_number)]
        slack = 0.06 * len(members)
        assert shift.total_lineal_ft == pytest.approx(sum(m.lineal_ft for m in members), abs=slack)
        assert shift.total_produced_sqft == pytest.approx(sum(m.total_produced_sqft for m in members), abs=slack)
        assert shift.side_trim_sqft == pytest.approx(sum(m.trim_sqft for m in members), abs=slack)
        assert shift.chop_sqft == pytest.approx(sum(m.chop_sqft for m in members), abs=slack)
        assert shift.reject_sqft == pytest.approx(sum(m.reject_sqft for m in members), abs=slack)
        assert shift.changeover_sqft == pytest.approx(sum(m.changeover_sqft for m in members), abs=slack)
        assert shift.admin_sqft == pytest.approx(sum(m.admin_sqft for m in members), abs=slack)
        assert shift.cutoff_waste_sqft == pytest.approx(sum(m.cutoff_waste_sqft for m in members), abs=slack)
        assert shift.stacker_waste_sqft == pytest.approx(sum(m.stacker_waste_sqft for m in members), abs=slack)
        assert shift.avg_speed_fpm == pytest.approx(shift.total_lineal_ft / 480, abs=0.06)
        assert shift.max_speed_fpm == pytest.approx(max(m.running_speed_fpm for m in members), abs=0.06)
        wet_ends = []
        for m in members:
            if m.wet_end_id not in wet_ends:
                wet_ends.append(m.wet_end_id)
        assert shift.wet_end_ids == wet_ends
        remainder = ratio(shift.bale_sqft - shift.accounted_sqft, shift.total_produced_sqft)
        assert 0.1 - 1e-6 <= remainder <= 0.4 + 1e-6


def test_time_partition_setups_tile_shifts_and_stops_sit_inside(data, by_shift):
    for shift in data["shifts"]:
        members = by_shift[(shift.production_date, shift.shift_number)]
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
        assert stop.duration_sec == int((stop.end - stop.start).total_seconds()) > 0
        assert setup.start <= stop.start and stop.end <= setup.end
        assert (stop.production_date, stop.shift_number) == (setup.production_date, setup.shift_number)
        assert stop.kind in R.STOP_KINDS and stop.place in R.STOP_PLACES and stop.reason in R.STOP_REASONS
        assert isinstance(stop.comment, str) and len(stop.comment) <= 200
        assert previous_end is None or stop.start >= previous_end    # never overlap
        previous_end = stop.end
        inside[stop.setup_id].append(stop)
    for setup in data["dry_end_setups"]:
        stops = inside.get(setup.setup_id, [])
        assert setup.number_of_stops == len(stops)
        assert setup.stop_time_sec == sum(s.duration_sec for s in stops)
        assert setup.elapsed_seconds - setup.stop_time_sec >= 60


def test_downtime_budget_per_month(data):
    scheduled = defaultdict(int)
    for shift in data["shifts"]:
        scheduled[month_of(shift)] += shift.scheduled_minutes
    minutes = defaultdict(float)
    for stop in data["stops"]:
        minutes[(month_of(stop), stop.kind)] += stop.duration_sec / 60
    for month in ("202608", "202609"):
        planned = ratio(minutes[(month, "Maintenance")] + minutes[(month, "PM")], scheduled[month])
        assert 2.0 <= planned <= 3.0, (month, planned)
        operator = ratio(minutes[(month, "Operator")], scheduled[month])
        assert 1.0 <= operator <= 3.0, (month, operator)
    pm = [s for s in data["stops"] if s.kind == "PM"]
    thursdays = [d for d in production_days((("2026", 8), ("2026", 9))) if d.weekday() == 3]
    assert [s.production_date for s in pm] == [d.strftime("%Y%m%d") for d in thursdays]
    assert all(s.shift_number == 1 and 120 <= s.duration_sec / 60 <= 240 for s in pm)


def test_waste_bands_per_month(data):
    gross, trim, chop, lines = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for setup in data["dry_end_setups"]:
        month = month_of(setup)
        gross[month] += setup.total_produced_sqft
        trim[month] += setup.trim_sqft
        chop[month] += setup.chop_sqft
        for entry in setup.rejects:
            lines[(month, entry.family)] += entry.sqft
        lines[(month, "cutoff")] += setup.cutoff_waste_sqft
        lines[(month, "stacker")] += setup.stacker_waste_sqft
    for month in ("202608", "202609"):
        assert 2.5 <= ratio(trim[month], gross[month]) <= 3.75    # schedule-driven: about 3%, September carries the story's creep
        assert 0.4 <= ratio(chop[month], gross[month]) <= 1.0
        bond, warp, mis, other = (ratio(lines[(month, f)], gross[month]) for f in R.DRY_END_FAMILIES)
        assert 0.9 <= bond + warp + mis + other <= 1.3   # "dry-end waste should be about 1%"
        assert 0.3 <= bond <= 0.5 and 0.25 <= warp <= 0.55 and 0.15 <= mis <= 0.25 and 0.06 <= other <= 0.14
        assert 0.08 <= ratio(lines[(month, "CHANGEOVER")], gross[month]) <= 0.25   # counted with shear
        assert 0.01 <= ratio(lines[(month, "ADMIN")], gross[month]) <= 0.08       # planning / admin, reported apart
        assert 0.1 <= ratio(lines[(month, "cutoff")] + lines[(month, "stacker")], gross[month]) <= 0.35
        # Reported reasons follow the stories' mix: up warp leads the warp category, delamination leads bond.
        by_reason = defaultdict(float)
        for setup in data["dry_end_setups"]:
            if month_of(setup) == month:
                for entry in setup.rejects:
                    by_reason[entry.reason] += entry.sqft
        assert by_reason["Up warp"] > by_reason["Down warp"] > by_reason["Twist warp"] > by_reason["S warp"] > by_reason["End-to-end warp"] > 0
        assert by_reason["Delamination"] > max(v for k, v in by_reason.items() if k in {r["reason"] for r in R.categories()["bond"]["reasons"]} - {"Delamination"})
    for shift in data["shifts"]:
        assert shift.bale_sqft > shift.accounted_sqft


def test_output_per_production_day(data):
    per_day = defaultdict(float)
    for shift in data["shifts"]:
        per_day[shift.production_date] += shift.total_produced_sqft
        assert 1_200_000 < shift.total_produced_sqft < 3_200_000   # about 2.5M a shift on a 101-104 in average web
    assert all(6_000_000 <= gross <= 8_500_000 for gross in per_day.values()), (min(per_day.values()), max(per_day.values()))
    setups_per_day = defaultdict(int)
    for setup in data["dry_end_setups"]:
        setups_per_day[setup.production_date] += 1
    assert 90 <= min(setups_per_day.values()) and max(setups_per_day.values()) <= 160   # about 7,000 lineal ft per setup
    feet = sum(s.lineal_ft for s in data["dry_end_setups"])
    assert 6_500 <= feet / len(data["dry_end_setups"]) <= 7_800
    assert 18_000 <= feet / len(data["wet_ends"]) <= 26_000   # about 20,000-23,000 lineal ft per paper run
    gross = sum(s.total_produced_sqft for s in data["dry_end_setups"])
    # The scheduler runs each setup on the narrowest paper that fits (issue #100): about 99 in of used width, the
    # owner's "98 in of throughput on a 110 in machine", on a web averaging 101-104 in.
    assert 101.0 <= gross / feet * 12 <= 104.0
    used = sum(s.order_travel_ft * s.used_width_in for s in data["dry_end_setups"]) / sum(s.order_travel_ft for s in data["dry_end_setups"])
    assert 97.0 <= used <= 101.0
    weeks = defaultdict(float)
    for day_text, total in per_day.items():
        weeks[date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:])).isocalendar()[1]] += total
    full = [weeks[w] for w in sorted(weeks)][:-1]   # the last week of September is short
    moves = [abs(b - a) / a for a, b in zip(full, full[1:])]
    assert max(moves) >= 0.04   # week-to-week volume moves a few percent, not a flat line


def test_trim_creep_ends_on_31_october_and_trim_returns_to_its_pre_story_level():
    """Owner, PR #91: the slitter rebuild's trim creep ends on 31 October 2026. Trim is schedule-driven now
    (issue #100), so the story is planted as the scheduler's minimum trim allowance: a tenth of an inch a week
    from the 20 August rebuild, back to the baseline from 1 November, so a window past October does not keep
    growing. The records show it as side trim of gross."""
    rebuild = stories.SLITTER_REBUILD
    assert rebuild["trim_creep_end"] == date(2026, 10, 31)
    baseline, creep = rebuild["baseline_min_trim_in"], rebuild["min_trim_creep_in_per_week"]
    assert min_trim_in(date(2026, 8, 19)) == min_trim_in(rebuild["pm_date"]) == baseline
    assert min_trim_in(date(2026, 10, 30)) == pytest.approx(baseline + creep * 71 / 7)   # the creep's last production day
    assert min_trim_in(date(2026, 10, 31)) == pytest.approx(baseline + creep * 72 / 7)
    assert all(min_trim_in(day) == baseline for day in (date(2025, 1, 1), date(2026, 11, 1), date(2026, 11, 2), date(2027, 3, 1), date(2027, 7, 30)))

    def trim_pct(first, last):   # side trim as % of gross, from the generated records
        setups = generate_days(weekdays_between(first, last))["dry_end_setups"]
        return ratio(sum(s.trim_sqft for s in setups), sum(s.total_produced_sqft for s in setups))
    before = trim_pct(date(2026, 7, 20), date(2026, 8, 14))          # four weeks before the rebuild (about 3.0%)
    late_october = trim_pct(date(2026, 10, 19), date(2026, 10, 30))  # the creep's last two weeks (about 4.0%)
    november = trim_pct(date(2026, 11, 2), date(2026, 11, 27))       # the four weeks after it ends (about 3.0%)
    next_july = trim_pct(date(2027, 7, 5), date(2027, 7, 16))        # far past the story
    assert late_october - before >= 0.6, (before, late_october)
    assert abs(november - before) <= 0.15 and abs(next_july - before) <= 0.15, (before, november, next_july)


def test_knife_slitter_jams_end_with_the_trim_creep_on_31_october():
    """Issue #100 (carried over from PR #91's review): the rebuild's knife / slitter jam effect (x3 minutes,
    half of them at the slitter/scorer) runs after the 20 August PM through 31 October, then ends with the
    trim creep: November's jam rate is back at the pre-rebuild level and the slitter's share with it."""
    rebuild = stories.SLITTER_REBUILD
    assert rebuild["knife_slitter_end"] == rebuild["trim_creep_end"] == date(2026, 10, 31)
    assert not knife_slitter_story(rebuild["pm_date"]) and knife_slitter_story(date(2026, 8, 21))
    assert knife_slitter_story(date(2026, 10, 30)) and not knife_slitter_story(date(2026, 11, 2))

    def jams(first, last):
        data = generate_days(weekdays_between(first, last))
        stops = [s for s in data["stops"] if s.reason == "Knife / slitter jam"]
        minutes = sum(s.duration_sec for s in stops) / 60
        return minutes / len(data["shifts"]), sum(s.place == rebuild["place"] for s in stops) / len(stops)
    before, before_share = jams(date(2026, 6, 1), date(2026, 8, 14))
    during, during_share = jams(date(2026, 9, 14), date(2026, 10, 30))
    after, after_share = jams(date(2026, 11, 2), date(2027, 1, 15))
    assert during > 2 * before and during_share > 0.35
    assert after < 1.4 * before and after_share < 0.3, (before, during, after, after_share)


def test_story_signals_are_visible_in_the_records(data):
    rebuild, splice, warp = stories.SLITTER_REBUILD, stories.SPLICER3_MISSED_SPLICE, stories.WEEKEND_WARP
    rebuild_text = rebuild["pm_date"].strftime("%Y%m%d")
    pm = [s for s in data["stops"] if s.kind == "PM" and s.production_date == rebuild_text]
    assert len(pm) == 1 and pm[0].place == rebuild["place"] and pm[0].duration_sec == rebuild["pm_minutes"] * 60
    # Story 1: trim creeps up and knife/slitter stops rise after the rebuild.
    before = [s for s in data["dry_end_setups"] if s.production_date < rebuild_text]
    late = [s for s in data["dry_end_setups"] if s.production_date >= "20260921"]
    trim_before = ratio(sum(s.trim_sqft for s in before), sum(s.total_produced_sqft for s in before))
    trim_late = ratio(sum(s.trim_sqft for s in late), sum(s.total_produced_sqft for s in late))
    assert 0.35 <= trim_late - trim_before <= 0.9
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
    per_day_story = sum(s.duration_sec for s in story) / 60 / story_days
    per_day_rest = sum(s.duration_sec for s in rest) / 60 / rest_days
    assert per_day_story > 2 * per_day_rest
    assert sum(s.duration_sec for s in story if s.place == splice["place"]) > 0.5 * sum(s.duration_sec for s in story)
    assert len(story) / story_days > 1.5 * len(rest) / rest_days
    chop = lambda rows: ratio(sum(s.chop_sqft for s in rows), sum(s.total_produced_sqft for s in rows))
    assert chop([s for s in data["dry_end_setups"] if s.production_date in week]) > \
        chop([s for s in data["dry_end_setups"] if s.production_date not in week])
    # Story 3: warp is higher on Mondays and in August.
    def warp_pct(rows):
        return ratio(sum(e.sqft for s in rows for e in s.rejects if e.family == "WARP"),
                     sum(s.total_produced_sqft for s in rows))
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
