---
id: PM-010
doc_type: postmortem
title: payments-api Service sent part of the traffic to an abandoned canary
services: [payments-api]
alerts: [HighErrorRate]
scenario: none
updated: 2026-08-01
related: [RB-011]
synthetic: true
---

# Postmortem: payments-api Service sent part of the traffic to an abandoned canary

**Status:** Reviewed · **Severity:** SEV-3 · **Synthetic example**

## Summary
A canary Deployment of `payments-api` from a cancelled release was left running with one replica. It carried the same `app: payments-api` label as the stable pods, so the Service balanced requests to it. The canary listened on a renamed port, so roughly a third of the requests were reset. The failures were intermittent and lasted almost two days before they were traced.

## Impact
- About 30% of `payments-api` requests failed for 46 hours; clients retried, so most payments eventually succeeded.
- Checkout latency p99 doubled because of the retries.

## Detection
`HighErrorRate` fired and auto-resolved several times, and the on-call treated it as noise until the second day. Time to detect: 2 days.

## Timeline (UTC)
- 09:00 Canary `payments-api-canary` deployed with one replica; release cancelled at 09:30, canary not removed.
- 09:40 Error rate on `payments-api` settles at ~30%; alert flaps.
- 11:15 (+1d) On-call opens an incident after customer complaints.
- 11:32 (+1d) `get endpointslices` for the Service lists three addresses; one belongs to the canary pod.
- 11:40 (+1d) Canary scaled to zero (approved); errors stop.

## Root cause
The Service selector (`app: payments-api`) also matched the canary, which exposed a different port name than the stable pods, so the share of traffic it received failed.

## What went well
- Listing the endpoint addresses found the stray pod in minutes once someone looked.

## What went wrong
- A flapping alert was treated as noise for a day.
- The release tooling did not clean up the canary on cancel.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Add a `track: stable` label to the Service selector and to stable pods | Prevent | P1 | Payments team | Done |
| Delete canaries automatically when a release is cancelled | Prevent | P2 | Platform team | Open |
| Page on error rate sustained for 15 minutes instead of flapping alerts | Detect | P2 | Platform team | Done |
