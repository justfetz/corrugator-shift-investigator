"""/docs/sql (issue #190): how a question becomes SQL, and how the SQL is tested, every block rendered by the real code.

The page's source types no SQL, plan, bound value, row or number; `sqlpage` fills each block at docs build from the router,
the compiler, the record store and the test files. These tests hold the built page to the compiler's own SQL, computed here
a second way, and hold the build to failing when a block cannot be rendered or the page would say what the code does not do.
"""
import html
import json
import re
import shutil
import types
from pathlib import Path

import pytest

from investigator import docsite, semantic, sqlpage
from investigator.agent import Context, route
from investigator.examples import SCORE_DAY, SCORE_SCOPE, SCORE_SHIFT
from investigator.record_store import default_store

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs' / 'SQL.md'


@pytest.fixture(scope='module')
def store():
    return default_store()


@pytest.fixture(scope='module')
def page(store):
    return docsite.build_site(ROOT)['/docs/sql'].decode()


def text_of(markup):
    return html.unescape(re.sub(r'<[^>]+>', '', markup))


def code_blocks(markup, lang):
    return [html.unescape(b) for b in re.findall(rf'<pre><code class="language-{lang}">(.*?)</code></pre>', markup, re.S)]


def repo_copy(tmp_path):
    """What the docs build reads, to break one file at a time."""
    for source in [p['source'] for p in docsite.pages()] + list(docsite.FIGURE_SOURCES) + list(docsite.RENDERED_SOURCES):
        (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / source, tmp_path / source)
    shutil.copytree(ROOT / 'evals' / 'fixtures', tmp_path / 'evals' / 'fixtures')
    return tmp_path


def test_the_page_sits_beside_the_semantic_model_and_is_linked(store):
    slugs = [p['slug'] for p in docsite.pages()]
    assert slugs.index('sql') == slugs.index('semantic-model') + 1
    assert next(p for p in docsite.pages() if p['slug'] == 'sql')['section'] == 'Numbers and evals'
    site = docsite.build_site(ROOT)
    for url in ('/docs/asking-questions', '/docs/numbers-and-evals', '/docs/semantic-model'):
        assert 'href="/docs/sql' in site[url].decode(), f'{url} links to the page'


def test_the_built_page_holds_the_compilers_sql(page, store):
    """Computed a second way: the router's plan for the worked question, validated and compiled here; each statement, its
    bound values and the rows semantic.run returns are on the page as they are."""
    request, _ = route(sqlpage.QUESTION, Context(SCORE_DAY, SCORE_SHIFT, scope=SCORE_SCOPE, period='day'), store.production_days, store)
    plan = request['plan'][0]['arguments']
    assert request['plan'][0]['tool'] == semantic.TOOL and plan['measure'] == 'downtime_minutes' and plan['group_by'] == ['reason']
    assert plan['filters'] == {'kind': 'Maintenance'} and plan['order'] == 'value' and plan['window']['period'] == 'week'
    sql = code_blocks(page, 'sql')
    result = semantic.run(plan, store)
    statements = result['trace']['sql'].split(';\n')
    assert len(statements) > 1
    for statement in statements:
        assert statement in sql, 'each statement the compiler wrote is a SQL block of its own'
    compiled = semantic.compile_query(semantic.validate(plan, store), bounds=(store._first, store._last))
    assert [s.sql for s in compiled.statements] == statements
    assert result['trace']['sql'] in sql, 'and the query box\'s joined text'
    assert str(result['trace']['parameters']).replace("'", '"') in code_blocks(page, 'json'), 'one list of bound values per statement'
    text = text_of(page)
    for row in result['rows']:
        assert row['label'] in text and repr(row['value']) in text and repr(row['share_pct']) in text
        assert row['source_ids'][0] in text
    assert repr(result['value']) in text and 'source_count' in text and '/data/stops?' in page
    assert not docsite.FIGURE.search(html.unescape(page)), 'every block is filled at build'


def test_the_oracle_beside_it_is_the_tests_own(page):
    blocks = code_blocks(page, 'python')
    source = (ROOT / 'tests' / 'test_examples.py').read_text(encoding='utf-8')
    oracle = re.search(r'^def top_maintenance_downtimes\(.*?(?=^\S)', source, re.M | re.S).group(0).rstrip()
    assert oracle in blocks, 'the hand-written oracle, as the test file holds it'
    value = (ROOT / 'evals' / 'make_value_goldens.py').read_text(encoding='utf-8')
    function = re.search(rf'^def {sqlpage.VALUE_FUNCTION}\(.*?(?=^\S)', value, re.M | re.S).group(0).rstrip()
    assert function in blocks, 'the value golden\'s plain-Python computation, as the script holds it'


def test_the_fixed_tool_is_shown_the_same_way(page, store):
    data = store.get_downtime_pareto({'period': 'week', 'anchor': SCORE_DAY}, 'reason', 'minutes', None)
    assert data['trace']['sql'].split(';\n')[0] in code_blocks(page, 'sql')
    text = text_of(page)
    for row in data['rows']:
        assert row['category'] in text
    case = next(c for c in json.loads((ROOT / 'evals' / 'golden.json').read_text(encoding='utf-8'))['cases'] if c['id'] == sqlpage.FIXED_CASE)
    assert html.escape(case['expect']['url']) in page and case['question'] in text


