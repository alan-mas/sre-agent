"""Budget guard tests — no network, no tokens spent."""

import warnings

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from sre_agent.budget import (
    BudgetCallbackHandler,
    BudgetExceededError,
    BudgetTracker,
    cost_usd,
)


def usage(inp=0, out=0, cache_read=0, write_5m=0):
    total_in = inp + cache_read + write_5m
    return {
        "input_tokens": total_in,
        "output_tokens": out,
        "total_tokens": total_in + out,
        "input_token_details": {
            "cache_read": cache_read,
            "ephemeral_5m_input_tokens": write_5m,
        },
    }


def test_cost_opus_plain():
    # 1M in * $4 + 1M out * $20
    assert cost_usd("claude-opus-5-5", usage(1_000_000, 1_000_000)) == pytest.approx(
        24.0
    )


def test_cost_cache_read_and_write():
    # 1M cache read * $0.20 + 1M cache write * $4 * 1.25
    assert cost_usd(
        "claude-opus-5-5", usage(cache_read=1_000_000, write_5m=1_000_000)
    ) == pytest.approx(5.20)


def test_haiku_long_context_tier():
    assert cost_usd("claude-haiku-5-5", usage(200_000)) == pytest.approx(
        0.10
    )  # 200k * $0.50


def test_unknown_model_priced_conservatively():
    assert cost_usd("claude-unknown", usage(1_000_000)) == pytest.approx(10.0)


def test_blocks_when_budget_reached(tmp_path):
    tracker = BudgetTracker(budget_usd=0.01, ledger_path=tmp_path / "ledger.json")
    handler = BudgetCallbackHandler(tracker, model="claude-opus-5-5")
    handler.on_chat_model_start({}, [])  # under budget: allowed

    msg = AIMessage(content="ok", usage_metadata=usage(1_000, 1_000))  # $0.024
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))

    assert tracker.spent() == pytest.approx(0.024)
    with pytest.raises(BudgetExceededError):
        handler.on_chat_model_start({}, [])


def test_ledger_persists_across_instances(tmp_path):
    path = tmp_path / "ledger.json"
    BudgetTracker(10, path).record("claude-haiku-5-5", usage(10_000, 1_000))
    assert BudgetTracker(10, path).spent() == pytest.approx(0.0015)


def test_real_model_blocked_before_any_request(tmp_path, monkeypatch):
    """With budget 0, make_model() must fail before reaching the network."""
    from sre_agent import llm

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("BUDGET_USD", "0")
    monkeypatch.setenv("BUDGET_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(llm, "_tracker", None)

    with pytest.raises(BudgetExceededError):
        llm.make_model("ic").invoke("hi")


def test_monthly_ledger_path(tmp_path):
    """`{month}` in the ledger path makes the cap reset each calendar month."""
    from datetime import UTC, datetime

    tracker = BudgetTracker(budget_usd=15, ledger_path=tmp_path / "ledger-{month}.json")
    month = datetime.now(UTC).strftime("%Y-%m")
    assert tracker.ledger_path == tmp_path / f"ledger-{month}.json"
    tracker.record("claude-haiku-5-5", {"input_tokens": 1000, "output_tokens": 100})
    assert tracker.ledger_path.exists()
    assert tracker.summary()["spent_usd"] > 0
