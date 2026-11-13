"""Record contract v2 (issue #100): the scheduler's worked example, the setup identities, the lane view,
the downtime log, the reject taxonomy, the dimensions and the roll stock.

docs/RECORD_CONTRACT.md is the contract these tests hold the generator and the store to.
"""
from collections import defaultdict
from datetime import date
import json
import math
from pathlib import Path
import random
import re

import pytest

from investigator import records as R
from investigator.record_store import TABLES, RecordStore, default_store
from investigator.synth import (CONSTRUCTION, CUSTOMERS, DATASET_FIRST_DAY, DOUBLE_WALL, _Planner, fill_outs, generate_months, lane_run,
                                min_trim_in, narrowest_web, trim_pct, window_bounds, window_days)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def data():
    return generate_months()


@pytest.fixture(scope="module")
def store():
    store = RecordStore(window=(date(2026, 9, 1), date(2026, 9, 30)), prebuilt="")
    yield store
    store.close()


def sql(store, text, params=()):
    return store._db.execute(text, list(params)).fetchall()


def order(width, length, quantity):
    return {"order_number": "SO-test", "part_number": "P-1", "customer_id": "C001", "w": float(width), "L": length,
            "quantity": quantity, "remaining": quantity, "fanfold": False, "stack": 200, "type": "product", "filler": False}


# ------------------------------------------------------------------ the owner's worked example

def test_the_owners_worked_example_reproduces_exactly():
    """Owner, 2026-11-11. Orders: AC Box 15 x 35 in, 2,000 sheets; Jerry's Packaging 17 x 47 in, 3,000 sheets. The
    example leaves 2 in of trim a side (4 in); at that allowance the scheduler's own search (`_Planner._choose`)
    picks the example's lanes and paper, and `lane_run` its cuts: the numbers come from the generator, not from
    arithmetic redone here."""
    allowance = 4.0
    planner = _Planner(random.Random(0), "261111")
    ac_box, jerrys = {**order(15, 35, 2000), "order_number": "SO-AC"}, {**order(17, 47, 3000), "order_number": "SO-JP"}
    # Setup 1, 100 in web, two knives: 3 out x 15 in beside 3 out x 17 in uses 96 in, trimming 4 in, 2 in a side.
    web, lanes = planner._choose(ac_box, [jerrys], allowance, None)
    assert web == 100 and [(o["order_number"], outs) for o, outs in lanes] == [("SO-AC", 3), ("SO-JP", 3)]
    used = sum(o["w"] * outs for o, outs in lanes)
    assert used == 96 and (web - used) / 2 == 2 and trim_pct(web, used) == pytest.approx(4.0)
    assert 2 * 15 + 3 * 17 == 81   # "2 outs x 15 in would be 81 in used, too little; bump to 3 out"
    # AC Box sets the length: ceil(2,000 / 3) = 667 cuts x 35 in / 12 = 1,945.4 lineal ft. Jerry's runs only as
    # far as its pair: floor(1,945.4 x 12 / 47) = 496 cuts = 1,942.7 ft, 2.7 ft apart, under one 47 in cut.
    cuts, definer = lane_run([(o["remaining"], outs, o["L"]) for o, outs in lanes])
    assert cuts == [667, 496] and definer == 0
    ac_ft, jerrys_ft = cuts[0] * 35 / 12, cuts[1] * 47 / 12
    assert round(ac_ft, 1) == 1945.4 and round(jerrys_ft, 1) == 1942.7
    assert round(round(ac_ft, 1) - round(jerrys_ft, 1), 1) == 2.7 and ac_ft - jerrys_ft < 47 / 12
    assert cuts[0] * 3 == 2001 and cuts[1] * 3 == 1488 and 3000 - cuts[1] * 3 == 1512
    # Setup 2, Jerry's remainder alone: the search picks 5 out = 85 in on the narrowest paper that fits, 90 in,
    # trimming 5 in = 5.6% (10.5% on 95 in, 15% on 100 in); 303 cuts, 1,186.8 ft, 1,515 sheets, 3 over.
    jerrys["remaining"] = 3000 - cuts[1] * 3
    web, lanes = planner._choose(jerrys, [], allowance, None)
    assert web == 90 and [(o["order_number"], outs) for o, outs in lanes] == [("SO-JP", 5)]
    assert [round(trim_pct(w, 85), 1) for w in (90, 95, 100)] == [5.6, 10.5, 15.0]
    cuts, _ = lane_run([(jerrys["remaining"], 5, 47)])
    assert cuts == [303] and round(303 * 47 / 12, 1) == 1186.8 and 303 * 5 == 1515 and 1515 - 1512 == 3
    # The field view (owner's second table): web 100 / 90, scheduled nominal feet about 1,945 / 1,187, trim 4.0 / 5.6.
    assert (round(667 * 35 / 12), round(303 * 47 / 12)) == (1945, 1187)


