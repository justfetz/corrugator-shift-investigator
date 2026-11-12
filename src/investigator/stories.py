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
# the week of 17 August. Afterwards the slitters cannot cut as close to the
# edge, so the crews run a wider minimum trim allowance: it creeps up by about
# a tenth of an inch per week, and because the scheduler must fit every setup's
# lanes on paper at least that much wider, side trim creeps upward with it
# until the end of October, when the allowance is reset and trim returns to its
# pre-story level. Over the same weeks the knife / slitter jam stops rise in
# minutes and count, concentrating on the slitter/scorer; they end with the
# allowance on 31 October (issue #100, carried over from PR #91's review).
SLITTER_REBUILD = {
    "pm_date": date(2026, 8, 20),          # Thursday, shift 1 PM window
    "pm_shift": 1,
    "pm_minutes": 180,                     # the rebuild uses a longer window
    "place": "Slitter/scorer",
    "baseline_min_trim_in": 2.0,           # the scheduler's minimum trim before the rebuild, and again after the story ends
    "min_trim_creep_in_per_week": 0.10,    # added per week after pm_date
    "trim_creep_end": date(2026, 10, 31),  # the creep's last day (owner, PR #91); baseline again from 1 November
    "knife_slitter_minutes_multiplier": 3.0,   # strong enough to survive Poisson noise at the month grain
    "knife_slitter_end": date(2026, 10, 31),   # the jam effect's last day, with the trim creep (issue #100)
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
# (Monday) and higher in August than in any other month. Rates are fractions of
# gross square feet by reject category (reject_reasons.json); bond,
# misalignment, moisture and the other dry-end categories stay flat (dry-end quality total
# about 1.0-1.1% of gross).
WEEKEND_WARP = {
    "monday_multiplier": 1.5,
    "warp_rate_by_month": {8: 0.0042, 9: 0.0030},
    "bond_rate": 0.0040,
    "misalignment_rate": 0.0020,
    "moisture_rate": 0.0006,               # its own family (owner, 2026-11-12)
    # the rest of dry-end quality, by category (family OTHER): about 0.04% of gross together
    "other_rates": {"cracking": 0.00014, "handling": 0.00012, "paper": 0.00010, "print": 0.00004},
    # reported reason mix inside a category (fractions of that category's rejects)
    "warp_reason_weights": {"Up warp": 0.42, "Down warp": 0.24, "Twist warp": 0.18, "S warp": 0.10, "End-to-end warp": 0.06},
    "bond_reason_weights": {"Delamination": 0.38, "Blisters": 0.12, "Blow out": 0.10, "Glue dam": 0.07, "Zipper board": 0.06,
                            "Fuzzy edges": 0.06, "Loose / tight edges": 0.06, "Dry board": 0.06, "Low caliper": 0.05,
                            "Between flutes (DW)": 0.04},
}
