"""The in-app documentation at `/docs` (issue #93): the repository's Markdown, rendered to static pages.

The pages are the repository's own `docs/*.md` (and the golden set's `evals/README.md`), in the order
of `SECTIONS`, each with a left table of contents. The image renders them once at build time
(`python -m investigator.docsite build /app/docs-site --root /app`, the folder `INVESTIGATOR_DOCS`
names); the loopback workbench renders the checkout's files in memory at start-up. Nothing here
runs per request, and the module imports nothing beyond the standard library, so the deploy workflow
can run `python -m investigator.docsite changelog` before installing anything (the case study's figures
also read `cost_model`, itself standard library only, and only when a site is built).

The Markdown is the subset these files use: ATX headings, paragraphs, `-` and `1.` lists, fenced
and indented code, pipe tables, block quotes, rules, and inline code, emphasis, links and autolinks. Every
character of the source is escaped; no HTML in a document reaches the page. A relative link to a
page of the site becomes its `/docs` address; a link to any other file goes to the file on GitHub.

A page in `FIGURE_PAGES` (the case study, issue #116) types no number: each `{{name}}` in it is filled
at build from the eval runner's files, the changelog and the cost model (`figures`), and the build
refuses the page when a digit or a number word is typed into its text (`typed_numbers`). The image build
is strict; the workbench renders such a page as a short notice instead of failing to start.
"""
import html
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = 'https://github.com/justfetz/corrugator-shift-investigator'

# The table of contents: (section, ((slug, source, label), ...)). The first page of a section is the
# one its heading links to; slug '' is /docs itself. Every source must exist (tests/test_docsite.py).
SECTIONS = (
    ('Case study', (('case-study', 'docs/CASE_STUDY.md', 'Case study'),)),
    ('Overview', (('', 'docs/OVERVIEW.md', 'Overview'),
                  ('how-this-is-built', 'docs/HOW_THIS_IS_BUILT.md', 'How this is built'))),
    ('Asking questions', (('asking-questions', 'docs/ASKING.md', 'Asking questions'),
                          ('workbench', 'docs/WORKBENCH.md', 'The workbench, step by step'),
                          ('urls', 'docs/URLS.md', 'The URL grammar'),
                          ('troubleshooting', 'docs/TROUBLESHOOTING.md', 'Troubleshooting'))),
    ('The analyst', (('analyst', 'docs/ANALYST.md', 'The analyst'),
                     ('anthropic', 'docs/ANTHROPIC_DEMO.md', 'Anthropic in the hosted demo'),
                     ('agent-contract', 'docs/AGENT_CONTRACT.md', 'The agent contract'),
                     ('agent-execution', 'docs/AGENT_EXECUTION.md', 'From question to evidence'),
                     ('openai', 'docs/OPENAI_BYOK.md', 'OpenAI, the optional provider'))),
    ('Numbers and evals', (('numbers-and-evals', 'docs/EVALS.md', 'Numbers and evals'),
                           ('golden-set', 'evals/README.md', 'The golden set in full'),
                           ('metrics', 'docs/METRICS.md', 'Metrics and formulas'),
                           ('testing', 'docs/TESTING.md', 'Testing'))),
    ('Cost model', (('cost-model', 'docs/COST_MODEL.md', 'Cost model'),)),
    ('Data', (('data', 'docs/DATA.md', 'Data'),
              ('records', 'docs/RECORDS.md', 'The record families'))),
    ('Deployment', (('deployment', 'docs/DEPLOYMENT.md', 'Deployment'),)),
    ('Changelog', (('changelog', 'docs/CHANGELOG.md', 'Changelog'),)),
)
# docs/*.md deliberately left out of the site, with the reason (a new file must be listed or named here).
UNLISTED = {'docs/FIRST_SLICE.md': 'a historical design snapshot of the first slice; the code has moved past it'}
# The pages that document a provider's own key. Everywhere else, user-facing text names no OpenAI key:
# the hosted demo runs on the server's Anthropic key, and OpenAI is an optional provider (issue #93).
PROVIDER_DOCS = ('docs/ANTHROPIC_DEMO.md', 'docs/OPENAI_BYOK.md')
OPENAI_KEY = re.compile(r"openai(?:'s)?\s+(?:api\s+)?key", re.I)

