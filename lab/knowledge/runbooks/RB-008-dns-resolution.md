---
id: RB-008
doc_type: runbook
title: DNS resolution failures inside the cluster
services: []
alerts: [CoreDNSDown, CoreDNSErrorsHigh]
scenario: none
updated: 2026-06-12
related: [PM-007, RB-001]
synthetic: true
---

# DNS resolution failures inside the cluster

## When to use
Pods cannot resolve service names. Symptoms in application logs: `no such host`, `Temporary failure in name resolution`, `getaddrinfo EAI_AGAIN`, or connection errors to dependencies that are actually healthy. Many unrelated services fail at the same time, which a single broken dependency would not explain.

## Triage
1. Check CoreDNS: `kubectl -n kube-system get pods -l k8s-app=kube-dns` and its logs.
2. Resolve from a debug pod:
   `kubectl run dnsutils --rm -it --image=registry.k8s.io/e2e-test-images/agnhost:2.39 -- nslookup <svc>.<ns>.svc.cluster.local`
3. Distinguish DNS from the dependency: if the Service has endpoints (`kubectl -n <ns> get endpoints <svc>`) and the IP answers, but the name does not resolve, it is DNS.

## Common causes
- CoreDNS pods crashed, OOMKilled or were scaled to zero.
- A bad change to the CoreDNS ConfigMap (`Corefile`).
- Upstream resolver unreachable for external names.
- NetworkPolicy blocking UDP/TCP 53 to `kube-system`.

## Mitigation
- Restart CoreDNS if it is unhealthy (`kubectl -n kube-system rollout restart deploy/coredns`, needs approval).
- Revert a recent `Corefile` change through a change request to the platform team.
- Applications in crash loops recover on their own once resolution works.

## Escalation
The platform team.

## Related
RB-001 (CrashLoopBackOff), PM-007 (CoreDNS outage).
