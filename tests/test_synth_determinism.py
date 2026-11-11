"""Determinism, serialisation round trip, speed and legacy isolation of synth."""
import json
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path
import pytest
from investigator import records as R
from investigator.analysis import Analysis
from investigator.fixture import generate
from investigator.record_store import TABLES, RecordStore
from investigator.synth import (content_hash, generate_day, generate_days, generate_months, generate_window, jsonl_bytes,
                                production_days, read_jsonl, window_days, write_jsonl)

REPO = Path(__file__).resolve().parents[1]
# Pinned hash of August and September 2026 at seed 2026: byte-identical output
# is the determinism claim. Change this constant only with an intended
# generator change, and say so in the commit. (Issue #35, per-day seeding:
# was d9143ff1d15029b5992069d664eb8430d3e0aaed617b82a953f84e0a4964e6c5.)
PINNED_HASH = "b07c2a7933dbb0339b843d9e1a78f36f3c1b544c3c37135f94da3092aecb38c7"


def day_bytes(data, day):
    """Every family's JSONL bytes for one production day (YYYYMMDD) of a generated range."""
    return {name: jsonl_bytes([r for r in data[name] if r.production_date == day]) for name in R.FAMILIES}


def test_same_seed_is_byte_identical_and_fast(tmp_path):
    started = time.perf_counter()
    first = generate_months()
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0, elapsed
    second = generate_months(seed=2026, months=(("2026", 8), ("2026", 9)), machine_id="CORR-1")
    assert first["content_hash"] == second["content_hash"] == content_hash(first)
    write_jsonl(first, tmp_path / "a")
    write_jsonl(second, tmp_path / "b")
    for name in R.FAMILIES:
        assert (tmp_path / "a" / f"{name}.jsonl").read_bytes() == (tmp_path / "b" / f"{name}.jsonl").read_bytes()
        assert (tmp_path / "a" / f"{name}.jsonl").read_bytes() == jsonl_bytes(first[name])
    assert first["manifest"]["counts"] == {name: len(first[name]) for name in R.FAMILIES}
    assert first["manifest"]["schema"] == R.SCHEMA


def test_pinned_content_hash():
    data = generate_months()
    assert data["content_hash"] == PINNED_HASH, data["content_hash"]


def test_two_ranges_that_share_a_day_produce_identical_records_for_it():
    """Issue #35: every draw of a day comes from streams keyed by the seed and dates, and nothing
    carries from one production day into the next, so a day is byte-identical in any range that
    contains it: a month, four months, a range that starts or ends on it, or the day alone."""
    september = generate_months(months=(("2026", 9),))
    summer = generate_days(production_days((("2026", 6), ("2026", 7), ("2026", 8))) + production_days((("2026", 9),))[:10])
    for day in ("20260901", "20260907", "20260914"):   # the first day of one range; the splice-story Monday; the last day of the other
        moment = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        alone = generate_day(moment)
        assert all(alone[name] for name in R.FAMILIES), day
        assert day_bytes(september, day) == day_bytes(summer, day) == {name: jsonl_bytes(alone[name]) for name in R.FAMILIES}, day
    assert generate_day(date(2026, 9, 14)) == generate_day(date(2026, 9, 14))   # and across two runs
    with pytest.raises(ValueError, match="weekend"):
        generate_day(date(2026, 9, 13))


def test_a_window_shift_keeps_every_shared_day_byte_identical():
    """The nightly deploy moves the window by a day: the days the two windows share must not move.
    A 30-day span keeps the test fast; the year is the same rule with DATASET_SPAN_DAYS."""
    today, tomorrow = generate_window(date(2026, 9, 29), span_days=30), generate_window(date(2026, 9, 30), span_days=30)
    shared = sorted({d.strftime("%Y%m%d") for d in window_days(date(2026, 9, 29), 30)} & {d.strftime("%Y%m%d") for d in window_days(date(2026, 9, 30), 30)})
    assert len(shared) >= 20 and today["content_hash"] != tomorrow["content_hash"]
    for day in shared:
        assert day_bytes(today, day) == day_bytes(tomorrow, day), day
    assert window_days(date(2026, 9, 30))[0] == date(2025, 9, 30) and window_days(date(2026, 9, 30))[-1] == date(2026, 9, 30)
    assert len(window_days(date(2026, 9, 30))) == 262


