"""Loopback webhook ingress. Put a TLS reverse proxy in front for deployment."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from .core import Queue, Conflict, InvalidEvent, verify_signature


def handler(db_path, secret):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log request URLs, signatures or customer payloads.

        def respond(self, status, result):
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.respond(200 if self.path == '/healthz' else 404, {'status': 'ok' if self.path == '/healthz' else 'not_found'})

        def do_POST(self):
            self.connection.settimeout(5)
            if self.path != '/webhooks/leads':
                return self.respond(404, {'error': 'not_found'})
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                return self.respond(400, {'error': 'content_length_required'})
            try:
                length = int(self.headers['Content-Length'])
            except (ValueError, TypeError):
                return self.respond(400, {'error': 'invalid_length'})
            if not 0 < length <= 65536:
                return self.respond(413, {'error': 'body_size_limit'})
            if self.headers.get_content_type() != 'application/json':
                return self.respond(415, {'error': 'json_required'})
            try:
                body = self.rfile.read(length)
            except (TimeoutError, OSError):
                return self.respond(408, {'error': 'read_timeout'})
            if len(body) != length:
                return self.respond(400, {'error': 'incomplete_body'})
            if not verify_signature(body, self.headers.get('X-Webhook-Timestamp'), self.headers.get('X-Webhook-Signature'), secret):
                return self.respond(401, {'error': 'invalid_signature'})
            try:
                event = json.loads(body)
                queue = Queue(db_path)
                try:
                    result = queue.enqueue(event)
                finally:
                    queue.close()
            except (InvalidEvent, UnicodeDecodeError, json.JSONDecodeError):
                return self.respond(422, {'error': 'invalid_event'})
            except Conflict:
                return self.respond(409, {'error': 'event_id_conflict'})
            self.respond(202 if result == 'queued' else 200, {'status': result})
    return Handler


def serve(db_path, secret, port):
    server = HTTPServer(('127.0.0.1', port), handler(db_path, secret))
    server.serve_forever()
