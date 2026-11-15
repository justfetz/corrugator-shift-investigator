"""The question lab (issue #177): ask many questions in any controls and get every card back for review.

    python3 evals/lab.py questions.txt                 # one question per line; '#' starts a comment
    python3 evals/lab.py questions.json                # [{"question": ..., "day": ..., "shift": ..., "scope": ..., "period": ...}, ...]
    python3 evals/lab.py --canned                      # the page's canned questions under every scope and period
    python3 evals/lab.py --ask "how many setups last week" --scope both --period day,week

A line of a text file may set its own controls before the question: `[scope=day period=week day=2026-09-16] stops by shift`.
Controls not given take the command line's (--day, --shift, --scope shift|day|both, --period day|week|month|accounting|all),
which default to the eval defaults: 2026-09-14, shift 2, the selected shift, Day; with --canned the scope defaults to both
and the period to all, so `--canned` alone asks every canned question under one shift and All, Day, Week, Month and 4-4-5.

Each question runs the server's own offline path (`Workbench.ask`: question_scope, parse_request, Agent.ask) against the golden
store, the window the golden set is scored on. No model is called. The lab writes two files under artifacts/lab/ (gitignored):

- `<name>.md`: one block per card (controls, address, headline, findings, tools with their shift and window, refusal) and a
  table of the lab's own flags, for the owner to read;
- `<name>.json`: the same records, for the analyst agent (.claude/agents/analyst.md), which gives each card a verdict and a
  golden-case candidate (the /question-lab skill runs both).

The flags are mechanical and say where to look, not what is right. They name a card under All that leads with one shift the
question did not name, a generic help refusal, a headline with no figure, a line long enough to strain the card, and a question
whose own words name a shift or window the answer did not take. The verdict is the analyst's.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'evals'))

PERIODS = ('day', 'week', 'month', 'accounting')
SCOPES = ('shift', 'day')
DEFAULTS = {'day': '2026-09-14', 'shift': 2, 'scope': 'shift', 'period': 'day'}
OUT = ROOT / 'artifacts' / 'lab'
CITE = re.compile(r'\s*\((?:query|row)[^)]*\)')
SHIFT_NAMED = re.compile(r'\bshift\s*[1-3]\b|\b(first|second|third|1st|2nd|3rd|night|day|swing)\s+shift\b', re.I)
ONE_SHIFT = re.compile(r'\bon shift [1-3]\b|^\*\*[^*]+\*\*[^·]*\bshift [1-3]\b|^Shift [1-3]\b', re.I)
GENERIC = ('Offline mode supports', 'Offline mode answers one quantity at a time')
LONG_LINE = 160      # a card line longer than this strains the card at phone width (issue #177, item 5)
LONG_WORD = 28       # an unbroken run of characters this long cannot wrap at a space


def canned_questions():
    """The page's canned questions (`QUESTIONS` in web/app.js): (label, text, scope or None, period or None)."""
    source = (ROOT / 'src/investigator/web/app.js').read_text()
    block = source[source.index('const QUESTIONS=['):]
    block = block[:block.index('\n];')]
    out = []
    for m in re.finditer(r"^\s*\[([^\n]*)\],?", block, re.M):
        parts = re.findall(r"'((?:[^'\\]|\\.)*)'|(null|true|false)", m[1])
        values = [a if a or not b else (None if b == 'null' else b == 'true') for a, b in parts]
        if len(values) >= 2:
            out.append((values[0], values[1], values[2] if len(values) > 2 else None, values[3] if len(values) > 3 else None))
    return out


def period_word(period):
    """The word the page puts for {period} under each Period control (app.js periodWord)."""
    return {'day': 'week', 'week': 'week', 'month': 'month', 'accounting': 'period'}[period]


def parse_line(line, base):
    """(question, controls) of one text line: `[key=value ...] question`."""
    controls = dict(base)
    m = re.match(r'\s*\[([^\]]*)\]\s*(.*)$', line)
    if m:
        for pair in m[1].split():
            key, _, value = pair.partition('=')
            controls[key] = int(value) if key == 'shift' else value
        line = m[2]
    return line.strip(), controls


def expand(controls, scopes, periods):
    """Every combination of the scopes and periods asked, for controls that did not fix their own."""
    for scope in ([controls['scope']] if 'scope' in controls.get('_fixed', ()) else scopes):
        for period in ([controls['period']] if 'period' in controls.get('_fixed', ()) else periods):
            yield {**{k: v for k, v in controls.items() if k != '_fixed'}, 'scope': scope, 'period': period}


