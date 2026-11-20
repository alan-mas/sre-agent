---
id: PM-008
doc_type: postmortem
title: Public gateway certificate expired
services: [gateway]
alerts: [ProbeFailing]
scenario: none
updated: 2026-04-02
related: [RB-010]
synthetic: true
---

# Postmortem: Public gateway certificate expired

**Status:** Reviewed · **Severity:** SEV-1 · **Synthetic example**

## Summary
The TLS certificate on the public gateway expired. Automatic renewal had been failing for 30 days because a DNS record used by the ACME challenge was changed. Every client saw `certificate has expired` for 52 minutes. All pods were healthy and Ready the whole time.

## Impact
- 52 minutes of full outage for browser and API clients.

## Detection
External probe failure and customer reports at the same time. Time to detect: 3 min. There was no expiry alert.

## Timeline (UTC)
- 00:00 Certificate expires.
- 00:03 Probes fail; on-call sees healthy pods and suspects the network.
- 00:20 `openssl s_client` shows `notAfter` in the past.
- 00:35 ACME challenge failure found in the `Certificate` events.
- 00:52 DNS record restored; certificate issued; traffic recovers.

## Root cause
A DNS change broke ACME validation; renewal failures were only visible in cert-manager events.

## Where we got lucky
- It expired at midnight on a weekday, not during a sale.

## Action items
| Action | Type | Priority | Owner | Status |
|---|---|---|---|---|
| Alert on certificate expiry at 21 and 7 days | Detect | P1 | Platform team | Done |
| Alert on `Certificate` not Ready for > 1 h | Detect | P1 | Platform team | Done |
