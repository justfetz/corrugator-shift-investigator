"""Learning workbench server.

Default: loopback only, for one person on one computer. With PUBLIC_DEMO_ORIGIN
set, it runs as the offline synthetic demo behind a hosting platform's TLS proxy:
binds 0.0.0.0:$PORT, allows only the configured browser origins and never accepts
visitor API keys. Offline investigations run one at a time under a lock, for tens of
milliseconds each. A live round answers as a stream and holds that lock only while one
of its tools reads the database, never across a model call (issue #23). The
optional demo guards (DEMO_RATE_LIMIT_PER_MIN, DEMO_DAILY_CAP, DEMO_MAX_TURNS,
TRUST_PROXY) are off unless set; see docs/ANTHROPIC_DEMO.md.

Offline questions are URL-addressable (issue #20, docs/URLS.md): `GET /q?day=&shift=&scope=&period=&q=`
resolves a typed question and redirects to its canonical URL, `/corr/<machine>/<grain>/<date>[/shift/<n>]
?measure=...`, which serves the page, the answer as `.json` (or with `Accept: application/json`), or the
PDF with `format=pdf`. That path never calls a model and never waits on the lock a live round holds.
Live analysis stays on `POST /api/ask`.
"""
from collections import Counter, OrderedDict, deque
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import gzip
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import select
import socket
import sqlite3
import threading
import time

from urllib.parse import parse_qsl, urlsplit

from .agent import Agent, Context, TOOL_SCHEMAS, parse_request
from .anthropic_provider import resolve_model
from .cost_model import effective as effective_rates, parse_rates
from .evalboard import EvalBoard, evals_dir
from .record_store import default_store
from .urls import PATH as URL_PATH, canonical_url, machine_slug, page_url, redirect_for, resolve_url, scope_url

WEB = Path(__file__).with_name('web')
LIVE_MODEL = 'gpt-4.1-mini-2025-04-14'
LIVE_MODES = ('openai', 'anthropic')
EVAL_RUNS_PER_MIN = 120   # POST /api/evals/run, container-wide: a case is 10-100 ms offline, the whole set about 3 s once a minute
LIVE_CONCURRENCY = 2      # live rounds in flight per container; a third is answered 429 `busy` instead of queueing
LEDGER = Path('artifacts/model-budget.sqlite3')  # monthly reservations and the daily round counter


class DemoLimit(Exception):
    """A visitor or service-wide limit was reached; answered as 429 with a code and Retry-After."""
    def __init__(self, message, code, retry_after):
        super().__init__(message)
        self.code = code
        self.retry_after = max(1, int(retry_after))


@dataclass(frozen=True)
class DemoGuards:
    """Env-gated public-demo controls. Zero means off; the defaults keep today's behavior."""
    rate_limit_per_min: int = 0   # /api/ask rounds per client IP per fixed 60-second window
    daily_cap: int = 0            # live-model rounds per UTC day for everyone together
    max_turns: int = 4            # question/scope entries carried to the next question
    trust_proxy: bool = False     # read the client IP from X-Forwarded-For (one trusted hop, e.g. Cloud Run)
    ledger: Path = LEDGER

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        values = {}
        for field, name, low, high in (('rate_limit_per_min', 'DEMO_RATE_LIMIT_PER_MIN', 0, 10000),
                                       ('daily_cap', 'DEMO_DAILY_CAP', 0, 1_000_000), ('max_turns', 'DEMO_MAX_TURNS', 1, 50)):
            raw = (environ.get(name) or '').strip()
            if not raw:
                continue
            if not raw.isdigit() or not low <= int(raw) <= high:
                raise ValueError(f'{name} must be an integer from {low} to {high}')
            values[field] = int(raw)
        return cls(trust_proxy=environ.get('TRUST_PROXY') == '1', **values)

    @property
    def summary(self):
        return {'rate_limit_per_min': self.rate_limit_per_min, 'daily_cap': self.daily_cap,
                'max_turns': self.max_turns, 'trust_proxy': self.trust_proxy}


@dataclass(frozen=True)
class PublicDemo:
    """Hosted synthetic-demo settings. Absent (None) means the loopback workbench."""
    origins: tuple          # browser origins allowed to POST, e.g. the Space URL
    embed_origins: tuple    # origins allowed to frame the page (CSP frame-ancestors)
    live_model: bool        # allow OpenAI mode with the SERVER key only (never a visitor key)

    @staticmethod
    def _origins(raw):
        out = []
        for item in (raw or '').split(','):
            item = item.strip().rstrip('/')
            if not item:
                continue
            local = item.startswith(('http://127.0.0.1', 'http://localhost'))
            scheme, _, hostport = item.partition('://')
            if not (item.startswith('https://') or local) or not hostport or any(c in hostport for c in ' /?#@'):
                raise ValueError(f'Origin must be an https scheme+host (or a loopback http origin): {item!r}')
            out.append(item)
        return tuple(out)

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        origins = cls._origins(environ.get('PUBLIC_DEMO_ORIGIN'))
        if not origins:
            return None
        return cls(origins=origins, embed_origins=cls._origins(environ.get('PUBLIC_DEMO_EMBED_ORIGINS')),
                   live_model=environ.get('PUBLIC_DEMO_LIVE_MODEL') == '1')

    @property
    def frame_ancestors(self):
        return ' '.join(self.embed_origins) if self.embed_origins else "'none'"


