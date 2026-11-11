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

import pytest

from investigator import records as R
from investigator.record_store import TABLES, RecordStore
from investigator.synth import (CONSTRUCTION, CUSTOMERS, DATASET_FIRST_DAY, _Planner, fill_outs, generate_months, lane_run,
                                narrowest_web, trim_pct, window_bounds, window_days)

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
    """Owner, 2026-11-11. Orders: AC Box 15 x 35 in, 2,000 sheets; Jerry's Packaging 17 x 47 in, 3,000 sheets."""
    # Setup 1, 100 in web, two knives. Jerry's at 3 out takes 51 in; AC Box fills what is left: 2 out would use
    # 81 in, too little; the most that fit beside 51 in under the minimum trim is 3 out.
    jerrys_in = 3 * 17
    assert 2 * 15 + jerrys_in == 81
    assert fill_outs(15, 100 - 2.0 - jerrys_in) == 3
    used = 3 * 15 + jerrys_in
    assert used == 96 and narrowest_web(used, 2.0) == 100
    assert 100 - used == 4 and (100 - used) / 2 == 2 and trim_pct(100, used) == pytest.approx(4.0)
    # AC Box sets the length: ceil(2,000 / 3) = 667 cuts x 35 in / 12 = 1,945.4 lineal ft. Jerry's runs only as
    # far as its pair: floor(1,945.4 x 12 / 47) = 496 cuts = 1,942.7 ft, 2.7 ft apart, under one 47 in cut.
    cuts, definer = lane_run([(2000, 3, 35), (3000, 3, 47)])
    assert cuts == [667, 496] and definer == 0
    ac_ft, jerrys_ft = cuts[0] * 35 / 12, cuts[1] * 47 / 12
    assert round(ac_ft, 1) == 1945.4 and round(jerrys_ft, 1) == 1942.7
    assert round(round(ac_ft, 1) - round(jerrys_ft, 1), 1) == 2.7 and ac_ft - jerrys_ft < 47 / 12
    assert cuts[0] * 3 == 2001 and cuts[1] * 3 == 1488 and 3000 - cuts[1] * 3 == 1512
    # Setup 2, Jerry's remainder alone at 5 out = 85 in: the scheduler runs it on the narrowest web at least as
    # wide as 85 in plus the minimum trim, 90 in paper: trim 5 in = 5.6%. On 95 in it would be 10.5%, on 100 in 15%.
    assert narrowest_web(5 * 17, 2.0) == 90
    assert [round(trim_pct(w, 85), 1) for w in (90, 95, 100)] == [5.6, 10.5, 15.0]
    # Left to choose the outs too, the search does no worse: 6 out = 102 in on 105 in paper is 2.9%.
    planner = _Planner(random.Random(0), "261111")
    web, lanes = planner._choose(order(17, 47, 1512), [], 2.0, None)
    used = sum(o["w"] * outs for o, outs in lanes)
    assert web == narrowest_web(used, 2.0) and trim_pct(web, used) <= trim_pct(90, 85)
    cuts, _ = lane_run([(1512, 5, 47)])
    assert cuts == [303] and round(303 * 47 / 12, 1) == 1186.8 and 303 * 5 == 1515 and 1515 - 1512 == 3
    # The field view (owner's second table): web 100 / 90, scheduled nominal feet about 1,945 / 1,187, trim 4.0 / 5.6.
    assert (round(667 * 35 / 12), round(303 * 47 / 12)) == (1945, 1187)


def test_the_scheduler_searches_every_pairing_and_never_does_worse_than_the_worked_example():
    """The generator pairs the lead order with every open partner at every outs and paper width: on the owner's
    two orders it finds a combination at least as tight as the example's 4.0% (2 out of AC Box beside 4 out of
    Jerry's uses 98 in of the 100 in web), and every pick is the narrowest web that fits its lanes."""
    planner = _Planner(random.Random(0), "261111")
    web, lanes = planner._choose(order(15, 35, 2000), [order(17, 47, 3000)], 2.0, None)
    used = sum(o["w"] * outs for o, outs in lanes)
    assert web == narrowest_web(used, 2.0) and trim_pct(web, used) <= 4.0


def test_lane_run_lets_a_near_tie_finish_both_lanes():
    """A lane that would finish within MIN_TAIL_FT of its pair finishes on the same setup: the longer of the two
    sets the length, and the shorter runs as far as its pair (its spares are scheduled over-run, never a tail
    setup of a few hundred feet)."""
    cuts, definer = lane_run([(1000, 2, 40), (1100, 2, 38)])
    assert definer == 1 and cuts[1] == 550 and cuts[0] == 550 * 38 // 40 and cuts[0] * 2 >= 1000


# ------------------------------------------------------------------ identities over the records

