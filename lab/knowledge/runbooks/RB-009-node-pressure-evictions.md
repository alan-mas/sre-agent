---
id: RB-009
doc_type: runbook
title: Node DiskPressure / MemoryPressure and pod evictions
services: []
alerts: [KubeNodePressure, NodeFilesystemSpaceFillingUp]
scenario: none
updated: 2026-08-08
related: [RB-002, RB-007]
synthetic: true
---

# Node DiskPressure / MemoryPressure and pod evictions

## When to use
The kubelet evicts pods because the node is running out of disk or memory. Symptoms: pods with status `Evicted` and messages like "The node was low on resource: ephemeral-storage" or "memory"; node condition `DiskPressure=True` or `MemoryPressure=True`; a taint `node.kubernetes.io/disk-pressure`.

## Triage
1. `kubectl describe node <node>` → `Conditions` and `Taints`.
2. `kubectl get pods -A --field-selector status.phase=Failed | grep Evicted`
3. Find the consumer: large container logs, `emptyDir` volumes, image cache, or pods without memory limits.

## How this differs from OOMKilled
- **OOMKilled (RB-002):** one container exceeded **its own limit**; exit code 137; the node is fine.
- **Eviction (this runbook):** the **node** ran short; the kubelet picks pods to evict by priority and usage; status `Evicted`.

## Mitigation
- Clean up evicted pods once the cause is fixed: `kubectl delete pod -A --field-selector status.phase=Failed` (needs approval).
- Disk: the platform team prunes images or expands the disk. Limits for `ephemeral-storage` or memory are configuration changes for the owning teams.

## Escalation
The platform team.

## Related
RB-002 (OOMKilled), RB-007 (node NotReady).