def test_the_scheduler_searches_every_pairing_and_never_does_worse_than_the_worked_example():
    """At the generator's own minimum trim (2 in, 1 in a side) the same search pairs the owner's two orders at
    least as tight as the example's 4.0% (2 out of AC Box beside 4 out of Jerry's uses 98 in of the 100 in web),
    and every pick is the narrowest web that fits its lanes."""
    planner = _Planner(random.Random(0), "261111")
    web, lanes = planner._choose(order(15, 35, 2000), [order(17, 47, 3000)], 2.0, None)
    used = sum(o["w"] * outs for o, outs in lanes)
    assert web == narrowest_web(used, 2.0) and trim_pct(web, used) <= 4.0
    # Jerry's 1,512-sheet remainder alone: the narrowest paper that fits, and no worse than the example's 5 out on
    # 90 in (5.6%); the search finds 6 out on 105 in at 2.9%
    web, lanes = planner._choose(order(17, 47, 1512), [], 2.0, None)
    used = sum(o["w"] * outs for o, outs in lanes)
    assert web == narrowest_web(used, 2.0) and trim_pct(web, used) <= trim_pct(90, 85)


def test_lane_run_lets_a_near_tie_finish_both_lanes():
    """A lane that would finish within MIN_TAIL_FT of its pair finishes on the same setup: the longer of the two
    sets the length, and the shorter runs as far as its pair (its spares are scheduled over-run, never a tail
    setup of a few hundred feet)."""
    cuts, definer = lane_run([(1000, 2, 40), (1100, 2, 38)])
    assert definer == 1 and cuts[1] == 550 and cuts[0] == 550 * 38 // 40 and cuts[0] * 2 >= 1000


# ------------------------------------------------------------------ identities over the records

def test_orders_complete_or_roll_their_remainder_into_a_later_setup(data):
    """Identity 5, on scheduled (nominal) sheets: an order is complete when its sheets over its setups reach the
    demand, and it never runs again; until then the remainder rolls into a later setup of its day (often on one
    knife at more outs, possibly on narrower paper). A setup closed at a shift boundary before its last cuts ran
    gives those sheets back to its orders, so an order left short is one its day closed on: after its last
    setup, its grade starts no other setup that day. (Counted on actual cuts an order lands anywhere in the
    over/under-run window around its demand.)"""
    sheets, complete, quantity, last = defaultdict(int), set(), {}, {}
    setups = data["dry_end_setups"]
    for index, setup in enumerate(setups):
        for lane in setup.lanes:
            if lane.filler:
                assert lane.order_quantity == 0 and lane.customer_id == "C000"
                continue
            assert lane.order_number not in complete, lane.order_number
            quantity[lane.order_number] = lane.order_quantity
            sheets[lane.order_number] += lane.nominal_cuts * lane.number_of_outs
            last[lane.order_number] = index
            if sheets[lane.order_number] >= lane.order_quantity:
                complete.add(lane.order_number)
    short = [order for order in quantity if order not in complete]
    for order in short:
        at = setups[last[order]]
        later = [s for s in setups[last[order] + 1:] if s.production_date == at.production_date
                 and s.quality_id == at.quality_id and s.change_code == "O"]
        assert later == [], (order, at.setup_id, later[0].setup_id)
    assert len(complete) / len(quantity) > 0.9, len(complete) / len(quantity)
    appearances = defaultdict(int)
    for setup in setups:
        if setup.change_code == "O":
            for lane in setup.lanes:
                appearances[lane.order_number] += 1
    assert sum(1 for n in appearances.values() if n >= 2) > 0.2 * len(appearances)   # remainders do roll over


