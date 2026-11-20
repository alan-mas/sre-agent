---
id: PM-012
doc_type: postmortem
title: notifications-worker crash loop after a database credential rotation
services: [notifications-worker, notifications-db]
alerts: [KubePodCrashLooping]
scenario: none
updated: 2026-07-29
related: [RB-001, PM-001]
synthetic: true
---

# Postmortem: notifications-worker crash loop after a database credential rotation

**Status:** Reviewed · **Severity:** SEV-3 · **Synthetic example**

## Summary
The scheduled rotation of the `notifications-db` user updated the database and the external secret store, but the Kubernetes Secret is only refreshed hourly. New `notifications-worker` pods started with the old value, were rejected by Postgres and exited at startup. Email and SMS notifications were delayed for 52 minutes. The database itself was healthy the whole time.

## Impact
- 52 minutes of delayed notifications (queued, then delivered); no messages lost.

## Detection
`KubePodCrashLooping` fired 7 minutes after the rotation. Time to detect: 7 min.

## Timeline (UTC)
- 03:00 Credential rotation job completes for `notifications-db`.
- 03:02 A node drain reschedules `notifications-worker`; new pods start with the cached Secret.
- 03:07 Alert fires; logs show `FATAL: password authentication failed for user "notifications"`.
- 03:15 On-call follows the crash-loop runbook, checks `notifications-db` endpoints: present, database accepting connections.
- 03:31 On-call notices the error is an authentication failure, not `connection refused`, and checks the Secret's last sync time.
- 03:48 Secret re-synced from the secret store (approved); pods restarted (approved).
- 03:54 Workers healthy; queue drained by 04:20.

## Root cause
The rotation changed the credential before the Kubernetes Secret could follow, and pods that started in between used a value the database no longer accepted.

## What went well
- The log line named the cause precisely once it was read carefully.

## What went wrong
- The first responder assumed a database outage because a similar crash loop (PM-001) had been one.
- Rotation and Secret sync were not coordinated.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Rotate with two valid credentials (dual-user) so old and new both work during the sync window | Prevent | P1 | Platform team | In progress |
| Trigger a Secret sync as the last step of the rotation job | Prevent | P2 | Platform team | Done |
| Add "authentication failed vs connection refused" to the crash-loop runbook triage | Process | P3 | SRE team | Open |
