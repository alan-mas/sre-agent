# Lab targets (D43), included by the root Makefile. Needs only Docker, kind,
# kubectl and helm; no API keys. Every command pins the kind context so it
# never touches another cluster.
LAB_CLUSTER ?= sre-lab
LAB_CTX := kind-$(LAB_CLUSTER)
LAB_K := kubectl --context $(LAB_CTX)
METRICS_SERVER_VERSION := v0.9.1
ENVOY_GATEWAY_VERSION := v1.9.4

.PHONY: lab-up lab-down lab-status lab-platform

lab-up: ## Create the kind lab cluster (1 CP + 2 workers) with metrics-server and Envoy Gateway
	@kind get clusters 2>/dev/null | grep -qx $(LAB_CLUSTER) \
	  && echo "cluster $(LAB_CLUSTER) already exists" \
	  || kind create cluster --config lab/kind/cluster.yaml --wait 120s
	$(MAKE) lab-platform

lab-platform: ## (Re)install metrics-server and Envoy Gateway on the lab cluster
	$(LAB_K) apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/$(METRICS_SERVER_VERSION)/components.yaml
	@# kind kubelets use self-signed serving certs
	@$(LAB_K) -n kube-system get deployment metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' \
	  | grep -q kubelet-insecure-tls \
	  || $(LAB_K) -n kube-system patch deployment metrics-server --type=json \
	     -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
	helm upgrade --install eg oci://docker.io/envoyproxy/gateway-helm --version $(ENVOY_GATEWAY_VERSION) \
	  --kube-context $(LAB_CTX) -n envoy-gateway-system --create-namespace --wait --timeout 5m
	$(LAB_K) create namespace lab-system --dry-run=client -o yaml | $(LAB_K) apply -f -
	$(LAB_K) apply -f lab/platform/gateway.yaml
	$(LAB_K) -n kube-system rollout status deployment/metrics-server --timeout=180s
	$(LAB_K) -n lab-system wait gateway/lab --for=condition=Programmed --timeout=180s

lab-status: ## Nodes, platform pods and gateway of the lab cluster
	$(LAB_K) get nodes -o wide
	$(LAB_K) get pods -n kube-system -l k8s-app=metrics-server
	$(LAB_K) get pods -n envoy-gateway-system
	$(LAB_K) get gateway -n lab-system

lab-down: ## Delete the kind lab cluster
	kind delete cluster --name $(LAB_CLUSTER)

.PHONY: break-dns fix-dns

break-dns: ## Scenario dns-outage: healthy billing-db, CoreDNS scaled to 0, billing-api restarted (real outage)
	$(LAB_K) apply -f lab/scenarios/dns-outage.yaml
	$(LAB_K) -n demo rollout status deploy/billing-db --timeout=120s
	$(LAB_K) -n kube-system scale deploy/coredns --replicas=0
	$(LAB_K) -n kube-system wait pod -l k8s-app=kube-dns --for=delete --timeout=60s
	@# A running process keeps working; the outage hits the next start (as in PM-007, a deploy).
	$(LAB_K) -n demo rollout restart deploy/billing-api
	@echo "waiting for billing-api to crash loop (the eval needs the signal first)..."
	@for i in $$(seq 1 30); do \
	  $(LAB_K) -n demo get pods -l app=billing-api \
	    -o jsonpath='{.items[*].status.containerStatuses[*].restartCount}' | awk '$$1 >= 2 {ok=1} END {exit !ok}' && exit 0; \
	  sleep 5; done; echo "billing-api not crash looping yet" >&2; exit 1

fix-dns: ## Undo dns-outage: restore CoreDNS (2 replicas) and remove the scenario
	$(LAB_K) -n kube-system scale deploy/coredns --replicas=2
	$(LAB_K) -n kube-system rollout status deploy/coredns --timeout=120s
	$(LAB_K) delete -f lab/scenarios/dns-outage.yaml --ignore-not-found