def test_orders_complete_or_roll_their_remainder_into_a_later_setup(data):
    """Identity 5: an order is complete when its sheets over its setups reach the demand; until then the remainder
    rolls into a later setup of its day (often on one knife at more outs, possibly on narrower paper). An order
    never runs again once complete; the few left open are the ones the day closed on."""
    sheets, complete, quantity = defaultdict(int), set(), {}
    for setup in data["dry_end_setups"]:
        for lane in setup.lanes:
            if lane.filler:
                assert lane.order_quantity == 0 and lane.customer_id == "C000"
                continue
            assert lane.order_number not in complete, lane.order_number
            quantity[lane.order_number] = lane.order_quantity
            sheets[lane.order_number] += lane.nominal_cuts * lane.number_of_outs
            if sheets[lane.order_number] >= lane.order_quantity:
                complete.add(lane.order_number)
    assert len(complete) / len(quantity) > 0.85, len(complete) / len(quantity)
    appearances = defaultdict(int)
    for setup in data["dry_end_setups"]:
        if setup.change_code == "O":
            for lane in setup.lanes:
                appearances[lane.order_number] += 1
    assert sum(1 for n in appearances.values() if n >= 2) > 0.2 * len(appearances)   # remainders do roll over


def test_per_customer_waste_allocation_is_the_lanes_share_of_used_width(data):
    """Identity 6: a setup's waste is allocated to its customers by each lane's share of the used width; the
    shares add to one and the allocations to the setup's waste."""
    for setup in data["dry_end_setups"][:2000]:
        shares = [lane.used_width_in / setup.used_width_in for lane in setup.lanes]
        assert sum(shares) == pytest.approx(1.0)
        waste = setup.trim_sqft + setup.chop_sqft
        assert sum(waste * share for share in shares) == pytest.approx(waste)


def test_setup_orders_outs_times_width_fit_the_web_minus_trim_for_every_setup(store):
    """Issue #100 acceptance: in the store's lane view, sum of outs x sheet width over a setup's lanes is at most
    the web width minus the trim, for every setup; it is exactly that, and never wider than the paper minus the
    one-inch-a-side minimum trim."""
    rows = sql(store, """SELECT s.setup_id, s.width_in, s.trim_in, sum(o.outs * o.sheet_width_in) AS used, count(*) AS lanes, max(s.lanes)
                         FROM setups s JOIN setup_orders o ON o.setup_id = s.setup_id GROUP BY ALL""")
    assert len(rows) == sql(store, "SELECT count(*) FROM setups")[0][0] > 2000
    for setup_id, width, trim_in, used, lanes, declared in rows:
        assert used <= width - trim_in + 1e-9, setup_id
        assert used == pytest.approx(width - trim_in) and used <= width - 2.0 + 1e-9
        assert lanes == declared and 1 <= lanes <= 3


def test_the_stop_log_sums_to_each_setups_summary(store):
    """Owner, 2026-11-11: for every setup, the sum of its stop rows' seconds equals its stop seconds and the row
    count its number of stops ("RunSetup 1234 had 2 stops, down 973 seconds")."""
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
    families = sum(rows[k]["sqft"] for k in ("bond", "warp", "misalignment", "other"))
    assert rows["dry_end"]["sqft"] == pytest.approx(families, abs=0.5)
    accounted = sum(rows[k]["sqft"] for k in ("trim", "shear", "changeover", "cutoff_waste", "stacker_waste", "dry_end", "admin"))
    assert rows["unexplained"]["sqft"] == pytest.approx(rows["bale"]["sqft"] - accounted, abs=1.0)
    assert 0.1 <= rows["unexplained"]["pct_of_gross"] <= 0.4


# ------------------------------------------------------------------ the reject taxonomy

def test_the_reject_taxonomy_is_data_and_positions_are_attributes():
    """reject_reasons.json: category -> reason, owner-approved; liner and side are attributes, never reasons;
    every category names a family whose waste line is dry_end, shear or excluded."""
    taxonomy = json.loads((ROOT / "src" / "investigator" / "reject_reasons.json").read_text())
    assert taxonomy["approved"] == "owner, 2026-11-11"
    assert taxonomy["attributes"] == {"liner": ["top", "bottom", "medium", "DB", "SF"], "side": ["drive", "operator", "middle"]}
    keys = [c["key"] for c in taxonomy["categories"]]
    assert keys == ["bond", "warp", "cracking", "misalignment", "handling", "paper", "changeover", "print", "admin"]
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
    setups = sql(store, "SELECT round(sum(rejects), 1), round(sum(changeover_sqft), 1), round(sum(admin_sqft), 1) FROM setups")[0]
    assert setups == pytest.approx((by_line["dry_end"], by_line["shear"], by_line["excluded"]), abs=5.0)


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
