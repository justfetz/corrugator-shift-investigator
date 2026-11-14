#!/usr/bin/env python3
"""Golden evaluation for the shift investigator.

Seven case types, one score table:

  routing        the planner chose the right tools with the right arguments
  faithfulness   every number in the answer came out of a tool row
  grounding      every finding cites evidence that exists; nothing was dropped
  refusal        out-of-scope, out-of-date, out-of-tool requests are refused
  value          a tool's numbers equal a second, plain computation over the records
                 (generated into golden_values.json by make_value_goldens.py)
  investigation  a multi-turn question finds the seeded story in the evidence,
                 reports its numbers, and asserts no cause the evidence lacks
  data_quality   a corrupt record is flagged, excluded, reported, never averaged in

Every case also passes the global invariants in `invariants()`, whatever its
type. Offline cases are deterministic and free, and each one also runs through the
URL path (issue #20): its question goes to `GET /q?...&q=` on a loopback server,
the redirect must land on `expect.url` when the case records one, and the answer
served at that canonical address is scored like the in-process one, within the
same per-case budget on the server's own time (`Server-Timing`). Cases marked `live: true` (and
the `mode: live` cases) also run against a live planner: OpenAI or Anthropic,
chosen by `--provider` or by whichever key is set. Live runs are recorded to
`evals/fixtures/<provider>/<id>.json` (stamped with the dataset hash; a recording made
against another dataset is reported as stale, never scored) and replayed afterwards; a live type passes
by pass rate (`THRESHOLDS`), an offline case passes or fails on its own.

    python3 evals/run.py                              # offline + replay of recorded fixtures
    python3 evals/run.py --live                       # also call the one provider whose key is set
    python3 evals/run.py --live --provider anthropic  # ANTHROPIC_API_KEY; or openai with OPENAI_API_KEY
    python3 evals/run.py --only why-slow              # one case
    python3 evals/run.py --json out.json              # machine-readable results
    python3 evals/run.py --judge                      # also score every narrative with the DeepEval judge (issue #25):
                                                      # a model call per rubric line, never in the CI gate

The judge reports and does not gate: until its agreement with the owner's grades
(`judge_calibration.json`) passes the bar, its scores change no exit status.

Exit status is the number of failed offline cases plus the number of live
(provider, type) pairs under their pass-rate threshold, plus, when the run
covers the whole offline set (no --only), one per offline latency percentile
(p50, p95) over its budget. Skips and known defects do not count.

On a shared CI runner, INVESTIGATOR_LATENCY_SCALE (default 1, at most 3; CI sets
it in .github/workflows/tests.yml) multiplies each case's own budget. The p50 / p95
budgets and every recorded time are never scaled (issue #139; docs/TESTING.md).
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from datetime import datetime, timezone
import subprocess
import math
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from investigator.agent import PERIOD_TOOLS, STORE_TOOLS, TOOL_SCHEMAS, Agent, Context, question_scope  # noqa: E402
from investigator import semantic  # noqa: E402
from investigator.urls import resolve_url  # noqa: E402
from investigator import judge as judging  # noqa: E402
from investigator.cost_model import parse_rates  # noqa: E402
from investigator import targets as owner_targets  # noqa: E402  the owner's bands (targets.json), read as data
from investigator.record_store import RecordStore, dataset_window, default_store  # noqa: E402

GOLDEN = ROOT / 'evals' / 'golden.json'
VALUES = ROOT / 'evals' / 'golden_values.json'
FIXTURES = ROOT / 'evals' / 'fixtures'
DEFAULT_DAY = '2026-09-14'
# The golden set is scored against the rolling window that ends on this day (issue #35), whatever
# window the process serving it holds: the demo's moves every night, the goldens' never does.
GOLDEN_AS_OF = '2026-09-30'
GOLDEN_PREBUILT_ENV = 'INVESTIGATOR_GOLDEN_PREBUILT'   # the image's prebuilt folder for that window (Dockerfile)
TYPES = ('routing', 'faithfulness', 'grounding', 'refusal', 'value', 'investigation', 'data_quality')
# Pass rate a live provider must reach per type; offline cases pass or fail one by one.
THRESHOLDS = {'routing': 0.9, 'faithfulness': 1.0, 'grounding': 1.0, 'refusal': 1.0,
              'investigation': 0.8, 'value': 1.0, 'data_quality': 1.0}
# Latency: an offline case fails above `expect.max_ms` (default DEFAULT_MAX_MS; a live or replayed
# run is never timed against it), and a whole-set run fails when the offline p50 or p95 is over
# LATENCY_BUDGET_MS (an --only subset prints its percentiles and is not gated: they are just those cases' times).
# The clock covers the agent's turns only: dataset build, fixture I/O and the checks are outside it.
DEFAULT_MAX_MS = 250
LATENCY_BUDGET_MS = {'p50': 100, 'p95': 250}
# A shared CI runner is slow in bursts (a scheduling tick on two vCPUs, a full garbage collection inside the pytest
# process), so CI multiplies each case's own budget (`expect.max_ms` or DEFAULT_MAX_MS, on the in-process clock and on
# the URL's server time) and every speed budget asserted in pytest by INVESTIGATOR_LATENCY_SCALE, through latency_budget()
# (issue #139). Unset, it is 1: a developer's machine and the deployed service check the product's own budgets. It never
# touches LATENCY_BUDGET_MS, the run's p50 / p95 (the product's promise, measured as it is), nor any measured time: the
# table, --json, history.jsonl and last_run.json keep the times as the clock read them. A tracked run under a scale also
# records `latency_scale` on its history line and in last_run.json, since its pass / fail verdicts were judged at the
# scaled budgets; tests/test_evals.py fails CI on a committed one, so /evals and the case study never read such a line.
LATENCY_SCALE_ENV = 'INVESTIGATOR_LATENCY_SCALE'
MAX_LATENCY_SCALE = 3   # loosens a budget for a slow runner, never switches one off


class LatencyScaleError(RuntimeError):
    """INVESTIGATOR_LATENCY_SCALE set to anything but a number from 1 to MAX_LATENCY_SCALE: refused, never read as 1.
    Not a ValueError, so run_case's refusal handling cannot mistake it for a case's own refusal."""


def latency_scale():
    """The factor on each case's own budget and on pytest's speed budgets: INVESTIGATOR_LATENCY_SCALE, 1 when unset.
    Read on every call, so a test can set it with monkeypatch."""
    raw = os.environ.get(LATENCY_SCALE_ENV, '').strip()
    if not raw:
        return 1.0
    try:
        scale = float(raw)
    except ValueError:
        scale = math.nan
    if not 1 <= scale <= MAX_LATENCY_SCALE:   # NaN and inf fail here too
        raise LatencyScaleError(f'{LATENCY_SCALE_ENV}={raw!r}: a number from 1 to {MAX_LATENCY_SCALE}, or unset for 1')
    return scale


def latency_budget(ms):
    """A case's own budget, or a speed budget a test asserts, as this run checks it: `ms` times latency_scale().
    The one helper every per-case and pytest latency check goes through; LATENCY_BUDGET_MS never does."""
    return ms * latency_scale()


def over_budget(ms, budget, where=''):
    """The failure line for a time over a case's own budget (`budget` ms, scaled by latency_budget), or None.
    A scaled budget names the case's own figure and the factor, so a CI failure reads against the product's budget."""
    allowed = latency_budget(budget)
    if ms <= allowed:
        return None
    scale = latency_scale()
    return f'{ms:.0f} ms{where}, budget {allowed:.0f} ms' + (f' ({budget} ms × {scale:g}, {LATENCY_SCALE_ENV})' if scale != 1 else '')

TOOL_NAMES = {s['name'] for s in TOOL_SCHEMAS}
# the semantic model's compiler (issue #117) is no planner's tool: only a case that asks by its address, or a value case
# that calls it, may run it, so a model that names it anywhere else still fails the unknown-tool invariant
SEMANTIC_TOOL_NAMES = TOOL_NAMES | {semantic.TOOL}
# Numbers the prose may state that are rules, not measurements (PROCESS_CONTEXT).
# The stated rules an answer may print beside its figures: 480 scheduled minutes a shift, the 12 minutes a downtime flag
# takes of them, and the downtime and per-setup trim flags, read from the owner's targets.json (issue #101) so a changed
# band moves with it. 0.4 (shear guidance) was retired by the owner's bands.
RULE_CONSTANTS = {'1', '480', '12', '0'} | {f'{owner_targets.BANDS[m]["flag"]:g}' for m in ('maintenance', 'operator', 'trim')}
# The answer shape (issue #75), stated here independently of the application: a bold figure, the
# evidence a line cites, a where-to-look question, and the result field each evidence part names.
BOLD = re.compile(r'\*\*([^*]+)\*\*')
EVIDENCE = re.compile(r' \(query (\d+)(?: · (row|detail|weekday|flag) (\d+))?\)')
LOOK_LINK = re.compile(r' → “([^”]+)”$')
EVIDENCE_PARTS = {'row': 'rows', 'detail': 'details', 'weekday': 'weekday_profile', 'flag': 'flags'}
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
RUN_ID = re.compile(r'[0-9a-f]{32}:\d+')
CITATION = re.compile(r'^(?:Shift (\d)|All shifts), (\w+), ([0-9a-f]{32}:\d+), row (\d+)$')
COMPARISON_WITHDRAWN_LINE = 'A comparison was withdrawn because it contradicted its own figures.'
# Exact over summary (issue #113), stated here independently of the router. The KPI summary (`measure=kpis`) answers
# "how did we do": a question that names a countable noun or asks for a unit never lands on it. And a count or unit
# question's headline carries the unit it asked for (first match wins: a ratio before the count of its parts).
# A specific thing named counts too: a stop reason or place, a board grade, a customer, dollars (the owner's audit list).
QUANTITY_NOUN = re.compile(r'\b(set-?ups?|paper changes?|order changes?|changeovers?|change (the )?paper|wet[- ]ends?|stops|stoppages?|'
                           r'splices?|step[- ]?ups?|rolls?|sheets?|boards?|boxes|chops?|(grade|flute) changes?|orders?|customers?|jams?|'
                           r'glue|starch|steam|stacker|splicers?|'
                           r'knife|knives|slitter|outages?|raw material|double backer|single facer|double ?wall|\d{2,3}-?(c|bc|eb))\b'
                           r'|\bper (set-?up|paper change|stop|wet[- ]end)\b', re.I)
ASKED_UNIT = re.compile(r'\b(minutes|mins|hours?|hrs?|feet|ft|footage|msf|sq ft|square feet|tons?|percent|costs?|dollars?|money)\b|[%$]', re.I)
COUNT_ASKED = r'\b(?:how many|number of|count of|total)\b.*'
# A ratio is in the unit it names ("minutes per setup" is not feet per setup, "feet per stop" is not the stops; the coordinator's
# re-review of PR 143, item 1); a bare "per setup" is the run length in feet.
PER_SPAN = r'(set-?up|paper change|wet[- ]end|order|stop|day|week|run|hour|minute)s?\b'
PER_NUMERATOR = {'lineal feet': 'ft', 'lineal ft': 'ft', 'lineal': 'ft', 'feet': 'ft', 'ft': 'ft', 'footage': 'ft', 'length': 'ft',
                 'minutes': 'min(?:utes)?', 'mins': 'min(?:utes)?', 'time': 'min(?:utes)?', 'downtime': 'min(?:utes)?', 'sq ft': 'sq ft',
                 'square feet': 'sq ft', 'volume': 'MSF', 'output': 'MSF', 'throughput': 'MSF', 'production': 'MSF'}
PER_DENOMINATOR = {'wet end': 'paper change', 'wet-end': 'paper change', 'day': '(?:production )?day', 'minute': 'min(?:ute)?'}
# The numerator of a "per" is the last unit or countable noun before it in its clause ("how much downtime did we have per stop" is
# minutes per stop; round 5 of the coordinator's review, item 3), a duration asked with none named is minutes ("how long are we down
# per stop"), and with neither, any other word right before the "per" names its numerator ("cost per setup" is not feet per setup;
# round 4, item 2), so an answer in another unit fails; a filler, a volume verb or a window is a bare "per" ("what was our per setup",
# "how much did we make per day"), and a speed per anything is the speed answer.
PER_NOUN = re.compile(r'\b(lineal feet|lineal ft|square feet|sq ft|lineal|feet|ft|footage|length|minutes|mins|time|downtime|msf|volume|'
                      r'output|throughput|production|sheets|stops|stoppages|setups?|set-ups?|paper changes?|splices?|jams?|hours|dollars)\b',
                      re.I)
ANY_PER = r"\b([a-z']+)\s+per\s+(?:production\s+)?" + PER_SPAN
BARE_PER = (r"^(what|whats|what's|was|is|were|are|our|my|the|we|us|did|do|does|how|much|many|average|avg|mean|typical|overall|run|ran|"
            r"running|make|made|making|produce|produced|producing|get|got|go|went|this|that|its|it's|a|an|on|of|for|in|at|so|far|today|"
            r"yesterday|week|weeks|month|months|period|day|days|shift|shifts|speed|speeds|pace|fpm)$")


def named_per(question):
    """The headline pattern of a ratio the question names ("feet per stop" wants `ft per stop`), or None."""
    named = re.search(ANY_PER, question, re.I)
    if not named:
        return None
    clause = re.split(r'[,;:?.!]|\band\b|\bversus\b|\bvs\b', question[:named.end(1)], flags=re.I)[-1]
    nouns = PER_NOUN.findall(clause)
    if re.search(r'\b(?:speeds?|pace|fpm|fast\w*|slow\w*)\b', clause, re.I):
        return None   # "average speed per day": the speed answer
    numerator = (nouns[-1].lower() if nouns else 'minutes' if re.search(r'\bhow long\b|\bhow much time\b', clause, re.I) else
                 None if re.search(BARE_PER, named[1].lower()) else named[1].lower())
    if numerator is None:
        return None
    numerator, denominator = re.sub(r'set-ups?', 'setups', numerator), re.sub(r'set-?up', 'setup', named[2].lower())
    if (numerator, denominator) == ('stops', 'day'):
        return r'\bstops\b'   # the stops of each production day (the stops profile), led by the day with the most: not a ratio
    if numerator in ('minutes', 'mins', 'downtime') and denominator == 'day':
        # the downtime of each production day, led by the day with the most minutes (round 4, item 9); a percent asked is a percent
        return r'%' if re.search(r'\bpercent(?:age)?\b|%', question, re.I) else r'\bmin(?:utes)?\b'
    return (rf'\b{PER_NUMERATOR.get(numerator, re.escape(numerator))}(?: per |/)'
            rf'{PER_DENOMINATOR.get(denominator, re.escape(denominator))}\b')


ASKED_UNITS = (# an average stop is minutes per stop; an average setup or run length is in no unit an answer keeps, so only a refusal
               # passes; the longest or shortest of anything is no total (round 4 of the coordinator's review of PR 143, item 4)
               (r'\b(?:average|avg|mean|typical)\s+(?:[a-z/-]+\s+){0,3}?(?:stop|stoppage)\b'
                r'|\b(?:average|avg|mean|typical)\s+(?:length|duration|time) of (?:a |the |our )?(?:stop|stoppage)s?\b', r'\bmin(?:utes)?(?: per |/)stop\b'),
               # ... and a run length is the router's own feet per setup ("average run length"; round 5, item 3)
               (r'\brun length\b', r'\bft(?: per |/)setup\b'),
               (r'\b(?:average|avg|mean|typical)\s+(?:set-?up|changeover|paper change|order change|run)s?\s+(?:length|duration|time)s?\b'
                r'|\b(?:average|avg|mean|typical)\s+(?:length|duration|time) of (?:a |the |our )?(?:set-?up|changeover|paper change|order change|run)s?\b',
                r'\bmin(?:utes)?(?: per |/)(?:setup|changeover|paper change|order change|run)\b'),
               # feet a minute is the speed (round 5, item 5)
               (r'\b(?:feet|ft) (?:a|an|per) min(?:ute)?\b', r'\bft/min\b'),
               (r'\b(?:longest|shortest|maximum|minimum|max)\s+(?:single\s+)?(?:stops?|stoppages?|set-?ups?|runs?|changeovers?|paper changes?|'
                r'order changes?)\b', r'\b(?:longest|shortest|maximum|minimum)\b'),
               # an average of a count or a total is a ratio, never the total ('average stops this week')
               (r'\b(?:average|avg|mean)\b(?!.*\bper\b)(?=.*\b(?:stops|setups|paper changes|order changes|changeovers|sheets|feet|ft|minutes|mins)\b)',
                r'(?: per |/)'),
               (r'\bminutes per stop\b', r'\bmin(?:utes)?(?: per |/)stop\b'),
               (r'\b(?:lineal|feet|ft|footage) per (?:paper change|wet[- ]end)\b|(?<!minutes )(?<!msf )\bper paper change\b', r'\bft(?: per |/)paper change\b'),
               (r'\b(?:lineal|feet|ft|footage) per set-?up\b|(?<!minutes )(?<!mins )(?<!msf )(?<!sheets )(?<!stops )\bper set-?up\b', r'\bft(?: per |/)setup\b'),
               (r'\bsquare f(?:ee|oo)t\b|\bsq\.? ?ft\b|\bsqft\b', r'\bsq ft\b'),
               (r'\bpercent(?:age)?\b|%', r'%'),
               (r'\bin msf\b|\bhow (?:many|much) msf\b', r'\bMSF\b'),
               (r'\bhow many (?:hours|hrs)\b|\b(?:hours|hrs) (?:did|were|was|lost)\b|\bin (?:hours|hrs)\b'   # not a span ("the first hours of the shift")
                r'|\b(?:hours|hrs) of (?:downtime|stops?|maintenance|operator)\b', r'\bh(?:ou)?rs?\b'),
               (r'\bhow many (?:minutes|mins)\b|\b(?:minutes|mins) (?:of|did|were|was|lost)\b|\bin minutes\b|\btotal (?:downtime )?minutes\b'
                r'|\bhow long\b|\bhow much time\b', r'\bmin(?:utes)?\b'),
               # stops said beat any other count noun (round 6, item 4): "how many stops for order changes" are stops, never order changes
               (COUNT_ASKED + r'\b(?:stops|stoppages)\b', r'\bstops?\b'),   # "1 stop" is the count too
               (COUNT_ASKED + r'\b(?:order changes?(?!\s+(?:stops?|downtime|minutes)\b)|changeovers?)\b', r'\border changes\b'),   # order change stops are stops
               (COUNT_ASKED + r'\b(?:paper changes?|wet[- ]ends?)\b', r'\bpaper changes\b'),
               (COUNT_ASKED + r'\bset-?ups?\b', r'\bsetups\b'),
               (COUNT_ASKED + r'\b(?:stops|stoppages|missed splices?|splices (?:did )?we miss)\b', r'\bstops?\b'),
               (COUNT_ASKED + r'\bsheets\b', r'\bsheets\b'),
               (r'\bhow many (?:lineal )?(?:feet|ft)\b|\blineal feet\b', r'\b(?:lineal )?ft\b'))


# A breakdown, a grouping or a comparison of shifts is never one total (round 4 of the coordinator's review of PR 143, items 5 and 7),
# and a shift named by its rank is never the shift the control selects (item 1).
GROUPED_ASK = (r'\bbreak ?downs?\b|\b(?:by|per|(?:at |for |in |on )?(?:each|every))\s+(?:stop\s+|downtime\s+|piece of\s+)?'
               r'(?:reason|place|equipment|kind|location|area|machine|section|line|family|shift)s?\b|\b(?:waste )?lines\b'
               r'|\bcompar\w*\s+(?:the\s+|all\s+(?:the\s+)?|our\s+|three\s+)?shifts\b|\bshift by shift\b|\bbetween (?:the )?shifts\b'
               r'|\bshift comparison\b|\bcompar\w*\b.*\bacross (?:the )?shifts\b|\bshifts? (?:vs|versus) shifts?\b|\bshifts\b.*\bstack up\b')
RANKED_SHIFT = (r'\b(?:slowest|fastest|quickest|best|worst|top|strongest|weakest|busiest|highest|lowest)\s+(?:shifts?|crews?|teams?)\b'
                r'|\bshifts?\s+(?:that|which)\s+(?:ran|was|were|did|had)\s+(?:the\s+)?(?:slowest|fastest|best|worst|most|least|fewest)\b'
                r'|\bshifts?\s+with\s+the\s+(?:most|least|fewest|highest|lowest|best|worst)\b')


# The allow-list (round 6 of the coordinator's review of PR 143), stated on the URL. One quantity (measure=quantity) and the stops of
# each production day (measure=stops) answer only a question whose every word the URL consumes: the ask words, the measure's noun and
# unit (metric=), the words of the reason, place, kind, waste line or grade it filters by and the connector before them, the window,
# a window compared with the one before, the shift, a kept ratio's "per <denominator>" and the average it is, and FUNCTION_WORDS. Any
# other word left asks a different question ("how many brief stops", "stops at the slitter", "stop rate"), so the URL fails. This is
# its own statement of the router's rule (agent.FUNCTION_WORDS, agent.allow_list); tests/test_quantity_questions.py checks that the
# two function-word tables are one. A word is in the table only if it carries no meaning a route could ignore (issue #153): a plan or a
# capacity ("can", "could", "would", "will", "need", "want", "to", "go"), the line running ("up", "went") and a span to date ("far")
# are not, so a URL that answers one fails; a request said first ("I need the number of", "can you tell me"), the verb of "how did it
# go", a particle ("rack up") and "compared to last week" are read by the patterns below.
FUNCTION_WORDS = frozenset((
    'a', 'an', 'the', 'this', 'that', 'these', 'those', 'any', 'all', 'some', 'our', 'my', 'your', 'its', 'their',
    'i', 'me', 'we', 'us', 'you', 'it', 'they', 'there', "what's", 'whats', "it's", "there's", "we've",
    'what', 'how', 'many', 'much', 'number', 'count', 'total', 'totals', 'overall', 'altogether', 'combined', 'amount',
    'is', 'are', 'was', 'were', 'be', 'been', 'do', 'does', 'did', 'have', 'has', 'had',
    'run', 'ran', 'make', 'made', 'get', 'got', 'see', 'saw', 'log', 'logged', 'record', 'recorded', 'rack', 'racked',
    'happen', 'happened', 'occur', 'occurred', 'show', 'tell', 'give', 'done', 'complete', 'completed',
    'know', 'of', 'in', 'on', 'at', 'for', 'from', 'so', 'please', 'line', 'corrugator', 'machine', 'plant'))
_AVG_ANY = r'\b(?:on\s+(?:the\s+)?)?(?:averag(?:e|es|ed|ing)|avg|mean|typical(?:ly)?)\b'
_PER_DAY = r'\b(?:per (?:production )?days?|a day)\b|\bdaily\b'
_EACH_DAY = (r"\b(?:per (?:production )?days?|(?:each|every|a|by) (?:production )?day(?:'s)?|day by day)\b|\bdaily\b"
             r"|\b(?:each|every) day's\b")
_STOP = r'\bstop(?:s|ped|page|pages)?\b|\b(?:go|goes|went|gone|going)\s+down\b'   # "go down" is the line stopping
_MINUTES = (r'\b(?:lost|lose|loses|losing|went|gone)\s+to\s+(?:downtime|down\s*time|stops|stoppages)\b|\b(?:go|goes|went|gone|going)\s+down\b|\b(?:minutes?|mins?|downtime|down\s*time|down|time|lost|lose|losing|stop(?:s|ped|page|pages)?|take|takes|took|taking|'
            r'spend|spent)\b|\blasted\b'   # "last" the verb only: "how long did stops last", never "the last stop"
            r'|\blasts?\b(?!\s+(?:stops?|stoppages?|time|times|one|shift|set-?up|run|week|month|day|period|year|\d))')
_FEET = r'\b(?:lineal|linear|feet|foot|ft|footage)\b'
_GROUPED = (r'\b(?:by|per|(?:at |for |in |on )?(?:each|every))\s+(?:stop\s+|downtime\s+|piece of\s+)?(?:reason|place|equipment|kind|'
            r'location|area|machine|section)s?\b', r'\bbreak\s*downs?(?:\s+of)?\b|\bbroken\s+down\b', r'\bpareto\b')
QUANTITY_CONSUMES = {   # each metric's own words: its noun and unit, a kept ratio's "per" and the average it is
    'setups': (r'\bset-?ups?\b|\bset ups?\b',),
    'paper_changes': (r'\bpaper changes?\b|\bwet[- ]?ends?(?:\s+(?:changes?|runs?))?\b|\bpaper runs?\b',),
    'order_changes': (r'\border change(?:over)?s?\b|\bchangeovers?\b',),
    'sheets': (r'\bsheets?\b', r'\bcut\b', r'\b(?:at|on|off|from)\s+(?:the\s+)?(?:knife|knives)\b'),
    'stops': (_STOP, r'\btimes\b'), 'maintenance_stops': (_STOP, r'\btimes\b'), 'operator_stops': (_STOP, r'\btimes\b'),
    'downtime_minutes': (_MINUTES, r'\bdowntime\s+(?:length|duration)s?\b'),
    'maintenance_minutes': (_MINUTES,), 'operator_minutes': (_MINUTES,),
    'minutes_per_stop': (_AVG_ANY, r'\bper\s+(?:stop|stoppage)s?\b', _MINUTES, r'\b(?:length|duration)s?\b'),
    'lineal_per_setup': (_AVG_ANY, r'\bper\s+set-?ups?\b', _FEET, r'\b(?:run\s+)?lengths?\b'),
    'lineal_per_paper_change': (_AVG_ANY, r'\bper\s+(?:paper changes?|wet[- ]ends?)\b', _FEET, r'\blengths?\b'),
    'lineal_ft': (_FEET, r'\bof\s+(?:paper|board)\b'),
    'msf_per_day': (_AVG_ANY, _PER_DAY, r'\b(?:msf|volume|output|throughput|production|produce[ds]?|producing)\b'),
    'downtime_per_day': (_EACH_DAY, _MINUTES),
    'bale_msf': (r'\bmsf\b', r'\b(?:waste|bale[ds]?|scrap)\b'),
    'waste_sqft': (r'\bsquare f(?:ee|oo)t\b|\bsq\.? ?ft\b|\bsqft\b',),
    'waste_pct': (r'\bpercent(?:age)?\b|%', r'\bof\s+(?:the\s+)?gross\b'),
    'stops_per_day': (_EACH_DAY, _STOP, r'\bby\s+weekday\b|\bday\s+of\s+the\s+week\b|\bweekday\s+profile\b|\bweekdays?\b|\bprofile\b'
                      r'|\bafter\s+the\s+weekend\b|\b(?:mondays|tuesdays|wednesdays|thursdays|fridays)\b|\b(?:stand|stick)s?\s+out\b'),
    # a count or minutes grouped by reason, place or kind (measure=pareto, issue #153 row 3): the grouping, and the measure's words
    'stops_by_group': (*_GROUPED, _STOP, r'\btimes\b', r'\bdown\s*time\b'),
    'minutes_by_group': (*_GROUPED, _MINUTES),
}
# The words a filter is said with beyond its own name (a URL slug names it: reason=missed-splice, place=splicer-3, waste=dry-end)
FILTER_SAID = {'missed-splice': r'\bmiss(?:ed|es)?\b', 'order-change': r'\border change(?:over)?s?\b|\bchangeovers?\b',
               'knife-slitter-jam': r'\bjams?\b', 'trim': r'\bside\b', 'shear': r'\bchops?\b',
               'dry-end': r'\bdry[- ]end\b|\brejects?\b|\bquality\b', 'bale': r'\b(?:waste|bale[ds]?|scrap)\b'}
_FILTER_CONNECTOR = (r'\b(?:(?:(?:can|could|may|might)\s+be\s+)?(?:caused|triggered|brought\s+on|lost|made|due|related|attributed|coded|charged|went|gone|'
                     r'go|goes|going)\s+)?(?:by|to|from|as)\s+'
                     r'(?:the\s+)?zfilteredz|\bbecause\s+of\s+(?:the\s+)?zfilteredz|zfilteredz')
# ... but an average stop is its length only (round 5, item 2): its count, frequency, rate, interval, cost, place, location or reason
# is never minutes per stop
NOT_A_LENGTH = r'\b(?:average|avg|mean|typical)\s+(?:stop|stoppage)\s+(?:count|frequency|rate|interval|cost|place|location|reason|number)s?\b'
_MONTHS = r'(?:january|february|march|april|may|june|july|august|september|october|november|december)'
_ALL_SHIFTS = r'\b(?:(?:across|on|for|over|in)\s+)?all\s+(?:the\s+|three\s+)*shifts\b|\bacross\s+(?:the\s+)?shifts\b|\bshifts\s+(?:together|combined)\b'
QUANTITY_CONSUMES_ALWAYS = (   # every route's: the window, the shift, how long or often, the whole line
    # a preposition before a window is the window's ("during the week", "over the week": issue #153, row 10), read before "the week"
    r'\b(?:across|over|during|for|in|on|through)\s+(?:the|this|that|all)\s+(?:whole\s+|entire\s+|full\s+)?(?:production\s+)?(?:week|month|period|day)\b',
    r'\b(?:this|that|the|current|selected|whole|full|entire)\s+(?:production\s+)?(?:day|week|month|(?:accounting\s+|fiscal\s+|4-4-5\s+)?period)\b',
    r'\b(?:(?:across|over|during|for|in|on|through)\s+)?(?:(?<!the )last|previous|prior)\s+(?:week|month|(?:accounting\s+)?period)\b(?!\s+(?:before|of)\b)',   # the question's own move (issue #105)
    rf'\b(?:(?:across|over|during|in|for|through)\s+)?(?:all\s+of\s+)?(?:the\s+month\s+of\s+)?{_MONTHS}\b',
    r'\ball\s+(?:week|month|day|period)\s+long\b', r'\b(?:fiscal|accounting)\s+period\b|\b4-4-5\b|\b445\b|\bp(?:1[0-3]|[1-9])\b',
    r'\byesterday\b',
    r'\b(?:(?:on|for|by|in|during|from)\s+)?(?:the\s+)?(?:shift\s*[1-3]|(?:first|second|third|1st|2nd|3rd)\s+shift|'
    r'(?:this|that|my|our|the)\s+shift)\b',
    _ALL_SHIFTS,
    r'\bhow long\b', r'\bhow often\b',
    r'\b(?:across|over|on|for)\s+(?:the\s+)?(?:whole\s+|entire\s+)?(?:line|corrugator|machine|plant)\b', r'\bof\s+any\s+(?:type|kind|sort)\b',
    # a request said first ("I need the number of stops", "I want to know", "can you tell me", "could I see"), never "do we need" or
    # "can we run"; a particle ("rack up", "add up"), never "up" the state (issue #153)
    r'^\s*(?:(?:(?:and|ok|okay|well)[,\s]+)?so\b(?!\s+far\b),?\s*)?(?:please\s+)?(?:(?:can|could|would|will)\s+(?:you|u)\b(?:\s+please\b)?'
    r'|(?:can|could|may)\s+(?:i|we)\s+(?:please\s+)?(?:see|get|have|know)\b'
    r'|(?:(?:i|we)\s+)?(?:need|want)\s+(?:you\s+to\s+(?:tell|show|give|find|count|check|get|pull|list)\b|to\s+(?:know|see|get|find\s+out)\b'
    r'|(?=(?:the|a|an)\s+(?:number|count|total|figure|tally)\b))'
    r"|(?:i|we)\s*(?:'d|\s+would)\s+like\s+to\s+(?:know|see|get|find\s+out)\b)",
    # "can you" asks the assistant wherever it stands ("can you get" is a capacity); "did we have to run" is what ran
    r'\b(?:can|could|would|will)\s+(?:you|u)\b(?:\s+please\b)?(?=\s*(?:tell|show|give|let|list|pull|find|count|check|see|say)\b|\s*[?.,!]|\s*$)',
    r'\bdid\s+(?:we|you|they|it|the\s+(?:line|corrugator|machine|plant|shift))\s+(?:have|need)\s+to\b',
    r'\b(?:rack|racks|racked|racking|add|adds|added|adding|pile|piles|piled|piling|sum|sums|summed|tot|tots|totted|tally|tallies|tallied)\s+up\b')
# the verb of "how did shift 1 go", "how is the week going" (the words between are read as they are)
HOW_DID_GO = r'\b(how\s+(?:did|does|do|has|have|is|was|are|were)\s+(?:[a-z0-9-]+\s+){0,3}?)(?:go|goes|gone|going|went)\b'
# a window compared with the one before ("compare stops this week to last week", "versus last week"), never the shifts compared
QUANTITY_WINDOW_COMPARED = (r'\b(?:compar\w*|versus|vs|better|worse|more|fewer|less|higher|lower)(?:\s+(?:to|with|against|than))?\b'
                            r'(?=(?:.*\b(?:to|with|against|versus|vs|than))?\s+(?:the\s+)?(?:last|previous|prior|week before|month before|day before)\b)')
ROLLING_WINDOW = (r'\b(?:last|past|previous|prior)\s+(?:\d+|few|couple(?:\s+of)?|several|two|three|four|five|six|seven|eight|nine|ten|'
                  r'eleven|twelve|fourteen|thirty)\s+(?:production\s+)?(?:days?|weeks?|months?|shifts?|hours?)\b')
# ... the word before the window compared ("than", "versus", "and", "this week to last week") and the window before itself
COMPARED_TO = r'\b(?:than|with|against|versus|vs|and)\b(?=\s+(?:the\s+)?(?:last|previous|prior|week before|month before|day before)\b)'
COMPARED_TO_SAID = r'\b(?:than|with|against|versus|vs|and|to)\b(?=\s+(?:the\s+)?(?:last|previous|prior|week before|month before|day before)\b)'
PREVIOUS_SAID_BEFORE = (r'\b(?:the\s+)?(?:last|previous|prior)\s+(?:week|month|period|day)\b'
                        r'|\b(?:the\s+)?(?:week|month|day|period)\s+before\b(?!\s+last)')
JOINED = (r'\b(?:this|current)\s+(week|month|period)\b.*\band\b.*\b(?:last|previous|prior)\s+\1\b'
          r'|\b(?:last|previous|prior)\s+(week|month|period)\b.*\band\b.*\b(?:this|current)\s+\2\b')   # two windows joined


def window_compared(text):
    """True when `text` compares a window with the one before ("versus last week", "this week and last week"), never the shifts."""
    return bool(not re.search(r'\bshifts\b', re.sub(_ALL_SHIFTS, ' ', text))
                and (re.search(QUANTITY_WINDOW_COMPARED, text) or re.search(COMPARED_TO, text) or re.search(JOINED, text)))


# ---- Words read but never applied (issue #153, the hotfix after PR 143), stated on the URL.
# A duration is downtime only when the question names a stop or downtime, a stop reason, place or kind (row 1): run time, uptime, the line
# on, up or running ("how many minutes were we up", "how long was the machine on", "minutes of machine time", "run time per stop") is
# never the minutes it stood.
DURATION_QUANTITIES = ('downtime_minutes', 'maintenance_minutes', 'operator_minutes', 'minutes_per_stop', 'downtime_per_day')
RUN_TIME_SAID = r'\b(?:run(?:ning)?|up|machine|line|production|operating|corrugator)[- ]?times?\b|\buptime\b|\bruntime\b'
DOWN_SAID = (r'\b(?:downtime|down\s*time|down|stop(?:s|ped|page|pages)?|stopping|lost|lose|loses|losing|idle|delays?|jams?|outages?|'
             r'missed)\b')
RUNNING_SAID = (r'\b(?:run|runs|ran|running|go|goes|went|going|gone|operat(?:e|es|ed|ing)|produc(?:e|es|ed|ing)|online|working)\b|\bup\b'
                r'|\b(?:is|was|were|are|been|be|being|stay(?:s|ed)?|kept|keep|turned|switched)\s+(?:(?:the\s+)?(?:line|machine|corrugator|plant|'
                r'it|we|they)\s+)?on\b')
# A window compared with the one before (rows 2 and 6) is the answer's only when the answer carries the window before (NO_PREVIOUS) and
# the windows said are the answered one and the one just before it, of the same length
PREVIOUS_SAID = (r'\byesterday\b|\b(?:last|previous|prior)\s+(?:production\s+)?(?:day|week|month|(?:accounting\s+)?period)\b'
                 r'|\b(?:the\s+)?(?:day|week|month|period)\s+before\b(?!\s+(?:last|yesterday)\b)')
BASE_SAID = r'\b(?:this|current)\s+(?:production\s+)?(?:day|week|month|(?:accounting\s+)?period)\b|\btoday\b'
# A rise or a fall of the measure itself ("were stops up", "did minutes per stop go down"), never the line running
_RISE_NOUNS = (r'(?:stops?|stoppages?|set-?ups?|paper changes?|order changes?|changeovers?|sheets?|downtime|minutes|feet|footage|msf|waste|'
               r'trim|shear|rejects?)(?:\s+per\s+[a-z-]+)?')
RISE_OF_MEASURE = (rf'\b(?P<before>(?:were|was|are|is|have|has|been)\s+(?:(?:the|our)\s+)?{_RISE_NOUNS}\s+)(?:up|down)\b(?!\s+(?:to|until|till)\b)'
                   rf'|\b(?P<after>{_RISE_NOUNS}\s+)(?:were|was|are|is|have|has|been|go|goes|went|gone|going)\s+(?:up|down)\b(?!\s+(?:to|until|till)\b)')


def without_rise(text, keep=' | '):
    """`text` with a measure's rise or fall (the measure its subject, with a verb) replaced by `keep`, the measure's words kept."""
    return re.sub(RISE_OF_MEASURE, lambda m: (m.group('before') or m.group('after')) + keep, text)
# "This shift", "my shift" (row 8): the selected shift, so its number's address names one
SELECTED_SHIFT_SAID = r'\b(?:this|that|my|our|the|current|selected)\s+shift\b'
# Who logged a stop (row 9): not its kind
KIND_LOGGED = (r'\b(?:operators?|maintenance|crews?|teams?)\s+(?:log|logs|logged|record|records|recorded|enter|enters|entered|report|reports|'
               r'reported|note|notes|noted)\b|\b(?:logged|recorded|entered|reported|noted|written)\s+by\s+(?:the\s+|an?\s+|our\s+)?(?:operators?|'
               r'maintenance|crews?|teams?)\b')
# A number and its cause asked together (row 7): two questions
NUMBER_ASKED = r'\bhow\s+(?:many|much|long|often)\b'   # "what drove the number of stops" is an investigation


def grain_said(words):
    """The window grain a window phrase names ("last week" week, "yesterday" day, "the period before" fiscal, a month's name month)."""
    for grain, pattern in (('day', r'\b(?:day|yesterday|today)\b'), ('week', r'\bweek\b'), ('month', rf'\bmonth\b|\b{_MONTHS}\b'),
                           ('fiscal', r'\bperiod\b')):
        if re.search(pattern, words):
            return grain
    return None


def url_window(url):
    """(grain, month) of the window a URL answers: `window=` when the question named one, else the path's grain (the selected day's
    window); the month is `YYYY-MM` for a named month, else None."""
    params = dict(parse_qsl(urlsplit(url).query))
    window = params.get('window')
    if window and re.fullmatch(r'\d{4}-\d{2}', window):
        return 'month', window
    if window:
        return {'period': 'fiscal'}.get(window, window), None
    grain = re.match(r'/corr/[^/]+/(day|week|month|period)/', urlsplit(url).path)
    return ({'period': 'fiscal'}.get(grain[1], grain[1]) if grain else None), None


def read_but_unapplied_failures(question, url):
    """Issue #153: a gated URL (one quantity, each production day's stops, a grouped Pareto) for a duration that names no downtime, a
    comparison its answer does not carry or of other windows than the answered one and the one before it, "this shift" with no shift in
    the address, or a kind said of who logged the stops."""
    params = dict(parse_qsl(urlsplit(url).query))
    metric, text = url_metric(params), question.lower()
    failures = []
    if metric in DURATION_QUANTITIES:
        said = re.sub(r'\bper\s+(?:stop|stoppage)s?\b', ' ', text) if metric == 'minutes_per_stop' else text
        connectors = without_rise(re.sub(r'\b(?:went|gone|go|goes|going)\s+to\b|\b(?:rack|racks|racked|racking|add|adds|added|adding)\s+up\b',
                                                        ' ', text))
        running = ((re.search(RUN_TIME_SAID, text) if not re.search(DOWN_SAID, said) else None)   # "machine time lost to downtime" is downtime
                   or re.search(r'\b(?:between|without|before|until|till)\s+(?:a\s+|any\s+|the\s+|each\s+)?(?:stop|stops|stoppages?|stopping|downtime)\b', text)
                   or (not re.search(DOWN_SAID, said) and re.search(RUNNING_SAID, connectors)))
        if running:
            failures.append(f'{question[:60]!r} asks how long the line ran ({running[0]!r}) but lands on {metric}: run time is never downtime')
        elif (not re.search(DOWN_SAID, said) and metric != 'minutes_per_stop' and not params.get('reason') and not params.get('place')
              and not metric.startswith(('maintenance_', 'operator_'))):
            failures.append(f'{question[:60]!r} names no stop or downtime but lands on {metric}: a duration is downtime only when asked')
    if window_compared(text):
        previous = [m.group(0) for m in re.finditer(PREVIOUS_SAID, text)]
        grain, month = url_window(url)
        grains = {grain_said(w) for w in previous} | {grain_said(m.group(0)) for m in re.finditer(BASE_SAID, text)}
        grains |= {'month'} if re.search(rf'\b{_MONTHS}\b', text) else set()
        day = re.search(r'/(\d{4}-\d{2})-\d{2}', urlsplit(url).path)
        if metric in NO_PREVIOUS or params.get('grade'):
            failures.append(f'{question[:60]!r} compares with the window before but lands on {metric or "a grade"}, which carries none beside it')
        elif len(previous) > 1 and not (len(previous) == 2 and not params.get('window') and grain == grain_said(previous[0])
                                        and not re.search(r'\b(?:compar\w*|versus|vs|than|against|relative)\b', text)):
            # two windows before the selected one, unless joined by "and" alone, where the first moved the scope to it (issue #105):
            # "how many stops last week and the week before" is last week with the week before beside it
            failures.append(f'{question[:60]!r} names two windows before the selected one ({previous[0]!r}, {previous[1]!r}) but lands on one window')
        elif previous and grains != {grain}:
            failures.append(f'{question[:60]!r} compares windows of another length ({", ".join(sorted(g for g in grains if g))}) than the '
                            f'answered {grain}')
        elif (previous and month and day and month != day[1] and re.search(r'\b(?:last|previous|prior)\s+month\b', previous[0])):
            failures.append(f'{question[:60]!r} compares a month with the month before the selected day, but lands on {month} beside its own')
    # a window said in words ("for the day", "this day", "over the period"; issue #153) is the window answered, never the selected one
    said = {grain_said(m.group(0)) for m in re.finditer(r'\b(?:the|this|that|current|selected|whole|full|entire)\s+(?:production\s+)?'
                                                         r'(?:day|week|month|period)\b(?!\s+(?:before|of\s+the\s+week)\b)', text)}
    answered = 'week' if metric in ('downtime_per_day', 'stops_per_day') and url_window(url)[0] == 'week' else url_window(url)[0]
    if (len(said) == 1 and not window_compared(text) and not re.search(rf'\b{_MONTHS}\b|\b(?:last|previous|prior)\b|\byesterday\b', text)
            and said != {answered} and not (metric in ('downtime_per_day', 'stops_per_day') and said == {'day'})):
        failures.append(f'{question[:60]!r} names {next(iter(said))} as its window but lands on the {answered}\'s')
    if re.match(r'\W*(?:please\s+)?may\s+(?:i|we)\b', text) and (url_window(url)[1] or '').endswith('-05') and \
            not re.search(r'\bmay\b(?!\s+(?:i|we)\b)', text):
        failures.append(f'{question[:60]!r} asks "may I", a request, but lands on May')
    if re.search(SELECTED_SHIFT_SAID, text) and not re.search(r'/shift/\d', urlsplit(url).path) and not re.search(r'\bshifts?\s*[1-3]\b', text):
        failures.append(f'{question[:60]!r} says {re.search(SELECTED_SHIFT_SAID, text)[0]!r} but its address names no shift: never every shift\'s figure')
    logged = re.search(KIND_LOGGED, text)
    if logged and (metric.startswith(('maintenance_', 'operator_')) or metric.endswith('_by_group')):
        failures.append(f'{question[:60]!r} says {logged[0]!r}, who logged the stops, but lands on {metric}, a stop\'s kind')
    return failures


# The Pareto grouped by reason, place or kind for a count or minutes asked (round 4, item 5) is the allow-list's too (issue #153, row 3),
# in the unit asked: a count asked is counted ("how many stops under 2 minutes at each place" is not minutes)
GROUPED_COUNT_OR_MINUTES = r'\b(?:how many|number of|count of|count|total)\b|\b(?:stops|stoppages|minutes|mins)\b'
MINUTES_ASKED = (r'\bhow many (?:minutes|mins)\b|\b(?:minutes|mins) (?:of|did|were|was|lost|down|we)\b|\bin minutes\b'
                 r'|\b(?:downtime|total|stop) (?:minutes|mins)\b')
EXPLICIT_PARETO = r'\bcost(?:ing)?(?: us)? the most\b|\bmost (?:downtime )?minutes\b|\bmost often\b|\bmost stops\b|\bmost frequent\b'


def grouped_pareto_failures(question, url):
    """A grouped count or minutes (measure=pareto from "how many stops by reason", "how many minutes of downtime at each place") that
    leaves a word unread, or answers minutes for a count asked or a count for minutes asked."""
    params, text = dict(parse_qsl(urlsplit(url).query)), question.lower()
    grouped = re.search(_GROUPED[0], text) or re.search(_GROUPED[1], text) and re.search(r'\b(?:downtime|stops?|stoppages?)\b', text)
    if (params.get('measure') != 'pareto' or not grouped or not re.search(GROUPED_COUNT_OR_MINUTES, text) or re.search(EXPLICIT_PARETO, text)
            or re.search(r'\bpercent(?:age)?\b|%|\b(?:average|avg|mean)\b|\bper (?:stops?|stoppages?|set-?ups?|paper changes?|wet[- ]ends?)\b', text)):
        return []
    counted = params.get('unit') == 'count'
    count_asked = re.search(r'\b(?:how many|number of|count)\b', text) and re.search(r'\b(?:stops|stoppages)\b', text)
    if counted and re.search(MINUTES_ASKED, text):
        return [f'{question[:60]!r} asks minutes but lands on the Pareto counted']
    if not counted and count_asked and not re.search(MINUTES_ASKED, text):
        return [f'{question[:60]!r} asks a count of stops but lands on the Pareto in minutes']
    unapplied = read_but_unapplied_failures(question, url)   # "this shift" with no shift, a comparison it does not carry
    if unapplied:
        return unapplied
    left = unconsumed_words(question, url)
    if left:
        return [f'{question[:60]!r} leaves {left[0]!r} unread but lands on the Pareto grouped ({url_metric(params)}): the Pareto applies no '
                'stop reason, place, kind or subset']
    return []


# A ratio kept for all stops or for the line (minutes per stop, each day's downtime, feet per setup) answers no one stop reason,
# place or kind (round 5, item 2): one named beside it is never the whole's figure
RATIO_QUANTITIES = ('minutes_per_stop', 'downtime_per_day', 'lineal_per_setup', 'lineal_per_paper_change', 'msf_per_day')
STOP_FILTER = (r'\b(maintenance|operators?|stacker|splicers?|knife|knives|slitter|jams?|glue|starch|steam|outages?|power|raw material|'
               r'double backer|single facer|glue dams?|missed splices?|order changes? (?:stops?|downtime)|planned|scheduled|unplanned)\b')


def url_metric(params):
    """The quantity a gated URL answers: one quantity's metric=, the stops of each production day (measure=stops), or a count or minutes
    grouped by reason, place or kind (measure=pareto, unit=count or minutes)."""
    if params.get('measure') == 'stops':
        return 'stops_per_day'
    if params.get('measure') == 'pareto':
        return 'stops_by_group' if params.get('unit') == 'count' else 'minutes_by_group'
    return params.get('metric', '')


# The answers that carry no window before beside them (issue #153, row 6): the over/under-run (sheets), the stops profile, each day's
# downtime, a grade's totals and the Pareto counted (the Pareto in minutes carries the previous minutes on every row). A comparison asked
# of one is never read.
NO_PREVIOUS = ('sheets', 'downtime_per_day', 'stops_per_day', 'stops_by_group')


def unconsumed_words(question, url):
    """The words of `question` that its URL (measure=quantity, measure=stops or a grouped measure=pareto) does not consume, in order:
    the allow-list's leftovers."""
    params = dict(parse_qsl(urlsplit(url).query))
    metric = url_metric(params)
    text = re.sub(r"['’]s\b", '', question.lower())
    filters = [f for key in ('reason', 'place', 'waste') for f in [params.get(key)] if f]
    patterns = [rf'\b{re.escape(word)}s?\b' for f in filters for word in f.split('-') if word]
    patterns += [FILTER_SAID[f] for f in filters if f in FILTER_SAID]
    if params.get('grade'):
        patterns += [r'\b' + re.escape(params['grade']).replace(r'\-', '-?') + r'\b', r'\b(?:board\s+)?grades?\b']
    if metric.startswith(('maintenance_', 'operator_')) or (metric.endswith('_by_group') and params.get('by') == 'kind'):
        patterns.append(r'\b(?:maintenance|operators?)\b')
    for pattern in patterns:
        text = re.sub(pattern, ' zfilteredz ', text)
    if 'zfilteredz' in text:   # "how many stops did the stacker cause", "stacker related stops"
        text = re.sub(r'\b(?:cause|causes|caused|related|driven)\b', ' zfilteredz ', text)
    # the week before beside a comparison, read only where the answer carries it (issue #153, row 6): never the over/under-run's
    compared = (QUANTITY_WINDOW_COMPARED, COMPARED_TO_SAID, PREVIOUS_SAID_BEFORE) + ((r'\band\b',) if re.search(JOINED, text) else ())
    if not window_compared(text) or metric in NO_PREVIOUS or params.get('grade'):
        compared = ()
    if metric not in NO_PREVIOUS and not params.get('grade'):
        text = without_rise(text)   # "were stops up", "did setups go up": the measure's rise, beside the window before
    text = re.sub(HOW_DID_GO, r'\1 | ', text)   # "how did shift 1 go": the verb only
    for pattern in (_FILTER_CONNECTOR, *compared, *QUANTITY_CONSUMES_ALWAYS, *QUANTITY_CONSUMES.get(metric, ())):
        text = re.sub(pattern, ' | ', text)
    left = [w for w in re.findall(r"[a-z0-9%$]+(?:[-'’][a-z0-9]+)*", text) if w not in FUNCTION_WORDS]
    # a misspelt measure word the router reads as the measure ("setpus", issue #105) is read here too: one edit from a word of the
    # question's measure, the same first letter
    measure_words = {'setups', 'setup', 'stops', 'stop', 'minutes', 'downtime', 'sheets', 'feet', 'lineal', 'changes', 'paper', 'order'}
    def misspelt(w):
        if w in measure_words or w.rstrip('s') in measure_words or w + 's' in measure_words:
            return False   # a measure word itself, spelt right, is left as it is: "stops" on an order-changes URL is unread
        return len(w) >= 4 and any(w[0] == m[0] and _one_edit(w, m) for m in measure_words)
    return [w for w in left if not misspelt(w)]


def _one_edit(a, b):
    """True when `a` is one insert, delete, substitution or swap of two neighbours from `b` (and not `b` itself)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def plain_quantity_failures(question, url):
    """The allow-list on a quantity's URL (measure=quantity, or the stops of each production day): a ratio asked of one stop reason,
    place or kind, an average stop that is not its length, a rolling window, or any word the URL does not consume, as a failure."""
    params = dict(parse_qsl(urlsplit(url).query))
    text = question.lower()
    named = re.search(STOP_FILTER, text)
    if params.get('metric') in RATIO_QUANTITIES and named:
        return [f'{question[:60]!r} names {named[0]!r} but lands on {params["metric"]}, kept for all stops together: a ratio asked of one '
                'stop reason, place or kind is refused, never the whole\'s figure']
    if params.get('metric') == 'minutes_per_stop' and re.search(NOT_A_LENGTH, text):
        return [f'{question[:60]!r} asks {re.search(NOT_A_LENGTH, text)[0]!r}, not the length of a stop, but lands on minutes per stop']
    if params.get('metric') == 'minutes_per_stop' and not re.search(r'\b(?:stops?|stoppages?)\b', text):
        return [f'{question[:60]!r} names no stop to average over ("how many minutes were we stopped on average") but lands on minutes per stop']
    if re.search(ROLLING_WINDOW, text):
        return [f'{question[:60]!r} asks a rolling window ({re.search(ROLLING_WINDOW, text)[0]!r}), which the records do not keep, but '
                f'lands on one quantity']
    unapplied = read_but_unapplied_failures(question, url)   # issue #153: words read but never applied
    if unapplied:
        return unapplied
    left = unconsumed_words(question, url)
    if not left:
        return []
    return [f'{question[:60]!r} leaves {left[0]!r} unread but lands on one quantity ({params.get("metric") or "stops per day"}): every '
            'word of a quantity question is its measure, its filter, the window, the shift or a function word, or it asks a different question']


# The owner's scope ruling (2026-11-13), stated on the URL: a question that asks for a number and names no shift answers all shifts, so a
# number's address under one selected shift (/shift/N) fails unless the question mentions a shift or investigates (why, what caused or
# drove, where it came from, what went wrong). The numbers are the store answers that read a window for every shift alike: one quantity,
# each production day's stops, the review, the Pareto, the waste breakdown, the totals by grade, rollstock, the over/under-run, and the
# speed of a window (measure=fpm with window=, or under a ranged grain); a production day's per-shift answers keep the controls.
SCOPED_NUMBER = (r'/shift/\d\?measure=(?:quantity|stops|review|pareto|waste|grade|rollstock|overrun)(?:&|$)'
                 r'|/(?:week|month|period)/[0-9-]+/shift/\d\?measure=fpm(?:&|$)|/shift/\d\?measure=fpm&(?:.*&)?window=')
INVESTIGATION = (r'\bwhy\b|\bwhat\s+(?:caused|causes|drove|drives|led\s+to|leads\s+to)\b|\bwhat\s*(?:is|was|are|were|\'s)\s+(?:causing|driving|behind)\b'
                 r'|\bcom(?:e|es|ing)\s+from\b|\bwhat\s+went\s+wrong\b')


def catch_all_failures(question, url):
    """A question naming a countable noun or a unit, or asking "how many" of anything, that resolved to the KPI summary; or a
    quantity asked ("how many", "how much", "how long", a unit) that resolved to the overview or the summary, which answer "why
    was it slow" and "summarize" (issue #113; the coordinator's review of PR 143, item 5). And one quantity (measure=quantity)
    for a breakdown, a grouping, a comparison of shifts or a ranked shift; or a quantity measure (one quantity, the stops of each
    production day) answered for the selected shift when the question names none (round 4, items 1, 5, 7 and 8)."""
    failures = []
    if re.search(r'[?&]measure=quantity(&|$)', url or ''):
        grouped = re.search(GROUPED_ASK, question, re.I) or re.search(RANKED_SHIFT, question, re.I)
        if grouped:
            failures.append(f'{question[:60]!r} asks {grouped[0]!r} but lands on one quantity (measure=quantity): a breakdown, a grouping '
                            'or a ranked shift is never one total')
        else:
            failures.extend(plain_quantity_failures(question, url))   # round 5: the plain-quantity gate
    if re.search(r'[?&]measure=stops(&|$)', url or '') and re.search(STOP_FILTER, question, re.I):
        # each production day's stops are every stop's (round 5): never the answer to one reason's, place's or kind's
        failures.append(f'{question[:60]!r} names {re.search(STOP_FILTER, question, re.I)[0]!r} but lands on every stop of each production day')
    elif re.search(r'[?&]measure=stops(&|$)', url or ''):
        failures.extend(plain_quantity_failures(question, url))   # round 6: the allow-list on the stops of each production day
    if re.search(r'[?&]measure=pareto(&|$)', url or ''):
        failures.extend(grouped_pareto_failures(question, url))   # issue #153, row 3: the grouped Pareto is the allow-list's too
    if re.search(INVESTIGATION, question, re.I) and re.search(NUMBER_ASKED, question, re.I) and re.search(r'\band\b|[,;]|[?.!]\s*\S|\s[-–—]+\s|[–—]', question, re.I) and url and \
            not re.search(r"\bwhat\s*(?:(?:is|was|are|were|'s)\s+)?(?:driving|drove|drives|caused|causes|causing|behind|led\s+to|leads\s+to)\b[\s,;:–—-]*"
                          r'(?:[a-z]+[\s,;:–—-]+){0,2}how\s+(?:many|much|long|often)\b', question, re.I) and \
            not re.search(r'[?&]measure=quantity(&|$)', url):
        # a number and its cause together (issue #153, row 7): two questions, never one reason's stops or minutes
        failures.append(f'{question[:60]!r} asks for a number and its cause together but lands on {url}, led by one reason or a summary')
    if re.search(SCOPED_NUMBER, url or '') and not re.search(r'\bshifts?\b', question, re.I) and not re.search(INVESTIGATION, question, re.I):
        failures.append(f'{question[:60]!r} asks for a number and names no shift but is answered for the selected one ({url}): the owner\'s '
                        'scope ruling answers it for every shift')
    if failures:
        return failures
    named = QUANTITY_NOUN.search(question) or ASKED_UNIT.search(question) or re.search(r'\b(how many|number of|count of)\b', question, re.I)
    if named and re.search(r'[?&]measure=kpis(&|$)', url or ''):
        return [f'{question[:60]!r} names {named[0]!r} but lands on the KPI summary (measure=kpis): a quantity question '
                'opens with its quantity or is refused']
    asked = re.search(r'\b(how many|number of|count of|how much|how long|how often)\b', question, re.I) or ASKED_UNIT.search(question)
    diagnosis = re.search(r'\bwhy\b|\bslow\w*\b|\bfast\w*\b|\bspeeds?\b|\bfpm\b|\bft/min\b|\bfeet per minute\b|\bpace\b|\bbad\b|\bkilled\b'
                          r'|\bsummar', question, re.I)
    if asked and not diagnosis and re.search(r'[?&]measure=(overview|summary|compare)(&|$)', url or ''):
        return [f'{question[:60]!r} asks {asked[0]!r} but lands on a summary (overview, summary or the shift comparison): a quantity '
                'question opens with its quantity or is refused']
    return []


def asked_unit_failures(question, sections):
    """A count or unit question whose headline's lead clause (the first section with a bold figure that is not a bullet, else
    the first section, up to its first ` · `) does not carry the unit it asked for (issue #113): "how many minutes did knife
    jams take" answered `**10 stops** · 17.62 minutes` fails (the coordinator's re-review of PR 143, item 4)."""
    if re.search(INVESTIGATION, question, re.I):
        return []   # "why were there so many stops per day": a cause asked, not a unit (round 6, item 3)
    want = named_per(question) or next((unit for ask, unit in ASKED_UNITS if re.search(ask, question, re.I)), None)
    if want is None or not sections:
        return []
    head = next((s for s in sections if '**' in s and not s.startswith('- ')), sections[0]).split(' · ')[0]
    if not re.search(want, head, re.I):
        return [f'asked for /{want}/ but the headline leads with {head[:70]!r}']
    if re.search(r'\b(?:average|avg|mean|typical|median)\b', question, re.I) and re.search(r'\bthe most of\b|\bthe only production day\b', head):
        return [f'asked for an average but the headline leads with the busiest day: {head[:70]!r}']   # round 4, item 4
    return []


# ---------------------------------------------------------------- cases

def load_cases():
    """golden.json cases followed by the generated value cases."""
    cases = list(json.loads(GOLDEN.read_text())['cases'])
    if VALUES.exists():
        cases.extend(json.loads(VALUES.read_text())['cases'])
    return cases


_VALUES_STATUS = {}
_GOLDEN_STORE = {}


def golden_store():
    """The store the golden set is scored against: the rolling window ending GOLDEN_AS_OF. It is the
    process's default store when that holds the same window (pytest, whose tests/conftest.py pins
    INVESTIGATOR_AS_OF to GOLDEN_AS_OF; or a command line that sets it); otherwise (the command line
    without it, the deployed /evals page whose demo window ends yesterday) a second store built once,
    from the GOLDEN_PREBUILT_ENV folder when the image has it, else generated (about 12 s)."""
    if 'store' not in _GOLDEN_STORE:
        window = dataset_window(as_of=GOLDEN_AS_OF)
        if dataset_window() == window:
            _GOLDEN_STORE['store'] = default_store()
        else:
            _GOLDEN_STORE['store'] = RecordStore(window=window, prebuilt=os.environ.get(GOLDEN_PREBUILT_ENV, ''))
    return _GOLDEN_STORE['store']


def golden_hash():
    """The dataset hash the value goldens were generated for (golden_values.json), without building a store."""
    try:
        return json.loads(VALUES.read_text()).get('content_hash')
    except (OSError, ValueError):
        return None


def dataset_version():
    """The golden store's content hash; recordings and value goldens are scored only against it."""
    return golden_store().content_hash


def stale_values():
    """None when golden_values.json matches the live store, else the reason."""
    if 'message' not in _VALUES_STATUS:
        if not VALUES.exists():
            _VALUES_STATUS['message'] = 'golden_values.json missing; run python3 evals/make_value_goldens.py'
        else:
            recorded = json.loads(VALUES.read_text()).get('content_hash')
            live = golden_store().content_hash
            _VALUES_STATUS['message'] = None if recorded == live else (
                f'golden_values.json was generated for dataset {str(recorded)[:12]}, the live store is {live[:12]}: '
                'stale goldens; run python3 evals/make_value_goldens.py')
    return _VALUES_STATUS['message']


# ---------------------------------------------------------------- datasets

_DATASETS = {}


def dataset(day, corrupt=False):
    """A production day of the record store (the app's data source), built once per run."""
    key = (day, corrupt)
    if key not in _DATASETS:
        _DATASETS[key] = golden_store().day_analysis(day, corrupt=corrupt)
    return _DATASETS[key]


def close_datasets():
    for analysis in _DATASETS.values():
        analysis.close()
    _DATASETS.clear()
    close_server()


# ---------------------------------------------------------------- the URL path (issue #20)

_SERVER = {}
URL_MS = {}   # case id -> server time of its answers over GET, for the table and the JSON


def local_server():
    """One loopback workbench per run, started on first use, over the same golden store."""
    if 'server' not in _SERVER:
        from investigator.server import make_server
        server = make_server(0, store=golden_store())
        server.app.request_limit = 1_000_000   # the harness is not a visitor: the per-minute window is for people
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _SERVER['server'], _SERVER['thread'] = server, thread
    return _SERVER['server']


def close_server():
    if 'server' in _SERVER:
        server, thread = _SERVER.pop('server'), _SERVER.pop('thread')
        server.shutdown()
        thread.join()
        server.server_close()
        server.app.close()


def http_get(path):
    server = local_server()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
    try:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def bare_url(url):
    """A canonical address without its follow-up chain (`after=`): what `expect.url` records."""
    path, query = urlsplit(url).path, urlsplit(url).query
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != 'after']
    return path + ('?' + urlencode(kept, safe=',/?=:-') if kept else '')