def test_per_customer_waste_allocation_is_the_lanes_share_of_used_width(store):
    """Identity 6: a setup's own waste (side trim and shear chops) is allocated to its customers by each lane's
    share of the used width. The store's lane view carries the share and the two allocations; they add up to the
    setup's figures for every setup (the reject lines and waste boards are already each lane's own)."""
    rows = sql(store, """SELECT s.setup_id, s.trim_sqft, s.shear_sqft, sum(o.width_share), sum(o.allocated_trim_sqft), sum(o.allocated_shear_sqft),
                                bool_and(abs(o.width_share - o.outs * o.sheet_width_in / (s.web_width_in - s.trim_in)) < 1e-9)
                         FROM setups s JOIN setup_orders o ON o.setup_id = s.setup_id GROUP BY ALL""")
    assert len(rows) == sql(store, "SELECT count(*) FROM setups")[0][0]
    for setup_id, trim, shear, share, trim_part, shear_part, by_width in rows:
        assert share == pytest.approx(1.0) and by_width, setup_id
        assert trim_part == pytest.approx(trim, abs=1e-6) and shear_part == pytest.approx(shear, abs=1e-6)
    by_customer = dict(sql(store, "SELECT customer_id, sum(allocated_trim_sqft) FROM setup_orders GROUP BY customer_id"))
    assert sum(by_customer.values()) == pytest.approx(sql(store, "SELECT sum(trim_sqft) FROM setups")[0][0], rel=1e-9)


def test_setup_orders_outs_times_width_fit_the_web_minus_trim_for_every_setup():
    """Issue #100 acceptance, over every setup of the golden window (1 January 2025 to 30 September 2026): in the
    store's lane view, sum of outs x sheet width over a setup's lanes leaves the day's minimum trim on the web
    (used + minimum trim <= web width), the trim column is exactly what is left (used = web - trim), and the web
    is the narrowest paper that holds the lanes with that trim."""
    store = default_store()
    rows = store._db.execute("""SELECT s.setup_id, s.production_date, s.web_width_in, s.trim_in, sum(o.outs * o.sheet_width_in) AS used,
                                       count(*) AS lanes, max(s.lanes)
                                FROM setups s JOIN setup_orders o ON o.setup_id = s.setup_id GROUP BY ALL""").fetchall()
    assert len(rows) == store._db.execute("SELECT count(*) FROM setups").fetchone()[0] > 50_000
    for setup_id, day, width, trim_in, used, lanes, declared in rows:
        minimum = min_trim_in(day)
        assert used + minimum <= width + 1e-9, (setup_id, used, minimum, width)
        assert used == pytest.approx(width - trim_in) and width == narrowest_web(used, minimum), setup_id
        assert lanes == declared and 1 <= lanes <= 3


def test_the_stop_log_sums_to_each_setups_summary(store):
    """Owner, 2026-11-11: for every setup, the sum of its stop rows' seconds equals its stop seconds and the row
    count its number of stops ("run setup 1234 had 2 stops, down 973 seconds")."""
    rows = sql(store, """SELECT s.setup_id, s.stop_seconds, s.stops, coalesce(sum(st.seconds), 0), count(st.stop_id)
                         FROM setups s LEFT JOIN stops st ON st.setup_id = s.setup_id GROUP BY ALL""")
    assert rows and all(seconds == logged and stops == count for _, seconds, stops, logged, count in rows)
    assert sum(1 for r in rows if r[2] >= 2) > 50


def test_stop_comments_are_free_text_often_empty_and_kept_verbatim(store, data):
    """The comment is the operator's free text: a realistic share is empty, the rest plausible detail. The store
    keeps an empty comment as '' (never NULL) and the text byte for byte."""
    comments = [s.comment for s in data["stops"]]
    empty = sum(1 for c in comments if c == "") / len(comments)
    assert 0.3 <= empty <= 0.7, empty
    assert len({c for c in comments if c}) > 30
    assert sql(store, "SELECT count(*) FROM stops WHERE comment IS NULL")[0][0] == 0
    sample = [s for s in data["stops"] if s.production_date == "20260914" and s.comment][:5]
    for stop in sample:
        assert sql(store, "SELECT comment FROM stops WHERE stop_id = ?", [stop.stop_id]) == [(stop.comment,)]


