"""Speed variance (issue #92): the speed gap split into stops, setups, running under baseline and grade mix,
every grade held at its trailing-year baseline (the owner's speed standard, issue #101), the mix schedule-driven;
planned maintenance (schedule-driven) and external stops (a power outage) are buckets of their own (the owner, PR #111).

The value goldens (evals/golden_values.json, `value-speedvar-*`) pin the numbers a second way, from plain
Python sums over the records. These tests pin the identity itself (the buckets add up to the gap, and
each bucket's details add up to it, for every reference), the router's phrasings and their canonical URLs,
the scope rule for a second shift, the pricing of each bucket and the answer's shape.
"""
import pytest

from investigator import cost_model
from investigator.agent import Agent, Context, parse_request, scope_violation, validate_plan
from investigator.record_store import SPEEDVAR_BUCKETS, default_store
from investigator.urls import canonical_url, redirect_for, resolve_url

DAY = "2026-09-14"
# A week whose stops offset (shift 1's Thursday planned maintenance) is the third of the tool's rows for shift 3 against
# shift 1 (issue #35). In the week of 14 September it comes after three contributors and gets its line by size instead
# (test_an_offset_larger_than_the_third_contributor_gets_a_line).
OFFSET_WEEK = "2026-01-26"
BUCKETS = [key for key, _ in SPEEDVAR_BUCKETS]
CORE = ["stops", "setups", "below_baseline", "mix"]   # always there; planned maintenance and external stops when held
# The week of 7 September 2026: shift 2 runs above shift 1, and its unplanned stops (missed splices) ran worse, an offset
# that is a controllable loss and gets a line (test_a_faster_window_prices_the_buckets_that_ran_worse_and_not_the_gain).
GAIN_WEEK = "2026-09-07"


@pytest.fixture(scope="module")
def store():
    return default_store()


def variance(store, period, shift=None, against="baseline", against_shift=None, anchor=DAY):
    return store.get_speed_variance({"period": period, "anchor": anchor}, shift, against, against_shift)


CASES = [("day", 2, "baseline", None), ("week", None, "baseline", None), ("month", None, "baseline", None), ("fiscal", 3, "baseline", None),
         ("week", 2, "previous", None), ("month", 1, "previous", None), ("day", 2, "previous", None),
         ("week", 3, "shift", 1), ("month", 1, "shift", 2), ("day", 2, "shift", 3)]


@pytest.mark.parametrize("period, shift, against, against_shift", CASES)
def test_the_buckets_add_up_to_the_gap_and_the_details_to_each_bucket(store, period, shift, against, against_shift):
    data = variance(store, period, shift, against, against_shift)
    rows = {r["bucket"]: r for r in data["rows"]}
    # the four always; planned maintenance and external stops exactly when a stop of theirs is on either side
    held = {d["bucket"] for d in data["details"]}
    assert sorted(rows) == sorted(CORE + [k for k in ("planned", "external") if k in held]) and len(data["rows"]) == len(rows)
    # each bucket is rounded to 0.01 ft/min on its own, so their sum may sit 0.01 a bucket off the rounded gap
    assert abs(sum(r["gap_fpm"] for r in data["rows"]) - data["gap_fpm"]) <= 0.005 * len(rows) + 0.005
    assert abs(sum(r["gap_lineal_ft"] for r in data["rows"]) - data["gap_lineal_ft"]) <= 0.25
    assert abs(data["reference_speed_fpm"] - data["speed_all_fpm"] - data["gap_fpm"]) <= 0.11   # two speeds rounded to 0.1, the gap to 0.01
    for key, row in rows.items():
        items = [d for d in data["details"] if d["bucket"] == key]
        assert len(items) == row["items"] and abs(sum(d["gap_fpm"] for d in items) - row["gap_fpm"]) <= 0.005 * len(items) + 0.005
        assert abs(row["gap_lineal_ft"] - row["gap_fpm"] * data["scheduled_minutes"]) <= 0.005 * data["scheduled_minutes"] + 0.05   # gap_fpm is rounded to 0.01
        if row["top_item"] is not None:   # the top item pulls the way its bucket does
            assert row["top_item_fpm"] * row["gap_fpm"] > 0 and row["top_item"] in {d["item"] for d in items}
    if abs(data["gap_fpm"]) >= 0.05:
        assert abs(sum(r["share_of_gap_pct"] for r in data["rows"]) - 100) <= 0.25
        assert all(data[f"{key}_share_pct"] == rows[key]["share_of_gap_pct"] for key in rows)
    # the buckets that make the gap come first, largest first, then the offsets
    toward = [r["gap_fpm"] * (1 if data["gap_fpm"] >= 0 else -1) >= 0 for r in data["rows"]]
    assert toward == sorted(toward, reverse=True)


