"""The in-app docs at /docs (issue #93): the table of contents, the rendered pages, the routes and the build.

Two acceptance tests of the issue live here: every docs/*.md listed in the table of contents exists (and
every docs/*.md is either listed or named as left out), and no user-facing HTML outside the provider docs
mentions an OpenAI API key. The rest pins the renderer's escaping, the links between pages, the old
/guide address, the image build and the changelog.
"""
import html
import json
import http.client
import re
import shutil
import subprocess
from pathlib import Path
from threading import Thread

import pytest

from investigator import cost_model, docsite
from investigator.server import make_server

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src' / 'investigator' / 'web'


@pytest.fixture(scope='module')
def site():
    return docsite.build_site(ROOT)


def page_of(source):
    return next(p for p in docsite.pages() if p['source'] == source)


def text_of(markup):
    """What a reader sees: the markup with its tags dropped and its entities decoded."""
    return html.unescape(re.sub(r'<[^>]+>', '', markup))


def test_every_docs_file_listed_in_the_toc_exists():
    listed = [p['source'] for p in docsite.pages()]
    missing = [s for s in listed if not (ROOT / s).is_file()]
    assert not missing, f'listed in docsite.SECTIONS but not in the repository: {missing}'
    assert len(listed) == len(set(listed)), 'a file is listed twice'
    slugs = [p['slug'] for p in docsite.pages()]
    assert len(slugs) == len(set(slugs)) and slugs[:2] == ['case-study', ''], \
        'unique slugs; the case study comes first (issue #116), then the overview at /docs itself'
    assert [s for s, _ in docsite.SECTIONS] == ['Case study', 'Overview', 'Asking questions', 'The analyst', 'Numbers and evals',
                                                'Cost model', 'Data', 'Deployment', 'Changelog'], 'the sections of issues #93 and #116'
    # The other direction: a new docs/*.md is either on the site or named, with a reason, as left out.
    on_disk = {f'docs/{p.name}' for p in (ROOT / 'docs').glob('*.md')}
    unaccounted = on_disk - set(listed) - set(docsite.UNLISTED)
    assert not unaccounted, f'add these to docsite.SECTIONS or docsite.UNLISTED: {sorted(unaccounted)}'
    assert all((ROOT / s).is_file() for s in docsite.UNLISTED)


def test_a_missing_listed_file_fails_the_build(tmp_path, monkeypatch):
    sections = docsite.SECTIONS + (('Extra', (('extra', 'docs/NOT_THERE.md', 'Extra'),)),)
    monkeypatch.setattr(docsite, 'SECTIONS', sections)
    with pytest.raises(FileNotFoundError, match='docs/NOT_THERE.md'):
        docsite.build_site(ROOT)


def test_the_openai_key_pattern_catches_the_stale_wording():
    for stale in ('OpenAI API key (optional if configured on server)', 'your OpenAI key', "OpenAI's API key", 'openai  api key'):
        assert docsite.OPENAI_KEY.search(stale), stale
    for fine in ('OPENAI_API_KEY', 'X-OpenAI-Key', 'OpenAI, the optional provider', 'Anthropic API key'):
        assert not docsite.OPENAI_KEY.search(fine), fine


def test_no_user_facing_html_mentions_an_openai_key_outside_the_provider_docs(site):
    """Issue #93: the public demo runs on the server's Anthropic key; OpenAI is an optional provider,
    documented as such on its own pages. Every other page a visitor can open names no OpenAI key."""
    pages = {f'web/{p.name}': p.read_text(encoding='utf-8') for p in WEB.glob('*.html')}
    pages.update({p['url']: site[p['url']].decode() for p in docsite.pages() if p['source'] not in docsite.PROVIDER_DOCS})
    assert 'web/index.html' in pages and '/docs/asking-questions' in pages and '/docs/analyst' in pages
    offending = {name: docsite.OPENAI_KEY.findall(html.unescape(markup) + '\n' + text_of(markup))
                 for name, markup in pages.items()}
    assert not {k: v for k, v in offending.items() if v}, offending
    assert not (WEB / 'guide.html').exists(), 'the old How it works page is retired; /guide redirects to /docs'
    # The provider docs keep documenting the provider, and the analyst page says what the default is.
    analyst = text_of(site['/docs/analyst'].decode())
    assert 'Anthropic is the default' in analyst and 'OpenAI is an optional provider' in analyst


