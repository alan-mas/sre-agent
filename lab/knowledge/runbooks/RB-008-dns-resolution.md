---
id: RB-008
doc_type: runbook
title: DNS resolution failures inside the cluster
services: []
alerts: [CoreDNSDown, CoreDNSErrorsHigh]
scenario: dns-outage
updated: 2026-11-20
related: [PM-007, RB-001]
synthetic: true
---

# DNS resolution failures inside the cluster

## When to use
Pods cannot resolve service names. Symptoms in application logs: `no such host`, `Temporary failure in name resolution`, `getaddrinfo EAI_AGAIN`, or connection errors to dependencies that are actually healthy. Many unrelated services fail at the same time, which a single broken dependency would not explain.

## Triage
1. Check that CoreDNS is serving: `kubectl -n kube-system get deploy coredns` (desired vs ready replicas) and `kubectl -n kube-system get endpoints kube-dns` (0 ready endpoints = no cluster DNS). Listing pods alone is not enough: no pods can mean scaled to zero, another name, or not installed.
2. If CoreDNS has pods but is unhealthy, read them: `kubectl -n kube-system get pods -l k8s-app=kube-dns` and their logs (look for `Corefile` parse errors or OOMKilled).
3. Resolve from a debug pod:
   `kubectl run dnsutils --rm -it --image=registry.k8s.io/e2e-test-images/agnhost:2.39 -- nslookup <svc>.<ns>.svc.cluster.local` (creates a temporary pod: needs approval)
4. Distinguish DNS from the dependency: if the Service has endpoints (`kubectl -n <ns> get endpoints <svc>`) and the IP answers, but the name does not resolve, it is DNS.

## Common causes
- CoreDNS pods crashed, OOMKilled or were scaled to zero.
- A bad change to the CoreDNS ConfigMap (`Corefile`).
- Upstream resolver unreachable for external names.
- NetworkPolicy blocking UDP/TCP 53 to `kube-system`.

## Mitigation
- CoreDNS belongs to the platform team. Application on-call does not scale, restart or edit it: escalate with the evidence (replicas, `kube-dns` endpoints, the failing lookup line).
- A bad `Corefile` change is reverted by the platform team through a change request.
- Do not restart the affected applications: they recover on their own once resolution works.

## Escalation
The platform team, immediately: cluster DNS down affects every service.

## Related
RB-001 (CrashLoopBackOff), PM-007 (CoreDNS outage).
