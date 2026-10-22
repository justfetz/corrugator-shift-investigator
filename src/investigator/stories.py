"""Seeded story answer keys for the two-month synthetic dataset.

THESE ANSWER KEYS ARE NOT EXPOSED THROUGH ANY TOOL. Only the generator
(`synth.py`) reads this module, to plant the effects in the records. The
analysis layer, the agent, the server, the period reports and the narrative
never import it, so an investigation has to find the effects in the records
rather than look the answer up. `tests/test_records_invariants.py` pins that
import boundary and checks that each planted effect is visible in the data.

All dates are production dates (the 07:00 start of the production day).
"""
from datetime import date

# Story 1: a slitter/scorer rebuild during the planned-maintenance window of
# the week of 17 August. Afterwards side trim creeps upward by about 0.1
# percentage point per week (the crews run wider trim allowances) and the
# knife / slitter jam stops rise in minutes and count, concentrating on the
# slitter/scorer.
SLITTER_REBUILD = {
    "pm_date": date(2026, 8, 20),          # Thursday, shift 1 PM window
    "pm_shift": 1,
    "pm_minutes": 180,                     # the rebuild uses a longer window
    "place": "Slitter/scorer",
    "baseline_trim_pct": 2.7,              # target before the rebuild
    "trim_creep_pp_per_week": 0.10,        # added per week after pm_date
    "knife_slitter_minutes_multiplier": 2.2,
    "slitter_place_share_before": 0.20,    # share of knife/slitter jams at the slitter
    "slitter_place_share_after": 0.50,
}

# Story 2: a missed-splice problem on Splicer 3 for the week of 7-13 September.
# Missed-splice minutes roughly triple (all of the extra lands on Splicer 3),
# stop counts rise with them, and the shear chop follows because every missed
# splice means a web break and a run of full-width chops.
SPLICER3_MISSED_SPLICE = {
    "start": date(2026, 9, 7),
    "end": date(2026, 9, 13),
    "place": "Splicer 3",
    "minutes_multiplier": 3.0,
    "web_break_chops": (8, 14),            # extra 34-inch chops per missed splice
}

# Story 3: warp rejects are higher on the first production day after a weekend
# (Monday) and higher in August than in September. Rates are fractions of
# gross square feet; bond, misalignment and moisture stay flat (dry-end total
# about 1.0-1.1% of gross).
WEEKEND_WARP = {
    "monday_multiplier": 1.5,
    "warp_rate_by_month": {8: 0.0042, 9: 0.0030},
    "bond_rate": 0.0040,
    "misalignment_rate": 0.0020,
    "moisture_rate": 0.0010,
    # reported sub-reason mix inside a family (fractions of that family's rejects)
    "warp_detail_weights": {"Up warp": 0.45, "Down warp": 0.25, "Twist warp": 0.20, "S-warp": 0.10},
    "bond_detail_weights": {"Delamination": 0.60, "Paper / raw material": 0.40},
}
