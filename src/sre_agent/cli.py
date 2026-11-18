# cert: [LC:monitor] [LC:build] [CCA:D5]
"""`uv run sre-ask "question" [--graph lean|single] [--tag scenario]` — one traced run with its exact cost.

Write actions pause for approval in the terminal: [a]pprove, [e]dit args (JSON), [r]eject with a reason.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sre_agent.agents import build_lean_agent, build_single_agent
from sre_agent.llm import tracker


def ask_decisions(interrupt_value: dict) -> dict:
    """Render a HITLRequest and collect one decision per action request."""
    decisions = []
    for req in interrupt_value["action_requests"]:
        print(f"\n⚠️  APPROVAL REQUIRED: {req['name']}")
        print(req.get("description", ""))
        print(f"args: {json.dumps(req['args'])}")
        choice = input("[a]pprove / [e]dit / [r]eject > ").strip().lower()[:1]
        if choice == "a":
            decisions.append({"type": "approve"})
        elif choice == "e":
            args = json.loads(input("new args as JSON > "))
            decisions.append(
                {"type": "edit", "edited_action": {"name": req["name"], "args": args}}
            )
        else:
            reason = (
                input("reason (optional) > ").strip() or "Rejected by the operator."
            )
            decisions.append({"type": "reject", "message": reason})
    return {"decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--graph", choices=["lean", "single"], default="lean")
    parser.add_argument(
        "--tag", default="adhoc", help="scenario name, stored as LangSmith metadata"
    )
    args = parser.parse_args()

    build = build_lean_agent if args.graph == "lean" else build_single_agent
    agent = build(checkpointer=InMemorySaver())  # HITL interrupts need a checkpointer
    calls_before = len(tracker()._read()["calls"])
    spent_before = tracker().spent()
    thread_id = str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "tags": [f"scenario:{args.tag}"],  # run_name / graph tag come from the graph itself
        "metadata": {"scenario": args.tag, "thread_id": thread_id},
    }
    started = time.monotonic()

    payload: object = {"messages": [{"role": "user", "content": args.question}]}
    while True:
        result = agent.invoke(payload, config=config)
        interrupts = result.get("__interrupt__")
        if not interrupts:
            break
        payload = Command(resume=ask_decisions(interrupts[0].value))

    elapsed = time.monotonic() - started
    calls = tracker()._read()["calls"][calls_before:]
    print(result["messages"][-1].text)
    print("\n---")
    by_role: dict[str, list[float]] = {}
    for c in calls:
        by_role.setdefault(c["tag"], []).append(c["cost_usd"])
    for role, costs in by_role.items():
        print(f"{role:10} calls={len(costs):2}  ${sum(costs):.4f}")
    print(
        f"total ${tracker().spent() - spent_before:.4f} · {elapsed:.0f}s (incl. approval wait) · "
        f"in={sum(c['input_tokens'] for c in calls)} out={sum(c['output_tokens'] for c in calls)} "
        f"cache_read={sum(c['cache_read'] for c in calls)} · budget left ${tracker().budget_usd - tracker().spent():.2f}"
    )
