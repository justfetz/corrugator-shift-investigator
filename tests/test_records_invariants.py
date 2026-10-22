"""Physics and accounting invariants of the two-month synthetic record set.

Every test walks every record of both months produced by the default seed.
The lineal check compares nominal cuts with ORDER travel (lineal_ft minus the
shear-chop travel): lineal_ft counts every foot of web, and ten 34-inch chops
can never fit inside one 20-inch cutoff, so gross = lineal x width / 12 is
kept exact and the cut check is made on the travel that reached the knives.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import pytest
from investigator import records as R
from investigator import stories
from investigator.fixture import GRADES
from investigator.synth import CHOP_LENGTH_IN, generate_months, production_days

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


def test_web_geometry_is_centered_within_trim_limits(setups):
    for setup in setups:
        assert setup.web_width_in in R.WEB_WIDTHS and setup.quality_id in GRADES
        assert 1 <= len(setup.customers) <= 3
        occupied = sum(c.sheet_width_in * c.number_of_outs for c in setup.customers)
        trim_in = setup.web_width_in - occupied
        assert trim_in == setup.trim_in
        assert 1 <= trim_in / 2 <= 7            # per side, web centered
        for index, customer in enumerate(setup.customers, 1):
            assert customer.cutoff_allocation == index
            assert 12 <= customer.sheet_width_in <= 60 and 20 <= customer.sheet_length_in <= 92
            assert customer.number_of_outs >= 1 and customer.stack_height_sheets > 0


def test_lineal_consistency_and_overrun_window(setups):
    within_two_percent = total = 0
    for setup in setups:
        travel = setup.order_travel_ft
        assert travel > 0
        feet = []
        for customer in setup.customers:
            cutoff_ft = customer.sheet_length_in / 12
            planned = customer.nominal_cuts * cutoff_ft
            assert abs(planned - travel) <= cutoff_ft
            feet.append((planned, cutoff_ft))
            assert 0.9 * customer.nominal_cuts <= customer.actual_cuts <= 1.1 * customer.nominal_cuts
            within_two_percent += abs(customer.actual_cuts - customer.nominal_cuts) <= 0.02 * customer.nominal_cuts
            total += 1
        for a, b in zip(feet, feet[1:]):
            assert abs(a[0] - b[0]) <= max(a[1], b[1])
        guide = setup.customers[0]
        assert setup.total_nominal_ft == pytest.approx(guide.nominal_cuts * guide.sheet_length_in / 12, abs=0.06)
    assert within_two_percent / total > 0.75


def test_setup_area_accounting(setups):
    for setup in setups:
        width = setup.web_width_in
        assert setup.total_produced_sqft == pytest.approx(setup.lineal_ft * width / 12, abs=0.1)
        chops = setup.chop_sqft * 144 / (CHOP_LENGTH_IN * width)
        assert chops == pytest.approx(round(chops), abs=0.01) and chops >= 0
        assert setup.trim_sqft == pytest.approx(setup.order_travel_ft * setup.trim_in / 12, abs=0.1)
        assert setup.trim_pct == pytest.approx(ratio(setup.trim_sqft, setup.total_produced_sqft), abs=0.01)
        assert setup.kqf_sqft == pytest.approx(setup.reject_sqft, abs=0.06)
        assert setup.trim_sqft + setup.chop_sqft + setup.reject_sqft < setup.total_produced_sqft
        waste = {c.cutoff_allocation: [0.0, 0.0, 0] for c in setup.customers}
        for entry in setup.rejects:
            customer = setup.customers[entry.cutoff_allocation - 1]
            assert entry.reason in R.REJECT_REASONS and entry.sheets > 0
            assert entry.detail in R.REJECT_DETAILS[entry.reason]
            assert entry.sqft == pytest.approx(entry.sheets * customer.sheet_width_in * customer.sheet_length_in / 144, abs=0.06)
            bucket = waste[entry.cutoff_allocation]
            bucket[0 if entry.reason == "MISALIGNMENT" else 1] += entry.sqft
            bucket[2] += entry.sheets
        for customer in setup.customers:
            hqm, asm, sheets = waste[customer.cutoff_allocation]
            assert customer.hqm_waste_sqft == pytest.approx(hqm, abs=0.06)
            assert customer.asm_waste_sqft == pytest.approx(asm, abs=0.06)
            assert sheets <= customer.actual_cuts * customer.number_of_outs
        assert setup.avg_speed_fpm == pytest.approx(setup.lineal_ft / (setup.elapsed_seconds / 60), abs=0.06)
        assert len(setup.setup_id) == 20 and setup.setup_id.startswith("CORR" + setup.start_date + setup.start_time)


def test_one_order_per_knife_and_continuation_codes(setups):
    assert len({s.setup_id for s in setups}) == len(setups)
    last_seen = {}
    for index, setup in enumerate(setups):
        orders = [c.order_number for c in setup.customers]
        assert len(set(orders)) == len(orders)           # one order per knife position
        assert setup.change_code in R.CHANGE_CODES
        for order in orders:                              # orders are contiguous in time
            assert last_seen.get(order, index - 1) == index - 1
            last_seen[order] = index
        previous = setups[index - 1] if index else None
        shared = set(orders) & {c.order_number for c in previous.customers} if previous else set()
        if setup.change_code == "O":
            assert not shared and setup.split_from_setup_id is None
        elif setup.change_code == "C":
            assert shared == {orders[0]} and orders[0] == previous.customers[0].order_number
            assert setup.wet_end_id == previous.wet_end_id and setup.split_from_setup_id is None
            kept, before = setup.customers[0], previous.customers[0]
            assert (kept.sheet_width_in, kept.sheet_length_in, kept.number_of_outs) == \
                (before.sheet_width_in, before.sheet_length_in, before.number_of_outs)
        else:
            assert setup.split_from_setup_id == previous.setup_id
            assert setup.wet_end_id == previous.wet_end_id
            assert [(c.order_number, c.sheet_width_in, c.sheet_length_in, c.number_of_outs) for c in setup.customers] == \
                [(c.order_number, c.sheet_width_in, c.sheet_length_in, c.number_of_outs) for c in previous.customers]
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
        assert shift.avg_speed_fpm == pytest.approx(shift.total_lineal_ft / 480, abs=0.06)
        assert shift.max_speed_fpm == pytest.approx(max(m.running_speed_fpm for m in members), abs=0.06)
        wet_ends = []
        for m in members:
            if m.wet_end_id not in wet_ends:
                wet_ends.append(m.wet_end_id)
        assert shift.wet_end_ids == wet_ends
        parts = shift.side_trim_sqft + shift.chop_sqft + shift.reject_sqft
        remainder = ratio(shift.bale_sqft - parts, shift.total_produced_sqft)
        assert 0.1 <= remainder <= 0.4


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
        assert "unverified" in stop.user_info
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
    gross, trim, chop, rejects = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for setup in data["dry_end_setups"]:
        month = month_of(setup)
        gross[month] += setup.total_produced_sqft
        trim[month] += setup.trim_sqft
        chop[month] += setup.chop_sqft
        for entry in setup.rejects:
            rejects[(month, entry.reason)] += entry.sqft
    for month in ("202608", "202609"):
        assert 2.5 <= ratio(trim[month], gross[month]) <= 3.5
        assert 0.4 <= ratio(chop[month], gross[month]) <= 1.0
        bond, warp, mis, moisture = (ratio(rejects[(month, r)], gross[month]) for r in R.REJECT_REASONS)
        assert 0.9 <= bond + warp + mis + moisture <= 1.3   # "dry-end waste should be about 1%"
        assert 0.3 <= bond <= 0.5 and 0.25 <= warp <= 0.55 and 0.15 <= mis <= 0.25 and 0.07 <= moisture <= 0.13
        # Reported sub-reasons follow the stories' mix: up warp leads the warp family, delamination leads bond.
        by_detail = defaultdict(float)
        for setup in data["dry_end_setups"]:
            if month_of(setup) == month:
                for entry in setup.rejects:
                    by_detail[entry.detail] += entry.sqft
        assert by_detail["Up warp"] > by_detail["Down warp"] > by_detail["Twist warp"] > by_detail["S-warp"] > 0
        assert by_detail["Delamination"] > by_detail["Paper / raw material"] > 0
    for shift in data["shifts"]:
        assert shift.bale_sqft > shift.side_trim_sqft + shift.chop_sqft + shift.reject_sqft


def test_output_per_production_day(data):
    per_day = defaultdict(float)
    for shift in data["shifts"]:
        per_day[shift.production_date] += shift.total_produced_sqft
        assert 1_200_000 < shift.total_produced_sqft < 3_000_000   # about 2.5M a shift at a 98-inch average web
    assert all(6_000_000 <= gross <= 8_500_000 for gross in per_day.values()), (min(per_day.values()), max(per_day.values()))
    setups_per_day = defaultdict(int)
    for setup in data["dry_end_setups"]:
        setups_per_day[setup.production_date] += 1
    assert 90 <= min(setups_per_day.values()) and max(setups_per_day.values()) <= 160   # about 7,000 lineal ft per setup
    feet = sum(s.lineal_ft for s in data["dry_end_setups"])
    assert 6_500 <= feet / len(data["dry_end_setups"]) <= 7_800
    assert 18_000 <= feet / len(data["wet_ends"]) <= 23_000   # about 20,000 lineal ft per paper run
    gross = sum(s.total_produced_sqft for s in data["dry_end_setups"])
    assert 97.0 <= gross / feet * 12 <= 99.5   # a 98-inch average web on a 110-inch machine
    weeks = defaultdict(float)
    for day_text, total in per_day.items():
        weeks[date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:])).isocalendar()[1]] += total
    full = [weeks[w] for w in sorted(weeks)][:-1]   # the last week of September is short
    moves = [abs(b - a) / a for a, b in zip(full, full[1:])]
    assert max(moves) >= 0.04   # week-to-week volume moves a few percent, not a flat line


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
        return ratio(sum(e.sqft for s in rows for e in s.rejects if e.reason == "WARP"),
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
