"""Issue #19: the synthetic window is built once (image build) and loaded at boot.

The prebuilt folder is an accelerator, never a source of truth for the records: a
store loaded from it must be indistinguishable from a generated one, and anything
stale or missing must fall back to generating. Since issue #35 it also fixes the
image's window: a store with no INVESTIGATOR_AS_OF takes the folder's window.

The equivalence and fallback checks use one month (fast); the boot budget is
measured on the full year the image ships."""
import json
import os
import subprocess
import sys
import time
from datetime import date

import duckdb
import pytest

from investigator import record_store
from investigator.agent import Agent, Context
from investigator.record_store import (RecordStore, TABLES, build_prebuilt, dataset_window, default_store, read_prebuilt,
                                       DEFAULT_SEED, DEFAULT_MACHINE)

MONTH = (date(2026, 9, 1), date(2026, 9, 30))   # the small window of the equivalence and fallback checks
YEAR = dataset_window()                           # the year ending INVESTIGATOR_AS_OF (tests/conftest.py)


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    where = tmp_path_factory.mktemp("prebuilt")
    manifest = build_prebuilt(where, window=MONTH)
    assert set(manifest["counts"]) == set(TABLES)
    assert manifest["window"] == ["2026-09-01", "2026-09-30"] and (manifest["first_day"], manifest["last_day"]) == ("2026-09-01", "2026-09-30")
    assert (where / "store.duckdb").is_file() and (where / "records.pkl").is_file() and (where / "manifest.json").is_file()
    return where


@pytest.fixture(scope="module")
def year_folder(tmp_path_factory):
    """The full year, as the image builds it; the build is timed (issue #35: under 2 minutes)."""
    where = tmp_path_factory.mktemp("prebuilt-year")
    started = time.perf_counter()
    manifest = build_prebuilt(where, window=YEAR)
    manifest["build_seconds"] = time.perf_counter() - started
    return where, manifest


@pytest.fixture(scope="module")
def generated():
    store = RecordStore(window=MONTH, prebuilt="")   # an empty argument means: ignore the environment, generate
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
    store = RecordStore(window=MONTH, prebuilt=folder)
    try:
        assert store.boot_source == "prebuilt" and generated.boot_source == "generated"
        assert store.content_hash == generated.content_hash
        assert store.counts == generated.counts
        assert store.production_days == generated.production_days
        for table in (*TABLES, "shift_kpis"):   # shift_kpis: built at image build, loaded, never recomputed (#34)
            assert _rows(store, table) == _rows(generated, table), table
        assert store._db.execute("SELECT count(*) FROM shift_kpis").fetchone()[0] == 2 * len(store.production_days) * 3
        # the data browser pages by rowid (issue #142): the prebuilt load keeps the order the store wrote, row for row,
        # so a page is the same rows on the image as on a generated store (tests/test_datapages.py holds that order)
        for table in (*TABLES, "shift_kpis"):
            written = f"SELECT * FROM {table} ORDER BY rowid"
            assert store._db.execute(written).fetchall() == generated._db.execute(written).fetchall(), table
        from investigator import datapages
        for table, query in (("stops", "page=2"), ("rejects", "family=warp&page=3"), ("setups", "crew=Crew%20A")):
            assert datapages.page(store, table, query)["rows"] == datapages.page(generated, table, query)["rows"], (table, query)
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


