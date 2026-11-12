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
lowercase hex characters (`ids`); anything else is dropped. The after= chain is not kept either, only its length,
nor the visitor's rates= (their own cost figures).
The question is cut at 500 characters and a comment at 300. Before a line is written, invisible format characters
(Unicode category Cf: bidi overrides, zero-width characters, the tag characters) are removed, and these are replaced
(`redact`): email addresses; IPv4 and IPv6 addresses; phone and other personal numbers (nine or more digits in one run
with spaces, dots, dashes, slashes or brackets between them, and a 3-4 local number such as 123-4567; an ISO date is
not one); and key-like tokens (20 or more letters and digits mixed, or a run with / or + in mixed case). A comment
with a format character is refused, not cleaned.

At most LINES_PER_MIN lines a minute are written for one client address (the address only counts; it is never written).

On by default only in the hosted demo (PUBLIC_DEMO_ORIGIN): QUESTION_LOG=1 turns it on for the loopback workbench
and QUESTION_LOG=0 off anywhere. The server writes a line after the response is sent, one print per request.
"""
from datetime import datetime, timezone
import json
import os
import re
import sys
import threading
import unicodedata

LOG_NAME = 'question_log'
SCHEMA = 1
QUESTION_MAX = 500
COMMENT_MAX = 300
REFUSAL_MAX = 300
LINES_PER_MIN = 30   # per client address: a script with a well-formed id cannot flood the log or the weekly review
ID = re.compile(r'[0-9a-f]{16}')
LINE_HEADER, SESSION_HEADER = 'X-Question-Line', 'X-Question-Session'

# The #113 guard: a question that asks for one quantity ("how many", "what was our …") of a countable noun or in a
# unit, and lands on the KPI summary (measure=kpis), got the catch-all it should not have. "How did shift 1 go … on
# lineal per paper change?" asks how a window went, which the KPI tiles answer, so it is not flagged.
COUNTABLE = re.compile(
    r"\b(counts?|set-?ups?|(paper|order|grade|flute|board) changes?|change-?overs?|"
    r"wet[- ]ends?|stops?|splices?|step-?ups?|rolls?|orders?|sheets?|boards?|boxes|jams?|rejects?|chops?|"
    r"minutes?|mins?|hours?|hrs?|feet|foot|ft|lineal|msf|sq\.? ?ft|square feet|fpm|percent|dollars?)\b|[%$]", re.I)
ASKS_QUANTITY = re.compile(r"\b(how many|number of|count of|how much|what(?:'s|\s+(?:is|was|were|are)))\b", re.I)

EMAIL = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')
IPV4 = re.compile(r'(?<![\w.])\d{1,3}(\.\d{1,3}){3}(?!\w|\.\d)')   # a full stop after it ends the sentence, not the address
# IPv6: eight full groups, or a compressed form with `::` (an IPv4 tail and a %zone included). A clock time (07:00:00)
# or a MAC address has neither, so it is left alone.
H4 = r'[0-9A-Fa-f]{1,4}'
IPV6 = re.compile(rf'(?<![\w:.])(?=[:\w]*[0-9A-Fa-f])(?:(?:{H4}:){{7}}{H4}|(?:{H4}(?::{H4}){{0,6}})?::(?:{H4}(?::{H4}){{0,6}})?'
                  rf'(?::\d{{1,3}}(?:\.\d{{1,3}}){{3}})?)(?:%[\w.]+)?(?![\w:]|\.\d)')
# Nine or more digits in one run, spaces, dots, dashes, slashes or brackets between them (phone, card, account and
# social-security numbers), or a 3-4 local number. A run holding an ISO date (2026-09-14 15:00) is a date, kept.
NUMBER = re.compile(r'(?<![\w.])\+?\(?\d(?:[ .()/-]{0,3}\d){8,}\)?(?!\w)|(?<![\w-])\d{3}-\d{4}(?![\w-])')
ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
TOKEN = re.compile(r'[A-Za-z0-9_\-]{20,}')
SLASHED_KEY = re.compile(r'[A-Za-z0-9+/=_\-]{20,}')   # base64-shaped secrets (an AWS secret key has / and +)
# Markup a comment may not carry: tags, entities, Markdown links, images and code. A question is logged as typed.
MARKUP = re.compile(r'[<>`]|&#?\w+;|\]\(|!\[')
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f  ]')


def _keylike(token):
    """A long run of letters and digits mixed, as a key or a token is; a hyphenated phrase has no digits."""
    word = token.group(0)
    return '[key]' if re.search(r'\d', word) and re.search(r'[A-Za-z]', word) else word


def _slashed_key(token):
    """A base64-shaped run with / or + in it, in mixed case with digits; a lower-case path (/corr/1/week/…) is kept."""
    word = token.group(0)
    mixed = all(re.search(p, word) for p in (r'[a-z]', r'[A-Z]', r'\d', r'[/+]'))
    return '[key]' if mixed else word


def _number(match):
    return match.group(0) if ISO_DATE.search(match.group(0)) else '[number]'


def invisible(ch):
    """A format character (Unicode category Cf): bidi controls, zero-width characters, the BOM, the tag characters."""
    return unicodedata.category(ch) == 'Cf'


def redact(text, limit):
    """`text` with format characters removed and emails, IP addresses, personal numbers and key-like tokens
    replaced, then cut to `limit` characters (replaced first, so nothing straddling the cut survives as a fragment)."""
    text = ''.join(ch for ch in text if not invisible(ch))
    text = EMAIL.sub('[email]', text)
    text = IPV6.sub('[ip]', text)
    text = IPV4.sub('[ip]', text)
    text = NUMBER.sub(_number, text)
    text = SLASHED_KEY.sub(_slashed_key, text)
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
    """The URL without its after= chain and without the visitor's rates=: the address that answers the question, not
    the views before it, and not the visitor's own cost figures (Codex on PR 131). The routing parameters stay."""
    if not url:
        return url
    path, _, query = url.split('&after=', 1)[0].partition('?')
    kept = [part for part in query.split('&') if part and not part.startswith('rates=')]
    return path + ('?' + '&'.join(kept) if kept else '')


def flags_of(question, measure=None, status=None, moved=False, corrected=False):
    out = []
    if status == 'refused':
        out.append('refused')
    if measure == 'kpis' and COUNTABLE.search(question or '') and ASKS_QUANTITY.search(question or ''):
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
    if (not isinstance(comment, str) or len(comment) > COMMENT_MAX or CONTROL.search(comment) or MARKUP.search(comment)
            or any(invisible(ch) for ch in comment)):
        raise ValueError(f'The comment is plain text of at most {COMMENT_MAX} characters: no markup (< > ` & links), '
                         'no line breaks and no invisible characters')
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
