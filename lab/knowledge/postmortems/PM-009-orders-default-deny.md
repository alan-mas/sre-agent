---
id: PM-009
doc_type: postmortem
title: orders-api lost access to inventory-api after a default-deny rollout
services: [orders-api, inventory-api]
alerts: [HighErrorRate]
scenario: none
updated: 2026-09-18
related: [RB-012, RB-008]
synthetic: true
---

# Postmortem: orders-api lost access to inventory-api after a default-deny rollout

**Status:** Reviewed · **Severity:** SEV-2 · **Synthetic example**

## Summary
The platform team rolled out a default-deny NetworkPolicy to the `inventory` namespace as part of a hardening project. The allow rule for `orders-api` selected the namespace by name instead of by label, so it matched nothing. Calls from `orders-api` to `inventory-api` timed out for 41 minutes and orders could not reserve stock.

## Impact
- 41 minutes in which new orders failed at the stock reservation step.
- About 2,300 orders retried by customers; no data loss.

## Detection
`HighErrorRate` on `orders-api` fired 5 minutes after the rollout. Time to detect: 5 min.

## Timeline (UTC)
- 10:00 Default-deny policy and allow rules applied to `inventory` by the GitOps pipeline.
- 10:05 Error rate on `orders-api` rises; errors are `context deadline exceeded` calling `inventory-api`.
- 10:09 On-call checks `inventory-api`: pods Ready, endpoints present, `nslookup` works.
- 10:18 A second responder notices the errors are timeouts, not `connection refused`, and asks what changed in `inventory`.
- 10:34 The allow rule is found to use `namespaceSelector: {name: orders}`; the namespace has no such label.
- 10:41 Revert of the policy merged and synced (approved); errors stop at 10:46.

## Root cause
The allow rule's `namespaceSelector` matched a label the `orders` namespace does not have, so the default-deny dropped all traffic from `orders-api`.

## What went well
- Checking endpoints and DNS early ruled out the Service and the resolver.

## What went wrong
- 25 minutes before anyone tied the timeouts to the policy rollout in the change log.
- The policy was not tested against real traffic before reaching production.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Test NetworkPolicies in staging with a connectivity check between declared clients and servers | Prevent | P1 | Platform team | In progress |
| Select namespaces by `kubernetes.io/metadata.name` in every policy template | Prevent | P2 | Platform team | Done |
| Show recent policy changes on the service dashboard | Detect | P3 | Platform team | Open |
