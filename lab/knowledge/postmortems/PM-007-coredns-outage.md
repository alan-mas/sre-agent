---
id: PM-007
doc_type: postmortem
title: CoreDNS outage caused crash loops across services
services: [coredns, payments-api, orders-api]
alerts: [CoreDNSDown, KubePodCrashLooping]
scenario: none
updated: 2026-07-14
related: [RB-001, RB-008]
synthetic: true
---

# Postmortem: CoreDNS outage caused crash loops across services

**Status:** Reviewed · **Severity:** SEV-2 · **Synthetic example**

## Summary
A malformed `Corefile` change made both CoreDNS pods fail to start. Services could not resolve each other: `payments-api` and `orders-api` logged connection errors at startup and entered `CrashLoopBackOff`, although their databases were healthy. Resolved by reverting the ConfigMap after 27 minutes.

## Impact
- 27 minutes of partial outage on payments and orders.

## Detection
`KubePodCrashLooping` for two services within a minute; `CoreDNSDown` fired 4 minutes later. Time to detect: 2 min.

## Timeline (UTC)
- 16:00 `Corefile` change applied directly with `kubectl edit`.
- 16:02 Crash loops on `payments-api` and `orders-api`.
- 16:05 First responder follows the crash-loop runbook and checks `payments-db`: healthy, endpoints present.
- 16:12 Logs show `no such host` rather than `connection refused`; attention moves to DNS.
- 16:15 `nslookup payments-db.payments.svc.cluster.local` from a debug pod times out; the `kube-dns` Service has no ready endpoints; CoreDNS logs show the `Corefile` parse error.
- 16:22 ConfigMap reverted; CoreDNS restarted (approved). Services recover by 16:27.

## Root cause
A syntax error in the `Corefile`, applied without review.

## What went well
- Checking the dependency's endpoints quickly ruled out the database.

## What went wrong
- The first 10 minutes treated it as an application incident. Several services crash-looping at once points to shared infrastructure, not one dependency.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| CoreDNS config only through reviewed Pull Requests | Prevent | P1 | Platform team | Done |
| Add "many services at once → check DNS" to the crash-loop runbook | Mitigate | P2 | Platform team | Done |
