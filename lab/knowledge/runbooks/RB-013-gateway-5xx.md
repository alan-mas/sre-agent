---
id: RB-013
doc_type: runbook
title: Gateway returns 502, 503 or 504 while backends look healthy
services: [envoy-gateway]
alerts: [GatewayHigh5xxRate, GatewayUpstreamLatencyHigh]
scenario: none
updated: 2026-10-02
related: [PM-011, RB-005, RB-010]
synthetic: true
---

# Gateway returns 502, 503 or 504 while backends look healthy

## When to use
External users get `502`, `503` or `504` from the gateway, or latency rises at the edge, while the backend pods are Running and calls inside the cluster succeed. The gateway sits between users and the Service, so its own limits and timeouts can fail requests the backend would have served.

## Triage
1. Read the status code: `504` = the upstream did not answer within the route timeout; `503` = no healthy upstream or the gateway shed load; `502` = the upstream reset or closed the connection.
2. Compare latency at the gateway with latency at the backend (gateway access logs `duration` vs application metrics). If the backend is slow too, the gateway is reporting it, not causing it.
3. Check the backend for saturation: CPU throttling (RB-005), connection pool limits, replica count vs traffic.
4. Check the gateway itself: Envoy pod CPU and memory, `kubectl -n envoy-gateway-system top pods`, and the route and backend timeouts in the `HTTPRoute` and `BackendTrafficPolicy`.
5. A certificate problem shows as a TLS error in the browser, not as a 5xx: see RB-010.

## Common causes
- Backend too slow under load (throttled or too few replicas); the route timeout turns slowness into `504`.
- Route timeout shorter than a legitimate slow request.
- Gateway pods saturated or too few for the traffic.

## Mitigation
- Add backend capacity (`kubectl -n <ns> scale deploy/<name> --replicas=<n>`, needs approval) when the backend is saturated.
- Adjust timeouts or gateway replicas through a Pull Request to the platform team.

## Escalation
The platform team (gateway) and the team that owns the backend.

## Related
RB-005 (CPU throttling), RB-010 (TLS at the gateway), PM-011 (504s during a sale).
