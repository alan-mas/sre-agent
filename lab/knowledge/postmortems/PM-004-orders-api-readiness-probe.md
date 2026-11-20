---
id: PM-004
doc_type: postmortem
title: orders-api rollout stuck on a readiness probe port
services: [orders-api]
alerts: [KubeDeploymentRolloutStuck]
scenario: bad-rollout
updated: 2026-08-26
related: [RB-006]
synthetic: true
---

# Postmortem: orders-api rollout stuck on a readiness probe port

**Status:** Reviewed · **Severity:** SEV-3 · **Synthetic example**

## Summary
Revision 2 of `orders-api` moved the readiness probe to port `9797`, where nothing listens. New pods ran but never became Ready, and the rollout stopped with `ProgressDeadlineExceeded`. Revision 1 kept serving at reduced capacity until a rollback.

## Impact
- 25 minutes at half capacity; p95 latency on order creation rose from 180 ms to 640 ms.

## Detection
`KubeDeploymentRolloutStuck` 10 minutes after the deploy (the progress deadline). Time to detect: 10 min.

## Timeline (UTC)
- 10:00 Revision 2 deployed; new pods `0/1 READY`, "Readiness probe failed: connection refused".
- 10:10 `ProgressDeadlineExceeded`; alert fires.
- 10:16 On-call compares `rollout history --revision=1` and `--revision=2`: only the probe port changed.
- 10:21 `rollout undo` approved with justification "revision 2 never became Ready; probe points to :9797". Recovery at 10:25.
- 11:30 Change request with the correct probe merged by the orders team.

## Root cause
A refactor renamed the metrics port and the probe was updated to the wrong one. No test checked the probe against the container's listening ports.

## What went well
- Comparing revisions found the diff in minutes.
- The rollback was approved with a written justification and verified with `rollout status`.

## What went wrong
- `maxSurge` let half the replicas be replaced before the deadline.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| CI check: probe ports must be declared container ports | Prevent | P1 | Orders team | Done |
| Lower `progressDeadlineSeconds` to 300 | Detect | P2 | Orders team | Done |
