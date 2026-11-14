"""Issue #137: where the page lands. `/` redirects to the store's last production day, shift 1, Day window, so the
URL is the state from the first response, and the goldens do not move: the eval harness keeps asking a case that
names no day or shift on 2026-09-14, shift 2, and the golden store lands there, pinned beside INVESTIGATOR_AS_OF in
tests/conftest.py rather than in the page. Landing on shift 1 changes no number question's answer (the owner's scope
ruling of 13 November 2026, docs/ASKING.md): a number with no shift named answers all shifts whatever the shift
control says, while the investigations keep the shift. The page's side (the controls, the greyed day under a period)
is in tests/test_ui.cjs."""
import http.client
import importlib.util
import json
import re
from datetime import date, timedelta
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode

import pytest

from investigator.record_store import FISCAL_START, FISCAL_WEEKS, RecordStore, default_store
from investigator.server import LANDING_ENV, Workbench, landing, make_server
from investigator.synth import window_bounds

ROOT = Path(__file__).resolve().parent.parent


def serve(store=None):
    server = make_server(0, host='127.0.0.1', store=store)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(server, thread):
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def get(server, path, accept=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
    try:
        client.request('GET', path, headers={'Accept': accept} if accept else {})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def latest_weekday(day):
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def runner():
    spec = importlib.util.spec_from_file_location('golden_runner_137', ROOT / 'evals' / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_store_whose_window_ends_yesterday_lands_on_yesterday_shift_1(monkeypatch):
    """The hosted demo's store ends yesterday (the nightly deploy moves it): `/` is a 302, never cached, to its last
    production day (the latest Monday to Friday on or before yesterday), shift 1, Day window, the address the page then
    reads; /api/health's last_day and /api/config's landing name the same day."""
    monkeypatch.delenv(LANDING_ENV, raising=False)
    yesterday = date.today() - timedelta(days=1)
    store = RecordStore(window=window_bounds(yesterday, first_day=yesterday - timedelta(days=20)), prebuilt='')
    expected = latest_weekday(yesterday)
    server, thread = serve(store)
    try:
        status, headers, body = get(server, '/')
        assert status == 302 and headers['Cache-Control'] == 'no-store'
        assert headers['Location'] == f'/corr/1/day/{expected}/shift/1' == json.loads(body)['url']
        assert get(server, '/?from=a-bookmark')[1]['Location'] == headers['Location']   # no query is state at /
        health = json.loads(get(server, '/api/health')[2])
        assert health['store']['window']['last_day'] == expected
        config = json.loads(get(server, '/api/config')[2])
        assert config['landing'] == {'day': expected, 'shift': 1, 'url': f'/corr/1/day/{expected}/shift/1'}
        status, _, page = get(server, headers['Location'], 'text/html')
        assert status == 200 and b'Shift Investigator' in page   # the app page, which reads the controls from the address
        status, _, answer = get(server, headers['Location'] + '.json?measure=kpis')
        assert status == 200 and json.loads(answer)['url'] == headers['Location'] + '?measure=kpis'   # an address that answers
    finally:
        stop(server, thread)


def test_the_landing_is_the_last_production_day_and_a_pin_must_be_well_formed():
    """Monday to Friday: a window that ends on a Sunday lands on its Friday. A pin names a production day and a shift;
    one in another form fails at boot, and one this store does not hold lands on the store's own default."""
    weekend = RecordStore(window=(date(2026, 11, 9), date(2026, 11, 15)), prebuilt='')   # Monday to Sunday
    assert landing(weekend, {}) == ('2026-11-13', 1)
    assert landing(weekend, {LANDING_ENV: ' '}) == ('2026-11-13', 1)
    assert landing(weekend, {LANDING_ENV: '2026-11-11/shift/3'}) == ('2026-11-11', 3)
    for outside in ('2026-11-14/shift/2', '2026-09-14/shift/2'):   # a Saturday; a day before the window
        assert landing(weekend, {LANDING_ENV: outside}) == ('2026-11-13', 1)
    for bad in ('2026-11-11', '2026-11-11/shift/4', '/corr/1/day/2026-11-11/shift/1', '11/11/2026/shift/1'):
        with pytest.raises(ValueError, match=LANDING_ENV):
            landing(weekend, {LANDING_ENV: bad})


def test_the_golden_store_lands_where_the_goldens_expect(monkeypatch):
    """The goldens don't move: evals/run.py asks a case that names no day or shift on DEFAULT_DAY, shift 2, against the
    window ending GOLDEN_AS_OF; tests/conftest.py pins INVESTIGATOR_AS_OF to that window and the landing to that day and
    shift, so the golden store's page opens where the goldens are asked. The page holds no date of its own."""
    from conftest import GOLDEN_AS_OF, GOLDEN_LANDING
    golden = runner()
    assert golden.DEFAULT_DAY == '2026-09-14' and golden.GOLDEN_AS_OF == GOLDEN_AS_OF
    assert GOLDEN_LANDING == f'{golden.DEFAULT_DAY}/shift/2'
    # the harness's own default, read from what it sends: a case with no day, shift, scope or period
    sent = []
    monkeypatch.setattr(golden, 'http_get', lambda path: sent.append(path) or (400, {}, b'{"error": "stopped here"}'))
    golden.via_url({'id': 'landing-probe', 'type': 'routing'}, {}, ['How many stops did we have?'])
    assert sent and sent[0].startswith('/q?day=2026-09-14&shift=2&scope=shift&period=day&q='), sent
    # the golden store, with the harness's pin: `/` lands on 2026-09-14, shift 2
    assert default_store().as_of.isoformat() == GOLDEN_AS_OF
    server, thread = serve()
    try:
        status, headers, _ = get(server, '/')
        assert status == 302 and headers['Location'] == '/corr/1/day/2026-09-14/shift/2'
        assert json.loads(get(server, '/api/config')[2])['landing'] == {'day': '2026-09-14', 'shift': 2, 'url': '/corr/1/day/2026-09-14/shift/2'}
    finally:
        stop(server, thread)
    # without the pin the same store lands on its own last production day, shift 1
    monkeypatch.delenv(LANDING_ENV)
    assert Workbench(store=default_store()).landing_url == '/corr/1/day/2026-09-30/shift/1'
    # the default is the harness's, not the page's: no golden day in the page's markup or script
    html = (ROOT / 'src/investigator/web/index.html').read_text()
    script = (ROOT / 'src/investigator/web/app.js').read_text()
    assert '2026-09-14' not in html and "'2026-09-14'" not in script
    assert '<option value="2" selected>' not in html


# Number questions with no shift named (docs/ASKING.md's table), and questions the shift control scopes.
NUMBER_QUESTIONS = ('How many setups did we run last week?', 'How many stops did we have?',
                    'How many minutes of maintenance downtime yesterday?', 'How much downtime this week',
                    'What was our MSF per day this week?', 'Average stop length this week',
                    'What were the top downtime reasons yesterday?', 'Volume by grade this month',
                    'How much paper did we consume this month?', 'Over/under-run by knife this week',
                    'Is it bond or warp?', 'How many square feet of trim this week?', 'Average speed this week')
SHIFT_QUESTIONS = ('Why was this shift slow? Chart downtime by reason.', 'Where did the money go this shift?')


def test_landing_on_shift_1_does_not_change_what_a_number_question_answers():
    """The owner's scope ruling: a number question with no shift named answers all shifts, so the landing's shift 1
    asks it exactly as shift 2 did (no /shift/ in its address); the investigations follow the shift control."""
    bench = Workbench(store=default_store())
    def resolved(question, day, shift):
        return bench.question_url(urlencode([('day', day), ('shift', str(shift)), ('scope', 'shift'), ('period', 'day'), ('q', question)]))
    for day in (default_store().last_day, '2026-09-14'):
        for question in NUMBER_QUESTIONS:
            on_one, on_two = resolved(question, day, 1), resolved(question, day, 2)
            assert on_one == on_two and '/shift/' not in on_one, (day, question, on_one, on_two)
        for question in SHIFT_QUESTIONS:
            assert f'/{day}/shift/1?' in resolved(question, day, 1) and f'/{day}/shift/2?' in resolved(question, day, 2), question


def test_the_day_label_names_4_4_5_periods_on_the_store_calendar():
    """The day field's label under 4-4-5 ("4-4-5 period 2") is counted from the anchor and quarter shape the period tools
    use (record_store.fiscal_period); tests/test_ui.cjs checks the labels at docs/METRICS.md's period boundaries."""
    script = (ROOT / 'src/investigator/web/app.js').read_text()
    match = re.search(r"const FISCAL_START='(\d{4}-\d{2}-\d{2})', FISCAL_WEEKS=\[(\d+),(\d+),(\d+)\];", script)
    assert match, 'app.js names its 4-4-5 anchor and quarter shape once'
    assert match.group(1) == FISCAL_START.isoformat() and tuple(int(w) for w in match.groups()[1:]) == FISCAL_WEEKS
