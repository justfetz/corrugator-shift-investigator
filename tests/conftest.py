"""Pin the dataset window for the whole test session (issue #35).

The app's dataset is the rolling year ending INVESTIGATOR_AS_OF (default: yesterday). Tests and the
golden set read the year ending 2026-09-30, the last day of the original two months, so the
expectations do not move with the calendar. Set before any test module imports the store; a test
that needs another window builds its own `RecordStore(window=...)`. Subprocesses inherit it.
"""
import importlib.util
import os
from pathlib import Path

import pytest

GOLDEN_AS_OF = "2026-09-30"   # evals/run.py GOLDEN_AS_OF; tests/test_goldens_values.py checks the two agree

os.environ["INVESTIGATOR_AS_OF"] = GOLDEN_AS_OF
os.environ.pop("INVESTIGATOR_PREBUILT", None)          # a developer's prebuilt folder must not decide the window
os.environ.pop("INVESTIGATOR_GOLDEN_PREBUILT", None)


@pytest.fixture(scope="session")
def latency_budget():
    """evals/run.py's latency_budget (issue #139): a speed budget a test asserts, times INVESTIGATOR_LATENCY_SCALE
    (CI sets it in .github/workflows/tests.yml; unset it is 1). The same helper the runner's per-case budgets go
    through, so every latency assert in pytest moves with the one switch; the run's p50 / p95 gate never does."""
    spec = importlib.util.spec_from_file_location("golden_run_latency", Path(__file__).resolve().parent.parent / "evals" / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner.latency_budget
