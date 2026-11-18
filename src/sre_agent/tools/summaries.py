# cert: [CCA:D1] [CCA:D2]
"""Deterministic summaries of Kubernetes objects (no LLM). Shared by the tools and the health snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes.client import V1Deployment, V1Pod, V1Probe

from sre_agent.k8s_client import age, cpu_millicores, mem_mib

IMAGE_PULL_REASONS = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}
NOT_READY_GRACE = timedelta(seconds=60)


def _container_issue(status: Any) -> dict[str, Any]:
    state, last = status.state, status.last_state
    waiting = state.waiting.reason if state and state.waiting else None
    # The message carries the decisive detail (e.g. registry "NotFound" vs an auth error on image pulls).
    waiting_message = (
        (state.waiting.message or "")[:200] or None if state and state.waiting else None
    )
    terminated = state.terminated.reason if state and state.terminated else None
    last_term = last.terminated if last and last.terminated else None
    return {
        "waiting": waiting,
        "waiting_message": waiting_message,
        "terminated": terminated,
        "last_reason": last_term.reason if last_term else None,
        "last_exit_code": last_term.exit_code if last_term else None,
        "restarts": status.restart_count or 0,
        "ready": bool(status.ready),
    }


def classify_pod(pod: V1Pod, now: datetime | None = None) -> str | None:
    """Return an issue label for an unhealthy pod, or None if healthy."""
    now = now or datetime.now(UTC)
    status = pod.status
    conditions = {c.type: c for c in (status.conditions or [])}
    scheduled = conditions.get("PodScheduled")
    if (
        status.phase == "Pending"
        and scheduled is not None
        and scheduled.status == "False"
    ):
        return "Unschedulable"
    for cs in status.container_statuses or []:
        issue = _container_issue(cs)
        if issue["waiting"] in IMAGE_PULL_REASONS:
            return "ImagePull"
        if (
            "OOMKilled" in (issue["terminated"], issue["last_reason"])
            and not issue["ready"]
        ):
            return "OOMKilled"
        # Restarts alone are not a crash loop: a node reboot leaves restarts=1 with reason Unknown/255.
        if issue["waiting"] == "CrashLoopBackOff" or (
            issue["terminated"] and issue["restarts"] > 0
        ):
            return "CrashLoop"
    if status.phase in ("Failed", "Unknown"):
        return status.phase
    if status.phase == "Running":
        started = status.start_time
        ready = conditions.get("Ready")
        if (
            ready is not None
            and ready.status != "True"
            and started
            and now - started > NOT_READY_GRACE
        ):
            return "NotReady"
    return None


def summarize_pod(pod: V1Pod, now: datetime | None = None) -> dict[str, Any]:
    statuses = pod.status.container_statuses or []
    ready = sum(1 for cs in statuses if cs.ready)
    row: dict[str, Any] = {
        "name": pod.metadata.name,
        "phase": pod.status.phase,
        "ready": f"{ready}/{len(pod.spec.containers)}",
        "restarts": sum(cs.restart_count or 0 for cs in statuses),
        "age": age(pod.metadata.creation_timestamp, now),
    }
    issue = classify_pod(pod, now)
    if issue:
        row["issue"] = issue
        first = next((_container_issue(cs) for cs in statuses), None)
        if first:
            row.update(
                {
                    k: v
                    for k, v in first.items()
                    if v and k in ("waiting", "last_reason", "last_exit_code")
                }
            )
            # Image pulls: the registry error (NotFound vs. unauthorized) is the evidence; other waits are generic.
            if issue == "ImagePull" and first["waiting_message"]:
                row["waiting_message"] = first["waiting_message"]
    return row


def _probe(probe: V1Probe | None) -> str | None:
    if probe is None:
        return None
    if probe.http_get:
        return f"http {probe.http_get.path}:{probe.http_get.port}"
    if probe.tcp_socket:
        return f"tcp :{probe.tcp_socket.port}"
    if probe._exec:
        return "exec " + " ".join(probe._exec.command or [])[:60]
    return "other"


def _resources(container: Any) -> dict[str, Any]:
    res = container.resources
    req, lim = (res.requests or {}) if res else {}, (res.limits or {}) if res else {}
    return {
        "cpu_req_m": cpu_millicores(req.get("cpu")),
        "cpu_lim_m": cpu_millicores(lim.get("cpu")),
        "mem_req_mi": mem_mib(req.get("memory")),
        "mem_lim_mi": mem_mib(lim.get("memory")),
    }


def summarize_container_spec(container: Any) -> dict[str, Any]:
    spec = {
        "name": container.name,
        "image": container.image,
        "readiness": _probe(container.readiness_probe),
        "liveness": _probe(container.liveness_probe),
        **_resources(container),
    }
    return {k: v for k, v in spec.items() if v is not None}


def describe_pod_compact(pod: V1Pod, now: datetime | None = None) -> dict[str, Any]:
    statuses = {cs.name: cs for cs in pod.status.container_statuses or []}
    containers = []
    for c in pod.spec.containers:
        entry = summarize_container_spec(c)
        if c.name in statuses:
            entry.update(
                {
                    k: v
                    for k, v in _container_issue(statuses[c.name]).items()
                    if v not in (None, False, 0)
                }
            )
            entry["ready"] = bool(statuses[c.name].ready)
        containers.append(entry)
    owner = (pod.metadata.owner_references or [None])[0]
    bad_conditions = [
        {"type": c.type, "reason": c.reason, "message": (c.message or "")[:200]}
        for c in pod.status.conditions or []
        if c.status != "True"
    ]
    return {
        **summarize_pod(pod, now),
        "node": pod.spec.node_name,
        "owner": f"{owner.kind}/{owner.name}" if owner else None,
        "containers": containers,
        "failing_conditions": bad_conditions,
    }


def summarize_deployment(dep: V1Deployment) -> dict[str, Any]:
    st = dep.status
    desired = dep.spec.replicas or 0
    row: dict[str, Any] = {
        "name": dep.metadata.name,
        "ready": f"{st.ready_replicas or 0}/{desired}",
        "updated": st.updated_replicas or 0,
        "available": st.available_replicas or 0,
        "revision": (dep.metadata.annotations or {}).get(
            "deployment.kubernetes.io/revision"
        ),
        "images": [c.image for c in dep.spec.template.spec.containers],
    }
    problems = []
    for c in st.conditions or []:
        if (c.type == "Progressing" and c.reason == "ProgressDeadlineExceeded") or (
            c.type == "Available" and c.status == "False"
        ):
            problems.append(
                {"type": c.type, "reason": c.reason, "message": (c.message or "")[:200]}
            )
    if problems:
        row["problems"] = problems
    return row


def event_rows(
    events: list[Any], since: datetime | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    """Deduplicate events by (reason, object), newest first, optionally only those seen after `since`."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        last = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
        if since and last and last < since:
            continue
        obj = f"{ev.involved_object.kind}/{ev.involved_object.name}"
        key = (ev.reason or "", obj)
        row = rows.get(key)
        count = ev.count or 1
        if row is None or (last and row["_last"] and last > row["_last"]):
            rows[key] = {
                "type": ev.type,
                "reason": ev.reason,
                "object": obj,
                "count": count + (row["count"] if row else 0),
                "message": (ev.message or "")[:200],
                "_last": last,
            }
        else:
            row["count"] += count
    ordered = sorted(
        rows.values(),
        key=lambda r: r["_last"] or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    for r in ordered:
        r["last_seen"] = age(r.pop("_last"))
    return ordered[:limit]
