"""Known-answer tests for the period tools and the offline routing to them.

Every expectation is a plain Python sum over the `generate_months()` lists:
the DuckDB path is never reused to compute what it is being checked against.
The literal numbers pinned below are the value goldens of the default seed;
they move only with an intended generator change (the content hash is
pinned in test_synth_determinism.py).
"""
from collections import defaultdict
from datetime import date, timedelta
import pytest
from investigator.agent import Agent, Context, PERIOD_TOOLS, TOOL_SCHEMAS, parse_request
from investigator.record_store import (KPI_CATALOG, TREND_METRICS, default_store, fiscal_bounds, parse_window, previous_window, window_label)
from investigator.synth import generate_months


@pytest.fixture(scope="module")
def store():
    return default_store()


@pytest.fixture(scope="module")
def data():
    return generate_months()


def between(records, start, end, shift=None):
    lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    return [r for r in records if lo <= r.production_date <= hi and (shift is None or r.shift_number == shift)]


def plain_kpis(data, start, end, shift=None):
    """The dashboard tiles by hand: sums first, ratios last."""
    setups = between(data["dry_end_setups"], start, end, shift)
    shifts = between(data["shifts"], start, end, shift)
    stops = between(data["stops"], start, end, shift)
    gross = sum(s.total_produced_sqft for s in setups)
    feet = sum(s.lineal_ft for s in setups)
    scheduled = sum(sh.scheduled_minutes for sh in shifts)
    running = sum(s.elapsed_seconds - s.stop_time_sec for s in setups) / 60
    stop_minutes = sum(st.duration_sec for st in stops) / 60
    reasons = defaultdict(float)
    for s in setups:
        for r in s.rejects:
            reasons[r.reason] += r.sqft
    trim, shear, rejects = (sum(getattr(s, f) for s in setups) for f in ("trim_sqft", "chop_sqft", "kqf_sqft"))
    bale = sum(sh.bale_sqft for sh in shifts)
    kinds = defaultdict(float)
    for st in stops:
        kinds[st.kind] += st.duration_sec / 60
    return {
        "volume_msf": gross / 1000, "lineal_ft": feet, "speed_run_fpm": feet / running, "speed_all_fpm": feet / scheduled,
        "downtime_pct": stop_minutes / scheduled * 100, "downtime_minutes": stop_minutes, "stops": len(stops),
        "minutes_per_stop": stop_minutes / len(stops), "trim_pct": trim / gross * 100, "shear_pct": shear / gross * 100,
        "dry_end_pct": rejects / gross * 100, "bond_pct": reasons["BOND"] / gross * 100, "warp_pct": reasons["WARP"] / gross * 100,
        "misalignment_pct": reasons["MISALIGNMENT"] / gross * 100, "moisture_pct": reasons["MOISTURE"] / gross * 100, "bale_pct": bale / gross * 100,
        "unexplained_pct": (bale - trim - shear - rejects) / gross * 100,
        "maintenance_pm_pct": (kinds["Maintenance"] + kinds["PM"]) / scheduled * 100, "operator_pct": kinds["Operator"] / scheduled * 100,
        "setups": len(setups), "paper_changes": len({s.wet_end_id for s in setups}),
        "lineal_per_setup": feet / len(setups), "lineal_per_paper_change": feet / len({s.wet_end_id for s in setups}),
        "bale_msf": bale / 1000,
        "msf_per_day": gross / 1000 / len({s.production_date for s in setups}),
    }


def as_dict(result):
    return {r["metric"]: r for r in result["rows"]}


# ---------------------------------------------------------------- windows

