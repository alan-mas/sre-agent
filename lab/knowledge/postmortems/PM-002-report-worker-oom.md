---
id: PM-002
doc_type: postmortem
title: report-worker OOMKilled during month-end reports
services: [report-worker]
alerts: [KubeContainerOOMKilled]
scenario: oomkilled
updated: 2026-05-01
related: [RB-002]
synthetic: true
---

# Postmortem: report-worker OOMKilled during month-end reports

**Status:** Reviewed · **Severity:** SEV-3 · **Synthetic example**

## Summary
At month end, `report-worker` processed a report about three times larger than usual. Each attempt allocated ~150Mi against a 64Mi memory limit, was killed with `OOMKilled` (exit code 137) and retried. Monthly reports were delivered 5 hours late.

## Impact
- Month-end reports for all customers delayed 5 hours. No data loss.

## Detection
Detected by a customer question 3 hours in; the OOM alert existed but routed to a muted channel. Time to detect: 3 h.

## Timeline (UTC)
- 00:05 Month-end job starts; first `OOMKilled`.
- 03:10 Customer asks about missing reports.
- 03:25 On-call finds `Last State: OOMKilled`, exit 137, limit 64Mi, peak usage ~150Mi.
- 03:40 Change request opened to raise the limit to 256Mi; reviewed and merged by the owning team at 04:30.
- 05:05 Reports delivered.

## Root cause
The memory limit was sized from an average day, not from the month-end peak. The worker loads the whole report in memory.

## What went well
- The change request carried the peak usage and the limit, so the review took minutes.

## What went wrong
- The alert went to a muted channel.
- A pod restart was tried first; it was killed again with the same input.

## Where we got lucky
- The retry policy kept retrying instead of dropping the job.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Route OOMKilled alerts to on-call | Detect | P1 | Platform team | Done |
| Size limits from p99 monthly peak, not the average | Prevent | P2 | Reports team | Done |
| Stream report generation instead of loading it whole | Prevent | P3 | Reports team | Open |