def test_the_source_types_no_block_and_no_number():
    text = SOURCE.read_text(encoding='utf-8')
    assert sqlpage.check_source(text) == [], 'the committed page types none of what it renders'
    names = set(docsite.FIGURE.findall(text))
    assert names and names <= set(sqlpage.figures(ROOT)), names - set(sqlpage.figures(ROOT))
    for typed in ('```sql\nSELECT 1\n```', 'The week holds 70.43 minutes.', 'the top five reasons', 'it runs twice'):
        assert sqlpage.check_source(f'Intro.\n\n{typed}\n'), typed
    for fine in ('`python3 evals/run.py` runs it', 'one question, one plan', '{{plan}}', '[#172](https://github.com/justfetz/corrugator-shift-investigator/issues/172)'):
        assert sqlpage.check_source(fine) == [], fine


def test_a_block_typed_into_the_source_fails_the_build(tmp_path):
    root = repo_copy(tmp_path)
    page = root / 'docs' / 'SQL.md'
    page.write_text(page.read_text(encoding='utf-8') + '\n```sql\nSELECT reason FROM stops LIMIT 5\n```\n', encoding='utf-8')
    with pytest.raises(sqlpage.PageError, match='a code block typed into the page'):
        docsite.build_site(root)


def test_an_unknown_block_fails_the_build(tmp_path):
    root = repo_copy(tmp_path)
    page = root / 'docs' / 'SQL.md'
    page.write_text(page.read_text(encoding='utf-8') + '\n{{sql.nope}}\n', encoding='utf-8')
    with pytest.raises(KeyError, match='sql.nope'):
        docsite.build_site(root)


@pytest.mark.parametrize('broken, message', [
    ('reading', 'not as the verified example'),
    ('oracle', 'has no function top_maintenance_downtimes'),
    ('value', 'holds no case'),
])
def test_a_block_that_cannot_be_rendered_fails_the_build_and_never_the_workbench(tmp_path, monkeypatch, broken, message):
    """The image build is strict; the workbench says why the page is missing and serves every other page as it was."""
    root = repo_copy(tmp_path)
    whole = docsite.build_site(root)
    if broken == 'reading':   # a question the router reads into another plan than the page's example
        monkeypatch.setattr(sqlpage, 'QUESTION', 'top 5 operator downtime reasons this week')
    elif broken == 'oracle':
        oracles = root / 'tests' / 'test_examples.py'
        oracles.write_text(oracles.read_text(encoding='utf-8').replace('def top_maintenance_downtimes(', 'def renamed_oracle('),
                           encoding='utf-8')
    else:
        monkeypatch.setattr(sqlpage, 'VALUE_CASE', 'value-no-such-case')
    with pytest.raises(sqlpage.PageError, match=message):
        docsite.build_site(root)
    lenient = docsite.build_site(root, strict=False)
    notice = text_of(lenient['/docs/sql'].decode())
    assert 'could not be built here' in notice and message in notice
    assert {u: p for u, p in lenient.items() if u != '/docs/sql'} == {u: p for u, p in whole.items() if u != '/docs/sql'}


def test_the_sql_the_answer_ran_must_be_the_compilers(tmp_path, monkeypatch):
    """The page says the answer ran the statements compile_query returns for its plan: an answer that ran other SQL (run
    compiling with other options than the page's, say) fails the build."""
    real = semantic.run

    def other(*args, **kwargs):
        result = real(*args, **kwargs)
        result['trace']['sql'] = result['trace']['sql'].replace('SELECT *', 'SELECT  *', 1)
        return result
    monkeypatch.setattr(semantic, 'run', other)
    with pytest.raises(sqlpage.PageError, match='not the statements compile_query returns'):
        sqlpage.figures(ROOT)


def test_a_given_store_is_read_only_when_it_holds_the_golden_window(store):
    first, last = sqlpage.golden_window(ROOT)
    assert sqlpage.usable(store, ROOT) and store.window == (first, last)
    assert not sqlpage.usable(None, ROOT)
    assert not sqlpage.usable(types.SimpleNamespace(window=(first, last.replace(day=last.day - 1))), ROOT), 'ends before it'
    assert not sqlpage.usable(types.SimpleNamespace(window=(first.replace(day=first.day + 1), last)), ROOT), 'starts after it'
    assert sqlpage.usable(types.SimpleNamespace(window=(first, last.replace(year=last.year + 1))), ROOT), 'a later rolling window'
    assert sqlpage.golden_store(ROOT) is store, 'pinned to the golden window, the process\'s own store'


def test_the_image_ships_the_oracle_and_reads_the_golden_prebuilt():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'COPY tests/test_examples.py ./tests/' in dockerfile
    assert 'INVESTIGATOR_GOLDEN_PREBUILT=/app/prebuilt-golden python -m investigator.docsite build /app/docs-site --root /app' in dockerfile
    assert dockerfile.index('/app/prebuilt-golden \\') < dockerfile.index('investigator.docsite build'), 'built before the docs read it'
    for name in ('.dockerignore', '.gcloudignore'):
        lines = [line.strip() for line in (ROOT / name).read_text(encoding='utf-8').splitlines() if line.strip() and not line.startswith('#')]
        assert 'tests' not in lines and 'tests/*' in lines and '!tests/test_examples.py' in lines, name
        assert lines.index('!tests/test_examples.py') > lines.index('tests/*'), f'{name}: the exception follows the rule'
    for source in docsite.RENDERED_SOURCES:
        assert (ROOT / source).is_file(), source
        assert source.startswith(('evals/', 'tests/test_examples.py')), f'{source}: the image copies evals/ and the oracle file only'