def test_window_semantics():
    assert parse_window({"period": "week", "anchor": "2026-09-16"})[2:] == (date(2026, 9, 14), date(2026, 9, 18))
    assert parse_window({"period": "week", "anchor": "2026-09-19"})[2:] == (date(2026, 9, 14), date(2026, 9, 18))   # Saturday -> its Mon-Fri week
    assert parse_window({"period": "month", "anchor": "2026-09-14"})[2:] == (date(2026, 9, 1), date(2026, 9, 30))
    assert parse_window({"period": "day", "anchor": "2026-09-14"})[2:] == (date(2026, 9, 14), date(2026, 9, 14))
    assert previous_window("week", date(2026, 9, 14), date(2026, 9, 18)) == (date(2026, 9, 7), date(2026, 9, 11))
    assert previous_window("month", date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert previous_window("day", date(2026, 9, 14), date(2026, 9, 14)) == (date(2026, 9, 11), date(2026, 9, 11))  # Monday -> Friday
    # 4-4-5 from Monday 3 August: P1 = 4 weeks, P2 = 4 weeks, P3 = 5 weeks (only its first three days are in the data)
    assert parse_window({"period": "fiscal", "anchor": "2026-08-14"})[2:] == (date(2026, 8, 3), date(2026, 8, 30))
    assert parse_window({"period": "fiscal", "anchor": "2026-09-14"})[2:] == (date(2026, 8, 31), date(2026, 9, 27))
    assert parse_window({"period": "fiscal", "anchor": "2026-09-29"})[2:] == (date(2026, 9, 28), date(2026, 11, 1))
    assert previous_window("fiscal", date(2026, 8, 31), date(2026, 9, 27)) == (date(2026, 8, 3), date(2026, 8, 30))
    assert previous_window("fiscal", date(2026, 8, 3), date(2026, 8, 30)) == (date(2026, 6, 29), date(2026, 8, 2))  # prior year P13, 5 weeks
    assert fiscal_bounds(date(2026, 7, 1))[2].startswith("4-4-5 P13 of the prior year (5 weeks)")
    assert window_label("fiscal", date(2026, 8, 31), date(2026, 9, 27)) == "4-4-5 P2 (4 weeks) 2026-08-31 to 2026-09-27"
    for bad in ({"period": "day", "anchor": "2026-09-12"}, {"period": "quarter", "anchor": "2026-09-01"},
                {"period": "week", "anchor": "September"}, {"period": "week"}, "week"):
        with pytest.raises(ValueError):
            parse_window(bad)
    with pytest.raises(ValueError, match="outside the dataset range"):
        parse_window({"period": "month", "anchor": "2026-07-15"}, date(2026, 8, 3), date(2026, 9, 30))


# ---------------------------------------------------------------- KPIs

def test_month_kpis_match_plain_sums_and_pinned_goldens(store, data):
    result = store.get_period_kpis({"period": "month", "anchor": "2026-09-14"})
    assert result["coverage"] == "complete" and result["comparison"] == "complete"
    assert result["window"]["label"] == "September 2026 (2026-09-01 to 2026-09-30)"
    assert result["previous_window"]["label"] == "August 2026 (2026-08-01 to 2026-08-31)"
    assert result["dataset_version"] == store.content_hash and result["shift"] is None
    september = plain_kpis(data, date(2026, 9, 1), date(2026, 9, 30))
    august = plain_kpis(data, date(2026, 8, 1), date(2026, 8, 31))
    rows = as_dict(result)
    assert [r["metric"] for r in result["rows"]] == [key for key, *_ in KPI_CATALOG]
    for key, label, unit, better in KPI_CATALOG:
        row = rows[key]
        assert row["current"] == pytest.approx(september[key], rel=1e-3, abs=0.006)   # tiles are rounded to display precision
        assert row["previous"] == pytest.approx(august[key], rel=1e-3, abs=0.006)
        assert row["delta"] == pytest.approx(row["current"] - row["previous"], abs=1e-6)   # the tile reconciles on screen
        assert row["delta"] == pytest.approx(september[key] - august[key], abs=0.11)
        assert (row["label"], row["unit"], row["better"]) == (label, unit, better)
        if better == "neutral":
            assert row["change"] == "neutral"
        else:
            improved = (row["delta"] < 0) == (better == "lower")
            assert row["change"] == ("better" if improved else "worse")
    # Pinned goldens of the default seed (ratios of sums, never averages of averages).
    assert rows["volume_msf"]["current"] == pytest.approx(146115.3) and rows["volume_msf"]["previous"] == pytest.approx(138232.0)
    assert rows["lineal_ft"]["current"] == pytest.approx(18839833.8)
    assert rows["stops"]["current"] == 558 and rows["stops"]["previous"] == 523
    assert rows["trim_pct"]["current"] == pytest.approx(3.1819, abs=1e-4) and rows["trim_pct"]["previous"] == pytest.approx(2.8317, abs=1e-4)
    assert rows["warp_pct"]["current"] == pytest.approx(0.3242, abs=1e-4) and rows["warp_pct"]["previous"] == pytest.approx(0.4718, abs=1e-4)
    assert rows["downtime_pct"]["current"] == pytest.approx(4.3783, abs=1e-4)
    assert rows["maintenance_pm_pct"]["current"] == pytest.approx(2.4827, abs=1e-4)
    assert rows["setups"]["current"] == 4115 and rows["paper_changes"]["current"] == 1914
    assert result["source_rows"]["current"]["setups"] == 4115 and result["source_rows"]["previous"]["setups"] == 3853
    # downtime_pct is a ratio of sums: it is NOT the mean of the 66 shift percentages
    shift_pcts = []
    for sh in between(data["shifts"], date(2026, 9, 1), date(2026, 9, 30)):
        minutes = sum(st.duration_sec for st in data["stops"] if (st.production_date, st.shift_number) == (sh.production_date, sh.shift_number)) / 60
        shift_pcts.append(minutes / 480 * 100)
    assert rows["downtime_pct"]["current"] == pytest.approx(sum(shift_pcts) / len(shift_pcts), abs=1e-4)  # equal here only because every shift is 480 min
    weighted = plain_kpis(data, date(2026, 9, 1), date(2026, 9, 30))["trim_pct"]
    per_day = [plain_kpis(data, d, d)["trim_pct"] for d in sorted({date.fromisoformat(x) for x in store.production_days if x.startswith("2026-09")})]
    assert weighted != pytest.approx(sum(per_day) / len(per_day), abs=1e-6)


def test_week_and_day_kpis_with_shift_filter(store, data):
    week = store.get_period_kpis({"period": "week", "anchor": "2026-09-16"}, shift=2)
    assert week["window"]["start"] == "2026-09-14" and week["previous_window"]["start"] == "2026-09-07"
    assert week["shift"] == 2 and week["coverage"] == "complete"
    expected = plain_kpis(data, date(2026, 9, 14), date(2026, 9, 18), shift=2)
    for key, row in as_dict(week).items():
        assert row["current"] == pytest.approx(expected[key], rel=1e-3), key
    assert expected["setups"] == sum(1 for s in data["dry_end_setups"] if "20260914" <= s.production_date <= "20260918" and s.shift_number == 2)
    day = store.get_period_kpis({"period": "day", "anchor": "2026-09-14"}, shift=3)
    expected = plain_kpis(data, date(2026, 9, 14), date(2026, 9, 14), shift=3)
    assert as_dict(day)["volume_msf"]["current"] == pytest.approx(expected["volume_msf"], rel=1e-4)
    assert day["previous_window"]["start"] == "2026-09-11"  # the Friday before the Monday
    no_compare = store.get_period_kpis({"period": "day", "anchor": "2026-09-14"}, compare=False)
    assert no_compare["previous_window"] is None and all(r["previous"] is None and r["delta"] is None for r in no_compare["rows"])
    with pytest.raises(ValueError):
        store.get_period_kpis({"period": "day", "anchor": "2026-09-14"}, shift=4)


def test_first_week_has_no_previous_window_and_withholds_deltas(store):
    result = store.get_period_kpis({"period": "week", "anchor": "2026-08-03"})
    assert result["window"]["coverage"] == "complete" and result["previous_window"]["coverage"] == "unavailable"
    assert result["coverage"] == "partial" and result["comparison"].startswith("withheld")
    assert all(r["previous"] is None and r["delta"] is None and r["change"] is None for r in result["rows"])
    assert all(r["current"] is not None for r in result["rows"])
    partial = store.get_period_kpis({"period": "week", "anchor": "2026-09-29"})
    assert partial["window"]["production_days_expected"] == 5 and partial["window"]["production_days_observed"] == 3
    assert partial["window"]["coverage"] == "partial" and all(r["delta"] is None for r in partial["rows"])
    with pytest.raises(ValueError, match="outside the dataset range"):
        store.get_period_kpis({"period": "month", "anchor": "2026-07-01"})
    with pytest.raises(ValueError, match="Saturday"):
        store.get_period_kpis({"period": "day", "anchor": "2026-09-12"})


def test_compare_periods_matches_month_kpis_both_ways(store, data):
    months = store.compare_periods({"period": "month", "anchor": "2026-09-01"}, {"period": "month", "anchor": "2026-08-01"})
    assert months["rows"] == store.get_period_kpis({"period": "month", "anchor": "2026-09-14"})["rows"]
    reverse = store.compare_periods({"period": "month", "anchor": "2026-08-20"}, {"period": "month", "anchor": "2026-09-20"})
    for a, b in zip(months["rows"], reverse["rows"]):
        assert (a["current"], a["previous"]) == (b["previous"], b["current"]) and a["delta"] == pytest.approx(-b["delta"], abs=1e-3)
    days = store.compare_periods({"period": "day", "anchor": "2026-09-14"}, {"period": "day", "anchor": "2026-08-03"}, shift=1)
    mon_sep, mon_aug = (plain_kpis(data, d, d, shift=1) for d in (date(2026, 9, 14), date(2026, 8, 3)))
    assert as_dict(days)["lineal_ft"]["delta"] == pytest.approx(mon_sep["lineal_ft"] - mon_aug["lineal_ft"], rel=1e-4)


# ---------------------------------------------------------------- Pareto, waste, stops, trend

def test_downtime_pareto_shares_cumulate_and_match_sums(store, data):
    start, end = date(2026, 9, 7), date(2026, 9, 11)
    result = store.get_downtime_pareto({"period": "week", "anchor": "2026-09-09"}, group_by="place", measure="minutes")
    stops = between(data["stops"], start, end)
    minutes, counts = defaultdict(float), defaultdict(int)
    for st in stops:
        minutes[st.place] += st.duration_sec / 60
        counts[st.place] += 1
    rows = result["rows"]
    assert [r["category"] for r in rows] == sorted(minutes, key=lambda p: (-minutes[p], p))
    assert {r["category"]: r["minutes"] for r in rows} == pytest.approx(dict(minutes), abs=0.01)
    assert {r["category"]: r["stops"] for r in rows} == dict(counts)
    total = sum(minutes.values())
    assert rows[0]["share_pct"] == pytest.approx(rows[0]["minutes"] / total * 100, abs=0.02)
    assert rows[-1]["cumulative_pct"] == pytest.approx(100, abs=0.02)
    assert all(a["cumulative_pct"] <= b["cumulative_pct"] + 1e-9 for a, b in zip(rows, rows[1:]))
    assert result["total_minutes"] == pytest.approx(total, abs=0.05) and result["total_stops"] == len(stops)
    previous = defaultdict(float)
    for st in between(data["stops"], start - timedelta(days=7), end - timedelta(days=7)):
        previous[st.place] += st.duration_sec / 60
    assert {r["category"]: r["previous_minutes"] for r in rows} == pytest.approx({p: previous[p] for p in minutes}, abs=0.01)
    splicer = next(r for r in rows if r["category"] == "Splicer 3")
    assert splicer["minutes"] > 3 * splicer["previous_minutes"]   # the week's missed-splice problem is visible in the data
    by_reason = {r["category"]: r for r in store.get_downtime_pareto({"period": "week", "anchor": "2026-09-09"}, group_by="reason")["rows"]}
    # The planned window is its own reason row: Thursday 10 September's 127-minute PM stop leads the week
    # (135 minutes the Thursday before), and "Other" holds only the unclassified stops.
    assert (by_reason["Planned maintenance"]["stops"], by_reason["Planned maintenance"]["minutes"]) == (1, pytest.approx(127.0, abs=0.01))
    assert by_reason["Planned maintenance"]["previous_minutes"] == pytest.approx(135.0, abs=0.01)
    assert (by_reason["Other"]["stops"], by_reason["Other"]["minutes"]) == (19, pytest.approx(30.0333, abs=0.01))
    assert next(iter(by_reason)) == "Planned maintenance" and by_reason["Missed splice"]["cumulative_pct"] == pytest.approx(56.14, abs=0.01)
    assert all(st.reason == "Planned maintenance" for st in data["stops"] if st.kind == "PM")
    assert all(st.kind == "PM" for st in data["stops"] if st.reason == "Planned maintenance")
    by_count = store.get_downtime_pareto({"period": "week", "anchor": "2026-09-09"}, group_by="reason", measure="count")
    assert [r["stops"] for r in by_count["rows"]] == sorted((r["stops"] for r in by_count["rows"]), reverse=True)
    assert by_count["rows"][-1]["cumulative_pct"] == pytest.approx(100, abs=0.02)
    for bad in (dict(group_by="user_info"), dict(measure="hours"), dict(shift=0)):
        with pytest.raises(ValueError):
            store.get_downtime_pareto({"period": "week", "anchor": "2026-09-09"}, **bad)


def test_waste_breakdown_matches_sums_and_month_story(store, data):
    result = store.get_waste_breakdown({"period": "month", "anchor": "2026-09-01"})
    september, august = (plain_kpis(data, *span) for span in ((date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 8, 1), date(2026, 8, 31))))
    rows = {r["category"]: r for r in result["rows"]}
    setups = between(data["dry_end_setups"], date(2026, 9, 1), date(2026, 9, 30))
    assert rows["trim"]["sqft"] == pytest.approx(sum(s.trim_sqft for s in setups), abs=0.1)
    assert rows["shear"]["sqft"] == pytest.approx(sum(s.chop_sqft for s in setups), abs=0.1)
    assert rows["dry_end"]["sqft"] == pytest.approx(rows["bond"]["sqft"] + rows["warp"]["sqft"] + rows["misalignment"]["sqft"] + rows["moisture"]["sqft"], abs=0.2)
    assert rows["bale"]["sqft"] == pytest.approx(sum(sh.bale_sqft for sh in between(data["shifts"], date(2026, 9, 1), date(2026, 9, 30))), abs=0.1)
    assert rows["unexplained"]["sqft"] == pytest.approx(rows["bale"]["sqft"] - rows["trim"]["sqft"] - rows["shear"]["sqft"] - rows["dry_end"]["sqft"], abs=0.3)
    for key, metric in (("trim", "trim_pct"), ("shear", "shear_pct"), ("warp", "warp_pct"), ("bale", "bale_pct"), ("unexplained", "unexplained_pct")):
        assert rows[key]["pct_of_gross"] == pytest.approx(september[metric], rel=1e-3)
        assert rows[key]["previous_pct_of_gross"] == pytest.approx(august[metric], rel=1e-3)
        assert rows[key]["delta_pct_points"] == pytest.approx(september[metric] - august[metric], abs=1e-3)
    assert result["gross_sqft"] == pytest.approx(september["volume_msf"] * 1000, rel=1e-6)
    assert 0.1 <= rows["unexplained"]["pct_of_gross"] <= 0.4
    assert rows["warp"]["previous_pct_of_gross"] > rows["warp"]["pct_of_gross"]   # August warp above September
    first = store.get_waste_breakdown({"period": "month", "anchor": "2026-08-03"})
    assert first["coverage"] == "partial" and all(r["previous_sqft"] is None and r["delta_pct_points"] is None for r in first["rows"])


