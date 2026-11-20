---
id: RB-006
doc_type: runbook
title: Deployment rollout stuck (ProgressDeadlineExceeded)
services: []
alerts: [KubeDeploymentRolloutStuck, KubeDeploymentReplicasMismatch]
scenario: bad-rollout
updated: 2026-10-21
related: [PM-004, RB-001, RB-003]
synthetic: true
---

# Deployment rollout stuck (ProgressDeadlineExceeded)

## When to use
A new revision of a Deployment never becomes Ready, so the rollout stops. Symptoms: condition `Progressing=False` with reason `ProgressDeadlineExceeded`; new pods `Running` but `0/1 READY`; readiness probe failures in events; the old ReplicaSet keeps serving traffic.

## Triage
1. Confirm the stuck rollout:
   `kubectl -n <ns> rollout status deploy/<name> --timeout=10s`
   `kubectl -n <ns> get deploy <name> -o jsonpath='{.status.conditions}'`
2. See what changed between revisions:
   `kubectl -n <ns> rollout history deploy/<name>` and `kubectl -n <ns> rollout history deploy/<name> --revision=<n>`
3. Read why new pods are not Ready: `kubectl -n <ns> describe pod <new-pod>` → `Readiness probe failed: ... connection refused`.

## Common causes
| Signal | Cause |
|---|---|
| `Readiness probe failed: connection refused` on a port nothing listens on | The new revision changed the probe port or path (e.g. probe moved to `:9797`) |
| New pods crash | Use RB-001 |
| New pods `ImagePullBackOff` | Use RB-003 |

## Mitigation
- **Roll back to the last good revision:** `kubectl -n <ns> rollout undo deploy/<name>` (needs approval and a justification). This is the safe operational fix: it restores the known-good spec.
- The fix to the probe or the release is a **configuration change** for the owning team: open a change request with the diff between revisions. Do not edit the probe in place.
- Verify after the rollback: `rollout status` succeeds and all replicas are Ready.

## Escalation
The owning team; the release owner if the change came from CI.

## Related
RB-001, RB-003, PM-004 (orders-api readiness probe).