def via_url(case, expect, turns, redirect_only=False):
    """Run the turns through the URL path: `GET /q` must redirect to the canonical address (the
    recorded `expect.url` when there is one), and that address must answer as the question would.
    Returns (results or None when a turn was refused, the refusal text, failures, server ms).
    `redirect_only` checks the resolution alone (a live case that records a URL)."""
    failures, results, after, ms, error, location = [], [], [], 0.0, None, None
    period = case.get('period', 'day')
    if case.get('url'):
        return via_address(case, expect)
    for text in turns:
        params = [('day', case.get('day', DEFAULT_DAY)), ('shift', case.get('shift', 2)), ('scope', case.get('scope', 'shift')),
                  ('period', 'accounting' if period == 'fiscal' else period)]
        if case.get('rates'):
            params.append(('rates', case['rates']))   # the case's rate overrides, as the page sends them (issue #31b)
        if case.get('corrupt'):
            params.append(('corrupt', '1'))
        params.append(('q', text))
        params.extend(after)
        status, headers, body = http_get('/q?' + urlencode(params))
        if status == 400:
            error = json.loads(body)['error']
            break
        if status != 302 or 'Location' not in headers:
            failures.append(f'GET /q answered {status}, expected a 302 to the canonical URL')
            break
        location = headers['Location']
        failures.extend(catch_all_failures(text, location))
        if redirect_only:
            continue
        status, headers, body = http_get(location)
        timing = re.search(r'dur=([0-9.]+)', headers.get('Server-Timing', ''))
        ms += float(timing[1]) if timing else 0.0
        if status == 400:
            error = json.loads(body)['error']
            break
        if status != 200:
            failures.append(f'GET {location} answered {status}')
            break
        result = json.loads(body)
        if result.get('url') != location:
            failures.append(f'the answer names {result.get("url")!r} as its address, served at {location!r}')
        results.append(result)
        after.append(('after', bare_url(location)))
    want = expect.get('url')
    if want and (location is None or bare_url(location) != want):
        failures.append(f'q= resolves to {bare_url(location) if location else repr(error)}, expected {want}')
    if error is not None:
        results = None
    return results, error, failures, ms


