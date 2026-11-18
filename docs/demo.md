# Demo (5–7 min)

> Goal: show **alert → multi-agent triage → human approval in Slack → verification → follow-up question → trace and cost**.
> Core message: *the agent investigates on its own, but never changes the cluster without a human, and every claim is measured.*

## Setup (10 min before, 0 tokens)

| Step | Command | Check |
|---|---|---|
| 1. Cluster | Docker running → `make cluster-up` | regenerates tokens (read-only 24 h, operator 8 h) |
| 2. Scenario | `make scenario-bad-rollout`, wait ~60 s | `make status`: `orders-api` with one new `0/1` pod and two old `1/1` |
| 3. Sanity | `make rbac-check` · `make test` | read-only identity cannot `delete pods`; tests green |
| 4. Server | `make dev` in another terminal | `curl localhost:2024/ok` → `{"ok":true}` |
| 5. Model | `.env`: `IC_MODEL=claude-sonnet-5-5` (D27) | restart `make dev` after changing it |
| 6. Slack bot | `make slack` in a third terminal | prints `sre-slack: listening for approvals and @mentions` |
| 7. Tabs | Slack alerts channel · Agent Chat UI · LangSmith project · terminal | — |

Fallbacks: if Slack fails, approve from the Agent Chat UI link (same thread, same interrupt). If the network or UI fails, run it from the terminal (`uv run sre-ask … --tag bad-rollout`, approve with `a`).

## Script

**0:00 — Context (30 s).**
A cluster with six real injected failures (crashloop, OOM, missing image, pending, CPU, broken release) and a healthy app as control. The agent has a cluster-wide **read-only** identity and a separate **operator** identity limited to `demo`, with an 8-hour token.

**0:30 — The alert (1 min).**
```bash
make alert-bad-rollout
```
A real Alertmanager payload (`KubeDeploymentRolloutStuck`). Nobody asks the agent anything: the alert triggers triage through the Agent Server SDK. Alert text enters as **untrusted, truncated data**.
→ In ~15–30 s it prints the diagnosis and `⏸️ awaiting approval: rollout_undo` with the revision diff, and **a message reaches the Slack channel** with the diff, the `Why:`, Approve/Reject buttons and links to LangSmith and the UI.

**1:30 — The reasoning (1.5 min).** Open the Agent Chat UI link from Slack.
- The commander first called `cluster_health_snapshot` (deterministic, ~1.3k tokens vs 36.7k for raw `kubectl`).
- It delegated to Haiku specialists with isolated contexts; each returns a structured report.
- Cause: the new revision moved the readiness probe to `:9797` → never Ready → `ProgressDeadlineExceeded`; the previous revision keeps serving (impact: blocked release, not an outage).

**3:00 — Human approval in Slack (1 min).** The message shows the **revision diff** and the **`Why:`** (justification required by schema: cause + evidence).
The on-call never leaves Slack. Approve asks for confirmation because it changes the cluster; only allowlisted approvers can decide. → **Approve** → confirm.
→ Buttons change to "⏳ Approved by @…" (nobody decides twice); the tool applies the change, **waits for the rollout (≤90 s) and verifies**; the commander's result lands **in the thread** and the message ends as "✅ Approved". Audit in `.logs/actions.jsonl`: the approval with the Slack user + the `verified` action.

**4:00 — Verification and follow-up (1 min).** `make status` → `orders-api` 2/2 on the good revision. In the alert thread: `@sreagent what was different between the broken revision and the one we rolled back to? anything else I should check before redeploying?` → it answers with the **incident's memory** (same thread) and proposes no actions. Show the **📝 blameless postmortem** the bot posted after the approval (real timeline, similar incidents, action items) and give it 👍: on-call feedback lands in LangSmith next to the cost.

**4:30 — Observability and cost (1.5 min).** In LangSmith:
- The `sre_lean` trace (🔎 link from Slack) with metadata `trigger=alert`, `alertname`, `scenario`, `thread_id`; the thread question appears with `trigger=slack`; the tree commander → specialists; cost per trace and per agent (`lc_agent_name`).
- The `sre_lean` vs `sre_single` experiment on the scenario dataset: root-cause accuracy (LLM judge), correct action / no unsafe action (code evaluators), cost and latency.
- Numbers (n=1): both graphs got every case right; per question with Sonnet: lean ~$0.016 / 15 s vs single ~$0.015 / 9 s. Multi-agent is justified by isolation, not cost, at this scale.
- What the evals caught: a false positive on the healthy control (fixed in the snapshot data) and approvals without justification (fixed in the tool schema).

**6:00 — Close (30 s).** A full incident costs ~$0.03 and under 30 s of agent time. In production: Alertmanager/PagerDuty as the trigger, remediation as a GitOps PR (Argo CD) instead of direct `kubectl`, workload identity and a secrets manager, and the same eval as a CI gate.

## After the demo
- `make scenario-bad-rollout` re-breaks the scenario (~60 s until `ProgressDeadlineExceeded`).
- After changing bot code, restart `make slack` (it doesn't hot-reload, unlike `make dev`).

## Likely questions
- *What if it proposes something dangerous?* → write tools only on the commander/single agent, all in `interrupt_on` (tested), namespaced RBAC, dry-run, guards, verification, audit; the eval measures `no_unsafe_action`.
- *Prompt injection from logs or alerts?* → marked as untrusted data, annotations truncated, specialists have no actions, secrets redacted in the Log Analyst.
- *Why multi-agent?* → cost/latency/accuracy numbers above and context isolation.
- *Why didn't the broken release cause an outage?* → `maxUnavailable: 0` + readiness probe: new pods never become Ready, so old ones keep serving and the rollout stalls. The real risk is that nobody notices.
- *How does Slack work without exposing the laptop?* → Socket Mode: outbound WebSocket; the bot only translates between Slack and the Agent Server (it doesn't call the LLM).