# ---- Figures (issue #116): pages whose every number is read at build, never typed.
FIGURE_PAGES = ('docs/CASE_STUDY.md',)
# The files `figures` reads from the root. They exist in the checkout and in the image (COPY evals, docs/*.md);
# the rates come from the installed `cost_model`.
FIGURE_SOURCES = ('evals/last_run.json', 'evals/history.jsonl', 'docs/CHANGELOG.md')
FIGURE = re.compile(r'\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}')
# An issue or pull request cited by its own link, [#104](<REPO>/issues/104): a name, not a figure. The number must
# be the one the link points at, so "[#289](anything)" stays typed.
CITED_REFERENCE = re.compile(r'\[#(\d+)\]\(\s*' + re.escape(REPO) + r'/(?:issues|pull)/\1\s*\)')
# Other names that are not figures: a percentile's name (p50, p95) and the unit MSF spells out.
NAMED = re.compile(r'\bp\d{2}\b|\bthousand square feet\b', re.I)
# A number word: counts and their plurals, fractions, ordinals from third up and multiples. "one" is left alone,
# as an article ("one issue, one session, one pull request"); "first" and "second" as order ("a second way").
TYPED_NUMBER = re.compile(
    r'\d|[\u00b2\u00b3\u00b9\u00bc-\u00be\u2070-\u209f\u2150-\u218b]'
    r'|\b(?:zero(?:e?s)?|twos|threes|fours|fives|sixes|sevens|eights|nines|tens|twelves|twenties|thirties|forties'
    r'|fifties|sixties|seventies|eighties|nineties|hundreds|thousands|millions|billions|trillions?|dozens?'
    r'|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen'
    r'|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion'
    r'|half|halves|quarters?|thirds?|fourths?|fifths?|sixths?|sevenths?|eighths?|ninths?|tenths?|elevenths?'
    r'|twelfths?|thirteenths?|fourteenths?|fifteenths?|sixteenths?|seventeenths?|eighteenths?|nineteenths?'
    r'|twentieths?|thirtieths?|fortieths?|fiftieths?|hundredths?|thousandths?|millionths?'
    r'|twice|thrice|single|double|triple|quadruple|quintuple|tenfold|hundredfold|thousandfold)\b', re.I)


def typed_numbers(text):
    """Every line of a figures page that types a number into its own text, as 'line N: text'.

    Each line is read without its `{{figures}}`, its `CITED_REFERENCE`s, its link addresses, its `NAMED` words
    and a `1.` list marker; what is left is what the writer typed, and none of it may be a figure. Only `1.` is a
    marker: an ordered list on such a page numbers every item `1.` (the renderer counts), so "289. runs" is typed."""
    out = []
    for n, line in enumerate(text.split('\n'), 1):
        bare = FIGURE.sub(' ', line)
        bare = CITED_REFERENCE.sub(' ', bare)
        bare = re.sub(r'\]\(\s*[^)\s]*(?:\s+"[^"]*")?\s*\)', '] ', bare)
        bare = re.sub(r'<https?://[^>\s]+>', ' ', bare)
        bare = re.sub(r'^\s*1[.)](?=\s)', ' ', bare)
        bare = NAMED.sub(' ', bare)
        if TYPED_NUMBER.search(bare):
            out.append(f'line {n}: {line.strip()}')
    return out


def expand_figures(text, values, source):
    """`text` with each `{{name}}` replaced by values[name]; an unknown name fails the build."""
    def fill(m):
        if m.group(1) not in values:
            raise KeyError(f'{source}: no figure named {m.group(1)!r}; figures() has {sorted(values)}')
        return values[m.group(1)]
    return FIGURE.sub(fill, text)


def _count(n):
    return f'{n:,}'


def _ms(value):
    return f'{value:.0f} ms' if isinstance(value, (int, float)) else 'not recorded yet'


def _usd(value, places=0):
    return f'${value:,.{places}f}'


def _code(text):
    return '`' + str(text).replace('`', '') + '`'


