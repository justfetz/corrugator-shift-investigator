"""The in-app docs at /docs (issue #93): the table of contents, the rendered pages, the routes and the build.

Two acceptance tests of the issue live here: every docs/*.md listed in the table of contents exists (and
every docs/*.md is either listed or named as left out), and no user-facing HTML outside the provider docs
mentions an OpenAI API key. The rest pins the renderer's escaping, the links between pages, the old
/guide address, the image build and the changelog.
"""
import html
import http.client
import re
import shutil
import subprocess
from pathlib import Path
from threading import Thread

import pytest

from investigator import docsite
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
    assert len(slugs) == len(set(slugs)) and slugs[0] == '', 'unique slugs; the first page is /docs itself'
    assert [s for s, _ in docsite.SECTIONS] == ['Overview', 'Asking questions', 'The analyst', 'Numbers and evals',
                                                'Cost model', 'Data', 'Deployment', 'Changelog'], 'the sections of issue #93'
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
