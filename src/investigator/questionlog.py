"""The question log (issue #125): what visitors ask, kept anonymously, so the misses become golden cases.

One JSON line per event on stdout, which Cloud Run ships to Cloud Logging with no new service; a log sink copies
the lines to BigQuery (docs/DEPLOYMENT.md, "The question log"). The events of one question share its line id, which
the page draws at random when the question is asked:

    question  /q routed the question, or refused it: the text, the rail's scope, the canonical URL, the measure, flags
    answer    that URL answered it (the page follows /q's redirect): the status and the server ms
    analyst   "Ask the analyst" ran on it: the provider, the round's status (plan_rejected among them), model calls, ms
    feedback  a thumbs up or down on its card, and the optional "What were you looking for?"

A question is one row of the `question_lines` view the review query reads, the four joined on the line id.

What a line never holds: an IP address, a user agent, a cookie, a key, or any request header's value. The handler
passes this module the two ids and nothing else from the request's headers, and an id is kept only when it is 16
lowercase hex characters (`ids`); anything else is dropped. The after= chain is not kept either, only its length.
The question is cut at 500 characters and a comment at 300, and anything in them shaped like an email address, a
phone number, an IP address or a key is replaced before the line is written (`redact`).

On by default only in the hosted demo (PUBLIC_DEMO_ORIGIN): QUESTION_LOG=1 turns it on for the loopback workbench
and QUESTION_LOG=0 off anywhere. The server writes a line after the response is sent, one print per request.
"""
from datetime import datetime, timezone
import json
import os
import re
import sys
import threading

LOG_NAME = 'question_log'
SCHEMA = 1
QUESTION_MAX = 500
COMMENT_MAX = 300
REFUSAL_MAX = 300
ID = re.compile(r'[0-9a-f]{16}')
LINE_HEADER, SESSION_HEADER = 'X-Question-Line', 'X-Question-Session'

# The #113 guard: a question that names a countable noun or a unit, or asks for a count, is asking for one quantity,
# and landing on the KPI summary (measure=kpis) is the catch-all it should not be.
COUNTABLE = re.compile(
    r"\b(how many|number of|count of|counts?|set-?ups?|(paper|order|grade|flute|board) changes?|change-?overs?|"
    r"wet[- ]ends?|stops?|splices?|step-?ups?|rolls?|orders?|sheets?|boards?|boxes|jams?|rejects?|chops?|"
    r"minutes?|mins?|hours?|hrs?|feet|foot|ft|lineal|msf|sq\.? ?ft|square feet|fpm|percent|dollars?)\b|[%$]", re.I)

EMAIL = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')
IPV4 = re.compile(r'(?<![\w.])\d{1,3}(\.\d{1,3}){3}(?![\w.])')
PHONE = re.compile(r'(?<![\w-])\+?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])|(?<![\w-])\+\d[\d\s-]{8,}\d(?![\w-])|\b\d{10,}\b')
TOKEN = re.compile(r'[A-Za-z0-9_\-]{20,}')
# Markup a comment may not carry: tags, entities, Markdown links, images and code. A question is logged as typed.
MARKUP = re.compile(r'[<>`]|&#?\w+;|\]\(|!\[')
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f  ]')


def _keylike(token):
    """A long run of letters and digits mixed, as a key or a token is; a hyphenated phrase has no digits."""
    word = token.group(0)
    return '[key]' if re.search(r'\d', word) and re.search(r'[A-Za-z]', word) else word


def redact(text, limit):
    """`text` with emails, IP addresses, phone numbers and key-like tokens replaced, then cut to `limit` characters
    (replaced first, so nothing straddling the cut survives as a fragment)."""
    text = EMAIL.sub('[email]', text)
    text = IPV4.sub('[ip]', text)
    text = PHONE.sub('[phone]', text)
    return TOKEN.sub(_keylike, text)[:limit]


def valid_id(value):
    """`value` when it is a line or session id the page drew (16 lowercase hex characters), else None."""
    return value if isinstance(value, str) and ID.fullmatch(value) else None


def ids(headers):
    """(line, session) from the request's X-Question-Line and X-Question-Session: each kept only when it is 16
    lowercase hex characters, else None. No other header is read, and no value but a well-formed id leaves here."""
    return valid_id(headers.get(LINE_HEADER)), valid_id(headers.get(SESSION_HEADER))


def measure_of(url):
    """The canonical URL's measure= (the first parameter of the grammar), or None."""
    match = re.search(r'\?measure=([a-z]+)', url or '')
    return match.group(1) if match else None


def bare(url):
    """The URL without its after= chain: the address that answers the question, not the views before it."""
    return url.split('&after=', 1)[0] if url else url


def flags_of(question, measure=None, status=None, moved=False, corrected=False):
    out = []
    if status == 'refused':
        out.append('refused')
    if measure == 'kpis' and COUNTABLE.search(question or ''):
        out.append('catch_all')
    if corrected:
        out.append('typo_corrected')
    if moved:
        out.append('scope_moved')
    if status == 'plan_rejected':
        out.append('analyst_plan_rejected')
    return out