def figures(root=ROOT):
    """{name: Markdown} for a figures page, read from generated files at build:

    - `evals/last_run.json` and the newest line of `evals/history.jsonl`, which `evals/run.py --track` writes
      together (a pair from different runs fails the build): the golden runs by type, the offline latency,
      the replayed live rounds and their recorded cost;
    - every line of `evals/history.jsonl`: the tracked runs, and each model's newest line for the comparison;
    - `docs/CHANGELOG.md`, generated from the merged pull requests;
    - `cost_model` at its default rates (`rates.json`, the configuration the tools price with), for the dollars
      a loss is priced at: its own `price` and `trim_points_usd_per_year`, so the page moves when they do.

    A live provider's recorded cost is summed over the live rows whose recording exists (`evals/run.py`'s
    `history_line`), whatever their outcome, so the cost per recorded case divides by those rows, found here the
    same way: `evals/fixtures/<provider>/<case>.json` for each of the newest run's rows. A model whose newest line
    is an older run has no rows here; its cost divides by that line's live rows, every one of which had a recording
    when it was written."""
    from . import cost_model   # standard library only; imported here so the changelog path never needs it
    root = Path(root)
    last = json.loads((root / 'evals/last_run.json').read_text(encoding='utf-8'))
    history = [json.loads(line) for line in (root / 'evals/history.jsonl').read_text(encoding='utf-8').splitlines()
               if line.strip()]
    newest = history[-1]
    pair = ('ran_at', 'commit', 'dataset')
    if [newest.get(k) for k in pair] != [last.get(k) for k in pair]:
        raise ValueError('evals/last_run.json and the newest line of evals/history.jsonl are from different runs; '
                         '`python3 evals/run.py --track` writes both')
    rows = last['rows']
    out = {
        'evals.ran': last['ran_at'][:10],
        'evals.commit': f'[{_code(last["commit"])}]({REPO}/commit/{last["commit"]})',
        'evals.dataset': _code(last['dataset']),
        'evals.runs': _count(len(rows)),
        'evals.passed': _count(sum(r['outcome'] == 'pass' for r in rows)),
        'evals.scored': _count(sum(r['outcome'] in ('pass', 'fail') for r in rows)),
        'evals.failed': _count(sum(r['outcome'] == 'fail' for r in rows)),
        'evals.types': _count(len({r['type'] for r in rows})),
        'evals.value': _count(sum(r['type'] == 'value' for r in rows)),
        'evals.over_url': _count(sum(bool(re.search(r'\bURL \d+(?:\.\d+)? ms', r.get('note') or '')) for r in rows)),
    }
    # The golden runs by type, most cases first: offline and replayed live, passed over scored. A skip and a known
    # defect (xfail) are not scored, as in the runner's own table and history_line; each is named as the runner
    # names it, so a known defect never reads as a skip.
    types = sorted({r['type'] for r in rows}, key=lambda t: (-sum(r['type'] == t for r in rows), t))

    def known_defects(n):
        return f'{n} known defect' + ('' if n == 1 else 's')

    def score(subset):
        if not subset:
            return '–'
        ran = [r for r in subset if r['outcome'] in ('pass', 'fail')]
        skips = sum(r['outcome'] == 'skip' for r in subset)
        known = len(subset) - len(ran) - skips   # xfail: run.py's only other outcome
        return ' · '.join(([f'{sum(r["outcome"] == "pass" for r in ran)}/{len(ran)}'] if ran else [])
                          + ([f'{skips} skipped'] if skips else []) + ([known_defects(known)] if known else []))
    offline, live = [r for r in rows if not r['provider']], [r for r in rows if r['provider']]
    skipped = [r for r in rows if r['outcome'] == 'skip']
    known = [r for r in rows if r['outcome'] not in ('pass', 'fail', 'skip')]
    stale = [r for r in skipped if str(r.get('note') or '').startswith('stale recording')]
    unscored = len(skipped) + len(known)
    if not unscored:
        out['evals.skipped'] = ''
    elif not known and len(stale) == len(skipped):
        out['evals.skipped'] = (f' The other {unscored} runs are live recordings that skip, never scored, until they '
                                f'are re-recorded against the current data.')
    else:
        parts = ([f'{len(stale)} live recordings that skip until they are re-recorded against the current data']
                 if stale else []) + ([f'{len(skipped) - len(stale)} skipped'] if len(skipped) > len(stale) else []) \
            + ([f'{known_defects(len(known))}, left out of the score as the runner leaves them '
                f'([known defects](../evals/README.md#known-defects))'] if known else [])
        out['evals.skipped'] = (f' The other {unscored} runs are not scored: '
                                + (', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else parts[0]) + '.')
    out['evals.table'] = '\n'.join([
        '| Run | ' + ' | '.join(types) + ' | All |',
        '| --- |' + ' ---: |' * (len(types) + 1),
        '| Offline | ' + ' | '.join(score([r for r in offline if r['type'] == t]) for t in types) + f' | **{score(offline)}** |',
        '| Replayed live | ' + ' | '.join(score([r for r in live if r['type'] == t]) for t in types) + f' | **{score(live)}** |'])
    latency = newest.get('latency_ms') or {}
    out['latency.p50'], out['latency.p95'] = _ms(latency.get('p50')), _ms(latency.get('p95'))
    # The recordings behind the newest run's live rows, as history_line finds them.
    recordings = {}
    for r in live:
        path = root / 'evals' / 'fixtures' / r['provider'] / f'{r["case"]}.json'
        if path.is_file():
            recordings.setdefault(r['provider'], []).append(json.loads(path.read_text(encoding='utf-8')))

    def recorded_cases(provider, line, entry):
        if line is newest:
            return len(recordings.get(provider, []))
        return sum(c.get('pass', 0) + c.get('fail', 0) + c.get('skip', 0) for c in (entry.get('types') or {}).values())

    def passed_cell(entry):
        counts = (entry.get('types') or {}).values()
        ran = sum(c.get('pass', 0) + c.get('fail', 0) for c in counts)
        unscored = sum(c.get('skip', 0) for c in counts)
        return ' · '.join(([f'{sum(c.get("pass", 0) for c in counts)}/{ran}'] if ran or not unscored else [])
                          + ([f'{unscored} skipped'] if unscored else []))
    # Each model's newest tracked line: its live cases passed, the recorded cost per case, the first words.
    by_model = {}
    for line in history:
        for provider, entry in (line.get('live') or {}).items():
            by_model[(provider, entry.get('model') or 'unknown')] = (line, entry)
    table = ['| Provider | Model | Live cases passed | Recorded cost per case | First words, p50 | Tracked |',
             '| --- | --- | ---: | ---: | ---: | --- |']
    for (provider, model), (line, entry) in sorted(by_model.items()):
        cases, cost = recorded_cases(provider, line, entry), entry.get('recorded_cost_usd')
        first = (line.get('live_ms') or {}).get('first_token_p50')
        table.append(f'| {provider} | {_code(model)} | {passed_cell(entry)} | '
                     f'{_usd(cost / cases, 4) if cases and cost is not None else "–"} | {_ms(first)} | {line["ran_at"][:10]} |')
    out['models.table'] = '\n'.join(table)
    out['models.count'] = _count(len(by_model))
    current = newest.get('live') or {}
    live_cases = sum(recorded_cases(p, newest, e) for p, e in current.items())
    live_cost = sum(e.get('recorded_cost_usd') or 0 for e in current.values())
    out['live.model'] = ', '.join(_code(e.get('model') or 'unknown') for _, e in sorted(current.items())) or 'none'
    out['live.cases'] = _count(live_cases)
    out['live.cost_per_case'] = _usd(live_cost / live_cases, 4) if live_cases else 'not recorded yet'
    # What the recordings are: the analyst's shape when they were made, and whether they still score.
    every = [rec for found in recordings.values() for rec in found]
    planned = sum(rec.get('shape') == 'plan' for rec in every)
    stale_live = [r for r in live if str(r.get('note') or '').startswith('stale recording')]
    out['live.recordings'] = (
        'No live round is recorded yet.' if not every else
        f'The {len(every)} live recordings were made with '
        + ('the one-plan analyst' if planned == len(every) else
           "the analyst's earlier tool loop, before it became a checked plan and a streamed narrative"
           if not planned else f'two shapes of the analyst ({planned} as a checked plan, the rest as its earlier tool loop)')
        + ('; they were made against an earlier dataset, so they skip until they are re-recorded '
           f'([#71]({REPO}/issues/71)), which also writes the first-words time.' if stale_live and len(stale_live) == len(live) else
           f'; {len(stale_live)} were made against an earlier dataset and skip until re-recorded ([#71]({REPO}/issues/71)).'
           if stale_live else ', and they are replayed and scored on every run.'))
    out['live.first_words'] = _ms((newest.get('live_ms') or {}).get('first_token_p50'))
    out['history.runs'] = _count(len(history))
    out['history.first'] = history[0]['ran_at'][:10]
    out['history.first_cases'] = _count(history[0]['cases'])
    log = (root / 'docs/CHANGELOG.md').read_text(encoding='utf-8')
    days = re.findall(r'^## (\d{4}-\d{2}-\d{2})\s*$', log, re.M)
    out['changelog.pulls'] = _count(len(re.findall(r'^- \[#\d+\]', log, re.M)))
    out['changelog.first'], out['changelog.last'] = (min(days), max(days)) if days else ('–', '–')
    defaults = {'values': {rid: spec['default'] for rid, spec in cost_model.RATE_SPECS.items()},
                'sources': {rid: spec['source'] for rid, spec in cost_model.RATE_SPECS.items()}}
    rate, minute = defaults['values'], cost_model.price('downtime', 1, defaults)
    out['rates.paper'] = f'{_usd(rate["paper_usd_msf"])}/MSF'
    out['rates.hour'] = f'{_usd(rate["machine_usd_hour"])}/hr'
    out['rates.machine_minute'] = _usd(minute['machine_time_usd'])
    out['rates.msf_per_minute'] = f'{minute["msf_not_made"]:g} MSF'
    out['rates.margin'] = ('unset in the demo' if rate['margin_usd_msf'] is None
                           else f'{_usd(rate["margin_usd_msf"])}/MSF in the demo')
    out['rates.trim_target'] = f'{rate["trim_target_pct"]:g}%'
    out['rates.trim_point_year'] = _usd(cost_model.trim_points_usd_per_year(1, defaults))
    return out


def pages():
    """Every page in reading order: dicts with slug, source, label, section, url."""
    out = []
    for section, entries in SECTIONS:
        for slug, source, label in entries:
            out.append({'slug': slug, 'source': source, 'label': label, 'section': section,
                        'url': '/docs' + (f'/{slug}' if slug else '')})
    return out


def slugify(text, seen=None):
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens; repeats get -1, -2."""
    base = re.sub(r'[^\w\- ]', '', text.strip().lower()).replace(' ', '-')
    if seen is None:
        return base
    slug, n = base, seen.get(base, 0)
    if n:
        slug = f'{base}-{n}'
    seen[base] = n + 1
    return slug


def plain(text):
    """The text a reader sees for an inline span: no backticks, no emphasis markers, link text only."""
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'<(https?://[^>\s]+)>', r'\1', text)
    return re.sub(r'`+|\*\*|(?<!\w)[*_]|[*_](?!\w)', '', text)


class Renderer:
    """One document's Markdown to HTML. `link` maps a source-relative href to the address to use."""

    FENCE = re.compile(r'^(\s*)(`{3,}|~{3,})\s*([\w+-]*)')
    HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
    ITEM = re.compile(r'^( {0,3})([-*+]|\d{1,9}[.)])\s+(.*)$')
    RULE = re.compile(r'^ {0,3}([-*_])(\s*\1){2,}\s*$')
    TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$')

    def __init__(self, link=lambda href: href):
        self.link = link
        self.seen = {}
        self.headings = []   # (level, id, plain text) in document order

    # ---- blocks
    def render(self, text):
        return self.blocks(text.replace('\r\n', '\n').replace('\t', '    ').split('\n'))

    def _starts_block(self, lines, i):
        line = lines[i]
        return bool(self.FENCE.match(line) or self.HEADING.match(line) or self.ITEM.match(line)
                    or self.RULE.match(line) or line.lstrip().startswith('>')
                    or (line.lstrip().startswith('|') and i + 1 < len(lines) and self.TABLE_SEP.match(lines[i + 1])))

    def blocks(self, lines):
        out, i = [], 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            fence = self.FENCE.match(line)
            if fence:
                indent, marker, lang = fence.groups()
                body, i = [], i + 1
                while i < len(lines) and not lines[i].strip().startswith(marker):
                    body.append(lines[i][len(indent):] if lines[i].startswith(indent) else lines[i].lstrip())
                    i += 1
                i += 1
                cls = f' class="language-{html.escape(lang)}"' if lang else ''
                out.append(f'<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>')
                continue
            if line.startswith('    '):
                # An indented code block (METRICS.md's formulas): four spaces after a blank line or a block, kept
                # line for line. It cannot interrupt a paragraph; the paragraph loop below swallows those lines.
                body = []
                while i < len(lines) and (lines[i].startswith('    ') or not lines[i].strip()):
                    body.append(lines[i][4:])
                    i += 1
                while body and not body[-1].strip():
                    body.pop()
                out.append(f'<pre><code>{html.escape(chr(10).join(body))}</code></pre>')
                continue
            heading = self.HEADING.match(line)
            if heading:
                level, text = len(heading.group(1)), heading.group(2)
                ident = slugify(plain(text), self.seen)
                self.headings.append((level, ident, plain(text)))
                out.append(f'<h{level} id="{ident}">{self.inline(text)}</h{level}>')
                i += 1
                continue
            if self.RULE.match(line):
                out.append('<hr>')
                i += 1
                continue
            if line.lstrip().startswith('|') and i + 1 < len(lines) and self.TABLE_SEP.match(lines[i + 1]):
                rows = [line, lines[i + 1]]
                i += 2
                while i < len(lines) and lines[i].lstrip().startswith('|'):
                    rows.append(lines[i])
                    i += 1
                out.append(self.table(rows))
                continue
            if line.lstrip().startswith('>'):
                quote = []
                while i < len(lines) and lines[i].strip() and lines[i].lstrip().startswith('>'):
                    quote.append(re.sub(r'^\s*>\s?', '', lines[i]))
                    i += 1
                out.append(f'<blockquote>{self.blocks(quote)}</blockquote>')
                continue
            if self.ITEM.match(line):
                html_list, i = self.list(lines, i)
                out.append(html_list)
                continue
            para = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not self._starts_block(lines, i):
                para.append(lines[i].strip())
                i += 1
            out.append(f'<p>{self.inline(" ".join(para))}</p>')
        return '\n'.join(out)

    def list(self, lines, i):
        first = self.ITEM.match(lines[i])
        ordered = first.group(2)[0].isdigit()
        indent = len(first.group(1))
        items, loose = [], False
        while i < len(lines):
            m = self.ITEM.match(lines[i])
            if not m or len(m.group(1)) != indent or m.group(2)[0].isdigit() != ordered:
                break
            width = len(m.group(1)) + len(m.group(2)) + 1
            body, i = [m.group(3)], i + 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    # a blank line continues the item only when indented content follows it
                    j = i
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and len(lines[j]) - len(lines[j].lstrip()) >= width:
                        body.extend([''] * (j - i))
                        loose = True
                        i = j
                        continue
                    break
                lead = len(nxt) - len(nxt.lstrip())
                sibling = self.ITEM.match(nxt)
                if sibling and len(sibling.group(1)) <= indent:
                    break
                if lead < width and self._starts_block(lines, i) and not sibling:
                    break
                body.append(nxt[width:] if lead >= width else nxt.strip())
                i += 1
            items.append(body)
            if i < len(lines) and not lines[i].strip():
                j = i
                while j < len(lines) and not lines[j].strip():
                    j += 1
                nxt = self.ITEM.match(lines[j]) if j < len(lines) else None
                if nxt and len(nxt.group(1)) == indent and nxt.group(2)[0].isdigit() == ordered:
                    loose = True
                    i = j
        rendered = []
        for body in items:
            inner = self.blocks(body)
            if not loose and inner.startswith('<p>') and inner.count('<p>') == 1:
                inner = inner[3:].replace('</p>', '', 1)
            rendered.append(f'<li>{inner}</li>')
        tag = 'ol' if ordered else 'ul'
        start = int(re.match(r'\d+', first.group(2)).group()) if ordered else 1
        attr = f' start="{start}"' if start != 1 else ''
        return f'<{tag}{attr}>{"".join(rendered)}</{tag}>', i

    @staticmethod
    def cells(row):
        """A table row's cells: split on | outside code spans; a backslash-escaped pipe stays in its cell."""
        row = row.strip()
        if row.startswith('|'):
            row = row[1:]
        if row.endswith('|') and not row.endswith('\\|'):
            row = row[:-1]
        out, cell, tick, k = [], '', 0, 0
        while k < len(row):
            ch = row[k]
            if ch == '\\' and k + 1 < len(row) and row[k + 1] == '|':
                cell += '|'
                k += 2
                continue
            if ch == '`':
                run = len(row[k:]) - len(row[k:].lstrip('`'))
                tick = 0 if tick == run else (run if not tick else tick)
                cell += '`' * run
                k += run
                continue
            if ch == '|' and not tick:
                out.append(cell.strip())
                cell = ''
            else:
                cell += ch
            k += 1
        out.append(cell.strip())
        return out

    def table(self, rows):
        head, sep, body = self.cells(rows[0]), self.cells(rows[1]), [self.cells(r) for r in rows[2:]]
        align = []
        for spec in sep:
            spec = spec.strip()
            align.append('center' if spec.startswith(':') and spec.endswith(':') else
                         'right' if spec.endswith(':') else 'left' if spec.startswith(':') else None)

        def cell(tag, text, n):
            cls = f' class="{align[n]}"' if n < len(align) and align[n] in ('right', 'center') else ''
            return f'<{tag}{cls}>{self.inline(text)}</{tag}>'
        thead = '<tr>' + ''.join(cell('th', t, n) for n, t in enumerate(head)) + '</tr>'
        tbody = ''.join('<tr>' + ''.join(cell('td', r[n] if n < len(r) else '', n) for n in range(len(head))) + '</tr>'
                        for r in body)
        return (f'<div class="table-scroll" tabindex="0" role="region" aria-label="Table">'
                f'<table><thead>{thead}</thead><tbody>{tbody}</tbody></table></div>')

    # ---- inline
    def inline(self, text):
        text = text.replace('\x00', '\ufffd')   # NUL marks a held span below; one in the source must not forge one
        held = []

        def hold(markup):
            held.append(markup)
            return f'\x00{len(held) - 1}\x00'

        def code(m):
            return hold(f'<code>{html.escape(m.group(2).strip() if m.group(2).strip() else m.group(2))}</code>')
        text = re.sub(r'(`+)(.+?)(?<!`)\1(?!`)', code, text)
        text = re.sub(r'<(https?://[^>\s]+)>', lambda m: hold(self.anchor(m.group(1), html.escape(m.group(1)))), text)

        def link(m):
            label, href = m.group(1), m.group(2)
            return hold(self.anchor(self.link(href), self.emphasis(html.escape(label))))
        text = re.sub(r'\[((?:[^\[\]]|\x00\d+\x00)+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)', link, text)
        text = re.sub(r'(?<![\w/"=])https?://[^\s<>()\x00]*[^\s<>().,;:!?\x00\'"]',
                      lambda m: hold(self.anchor(m.group(0), html.escape(m.group(0)))), text)
        text = self.emphasis(html.escape(text, quote=False))
        while '\x00' in text:
            text = re.sub(r'\x00(\d+)\x00', lambda m: held[int(m.group(1))], text)
        return text

    @staticmethod
    def emphasis(text):
        text = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])', r'<em>\1</em>', text)
        return re.sub(r'(?<![\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w])', r'<em>\1</em>', text)

    @staticmethod
    def anchor(href, label):
        return f'<a href="{html.escape(href)}">{label}</a>'


