"""Writes tests/question_log_week.jsonl: a week of question-log lines (issue #125) for the `/review-questions` skill's test.

The lines come from the server's own builders (Workbench.question_url, QuestionLog), over the golden store, with fixed
ids and timestamps, so the fixture has exactly the fields the hosted demo writes. Rerun it after a change to the log's
schema: `python3 tests/make_question_log_fixture.py` (tests/test_review_questions_skill.py fails until then).
"""
import io
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'evals'))

# (question, period, sessions asking it, feedback per asking: None, 'up', 'down' or ('down', comment), analyst status)
ASKED = [
    ('how many setups did we run last week', 'day', 3, ('down', 'the number of setups, not the MSF'), None),
    ('how many paper changes last week', 'day', 2, None, None),
    ('how many order changes this week', 'day', 1, 'down', None),
    ('what was our lineal per setup last week', 'day', 1, ('down', 'feet per setup for the week'), None),
    ('how many splices last week', 'day', 1, None, None),
    ('hello', 'day', 2, None, None),
    ("what's the weather", 'day', 1, None, None),
    ('how many feet did we run', 'day', 2, ('down', 'lineal feet run on the shift'), None),
    ('what should we change to run faster', 'day', 1, None, 'plan_rejected'),
    ('downtiem on this shift', 'day', 1, 'up', None),
    ('how much downtime on 1st shift last week', 'day', 1, None, None),
    ('how many setups?', 'week', 1, 'down', None),
    ('how fast did we change paper', 'week', 1, None, None),        # a golden already: listed, never proposed again
    ('Why was this shift slow?', 'day', 4, 'up', None),
    ('How did this week go versus the previous one?', 'week', 2, 'up', None),
    ('speed to targte', 'day', 1, None, None),
]


def main():
    from investigator.questionlog import QuestionLog
    from investigator.server import Workbench
    from run import golden_store
    app = Workbench(store=golden_store())
    log = QuestionLog(True, stream=io.StringIO(), dataset=app.store.content_hash[:12], environ={'K_REVISION': 'fixture-00001'})
    log.version = '0.1.0'
    out, n = [], 0
    for question, period, sessions, vote, analyst in ASKED:
        for k in range(sessions):
            n += 1
            line, session = f'{n:016x}', f'{1000 + n % 7 + k:016x}'
            ts = f'2026-11-{2 + n % 5:02d}T{8 + n % 9:02d}:{n % 60:02d}:00.000Z'
            seen, query = {}, urlencode({'day': '2026-09-14', 'shift': '2', 'scope': 'shift', 'period': period, 'q': question})
            events = []
            try:
                url = app.question_url(query, seen)
                events.append(log.question(line, session, seen, url=url, ms=0.4))
                result, _ = app.answer_url(url)
                events.append(log.answer(line, session, url=result['url'], status=result['status'], ms=18.0))
            except ValueError as exc:
                events.append(log.question(line, session, seen, refusal=str(exc), ms=0.2))
            if analyst:
                job = {'question': question, 'day': '2026-09-14', 'shift': 2, 'scope': 'shift', 'period': period,
                       'corrupt': False, 'mode': 'anthropic'}
                events.append(log.analyst(line, session, job, analyst, {'usage': {'model_calls': 1, 'tool_calls': 0},
                                                                        'narrative_status': 'not_requested'}, ms=2100.0))
            if vote and k == 0:
                choice, comment = (vote, None) if isinstance(vote, str) else vote
                events.append(log.feedback(line, session, 'answer', choice, None))
                if comment:
                    events.append(log.feedback(line, session, 'answer', choice, comment))
            for event in events:
                event['ts'] = ts
            out.extend(events)
    rows = [json.dumps(e, ensure_ascii=False, separators=(',', ':')) for e in out]
    # two lines as BigQuery hands them back: an export row, and a row of the skill's own SQL
    rows[0] = json.dumps({'timestamp': out[0]['ts'], 'jsonPayload': out[0]}, ensure_ascii=False, separators=(',', ':'))
    rows[1] = json.dumps({'line': json.dumps(out[1], ensure_ascii=False, separators=(',', ':'))}, ensure_ascii=False, separators=(',', ':'))
    (ROOT / 'tests' / 'question_log_week.jsonl').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    app.close()
    print(f'wrote {len(rows)} lines for {n} questions')


if __name__ == '__main__':
    main()