def test_speed_with_stops_is_the_kpi_tile_and_the_baseline_reference_is_the_usual_mix(store):
    for period, shift in (("month", None), ("week", 2), ("day", 3)):
        data = variance(store, period, shift)
        tiles = store.get_period_kpis({"period": period, "anchor": DAY}, False, shift)
        tile = next(r for r in tiles["rows"] if r["metric"] == "speed_all_fpm")
        assert data["speed_all_fpm"] == tile["current"]
    # against the baseline the reference is each grade's trailing-year baseline at its share of the setup clock over
    # that same year, all shifts, whatever shift is explained; the mix is that minus this window's baselines at its mix
    for window, shift in (({"period": "month", "anchor": DAY}, None), ({"period": "day", "anchor": DAY}, 2), ({"period": "day", "anchor": DAY}, 3)):
        data = store.get_speed_variance(window, shift)
        baselines = store.grade_speed_baselines(data["window"]["end"])
        clock = store._fetch("SELECT grade, sum(seconds) AS seconds FROM setups WHERE production_date BETWEEN ? AND ? GROUP BY grade",
                             [baselines["since"], baselines["through"]])
        standard = sum(baselines["grades"][r["grade"]]["baseline_fpm"] * r["seconds"] for r in clock) / sum(r["seconds"] for r in clock)
        assert (data["baseline_since"], data["baseline_through"], data["baseline_fallback"]) == (baselines["since"], baselines["through"], [])
        assert data["reference_speed_fpm"] == data["reference_baseline_speed_fpm"] == round(standard, 1)
        mix = next(r for r in data["rows"] if r["bucket"] == "mix")
        assert abs(mix["gap_fpm"] - (standard - data["baseline_speed_fpm"])) <= 0.06
        assert all(r["reference_loss_fpm"] == 0 for r in data["rows"] if r["bucket"] != "mix")


def test_every_grade_is_held_at_its_baseline_not_its_target(store):
    """Review of #111, fix 1: the stops, the setups and the running are each priced at the grade's trailing-year
    sustained speed (targets.json, speed), never its target; the bucket says controllable or schedule-driven."""
    data = variance(store, "week", 2)
    baselines = store.grade_speed_baselines(data["window"]["end"])["grades"]
    running = [d for d in data["details"] if d["bucket"] == "below_baseline"]
    assert running and all(d["baseline_fpm"] == baselines[d["item"]]["baseline_fpm"] for d in running)
    # speed_run_fpm is shown to 0.1 ft/min, so its share of the baseline is within that step of the tool's
    assert all(abs(d["speed_to_baseline_pct"] - d["speed_run_fpm"] / d["baseline_fpm"] * 100) <= 0.06 + 5 / d["baseline_fpm"] for d in running if d["speed_run_fpm"])
    # a stop reason's loss is its minutes at the stopped setup's baseline per scheduled minute
    stops = store._fetch("SELECT st.reason AS reason, s.grade AS grade, sum(st.seconds) / 60 AS minutes FROM stops st "
                         "JOIN setups s ON s.setup_id = st.setup_id WHERE s.production_date BETWEEN ? AND ? AND s.shift = 2 "
                         "AND st.reason <> 'Order change' GROUP BY ALL", [data["window"]["start"], data["window"]["end"]])
    lost = {}
    for r in stops:
        lost[r["reason"]] = lost.get(r["reason"], 0.0) + r["minutes"] * baselines[r["grade"]]["baseline_fpm"] / data["scheduled_minutes"]
    for d in (d for d in data["details"] if d["bucket"] == "stops"):
        assert d["current_loss_fpm"] == pytest.approx(lost[d["item"]], abs=0.006), d["item"]
    assert {r["bucket"]: r["control"] for r in data["rows"]} == {"stops": "controllable", "setups": "controllable",
                                                                 "below_baseline": "controllable", "mix": "schedule-driven"}
    assert variance(store, "week", 2, against="target")["rows"] == data["rows"]   # the old name of the default is the baseline


