# cert: [CCA:D2] [LC:build]
"""Inspector tools for how load and traffic reach pods: autoscalers, routes and network policies.

Objects are read as plain dicts (camelCase, as in the manifests) so built-in and Gateway API resources share
one code path; the summaries keep only what explains a failure.
"""

from __future__ import annotations

from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException
from langchain_core.tools import tool

from sre_agent.k8s_client import (
    autoscaling,
    check_namespace,
    core,
    custom,
    k8s_errors,
    networking,
    to_json,
)

MSG_CHARS = 200
MAX_PODS = 10
_serialize = client.ApiClient().sanitize_for_serialization


def _problems(conditions: list[dict], healthy: dict[str, str]) -> list[dict]:
    """Conditions whose status differs from the healthy one (unknown types are ignored)."""
    return [
        {
            "type": c.get("type"),
            "reason": c.get("reason"),
            "message": (c.get("message") or "")[:MSG_CHARS] or None,
        }
        for c in conditions
        if c.get("type") in healthy and c.get("status") != healthy[c["type"]]
    ]


# ---------- HPA ----------


def _metric_value(spec: dict) -> str | None:
    if spec.get("averageUtilization") is not None:
        return f"{spec['averageUtilization']}%"
    return spec.get("averageValue") or spec.get("value")


def _metric_rows(spec: list[dict], current: list[dict]) -> list[str]:
    seen = {}
    for m in current:
        body = m.get(_key(m)) or {}
        seen[(m.get("type"), _name(body))] = _metric_value(body.get("current") or {})
    rows = []
    for m in spec:
        body = m.get(_key(m)) or {}
        now = seen.get((m.get("type"), _name(body))) or "unknown"
        rows.append(
            f"{_name(body)}: {now} / target {_metric_value(body.get('target') or {})}"
        )
    return rows


def _key(metric: dict) -> str:
    t = metric.get("type") or ""
    return {"ContainerResource": "containerResource"}.get(t, t[:1].lower() + t[1:])


def _name(body: dict) -> str | None:
    return body.get("name") or (body.get("metric") or {}).get("name")


def summarize_hpa(hpa: dict) -> dict[str, Any]:
    spec, status = hpa.get("spec") or {}, hpa.get("status") or {}
    ref = spec.get("scaleTargetRef") or {}
    row: dict[str, Any] = {
        "name": hpa["metadata"]["name"],
        "target": f"{ref.get('kind')}/{ref.get('name')}",
        "replicas": f"{status.get('currentReplicas', 0)} (desired {status.get('desiredReplicas', 0)}, "
        f"min {spec.get('minReplicas', 1)}, max {spec.get('maxReplicas')})",
        "metrics": _metric_rows(
            spec.get("metrics") or [], status.get("currentMetrics") or []
        ),
    }
    # ScalingLimited=True means it wants more (or fewer) replicas than min/max allow.
    problems = _problems(
        status.get("conditions") or [],
        {"AbleToScale": "True", "ScalingActive": "True", "ScalingLimited": "False"},
    )
    if problems:
        row["problems"] = problems
    return row


@tool(parse_docstring=True)
@k8s_errors
def get_hpas(namespace: str) -> str:
    """HorizontalPodAutoscalers: target, current/desired/min/max replicas, each metric's current value vs target, and failing conditions (e.g. ScalingActive=False when metrics cannot be computed, ScalingLimited at max).

    Args:
        namespace: Kubernetes namespace.
    """
    items = (
        autoscaling()
        .list_namespaced_horizontal_pod_autoscaler(check_namespace(namespace))
        .items
    )
    return to_json([summarize_hpa(_serialize(h)) for h in items])


# ---------- routes ----------


def _service_ports(namespace: str) -> dict[str, set[int]]:
    return {
        s.metadata.name: {p.port for p in s.spec.ports or []}
        for s in core().list_namespaced_service(namespace).items
    }


def _backend(
    name: str, port: int | None, services: dict[str, set[int]]
) -> dict[str, Any]:
    row: dict[str, Any] = {"service": f"{name}:{port}"}
    if name not in services:
        row["problem"] = "service not found"
    elif port is not None and port not in services[name]:
        row["problem"] = f"port not exposed (service ports: {sorted(services[name])})"
    return row


