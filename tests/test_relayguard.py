import hashlib
import hmac
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from relayguard.core import (Queue, Conflict, InvalidEvent, HTTPDestination,
                            normalize, retry_delay, verify_signature, work_once)
from relayguard.server import handler

EVENT = {'event_id': 'evt-1', 'lead': {'email': ' ADA@EXAMPLE.TEST ', 'name': 'Ada'}}
SECRET = 'test-secret-not-a-credential-' * 2

class Destination:
    def __init__(self, code=200, retry=None):
        self.code, self.retry, self.calls = code, retry, []
    def send(self, key, payload):
        self.calls.append((key, json.loads(payload)))
        return self.code, self.retry

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.db')
        self.q = Queue(self.path)
    def tearDown(self):
        self.q.close()
        self.tmp.cleanup()
    def test_validation(self):
        self.assertEqual(normalize(EVENT)[1]['email'], 'ada@example.test')
        for event in [None, [], {}, {'event_id': '../x', 'lead': {}}, {'event_id': 'x', 'lead': {'email': 'bad'}}]:
            with self.assertRaises(InvalidEvent): normalize(event)
    def test_durable_duplicate(self):
        self.assertEqual(self.q.enqueue(EVENT), 'queued')
        other = Queue(self.path)
        try: self.assertEqual(other.enqueue(EVENT), 'duplicate')
        finally: other.close()
    def test_conflicting_id(self):
        self.q.enqueue(EVENT)
        with self.assertRaises(Conflict):
            self.q.enqueue({'event_id': 'evt-1', 'lead': {'email': 'other@example.test'}})
    def test_success_does_not_repeat(self):
        self.q.enqueue(EVENT)
        dst = Destination()
        self.assertTrue(work_once(self.q, dst))
        self.assertFalse(work_once(self.q, dst))
        self.assertEqual(self.q.stats(), {'delivered': 1})
        self.assertEqual(len(dst.calls), 1)
    def test_bounded_retry_and_replay(self):
        self.q.enqueue(EVENT)
        dst = Destination(429, '30')
        work_once(self.q, dst, max_attempts=2, now=100)
        self.assertFalse(work_once(self.q, dst, max_attempts=2, now=110))
        work_once(self.q, dst, max_attempts=2, now=131)
        self.assertEqual(self.q.dead()[0]['attempts'], 2)
        self.assertTrue(self.q.replay('evt-1'))
        self.assertFalse(self.q.replay('evt-1'))
        work_once(self.q, Destination(), now=132)
        self.assertEqual(self.q.stats(), {'delivered': 1})
    def test_permanent_failure(self):
        self.q.enqueue(EVENT)
        work_once(self.q, Destination(401))
        self.assertEqual(self.q.dead()[0]['last_code'], 401)
    def test_network_retry(self):
        self.q.enqueue(EVENT)
        work_once(self.q, Destination(0), now=100)
        self.assertEqual(self.q.stats(), {'pending': 1})
    def test_lease_and_fencing(self):
        self.q.enqueue(EVENT)
        first = self.q.claim(now=10)
        self.assertIsNone(self.q.claim(now=20))
        second = self.q.claim(now=71)
        self.assertFalse(self.q.finish(first, 'delivered', 200))
        self.assertTrue(self.q.finish(second, 'delivered', 200))
    def test_crashed_attempt_budget(self):
        self.q.enqueue(EVENT)
        self.q.claim(now=10, max_attempts=1)
        self.assertIsNone(self.q.claim(now=71, max_attempts=1))
        self.assertEqual(self.q.stats(), {'dead': 1})
    def test_concurrent_ingestion(self):
        results = []
        def add():
            queue = Queue(self.path)
            try: results.append(queue.enqueue(EVENT))
            finally: queue.close()
        threads = [threading.Thread(target=add) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results.count('queued'), 1)
        self.assertEqual(results.count('duplicate'), 5)
    def test_signature_window_and_body(self):
        body = b'{"test":1}'
        signature = 'sha256=' + hmac.new(SECRET.encode(), b'100.' + body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, '100', signature, SECRET, now=100))
        self.assertFalse(verify_signature(body, '100', signature, SECRET, now=401))
        self.assertFalse(verify_signature(b'changed', '100', signature, SECRET, now=100))
        self.assertFalse(verify_signature(body, '100', 'é', SECRET, now=100))
    def test_retry_after(self):
        self.assertEqual(retry_delay(1, '120', 0), 120)
        self.assertEqual(retry_delay(1, 'Thu, 01 Jan 1970 00:02:00 GMT', 0), 120)
        self.assertGreaterEqual(retry_delay(1, 'invalid', 0), 2)
    def test_url_restrictions(self):
        for url in ['http://example.test', 'file:///etc/passwd', 'https://user:pass@example.test']:
            with self.assertRaises(ValueError): HTTPDestination(url, 'test')

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.db')
        self.server = HTTPServer(('127.0.0.1', 0), handler(self.path, SECRET))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/webhooks/leads'
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()
    def post(self, value, sign=True):
        body = json.dumps(value).encode()
        stamp = str(int(time.time()))
        signature = 'sha256=' + hmac.new(SECRET.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(self.url, data=body, headers={'Content-Type': 'application/json', 'X-Webhook-Timestamp': stamp, 'X-Webhook-Signature': signature if sign else 'invalid'})
        try:
            with urllib.request.urlopen(req) as response: return response.status
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            return code
    def test_ingest_duplicate_conflict_invalid_auth(self):
        self.assertEqual(self.post(EVENT, False), 401)
        self.assertEqual(self.post(EVENT), 202)
        self.assertEqual(self.post(EVENT), 200)
        self.assertEqual(self.post({'event_id': 'evt-1', 'lead': {'email': 'other@example.test'}}), 409)
        self.assertEqual(self.post([]), 422)
    def test_end_to_end_real_http_adapter(self):
        received = []
        class CRM(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                received.append((self.headers['Idempotency-Key'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(201)
                self.end_headers()
        crm = HTTPServer(('127.0.0.1', 0), CRM)
        thread = threading.Thread(target=crm.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(self.post(EVENT), 202)
            q = Queue(self.path)
            try:
                dest = HTTPDestination(f'http://127.0.0.1:{crm.server_port}', 'test-token', allow_local=True)
                self.assertTrue(work_once(q, dest))
                self.assertEqual(q.stats(), {'delivered': 1})
            finally: q.close()
            self.assertEqual(received, [('evt-1', {'email': 'ada@example.test', 'name': 'Ada'})])
        finally:
            crm.shutdown()
            crm.server_close()
            thread.join()
    def test_adapter_does_not_follow_redirect(self):
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', 'https://example.test/must-not-follow')
                self.end_headers()
        server = HTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            dest = HTTPDestination(f'http://127.0.0.1:{server.server_port}', 'test-token', allow_local=True)
            self.assertEqual(dest.send('evt-1', '{}')[0], 302)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