def test_prebuilt_year_builds_in_budget_and_boots_fast(year_folder, latency_budget):
    """Issue #19 asked for default_store() under 200 ms with the prebuilt folder over two months;
    issue #35 kept image boot under 300 ms over a year and the build under 2 minutes; issue #100
    (the data from 1 January 2025, about 1.75 times the year, and the record contract's larger
    tables) expects roughly double: boot under 500 ms (owner, 2026-11-11). Measured here: about
    30 s to build and 350-400 ms to boot (the contract tables and shift_kpis are copied in; the
    record objects stay pickled per day until a day is read), against about 25 s to generate.
    The relative bound (under a twentieth of the generated boot) holds the claim on any machine;
    the absolute ones are the issue's budgets, scaled on a CI runner by latency_budget (issue #139)."""
    where, manifest = year_folder
    assert manifest["build_seconds"] < latency_budget(120), manifest["build_seconds"]
    assert manifest["window"] == [YEAR[0].isoformat(), YEAR[1].isoformat()] and manifest["production_days"] >= 450
    prebuilt = min((_fresh_process_boot({"INVESTIGATOR_PREBUILT": str(where)}) for _ in range(3)), key=lambda r: r["ms"])
    generated = default_store()   # this process's store: the same year, generated (conftest unsets INVESTIGATOR_PREBUILT)
    print(f"year: built in {manifest['build_seconds']:.1f} s; fresh-process default_store(): prebuilt {prebuilt['ms']} ms, "
          f"generated {generated.boot_seconds * 1000:.0f} ms")
    assert prebuilt["source"] == "prebuilt" and generated.boot_source == "generated"
    assert prebuilt["ms"] < latency_budget(500), prebuilt
    assert prebuilt["ms"] < generated.boot_seconds * 1000 / 20


def test_prebuilt_records_are_unpickled_one_day_at_a_time(year_folder):
    """The record objects of a year cost about 600 ms to unpickle at once; a prebuilt store holds
    them as one pickled blob per day and unpickles a day when it is first read, keeping at most
    DAY_CACHE days. Answers are identical to the generated store's."""
    where, manifest = year_folder
    store = RecordStore(prebuilt=where)   # no window argument: INVESTIGATOR_AS_OF's, which the folder was built for
    try:
        assert store.boot_source == "prebuilt" and store.content_hash == manifest["content_hash"] == default_store().content_hash
        assert store._by_day == {} and len(store._day_blobs) == manifest["production_days"]
        a, b = store.day_analysis("2025-11-17"), default_store().day_analysis("2025-11-17")
        try:
            assert list(store._by_day) == ["2025-11-17"]
            for tool, args in (("get_shift_kpis", (1,)), ("get_downtime_breakdown", (2, None, "reason"))):
                assert json.dumps(_normalized(getattr(a, tool)(*args)), sort_keys=True, default=str) == \
                    json.dumps(_normalized(getattr(b, tool)(*args)), sort_keys=True, default=str), tool
        finally:
            a.close()
            b.close()
        for day in store.production_days[: record_store.DAY_CACHE + 5]:
            store.day_analysis(day).close()
        assert len(store._by_day) == record_store.DAY_CACHE
    finally:
        store.close()


def test_a_store_without_as_of_takes_the_prebuilt_window(folder, monkeypatch):
    """The image's window is fixed when the image is built: a container that starts days later
    (scale from zero) must not move it, or the folder would no longer match and it would generate."""
    monkeypatch.delenv("INVESTIGATOR_AS_OF", raising=False)
    assert dataset_window(prebuilt=folder) == MONTH
    assert dataset_window(prebuilt="", today=date(2026, 11, 10)) == (date(2025, 1, 1), date(2026, 11, 9))   # else: to yesterday
    monkeypatch.setenv("INVESTIGATOR_PREBUILT", str(folder))
    store = RecordStore()
    try:
        assert store.boot_source == "prebuilt" and store.window == MONTH and store.as_of == date(2026, 9, 30)
    finally:
        store.close()
    monkeypatch.setenv("INVESTIGATOR_AS_OF", "2026-09-30")   # an explicit as-of wins over the folder
    assert dataset_window(prebuilt=folder) == YEAR
    monkeypatch.setenv("INVESTIGATOR_AS_OF", "30 September")
    with pytest.raises(ValueError, match="INVESTIGATOR_AS_OF must be a YYYY-MM-DD date"):
        dataset_window()


