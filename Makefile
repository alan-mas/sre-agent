# SRE K8s agent PoC — local lab commands. `make help` lists targets.
PROFILE ?= sre-poc
# Agent kubeconfigs are minted from this kube context (the kind lab since D33).
AGENT_CTX ?= kind-sre-lab
KCFG_RO := infra/rbac/generated/kubeconfig-agent-readonly
KCFG_OP := infra/rbac/generated/kubeconfig-agent-operator
SCENARIOS := crashloop oomkilled imagepullbackoff pending cpu-hog hpa-no-requests netpol-deny route-backend

include lab/lab.mk

.PHONY: help cluster-up cluster-stop cluster-delete demo rbac kubeconfig rbac-check scenarios $(addprefix scenario-,$(SCENARIOS)) scenario-bad-rollout scenarios-clean status test test-unit dev budget alert-bad-rollout alert-crashloop alert-oomkilled slack postmortem hooks

help: ## Show targets
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-22s %s\n", $$1, $$2}'

cluster-up: ## Start minikube (+metrics-server), demo app and read-only RBAC
	PROFILE=$(PROFILE) infra/minikube/up.sh
	$(MAKE) demo rbac kubeconfig

cluster-stop: ## Stop minikube (keeps state)
	PROFILE=$(PROFILE) infra/minikube/down.sh

cluster-delete: ## Delete minikube profile
	PROFILE=$(PROFILE) infra/minikube/down.sh --delete

demo: ## Deploy the healthy baseline app (podinfo)
	$(LAB_K) apply -f lab/demo/podinfo.yaml
	$(LAB_K) rollout status deploy/podinfo -n demo --timeout=180s

rbac: ## Apply read-only (cluster-wide) and operator (demo only) identities
	$(LAB_K) apply -f infra/rbac/readonly.yaml -f infra/rbac/operator.yaml

kubeconfig: ## (Re)generate agent kubeconfigs: read-only 24h, operator 8h
	PROFILE=$(AGENT_CTX) infra/rbac/make-kubeconfig.sh readonly 24h
	PROFILE=$(AGENT_CTX) infra/rbac/make-kubeconfig.sh operator 8h

rbac-check: ## Prove what each agent identity can and cannot do
	@echo "read-only:"
	@for check in "get pods" "get pods/log" "list deployments" "list nodes" "get pods.metrics.k8s.io" "get secrets" "list configmaps" "delete pods" "patch deployments" "create pods/exec"; do \
	  printf "  %-26s %s\n" "$$check" "$$(kubectl --kubeconfig $(KCFG_RO) auth can-i $$check -n demo 2>/dev/null || true)"; done
	@echo "operator:"
	@for check in "delete pods" "patch deployments" "patch deployments.apps/scale" "create deployments" "delete deployments" "get secrets" "create pods/exec"; do \
	  printf "  %-26s %s\n" "$$check" "$$(kubectl --kubeconfig $(KCFG_OP) auth can-i $$check -n demo 2>/dev/null || true)"; done
	@printf "  %-26s %s\n" "delete pods -n kube-system" "$$(kubectl --kubeconfig $(KCFG_OP) auth can-i delete pods -n kube-system 2>/dev/null || true)"

scenarios: $(addprefix scenario-,$(SCENARIOS)) scenario-bad-rollout ## Inject every failure scenario

$(addprefix scenario-,$(SCENARIOS)): scenario-%:
	$(LAB_K) apply -f lab/scenarios/$*.yaml

scenario-bad-rollout: ## Deploy orders-api v1, wait, then roll out broken v2
	$(LAB_K) apply -f lab/scenarios/bad-rollout-v1.yaml
	$(LAB_K) rollout status deploy/orders-api -n demo --timeout=180s
	$(LAB_K) apply -f lab/scenarios/bad-rollout-v2.yaml

scenarios-clean: ## Remove all failure scenarios (keeps podinfo)
	$(LAB_K) delete deploy,svc,hpa,networkpolicy,httproute -n demo -l 'scenario,scenario!=healthy' --ignore-not-found

status: ## Pods in demo
	$(LAB_K) get pods -n demo -o wide

test: ## All tests: unit + cluster integration (no tokens)
	uv run pytest -q

test-unit: ## Unit tests only (no cluster, no tokens)
	uv run pytest -q -m 'not cluster'

dev: ## LangGraph dev server on :2024 (truststore off: macOS rejects minikube's self-signed cert; allow-blocking: sync k8s client in the HITL diff preview)
	DISABLE_TRUSTSTORE=true uv run langgraph dev --no-browser --port 2024 --allow-blocking

alert-bad-rollout alert-crashloop alert-oomkilled: alert-%: ## Fire an Alertmanager payload at the agent (needs `make dev`)
	uv run sre-alert infra/alerts/$*.json --fresh

postmortem: ## Blameless postmortem draft for a thread: make postmortem T=<thread_id>
	uv run sre-postmortem $(T)

slack: ## Slack approval bot (Socket Mode; needs `make dev` and SLACK_* in .env)
	uv run sre-slack

hooks: ## Install git hooks (commit-msg convention, pre-push squash check) and the commit template
	git config core.hooksPath .githooks
	git config commit.template .gitmessage
	git config rebase.autoSquash true
	@echo "commit-msg and pre-push hooks active; see CONTRIBUTING.md"

budget: ## Claude spend so far
	uv run sre-budget

# Knowledge base (D41/D42/D45): local Postgres + pgvector, trust auth bound to 127.0.0.1 only.
KB_IMAGE := pgvector/pgvector:0.8.0-pg17
KB_CONTAINER := sre-kb

.PHONY: kb-up kb-down kb-lint kb-ingest kb-eval

kb-up: ## Start Postgres + pgvector for the knowledge base on 127.0.0.1:5433
	@docker ps -a --format '{{.Names}}' | grep -qx $(KB_CONTAINER) \
	  && docker start $(KB_CONTAINER) >/dev/null \
	  || docker run -d --name $(KB_CONTAINER) -p 127.0.0.1:5433:5432 \
	     -e POSTGRES_USER=sre -e POSTGRES_DB=kb -e POSTGRES_HOST_AUTH_METHOD=trust \
	     -v sre-kb-data:/var/lib/postgresql/data $(KB_IMAGE) >/dev/null
	@until docker exec $(KB_CONTAINER) pg_isready -U sre -d kb >/dev/null 2>&1; do sleep 1; done
	@echo "knowledge base ready on 127.0.0.1:5433"

kb-down: ## Stop the knowledge base container (keeps the data volume)
	docker stop $(KB_CONTAINER)

kb-lint: ## Lint runbooks and postmortems (layer 0, D45; 0 tokens)
	uv run sre-doc-lint

kb-ingest: kb-up ## Lint, chunk, embed and index the knowledge base (incremental; 0 tokens)
	uv run sre-ingest

kb-eval: kb-ingest ## Retrieval eval: recall@5 and MRR per chunking, search mode and cap (0 tokens)
	uv run sre-eval-retrieval
