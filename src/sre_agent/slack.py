# cert: [CCA:D1] [CCA:D4] [LC:deploy] [LC:monitor]
"""`uv run sre-slack` — on-call approvals and questions from Slack (Socket Mode: no public URL needed).

`sre-alert` posts each triaged alert to `SLACK_CHANNEL`: diagnosis, the exact change (diff + Why), Approve/Reject
buttons and links to the LangSmith trace and the Agent Chat UI thread. This process listens for the button clicks
and resumes the paused thread on the Agent Server with the on-call's decision (`Command(resume=...)`), then replies
in the Slack thread with the verified outcome. The approver is checked against `SLACK_APPROVERS` and audited.
After an approved fix it posts a blameless postmortem draft in the thread (`postmortem.py`). Agent answers and the
postmortem carry 👍/👎 buttons that become LangSmith feedback (`oncall_helpful`) on that run.
`@sre-agent <question>` asks the agent: each Slack thread maps to one agent thread (via thread metadata), so a
mention inside an alert's thread continues that incident with all its context.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

_TEXT_LIMIT = 2900  # Slack section text max is 3000 chars


def configured() -> bool:
    return bool(os.getenv("SLACK_BOT_TOKEN") and os.getenv("SLACK_CHANNEL"))


def _clip(text: str) -> str:
    return text if len(text) <= _TEXT_LIMIT else text[: _TEXT_LIMIT - 1] + "…"


def _table_to_code(match: re.Match) -> str:
    """Markdown table -> aligned monospace block (Slack has no tables)."""
    rows = [
        [c.strip().replace("**", "") for c in line.strip().strip("|").split("|")]
        for line in match.group(0).strip().splitlines()
        if not re.fullmatch(r"\|?[\s:|-]+\|?", line.strip())
    ]
    widths = [
        max(len(r[i]) if i < len(r) else 0 for r in rows)
        for i in range(max(map(len, rows)))
    ]
    lines = [
        "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)).rstrip()
        for r in rows
    ]
    return "```\n" + "\n".join(lines) + "\n```\n"


def to_mrkdwn(text: str) -> str:
    """The agent writes Markdown; Slack renders its own mrkdwn (`*bold*`, `<url|label>`, no headings or tables)."""
    text = re.sub(
        r"(?:^\|.*\|[ \t]*(?:\n|$))+", _table_to_code, text, flags=re.MULTILINE
    )
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    return re.sub(r"^(\s*)[-*]\s+", r"\1• ", text, flags=re.MULTILINE)


def _links(result: dict) -> dict:
    parts = []
    if result.get("trace_url"):
        parts.append(f"<{result['trace_url']}|🔎 LangSmith trace>")
    if result.get("chat_url"):
        parts.append(f"<{result['chat_url']}|💬 Full detail in Agent Chat UI>")
    parts.append(f"{result['seconds']}s · ${result['cost_usd']:.4f}")
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": " · ".join(parts)}],
    }


def action_blocks(result: dict) -> list[dict]:
    """Proposed write actions with Approve/Reject buttons; the button value points at the paused thread."""
    ref = json.dumps(
        {"thread_id": result["thread_id"], "assistant_id": result["assistant_id"]}
    )
    blocks = []
    for req in result["pending"]:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _clip(
                        f"*Proposed action:* `{req['name']}`\n```{req.get('description', '')}```"
                    ),
                },
            }
        )
    blocks.append(
        {
            "type": "actions",
            "block_id": "decision",
            "elements": [
                {
                    "type": "button",
                    "action_id": "approve",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "value": ref,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Apply this change?"},
                        "text": {
                            "type": "plain_text",
                            "text": "It runs on the cluster, then gets verified and audited.",
                        },
                        "confirm": {"type": "plain_text", "text": "Approve"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "action_id": "reject",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "value": ref,
                },
            ],
        }
    )
    return blocks


def alert_blocks(payload: dict, metadata: dict, result: dict) -> list[dict]:
    from sre_agent.alerts import firing_alerts

    labels = firing_alerts(payload)[0].get("labels", {})
    target = "/".join(
        v for k in ("namespace", "deployment", "pod") if (v := labels.get(k))
    )
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 {metadata['alertname']}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{target or 'cluster'}* · severity `{labels.get('severity', 'unknown')}` · "
                f"triaged by `{result['assistant_id']}`",
            },
        },
    ]
    if (
        result["answer"] and not result["pending"]
    ):  # with an action, its diff + Why is the diagnosis
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _clip(to_mrkdwn(result["answer"]))},
            }
        )
    if result["pending"]:
        blocks += action_blocks(result)
    elif not result["answer"]:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Triage finished with no action proposed.",
                },
            }
        )
    blocks.append(_links(result))
    return blocks


def feedback_block(run_id: str) -> dict:
    """👍/👎 that land as LangSmith feedback on the run that produced the message."""
    return {
        "type": "actions",
        "block_id": "feedback",
        "elements": [
            {
                "type": "button",
                "action_id": "feedback_up",
                "text": {"type": "plain_text", "text": "👍 Helpful"},
                "value": run_id,
            },
            {
                "type": "button",
                "action_id": "feedback_down",
                "text": {"type": "plain_text", "text": "👎 Not helpful"},
                "value": run_id,
            },
        ],
    }


def _sections(text: str) -> list[dict]:
    """Long mrkdwn split on paragraph boundaries into section blocks (3000-char limit each)."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > _TEXT_LIMIT:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    chunks.append(current)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _clip(c)}}
        for c in chunks
        if c.strip()
    ]