def test_one_shift_against_another_is_the_mirror_of_the_reverse(store):
    a, b = variance(store, "week", 3, "shift", 1), variance(store, "week", 1, "shift", 3)
    assert abs(a["gap_fpm"] + b["gap_fpm"]) <= 0.01 and a["speed_all_fpm"] == b["reference_speed_fpm"]
    for key in ("stops", "planned", "external", "setups", "below_baseline"):   # the loss buckets mirror exactly; the mix is held at each reference's own target speed
        ra, rb = (next((r for r in d["rows"] if r["bucket"] == key), None) for d in (a, b))
        assert (ra is None) == (rb is None) and (ra is None or abs(ra["gap_fpm"] + rb["gap_fpm"]) <= 0.01)
    planned = next(r for r in a["rows"] if r["bucket"] == "planned")
    assert planned["gap_fpm"] < 0 and planned["top_item"] == "Planned maintenance"   # shift 1 carries the Thursday PM: an offset for shift 3
    assert planned["control"] == "schedule-driven" and next(r for r in a["rows"] if r["bucket"] == "stops")["top_item"] != "Planned maintenance"


@pytest.mark.parametrize("args, message", [
    (("week", 2, "budget", None), "against is baseline, previous or shift"),
    (("week", 2, "baseline", 1), "against_shift is only for against=shift"),
    (("week", None, "shift", 1), "give shift and against_shift"),
    (("week", 2, "shift", 2), "another shift"),
    # September 2025 holds the year's first day; its previous window, August 2025, is before it (issue #35: July 2026 is inside)
    (("month", None, "previous", None, "2025-09-30"), "outside the record store: ask against the baseline instead"),
])
def test_refusals_say_what_to_change(store, args, message):
    with pytest.raises(ValueError, match=message):
        variance(store, *args)


def test_each_controllable_loss_bucket_is_priced_as_machine_time_and_the_mix_and_an_offset_are_not(store):
    """Review of #111: the grade mix is schedule-driven (the order book decides it), so it is never priced as machine
    time; the controllable dollars are the priced loss buckets, and the gap as a whole (which holds the mix) is not priced.
    The owner on PR #111: planned maintenance is schedule-driven and a power outage external, so neither is priced either."""
    rates = cost_model.effective()
    data = cost_model.attach("get_speed_variance", variance(store, "week", 3, "shift", 1), rates)
    mix = next(r for r in data["rows"] if r["bucket"] == "mix")
    assert mix["gap_lineal_ft"] > 0 and mix["usd"] is None and mix["usd_basis"] == cost_model.SCHEDULE_DRIVEN_MIX
    controls = {r["bucket"]: r["control"] for r in data["rows"]}
    assert controls == {"stops": "controllable", "planned": "schedule-driven", "external": "external", "setups": "controllable",
                        "below_baseline": "controllable", "mix": "schedule-driven"}   # the week holds shift 1's PM and shift 3's power outage
    for row in data["rows"]:
        if row["bucket"] in ("planned", "external"):
            assert row["usd"] is None and row["usd_basis"] == cost_model.NOT_PRICED[row["bucket"]]
        if row["control"] != "controllable":
            continue
        if row["gap_lineal_ft"] > 0:
            minutes = row["gap_lineal_ft"] / data["reference_speed_fpm"]
            assert row["machine_minutes"] == round(minutes, 2) and row["usd"] == round(minutes / 60 * 1200, 2)
            assert "lineal ft / 595.9 ft/min reference speed" in row["usd_basis"]   # shift 1's speed with stops that week, in the golden year
        else:
            assert row["usd"] is None and row["usd_basis"] == cost_model.GAIN
    priced = [r["usd"] for r in data["rows"] if r["control"] == "controllable" and r["usd"] is not None]
    assert priced and data["controllable_usd"] == round(sum(priced), 2) and "gap_usd" not in data
    assert data["contribution_status"] == "margin not set" and "provenance" in data


