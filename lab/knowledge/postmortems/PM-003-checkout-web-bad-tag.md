---
id: PM-003
doc_type: postmortem
title: checkout-web release with a non-existent image tag
services: [checkout-web]
alerts: [KubeContainerWaiting]
scenario: imagepullbackoff
updated: 2026-06-17
related: [RB-003]
synthetic: true
---

# Postmortem: checkout-web release with a non-existent image tag

**Status:** Reviewed · **Severity:** SEV-3 · **Synthetic example**

## Summary
A release pipeline rendered the image tag from the wrong variable and deployed `checkout-web:9.9.9-does-not-exist`. New pods stayed in `ImagePullBackOff` with "manifest unknown". The old ReplicaSet kept serving, so users saw no errors, but the release was blocked for 50 minutes.

## Impact
- No customer impact (rolling update kept the previous pods).
- A security fix in the release was delayed 50 minutes.

## Detection
`KubeContainerWaiting` after 15 minutes. Time to detect: 15 min.

## Timeline (UTC)
- 14:02 Pipeline deploys revision 7 with the bad tag.
- 14:17 Alert; on-call reads the `Failed` event: "manifest unknown".
- 14:25 Rollback considered unnecessary: revision 6 is still serving. Change request opened for the correct tag.
- 14:52 Release team merges the fix; revision 8 rolls out.

## Root cause
The pipeline read `RELEASE_TAG` from a template default instead of the build output. The tag was never pushed.

## What went well
- `RollingUpdate` with `maxUnavailable: 0` kept the service up.
- Nobody guessed a tag or copied one from another service.

## What went wrong
- The pipeline did not check that the image exists before deploying.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Verify the image digest exists in the registry before deploy | Prevent | P1 | Release team | Done |
| Deploy by digest instead of tag | Prevent | P2 | Release team | Open |