def test_the_app_links_to_docs_not_the_old_guide():
    index = (WEB / 'index.html').read_text(encoding='utf-8')
    evals = (WEB / 'evals.html').read_text(encoding='utf-8')
    assert 'href="/guide' not in index + evals
    assert '<a class="top-link" href="/docs">Docs</a>' in index
    assert '<a class="top-link" href="/docs/case-study">Case study</a>' in index, 'issue #116: linked from the app header'
    assert 'href="/docs/asking-questions"' in index and '<a class="nav-btn" href="/docs">Docs</a>' in evals


def test_every_page_has_the_toc_and_every_internal_link_resolves(site):
    ids = {url: set(re.findall(r' id="([^"]+)"', page.decode())) for url, page in site.items()}
    broken = []
    for url, page in site.items():
        markup = page.decode()
        assert markup.count('<nav class="docs-toc"') == 1 and markup.count('aria-current="page"') == 1, url
        assert all(f'href="{p["url"]}"' in markup for p in docsite.pages()), f'{url}: the TOC lists every page'
        for found in re.findall(r' id="([^"]+)"', markup):
            assert markup.count(f' id="{found}"') == 1, f'{url}: duplicate id {found}'
        for href in map(html.unescape, re.findall(r'href="([^"]+)"', markup)):
            path, _, frag = href.partition('#')
            if href.startswith('#'):
                path = url
            elif not href.startswith('/docs'):
                assert not re.search(r'\.md(#|$)', href) or href.startswith(docsite.REPO), f'{url}: unresolved {href}'
                continue
            if path not in site or (frag and frag not in ids[path]):
                broken.append(f'{url} -> {href}')
    assert not broken, broken


def test_the_overview_keeps_the_old_guide_anchors(site):
    """/guide#asking-questions and /guide#how-this-is-built were linked from the app; the 301 keeps the
    fragment, so the overview carries both ids, on the contents entries for those pages."""
    overview = site['/docs'].decode()
    assert '<li id="asking-questions"><a href="/docs/asking-questions">' in overview
    assert '<li id="how-this-is-built"><a href="/docs/how-this-is-built">' in overview


def test_the_renderer_escapes_everything_and_renders_the_subset():
    r = docsite.Renderer(docsite.link_resolver('docs/X.md', {'docs/URLS.md': '/docs/urls'}))
    out = r.render('\n'.join([
        '# Title <script>alert(1)</script>',
        'Text with <img src=x onerror=alert(1)> and **bold**, *em*, `a|b<c>`, snake_case, [urls](URLS.md#the-routes),',
        '[code](../src/investigator/server.py) and <https://example.com/a_b>, [x](javascript:alert(1)), [y](JaVaScRiPt:alert(1)).',
        '',
        '| A | B |', '| --- | ---: |', '| `x | y` | 2 |',
        '',
        '- one', '  continued', '- two',
        '',
        '3. three', '4. four',
        '',
        '```bash', 'echo "<hi>" & bye', '```',
    ]))
    assert '<script>' not in out and '<img' not in out and 'javascript:' not in out.lower()
    assert '<a href="#">x</a>' in out and '<a href="#">y</a>' in out
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in out and '&lt;img src=x onerror=alert(1)&gt;' in out
    assert '<strong>bold</strong>' in out and '<em>em</em>' in out and 'snake_case' in out
    assert '<code>a|b&lt;c&gt;</code>' in out
    assert '<a href="/docs/urls#the-routes">urls</a>' in out
    assert f'<a href="{docsite.REPO}/blob/main/src/investigator/server.py">code</a>' in out
    assert '<a href="https://example.com/a_b">https://example.com/a_b</a>' in out
    assert '<td><code>x | y</code></td><td class="right">2</td>' in out
    assert '<ul><li>one continued</li><li>two</li></ul>' in out and '<ol start="3"><li>three</li><li>four</li></ol>' in out
    assert '<pre><code class="language-bash">echo &quot;&lt;hi&gt;&quot; &amp; bye</code></pre>' in out
    assert r.headings[0][:2] == (1, 'title-scriptalert1script')


def test_indented_code_blocks_keep_their_lines():
    """Codex on #107: METRICS.md's four-space formula blocks were folded into one paragraph."""
    out = docsite.Renderer().render('\n'.join([
        'Intro text', '    still the same paragraph (an indented line cannot interrupt one)', '',
        '    a = lcm(x)', '    b = a_b * 2', '', '      c = <d>', '', 'After.', '',
        '- item', '', '      code in the item']))
    assert out.startswith('<p>Intro text still the same paragraph (an indented line cannot interrupt one)</p>')
    assert '<pre><code>a = lcm(x)\nb = a_b * 2\n\n  c = &lt;d&gt;</code></pre>\n<p>After.</p>' in out
    assert '<li><p>item</p>\n<pre><code>code in the item</code></pre></li>' in out


