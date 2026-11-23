# cert: [CCA:D1] [CCA:D4] [LC:build]
"""Agents: Incident Commander + 3 specialists (lean, subagents-as-tools), the same plus a Runbook Analyst (rag),
and a single-agent baseline."""

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
from sre_agent.tools.knowledge import KNOWLEDGE_TOOLS


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


class DocMatch(BaseModel):
    id: str = Field(description="Document id, e.g. RB-008 or PM-007")
    title: str
    why: str = Field(description="Why it fits the symptom, in one line")


class RunbookReport(BaseModel):
    """Compact report the Runbook Analyst returns to the Incident Commander."""

    summary: str = Field(description="One or two sentences: what the documents say")
    matches: list[DocMatch] = Field(default_factory=list)
    checks: list[str] = Field(
        default_factory=list,
        description="Read-only checks from the documents that confirm or rule out the cause",
    )
    escalation: str | None = Field(
        default=None, description="Owner or escalation rule the documents give"
    )
    no_match: bool = Field(default=False, description="True if no document fits")


class SpecialistReport(BaseModel):
    """Compact report a specialist returns to the Incident Commander."""

    summary: str = Field(description="One or two sentences")
    findings: list[Finding] = Field(default_factory=list)
    suspected_root_cause: str | None = None
    confidence: Literal["low", "medium", "high"]
    suggested_next_check: str | None = None


def _specialist(
    name: str,
    system_prompt: str,
    tools: list,
    redact_secrets: bool = False,
    report: type[BaseModel] = SpecialistReport,
) -> object:
    agent = create_agent(
        model=make_model("subagent"),
        tools=tools,
        system_prompt=system_prompt,
        response_format=ProviderStrategy(report),
        middleware=specialist_stack(redact_secrets),
        name=name,
    )
    # Agent identity on every child run, so LangSmith can filter and group cost/latency per specialist.
    return agent.with_config(tags=[f"agent:{name}"], metadata={"agent": name})


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
    return _build_commander("lean", checkpointer)


def build_rag_agent(checkpointer=None):
    """Lean graph plus the Runbook Analyst (D41)."""
    return _build_commander("rag", checkpointer)


def _build_commander(graph: str, checkpointer=None):
    inspector = _specialist("k8s_inspector", prompts.INSPECTOR, INSPECTOR_TOOLS)
    log_analyst = _specialist(
        "log_analyst", prompts.LOG_ANALYST, LOG_TOOLS, redact_secrets=True
    )
    metrics_analyst = _specialist(
        "metrics_analyst", prompts.METRICS_ANALYST, METRICS_TOOLS
    )

    @tool("k8s_inspector", parse_docstring=True)
    def call_inspector(task: str) -> str:
        """Delegate to the Kubernetes Inspector: pods, deployments, rollout history, events, nodes, services, autoscalers (HPA), routes (HTTPRoute/Ingress), network policies.

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

    delegates = [call_inspector, call_log_analyst, call_metrics_analyst]
    system_prompt = prompts.INCIDENT_COMMANDER
    if graph == "rag":
        runbook_analyst = _specialist(
            "runbook_analyst",
            prompts.RUNBOOK_ANALYST,
            KNOWLEDGE_TOOLS,
            report=RunbookReport,
        )

        @tool("runbook_analyst", parse_docstring=True)
        def call_runbook_analyst(task: str) -> str:
            """Delegate to the Runbook Analyst: runbooks and past postmortems for a symptom (known causes, checks, escalation).

            Args:
                task: The decisive symptom: exact error line or alert, affected workload and namespace.
            """
            return _run_specialist(runbook_analyst, task)

        delegates.append(call_runbook_analyst)
        system_prompt = prompts.INCIDENT_COMMANDER_RAG

    agent = create_agent(
        model=make_model("ic"),
        tools=[cluster_health_snapshot, *delegates, *ACTION_TOOLS],
        system_prompt=system_prompt,
        middleware=commander_stack(),
        checkpointer=checkpointer,
        name="incident_commander",
    )
    return _traced(agent, graph)


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
