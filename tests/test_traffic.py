"""Pure tests for the HPA, route and NetworkPolicy summaries (no cluster, no tokens)."""

from sre_agent.tools.traffic import (
    selects,
    summarize_hpa,
    summarize_httproute,
    summarize_ingress,
    summarize_netpol,
)

META = {"name": "x", "namespace": "demo"}


def test_hpa_without_metrics_reports_unknown_and_the_failing_condition():
    hpa = {
        "metadata": META,
        "spec": {
            "scaleTargetRef": {"kind": "Deployment", "name": "quote-api"},
            "minReplicas": 1,
            "maxReplicas": 4,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {"type": "Utilization", "averageUtilization": 50},
                    },
                }
            ],
        },
        "status": {
            "currentReplicas": 1,
            "conditions": [
                {"type": "AbleToScale", "status": "True"},
                {
                    "type": "ScalingActive",
                    "status": "False",
                    "reason": "FailedGetResourceMetric",
                    "message": "missing request for cpu",
                },
            ],
        },
    }
    row = summarize_hpa(hpa)
    assert row["metrics"] == ["cpu: unknown / target 50%"]
    assert row["problems"] == [
        {
            "type": "ScalingActive",
            "reason": "FailedGetResourceMetric",
            "message": "missing request for cpu",
        }
    ]


def test_hpa_at_max_is_a_problem_and_healthy_hpa_is_not():
    base = {
        "metadata": META,
        "spec": {"scaleTargetRef": {}, "maxReplicas": 3, "metrics": []},
    }
    limited = {
        **base,
        "status": {
            "conditions": [
                {
                    "type": "ScalingLimited",
                    "status": "True",
                    "reason": "TooManyReplicas",
                }
            ]
        },
    }
    healthy = {
        **base,
        "status": {"conditions": [{"type": "ScalingLimited", "status": "False"}]},
    }
    assert summarize_hpa(limited)["problems"][0]["reason"] == "TooManyReplicas"
    assert "problems" not in summarize_hpa(healthy)


def test_httproute_flags_missing_backend_and_wrong_port():
    route = {
        "metadata": META,
        "spec": {
            "parentRefs": [{"name": "lab", "namespace": "lab-system"}],
            "rules": [
                {
                    "backendRefs": [
                        {"name": "shop-web-v2", "port": 80},
                        {"name": "shop-web", "port": 8080},
                    ]
                }
            ],
        },
        "status": {
            "parents": [
                {
                    "conditions": [
                        {"type": "Accepted", "status": "True"},
                        {
                            "type": "ResolvedRefs",
                            "status": "False",
                            "reason": "BackendNotFound",
                        },
                    ]
                }
            ]
        },
    }
    row = summarize_httproute(route, {"shop-web": {80}})
    assert row["parents"] == ["lab-system/lab"]
    assert row["backends"][0]["problem"] == "service not found"
    assert "port not exposed" in row["backends"][1]["problem"]
    assert row["problems"][0]["reason"] == "BackendNotFound"


def test_httproute_without_status_was_not_accepted():
    route = {"metadata": META, "spec": {"parentRefs": [{"name": "lab"}]}}
    assert summarize_httproute(route, {})["problems"][0]["reason"] == "NoStatus"


def test_ingress_backends_are_checked_against_services():
    ing = {
        "metadata": META,
        "spec": {
            "rules": [
                {
                    "host": "a.lab",
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "backend": {
                                    "service": {"name": "web", "port": {"number": 80}}
                                },
                            }
                        ]
                    },
                }
            ]
        },
    }
    assert summarize_ingress(ing, {"web": {80}})["backends"] == [
        {"path": "a.lab/", "service": "web:80"}
    ]


def test_selector_semantics():
    labels = {"app": "api", "tier": "be"}
    assert selects(None, labels)  # empty selector selects everything
    assert selects({"matchLabels": {"app": "api"}}, labels)
    assert not selects({"matchLabels": {"role": "checkout"}}, labels)
    assert selects(
        {"matchExpressions": [{"key": "tier", "operator": "In", "values": ["be"]}]},
        labels,
    )
    assert not selects(
        {"matchExpressions": [{"key": "app", "operator": "DoesNotExist"}]}, labels
    )


def test_netpol_names_the_pods_a_rule_allows():
    pods = {
        "inventory-1": {"app": "inventory-api"},
        "storefront-1": {"app": "storefront"},
    }
    policy = {
        "metadata": META,
        "spec": {
            "podSelector": {"matchLabels": {"app": "inventory-api"}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [{"podSelector": {"matchLabels": {"role": "checkout"}}}],
                    "ports": [{"protocol": "TCP", "port": 9898}],
                }
            ],
        },
    }
    row = summarize_netpol(policy, pods)
    assert row["selected_pods"] == ["inventory-1"]
    assert row["ingress_denied_pods"] == [
        {"pod": "storefront-1", "labels": "app=storefront"}
    ]
    assert row["ingress"] == [
        {"from": ["pods(role=checkout)"], "ports": ["TCP/9898"], "matching_pods": []}
    ]


def test_netpol_type_without_rules_denies_all():
    policy = {
        "metadata": META,
        "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "egress": []},
    }
    row = summarize_netpol(policy, {"p": {}})
    assert row["ingress"] == "deny all" and row["egress"] == "deny all"


def test_netpol_denied_pods_are_skipped_when_peers_leave_the_namespace():
    policy = {
        "metadata": META,
        "spec": {"podSelector": {}, "ingress": [{"from": [{"namespaceSelector": {}}]}]},
    }
    assert "ingress_denied_pods" not in summarize_netpol(policy, {"p": {}})
