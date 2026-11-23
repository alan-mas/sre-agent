---
id: PM-016
doc_type: postmortem
title: catalog-web latency spike with its HPA pinned at max during an unannounced event
services: [catalog-web]
alerts: [HighLatency, KubeHpaMaxedOut]
scenario: none
updated: 2026-11-22
related: [RB-014, PM-011, RB-005, RB-013]
synthetic: true
---

# Postmortem: catalog-web latency spike with its HPA pinned at max during an unannounced event

**Status:** Reviewed · **Severity:** SEV-2 · **Synthetic example** (reconstructed from an engineer's past incident; names, times and figures are illustrative)

## Summary
A marketing event that infrastructure had not been told about drove a sharp rise in requests to `catalog-web`. Its HorizontalPodAutoscaler scaled out as designed until it reached `maxReplicas`; from then on every pod ran at 100% CPU and p95/p99 latency spiked. The usual pre-event step, raising the HPA's `minReplicas` and `maxReplicas` before a known peak, never ran because nobody knew the event was coming. Raising both limits restored normal latency.

## Impact
- Elevated p95 and p99 latency on `catalog-web` for the length of the incident (illustrative: about 30 minutes); no outage and no data loss.
- Slower pages for the event's traffic, the users the event was meant to attract.

## Detection
The `HighLatency` alert on `catalog-web` fired on the rise in p95/p99. Time to detect: about 5 min (illustrative).

## Timeline (UTC)
- 14:00 Event traffic starts; requests per second rise sharply.
- 14:05 `HighLatency` fires; APM shows p95 and p99 spiking in step with request volume.
- 14:10 `kubectl get pods` and `kubectl top pods` in the namespace: many pods at 100% CPU. `kubectl get hpa`: replicas at `maxReplicas` (`ScalingLimited=True`), which was unusual.
- 14:15 With load as the only anomaly, the team raises the HPA's `minReplicas` and `maxReplicas` as an emergency change.
- 14:20 The events Slack channel reveals an event nobody had announced to infrastructure; an incident channel is opened to get its expected duration.
- 14:30 New replicas are Ready; latency back to normal. Limits stay raised until the event ends, then are lowered again.

## Root cause
The HPA's `maxReplicas` was sized for normal traffic. The process that raises the HPA limits before planned peaks depends on the events team telling infrastructure in advance, and this event was not communicated, so the HPA hit its ceiling and could not add the capacity the traffic needed. The HPA itself worked as configured.

## What went well
- APM made the link between latency and request volume obvious within minutes.
- Checking the HPA, not only the pods, showed the ceiling instead of a broken service.
- Asking for the event's duration in the incident channel made the scale-down planned, not guessed.

## What went wrong
- Capacity for events depended on a manual, informal heads-up.
- `ScalingLimited=True` at max had no alert of its own; latency was the first signal.

## Where we got lucky
- The event started during a staffed shift, and the nodes had room for the extra replicas; a larger peak would have left pods Pending (RB-004).

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Weekly planning meeting between the events team and infrastructure to review upcoming events | Process | P1 | Infrastructure team | Done |
| Size HPA limits per event from the expected requests (marketing email volume) with DevOps computing the pods needed | Prevent | P1 | DevOps team | Done |
| Alert when an HPA stays at `maxReplicas` (`ScalingLimited=True`) for more than 10 minutes | Detect | P2 | Platform team | Open |
