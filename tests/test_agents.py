"""Agent wiring tests (no tokens): tool scoping per role and prompt cacheability."""

import re

import pytest

from sre_agent import prompts
from sre_agent.agents import build_lean_agent, build_rag_agent, build_single_agent
from sre_agent.middleware import commander_stack, single_agent_stack, specialist_stack
from sre_agent.tools import INSPECTOR_TOOLS, LOG_TOOLS, METRICS_TOOLS
from sre_agent.tools.actions import ACTION_TOOLS
from sre_agent.tools.knowledge import KNOWLEDGE_TOOLS

ACTIONS = {t.name for t in ACTION_TOOLS}


def tool_names(agent) -> set[str]:
    return set(agent.get_graph().nodes["tools"].data.tools_by_name)


def test_commander_only_sees_snapshot_and_specialists(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert tool_names(build_lean_agent()) == {
        "cluster_health_snapshot",
        "k8s_inspector",
        "log_analyst",
        "metrics_analyst",
        *ACTIONS,
    }


def test_rag_commander_adds_only_the_runbook_analyst(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert tool_names(build_rag_agent()) == tool_names(build_lean_agent()) | {
        "runbook_analyst"
    }


def test_rag_prompt_extends_lean_prompt():
    assert prompts.INCIDENT_COMMANDER_RAG != prompts.INCIDENT_COMMANDER
    assert "runbook_analyst" in prompts.INCIDENT_COMMANDER_RAG
    assert "guidance, not evidence" in prompts.INCIDENT_COMMANDER_RAG


def test_runbook_analyst_has_no_cluster_or_write_tools():
    names = {t.name for t in KNOWLEDGE_TOOLS}
    assert names == {"search_runbooks", "get_document"}
    cluster = {t.name for t in INSPECTOR_TOOLS + LOG_TOOLS + METRICS_TOOLS}
    assert not names & (ACTIONS | cluster)


def test_single_agent_sees_every_tool(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert len(tool_names(build_single_agent())) == 16 + len(ACTIONS)


def test_prompts_are_static_for_caching():
    """Timestamps or IDs in a system prompt silently invalidate prompt caching."""
    for name in (
        "INSPECTOR",
        "LOG_ANALYST",
        "METRICS_ANALYST",
        "INCIDENT_COMMANDER",
        "INCIDENT_COMMANDER_RAG",
        "RUNBOOK_ANALYST",
        "SINGLE_AGENT",
    ):
        text = getattr(prompts, name)
        assert not re.search(r"\d{4}-\d{2}-\d{2}|\{|\}", text), name
        assert "untrusted" in text, name  # prompt-injection guidance present everywhere


def _hitl(stack):
    return next(
        (m for m in stack if type(m).__name__ == "HumanInTheLoopMiddleware"), None
    )


def test_every_write_tool_is_gated(monkeypatch):
    """Safety invariant: an agent that can write must gate EVERY write tool behind approval."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    for stack in (commander_stack(), single_agent_stack()):
        gated = {name for name, cfg in _hitl(stack).interrupt_on.items() if cfg}
        assert gated == ACTIONS


def test_specialists_cannot_write(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert not ACTIONS & {t.name for t in INSPECTOR_TOOLS + LOG_TOOLS + METRICS_TOOLS}
    assert _hitl(specialist_stack()) is None  # no write tools, so no approval needed


def test_every_write_tool_requires_a_justification():
    """The approver and the audit log always get the root cause, even if the model writes no prose."""
    for t in ACTION_TOOLS:
        assert "justification" in t.tool_call_schema.model_json_schema()["required"], (
            t.name
        )


def test_write_identity_is_only_reachable_from_gated_tools():
    """Structural guard: a new write tool cannot skip HITL by forgetting to join ACTION_TOOLS.

    Writes need the operator identity; only tools/actions.py may touch it, and every tool there must be gated.
    Everything else runs on the read-only ServiceAccount, which RBAC denies any write."""
    from pathlib import Path

    from langchain_core.tools import BaseTool

    from sre_agent.tools import actions

    src = Path(__file__).parent.parent / "src" / "sre_agent"
    allowed = {src / "k8s_client.py", src / "tools" / "actions.py"}
    for path in src.rglob("*.py"):
        if path not in allowed:
            text = path.read_text()
            assert not re.search(
                r"\b(core_write|apps_write|_operator_client)\b", text
            ), path
    defined = {v.name for v in vars(actions).values() if isinstance(v, BaseTool)}
    assert defined == ACTIONS


def test_kube_system_is_readable_but_never_writable(monkeypatch):
    """D47: reads may include kube-system (CoreDNS, kube-proxy); writes there are refused even if allowlisted."""
    from sre_agent.k8s_client import NamespaceNotAllowedError, check_namespace

    monkeypatch.setenv("AGENT_NAMESPACE_ALLOWLIST", "demo,kube-system")
    assert check_namespace("kube-system") == "kube-system"
    with pytest.raises(NamespaceNotAllowedError, match="protected"):
        check_namespace("kube-system", write=True)
    with pytest.raises(NamespaceNotAllowedError, match="not allowed"):
        check_namespace("kube-public")
