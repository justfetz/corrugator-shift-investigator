"""Issue #34: per-shift KPIs precomputed once; period reports and period tools are lookups.

The references are the implementations this replaced, frozen in tests/legacy_period_report.py:
the period report that rebuilt every day through `day_analysis`, and the period tools' sums
over the raw tables. Every tile must agree with them to 4 decimals.
"""
import re
import statistics
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from legacy_period_report import LegacyPeriodReports, legacy_sums  # noqa: E402
from investigator.periods import PeriodReports, shift_matrix_row  # noqa: E402
from investigator.record_store import KPI_CATALOG, SHIFT_KPIS, default_store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Fields that name a run or time it, not a tile: they differ between any two runs.
VOLATILE = {"run_id", "result_id", "source_result_id", "evidence_ids", "elapsed_ms"}
# The report's trace now names the lookup instead of the per-day query (#40 still holds: SQL plus bound values).
TRACE_ONLY = {"daily_sql", "daily_parameters", "trace", "calculation"}
REPORTS = [("week", "2026-08-03"), ("week", "2026-09-14"), ("week", "2026-09-29"),
           ("month", "2026-08-12"), ("month", "2026-09-14"),
           ("accounting", "2026-08-14"), ("accounting", "2026-09-14"), ("accounting", "2026-09-29")]
SCOPES = [(1, "shift"), (2, "shift"), (3, "shift"), (2, "day")]   # each shift alone, then all three


@pytest.fixture(scope="module")
def store():
    return default_store()


def assert_same(new, old, path="result"):
    """Equal structure; every number equal to 4 decimals; everything else exactly equal."""
    if isinstance(old, dict):
        keys = set(old) - VOLATILE - (TRACE_ONLY if path.endswith("data") else set())
        assert keys <= set(new), (path, keys - set(new))
        for key in keys:
            assert_same(new[key], old[key], f"{path}.{key}")
    elif isinstance(old, list):
        assert len(new) == len(old), (path, len(new), len(old))
        for i, (a, b) in enumerate(zip(new, old)):
            assert_same(a, b, f"{path}[{i}]")
    elif isinstance(old, float) and not isinstance(old, bool):
        assert isinstance(new, (int, float)) and round(new, 4) == round(old, 4), (path, new, old)
    else:
        assert new == old, (path, new, old)


def test_period_report_equals_the_rebuilt_report_for_every_period_and_shift_filter(store):
    new, old = PeriodReports(store), LegacyPeriodReports(store)
    checked = 0
    for period, anchor in REPORTS:
        for shift, scope in SCOPES:
            a, b = new.investigate(anchor, shift, scope, period), old.investigate(anchor, shift, scope, period)
            assert_same(a, b, f"{period} {anchor} {scope} {shift}")
            assert a["tables"][2]["rows"] and a["charts"][0]["rows"]   # the matrix and the heatmap were compared, not empty
            checked += 1
    # the data-quality demo: shift 2's first setup excluded each day, its shift incomplete
    for period, anchor, shift, scope in (("week", "2026-09-14", 2, "shift"), ("month", "2026-09-14", 2, "day")):
        a, b = new.investigate(anchor, shift, scope, period, corrupt=True), old.investigate(anchor, shift, scope, period, corrupt=True)
        assert a["status"] == "partial"
        assert_same(a, b, f"corrupt {period} {scope}")
        checked += 1
    assert checked == len(REPORTS) * len(SCOPES) + 2


def test_matrix_rows_equal_the_daily_kpi_tool_for_every_shift(store):
    """Each shift_kpis row reproduces analysis.get_shift_kpis for its day and shift, clean and corrupt:
    every day of August and September 2026 and every fifth day of the rest of the year (issue #35: a
    year of days, rebuilt through day_analysis twice, would cost half a minute)."""
    rows = store._fetch("SELECT * FROM shift_kpis ORDER BY production_date, shift, corrupt", [], limit=10_000)
    assert len(rows) == 2 * 3 * len(store.production_days)
    by_key = {(r["production_date"].isoformat(), r["shift"], r["corrupt"]): r for r in rows}
    rest = [d for d in store.production_days if not d.startswith(("2026-08", "2026-09"))]
    days = [d for d in store.production_days if d.startswith(("2026-08", "2026-09"))] + rest[::5]
    for corrupt in (False, True):
        for day in days:
            analysis = store.day_analysis(day, corrupt)
            try:
                for shift in (1, 2, 3):
                    daily = analysis.get_shift_kpis(shift)
                    row = by_key[(day, shift, corrupt)]
                    assert row["coverage"] == daily["coverage"], (day, shift, corrupt)
                    assert_same(shift_matrix_row(row), daily["rows"][0], f"{day} shift {shift} corrupt={corrupt}")
            finally:
                analysis.close()