def test_stops_profile_per_day_and_weekday(store, data):
    result = store.get_stops_profile({"period": "month", "anchor": "2026-08-15"})
    days = sorted({sh.production_date for sh in between(data["shifts"], date(2026, 8, 1), date(2026, 8, 31))})
    assert [r["production_day"] for r in result["rows"]] == [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days]
    per_day = defaultdict(lambda: [0, 0.0])
    for st in between(data["stops"], date(2026, 8, 1), date(2026, 8, 31)):
        per_day[st.production_date][0] += 1
        per_day[st.production_date][1] += st.duration_sec / 60
    for row in result["rows"]:
        count, minutes = per_day[row["production_day"].replace("-", "")]
        assert (row["stops"], row["minutes"]) == (count, pytest.approx(minutes, abs=0.01))
        assert row["minutes_per_stop"] == pytest.approx(minutes / count, abs=0.01)
        assert row["downtime_pct"] == pytest.approx(minutes / 1440 * 100, abs=1e-3)
        assert row["weekday"] == ("Mon", "Tue", "Wed", "Thu", "Fri")[date.fromisoformat(row["production_day"]).weekday()]
    profile = {r["weekday"]: r for r in result["weekday_profile"]}
    assert [r["weekday"] for r in result["weekday_profile"]] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert sum(r["days"] for r in profile.values()) == len(days) == 21
    mondays = [r for r in result["rows"] if r["weekday"] == "Mon"]
    assert profile["Mon"]["stops_per_day"] == pytest.approx(sum(r["stops"] for r in mondays) / len(mondays), abs=0.01)
    assert profile["Mon"]["minutes_per_day"] == pytest.approx(sum(r["minutes"] for r in mondays) / len(mondays), abs=0.01)
    assert profile["Mon"]["stops_per_day"] > min(profile[d]["stops_per_day"] for d in ("Tue", "Wed", "Fri"))
    shift_only = store.get_stops_profile({"period": "week", "anchor": "2026-09-14"}, shift=1)
    assert len(shift_only["rows"]) == 5 and all(r["downtime_pct"] == pytest.approx(r["minutes"] / 480 * 100, abs=1e-3) for r in shift_only["rows"])


