"""Retrieval eval: recall@k and MRR over the labeled query set (`lab/knowledge/queries.yaml`).

Compares chunking variants x search modes x per-document caps on the same index.
Uses only the local embedding model and Postgres: 0 tokens."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from sre_agent.knowledge.store import Hit


@dataclass(frozen=True)
class Query:
    id: str
    style: str
    query: str
    relevant: tuple[str, ...]
    hard: bool = False


def load_queries(path: Path) -> list[Query]:
    raw = yaml.safe_load(path.read_text())["queries"]
    return [
        Query(
            q["id"], q["style"], q["query"], tuple(q["relevant"]), bool(q.get("hard"))
        )
        for q in raw
    ]


def first_relevant_rank(hits: Sequence[Hit], relevant: Sequence[str]) -> int | None:
    """1-based rank of the first chunk from a relevant document, or None."""
    for rank, h in enumerate(hits, start=1):
        if h.doc_id in relevant:
            return rank
    return None


def score(ranks: Sequence[int | None]) -> dict[str, float]:
    """recall@k = share of queries with any relevant document in the top-k chunks;
    MRR = mean of 1/rank of the first relevant chunk (0 when missed)."""
    n = len(ranks) or 1
    return {
        "recall": sum(r is not None for r in ranks) / n,
        "mrr": sum(1 / r for r in ranks if r) / n,
    }


@dataclass(frozen=True)
class Result:
    variant: str
    mode: str
    max_per_doc: int | None
    recall: float
    mrr: float
    distinct_docs: float  # average distinct documents in the top-k (diversity)
    by_style: dict[str, float]  # recall per query style
    misses: tuple[str, ...]  # query ids with no relevant document in the top-k


def evaluate(
    queries: Sequence[Query],
    search: Callable[[str], list[Hit]],
    *,
    variant: str,
    mode: str,
    max_per_doc: int | None,
) -> Result:
    ranks: list[int | None] = []
    distinct = 0
    for q in queries:
        hits = search(q.query)
        ranks.append(first_relevant_rank(hits, q.relevant))
        distinct += len({h.doc_id for h in hits})
    styles = sorted({q.style for q in queries})
    by_style = {
        s: score([r for q, r in zip(queries, ranks) if q.style == s])["recall"]
        for s in styles
    }
    return Result(
        variant,
        mode,
        max_per_doc,
        **score(ranks),
        distinct_docs=distinct / (len(queries) or 1),
        by_style=by_style,
        misses=tuple(q.id for q, r in zip(queries, ranks) if r is None),
    )
