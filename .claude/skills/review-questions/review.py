"""Last week's visitor questions, grouped into golden-case candidates (the `/review-questions` skill, issue #125).

Reads the question log (src/investigator/questionlog.py): its four events (question, answer, analyst, feedback)
joined on the line id, either from BigQuery, where the log sink copies them (docs/DEPLOYMENT.md, "The question log"),
or from a file of log lines (one JSON object per line: a line as the server wrote it, or a BigQuery export row with
the line under `jsonPayload`). Prints, for the owner:

  top refused questions, top catch-all questions (#113), thumbs down with their comments, analyst plans rejected,
  the most-asked phrasings, and then the candidates: one per question that was refused, landed on the catch-all,
  got a thumbs down, had its analyst plan rejected, or was read through a typo or a moved scope. Each candidate is a
  draft golden case for evals/golden.json and a draft question-bank entry (#117), in batches of ten for the owner to
  approve. A question already in the golden set is listed as such, not proposed again.

The drafts are proposals: the expected address of a refused or catch-all question is the owner's to give, so it is
left as a TODO, and the rail's day of a live question is not in the golden window, so a draft takes the golden day.
Nothing here writes a file or posts anywhere.

    python3 .claude/skills/review-questions/review.py --file <log.jsonl> [--batch N] [--json]
    python3 .claude/skills/review-questions/review.py --bq <project> [--days 7] [--batch N] [--json]
    python3 .claude/skills/review-questions/review.py --sql <project>     # the BigQuery SQL it would run
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
DATASET, TABLE = 'question_log', 'run_googleapis_com_stdout'   # the sink's dataset; Cloud Run's stdout log table
BATCH = 10
GOLDEN_DAY = '2026-09-14'   # the golden set's default day (evals/golden.json `about`)
# What makes a question worth a golden case, and how much: the misses first.
WEIGHT = {'refused': 3, 'catch_all': 3, 'thumbs_down': 2, 'analyst_plan_rejected': 2, 'typo_corrected': 1, 'scope_moved': 1}
UNIT = re.compile(r"\b((?:lineal|feet|ft) per (?:setup|wet end)|setups?|paper changes?|order changes?|wet ends?|stops?|splices?|step-?ups?|rolls?|orders?|sheets?|"
                  r"boards?|minutes?|hours?|feet|ft per setup|msf|sq ft|percent|dollars?)\b", re.I)


def sql(project, days=7):
    """The raw events of the last `days` days; the grouping is done here, so a file and BigQuery read the same."""
    return (f"SELECT TO_JSON_STRING(jsonPayload) AS line FROM `{project}.{DATASET}.{TABLE}` "
            f"WHERE jsonPayload.log = 'question_log' AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(days)} DAY) "
            "ORDER BY timestamp")


def from_bigquery(project, days=7):
    out = subprocess.run(['bq', 'query', '--use_legacy_sql=false', '--format=json', '--max_rows=100000', sql(project, days)],
                         capture_output=True, text=True, check=True).stdout
    return [json.loads(row['line']) for row in json.loads(out or '[]')]


def from_file(path):
    events = []
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        record = record.get('jsonPayload', record) if isinstance(record, dict) else None   # a BigQuery export row
        if isinstance(record, dict) and isinstance(record.get('line'), str) and record.get('line').startswith('{'):
            record = json.loads(record['line'])                                            # a row of the SQL above
        if isinstance(record, dict) and record.get('log') == 'question_log':
            events.append(record)
    return events


def short(text, limit=120):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '…'


def normalize(text):
    return re.sub(r'[\s?.!]+$', '', ' '.join((text or '').lower().split()))


def goldens(root=ROOT):
    """Normalized question -> golden case id, from evals/golden.json."""
    path = Path(root) / 'evals' / 'golden.json'
    if not path.exists():
        return {}
    cases = json.loads(path.read_text(encoding='utf-8'))['cases']
    return {normalize(c['question']): c['id'] for c in cases if isinstance(c.get('question'), str)}


def questions(events):
    """One record per line id: the question (or an analyst round asked with no offline question), its answer, its
    analyst round and its feedback."""
    by_line = defaultdict(lambda: {'question': None, 'answer': None, 'analyst': None, 'feedback': []})
    for event in events:
        line = event.get('line')
        if not line:
            continue
        kind = event.get('event')
        if kind == 'feedback':
            by_line[line]['feedback'].append(event)
        elif kind in ('question', 'answer', 'analyst') and by_line[line][kind] is None:
            by_line[line][kind] = event
    out = []
    for line, parts in by_line.items():
        asked = parts['question'] or parts['analyst']
        if asked is None:
            continue
        flags = set(asked.get('flags') or [])
        if parts['analyst']:
            flags.update(parts['analyst'].get('flags') or [])
        # one vote per card: a comment repeats its card's thumbs down, so votes count per card, not per event
        votes = {(f.get('card'), f['vote']) for f in parts['feedback'] if f.get('vote') in ('up', 'down')}
        if any(vote == 'down' for _, vote in votes):
            flags.add('thumbs_down')
        out.append({'line': line, 'session': asked.get('session'), 'ts': asked.get('ts'), 'question': asked.get('question') or '',
                    'scope': asked.get('scope') or {}, 'url': asked.get('url'), 'measure': asked.get('measure'),
                    'status': (parts['answer'] or {}).get('status') or asked.get('status'), 'refusal': asked.get('refusal'),
                    'flags': flags, 'analyst': (parts['analyst'] or {}).get('status'),
                    'up': sum(vote == 'up' for _, vote in votes), 'down': sum(vote == 'down' for _, vote in votes),
                    'comments': [f['comment'] for f in parts['feedback'] if f.get('comment')]})
    return sorted(out, key=lambda q: q['ts'] or '')


def groups(rows, known=None):
    """Questions grouped by their normalized text, most-asked first, with what the log says about each."""
    known = known or {}
    by_text = defaultdict(list)
    for row in rows:
        by_text[normalize(row['question'])].append(row)
    out = []
    for text, items in by_text.items():
        flags = Counter(flag for item in items for flag in item['flags'])
        scopes = Counter(json.dumps({k: v for k, v in item['scope'].items() if k != 'day'}, sort_keys=True) for item in items)
        out.append({'text': text, 'question': Counter(i['question'] for i in items).most_common(1)[0][0], 'asks': len(items),
                    'sessions': len({i['session'] or i['line'] for i in items}), 'flags': dict(flags),
                    'urls': Counter(i['url'] for i in items if i['url']).most_common(3),
                    'refusals': Counter(short(i['refusal']) for i in items if i['refusal']).most_common(2),
                    'scope': json.loads(scopes.most_common(1)[0][0]), 'up': sum(i['up'] for i in items),
                    'down': sum(i['down'] for i in items), 'comments': [c for i in items for c in i['comments']],
                    'analyst': Counter(i['analyst'] for i in items if i['analyst']).most_common(2),
                    'golden': known.get(text)})
    return sorted(out, key=lambda g: (-g['asks'], -g['sessions'], g['text']))


def score(group):
    return group['sessions'] * sum(WEIGHT.get(flag, 0) for flag in group['flags'])


def case_id(text, taken):
    words = re.findall(r'[a-z0-9]+', text)[:7] or ['question']
    base = 'log-' + '-'.join(words)
    out, n = base, 2
    while out in taken:
        out, n = f'{base}-{n}', n + 1
    taken.add(out)
    return out


def draft(group, taken):
    """A golden case and a question-bank entry for one group: proposals for the owner, never written here."""
    scope = group['scope']
    landed = group['urls'][0][0] if group['urls'] else None
    miss = bool({'catch_all', 'thumbs_down', 'analyst_plan_rejected'} & set(group['flags']))
    refused_only = 'refused' in group['flags'] and not miss
    times = 'once' if group['asks'] == 1 else f"{group['asks']} times"
    evidence = [f"asked {times} by {group['sessions']} session{'s' if group['sessions'] != 1 else ''}"]
    if landed:
        evidence.append(f'lands on {landed}')
    if group['refusals']:
        evidence.append(f"refused: {group['refusals'][0][0]}")
    if group['flags']:
        evidence.append('flags ' + ', '.join(sorted(group['flags'])))
    if group['down'] or group['up']:
        evidence.append(f"{group['up']} up, {group['down']} down")
    case = {'id': case_id(group['text'], taken), 'type': 'routing', 'mode': 'offline', 'question': group['question']}
    if scope.get('scope') == 'day':
        case['scope'] = 'day'
    if scope.get('shift') not in (None, 2):
        case['shift'] = scope['shift']
    if scope.get('period') not in (None, 'day'):
        case['period'] = 'fiscal' if scope['period'] == 'accounting' else scope['period']
    case['note'] = f'From the question log (issue #125): {"; ".join(evidence)}. Asked on the live window; drafted on {GOLDEN_DAY}.'
    # A miss (catch-all, thumbs down, plan rejected): the owner names the address it should open. Refused and nothing
    # more: a refusal case pinning the reason, which the owner turns into a routing case if it should be answered.
    # Read right (a typo or a moved scope): pin the address it opened.
    if miss:
        case['expect'] = {'url': 'TODO: the address the owner says this should open, on ' + GOLDEN_DAY}
    elif refused_only:
        case['type'] = 'refusal'
        reason = group['refusals'][0][0] if group['refusals'] else 'TODO'
        case['expect'] = {'raises': re.split(r'[.;:]', reason, maxsplit=1)[0]}
        case['note'] += ' If it should be answered, make it a routing case with the address it should open.'
    else:
        case['expect'] = {'url': landed, 'status': 'complete'}
    unit = UNIT.search(group['question'])
    bank = {'question': group['question'], 'reading': 'TODO: metric, dimensions, window (#117)',
            'url': landed if not (miss or refused_only) else 'TODO', 'headline_unit': unit.group(1).lower() if unit else 'TODO',
            'reviewed_by': None}
    return {'case': case, 'bank': bank, 'score': score(group), 'group': group}


def candidates(rows, known=None):
    taken = set(known.values()) if known else set()
    found = [g for g in groups(rows, known) if g['flags'] and not g['golden']]
    return [draft(g, taken) for g in sorted(found, key=lambda g: (-score(g), -g['asks'], g['text']))]


def quoted(text):
    """A visitor's text in a code span: never Markdown, never a mention."""
    return '`' + str(text).replace('`', "'") + '`'


