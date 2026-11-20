---
id: PM-006
doc_type: postmortem
title: ml-batch jobs Pending with Insufficient cpu
services: [ml-batch]
alerts: [KubePodNotScheduled]
scenario: pending
updated: 2026-10-08
related: [RB-004]
synthetic: true
---

# Postmortem: ml-batch jobs Pending with Insufficient cpu

**Status:** Reviewed · **Severity:** SEV-4 · **Synthetic example**

## Summary
A new `ml-batch` configuration requested 16 CPU per pod, copied from a larger environment. No node in the cluster has 16 allocatable CPU, so every pod stayed `Pending` with `FailedScheduling: 0/3 nodes are available: 3 Insufficient cpu`. The nightly training run did not happen.

## Impact
- One nightly model was not retrained; recommendations used a one-day-old model.

## Detection
Detected the next morning by the ML team. Time to detect: 9 h.

## Timeline (UTC)
- 22:00 Job created; pods `Pending`.
- 07:00 (+1d) ML team notices; on-call compares the 16 CPU request with ~4 allocatable per node.
- 07:30 (+1d) Change request with a request of 3 CPU merged; job runs at 08:00.

## Root cause
Resource requests copied from another environment without checking the target cluster's node sizes.

## What went wrong
- No alert for pods Pending longer than 15 minutes.
- Someone proposed scaling down other workloads to make room; it was rejected because it would not fit 16 CPU anyway.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Alert on pods Pending > 15 min | Detect | P2 | Platform team | Done |
| Admission check: requests must fit the largest node | Prevent | P3 | Platform team | Open |
