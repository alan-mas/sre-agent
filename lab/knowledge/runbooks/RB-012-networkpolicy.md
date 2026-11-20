---
id: RB-012
doc_type: runbook
title: NetworkPolicy dropping traffic between workloads
services: []
alerts: [HighErrorRate, KubeProbeTimeouts]
scenario: none
updated: 2026-09-21
related: [PM-009, RB-008, RB-011]
synthetic: true
---

# NetworkPolicy dropping traffic between workloads

## When to use
Traffic between two workloads stops after a policy change: client connections **time out** (packets are silently dropped), they are not refused. Both sides are Running and Ready, the name resolves and the Service has endpoints. DNS itself can break the same way if egress to port 53 is blocked.

## Triage
1. Confirm it is a drop, not a refusal: a timeout points to policy or network; `connection refused` points to the process or the port.
2. List policies on both sides: `kubectl -n <client-ns> get networkpolicy` and `kubectl -n <server-ns> get networkpolicy`, then `describe` the recent ones.
3. Check the direction: the server namespace needs an **ingress** rule from the client, and the client namespace, if it has a default-deny, needs an **egress** rule to the server and to DNS (UDP and TCP 53 to `kube-system`).
4. Check the selectors in the rules: `namespaceSelector` uses namespace labels (e.g. `kubernetes.io/metadata.name`), not the namespace name.
5. Compare with the change log: the start of the errors usually matches a policy rollout.

## Common causes
- A namespace-wide default-deny without the matching allow rules.
- An allow rule with a `podSelector` or `namespaceSelector` that matches nothing.
- Egress to DNS forgotten, so name lookups time out too.

## Mitigation
- Revert the policy change through the GitOps Pull Request that introduced it, or apply the missing allow rule after review (`kubectl apply`, needs approval).

## Escalation
The platform team (network policies) and the team that owns the policy.

## Related
RB-008 (DNS), RB-011 (Service endpoints), PM-009 (default-deny rollout).
