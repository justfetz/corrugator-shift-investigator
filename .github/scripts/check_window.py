#!/usr/bin/env python3
"""The deploy smoke test's window check (issue #35): the image built by this run serves the year ending
yesterday. A cached image layer that froze an older window would otherwise pass the smoke test silently.

    python3 .github/scripts/check_window.py <health.json> <run day> [<today>]

<health.json> is the body of GET /api/health. <run day> is the UTC date the deploy step started
(the image is built after it); <today> defaults to the UTC date now. Either day's yesterday is
accepted, so a build that crosses midnight UTC passes, and a window a day older does not.

`store.window.as_of` must be that yesterday, and `store.window.last_day` the last production day
(Mon-Fri) on or before it: yesterday itself on a Tuesday-to-Saturday run, the Friday before on a
Sunday or Monday run. Prints the window and exits 0, or names what is wrong and exits 1.
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone


def last_production_day(day):
    while day.weekday() >= 5:   # Saturday, Sunday: no production
        day -= timedelta(days=1)
    return day


def check(health, run_day, today):
    """The failures of `health` (the /api/health body) against the window a build on `run_day`
    (or, past midnight UTC, on `today`) produces; an empty list when it is right."""
    window = ((health or {}).get("store") or {}).get("window") or {}
    as_of, last_day = window.get("as_of"), window.get("last_day")
    if not as_of or not last_day:
        return [f"/api/health has no store.window.as_of and last_day: {json.dumps(health)[:300]}"]
    accepted = sorted({run_day - timedelta(days=1), today - timedelta(days=1)})
    for yesterday in accepted:
        if as_of == yesterday.isoformat():
            expected = last_production_day(yesterday).isoformat()
            if last_day != expected:
                return [f"store.window.last_day is {last_day}, expected {expected}, the last production day on or before {as_of}"]
            return []
    names = " or ".join(d.isoformat() for d in accepted)
    return [f"store.window ends {as_of} (last production day {last_day}), expected yesterday, {names}: "
            "the image did not regenerate the window (a cached build?)"]


def main(argv):
    if len(argv) not in (3, 4):
        print(next(line.strip() for line in __doc__.splitlines() if line.strip().startswith('python3')), file=sys.stderr)
        return 2
    with open(argv[1]) as handle:
        health = json.load(handle)
    run_day = date.fromisoformat(argv[2])
    today = date.fromisoformat(argv[3]) if len(argv) == 4 else datetime.now(timezone.utc).date()
    failures = check(health, run_day, today)
    window = ((health or {}).get("store") or {}).get("window") or {}
    if failures:
        for failure in failures:
            print(f"::error::{failure}")
        return 1
    print(f"window {window.get('first_day')} to {window.get('last_day')} (as of {window.get('as_of')}, "
          f"{window.get('production_days')} production days): ends yesterday")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
