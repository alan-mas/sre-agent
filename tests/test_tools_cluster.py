"""Integration tests against the lab cluster (0 tokens). Skipped if the cluster or agent kubeconfig is unavailable.

Expects `make cluster-up scenarios` (ground truth in docs/scenarios.md).
"""

import json
import os

import pytest

from sre_agent.k8s_client import core


def _cluster_up() -> bool:
    if not os.path.exists(
        os.getenv("AGENT_KUBECONFIG", "infra/rbac/generated/kubeconfig-agent-readonly")
    ):
        return False
    try:
        core().list_namespaced_pod("demo", limit=1, _request_timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.cluster,
    pytest.mark.skipif(not _cluster_up(), reason="lab cluster not reachable"),
]


def pod(app: str) -> str:
    return (
        core()
        .list_namespaced_pod("demo", label_selector=f"app={app}")
        .items[0]
        .metadata.name
    )


@pytest.fixture(scope="module")
def snap():
    from sre_agent.tools import cluster_health_snapshot

    return json.loads(cluster_health_snapshot.invoke({"namespace": "demo"}))[0]


def test_snapshot_ground_truth(snap):
    issues = {p["name"].rsplit("-", 2)[0]: p["issue"] for p in snap["unhealthy_pods"]}
    assert issues.get("payments-api") == "CrashLoop"
    assert issues.get("report-worker") == "OOMKilled"
    assert issues.get("checkout-web") == "ImagePull"
    assert issues.get("ml-batch") == "Unschedulable"
    assert issues.get("orders-api") == "NotReady"
    assert "podinfo" not in issues  # healthy control: no false positive
    assert "podinfo" in snap["deployments_ok"]  # eval finding (D22): absent from issues was misread as "does not exist"


def test_snapshot_rollout_and_saturation(snap):
    assert [d["name"] for d in snap["stalled_rollouts"]] == ["orders-api"]
    assert set(snap["deployments_down"]) == {
        "payments-api",
        "report-worker",
        "checkout-web",
        "ml-batch",
    }
    assert any(
        c.get("pod", "").startswith("search-indexer") and c["flag"] == "cpu_throttled"
        for c in snap["saturated_containers"]
    )


def test_previous_logs_show_root_cause():
    from sre_agent.tools.logs import get_pod_logs

    out = get_pod_logs.invoke(
        {"namespace": "demo", "pod": pod("payments-api"), "previous": True}
    )
    assert "connection refused" in out


def test_rollout_history_exposes_probe_change():
    from sre_agent.tools.inspector import get_rollout_history

    revs = json.loads(
        get_rollout_history.invoke({"namespace": "demo", "deployment": "orders-api"})
    )["revisions"]
    assert [r["containers"][0]["readiness"] for r in revs] == [
        "http /readyz:9898",
        "http /readyz:9797",
    ]


def test_guardrails():
    from sre_agent.tools.inspector import describe_pod, get_pods

    assert "not allowed" in get_pods.invoke({"namespace": "kube-system"})
    assert describe_pod.invoke({"namespace": "demo", "name": "nope"}).startswith(
        "error: 404"
    )
    with pytest.raises(Exception, match="(?i)forbidden"):
        core().list_namespaced_secret("demo")


def test_tls_error_under_truststore_is_actionable():
    """Regression: `langgraph dev` injects truststore; macOS rejects minikube's self-signed cert."""
    import subprocess
    import sys

    code = (
        "import truststore; truststore.inject_into_ssl();"
        "from sre_agent.tools.inspector import list_namespaces;"
        "print(list_namespaces.invoke({}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "make dev" in out or out.startswith(
        "[{"
    )  # actionable error on macOS; plain success elsewhere
