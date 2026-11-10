"""Issue #19: the two synthetic months are built once (image build) and loaded at boot.

The prebuilt folder is an accelerator, never a source of truth: a store loaded from
it must be indistinguishable from a generated one, and anything stale or missing
must fall back to generating."""
import json
import os
import subprocess
import sys

import duckdb
import pytest

from investigator.agent import Agent, Context
from investigator.record_store import RecordStore, TABLES, build_prebuilt, read_prebuilt, DEFAULT_SEED, DEFAULT_MONTHS, DEFAULT_MACHINE


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    where = tmp_path_factory.mktemp("prebuilt")
    manifest = build_prebuilt(where)
    assert set(manifest["counts"]) == set(TABLES)
    assert (where / "store.duckdb").is_file() and (where / "records.pkl").is_file() and (where / "manifest.json").is_file()
    return where


@pytest.fixture(scope="module")
def generated():
    store = RecordStore(prebuilt="")   # an empty argument means: ignore the environment, generate
    yield store
    store.close()


def _normalized(value):
    """Drop what differs between any two runs even of the same store: per-run result ids and timings."""
    if isinstance(value, dict):
        return {k: _normalized(v) for k, v in value.items() if not (k.endswith("result_id") or k == "elapsed_ms")}
    if isinstance(value, list):
        return [_normalized(v) for v in value]
    return value


def _rows(store, table):
    columns = [c[0] for c in store._db.execute(f"DESCRIBE {table}").fetchall()]
    return store._db.execute(f"SELECT * FROM {table} ORDER BY {', '.join(columns)}").fetchall()


def test_prebuilt_store_is_identical_to_a_generated_one(folder, generated):
    store = RecordStore(prebuilt=folder)
    try:
        assert store.boot_source == "prebuilt" and generated.boot_source == "generated"
        assert store.content_hash == generated.content_hash
        assert store.counts == generated.counts
        assert store.production_days == generated.production_days
        for table in (*TABLES, "shift_kpis"):   # shift_kpis: built at image build, loaded, never recomputed (#34)
            assert _rows(store, table) == _rows(generated, table), table
        assert store._db.execute("SELECT count(*) FROM shift_kpis").fetchone()[0] == 2 * len(store.production_days) * 3
        # the per-day objects drive the Analysis: an answer must be byte-identical either way
        day = "2026-09-14"
        a, b = store.day_analysis(day), generated.day_analysis(day)
        try:
            for question in ("Why was shift 2 slow?", "Show the downtime breakdown", "What was the waste by reason?"):
                x = Agent(a).ask(question, Context(day, 2, period="day"))
                y = Agent(b).ask(question, Context(day, 2, period="day"))
                for key in ("sections", "charts", "tables", "trace", "status"):
                    assert json.dumps(_normalized(x.get(key)), sort_keys=True, default=str) == json.dumps(_normalized(y.get(key)), sort_keys=True, default=str), (question, key)
        finally:
            a.close()
            b.close()
    finally:
        store.close()


BOOT_SCRIPT = '''
import json, sys, time
from investigator.record_store import default_store
started = time.perf_counter()
store = default_store()
print(json.dumps({"source": store.boot_source, "ms": round((time.perf_counter() - started) * 1000)}))
'''


def _fresh_process_boot(env):
    """default_store() timed in a fresh interpreter, which is what a container start pays."""
    run = subprocess.run([sys.executable, "-c", BOOT_SCRIPT], capture_output=True, text=True, check=True,
                         env={**os.environ, **env})
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_prebuilt_boot_is_fast(folder):
    """Issue #19 asks for default_store() under 200 ms with the prebuilt folder. Measured
    here: 140-160 ms prebuilt against about 1,900 ms generated. The absolute bound is
    300 ms so a slower CI runner does not flake; the relative bound (under a quarter of
    the generated boot) holds the real claim on any machine."""
    prebuilt = min((_fresh_process_boot({"INVESTIGATOR_PREBUILT": str(folder)}) for _ in range(3)), key=lambda r: r["ms"])
    generated = _fresh_process_boot({"INVESTIGATOR_PREBUILT": ""})
    print(f"fresh-process default_store(): prebuilt {prebuilt['ms']} ms, generated {generated['ms']} ms")
    assert prebuilt["source"] == "prebuilt" and generated["source"] == "generated"
    assert prebuilt["ms"] < 300
    assert prebuilt["ms"] < generated["ms"] / 4