def postmortem_blocks(pm: dict, trace_url: str | None) -> list[dict]:
    blocks = _sections(to_mrkdwn(pm["markdown"]))
    if pm.get("run_id"):
        blocks.append(feedback_block(pm["run_id"]))
    parts = [f"<{trace_url}|🔎 LangSmith trace>"] if trace_url else []
    parts.append(f"${pm['cost_usd']:.4f} · saved `{pm['path']}`")
    return blocks + [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(parts)}]}
    ]


def outcome_blocks(result: dict) -> list[dict]:
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _clip(to_mrkdwn(result["answer"] or "(no summary)")),
            },
        }
    ]
    if result["pending"]:  # the agent proposed a follow-up action: ask again
        blocks += action_blocks(result)
    elif result.get("run_id"):
        blocks.append(feedback_block(result["run_id"]))
    return blocks + [_links(result)]


def decisions(pending: list[dict], decision: str, user: str) -> dict:
    """HITL resume payload: one decision per pending action request, in order."""
    if decision == "approve":
        return {"decisions": [{"type": "approve"} for _ in pending]}
    message = f"Rejected by on-call {user} in Slack. Do not retry this action; summarize and stop."
    return {"decisions": [{"type": "reject", "message": message} for _ in pending]}


def allowed(user_id: str) -> bool:
    approvers = {
        u.strip() for u in os.getenv("SLACK_APPROVERS", "").split(",") if u.strip()
    }
    return not approvers or user_id in approvers


def _audit_decision(
    thread_id: str, pending: list[dict], decision: str, user: dict
) -> None:
    from sre_agent.tools.actions import AUDIT_LOG

    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": "approval",
        "decision": decision,
        "actions": [r["name"] for r in pending],
        "by": {
            "slack_id": user["id"],
            "name": user.get("username") or user.get("name"),
        },
        "channel": "slack",
        "thread_id": thread_id,
    }
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def slack_metadata(channel: str, root_ts: str, assistant_id: str) -> dict:
    """Thread metadata that links a Slack thread to an agent thread (searchable on the Agent Server)."""
    return {
        "slack_channel": channel,
        "slack_ts": root_ts,
        "sre_assistant": assistant_id,
    }  # server reserves "assistant_id"


