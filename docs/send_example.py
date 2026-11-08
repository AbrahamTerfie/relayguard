"""Send synthetic data to the local RelayGuard ingress; no external requests."""
import hashlib
import hmac
import json
import os
import time
import urllib.request

body = json.dumps({'event_id': 'sample-001', 'lead': {'email': 'ada@example.test', 'name': 'Ada'}}).encode()
stamp = str(int(time.time()))
secret = os.environ['WEBHOOK_SECRET']
signature = 'sha256=' + hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
request = urllib.request.Request('http://127.0.0.1:8080/webhooks/leads', data=body, headers={
    'Content-Type': 'application/json', 'X-Webhook-Timestamp': stamp, 'X-Webhook-Signature': signature})
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.status, response.read().decode())
