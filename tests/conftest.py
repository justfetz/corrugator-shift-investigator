"""Pin the dataset window for the whole test session (issue #35).

The app's dataset is the rolling year ending INVESTIGATOR_AS_OF (default: yesterday). Tests and the
golden set read the year ending 2026-09-30, the last day of the original two months, so the
expectations do not move with the calendar. Set before any test module imports the store; a test
that needs another window builds its own `RecordStore(window=...)`. Subprocesses inherit it.
"""
import os

GOLDEN_AS_OF = "2026-09-30"   # evals/run.py GOLDEN_AS_OF; tests/test_goldens_values.py checks the two agree

os.environ["INVESTIGATOR_AS_OF"] = GOLDEN_AS_OF
os.environ.pop("INVESTIGATOR_PREBUILT", None)          # a developer's prebuilt folder must not decide the window
os.environ.pop("INVESTIGATOR_GOLDEN_PREBUILT", None)
