---
id: PM-015
doc_type: postmortem
title: Intermittent 5-second delays on in-cluster name lookups
services: [coredns, orders-api]
alerts: [CoreDNSLatencyHigh]
scenario: none
updated: 2026-10-09
related: [RB-008, PM-007]
synthetic: true
---

# Postmortem: Intermittent 5-second delays on in-cluster name lookups

**Status:** Reviewed · **Severity:** SEV-3 · **Synthetic example**

## Summary
For a week, a small share of requests from `orders-api` took 5 seconds longer than normal. CoreDNS was up and answered most queries in milliseconds. Parallel A and AAAA queries from the same socket occasionally lost one reply to a conntrack race on the node, and the client resolver waited for its 5-second timeout before retrying. Unlike the CoreDNS outage (PM-007), nothing crashed and no alert pointed at DNS at first.

## Impact
- About 1% of `orders-api` requests delayed by 5 s for 7 days; p99 latency went from 300 ms to 5.3 s.

## Detection
A latency SLO burn alert on `orders-api` after several days of slow burn. Time to detect: 6 days.

## Timeline (UTC)
- 09:00 Node image upgrade rolled out (kernel and conntrack defaults changed).
- 09:00 (+6d) SLO burn alert on `orders-api` p99 latency.
- 10:20 (+6d) Traces show the extra 5 s before the first outbound call, during name resolution.
- 11:05 (+6d) `CoreDNSLatencyHigh` threshold lowered temporarily; the CoreDNS dashboard shows timeouts from a few nodes only.
- 13:30 (+6d) Packet capture on a node shows a dropped DNS reply under parallel A/AAAA queries.
- 15:00 (+6d) `single-request-reopen` added to the pod `dnsConfig` and NodeLocal DNSCache scheduled; delays stop after the rollout.

## Root cause
A conntrack race on the upgraded nodes dropped one of two parallel DNS replies; the client waited for the full 5-second resolver timeout before retrying.

## What went well
- Distributed tracing isolated the delay to name resolution.

## What went wrong
- A latency problem with a fixed 5-second signature went six days without investigation.
- The node upgrade had no DNS latency check.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Deploy NodeLocal DNSCache on all nodes | Prevent | P1 | Platform team | In progress |
| Add DNS lookup latency p99 to the node upgrade checks | Detect | P2 | Platform team | Open |
| Alert on CoreDNS p99 latency, not only on errors | Detect | P2 | Platform team | Done |