def via_address(case, expect):
    """A URL case (issue #117) over GET: its address answers as the in-process run did, or a hand-typed spelling
    redirects to `expect.url` and that address answers. Same return as `via_url`."""
    failures, results, ms, error, location = [], None, 0.0, None, case['url']
    status, headers, body = http_get(case['url'])
    if status == 302 and 'Location' in headers:
        location = headers['Location']
        status, headers, body = http_get(location)
    if status == 400:
        error = json.loads(body)['error']
    elif status != 200:
        failures.append(f'GET {location} answered {status}')
    else:
        timing = re.search(r'dur=([0-9.]+)', headers.get('Server-Timing', ''))
        ms = float(timing[1]) if timing else 0.0
        result = json.loads(body)
        if result.get('url') != location:
            failures.append(f'the answer names {result.get("url")!r} as its address, served at {location!r}')
        results = [result]
    want = expect.get('url')
    if want and error is None and bare_url(location) != want:
        failures.append(f'{case["url"]} resolves to {bare_url(location)}, expected {want}')
    return results, error, failures, ms


# ---------------------------------------------------------------- planners

class ScriptedPlanner:
    """Issues exactly one tool call and finishes on it: the value cases go through
    the real agent loop (validation, trace, summary) without a language model."""
    mode = 'scripted single tool call (value golden); no language model'
    semantic = True   # no model: a value case may call the semantic model's compiler (issue #117), which no model planner may

    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments

    def next_step(self, context):
        if not context['results']:
            return {'tool': self.tool, 'arguments': self.arguments}
        result = context['results'][-1]
        if result['ok']:
            return {'final': [result['result_id']]}
        return {'unavailable': result['error']['message']}