def report(rows, known=None, batch=1):
    known = known or {}
    every = groups(rows, known)
    picks = candidates(rows, known)
    batches = max(1, -(-len(picks) // BATCH))
    out = [f'# Question log review: {len(rows)} questions, {len(every)} phrasings, {len(picks)} candidates', '']

    def section(title, items, line):
        out.append(f'## {title}')
        out.extend([line(g) for g in items[:10]] or ['- none'])
        out.append('')

    def asks(g):
        return f"{g['asks']}× ({g['sessions']} session{'s' if g['sessions'] != 1 else ''})"
    section('Top refused', [g for g in every if g['flags'].get('refused')],
            lambda g: f"- {asks(g)} {quoted(g['question'])}: {g['refusals'][0][0] if g['refusals'] else ''}")
    section('Top catch-all (landed on measure=kpis, #113)', [g for g in every if g['flags'].get('catch_all')],
            lambda g: f"- {asks(g)} {quoted(g['question'])} → `{g['urls'][0][0] if g['urls'] else ''}`")
    section('Thumbs down, with comments', [g for g in every if g['down']],
            lambda g: f"- {g['down']} down {quoted(g['question'])}" + ''.join(f'\n  - {quoted(c)}' for c in g['comments']))
    section('Analyst plans rejected', [g for g in every if g['flags'].get('analyst_plan_rejected')],
            lambda g: f"- {asks(g)} {quoted(g['question'])}")
    section('Most-asked phrasings', every,
            lambda g: f"- {asks(g)} {quoted(g['question'])}" + (f" (golden: `{g['golden']}`)" if g['golden'] else ''))
    shown = picks[(batch - 1) * BATCH: batch * BATCH]
    out.append(f'## Candidates: batch {batch} of {batches} ({len(shown)} of {len(picks)})')
    out.append('Approve, edit or drop each; an approved case goes into evals/golden.json through /add-golden-case, its bank '
               'entry into the question bank once #117 lands. Text in backticks is a visitor\'s, quoted as data.')
    for n, pick in enumerate(shown, (batch - 1) * BATCH + 1):
        out.append(f"\n### {n}. {quoted(pick['group']['question'])} (score {pick['score']})")
        out.append('```json\n' + json.dumps(pick['case'], indent=1, ensure_ascii=False) + '\n```')
        out.append('Question bank (#117): ```' + json.dumps(pick['bank'], ensure_ascii=False) + '```')
    return '\n'.join(out) + '\n'


def main(argv):
    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    if '--sql' in argv:
        print(sql(arg('--sql'), int(arg('--days', 7))))
        return 0
    if '--file' in argv:
        events = from_file(arg('--file'))
    elif '--bq' in argv:
        events = from_bigquery(arg('--bq'), int(arg('--days', 7)))
    else:
        print('usage: review.py --file <log.jsonl> | --bq <project> [--days 7] [--batch N] [--json] | --sql <project>', file=sys.stderr)
        return 2
    rows, known = questions(events), goldens()
    if '--json' in argv:
        print(json.dumps([{'case': p['case'], 'bank': p['bank'], 'score': p['score']} for p in candidates(rows, known)], indent=1))
        return 0
    sys.stdout.write(report(rows, known, int(arg('--batch', 1))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