def test_day_basis_flags_unequal_production_days(store):
    months = store.get_period_kpis({"period": "month", "anchor": "2026-09-14"})
    assert (months["production_days_current"], months["production_days_previous"], months["equal_production_days"]) == (22, 21, False)
    assert "Unequal production days: 22 vs 21" in months["day_basis_note"] and "MSF/day" in months["day_basis_note"]
    fiscal = store.get_period_kpis({"period": "fiscal", "anchor": "2026-09-14"})
    assert fiscal["window"]["label"].startswith("4-4-5 P2 (4 weeks)") and fiscal["previous_window"]["label"].startswith("4-4-5 P1 (4 weeks)")
    assert (fiscal["production_days_current"], fiscal["production_days_previous"], fiscal["equal_production_days"]) == (20, 20, True)
    assert fiscal["coverage"] == "complete" and fiscal["comparison"] == "complete"
    rows = {r["metric"]: r for r in fiscal["rows"]}
    assert rows["msf_per_day"]["current"] == pytest.approx(rows["volume_msf"]["current"] / 20, abs=0.06)
    first = store.get_period_kpis({"period": "fiscal", "anchor": "2026-08-14"})
    assert first["previous_window"]["coverage"] == "unavailable" and first["equal_production_days"] is False  # 20 vs 0 observed
    assert first["comparison"].startswith("withheld")
    single = store.get_period_kpis({"period": "week", "anchor": "2026-09-14"}, compare=False)
    assert single["production_days_previous"] is None and single["equal_production_days"] is None


