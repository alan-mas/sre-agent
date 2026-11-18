# cert: [CCA:D1] [CCA:D5] [LC:build]
"""cluster_health_snapshot: deterministic pre-triage (no LLM). One tool call replaces 5-6 exploratory ones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.tools import tool

from sre_agent.k8s_client import (
    allowed_namespaces,
    apps,
    check_namespace,
    core,
    k8s_errors,
    to_json,
)
from sre_agent.tools.metrics import pod_usage
from sre_agent.tools.summaries import event_rows, summarize_deployment, summarize_pod

RECENT_EVENTS = timedelta(minutes=15)


def snapshot(namespace: str) -> dict[str, Any]:
    ns = check_namespace(namespace)
    pods = core().list_namespaced_pod(ns).items
    rows = [summarize_pod(p) for p in pods]
    unhealthy = [r for r in rows if "issue" in r]

    deployments = [
        summarize_deployment(d) for d in apps().list_namespaced_deployment(ns).items
    ]
    # Stalled rollout: new revision stuck while the old one still serves (impact: none yet, but release blocked).
    stalled = [d for d in deployments if "problems" in d and d["available"] > 0]
    # Fully down: zero available replicas (impact: service unavailable). Names only; pod details are above.
    down = [
        d["name"] for d in deployments if d["available"] == 0 and d["ready"] != "0/0"
    ]
    # Names only, so "is X healthy?" is answerable without guessing (absent from the issue lists != nonexistent).
    ok = [
        d["name"]
        for d in deployments
        if "problems" not in d
        and d["available"] > 0
        and len(set(d["ready"].split("/"))) == 1
    ]

    try:
        saturated = [u for u in pod_usage(ns) if "flag" in u]
    except Exception:  # noqa: BLE001 - metrics-server may be warming up
        saturated = [{"error": "metrics unavailable"}]

    warnings = core().list_namespaced_event(ns, field_selector="type=Warning").items
    recent = event_rows(warnings, since=datetime.now(UTC) - RECENT_EVENTS, limit=15)

    nodes = []
    for n in core().list_node().items:
        conds = {c.type: c.status for c in n.status.conditions or []}
        pressure = [t for t, s in conds.items() if t != "Ready" and s == "True"]
        if conds.get("Ready") != "True" or pressure:
            nodes.append(
                {
                    "node": n.metadata.name,
                    "ready": conds.get("Ready"),
                    "pressure": pressure,
                }
            )

    return {
        "namespace": ns,
        "pods_total": len(rows),
        "pods_healthy": len(rows) - len(unhealthy),
        "unhealthy_pods": unhealthy,
        "deployments_ok": ok,
        "deployments_down": down,
        "stalled_rollouts": stalled,
        "saturated_containers": saturated,
        "node_issues": nodes,
        "recent_warnings": recent,
    }


@tool(parse_docstring=True)
@k8s_errors
def cluster_health_snapshot(namespace: str | None = None) -> str:
    """Deterministic health summary: unhealthy pods with issue labels, healthy/down deployments, stalled rollouts, CPU/memory-saturated containers, node pressure and warnings from the last 15 minutes.

    Call this FIRST for any "what is broken?" question; then delegate only the items that need deeper investigation.

    Args:
        namespace: Namespace to check; omit to check every allowed namespace.
    """
    namespaces = [namespace] if namespace else allowed_namespaces()
    return to_json([snapshot(ns) for ns in namespaces], max_chars=6000)