def link_resolver(source, by_source):
    """A source-relative href as the reader's address: a page of the site, the file on GitHub, or as written."""
    def resolve(href):
        scheme = re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*):', href)
        if scheme:   # only web and mail links leave the page; a javascript: or data: link becomes inert
            return href if scheme.group(1).lower() in ('http', 'https', 'mailto') else '#'
        if href.startswith('#') or href.startswith('/'):
            return href
        path, _, frag = href.partition('#')
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
        if target.startswith('..'):
            return REPO
        if target in by_source:
            return by_source[target] + (f'#{frag}' if frag else '')
        kind = 'tree' if path.endswith('/') or '.' not in posixpath.basename(target) else 'blob'
        return f'{REPO}/{kind}/main/{target}' + (f'#{frag}' if frag else '')
    return resolve


def render_page(page, text, by_source):
    """(title, article html, headings) for one source document."""
    renderer = Renderer(link_resolver(page['source'], by_source))
    body = renderer.render(text)
    title = next((t for level, _, t in renderer.headings if level == 1), page['label'])
    return title, body, renderer.headings


def _contents(all_pages):
    """The whole site as a list, for the overview page; each entry carries its page's slug as an id,
    so an old /guide#asking-questions or #how-this-is-built link lands on its entry."""
    rows = []
    for section, _ in SECTIONS:
        items = ''.join(f'<li id="{p["slug"] or "overview"}"><a href="{p["url"]}">{html.escape(p["label"])}</a></li>'
                        for p in all_pages if p['section'] == section)
        rows.append(f'<li><span class="docs-contents-section">{html.escape(section)}</span><ul>{items}</ul></li>')
    return f'<h2 id="contents">Contents</h2>\n<ol class="docs-contents">{"".join(rows)}</ol>'


