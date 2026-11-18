# cert: [CCA:D1] [CCA:D4] [LC:build]
"""Agents: Incident Commander + 3 specialists (lean, subagents-as-tools) and a single-agent baseline."""

from __future__ import annotations

from typing import Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from sre_agent import prompts
from sre_agent.llm import make_model
from sre_agent.middleware import commander_stack, single_agent_stack, specialist_stack
from sre_agent.tools import (
    INSPECTOR_TOOLS,
    LOG_TOOLS,
    METRICS_TOOLS,
    cluster_health_snapshot,
)
from sre_agent.tools.actions import ACTION_TOOLS


class Finding(BaseModel):
    resource: str = Field(
        description="kind/name, e.g. pod/payments-api-6d59 or deployment/orders-api"
    )
    signal: str = Field(
        description="What is wrong, e.g. CrashLoop, OOMKilled, cpu_throttled, ProgressDeadlineExceeded"
    )
    evidence: str = Field(
        description="Decisive fact: exact log line, reason + exit code, or number vs. limit"
    )


class SpecialistReport(BaseModel):
    """Compact report a specialist returns to the Incident Commander."""

    summary: str = Field(description="One or two sentences")
    findings: list[Finding] = Field(default_factory=list)
    suspected_root_cause: str | None = None
    confidence: Literal["low", "medium", "high"]
    suggested_next_check: str | None = None


def _specialist(
    name: str, system_prompt: str, tools: list, redact_secrets: bool = False
) -> object:
    return create_agent(
        model=make_model("subagent"),
        tools=tools,
        system_prompt=system_prompt,
        response_format=ProviderStrategy(SpecialistReport),
        middleware=specialist_stack(redact_secrets),
        name=name,
    )


def _run_specialist(agent, task: str) -> str:
    """Isolated context: the specialist sees only the task, and returns only its structured report."""
    result = agent.invoke({"messages": [{"role": "user", "content": task}]})
    report = result.get("structured_response")
    if report is None:  # hit the call limit before producing a report
        last = result["messages"][-1].content if result.get("messages") else ""
        return f'{{"summary":"incomplete: specialist stopped before reporting","partial":{str(last)[:500]!r}}}'
    return report.model_dump_json(exclude_none=True)


def _traced(agent, graph: str):
    """Bake trace identity into the graph so CLI, server (UI/Studio), alerts and evals all tag runs the same way."""
    return agent.with_config(
        run_name=f"sre_{graph}", tags=[f"graph:{graph}"], metadata={"graph": graph}
    )


def build_lean_agent(checkpointer=None):
    inspector = _specialist("k8s_inspector", prompts.INSPECTOR, INSPECTOR_TOOLS)
    log_analyst = _specialist(
        "log_analyst", prompts.LOG_ANALYST, LOG_TOOLS, redact_secrets=True
    )
    metrics_analyst = _specialist(
        "metrics_analyst", prompts.METRICS_ANALYST, METRICS_TOOLS
    )

    @tool("k8s_inspector", parse_docstring=True)
    def call_inspector(task: str) -> str:
        """Delegate to the Kubernetes Inspector: pods, deployments, rollout history, events, nodes, services.

        Args:
            task: Precise task with namespace, exact resource names and the question to answer.
        """
        return _run_specialist(inspector, task)

    @tool("log_analyst", parse_docstring=True)
    def call_log_analyst(task: str) -> str:
        """Delegate to the Log Analyst: container logs, crash reasons, exact error lines (incl. previous instance).

        Args:
            task: Precise task with namespace, exact pod names and the question to answer.
        """
        return _run_specialist(log_analyst, task)

    @tool("metrics_analyst", parse_docstring=True)
    def call_metrics_analyst(task: str) -> str:
        """Delegate to the Metrics Analyst: CPU/memory usage vs. requests/limits, OOM history, node pressure.

        Args:
            task: Precise task with namespace, exact pod names and the question to answer.
        """
        return _run_specialist(metrics_analyst, task)

    agent = create_agent(
        model=make_model("ic"),
        tools=[
            cluster_health_snapshot,
            call_inspector,
            call_log_analyst,
            call_metrics_analyst,
            *ACTION_TOOLS,
        ],
        system_prompt=prompts.INCIDENT_COMMANDER,
        middleware=commander_stack(),
        checkpointer=checkpointer,
        name="incident_commander",
    )
    return _traced(agent, "lean")


def build_single_agent(checkpointer=None):
    """Baseline (D10): one agent, every tool. Same model and answer format as the Commander."""
    agent = create_agent(
        model=make_model("ic"),
        tools=[
            cluster_health_snapshot,
            *INSPECTOR_TOOLS,
            *LOG_TOOLS,
            *METRICS_TOOLS,
            *ACTION_TOOLS,
        ],
        system_prompt=prompts.SINGLE_AGENT,
        middleware=single_agent_stack(),
        checkpointer=checkpointer,
        name="sre_single",
    )
    return _traced(agent, "single")