def question_text(text: str) -> str:
    """`<@U123> why is X failing?` -> `why is X failing?`"""
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def post_alert(payload: dict, metadata: dict, result: dict) -> str:
    """Post the triaged alert; returns the message ts (the root of the incident's Slack thread)."""
    from slack_sdk import WebClient

    response = WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
        channel=os.environ["SLACK_CHANNEL"],
        text=f"🚨 {metadata['alertname']}: "
        + ("action awaiting approval" if result["pending"] else "triage complete"),
        blocks=alert_blocks(payload, metadata, result),
        unfurl_links=False,
    )
    return response["ts"]


def _status(blocks: list[dict], text: str, block_id: str = "decision") -> list[dict]:
    """Replace a button row with a status line so nobody can decide (or vote) twice."""
    kept = [b for b in blocks if b.get("block_id") != block_id]
    return kept + [{"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}]


def record_feedback(run_id: str, helpful: bool, user: dict) -> None:
    from langsmith import Client

    Client().create_feedback(
        run_id,
        key="oncall_helpful",
        score=int(helpful),
        comment=f"Slack feedback from {user.get('username') or user.get('name') or user['id']}",
        source_info={"channel": "slack", "slack_user": user["id"]},
    )


def main() -> None:
    from langgraph_sdk import get_sync_client
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from sre_agent.alerts import pending_actions, run_and_collect, trace_link, ui_link

    server = os.getenv("AGENT_SERVER_URL", "http://localhost:2024")
    ui = os.getenv("AGENT_CHAT_UI_URL", "https://agentchat.vercel.app")
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    def decide(body: dict, client, decision: str) -> None:
        user, channel = body["user"], body["channel"]["id"]
        message = body["message"]
        if not allowed(user["id"]):
            client.chat_postEphemeral(
                channel=channel,
                user=user["id"],
                text="You are not an approver for this channel.",
            )
            return
        ref = json.loads(body["actions"][0]["value"])
        thread_id, assistant_id = ref["thread_id"], ref["assistant_id"]
        lg = get_sync_client(url=server)
        pending = pending_actions(lg.threads.get_state(thread_id))
        if not pending:
            client.chat_update(
                channel=channel,
                ts=message["ts"],
                blocks=_status(message["blocks"], "Already handled."),
            )
            return
        verb = "approved" if decision == "approve" else "rejected"
        icon = "⏳" if decision == "approve" else "🚫"
        client.chat_update(
            channel=channel,
            ts=message["ts"],
            text=f"{verb} by {user['id']}",
            blocks=_status(
                message["blocks"],
                f"{icon} {verb.capitalize()} by <@{user['id']}> · resuming the agent…",
            ),
        )
        _audit_decision(thread_id, pending, decision, user)
        result = run_and_collect(
            lg,
            thread_id,
            assistant_id,
            command={"resume": decisions(pending, decision, user["id"])},
        )
        result["chat_url"] = ui_link(ui, server, assistant_id, thread_id)
        result["trace_url"] = trace_link(result["run_id"])
        client.chat_postMessage(
            channel=channel,
            thread_ts=message.get("thread_ts") or message["ts"],
            text=result["answer"][:200] or "done",
            blocks=outcome_blocks(result),
            unfurl_links=False,
        )
        if decision == "approve" and not result["pending"]:
            post_postmortem(
                client,
                channel,
                message.get("thread_ts") or message["ts"],
                lg,
                thread_id,
            )
        done = "✅" if decision == "approve" else "🚫"
        client.chat_update(
            channel=channel,
            ts=message["ts"],
            text=f"{verb} by {user['id']}",
            blocks=_status(
                message["blocks"],
                f"{done} {verb.capitalize()} by <@{user['id']}> · outcome in thread",
            ),
        )

    def post_postmortem(
        client, channel: str, thread_ts: str, lg, thread_id: str
    ) -> None:
        from sre_agent.postmortem import draft

        try:
            pm = draft(lg, thread_id)
        except Exception as exc:  # noqa: BLE001 - the fix already happened; the draft is a bonus
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"Could not draft the postmortem ({type(exc).__name__}).",
            )
            return
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="📝 Postmortem draft (blameless)",
            blocks=postmortem_blocks(
                pm, trace_link(pm["run_id"]) if pm.get("run_id") else None
            ),
            unfurl_links=False,
        )

    def vote(body: dict, client, helpful: bool) -> None:
        user, channel, message = body["user"], body["channel"]["id"], body["message"]
        try:
            record_feedback(body["actions"][0]["value"], helpful, user)
            text = f"{'👍' if helpful else '👎'} feedback recorded in LangSmith by <@{user['id']}>, thanks"
        except Exception as exc:  # noqa: BLE001 - keep the buttons so they can retry
            client.chat_postEphemeral(
                channel=channel,
                user=user["id"],
                text=f"Feedback not recorded ({type(exc).__name__}).",
            )
            return
        client.chat_update(
            channel=channel,
            ts=message["ts"],
            text=message.get("text", ""),
            blocks=_status(message["blocks"], text, "feedback"),
        )

    @app.action("feedback_up")
    def on_feedback_up(ack, body, client):
        ack()
        vote(body, client, helpful=True)

    @app.action("feedback_down")
    def on_feedback_down(ack, body, client):
        ack()
        vote(body, client, helpful=False)

    @app.action("approve")
    def on_approve(ack, body, client):
        ack()  # Slack needs the ack within 3 s; the agent runs after it
        decide(body, client, "approve")

    @app.action("reject")
    def on_reject(ack, body, client):
        ack()
        decide(body, client, "reject")

    def thread_for(lg, channel: str, root_ts: str) -> tuple[str, str]:
        """Agent thread behind a Slack thread: the alert's thread if this is one, else a new one."""
        found = lg.threads.search(
            metadata={"slack_channel": channel, "slack_ts": root_ts}, limit=1
        )
        if found:
            return found[0]["thread_id"], found[0]["metadata"].get(
                "sre_assistant", "sre_lean"
            )
        meta = {"trigger": "slack", **slack_metadata(channel, root_ts, "sre_lean")}
        return lg.threads.create(metadata=meta)["thread_id"], "sre_lean"

    def react(client, channel: str, ts: str, name: str) -> None:
        try:
            client.reactions_add(channel=channel, timestamp=ts, name=name)
        except Exception:  # noqa: BLE001, S110 - a missing emoji is cosmetic
            pass

    @app.event("app_mention")
    def on_mention(event, client):
        channel, root = event["channel"], event.get("thread_ts") or event["ts"]
        question = question_text(event.get("text", ""))
        if not question:
            client.chat_postMessage(
                channel=channel,
                thread_ts=root,
                text="Ask me about the cluster, e.g. `@sre-agent why is payments-api failing?`",
            )
            return
        lg = get_sync_client(url=server)
        thread_id, assistant_id = thread_for(lg, channel, root)
        if pending_actions(lg.threads.get_state(thread_id)):
            client.chat_postMessage(
                channel=channel,
                thread_ts=root,
                text="An action in this thread is awaiting approval: approve or reject it first.",
            )
            return
        react(client, channel, event["ts"], "eyes")
        try:
            result = run_and_collect(
                lg,
                thread_id,
                assistant_id,
                input={"messages": [{"role": "user", "content": question}]},
                metadata={"trigger": "slack", "slack_user": event.get("user")},
                multitask_strategy="reject",  # one run per thread at a time
            )
        except Exception as exc:  # noqa: BLE001 - tell the on-call instead of failing silently
            client.chat_postMessage(
                channel=channel,
                thread_ts=root,
                text=f"Could not run the agent ({type(exc).__name__}). Is `make dev` up?",
            )
            return
        result["chat_url"] = ui_link(ui, server, assistant_id, thread_id)
        result["trace_url"] = trace_link(result["run_id"])
        client.chat_postMessage(
            channel=channel,
            thread_ts=root,
            text=result["answer"][:200] or "done",
            blocks=outcome_blocks(result),
            unfurl_links=False,
        )
        react(client, channel, event["ts"], "white_check_mark")

    print(f"sre-slack: listening for approvals and @mentions (agent server {server})")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