ROUTES = [
    ("shift", "day", "What were the top 3 contributors to speed variations?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar"),
    ("day", "day", "Why did speed vary this week?", "/corr/1/day/2026-09-14?measure=speedvar&window=week"),
    ("shift", "day", "What drove speed this month?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar&window=month"),
    ("shift", "week", "Speed loss breakdown", "/corr/1/week/2026-09-14/shift/2?measure=speedvar"),
    ("shift", "day", "What were our biggest speed losses?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar"),
    ("shift", "day", "Why was speed below target?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar"),
    ("shift", "day", "What's driving our speed this week?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar&window=week"),
    ("shift", "day", "Where did we lose speed this month?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar&window=month"),
    ("shift", "day", "What drove speed this week versus last week?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar&window=week&against=previous"),
    ("day", "day", "What drove speed in September versus August?", "/corr/1/day/2026-09-14?measure=speedvar&window=2026-09&against=previous"),
    ("day", "week", "Why did speed differ on shift 3 versus shift 1?", "/corr/1/week/2026-09-14?measure=speedvar&against=shift&shifts=3,1"),
    ("day", "day", "Speed variation between shifts this month", "/corr/1/day/2026-09-14?measure=speedvar&window=month&shifts=1,2,3"),
    ("shift", "day", "What did our speed losses cost us this week?", "/corr/1/day/2026-09-14/shift/2?measure=speedvar&window=week"),
]


@pytest.mark.parametrize("scope, period, question, url", ROUTES)
def test_the_phrasings_route_to_measure_speedvar_and_back(store, scope, period, question, url):
    context = Context(DAY, 2, scope=scope, period=period)
    request = parse_request(question, context)
    assert request["intent"] == "period_speedvar" and {s["tool"] for s in request["plan"]} == {"get_speed_variance"}
    assert canonical_url(request, context) == url
    back = resolve_url(url, store)["request"]
    assert (back["plan"], back["shifts"], back["intent"]) == (request["plan"], request["shifts"], request["intent"])


@pytest.mark.parametrize("question, intent", [
    ("Why was this shift slow?", "overview"),                         # a diagnosis stays the overview
    ("What does the speed gap cost us?", "dollars"),                  # the shift's dollars price the speed gap (issue #31)
    ("Where is the speed opportunity this month?", "period_speed_opportunity"),
    ("Trend of speed over eight weeks", "period_trend"),
    ("Which shift lost the most speed?", "speed"),
    ("Which grade is slowest against target this week?", "period_speed"),
])
def test_neighbouring_speed_questions_keep_their_answers(question, intent):
    assert parse_request(question, Context(DAY, 2, scope="shift"))["intent"] == intent


@pytest.mark.parametrize("scope, question, message", [
    ("shift", "What drove speed on shift 3 versus shift 1?", "Selected shift only"),
    ("shift", "Speed variation between shifts", "Selected shift only"),
    ("day", "What drove speed in September versus July?", "compares a window with the one just before it"),
    ("day", "What drove speed on shift 3 versus shift 1 compared with last week?", "not both"),
])
def test_router_refusals(scope, question, message):
    with pytest.raises(ValueError, match=message):
        parse_request(question, Context(DAY, 2, scope=scope))


@pytest.mark.parametrize("url, target", [
    ("/corr/1/day/2026-09-14/shift/2?measure=speedvar&compare=prev", "/corr/1/day/2026-09-14/shift/2?measure=speedvar&against=previous"),
    ("/corr/1/day/2026-09-14/shift/2?measure=speedvar&against=target", "/corr/1/day/2026-09-14/shift/2?measure=speedvar"),   # its old name
    ("/corr/1/day/2026-09-14/shift/2?measure=speedvar&against=baseline", "/corr/1/day/2026-09-14/shift/2?measure=speedvar"),
    ("/corr/1/day/2026-09-14?against=shift&shifts=3,1&measure=speedvar&window=week", "/corr/1/day/2026-09-14?measure=speedvar&window=week&against=shift&shifts=3,1"),
])
def test_aliases_redirect_to_the_canonical_address(store, url, target):
    assert redirect_for(url, store) == target and redirect_for(target, store) is None


