# cert: [CCA:D5] [LC:build]
"""Middleware stacks per role: cost (caching, summarization, context editing), safety (limits, PII, refusals).

Order matters: middleware runs in list order before the model and in reverse after it, and
`wrap_model_call` nests with the first entry outermost. History-shaping middleware goes first,
prompt caching last so the cache markers land on the final request.
"""

from __future__ import annotations

from langchain.agents.middleware import (
    AgentMiddleware,
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from sre_agent.llm import make_model

# Credentials that commonly leak into logs: key=value secrets, URL userinfo, AWS keys, JWTs, API keys.
SECRET_PATTERN = (
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*[^\s,;\"']+"
    r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
)


REFUSAL_TEXT = (
    "⛔ Blocked by the model provider's safety policy (category: {category}). "
    "I can't help with that request; it is excluded from this conversation's context. "
    "Ask me something else about the cluster."
)


def _is_refusal(msg: BaseMessage) -> bool:
    return (
        isinstance(msg, AIMessage)
        and msg.response_metadata.get("stop_reason") == "refusal"
    )


def without_refused_turns(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Drop every turn (human message .. refusal) that ended in a refusal.

    The API's classifiers read the whole history, so one refused request would get every later turn refused too.
    Dropping whole turns keeps tool_use/tool_result pairs intact.
    """
    out: list[BaseMessage] = []
    turn: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            out.extend(turn)
            turn = []
        turn.append(msg)
        if _is_refusal(msg):
            turn = []
    return out + turn


class RefusalGuard(AgentMiddleware):
    """`stop_reason: "refusal"` comes back as an empty HTTP 200: show the operator why, and quarantine the turn."""

    def _filter(self, request):
        return request.override(messages=without_refused_turns(request.messages))

    @staticmethod
    def _explain(response):
        for i, msg in enumerate(response.result):
            if _is_refusal(msg):
                category = (msg.response_metadata.get("stop_details") or {}).get(
                    "category"
                )
                response.result[i] = AIMessage(
                    content=REFUSAL_TEXT.format(category=category or "unspecified"),
                    id=msg.id,
                    response_metadata=msg.response_metadata,  # keeps stop_reason so later turns can skip it
                    usage_metadata=msg.usage_metadata,
                )
        return response

    def wrap_model_call(self, request, handler):
        return self._explain(handler(self._filter(request)))

    async def awrap_model_call(self, request, handler):
        return self._explain(await handler(self._filter(request)))


def _caching() -> AnthropicPromptCachingMiddleware:
    # 5m TTL: an investigation makes several calls within seconds; 1h writes cost 2x and only pay off on bursty reuse.
    return AnthropicPromptCachingMiddleware(ttl="5m")


def specialist_stack(redact_secrets: bool = False) -> list:
    stack = [
        # Old tool outputs are the bulk of a specialist's context; keep the 2 most recent in full.
        ContextEditingMiddleware(
            edits=[
                ClearToolUsesEdit(
                    trigger=4000, keep=2, placeholder="[older tool output cleared]"
                )
            ]
        ),
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=10, exit_behavior="continue"),
    ]
    if redact_secrets:
        stack[:0] = [
            PIIMiddleware(
                "secret",
                detector=SECRET_PATTERN,
                strategy="redact",
                apply_to_input=False,
                apply_to_tool_results=True,
            ),
            PIIMiddleware(
                "email",
                strategy="redact",
                apply_to_input=False,
                apply_to_tool_results=True,
            ),
        ]
    return [*stack, _caching()]


def commander_stack() -> list:
    return [
        # Long incident threads: summarize older turns with the cheap model, keep the recent exchange verbatim.
        SummarizationMiddleware(
            model=make_model("summarizer"),
            trigger=("tokens", 12_000),
            keep=("messages", 8),
        ),
        ModelCallLimitMiddleware(run_limit=10, exit_behavior="end"),
        ToolCallLimitMiddleware(
            run_limit=8, exit_behavior="continue"
        ),  # snapshot + delegations per question
        hitl(),
        RefusalGuard(),
        _caching(),
    ]


def single_agent_stack() -> list:
    return [
        SummarizationMiddleware(
            model=make_model("summarizer"),
            trigger=("tokens", 12_000),
            keep=("messages", 8),
        ),
        ContextEditingMiddleware(
            edits=[
                ClearToolUsesEdit(
                    trigger=8000, keep=3, placeholder="[older tool output cleared]"
                )
            ]
        ),
        PIIMiddleware(
            "secret",
            detector=SECRET_PATTERN,
            strategy="redact",
            apply_to_input=False,
            apply_to_tool_results=True,
        ),
        ModelCallLimitMiddleware(run_limit=16, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=20, exit_behavior="continue"),
        hitl(),
        RefusalGuard(),
        _caching(),
    ]


# ---------- human-in-the-loop ----------


def _describe_change(tool_call: dict) -> str:
    from sre_agent.tools.actions import describe_undo

    name, args = tool_call["name"], tool_call["args"]
    ns = args.get("namespace")
    if name == "rollout_undo":
        try:
            return describe_undo(ns, args["deployment"], args.get("to_revision"))
        except Exception as exc:  # noqa: BLE001 - preview must never block the approval itself
            return f"Rollback {ns}/{args.get('deployment')} (preview unavailable: {type(exc).__name__})"
    if name == "rollout_restart":
        return f"Rolling restart of deployment {ns}/{args.get('deployment')} (pods replaced one by one)"
    if name == "restart_pod":
        return f"Delete pod {ns}/{args.get('pod')}; its controller recreates it"
    if name == "scale_deployment":
        return f"Scale deployment {ns}/{args.get('deployment')} to {args.get('replicas')} replicas"
    return f"{name} {args}"


def _describe_action(tool_call: dict, state, runtime) -> str:
    """What the approver sees: the concrete change and why, not just the tool name."""
    why = tool_call["args"].get("justification") or "(no justification given)"
    return f"{_describe_change(tool_call)}\nWhy: {why}"


def hitl() -> HumanInTheLoopMiddleware:
    """Every write tool pauses for approve / edit / reject. Needs a checkpointer (the server provides one)."""
    from sre_agent.tools.actions import ACTION_TOOLS

    review = {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": _describe_action,
    }
    return HumanInTheLoopMiddleware(interrupt_on={t.name: review for t in ACTION_TOOLS})
