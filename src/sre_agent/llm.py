# cert: [CCA:D5] [LC:build]
"""Single factory for every Claude model in the project, so all calls go through the budget guard."""

from __future__ import annotations

import os
from typing import Literal

from langchain_anthropic import ChatAnthropic

from sre_agent.budget import BudgetCallbackHandler, BudgetTracker

Role = Literal["ic", "subagent", "summarizer", "judge", "postmortem"]

_DEFAULTS: dict[Role, tuple[str, str]] = {
    "ic": ("claude-sonnet-5-5", "medium"),  # D27: matched Opus at ~40% of the cost
    "subagent": ("claude-haiku-5-5", "low"),
    "summarizer": ("claude-haiku-5-5", "low"),
    "judge": ("claude-haiku-5-5", "low"),
    "postmortem": ("claude-haiku-5-5", "medium"),
}

_tracker: BudgetTracker | None = None


def tracker() -> BudgetTracker:
    global _tracker
    if _tracker is None:
        _tracker = BudgetTracker.from_env()
    return _tracker


def model_for(role: Role) -> tuple[str, str]:
    """(model, effort) a role resolves to, after <ROLE>_MODEL / <ROLE>_EFFORT env overrides."""
    default_model, default_effort = _DEFAULTS[role]
    return (
        os.getenv(f"{role.upper()}_MODEL", default_model),
        os.getenv(f"{role.upper()}_EFFORT", default_effort),
    )


def make_model(role: Role, max_tokens: int = 4096) -> ChatAnthropic:
    """Model for a role; override with <ROLE>_MODEL / <ROLE>_EFFORT env vars (e.g. IC_MODEL)."""
    model, effort = model_for(role)
    return ChatAnthropic(
        model=model,
        max_tokens=max_tokens,
        effort=effort,  # -> output_config.effort
        # Preserved thinking: on Opus/Sonnet/Haiku 5.5 thinking blocks are bound to the exact history.
        # Client-side middleware (summarization, context editing, PII redaction) edits history, which
        # would 400 on newer accounts; drop_block makes the API discard the stale blocks instead.
        thinking={
            "type": "adaptive",
            "display": "summarized",  # readable reasoning in LangSmith traces; billed the same
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        },
        betas=["thinking-binding-controls-2026-08-01"],
        callbacks=[BudgetCallbackHandler(tracker(), model=model, tag=role)],
    )