class ScriptedAnalyst:
    """Returns one fixed plan and, when the case scripts one, the narrative lines a model might
    stream: a `plan` case puts both through the real plan check, tool execution and stream
    validator (`Agent.ask_planned`, `NarrativeStream`) without a model. Without `narrative` it
    writes none."""
    planned = True
    mode = 'scripted plan (golden); no language model'

    def __init__(self, plan, narrative=None):
        self._plan = plan
        self._narrative = narrative

    def plan(self, context):
        return json.loads(json.dumps(self._plan))

    def narrate(self, question, evidence, emit=None):
        if self._narrative is None:
            raise ValueError('a scripted plan has no narrative')
        from investigator.narrative import NarrativeStream, evidence_packet
        stream = NarrativeStream(evidence_packet(evidence), emit)
        text = '\n'.join(self._narrative) + '\n'
        for i in range(0, len(text), 9):   # small text deltas, the way a model's stream arrives
            stream.feed(text[i:i + 9])
        return stream.finish()


def run_turn(agent, text, context):
    """One question through the agent: the planned round for an analyst, the loop for everything else."""
    if getattr(agent.planner, 'planned', False):
        return agent.ask_planned(text, context)
    return agent.ask(text, context)


# ---------------------------------------------------------------- checks

def renderings(value):
    """Every way the application formats a numeric field."""
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)) and value < 0:
        return renderings(-value)  # a signed delta prints as "-0.12"; the number token is "0.12"
    if isinstance(value, float):
        value = abs(value)         # -0.0 prints as "-0.00"; its number token is "0.00"
    if isinstance(value, int):
        return {str(value), f'{value:,}', f'{value:.1f}', f'{value:.2f}', f'{value:,.1f}', f'{value:,.2f}'}
    if isinstance(value, float) and math.isfinite(value):
        return {f'{value:.0f}', f'{value:.1f}', f'{value:.2f}', f'{value:,.0f}', f'{value:,.1f}',
                f'{value:,.2f}', f'{value:g}'}
    return set()


