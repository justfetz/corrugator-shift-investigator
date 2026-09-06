"""Loopback-only learning workbench. Not a public deployment server."""
from collections import OrderedDict, deque
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import hmac
import json
from pathlib import Path
import secrets
import time

from .agent import Agent, Context, TOOL_SCHEMAS
from .analysis import Analysis
from .fixture import generate

WEB = Path(__file__).with_name('web')
DAYS = [(date(2026, 9, 1)+timedelta(days=n)).isoformat() for n in range(30)]


class Workbench:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.sessions = OrderedDict()
        self.datasets = OrderedDict()
        self.requests = deque()

    def close(self):
        for analysis in self.datasets.values():
            analysis.close()

    def ask(self, payload):
        if not isinstance(payload, dict) or set(payload)-{'question', 'day', 'shift', 'session_id', 'corrupt'}:
            raise ValueError('Invalid request fields')
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
        context = self.sessions[session_id]
        # Visible date/shift controls own the scope; same-day follow-ups retain history.
        if context.production_day != day:
            context = Context(day, shift)
            self.sessions[session_id] = context
        context.shift = shift
        self.sessions.move_to_end(session_id)
        key = (day, corrupt)
        if key not in self.datasets:
            if len(self.datasets) >= 3:
                _, evicted = self.datasets.popitem(last=False)
                evicted.close()
            self.datasets[key] = Analysis(generate(seed=7+DAYS.index(day), production_day=day, corrupt=corrupt))
        self.datasets.move_to_end(key)
        result = Agent(self.datasets[key]).ask(question, context)
        result['session_id'] = session_id
        result['report_markdown'] = '\n\n'.join([
            f'# Synthetic production investigation: {day}',
            'Offline rule-based planner. No live model or email delivery.',
            *result['sections'], result['limitations']])
        return result


def make_server(port=8765):
    app = Workbench()
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
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {'error': 'Local host only'})
            if self.path == '/api/config':
                return self._send(200, {'days': DAYS, 'token': app.token, 'tools': TOOL_SCHEMAS,
                                       'mode': 'offline', 'model_connected': False})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if self.path not in files:
                return self._send(404, {'error': 'Not found'})
            filename, content_type = files[self.path]
            self._send(200, (WEB/filename).read_bytes(), content_type)

        def do_POST(self):
            if not self._host_ok() or self.path != '/api/ask':
                return self._send(403, {'error': 'Request not allowed'})
            origin = self.headers.get('Origin')
            if origin is not None and origin != f'http://127.0.0.1:{self.server.server_port}':
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
                self.connection.settimeout(5)
                payload = json.loads(self.rfile.read(length))
                self._send(200, app.ask(payload))
            except (ValueError, UnicodeError, TypeError, KeyError) as exc:
                self._send(400, {'error': str(exc)})
            except TimeoutError:
                self._send(408, {'error': 'Request timed out'})
            except Exception:
                self._send(500, {'error': 'Investigation failed; no answer was fabricated'})
    server = HTTPServer(('127.0.0.1', port), Handler)
    server.app = app
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f'Local workbench: http://127.0.0.1:{server.server_port}', flush=True)
    print('Synthetic data. Offline planner. No model or email service connected.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.close()

if __name__ == '__main__':
    main()