def _toc(all_pages, current, headings):
    subs = [(i, t) for level, i, t in headings if level == 2]
    on_page = ('<ol class="docs-headings">' + ''.join(f'<li><a href="#{i}">{html.escape(t)}</a></li>' for i, t in subs)
               + '</ol>') if subs else ''
    out = []
    for section, _ in SECTIONS:
        members = [p for p in all_pages if p['section'] == section]
        cls = 'docs-section current' if current['section'] == section else 'docs-section'
        if len(members) == 1:   # a one-page section is its page: no list repeating its name
            p = members[0]
            mark = ' aria-current="page"' if p is current else ''
            out.append(f'<li class="{cls}"><a class="docs-section-link" href="{p["url"]}"{mark}>{html.escape(section)}</a>'
                       f'{on_page if p is current else ""}</li>')
            continue
        links = []
        for p in members:
            mark = ' aria-current="page"' if p is current else ''
            links.append(f'<li><a href="{p["url"]}"{mark}>{html.escape(p["label"])}</a>{on_page if p is current else ""}</li>')
        out.append(f'<li class="{cls}"><a class="docs-section-link" href="{members[0]["url"]}">{html.escape(section)}</a>'
                   f'<ol class="docs-pages">{"".join(links)}</ol></li>')
    return ('<nav class="docs-toc" aria-label="Documentation"><p class="docs-toc-title">Documentation</p>'
            f'<ol class="docs-sections">{"".join(out)}</ol></nav>')


