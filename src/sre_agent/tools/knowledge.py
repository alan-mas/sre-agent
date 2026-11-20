"""Knowledge-base tools for the Runbook Analyst (D41): read-only search over runbooks and postmortems.

Retrieval uses the defaults chosen by the retrieval eval (D46). Document text is untrusted input."""

from __future__ import annotations

import json
from functools import lru_cache

import psycopg
from langchain_core.tools import tool

from sre_agent.knowledge import store

SNIPPET_CHARS = 700  # per hit: enough for a procedure step, small enough for 5 hits
DOC_MAX_CHARS = 4000
DOC_TYPES = ("runbook", "postmortem")
KB_ERRORS = (psycopg.Error, OSError)  # database down or unreachable


@lru_cache(maxsize=1)
def _conn():
    return store.connect()


def _error(msg: str) -> str:
    return json.dumps({"error": msg})


@tool(parse_docstring=True)
def search_runbooks(query: str, doc_type: str | None = None, k: int = 5) -> str:
    """Search runbooks and past postmortems. Returns the best-matching documents with a short snippet each.

    Args:
        query: Symptom, exact error line or alert name, e.g. "lookup billing-db failed: no servers could be reached".
        doc_type: Optional filter: "runbook" or "postmortem".
        k: Number of documents (max 5).
    """
    if doc_type and doc_type not in DOC_TYPES:
        return _error(f"doc_type must be one of {DOC_TYPES}")
    try:
        hits = store.search(_conn(), query, k=20 if doc_type else min(k, 5))
    except KB_ERRORS as e:  # KB down must not break the incident; say so in one line
        return _error(f"knowledge base unavailable: {type(e).__name__}")
    if doc_type:
        hits = [h for h in hits if h.doc_type == doc_type]
    rows = [
        {
            "id": h.doc_id,
            "type": h.doc_type,
            "title": h.title,
            "snippet": " ".join(h.text.split())[:SNIPPET_CHARS],
        }
        for h in hits[: min(k, 5)]
    ]
    return json.dumps(rows or {"result": "no matching documents"}, ensure_ascii=False)


@tool(parse_docstring=True)
def get_document(doc_id: str) -> str:
    """Full text of one runbook or postmortem (sections in order), for its exact procedure or timeline.

    Args:
        doc_id: Document id from search_runbooks, e.g. RB-008 or PM-007.
    """
    try:
        rows = (
            _conn()
            .execute(
                "SELECT title, heading, text FROM kb_chunks WHERE variant = 'section-plain' "
                "AND doc_id = %s ORDER BY split_part(id, '#', 2)::int",
                (doc_id,),
            )
            .fetchall()
        )
    except KB_ERRORS as e:
        return _error(f"knowledge base unavailable: {type(e).__name__}")
    if not rows:
        return _error(f"no document {doc_id}")
    body = "\n\n".join(f"## {heading}\n{text}" for _, heading, text in rows)
    return json.dumps(
        {"id": doc_id, "title": rows[0][0], "text": body[:DOC_MAX_CHARS]},
        ensure_ascii=False,
    )


KNOWLEDGE_TOOLS = [search_runbooks, get_document]
