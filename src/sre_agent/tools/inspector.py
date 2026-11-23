# cert: [CCA:D2] [LC:build]
"""K8s Inspector tools: object state, compact and read-only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.tools import tool

from sre_agent.k8s_client import (
    allowed_namespaces,
    apps,
    check_namespace,
    core,
    k8s_errors,
    to_json,
)
from sre_agent.tools.summaries import (
    classify_pod,
    describe_pod_compact,
    event_rows,
    summarize_container_spec,
    summarize_deployment,
    summarize_pod,
)
from sre_agent.tools.traffic import TRAFFIC_TOOLS

MAX_ROWS = 50


@tool(parse_docstring=True)
@k8s_errors
def list_namespaces() -> str:
    """List the namespaces the agent may inspect, with pod counts.

    Use first when the user does not name a namespace.
    """
    rows = []
    for ns in allowed_namespaces():
        pods = core().list_namespaced_pod(ns).items
        rows.append(
            {
                "namespace": ns,
                "pods": len(pods),
                "unhealthy": sum(1 for p in pods if classify_pod(p)),
            }
        )
    return to_json(rows)


@tool(parse_docstring=True)
@k8s_errors
def get_pods(
    namespace: str, only_unhealthy: bool = False, label_selector: str | None = None
) -> str:
    """List pods with phase, readiness, restarts and a detected issue label.

    Issue labels: CrashLoop, OOMKilled, ImagePull, Unschedulable, NotReady, Failed.

    Args:
        namespace: Kubernetes namespace.
        only_unhealthy: Return only pods with a detected issue.
        label_selector: Optional selector, e.g. "app=payments-api".
    """
    pods = (
        core()
        .list_namespaced_pod(check_namespace(namespace), label_selector=label_selector)
        .items
    )
    rows = [summarize_pod(p) for p in pods]
    if only_unhealthy:
        rows = [r for r in rows if "issue" in r]
    return to_json({"count": len(rows), "pods": rows[:MAX_ROWS]})


@tool(parse_docstring=True)
@k8s_errors
def describe_pod(namespace: str, name: str) -> str:
    """Compact describe of one pod: containers (image, probes, requests/limits, state, last termination), failing conditions, owner and its recent events.

    Args:
        namespace: Kubernetes namespace.
        name: Exact pod name (from get_pods).
    """
    ns = check_namespace(namespace)
    pod = core().read_namespaced_pod(name, ns)
    events = (
        core()
        .list_namespaced_event(ns, field_selector=f"involvedObject.name={name}")
        .items
    )
    return to_json(
        {**describe_pod_compact(pod), "events": event_rows(events, limit=10)}
    )


@tool(parse_docstring=True)
@k8s_errors
def get_deployments(namespace: str) -> str:
    """List deployments with ready/updated/available replicas, current revision, images and rollout problems (e.g. ProgressDeadlineExceeded).

    Args:
        namespace: Kubernetes namespace.
    """
    deps = apps().list_namespaced_deployment(check_namespace(namespace)).items
    return to_json([summarize_deployment(d) for d in deps][:MAX_ROWS])


@tool(parse_docstring=True)
@k8s_errors
def get_rollout_history(namespace: str, deployment: str) -> str:
    """Revisions of a deployment with change-cause and the pod template of each (image, probes, resources) so revisions can be compared.

    Args:
        namespace: Kubernetes namespace.
        deployment: Deployment name.
    """
    ns = check_namespace(namespace)
    dep = apps().read_namespaced_deployment(deployment, ns)
    selector = ",".join(
        f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items()
    )
    revisions = []
    for rs in apps().list_namespaced_replica_set(ns, label_selector=selector).items:
        if not any(o.name == deployment for o in rs.metadata.owner_references or []):
            continue
        ann = rs.metadata.annotations or {}
        revisions.append(
            {
                "revision": int(ann.get("deployment.kubernetes.io/revision", 0)),
                "change_cause": ann.get("kubernetes.io/change-cause"),
                "replicas": rs.status.replicas or 0,
                "ready": rs.status.ready_replicas or 0,
                "containers": [
                    summarize_container_spec(c)
                    for c in rs.spec.template.spec.containers
                ],
            }
        )
    revisions.sort(key=lambda r: r["revision"])
    return to_json(
        {
            "deployment": deployment,
            "current": summarize_deployment(dep),
            "revisions": revisions,
        }
    )


@tool(parse_docstring=True)
@k8s_errors
def get_events(
    namespace: str,
    warnings_only: bool = True,
    since_minutes: int = 30,
    object_name: str | None = None,
) -> str:
    """Recent events, deduplicated by (reason, object), newest first.

    Old events (e.g. probe failures while a pod was starting) are excluded by since_minutes, so they are not mistaken for current problems.

    Args:
        namespace: Kubernetes namespace.
        warnings_only: Only type=Warning events.
        since_minutes: Only events last seen within this window.
        object_name: Optional pod/deployment name to filter on.
    """
    selectors = []
    if warnings_only:
        selectors.append("type=Warning")
    if object_name:
        selectors.append(f"involvedObject.name={object_name}")
    events = (
        core()
        .list_namespaced_event(
            check_namespace(namespace), field_selector=",".join(selectors) or None
        )
        .items
    )
    since = datetime.now(UTC) - timedelta(minutes=since_minutes)
    return to_json(event_rows(events, since=since))


@tool(parse_docstring=True)
@k8s_errors
def get_nodes() -> str:
    """Nodes with readiness, allocatable CPU/memory, active pressure conditions and taints."""
    rows = []
    for n in core().list_node().items:
        conds = {c.type: c.status for c in n.status.conditions or []}
        rows.append(
            {
                "name": n.metadata.name,
                "ready": conds.get("Ready") == "True",
                "pressure": [
                    t for t, s in conds.items() if t != "Ready" and s == "True"
                ],
                "allocatable": {
                    "cpu": n.status.allocatable.get("cpu"),
                    "memory": n.status.allocatable.get("memory"),
                },
                "taints": [f"{t.key}:{t.effect}" for t in n.spec.taints or []],
            }
        )
    return to_json(rows)


@tool(parse_docstring=True)
@k8s_errors
def get_services(namespace: str) -> str:
    """Services with type, selector, ports and how many ready endpoints back them (0 = traffic has nowhere to go).

    Args:
        namespace: Kubernetes namespace.
    """
    ns = check_namespace(namespace)
    endpoints = {e.metadata.name: e for e in core().list_namespaced_endpoints(ns).items}
    rows = []
    for svc in core().list_namespaced_service(ns).items:
        ep = endpoints.get(svc.metadata.name)
        ready = sum(len(s.addresses or []) for s in (ep.subsets or [])) if ep else 0
        not_ready = (
            sum(len(s.not_ready_addresses or []) for s in (ep.subsets or []))
            if ep
            else 0
        )
        rows.append(
            {
                "name": svc.metadata.name,
                "type": svc.spec.type,
                "selector": svc.spec.selector,
                "ports": [f"{p.port}->{p.target_port}" for p in svc.spec.ports or []],
                "ready_endpoints": ready,
                "not_ready_endpoints": not_ready,
            }
        )
    return to_json(rows)


INSPECTOR_TOOLS = [
    list_namespaces,
    get_pods,
    describe_pod,
    get_deployments,
    get_rollout_history,
    get_events,
    get_nodes,
    get_services,
    *TRAFFIC_TOOLS,
]
