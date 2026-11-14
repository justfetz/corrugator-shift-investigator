"""Pin the dataset window for the whole test session (issue #35), and where the page lands on it (issue #137).

The app's dataset is the rolling year ending INVESTIGATOR_AS_OF (default: yesterday). Tests and the
golden set read the year ending 2026-09-30, the last day of the original two months, so the
expectations do not move with the calendar. Set before any test module imports the store; a test
that needs another window builds its own `RecordStore(window=...)`. Subprocesses inherit it.

The page lands on the store's last production day, shift 1 (issue #137); on the golden store it lands on
the golden default instead, the day and shift evals/run.py asks a case on when the case names neither, so
the page the tests open is the one the goldens are asked on. The pin lives here, beside the window, not
in the page.
"""
import importlib.util
import os
from pathlib import Path

import pytest

GOLDEN_AS_OF = "2026-09-30"   # evals/run.py GOLDEN_AS_OF; tests/test_goldens_values.py checks the two agree
GOLDEN_LANDING = "2026-09-14/shift/2"   # evals/run.py DEFAULT_DAY and its default shift; tests/test_landing.py checks they agree

os.environ["INVESTIGATOR_AS_OF"] = GOLDEN_AS_OF
os.environ["INVESTIGATOR_LANDING"] = GOLDEN_LANDING
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
