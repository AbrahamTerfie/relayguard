# Automation repair: synthetic webhook-to-CRM demo

This is a local demonstration, not a client case study or a deployed integration. It uses Python's standard library only, no accounts, network calls, keys, or paid services. All addresses use the reserved example.test domain.

## Run

```sh
cd /Users/Abraham/workspace/automation-repair-demo
PYTHONDONTWRITEBYTECODE=1 python3 repair_demo.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
```

## Two-minute demonstration

1. Show the BEFORE failure: a source changed from a flat email field to a nested lead object, breaking the mapping.
2. Run the corrected pipeline: normalize email, quarantine bad events, suppress duplicate deliveries, and retry a simulated rate limit within a fixed budget.
3. Replay the events: the CRM still has only two records.
4. Show tests for schema failure, malformed input, duplicate events, replay, retry recovery, exhausted retries, and permanent failures.

## Honest portfolio description

“I built a synthetic webhook-to-CRM recovery demo showing how I diagnose payload mapping failures, prevent duplicate deliveries, quarantine invalid events, and handle transient errors with bounded retries.”

Do not describe this as paid client work, real uptime improvement, or a Make/Zapier/n8n certification. No such claims have been verified.

## What would change in a real engagement

- First obtain an approved scope, redacted failed-run samples, workflow export, and least-privilege access.
- Back up the original workflow and reproduce the failure in a sandbox.
- Confirm whether the destination truly supports idempotency. This fake CRM does; not all CRMs do.
- Replace the in-memory ledger with durable atomic storage and handle concurrent delivery.
- Add actual backoff with jitter, Retry-After handling, request timeouts, and secret management. Delays here are logged, not slept.
- Validate the actual platform schema and email requirements. The demo's email check is intentionally minimal.
- Review event payload changes that reuse an existing ID, data retention, privacy, safe dead-letter handling, and monitoring.
- Test partial failures, restart/replay, and reconciliation before an approved deployment.

## Prepared next steps

Use this demo to support a narrowly scoped diagnosis offer. Ask for platform, failing step, last successful run, observed versus expected result, and a redacted sample. Quote repairs only after inspecting the failure. Do not request passwords or unredacted customer lists in intake.