def test_the_metrics_formulas_render_as_code(site):
    page = site['/docs/metrics'].decode()
    assert '<pre><code>common_inches = lcm(cutoff lengths)\nplanned_travel_inches = ' in page
    assert '<pre><code>density = (value − minimum) / (maximum − minimum)\nalpha = 0.22 + 0.78 × density\n' in page


def test_a_nul_in_the_source_cannot_forge_a_held_span():
    """The inline renderer holds finished spans behind NUL-delimited markers; a NUL in a document must
    neither splice one span's markup into another's attribute nor loop forever."""
    out = docsite.Renderer(docsite.link_resolver('docs/X.md', {})).render(
        '<https://x/onmouseover=alert(1)//> [a](\x000\x00) and a lone \x00 here')
    assert out.count('<a ') == 2 and 'href="https://x/onmouseover=alert(1)//"' in out
    assert re.findall(r'href="([^"]*)"', out)[1].startswith(docsite.REPO) and '\x00' not in out


def test_prebuilt_folder_is_what_the_server_serves(tmp_path, monkeypatch, site):
    out = tmp_path / 'docs-site'
    assert docsite.main(['build', str(out), '--root', str(ROOT)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(docsite._filename(u) for u in site)
    monkeypatch.setenv('INVESTIGATOR_DOCS', str(out))
    assert docsite.load_site() == site
    monkeypatch.setenv('INVESTIGATOR_DOCS', str(tmp_path / 'absent'))
    assert docsite.load_site() is None


def test_docs_routes():
    server = make_server(0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(path, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        try:
            client.request('GET', path, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()
    try:
        status, headers, body = get('/docs')
        assert status == 200 and headers['Content-Type'] == 'text/html; charset=utf-8'
        assert b'<nav class="docs-toc"' in body and "script-src 'self'" in headers['Content-Security-Policy']
        assert get('/docs/')[2] == body
        assert get('/docs', {'If-None-Match': headers['ETag']})[0] == 304
        for page in docsite.pages():
            assert get(page['url'])[0] == 200, page['url']
        assert get('/docs/analyst?x=1')[0] == 200
        assert get('/docs/not-a-page')[0] == 404 and get('/docs/../README.md')[0] == 404
        status, headers, _ = get('/guide')
        assert status == 301 and headers['Location'] == '/docs'
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        server.app.close()


def test_the_image_renders_the_docs_and_the_deploy_refreshes_the_changelog():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'COPY docs/*.md ./docs/' in dockerfile
    assert 'python -m investigator.docsite build /app/docs-site --root /app' in dockerfile
    assert 'INVESTIGATOR_DOCS=/app/docs-site' in dockerfile
    assert 'COPY evals ./evals' in dockerfile, 'the golden set page renders evals/README.md'
    # The owner's condition: a failed changelog step can never fail the deploy. So the step may fail and may
    # time out, and the history fetch it needs is inside it, not in the deploy's own checkout.
    deploy = (ROOT / '.github' / 'workflows' / 'deploy-cloudrun.yml').read_text(encoding='utf-8')
    step = deploy[deploy.index('- name: Changelog from the merged pull requests'):]
    step = step[:step.index('- name:', 10)]
    assert 'continue-on-error: true' in step and re.search(r'timeout-minutes: [1-5]\n', step)
    assert 'git fetch --quiet --unshallow origin' in step and 'python3 -m investigator.docsite changelog' in step
    assert 'fetch-depth' not in deploy, 'the checkout stays as it was; the full history is fetched inside the step'


def test_the_cloud_build_upload_keeps_the_docs():
    """`gcloud run deploy --source .` uploads what .gcloudignore allows; the image's COPY docs/*.md needs them."""
    ignored = [line.strip() for line in (ROOT / '.gcloudignore').read_text(encoding='utf-8').splitlines()
               if line.strip() and not line.startswith('#')]
    for pattern in ('docs', 'docs/', 'docs/*', '*.md', 'evals', 'evals/'):
        assert pattern not in ignored, f'.gcloudignore drops {pattern}: the docs site cannot be built'
    gcloud = shutil.which('gcloud')
    if gcloud:
        listed = subprocess.run([gcloud, 'meta', 'list-files-for-upload', str(ROOT)], capture_output=True, text=True)
        if listed.returncode == 0:
            uploaded = {line.strip().replace('\\', '/') for line in listed.stdout.splitlines()}
            assert {p['source'] for p in docsite.pages()} <= uploaded


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                           '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True, text=True).stdout


def test_changelog_lists_merged_and_squashed_pull_requests(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'Direct push, no pull request')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'Squashed change [wip] (#7)')
    git(repo, 'checkout', '-q', '-b', 'feature')
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'Work on the branch (#12)')
    git(repo, 'checkout', '-q', 'main')
    git(repo, 'merge', '-q', '--no-ff', 'feature', '-m', 'Merge pull request #9 from someone/feature',
        '-m', 'Docs site for issue #93')
    pulls = docsite.merged_pulls(root=repo)
    assert [(n, t) for _, n, t in pulls] == [(9, 'Docs site for issue #93'), (7, 'Squashed change [wip]')], \
        'first-parent only: a branch commit is not a merged pull request, and a direct push is skipped'
    text = docsite.changelog(pulls)
    assert text.startswith(docsite.CHANGELOG_HEAD)
    assert f'- [#9]({docsite.REPO}/pull/9) Docs site for issue [#93]({docsite.REPO}/issues/93)' in text
    assert f'- [#7]({docsite.REPO}/pull/7) Squashed change (wip)' in text
    rendered = docsite.Renderer().render(text)
    assert f'<a href="{docsite.REPO}/pull/9">#9</a>' in rendered


def test_the_changelog_never_shrinks_and_never_lands_half_written(tmp_path):
    """A shallow or partial history on the deploy runner must not replace the committed list with a shorter one."""
    target = tmp_path / 'CHANGELOG.md'
    full = [('2026-11-10', n, f'Change {n}') for n in (3, 2, 1)]
    assert docsite.write_changelog(target, full) == 0 and target.read_text() == docsite.changelog(full)
    assert docsite.write_changelog(target, full[:1]) == 1, 'fewer pull requests than the file holds: refused'
    assert target.read_text() == docsite.changelog(full), 'and the committed file is untouched'
    longer = [('2026-11-11', 4, 'Change 4')] + full
    assert docsite.write_changelog(target, longer) == 0 and target.read_text() == docsite.changelog(longer)
    assert [p.name for p in tmp_path.iterdir()] == ['CHANGELOG.md'], 'written by rename: no partial file left'


def test_the_committed_changelog_is_in_the_generated_shape():
    text = (ROOT / 'docs' / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert text.startswith(docsite.CHANGELOG_HEAD)
    entries = re.findall(r'^- \[#(\d+)\]\(' + re.escape(docsite.REPO) + r'/pull/\1\) \S', text, re.M)
    assert entries and len(entries) == len(set(entries)) == text.count('\n- ')


# ---- The case study (issue #116): first in the table of contents, every figure read from a generated file.
CASE_STUDY = ROOT / 'docs' / 'CASE_STUDY.md'


def figure_root(tmp_path):
    """A copy of what the docs build reads (the recordings too), to break one file at a time."""
    for source in [p['source'] for p in docsite.pages()] + list(docsite.FIGURE_SOURCES):
        (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / source, tmp_path / source)
    shutil.copytree(ROOT / 'evals' / 'fixtures', tmp_path / 'evals' / 'fixtures')
    return tmp_path


def recorded_live_cases(root, last):
    """The newest run's live rows whose recording exists, as evals/run.py's history_line sums their cost."""
    return [r for r in last['rows'] if r['provider']
            and (Path(root) / 'evals' / 'fixtures' / r['provider'] / f'{r["case"]}.json').is_file()]


def test_the_case_study_is_first_and_renders_its_diagram(site):
    page = site['/docs/case-study'].decode()
    toc = re.findall(r'<a class="docs-section-link" href="([^"]+)"', page)
    assert toc[0] == '/docs/case-study', 'first in the /docs table of contents'
    assert '<li id="case-study"><a href="/docs/case-study">Case study</a></li>' in site['/docs'].decode(), \
        'and first in the overview contents'
    diagram = re.search(r'<pre><code class="language-diagram">(.*?)</code></pre>', page, re.S)
    assert diagram, 'the design in one diagram'
    for box in ('offline router', 'DuckDB tools', 'answer shape', 'card and URL', 'Ask the analyst', 'app checks every step', 'validators'):
        assert box in diagram.group(1), box
    headings = re.findall(r'<h2 id="[^"]+">([^<]+)</h2>', page)
    assert headings == ['The problem', 'The design in one diagram', 'How we know the numbers are right', 'Results',
                        "Trade-offs and what I'd do next", 'Built with'], headings
    assert '{{' not in page and '}}' not in page, 'every figure is filled at build'


def test_every_case_study_figure_comes_from_a_generated_file(site):
    """The figures on the page are the files' own values, computed here a second way."""
    text = text_of(site['/docs/case-study'].decode())
    last = json.loads((ROOT / 'evals' / 'last_run.json').read_text(encoding='utf-8'))
    history = [json.loads(line) for line in (ROOT / 'evals' / 'history.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    rows, newest = last['rows'], history[-1]
    passed = sum(r['outcome'] == 'pass' for r in rows)
    scored = sum(r['outcome'] in ('pass', 'fail') for r in rows)
    # Results leads with the score and the offline latency of the newest tracked run.
    assert (f'{passed:,} of {scored:,} scored golden runs pass, and an offline answer takes '
            f'{newest["latency_ms"]["p50"]:.0f} ms at p50 and {newest["latency_ms"]["p95"]:.0f} ms at p95, '
            f'in the last tracked run ({last["ran_at"][:10]}, commit {last["commit"]};') in text
    unscored = len(rows) - scored
    assert (f'The other {unscored} runs' in text) == bool(unscored), 'skipped runs are said, never counted as failures'
    assert last['dataset'] in text
    offline = [r for r in rows if not r['provider']]
    assert 'Offline' in text and f'{sum(r["outcome"] == "pass" for r in offline)}/{len(offline)}' in text
    assert f'{sum(r["type"] == "value" for r in rows)} cases hold a tool\'s numbers' in text
    live = newest['live']['anthropic']
    cases = len(recorded_live_cases(ROOT, last))
    assert f'${live["recorded_cost_usd"] / cases:.4f} on {live["model"]}, over {cases} recorded cases' in text
    assert f'{len(rows):,} now, up from {history[0]["cases"]} on {history[0]["ran_at"][:10]}, over {len(history)} tracked runs' in text
    log = (ROOT / 'docs' / 'CHANGELOG.md').read_text(encoding='utf-8')
    days = re.findall(r'^## (\S+)$', log, re.M)
    assert f'{len(re.findall(r"^- \[#", log, re.M))} pull requests merged between {min(days)} and {max(days)}' in text
    # The dollars are the cost model's own, at its default rates: a change there moves the page.
    defaults = {'values': {k: spec['default'] for k, spec in cost_model.RATE_SPECS.items()}, 'sources': {}}
    paper, minute = defaults['values']['paper_usd_msf'], cost_model.price('downtime', 1, defaults)
    assert f'threshold is ${cost_model.trim_points_usd_per_year(1, defaults):,.0f} a year on one machine at ${paper}/MSF' in text
    assert f'It is ${minute["machine_time_usd"]:,.0f} of machine time' in text and f'or {minute["msf_not_made"]:g} MSF of board' in text
    assert 'MSF: thousand square feet' in text, 'MSF is defined once, at its first use'
    margin = defaults['values']['margin_usd_msf']
    assert f'a contribution margin the owner sets ({"unset in the demo" if margin is None else f"${margin:,.0f}/MSF in the demo"})' in text
    assert f'{defaults["values"]["trim_target_pct"]:g}% threshold' in text
    # Every {{name}} in the source is a figure the build knows.
    names = set(docsite.FIGURE.findall(CASE_STUDY.read_text(encoding='utf-8')))
    assert names and names <= set(docsite.figures(ROOT)), names - set(docsite.figures(ROOT))


def test_a_number_typed_into_the_case_study_fails_the_build(tmp_path):
    assert docsite.typed_numbers(CASE_STUDY.read_text(encoding='utf-8')) == [], 'the committed page types no number'
    for typed in ('The set has 289 cases.', 'It answers in 14 ms.', 'Cost: $0.0027 an answer.', 'seven case types',
                  'twenty-nine runs', 'a tenth of a point', 'asked twice', 'Dated 2026-11-11.', '1. an ordered item at 50 ms',
                  'a third of the runs', 'a fifth of the setups', '½ point', 'answers x10 faster', 'the #1 loss', 'utf8',
                  # the coordinator's review on #120: a list marker other than 1., plural magnitudes, ordinals, a
                  # reference whose number is not the issue it links to
                  '289. golden runs pass', '1. ok\n\n289. golden runs pass', 'hundreds of cases', 'thousands of setups',
                  'tens of milliseconds', 'millions of square feet', 'the thirteenth run', 'the nineteenth shift',
                  'a quadruple win', '[#289](CHANGELOG.md)', f'[#289]({docsite.REPO}/issues/71)', '[#289](https://example.com/issues/289)'):
        assert docsite.typed_numbers(f'Intro.\n\n- {typed}\n'), typed
    for fine in ('p50 and p95 of the offline run', f'see [#104]({docsite.REPO}/issues/104)', f'[#99]({docsite.REPO}/pull/99)',
                 'MSF (thousand square feet)', 'a review scores every criterion', '{{evals.runs}} runs',
                 'one issue, one session, one pull request', '[the rubric](../evals/README.md#v2-rubric)', '1. a list item'):
        assert docsite.typed_numbers(fine) == [], fine
    root = figure_root(tmp_path)
    page = root / 'docs' / 'CASE_STUDY.md'
    page.write_text(page.read_text(encoding='utf-8') + '\nThe golden set holds 289 cases.\n', encoding='utf-8')
    with pytest.raises(ValueError, match=r"docs/CASE_STUDY.md reads every figure .*'line \d+: The golden set holds 289 cases\.'"):
        docsite.build_site(root)


def test_an_unknown_figure_fails_the_build(tmp_path):
    with pytest.raises(KeyError, match='evals.nope'):
        docsite.expand_figures('{{ evals.nope }}', docsite.figures(ROOT), 'docs/CASE_STUDY.md')
    root = figure_root(tmp_path)
    page = root / 'docs' / 'CASE_STUDY.md'
    page.write_text(page.read_text(encoding='utf-8') + '\n{{latency.p99}}\n', encoding='utf-8')
    with pytest.raises(KeyError, match='latency.p99'):
        docsite.build_site(root)


def test_the_figures_refuse_last_run_and_history_from_different_runs(tmp_path):
    """`evals/run.py --track` writes the two files together; a pair that disagrees is a stale figure, by its
    time, its commit or its dataset (Codex on #120: two runs in one minute at one commit, data regenerated)."""
    root = figure_root(tmp_path)
    history, path = root / 'evals' / 'history.jsonl', root / 'evals' / 'last_run.json'
    lines, kept = history.read_text(encoding='utf-8').splitlines(), path.read_text(encoding='utf-8')
    newest, last = json.loads(lines[-1]), json.loads(kept)
    assert [newest[k] for k in ('ran_at', 'commit', 'dataset')] == [last[k] for k in ('ran_at', 'commit', 'dataset')]
    for key, other in (('commit', 'deadbee'), ('ran_at', '2026-01-01T00:00Z'), ('dataset', '000000000000')):
        history.write_text('\n'.join(lines[:-1] + [json.dumps({**newest, key: other})]) + '\n', encoding='utf-8')
        with pytest.raises(ValueError, match='different runs'):
            docsite.figures(root)
    history.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    docsite.figures(root)
    # The coordinator's amended verdict on #120, item 1: the same minute and the same commit on both sides, the
    # dataset regenerated in between, from either file's side.
    path.write_text(json.dumps({**last, 'dataset': '000000000000'}), encoding='utf-8')
    with pytest.raises(ValueError, match='different runs'):
        docsite.figures(root)
    path.write_text(json.dumps({key: value for key, value in last.items() if key != 'dataset'}), encoding='utf-8')
    with pytest.raises(ValueError, match='different runs'):
        docsite.figures(root)


def test_the_score_table_leaves_skips_and_known_defects_unscored(tmp_path):
    """Codex on #120 and the amended verdict's item 2: the runner and history_line score neither a skip nor a known
    defect (xfail), so neither is in a denominator here; each is named as the runner names it."""
    root = figure_root(tmp_path)
    path = root / 'evals' / 'last_run.json'
    last = json.loads(path.read_text(encoding='utf-8'))
    offline = [r for r in last['rows'] if not r['provider']]
    marked = {offline[0]['id']: 'xfail', offline[1]['id']: 'skip', offline[2]['id']: 'fail'}
    last['rows'] = [{**r, 'outcome': marked.get(r['id'], r['outcome'])} for r in last['rows']]
    path.write_text(json.dumps(last), encoding='utf-8')
    values = docsite.figures(root)
    scored = len(offline) - 2
    assert f'| **{scored - 1}/{scored} · 1 skipped · 1 known defect** |' in values['evals.table'].splitlines()[2]
    rows = last['rows']
    assert values['evals.passed'] == f'{sum(r["outcome"] == "pass" for r in rows):,}'
    assert values['evals.scored'] == f'{sum(r["outcome"] in ("pass", "fail") for r in rows):,}', \
        'the headline is passed over scored runs; the unscored ones are said beside it'
    assert values['evals.runs'] == f'{len(rows):,}'
    # The xfail's own type: its known defect is out of the cell's denominator, as in run.py's per-type summary.
    kind = offline[0]['type']
    of_kind = [r for r in rows if not r['provider'] and r['type'] == kind]
    ran = [r for r in of_kind if r['outcome'] in ('pass', 'fail')]
    cell = values['evals.table'].splitlines()[2].split(' | ')[1 + sorted(
        {r['type'] for r in rows}, key=lambda t: (-sum(r['type'] == t for r in rows), t)).index(kind)]
    assert cell.startswith(f'{sum(r["outcome"] == "pass" for r in ran)}/{len(ran)}') and '1 known defect' in cell, cell
    unscored = len(rows) - sum(r['outcome'] in ('pass', 'fail') for r in rows)
    assert values['evals.skipped'].startswith(f' The other {unscored} runs are not scored: ')
    assert '1 known defect, left out of the score as the runner leaves them' in values['evals.skipped']
    page = text_of(docsite.build_site(root)['/docs/case-study'].decode())
    assert f'The other {unscored} runs are not scored' in page and '1 known defect' in page
    assert '<a href="/docs/golden-set#known-defects">known defects</a>' in docsite.build_site(root)['/docs/case-study'].decode()


def test_the_model_comparison_grows_a_row_per_model(tmp_path):
    """The bake-off (#115) writes a history line per model; each model's newest line becomes a row, with its
    first-words time once a live run records one (#71)."""
    root = figure_root(tmp_path)
    history = root / 'evals' / 'history.jsonl'
    lines = history.read_text(encoding='utf-8').splitlines()
    newest = json.loads(lines[-1])
    other = {**newest, 'live': {'openai': {'model': 'gpt-small', 'recorded_cost_usd': 0.03,
                                           'types': {'routing': {'pass': 2, 'fail': 1, 'skip': 0}}}},
             'live_ms': {'p50': 2400.0, 'p95': 4100.0, 'first_token_p50': 812.4, 'model_calls_max': 2}}
    history.write_text('\n'.join(lines[:-1] + [json.dumps(other), lines[-1]]) + '\n', encoding='utf-8')
    values = docsite.figures(root)
    table = values['models.table'].splitlines()
    assert values['models.count'] == str(1 + len(newest['live'])) and len(table) == 3 + len(newest['live'])
    # The anthropic row, read from the files: a re-recording (#71) changes the data, not this test. Its cost divides
    # by the newest run's live rows whose recording exists, the rows history_line summed the cost over.
    anthropic = newest['live']['anthropic']
    last = json.loads((root / 'evals' / 'last_run.json').read_text(encoding='utf-8'))
    counts = anthropic['types'].values()
    ran, unscored = sum(c['pass'] + c['fail'] for c in counts), sum(c['skip'] for c in counts)
    passed = ' · '.join(([f'{sum(c["pass"] for c in counts)}/{ran}'] if ran or not unscored else [])
                        + ([f'{unscored} skipped'] if unscored else []))
    recorded = len(recorded_live_cases(root, last))
    first = (newest.get('live_ms') or {}).get('first_token_p50')
    assert table[2] == (f'| anthropic | `{anthropic["model"]}` | {passed} | '
                        f'${anthropic["recorded_cost_usd"] / recorded:.4f} | '
                        f'{f"{first:.0f} ms" if first is not None else "not recorded yet"} | {newest["ran_at"][:10]} |')
    assert values['live.cost_per_case'] == f'${anthropic["recorded_cost_usd"] / recorded:.4f}'
    # A case whose recording is gone is not a recorded case.
    gone = recorded_live_cases(root, last)[0]
    (root / 'evals' / 'fixtures' / gone['provider'] / f'{gone["case"]}.json').unlink()
    assert docsite.figures(root)['live.cost_per_case'] == f'${anthropic["recorded_cost_usd"] / (recorded - 1):.4f}'
    assert table[3].startswith('| openai | `gpt-small` | 2/3 | $0.0100 | 812 ms |'), \
        'an older line has no rows here: its cost divides by its live rows, each recorded when it was written'
    skipped = {**other, 'live': {'openai': {**other['live']['openai'],
                                            'types': {'routing': {'pass': 2, 'fail': 1, 'skip': 1}}}}}
    history.write_text('\n'.join(lines[:-1] + [json.dumps(skipped), lines[-1]]) + '\n', encoding='utf-8')
    assert '| openai | `gpt-small` | 2/3 · 1 skipped | $0.0075 |' in docsite.figures(root)['models.table']
    rendered = docsite.Renderer().render(values['models.table'])
    assert rendered.count('<tr>') == 3 and '<td class="right">812 ms</td>' in rendered


def test_the_case_study_links_every_claim_and_names_no_plant_vendor():
    text = CASE_STUDY.read_text(encoding='utf-8')
    claims = [line for line in text.splitlines() if line.startswith('- ')]
    assert len(claims) >= 15 and all('](' in line for line in claims), [line for line in claims if '](' not in line]
    results = text[text.index('## Results'):text.index('## Trade-offs')]
    rows = [line for line in results.splitlines() if line.startswith('| ') and not line.startswith(('| Measure', '| ---'))]
    assert rows and all('](' in line.rsplit('|', 2)[-2] for line in rows), 'every result names its source'
    paragraphs = [block for block in re.split(r'\n\s*\n', text) if block and block[0].isalpha()]
    assert all('](' in block for block in paragraphs if '{{' in block or 'never' in block), 'a paragraph with a claim links it'
    vendors = re.compile(r'\b(BHS|Fosber|Marquip|Agnati|Mitsubishi|Isowa|Kiwiplan|Amtech|Valmet|Peters|TCY|Kolbus|Simon|'
                         r'Langston|EFI|Vanguard|P04[A-Z])\b')
    assert not vendors.search(text), vendors.findall(text)


def test_the_docs_print_as_the_document_alone():
    """A print or PDF version: the stylesheet's print rules drop the header, the table of contents and the
    pager, and force the light palette (tests/docs_layout_check.cjs prints the page in Chromium)."""
    css = (WEB / 'style.css').read_text(encoding='utf-8')
    block = css[css.index('@media print{'):]
    block = block[:block.index('\n}\n')]
    assert '.docs-body header.app,.docs-body .docs-toc,.docs-body .docs-pager{display:none!important}' in block
    assert '.docs-body{color-scheme:light;--bg:#fff;' in block
    rules = re.findall(r'(?:^|\})\s*([^{}@]+)\{', block.split('{', 1)[1])
    assert rules and all(sel.strip().startswith(('.docs-body', '.docs-page', '.docs-source')) for r in rules for sel in r.split(',')), \
        'scoped to docs pages: printing the app or /evals is unchanged'
    assert '<body class="docs-body">' in docsite.TEMPLATE
    assert '.docs-page pre code.language-diagram{white-space:pre;' in css, 'the diagram never wraps'


def test_a_figures_page_that_cannot_be_built_never_stops_the_workbench(tmp_path, monkeypatch):
    """The image build is strict; a checkout whose last_run.json and history.jsonl disagree (an interrupted
    --track, a merge of two tracked branches) still starts the server, and the page says why it is missing."""
    root = figure_root(tmp_path)
    history = root / 'evals' / 'history.jsonl'
    lines = history.read_text(encoding='utf-8').splitlines()
    history.write_text('\n'.join(lines[:-1] + [json.dumps({**json.loads(lines[-1]), 'commit': 'deadbee'})]) + '\n',
                       encoding='utf-8')
    with pytest.raises(ValueError, match='different runs'):
        docsite.build_site(root)
    with pytest.raises(ValueError):
        docsite.main(['build', str(tmp_path / 'out'), '--root', str(root)])
    lenient = docsite.build_site(root, strict=False)
    assert set(lenient) == {p['url'] for p in docsite.pages()}
    page = text_of(lenient['/docs/case-study'].decode())
    assert 'could not be built here' in page and 'different runs' in page
    monkeypatch.delenv('INVESTIGATOR_DOCS', raising=False)
    monkeypatch.setattr(docsite, 'ROOT', root)
    server = make_server(0)
    try:
        assert b'could not be built here' in server.app.docs['/docs/case-study']
    finally:
        server.server_close()
        server.app.close()


def test_a_missing_figure_source_leaves_the_rest_of_the_docs_up(tmp_path, monkeypatch):
    """Codex on #120: a checkout with every page but no evals/history.jsonl serves every page; only the case
    study says it could not be built. A missing page source still means no site (a wheel without the repo)."""
    root = figure_root(tmp_path)
    (root / 'evals' / 'history.jsonl').unlink()
    monkeypatch.delenv('INVESTIGATOR_DOCS', raising=False)
    monkeypatch.setattr(docsite, 'ROOT', root)
    site = docsite.load_site()
    assert site is not None and set(site) == {p['url'] for p in docsite.pages()}
    assert b'could not be built here' in site['/docs/case-study'] and b'history.jsonl' in site['/docs/case-study']
    (root / 'docs' / 'URLS.md').unlink()
    assert docsite.load_site() is None
