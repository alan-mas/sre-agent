"""Postmortem facts: timeline, approvals, similar incidents, transcript and rendering (no tokens, no server)."""

from sre_agent.postmortem import (
    ActionItem,
    Postmortem,
    decisions_for,
    render,
    similar_incidents,
    timeline,
    transcript,
)

AUDIT = [
    {
        "ts": "2026-10-20T21:00:00+00:00",
        "action": "rollout_undo",
        "outcome": "verified",
    },  # older, other thread
    {
        "ts": "2026-10-20T21:28:59+00:00",
        "event": "approval",
        "decision": "approve",
        "actions": ["rollout_undo"],
        "by": {"name": "oncall-user"},
        "channel": "slack",
        "thread_id": "t-now",
    },
    {
        "ts": "2026-10-20T21:29:04+00:00",
        "action": "rollout_undo",
        "outcome": "verified",
    },
    {
        "ts": "2026-10-20T20:10:00+00:00",
        "event": "approval",
        "decision": "approve",
        "actions": ["rollout_undo"],
        "by": {"name": "oncall-user"},
        "channel": "slack",
        "thread_id": "t-old",
    },
]
THREAD = {
    "thread_id": "t-now",
    "created_at": "2026-10-20T21:25:14+00:00",
    "metadata": {
        "trigger": "alert",
        "alertname": "KubeDeploymentRolloutStuck",
        "target": "demo/orders-api",
        "starts_at": "2026-10-20T21:25:00+00:00",
    },
}
RUNS = [
    {
        "created_at": "2026-10-20T21:31:34+00:00",
        "updated_at": "2026-10-20T21:31:54+00:00",
        "metadata": {"trigger": "slack"},
    },
    {
        "created_at": "2026-10-20T21:25:15+00:00",
        "updated_at": "2026-10-20T21:25:33+00:00",
        "metadata": {"trigger": "alert"},
    },
]


def test_approval_is_paired_with_the_action_that_followed_it():
    (d,) = decisions_for("t-now", AUDIT)
    assert (
        d["result"]["ts"] == "2026-10-20T21:29:04+00:00"
        and d["result"]["outcome"] == "verified"
    )


def test_timeline_is_built_from_facts_in_order():
    events = timeline(THREAD, RUNS, decisions_for("t-now", AUDIT))
    texts = [what for _, what in events]
    assert texts[0].startswith("Alert `KubeDeploymentRolloutStuck` fired")
    assert "triage started" in texts[1] and "paused for approval" in texts[2]
    assert "approved by oncall-user via slack" in texts[3] and "*verified*" in texts[4]
    assert len(texts) == 5  # the follow-up Slack question is not an incident event


class FakeThreads:
    def __init__(self, threads):
        self._threads = threads

    def search(self, metadata, limit):
        return [
            t
            for t in self._threads
            if all(t["metadata"].get(k) == v for k, v in metadata.items())
        ]


class FakeServer:
    def __init__(self, threads):
        self.threads = FakeThreads(threads)


def test_similar_incidents_counts_earlier_same_alert_and_their_fixes():
    older = {
        "thread_id": "t-old",
        "created_at": "2026-10-20T20:00:00+00:00",
        "metadata": {
            "trigger": "alert",
            "alertname": "KubeDeploymentRolloutStuck",
            "target": "demo/orders-api",
        },
    }
    other_target = {
        "thread_id": "t-x",
        "created_at": "2026-10-20T19:00:00+00:00",
        "metadata": {
            "trigger": "alert",
            "alertname": "KubeDeploymentRolloutStuck",
            "target": "demo/other",
        },
    }
    later = {
        "thread_id": "t-later",
        "created_at": "2026-10-20T22:00:00+00:00",
        "metadata": {"trigger": "alert", "alertname": "KubeDeploymentRolloutStuck"},
    }
    sim = similar_incidents(
        FakeServer([THREAD, older, other_target, later]), THREAD, AUDIT
    )
    assert sim == {
        "count": 1,
        "last": "2026-10-20T20:00:00+00:00",
        "fixes": {"rollout_undo": 1},
    }


def test_transcript_is_compact_and_truncates_tool_output():
    messages = [
        {"type": "human", "content": "AUTOMATED ALERT ..."},
        {
            "type": "ai",
            "content": [{"type": "thinking", "thinking": "secret reasoning"}],
            "tool_calls": [
                {"name": "k8s_inspector", "args": {"task": "check orders-api"}}
            ],
        },
        {"type": "tool", "name": "k8s_inspector", "content": "x" * 5000},
    ]
    text = transcript(messages)
    assert (
        "[operator/alert] AUTOMATED ALERT" in text and "[agent → k8s_inspector]" in text
    )
    assert "secret reasoning" not in text  # thinking blocks never reach the writer
    assert len(text) < 2000


def test_render_has_every_section_and_says_it_is_a_draft():
    pm = Postmortem(
        title="orders-api rollout stuck",
        summary="s",
        impact="none",
        root_cause="probe port",
        trigger="release 1.1.0",
        resolution="rollback",
        went_well=["fast"],
        went_wrong=["no check"],
        action_items=[ActionItem(action="add probe test", owner="service team")],
    )
    md = render(pm, [("2026-10-20T21:25:00+00:00", "Alert fired")], {"count": 0})
    for part in (
        "blameless",
        "**Timeline**",
        "2026-10-20 21:25:00 UTC — Alert fired",
        "**Similar incidents** — none",
        "**Action items**",
        "_(owner: service team)_",
        "review before publishing",
    ):
        assert part in md
