"""Seeded story answer keys for the synthetic dataset (dated in August to October 2026).

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
# percentage point per week (the crews run wider trim allowances) until the end
# of October, when the allowances are reset and trim returns to its pre-story
# level; over the same span the knife / slitter jam stops rise in minutes and
# count, concentrating on the slitter/scorer, and they return to their baseline
# together with the trim from 1 November (issue #100, carried over from #91).
SLITTER_REBUILD = {
    "pm_date": date(2026, 8, 20),          # Thursday, shift 1 PM window
    "pm_shift": 1,
    "pm_minutes": 180,                     # the rebuild uses a longer window
    "place": "Slitter/scorer",
    "baseline_trim_pct": 2.7,              # target before the rebuild, and again after the creep ends
    "trim_creep_pp_per_week": 0.10,        # added per week after pm_date
    "trim_creep_end": date(2026, 10, 31),  # the creep's last day (owner, PR #91); baseline again from 1 November
    "knife_slitter_minutes_multiplier": 3.0,   # strong enough to survive Poisson noise at the month grain
    "knife_slitter_effect_end": date(2026, 10, 31),   # the jam effect's last day, with the trim creep (issue #100)
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
# gross square feet; bond, misalignment and cracking / surface stay flat
# (dry-end total about 1.0-1.1% of gross).
WEEKEND_WARP = {
    "monday_multiplier": 1.5,
    "warp_rate_by_month": {8: 0.0042, 9: 0.0030},
    "bond_rate": 0.0040,
    "misalignment_rate": 0.0020,
    "cracking_rate": 0.0010,
    # reported reason mix inside a category (reject_reasons.json names; fractions of that category's rejects)
    "reason_weights": {
        "warp": {"Up warp": 0.40, "Down warp": 0.25, "Twist warp": 0.18, "S warp": 0.10, "End to end warp": 0.07},
        "bond": {"Delamination": 0.40, "Blisters (tension blister)": 0.12, "Blow out": 0.10, "Glue dam": 0.08,
                 "Zipper board": 0.06, "Fuzzy edges": 0.06, "Loose / tight edges": 0.06, "Dry board": 0.05,
                 "Low caliper": 0.04, "Between flutes (DW)": 0.03},
        "misalignment": {"Misalign": 0.35, "Web walking": 0.12, "Light scores": 0.10, "Bad slit cut": 0.08,
                         "Wrong scores": 0.08, "Score cutting": 0.07, "Slitter off line": 0.06,
                         "Top / bottom knife not cutting to spec": 0.04, "Frozen slitters": 0.02,
                         "Knife overheating": 0.02, "No change in slitters": 0.02, "Moon outs": 0.01,
                         "Wrong slat cuts": 0.01, "Wrong cut outs": 0.01, "Wrong dimensions / width": 0.01},
        "cracking": {"Cracking board": 0.55, "Wrinkles": 0.35, "Brake line": 0.10},
    },
}
