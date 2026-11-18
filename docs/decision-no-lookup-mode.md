# Decision: no "lookup mode" in the agent (D30)

> Date: 2026-10-22 · Status: accepted · Related: D1 (subagents pattern), D14 (compact tools), D24 (scope by prompt)

## Context

Question: should the agent answer direct lookups such as *"describe pod X"* or *"list all nodes"*?

How the agent behaved (checked in the code):

- A specialist returns a `SpecialistReport` (`agents/__init__.py`): `summary`, `findings` (what is **wrong**), suspected cause and confidence. Raw tool output stays in the specialist's isolated context and **never reaches the commander**. For a healthy pod, `findings` is empty.
- The commander always answered in incident format (Status / Impact / Probable cause / Evidence / Next steps).
- **Correctness bug:** commander rule 3 said *"Counting or listing questions are answered from the snapshot alone"*. The snapshot only contains unhealthy pods and nodes with problems, and no services. So "list all nodes" could get an incomplete answer that sounded confident ("no node problems").

Option considered: a **lookup mode**, with a `data` field in `SpecialistReport` to pass raw output up and a free format that reproduces `kubectl`.

## What good practice says

| Source | What it says | Implication |
|---|---|---|
| Anthropic, *Building effective agents* | Start with the simplest solution and add complexity only when needed, sometimes without an agent. Agents pay off when the path can't be predetermined; agentic systems trade latency and cost for performance | "Describe pod X" is a fixed one-step path: `kubectl describe` is exact, free and instant. An LLM adds cost, latency and the risk of over-summarizing |
| LangChain docs, *Subagents* | Each subagent works in a clean context so it doesn't bloat the main conversation | Passing raw output to the commander breaks the very isolation that justifies multi-agent (D23) |
| Agent Patterns, *Sub-agents fan out* | Subagents return the minimum needed to synthesize (findings, structured summaries), not raw API responses (e.g. 5,000 → ~200 tokens) | `SpecialistReport` already follows this; a raw `data` field would go against it |
| HolmesGPT, k8sgpt (reference tools) | Focused on diagnosis: they read the cluster (get/list/describe, events, logs) to explain what is wrong, with read-only RBAC | The value of an SRE agent is interpreting and correlating, not replacing `kubectl` |

Caveat: the HolmesGPT/k8sgpt and pattern pages are third-party READMEs or guides; the Anthropic post was read through a mirror and search excerpts.

## Decision

**No lookup mode.** Raw data already has `kubectl`. The agent is used where it adds value: interpreting and correlating.

The behavior was fixed with **two prompt changes**, no new graph code or schema:

1. **Commander rule 3:** answer from the snapshot only what the snapshot covers. "All nodes", services, ports or object details are delegated to `k8s_inspector`.
2. **Direct lookups** (in `_RESPONSE_FORMAT`, both graphs): no incident format; answer with the relevant facts in a short list and **end with the exact `kubectl` command** for the full output. Specialists put facts in `summary` when the task asks for facts rather than a problem.

**Eval:** `list-nodes` and `describe-pod` were added to the dataset (8 → 10), with a code evaluator `lookup_handled`. It requires the lookup to reach the tool with the data (`get_nodes` / `describe_pod`, or via `k8s_inspector`) and the answer to include `kubectl`. The LLM judge accepts lookups without raw dumps. Result: 10/10 on every evaluator, both graphs (n=1).

## When to revisit

- If the main users operate **only from Slack or mobile**, without terminal or cluster access. Even then, expand interpretation first, not dumping.
- If lookups start **crossing sources** (Kubernetes + Datadog + CloudWatch), where one `kubectl` isn't enough. That is correlation, the agent's use case, not a lookup mode.

## Sources

- [Building effective agents (mirror)](https://scrapbox.io/nikkie-memos/Building%20effective%20agents)
- [When not to build AI agents: Anthropic's workflow vs agent playbook](https://mer.vin/2026/05/when-not-to-build-ai-agents-anthropics-workflow-vs-agent-playbook/)
- [LangChain docs — Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents.md)
- [Agent Patterns — Sub-agents fan out](https://agentpatterns.ai/patterns/multi-agent/sub-agents-fan-out/)
- [Multi-Agent Context Isolation](https://jiangren.com.au/en/learn/context-engineering/agent-context-engineering)
- [HolmesGPT alternatives (Metoro)](https://metoro.io/comparisons/ai-sre/holmesgpt-alternatives)
- [HolmesGPT skill](https://smithery.ai/skills/julianobarbosa/holmesgpt-skill)
- [K8sGPT (Komodor)](https://komodor.com/?p=13466)
- [k8agent (PyPI)](https://pypi.org/project/k8agent/)