def summarize_httproute(route: dict, services: dict[str, set[int]]) -> dict[str, Any]:
    ns = route["metadata"].get("namespace")
    spec, status = route.get("spec") or {}, route.get("status") or {}
    backends = []
    for rule in spec.get("rules") or []:
        for ref in rule.get("backendRefs") or []:
            if ref.get("namespace", ns) != ns:
                backends.append(
                    {"service": f"{ref['namespace']}/{ref['name']}:{ref.get('port')}"}
                )
            else:
                backends.append(_backend(ref["name"], ref.get("port"), services))
    row: dict[str, Any] = {
        "kind": "HTTPRoute",
        "name": route["metadata"]["name"],
        "hostnames": spec.get("hostnames") or ["*"],
        "parents": [
            f"{p.get('namespace', ns)}/{p['name']}"
            for p in spec.get("parentRefs") or []
        ],
        "backends": backends,
    }
    parents = status.get("parents") or []
    problems = [
        p
        for parent in parents
        for p in _problems(
            parent.get("conditions") or [], {"Accepted": "True", "ResolvedRefs": "True"}
        )
    ]
    if not parents:
        problems.append(
            {
                "type": "Accepted",
                "reason": "NoStatus",
                "message": "no Gateway has accepted this route",
            }
        )
    if problems:
        row["problems"] = problems
    return row


