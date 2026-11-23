---
id: RB-014
doc_type: runbook
title: HorizontalPodAutoscaler does not scale out under load
services: []
alerts: [KubeHpaMaxedOut, KubeHpaReplicasMismatch, HighLatency]
scenario: hpa-no-requests
updated: 2026-11-22
related: [PM-016, PM-011, RB-005, RB-013]
synthetic: true
---

# HorizontalPodAutoscaler does not scale out under load

## When to use
Load or latency rises but the replica count stays flat: the HPA exists, yet `kubectl get hpa` shows the target as `<unknown>`, the replicas stuck at `minReplicas`, or pinned at `maxReplicas` while CPU stays high.

## Triage
1. Read the HPA conditions: `kubectl -n <ns> describe hpa <name>`. `ScalingActive=False` means it cannot compute a desired count; `ScalingLimited=True` means it wants more (or fewer) replicas than min/max allow; `AbleToScale=False` means it cannot change the target's scale.
2. If the reason is `FailedGetResourceMetric` with `missing request for cpu`: utilization is usage divided by the **request**, so a container without a CPU request has no denominator. Check `kubectl -n <ns> get deploy <name> -o jsonpath='{.spec.template.spec.containers[*].resources}'`. Setting only limits defaults the request to the limit and hides this.
3. If the reason says no metrics were returned for any pod: check the metrics pipeline (`kubectl top pods -n <ns>`; `kubectl -n kube-system get deploy metrics-server`). A metrics-server outage stops every HPA in the cluster at once.
4. If `ScalingLimited=True` at `maxReplicas`: the HPA works and capacity is the ceiling. Compare the max with the traffic, the node capacity (pending pods, RB-004) and the quota.
5. Check `scaleTargetRef`: it must name the Deployment that serves the traffic (a renamed Deployment leaves the HPA pointing at nothing).

## Common causes
- No CPU (or memory) request on a container of the target pods.
- metrics-server down or not yet serving metrics after a restart.
- `maxReplicas` set for normal traffic, too low for a peak.
- New pods cannot schedule, so desired replicas never become ready replicas.

## Mitigation
- Add capacity now with a manual scale (`kubectl -n <ns> scale deploy/<name> --replicas=<n>`, needs approval); the HPA takes over again once its metrics work, so raise its `minReplicas` too if the scale must hold.
- Add the missing requests or raise `maxReplicas` through a Pull Request to the team that owns the workload; both are configuration changes.
- Restore metrics-server through the platform team.

## Escalation
The team that owns the workload (requests, min/max); the platform team for metrics-server or node capacity.

## Related
PM-016 (HPA at max during an unannounced event), PM-011 (fixed replica count during a sale), RB-005 (CPU throttling), RB-013 (gateway 5xx under load), RB-004 (pending pods).