def test_period_tools_equal_the_raw_table_sums(store):
    windows = [{"period": "day", "anchor": "2026-09-14"}, {"period": "day", "anchor": "2026-08-03"},
               {"period": "week", "anchor": "2026-09-14"}, {"period": "week", "anchor": "2026-09-29"},
               {"period": "month", "anchor": "2026-08-12"}, {"period": "month", "anchor": "2026-09-14"},
               {"period": "fiscal", "anchor": "2026-08-14"}, {"period": "fiscal", "anchor": "2026-09-14"},
               {"period": "fiscal", "anchor": "2026-09-29"}]
    for spec in windows:
        current = store._window(spec)
        for w in (current, store._previous(current)):
            for shift in (None, 1, 2, 3):
                trace = {"sql": [], "parameters": []}
                new, old = store._sums(w, shift, trace), legacy_sums(store, w, shift, {"sql": [], "parameters": []})
                assert trace["sql"][0].count("FROM shift_kpis") == 1   # the tiles are a lookup
                label = f"{spec} {w['start']} shift {shift}"
                assert_same({k: new[k] for k in old}, old, label)   # every raw sum, the source counts and the details too
                assert_tiles_same(store._kpis(new), store._kpis(old), label)


def assert_tiles_same(new, old, label):
    """The tiles of two sets of sums that agree to 4 decimals (checked just before): equal to 4 decimals,
    or one flip of the last displayed digit where a value sits on a rounding boundary and the two
    summation orders land either side of it (July against June, shift 2: lineal per paper change
    19165.9 and 19166.0; issue #100's regenerated records: speed with stops 604.5 and 604.6)."""
    def unit(value):   # the last displayed place of a tile rounded to 0-4 decimals
        return next((10.0 ** -k for k in range(5) if round(value, k) == value), 1e-4)
    assert set(new) == set(old), label
    for key, value in old.items():
        if isinstance(value, float) and isinstance(new[key], (int, float)):
            assert round(new[key], 4) == round(value, 4) or abs(new[key] - value) <= max(1e-4, abs(value) * 1e-5, unit(value) + 1e-9), \
                (label, key, new[key], value)
        else:
            assert new[key] == value, (label, key)


def _best_of(report, *args):
    """Median of three runs. No report cache remains, so every run pays the whole lookup."""
    times = []
    for _ in range(3):
        started = time.perf_counter()
        report.investigate(*args)
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


def test_period_reports_are_lookups(store):
    """Issue #34: month < 150 ms and week < 100 ms (they measured 2,120 ms and 614 ms when every
    day was rebuilt; about 18 ms and 10 ms as lookups)."""
    report = PeriodReports(store)
    for shift, scope in ((2, "shift"), (2, "day")):
        month, week = _best_of(report, "2026-09-14", shift, scope, "month"), _best_of(report, "2026-09-14", shift, scope, "week")
        accounting = _best_of(report, "2026-09-14", shift, scope, "accounting")
        print(f"{scope} {shift}: month {month:.1f} ms, week {week:.1f} ms, 4-4-5 {accounting:.1f} ms")
        assert month < 150 and accounting < 150, (scope, month, accounting)
        assert week < 100, (scope, week)


def _inventory():
    """The `shift_kpis` column table of docs/METRICS.md: [(column, type, formula row)]."""
    text = (ROOT / "docs" / "METRICS.md").read_text()
    section = text.split("## The shift_kpis table", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) == 3:
            rows.append((cells[0].strip("`"), cells[1].strip("`"), cells[2]))
    return rows


def test_metrics_inventory_matches_the_table(store):
    inventory = _inventory()
    described = [(name, kind) for name, kind, *_ in store._db.execute("DESCRIBE shift_kpis").fetchall()]
    assert [(name, kind) for name, kind, _ in inventory] == described == list(SHIFT_KPIS)
    # every measure column names its formula rows: tiles of the period tools or fields of the daily matrix
    named = {key for key, *_ in KPI_CATALOG} | set(shift_matrix_row({c: [] if c == "source_ids" else 0 for c, _ in SHIFT_KPIS}))
    for column, _, formula in inventory:
        if formula.startswith("key:"):
            continue
        tokens = re.findall(r"`([a-z_]+)`", formula)
        assert tokens, (column, "names no formula row")
        assert set(tokens) <= named, (column, set(tokens) - named)
