# RelayGuard

A runnable, standard-library Python service that receives signed lead webhooks, durably queues them in SQLite, and delivers normalized contacts to a configured HTTP CRM endpoint. It includes bounded retries, crash recovery, duplicate suppression and dead-letter replay.

**Status:** working integration foundation, not a hosted service or a claim of client work. All bundled examples use synthetic data. No live CRM has been connected. The original offline repair demonstration remains in `repair_demo.py`, `test_repair_demo.py`, and [its original guide](docs/original-demo.md).

## Quick start: prove it locally

Python 3.11+; no runtime dependencies or accounts needed for tests.

```sh
python3 -m unittest discover -v
python3 repair_demo.py
python3 -m relayguard --help
python3 -m relayguard status
```

The tests include a genuine loopback HTTP webhook → SQLite → HTTP CRM flow, alongside concurrency, crashes, retry exhaustion, authentication and redirect protection. They do not send data to external services.

## Run an integration

1. Configure a destination that accepts `POST {"email":"…","name":"…"}` with a Bearer token and **durably honors `Idempotency-Key`**. This is a generic adapter contract, not a ready-made HubSpot/Salesforce connector.
2. Export `WEBHOOK_SECRET` (at least 32 random characters), `CRM_URL` (HTTPS), and `CRM_TOKEN` through your secret manager or local environment. Do not put them in Git. The program does not auto-load `.env`.
3. Run ingress and worker separately using the same absolute database path:

```sh
python3 -m relayguard --db /path/to/private/data/relayguard.sqlite3 serve --port 8080
python3 -m relayguard --db /path/to/private/data/relayguard.sqlite3 worker
```

The ingress binds **127.0.0.1 only**. Before exposing it, use a trusted TLS reverse proxy with request timeouts, rate limits and a body limit of 64 KiB. The built-in HTTP server is intentionally a local integration service, not a hardened public edge.

### Webhook protocol

`POST /webhooks/leads`, `Content-Type: application/json`:

```json
{"event_id":"evt-001","lead":{"email":"ada@example.test","name":"Ada"}}
```

- `X-Webhook-Timestamp`: Unix seconds; accepted within five minutes.
- `X-Webhook-Signature`: `sha256=` followed by hex HMAC-SHA256 over `timestamp + "." + raw_body`, using `WEBHOOK_SECRET`.
- Event IDs: 1–128 ASCII letters, digits, `.`, `_`, `:`, `-`.
- 202 = durably queued; 200 = same ID and same normalized content; 409 = ID reused with changed normalized content; 401 = bad/stale signature; 422 = invalid event; 413 = oversized body.
- Unknown fields are ignored. Email is trimmed and lowercased, name trimmed. Email validation checks shape, not deliverability. Invalid requests are rejected without retaining customer bodies.

[Send a signed synthetic request](docs/send_example.py) with `WEBHOOK_SECRET` set, while ingress is running:

```sh
python3 docs/send_example.py
```

## Operations

```sh
python3 -m relayguard --db /path/to/queue.sqlite3 status
python3 -m relayguard --db /path/to/queue.sqlite3 dead
python3 -m relayguard --db /path/to/queue.sqlite3 replay evt-001
python3 -m relayguard --db /path/to/queue.sqlite3 worker --once --max-attempts 5
```

`dead` emits IDs, attempt counts and HTTP codes, never contact payloads. Replay is an explicit operator action that resets the retry budget for a dead event. Correct credential/configuration problems first. Replaying a delivery with an uncertain outcome is safe only if the destination enforces idempotency.

## Reliability model

- SQLite WAL with atomic ingestion and claim transactions; multiple worker processes can share one database on one host. Do not use a network filesystem.
- At-least-once delivery, **not exactly once**. A crash after the CRM accepts but before the local acknowledgment can repeat a request. The remote idempotency contract is essential.
- Same event ID and normalized content is deduplicated across restarts. Changed content under an existing ID is rejected.
- A 60-second lease allows crash recovery; generation fencing prevents an old worker from overwriting a newer claim. Outbound timeout is 10 seconds.
- 2xx succeeds; 408, 429, 5xx and network failures retry. Other responses dead-letter immediately. Redirects are not followed so credentials cannot leak through a redirect.
- Retries use exponential backoff with jitter and Retry-After seconds/date support (bounded to 24 hours). Default maximum is five attempts, including reclaimed crashed attempts. Configure all workers with the same budget.

## Security and deployment limits

Read [the runbook](docs/operations.md) before real data. No real customer data, keys, production credentials or deployment were used in this project. Do not describe test results as production uptime or client outcomes.

The queue contains contact PII in plaintext. Keep it on encrypted local storage with restricted access and approved retention. New CLI-created files use owner-only permissions; existing files are not chmod'ed automatically. Logs are deliberately quiet. IDs must not contain PII. No public admin API exists; commands require local filesystem access.

Not yet provided: provider-specific adapters/signature schemes, automatic schema migrations, encryption at rest, retention automation, a dashboard, distributed hosting, hosted metrics or production load testing. Existing providers may need an authenticated translation layer because their signing formats differ. This project must be validated against the selected CRM before deployment.

## Development

```sh
python3 -m unittest discover -v
python3 -m compileall -q relayguard
```

A GitHub Actions template for Python 3.11–3.14 is included in `docs/github-actions-test.yml`. CI is not active: the current GitHub OAuth grant cannot write workflow files. An authorized maintainer can copy the template to `.github/workflows/test.yml` later. There are no third-party runtime dependencies. Source is private by default; no open-source license has been granted.