def test_stale_or_missing_prebuilt_falls_back_to_generating(folder, tmp_path, monkeypatch):
    assert read_prebuilt(folder, DEFAULT_SEED, DEFAULT_MONTHS, DEFAULT_MACHINE) is not None
    # other inputs than the folder was built for
    assert read_prebuilt(folder, DEFAULT_SEED + 1, DEFAULT_MONTHS, DEFAULT_MACHINE) is None
    assert read_prebuilt(folder, DEFAULT_SEED, DEFAULT_MONTHS[:1], DEFAULT_MACHINE) is None
    assert read_prebuilt(folder, DEFAULT_SEED, DEFAULT_MONTHS, "CORR-9") is None
    # no folder at all
    assert read_prebuilt(tmp_path / "nowhere", DEFAULT_SEED, DEFAULT_MONTHS, DEFAULT_MACHINE) is None
    # the generator changed since the folder was built: the fingerprint no longer matches
    stale = tmp_path / "stale"
    stale.mkdir()
    for name in ("manifest.json", "records.pkl", "store.duckdb"):
        stale.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    manifest = json.loads((stale / "manifest.json").read_text())
    manifest["generator_fingerprint"] = "0" * 64
    (stale / "manifest.json").write_text(json.dumps(manifest))
    assert read_prebuilt(stale, DEFAULT_SEED, DEFAULT_MONTHS, DEFAULT_MACHINE) is None
    # a truncated pickle
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in ("manifest.json", "store.duckdb"):
        broken.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    (broken / "records.pkl").write_bytes(folder.joinpath("records.pkl").read_bytes()[:1000])
    assert read_prebuilt(broken, DEFAULT_SEED, DEFAULT_MONTHS, DEFAULT_MACHINE) is None
    # a RecordStore pointed at a bad folder still boots, by generating
    store = RecordStore(prebuilt=broken)
    try:
        assert store.boot_source == "generated" and store.counts["setups"] > 0
    finally:
        store.close()
    # a truncated store.duckdb: the manifest and pickle pass, the attach fails, the store generates
    cut = tmp_path / "cut"
    cut.mkdir()
    for name in ("manifest.json", "records.pkl"):
        cut.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    (cut / "store.duckdb").write_bytes(folder.joinpath("store.duckdb").read_bytes()[:4096])
    store = RecordStore(prebuilt=cut)
    try:
        assert store.boot_source == "generated" and store.counts["setups"] > 0
    finally:
        store.close()
    # a store.duckdb whose schema no longer matches TABLES (a column dropped): the insert fails, the store generates
    drift = tmp_path / "drift"
    drift.mkdir()
    for name in ("manifest.json", "records.pkl"):
        drift.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    db = duckdb.connect(str(drift / "store.duckdb"))
    try:
        db.execute(f"ATTACH '{folder / 'store.duckdb'}' AS good (READ_ONLY)")
        for table in TABLES:
            db.execute(f"CREATE TABLE {table} AS SELECT * FROM good.{table}")
        db.execute("ALTER TABLE setups DROP COLUMN split_from")
        db.execute("CHECKPOINT")
    finally:
        db.close()
    store = RecordStore(prebuilt=drift)
    try:
        assert store.boot_source == "generated"
        assert store.counts == json.loads((folder / "manifest.json").read_text())["counts"]   # nothing half-loaded was left behind
    finally:
        store.close()
    # a store.duckdb with the right schema but a row missing: the manifest counts catch it
    short = tmp_path / "short"
    short.mkdir()
    for name in ("manifest.json", "records.pkl"):
        short.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    db = duckdb.connect(str(short / "store.duckdb"))
    try:
        db.execute(f"ATTACH '{folder / 'store.duckdb'}' AS good (READ_ONLY)")
        for table in TABLES:
            db.execute(f"CREATE TABLE {table} AS SELECT * FROM good.{table}")
        db.execute("DELETE FROM stops WHERE stop_id = (SELECT min(stop_id) FROM stops)")
        db.execute("CHECKPOINT")
    finally:
        db.close()
    store = RecordStore(prebuilt=short)
    try:
        assert store.boot_source == "generated"
    finally:
        store.close()
    # the fingerprint covers every module in the package (the seeded answer keys and the
    # row builder included), so any source change invalidates a folder built before it
    from investigator import record_store as module
    source = module.generator_fingerprint()
    package = module.Path(module.__file__).parent
    for name in ("synth.py", "records.py", "record_store.py"):
        target = package / name
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# probe\n")
            assert module.generator_fingerprint() != source, name
        finally:
            target.write_bytes(original)
    assert module.generator_fingerprint() == source
    assert len(list(package.glob("*.py"))) > 10   # the glob is doing real work
    # the environment variable is the production switch
    monkeypatch.setenv("INVESTIGATOR_PREBUILT", str(folder))
    store = RecordStore()
    try:
        assert store.boot_source == "prebuilt"
    finally:
        store.close()