def test_two_store_windows_hold_identical_rows_for_a_shared_day():
    """The same claim at the store: two RecordStores over different windows hold the same rows of
    every table, and the same precomputed shift_kpis, for a day both contain."""
    a = RecordStore(window=(date(2026, 9, 1), date(2026, 9, 30)), prebuilt="")
    b = RecordStore(window=(date(2026, 9, 10), date(2026, 10, 9)), prebuilt="")
    try:
        for table in (*TABLES, "shift_kpis"):
            columns = [c[0] for c in a._db.execute(f"DESCRIBE {table}").fetchall()]
            sql = f"SELECT * FROM {table} WHERE production_date = DATE '2026-09-14' ORDER BY {', '.join(columns)}"
            rows = a._db.execute(sql).fetchall()
            assert rows and rows == b._db.execute(sql).fetchall(), table
        x, y = a.day_analysis("2026-09-14"), b.day_analysis("2026-09-14")
        try:
            assert x.get_shift_kpis(2)["rows"] == y.get_shift_kpis(2)["rows"]
        finally:
            x.close()
            y.close()
    finally:
        a.close()
        b.close()


def test_order_numbers_are_unique_across_days():
    """Each day numbers its own orders, prefixed with the day, so a range never repeats one."""
    data = generate_months(months=(("2026", 9),))
    days_of = {}
    for setup in data["dry_end_setups"]:
        for c in setup.customers:
            assert c.order_number.startswith("SO-" + setup.production_date[2:]), (c.order_number, setup.production_date)
            days_of.setdefault(c.order_number, set()).add(setup.production_date)
    assert all(len(days) == 1 for days in days_of.values()) and len(days_of) > 22 * 100


def test_other_seed_or_machine_changes_the_hash():
    base = generate_months(months=(("2026", 8),))
    assert generate_months(seed=7, months=(("2026", 8),))["content_hash"] != base["content_hash"]
    assert generate_months(months=(("2026", 8),), machine_id="CORR-2")["content_hash"] != base["content_hash"]
    assert {s.production_date[:6] for s in base["dry_end_setups"]} == {"202608"}


def test_jsonl_round_trip_restores_equal_records(tmp_path):
    data = generate_months(months=(("2026", 9),))
    write_jsonl(data, tmp_path)
    loaded = read_jsonl(tmp_path)
    assert loaded["content_hash"] == data["content_hash"]
    for name in R.FAMILIES:
        assert loaded[name] == data[name]
        assert all(isinstance(r, R.FAMILIES[name]) for r in loaded[name])
    line = json.loads((tmp_path / "dry_end_setups.jsonl").read_text().splitlines()[0])
    assert set(line) == {f.name for f in R.DryEndSetupRecord.__dataclass_fields__.values()}
    assert isinstance(line["customers"][0], dict) and isinstance(line["rejects"], list)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["content_hash"] == data["content_hash"]


def test_tampered_files_fail_the_manifest_hash(tmp_path):
    write_jsonl(generate_months(months=(("2026", 9),)), tmp_path)
    path = tmp_path / "stops.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"duration_sec":', b'"duration_sec":1', 1))
    with pytest.raises(ValueError, match="content hash"):
        read_jsonl(tmp_path)


def test_legacy_fixture_and_analysis_are_untouched():
    data = generate()
    analysis = Analysis(data)
    try:
        assert not analysis.errors
        assert analysis.get_shift_kpis(1)["rows"][0]["valid_setups"] > 0
    finally:
        analysis.close()
    assert generate() == data


def test_no_generated_record_files_are_tracked():
    """Generated synthetic record files live under artifacts/ and are never committed. The one
    committed .jsonl is the eval ledger, evals/history.jsonl: one line per `evals/run.py --track`
    run, which is a result, not a record file."""
    try:
        tracked = subprocess.run(["git", "ls-files", "*.jsonl", "**/manifest.json"], cwd=REPO,
                                 capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable")
    assert set(tracked.stdout.split()) - {"evals/history.jsonl"} == set()
