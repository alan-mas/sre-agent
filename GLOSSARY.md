# Glossary

The shared vocabulary of this project. Code, prompts, docs, evals and commits use each term with the meaning below; when two terms are easy to confuse, the entry says what it is **not**. Decision ids (`Dnn`) point to [`docs/decisions.md`](docs/decisions.md). Terms marked *planned* are decided but not built yet.

## Incidents

**Incident**
: An unplanned interruption to a service or reduction in its quality (ITIL 4), whether the impact is customer-facing or internal. It includes a component failure that has not yet affected the service (e.g. lost redundancy) and a degradation that recovers on its own (e.g. a latency spike absorbed by autoscaling). Every incident gets a **severity**; the severity, not the existence of impact, decides the response. Declare early: it is cheaper to declare and close than to declare late.

**Severity**
: The impact level of an incident, from critical (broad customer impact, executive and public communication) to minor (internal only, no customer impact; handled as a ticket or a logged review). It sets who responds, how fast, and whether a postmortem is required.

**Event**
: A change of state that matters for operating a service (a pod restart, a scaling action, a Kubernetes `Warning`). It becomes an incident only when it interrupts or degrades a service. *Not* the same as a Kubernetes Event object, although those are one source of events.

**Alert**
: An automated notification that a signal crossed a threshold. It is an input to the agent, not proof of an incident: an alert can fire without an incident (false positive), and an incident can happen with no alert (missed detection).

**Scenario**
: A reproducible failure that we inject on purpose into the lab (`make scenario-<name>`), with a verified signal and a known root cause. Scenarios are the eval ground truth ([`docs/scenarios.md`](docs/scenarios.md), D13). The `healthy` scenario is the negative control. *Not* the environment where an incident happened.

**Root cause**
: The underlying condition that explains the observed signals (e.g. a 64Mi limit for a process that needs ~150Mi). *Not* the symptom (`OOMKilled`).

**Runbook**
: A written, step-by-step procedure for diagnosing or mitigating a known type of problem. Source of truth: Confluence (D42, *planned*).

**Postmortem**
: A blameless write-up after an incident: impact, timeline, root cause, what went well, action items. The agent posts a draft after an approved action (D29); a human reviews it.

## Agents

**Incident Commander**
: The orchestrator agent (`incident_commander`, graph `sre_lean`). It delegates investigation to specialists, correlates their findings, proposes remediations and is the only agent with write tools (D1, D19).

**Specialist** (subagent)
: A read-only agent with a narrow tool set, exposed to the commander as a tool: `inspector`, `log_analyst`, `metrics_analyst`. It sees only its own tool output, which keeps the commander's context small (D1, D3). The **Runbook Analyst** is a *planned* specialist (D41).

**Single agent** (`sre_single`)
: The baseline: one agent with all tools and no delegation. Multi-agent must beat or justify itself against it with data (D10, D23).

**Lean graph** (`sre_lean`)
: The multi-agent graph: Incident Commander plus specialists.

## Tools and data

**Read-only tool**
: A tool that only reads cluster state (pods, events, logs, metrics). Specialists get only these.

**Write tool**
: A tool that changes cluster state: `restart_pod`, `rollout_restart`, `rollout_undo`, `scale_deployment`. It requires a `justification` and an approval (HITL) before it runs (D19, D22).

**Snapshot**
: A compact, pre-digested summary of a namespace's health (problem pods, warnings, `deployments_ok`), used as the first look instead of raw `kubectl` output (D14).

**Tool output budget** (`TOOL_MAX_CHARS`)
: The cap on any tool's output (4000 characters), with one-line errors, to keep token use predictable (D14).

## Actions and safety

**Remediation**
: An operational action that restores service without changing configuration: restart, rollout restart, rollback, scale. Executed only after approval.

**Change request**
: A proposed configuration change (image, resources, env, probes, missing dependencies) with a manifest diff, evidence and owner, meant as a GitOps Pull Request. The agent drafts it and never applies or merges it (D32). *Not* a remediation.

**HITL** (human in the loop)
: The approval step that pauses the graph before any write tool runs (`interrupt_on`). A human approves or rejects in the Agent Chat UI or Slack (D5/D28).

**Justification**
: A mandatory argument on every write tool explaining why the action is needed. It is shown on the approval card as `Why:` and stored in the audit log (D22).

**Scope**
: The set of questions the agent answers: Kubernetes operations and incidents. Out-of-scope requests get a one-line refusal with no tool calls (D24).

**Refusal**
: A model response with `stop_reason: "refusal"`. `RefusalGuard` explains it to the operator and removes the refused turn from the model's context (D25). *Not* an out-of-scope answer, which is the agent's own decision.

## Measurement

**Eval dataset**
: The LangSmith dataset of cases (scenarios plus out-of-scope, lookup and change-request cases) used to compare versions on accuracy, cost and latency.

**Ground truth**
: The expected answer for an eval case, verified on the cluster before being used (D13).

**Negative control**
: A case where nothing is broken (`healthy`). It catches false positives, not only misses.

**Fixture**
: A recorded tool output used to replay evals without a live cluster, as in the CI eval gate (D39).

**Baseline**
: The measurement a change is compared against (a previous version, or the single agent).

**Budget ledger**
: The monthly spend record (`.budget/ledger-YYYY-MM.json`). Every model call checks it before running; the cap is $15 per month (D36). Check spend with `uv run sre-budget`.

## Repositories

**Agent** (`src/`)
: The operator: investigates and proposes.

**Lab** (`lab/`)
: The system being operated: kind cluster, demo app, scenarios and load tests. It needs no API keys (D43).

**GitOps repo** (*planned*)
: A separate repo with the desired cluster state, watched by Argo CD. The agent changes the cluster's configuration only through Pull Requests there (D43).
