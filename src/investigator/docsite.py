"""The in-app documentation at `/docs` (issue #93): the repository's Markdown, rendered to static pages.

The pages are the repository's own `docs/*.md` (and the golden set's `evals/README.md`), in the order
of `SECTIONS`, each with a left table of contents. The image renders them once at build time
(`python -m investigator.docsite build /app/docs-site --root /app`, the folder `INVESTIGATOR_DOCS`
names); the loopback workbench renders the checkout's files in memory at start-up. Nothing here
runs per request, and nothing here imports beyond the standard library, so the deploy workflow can
run `python -m investigator.docsite changelog` before installing anything.

The Markdown is the subset these files use: ATX headings, paragraphs, `-` and `1.` lists, fenced
and indented code, pipe tables, block quotes, rules, and inline code, emphasis, links and autolinks. Every
character of the source is escaped; no HTML in a document reaches the page. A relative link to a
page of the site becomes its `/docs` address; a link to any other file goes to the file on GitHub.
"""
import html
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
    ('Overview', (('', 'docs/OVERVIEW.md', 'Overview'),
                  ('how-this-is-built', 'docs/HOW_THIS_IS_BUILT.md', 'How this is built'),
                  ('agents', 'docs/AGENTS.md', 'Agents: issue to pull request'))),
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
<body><header class="app"><div class="wrap"><div class="brand"><a class="home" href="https://justfetz.github.io/">← jason fetzer</a><b>Shift Investigator</b><span>docs</span></div><a class="nav-btn" href="/">Back to the investigator</a><a class="nav-btn" href="/evals">Golden evals</a><span class="badge">Synthetic data / read-only tools</span><button type="button" id="theme" class="theme-toggle" aria-pressed="false" title="Switch between light and dark theme">Dark</button></div></header>
<div class="wrap docs-layout">
{toc}
<main class="docs-page"><article>
{body}
</article>
<nav class="docs-pager" aria-label="Previous and next page">{pager}</nav>
<p class="docs-source">Rendered from <a href="{source_url}">{source}</a> in the repository.</p>
</main></div></body></html>
"""


def build_site(root=ROOT):
    """{url path: html bytes} for every page; raises when a listed source is missing."""
    root = Path(root)
    all_pages = pages()
    by_source = {p['source']: p['url'] for p in all_pages}
    site = {}
    for n, page in enumerate(all_pages):
        path = root / page['source']
        if not path.is_file():
            raise FileNotFoundError(f'docs site: {page["source"]} is listed in SECTIONS but does not exist')
        title, body, headings = render_page(page, path.read_text(encoding='utf-8'), by_source)
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
    stamp = tuple(path.stat().st_mtime_ns for path in sources)   # one render per process until a source changes
    if _RENDERED.get('stamp') != stamp:
        _RENDERED.update(stamp=stamp, site=build_site(ROOT))
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