def test_stale_or_missing_prebuilt_falls_back_to_generating(folder, tmp_path, monkeypatch):
    assert read_prebuilt(folder, DEFAULT_SEED, MONTH, DEFAULT_MACHINE) is not None
    # other inputs than the folder was built for: the seed, the window (shifted by one day), the machine
    assert read_prebuilt(folder, DEFAULT_SEED + 1, MONTH, DEFAULT_MACHINE) is None
    assert read_prebuilt(folder, DEFAULT_SEED, (MONTH[0], date(2026, 10, 1)), DEFAULT_MACHINE) is None
    assert read_prebuilt(folder, DEFAULT_SEED, MONTH, "CORR-9") is None
    # no folder at all
    assert read_prebuilt(tmp_path / "nowhere", DEFAULT_SEED, MONTH, DEFAULT_MACHINE) is None
    # the generator changed since the folder was built: the fingerprint no longer matches
    stale = tmp_path / "stale"
    stale.mkdir()
    for name in ("manifest.json", "records.pkl", "store.duckdb"):
        stale.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    manifest = json.loads((stale / "manifest.json").read_text())
    manifest["generator_fingerprint"] = "0" * 64
    (stale / "manifest.json").write_text(json.dumps(manifest))
    assert read_prebuilt(stale, DEFAULT_SEED, MONTH, DEFAULT_MACHINE) is None
    # a truncated pickle
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in ("manifest.json", "store.duckdb"):
        broken.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    (broken / "records.pkl").write_bytes(folder.joinpath("records.pkl").read_bytes()[:1000])
    assert read_prebuilt(broken, DEFAULT_SEED, MONTH, DEFAULT_MACHINE) is None
    # a RecordStore pointed at a bad folder still boots, by generating
    store = RecordStore(window=MONTH, prebuilt=broken)
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
    store = RecordStore(window=MONTH, prebuilt=cut)
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
        for table in (*TABLES, "shift_kpis"):
            db.execute(f"CREATE TABLE {table} AS SELECT * FROM good.{table}")
        db.execute("ALTER TABLE setups DROP COLUMN split_from")
        db.execute("CHECKPOINT")
    finally:
        db.close()
    store = RecordStore(window=MONTH, prebuilt=drift)
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
        for table in (*TABLES, "shift_kpis"):
            db.execute(f"CREATE TABLE {table} AS SELECT * FROM good.{table}")
        db.execute("DELETE FROM stops WHERE stop_id = (SELECT min(stop_id) FROM stops)")
        db.execute("CHECKPOINT")
    finally:
        db.close()
    store = RecordStore(window=MONTH, prebuilt=short)
    try:
        assert store.boot_source == "generated"
    finally:
        store.close()
    # issue #34: the derived shift_kpis table is checked like the six. A manifest that does not
    # record its row count (an older format), a file without the table, or a row missing: generate,
    # and the generated store builds its own shift_kpis.
    unnumbered = tmp_path / "unnumbered"
    unnumbered.mkdir()
    for name in ("records.pkl", "store.duckdb"):
        unnumbered.joinpath(name).write_bytes(folder.joinpath(name).read_bytes())
    manifest = json.loads((folder / "manifest.json").read_text())
    del manifest["shift_kpis_rows"]
    (unnumbered / "manifest.json").write_text(json.dumps(manifest))
    assert read_prebuilt(unnumbered, DEFAULT_SEED, MONTH, DEFAULT_MACHINE) is None
    for name, damage in (("no_kpis", None), ("short_kpis", "DELETE FROM shift_kpis WHERE shift = 3 AND corrupt")):
        bad = tmp_path / name
        bad.mkdir()
        for file in ("manifest.json", "records.pkl"):
            bad.joinpath(file).write_bytes(folder.joinpath(file).read_bytes())
        db = duckdb.connect(str(bad / "store.duckdb"))
        try:
            db.execute(f"ATTACH '{folder / 'store.duckdb'}' AS good (READ_ONLY)")
            for table in (*TABLES, *(("shift_kpis",) if damage else ())):
                db.execute(f"CREATE TABLE {table} AS SELECT * FROM good.{table}")
            if damage:
                db.execute(damage)
            db.execute("CHECKPOINT")
        finally:
            db.close()
        store = RecordStore(window=MONTH, prebuilt=bad)
        try:
            assert store.boot_source == "generated", name
            assert store._db.execute("SELECT count(*) FROM shift_kpis").fetchone()[0] == 2 * 3 * len(store.production_days), name
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
    store = RecordStore(window=MONTH)
    try:
        assert store.boot_source == "prebuilt"
    finally:
        store.close()
