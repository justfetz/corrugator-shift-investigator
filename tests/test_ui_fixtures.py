"""The UI test's why-slow answer (tests/ui_fixtures/why_slow.json, issue #65) is the offline result of its golden
URL. If the generator or the planner changes, this fails and names the command that refreshes the fixture."""
import json
from pathlib import Path

from investigator.server import Workbench

FIXTURE = Path(__file__).parent / 'ui_fixtures' / 'why_slow.json'
REFRESH = ("python3 -c \"import json;from investigator.server import Workbench;"
           "r,_=Workbench().answer_url('/corr/1/day/2026-09-14/shift/2?measure=overview');r.pop('report_markdown',None);"
           "json.dump(r,open('tests/ui_fixtures/why_slow.json','w'),separators=(',',':'),sort_keys=True)\"")


def test_why_slow_ui_fixture_is_the_offline_answer_of_its_url():
    saved = json.loads(FIXTURE.read_text())
    golden = next(c for c in json.loads((Path(__file__).parents[1] / 'evals' / 'golden.json').read_text())['cases'] if c['id'] == 'why-slow')
    assert saved['url'] == golden['expect']['url']
    result, _ = Workbench().answer_url(saved['url'])
    fresh = json.loads(json.dumps(result))
    for key in ('sections', 'status', 'url', 'selected_shifts', 'production_day'):
        assert saved[key] == fresh[key], f'{key} drifted; refresh with: {REFRESH}'
    assert [e['tool'] for e in saved['trace']] == [e['tool'] for e in fresh['trace']], REFRESH
    assert [c.get('title') for c in saved['charts']] == [c.get('title') for c in fresh['charts']], REFRESH
