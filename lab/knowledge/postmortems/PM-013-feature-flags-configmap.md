---
id: PM-013
doc_type: postmortem
title: search-api kept serving an old feature flag after a ConfigMap update
services: [search-api]
alerts: []
scenario: none
updated: 2026-08-19
related: [RB-006]
synthetic: true
---

# Postmortem: search-api kept serving an old feature flag after a ConfigMap update

**Status:** Reviewed · **Severity:** SEV-4 · **Synthetic example**

## Summary
To switch off an expensive ranking feature, the search team changed `ranking.enabled` to `false` in the `search-api-flags` ConfigMap. The file is mounted with `subPath`, which never receives ConfigMap updates, and the application reads it only at startup anyway. Pods kept the feature on for 3 hours, until a manual restart.

## Impact
- 3 hours of higher search latency and cost than intended; no user-facing errors.

## Detection
Noticed by an engineer comparing the cost dashboard with the change. Time to detect: 2 h 40 min.

## Timeline (UTC)
- 13:00 ConfigMap updated through a merged Pull Request.
- 13:05 Team expects the flag to take effect; the dashboard shows no change.
- 15:40 Engineer asks why ranking cost is unchanged; `kubectl exec` shows the old file contents in the pod.
- 15:52 Team finds the `subPath` mount in the Deployment.
- 16:00 `rollout restart` of `search-api` (approved); new pods read the new value.

## Root cause
A `subPath` volume mount copies the file once at container start, and the application caches configuration at startup, so a ConfigMap change needs a new rollout to take effect.

## What went well
- `kubectl exec ... cat` showed the stale file directly.

## What went wrong
- Nobody verified the change in the running pods.
- The team assumed mounted ConfigMaps always refresh.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Put a hash of the ConfigMap in the pod template annotations so a config change triggers a rollout | Prevent | P2 | Search team | Done |
| Expose the active flag values on a `/config` debug endpoint | Detect | P3 | Search team | Open |
