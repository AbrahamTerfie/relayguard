"""Validation, authenticated ingestion, durable queue and bounded delivery."""
import hashlib
import hmac
import json
import random
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


class InvalidEvent(ValueError):
    pass


class Conflict(ValueError):
    pass


def normalize(event):
    if not isinstance(event, dict):
        raise InvalidEvent('event must be an object')
    key, lead = event.get('event_id'), event.get('lead')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', key):
        raise InvalidEvent('invalid event_id')
    if not isinstance(lead, dict):
        raise InvalidEvent('lead must be an object')
    email = lead.get('email')
    if not isinstance(email, str):
        raise InvalidEvent('email is required')
    email = email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise InvalidEvent('invalid email')
    name = lead.get('name', '')
    if not isinstance(name, str) or len(name) > 200:
        raise InvalidEvent('invalid name')
    return key, {'email': email, 'name': name.strip()}


def verify_signature(body, timestamp, signature, secret, now=None):
    """Signature is sha256=<hex HMAC(timestamp + '.' + raw request bytes)>."""
    now = time.time() if now is None else now
    if not timestamp or not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        return False
    if abs(now - int(timestamp)) > 300:
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not signature.isascii():
        return False
    return hmac.compare_digest(expected, signature)


class Queue:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            available REAL NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0,
            generation INTEGER NOT NULL DEFAULT 0, last_code INTEGER, created REAL NOT NULL)''')

    def close(self):
        self.db.close()

    def enqueue(self, event):
        key, payload = normalize(event)
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior = self.db.execute('SELECT digest FROM events WHERE event_id=?', (key,)).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise Conflict('event_id already has different content')
                result = 'duplicate'
            else:
                self.db.execute("INSERT INTO events(event_id,payload,digest,status,created) VALUES(?,?,?,'pending',?)", (key, encoded, digest, time.time()))
                result = 'queued'
            self.db.execute('COMMIT')
            return result
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def claim(self, now=None, lease_seconds=60, max_attempts=5):
        now = time.time() if now is None else now
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE events SET status='dead' WHERE status='processing' AND lease<=? AND attempts>=?", (now, max_attempts))
            row = self.db.execute("SELECT * FROM events WHERE (status='pending' AND available<=?) OR (status='processing' AND lease<=?) ORDER BY created LIMIT 1", (now, now)).fetchone()
            if row:
                self.db.execute("UPDATE events SET status='processing', attempts=attempts+1, generation=generation+1, lease=? WHERE event_id=?", (now + lease_seconds, row['event_id']))
                row = self.db.execute('SELECT * FROM events WHERE event_id=?', (row['event_id'],)).fetchone()
            self.db.execute('COMMIT')
            return dict(row) if row else None
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def finish(self, row, status, code, available=0):
        # A stale worker must not overwrite a new claim after its lease expires.
        return self.db.execute("UPDATE events SET status=?, last_code=?, available=?, lease=0 WHERE event_id=? AND generation=? AND status='processing'", (status, code, available, row['event_id'], row['generation'])).rowcount == 1

    def stats(self):
        return {r['status']: r['n'] for r in self.db.execute('SELECT status,COUNT(*) AS n FROM events GROUP BY status')}

    def dead(self):
        return [dict(r) for r in self.db.execute("SELECT event_id,attempts,last_code FROM events WHERE status='dead' ORDER BY created")]

    def replay(self, event_id):
        return self.db.execute("UPDATE events SET status='pending',attempts=0,available=0 WHERE event_id=? AND status='dead'", (event_id,)).rowcount == 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPDestination:
    """Generic JSON CRM adapter; requires a destination honoring Idempotency-Key."""
    def __init__(self, url, token, allow_local=False):
        parsed = urlsplit(url)
        local = parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
            raise ValueError('invalid destination URL')
        if parsed.scheme != 'https' and not (allow_local and local and parsed.scheme == 'http'):
            raise ValueError('HTTPS required (explicit localhost exception for tests)')
        if not token or '\n' in token or '\r' in token:
            raise ValueError('destination token required')
        self.url, self.token = url, token
        self.opener = urllib.request.build_opener(NoRedirect())

    def send(self, key, payload):
        req = urllib.request.Request(self.url, data=payload.encode(), method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token,
            'Idempotency-Key': key})
        try:
            with self.opener.open(req, timeout=10) as response:
                return response.status, response.headers.get('Retry-After')
        except urllib.error.HTTPError as error:
            code, retry = error.code, error.headers.get('Retry-After')
            error.close()
            return code, retry
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, None


def retry_delay(attempt, retry_after, now):
    delay = min(300, 2 ** min(attempt, 8)) + random.uniform(0, 1)
    if retry_after:
        try:
            value = float(retry_after)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(retry_after)
                value = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - now
            except (ValueError, TypeError, OverflowError):
                value = 0
        if value > 0:
            delay = max(delay, min(value, 86400))
    return delay


def work_once(queue, destination, max_attempts=5, now=None):
    if max_attempts < 1:
        raise ValueError('max_attempts must be positive')
    now = time.time() if now is None else now
    row = queue.claim(now=now, max_attempts=max_attempts)
    if not row:
        return False
    code, retry_after = destination.send(row['event_id'], row['payload'])
    retryable = code in (0, 408, 429) or 500 <= code < 600
    status = 'delivered' if 200 <= code < 300 else ('pending' if retryable and row['attempts'] < max_attempts else 'dead')
    queue.finish(row, status, code, now + retry_delay(row['attempts'], retry_after, now) if status == 'pending' else 0)
    return True
