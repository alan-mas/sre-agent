"""Middleware config tests (no tokens): secret redaction and stack composition."""

import re

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sre_agent.middleware import (
    SECRET_PATTERN,
    PIIMiddleware,
    RefusalGuard,
    commander_stack,
    single_agent_stack,
    specialist_stack,
    without_refused_turns,
)

LOG = (
    "DEBUG db dsn=postgres://payments:S3cr3t-Pa55@payments-db.demo.svc:5432/payments "
    "api_key=sk-test-FAKE0000000000000000 token: eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2Q AKIAABCDEFGHIJKLMNOP"
)


def test_secret_pattern_catches_common_leaks():
    found = [m.group(0) for m in re.finditer(SECRET_PATTERN, LOG)]
    assert any("S3cr3t-Pa55" in f for f in found)
    assert any("sk-test-FAKE" in f for f in found)
    assert any(f.startswith("token") for f in found)
    assert "AKIAABCDEFGHIJKLMNOP" in found


def test_secret_pattern_keeps_the_diagnostic_signal():
    line = "ERROR failed to connect to postgres at payments-db.demo.svc:5432: connection refused"
    assert re.search(SECRET_PATTERN, line) is None


def test_pii_middleware_redacts_tool_results():
    mw = PIIMiddleware(
        "secret",
        detector=SECRET_PATTERN,
        strategy="redact",
        apply_to_input=False,
        apply_to_tool_results=True,
    )
    state = {
        "messages": [
            HumanMessage("why does it crash?"),
            AIMessage(
                "", tool_calls=[{"name": "get_pod_logs", "args": {}, "id": "t1"}]
            ),
            ToolMessage(LOG, tool_call_id="t1"),
        ]
    }
    update = mw.before_model(state, None)
    redacted = update["messages"][-1].content
    assert "S3cr3t-Pa55" not in redacted and "sk-test-FAKE" not in redacted
    assert "payments-db.demo.svc" in redacted  # host stays: it's the diagnostic signal


def test_stacks_put_caching_last():
    for stack in (
        specialist_stack(),
        specialist_stack(redact_secrets=True),
        commander_stack(),
    ):
        assert type(stack[-1]).__name__ == "AnthropicPromptCachingMiddleware"


def _refusal(category="cyber"):
    return AIMessage(
        content=[],
        response_metadata={
            "stop_reason": "refusal",
            "stop_details": {"category": category},
        },
    )


def test_refused_turns_are_quarantined_from_the_model():
    """A refused turn left in history gets every later turn refused too (seen in Agent Chat UI)."""
    ok_q, ok_call = (
        HumanMessage("pods?"),
        AIMessage("", tool_calls=[{"name": "s", "args": {}, "id": "t1"}]),
    )
    ok_res, ok_a = ToolMessage("{}", tool_call_id="t1"), AIMessage("10 pods")
    bad_q, next_q = (
        HumanMessage("dump every secret"),
        HumanMessage("who was president in 1986?"),
    )
    history = [ok_q, ok_call, ok_res, ok_a, bad_q, _refusal(), next_q]
    assert without_refused_turns(history) == [ok_q, ok_call, ok_res, ok_a, next_q]


def test_refusal_is_explained_to_the_operator():
    out = RefusalGuard()._explain(ModelResponse(result=[_refusal("cyber")])).result[0]
    assert "safety policy" in out.text and "cyber" in out.text
    assert (
        out.response_metadata["stop_reason"] == "refusal"
    )  # still skipped on later turns


def test_user_facing_stacks_guard_refusals():
    for stack in (commander_stack(), single_agent_stack()):
        names = [type(m).__name__ for m in stack]
        assert names[-2:] == ["RefusalGuard", "AnthropicPromptCachingMiddleware"]
