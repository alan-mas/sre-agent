# cert: [CCA:D5] [LC:build] [LC:monitor]
"""Local hard budget for Claude spend.

Every chat model built by `sre_agent.llm.make_model` carries `BudgetCallbackHandler`:
- before each call: refuses to start if the ledger already reached the budget;
- after each call: prices `usage_metadata` and appends it to a JSON ledger.

The ledger lives on disk so the cap holds across processes, restarts and eval runs.
The cap is monthly: the default ledger path contains `{month}` (UTC `YYYY-MM`), so each
calendar month starts a fresh ledger; old months stay on disk for review.
This is a client-side guard: it only sees calls made through this code.
"""

from __future__ import annotations

import json
import os
import threading
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

DEFAULT_BUDGET_USD = 15.0  # per calendar month
DEFAULT_WARN_RATIO = 0.8
DEFAULT_LEDGER = ".budget/ledger-{month}.json"


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    output: float
    cache_read: float
    long_input: float | None = None  # price above `long_threshold` prompt tokens
    long_output: float | None = None
    long_threshold: int = 100_000


# Anthropic first-party prices (2026-10). Cache writes: 1.25x input (5m TTL), 2x (1h TTL).
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.0, output=20.0, cache_read=0.20),
    "claude-sonnet-5-5": Price(input=2.0, output=10.0, cache_read=0.20),
    "claude-haiku-5-5": Price(
        input=0.10, output=0.50, cache_read=0.01, long_input=0.50, long_output=2.50
    ),
    "claude-fable-5-1": Price(input=10.0, output=50.0, cache_read=0.25),
}
# Unknown models are priced at the most expensive tier so the guard errs on the safe side.
FALLBACK_PRICE = PRICES["claude-fable-5-1"]


class BudgetExceededError(RuntimeError):
    """Raised before a model call when the budget is exhausted."""


def cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Price one call from LangChain `usage_metadata` (input_tokens already includes cache tokens)."""
    price = PRICES.get(model, FALLBACK_PRICE)
    details = usage.get("input_token_details") or {}
    total_in = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cache_read = details.get("cache_read") or 0
    write_5m = details.get("ephemeral_5m_input_tokens") or 0
    write_1h = details.get("ephemeral_1h_input_tokens") or 0
    if not (write_5m or write_1h):
        write_5m = details.get("cache_creation") or 0
    uncached = max(total_in - cache_read - write_5m - write_1h, 0)

    in_price, out_price = price.input, price.output
    if price.long_input is not None and total_in > price.long_threshold:
        in_price, out_price = price.long_input, price.long_output or out_price

    return (
        uncached * in_price
        + write_5m * in_price * 1.25
        + write_1h * in_price * 2.0
        + cache_read * price.cache_read
        + out * out_price
    ) / 1_000_000


class BudgetTracker:
    """Thread-safe JSON ledger: {"spent_usd": float, "calls": [...]}."""

    def __init__(
        self,
        budget_usd: float,
        ledger_path: Path,
        warn_ratio: float = DEFAULT_WARN_RATIO,
    ):
        self.budget_usd = budget_usd
        self._ledger_template = str(ledger_path)
        self.warn_ratio = warn_ratio
        self._lock = threading.Lock()

    @property
    def ledger_path(self) -> Path:
        """Current ledger file; `{month}` in the path is replaced by the UTC `YYYY-MM`."""
        month = datetime.now(UTC).strftime("%Y-%m")
        return Path(self._ledger_template.replace("{month}", month))

    @classmethod
    def from_env(cls) -> BudgetTracker:
        return cls(
            budget_usd=float(os.getenv("BUDGET_USD", DEFAULT_BUDGET_USD)),
            ledger_path=Path(os.getenv("BUDGET_LEDGER", DEFAULT_LEDGER)),
            warn_ratio=float(os.getenv("BUDGET_WARN_RATIO", DEFAULT_WARN_RATIO)),
        )

    def _read(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"spent_usd": 0.0, "calls": []}
        return json.loads(self.ledger_path.read_text())

    def spent(self) -> float:
        with self._lock:
            return self._read()["spent_usd"]

    def check(self) -> None:
        spent = self.spent()
        if spent >= self.budget_usd:
            raise BudgetExceededError(
                f"Claude budget exhausted: ${spent:.4f} of ${self.budget_usd:.2f} this month. "
                f"Raise BUDGET_USD or wait for next month ({self.ledger_path})."
            )

    def record(
        self, model: str, usage: dict[str, Any], tag: str | None = None
    ) -> float:
        cost = cost_usd(model, usage)
        with self._lock:
            data = self._read()
            data["spent_usd"] = round(data["spent_usd"] + cost, 6)
            data["calls"].append(
                {
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "model": model,
                    "tag": tag,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read": (usage.get("input_token_details") or {}).get(
                        "cache_read"
                    )
                    or 0,
                    "cost_usd": round(cost, 6),
                }
            )
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self.ledger_path.write_text(json.dumps(data, indent=1))
            spent = data["spent_usd"]
        if spent >= self.budget_usd * self.warn_ratio:
            warnings.warn(
                f"Claude spend at ${spent:.2f} of ${self.budget_usd:.2f}", stacklevel=2
            )
        return cost

    def summary(self) -> dict[str, Any]:
        with self._lock:
            data = self._read()
        by_model: dict[str, dict[str, float]] = {}
        for call in data["calls"]:
            row = by_model.setdefault(call["model"], {"calls": 0, "cost_usd": 0.0})
            row["calls"] += 1
            row["cost_usd"] = round(row["cost_usd"] + call["cost_usd"], 6)
        return {
            "ledger": str(self.ledger_path),
            "budget_usd": self.budget_usd,
            "spent_usd": data["spent_usd"],
            "remaining_usd": round(self.budget_usd - data["spent_usd"], 4),
            "by_model": by_model,
        }


class BudgetCallbackHandler(BaseCallbackHandler):
    """Blocks calls once over budget and records the cost of each completed call."""

    raise_error = True  # otherwise LangChain swallows exceptions raised in callbacks

    def __init__(self, tracker: BudgetTracker, model: str, tag: str | None = None):
        self.tracker = tracker
        self.model = model
        self.tag = tag

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list, **kwargs: Any
    ) -> None:
        self.tracker.check()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for gen in generations:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
                if usage:
                    self.tracker.record(self.model, dict(usage), self.tag)


def main() -> None:
    """`uv run sre-budget` — print current spend."""
    from dotenv import load_dotenv

    load_dotenv()
    print(json.dumps(BudgetTracker.from_env().summary(), indent=2))
