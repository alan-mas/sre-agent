---
id: PM-005
doc_type: postmortem
title: search-indexer stuck at its CPU limit after a release
services: [search-indexer]
alerts: [CPUThrottlingHigh]
scenario: cpu-hog
updated: 2026-09-23
related: [RB-005]
synthetic: true
---

# Postmortem: search-indexer stuck at its CPU limit after a release

**Status:** Reviewed · **Severity:** SEV-4 · **Synthetic example**

## Summary
A release of `search-indexer` introduced a polling loop without a sleep. The container used ~500m CPU, exactly its 500m limit, and was throttled continuously. Indexing fell behind: new products took 2 hours instead of 2 minutes to appear in search. There were no restarts and no error events.

## Impact
- Search freshness degraded for 7 hours. No errors returned to users.

## Detection
A merchant reported missing products. `CPUThrottlingHigh` had fired but was treated as noise. Time to detect: 6 h.

## Timeline (UTC)
- 09:00 Release deployed.
- 15:00 Merchant report; on-call sees CPU pinned at the limit with a 50m request.
- 15:40 Rollback approved; indexing catches up by 16:10.

## Root cause
A busy loop in the new poller. The low request (50m) also let the scheduler place the pod on an already busy node.

## What went wrong
- Restarting the pod was tried first and changed nothing: the new pod hit the same limit.
- A throttling alert without a freshness SLO was easy to ignore.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Freshness SLO and alert for the search index | Detect | P1 | Search team | Done |
| Raise the CPU request to measured baseline (change request) | Prevent | P3 | Search team | Done |
