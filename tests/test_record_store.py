"""The RecordStore builds the per-day tool tables from the record families.

Expectations are plain Python sums over the generated dataclasses, never a
second DuckDB query, so a wrong mapping cannot agree with itself.
"""
from collections import defaultdict
from datetime import date
import pytest
from investigator.fixture import GRADES
from investigator.record_store import CORRUPT_TRIM_SQFT, RecordStore, default_store
from investigator.synth import generate_months, production_days

DAY = "2026-09-14"   # a Monday in the second month


@pytest.fixture(scope="module")
def store():
    return default_store()


@pytest.fixture(scope="module")
def data():
    return generate_months()


def day_records(data, day):
    key = day.replace("-", "")
    return ([s for s in data["dry_end_setups"] if s.production_date == key],
            [st for st in data["stops"] if st.production_date == key],
            [sh for sh in data["shifts"] if sh.production_date == key])


def test_store_is_the_default_months_built_once(store, data):
    assert store.content_hash == data["content_hash"]
    assert store.production_days == [d.isoformat() for d in production_days((("2026", 8), ("2026", 9)))]
    assert (store.first_day, store.last_day) == ("2026-08-03", "2026-09-30")
    assert len(store.production_days) == 43 and all(date.fromisoformat(d).weekday() < 5 for d in store.production_days)
    assert store.counts == {name: len(data[family]) for name, family in
                            (("setups", "dry_end_setups"), ("wet_ends", "wet_ends"), ("shifts", "shifts"), ("stops", "stops"))} | \
        {"customers": sum(len(s.customers) for s in data["dry_end_setups"]),
         "rejects": sum(len(s.rejects) for s in data["dry_end_setups"])}
    assert default_store() is store
    assert store.boot_seconds < 15


def test_shift_kpis_reconcile_to_summed_setup_records(store, data):
    setups, stops, shifts = day_records(data, DAY)
    analysis = store.day_analysis(DAY)
    try:
        assert analysis.day == DAY and analysis.store is store and not analysis.errors
        for shift in (1, 2, 3):
            mine = [s for s in setups if s.shift_number == shift]
            record = next(sh for sh in shifts if sh.shift_number == shift)
            row = analysis.get_shift_kpis(shift)["rows"][0]
            feet = sum(s.lineal_ft for s in mine)
            gross = sum(s.total_produced_sqft for s in mine)
            assert row["valid_setups"] == len(mine)
            assert row["paper_changes"] == len({s.wet_end_id for s in mine}) == len(record.wet_end_ids)
            assert row["observed_lineal_ft"] == pytest.approx(feet) and feet == pytest.approx(record.total_lineal_ft, abs=0.1)
            assert row["observed_shift_fpm"] == pytest.approx(feet / 480)
            assert row["gross_sqft"] == pytest.approx(gross) and gross == pytest.approx(record.total_produced_sqft, abs=0.1)
            assert row["trim_pct"] == pytest.approx(sum(s.trim_sqft for s in mine) / gross * 100)
            assert row["shear_pct"] == pytest.approx(sum(s.chop_sqft for s in mine) / gross * 100)
            assert row["dry_end_pct"] == pytest.approx(sum(s.kqf_sqft for s in mine) / gross * 100)
            assert row["speed_to_target_pct"] == pytest.approx(
                feet / sum(GRADES[s.quality_id] * s.elapsed_seconds / 60 for s in mine) * 100)
            by_kind = defaultdict(float)
            for st in stops:
                if st.shift_number == shift:
                    by_kind[st.kind] += st.duration_sec
            assert row["maintenance_pct"] == pytest.approx(by_kind["Maintenance"] / 28800 * 100)
            assert row["operator_pct"] == pytest.approx(by_kind["Operator"] / 28800 * 100)
            assert row["pm_pct"] == pytest.approx(by_kind["PM"] / 28800 * 100)
            assert row["downtime_pct"] == pytest.approx(sum(by_kind.values()) / 28800 * 100)
            assert sorted(row["source_ids"]) == sorted(s.setup_id for s in mine)
    finally:
        analysis.close()


def test_split_setups_sit_in_the_shift_their_half_falls_in(store, data):
    setups, _, _ = day_records(data, DAY)
    split = [s for s in setups if s.change_code == "S"]
    assert split, "the chosen day has no shift-split setup"
    analysis = store.day_analysis(DAY)
    try:
        for s in split:
            parent = next(p for p in setups if p.setup_id == s.split_from_setup_id)
            assert parent.shift_number == s.shift_number - 1
            ids = {r["setup_id"] for r in analysis.get_setup_matrix(s.shift_number)["rows"]}
            other = {r["setup_id"] for r in analysis.get_setup_matrix(parent.shift_number)["rows"]}
            assert s.setup_id in ids and s.setup_id not in other
            assert parent.setup_id in other and parent.setup_id not in ids
            fragments = [r for r in analysis.get_order_matrix(s.shift_number)["rows"] if r["record_id"] == s.setup_id]
            assert fragments and all(r["setup_id"] == parent.setup_id for r in fragments)
    finally:
        analysis.close()


