"""Learning workbench server.

Default: loopback only, for one person on one computer. With PUBLIC_DEMO_ORIGIN
set, it runs as the offline synthetic demo behind a hosting platform's TLS proxy:
binds 0.0.0.0:$PORT, allows only the configured browser origins, never accepts
visitor API keys, and serves investigations one at a time under a lock.
"""
from collections import OrderedDict, deque
from datetime import date, timedelta
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

from .agent import Agent, Context, TOOL_SCHEMAS
from .analysis import Analysis
from .fixture import generate

WEB = Path(__file__).with_name('web')
DAYS = [(date(2026, 9, 1)+timedelta(days=n)).isoformat() for n in range(30)]
LIVE_MODEL = 'gpt-4.1-mini-2025-04-14'
# Per-visitor token bucket on POST: a visitor gets VISITOR_BURST requests at once,
# then one more every VISITOR_REFILL_SECONDS. The service-wide 60/min window still applies.
VISITOR_BURST = 10
VISITOR_REFILL_SECONDS = 6.0
VISITOR_TABLE_LIMIT = 4096


class VisitorLimiter:
    """Token bucket per client address, in memory, bounded in size."""
    def __init__(self, burst=VISITOR_BURST, refill_seconds=VISITOR_REFILL_SECONDS, size=VISITOR_TABLE_LIMIT):
        self.burst, self.refill_seconds, self.size = burst, refill_seconds, size
        self.buckets = OrderedDict()   # address -> [tokens, last_refill]
        self.lock = threading.Lock()

    def allow(self, address, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            tokens, last = self.buckets.pop(address, (self.burst, now))
            tokens = min(self.burst, tokens + (now-last)/self.refill_seconds)
            allowed = tokens >= 1
            self.buckets[address] = [tokens-1 if allowed else tokens, now]
            while len(self.buckets) > self.size:
                self.buckets.popitem(last=False)
            return allowed


def visitor_id(address, salt=None):
    """Stable, non-reversible identifier for abuse signals (OpenAI safety_identifier). Never the raw address."""
    salt = salt if salt is not None else os.environ.get('VISITOR_ID_SALT', 'corrugator-shift-investigator')
    return hashlib.sha256(f'{salt}|{address}'.encode()).hexdigest()[:32]


def budget_path():
    """Where the model-budget ledger lives; a mounted volume keeps it across container restarts."""
    return Path(os.environ.get('MODEL_BUDGET_PATH', 'artifacts/model-budget.sqlite3'))


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
    def __init__(self, public=None):
        self.public = public
        self.lock = threading.Lock()  # investigations and PDFs run one at a time; static files do not wait
        self.token = secrets.token_urlsafe(32)
        self.sessions = OrderedDict()
        self.datasets = OrderedDict()
        self.requests = deque()
        self.visitors = VisitorLimiter()
        self.reports = OrderedDict()
        from .periods import PeriodReports
        self.periods = PeriodReports()

    @property
    def live_model_available(self):
        if self.public is None:
            return bool(os.environ.get('OPENAI_API_KEY'))
        return self.public.live_model and bool(os.environ.get('OPENAI_API_KEY'))

    def close(self):
        for analysis in self.datasets.values():
            analysis.close()

    def ask(self, payload, api_key=None, visitor=None):
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
        if mode not in ('offline', 'openai'):
            raise ValueError('Choose offline or OpenAI mode')
        if self.public is not None:
            api_key = None  # a hosted demo never uses a visitor's key, even if one is sent
            if mode == 'openai' and not self.live_model_available:
                raise ValueError('This public demo runs the free offline planner only')
        if mode == 'openai' and not (api_key or os.environ.get('OPENAI_API_KEY')):
            raise ValueError('OpenAI mode needs your key in the local key field or server environment')
        day, shift = payload.get('day'), payload.get('shift')
        if day not in DAYS or type(shift) is not int or shift not in (1,2,3):
            raise ValueError('Select a supported production day and shift')
        question = payload.get('question')
        if not isinstance(question, str) or not question.strip() or len(question)>2000:
            raise ValueError('Enter a question of 1-2000 characters')
        corrupt = payload.get('corrupt', False)
        if type(corrupt) is not bool:
            raise ValueError('Invalid data-quality option')
        session_id = payload.get('session_id')
        if session_id is not None and (not isinstance(session_id, str) or session_id not in self.sessions):
            raise ValueError('Session expired; reset the conversation')
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
            self.datasets[key] = Analysis(generate(seed=7+DAYS.index(day), production_day=day, corrupt=corrupt))
        self.datasets.move_to_end(key)
        if mode == 'openai':
            from .openai_provider import OpenAIPlanner, Budget
            planner = OpenAIPlanner(api_key or os.environ.get('OPENAI_API_KEY'), Budget(budget_path()),
                                    safety_identifier=visitor if self.public is not None else None)
            try:
                result = Agent(self.datasets[key], planner=planner, max_calls=10, max_seconds=60).ask(question, context)
            finally:
                planner.close()
        else:
            result = Agent(self.datasets[key]).ask(question, context)
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


def make_server(port=8765, public=None, host=None):
    app = Workbench(public)
    frame_ancestors = public.frame_ancestors if public else "'none'"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not retain visitor prompts or session identifiers in access logs.

        def _send(self, status, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
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

        def _origin_ok(self):
            origin = self.headers.get('Origin')
            if public is not None:
                return origin is not None and origin.rstrip('/') in public.origins
            return origin is None or origin == f'http://127.0.0.1:{self.server.server_port}'

        def _visitor(self):
            # Behind the platform proxy the socket peer is the proxy; the client is the first
            # X-Forwarded-For hop, which Cloud Run sets itself. Locally the header is untrusted.
            if public is not None:
                forwarded = self.headers.get('X-Forwarded-For', '')
                first = forwarded.split(',')[0].strip()
                if first and len(first) <= 64:
                    return first
            return self.client_address[0]

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {'error': 'Local host only'})
            if self.path in ('/api/health', '/healthz'):  # /healthz is reserved by Google's front end on *.run.app; /api/health reaches the container everywhere
                return self._send(200, {'ok': True, 'public': public is not None})
            if self.path == '/api/config':
                return self._send(200, {'days': DAYS, 'token': app.token, 'tools': TOOL_SCHEMAS, 'mode': 'offline',
                                       'public': public is not None, 'server_key_available': app.live_model_available,
                                       'live_model_available': app.live_model_available, 'live_model': LIVE_MODEL})
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
                visitor = self._visitor()
                if not app.visitors.allow(visitor):
                    return self._send(429, {'error': 'Too many requests from this address; retry shortly'})
                now = time.monotonic()
                while app.requests and app.requests[0] < now-60:
                    app.requests.popleft()
                if len(app.requests) >= 60:
                    return self._send(429, {'error': 'Local request limit reached; retry in a minute'})
                app.requests.append(now)
                self.connection.settimeout(5)
                payload = json.loads(raw_body)
                api_key = None if public is not None else self.headers.get('X-OpenAI-Key')
                with app.lock:
                    if self.path == '/api/report':
                        body = app.pdf(payload)
                    else:
                        body = app.ask(payload, api_key=api_key, visitor=visitor_id(visitor))
                self._send(200, body, 'application/pdf' if self.path == '/api/report' else 'application/json; charset=utf-8')
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
    if public is None:
        server = make_server(args.port or 8765)
        print(f'Local workbench: http://127.0.0.1:{server.server_port}', flush=True)
        print('Synthetic data. Offline by default; OpenAI mode requires a key. No email delivery.', flush=True)
    else:
        server = make_server(args.port or int(os.environ.get('PORT', '7860')), public=public)
        live = 'server key with the spend cap' if server.app.live_model_available else 'offline planner only'
        print(f'Public synthetic demo on 0.0.0.0:{server.server_port} for {", ".join(public.origins)}; '
              f'embeddable by {public.frame_ancestors}; {live}. No visitor keys, no email.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.close()

if __name__ == '__main__':
    main()