def test_grade_totals_are_ratios_of_sums_within_each_grade(store, data):
    result = store.get_grade_totals({"period": "month", "anchor": "2026-09-14"})
    setups = between(data["dry_end_setups"], date(2026, 9, 1), date(2026, 9, 30))
    by_grade = defaultdict(list)
    for s in setups:
        by_grade[s.quality_id].append(s)
    assert [r["grade"] for r in result["rows"]] == sorted(by_grade, key=lambda g: (-sum(s.total_produced_sqft for s in by_grade[g]), g))
    total = sum(s.total_produced_sqft for s in setups)
    assert result["total_volume_msf"] == pytest.approx(total / 1000, abs=0.06)
    assert sum(r["share_of_volume_pct"] for r in result["rows"]) == pytest.approx(100, abs=0.01)
    for row in result["rows"]:
        mine = by_grade[row["grade"]]
        gross, feet = sum(s.total_produced_sqft for s in mine), sum(s.lineal_ft for s in mine)
        running = sum(s.elapsed_seconds - s.stop_time_sec for s in mine) / 60
        assert row["setups"] == len(mine) and row["wet_ends"] == len({s.wet_end_id for s in mine})
        assert row["volume_msf"] == pytest.approx(gross / 1000, abs=0.06) and row["lineal_ft"] == pytest.approx(feet, abs=0.06)
        assert row["speed_run_fpm"] == pytest.approx(feet / running, abs=0.06)
        assert row["speed_elapsed_fpm"] == pytest.approx(feet / (sum(s.elapsed_seconds for s in mine) / 60), abs=0.06)
        assert row["speed_elapsed_fpm"] <= row["speed_run_fpm"]
        assert row["speed_to_target_pct"] == pytest.approx(row["speed_run_fpm"] / row["target_fpm"] * 100, abs=0.06)
        assert row["trim_pct"] == pytest.approx(sum(s.trim_sqft for s in mine) / gross * 100, abs=1e-3)
        assert row["dry_end_pct"] == pytest.approx(sum(s.kqf_sqft for s in mine) / gross * 100, abs=1e-3)
        assert row["downtime_pct_of_elapsed"] == pytest.approx(sum(s.stop_time_sec for s in mine) / sum(s.elapsed_seconds for s in mine) * 100, abs=1e-3)
        assert row["lineal_per_setup"] == pytest.approx(feet / len(mine), abs=0.06)
        assert row["lineal_per_wet_end"] == pytest.approx(feet / row["wet_ends"], abs=0.06)
        previous = [s for s in between(data["dry_end_setups"], date(2026, 8, 1), date(2026, 8, 31)) if s.quality_id == row["grade"]]
        assert row["previous_volume_msf"] == pytest.approx(sum(s.total_produced_sqft for s in previous) / 1000, abs=0.06)
    august = store.get_grade_totals({"period": "month", "anchor": "2026-08-14"}, shift=3)
    assert all(r["previous_volume_msf"] is None and r["delta_volume_msf"] is None for r in august["rows"])  # July is unavailable, never zero