def test_swing_knife_is_null_never_zero(store, data):
    """Owner, 2026-11-11: the swing knife is optional; the demo machine has none, so its square footage is null
    on every setup, in the records and in the store, and no sum turns it into a zero."""
    assert all(s.swing_knife_sqft is None for s in data["dry_end_setups"])
    assert sql(store, "SELECT count(*), count(swing_knife_sqft), sum(swing_knife_sqft) FROM setups")[0][1:] == (0, None)


def test_change_codes_are_a_code_with_a_lookup(data):
    assert R.CHANGE_CODES == {"O": "order change", "S": "shift split"}
    counts = defaultdict(int)
    for setup in data["dry_end_setups"]:
        counts[setup.change_code] += 1
        assert (setup.change_code == "S") == (setup.split_from_setup_id is not None)
    assert set(counts) == {"O", "S"} and counts["O"] > 10 * counts["S"]


def test_cutoff_knife_and_stacker_waste_boards_are_their_own_lines(store):
    """Owner, 2026-11-11: the boards lost at a lane's cutoff knife and at its stacker are waste-board counts on the
    lane, converted with the lane's sheet size, shown as their own waste lines and kept out of dry-end quality."""
    lane_sum = sql(store, "SELECT round(sum(cutoff_waste_sqft), 1), round(sum(stacker_waste_sqft), 1) FROM setup_orders")[0]
    setup_sum = sql(store, "SELECT round(sum(cutoff_waste_sqft), 1), round(sum(stacker_waste_sqft), 1) FROM setups")[0]
    assert lane_sum == pytest.approx(setup_sum, abs=1.0) and min(lane_sum) > 0
    result = store.get_waste_breakdown({"period": "month", "anchor": "2026-09-14"})
    rows = {r["category"]: r for r in result["rows"]}
    assert rows["cutoff_waste"]["sqft"] == pytest.approx(lane_sum[0], abs=1.0)
    assert rows["stacker_waste"]["sqft"] == pytest.approx(lane_sum[1], abs=1.0)
    families = sum(rows[k]["sqft"] for k in ("bond", "warp", "misalignment", "moisture", "other"))
    assert rows["dry_end"]["sqft"] == pytest.approx(families, abs=0.5)
    accounted = sum(rows[k]["sqft"] for k in ("trim", "shear", "changeover", "cutoff_waste", "stacker_waste", "dry_end", "admin"))
    assert rows["unexplained"]["sqft"] == pytest.approx(rows["bale"]["sqft"] - accounted, abs=1.0)
    assert 0.1 <= rows["unexplained"]["pct_of_gross"] <= 0.4


# ------------------------------------------------------------------ the reject taxonomy

def test_the_reject_taxonomy_is_data_and_positions_are_attributes():
    """reject_reasons.json: category -> reason, owner-approved; liner and side are attributes, never reasons;
    every category names a family whose waste line is dry_end, shear or excluded."""
    taxonomy = json.loads((ROOT / "src" / "investigator" / "reject_reasons.json").read_text())
    assert taxonomy["approved"] == "owner, 2026-11-11; moisture kept as its own category, owner 2026-11-12"
    assert taxonomy["attributes"] == {"liner": ["top", "bottom", "medium", "DB", "SF"], "side": ["drive", "operator", "middle"]}
    keys = [c["key"] for c in taxonomy["categories"]]
    assert keys == ["bond", "warp", "moisture", "cracking", "misalignment", "handling", "paper", "changeover", "print", "admin"]
    assert {f["counted_in"] for f in taxonomy["families"].values()} == {"dry_end", "shear", "excluded"}
    assert [f for f, v in taxonomy["families"].items() if v["counted_in"] == "dry_end"] == list(R.DRY_END_FAMILIES)
    assert R.counted_in("changeover") == "shear" and R.counted_in("admin") == "excluded" and R.counted_in("paper") == "dry_end"
    reasons = [r["reason"].lower() for c in taxonomy["categories"] for r in c["reasons"]]
    assert len(reasons) == len(set(reasons))
    for word in ("top liner", "bottom liner", "drive side", "operator side"):
        assert not any(reason == word or reason.startswith(word) for reason in reasons), word
    for category in taxonomy["categories"]:
        assert category["family"] in taxonomy["families"] and category["controllable"] in ("yes", "partly", "no")
        for reason in category["reasons"]:
            assert set(reason.get("liner", [])) <= set(taxonomy["attributes"]["liner"])
            assert set(reason.get("side", [])) <= set(taxonomy["attributes"]["side"])


