# cert: [CCA:D2] [LC:build]
"""Metrics Analyst tools: metrics-server usage joined with requests/limits, so saturation is explicit."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from sre_agent.k8s_client import (
    check_namespace,
    core,
    cpu_millicores,
    custom,
    k8s_errors,
    mem_mib,
    to_json,
)
from sre_agent.tools.summaries import summarize_container_spec

SATURATION_PCT = 90


def _pct(used: int | None, total: int | None) -> int | None:
    return round(100 * used / total) if used is not None and total else None


def pod_usage(namespace: str) -> list[dict[str, Any]]:
    """Per-container usage vs. request/limit for every pod with metrics (sorted by CPU)."""
    ns = check_namespace(namespace)
    metrics = custom().list_namespaced_custom_object(
        "metrics.k8s.io", "v1beta1", ns, "pods"
    )["items"]
    specs = {p.metadata.name: p for p in core().list_namespaced_pod(ns).items}
    rows = []
    for m in metrics:
        pod = specs.get(m["metadata"]["name"])
        containers = (
            {c.name: summarize_container_spec(c) for c in pod.spec.containers}
            if pod
            else {}
        )
        for c in m["containers"]:
            spec = containers.get(c["name"], {})
            cpu, mem = cpu_millicores(c["usage"]["cpu"]), mem_mib(c["usage"]["memory"])
            row = {
                "pod": m["metadata"]["name"],
                "container": c["name"],
                "cpu_m": cpu,
                "cpu_pct_of_limit": _pct(cpu, spec.get("cpu_lim_m")),
                "cpu_pct_of_request": _pct(cpu, spec.get("cpu_req_m")),
                "mem_mi": mem,
                "mem_pct_of_limit": _pct(mem, spec.get("mem_lim_mi")),
            }
            if (row["cpu_pct_of_limit"] or 0) >= SATURATION_PCT:
                row["flag"] = "cpu_throttled"
            elif (row["mem_pct_of_limit"] or 0) >= SATURATION_PCT:
                row["flag"] = "near_oom"
            rows.append({k: v for k, v in row.items() if v is not None})
    return sorted(rows, key=lambda r: r.get("cpu_m", 0), reverse=True)


@tool(parse_docstring=True)
@k8s_errors
def get_pod_metrics(namespace: str, top: int = 15) -> str:
    """Current CPU (millicores) and memory (MiB) per container, with % of limit and request. Flags cpu_throttled / near_oom at >=90% of limit.

    Pods in CrashLoop/OOMKilled often have no metrics (not running long enough); use get_restart_counts for those.

    Args:
        namespace: Kubernetes namespace.
        top: Return the N highest CPU consumers.
    """
    return to_json(pod_usage(namespace)[:top])


@tool(parse_docstring=True)
@k8s_errors
def get_node_metrics() -> str:
    """Node CPU and memory usage as a percentage of allocatable."""
    nodes = {n.metadata.name: n for n in core().list_node().items}
    rows = []
    for m in custom().list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")[
        "items"
    ]:
        node = nodes.get(m["metadata"]["name"])
        alloc = node.status.allocatable if node else {}
        cpu, mem = cpu_millicores(m["usage"]["cpu"]), mem_mib(m["usage"]["memory"])
        rows.append(
            {
                "node": m["metadata"]["name"],
                "cpu_m": cpu,
                "cpu_pct": _pct(cpu, cpu_millicores(alloc.get("cpu"))),
                "mem_mi": mem,
                "mem_pct": _pct(mem, mem_mib(alloc.get("memory"))),
            }
        )
    return to_json(rows)


@tool(parse_docstring=True)
@k8s_errors
def get_resource_requests_limits(namespace: str, pod: str | None = None) -> str:
    """CPU/memory requests and limits per container (all pods in the namespace, or one pod).

    Args:
        namespace: Kubernetes namespace.
        pod: Optional pod name.
    """
    ns = check_namespace(namespace)
    pods = (
        [core().read_namespaced_pod(pod, ns)]
        if pod
        else core().list_namespaced_pod(ns).items
    )
    rows = []
    for p in pods:
        for c in p.spec.containers:
            spec = summarize_container_spec(c)
            rows.append(
                {
                    "pod": p.metadata.name,
                    **{
                        k: v
                        for k, v in spec.items()
                        if k not in ("readiness", "liveness")
                    },
                }
            )
    return to_json(rows[:50])


@tool(parse_docstring=True)
@k8s_errors
def get_restart_counts(namespace: str, min_restarts: int = 1) -> str:
    """Containers that restarted, with last termination reason (e.g. OOMKilled, Error), exit code and when it happened.

    Args:
        namespace: Kubernetes namespace.
        min_restarts: Minimum restarts to include.
    """
    rows = []
    for p in core().list_namespaced_pod(check_namespace(namespace)).items:
        for cs in p.status.container_statuses or []:
            if (cs.restart_count or 0) < min_restarts:
                continue
            term = cs.last_state.terminated if cs.last_state else None
            rows.append(
                {
                    "pod": p.metadata.name,
                    "container": cs.name,
                    "restarts": cs.restart_count,
                    "last_reason": term.reason if term else None,
                    "exit_code": term.exit_code if term else None,
                    "finished_at": term.finished_at if term else None,
                }
            )
    return to_json(sorted(rows, key=lambda r: r["restarts"], reverse=True))


METRICS_TOOLS = [
    get_pod_metrics,
    get_node_metrics,
    get_resource_requests_limits,
    get_restart_counts,
]