@pytest.mark.parametrize("url, message", [
    ("/corr/1/day/2026-09-14?measure=speedvar&against=shift&shifts=3", "two shifts"),
    ("/corr/1/day/2026-09-14/shift/2?measure=speedvar&against=shift", "Selected shift only"),
    ("/corr/1/day/2026-09-14?measure=speedvar&against=budget", "against is baseline, previous or shift"),
    ("/corr/1/day/2026-09-14?measure=pareto&against=previous", "against= does not apply to measure=pareto"),
    ("/corr/1/day/2026-09-14?measure=speedvar&against=shift&compare=prev&shifts=3,1", "does not combine with against=shift"),
])
def test_url_refusals(store, url, message):
    with pytest.raises(ValueError, match=message):
        resolve_url(url, store)


def test_a_second_shift_is_outside_the_selected_shift_scope():
    step = {"window": {"period": "week", "anchor": DAY}, "shift": 2, "against": "shift", "against_shift": 1}
    assert scope_violation("get_speed_variance", step, [2], "shift", "day", DAY) == "Tool shift is outside the selected scope"
    assert scope_violation("get_speed_variance", step, [1, 2, 3], "day", "day", DAY) is None
    steps, reason = validate_plan({"steps": [{"tool": "get_speed_variance", "arguments": step}]}, [2], "shift", "day", DAY)
    assert steps == [] and reason == "get_speed_variance is outside the selected scope"
    steps, reason = validate_plan({"steps": [{"tool": "get_speed_variance", "arguments": {**step, "against_shift": "1"}}]}, [1, 2, 3], "day", "day", DAY)
    assert reason == "get_speed_variance has an argument value its schema does not allow"


def test_the_answer_is_a_headline_three_bullets_a_waterfall_and_where_to_look(store):
    agent = Agent(store.day_analysis(DAY))
    result = agent.ask("What were the top 3 contributors to speed variations?", Context(DAY, 2, scope="shift"))
    data = next(s["data"] for s in result["trace"] if s["tool"] == "get_speed_variance")
    sections = result["sections"]
    assert sections[0].startswith(f"Speed gap **{abs(data['gap_fpm']):,.1f} ft/min** below baseline: ") and sections[0].endswith("(query 1)")
    bullets = [s for s in sections[1:4]]
    assert all(b.startswith("- ") and b.endswith(f"(query 1 · row {i})") for i, b in enumerate(bullets)) and not sections[4].startswith("- ")
    assert [b[2:].split(":")[0] for b in bullets] == [r["label"] for r in data["rows"][:3]]
    looks = sections[sections.index("Where to look") + 1:]
    assert 2 <= len(looks) <= 3 and all("→ “" in x for x in looks)
    [chart] = result["charts"]
    assert chart["type"] == "waterfall" and chart["rows"] == data["rows"] and chart["unit"] == "ft/min"
    assert (chart["start"], chart["end"], chart["start_label"], chart["end_label"]) == (data["reference_speed_fpm"], data["speed_all_fpm"], "Baseline speed", "Actual speed")
    assert [t["title"] for t in result["tables"]] == ["get_speed_variance"]


