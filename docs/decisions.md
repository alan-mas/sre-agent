# Design decisions

Accepted decisions, newest last. Each one states what was decided, why, and the data behind it when there is any. Decisions are not reopened without new evidence; superseded ones say so.

## Architecture and tools

| ID | Decision | Why / evidence |
|---|---|---|
| D1 | LangChain v1 *subagents* pattern: an Incident Commander plus three specialists exposed as tools (tool-per-agent). | Context isolation: each specialist sees only its own tools and raw output. |
| D2 | Own tools on the official `kubernetes` Python client instead of an MCP server; compact JSON outputs. | Control over output size and safety; see D14 and D31. |
| D3 | Commander on a large model (medium effort), specialists on `claude-haiku-5-5` (low effort); configurable via env. | Specialists do narrow, tool-heavy work; the commander does synthesis. |
| D4 | `metrics-server` for metrics; Prometheus/Loki only as a stretch goal. | Smallest footprint that answers CPU/memory questions. |
| D6 | Python 3.12 via `uv`. | Reproducible environment with a lockfile, independent of the system Python. |
| D10 | A single-agent baseline (`sre_single`) is mandatory. | Multi-agent must be justified with data, not assumed. |
| D13 | Verified failure scenarios are the eval ground truth ([`scenarios.md`](scenarios.md)); a healthy app with startup warnings is the negative control. | Catches false positives, not only misses. |
| D14 | Tools return compact JSON, capped at `TOOL_MAX_CHARS=4000`, with one-line errors; per-role tool sets. | Measured: snapshot 1.3k tokens vs 36.7k for raw `kubectl` (−96%); 16 tools together = 3.3k schema tokens per call vs 0.9–1.5k per specialist. |
| D16 | The dev server always runs via `make dev` (truststore disabled for the local cluster's self-signed cert); `get_pod_logs(previous=True)` falls back to the current container when the previous one is gone. | macOS truststore rejects the local cluster CA. |
| D30 | No "lookup mode": raw data is `kubectl`'s job; the agent interprets and correlates. Direct lookups are answered with key facts plus the exact `kubectl` command. Fixed a bug where "list all nodes" was answered from a snapshot that doesn't see healthy nodes. | Eval cases `list-nodes`, `describe-pod` + `lookup_handled` evaluator: 10/10 on both graphs. [Write-up](decision-no-lookup-mode.md). |
| D31 | Official MCP servers (`kubernetes-mcp-server`, EKS MCP) as a hybrid **read-only** complement in one specialist, pinned version, minimal toolsets. The snapshot and all write tools stay custom. | Domain logic (−96% tokens) and the write pipeline have no MCP equivalent; to be measured against the same eval. [Write-up](decision-hybrid-mcp.md). |

## Models, cost and reliability

| ID | Decision | Why / evidence |
|---|---|---|
| D15 | First measurements (n=1): lean vs single, 6/6 each with no false positives; ~95% of lean cost was the commander's output. | Pointed optimization at the commander, not the specialists. |
| D17 / D27 | Commander on `claude-sonnet-5-5` (supersedes Opus for demos). | Same 7/7 eval accuracy as Opus at ~40% of the cost. |
| D18 | Middleware stack per role (caching last). Models use adaptive thinking with `block_binding: drop_block` because client-side middleware edits history. | Measured with Sonnet commander: lean $0.0405 → $0.0356 per question, single $0.0509 → $0.0395. |
| D21 | Trace identity is baked into the graph (`run_name`, `graph` tag/metadata), so CLI, server, alerts and evals label runs the same way. | LangSmith cost per model matches the local ledger within ±2%; `lc_agent_name` gives cost per agent. |
| D23 | Multi-agent is justified by isolation and scale, not cost. | 7/7 on both graphs with Sonnet (lean $0.0158 · 15 s vs single $0.0147 · 9 s) and with Opus (lean $0.0388 · 22 s vs single $0.0400 · 16 s). The LLM judge showed variance on identical output. |
| D25 | `RefusalGuard` middleware: when the API returns `stop_reason: "refusal"`, explain it to the operator and drop the refused turn from the model context (the thread keeps it). | A refused turn otherwise poisoned every following turn of the thread. Verified the thread recovers. |
| D26 | No server-side model fallbacks; only `RefusalGuard`. | Here a refusal matches our own policy; LangChain fallbacks only act on exceptions. |
| D36 | Monthly spend cap of **$15**, one ledger per calendar month (`.budget/ledger-YYYY-MM.json`), enforced in code before every call. Supersedes the original one-off $10 cap (D12). | Hard guard independent of discipline; tests never call the LLM. |

## Actions and safety

| ID | Decision | Why / evidence |
|---|---|---|
| D19 | Write tools exist only on the commander and the single agent; every write tool is in `interrupt_on`. | Both invariants are enforced by tests. |
| D22 | Write tools require a `justification` argument (schema), shown as `Why:` on the approval card and stored in the audit log. The snapshot reports `deployments_ok`. | Prompt instructions alone did not produce justifications; the eval found a false positive on the healthy control. |
| D24 | Scope is enforced by prompt, not middleware: off-topic requests get a one-line refusal with no tool calls. | Rule of thumb: mandatory/harmful → code; desirable/low-risk → prompt + eval. `out-of-scope` case: 1/1 on both graphs. |
| D32 | Configuration changes (image, resources, env, probes, missing dependencies) are never applied. The agent drafts a **change request** (manifest diff, evidence, owner) meant as a GitOps PR it never merges; unverifiable values are placeholders, never borrowed from other workloads. No `set_image` tool on purpose. | The `fix-config` case + `config_change_routed` evaluator caught the agent suggesting another service's tag "as a hint"; after tightening the rule, 3/3 on the affected cases on both graphs. [Write-up](decision-config-changes.md). |

## Interfaces and operations

| ID | Decision | Why / evidence |
|---|---|---|
| D5 / D28 | Agent Chat UI + `langgraph dev` for development; Slack (Socket Mode) as the on-call interface: alert in a channel, Approve/Reject with confirmation, an approver allowlist and audit; a Slack thread maps to an agent thread (metadata `slack_ts`). | Socket Mode needs no public endpoint; durability comes from the Agent Server. |
| D29 | After an approval, a blameless postmortem draft is posted in the Slack thread: facts in code (timeline from threads/runs + audit log, similar incidents by metadata, 0 tokens) and prose from one structured-output Haiku call (~$0.0015). Replies carry 👍/👎 sent to LangSmith as `oncall_helpful`. | Facts must not be generated; feedback closes the monitoring loop. Semantic incident search postponed until there are enough incidents; *now planned in D41*. |
| D9 | Managed LangSmith Deployment is paid and not used; deployment concepts are practiced locally with the same Agent Server API. | Cost. See D35 for the in-cluster runtime. |
| D40 | Commit messages follow Conventional Commits with the 50/72 rule, checked locally by a `commit-msg` hook (`make hooks`, stdlib Python, unit-tested); `feat`/`fix`/`perf`/`refactor`/`revert` and breaking changes need a body explaining why. [Rules](../CONTRIBUTING.md#commit-messages). | Consensus across Conventional Commits, git, Linux, Angular, Kubernetes, Node.js, Go, Vue, Django, Electron and LangChain: short imperative header, blank line, wrapped body that explains why. Types enable changelogs and filtering (`git log --grep "^fix"`). |

## Lab and runtime (current phase)

| ID | Decision | Why / evidence |
|---|---|---|
| D33 | Move from minikube to **kind** (1 control plane + 2 workers), one namespace per scenario, shared with the lab repo. | Multi-node is needed for node-pressure and scheduling scenarios; lighter and reproducible. |
| D34 | AWS practice runs locally on **Floci** (primary). Postgres + pgvector in a container for RDS-like and RAG work. Behavior no emulator reproduces (security groups, NACLs, ALB, trust policies) is checked in a real sandbox account and labeled as such. | The official AWS Emulator does not evaluate IAM policies and has no prebuilt image for macOS; Floci offers opt-in IAM enforcement, Terraform compatibility and CloudWatch metrics. |
| D35 | In-cluster runtime: **FastAPI + LangGraph `PostgresSaver`**, keeping an API close to threads/runs/resume; `langgraph dev` stays for local development and Studio. | The standalone Agent Server needs an enterprise license key. Building the runtime shows what a managed platform provides (durable runs, resume after interrupt, streaming, auth) and how to replace it on your own Kubernetes. |
| D37 | Two public repos: **`sre-incident-lab`** (the system: cluster, app, failures, runbooks; no API keys needed) and **`sre-agent`** (the operator). Scenario ids are shared; the agent changes the lab only through Pull Requests. | Each repo is useful on its own; the boundary mirrors a real client environment. *Amended by D42:* runbooks and postmortems live in Confluence; the lab keeps seed runbooks as Markdown plus a publish script, so it still needs no API keys. |
| D38 | Code, prompts, docs and commits in English. | Public portfolio for English-speaking audiences. |
| D39 | CI eval gate: PRs that touch prompts or models replay the dataset against **recorded tool outputs** (fixtures); full runs against a live kind cluster are manual or nightly. The gate uses a tolerance and repeated runs to avoid flakiness. | The agent's tools need a live cluster; fixtures keep CI fast, deterministic and cheap. |

## Knowledge: runbooks and postmortems

| ID | Decision | Why / evidence |
|---|---|---|
| D41 | A read-only **Runbook Analyst** subagent answers from runbooks and past postmortems: for the commander during an incident, and for direct questions from an SRE. It returns a short answer, the relevant steps and links to the source pages, never raw chunks. Postmortem drafting reuses the same retriever (`doc_type=postmortem`) for similar incidents, prefilled sections and recurrence warnings; no separate subagent unless evals show the need. Chosen over a `search_runbooks` tool on the commander. | The corpus grows with every incident: isolating retrieval keeps the commander's context flat and keeps untrusted page content out of it, as the Log Analyst does with logs. Repeated incidents are a warning sign of unclosed action items ([SRE Workbook](https://sre.google/workbook/postmortem-culture/)). Measured before/after (recall@5, MRR, accuracy, cost, latency); evidence pending. |
| D42 | **Confluence** (Cloud Free) is the source of truth for runbooks and postmortems. The agent builds its **own pgvector index** from it (own chunking, embeddings, hybrid search, space-permission filters) through the REST API with a read-only scoped token, synced incrementally by page version; Confluence search and Rovo are not used for retrieval. Writes are **drafts only** (label `proposed`), through HITL, with a separate token limited to one space; a human publishes. A local Markdown source with the same interface keeps everything runnable without an Atlassian account. Demo content is synthetic and labeled as such. | Real teams keep runbooks and postmortems in a wiki; permissions, page versions and human-edited content are part of the problem. Owning the index makes chunking and retrieval measurable. Page content is untrusted input. Google SRE: postmortems are stored in a searchable repository and "an unreviewed postmortem might as well never have existed" ([SRE Book](https://sre.google/sre-book/postmortem-culture/)). |

Decisions D7, D8, D11 and D20 concerned how the original learning plan was organized and are not product decisions.
