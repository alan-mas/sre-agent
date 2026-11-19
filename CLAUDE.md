# SRE Agent — project rules

Multi-agent SRE assistant for Kubernetes: an Incident Commander delegates to read-only specialist subagents, proposes remediations, and only changes state through human approval (HITL) or a Pull Request.

## Stack
Python 3.12 (`uv`) · LangChain v1 `create_agent` + middleware · LangGraph · LangSmith · Claude · Kubernetes (local cluster).

## Engineering rules
- **Read-only by default.** Every tool that changes cluster state goes through HITL (`interrupt_on`) with a mandatory `justification`, or is proposed as a GitOps PR. Specialists never get write tools (enforced by tests).
- **Configuration changes are never applied** by the agent: image, resources or missing dependencies become a change request / PR for the owning team.
- **No credentials** in code, prompts, fixtures or logs. Secrets live in `.env` (gitignored); see `.env.example`.
- **Token-efficient tools:** compact JSON outputs, `TOOL_MAX_CHARS`, one-line errors, minimal tool sets per subagent, prompt caching.
- **Every model is built with `sre_agent.llm.make_model()`** (never `ChatAnthropic` directly): it attaches the monthly budget guard. Check spend with `uv run sre-budget`; never delete `.budget/` ledgers.
- **Every change is measurable:** changes to prompts, models, retrieval (chunking, embeddings) or tools are evaluated against the LangSmith dataset with a before/after comparison (accuracy, cost, latency). Run the affected subset first; the full dataset when the change warrants it.
- **Code quality:** typed Python, `pytest` (tests must not call the LLM), `ruff`.
- **Vocabulary:** use terms as defined in `GLOSSARY.md`; add new domain terms there first.
- **Small, explained changes.** For larger changes, propose a 3–5 step plan first.
- **Commits:** Conventional Commits + 50/72, one logical change each; rules in `CONTRIBUTING.md`, enforced by the `commit-msg` hook; the `pre-push` hook flags unpushed commits to squash (`make hooks`).
- **Language:** code, comments, prompts, docs, commits and README in English.
- **Architecture decisions** are recorded in `docs/decisions.md`; do not reopen accepted decisions without new evidence.

## Commands
`make help` lists everything. Common: `make cluster-up`, `make status`, `make test`, `make dev`, `uv run sre-ask "..."`, `uv run sre-eval`, `uv run sre-budget`.