def load(args):
    """[(question, controls, label)] for the command line's inputs."""
    base = {'day': args.day, 'shift': args.shift}
    scopes = SCOPES if args.scope == 'both' else (args.scope,)
    periods = PERIODS if args.period == 'all' else tuple(args.period.split(','))
    items = []
    if args.canned:
        for label, text, scope, period in canned_questions():
            for p in periods:
                for s in scopes:
                    controls = {**base, 'scope': scope or s, 'period': period or p}
                    items.append((text.replace('{period}', period_word(controls['period'])), controls, label.replace('{period}', period_word(controls['period']))))
    for text in args.ask or []:
        for c in expand(dict(base), scopes, periods):
            items.append((text, c, None))
    for path in args.files:
        path = Path(path)
        if path.suffix == '.json':
            for entry in json.loads(path.read_text()):
                entry = {'question': entry} if isinstance(entry, str) else entry
                fixed = {k: entry[k] for k in ('day', 'shift', 'scope', 'period') if k in entry}
                for c in expand({**base, **fixed, '_fixed': tuple(fixed)}, scopes, periods):
                    items.append((entry['question'], c, entry.get('label')))
        else:
            for line in path.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                text, controls = parse_line(line, base)
                fixed = tuple(k for k in ('scope', 'period') if k in controls)
                for c in expand({**controls, '_fixed': fixed}, scopes, periods):
                    items.append((text, c, None))
    seen, out = set(), []
    for text, c, label in items:   # a canned question whose chip fixes its scope appears once per period
        key = (text, c['day'], c['shift'], c['scope'], c['period'])
        if key not in seen:
            seen.add(key)
            out.append((text, c, label))
    return out


def tools_of(result):
    """Each tool the card ran, with the shift and window it read."""
    out = []
    for step in result.get('trace') or []:
        args = step.get('arguments') or {}
        window = args.get('window') or args.get('current_window')
        where = f"{window['period']} {window['anchor']}" if isinstance(window, dict) else 'day'
        shift, shifts = args.get('shift'), args.get('shifts')
        read = ('shift ' + str(shift) if shift else 'all shifts' if 'shift' in args else
                'shifts ' + ','.join(map(str, shifts)) if isinstance(shifts, list) and shifts else '-')   # a day total's shifts (#177)
        out.append(f"{step['tool']}({read}, {where})")
    return out


def flags_of(question, controls, record):
    """The lab's mechanical flags for one card (see the module docstring)."""
    flags = []
    if record['refusal']:
        if record['refusal'].startswith(GENERIC):
            flags.append('generic help refusal')
        return flags
    head = record['headline'] or ''
    if controls['scope'] == 'day' and not SHIFT_NAMED.search(question) and ONE_SHIFT.search(head):
        flags.append('under All, leads with one shift')
    if not re.search(r'\*\*[^*]*\d', head):
        flags.append('headline has no figure')
    lines = [head, *record['findings']]
    if any(len(CITE.sub('', l)) > LONG_LINE for l in lines):
        flags.append(f'a line over {LONG_LINE} characters')
    if any(len(w) > LONG_WORD for l in lines for w in re.split(r'\s+', CITE.sub('', l))):
        flags.append(f'an unbroken run over {LONG_WORD} characters')
    from investigator.agent import named_shifts   # the router's own reading of the shifts a question names
    named = sorted(set(named_shifts(question.lower())))
    if named and controls['scope'] == 'day' and record['shifts'] and sorted(record['shifts']) != named:
        flags.append(f'names shift{"s" if len(named) > 1 else ""} {", ".join(map(str, named))}, answered shifts {record["shifts"]}')
    return flags


