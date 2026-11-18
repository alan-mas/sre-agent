# cert: [CCA:D4] [CCA:D5] [LC:build] [LC:monitor]
"""`uv run sre-postmortem <thread_id>` — blameless postmortem draft from an incident thread.

Facts come from code, prose from the model: the timeline is built from the Agent Server (thread/run timestamps) and the
audit log (who approved, what was verified), similar incidents from thread metadata search (no tokens). One cheap
structured-output call (Haiku) writes the narrative sections from the thread transcript. `sre-slack` posts the draft
in the incident's Slack thread after an approved fix; the markdown is also saved to `.logs/postmortems/`.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

POSTMORTEM_DIR = Path(os.getenv("POSTMORTEM_DIR", ".logs/postmortems"))
_TRANSCRIPT_LIMIT = 15000  # chars sent to the model; tool outputs are already compact
_TOOL_RESULT_LIMIT = 1500


class ActionItem(BaseModel):
    action: str = Field(
        description="Concrete follow-up that prevents a repeat or speeds up detection"
    )
    owner: str = Field(
        description="Owning team or role (e.g. 'service team', 'platform'), never a person's name"
    )


class Postmortem(BaseModel):
    title: str = Field(description="Short incident title: service + what happened")
    summary: str = Field(
        description="2-3 sentences: what happened, impact, how it was resolved"
    )
    impact: str = Field(
        description="Who/what was affected and how badly (outage, degraded, release blocked, none)"
    )
    root_cause: str = Field(
        description="The underlying cause with its decisive evidence"
    )
    trigger: str = Field(description="The change or event that set it off")
    resolution: str = Field(description="What fixed it and how it was verified")
    went_well: list[str] = Field(
        description="What worked (detection, safeguards, speed)"
    )
    went_wrong: list[str] = Field(
        description="Gaps in process or systems, never individuals"
    )
    action_items: list[ActionItem]


# --- facts (code, no tokens) ---


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def transcript(messages: list[dict]) -> str:
    """Compact, model-readable transcript: questions, answers, tool calls and (truncated) tool results."""
    lines = []
    for m in messages:
        if m["type"] == "human":
            lines.append(f"[operator/alert] {_text(m['content'])}")
        elif m["type"] == "ai":
            if text := _text(m["content"]).strip():
                lines.append(f"[agent] {text}")
            for tc in m.get("tool_calls") or []:
                lines.append(f"[agent → {tc['name']}] {json.dumps(tc.get('args', {}))}")
        elif m["type"] == "tool":
            result = _text(m["content"])
            lines.append(f"[{m.get('name')} result] {result[:_TOOL_RESULT_LIMIT]}")
    text = "\n".join(lines)
    return (
        text
        if len(text) <= _TRANSCRIPT_LIMIT
        else text[:_TRANSCRIPT_LIMIT] + "\n[…truncated]"
    )


def _audit_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def decisions_for(thread_id: str, audit: list[dict]) -> list[dict]:
    """Approval events for a thread, each paired with the action entry that followed it (outcome)."""
    out = []
    for i, e in enumerate(audit):
        if e.get("event") == "approval" and e.get("thread_id") == thread_id:
            done = next(
                (
                    a
                    for a in audit[i + 1 :]
                    if a.get("action") in e.get("actions", []) and "outcome" in a
                ),
                None,
            )
            out.append({**e, "result": done})
    return out


def timeline(
    thread: dict, runs: list[dict], decisions: list[dict]
) -> list[tuple[str, str]]:
    meta = thread.get("metadata", {})
    events = []
    if starts := meta.get("starts_at"):
        events.append((starts, f"Alert `{meta.get('alertname')}` fired"))
    alert_runs = sorted(
        (r for r in runs if (r.get("metadata") or {}).get("trigger") == "alert"),
        key=lambda r: r["created_at"],
    )
    if alert_runs:
        events.append(
            (
                alert_runs[0]["created_at"],
                "Agent triage started (no human initiated it)",
            )
        )
        events.append(
            (
                alert_runs[0]["updated_at"],
                "Diagnosis ready; fix proposed and paused for approval",
            )
        )
    for d in decisions:
        who = d.get("by", {}).get("name") or "on-call"
        events.append(
            (
                d["ts"],
                f"`{', '.join(d['actions'])}` {d['decision']}d by {who} via {d.get('channel', '?')}",
            )
        )
        if res := d.get("result"):
            events.append(
                (res["ts"], f"`{res['action']}` applied, outcome: *{res['outcome']}*")
            )
    return sorted(events, key=lambda e: datetime.fromisoformat(e[0]))


def similar_incidents(lg, thread: dict, audit: list[dict], limit: int = 50) -> dict:
    """Earlier alert threads with the same alertname (and target when known), and how they were resolved."""
    meta = thread.get("metadata", {})
    if not meta.get("alertname"):
        return {"count": 0}
    found = [
        t
        for t in lg.threads.search(
            metadata={"trigger": "alert", "alertname": meta["alertname"]}, limit=limit
        )
        if t["thread_id"] != thread["thread_id"]
        and t["created_at"] < thread["created_at"]
        and (
            not meta.get("target")
            or t["metadata"].get("target") in (None, meta["target"])
        )
    ]
    if not found:
        return {"count": 0}
    fixes = Counter(
        a
        for t in found
        for d in decisions_for(t["thread_id"], audit)
        if d["decision"] == "approve"
        for a in d["actions"]
    )
    return {
        "count": len(found),
        "last": max(t["created_at"] for t in found),
        "fixes": dict(fixes),
    }


# --- prose (one structured-output call) ---


def write(
    transcript_text: str, facts: str, thread_id: str
) -> tuple[Postmortem, str | None]:
    """Returns the narrative and the LangSmith run id (for links and on-call feedback)."""
    from langchain_core.tracers.context import collect_runs

    from sre_agent.llm import make_model
    from sre_agent.prompts import POSTMORTEM

    writer = (
        make_model("postmortem", max_tokens=2048)
        .with_structured_output(Postmortem, method="json_schema")
        .with_config(
            run_name="postmortem",
            tags=["postmortem"],
            metadata={"thread_id": thread_id, "trigger": "postmortem"},
        )
    )
    with collect_runs() as runs:
        pm = writer.invoke(
            [
                {"role": "system", "content": POSTMORTEM},
                {
                    "role": "user",
                    "content": f"<facts>\n{facts}\n</facts>\n<transcript>\n{transcript_text}\n</transcript>",
                },
            ]
        )
    run_id = str(runs.traced_runs[0].id) if runs.traced_runs else None
    return pm, run_id


def _hhmm(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def render(pm: Postmortem, events: list[tuple[str, str]], similar: dict) -> str:
    """Markdown (saved to disk; Slack converts it with `to_mrkdwn`)."""
    md = [
        f"## 📝 Postmortem draft (blameless): {pm.title}",
        "",
        f"**Summary** — {pm.summary}",
        "",
    ]
    md += [
        f"**Impact** — {pm.impact}",
        f"**Trigger** — {pm.trigger}",
        f"**Root cause** — {pm.root_cause}",
    ]
    md += [f"**Resolution** — {pm.resolution}", "", "**Timeline**"]
    md += [f"- {_hhmm(ts)} — {what}" for ts, what in events] or ["- (no timeline data)"]
    if similar.get("count"):
        fixes = (
            ", ".join(f"`{a}` ×{n}" for a, n in similar.get("fixes", {}).items())
            or "none approved via Slack"
        )
        md += [
            "",
            f"**Similar incidents** — {similar['count']} earlier with the same alert (last: {_hhmm(similar['last'])}); fixes: {fixes}. Recurrence is itself an action item.",
        ]
    else:
        md += ["", "**Similar incidents** — none found for this alert."]
    md += ["", "**What went well**"] + [f"- {x}" for x in pm.went_well]
    md += ["", "**What went wrong**"] + [f"- {x}" for x in pm.went_wrong]
    md += ["", "**Action items**"] + [
        f"- {a.action} _(owner: {a.owner})_" for a in pm.action_items
    ]
    md += [
        "",
        "_Draft written by the agent from the incident thread and the audit log; review before publishing._",
    ]
    return "\n".join(md)


def draft(lg, thread_id: str, audit_path: Path | None = None) -> dict:
    """Build the postmortem for a thread; returns markdown, run id, cost and where it was saved."""
    from sre_agent.llm import tracker
    from sre_agent.tools.actions import AUDIT_LOG

    audit = _audit_entries(audit_path or AUDIT_LOG)
    thread = lg.threads.get(thread_id)
    runs = list(lg.runs.list(thread_id, limit=50))
    messages = lg.threads.get_state(thread_id)["values"]["messages"]
    decisions = decisions_for(thread_id, audit)
    events = timeline(thread, runs, decisions)
    similar = similar_incidents(lg, thread, audit)
    facts = "\n".join(f"{_hhmm(ts)} — {what}" for ts, what in events)

    spent_before = tracker().spent()
    pm, run_id = write(transcript(messages), facts, thread_id)
    markdown = render(pm, events, similar)
    POSTMORTEM_DIR.mkdir(parents=True, exist_ok=True)
    path = POSTMORTEM_DIR / f"{thread_id}.md"
    path.write_text(markdown + "\n")
    return {
        "markdown": markdown,
        "run_id": run_id,
        "cost_usd": round(tracker().spent() - spent_before, 4),
        "path": str(path),
        "similar": similar,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thread_id")
    parser.add_argument(
        "--url", default=os.getenv("AGENT_SERVER_URL", "http://localhost:2024")
    )
    args = parser.parse_args()

    from langgraph_sdk import get_sync_client

    result = draft(get_sync_client(url=args.url), args.thread_id)
    print(result["markdown"])
    print(
        f"\n---\nsaved {result['path']} · ${result['cost_usd']} · run {result['run_id']}"
    )
