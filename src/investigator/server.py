"""Learning workbench server.

Default: loopback only, for one person on one computer. With PUBLIC_DEMO_ORIGIN
set, it runs as the offline synthetic demo behind a hosting platform's TLS proxy:
binds 0.0.0.0:$PORT, allows only the configured browser origins, never accepts
visitor API keys, and serves investigations one at a time under a lock. The
optional demo guards (DEMO_RATE_LIMIT_PER_MIN, DEMO_DAILY_CAP, DEMO_MAX_TURNS,
TRUST_PROXY) are off unless set; see docs/ANTHROPIC_DEMO.md.
"""
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import argparse
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from .agent import Agent, Context, TOOL_SCHEMAS
from .anthropic_provider import resolve_model
from .record_store import DEFAULT_MONTHS, default_store
from .synth import production_days

WEB = Path(__file__).with_name('web')
# The record store's Monday-Friday production calendar (August and September 2026);
# a pure calendar here, the records themselves are built once by default_store().
DAYS = [d.isoformat() for d in production_days(DEFAULT_MONTHS)]
LIVE_MODEL = 'gpt-4.1-mini-2025-04-14'
LIVE_MODES = ('openai', 'anthropic')
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
    def __init__(self, public=None, guards=None):
        self.public = public
        self.guards = guards or DemoGuards()
        self.anthropic_model = resolve_model(os.environ.get('ANTHROPIC_MODEL'))  # an unsupported value fails at boot
        self.lock = threading.Lock()  # investigations and PDFs run one at a time; static files do not wait
        self.token = secrets.token_urlsafe(32)
        self.sessions = OrderedDict()
        self.datasets = OrderedDict()
        self.requests = deque()
        self.rate_windows = {}        # client ip -> deque of /api/ask timestamps (fixed 60-second window)
        self.rate_lock = threading.Lock()
        self.reports = OrderedDict()
        self.store = default_store()  # the two synthetic months, built once per process
        from .periods import PeriodReports
        self.periods = PeriodReports(self.store)

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
        for analysis in self.datasets.values():
            analysis.close()

    def ask(self, payload, api_key=None, anthropic_key=None):
        if not isinstance(payload, dict) or set(payload)-{'question', 'day', 'shift', 'session_id', 'corrupt', 'mode', 'scope', 'period'}:
            raise ValueError('Invalid request fields')
        scope = payload.get('scope', 'shift')
        if scope not in ('shift', 'day'):
            raise ValueError('Choose selected shift or production-day comparison')
        period = payload.get('period', 'day')
        if period not in ('day', 'week', 'month', 'accounting'):
            raise ValueError('Choose day, week, calendar month or accounting period')
        mode = payload.get('mode', 'offline')
        if period != 'day' and mode != 'offline':
            raise ValueError('Week/month reports use the free deterministic tools; select Offline mode')
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
        session_id = payload.get('session_id')
        if session_id is not None and (not isinstance(session_id, str) or session_id not in self.sessions):
            raise ValueError('Session expired; reset the conversation')
        if mode != 'offline':
            self.daily_round()  # counted before the model is asked; failed rounds still count
        if session_id is None:
            session_id = secrets.token_urlsafe(24)
            if len(self.sessions) >= 32:
                self.sessions.popitem(last=False)
            self.sessions[session_id] = Context(day, shift)
        if period != 'day':
            result = self.periods.investigate(day, shift, scope, period, corrupt)
            return self._retain(result, session_id)
        context = self.sessions[session_id]
        # Visible date/shift controls own the scope; same-day follow-ups retain history.
        if context.production_day != day or context.shift != shift or context.scope != scope:
            context = Context(day, shift)
            self.sessions[session_id] = context
        context.shift = shift
        context.scope = scope
        self.sessions.move_to_end(session_id)
        key = (day, corrupt)
        if key not in self.datasets:
            if len(self.datasets) >= 3:
                _, evicted = self.datasets.popitem(last=False)
                evicted.close()
            self.datasets[key] = self.store.day_analysis(day, corrupt=corrupt)
        self.datasets.move_to_end(key)
        if mode == 'offline':
            result = Agent(self.datasets[key], max_turns=self.guards.max_turns).ask(question, context)
        else:
            from .openai_provider import Budget
            budget = Budget(Path(self.guards.ledger))
            if mode == 'openai':
                from .openai_provider import OpenAIPlanner
                planner = OpenAIPlanner(api_key or os.environ.get('OPENAI_API_KEY'), budget)
            else:
                from .anthropic_provider import AnthropicPlanner
                planner = AnthropicPlanner(anthropic_key or os.environ.get('ANTHROPIC_API_KEY'), budget, model=self.anthropic_model)
            try:
                result = Agent(self.datasets[key], planner=planner, max_calls=10, max_seconds=60, max_turns=self.guards.max_turns).ask(question, context)
            finally:
                planner.close()
        return self._retain(result, session_id)

    def _retain(self, result, session_id):
        result['session_id'] = session_id
        result['report_markdown'] = '\n\n'.join([
            f'# Synthetic production investigation: {result.get("period_label", result["production_day"])}',
            result['mode'] + '. No email delivery.',
            *result['sections'], result['limitations']])
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


def make_server(port=8765, public=None, host=None, guards=None):
    app = Workbench(public, guards)
    frame_ancestors = public.frame_ancestors if public else "'none'"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not retain visitor prompts or session identifiers in access logs.

        def _send(self, status, body, content_type='application/json; charset=utf-8', extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors "+frame_ancestors+"; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

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

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {'error': 'Local host only'})
            if self.path in ('/api/health', '/healthz'):  # /healthz is reserved by Google's front end on *.run.app; /api/health reaches the container everywhere
                return self._send(200, {'ok': True, 'public': public is not None})
            if self.path == '/api/config':
                return self._send(200, {'days': app.store.production_days, 'first_day': app.store.first_day,
                                       'last_day': app.store.last_day, 'production_calendar': 'Mon-Fri',
                                       'dataset_hash': app.store.content_hash,
                                       'token': app.token, 'tools': TOOL_SCHEMAS, 'mode': 'offline',
                                       'public': public is not None, 'server_key_available': app.live_model_available,
                                       'live_model_available': app.live_model_available, 'live_model': LIVE_MODEL,
                                       'live_modes': list(app.live_modes), 'live_models': app.live_models,
                                       'demo_guards': app.guards.summary})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/guide': ('guide.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if self.path not in files:
                return self._send(404, {'error': 'Not found'})
            filename, content_type = files[self.path]
            self._send(200, (WEB/filename).read_bytes(), content_type)

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
            if not self._host_ok() or self.path not in ('/api/ask', '/api/report'):
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
                now = time.monotonic()
                while app.requests and app.requests[0] < now-60:
                    app.requests.popleft()
                if len(app.requests) >= 60:
                    return self._send(429, {'error': 'Local request limit reached; retry in a minute'})
                app.requests.append(now)
                if self.path == '/api/ask':
                    app.client_round(self._client_ip())  # a round is counted even if the question is later rejected
                self.connection.settimeout(5)
                payload = json.loads(raw_body)
                api_key = anthropic_key = None
                if public is None:
                    api_key, anthropic_key = self.headers.get('X-OpenAI-Key'), self.headers.get('X-Anthropic-Key')
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
    if public is None:
        server = HTTPServer((host or '127.0.0.1', port), Handler)
    else:
        server = ThreadingHTTPServer((host or '0.0.0.0', port), Handler)
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