def test_overrun_sums_cuts_and_sheets_per_knife(store, data):
    result = store.get_overrun({"period": "week", "anchor": "2026-09-14"}, shift=2)
    setups = between(data["dry_end_setups"], date(2026, 9, 14), date(2026, 9, 18), 2)
    lines = defaultdict(list)
    for s in setups:
        for c in s.customers:
            lines[c.cutoff_allocation].append(c)
    assert [r["knife"] for r in result["rows"]] == [f"knife {k}" for k in sorted(lines)] + ["all knives"]
    assert result["band_pct"] == 2.0 and result["window_pct"] == 10.0
    everything = [c for group in lines.values() for c in group]
    for row in result["rows"]:
        mine = everything if row["knife"] == "all knives" else lines[int(row["knife"].split()[1])]
        nominal, actual = sum(c.nominal_cuts for c in mine), sum(c.actual_cuts for c in mine)
        assert (row["lines"], row["nominal_cuts"], row["actual_cuts"], row["delta_cuts"]) == (len(mine), nominal, actual, actual - nominal)
        assert row["overrun_pct"] == pytest.approx((actual - nominal) / nominal * 100, abs=0.006)
        assert row["nominal_sheets"] == sum(c.nominal_cuts * c.number_of_outs for c in mine)
        assert row["over_sheets"] == sum((c.actual_cuts - c.nominal_cuts) * c.number_of_outs for c in mine if c.actual_cuts > c.nominal_cuts)
        assert row["under_sheets"] == sum((c.nominal_cuts - c.actual_cuts) * c.number_of_outs for c in mine if c.actual_cuts < c.nominal_cuts)
        over = sum(1 for c in mine if (c.actual_cuts - c.nominal_cuts) / c.nominal_cuts * 100 > 2)
        under = sum(1 for c in mine if (c.actual_cuts - c.nominal_cuts) / c.nominal_cuts * 100 < -2)
        assert row["lines_over_pct"] == pytest.approx(over / len(mine) * 100, abs=0.06)
        assert row["lines_under_pct"] == pytest.approx(under / len(mine) * 100, abs=0.06)
        assert row["lines_inside_band_pct"] == pytest.approx((len(mine) - over - under) / len(mine) * 100, abs=0.06)
        assert abs(row["overrun_pct"]) <= 10  # the generator never leaves the record window
    assert 0.80 <= result["rows"][-1]["lines_inside_band_pct"] / 100 <= 0.90  # the generator keeps ~85% of lines inside +-2%


def test_overrun_chart_is_a_range_with_the_band(store):
    analysis = store.day_analysis("2026-09-14")
    context = Context("2026-09-14", 2, scope="day")
    context.scope = "day"
    result = Agent(analysis, store=store).ask("Over/under-run by knife this week", context)
    assert result["status"] == "complete"
    [chart] = result["charts"]
    assert chart["type"] == "range" and chart["y"] == "overrun_pct" and chart["band_pct"] == 2.0
    assert chart["rows"][-1]["knife"] == "all knives"
    assert "Over/under-run by knife" in result["sections"][1] and "inside the band" in result["sections"][1]
    grade = Agent(analysis, store=store).ask("Volume by grade this month", context)
    assert grade["status"] == "complete" and grade["charts"][0]["y2"] == "previous_volume_msf"
    assert "Totals by grade" in grade["sections"][1] and "% of the" in grade["sections"][1]