def test_the_store_carries_the_taxonomy_and_every_reject_line_names_it(store):
    rows = sql(store, "SELECT count(*) FROM reject_reasons")[0][0]
    assert rows == sum(len(c["reasons"]) for c in R.taxonomy()["categories"])
    orphans = sql(store, """SELECT count(*) FROM rejects r LEFT JOIN reject_reasons t ON t.category = r.category AND t.reason = r.reason
                            WHERE t.reason IS NULL OR t.family <> r.family OR t.counted_in <> r.counted_in OR NOT t.generated""")
    assert orphans == [(0,)]
    by_line = dict(sql(store, "SELECT counted_in, round(sum(sqft), 1) FROM rejects GROUP BY counted_in"))
    assert set(by_line) == {"dry_end", "shear", "excluded"}
    setups = sql(store, "SELECT round(sum(reject_sqft), 1), round(sum(changeover_sqft), 1), round(sum(admin_sqft), 1) FROM setups")[0]
    assert setups == pytest.approx((by_line["dry_end"], by_line["shear"], by_line["excluded"]), abs=5.0)



def test_a_single_wall_board_never_records_a_double_wall_reason_or_liner(data):
    """Codex review of PR #123: a single-wall grade (26-C, 32-C, 55-C, 200-C) has no second flute and no DB or SF
    liner, so its reject lines never carry a `double_wall_only` reason (a wrong DB/SF liner, between the flutes) or
    a DB / SF position; double-wall grades do."""
    double_only = {(c["key"], r["reason"]) for c in R.taxonomy()["categories"] for r in c["reasons"] if r.get("double_wall_only")}
    assert ("paper", "Wrong DB/SF liner") in double_only and ("bond", "Between flutes (DW)") in double_only
    single, double = [], []
    for setup in data["dry_end_setups"]:
        (double if setup.quality_id in DOUBLE_WALL else single).extend(setup.rejects)
    assert single and double
    assert [r for r in single if (r.category, r.reason) in double_only or r.liner in ("DB", "SF")] == []
    assert any(r.liner in ("DB", "SF") for r in double) and any((r.category, r.reason) in double_only for r in double)


def test_moisture_is_its_own_dry_end_family():
    """Owner, 2026-11-12: moisture stays a reject category with its own reasons, not folded into other."""
    moisture = R.categories()["moisture"]
    assert moisture["family"] == "MOISTURE" and R.counted_in("moisture") == "dry_end" and len(moisture["reasons"]) >= 3
    assert R.DRY_END_FAMILIES == ("BOND", "WARP", "MISALIGNMENT", "MOISTURE", "OTHER")
    assert "moisture" not in {c["key"] for c in R.categories().values() if c["family"] == "OTHER"}


def test_the_contract_document_maps_every_field_and_column():
    """docs/RECORD_CONTRACT.md names every record field of every family and every column of every table, each in
    backticks, so nothing reaches the store undocumented; it gives each field its generic plant term and says what is
    not modeled (taken from #122's test, adapted to this contract's families). The plant terms are plain words, never
    one source system's CamelCase field names (a setup key, a fanfold flag or a lane's numbered fields as an export
    spells them): the contract describes a generic corrugator, not one system's export (the coordinator's re-review of
    PR #123, item 6). Only this package's own record classes are CamelCase there."""
    text = (ROOT / "docs" / "RECORD_CONTRACT.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_]+)`", text))
    for family in (*R.FAMILIES.values(), *R.DIMENSIONS.values(), R.OrderLane, R.RejectEntry):
        missing = {f for f in family.__dataclass_fields__} - named
        assert not missing, (family.__name__, missing)
    for table, columns in TABLES.items():
        missing = {c.split()[0] for c in columns.split(", ")} - named
        assert not missing, (table, missing)
    assert "not modeled" in text.lower() and "| Ours | Record field | Plant term | Notes |" in text and "Typical MES" not in text
    ours = {family.__name__ for family in (*R.FAMILIES.values(), *R.DIMENSIONS.values(), R.OrderLane, R.RejectEntry)}
    camel = set(re.findall(r"`([A-Za-z]*[a-z][A-Z][A-Za-z0-9]*|[A-Z][a-z]+[A-Z][A-Za-z0-9]*)`", text)) - ours
    assert not camel, f"source-system field names in the contract: {sorted(camel)}"
    for change in R.CHANGE_CODES:
        assert f"`{change}`" in text


