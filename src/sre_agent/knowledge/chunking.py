"""Chunking strategies. Which one wins is decided by the retrieval eval, not by taste."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sre_agent.knowledge.docs import Document


@dataclass(frozen=True)
class Chunk:
    id: str  # "<doc_id>#<n>"
    doc_id: str
    doc_type: str
    title: str
    heading: str
    text: str  # what gets embedded and keyword-indexed
    meta: dict

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


def _header(doc: Document, heading: str) -> str:
    """Context line prepended to every chunk so a section still says what it belongs to."""
    parts = [f"{doc.title} > {heading}"]
    if alerts := doc.meta.get("alerts"):
        parts.append("Alerts: " + ", ".join(alerts))
    if services := doc.meta.get("services"):
        parts.append("Services: " + ", ".join(services))
    return "\n".join(parts)


def _meta(doc: Document) -> dict:
    keep = ("services", "alerts", "scenario", "related", "updated", "synthetic")
    return {
        k: (str(v) if k == "updated" else v) for k, v in doc.meta.items() if k in keep
    }


def by_section(doc: Document, contextual: bool = True) -> list[Chunk]:
    """One chunk per `##` section; the preamble (H1, status line) joins the first section."""
    sections = doc.sections or []
    chunks = []
    for i, s in enumerate(sections):
        body = s.text if i else f"{doc.preamble}\n\n{s.text}".strip()
        text = f"{_header(doc, s.heading)}\n\n{body}" if contextual else body
        chunks.append(
            Chunk(
                f"{doc.id}#{i}",
                doc.id,
                doc.doc_type,
                doc.title,
                s.heading,
                text,
                _meta(doc),
            )
        )
    return chunks


def fixed_size(doc: Document, size: int = 800, overlap: int = 100) -> list[Chunk]:
    """Baseline: fixed character windows with overlap, no headings, no context line."""
    text = doc.body.strip()
    step = size - overlap
    chunks = []
    for n, start in enumerate(range(0, max(len(text) - overlap, 1), step)):
        piece = text[start : start + size]
        chunks.append(
            Chunk(
                f"{doc.id}#{n}", doc.id, doc.doc_type, doc.title, "", piece, _meta(doc)
            )
        )
    return chunks


STRATEGIES = {
    "section": lambda d: by_section(d, contextual=True),
    "section-plain": lambda d: by_section(d, contextual=False),
    "fixed": fixed_size,
}