def test_stops_orders_and_rejects_map_field_by_field(store, data):
    setups, stops, shifts = day_records(data, DAY)
    analysis = store.day_analysis(DAY)
    try:
        for shift in (1, 2, 3):
            mine = [st for st in stops if st.shift_number == shift]
            grouped = defaultdict(lambda: [0.0, 0])
            for st in mine:
                grouped[(st.kind, st.place, st.reason)][0] += st.duration_sec / 60
                grouped[(st.kind, st.place, st.reason)][1] += 1
            by_place = defaultdict(float)
            for st in mine:
                by_place[st.place] += st.duration_sec / 60
            rows = analysis.get_downtime_breakdown(shift, group_by="place")["rows"]
            assert {r["category"]: r["down_minutes"] for r in rows} == pytest.approx(dict(by_place))
            assert sum(len(r["source_ids"]) for r in rows) == len(mine)
            notes = analysis.get_shift_notes(shift)["rows"]
            crew = next(sh for sh in shifts if sh.shift_number == shift).group_name
            assert notes[0]["record_type"] == "Shift" and crew in notes[0]["notes"]
            assert [n["record_id"] for n in notes[1:]] == [st.stop_id for st in sorted(mine, key=lambda st: st.start)]
            quality = {r["reason"]: r["rejected_sqft"] for r in analysis.get_quality_breakdown(shift)["rows"]}
            expected = defaultdict(float)
            for s in setups:
                if s.shift_number == shift:
                    for r in s.rejects:
                        expected[r.reason] += r.sqft
            assert quality == pytest.approx(dict(expected))
            orders = analysis.get_order_matrix(shift)["rows"]
            customers = [(s, c) for s in setups if s.shift_number == shift for c in s.customers]
            assert len(orders) == len(customers)
            for row, (s, c) in zip(sorted(orders, key=lambda r: (r["record_id"], r["knife"])),
                                   sorted(customers, key=lambda sc: (sc[0].setup_id, sc[1].cutoff_allocation))):
                assert row["order_id"] == c.order_number and row["knife"] == f"knife {c.cutoff_allocation}"
                assert (row["width_in"], row["length_in"], row["outs"], row["cuts"]) == (c.sheet_width_in, c.sheet_length_in, c.number_of_outs, c.actual_cuts)
                assert row["planned_sheets"] == c.nominal_cuts * c.number_of_outs
                assert row["produced_sheets"] == c.actual_cuts * c.number_of_outs
                assert row["requested_sheets"] is None and row["remaining_after"] is None and row["planned_overrun_sheets"] is None
    finally:
        analysis.close()


def test_corrupt_flag_injects_the_bad_trim_record(store, data):
    setups, _, _ = day_records(data, DAY)
    first = min((s for s in setups if s.shift_number == 2), key=lambda s: s.start)
    bad = store.day_analysis(DAY, corrupt=True)
    clean = store.day_analysis(DAY)
    try:
        result = bad.get_shift_kpis(2)
        assert result["coverage"] == "incomplete" and bad.get_shift_kpis(1)["coverage"] == "complete"
        assert result["rows"][0]["valid_setups"] == clean.get_shift_kpis(2)["rows"][0]["valid_setups"] - 1
        [error] = result["excluded_records"]
        assert error["record_id"] == first.setup_id and error["shift"] == 2
        assert error["raw"]["trim_sqft"] == CORRUPT_TRIM_SQFT and error["raw"]["lineal_ft"] == first.lineal_ft
        assert "Waste/gross" in error["error"]
        assert bad.get_rankings(2)["rows"] == [] and clean.get_rankings(2)["rows"]
        assert bad.version != clean.version
        assert clean.version == store.day_analysis(DAY).version
    finally:
        bad.close()
        clean.close()


@pytest.mark.parametrize("day, message", [
    ("2026-09-12", "Saturday"), ("2026-09-13", "Sunday"), ("2026-07-31", "outside the dataset"),
    ("2026-10-01", "outside the dataset"), ("14-09-2026", "YYYY-MM-DD"), (20260914, "YYYY-MM-DD")])
def test_weekend_and_out_of_range_days_are_refused(store, day, message):
    with pytest.raises(ValueError, match=message):
        store.day_analysis(day)


def test_every_production_day_loads_clean(store):
    for day in store.production_days[::6]:
        analysis = store.day_analysis(day)
        try:
            assert not analysis.errors
            assert all(analysis.get_shift_kpis(s)["rows"][0]["valid_setups"] > 0 for s in (1, 2, 3))
        finally:
            analysis.close()


def test_other_months_build_their_own_calendar():
    small = RecordStore(seed=5, months=(("2026", 9),))
    try:
        assert small.production_days[0] == "2026-09-01" and small.production_days[-1] == "2026-09-30"
        assert small.content_hash != default_store().content_hash
        with pytest.raises(ValueError, match="outside the dataset"):
            small.day_analysis("2026-08-31")
    finally:
        small.close()
