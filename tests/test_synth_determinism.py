"""Determinism, serialisation round trip, speed and legacy isolation of synth."""
import json
import subprocess
import time
from pathlib import Path
import pytest
from investigator import records as R
from investigator.analysis import Analysis
from investigator.fixture import generate
from investigator.synth import content_hash, generate_months, jsonl_bytes, read_jsonl, write_jsonl

REPO = Path(__file__).resolve().parents[1]
# Pinned hash of the default two months at seed 2026: byte-identical output
# is the determinism claim. Change this constant only with an intended
# generator change, and say so in the commit.
PINNED_HASH = "4b61a3c61953ec23f5c9ef10f398771ba28d19e1bd09852c57d81b0c8237634e"


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
    try:
        tracked = subprocess.run(["git", "ls-files", "*.jsonl", "**/manifest.json"], cwd=REPO,
                                 capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable")
    assert tracked.stdout.strip() == ""
