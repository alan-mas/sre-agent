---
id: PM-014
doc_type: postmortem
title: report-api pods stuck in Terminating during a release
services: [report-api]
alerts: [KubeDeploymentReplicasMismatch]
scenario: none
updated: 2026-09-05
related: [RB-006]
synthetic: true
---

# Postmortem: report-api pods stuck in Terminating during a release

**Status:** Reviewed · **Severity:** SEV-4 · **Synthetic example**

## Summary
A release of `report-api` completed, but the old pods stayed in `Terminating` for up to 10 minutes. A `preStop` hook waited for in-flight report exports to finish, and `terminationGracePeriodSeconds` had been raised to 600 to allow it. One pod also kept a finalizer from a backup operator that was no longer installed, so it never went away on its own. The old pods held node capacity and database connections and delayed a second release by 25 minutes.

## Impact
- No user impact; two releases delayed by 25 minutes.

## Detection
The release engineer saw old pods in `kubectl get pods` after the rollout reported success. Time to detect: 4 min.

## Timeline (UTC)
- 14:00 Release starts; new pods Ready by 14:03.
- 14:04 Old pods enter `Terminating` and stay there.
- 14:10 `describe pod`: grace period 600 s, `preStop` hook still running; logs show it waiting for long exports.
- 14:14 All old pods but one exit when their exports finish.
- 14:20 The last pod still has `metadata.finalizers: [backup.example.com/protect]`; the operator that removes it was uninstalled last month.
- 14:29 Finalizer removed by the platform team (approved); pod deleted.

## Root cause
A `preStop` hook bound to long-running work, with a 600-second grace period, made old pods linger by design; an orphaned finalizer kept one pod forever.

## What went well
- `describe pod` showed both the grace period and the finalizer.

## What went wrong
- Nobody knew the grace period had been raised to 600 s.
- Uninstalling the backup operator left its finalizers behind.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Move report exports to a Job so web pods can stop in under 60 s | Prevent | P2 | Reports team | Open |
| Remove an operator's finalizers as part of its uninstall procedure | Process | P2 | Platform team | Done |
| Alert on pods in Terminating for more than 15 minutes | Detect | P3 | Platform team | Open |
