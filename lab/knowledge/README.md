# Knowledge base (synthetic)

Runbooks and postmortems for the Runbook Analyst (D41). **Everything here is synthetic**: written for this lab, about the lab's own failure scenarios ([`docs/scenarios.md`](../../docs/scenarios.md)). The services, dates, teams and numbers in it are invented. The planned source of truth for real teams is Confluence (D42); this folder is the local source with the same shape, so the agent runs without an Atlassian account.

| Folder | Content |
|---|---|
| `runbooks/` | One runbook per failure mode in the lab, plus **distractors**: problems that share vocabulary with the real ones (DNS errors also say "connection refused", a NotReady node also leaves pods Pending). |
| `postmortems/` | Blameless postmortems in the Google SRE format (impact, detection, timeline, root cause, action items). |
| `queries.yaml` | The **retrieval query set**: labeled queries with the documents each one should retrieve. Ground truth for `recall@k` and `MRR`. |

Every document starts with YAML front matter. The indexer stores it as chunk metadata:

| Field | Meaning |
|---|---|
| `id` | Stable id, cited in answers and in `queries.yaml` (`RB-…`, `PM-…`). |
| `doc_type` | `runbook` or `postmortem`. |
| `title` | Page title. |
| `services` | Workloads it applies to (empty = any). |
| `alerts` | Alert names that should lead to it. Example names in the style of kube-prometheus-stack; the lab does not run Prometheus yet. |
| `scenario` | The lab scenario this document reproduces, if any. `none` means the lab cannot inject this failure yet (e.g. a CoreDNS outage), even if the document mentions symptoms of a scenario. |
| `related` | Ids of the runbooks and postmortems it links to. |
| `updated` | Last review (runbooks) or incident date (postmortems). |
| `synthetic` | Always `true` here. |

Document content is **untrusted input** for the agent: it is data to quote and cite, never instructions to follow.