def evidence_vocabulary(trace):
    """Numbers and strings that tool output makes available to the prose."""
    numbers, strings = set(RULE_CONSTANTS), set()
    for result in trace:
        if not result['ok']:
            # A refused call's own message (a window outside the dataset range, a shift
            # outside the scope) is tool output the answer may quote verbatim; the numbers
            # inside it are the tool's, not the planner's.
            strings.add(result['error']['message'])
            continue
        data = result['data']
        # Every row set the envelope carries: rows, chart components, the weekday profile, reported
        # sub-reason details, and any other list of records a tool adds.
        row_sets = [data.get('rows', [])] + [c['rows'] for c in data.get('components', [])]
        row_sets += [v for k, v in data.items() if k not in ('rows', 'components', 'excluded_records')
                     and isinstance(v, list) and v and all(isinstance(r, dict) for r in v)]
        # Period evidence names its windows (labels carry dates) and the weeks it could not find.
        for window in (data.get('window'), data.get('previous_window')):
            if isinstance(window, dict):
                strings.update(v for v in window.values() if isinstance(v, str))
        strings.update(data.get('missing_weeks', []))
        for rows in row_sets:
            numbers.update(renderings(len(rows)))
            if rows and 'actual_fpm' in rows[0] and 'target_fpm' in rows[0]:
                numbers.update(renderings(sum(r['actual_fpm'] < r['target_fpm'] for r in rows)))
            for row in rows:
                for value in row.values():
                    if isinstance(value, str):
                        strings.add(value)
                    else:
                        numbers.update(renderings(value))
        # A band's words on the envelope (`*_band_text`, issue #101: a subtotal's or a total's band) are tool output, as
        # a row's are; only those exact strings, never the envelope's notes.
        strings.update(v for k, v in data.items() if isinstance(v, str) and k.endswith('band_text'))
        # Envelope scalars (a window total, a band width, a gross area) are tool output as much as rows are.
        for value in data.values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numbers.update(renderings(value))
        numbers.update(renderings(len(data.get('excluded_records', []))))
        for record in data.get('excluded_records', []):
            strings.update(str(v) for v in record.values() if isinstance(v, str))
    return numbers, strings


def unfaithful_numbers(lines, trace):
    """Numeric tokens in the prose that no tool row produced."""
    numbers, strings = evidence_vocabulary(trace)
    strings = sorted(strings, key=len, reverse=True)
    bad = []
    for line in lines:
        text = re.sub(r'\s*Evidence: .*$', '', line)
        text = EVIDENCE.sub(' ', text)   # `(query 2 · row 0)` names a step and a row; it states no measurement
        text = LOOK_LINK.sub(' ', text)  # a where-to-look question is the router's own text
        text = RUN_ID.sub(' ', text)
        text = re.sub(r'\b[Ss]hift [123]\b', ' ', text)
        for s in strings:
            if s:
                text = text.replace(s, ' ')
        for token in NUMBER.findall(text):
            token = token.rstrip(',')  # "523, better": the comma is punctuation, not a thousands separator
            if token not in numbers:
                bad.append(f'{token!r} in {line[:70]!r}')
    return bad


def unprovenanced(lines, trace):
    """Numbers on a cited line that its cited evidence does not hold (issue #75). A line with a `**figure**`
    carries exactly one, and an evidence reference `(query N · row M)` that resolves: step N of the trace, a
    successful tool call, row M of its rows (`detail M`, `weekday M`, `flag M`: of its sub-reason, weekday and flag lists).
    Every number in the figure is a rendering of a value of that row; with no row named, of the result as a
    whole: a count of its rows, a below-target count, or a total its envelope carries. Every other number on
    the line is one of those too, a count of the result's rows, or a stated rule constant: a line never
    states a figure from a row it does not cite."""
    bad = []
    for line in lines:
        figures = BOLD.findall(line)
        if not figures:
            continue
        refs = EVIDENCE.findall(line)
        if len(figures) != 1 or len(refs) != 1:
            bad.append(f'{len(figures)} figures and {len(refs)} evidence references in {line[:70]!r}')
            continue
        step, part, index = refs[0]
        step = int(step)
        source = trace[step - 1] if 0 < step <= len(trace) else None
        if source is None or not source['ok'] or source['tool'] == 'render_chart':
            bad.append(f'query {step} is not a successful tool call: {line[:70]!r}')
            continue
        data = source['data']
        rows = data.get('rows', [])
        if part:
            records = data.get(EVIDENCE_PARTS[part]) or []
            if int(index) >= len(records):
                bad.append(f'query {step} has no {part} {index}: {line[:70]!r}')
                continue
            record = records[int(index)]
            allowed = set().union(*(renderings(v) for v in record.values() if not isinstance(v, (str, list, dict))))
            names = [v for v in record.values() if isinstance(v, str)]
        else:
            allowed = renderings(len(rows))
            if rows and 'actual_fpm' in rows[0] and 'target_fpm' in rows[0]:
                allowed |= renderings(sum(r['actual_fpm'] < r['target_fpm'] for r in rows))
            allowed |= set().union(set(), *(renderings(v) for v in data.values() if isinstance(v, (int, float)) and not isinstance(v, bool)))
            names = [v for v in data.values() if isinstance(v, str)]
        for token in NUMBER.findall(figures[0]):
            if token.rstrip(',') not in allowed:
                bad.append(f'{token!r} is not in query {step} {part or "result"} {index}: {line[:70]!r}')
        beside = allowed | renderings(len(rows)) | set(RULE_CONSTANTS)
        rest = LOOK_LINK.sub(' ', EVIDENCE.sub(' ', line))
        for name in sorted(names, key=len, reverse=True):
            rest = rest.replace(name, ' ')
        rest = re.sub(r'\b[Ss]hift [123]\b', ' ', rest)
        for token in NUMBER.findall(rest):
            if token.rstrip(',') not in beside:
                bad.append(f'{token!r} on a line citing query {step} {part or "result"} {index} is not from it: {line[:70]!r}')
    return bad


def shape_failures(spec, sections):
    """`expect.shape`: one headline (the first section with a bold figure that is not a bullet), finding
    bullets after it with exactly one bold figure each, and a `Where to look` list of question links."""
    failures = []
    heads = [i for i, s in enumerate(sections) if '**' in s and not s.startswith('- ')]
    if len(heads) != 1:
        return [f'{len(heads)} headlines, expected one']
    head = heads[0]
    if 'headline_match' in spec and not re.search(spec['headline_match'], sections[head]):
        failures.append(f'headline {sections[head][:70]!r} does not match /{spec["headline_match"]}/')
    findings = []
    for s in sections[head + 1:]:
        if not s.startswith('- '):
            break
        findings.append(s)
    low, high = spec.get('bullets', [3, 5])
    if not low <= len(findings) <= high or any(len(BOLD.findall(b)) != 1 for b in findings):
        failures.append(f'{len(findings)} finding bullets right under the headline (want {low}-{high}, one bold figure each)')
    if 'Where to look' not in sections:
        return failures + ['no Where to look list']
    looks = []
    for s in sections[sections.index('Where to look') + 1:]:
        if not s.startswith('- '):
            break
        looks.append(s)
    low, high = spec.get('look', [2, 3])
    if not low <= len(looks) <= high or not all(LOOK_LINK.search(s) for s in looks):
        failures.append(f'{len(looks)} where-to-look links (want {low}-{high}, each ending with a question)')
    return failures


def invariants(mode, result, analysis, allowed_shifts, tools=TOOL_NAMES):
    """Checks every case must pass, whatever its category. `tools`: the tools the case may run."""
    failures = []
    trace = result['trace']
    if mode == 'offline' and (result['usage']['model_calls'] or result['usage']['cost_usd']):
        failures.append('offline run reported model usage')
    for step in trace:
        if step['ok'] and step['tool'] not in tools:
            failures.append(f'executed unknown tool {step["tool"]}')
        if step['ok'] and step['arguments'].get('shift') is not None and step['arguments']['shift'] not in allowed_shifts:
            failures.append(f'{step["tool"]} ran for shift {step["arguments"]["shift"]} outside scope {allowed_shifts}')
        if step['ok'] and step['arguments'].get('against_shift') is not None and step['arguments']['against_shift'] not in allowed_shifts:
            # a speed variance against another shift reads that shift too (issue #92)
            failures.append(f'{step["tool"]} read shift {step["arguments"]["against_shift"]} outside scope {allowed_shifts}')
    evidence_shifts = {step['data']['shift'] for step in trace if step['ok'] and step['tool'] != 'render_chart'
                       and step['data']['shift'] is not None}  # period tools report None for all shifts
    if evidence_shifts - set(allowed_shifts):
        failures.append(f'evidence for shifts {sorted(evidence_shifts - set(allowed_shifts))} outside scope')
    by_id = {step['result_id']: step for step in trace}
    for chart in result['charts']:
        source = by_id.get(chart['source_result_id'])
        if source is None:
            failures.append('chart cites a result that is not in the trace')
            continue
        rows = source['data']['rows'] if source['tool'] != 'get_shift_overview' else None
        if rows is not None and rows != chart['rows']:
            failures.append(f'chart rows differ from {source["tool"]} rows')
    bad = unfaithful_numbers(result['sections'], trace)
    if bad:
        failures.append('numbers not from tool output: ' + '; '.join(bad[:3]))
    bad = unprovenanced(result['sections'], trace)
    if bad:
        failures.append('figures not from their cited row: ' + '; '.join(bad[:3]))
    for paragraph in result.get('narrative', []):
        bad = unfaithful_numbers([paragraph['text']], trace)
        if bad:
            failures.append(f'narrative {paragraph["kind"]} has numbers not from tool output: ' + '; '.join(bad[:2]))
        for citation in paragraph['citations']:
            m = CITATION.match(citation)
            if not m:
                failures.append(f'malformed citation {citation!r}')
                continue
            shift, field, rid, row = (int(m[1]) if m[1] else None), m[2], m[3], int(m[4])
            source = by_id.get(rid)
            # `shift` may be cited as a field: the validator answers it from the result's metadata.
            row_has_field = row < len(source['data']['rows']) and (field in source['data']['rows'][row] or (field == 'shift' and shift is not None)) if source and source['ok'] else False
            if source is None or not source['ok'] or source['data']['shift'] != shift or not row_has_field:
                failures.append(f'citation does not resolve: {citation}')
        if paragraph['kind'] != 'limitation' and not paragraph['citations']:
            failures.append(f'{paragraph["kind"]} paragraph cites nothing')
    for table in result['tables']:
        if table['title'] in STORE_TOOLS:
            continue  # period and semantic tables are the whole store result; chart-row equality covers them above
        shift = table['shift']
        full = getattr(analysis, table['title'])(shift, **({'metric': table['metric']} if 'metric' in table else {}))
        if len(table['rows']) != len(full['rows']):
            failures.append(f'{table["title"]} table shows {len(table["rows"])} of {len(full["rows"])} rows')
    return failures


def excluded_failures(spec, steps):
    """`excluded_records`: a {count, shift, error_match, raw} requirement or a list of them.
    The excluded records of every successful tool call, deduplicated by record id, must
    satisfy the count and each listed requirement (raw values are the record's own)."""
    records = {}
    for step in steps:
        for record in step['data'].get('excluded_records', []):
            records[record['record_id']] = record
    wants = spec if isinstance(spec, list) else [spec]
    failures = []
    for want in wants:
        if 'count' in want and len(records) != want['count']:
            failures.append(f'{len(records)} excluded records, expected {want["count"]}')
        def matches(record):
            return (want.get('record_id') in (None, record['record_id'])
                    and want.get('shift') in (None, record['shift'])
                    and ('error_match' not in want or re.search(want['error_match'], record['error']))
                    and all(record['raw'].get(k) == v for k, v in want.get('raw', {}).items()))
        if any(k in want for k in ('record_id', 'shift', 'error_match', 'raw')) and not any(matches(r) for r in records.values()):
            failures.append(f'no excluded record matches {want}: have {[(r["record_id"], r["error"]) for r in records.values()]}')
    return failures


