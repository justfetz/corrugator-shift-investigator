"""The record contract v2 (issue #100) at the store: the fact table and its dimensions over the whole golden
year, the identities the owner asked to be tested there, and the contract document covering every field.

The per-record identities over two months are in tests/test_records_invariants.py; these read the DuckDB
tables the tools read (`setups`, `setup_orders`, `customers`, `rejects`, `stops`, `rolls`).
"""
import copy
import re
from pathlib import Path

import pytest

from investigator import records as R
from investigator.record_store import TABLES, RecordStore, default_store
from investigator.synth import TRIM_MIN_IN

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "RECORD_CONTRACT.md"


@pytest.fixture(scope="module")
def store():
    return default_store()


def count(store, sql):
    return store._fetch(sql, [])[0]["n"]


def test_setup_orders_outs_times_width_fit_the_web_less_the_trim_for_every_setup(store):
    """Acceptance: for every setup of the year, the order lanes' outs x sheet width is at most the web width minus
    the minimum trim (1 inch a side), and the trim the setup records is the rest of the web, both as trim_pct and
    as trim_sqft over its order travel (lineal less the chops); every setup has one to three lanes."""
    lanes = ("SELECT s.setup_id, s.web_width_in AS web, s.trim_pct, s.trim_sqft, "
             "s.lineal_ft - s.shear_sqft * 12 / s.web_width_in AS travel, sum(o.outs * o.sheet_width_in) AS used "
             "FROM setups s JOIN setup_orders o USING (setup_id) GROUP BY ALL")
    over = f"SELECT count(*) AS n FROM ({lanes}) WHERE used > web - {TRIM_MIN_IN}"
    assert count(store, over) == 0
    off = f"SELECT count(*) AS n FROM ({lanes}) WHERE abs(used + trim_pct * web / 100 - web) > 0.01 OR abs(trim_sqft * 12 / travel - (web - used)) > 0.05"
    assert count(store, off) == 0
    lanes = "SELECT count(*) AS n FROM (SELECT setup_id, count(*) AS k FROM setup_orders GROUP BY setup_id) WHERE k BETWEEN 1 AND 3"
    assert count(store, lanes) == count(store, "SELECT count(*) AS n FROM setups") == store.counts["setups"]


def test_a_setup_whose_lanes_overfill_the_web_is_refused():
    """The validator bounds the lanes by the web itself, and the recorded trim must be the rest of it: a record whose
    lanes add to more than the web, or whose trim_pct disagrees with its lanes, is excluded, never charted."""
    day = default_store()._day_records(default_store().last_day)["setups"]
    wide = copy.deepcopy(day[0])
    wide.lanes[0].outs += 10   # e.g. 247 in of sheets on a 100 in web
    with pytest.raises(ValueError, match="centered web placement"):
        RecordStore._validate_setup(wide, wide.trim_sqft)
    off = copy.deepcopy(day[0])
    off.trim_pct += 1.0
    with pytest.raises(ValueError, match="centered web placement"):
        RecordStore._validate_setup(off, off.trim_sqft)


def test_a_lanes_share_of_the_setups_waste_is_its_share_of_the_used_width(store):
    """Identity 6 (owner, issue #100): per-customer waste allocation = the setup's waste x the lane's share of the
    used width. setup_orders carries the share and the lane's part of the setup's side trim and shear; the shares add
    to one and the parts to the setup's own trim and shear, for every setup of the year."""
    sql = ("SELECT count(*) AS n FROM (SELECT s.setup_id, sum(o.width_share) AS share, sum(o.allocated_trim_sqft) AS trim, "
           "sum(o.allocated_shear_sqft) AS shear, any_value(s.trim_sqft) AS t, any_value(s.shear_sqft) AS c "
           "FROM setups s JOIN setup_orders o USING (setup_id) GROUP BY s.setup_id) "
           "WHERE abs(share - 1) > 1e-9 OR abs(trim - t) > 1e-6 OR abs(shear - c) > 1e-6")
    assert count(store, sql) == 0
    wrong = "SELECT count(*) AS n FROM setup_orders o JOIN (SELECT setup_id, sum(outs * sheet_width_in) AS used FROM setup_orders GROUP BY setup_id) u USING (setup_id) WHERE abs(o.width_share - o.outs * o.sheet_width_in / u.used) > 1e-12"
    assert count(store, wrong) == 0


def test_every_setups_stop_rows_sum_to_its_stop_seconds_and_count(store):
    """Owner, issue #100: for every setup, the sum of its downtime-log rows' seconds equals its stop seconds
    (StopTimeForThisOrder) and the row count equals its stops (NumberOfStops)."""
    sql = ("SELECT count(*) AS n FROM setups s LEFT JOIN (SELECT setup_id, count(*) AS k, sum(seconds) AS secs FROM stops "
           "GROUP BY setup_id) t USING (setup_id) WHERE coalesce(t.k, 0) <> s.stops OR coalesce(t.secs, 0) <> s.stop_seconds")
    assert count(store, sql) == 0
    assert count(store, "SELECT count(*) AS n FROM stops WHERE setup_id NOT IN (SELECT setup_id FROM setups)") == 0


