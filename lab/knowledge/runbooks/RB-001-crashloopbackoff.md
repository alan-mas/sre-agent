---
id: RB-001
doc_type: runbook
title: Pod in CrashLoopBackOff
services: []
alerts: [KubePodCrashLooping]
scenario: crashloop
updated: 2026-10-02
related: [PM-001, PM-007, RB-002, RB-008]
synthetic: true
---

# Pod in CrashLoopBackOff

## When to use
A container starts, exits, and Kubernetes restarts it with growing back-off delays. Symptoms: pod status `CrashLoopBackOff` or `Error`, the restart count keeps rising, `BackOff` warning events ("Back-off restarting failed container").

## Triage
1. Confirm the loop and the exit code:
   `kubectl -n <ns> get pod <pod> -o wide` and `kubectl -n <ns> describe pod <pod>` (look at `Last State: Terminated`, `Exit Code`, `Reason`).
2. Read the logs of the **previous** container, not the current one, which may have just started:
   `kubectl -n <ns> logs <pod> --previous --tail=100`
3. Check what changed recently: `kubectl -n <ns> rollout history deploy/<name>`, recent ConfigMap or Secret edits, and the health of dependencies. Correlate the first crash with those changes before proposing a rollback.
4. Check how many services are affected: several workloads crash-looping at once points to shared infrastructure (DNS, network, a common dependency), not to each application.

## Common causes
| Signal | Likely cause | Next step |
|---|---|---|
| Exit code 1 and `connection refused` to a database or another service | The dependency is down or not reachable on that host and port | Check the dependency's pods and Service endpoints: `kubectl -n <ns> get endpoints <svc>`. If the endpoints are empty, the dependency is the incident. |
| Exit code 1 and a missing env var or config key | Bad or missing ConfigMap/Secret | Compare the pod spec with the last working revision. |
| Exit code 137 with `Reason: OOMKilled` | Killed for exceeding its memory limit | Use RB-002 (OOMKilled). |
| Exit code 137 or 143 with `Liveness probe failed` events | The kubelet kills a container that fails its liveness probe (wrong port or path, too short a timeout, slow startup without a startup probe) | Compare the probe with the ports the container listens on; check `initialDelaySeconds` and whether a `startupProbe` is needed. 137 alone only means SIGKILL: always confirm the termination `Reason` and events. |
| Exit code 0, restarts anyway | The process finishes instead of serving (wrong command) | Check `command`/`args` in the spec. |
| `no such host` in the logs | DNS resolution, not the dependency | Use RB-008 (DNS resolution failures). |

## Mitigation
- **Dependency down:** fix or restore the dependency first; restarting the crashing pod does not help while the dependency is down. Once it is back, the back-off resolves on its own within a few minutes, or restart the pod to skip the wait.
- **Bad release:** roll back with `kubectl -n <ns> rollout undo deploy/<name>` (needs approval).
- **Bad configuration:** do not edit config in place. Open a change request (Pull Request) for the owning team with the diff and the evidence.

## Escalation
The owning team of the workload; the owning team of the dependency if endpoints are empty.

## Related
RB-002 (OOMKilled), RB-008 (DNS), PM-001 (payments-api crash loop), PM-007 (CoreDNS outage).
