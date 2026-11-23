# Failure scenarios (eval ground truth)

Each scenario is injected with `make scenario-<name>` (all at once: `make scenarios`) into the `demo` namespace. The signals below were verified on the cluster before being used as ground truth for the LangSmith dataset (`src/sre_agent/evals.py`).

| Scenario | Workload | Observed signal (verified) | Where the agent sees it | Expected root cause |
|---|---|---|---|---|
| crashloop | `payments-api` | `Error`, restarts rising, `BackOff` | pod status + **logs `--previous`** | Cannot connect to Postgres `payments-db:5432` (connection refused) |
| oomkilled | `report-worker` | `lastState: OOMKilled` (137), restarts rising | pod status + limits | Allocates ~150Mi with a 64Mi limit |
| imagepullbackoff | `checkout-web` | `ImagePullBackOff`, `Failed` event | events (`waiting.message`) | Tag `9.9.9-does-not-exist` does not exist |
| pending | `ml-batch` | `Pending`, `FailedScheduling: Insufficient cpu` | events + requests | Requests 16 CPU; the node has fewer allocatable |
| cpu-hog | `search-indexer` | `top`: ~501m = its limit (500m); request 50m | metrics + limits | Busy loop; CPU throttled at the limit |
| bad-rollout | `orders-api` | `ProgressDeadlineExceeded`; revision 2 never Ready; revision 1 keeps serving | deployment conditions + rollout history + events | Revision 2 moved the readiness probe to `:9797` (nothing listens) → `rollout undo` |
| dns-outage | `billing-api`, `billing-db` | `billing-api` `Error`, restarts rising; `billing-db` Ready with endpoints; CoreDNS 0/0 | logs + service endpoints + `kube-system` pods | **Real outage** (`make break-dns`, undone by `make fix-dns`): CoreDNS scaled to 0, so the name lookup times out; the database is healthy |
| hpa-no-requests | `quote-api` | HPA `ScalingActive=False` `FailedGetResourceMetric: missing request for cpu`; stays at 1 replica while busy | HPA conditions + container resources | No CPU request, so utilization (usage / request) cannot be computed → add requests (config change) |
| netpol-deny | `storefront`, `inventory-api` | `storefront` `Error`, restarts rising, logs `download timed out`; `inventory-api` Ready with endpoints | logs + NetworkPolicy (selected pods, allowed peers) | Policy `inventory-api-ingress` admits only `role=checkout`; storefront lacks the label (kindnet enforces policies) |
| route-backend | `shop-web` | `curl -H 'Host: shop.lab' localhost:8080` → 500; HTTPRoute `ResolvedRefs=False BackendNotFound` | HTTPRoute status + Services | `backendRef` points to `shop-web-v2`, which does not exist; the real Service is `shop-web` |
| *(control)* healthy | `podinfo` | Ready; **transient readiness warnings at startup** | — | **Not broken.** A trap for false positives: old startup events must not be reported |

Other dataset cases: `out-of-scope` (refuse in one line), `list-nodes` and `describe-pod` (direct lookups, D30), `fix-config` (must produce a change request, D32).

After demoing `rollout_undo`, re-break the rollout with `make scenario-bad-rollout` (~60 s until `ProgressDeadlineExceeded`).
