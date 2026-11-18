# cert: [CCA:D1] [CCA:D2] [LC:build]
"""Write tools. Every call is gated by HumanInTheLoopMiddleware (see middleware.py) and runs as the
separate `sre-agent-operator` identity. Each tool: server-side dry run -> apply -> verify -> audit."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from sre_agent.k8s_client import (
    apps,
    apps_write,
    check_namespace,
    core,
    core_write,
    k8s_errors,
    to_json,
)
from sre_agent.tools.summaries import summarize_container_spec, summarize_deployment

MAX_REPLICAS = 10
VERIFY_TIMEOUT_S = int(os.getenv("ACTION_VERIFY_TIMEOUT_S", "90"))
AUDIT_LOG = Path(os.getenv("ACTION_AUDIT_LOG", ".logs/actions.jsonl"))


def _audit(action: str, args: dict[str, Any], outcome: str, justification: str) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "action": action,
        "args": args,
        "outcome": outcome,
        "justification": justification,
    }
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _wait_rollout(namespace: str, name: str) -> dict[str, Any]:
    """Poll (read-only identity) until the deployment is fully rolled out or the timeout hits."""
    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    while True:
        dep = apps().read_namespaced_deployment(name, namespace)
        st, desired = dep.status, dep.spec.replicas or 0
        done = (
            (st.observed_generation or 0) >= (dep.metadata.generation or 0)
            and (st.updated_replicas or 0) == desired
            and (st.ready_replicas or 0) == desired
            and (st.replicas or 0) == desired
        )
        summary = summarize_deployment(dep)
        if done:
            return {"verified": True, **summary}
        if time.monotonic() > deadline:
            return {
                "verified": False,
                "note": f"not healthy after {VERIFY_TIMEOUT_S}s",
                **summary,
            }
        time.sleep(3)


def _owned_by_controller(pod) -> bool:
    return any(o.controller for o in pod.metadata.owner_references or [])


@tool(parse_docstring=True)
@k8s_errors
def restart_pod(namespace: str, pod: str, justification: str) -> str:
    """Delete one pod so its controller recreates it. Requires human approval.

    Only for pods owned by a controller (ReplicaSet/StatefulSet); bare pods would be lost.

    Args:
        namespace: Kubernetes namespace.
        pod: Exact pod name.
        justification: Root cause and decisive evidence that make this the right fix; shown to the approver and audited.
    """
    ns = check_namespace(namespace, write=True)
    target = core().read_namespaced_pod(pod, ns)
    if not _owned_by_controller(target):
        return (
            "error: refused: pod has no controller, deleting it would not recreate it"
        )
    core_write().delete_namespaced_pod(pod, ns, dry_run="All")
    core_write().delete_namespaced_pod(pod, ns)
    owner = target.metadata.owner_references[0]
    _audit("restart_pod", {"namespace": ns, "pod": pod}, "deleted", justification)
    return to_json(
        {"done": f"deleted pod/{pod}; {owner.kind}/{owner.name} will recreate it"}
    )


@tool(parse_docstring=True)
@k8s_errors
def rollout_restart(namespace: str, deployment: str, justification: str) -> str:
    """Rolling restart of a deployment (same as `kubectl rollout restart`), then verify it becomes healthy. Requires human approval.

    Args:
        namespace: Kubernetes namespace.
        deployment: Deployment name.
        justification: Root cause and decisive evidence that make this the right fix; shown to the approver and audited.
    """
    ns = check_namespace(namespace, write=True)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    patch = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}
            }
        }
    }
    apps_write().patch_namespaced_deployment(deployment, ns, patch, dry_run="All")
    apps_write().patch_namespaced_deployment(deployment, ns, patch)
    result = _wait_rollout(ns, deployment)
    _audit(
        "rollout_restart",
        {"namespace": ns, "deployment": deployment},
        "verified" if result["verified"] else "unverified",
        justification,
    )
    return to_json(result)


def _revisions(ns: str, deployment: str) -> list[tuple[int, Any]]:
    dep = apps().read_namespaced_deployment(deployment, ns)
    selector = ",".join(
        f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items()
    )
    out = []
    for rs in apps().list_namespaced_replica_set(ns, label_selector=selector).items:
        if any(o.name == deployment for o in rs.metadata.owner_references or []):
            rev = int(
                (rs.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision", 0
                )
            )
            out.append((rev, rs))
    return sorted(out, key=lambda r: r[0])


def _template_patch(rs) -> dict[str, Any]:
    """Pod template of a previous ReplicaSet, as a deployment patch (what `kubectl rollout undo` does)."""
    template = core().api_client.sanitize_for_serialization(rs.spec.template)
    template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
    cause = (rs.metadata.annotations or {}).get("kubernetes.io/change-cause")
    patch: dict[str, Any] = {"spec": {"template": template}}
    if cause:
        patch["metadata"] = {
            "annotations": {"kubernetes.io/change-cause": f"rollback to: {cause}"}
        }
    return patch


def describe_undo(
    namespace: str, deployment: str, to_revision: int | None = None
) -> str:
    """Human-readable preview of a rollback for the approval prompt (read-only)."""
    revs = _revisions(namespace, deployment)
    if len(revs) < 2:
        return f"Rollback {namespace}/{deployment}: no previous revision found."
    current_rev, current = revs[-1]
    target_rev, target = (
        next((r for r in revs if r[0] == to_revision), revs[-2])
        if to_revision
        else revs[-2]
    )
    cur = [summarize_container_spec(c) for c in current.spec.template.spec.containers]
    tgt = [summarize_container_spec(c) for c in target.spec.template.spec.containers]
    changes = []
    for a, b in zip(cur, tgt, strict=False):
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                changes.append(f"  {a['name']}.{key}: {a.get(key)} -> {b.get(key)}")
    return (
        f"Rollback deployment {namespace}/{deployment}: revision {current_rev} -> {target_rev} "
        f"('{(target.metadata.annotations or {}).get('kubernetes.io/change-cause', 'n/a')}')\n"
        + ("\n".join(changes) if changes else "  (no container spec differences)")
    )


@tool(parse_docstring=True)
@k8s_errors
def rollout_undo(
    namespace: str, deployment: str, justification: str, to_revision: int | None = None
) -> str:
    """Roll a deployment back to a previous revision (same as `kubectl rollout undo`), then verify it becomes healthy. Requires human approval.

    Args:
        namespace: Kubernetes namespace.
        deployment: Deployment name.
        justification: Root cause and decisive evidence that make this the right fix; shown to the approver and audited.
        to_revision: Revision to restore; defaults to the one before the current.
    """
    ns = check_namespace(namespace, write=True)
    revs = _revisions(ns, deployment)
    if len(revs) < 2:
        return "error: refused: no previous revision to roll back to"
    target = (
        next((rs for rev, rs in revs if rev == to_revision), None)
        if to_revision
        else revs[-2][1]
    )
    if target is None:
        return f"error: revision {to_revision} not found; available: {[r for r, _ in revs]}"
    patch = _template_patch(target)
    apps_write().patch_namespaced_deployment(deployment, ns, patch, dry_run="All")
    apps_write().patch_namespaced_deployment(deployment, ns, patch)
    result = _wait_rollout(ns, deployment)
    _audit(
        "rollout_undo",
        {"namespace": ns, "deployment": deployment, "to_revision": to_revision},
        "verified" if result["verified"] else "unverified",
        justification,
    )
    return to_json(result)


@tool(parse_docstring=True)
@k8s_errors
def scale_deployment(
    namespace: str, deployment: str, replicas: int, justification: str
) -> str:
    """Set the replica count of a deployment (0-10), then verify. Requires human approval.

    Args:
        namespace: Kubernetes namespace.
        deployment: Deployment name.
        replicas: Desired replicas, 0 to 10.
        justification: Root cause and decisive evidence that make this the right fix; shown to the approver and audited.
    """
    ns = check_namespace(namespace, write=True)
    if not 0 <= replicas <= MAX_REPLICAS:
        return f"error: refused: replicas must be between 0 and {MAX_REPLICAS}"
    body = {"spec": {"replicas": replicas}}
    apps_write().patch_namespaced_deployment_scale(deployment, ns, body, dry_run="All")
    apps_write().patch_namespaced_deployment_scale(deployment, ns, body)
    result = _wait_rollout(ns, deployment)
    _audit(
        "scale_deployment",
        {"namespace": ns, "deployment": deployment, "replicas": replicas},
        "verified" if result["verified"] else "unverified",
        justification,
    )
    return to_json(result)


ACTION_TOOLS = [restart_pod, rollout_restart, rollout_undo, scale_deployment]