def expectations(expect, result):
    """Checks the case asks for explicitly."""
    trace = result['trace']
    failures = []
    if 'status' in expect and result['status'] != expect['status']:
        failures.append(f'status {result["status"]!r}, expected {expect["status"]!r}')
    if 'status_in' in expect and result['status'] not in expect['status_in']:
        failures.append(f'status {result["status"]!r} not in {expect["status_in"]}')
    if 'shifts' in expect and result['selected_shifts'] != expect['shifts']:
        failures.append(f'selected shifts {result["selected_shifts"]}, expected {expect["shifts"]}')
    ok_steps = [s for s in trace if s['ok']]
    for want in expect.get('tools', []):
        hit = any(s['tool'] == want['tool'] and all(s['arguments'].get(k) == v for k, v in want.get('arguments', {}).items())
                  for s in ok_steps)
        if not hit:
            failures.append(f'missing tool call {want}')
    if 'tools_any' in expect and not any(s['tool'] in expect['tools_any'] for s in ok_steps):
        failures.append(f'none of {expect["tools_any"]} was called')
    if 'tools_only' in expect:
        extra = {s['tool'] for s in ok_steps} - set(expect['tools_only'])
        if extra:
            failures.append(f'unexpected tools {sorted(extra)}')
    if 'tool_calls' in expect and len(trace) != expect['tool_calls']:
        failures.append(f'{len(trace)} tool calls, expected {expect["tool_calls"]}')
    if 'charts' in expect:
        want = expect['charts']
        n = len(result['charts'])
        if isinstance(want, int) and n != want:
            failures.append(f'{n} charts, expected {want}')
        if isinstance(want, dict) and n < want.get('min', 0):
            failures.append(f'{n} charts, expected at least {want["min"]}')
    if 'chart_units' in expect:
        units = [c.get('unit') for c in result['charts']]
        if units != expect['chart_units']:
            failures.append(f'chart units {units}, expected {expect["chart_units"]}')
    if 'tables' in expect:
        titles = sorted(t['title'] for t in result['tables'])
        if titles != sorted(expect['tables']):
            failures.append(f'tables {titles}, expected {sorted(expect["tables"])}')
    if 'coverage' in expect:
        wrong = [s['tool'] for s in ok_steps if s['tool'] != 'render_chart' and s['data']['coverage'] != expect['coverage']]
        if wrong:
            failures.append(f'coverage not {expect["coverage"]} for {wrong}')
    if 'excluded_records' in expect:
        failures.extend(excluded_failures(expect['excluded_records'], [s for s in ok_steps if s['tool'] != 'render_chart']))
    # sections_match: one pattern, or a list of patterns each of which some section matches
    for pattern in ([expect['sections_match']] if isinstance(expect.get('sections_match'), str) else expect.get('sections_match', [])):
        if not any(re.search(pattern, s, re.I) for s in result['sections']):
            failures.append(f'no section matches /{pattern}/')
    if 'shape' in expect:
        failures.extend(shape_failures(expect['shape'], result['sections']))
    if 'sections_not_match' in expect:
        hits = [s[:60] for s in result['sections'] if re.search(expect['sections_not_match'], s, re.I)]
        if hits:
            failures.append(f'section matches forbidden /{expect["sections_not_match"]}/: {hits[0]!r}')
    prose = [p['text'] for p in result.get('narrative', []) if p['kind'] != 'limitation']
    if 'narrative_not_match' in expect:
        hits = [t[:60] for t in prose if re.search(expect['narrative_not_match'], t, re.I)]
        if hits:
            failures.append(f'narrative matches forbidden /{expect["narrative_not_match"]}/: {hits[0]!r}')
    if 'narrative_match' in expect and not any(re.search(expect['narrative_match'], t, re.I) for t in prose):
        failures.append(f'no interpretation paragraph matches /{expect["narrative_match"]}/')
    if 'narrative_status' in expect and result.get('narrative_status') != expect['narrative_status']:
        failures.append(f'narrative_status {result.get("narrative_status")!r}, expected {expect["narrative_status"]!r}')
    if 'comparisons_withdrawn' in expect:
        # Issue #79: a paragraph whose ordering words contradict its own cited values is withdrawn,
        # counted, and disclosed in one limitation line (the issue's wording, not imported from the app).
        want, got = expect['comparisons_withdrawn'], result['usage'].get('comparisons_withdrawn', 0)
        disclosed = any(p['kind'] == 'limitation' and p['text'] == COMPARISON_WITHDRAWN_LINE for p in result.get('narrative', []))
        if got != want:
            failures.append(f'{got} comparisons withdrawn, expected {want}')
        if disclosed != bool(want):
            failures.append(f'the limitation line {COMPARISON_WITHDRAWN_LINE!r} is ' + ('missing' if want else 'shown with nothing withdrawn'))
    if 'history_length' in expect and len(result['context']['history']) != expect['history_length']:
        failures.append(f'history length {len(result["context"]["history"])}, expected {expect["history_length"]}')
    if 'max_cost_usd' in expect and result['usage'].get('cost_usd', 0) > expect['max_cost_usd']:
        failures.append(f'cost ${result["usage"]["cost_usd"]:.4f} above ${expect["max_cost_usd"]}')
    if 'rejected_tool_calls' in expect:
        n = sum(not s['ok'] for s in trace)
        if n < expect['rejected_tool_calls']:
            failures.append(f'{n} rejected tool calls, expected at least {expect["rejected_tool_calls"]}')
    return failures


# ---------------------------------------------------------------- value cases

def select_row(data, selector):
    """`$` the result envelope; `#N` row N; `field=value` the first row whose field
    prints as value; `set:...` the same inside another row set (weekday_profile)."""
    if selector == '$':
        return data
    rows_key, _, match = selector.partition(':') if ':' in selector else ('rows', '', selector)
    rows = data.get(rows_key)
    if not isinstance(rows, list):
        raise LookupError(f'no row set {rows_key!r}')
    if match.startswith('#'):
        index = int(match[1:])
        if index >= len(rows):
            raise LookupError(f'row {index} beyond {len(rows)} rows')
        return rows[index]
    key, _, value = match.partition('=')
    for row in rows:
        if str(row.get(key)) == value:
            return row
    raise LookupError(f'no row with {key}={value!r} among {[str(r.get(key)) for r in rows][:8]}')


def close_enough(got, want, tolerance):
    """[expected, tolerance]: null tolerance (and strings, booleans, lists, null) are exact."""
    if tolerance is None or want is None or isinstance(want, (str, bool, list)):
        if isinstance(want, bool) or isinstance(got, bool):
            return got is want
        return got == want
    if not isinstance(got, (int, float)) or isinstance(got, bool) or not math.isfinite(got):
        return False
    return abs(got - want) <= tolerance


def value_failures(expect, result):
    steps = [s for s in result['trace'] if s['tool'] != 'render_chart']
    if not steps:
        return ['no tool call was made']
    if not steps[0]['ok']:
        return ['tool call failed: ' + steps[0]['error']['message']]
    data = steps[0]['data']
    failures = []
    if 'row_count' in expect and len(data.get('rows', [])) != expect['row_count']:
        failures.append(f'{len(data.get("rows", []))} rows, expected {expect["row_count"]}')
    for selector, fields in expect.get('values', {}).items():
        try:
            row = select_row(data, selector)
        except (LookupError, ValueError) as exc:
            failures.append(f'{selector}: {exc}')
            continue
        for field, (want, tolerance) in fields.items():
            if field not in row:
                failures.append(f'{selector}.{field} missing')
            elif not close_enough(row[field], want, tolerance):
                failures.append(f'{selector}.{field} = {row[field]!r}, expected {want!r}' + (f' ±{tolerance}' if tolerance else ''))
    return failures


# ---------------------------------------------------------------- investigation cases

def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def find_row(rows, wanted):
    return next((r for r in rows if all(str(r.get(k)) == str(v) for k, v in wanted.items())), None)


def rule_candidates(rule, steps):
    """Successful tool results that fit a rule's tool, argument subset, window and row."""
    for step in steps:
        if not step['ok'] or step['tool'] != rule['tool']:
            continue
        if not all(step['arguments'].get(k) == v for k, v in rule.get('arguments', {}).items()):
            continue
        data = step['data']
        if 'window_covers' in rule:
            window = data.get('window') or {}
            lo, hi = rule['window_covers']
            if not window or window.get('start', '9') > lo or window.get('end', '0') < hi:
                continue
        rows = data.get(rule.get('rows', 'rows')) or []
        row = find_row(rows, rule['row']) if 'row' in rule else None
        if 'row' in rule and row is None:
            continue
        yield step, rows, row


def rule_holds(rule, rows, row):
    """None when the rule holds for this result, else why not."""
    field = rule.get('field')
    value = row.get(field) if row is not None else None
    if field and not is_number(value):
        return f'{field} is {value!r}'
    if 'at_least_times' in rule:
        other, factor = row.get(rule['at_least_times']['field']), rule['at_least_times'].get('factor', 1)
        if not is_number(other):
            return f'{rule["at_least_times"]["field"]} is {other!r}'
        if value < factor * other:
            return f'{field} {value} is below {factor} x {rule["at_least_times"]["field"]} {other}'
    if 'greater_than' in rule:
        other = row.get(rule['greater_than']['field'])
        if not is_number(other) or not value > other:
            return f'{field} {value} is not above {rule["greater_than"]["field"]} {other!r}'
    if 'change_from' in rule:
        spec = rule['change_from']
        base = find_row(rows, spec['row'])
        if base is None or not is_number(base.get(field)):
            return f'no base row {spec["row"]} with {field}'
        delta = value - base[field]
        if 'min' in spec and delta < spec['min']:
            return f'{field} changed {delta:+.4f} from {spec["row"]}, less than {spec["min"]}'
        if 'max' in spec and delta > spec['max']:
            return f'{field} changed {delta:+.4f} from {spec["row"]}, more than {spec["max"]}'
    if rule.get('max_among'):
        others = [r.get(field) for r in rows if r is not row and is_number(r.get(field))]
        if not others or not all(value > o for o in others):
            return f'{field} {value} is not the maximum of {sorted(others, reverse=True)[:3]}...'
    if 'min_value' in rule and value < rule['min_value']:
        return f'{field} {value} below {rule["min_value"]}'
    if 'max_value' in rule and value > rule['max_value']:
        return f'{field} {value} above {rule["max_value"]}'
    return None


def investigation_failures(expect, results):
    """Evidence rules over every turn, the found numbers in the prose, forbidden claims absent."""
    failures = []
    for index, result in enumerate(results, 1):
        if 'status' in expect and result['status'] != expect['status']:
            failures.append(f'turn {index} status {result["status"]!r}, expected {expect["status"]!r}')
        if 'status_in' in expect and result['status'] not in expect['status_in']:
            failures.append(f'turn {index} status {result["status"]!r} not in {expect["status_in"]}')
    steps = [s for r in results for s in r['trace']]
    sections = [line for r in results for line in r['sections']]
    narrative = [p for r in results for p in r.get('narrative', [])]
    prose = sections + [p['text'] for p in narrative]
    matched = []
    for index, rule in enumerate(expect.get('evidence', [])):
        reasons, hit = [], None
        for step, rows, row in rule_candidates(rule, steps):
            reason = rule_holds(rule, rows, row)
            if reason is None:
                hit = row if row is not None else {}
                break
            reasons.append(reason)
        matched.append(hit)
        if hit is None:
            label = rule.get('why') or f'{rule["tool"]} {rule.get("row", "")}'.strip()
            failures.append(f'evidence {index} ({label}): ' + ('; '.join(reasons[:2]) if reasons else
                            f'no {rule["tool"]} result matches {rule.get("arguments", {})} {rule.get("window_covers", "")}'))
    tokens = {t.rstrip(',') for t in NUMBER.findall(' '.join(prose))}
    for want in expect.get('prose_numbers', []):
        row = matched[want['evidence']] if want['evidence'] < len(matched) else None
        if not row:
            continue  # the evidence failure above already says why
        value = row.get(want['field'])
        if not renderings(value) & tokens:
            failures.append(f'prose never states evidence {want["evidence"]} {want["field"]} = {value!r}')
    claims = sections + [p['text'] for p in narrative if p['kind'] != 'limitation']
    for pattern in expect.get('forbidden', []):
        hits = [t[:80] for t in claims if re.search(pattern, t, re.I)]
        if hits:
            failures.append(f'asserts a cause the evidence lacks /{pattern[:50]}/: {hits[0]!r}')
    generic = {k: v for k, v in expect.items() if k not in ('status', 'status_in', 'evidence', 'prose_numbers', 'forbidden')}
    failures.extend(expectations(generic, results[-1]))
    return failures


# ---------------------------------------------------------------- live providers

def prompt_digest(payload):
    """OpenAI: the developer message; Anthropic: the system prompt."""
    if payload.get('input'):
        first = payload['input'][0]['content']
    else:
        first = payload.get('system') or ''
    return hashlib.sha256(first.encode()).hexdigest()[:12]


def exchange_record(payload, response, status=None):
    seen = RUN_ID.search(json.dumps(payload))
    record = {'run_id': seen.group(0).split(':')[0] if seen else None, 'model': payload['model'],
              'prompt_digest': prompt_digest(payload), 'tools': [t['name'] for t in payload.get('tools', [])], 'response': response}
    if status is not None:
        record['status'] = status
    return record


def replay_body(exchange, payload, warnings):
    if exchange.get('prompt_digest') != prompt_digest(payload):
        warnings.add('prompt changed since the fixture was recorded; re-record with --live')
    body = json.dumps(exchange['response'])
    current = RUN_ID.search(json.dumps(payload))
    if exchange.get('run_id') and current:
        body = body.replace(exchange['run_id'], current.group(0).split(':')[0])
    return body


def openai_record(exchanges):
    from investigator.openai_provider import transport

    def send(payload, key, timeout):
        response = transport(payload, key, timeout)
        exchanges.append(exchange_record(payload, response))
        return response
    return send


