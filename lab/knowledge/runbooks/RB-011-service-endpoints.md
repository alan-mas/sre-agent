---
id: RB-011
doc_type: runbook
title: Service routes to the wrong pods or to none
services: []
alerts: [KubeServiceWithNoEndpoints, HighErrorRate]
scenario: none
updated: 2026-08-03
related: [PM-010, RB-008, RB-001]
synthetic: true
---

# Service routes to the wrong pods or to none

## When to use
Clients reach the Service by name (DNS works) but a share of requests fails with `connection refused`, `503` or resets, while the pods behind it look Running and Ready. Typical after a relabel, a canary, a port rename or a second Deployment that reuses labels.

## Triage
1. Compare the Service selector with the pod labels: `kubectl -n <ns> get svc <svc> -o jsonpath='{.spec.selector}'` and `kubectl -n <ns> get pods --show-labels`.
2. List what the Service actually targets: `kubectl -n <ns> get endpointslices -l kubernetes.io/service-name=<svc> -o wide`. Every address should belong to a pod you expect.
3. Check the port mapping: the Service `targetPort` must match a `containerPort` (or its name) on every selected pod.
4. Look for pods that match the selector but are not part of the current release (old canary, debug copy, another team's Deployment).

## Common causes
- Selector too broad: an unrelated or outdated pod receives a share of the traffic.
- `targetPort` points to a port that only some pods expose.
- Selector matches nothing after a label change: the Service has no endpoints at all.

## Mitigation
- Remove the stray pods from the selection by scaling down the unexpected workload (`kubectl -n <ns> scale deploy/<name> --replicas=0`, needs approval).
- Fix the selector or `targetPort` in the manifest through a Pull Request to the owning team; do not edit the live Service.

## Escalation
The team that owns the Service.

## Related
RB-008 (DNS; rule it out first), PM-010 (stray canary pods behind a Service).