# ------------------------------------------------------------------ dimensions, sheet types, rolls

def test_customer_is_a_dimension_without_measures(store, data):
    assert TABLES["customers"] == "customer_id VARCHAR, name VARCHAR, destination VARCHAR, ship_to VARCHAR"
    ids = {c.customer_id for c in CUSTOMERS}
    assert len(ids) == len(CUSTOMERS) and data["customers"] == list(CUSTOMERS)
    assert sql(store, "SELECT count(*) FROM setup_orders o LEFT JOIN customers c USING (customer_id) WHERE c.name IS NULL") == [(0,)]
    plant = sql(store, "SELECT bool_and(filler), bool_and(sheet_type IN ('bottom_sheet', 'cap_sheet')), count(*) FROM setup_orders WHERE customer_id = 'C000'")[0]
    assert plant[:2] == (True, True) and plant[2] > 0
    assert sql(store, "SELECT count(DISTINCT customer_id) FROM setup_orders")[0][0] > 30


def test_sheet_types_fillers_and_fanfold_fields(data):
    fanfold = filler = 0
    for setup in data["dry_end_setups"]:
        assert setup.sheet_type in R.SHEET_TYPES and setup.filler == any(lane.filler for lane in setup.lanes)
        assert setup.sheet_type == ("product" if any(lane.sheet_type == "product" for lane in setup.lanes) else setup.lanes[0].sheet_type)
        filler += setup.filler
        if setup.is_fanfold:
            fanfold += 1
            assert len(setup.lanes) == 1
            lane = setup.lanes[0]
            assert lane.folds == lane.actual_cuts and lane.perforation_length_in == lane.sheet_length_in
            assert lane.pallets == math.ceil(lane.actual_cuts * lane.number_of_outs / lane.sheets_per_pallet)
        else:
            assert all(lane.folds is lane.sheets_per_pallet is lane.pallets is lane.perforation_length_in is None for lane in setup.lanes)
        for lane in setup.lanes:
            assert lane.filler == (lane.sheet_type != "product")
    assert fanfold > 5 and filler > 20


def test_roll_stock_feeds_each_paper_run_with_peel_and_core(data):
    """Rolls: each stand of a paper run's grade feeds the run's feet (a medium with its flute take-up); a roll
    run down inside the run is spliced out and leaves core paper, the last roll on a stand goes back to stock;
    every roll's outer wraps are peeled at prep."""
    by_run = defaultdict(list)
    for roll in data["rolls"]:
        by_run[roll.wet_end_id].append(roll)
        assert roll.peel_sqft > 0 and (roll.core_sqft > 0) == roll.spliced_out
    for wet in data["wet_ends"]:
        rolls = by_run[wet.wet_end_id]
        stands = CONSTRUCTION[wet.quality_id]
        assert [s for s, *_ in stands] == list(dict.fromkeys(r.stand for r in rolls))
        for stand, paper, take_up in stands:
            mine = [r for r in rolls if r.stand == stand]
            assert all(r.paper_grade == paper and r.width_in == wet.web_width_in for r in mine)
            assert sum(r.lineal_ft for r in mine) == pytest.approx(wet.lineal_ft * take_up, abs=0.1 * len(mine))
            assert not mine[-1].spliced_out and all(r.spliced_out for r in mine[:-1])


def test_the_data_starts_on_1_january_2025_with_a_rolling_end():
    """Owner, 2026-11-11: the window rule is 2025-01-01 to yesterday, a fixed start and a rolling end, so a
    year-to-date has a full previous year under it."""
    assert DATASET_FIRST_DAY == date(2025, 1, 1)
    assert window_bounds(date(2026, 9, 30)) == (date(2025, 1, 1), date(2026, 9, 30))
    assert window_bounds(date(2026, 11, 10))[0] == date(2025, 1, 1)
    assert window_days(date(2026, 9, 30))[0] == date(2025, 1, 1) and len(window_days(date(2026, 9, 30))) == 456
    with pytest.raises(ValueError, match="starts on 2025-01-01"):
        window_bounds(date(2024, 12, 31))
