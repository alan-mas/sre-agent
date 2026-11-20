---
id: RB-900
doc_type: runbook
title: Test runbook
services: []
alerts: [TestAlert]
scenario: none
updated: 2026-01-01
related: []
synthetic: true
---

# Test runbook

## When to use
Something breaks.

## Triage
`kubectl -n x get pods` and `kubectl -n x rollout status deploy/y`

## Mitigation
Roll back: `kubectl -n x rollout undo deploy/y` (needs approval).

## Escalation
Owning team.