TEMPLATE = """<!doctype html>
<html lang="en"><head><script src="/theme.js"></script><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · Shift Investigator docs</title><link rel="stylesheet" href="/style.css"></head>
<body class="docs-body"><header class="app"><div class="wrap"><div class="brand"><a class="home" href="https://justfetz.github.io/">← jason fetzer</a><b>Shift Investigator</b><span>docs</span></div><a class="nav-btn" href="/">Back to the investigator</a><a class="nav-btn" href="/evals">Golden evals</a><span class="badge">Synthetic data / read-only tools</span><button type="button" id="theme" class="theme-toggle" aria-pressed="false" title="Switch between light and dark theme">Dark</button></div></header>
<div class="wrap docs-layout">
{toc}
<main class="docs-page"><article>
{body}
</article>
<nav class="docs-pager" aria-label="Previous and next page">{pager}</nav>
<p class="docs-source">Rendered from <a href="{source_url}">{source}</a> in the repository.</p>
<p class="docs-source">Questions asked on the hosted demo are logged anonymously to improve the answers; the question log keeps no IP address, browser details or cookies (<a href="/docs/data#the-question-log">what is kept</a>).</p>
</main></div></body></html>
"""


def _figures_page(page, text, root, values):
    """A figures page's Markdown with its figures filled, and the values for the next one; raises when the page
    types a number, names a figure the build does not know, or its files cannot be read or disagree."""
    typed = typed_numbers(text)
    if typed:
        raise ValueError(f'docs site: {page["source"]} reads every figure from a generated file, '
                         f'but these lines type one; use a {{{{figure}}}}: {typed}')
    values = figures(root) if values is None else values
    return expand_figures(text, values, page['source']), values