def test_trend_is_weekly_ratio_of_sums_and_allow_listed(store, data):
    result = store.get_trend("trim_pct", weeks=8, anchor="2026-09-30")
    assert result["metric"] == "trim_pct" and result["unit"] == "% of gross" and result["weeks_requested"] == 8
    assert [r["week_start"] for r in result["rows"]] == ["2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    for row in result["rows"]:
        start = date.fromisoformat(row["week_start"])
        assert row["value"] == pytest.approx(plain_kpis(data, start, start + timedelta(days=4))["trim_pct"], rel=1e-3)
    assert result["rows"][-1]["partial"] is True and result["rows"][-1]["production_days"] == 3
    assert result["coverage"] == "partial" and result["missing_weeks"] == []
    values = [r["value"] for r in result["rows"]]
    assert values[-1] - values[0] > 0.4   # the trim creep after the rebuild week is visible
    for metric in TREND_METRICS:
        series = store.get_trend(metric, weeks=3, anchor="2026-09-14")
        assert len(series["rows"]) == 3 and all(r["value"] is not None for r in series["rows"])
    nine = store.get_trend("stops", weeks=9, anchor="2026-09-14")
    assert nine["missing_weeks"] == ["2026-07-20", "2026-07-27"] and len(nine["rows"]) == 7
    assert nine["rows"][0]["value"] == sum(1 for st in data["stops"] if "20260803" <= st.production_date <= "20260807")
    for bad in (dict(metric="profit"), dict(metric="trim_pct", weeks=0), dict(metric="trim_pct", weeks=27), dict(metric="trim_pct", weeks="8")):
        with pytest.raises(ValueError):
            store.get_trend(**bad)


# ---------------------------------------------------------------- routing

DAY = "2026-09-14"
ROUTES = [
    ("compare this month to last", "get_period_kpis", {"window": {"period": "month", "anchor": DAY}, "compare": True}),
    ("How did this month go versus the previous one?", "get_period_kpis", {"window": {"period": "month", "anchor": DAY}}),
    ("how did this week go versus the previous one", "get_period_kpis", {"window": {"period": "week", "anchor": DAY}}),
    ("how did this shift go versus the previous one", "get_period_kpis", {"window": {"period": "day", "anchor": DAY}}),
    ("how did September go versus August", "compare_periods", {"current_window": {"period": "month", "anchor": "2026-09-01"},
                                                                 "previous_window": {"period": "month", "anchor": "2026-08-01"}}),
    ("How did August go?", "get_period_kpis", {"window": {"period": "month", "anchor": "2026-08-01"}, "compare": True}),
    ("which downtime reason cost the most minutes", "get_downtime_pareto", {"window": {"period": "day", "anchor": DAY}, "group_by": "reason", "measure": "minutes"}),
    ("which downtime reason cost the most minutes this month", "get_downtime_pareto", {"window": {"period": "month", "anchor": DAY}, "group_by": "reason"}),
    ("where did we stop most often this week", "get_downtime_pareto", {"window": {"period": "week", "anchor": DAY}, "group_by": "place", "measure": "count"}),
    ("is it bond or warp", "get_waste_breakdown", {"window": {"period": "day", "anchor": DAY}}),
    ("is it bond or warp this month", "get_waste_breakdown", {"window": {"period": "month", "anchor": DAY}}),
    ("waste breakdown for the week", "get_waste_breakdown", {"window": {"period": "week", "anchor": DAY}}),
    ("stops per day this week", "get_stops_profile", {"window": {"period": "week", "anchor": DAY}}),
    ("stops per day this month", "get_stops_profile", {"window": {"period": "month", "anchor": DAY}}),
    ("trend of trim over eight weeks", "get_trend", {"metric": "trim_pct", "weeks": 8, "anchor": DAY}),
    ("show the downtime trend over 4 weeks", "get_trend", {"metric": "downtime_pct", "weeks": 4, "anchor": DAY}),
    ("trend of minutes per stop", "get_trend", {"metric": "minutes_per_stop", "weeks": 8}),
    ("weekly warp trend", "get_trend", {"metric": "warp_pct"}),
    ("volume by grade this month", "get_grade_totals", {"window": {"period": "month", "anchor": DAY}}),
    ("compare this period to the previous period", "get_period_kpis", {"window": {"period": "fiscal", "anchor": DAY}, "compare": True}),
    ("which downtime reason cost the most minutes this fiscal period", "get_downtime_pareto", {"window": {"period": "fiscal", "anchor": DAY}, "group_by": "reason"}),
    ("volume by grade this fiscal period", "get_grade_totals", {"window": {"period": "fiscal", "anchor": DAY}}),
    ("how much 275-BC did we run this week", "get_grade_totals", {"window": {"period": "week", "anchor": DAY}}),
    ("over/under-run by knife this week", "get_overrun", {"window": {"period": "week", "anchor": DAY}}),
    ("are we inside the cut range this month", "get_overrun", {"window": {"period": "month", "anchor": DAY}}),
]


@pytest.mark.parametrize("text, tool, arguments", ROUTES, ids=[r[0] for r in ROUTES])
def test_period_phrasing_routes_to_the_store_tools(text, tool, arguments):
    request = parse_request(text, Context(DAY, 2, scope="day"))
    [step] = request["plan"]
    assert step["tool"] == tool and request["intent"].startswith("period_")
    for key, value in arguments.items():
        assert step["arguments"][key] == value, key
    assert step["arguments"]["shift"] is None   # day scope, no shift named: all three shifts


def test_shift_named_or_selected_scope_filters_the_period_tools():
    request = parse_request("how did shift 2 go compared with last week", Context(DAY, 1, scope="day"))
    [step] = request["plan"]
    assert step["tool"] == "get_period_kpis" and step["arguments"] == {"window": {"period": "week", "anchor": DAY}, "compare": True, "shift": 2}
    assert request["shifts"] == [2]
    selected = parse_request("compare this month to last", Context(DAY, 3, scope="shift"))
    assert selected["plan"][0]["arguments"]["shift"] == 3
    with pytest.raises(ValueError, match="Selected shift only"):
        parse_request("how did shift 2 go compared with last week", Context(DAY, 1, scope="shift"))


@pytest.mark.parametrize("text, message", [
    ("Compare last week", "Ambiguous window"), ("Compare with yesterday.", "day control"),
    ("trend over eight weeks", "Name the metric"), ("trend of trim over 40 weeks", "1 to 26"),
    ("Show shift 2 on 2026-09-13", "Select that production day")])
def test_period_refusals(text, message):
    with pytest.raises(ValueError, match=message):
        parse_request(text, Context(DAY, 2, scope="day"))


def test_day_questions_still_route_to_the_day_tools():
    for text in ("Which equipment caused the most downtime?", "Show dry-end quality waste", "Why was this shift slow?",
                 "Was it maintenance or operator downtime?", "Show setup matrix", "Rank downtime losses"):
        assert "plan" not in parse_request(text, Context(DAY, 2, scope="shift")), text


def test_period_tool_schemas_are_declared():
    names = {s["name"] for s in TOOL_SCHEMAS}
    assert PERIOD_TOOLS <= names and PERIOD_TOOLS == {"get_period_kpis", "compare_periods", "get_downtime_pareto",
                                                     "get_waste_breakdown", "get_stops_profile", "get_trend",
                                                     "get_grade_totals", "get_overrun"}
    for schema in TOOL_SCHEMAS:
        if schema["name"] in PERIOD_TOOLS:
            assert schema["parameters"]["additionalProperties"] is False and "shift" in schema["parameters"]["properties"]
            assert schema["parameters"]["properties"]["shift"]["enum"] == [None, 1, 2, 3]


def test_offline_investigation_end_to_end(store):
    analysis = store.day_analysis(DAY)
    try:
        agent = Agent(analysis)
        result = agent.ask("how did September go versus August", Context(DAY, 2, scope="day"))
        assert result["status"] == "complete" and result["selected_shifts"] == []
        [trace, chart] = result["trace"]
        assert trace["tool"] == "compare_periods" and chart["tool"] == "render_chart" and chart["ok"]
        assert result["charts"][0]["type"] == "bar" and result["charts"][0]["y2"] == "previous" and result["charts"][0]["rows"] == trace["data"]["rows"]
        assert "September 2026" in result["charts"][0]["title"] and "all shifts" in result["charts"][0]["title"]
        assert any("Volume" in s and "MSF" in s for s in result["sections"])
        assert [t["title"] for t in result["tables"]] == ["compare_periods"]
        trend = agent.ask("trend of trim over eight weeks", Context(DAY, 2, scope="day"))
        assert trend["charts"][0]["type"] == "line" and trend["charts"][0]["unit"] == "% of gross"
        pareto = agent.ask("which downtime reason cost the most minutes this month", Context(DAY, 2, scope="shift"))
        assert pareto["trace"][0]["arguments"]["shift"] == 2 and pareto["charts"][0]["y"] == "minutes"
        assert pareto["selected_shifts"] == [2] and pareto["context"]["history"][-1]["intent"] == "period_pareto"
        waste = agent.ask("is it bond or warp", Context(DAY, 2, scope="shift"))
        assert waste["trace"][0]["data"]["window"] == {**waste["trace"][0]["data"]["window"], "period": "day", "anchor": DAY}
        stops = agent.ask("stops per day this week", Context(DAY, 2, scope="day"))
        assert len(stops["trace"][0]["data"]["rows"]) == 5 and any("Weekday profile" in s for s in stops["sections"])
    finally:
        analysis.close()
