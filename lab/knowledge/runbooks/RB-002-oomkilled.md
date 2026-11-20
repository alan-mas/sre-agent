---
id: RB-002
doc_type: runbook
title: Container OOMKilled (exit code 137)
services: []
alerts: [KubeContainerOOMKilled, KubePodCrashLooping]
scenario: oomkilled
updated: 2026-09-18
related: [PM-002, RB-001, RB-009]
synthetic: true
---

# Container OOMKilled (exit code 137)

## When to use
A container is killed by the kernel for exceeding its memory limit. Symptoms: `Last State: Terminated`, `Reason: OOMKilled`, `Exit Code: 137`; the pod restarts and may end up in `CrashLoopBackOff`. Exit code 137 alone only means SIGKILL: a failed liveness probe also ends in 137. Confirm `Reason: OOMKilled` before using this runbook (otherwise see RB-001).

## Triage
1. Confirm it is the container limit and not node pressure:
   `kubectl -n <ns> get pod <pod> -o jsonpath='{.status.containerStatuses[*].lastState}'`
   `OOMKilled` on the container means its own limit. Pods marked `Evicted` with a `MemoryPressure` message mean the node ran out; use RB-009.
2. Compare usage with the limit:
   `kubectl -n <ns> top pod <pod> --containers` and `kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].resources}'`
3. Ask what grew: input size (a bigger report, a batch at month end), a new release with a leak, or a lowered limit.

## Common causes
| Signal | Likely cause |
|---|---|
| Usage climbs steadily until the limit, then a kill | Real need above the limit, or a leak |
| Kill right after start | The limit is below the process's baseline (e.g. a 64Mi limit for a process that needs ~150Mi) |
| Started after a release | Regression in the new version |

## Mitigation
- **Short term:** a restart only buys time if the input is the same; it will be killed again. If a release caused it, roll back (needs approval).
- **Fix:** raising the memory limit is a **configuration change**. Open a change request for the owning team with the observed peak usage, the current limit and a proposed value with headroom. Never apply it directly from an incident.
- **Leak:** attach the memory graph and the release diff to the change request.

## Escalation
The owning team of the workload.

## Related
RB-009 (node DiskPressure and MemoryPressure evictions), PM-002 (report-worker OOM at month end).
