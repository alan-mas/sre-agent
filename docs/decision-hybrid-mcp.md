# Decision: official MCP servers as a hybrid complement, not a replacement (D31)

> Date: 2026-10-23 · Status: accepted (measurement pending) · Related: D2 (own tools), D14 (compact outputs), D19 (actions only on the commander), D30 (no lookup mode)

## Context

A reviewer with agent-architecture experience asked: *why not use the official MCP servers that already exist, like the Kubernetes one, or the EKS one if you are on AWS?*

The agent uses **only its own tools** (D2), on the official `kubernetes` Python client: 15 read tools, `cluster_health_snapshot` and 4 write tools. The original design had already considered `kubernetes-mcp-server --read-only` + `langchain-mcp-adapters` and dropped it because of the number of tools, large schemas and raw YAML outputs. The question forced a review of that choice against official guidance.

## What the sources say

| Source | What it says | Favors… |
|---|---|---|
| Anthropic, *Writing effective tools for agents* | Design tools for the agent: few of them, with clear functional boundaries (namespacing), high-signal outputs with filtering, pagination and truncation; measure tokens, accuracy, latency and errors with evals | **Own tools** for the core. `cluster_health_snapshot` is the consolidated tool they recommend: −96% tokens vs raw `kubectl` (measured, D14) |
| Anthropic, *Code execution with MCP* | Loading every tool definition and passing intermediate results through context makes the agent slower and more expensive as tools grow | Don't connect a full generic MCP. Measured: 16 tools together = 3.3k schema tokens per call vs 0.9–1.5k per specialist |
| kubernetes-mcp-server (containers) | `--read-only` blocks create/update/delete; `--disable-destructive` blocks delete/update; `--toolsets` limits tool groups. Its docs say enabling only what's needed reduces context and improves tool selection | **MCP is viable** if configured read-only and minimal |
| AWS EKS MCP server (official) | Hosted (`--read-only`, tools marked `readOnlyHint`) or local (`--allow-write`, `--allow-sensitive-data-access` for logs and events). IAM separates `eks-mcp:CallReadOnlyTool` from `eks-mcp:CallPrivilegedTool` | **MCP on EKS**: AWS context (CloudWatch, IAM, EKS docs) our tools don't have, with least privilege through IAM |
| LangChain (`langchain-mcp-adapters`; `langchain.mcp` / `MCPAdapter` in 1.4+, beta) | MCP tools become normal `create_agent` tools; they can be intercepted with `@wrap_tool_call` middleware; provenance is in `metadata["mcp"]`; `tool_name_prefix` avoids name clashes | Adoption is cheap: HITL (`interrupt_on`), secret redaction and limits keep working |
| LangSmith (agent evals) | Evaluate final output and trajectory against a dataset and compare experiments | Decide with data: run the same eval with and without MCP |
| OWASP MCP Security Cheat Sheet and security guides | *Tool poisoning* (hidden instructions in descriptions), *rug pulls* (descriptions that change after approval), supply chain. Controls: least privilege, pin and verify servers, treat descriptions and results as untrusted, HITL for destructive actions | Only **official servers with a pinned version**; results treated as untrusted data (already the case) |

Caveat: several token-savings figures quoted about *Code execution with MCP* come from third-party summaries and were not verified in the original post; only its core idea is used here. The exact tool list of each server was not verified.

## Analysis

**Why use MCP**
1. **Breadth without maintenance.** CRDs, Helm and resources we didn't anticipate; our tools only cover what was planned.
2. **On EKS it adds what we lack:** CloudWatch, IAM and EKS documentation, with read-only IAM permissions.
3. **It is the standard.** Maintained by the vendor, known by other teams, a few lines to integrate in LangChain.

**Why not replace our tools**
1. **Tokens.** A generic MCP exposes many tools and returns nearly raw objects; the gains from compact outputs (D14) and 3–8 tools per specialist would be lost.
2. **The snapshot exists in no MCP.** It is triage domain logic (`stalled_rollouts`, `deployments_ok`), not an API wrapper. It is where the −96% comes from and where the eval's false positive was fixed.
3. **Actions are not delegated.** Generic write tools just apply the change. Ours have guards, dry-run, a mandatory `justification`, a diff on the HITL card, rollout verification and audit (D19, D22). An MCP with write access would open a path around that pipeline.
4. **Channel security.** Tool descriptions from an external server enter the prompt. With official servers, pinned versions and `--read-only` the risk is manageable, but not zero.

## Decision

**Hybrid architecture.** MCP as a read complement, never for writes:

| Layer | Source |
|---|---|
| Triage (`cluster_health_snapshot`) | Own tool: domain logic, deterministic |
| Write actions | Own tools: safety pipeline |
| Common Inspector reads | Own tools today; MCP candidates if the eval justifies it |
| Long-tail reads (CRDs, Helm) | `kubernetes-mcp-server --read-only` with minimal toolsets |
| AWS in production (CloudWatch, IAM, EKS docs) | EKS MCP server, read-only, with the `CallReadOnlyTool` IAM permission |

Conditions for any MCP server:
- Read-only, minimal toolsets.
- Pinned version, official servers only.
- Tools prefixed by server, assigned to a read-only specialist, never to the commander.
- Same middleware: HITL, redaction, limits and output compaction via `@wrap_tool_call`.
- Results treated as untrusted data.

**Nothing changes in the agent yet**: the decision waits for the measurement below.

## How to measure it

An Inspector variant with `kubernetes-mcp-server --read-only --toolsets core`, compared against our tools on the same eval (`uv run sre-eval`):
- root-cause accuracy, `correct_action`, `no_unsafe_action`, `lookup_handled`;
- input tokens (schemas + outputs), cost and latency per question;
- with repeated runs, so the decision isn't made on n=1 noise.

Hypothesis: MCP ties on accuracy for simple reads, costs more tokens because of less compact outputs, and wins on coverage (`describe-pod`, resources outside our 15 tools).

## When to revisit

- If the measurement shows equal accuracy at an acceptable token cost: move generic Inspector reads to MCP and keep only the snapshot and actions custom.
- When moving to EKS: add the EKS MCP server, read-only, as a source (possibly a new specialist).
- If an official MCP server with write access offers equivalent dry-run, approval and audit: revisit point 3.

## Sources

- [Anthropic — Writing effective tools for agents](https://anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [kubernetes-mcp-server README (mirror)](https://glama.ai/mcp/servers/@containers/kubernetes-mcp-server/blob/8e666d4c67a1f60e929049b890e7268a14b657b1/README.md)
- [kubernetes-mcp-server (socket.dev)](https://socket.dev/pypi/package/kubernetes-mcp-server)
- [Amazon EKS MCP Server — Tool configuration](https://docs.aws.amazon.com/eks/latest/userguide/eks-mcp-tool-configurations.html)
- [Amazon EKS MCP Server — Getting started](https://docs.aws.amazon.com/eks/latest/userguide/eks-mcp-getting-started.html)
- [awslabs EKS MCP server](https://awslabs.github.io/mcp/ja/servers/eks-mcp-server)
- [LangChain — langchain-mcp-adapters migration](https://docs.langchain.com/oss/python/migrate/langchain-mcp-adapters)
- [LangChain — langchain_mcp_adapters reference](https://reference.langchain.com/python/langchain_mcp_adapters)
- [LangChain — Agent evals](https://docs.langchain.com/oss/python/langchain/evals)
- [LangSmith Cookbook](https://github.com/langchain-ai/langsmith-cookbook)
- [OWASP MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)
- [Corgea — MCP security best practices](https://corgea.com/learn/mcp-security-best-practices)