def build_site(root=ROOT, strict=True):
    """{url path: html bytes} for every page; raises when a listed source is missing.

    A figures page that cannot be built raises too, unless `strict` is False (the workbench, `load_site`): then
    the page says why in one line and the rest of the site, and the server, carry on."""
    root = Path(root)
    all_pages = pages()
    by_source = {p['source']: p['url'] for p in all_pages}
    site, values = {}, None
    for n, page in enumerate(all_pages):
        path = root / page['source']
        if not path.is_file():
            raise FileNotFoundError(f'docs site: {page["source"]} is listed in SECTIONS but does not exist')
        text = path.read_text(encoding='utf-8')
        if page['source'] in FIGURE_PAGES:
            try:
                text, values = _figures_page(page, text, root, values)
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:   # JSONDecodeError is a ValueError
                if strict:
                    raise
                print(f'docs site: {page["source"]} not built: {exc}', file=sys.stderr)
                text = (f'# {page["label"]}\n\nThis page reads every figure from the eval history, the changelog and the '
                        f'cost model when the docs are built, and could not be built here: {exc}\n')
        title, body, headings = render_page(page, text, by_source)
        if page['slug'] == '':
            body += '\n' + _contents(all_pages)
        prev = all_pages[n - 1] if n else None
        nxt = all_pages[n + 1] if n + 1 < len(all_pages) else None
        pager = ''.join([
            f'<a class="docs-prev" href="{prev["url"]}"><small>Previous</small>{html.escape(prev["label"])}</a>' if prev else '',
            f'<a class="docs-next" href="{nxt["url"]}"><small>Next</small>{html.escape(nxt["label"])}</a>' if nxt else ''])
        site[page['url']] = TEMPLATE.format(
            title=html.escape(title), toc=_toc(all_pages, page, headings), body=body, pager=pager,
            source=html.escape(page['source']), source_url=html.escape(f'{REPO}/blob/main/{page["source"]}')).encode()
    return site