def openai_replay(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        return json.loads(replay_body(pending.pop(0), payload, warnings))
    return send


def anthropic_record(exchanges):
    from investigator.anthropic_provider import transport

    def send(payload, key, timeout):
        status, body = transport(payload, key, timeout)
        try:
            response = json.loads(body)
        except ValueError:
            response = {'unparsed_body': body.decode('utf-8', 'replace')[:2000]}
        exchanges.append(exchange_record(payload, response, status))
        return status, body
    return send


def anthropic_replay(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        exchange = pending.pop(0)
        return exchange.get('status', 200), replay_body(exchange, payload, warnings).encode()
    return send


# The analyst (issue #23) makes two streamed calls with no tools. A recording of that shape is
# stamped `shape: "plan"` and stores each call's stream events; it replays through AnthropicAnalyst.
# A recording without the stamp was made by the tool loop and replays through AnthropicPlanner
# until it is re-recorded (`--live --provider anthropic`), which always records the new shape.
ANALYST_SHAPE = 'plan'


def analyst_record(exchanges):
    from investigator.anthropic_provider import stream_transport

    def send(payload, key, timeout):
        status, events = stream_transport(payload, key, timeout)
        seen = []
        exchanges.append(exchange_record(payload, {'events': seen}, status))

        def relay():
            try:
                for event in events:
                    seen.append(event)
                    yield event
            finally:
                events.close()
        return status, relay()
    return send


def analyst_replay(exchanges, warnings):
    pending = list(exchanges)

    def send(payload, key, timeout):
        from investigator.openai_provider import ProviderError
        if not pending:
            raise ProviderError('Recorded fixture has no more responses')
        exchange = pending.pop(0)
        return exchange.get('status', 200), iter(json.loads(replay_body(exchange, payload, warnings))['events'])
    return send


def analyst_planner(key, ledger, send):
    from investigator.anthropic_provider import AnthropicAnalyst, Budget
    # Replays run as fast as the events can be read; a live round keeps the product's own ten-second ceiling.
    return AnthropicAnalyst(key, Budget(ledger), send, model=os.environ.get('ANTHROPIC_MODEL') or None)


def openai_planner(key, ledger, send):
    from investigator.openai_provider import Budget, OpenAIPlanner
    return OpenAIPlanner(key, Budget(ledger), send)


def anthropic_planner(key, ledger, send):
    from investigator.anthropic_provider import AnthropicPlanner, Budget
    return AnthropicPlanner(key, Budget(ledger), send, model=os.environ.get('ANTHROPIC_MODEL') or None)


PROVIDERS = {
    'openai': {'env': 'OPENAI_API_KEY', 'planner': openai_planner, 'record': openai_record,
               'replay': openai_replay, 'replay_key': 'sk-replay-fixture-not-a-credential'},
    'anthropic': {'env': 'ANTHROPIC_API_KEY', 'planner': anthropic_planner, 'record': anthropic_record,
                  'replay': anthropic_replay, 'replay_key': 'sk-ant-replay-fixture-not-a-credential'},
}


def fixture_path(case_id, provider):
    return FIXTURES / provider / f'{case_id}.json'


def default_provider():
    """The one provider whose key is set; None when none or both are."""
    keyed = [name for name, spec in PROVIDERS.items() if os.environ.get(spec['env'])]
    return keyed[0] if len(keyed) == 1 else None


# ---------------------------------------------------------------- running one case

def run_case(case, provider=None, live=False, record=False):
    """(outcome, failures, note, ms): `run_case_detail` without the turn results."""
    return run_case_detail(case, provider, live, record)[:4]


def run_case_detail(case, provider=None, live=False, record=False):
    """`_run_case` plus the agent's result for each turn that ran (the /evals page draws its tool
    trail from them). The temporary budget ledger of a live or replayed run is removed afterwards."""
    scratch = []
    try:
        return _run_case(case, provider, live, record, scratch)
    finally:
        for folder in scratch:
            shutil.rmtree(folder, ignore_errors=True)


def _run_case(case, provider, live, record, scratch):
    """Return (outcome, failures, note, ms) with outcome in pass/fail/skip/xfail and
    ms the wall time of the agent's turns (0 when the case did not run).

    provider None runs the case offline (OfflinePlanner, or ScriptedPlanner for a
    value case). A provider name runs it live when `live` is set, else replays the
    recorded fixture for that provider."""
    day = case.get('day', DEFAULT_DAY)
    scope = case.get('scope', 'shift')
    shift = case.get('shift', 2)
    if case['type'] == 'value':
        scope, shift = 'day', case['arguments'].get('shift') or 2
    expect = case.get('expect', {})
    if provider and 'expect_live' in case:
        expect = case['expect_live']
    allowed = [shift] if scope == 'shift' else [1, 2, 3]
    results = []
    # a URL case (issue #117) asks by its address: `url` is the input, answered as the server answers it
    turns = case.get('turns') or [case.get('question') or case.get('url') or f'value golden: {case.get("tool")}']
    mode = 'live' if provider else 'offline'
    note, warnings, exchanges = '', set(), []

    if provider is None:
        if case['type'] == 'value':
            stale = stale_values()
            if stale:
                return 'fail', [stale], '', 0.0, results

            def make_agent(analysis):
                return Agent(analysis, planner=ScriptedPlanner(case['tool'], case['arguments']))
        elif case.get('plan') is not None:
            def make_agent(analysis):
                return Agent(analysis, planner=ScriptedAnalyst(case['plan'], case.get('narrative')))
        else:
            def make_agent(analysis):
                return Agent(analysis)
    else:
        spec = PROVIDERS[provider]
        fixture = fixture_path(case['id'], provider)
        planned = provider == 'anthropic'   # what a live run records; a replay follows the recording's own shape
        if live:
            key = os.environ.get(spec['env'])
            if not key:
                return 'skip', [], f'{spec["env"]} not set', 0.0, results
            send = analyst_record(exchanges) if planned else spec['record'](exchanges)
        elif fixture.exists():
            recording = json.loads(fixture.read_text())
            # A recording replays the model's choices against the live store's data. Made against
            # another dataset (or before recordings were versioned) it cannot be scored: the rows the
            # model cited may no longer exist. That is a stale recording, not a failure.
            recorded_dataset, live_dataset = recording.get('dataset_version'), dataset_version()
            if recorded_dataset != live_dataset:
                return 'skip', [], (f'stale recording: dataset {str(recorded_dataset)[:12] if recorded_dataset else "unversioned"} '
                                    f'vs live {live_dataset[:12]}; re-record with --live --provider {provider}'), 0.0, results
            exchanges = recording['exchanges']
            planned = recording.get('shape') == ANALYST_SHAPE
            send = analyst_replay(exchanges, warnings) if planned else spec['replay'](exchanges, warnings)
            key = spec['replay_key']
        else:
            return 'skip', [], f'no {provider} fixture; run --live --provider {provider} to record', 0.0, results
        scratch.append(tempfile.mkdtemp())
        ledger = Path(scratch[-1]) / 'budget.sqlite3'

        def make_agent(analysis):
            if planned:
                return Agent(analysis, planner=analyst_planner(key, ledger, send), max_calls=2, max_seconds=10)
            return Agent(analysis, planner=spec['planner'](key, ledger, send), max_calls=10, max_seconds=60)

    analysis, ms = None, 0.0
    try:
        analysis = dataset(day, case.get('corrupt', False))
        context = Context(day, shift)
        context.scope = scope
        context.period = case.get('period', 'day')   # the Period control: week, month or fiscal routes to the period tools
        context.rates = parse_rates(case.get('rates'))   # a case's rate overrides, spelled as rates= spells them (issue #31)
        started = time.perf_counter()
        try:
            for text in turns:
                if case.get('url'):
                    resolved = resolve_url(text, golden_store())   # the server's path: the address, resolved, then the agent
                    turn, turn.history = resolved['context'], resolved['history']
                    results.append(make_agent(dataset(resolved['day'], resolved['corrupt'])).ask(resolved['text'], turn, request=resolved['request']))
                    continue
                # The question's own shift and window win over the case's controls (issue #105), as on the server:
                # the turn runs in the scope it names, on that day's records. A turn that stays on the controls' day
                # carries the follow-up history back; one on another day starts its own, as /q drops the priors.
                # against the golden year's own days (issue #35): the process's default store is the window ending
                # yesterday, a different calendar, and building it inside the timed turn cost the first case 10 s
                turn = context if case['type'] == 'value' else question_scope(text, context, golden_store().production_days)
                results.append(run_turn(make_agent(dataset(turn.production_day, case.get('corrupt', False))), text, turn))
                if turn is not context and turn.production_day == context.production_day:
                    context.history = turn.history
        finally:
            ms = (time.perf_counter() - started) * 1000
    except ValueError as exc:
        want = expect.get('raises')
        if want and re.search(want, str(exc), re.I):
            slow = latency_failures(expect, provider, ms) + url_failures(case, expect, turns, provider)
            return ('fail' if slow else 'pass'), slow, f'refused: {str(exc)[:60]}', ms, results
        return 'fail', [f'raised {str(exc)[:90]!r}' + (f', expected /{want}/' if want else '')], '', ms, results
    if expect.get('raises'):
        return 'fail', [f'did not raise; status {results[-1]["status"]}'], '', ms, results

    failures = latency_failures(expect, provider, ms)
    failures.extend(score(case, expect, results, analysis, allowed, mode))
    failures.extend(url_failures(case, expect, turns, provider, analysis, allowed))
    last = results[-1]
    if provider and planned:
        # The analyst is two model calls at most: one plan, one narrative. An answer with a narrative used exactly two.
        calls = [r['usage']['model_calls'] for r in results]
        if any(c > 2 for c in calls) or any(r['narrative_status'] == 'available' and c != 2 for r, c in zip(results, calls)):
            failures.append(f'analyst made {calls} model calls; a round is one plan and one narrative')
    if provider and live and record:
        fixture = fixture_path(case['id'], provider)
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(json.dumps(
            {'case': case['id'], 'provider': provider, 'question': turns[-1], 'recorded_status': last['status'],
             'recorded_cost_usd': last['usage']['cost_usd'], 'dataset_version': dataset_version(),
             **({'shape': ANALYST_SHAPE} if planned else {}), 'exchanges': exchanges}, indent=1))
        note = (f'recorded {len(exchanges)} exchanges, {last["usage"]["model_calls"]} model calls, ${last["usage"]["cost_usd"]:.4f}'
                + (f', first token {last["usage"]["first_token_ms"]:.0f} ms' if 'first_token_ms' in last['usage'] else ''))
    elif provider:
        note = f'{last["status"]}, {last["usage"]["model_calls"]} model calls'
    else:
        note = f'{last["status"]}, {sum(len(r["trace"]) for r in results)} tool calls'
        if case['id'] in URL_MS:
            note += f'; URL {URL_MS[case["id"]]:.0f} ms'
    if warnings:
        note += '; ' + '; '.join(sorted(warnings))
    if failures and case.get('known_defect'):
        return 'xfail', failures, 'known defect: ' + case['known_defect'], ms, results
    if not failures and case.get('known_defect'):
        note += '; known_defect marker is stale: the case passes, remove the marker'
    return ('fail' if failures else 'pass'), failures, note, ms, results


def score(case, expect, results, analysis, allowed, mode):
    """The invariants over every turn and the case's own expectations over the last (or, for an
    investigation, over all of them): the same scoring for the in-process and the URL answers."""
    failures = []
    tools = SEMANTIC_TOOL_NAMES if case.get('url') or case.get('tool') == semantic.TOOL else TOOL_NAMES
    for index, result in enumerate(results):
        failures.extend(invariants(mode, result, *answered_scope(case, expect, result, analysis, allowed, index == len(results) - 1), tools=tools))
    if case['type'] == 'investigation':
        failures.extend(investigation_failures(expect, results))
    else:
        failures.extend(expectations(expect, results[-1]))
        if case['type'] == 'value':
            failures.extend(value_failures(expect, results[-1]))
    turns = case.get('turns') or [case.get('question')]
    if mode == 'offline' and case.get('plan') is None and turns[-1]:
        failures.extend(asked_unit_failures(turns[-1], results[-1]['sections']))   # issue #113: the unit asked opens the answer
    return failures


def answered_scope(case, expect, result, analysis, allowed, last):
    """(analysis, allowed shifts) for the invariants of one answer. The allowed shifts are the scope the case states,
    never the answer's own: the case's controls, or, for the last turn of a case whose question names its own shift
    (issue #105), the shift of its `expect.url` (a scripted analyst plan, which has no URL, states `expect.shifts`).
    A turn that moves the shift without the case stating it is held to the controls and fails. The analysis that
    recounts a daily table is the one for the day the answer read, which `expect.url` pins as well."""
    stated = allowed
    if last and expect.get('url'):
        match = re.match(r'^/corr/[^/]+/(?:day|week|month|period)/\d{4}-\d{2}-\d{2}(?:/shift/([123]))?', expect['url'])
        if match:
            stated = [int(match[1])] if match[1] else [1, 2, 3]
    elif last and case.get('plan') is not None and expect.get('shifts'):
        stated = list(expect['shifts'])
    day = result.get('production_day')
    if day is None or day == getattr(analysis, 'day', None):
        return analysis, stated
    return dataset(day, case.get('corrupt', False)), stated


def url_failures(case, expect, turns, provider, analysis=None, allowed=None):
    """The URL path of an offline case: the redirect lands on `expect.url`, a refusal comes back as
    the same text, an answer scores like the in-process one and stays within the case's budget on
    the server's own time. A value case has no question, and an analyst-plan case (`plan`, issue #23)
    is a scripted live round, which has no URL; a live row checks the resolution only."""
    if case['type'] == 'value' or case.get('plan') is not None:
        return []
    if provider:
        if 'url' not in expect:
            return []
        _, _, failures, _ = via_url(case, expect, turns, redirect_only=True)
        return [f'URL: {f}' for f in failures]
    results, error, failures, ms = via_url(case, expect, turns)
    want = expect.get('raises')
    if want:
        if error is None:
            failures.append(f'answered over GET; expected a refusal /{want}/')
        elif not re.search(want, error, re.I):
            failures.append(f'GET refused with {error[:90]!r}, expected /{want}/')
    elif error is not None:
        failures.append(f'GET refused: {error[:90]!r}')
    elif results and not failures:
        URL_MS[case['id']] = ms
        slow = over_budget(ms, expect.get('max_ms', DEFAULT_MAX_MS), ' server time')
        if slow:
            failures.append(slow)
        failures.extend(score(case, expect, results, analysis, allowed, 'offline'))
    return [f'URL: {f}' for f in failures]


def latency_failures(expect, provider, ms):
    """The case's own budget, offline only: `expect.max_ms`, default DEFAULT_MAX_MS, times latency_scale()
    (1 unless CI sets INVESTIGATOR_LATENCY_SCALE). A live or replayed run is never timed against it: a
    `live: true` case shares its expect with its live variant, and that time is the provider's."""
    if provider:
        return []
    slow = over_budget(ms, expect.get('max_ms', DEFAULT_MAX_MS))
    return [slow] if slow else []


def percentiles(values):
    """Nearest-rank p50 and p95 of the case times, in ms; None when there are no values."""
    ordered = sorted(values)
    if not ordered:
        return {'p50': None, 'p95': None}
    return {name: round(ordered[max(math.ceil(q * len(ordered)), 1) - 1], 1) for name, q in (('p50', 0.5), ('p95', 0.95))}


# ---------------------------------------------------------------- score table

def plan_rows(cases, live, provider):
    """(case, provider or None) pairs: every offline case once, plus a live variant per
    provider for `mode: live` cases and cases marked `live: true`. An unrecorded live variant
    shows as a skipped `@live` row, except for a scripted analyst case (`plan`): its offline
    row already puts the round through the real checks, and its live variant runs on demand."""
    plan = []
    for case in cases:
        if case['mode'] == 'offline':
            plan.append((case, None))
        if case['mode'] == 'live' or case.get('live'):
            if live:
                plan.append((case, provider))
            else:
                recorded = [p for p in PROVIDERS if fixture_path(case['id'], p).exists()]
                plan.extend((case, p) for p in recorded)
                if not recorded and case.get('plan') is None:
                    plan.append((case, '?'))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='run the live variants against a provider and record fixtures')
    parser.add_argument('--provider', choices=sorted(PROVIDERS), help='openai or anthropic; default: the one whose key is set')
    parser.add_argument('--no-record', action='store_true', help='with --live: do not overwrite fixtures')
    parser.add_argument('--only', action='append', help='run only this case id (repeatable)')
    parser.add_argument('--json', type=Path, help='write results here')
    parser.add_argument('--track', action='store_true', help='append one line per run to evals/history.jsonl (commit, dataset, model, pass rate per type, cost)')
    parser.add_argument('--judge', action='store_true', help="live: score every narrative the run wrote (replayed, scripted or, with --live, fresh) "
                        "with the DeepEval G-Eval judge, one model call per rubric line; needs pip install -e '.[judge]' and the judge model's key")
    parser.add_argument('--judge-model', help=f'with --judge: the judge model (default: ${judging.JUDGE_ENV}, else {judging.DEFAULT_JUDGE_MODEL})')
    parser.add_argument('--judge-secret', help=f'with --judge: read the judge key from this Google Secret Manager secret when the provider key '
                        f'variable is not set (default: ${judging.JUDGE_SECRET_ENV}); a name, never a key')
    parser.add_argument('--judge-project', help=f'with --judge-secret: the Google Cloud project that holds it (default: ${judging.JUDGE_PROJECT_ENV}, '
                        'else the gcloud default)')
    args = parser.parse_args(argv)

    if args.track and args.only:
        # history.jsonl and last_run.json describe whole-set runs: the /evals page and the main page's badge
        # read the newest line as the score of the set, so a subset must never be appended.
        print('--track records a whole-set run; it cannot be combined with --only')
        return 2
    try:
        scale = latency_scale()   # checked before any case runs: a mistyped scale fails the run, it is never read as 1
    except LatencyScaleError as exc:
        print(exc)
        return 2
    provider = args.provider or default_provider()
    if args.live and provider is None:
        print('--live needs exactly one of ' + ' / '.join(s['env'] for s in PROVIDERS.values()) + ' set, or --provider')
        return 2
    client = None
    if (args.judge_model or args.judge_secret or args.judge_project) and not args.judge:
        print('--judge-model, --judge-secret and --judge-project go with --judge')
        return 2
    if args.judge:
        # Checked before any case runs: a judged run that cannot judge would spend the replays for nothing.
        try:
            judging._deepeval()
            client = judging.JudgeClient(args.judge_model, secret=args.judge_secret, project=args.judge_project)
        except judging.JudgeUnavailable as exc:
            print(f'--judge: {exc}')
            return 2
    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c['id'] in args.only]
    rows, narratives = [], []
    try:
        for case, case_provider in plan_rows(cases, args.live, provider):
            if case_provider == '?':
                outcome, failures, note, ms, usage = 'skip', [], 'no fixture; run --live to record', 0.0, {}
                case_provider = None
                label = f'{case["id"]}@live'
            else:
                outcome, failures, note, ms, turn_results = run_case_detail(case, case_provider, args.live, not args.no_record)
                label = case['id'] + (f'@{case_provider}' if case_provider else '')
                usage = turn_results[-1]['usage'] if turn_results else {}
                if client is not None:
                    source = 'scripted plan' if not case_provider else f'{"live" if args.live else "recorded"} {case_provider}'
                    narratives.extend(judging.narrative_cases(label, case.get('turns') or [case.get('question') or ''], turn_results, source))
            rows.append({'id': label, 'case': case['id'], 'type': case['type'], 'provider': case_provider,
                         'mode': 'live' if case_provider or label.endswith('@live') else 'offline',
                         'outcome': outcome, 'failures': failures, 'note': note, 'ms': round(ms, 1),
                         'url_ms': round(URL_MS[case['id']], 1) if not case_provider and case['id'] in URL_MS else None,
                         'comparisons_withdrawn': usage.get('comparisons_withdrawn')})   # None: no narrative was written
            if args.live and case_provider and outcome != 'skip':   # a real provider answered: its clock is worth keeping
                rows[-1].update(called=True, model_calls=usage.get('model_calls'), first_token_ms=usage.get('first_token_ms'))
    finally:
        close_datasets()

    width = max(len(r['id']) for r in rows) + 2
    print(f'{"case":{width}}{"type":15}{"mode":9}{"result":8}{"ms":>6}  note')
    print('-' * (width + 40 + 40))
    for r in rows:
        print(f'{r["id"]:{width}}{r["type"]:15}{r["mode"]:9}{r["outcome"]:8}{r["ms"]:6.0f}  {r["note"]}')
        for failure in r['failures'][:6]:
            print(f'{"":{width}}  - {failure}')
        if len(r['failures']) > 6:
            print(f'{"":{width}}  - ... {len(r["failures"]) - 6} more')
    print('-' * (width + 40 + 40))
    totals = Counter((r['type'], r['mode'], r['outcome']) for r in rows)
    summary = []
    for kind in TYPES:
        p, f, s, x = (sum(totals[(kind, m, o)] for m in ('offline', 'live')) for o in ('pass', 'fail', 'skip', 'xfail'))
        if p + f + s + x:
            summary.append(f'{kind} {p}/{p + f}' + (f' ({s} skipped)' if s else '') + (f' ({x} known defect)' if x else ''))
    print('  '.join(summary))
    breaches = 0
    live_rows = [r for r in rows if r['provider']]
    for name in sorted({r['provider'] for r in live_rows}):
        parts = []
        for kind in TYPES:
            p = sum(1 for r in live_rows if r['provider'] == name and r['type'] == kind and r['outcome'] == 'pass')
            f = sum(1 for r in live_rows if r['provider'] == name and r['type'] == kind and r['outcome'] == 'fail')
            if p + f:
                rate = p / (p + f)
                ok = rate >= THRESHOLDS[kind]
                breaches += not ok
                parts.append(f'{kind} {p}/{p + f} (threshold {THRESHOLDS[kind]:.0%}: {"ok" if ok else "BELOW"})')
        stale = sum(1 for r in live_rows if r['provider'] == name and r['outcome'] == 'skip' and str(r.get('note', '')).startswith('stale recording'))
        if stale:
            parts.append(f'{stale} stale recording(s): re-record with --live --provider {name}')
        if parts:
            print(f'live {name}: ' + '  '.join(parts))
    url_latency = percentiles(URL_MS[r['case']] for r in rows if r['case'] in URL_MS and not r['provider'])
    if url_latency['p50'] is not None:
        print(f'URL path: {sum(1 for r in rows if r["case"] in URL_MS and not r["provider"])} offline cases answered over GET; '
              + '  '.join(f'server {k} {url_latency[k]:.0f} ms' for k in ('p50', 'p95')) + ' (each case within its own budget)')
    latency = offline_latency(rows)
    if latency['p50'] is not None and args.only:   # a subset's percentiles are those cases' own times: reported, not gated
        print('offline latency: ' + '  '.join(f'{k} {latency[k]:.0f} ms' for k in ('p50', 'p95')) + ' (not gated under --only)')
    elif latency['p50'] is not None:
        verdicts = [f'{k} {latency[k]:.0f} ms (budget {LATENCY_BUDGET_MS[k]}: {"ok" if latency[k] <= LATENCY_BUDGET_MS[k] else "OVER"})'
                    for k in ('p50', 'p95')]
        breaches += sum(latency[k] > LATENCY_BUDGET_MS[k] for k in ('p50', 'p95'))
        print('offline latency: ' + '  '.join(verdicts))
    if scale != 1:
        print(f'{LATENCY_SCALE_ENV}={scale:g}: each case\'s own budget × {scale:g}; '
              'the p50 / p95 budgets and every time above are not scaled')
    timed = live_latency(rows)
    if timed:
        print('live latency: ' + '  '.join(f'{k} {v:.0f} ms' for k, v in timed.items() if isinstance(v, (int, float)) and k != 'model_calls_max')
              + f'  model calls per case: at most {timed["model_calls_max"]}')
    withdrawn = comparisons_withdrawn(rows)
    if withdrawn:
        print('comparisons withdrawn: ' + '  '.join(f'{label} {n} in {k} narrative{"" if k == 1 else "s"}' for label, (n, k) in withdrawn.items()))
    judged = None
    if client is not None:
        try:
            judged = judging.judge(narratives, client)
        finally:
            client.close()
        print_judged(*judged, rerecord=stale_recordings(rows))
        if not judged[1]['judged']:   # nothing scored (a bound or a refused key): spent, printed, never shown as the judge's result
            print('judge: no narrative was scored, so this run is not tracked as a judged run')
            judged = None
    else:
        print(stored_judge_line(stale_recordings(rows)))
    failed = sum(r['outcome'] == 'fail' and not r['provider'] for r in rows)
    skipped = sum(r['outcome'] == 'skip' for r in rows)
    xfailed = sum(r['outcome'] == 'xfail' for r in rows)
    live_failed = sum(r['outcome'] == 'fail' and bool(r['provider']) for r in rows)
    print(f'{len(rows) - failed - live_failed - skipped - xfailed} passed, {failed} failed, {skipped} skipped'
          + (f', {xfailed} known defect' if xfailed else '') + (f', {live_failed} live failed' if live_failed else ''))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))
    if args.track:
        line = history_line(rows, failed + breaches, judged)
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open('a') as out:
            out.write(json.dumps(line, separators=(',', ':')) + '\n')
        LAST_RUN.write_text(json.dumps(last_run(rows, line), indent=1) + '\n')
        if judged:
            JUDGE_LAST_RUN.write_text(json.dumps({'ran_at': line['ran_at'], 'commit': line['commit'], 'dataset': line['dataset'],
                                                  'summary': judged[1], 'rows': judged[0]}, indent=1) + '\n')
        print(f'tracked in {HISTORY.relative_to(ROOT)} (rows in {LAST_RUN.relative_to(ROOT)}): ' + ', '.join(f'{k} {v["pass"]}/{v["pass"] + v["fail"]}' for k, v in line['offline'].items())
              + (f'; latency p50 {line["latency_ms"]["p50"]:.0f} ms, p95 {line["latency_ms"]["p95"]:.0f} ms' if line['latency_ms']['p50'] is not None else '')
              + ''.join(f'; {p} ({v["model"]}): ' + ', '.join(f'{k} {c["pass"]}/{c["pass"] + c["fail"]}' for k, c in v['types'].items()) for p, v in line['live'].items())
              + (f'; judge {line["judge"]["model"]}, ${line["judge"]["cost_per_case_usd"]:.4f} per judged case (rows in {JUDGE_LAST_RUN.relative_to(ROOT)})'
                 if line.get('judge') and line['judge'].get('cost_per_case_usd') is not None else '')
              + (f'; judged at case budgets × {scale:g} ({LATENCY_SCALE_ENV}): do not commit it (CI refuses a scaled line);'
                 ' track it again with the scale unset' if scale != 1 else ''))
    return failed + breaches


