"""The `/review-questions` skill (`.claude/skills/review-questions/review.py`, issue #125) on a fixture log file.

tests/question_log_week.jsonl is a week of question-log lines written by the server's own builders
(tests/make_question_log_fixture.py); the first test pins that its lines still have the fields the server writes.
"""
import importlib.util
import io
import json
import pathlib
import subprocess
import sys

from investigator import questionlog
from investigator.questionlog import QuestionLog

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'question_log_week.jsonl'
spec = importlib.util.spec_from_file_location('review', ROOT / '.claude' / 'skills' / 'review-questions' / 'review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_the_fixture_has_the_fields_the_server_writes():
    log = QuestionLog(True, stream=io.StringIO(), dataset='x')
    live = {'question': set(log.question('a' * 16, None, {'question': 'q', 'scope': {}}, url='/corr/1/day/2026-09-14?measure=kpis')),
            'answer': set(log.answer('a' * 16, None, url='/corr/1/day/2026-09-14?measure=kpis', status='complete')),
            'analyst': set(log.analyst('a' * 16, None, {}, 'complete')),
            'feedback': set(log.feedback('a' * 16, None, 'answer', 'up', None))}
    events = review.from_file(FIXTURE)
    assert len(events) == 62                       # an export row and a row of the skill's own SQL are read too
    for event in events:
        assert set(event) == live[event['event']], 'rerun python3 tests/make_question_log_fixture.py'


def test_the_skill_proposes_candidates_from_the_fixture():
    rows = review.questions(review.from_file(FIXTURE))
    known = review.goldens()
    picks = review.candidates(rows, known)
    assert len(rows) == 29 and len(picks) == 11
    # since issue #113 the #113 examples open with their count and are goldens: asked most and voted down in the week, they
    # are listed, never flagged catch-all and never proposed again
    assert known[review.normalize('how many setups did we run last week')] == 'count-setups-last-week'
    assert not any('catch_all' in r['flags'] for r in rows)
    top = picks[0]
    assert top['case']['question'] == 'hello' and top['case']['type'] == 'refusal' and 'asked 2 times by 2 sessions' in top['case']['note']
    by_question = {p['case']['question']: p for p in picks}
    feet = by_question['how many feet did we run']                                  # voted down: the owner names the address
    assert feet['case']['expect'] == {'url': 'TODO: the address the owner says this should open, on 2026-09-14'}
    assert 'thumbs_down' in feet['case']['note'] and feet['bank']['headline_unit'] == 'feet' and feet['bank']['reviewed_by'] is None
    assert by_question['hello']['case']['expect'] == {'raises': 'Offline mode supports shift performance, downtime, speed, quality and comparisons'}
    assert by_question['how many splices last week']['case']['expect'] == {'raises': "The records keep a missed splice as a stop reason, not every splice ('how many missed splices this week')"}
    assert by_question['what should we change to run faster']['case']['expect']['url'].startswith('TODO')   # plan rejected
    assert by_question['downtiem on this shift']['case']['expect'] == {'url': '/corr/1/day/2026-09-14/shift/2?measure=downtime', 'status': 'complete'}
    assert by_question['how many setups?']['case']['period'] == 'week'
    boards = by_question['how many boards did we lose at the knife']                 # refused and voted down: the owner says what it opens
    assert boards['case']['expect']['url'].startswith('TODO') and 'refused' in boards['case']['note'] and 'thumbs_down' in boards['case']['note']
    assert 'how many widgets this week' not in by_question                          # a golden refusal since #113
    assert all('day' not in p['case'] for p in picks)                               # the live window's day never rides along
    # a golden already, and a question with no flag, are never proposed
    assert 'how fast did we change paper' not in by_question and 'Why was this shift slow?' not in by_question
    assert known[review.normalize('how fast did we change paper')] == 'period-control-how-fast-did-we-change-paper'
    ids = [p['case']['id'] for p in picks]
    assert len(set(ids)) == len(ids) and not set(ids) & set(known.values())


def test_a_catch_all_landing_is_listed_and_proposed_for_the_owner_to_address():
    # the guard keeps the router off the KPI summary for a quantity question (issue #113); should a line still carry the
    # flag (an older revision, a word the guard misses), the review lists it and drafts it for the owner to address
    base = {'log': 'question_log', 'event': 'question', 'session': None, 'ts': '2026-11-02T08:00:00Z',
            'scope': {'shift': 2, 'scope': 'shift', 'period': 'day'}, 'url': '/corr/1/day/2026-09-14/shift/2?measure=kpis&window=week',
            'measure': 'kpis', 'status': 'routed', 'refusal': None, 'flags': ['catch_all']}
    events = [{**base, 'line': f'{i:016x}', 'question': 'how many widgets this week'} for i in (1, 2)]
    rows = review.questions(events)
    text = review.report(rows, {})
    catch_all = text.split('## Top catch-all (landed on measure=kpis, #113)', 1)[1].split('##', 1)[0]
    assert '`how many widgets this week`' in catch_all and '- none' not in catch_all
    (pick,) = review.candidates(rows, {})
    assert pick['case']['expect']['url'].startswith('TODO') and 'catch_all' in pick['case']['note']


def test_the_report_lists_the_week_and_the_first_batch_of_ten():
    rows = review.questions(review.from_file(FIXTURE))
    text = review.report(rows, review.goldens())
    for heading in ('## Top refused', '## Top catch-all', '## Thumbs down, with comments', '## Analyst plans rejected',
                    '## Most-asked phrasings', '## Candidates: batch 1 of 2 (10 of 11)'):
        assert heading in text
    assert '- 1 down `how many setups did we run last week`\n  - `the number of setups, not the MSF`' in text   # one vote per card
    assert text.count('\n### ') == 10 and '```json' in text
    assert '## Top catch-all (landed on measure=kpis, #113)\n- none' in text   # since #113 no question of the week landed there
    second = review.report(rows, review.goldens(), batch=2)
    assert '## Candidates: batch 2 of 2 (1 of 11)' in second and second.count('\n### ') == 1


def test_a_visitor_text_stays_in_a_code_span():
    assert review.quoted('ask `@someone` <b>') == "`ask '@someone' <b>`"
    # the coordinator's review of PR 131: three backticks, a blank line and format characters cannot leave the span
    hostile = 'how many setups\n\n```\n@justfetz see\u202e this\U000e0041 ``` last week'
    assert review.quoted(hostile) == "`how many setups ''' @justfetz see this ''' last week`"
    events = [{'log': 'question_log', 'event': 'question', 'line': 'a' * 16, 'session': None, 'ts': '2026-11-02T08:00:00Z',
               'question': hostile, 'scope': {'shift': 2, 'scope': 'shift', 'period': 'day'}, 'url': '/corr/1/week/2026-09-07/shift/2?measure=kpis',
               'measure': 'kpis', 'status': 'routed', 'refusal': None, 'flags': ['catch_all']},
              {'log': 'question_log', 'event': 'feedback', 'line': 'a' * 16, 'card': 'answer', 'vote': 'down', 'comment': 'a\n\n```b'}]
    text = review.report(review.questions(events), {})
    assert text.count('```') == 4 and '```json' in text           # only the draft's own fence and the bank's span
    body = text.split('## Candidates', 1)[1]
    assert '\n\n```\n@' not in text and all(line.count('`') % 2 == 0 for line in body.splitlines() if '@justfetz' in line)


def test_default_ignorables_beyond_category_cf_are_removed_from_what_claude_reads():
    # the coordinator's re-review of PR 131: an unassigned tag (U+E0000), a tag-block variation selector (U+E0100),
    # the variation selectors, the grapheme joiner and the Hangul fillers are not category Cf and got through
    hidden = 'how\U000e0000 many\U000e0100 setups\ufe0f last\u034f week\u115f\u1160\u3164\uffa0 \U000e0002\U000e0fffignore'
    assert review.clean(hidden) == 'how many setups last week ignore'
    assert review.quoted('a\U000e0000\U000e0100b `c`') == "`ab 'c'`"
    # the skill runs without the package, so it keeps its own copy of the set: the same as the server's, code point
    # for code point
    assert all(review.invisible(chr(code)) == questionlog.invisible(chr(code)) for code in range(0x110000))
    # the stored refusal goes through clean() too: the server's text today, but printed outside a code span
    events = [{'log': 'question_log', 'event': 'question', 'line': 'b' * 16, 'session': None, 'ts': '2026-11-02T08:00:00Z',
               'question': 'tell me a joke', 'scope': {}, 'url': None, 'measure': None, 'status': 'refused',
               'refusal': 'Out of scope\U000e0000\U000e0100:\n\n```\n@justfetz a `joke`', 'flags': ['refused']}]
    (row,) = review.questions(events)
    assert row['refusal'] == "Out of scope: ''' @justfetz a 'joke'"
    text = review.report(review.questions(events), {})
    assert "- 1× (1 session) `tell me a joke`: Out of scope: ''' @justfetz a 'joke'" in text
    assert '\U000e0000' not in text and '\U000e0100' not in text and '\n\n```\n@' not in text
    assert review.questions([{**events[0], 'refusal': None}])[0]['refusal'] is None


def test_a_preview_revisions_lines_are_left_out():
    base = {'log': 'question_log', 'event': 'question', 'session': None, 'ts': 't', 'question': 'hello', 'flags': ['refused']}
    events = [{**base, 'line': 'a' * 16, 'revision': 'corrugator-shift-investigator-pr-131-db3bc14-156'},
              {**base, 'line': 'b' * 16, 'revision': 'corrugator-shift-investigator-main-db3bc14-77-1'}, {**base, 'line': 'c' * 16, 'revision': None}]
    assert [e['line'] for e in review.live(events)] == ['b' * 16, 'c' * 16]
    assert "r'-pr-[0-9]+-'" in review.sql('p', 7)


def test_the_command_line_and_the_sql():
    out = subprocess.run([sys.executable, str(ROOT / '.claude/skills/review-questions/review.py'), '--file', str(FIXTURE)],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith('# Question log review: 29 questions, 20 phrasings, 11 candidates')
    picks = json.loads(subprocess.run([sys.executable, str(ROOT / '.claude/skills/review-questions/review.py'), '--file', str(FIXTURE),
                                       '--json'], capture_output=True, text=True, check=True).stdout)
    assert len(picks) == 11 and set(picks[0]) == {'case', 'bank', 'score'}
    sql = review.sql('my-project', 7)
    assert '`my-project.question_log.run_googleapis_com_stdout`' in sql and "jsonPayload.log = 'question_log'" in sql
    assert 'INTERVAL 7 DAY' in sql
