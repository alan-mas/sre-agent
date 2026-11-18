"""Slack approval message and resume payload (no tokens, no Slack, no server)."""

import json
from pathlib import Path

from sre_agent.alerts import pending_actions, run_metadata
from sre_agent.slack import (
    _status,
    alert_blocks,
    allowed,
    decisions,
    question_text,
    slack_metadata,
    to_mrkdwn,
)

PAYLOAD = json.loads(
    (Path(__file__).parent.parent / "infra" / "alerts" / "bad-rollout.json").read_text()
)
PENDING = [
    {
        "name": "rollout_undo",
        "args": {
            "namespace": "demo",
            "deployment": "orders-api",
            "justification": "probe port",
        },
        "description": "Rollback deployment demo/orders-api: revision 6 -> 5\nWhy: probe port",
    }
]
RESULT = {
    "thread_id": "t-1",
    "assistant_id": "sre_lean",
    "run_id": "r-1",
    "answer": "",
    "pending": PENDING,
    "seconds": 17,
    "cost_usd": 0.0154,
    "trace_url": "https://smith.langchain.com/o/x/projects/p/y/r/r-1?poll=true",
    "chat_url": "https://agentchat.vercel.app/?threadId=t-1",
}


def _text(blocks):
    return json.dumps(blocks)


def test_alert_message_shows_change_buttons_and_links():
    blocks = alert_blocks(PAYLOAD, run_metadata(PAYLOAD, "bad-rollout"), RESULT)
    text = _text(blocks)
    assert "KubeDeploymentRolloutStuck" in text and "demo/orders-api" in text
    assert "revision 6 -> 5" in text and "Why: probe port" in text
    assert "LangSmith trace" in text and "Agent Chat UI" in text
    buttons = next(b for b in blocks if b.get("block_id") == "decision")["elements"]
    assert [b["action_id"] for b in buttons] == ["approve", "reject"]
    assert json.loads(buttons[0]["value"]) == {
        "thread_id": "t-1",
        "assistant_id": "sre_lean",
    }
    assert "confirm" in buttons[0]  # approving a cluster change takes a second click


def test_no_buttons_when_nothing_awaits_approval():
    blocks = alert_blocks(
        PAYLOAD,
        run_metadata(PAYLOAD, "bad-rollout"),
        {**RESULT, "pending": [], "answer": "Healthy."},
    )
    assert not any(b.get("block_id") == "decision" for b in blocks)


def test_status_replaces_buttons_so_nobody_decides_twice():
    blocks = alert_blocks(PAYLOAD, run_metadata(PAYLOAD, "bad-rollout"), RESULT)
    after = _status(blocks, "approved")
    assert not any(b.get("block_id") == "decision" for b in after)
    assert after[-1]["elements"][0]["text"] == "approved"


def test_resume_payload_has_one_decision_per_action():
    assert decisions(PENDING * 2, "approve", "U1") == {
        "decisions": [{"type": "approve"}] * 2
    }
    rejected = decisions(PENDING, "reject", "U1")["decisions"]
    assert rejected[0]["type"] == "reject" and "U1" in rejected[0]["message"]


def test_approver_allowlist(monkeypatch):
    monkeypatch.delenv("SLACK_APPROVERS", raising=False)
    assert allowed("U9")
    monkeypatch.setenv("SLACK_APPROVERS", "U1, U2")
    assert allowed("U2") and not allowed("U9")


def test_pending_actions_reads_the_hitl_interrupt():
    state = {"tasks": [{"interrupts": [{"value": {"action_requests": PENDING}}]}]}
    assert pending_actions(state) == PENDING
    assert pending_actions({"tasks": []}) == []


def test_mention_text_drops_the_bot_handle():
    assert (
        question_text("<@U0BOT> why is payments-api failing?")
        == "why is payments-api failing?"
    )
    assert question_text("<@U0BOT>") == ""


def test_slack_thread_metadata_links_back_to_the_agent_thread():
    assert slack_metadata("C1", "1700.01", "sre_lean") == {
        "slack_channel": "C1",
        "slack_ts": "1700.01",
        "sre_assistant": "sre_lean",
    }


def test_markdown_becomes_slack_mrkdwn():
    md = "## Status\n**Impact** — none\n- see [trace](https://x.y/z)"
    assert to_mrkdwn(md) == "*Status*\n*Impact* — none\n• see <https://x.y/z|trace>"


def test_markdown_tables_become_monospace_blocks():
    md = "Diff:\n| | rev 10 | rev 11 |\n|---|---|---|\n| probe | **9797** | 9898 |\nDone."
    out = to_mrkdwn(md)
    assert "```\n" in out and "|---" not in out and "**" not in out
    assert "probe  9797    9898" in out and out.endswith("Done.")


def test_answers_carry_feedback_buttons_for_their_run():
    from sre_agent.slack import outcome_blocks

    blocks = outcome_blocks({**RESULT, "pending": [], "answer": "Healthy."})
    fb = next(b for b in blocks if b.get("block_id") == "feedback")
    assert [e["action_id"] for e in fb["elements"]] == ["feedback_up", "feedback_down"]
    assert {e["value"] for e in fb["elements"]} == {"r-1"}
    assert not any(
        b.get("block_id") == "feedback" for b in outcome_blocks(RESULT)
    )  # decide first, vote later


def test_vote_replaces_only_the_feedback_row():
    from sre_agent.slack import feedback_block

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "x"}},
        feedback_block("r-1"),
    ]
    after = _status(blocks, "👍 recorded", "feedback")
    assert (
        after[0]["type"] == "section"
        and after[-1]["elements"][0]["text"] == "👍 recorded"
    )
    assert not any(b.get("block_id") == "feedback" for b in after)


def test_long_postmortem_is_split_into_valid_sections():
    from sre_agent.slack import postmortem_blocks

    md = "\n\n".join(f"**Section {i}** — " + "word " * 150 for i in range(8))
    blocks = postmortem_blocks(
        {
            "markdown": md,
            "run_id": "pm-1",
            "cost_usd": 0.0015,
            "path": ".logs/postmortems/t.md",
        },
        None,
    )
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) > 1 and all(len(b["text"]["text"]) <= 3000 for b in sections)
    assert any(b.get("block_id") == "feedback" for b in blocks)