class Workbench:
    def __init__(self, public=None, guards=None, store=None):
        self.public = public
        self.guards = guards or DemoGuards()
        self.anthropic_model = resolve_model(os.environ.get('ANTHROPIC_MODEL'))  # an unsupported value fails at boot
        effective_rates()   # an invalid INVESTIGATOR_RATES fails at boot, not on every visitor's question
        # Offline investigations, PDFs and each tool call of a live round take this lock, one at a time, for
        # milliseconds. No model call is ever made while it is held; static files do not wait on it.
        self.lock = threading.Lock()
        self.live_slots = threading.BoundedSemaphore(LIVE_CONCURRENCY)
        self.pins = Counter()         # dataset key -> live rounds reading it; a pinned day is not evicted
        self.read_lock = threading.Lock()   # URL answers (offline only) queue here, never behind a live round
        self.token = self._workbench_token(public)
        self.sessions = OrderedDict()
        self.datasets = OrderedDict()        # day analyses for the POST path, under self.lock
        self.read_datasets = OrderedDict()   # day analyses for the URL path, under self.read_lock
        self.requests = deque()
        self.request_limit = 60       # investigations per process per minute (POST and URL answers); the eval harness raises it
        self.eval_requests = deque()  # /api/evals/run has its own window, so browsing the golden set never spends the question limits
        self.rate_windows = {}        # client ip -> deque of /api/ask timestamps (fixed 60-second window)
        self.rate_lock = threading.Lock()
        self.reports = OrderedDict()
        # The rolling year of synthetic production, built once per process; the eval harness passes the golden window's store.
        self.store = store if store is not None else default_store()
        from .periods import PeriodReports
        self.periods = PeriodReports(self.store)
        # The golden set behind /evals, read from the shipped evals/ folder; None when it is not there
        # (a wheel installed without the repository), and the page and the badge are then absent.
        folder = evals_dir()
        self.evals = EvalBoard(folder) if folder else None   # scored against the golden window, not this store (issue #35)
        self.evals_json = json.dumps(self.evals.board(), allow_nan=False).encode() if self.evals else None

    @staticmethod
    def _workbench_token(public):
        """The X-Workbench-Token every POST must carry.

        Loopback workbench: a random value per process, so nothing outside this
        browser can guess it. Hosted demo: every instance and every restart must
        agree, or a visitor whose page loaded on one instance is refused by the
        next (Cloud Run runs up to two instances and scales to zero). The value is
        not a secret there anyway: `/api/config` hands it to any GET, and the CSRF
        defence is the Origin allowlist plus the custom header (which forces a
        CORS preflight). So derive it from WORKBENCH_SECRET when set, else from
        the demo's own origin; rotate it by changing the secret."""
        if public is None:
            return secrets.token_urlsafe(32)
        seed = os.environ.get('WORKBENCH_SECRET') or public.origins[0]
        return hmac.new(seed.encode('utf-8'), b'workbench-token-v1', 'sha256').hexdigest()

    @property
    def live_modes(self):
        """Live modes the SERVER key enables. Public mode also needs PUBLIC_DEMO_LIVE_MODEL=1."""
        if self.public is not None and not self.public.live_model:
            return ()
        return tuple(mode for mode, var in (('openai', 'OPENAI_API_KEY'), ('anthropic', 'ANTHROPIC_API_KEY')) if os.environ.get(var))

    @property
    def live_model_available(self):
        return bool(self.live_modes)

    @property
    def live_models(self):
        return {'openai': LIVE_MODEL, 'anthropic': self.anthropic_model}

    def client_round(self, ip):
        """Per-IP fixed window over /api/ask rounds. Off unless DEMO_RATE_LIMIT_PER_MIN is set."""
        limit = self.guards.rate_limit_per_min
        if not limit:
            return
        now = time.monotonic()
        with self.rate_lock:
            for stale in [k for k, window in self.rate_windows.items() if not window or window[0] < now-60]:
                window = self.rate_windows[stale]
                while window and window[0] < now-60:
                    window.popleft()
                if not window:
                    del self.rate_windows[stale]
            window = self.rate_windows.setdefault(ip, deque())
            if len(window) >= limit:
                raise DemoLimit('too many questions, slow down', 'rate_limited', math.ceil(window[0]+60-now))
            window.append(now)

    def daily_round(self):
        """Service-wide live-model rounds per UTC day, persisted beside the monthly budget."""
        cap = self.guards.daily_cap
        if not cap:
            return
        now = datetime.now(timezone.utc)
        day = now.strftime('%Y-%m-%d')
        ledger = Path(self.guards.ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(ledger, timeout=5) as db:
            db.execute('CREATE TABLE IF NOT EXISTS demo_rounds (day TEXT PRIMARY KEY, rounds INTEGER NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT rounds FROM demo_rounds WHERE day=?', (day,)).fetchone()
            rounds = row[0] if row else 0
            if rounds >= cap:
                midnight = datetime.combine(now.date()+timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
                raise DemoLimit("today's budget for the chat is used up", 'budget', (midnight-now).total_seconds())
            db.execute('INSERT INTO demo_rounds VALUES (?,?) ON CONFLICT(day) DO UPDATE SET rounds=excluded.rounds', (day, rounds+1))
            db.execute('DELETE FROM demo_rounds WHERE day < ?', ((now-timedelta(days=2)).strftime('%Y-%m-%d'),))

    def close(self):
        for analysis in (*self.datasets.values(), *self.read_datasets.values()):
            analysis.close()

    def _read_dataset(self, day, corrupt):
        """The day's Analysis for the URL path (three entries, least recent evicted and closed). Read lock held."""
        key, cache = (day, corrupt), self.read_datasets
        if key not in cache:
            if len(cache) >= 3:
                _, evicted = cache.popitem(last=False)
                evicted.close()
            cache[key] = self.store.day_analysis(day, corrupt=corrupt)
        cache.move_to_end(key)
        return cache[key]

    @staticmethod
    def _markdown(result):
        return '\n\n'.join([
            f'# Synthetic production investigation: {result.get("period_label", result["production_day"])}',
            result['mode'] + '. No email delivery.',
            *result['sections'], result['limitations']])

    def question_url(self, query):
        """`/q?day=&shift=&scope=&period=&corrupt=&q=&after=`: the same validation as /api/ask for an
        offline question, then the canonical URL the question resolves to (the redirect target)."""
        fields, after = {}, []
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key == 'after':
                after.append(value)
            elif key in fields or key not in ('q', 'day', 'shift', 'scope', 'period', 'corrupt', 'rates'):
                raise ValueError('Invalid request fields')
            else:
                fields[key] = value
        scope = fields.get('scope', 'shift')
        if scope not in ('shift', 'day'):
            raise ValueError('Choose selected shift or production-day comparison')
        period = fields.get('period', 'day')
        if period not in ('day', 'week', 'month', 'accounting'):
            raise ValueError('Choose day, week, calendar month or accounting period')
        shift = fields.get('shift', '')
        if shift not in ('1', '2', '3'):
            raise ValueError('Select a supported shift (1, 2 or 3)')
        day = self.store.check_day(fields.get('day'))
        question = fields.get('q', '')
        if not question.strip() or len(question) > 2000:
            raise ValueError('Enter a question of 1-2000 characters')
        corrupt = fields.get('corrupt', '0')
        if corrupt not in ('0', '1'):
            raise ValueError('Invalid data-quality option')
        context = Context(day, int(shift), period='fiscal' if period == 'accounting' else period)
        context.scope = scope
        context.rates = parse_rates(fields.get('rates'))
        request = parse_request(question, context)   # the refusals of the offline router, verbatim
        return canonical_url(request, context, corrupt == '1', self.store.machine_id, after)

    def canonical_redirect(self, url):
        """The canonical address a hand-typed alias of `url` redirects to (issue #70), or None: `url` is
        canonical already, or the grammar refuses it and the caller answers with that refusal. No tool runs."""
        return redirect_for(url, self.store, max_turns=self.guards.max_turns)

    def answer_url(self, url):
        """A canonical URL answered offline: the result `/api/ask` returns for the same state, plus `url`
        (its page address) and no session. Returns (result, format). ValueError names what is wrong."""
        resolved = resolve_url(url, self.store, max_turns=self.guards.max_turns)
        if resolved['report']:
            result = self.periods.investigate(resolved['day'], resolved['shift'], resolved['scope'], resolved['control_period'], resolved['corrupt'])
        else:
            context = resolved['context']
            context.history = resolved['history']
            analysis = self._read_dataset(resolved['day'], resolved['corrupt'])
            result = Agent(analysis, max_turns=self.guards.max_turns).ask(resolved['text'], context, request=resolved['request'])
        result['url'] = page_url(resolved)
        result['report_markdown'] = self._markdown(result)
        return result, resolved['format']

    def prepare(self, payload, api_key=None, anthropic_key=None):
        """Validate a question and bind it to its session: everything before the investigation runs.
        Call with the lock held. Returns the job `run_offline` or `run_live` takes."""
        if not isinstance(payload, dict) or set(payload)-{'question', 'day', 'shift', 'session_id', 'corrupt', 'mode', 'scope', 'period', 'report', 'rates'}:
            raise ValueError('Invalid request fields')
        scope = payload.get('scope', 'shift')
        if scope not in ('shift', 'day'):
            raise ValueError('Choose selected shift or production-day comparison')
        period = payload.get('period', 'day')
        if period not in ('day', 'week', 'month', 'accounting'):
            raise ValueError('Choose day, week, calendar month or accounting period')
        report = payload.get('report', False)   # the deterministic period report (heatmap, daily matrix, notes) instead of a question
        if type(report) is not bool:
            raise ValueError('Invalid report option')
        if report and period == 'day':
            raise ValueError('The period report needs Week, Month or 4-4-5; a day is investigated with a question')
        mode = payload.get('mode', 'offline')
        if mode not in ('offline', *LIVE_MODES):
            raise ValueError('Choose offline, OpenAI or Anthropic mode')
        if self.public is not None:
            api_key = anthropic_key = None  # a hosted demo never uses a visitor's key, even if one is sent
            if mode != 'offline' and mode not in self.live_modes:
                raise ValueError('This public demo runs the free offline planner only' if not self.live_modes
                                 else f'This public demo does not offer {mode} mode')
        if mode == 'openai' and not (api_key or os.environ.get('OPENAI_API_KEY')):
            raise ValueError('OpenAI mode needs your key in the local key field or server environment')
        if mode == 'anthropic' and not (anthropic_key or os.environ.get('ANTHROPIC_API_KEY')):
            raise ValueError('Anthropic mode needs your key in the local key field or server environment')
        day, shift = payload.get('day'), payload.get('shift')
        if type(shift) is not int or shift not in (1,2,3):
            raise ValueError('Select a supported shift (1, 2 or 3)')
        day = self.store.check_day(day)  # a weekend or out-of-range date is refused with the reason
        question = payload.get('question')
        if not isinstance(question, str) or not question.strip() or len(question)>2000:
            raise ValueError('Enter a question of 1-2000 characters')
        corrupt = payload.get('corrupt', False)
        if type(corrupt) is not bool:
            raise ValueError('Invalid data-quality option')
        rates = parse_rates(payload.get('rates'))   # 'paper:55,margin:40'; the visitor's assumptions, never a key
        session_id = payload.get('session_id')
        if session_id is not None and not isinstance(session_id, str):
            raise ValueError('Invalid session id')
        # Sessions live in this process. An id this instance has never seen (another
        # instance answered the earlier question, or the process restarted) starts a
        # fresh conversation instead of failing: the request carries day, shift and
        # period itself, so only the follow-up history is lost, and the answer says so.
        session_reset = session_id is not None and session_id not in self.sessions
        if session_reset:
            session_id = None
        if mode != 'offline' and not report:
            self.daily_round()  # counted before the model is asked; failed rounds still count
        tool_period = 'fiscal' if period == 'accounting' else period   # the period tools' name for the 4-4-5 window
        job = {'mode': mode, 'report': report, 'question': question, 'day': day, 'shift': shift, 'scope': scope, 'period': period,
               'tool_period': tool_period, 'corrupt': corrupt, 'key': (day, corrupt), 'session_reset': session_reset, 'rates': rates,
               'api_key': api_key, 'anthropic_key': anthropic_key}
        if session_id is None:
            session_id = secrets.token_urlsafe(24)
            if len(self.sessions) >= 32:
                self.sessions.popitem(last=False)
            self.sessions[session_id] = Context(day, shift, period=tool_period)
        job['session_id'] = session_id
        return job

    def _context(self, job):
        """The session's context for this job; a changed scope starts a new one. Call with the lock held."""
        session_id = job['session_id']
        context = self.sessions.get(session_id)
        # Visible date/period/shift controls own the scope; follow-ups retain history while they stay unchanged.
        if (context is None or context.production_day != job['day'] or context.shift != job['shift']
                or context.scope != job['scope'] or context.period != job['tool_period']):
            context = Context(job['day'], job['shift'], period=job['tool_period'])
            self.sessions[session_id] = context
        context.shift = job['shift']
        context.scope = job['scope']
        context.rates = job.get('rates') or {}
        self.sessions.move_to_end(session_id)
        return context

    def _dataset(self, key):
        """The day's analysis, building it on first use; at most three unpinned days stay open. Lock held."""
        if key not in self.datasets:
            idle = [k for k in self.datasets if not self.pins[k]]
            if len(self.datasets) >= 3 and idle:
                self.datasets.pop(idle[0]).close()
            self.datasets[key] = self.store.day_analysis(key[0], corrupt=key[1])
        self.datasets.move_to_end(key)
        return self.datasets[key]

    def run_offline(self, job):
        """The deterministic answer or the period report: no model, whatever the mode. Lock held."""
        if job['report']:
            result = self.periods.investigate(job['day'], job['shift'], job['scope'], job['period'], job['corrupt'])
            result['url'] = (scope_url(job['day'], job['period'], job['scope'], job['shift'], self.store.machine_id)
                             + '?measure=report' + ('&corrupt=1' if job['corrupt'] else ''))
            return self._retain(result, job['session_id'], job['session_reset'])
        context = self._context(job)
        request = parse_request(job['question'], context)
        result = Agent(self._dataset(job['key']), max_turns=self.guards.max_turns).ask(job['question'], context, request=request)
        result['url'] = canonical_url(request, context, job['corrupt'], self.store.machine_id)   # the question's own address
        return self._retain(result, job['session_id'], job['session_reset'])

    def make_planner(self, mode, key, budget, cancelled=None):
        """The provider adapter for a live round; tests replace this to stub the model."""
        if mode == 'openai':
            from .openai_provider import OpenAIPlanner
            return OpenAIPlanner(key or os.environ.get('OPENAI_API_KEY'), budget)
        from .anthropic_provider import AnthropicAnalyst
        return AnthropicAnalyst(key or os.environ.get('ANTHROPIC_API_KEY'), budget, model=self.anthropic_model, cancelled=cancelled)

    def run_live(self, job, emit=None, cancelled=None):
        """A live round. Call WITHOUT the lock: it is taken only around each tool call and the
        context updates (the agent's guard), so offline questions never wait behind a model call."""
        from .openai_provider import Budget
        mode = job['mode']
        planner = self.make_planner(mode, job['api_key'] if mode == 'openai' else job['anthropic_key'], Budget(Path(self.guards.ledger)), cancelled)
        with self.lock:
            context = self._context(job)
            analysis = self._dataset(job['key'])
            self.pins[job['key']] += 1
        try:
            if getattr(planner, 'planned', False):   # one planning call, tools, one streamed narrative
                agent = Agent(analysis, planner=planner, max_calls=2, max_seconds=10, max_turns=self.guards.max_turns, guard=self.lock)
                result = agent.ask_planned(job['question'], context, emit)
            else:                                    # the tool loop (OpenAI): same stream, steps as they finish
                agent = Agent(analysis, planner=planner, max_calls=10, max_seconds=60, max_turns=self.guards.max_turns, guard=self.lock)
                result = agent.ask(job['question'], context, emit=emit)
        finally:
            planner.close()
            with self.lock:
                self.pins[job['key']] -= 1
        with self.lock:
            return self._retain(result, job['session_id'], job['session_reset'])

    def ask(self, payload, api_key=None, anthropic_key=None):
        """One question, start to finish, as a single result. The HTTP handler streams live modes
        instead (see do_POST); this is the direct entry for offline questions, reports and tests.
        Call with the lock held for offline work, as the handler does; a live mode must be called
        without it."""
        job = self.prepare(payload, api_key=api_key, anthropic_key=anthropic_key)
        if job['mode'] == 'offline' or job['report']:
            return self.run_offline(job)
        return self.run_live(job)

    def _retain(self, result, session_id, session_reset=False):
        result['session_id'] = session_id
        result['session_reset'] = session_reset   # True: the id the client sent was unknown here; a fresh conversation answered
        result['report_markdown'] = self._markdown(result)
        self.reports[session_id] = result
        self.reports.move_to_end(session_id)
        if len(self.reports) > 32:
            self.reports.popitem(last=False)
        return result

    def pdf(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'session_id', 'run_id'}:
            raise ValueError('Invalid report request')
        if not all(isinstance(v, str) for v in payload.values()):
            raise ValueError('Invalid report reference')
        result = self.reports.get(payload['session_id'])
        if result is None or result['run_id'] != payload['run_id']:
            raise ValueError('Report expired; run the investigation again')
        from .reports import report_pdf
        return report_pdf(result)


def make_server(port=8765, public=None, host=None, guards=None, store=None):
    app = Workbench(public, guards, store)
    frame_ancestors = public.frame_ancestors if public else "'none'"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not retain visitor prompts or session identifiers in access logs.

        def _accepts_gzip(self):
            """True when Accept-Encoding lists gzip with a non-zero q (RFC 9110 12.5.3)."""
            for token in self.headers.get('Accept-Encoding', '').lower().split(','):
                coding, _, params = token.strip().partition(';')
                if coding in ('gzip', 'x-gzip'):
                    q = params.strip().removeprefix('q=').strip() if params.strip().startswith('q=') else '1'
                    try:
                        return float(q) > 0
                    except ValueError:
                        return True
            return False

        def _send(self, status, body, content_type='application/json; charset=utf-8', extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            headers = dict(extra or {})
            # gzip anything a browser can wait on (issue #33): an answer is 20-145 KB raw and
            # 3-21 KB compressed, and the trace is half of it. Small bodies and the PDF are left alone.
            if status != 304 and len(body) >= 1024 and content_type != 'application/pdf' and self._accepts_gzip():
                body = gzip.compress(body, compresslevel=6)
                headers['Content-Encoding'] = 'gzip'
                if 'ETag' in headers:   # a strong validator names one representation; the gzip one gets its own
                    headers['ETag'] = headers['ETag'][:-1] + '-gzip"'
            headers.setdefault('Cache-Control', 'no-store')   # static files set their own (revalidated with an ETag)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header('Vary', 'Accept-Encoding')
            if status == 304:   # RFC 9110 8.6: no Content-Length, and no body, on a 304
                self.end_headers()
                return
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors "+frame_ancestors+"; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _stream_open(self):
            """Start a newline-delimited JSON stream: one event per line, flushed as it happens.
            Chunked on HTTP/1.1 so proxies pass each line on; the connection closes at the end."""
            self.chunked = self.request_version == 'HTTP/1.1'
            if self.chunked:
                self.protocol_version = 'HTTP/1.1'
            self.close_connection = True
            self.send_response(200)
            for name, value in (('Content-Type', 'application/x-ndjson; charset=utf-8'), ('Cache-Control', 'no-store'),
                                ('X-Accel-Buffering', 'no'), ('Connection', 'close'), ('X-Content-Type-Options', 'nosniff'),
                                ('Referrer-Policy', 'no-referrer')):
                self.send_header(name, value)
            if self.chunked:
                self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self.gone = False

        def _stream_event(self, event):
            if self.gone:
                return
            line = json.dumps(event, allow_nan=False).encode() + b'\n'
            try:
                self.wfile.write(b'%x\r\n%s\r\n' % (len(line), line) if self.chunked else line)
                self.wfile.flush()
            except OSError:
                self.gone = True   # the visitor left: stop writing; the model call is cancelled at its next event

        def _stream_close(self):
            if self.chunked and not self.gone:
                try:
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                except OSError:
                    self.gone = True

        def _client_gone(self):
            """True once the visitor's connection is closed: a failed write, or end-of-file on the socket."""
            if self.gone:
                return True
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and self.connection.recv(1, socket.MSG_PEEK) == b'':
                    self.gone = True
            except (OSError, ValueError):
                self.gone = True
            return self.gone

        def _live(self, payload, api_key, anthropic_key):
            """A live round as a stream: start, plan, a step per tool, narrative deltas, done.
            Runs outside the shared lock under its own semaphore; a visitor who leaves cancels the model call."""
            if not app.live_slots.acquire(blocking=False):
                raise DemoLimit('the analyst is busy with other questions; retry in a few seconds', 'busy', 3)
            try:
                with app.lock:
                    job = app.prepare(payload, api_key=api_key, anthropic_key=anthropic_key)   # a refused request is still a plain 400/429
                self._stream_open()
                self._stream_event({'type': 'start', 'mode': job['mode'], 'session_id': job['session_id']})
                try:
                    result = app.run_live(job, emit=self._stream_event, cancelled=self._client_gone)
                    self._stream_event({'type': 'done', 'result': result})
                except (ValueError, UnicodeError, TypeError, KeyError) as exc:
                    self._stream_event({'type': 'error', 'error': str(exc)})
                except Exception:
                    self._stream_event({'type': 'error', 'error': 'Investigation failed; no answer was fabricated'})
                self._stream_close()
            finally:
                app.live_slots.release()

        def _host_ok(self):
            if public is not None:
                return True  # the platform proxy owns the hostname; POSTs are checked by Origin instead
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def _client_ip(self):
            # Behind exactly one trusted proxy (TRUST_PROXY=1) the proxy appends the real client to
            # X-Forwarded-For, so the rightmost entry is the one it vouches for; anything a client
            # sent itself sits further left and is ignored. Without the flag the header is ignored.
            if app.guards.trust_proxy:
                forwarded = self.headers.get('X-Forwarded-For', '')
                if forwarded.strip():
                    return forwarded.split(',')[-1].strip()[:64] or self.client_address[0]
            return self.client_address[0]

        def _origin_ok(self):
            origin = self.headers.get('Origin')
            if public is not None:
                return origin is not None and origin.rstrip('/') in public.origins
            return origin is None or origin == f'http://127.0.0.1:{self.server.server_port}'

        def _count_request(self):
            """The per-process 60-per-minute window over investigations (POST and URL answers alike)."""
            now = time.monotonic()
            while app.requests and app.requests[0] < now-60:
                app.requests.popleft()
            if len(app.requests) >= app.request_limit:
                return False
            app.requests.append(now)
            return True

        def _wants_json(self, query):
            """The `.json` suffix, `format=json`, or an Accept header that asks for JSON and not HTML."""
            if 'format=json' in query.split('&'):
                return True
            accept = self.headers.get('Accept', '')
            return 'application/json' in accept and 'text/html' not in accept

        def _page(self):
            """The app page, revalidated with an ETag like `/`: a canonical URL serves it and app.js reads the address."""
            data = (WEB/'index.html').read_bytes()
            etag = '"' + hashlib.sha256(data).hexdigest()[:16] + '"'
            held = [v.strip() for v in self.headers.get('If-None-Match', '').split(',')]
            if etag in held or etag[:-1] + '-gzip"' in held:
                return self._send(304, b'', 'text/html; charset=utf-8', {'ETag': etag, 'Cache-Control': 'no-cache'})
            self._send(200, data, 'text/html; charset=utf-8', {'ETag': etag, 'Cache-Control': 'no-cache'})

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {'error': 'Local host only'})
            url = urlsplit(self.path)
            if url.path == '/q':
                # A typed question is a shortcut: resolve it and redirect to the one URL for it.
                try:
                    target = app.question_url(url.query)
                except (ValueError, UnicodeError, TypeError, KeyError) as exc:
                    return self._send(400, {'error': str(exc)})
                return self._send(302, {'url': target}, extra={'Location': target, 'Cache-Control': 'no-store'})
            if url.path.startswith('/corr/'):
                return self._answer_url(url)
            if self.path in ('/api/health', '/healthz'):  # /healthz is reserved by Google's front end on *.run.app; /api/health reaches the container everywhere
                return self._send(200, {'ok': True, 'public': public is not None,
                                        'store': {'source': app.store.boot_source, 'boot_ms': round(app.store.boot_seconds * 1000),
                                                  'dataset_hash': app.store.content_hash[:12],
                                                  # the rolling window (issue #35): fixed per image, moved by the nightly deploy
                                                  'window': {'as_of': app.store.as_of.isoformat(), 'first_day': app.store.first_day,
                                                             'last_day': app.store.last_day,
                                                             'production_days': len(app.store.production_days)}}})
            if self.path == '/api/config':
                return self._send(200, {'days': app.store.production_days, 'first_day': app.store.first_day,
                                       'last_day': app.store.last_day, 'production_calendar': 'Mon-Fri',
                                       'dataset_hash': app.store.content_hash, 'machine': machine_slug(app.store.machine_id),
                                       'token': app.token, 'tools': TOOL_SCHEMAS, 'mode': 'offline',
                                       'public': public is not None, 'server_key_available': app.live_model_available,
                                       'live_model_available': app.live_model_available, 'live_model': LIVE_MODEL,
                                       'live_modes': list(app.live_modes), 'live_models': app.live_models,
                                       'demo_guards': app.guards.summary,
                                       'evals': app.evals.summary if app.evals else None})
            if self.path == '/evals.json':
                # The scoreboard as data, Datasette-style: public, read-only, and readable from another
                # origin (the blog embeds it), so this one response carries Access-Control-Allow-Origin.
                if app.evals is None:
                    return self._send(404, {'error': 'The golden set is not shipped with this build'})
                return self._send(200, app.evals_json, extra={'Access-Control-Allow-Origin': '*'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/guide': ('guide.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/theme.js': ('theme.js', 'text/javascript; charset=utf-8'),
                     '/trail.js': ('trail.js', 'text/javascript; charset=utf-8'), '/evals.js': ('evals.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            path = self.path
            if app.evals is not None and path.split('?', 1)[0] == '/evals':   # /evals?case=<id> opens that case: the URL is the state
                path, files = '/evals', {'/evals': ('evals.html', 'text/html; charset=utf-8')}
            if path not in files:
                return self._send(404, {'error': 'Not found'})
            filename, content_type = files[path]
            data = (WEB/filename).read_bytes()
            # Revalidate, never re-download: the browser keeps app.js and style.css and asks with
            # If-None-Match on each load; an unchanged file is a 304 with no body (issue #33).
            etag = '"' + hashlib.sha256(data).hexdigest()[:16] + '"'
            held = [v.strip() for v in self.headers.get('If-None-Match', '').split(',')]
            if etag in held or etag[:-1] + '-gzip"' in held:   # either representation the browser may hold
                return self._send(304, b'', content_type, {'ETag': etag, 'Cache-Control': 'no-cache'})
            self._send(200, data, content_type, {'ETag': etag, 'Cache-Control': 'no-cache'})

        def _answer_url(self, url):
            """`/corr/<machine>/<grain>/<date>[/shift/<n>][.json]?measure=...`: the page, the answer or the PDF.
            Offline only; the same guards as /api/ask except the token (a GET carries no key and changes
            nothing), and its own lock, so it never queues behind a live round."""
            pdf = 'format=pdf' in url.query.split('&')
            page = not pdf and not url.path.endswith('.json') and not self._wants_json(url.query)
            if page and not URL_PATH.match(url.path):
                return self._send(404, {'error': 'Not found'})
            # A hand-typed alias (docs/URLS.md) is a 302 to its canonical address, as /q is, so the address bar ends
            # on the one spelling; like /q it runs no tool and is not a round. A refused URL is answered below.
            target = app.canonical_redirect(self.path)
            if target:
                return self._send(302, {'url': target}, extra={'Location': target, 'Cache-Control': 'no-store'})
            if page:
                return self._page()   # a refused address still gets the page, which shows the refusal of its .json
            try:
                if not self._count_request():
                    return self._send(429, {'error': 'Local request limit reached; retry in a minute'})
                app.client_round(self._client_ip())   # a round is counted even if the URL is later refused
                started = time.perf_counter()
                with app.read_lock:
                    result, fmt = app.answer_url(self.path)
                headers = {'Server-Timing': f'app;dur={(time.perf_counter()-started)*1000:.1f}'}
                if fmt == 'pdf':
                    from .reports import report_pdf
                    headers['Content-Disposition'] = f'attachment; filename="synthetic-report-{result["production_day"]}.pdf"'
                    return self._send(200, report_pdf(result), 'application/pdf', headers)
                self._send(200, result, extra=headers)
            except DemoLimit as exc:
                self._send(429, {'error': str(exc), 'code': exc.code}, extra={'Retry-After': str(exc.retry_after)})
            except (ValueError, UnicodeError, TypeError, KeyError) as exc:
                self._send(404 if str(exc).startswith('No machine') else 400, {'error': str(exc)})
            except Exception:
                self._send(500, {'error': 'Investigation failed; no answer was fabricated'})

        def do_POST(self):
            # Consume bounded bodies before a rejection so Windows clients receive
            # the HTTP error instead of a reset from closing over unread bytes.
            try:
                self.connection.settimeout(5)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16000:
                    if 0 < length <= 65536:
                        self.rfile.read(length)
                    return self._send(413, {'error': 'Request too large or empty'})
                raw_body = self.rfile.read(length)
                if len(raw_body) != length:
                    return self._send(400, {'error': 'Incomplete request body'})
            except (ValueError, TimeoutError):
                return self._send(400, {'error': 'Invalid or timed-out request body'})
            if not self._host_ok() or self.path not in ('/api/ask', '/api/report', '/api/evals/run'):
                return self._send(403, {'error': 'Request not allowed'})
            if not self._origin_ok():
                return self._send(403, {'error': 'Origin not allowed'})
            if not hmac.compare_digest(self.headers.get('X-Workbench-Token', ''), app.token):
                return self._send(403, {'error': 'Missing session token'})
            if self.headers.get('Content-Type') != 'application/json':
                return self._send(415, {'error': 'JSON required'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16000:
                    return self._send(413, {'error': 'Request too large or empty'})
                # Eval runs are counted apart from questions (EVAL_RUNS_PER_MIN for everyone together): a visitor
                # opening cases on /evals must not use up the question window or their own per-IP rounds.
                if self.path == '/api/evals/run':
                    now = time.monotonic()
                    window = app.eval_requests
                    while window and window[0] < now-60:
                        window.popleft()
                    if len(window) >= EVAL_RUNS_PER_MIN:
                        return self._send(429, {'error': 'Golden-case run limit reached; retry in a minute'}, extra={'Retry-After': str(math.ceil(window[0]+60-now))})
                    window.append(now)
                elif not self._count_request():
                    return self._send(429, {'error': 'Local request limit reached; retry in a minute'}, extra={'Retry-After': str(math.ceil(app.requests[0]+60-time.monotonic()))})
                if self.path == '/api/ask':
                    app.client_round(self._client_ip())  # a round is counted even if the question is later rejected
                self.connection.settimeout(5)
                payload = json.loads(raw_body)
                api_key = anthropic_key = None
                if public is None:
                    api_key, anthropic_key = self.headers.get('X-OpenAI-Key'), self.headers.get('X-Anthropic-Key')
                if self.path == '/api/evals/run':
                    # Offline, or a replay of a recorded fixture: never a model call, whatever the server's keys.
                    # The board takes app.lock itself (per case), so a whole-set run does not starve questions.
                    if app.evals is None:
                        return self._send(404, {'error': 'The golden set is not shipped with this build'})
                    if not isinstance(payload, dict) or set(payload) not in ({'id'}, {'all'}) or payload.get('all', True) is not True:
                        raise ValueError('Send {"id": "<case>"} or {"all": true}')
                    return self._send(200, app.evals.run_all(app.lock) if 'all' in payload else app.evals.run_one(payload['id'], app.lock))
                if self.path == '/api/ask' and isinstance(payload, dict) and payload.get('mode') in LIVE_MODES and payload.get('report') is not True:
                    return self._live(payload, api_key, anthropic_key)   # a stream; offline keeps its single JSON response
                with app.lock:
                    if self.path == '/api/report':
                        body = app.pdf(payload)
                    else:
                        body = app.ask(payload, api_key=api_key, anthropic_key=anthropic_key)
                self._send(200, body, 'application/pdf' if self.path == '/api/report' else 'application/json; charset=utf-8')
            except DemoLimit as exc:
                self._send(429, {'error': str(exc), 'code': exc.code}, extra={'Retry-After': str(exc.retry_after)})
            except (ValueError, UnicodeError, TypeError, KeyError) as exc:
                self._send(400, {'error': str(exc)})
            except TimeoutError:
                self._send(408, {'error': 'Request timed out'})
            except Exception:
                self._send(500, {'error': 'Investigation failed; no answer was fabricated'})
    # One thread per request in both modes: a live round streams for seconds, and an offline
    # question arriving meanwhile must be answered at once (issue #23).
    server = ThreadingHTTPServer((host or ('127.0.0.1' if public is None else '0.0.0.0'), port), Handler)
    server.daemon_threads = True
    server.app = app
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=None)
    args = parser.parse_args()
    public = PublicDemo.from_env()
    guards = DemoGuards.from_env()
    if public is None:
        server = make_server(args.port or 8765, guards=guards)
        print(f'Local workbench: http://127.0.0.1:{server.server_port}', flush=True)
        print('Synthetic data. Offline by default; OpenAI and Anthropic modes require a key. No email delivery.', flush=True)
    else:
        server = make_server(args.port or int(os.environ.get('PORT', '7860')), public=public, guards=guards)
        live = ('server key for ' + '/'.join(server.app.live_modes) + ' with the spend cap') if server.app.live_modes else 'offline planner only'
        print(f'Public synthetic demo on 0.0.0.0:{server.server_port} for {", ".join(public.origins)}; '
              f'embeddable by {public.frame_ancestors}; {live}. No visitor keys, no email. Guards: {guards.summary}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.close()

if __name__ == '__main__':
    main()