def test_the_swing_knife_is_null_never_zero(store):
    """Swing-knife area (owner, issue #100): CORR-1 has no swing knife, so the column is null on
    every setup, never zero; a machine that has one carries its area, and validation never reads null as 0."""
    assert count(store, "SELECT count(*) AS n FROM setups WHERE swing_knife_sqft IS NOT NULL") == 0
    assert count(store, "SELECT count(*) AS n FROM setups WHERE swing_knife_sqft IS NULL") == store.counts["setups"]
    day = store._day_records(store.last_day)["setups"]
    with_knife = copy.deepcopy(day[0])
    with_knife.swing_knife_sqft = 125.0
    RecordStore._validate_setup(with_knife, with_knife.trim_sqft)   # a machine with a swing knife is valid
    with_knife.swing_knife_sqft = -1.0
    with pytest.raises(ValueError, match="swing_knife_sqft"):
        RecordStore._validate_setup(with_knife, with_knife.trim_sqft)


def test_customers_are_a_dimension_and_lanes_point_at_it(store):
    assert [c[0] for c in store._db.execute("DESCRIBE customers").fetchall()] == ["customer_id", "name", "segment", "destination", "ship_to"]
    assert count(store, "SELECT count(*) AS n FROM setup_orders WHERE customer_id NOT IN (SELECT customer_id FROM customers)") == 0
    assert count(store, f"SELECT count(*) AS n FROM setups s JOIN setup_orders o USING (setup_id) "
                        f"WHERE s.sheet_type <> 'product' AND o.lane = 1 AND o.customer_id <> '{R.INTERNAL_CUSTOMER}'") == 0


def test_reject_lines_carry_category_reason_and_positions_from_the_taxonomy(store):
    rows = store._fetch("SELECT category, reason, liner, side, counted_as, count(*) AS n FROM rejects GROUP BY ALL ORDER BY ALL", [])
    for row in rows:
        reason = R.reject_reasons(row["category"])[row["reason"]]
        assert row["counted_as"] == R.counted_as(row["category"])
        assert (row["liner"] in reason["liner"]) if reason["liner"] else row["liner"] is None
        assert (row["side"] in reason["side"]) if reason["side"] else row["side"] is None
    # the setup's dry-end rejects are its dry-end lines; a position is an attribute, never a reason
    mismatch = ("SELECT count(*) AS n FROM setups s LEFT JOIN (SELECT setup_id, sum(sqft) AS sqft FROM rejects WHERE counted_as = 'dry_end' "
                "GROUP BY setup_id) r USING (setup_id) WHERE abs(coalesce(r.sqft, 0) - s.reject_sqft) > 0.06")
    assert count(store, mismatch) == 0


def test_lines_outside_dry_end_quality_are_kept_out_of_dry_end_waste():
    """Changeover lines count with shear and sequencing, planning / admin lines are not production waste
    (owner taxonomy): a record carrying them keeps its dry-end rejects to the dry-end lines, and the store
    marks each line with where it is reported."""
    data = default_store()._day_records(default_store().last_day)
    setup = copy.deepcopy(data["setups"][0])
    lane = setup.lanes[0]
    setup.rejects += [R.RejectEntry(1, "planning", "Count adjustment", None, None, 3, round(3 * lane.sheet_sqft, 1)),
                      R.RejectEntry(1, "changeover", "Order change", None, None, 2, round(2 * lane.sheet_sqft, 1))]
    RecordStore._validate_setup(setup, setup.trim_sqft)   # reject_sqft still reconciles: neither line is dry-end
    assert [R.counted_as(e.category) for e in setup.rejects[-2:]] == ["excluded", "changeover"]
    rows = RecordStore._rows({"dry_end_setups": [setup], "wet_ends": [], "shifts": [], "stops": [], "rolls": [], "customers": []})
    assert [r[8] for r in rows["rejects"][-2:]] == ["excluded", "changeover"]
    setup.rejects.append(R.RejectEntry(1, "bond", "Delamination", "top", "drive", 1, round(lane.sheet_sqft, 1)))
    with pytest.raises(ValueError, match="do not reconcile"):
        RecordStore._validate_setup(setup, setup.trim_sqft)
    setup.rejects[-1] = R.RejectEntry(1, "bond", "Moisture", None, None, 1, round(lane.sheet_sqft, 1))
    with pytest.raises(ValueError, match="taxonomy"):
        RecordStore._validate_setup(setup, setup.trim_sqft)


def test_the_contract_document_maps_every_field_and_column():
    """docs/RECORD_CONTRACT.md names every record field of every family and every column of every table,
    each in backticks, so nothing reaches the store undocumented; and it never names a vendor."""
    text = CONTRACT.read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_]+)`", text))
    for family in (*R.ALL_FAMILIES.values(), R.OrderLane, R.RejectEntry):
        missing = {f for f in family.__dataclass_fields__} - named
        assert not missing, (family.__name__, missing)
    for table, columns in TABLES.items():
        missing = {c.split()[0] for c in columns.split(", ")} - named
        assert not missing, (table, missing)
    assert "not modeled" in text.lower() and "Typical MES field" in text
    for change in R.CHANGE_CODES:
        assert f"`{change}`" in text
