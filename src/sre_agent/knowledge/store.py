"""pgvector store: incremental ingest and vector / keyword / hybrid search.

Several chunking strategies can live side by side (column `variant`) so the retrieval eval
compares them on the same database."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from sre_agent.knowledge.chunking import Chunk
from sre_agent.knowledge.embed import EMBED_DIM

# Local container only (`make kb-up`): trust auth bound to 127.0.0.1, so no password exists.
DEFAULT_DSN = "postgresql://sre@127.0.0.1:5433/kb"
RRF_K = 60  # standard reciprocal-rank-fusion constant
CANDIDATES = 20  # results taken from each ranking before fusion

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS kb_chunks (
    variant      text NOT NULL,
    id           text NOT NULL,
    doc_id       text NOT NULL,
    doc_type     text NOT NULL,
    title        text NOT NULL,
    heading      text NOT NULL,
    text         text NOT NULL,
    meta         jsonb NOT NULL,
    content_hash text NOT NULL,
    embedding    vector({EMBED_DIM}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    PRIMARY KEY (variant, id)
);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv ON kb_chunks USING gin (tsv);
"""


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    doc_type: str
    title: str
    heading: str
    text: str
    score: float


def connect(dsn: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(dsn or os.getenv("KB_DSN", DEFAULT_DSN), autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)


def sync(
    conn: psycopg.Connection,
    variant: str,
    chunks: Sequence[Chunk],
    embed: Callable[[list[str]], list[list[float]]],
) -> dict[str, int]:
    """Make the index match `chunks`: embed only new or changed chunks, delete the rest."""
    existing = dict(
        conn.execute(
            "SELECT id, content_hash FROM kb_chunks WHERE variant = %s", (variant,)
        ).fetchall()
    )
    changed = [c for c in chunks if existing.get(c.id) != c.content_hash]
    vectors = embed([c.text for c in changed]) if changed else []
    with conn.transaction():
        for c, v in zip(changed, vectors, strict=True):
            conn.execute(
                """INSERT INTO kb_chunks (variant, id, doc_id, doc_type, title, heading, text, meta, content_hash, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (variant, id) DO UPDATE SET doc_id = EXCLUDED.doc_id, doc_type = EXCLUDED.doc_type,
                     title = EXCLUDED.title, heading = EXCLUDED.heading, text = EXCLUDED.text, meta = EXCLUDED.meta,
                     content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding""",
                (
                    variant,
                    c.id,
                    c.doc_id,
                    c.doc_type,
                    c.title,
                    c.heading,
                    c.text,
                    Jsonb(c.meta),
                    c.content_hash,
                    v,
                ),
            )
        stale = set(existing) - {c.id for c in chunks}
        if stale:
            conn.execute(
                "DELETE FROM kb_chunks WHERE variant = %s AND id = ANY(%s)",
                (variant, list(stale)),
            )
    return {
        "embedded": len(changed),
        "unchanged": len(chunks) - len(changed),
        "deleted": len(stale),
    }


_COLS = "id, doc_id, doc_type, title, heading, text"


def vector_search(
    conn: psycopg.Connection, variant: str, qvec: list[float], k: int = CANDIDATES
) -> list[Hit]:
    import numpy as np

    rows = conn.execute(
        f"SELECT {_COLS}, 1 - (embedding <=> %s) AS score FROM kb_chunks WHERE variant = %s "
        "ORDER BY embedding <=> %s LIMIT %s",
        (np.array(qvec), variant, np.array(qvec), k),
    ).fetchall()
    return [Hit(*r) for r in rows]


def keyword_search(
    conn: psycopg.Connection, variant: str, query: str, k: int = CANDIDATES
) -> list[Hit]:
    # OR semantics: plainto_tsquery ANDs every term, which long alert-style queries never satisfy.
    rows = conn.execute(
        f"""WITH q AS (SELECT replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery AS tsq)
            SELECT {_COLS}, ts_rank_cd(tsv, q.tsq) AS score FROM kb_chunks, q
            WHERE variant = %s AND q.tsq::text <> '' AND tsv @@ q.tsq
            ORDER BY score DESC LIMIT %s""",
        (query, variant, k),
    ).fetchall()
    return [Hit(*r) for r in rows]


def rrf(rankings: Sequence[Sequence[Hit]], k: int = RRF_K) -> list[Hit]:
    """Reciprocal rank fusion: score = sum of 1 / (k + rank) over the rankings a chunk appears in."""
    scores: dict[str, float] = {}
    best: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            best.setdefault(hit.chunk_id, hit)
    order = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [Hit(**{**best[c].__dict__, "score": round(scores[c], 5)}) for c in order]


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    variant: str = "section",
    mode: str = "hybrid",
    k: int = 5,
    max_per_doc: int | None = None,
    embed_query: Callable[[str], list[float]] | None = None,
) -> list[Hit]:
    """Top-k chunks for a query. mode: vector | keyword | hybrid.

    `max_per_doc` keeps at most that many chunks of one document, so a single long
    document cannot fill the top-k (None = no cap)."""
    if mode == "keyword":
        ranked = keyword_search(conn, variant, query)
    else:
        if embed_query is None:
            from sre_agent.knowledge.embed import embed_query
        ranked = vector_search(conn, variant, embed_query(query))
        if mode == "hybrid":
            ranked = rrf([ranked, keyword_search(conn, variant, query)])
    return cap_per_doc(ranked, max_per_doc)[:k]


def cap_per_doc(hits: Sequence[Hit], n: int | None) -> list[Hit]:
    """Drop chunks beyond the first `n` of each document, keeping rank order."""
    if n is None:
        return list(hits)
    counts: dict[str, int] = {}
    out = []
    for h in hits:
        counts[h.doc_id] = counts.get(h.doc_id, 0) + 1
        if counts[h.doc_id] <= n:
            out.append(h)
    return out


def top_docs(hits: Sequence[Hit], k: int = 5) -> list[str]:
    """Distinct document ids in rank order (several chunks of one document count once)."""
    seen: list[str] = []
    for h in hits:
        if h.doc_id not in seen:
            seen.append(h.doc_id)
    return seen[:k]