# ---------------------------------------------------------------- the narrative judge (issue #25)

JUDGE_LAST_RUN = ROOT / 'evals' / judging.LAST_RUN_FILE
EVALS = ROOT / 'evals'


def stale_recordings(rows):
    """True when a live recording skipped as stale this run: the calibration target then waits on re-recording (#71)."""
    return any(r['outcome'] == 'skip' and str(r.get('note', '')).startswith('stale recording') for r in rows)


def print_judged(rows, summary, rerecord=False):
    """The judged run: one row per narrative with its four 0/1 scores, the reason for each failing line, the
    pass count per criterion, the cost per judged case, and the agreement with the owner's grades."""
    print(f'judge: {summary["metric"]} ({summary["model"]}), rubric {summary["rubric"]}: {summary["narratives"]} narratives, '
          f'{summary["calls"]} model calls, ${summary["cost_usd"]:.4f}'
          + (f', ${summary["cost_per_case_usd"]:.4f} per judged case' if summary['cost_per_case_usd'] is not None else ''))
    if rows:
        width = max(len(r['id']) for r in rows) + 2
        short = {'grounded': 'grnd', 'recorded_not_proven': 'rec', 'prescribes_nothing': 'presc', 'unavailable_not_zero': 'unav',
                 'comparisons_consistent': 'comp'}
        print(f'  {"narrative":{width}}' + ''.join(f'{short[k]:>6}' for k in judging.CRITERIA) + '  source')
        for r in rows:
            print(f'  {r["id"]:{width}}' + ''.join(f'{"-" if r["scores"][k] is None else r["scores"][k]:>6}' for k in judging.CRITERIA) + f'  {r["source"]}')
            for k in judging.CRITERIA:
                if r['scores'][k] != 1:
                    print(f'  {"":{width}}  - {k}: {r["reasons"][k][:150]}')
    print('judge pass: ' + '  '.join(f'{k} {c["pass"]}/{c["pass"] + c["fail"]}' + (f' ({c["error"]} unscored)' if c['error'] else '')
                                     for k, c in summary['criteria'].items()))
    print('judge vs owner: ' + judging.agreement_text(judging.agreement(rows, judging.load_grades(EVALS)), rerecord))


def stored_judge_line(rerecord=False):
    """The judge as last tracked, read from the committed files: no model is called. The agreement is
    the owner's grades against that run's scores."""
    grades = judging.load_grades(EVALS)
    stored = judging.load_json(JUDGE_LAST_RUN, None)
    if not stored:
        return (f'judge: no judged run tracked yet (python3 evals/run.py --judge --track); '
                + judging.agreement_text(judging.agreement([], grades), rerecord))
    s = stored['summary']
    return (f'judge, last tracked {stored["ran_at"]} ({s["model"]}, {s["metric"]}): '
            + '  '.join(f'{k} {c["pass"]}/{c["pass"] + c["fail"]}' for k, c in s['criteria'].items())
            + (f'  ${s["cost_per_case_usd"]:.4f} per judged case' if s.get('cost_per_case_usd') is not None else '')
            + '; vs owner: ' + judging.agreement_text(judging.agreement(stored['rows'], grades), rerecord))


HISTORY = ROOT / 'evals' / 'history.jsonl'


LAST_RUN = ROOT / 'evals' / 'last_run.json'


def last_run(rows, line):
    """The rows behind the newest history line: id, case, type, provider, outcome and ms per row.
    `ran_at` and `commit` are that line's, so a reader can tell the two files belong together; the
    /evals page shows each case's result from here without running anything. A scaled run's `latency_scale` comes along."""
    return {'ran_at': line['ran_at'], 'commit': line['commit'], 'dataset': line['dataset'],
            **({'latency_scale': line['latency_scale']} if 'latency_scale' in line else {}),
            'rows': [{k: r[k] for k in ('id', 'case', 'type', 'provider', 'mode', 'outcome', 'ms', 'note')} for r in rows]}


def offline_latency(rows):
    """p50 and p95 of the offline cases that ran (skips did not run, so they carry no time)."""
    return percentiles(r['ms'] for r in rows if not r['provider'] and r['outcome'] != 'skip')


def live_latency(rows):
    """Wall time of the rounds a real provider answered in this run (`--live`): p50 and p95 of the whole
    round, the median time to the first narrative token, and the most model calls any case made.
    None when no provider was called; replays are never timed."""
    called = [r for r in rows if r.get('called')]
    if not called:
        return None
    out = dict(percentiles(r['ms'] for r in called))
    first = [r['first_token_ms'] for r in called if isinstance(r.get('first_token_ms'), (int, float))]
    if first:
        out['first_token_p50'] = percentiles(first)['p50']
    out['model_calls_max'] = max((r.get('model_calls') or 0) for r in called)
    return out


def comparisons_withdrawn(rows):
    """Issue #79: paragraphs the stream validator withdrew because their ordering words ("fastest",
    "below", "rose") contradicted the values they cite, summed per kind of run, with the number of
    narratives written: {'offline': (n, narratives), 'replayed anthropic': ..., 'live anthropic': ...}."""
    out = {}
    for r in rows:
        if r.get('comparisons_withdrawn') is None:
            continue
        label = 'offline' if not r['provider'] else f'{"live" if r.get("called") else "replayed"} {r["provider"]}'
        n, k = out.get(label, (0, 0))
        out[label] = (n + r['comparisons_withdrawn'], k + 1)
    return out


def history_line(rows, exit_status, judged=None):
    """One record per run: enough to tell whether a change in the planner, the prompt or the
    data moved the score, and whether it moved the clock. Live rows are scored against the
    recordings' model; `latency_ms` is the offline p50 / p95. A run under INVESTIGATOR_LATENCY_SCALE adds
    `latency_scale`: its verdicts were judged at scaled case budgets (issue #139). A judged run (`--judge`) adds `judge`:
    the judge model and rubric, the pass count per criterion, the cost and the cost per judged case,
    and its agreement with the owner's grades at that time."""
    def tally(subset):
        out = {}
        for r in subset:
            counts = out.setdefault(r['type'], {'pass': 0, 'fail': 0, 'skip': 0})
            counts[r['outcome'] if r['outcome'] in counts else 'skip'] += 1   # xfail counts as a skip
        return out
    try:
        commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    live = {}
    for provider in sorted({r['provider'] for r in rows if r['provider']}):
        models, cost = set(), 0.0
        for r in rows:
            if r['provider'] != provider:
                continue
            path = fixture_path(r['case'], provider)
            if path.exists():
                recording = json.loads(path.read_text())
                cost += float(recording.get('recorded_cost_usd') or 0)
                models.update(e.get('model') for e in recording.get('exchanges', []) if e.get('model'))
        live[provider] = {'model': '/'.join(sorted(models)) or None, 'recorded_cost_usd': round(cost, 5),
                          'types': tally(r for r in rows if r['provider'] == provider)}
    return {'ran_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), 'commit': commit, 'dataset': dataset_version()[:12],
            'cases': len(rows), 'offline': tally(r for r in rows if not r['provider']), 'latency_ms': offline_latency(rows),
            **({'latency_scale': scale} if (scale := latency_scale()) != 1 else {}), 'live': live, **({'live_ms': live_latency(rows)} if live_latency(rows) else {}),
            **({'judge': judge_record(*judged)} if judged else {}), 'exit': exit_status}


def judge_record(rows, summary):
    """The history line's `judge` block: the summary, and the agreement reduced to its numbers."""
    found = judging.agreement(rows, judging.load_grades(EVALS))
    keep = ('n', 'agree', 'pct', 'kappa')
    return {**summary, 'agreement': {'graded': found['graded'], 'matched': found['matched'],
                                     'criteria': {k: {f: v[f] for f in keep} for k, v in found['criteria'].items()},
                                     'overall': {f: found['overall'][f] for f in keep}}}


if __name__ == '__main__':
    sys.exit(main())
