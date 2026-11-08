"""Offline, synthetic webhook repair demo. No network, credentials, or clients."""
from dataclasses import dataclass
import json

EVENTS = [
    {'event_id': 'evt-101', 'lead': {'email': ' ADA@EXAMPLE.TEST ', 'name': 'Ada'}},
    {'event_id': 'evt-101', 'lead': {'email': ' ADA@EXAMPLE.TEST ', 'name': 'Ada'}},
    {'event_id': 'evt-102', 'lead': {'email': 'lin@example.test', 'name': 'Lin'}},
    {'event_id': 'evt-103', 'lead': {'name': 'Missing email'}},
    {'event_id': 'evt-104', 'lead': {'email': 'invalid', 'name': 'Bad email'}},
]

@dataclass
class UpstreamError(Exception):
    status: int

class FakeCRM:
    def __init__(self, transient_failures=None):
        self.rows = {}
        self.attempts = {}
        self.failures = dict(transient_failures or {})

    def create(self, key, lead):
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if self.failures.get(key, 0):
            self.failures[key] -= 1
            raise UpstreamError(429)
        # Simulated CRM supports a stable idempotency key.
        self.rows.setdefault(key, lead)
        return self.rows[key]

def broken_flow(events):
    crm = FakeCRM()
    for event in events:
        # Demonstrates an old flat-schema mapping after payload nesting changed.
        crm.create(event['event_id'], {'email': event['email']})
    return crm.rows

def repaired_flow(events, crm, ledger=None, max_attempts=3):
    if max_attempts < 1:
        raise ValueError('max_attempts must be positive')
    ledger = set() if ledger is None else ledger
    audit = []
    for event in events:
        key = event.get('event_id')
        lead = event.get('lead')
        if not isinstance(key, str) or not key.strip() or not isinstance(lead, dict):
            audit.append({'event_id': key, 'status': 'quarantined', 'reason': 'invalid envelope'})
            continue
        if key in ledger:
            audit.append({'event_id': key, 'status': 'duplicate_skipped'})
            continue
        email = lead.get('email')
        email = email.strip().lower() if isinstance(email, str) else ''
        if email.count('@') != 1 or any(c.isspace() for c in email) or not all(email.split('@')):
            audit.append({'event_id': key, 'status': 'quarantined', 'reason': 'invalid email'})
            continue
        for attempt in range(1, max_attempts + 1):
            try:
                crm.create(key, {'email': email, 'name': str(lead.get('name', '')).strip()})
            except UpstreamError as error:
                retryable = error.status == 429 or 500 <= error.status < 600
                if retryable and attempt < max_attempts:
                    audit.append({'event_id': key, 'status': 'retry_scheduled', 'attempt': attempt,
                                  'simulated_delay_seconds': 2 ** (attempt - 1)})
                    continue
                audit.append({'event_id': key, 'status': 'failed', 'attempts': attempt, 'code': error.status})
                break
            else:
                ledger.add(key)
                audit.append({'event_id': key, 'status': 'created', 'attempts': attempt})
                break
    return audit

if __name__ == '__main__':
    print('SYNTHETIC OFFLINE DEMO: webhook to CRM repair')
    try:
        broken_flow(EVENTS)
    except KeyError as error:
        print(f'BEFORE: workflow stops at missing old-schema field {error}.')
    crm, ledger = FakeCRM({'evt-102': 1}), set()
    audit = repaired_flow(EVENTS, crm, ledger)
    print('AFTER:', json.dumps({'crm_records': len(crm.rows), 'audit': audit}, indent=2))
    repaired_flow(EVENTS, crm, ledger)
    print(f'REPLAY: still {len(crm.rows)} CRM records; no duplicated deliveries.')