def _filename(url):
    return 'index.html' if url == '/docs' else url.rsplit('/', 1)[1] + '.html'


def write_site(out, root=ROOT):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    site = build_site(root)
    for url, data in site.items():
        (out / _filename(url)).write_bytes(data)
    return site


def load_site():
    """The pages the server serves: the prebuilt folder INVESTIGATOR_DOCS names (the image), else the
    checkout's docs rendered now (the workbench), else None (a wheel installed without the repository)."""
    named = os.environ.get('INVESTIGATOR_DOCS')
    if named:
        folder = Path(named)
        return {p['url']: (folder / _filename(p['url'])).read_bytes() for p in pages()
                if (folder / _filename(p['url'])).is_file()} or None
    sources = [ROOT / p['source'] for p in pages()]
    if not all(path.is_file() for path in sources):
        return None
    # A missing figure source is the lenient build's to report on its page; it does not take the site down.
    inputs = sources + [ROOT / s for s in FIGURE_SOURCES]
    stamp = tuple(path.stat().st_mtime_ns if path.is_file() else None for path in inputs)   # one render until one changes
    if _RENDERED.get('stamp') != stamp:
        _RENDERED.update(stamp=stamp, site=build_site(ROOT, strict=False))
    return _RENDERED['site']


_RENDERED = {}


# ---- The changelog: every pull request merged into main, from the git history.
CHANGELOG_HEAD = """# Changelog

Every pull request merged into `main`, newest first, under the day it merged. Generated from the git
history by `python -m investigator.docsite changelog`, which the Cloud Run deploy runs on the full history
before it builds, so the deployed page includes the pull request that triggered the deploy. Earlier
commits pushed to `main` without a pull request are not listed; `git log` has them.
"""


def merged_pulls(ref='HEAD', root=ROOT):
    """[(date, number, title)] newest first, from the first-parent history of `ref`."""
    log = subprocess.run(['git', '-C', str(root), 'log', '--first-parent', '--date=short',
                          '--format=%ad%x1f%s%x1f%b%x1e', ref], capture_output=True, text=True, check=True).stdout
    out = []
    for record in log.split('\x1e'):
        if not record.strip():
            continue
        date, subject, body = (record.strip('\n').split('\x1f') + ['', ''])[:3]
        merge = re.match(r'Merge pull request #(\d+)\b', subject)
        squash = re.search(r'\s*\(#(\d+)\)$', subject)
        if merge:
            title = next((line.strip() for line in body.splitlines() if line.strip()), subject)
            out.append((date, int(merge.group(1)), title))
        elif squash:
            out.append((date, int(squash.group(1)), subject[:squash.start()]))
    return out


def changelog(pulls):
    lines, day = [CHANGELOG_HEAD], None
    for date, number, title in pulls:
        if date != day:
            lines.append(f'\n## {date}\n')
            day = date
        title = re.sub(r'(?<![\w/\[])#(\d+)(?!\w)', rf'[#\1]({REPO}/issues/\1)', title.replace('[', '(').replace(']', ')'))
        lines.append(f'- [#{number}]({REPO}/pull/{number}) {title}')
    return '\n'.join(lines) + '\n'


def write_changelog(target, pulls):
    """Replace `target` with the changelog of `pulls`, or keep it and return 1.

    The deploy runs this on a fresh checkout; a shallow or partial history must not shrink the page, so a
    list shorter than the one already committed is refused. The text is rendered once before it is written
    (a page the docs build could not take never lands), and the write is a rename, so a step killed midway
    leaves the committed file whole."""
    target = Path(target)
    held = len(re.findall(r'^- \[#\d+\]', target.read_text(encoding='utf-8'), re.M)) if target.is_file() else 0
    if len(pulls) < held:
        print(f'{target}: kept; the history lists {len(pulls)} merged pull requests, the file already {held}', file=sys.stderr)
        return 1
    text = changelog(pulls)
    Renderer().render(text)
    partial = target.with_name(target.name + '.partial')
    partial.write_text(text, encoding='utf-8')
    os.replace(partial, target)
    print(f'{target}: {len(pulls)} merged pull requests')
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['changelog']:
        # --ref origin/main on a branch: main's merges sit on a merge's second parent there, off the first-parent walk
        ref = argv.pop(argv.index('--ref') + 1) if '--ref' in argv else 'HEAD'
        root = Path(argv.pop(argv.index('--root') + 1)) if '--root' in argv else ROOT
        argv = [a for a in argv if a not in ('--ref', '--root')]
        target = Path(argv[1]) if len(argv) > 1 else root / 'docs' / 'CHANGELOG.md'
        return write_changelog(target, merged_pulls(ref, root=root))
    if argv[:1] == ['build'] and len(argv) >= 2:
        root = Path(argv[argv.index('--root') + 1]) if '--root' in argv else ROOT
        site = write_site(argv[1], root)
        print(f'{argv[1]}: {len(site)} pages')
        return 0
    print('usage: python -m investigator.docsite build <out> [--root <repo>] | changelog [<file>] [--ref <git ref>]', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
