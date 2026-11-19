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
