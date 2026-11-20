---
id: RB-007
doc_type: runbook
title: Node NotReady
services: []
alerts: [KubeNodeNotReady, KubeletDown]
scenario: none
updated: 2026-07-30
related: [RB-004, RB-009]
synthetic: true
---

# Node NotReady

## When to use
A node stops reporting to the control plane. Symptoms: `kubectl get nodes` shows `NotReady`; its pods go `Unknown` or `Terminating`; replacement pods may stay `Pending` if the remaining nodes lack capacity.

## Triage
1. `kubectl get nodes -o wide` and `kubectl describe node <node>` → `Conditions` (`Ready=Unknown`, "Kubelet stopped posting node status").
2. Check how many nodes are affected: one node is usually the machine or its kubelet; all nodes at once is usually the control plane or the network.
3. List what ran there: `kubectl get pods -A -o wide --field-selector spec.nodeName=<node>`.

## Common causes
- Kubelet crashed or was stopped; container runtime unhealthy.
- The machine is out of disk or memory (see RB-009).
- Network partition between the node and the API server.

## Mitigation
- Cordon the node so nothing new lands there: `kubectl cordon <node>` (needs approval).
- Workloads with several replicas recover on the other nodes; singletons are the risk.
- Repairing or replacing the machine belongs to the platform team.

## Escalation
The platform team.

## Related
RB-004 (Pending pods), RB-009 (node pressure).
