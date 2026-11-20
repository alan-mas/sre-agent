---
id: PM-011
doc_type: postmortem
title: checkout-web returned 504 at the gateway during a flash sale
services: [checkout-web, envoy-gateway]
alerts: [GatewayHigh5xxRate]
scenario: none
updated: 2026-10-24
related: [RB-013, RB-005, PM-005]
synthetic: true
---

# Postmortem: checkout-web returned 504 at the gateway during a flash sale

**Status:** Reviewed · **Severity:** SEV-2 · **Synthetic example**

## Summary
Traffic tripled at the start of a flash sale. `checkout-web` had a fixed replica count and a tight CPU limit; its response time rose above the gateway's 5-second route timeout and the gateway answered `504` to about 12% of requests. No pod restarted and in-cluster health checks stayed green, which delayed the diagnosis.

## Impact
- 33 minutes with ~12% of checkout page loads failing with `504`.
- Estimated 4% of sale revenue lost.

## Detection
`GatewayHigh5xxRate` fired 3 minutes after the sale started. Time to detect: 3 min.

## Timeline (UTC)
- 18:00 Sale starts; requests per second triple.
- 18:03 Gateway alert fires; on-call sees all `checkout-web` pods Running and Ready.
- 18:10 On-call checks the certificate (recent PM-008) and the gateway pods: both fine.
- 18:19 Application metrics show p95 latency of 6 s and CPU pinned at the limit on every pod.
- 18:24 `checkout-web` scaled from 4 to 12 replicas (approved).
- 18:33 Latency back under 1 s; `504` rate returns to zero.

## Root cause
Too few replicas, each at its CPU limit, made responses slower than the gateway route timeout. The gateway reported the backend's slowness as `504`.

## What went well
- Scaling out took effect within minutes.

## What went wrong
- No load test before the sale.
- Health probes passed throughout, so "pods are healthy" misled the first 15 minutes.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Load test checkout before every planned sale | Prevent | P1 | Checkout team | Open |
| Autoscale `checkout-web` on CPU and request rate | Mitigate | P1 | Checkout team | In progress |
| Dashboard with gateway latency next to backend latency | Detect | P2 | Platform team | Done |
