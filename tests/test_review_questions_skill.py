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
    assert len(events) == 55                       # an export row and a row of the skill's own SQL are read too
    for event in events:
        assert set(event) == live[event['event']], 'rerun python3 tests/make_question_log_fixture.py'


def test_the_skill_proposes_candidates_from_the_fixture():
    rows = review.questions(review.from_file(FIXTURE))
    known = review.goldens()
    picks = review.candidates(rows, known)
    assert len(rows) == 25 and len(picks) == 12
    top = picks[0]
    assert top['case']['question'] == 'how many setups did we run last week'       # the #113 example, asked most, thumbs down
    assert top['case']['expect'] == {'url': 'TODO: the address the owner says this should open, on 2026-09-14'}
    assert 'catch_all' in top['case']['note'] and 'asked 3 times by 3 sessions' in top['case']['note']
    assert top['bank']['headline_unit'] == 'setups' and top['bank']['reviewed_by'] is None
    by_question = {p['case']['question']: p for p in picks}
    assert by_question['hello']['case']['type'] == 'refusal'
    assert by_question['hello']['case']['expect'] == {'raises': 'Offline mode supports shift performance, downtime, speed, quality and comparisons'}
    assert by_question['what should we change to run faster']['case']['expect']['url'].startswith('TODO')   # plan rejected
    assert by_question['downtiem on this shift']['case']['expect'] == {'url': '/corr/1/day/2026-09-14/shift/2?measure=downtime', 'status': 'complete'}
    assert by_question['how many setups?']['case']['period'] == 'week'
    assert all('day' not in p['case'] for p in picks)                               # the live window's day never rides along
    # a golden already, and a question with no flag, are never proposed
    assert 'how fast did we change paper' not in by_question and 'Why was this shift slow?' not in by_question
    assert known[review.normalize('how fast did we change paper')] == 'period-control-how-fast-did-we-change-paper'
    ids = [p['case']['id'] for p in picks]
    assert len(set(ids)) == len(ids) and not set(ids) & set(known.values())


def test_the_report_lists_the_week_and_the_first_batch_of_ten():
    rows = review.questions(review.from_file(FIXTURE))
    text = review.report(rows, review.goldens())
    for heading in ('## Top refused', '## Top catch-all', '## Thumbs down, with comments', '## Analyst plans rejected',
                    '## Most-asked phrasings', '## Candidates: batch 1 of 2 (10 of 12)'):
        assert heading in text
    assert '- 1 down `how many setups did we run last week`\n  - `the number of setups, not the MSF`' in text   # one vote per card
    assert text.count('\n### ') == 10 and '```json' in text
    second = review.report(rows, review.goldens(), batch=2)
    assert '## Candidates: batch 2 of 2 (2 of 12)' in second and second.count('\n### ') == 2


def test_a_visitor_text_stays_in_a_code_span():
    assert review.quoted('ask `@someone` <b>') == "`ask '@someone' <b>`"


def test_the_command_line_and_the_sql():
    out = subprocess.run([sys.executable, str(ROOT / '.claude/skills/review-questions/review.py'), '--file', str(FIXTURE)],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith('# Question log review: 25 questions, 16 phrasings, 12 candidates')
    picks = json.loads(subprocess.run([sys.executable, str(ROOT / '.claude/skills/review-questions/review.py'), '--file', str(FIXTURE),
                                       '--json'], capture_output=True, text=True, check=True).stdout)
    assert len(picks) == 12 and set(picks[0]) == {'case', 'bank', 'score'}
    sql = review.sql('my-project', 7)
    assert '`my-project.question_log.run_googleapis_com_stdout`' in sql and "jsonPayload.log = 'question_log'" in sql
    assert 'INTERVAL 7 DAY' in sql
