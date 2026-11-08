# Operator runbook

## Before real data

- Obtain the data owner's authorization for the exact source, destination and fields.
- Validate the CRM's idempotency retention period and behavior for duplicate keys, conflicting payloads and timeout-after-acceptance. A header alone does not create idempotency.
- Map this adapter's JSON to the actual CRM API in a sandbox. CRM APIs often require different envelopes, OAuth handling, or upserts.
- Use separate source-signing and destination tokens with least privilege; never put secrets in source, issue descriptions or shell arguments.
- Keep ingress on loopback behind a TLS proxy. Configure client/header/body deadlines, concurrency/rate limits and maximum body size at the proxy. This stdlib server processes requests sequentially and is not suitable as an unprotected public server.
- Synchronize host time for signature verification. Monitor `/healthz` for process liveness only; it does not assert CRM availability or database health. Monitor `status` counts and `dead` locally for delivery health.
- Restrict access to the data directory, encrypt storage, set a retention policy and use SQLite's online backup API. Do not copy only the main database while WAL writes are active.

## Failure recovery

1. Check status and dead letters without copying customer payloads into tickets.
2. 401/403: inspect destination token authorization securely. 400/422: inspect a redacted schema sample in an approved environment. 429/5xx/network: review destination availability and retry budget.
3. Fix the cause and replay only the chosen event ID. `replay` only changes dead events; it does not replay successful deliveries or alter payloads.
4. If a provider accepted a request before a timeout, reconcile by idempotency key before any manual create. Never assume local failure means remote failure.
5. If a worker crashes, another can reclaim after 60 seconds. Stale worker completion cannot overwrite a newer generation. SQLite files must remain on the same local host.

## Retention and upgrades

No automated deletion is implemented. Decide retention before collecting PII; deleting delivered records also removes duplicate detection for those IDs. Expire source replay windows consistently with destination idempotency retention. Backups contain the same PII and need the same controls.

The initial schema has no migration framework. Back up the database and test upgrades on a copy. Do not assume a future version can open this database unchanged.

## What is verified

Automated tests exercise local ingestion and delivery, validation, duplicate/conflicting IDs, concurrency, crash leases, generation fencing, retry limits, replay, HMAC timestamps and redirects. They use synthetic example.test contacts and temporary databases. No live CRM, cloud deployment, penetration test or load test has been performed.