def summarize_ingress(ing: dict, services: dict[str, set[int]]) -> dict[str, Any]:
    spec = ing.get("spec") or {}
    backends = []
    for rule in spec.get("rules") or []:
        for path in (rule.get("http") or {}).get("paths") or []:
            svc = (path.get("backend") or {}).get("service") or {}
            backends.append(
                {
                    "path": f"{rule.get('host', '*')}{path.get('path', '/')}",
                    **_backend(
                        svc.get("name"), (svc.get("port") or {}).get("number"), services
                    ),
                }
            )
    lb = ((ing.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []
    return {
        "kind": "Ingress",
        "name": ing["metadata"]["name"],
        "class": spec.get("ingressClassName"),
        "backends": backends,
        "address": [a.get("ip") or a.get("hostname") for a in lb] or None,
        "tls_secrets": [t.get("secretName") for t in spec.get("tls") or []] or None,
    }


@tool(parse_docstring=True)
@k8s_errors
def get_routes(namespace: str) -> str:
    """How external traffic reaches services: Gateway API HTTPRoutes (hostnames, parent Gateway, backends, Accepted/ResolvedRefs problems) and Ingresses, each backend checked against the namespace's Services and ports.

    Args:
        namespace: Kubernetes namespace.
    """
    ns = check_namespace(namespace)
    services = _service_ports(ns)
    rows: list[dict[str, Any]] = []
    try:
        routes = custom().list_namespaced_custom_object(
            "gateway.networking.k8s.io", "v1", ns, "httproutes"
        )["items"]
        rows += [summarize_httproute(r, services) for r in routes]
    except ApiException as e:
        if e.status != 404:  # 404: Gateway API not installed; Ingresses may still exist
            raise
    rows += [
        summarize_ingress(_serialize(i), services)
        for i in networking().list_namespaced_ingress(ns).items
    ]
    return to_json(rows)


# ---------- network policies ----------


def selects(selector: dict | None, labels: dict[str, str]) -> bool:
    """Kubernetes label-selector semantics; an empty selector selects everything."""
    selector = selector or {}
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for e in selector.get("matchExpressions") or []:
        key, op, values = e["key"], e["operator"], e.get("values") or []
        ok = {
            "In": labels.get(key) in values,
            "NotIn": labels.get(key) not in values,
            "Exists": key in labels,
            "DoesNotExist": key not in labels,
        }.get(op, False)
        if not ok:
            return False
    return True


def _selector_text(selector: dict | None) -> str:
    selector = selector or {}
    parts = [f"{k}={v}" for k, v in (selector.get("matchLabels") or {}).items()]
    parts += [
        f"{e['key']} {e['operator']} {e.get('values') or ''}".strip()
        for e in selector.get("matchExpressions") or []
    ]
    return ",".join(parts) or "all"


def _peer_text(peer: dict) -> str:
    if "ipBlock" in peer:
        block = peer["ipBlock"]
        exc = f" except {block['except']}" if block.get("except") else ""
        return f"ip {block.get('cidr')}{exc}"
    parts = []
    if "namespaceSelector" in peer:
        parts.append(f"namespaces({_selector_text(peer['namespaceSelector'])})")
    if "podSelector" in peer:
        parts.append(f"pods({_selector_text(peer['podSelector'])})")
    return " & ".join(parts)


def _rules(rules: list[dict], peers_key: str, pods: dict[str, dict]) -> list[dict]:
    out = []
    for r in rules:
        peers = r.get(peers_key)
        row: dict[str, Any] = {
            peers_key: [_peer_text(p) for p in peers] if peers else ["any"],
            "ports": [
                f"{p.get('protocol', 'TCP')}/{p.get('port', 'all')}"
                for p in r.get("ports") or []
            ]
            or ["all"],
        }
        # Same-namespace pod peers: name the pods that ARE allowed, so a missing client is visible.
        local = [
            p["podSelector"]
            for p in peers or []
            if "podSelector" in p and "namespaceSelector" not in p
        ]
        if local:
            row["matching_pods"] = sorted(
                n for n, lb in pods.items() if any(selects(s, lb) for s in local)
            )[:MAX_PODS]
        out.append(row)
    return out


def summarize_netpol(policy: dict, pods: dict[str, dict]) -> dict[str, Any]:
    spec = policy.get("spec") or {}
    types = spec.get("policyTypes") or ["Ingress"]
    selected = sorted(
        n for n, lb in pods.items() if selects(spec.get("podSelector"), lb)
    )
    row: dict[str, Any] = {
        "name": policy["metadata"]["name"],
        "applies_to": _selector_text(spec.get("podSelector")),
        "selected_pods": selected[:MAX_PODS],
        "types": types,
    }
    for kind, key, peers in (
        ("Ingress", "ingress", "from"),
        ("Egress", "egress", "to"),
    ):
        if kind in types:
            rules = spec.get(key) or []
            # A listed type with no rules denies all traffic of that direction to the selected pods.
            row[key] = _rules(rules, peers, pods) if rules else "deny all"
    denied = _denied_local_pods(spec, selected, pods)
    if denied:
        row["ingress_denied_pods"] = denied
    return row


def _denied_local_pods(
    spec: dict, selected: list[str], pods: dict[str, dict]
) -> list[dict] | None:
    """Pods of the namespace that may NOT reach the selected pods, with their labels (the evidence for "why not").

    Only computed when every ingress peer is a same-namespace pod selector; with "any", ipBlock or namespace
    peers the answer depends on objects outside this namespace, so it is left to the reader."""
    if "Ingress" not in (spec.get("policyTypes") or ["Ingress"]):
        return None
    peers = [p for r in spec.get("ingress") or [] for p in r.get("from") or [None]]
    if any(
        p is None or "podSelector" not in p or "namespaceSelector" in p for p in peers
    ):
        return None
    allowed = [p["podSelector"] for p in peers]
    return [
        {"pod": n, "labels": _selector_text({"matchLabels": lb})}
        for n, lb in sorted(pods.items())
        if n not in selected and not any(selects(s, lb) for s in allowed)
    ][:MAX_PODS]


@tool(parse_docstring=True)
@k8s_errors
def get_network_policies(namespace: str) -> str:
    """NetworkPolicies: which pods each one selects, and the ingress/egress it allows (peers, ports, and which pods in the namespace match a pod peer). Traffic not allowed by any policy that selects a pod is dropped (often seen by the client as a timeout).

    Args:
        namespace: Kubernetes namespace.
    """
    ns = check_namespace(namespace)
    pods = {
        p.metadata.name: p.metadata.labels or {}
        for p in core().list_namespaced_pod(ns).items
    }
    items = networking().list_namespaced_network_policy(ns).items
    return to_json([summarize_netpol(_serialize(p), pods) for p in items])


TRAFFIC_TOOLS = [get_hpas, get_routes, get_network_policies]
