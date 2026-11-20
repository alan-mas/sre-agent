---
id: RB-003
doc_type: runbook
title: ImagePullBackOff / ErrImagePull
services: []
alerts: [KubeContainerWaiting]
scenario: imagepullbackoff
updated: 2026-08-27
related: [PM-003, RB-006, RB-008]
synthetic: true
---

# ImagePullBackOff / ErrImagePull

## When to use
A pod cannot start because the node cannot pull its image. Symptoms: status `ErrImagePull`, then `ImagePullBackOff`; a `Failed` event "Failed to pull image".

## Triage
1. Read the exact error in the events:
   `kubectl -n <ns> describe pod <pod>` (section `Events`) or
   `kubectl -n <ns> get events --field-selector involvedObject.name=<pod>`
2. Read the image reference in the spec: `kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].image}'`

## Common causes
| Message in the event | Cause |
|---|---|
| `manifest unknown`, `not found` | The tag does not exist in the registry (typo, tag never pushed, wrong pipeline variable) |
| `unauthorized`, `authentication required` | Missing or expired `imagePullSecret` |
| `i/o timeout`, `no such host` for the registry | Network or DNS from the node to the registry; see RB-008 |
| `toomanyrequests` | Registry rate limit |

## Mitigation
- **Tag does not exist:** if a previous revision works, roll back (needs approval); the previous pods keep serving meanwhile if the rollout strategy is `RollingUpdate`. The correct tag is a **configuration change**: open a change request for the owning team. Never guess a tag or copy one from another workload.
- **Credentials:** the owning team rotates the pull secret; do not create secrets from an incident.

## Escalation
The owning team; the platform team for registry or network issues.

## Related
RB-006 (stuck rollout), RB-008 (DNS), PM-003 (checkout-web wrong tag).