def test_a_faster_window_prices_the_buckets_that_ran_worse_and_not_the_gain(store):
    """Review of #92: "offset" pulls against the gap. Shift 2 against shift 1 in the week of 7 September is a gain (above);
    its unplanned stops ran worse (missed splices), so stops is an offset on the card and is priced as machine time, while
    the gain buckets, the schedule-driven ones (shift 1's planned maintenance, the mix) and the gap itself are not; the
    contribution status is the rates', not the gap's."""
    data = cost_model.attach("get_speed_variance", variance(store, "week", 2, "shift", 1, anchor=GAIN_WEEK), cost_model.effective())
    assert data["direction"] == "above" and data["gap_fpm"] < 0 and "gap_usd" not in data
    assert data["contribution_status"] == "margin not set"
    for row in data["rows"]:
        assert (row["usd"] is not None) == (row["gap_fpm"] > 0 and row["control"] == "controllable")
        if row["control"] != "controllable":
            assert row["usd_basis"] == cost_model.NOT_PRICED[row["bucket"]]
        elif row["gap_fpm"] < 0:
            assert row["usd_basis"] == cost_model.GAIN
    agent = Agent(store.day_analysis(GAIN_WEEK))
    sections = agent.ask("Why did speed differ on shift 2 versus shift 1 this week?", Context(GAIN_WEEK, 2, scope="day"))["sections"]
    assert sections[0].startswith("Speed gap **") and " on shift 2 above shift 1: " in sections[0]
    stops = next(s for s in sections if s.startswith("- Stops: "))
    assert "the other way, an offset" in stops and " · $" in stops
    planned = next(s for s in sections if s.startswith("- Planned maintenance: "))
    assert "of the gap" in planned and " · $" not in planned and "schedule-driven" in planned
    assert ("Shift 2 is faster than shift 1, so the gap is a gain and is not priced; the controllable buckets that ran worse are priced "
            "as machine time.") in sections
    assert any(s.startswith("Contribution margin not set") for s in sections) and not any(s.startswith("The whole gap is") for s in sections)


def test_an_offset_larger_than_the_third_contributor_gets_a_line(store):
    """Owner, PR #91: the card lists the three buckets adding to the gap, and a bucket pulling against it gets a line
    too when it is larger than the third of them. Shift 3 against shift 1 in the week of 14 September: below-baseline
    running, grade mix and unplanned stops (4.7 ft/min) add to the gap; shift 1's planned maintenance (39.5 ft/min, -94%,
    its own bucket since the owner's decision on PR #111) pulls against it."""
    agent = Agent(store.day_analysis(DAY))
    sections = agent.ask("Why did speed differ on shift 3 versus shift 1 this week?", Context(DAY, 2, scope="day"))["sections"]
    lines = [s for s in sections if s.startswith("- ")][:4]
    assert [s.split(":")[0] for s in lines] == ["- Below baseline", "- Grade mix", "- Stops", "- Planned maintenance"]
    assert all("of the gap" in s for s in lines[:3]) and "the other way, an offset" in lines[3]
    assert lines[3].endswith("(query 1 · row 5)") and "largest Planned maintenance" in lines[3]   # its own row of the tool, the last
    assert "schedule-driven, the maintenance plan sets planned stops" in lines[3] and " · $" not in lines[3]
    # an offset smaller than the third contributor stays off: shift 2 against shift 1 in the week of 4 May 2026
    agent = Agent(store.day_analysis("2026-05-04"))
    sections = agent.ask("Why did speed differ on shift 2 versus shift 1 this week?", Context("2026-05-04", 2, scope="day"))["sections"]
    data = variance(store, "week", 2, "shift", 1, anchor="2026-05-04")
    offsets = [r for r in data["rows"] if r["gap_fpm"] * data["gap_fpm"] < 0]
    assert offsets and abs(offsets[0]["gap_fpm"]) < abs(data["rows"][2]["gap_fpm"])
    assert not any("the other way, an offset" in s for s in sections)
    assert len([s for s in sections if s.startswith("- ") and " ft/min** " in s]) == 3


def test_the_waterfall_draws_in_the_pdf(store):
    from reportlab.graphics.shapes import Rect, String
    from investigator.pdf_charts import drawing_for
    from investigator.reports import report_pdf
    agent = Agent(store.day_analysis(DAY))
    result = agent.ask("Why did speed differ on shift 3 versus shift 1 this week?", Context(DAY, 2, scope="day"))
    [chart] = result["charts"]
    drawing = drawing_for(chart)
    bars = [e for e in drawing.contents if isinstance(e, Rect)]
    texts = [e.text for e in drawing.contents if isinstance(e, String)]
    buckets = [r["label"] for r in next(e for e in result["trace"] if e["tool"] == "get_speed_variance")["data"]["rows"]]
    assert len(bars) == len(buckets) == 6 and f"{chart['start']:,.1f} ft/min" in texts and f"{chart['end']:,.1f} ft/min" in texts
    assert {"Shift 1", "Shift 3", "Stops", "Grade mix", "Planned maintenance", "External stops"} <= set(texts)
    pdf = report_pdf(result)
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000
