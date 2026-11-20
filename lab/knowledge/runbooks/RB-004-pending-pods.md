---
id: RB-004
doc_type: runbook
title: Pods stuck in Pending (FailedScheduling)
services: []
alerts: [KubePodNotScheduled, KubePodNotReady]
scenario: pending
updated: 2026-09-05
related: [PM-006, RB-007]
synthetic: true
---

# Pods stuck in Pending (FailedScheduling)

## When to use
A pod stays `Pending` and never gets a node. Symptoms: `FailedScheduling` warning events from `default-scheduler`.

## Triage
1. Read the scheduler's reason:
   `kubectl -n <ns> describe pod <pod>` → `Events`, e.g. `0/3 nodes are available: 3 Insufficient cpu.`
2. Compare the pod's requests with what the nodes can allocate:
   `kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].resources.requests}'`
   `kubectl describe nodes | grep -A 7 "Allocated resources"`
3. Check the nodes themselves: `kubectl get nodes`. If nodes are `NotReady`, use RB-007.

## Common causes
| Scheduler message | Cause |
|---|---|
| `Insufficient cpu` / `Insufficient memory` | Requests larger than any node's free allocatable (e.g. a pod requesting 16 CPU on nodes with 4) |
| `node(s) had untolerated taint` | Taints without matching tolerations |
| `didn't match Pod's node affinity/selector` | Affinity or `nodeSelector` that no node satisfies |
| `pod has unbound immediate PersistentVolumeClaims` | Storage not provisioned |
| `0/N nodes are available` with nodes NotReady | Node problem, not the pod; see RB-007 |

## Mitigation
- **Requests too large:** the request is a **configuration change**; open a change request with the requested vs allocatable numbers. Adding nodes is a capacity decision for the platform team, not an incident action.
- **Scaling down another workload** to make room is not a fix; do not do it without the owners.

## Escalation
The owning team for requests; the platform team for capacity, taints or storage.

## Related
RB-007 (node NotReady), PM-006 (ml-batch pending).
