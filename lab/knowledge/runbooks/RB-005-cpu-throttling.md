---
id: RB-005
doc_type: runbook
title: CPU throttling at the limit
services: []
alerts: [CPUThrottlingHigh]
scenario: cpu-hog
updated: 2026-10-14
related: [PM-005, RB-002]
synthetic: true
---

# CPU throttling at the limit

## When to use
A container uses all the CPU its limit allows and the kernel throttles it. Symptoms: CPU usage flat at the limit (e.g. ~500m with `limits.cpu: 500m`), higher latency or slow batch progress, often **no restarts and no error events**: throttling is silent in pod status.

## Triage
1. Compare usage with the limit:
   `kubectl -n <ns> top pod <pod> --containers` and the pod's `resources`.
   Usage pinned at exactly the limit is the signature of throttling.
2. Check the request: a tiny request (e.g. 50m) with a high limit means the scheduler packed the pod onto a busy node.
3. Decide whether the load is legitimate (more traffic, bigger index) or a bug (a busy loop after a release).

## Common causes
| Signal | Cause |
|---|---|
| Pinned at the limit right after a release | Regression: busy loop, retry storm, missing sleep |
| Pinned at the limit with traffic growth | Undersized limit for real load |
| High node CPU, many pods slow | Noisy neighbor; requests too low across pods |

## Mitigation
- **Regression:** roll back (needs approval).
- **Real load:** scaling out replicas is an operational remediation (needs approval) if the service is stateless. Changing CPU requests or limits is a **configuration change**: open a change request with usage, limit and latency data.
- Restarting a throttled pod does not help: the new pod hits the same limit.

## Escalation
The owning team of the workload.

## Related
RB-002 (memory), PM-005 (search-indexer throttling).
