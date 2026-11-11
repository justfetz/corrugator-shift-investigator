"""The owner's worked example of the setup math (issue #100), reproduced exactly by the scheduler's own
functions in `synth.py`: pair two orders across the two cutoff knives on a 100-inch web, the shorter lane
sets the length, the other runs as far as its pair and its remainder rolls into a second setup alone on
the narrowest paper that fits.

Orders: AC Box 15 x 35 in, 2,000 sheets; Jerry's Packaging 17 x 47 in, 3,000 sheets. The example leaves
2 inches of trim a side (4 in), so MIN_TRIM_IN is 4 here; the generator's own minimum is 1 inch a side.
"""
import random

import pytest

from investigator.synth import TRIM_MIN_IN, _Planner, best_outs, narrowest_web, plan_lanes, trim_pct

MIN_TRIM_IN = 4
AC_BOX = {"remaining": 2000, "w": 15, "L": 35}
JERRYS = {"remaining": 3000, "w": 17, "L": 47}


def order(spec, number):
    return {**spec, "order_number": number, "filler": False, "fanfold": False, "quantity": spec["remaining"]}


def test_setup_one_two_knives_on_100_inch_paper():
    # the fill that uses the most width and still leaves the 4-inch trim: 3 out of each, 96 in used
    trim, (outs1, partner, outs2) = min(best_outs(15, [17], 100, MIN_TRIM_IN), key=lambda option: option[0])
    assert (trim, outs1, partner, outs2) == (4, 3, 0, 3)
    assert 3 * 15 + 3 * 17 == 96 and trim / 2 == 2 and trim_pct(96, 100) == pytest.approx(4.0)
    assert 2 * 15 + 3 * 17 == 81   # "2 outs x 15 in would be 81 in used, too little; bump to 3 out"
    # AC Box sets the length; Jerry's runs only as far as its pair
    guide, cuts = plan_lanes([(2000, 3, 35), (3000, 3, 47)])
    assert guide == 0 and cuts == [667, 496]
    ac_ft, jerrys_ft = 667 * 35 / 12, 496 * 47 / 12
    assert round(ac_ft, 1) == 1945.4 and round(jerrys_ft, 1) == 1942.7
    # "2.7 ft apart": the two rounded lineals (1,945.4 - 1,942.7; 2.75 ft unrounded), under one 47-in cut
    assert round(round(ac_ft, 1) - round(jerrys_ft, 1), 1) == 2.7 and ac_ft - jerrys_ft < 47 / 12
    assert 496 * 3 == 1488 and 3000 - 1488 == 1512


def test_setup_two_the_remainder_alone_on_the_narrowest_paper():
    choices = []
    for outs in range(1, 7):
        web = narrowest_web(outs * 17, MIN_TRIM_IN)
        if web is not None:
            choices.append((trim_pct(outs * 17, web), outs, web))
    pct, outs, web = min(choices)
    assert (outs, web) == (5, 90) and round(pct, 1) == 5.6   # 5 out x 17 in = 85 in on 90-in paper, 5 in of trim
    guide, cuts = plan_lanes([(1512, 5, 47)])
    assert cuts == [303] and round(303 * 47 / 12, 1) == 1186.8
    assert 303 * 5 == 1515 and 1515 - 1512 == 3   # 3 sheets over
    # paper choice matters: the same 85 in on wider paper
    assert round(trim_pct(85, 95), 1) == 10.5 and trim_pct(85, 100) == pytest.approx(15.0)
    assert narrowest_web(85, MIN_TRIM_IN) == 90


def test_the_scheduler_pairs_the_two_orders_the_same_way():
    """The generator's pairing search (`_Planner._fit`) over the same two orders, with the example's
    allowance, picks the same lanes and cuts and carries Jerry's 1,512-sheet remainder."""
    planner = _Planner(random.Random(0), "261111", 2026)
    ac, jerrys = order(AC_BOX, "SO-AC"), order(JERRYS, "SO-JP")
    trim, lanes, guide, cuts = planner._fit(ac, [jerrys], 100, MIN_TRIM_IN)
    assert trim == 4 and [(o["order_number"], outs) for o, outs in lanes] == [("SO-AC", 3), ("SO-JP", 3)]
    assert guide == 0 and cuts == [667, 496]
    assert jerrys["remaining"] - cuts[1] * 3 == 1512
    # With the generator's own minimum (1 in a side) the narrowest paper for the remainder is wider:
    # 6 out x 17 = 102 in on 105-in paper trims 2.9%, less than 5 out on 90-in paper.
    assert TRIM_MIN_IN == 2 and narrowest_web(102, TRIM_MIN_IN) == 105 and round(trim_pct(102, 105), 1) == 2.9
