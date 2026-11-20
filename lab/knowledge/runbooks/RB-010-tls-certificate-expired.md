---
id: RB-010
doc_type: runbook
title: TLS certificate expired or expiring at the gateway
services: [gateway]
alerts: [SSLCertExpiringSoon, ProbeFailing]
scenario: none
updated: 2026-05-20
related: [PM-008, RB-008]
synthetic: true
---

# TLS certificate expired or expiring at the gateway

## When to use
Clients fail the TLS handshake against the public gateway. Symptoms: browsers show "certificate has expired"; clients log `x509: certificate has expired or is not yet valid`; external probes fail while pods are healthy and Ready.

## Triage
1. Check the served certificate:
   `openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -dates`
2. Find the Secret the Gateway listener references: `kubectl get gateway -A -o yaml` → `certificateRefs`.
3. If cert-manager issues it: `kubectl get certificate -A` and the `Ready` condition and events of the `Certificate`.

## Common causes
- Automatic renewal failed (ACME challenge blocked, DNS record changed).
- A manually managed certificate nobody tracked.
- The Secret was renewed but the listener references another one.

## Mitigation
- Renew through the issuer the platform team owns; a manual certificate is a change request, never pasted from an incident channel.
- Add an expiry alert at 21 and 7 days if it was missing.

## Escalation
The platform team.

## Related
RB-008 (DNS, which also breaks ACME challenges), PM-008 (gateway certificate expiry).