def run(items, store, limit_findings=6):
    from investigator.server import Workbench
    bench = Workbench(store=store)
    bench.request_limit = 10 ** 9   # the lab is no visitor
    records = []
    for i, (text, controls, label) in enumerate(items, 1):
        payload = {'question': text, 'day': controls['day'], 'shift': controls['shift'], 'scope': controls['scope'],
                   'period': controls['period'], 'mode': 'offline'}
        started = time.perf_counter()
        record = {'n': i, 'question': text, 'label': label, 'controls': controls, 'refusal': None, 'url': None, 'status': None,
                  'intent': None, 'shifts': [], 'headline': None, 'read_as': None, 'findings': [], 'tools': [], 'charts': []}
        try:
            with bench.lock:
                result = bench.ask(payload)
        except ValueError as exc:
            record['refusal'] = str(exc)
        else:
            sections = [CITE.sub('', s) for s in result.get('sections') or []]
            history = (result.get('context') or {}).get('history') or [{}]
            record.update(url=result.get('url'), status=result.get('status'), intent=history[-1].get('intent'),
                          shifts=result.get('selected_shifts') or [], headline=sections[0] if sections else None,
                          read_as=next((s for s in sections if s.startswith('Read as')), None),
                          findings=[s for s in sections[1:] if not s.startswith('Read as')][:limit_findings],
                          tools=tools_of(result), charts=[c.get('title') for c in result.get('charts') or []])
        record['ms'] = round((time.perf_counter() - started) * 1000, 1)
        record['flags'] = flags_of(text, controls, record)
        records.append(record)
    return records


def controls_text(c):
    scope = 'All shifts' if c['scope'] == 'day' else f"shift {c['shift']}"
    return f"{c['day']} · {scope} · {c['period']}"


def markdown(records, title):
    flagged = [r for r in records if r['flags']]
    lines = [f'# {title}', '', f'{len(records)} cards, {sum(1 for r in records if r["refusal"])} refused, {len(flagged)} flagged by the lab.',
             'The flags are mechanical (evals/lab.py); the verdicts are the analyst\'s (.claude/agents/analyst.md).', '']
    if flagged:
        lines += ['| # | Question | Controls | Flags |', '| --- | --- | --- | --- |']
        lines += [f"| {r['n']} | {r['question']} | {controls_text(r['controls'])} | {'; '.join(r['flags'])} |" for r in flagged]
        lines.append('')
    for r in records:
        lines += [f"## {r['n']}. {r['question']}", '', f"- Controls: {controls_text(r['controls'])}" + (f" (chip '{r['label']}')" if r['label'] else '')]
        if r['refusal']:
            lines += [f"- Refused: {r['refusal']}"]
        else:
            lines += [f"- Address: `{r['url']}` · intent `{r['intent']}` · shifts {r['shifts']} · {r['status']} · {r['ms']} ms",
                      f"- Headline: {r['headline']}"]
            if r['read_as']:
                lines.append(f"- {r['read_as']}")
            lines += [f'  {f}' if f.startswith('-') else f'  - {f}' for f in r['findings']]
            lines.append(f"- Tools: {', '.join(r['tools']) or 'none'}")
        if r['flags']:
            lines.append(f"- Lab flags: {'; '.join(r['flags'])}")
        lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('files', nargs='*', help='question files (.txt or .json)')
    parser.add_argument('--canned', action='store_true', help="the page's canned questions")
    parser.add_argument('--ask', action='append', help='one question (repeatable)')
    parser.add_argument('--day', default=DEFAULTS['day'])
    parser.add_argument('--shift', type=int, default=DEFAULTS['shift'])
    parser.add_argument('--scope', choices=('shift', 'day', 'both'),
                        help='shift: the selected shift; day: All (default: both with --canned, else shift)')
    parser.add_argument('--period', help='day, week, month, accounting, a comma list, or all (default: all with --canned, else day)')
    parser.add_argument('--name', help='the output files\' name (default: lab-<UTC time>)')
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args(argv)
    if not (args.files or args.canned or args.ask):
        parser.error('give a question file, --ask or --canned')
    # --canned alone is the page's front door: every canned question under every scope and period (the issue's criterion)
    args.scope = args.scope or ('both' if args.canned else DEFAULTS['scope'])
    args.period = args.period or ('all' if args.canned else DEFAULTS['period'])
    items = load(args)
    import run as runner   # the golden store: the window the golden set is scored on
    records = run(items, runner.golden_store())
    name = args.name or 'lab-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f'{name}.json').write_text(json.dumps({'name': name, 'records': records}, indent=1, ensure_ascii=False))
    (args.out / f'{name}.md').write_text(markdown(records, f'Question lab: {name}'))
    flagged = sum(1 for r in records if r['flags'])
    print(f'{len(records)} cards, {sum(1 for r in records if r["refusal"])} refused, {flagged} flagged -> {args.out / name}.md and .json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
