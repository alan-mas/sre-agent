---
id: PM-001
doc_type: postmortem
title: payments-api crash loop after payments-db maintenance
services: [payments-api, payments-db]
alerts: [KubePodCrashLooping]
scenario: crashloop
updated: 2026-03-11
related: [RB-001]
synthetic: true
---

# Postmortem: payments-api crash loop after payments-db maintenance

**Status:** Reviewed · **Severity:** SEV-2 · **Synthetic example**

## Summary
During a planned maintenance window, `payments-db` was scaled to zero for a storage migration and not scaled back. `payments-api` could not connect to Postgres at `payments-db:5432`, exited at startup and entered `CrashLoopBackOff`. Card payments failed for 38 minutes.

## Impact
- 38 minutes of failed payment requests (HTTP 503 at the gateway).
- ~4% of checkout attempts in the window abandoned.

## Detection
`KubePodCrashLooping` fired 6 minutes after the first crash. Time to detect: 6 min. Customer support reports arrived 2 minutes later.

## Timeline (UTC)
- 02:00 Maintenance starts; `payments-db` StatefulSet scaled to 0.
- 02:14 Migration finishes; the runbook step "scale back to 1" is skipped.
- 02:15 `payments-api` deploy (unrelated) restarts pods; new pods fail with `connection refused` to `payments-db:5432`.
- 02:21 Alert fires; on-call restarts `payments-api` twice with no effect.
- 02:41 On-call reads `logs --previous`, checks `get endpoints payments-db`: empty.
- 02:49 `payments-db` scaled back to 1; `payments-api` recovers by itself at 02:53.

## Root cause
The database had zero replicas, so the Service had no endpoints. `payments-api` treats a failed database connection at startup as fatal and exits, which turns a dependency outage into a crash loop.

## Trigger
A skipped manual step in the maintenance checklist.

## What went well
- `logs --previous` showed the exact error on the first read.

## What went wrong
- 20 minutes spent restarting the crashing pod, which cannot help while the dependency is down.
- The maintenance checklist had no verification step.

## Where we got lucky
- The maintenance ran at low traffic.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Add "verify endpoints are non-empty" to the maintenance checklist | Prevent | P1 | Platform team | Done |
| `payments-api`: retry the DB connection with exponential back-off instead of exiting | Mitigate | P2 | Payments team | Done |
| `payments-api`: report the database in the readiness probe, not the liveness probe, so a DB outage takes pods out of traffic without restarting them | Mitigate | P2 | Payments team | Open |
| Alert on Services with zero endpoints | Detect | P2 | Platform team | Open |
