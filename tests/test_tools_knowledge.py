"""Knowledge tools (no tokens, no database): output shape, filters and failure handling."""

import json

from sre_agent.knowledge.store import Hit
from sre_agent.tools import knowledge


def _hit(doc_id, doc_type, text="x " * 600):
    return Hit(f"{doc_id}#0", doc_id, doc_type, f"title {doc_id}", "", text, 0.1)


def test_search_returns_compact_cited_snippets(monkeypatch):
    monkeypatch.setattr(knowledge, "_conn", lambda: None)
    monkeypatch.setattr(
        knowledge.store,
        "search",
        lambda conn, q, variant, k: [
            _hit("RB-008", "runbook"),
            _hit("PM-007", "postmortem"),
        ],
    )
    rows = json.loads(knowledge.search_runbooks.invoke({"query": "no such host"}))
    assert [r["id"] for r in rows] == ["RB-008", "PM-007"]
    assert all(len(r["snippet"]) <= knowledge.SNIPPET_CHARS for r in rows)


def test_search_filters_by_doc_type(monkeypatch):
    monkeypatch.setattr(knowledge, "_conn", lambda: None)
    monkeypatch.setattr(
        knowledge.store,
        "search",
        lambda conn, q, variant, k: [
            _hit("RB-008", "runbook"),
            _hit("PM-007", "postmortem"),
        ],
    )
    rows = json.loads(
        knowledge.search_runbooks.invoke({"query": "dns", "doc_type": "postmortem"})
    )
    assert [r["id"] for r in rows] == ["PM-007"]
    bad = json.loads(
        knowledge.search_runbooks.invoke({"query": "dns", "doc_type": "x"})
    )
    assert "error" in bad


def test_kb_down_is_a_one_line_error(monkeypatch):
    def boom():
        raise ConnectionError("refused")

    monkeypatch.setattr(knowledge, "_conn", boom)
    out = json.loads(knowledge.search_runbooks.invoke({"query": "dns"}))
    assert out == {"error": "knowledge base unavailable: ConnectionError"}


def test_arguments_are_validated_before_the_database(monkeypatch):
    def no_db():
        raise AssertionError("invalid input must not reach the database")

    monkeypatch.setattr(knowledge, "_conn", no_db)
    for args in (
        {"query": ""},
        {"query": "x" * (knowledge.QUERY_MAX_CHARS + 1)},
        {"query": "dns", "k": 0},
    ):
        assert "error" in json.loads(knowledge.search_runbooks.invoke(args))
    for doc_id in ("RB-8", "rb-008", "RB-008; DROP TABLE kb_chunks", "../etc"):
        out = json.loads(knowledge.get_document.invoke({"doc_id": doc_id}))
        assert "error" in out, doc_id