def check_comment(comment):
    """The optional comment of a feedback post: plain text of at most 300 characters, or a ValueError."""
    if comment is None:
        return None
    if not isinstance(comment, str) or len(comment) > COMMENT_MAX or CONTROL.search(comment) or MARKUP.search(comment):
        raise ValueError(f'The comment is plain text of at most {COMMENT_MAX} characters: no markup (< > ` & links) '
                         'and no line breaks')
    comment = ' '.join(comment.split())
    return comment or None


def scope_of(fields):
    """The rail's scope from a request's own fields (/q's strings or /api/ask's JSON), each kept only in its valid
    form, so a refused request logs no junk."""
    day, shift, scope, period = (fields.get(key) for key in ('day', 'shift', 'scope', 'period'))
    scope, period = scope or 'shift', period or 'day'
    return {'day': day if isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) else None,
            'shift': int(shift) if type(shift) in (int, str) and str(shift) in ('1', '2', '3') else None,
            'scope': scope if scope in ('shift', 'day') else None,
            'period': period if period in ('day', 'week', 'month', 'accounting') else None,
            'corrupt': fields.get('corrupt') in ('1', True)}


class QuestionLog:
    def __init__(self, enabled, stream=None, dataset=None, environ=None):
        environ = os.environ if environ is None else environ
        self.enabled = bool(enabled)
        self.stream = stream            # None: sys.stdout at write time (pytest swaps it per test)
        self.dataset = dataset
        try:
            from importlib.metadata import version
            self.version = version('corrugator-shift-investigator')
        except Exception:
            self.version = None
        self.revision = environ.get('K_REVISION') or None   # Cloud Run's revision name: a preview's lines say so
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls, public, dataset=None, environ=None):
        environ = os.environ if environ is None else environ
        raw = (environ.get('QUESTION_LOG') or '').strip()
        if raw not in ('', '0', '1'):
            raise ValueError('QUESTION_LOG must be 0 or 1')
        return cls(raw == '1' or (raw == '' and public is not None), dataset=dataset, environ=environ)

    def _event(self, event, line, session, **fields):
        return {'severity': 'INFO', 'message': f'{LOG_NAME} {event}', 'log': LOG_NAME, 'v': SCHEMA, 'event': event,
                'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                'line': line, 'session': session, **fields,
                'version': self.version, 'revision': self.revision, 'dataset': self.dataset}

    def question(self, line, session, seen, url=None, refusal=None, ms=None):
        """The question event of a /q request, from what `Workbench.question_url` saw; None when not logged."""
        if not (self.enabled and line):
            return None
        from .agent import correct_typos
        text = seen.get('question') if isinstance(seen.get('question'), str) else ''
        status = 'refused' if refusal is not None else 'routed'
        measure = measure_of(url)
        return self._event('question', line, session, question=redact(text, QUESTION_MAX), truncated=len(text) > QUESTION_MAX,
                           scope=seen.get('scope'), follow_ups=seen.get('follow_ups', 0), url=bare(url), measure=measure,
                           status=status, refusal=redact(refusal, REFUSAL_MAX) if refusal is not None else None,
                           server_ms=ms, flags=flags_of(text, measure, status, seen.get('moved', False),
                                                        correct_typos(text.lower()) != text.lower()))

    def answer(self, line, session, url=None, status=None, refusal=None, ms=None):
        if not (self.enabled and line):
            return None
        return self._event('answer', line, session, url=bare(url), measure=measure_of(url), status=status,
                           refusal=redact(refusal, REFUSAL_MAX) if refusal is not None else None, server_ms=ms)

    def analyst(self, line, session, asked, status, result=None, refusal=None, ms=None):
        """A live round: `asked` is the prepared job, or the posted body when the round was refused before that."""
        if not (self.enabled and line):
            return None
        asked = asked if isinstance(asked, dict) else {}
        text = asked.get('question') if isinstance(asked.get('question'), str) else ''
        usage = (result or {}).get('usage') or {}
        mode = asked.get('mode') if asked.get('mode') in ('openai', 'anthropic') else None
        return self._event('analyst', line, session, question=redact(text, QUESTION_MAX), truncated=len(text) > QUESTION_MAX,
                           scope=scope_of(asked), mode=mode,
                           status=status, narrative=(result or {}).get('narrative_status'),
                           model_calls=usage.get('model_calls'), tool_calls=usage.get('tool_calls'),
                           refusal=redact(refusal, REFUSAL_MAX) if refusal is not None else None, server_ms=ms,
                           flags=flags_of(text, status=status))

    def feedback(self, line, session, card, vote, comment):
        if not (self.enabled and line):
            return None
        return self._event('feedback', line, session, card=card, vote=vote, comment=redact(comment, COMMENT_MAX) if comment else None)

    def write(self, record):
        """One line, whole: never raises into the request that produced it."""
        if not record:
            return
        try:
            text = json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            with self.lock:
                stream = self.stream or sys.stdout
                stream.write(text + '\n')
                stream.flush()
        except Exception:
            pass
