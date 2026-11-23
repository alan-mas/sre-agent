# cert: [CCA:D2] [LC:build]
"""Kubernetes access for the agent: its own kubeconfig, a namespace allowlist and token-frugal output helpers."""

from __future__ import annotations

import functools
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.utils.quantity import parse_quantity

load_dotenv(
    override=False
)  # never override env set by tests/CI (e.g. LANGSMITH_TRACING=false)

MAX_CHARS = int(os.getenv("TOOL_MAX_CHARS", "4000"))


class NamespaceNotAllowedError(ValueError):
    pass


@functools.cache
def _api_client() -> client.ApiClient:
    path = os.getenv(
        "AGENT_KUBECONFIG", "infra/rbac/generated/kubeconfig-agent-readonly"
    )
    return config.new_client_from_config(config_file=path)


def core() -> client.CoreV1Api:
    return client.CoreV1Api(_api_client())


def apps() -> client.AppsV1Api:
    return client.AppsV1Api(_api_client())


def custom() -> client.CustomObjectsApi:
    return client.CustomObjectsApi(_api_client())


def autoscaling() -> client.AutoscalingV2Api:
    return client.AutoscalingV2Api(_api_client())


def networking() -> client.NetworkingV1Api:
    return client.NetworkingV1Api(_api_client())


# Write identity: separate ServiceAccount, used only by HITL-approved action tools.
@functools.cache
def _operator_client() -> client.ApiClient:
    path = os.getenv(
        "AGENT_OPERATOR_KUBECONFIG", "infra/rbac/generated/kubeconfig-agent-operator"
    )
    return config.new_client_from_config(config_file=path)


def core_write() -> client.CoreV1Api:
    return client.CoreV1Api(_operator_client())


def apps_write() -> client.AppsV1Api:
    return client.AppsV1Api(_operator_client())


PROTECTED_NAMESPACES = frozenset(
    {"kube-system", "kube-public", "kube-node-lease", "sre-agent"}
)


def allowed_namespaces() -> list[str]:
    raw = os.getenv("AGENT_NAMESPACE_ALLOWLIST", "demo")
    return [ns.strip() for ns in raw.split(",") if ns.strip()]


def check_namespace(namespace: str, write: bool = False) -> str:
    if write and namespace in PROTECTED_NAMESPACES:
        raise NamespaceNotAllowedError(
            f"namespace '{namespace}' is protected: no actions allowed"
        )
    if namespace not in allowed_namespaces():
        raise NamespaceNotAllowedError(
            f"namespace '{namespace}' is not allowed; allowed: {', '.join(allowed_namespaces())}"
        )
    return namespace


# ---------- output helpers ----------


def to_json(data: Any, max_chars: int = MAX_CHARS) -> str:
    """Compact JSON for tool results; truncates long payloads and says so."""
    text = json.dumps(data, separators=(",", ":"), default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return (
        text[: max_chars - 40] + f'..."[truncated {len(text) - max_chars + 40} chars]"'
    )


def tail_text(text: str, max_chars: int = MAX_CHARS) -> str:
    """Keep the END of a text (where the error usually is)."""
    if len(text) <= max_chars:
        return text
    return f"[truncated {len(text) - max_chars} chars]\n" + text[-max_chars:]


def collapse_repeats(lines: list[str]) -> list[str]:
    """Collapse consecutive identical lines: 'x', 'x', 'x' -> 'x  (x3)'."""
    out: list[str] = []
    prev, count = None, 0
    for line in [*lines, None]:
        if line == prev:
            count += 1
            continue
        if prev is not None:
            out.append(prev if count == 1 else f"{prev}  (x{count})")
        prev, count = line, 1
    return out


def age(ts: datetime | None, now: datetime | None = None) -> str | None:
    if ts is None:
        return None
    seconds = int(((now or datetime.now(UTC)) - ts).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{max(seconds, 0)}s"


def cpu_millicores(quantity: str | None) -> int | None:
    return None if quantity is None else int(parse_quantity(quantity) * 1000)


def mem_mib(quantity: str | None) -> int | None:
    return None if quantity is None else int(parse_quantity(quantity) / (1024 * 1024))


def compile_pattern(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def k8s_errors(fn: Callable[..., str]) -> Callable[..., str]:
    """Turn API/permission errors into one short, actionable line for the model (no stack traces)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except NamespaceNotAllowedError as exc:
            return f"error: {exc}"
        except ApiException as exc:
            hint = {
                401: "agent token expired or invalid (run `make kubeconfig`)",
                403: "forbidden by the agent identity RBAC (read-only, or operator outside its namespace)",
                404: "not found; check the name with get_pods/get_deployments",
            }.get(exc.status, exc.reason)
            return f"error: {exc.status} {hint}"
        except Exception as exc:  # noqa: BLE001 - tools must never crash the agent loop
            if "CERTIFICATE_VERIFY_FAILED" in str(
                exc
            ) or "SSLCertVerificationError" in str(exc):
                return (
                    "error: TLS verification of the Kubernetes API failed (environment issue, not the cluster). "
                    "Operator: under `langgraph dev` run `make dev` (DISABLE_TRUSTSTORE=true)."
                )
            return f"error: {type(exc).__name__}: {str(exc)[:200]}"

    return wrapper
