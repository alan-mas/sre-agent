"""Knowledge base: chunking, fusion and the pgvector store. No LLM; DB tests skip without `make kb-up`."""

import hashlib
from pathlib import Path

import pytest

from sre_agent.knowledge import KB_DIR
from sre_agent.knowledge.chunking import STRATEGIES, by_section, fixed_size
from sre_agent.knowledge.docs import load_dir, parse
from sre_agent.knowledge.store import Hit, rrf, top_docs

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
RUNBOOK = (FIXTURES / "RB-900-test-runbook.md").read_text()
POSTMORTEM = (FIXTURES / "PM-900-test-postmortem.md").read_text()


def test_section_chunks_carry_context_and_preamble():
    chunks = by_section(parse(POSTMORTEM))
    assert [c.heading for c in chunks][:2] == ["Summary", "Impact"]
    assert chunks[0].text.startswith("Test postmortem > Summary")
    assert "SEV-3" in chunks[0].text  # preamble joins the first section
    assert "SEV-3" not in chunks[1].text
    assert {c.id for c in chunks} == {f"PM-900#{i}" for i in range(len(chunks))}


def test_plain_and_fixed_chunks_have_no_context_line():
    doc = parse(RUNBOOK)
    assert not by_section(doc, contextual=False)[0].text.startswith("Test runbook >")
    fixed = fixed_size(doc, size=120, overlap=20)
    assert len(fixed) > 1 and all(len(c.text) <= 120 for c in fixed)


def test_every_strategy_covers_every_document():
    docs = load_dir(KB_DIR)
    for name, strategy in STRATEGIES.items():
        assert {c.doc_id for d in docs for c in strategy(d)} == {d.id for d in docs}, (
            name
        )


def _hit(cid: str) -> Hit:
    return Hit(cid, cid.split("#")[0], "runbook", "t", "h", "x", 0.0)


def test_rrf_rewards_agreement_between_rankings():
    vector = [_hit("A#0"), _hit("B#0")]
    keyword = [_hit("C#0"), _hit("B#0")]
    fused = [h.chunk_id for h in rrf([vector, keyword])]
    assert fused[0] == "B#0"  # 2nd in both rankings beats 1st in only one
    assert set(fused) == {"A#0", "B#0", "C#0"}


def test_top_docs_counts_each_document_once():
    assert top_docs([_hit("A#0"), _hit("A#1"), _hit("B#0")], k=5) == ["A", "B"]


# --- integration with the local pgvector container (0 tokens) ---


def _fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        digest = hashlib.sha256(t.encode()).digest()
        out.append([b / 255 for b in digest] * 12)  # 384 dims, deterministic
    return out


@pytest.fixture
def kb_conn():
    import psycopg

    from sre_agent.knowledge.store import connect, init_schema

    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("knowledge base not running (make kb-up)")
    init_schema(conn)
    conn.execute("DELETE FROM kb_chunks WHERE variant = 'test'")
    yield conn
    conn.execute("DELETE FROM kb_chunks WHERE variant = 'test'")
    conn.close()


@pytest.mark.kb
def test_sync_is_incremental_and_deletes_stale_chunks(kb_conn):
    from sre_agent.knowledge.store import sync

    chunks = by_section(parse(RUNBOOK))
    assert sync(kb_conn, "test", chunks, _fake_embed)["embedded"] == len(chunks)
    assert sync(kb_conn, "test", chunks, _fake_embed) == {
        "embedded": 0,
        "unchanged": len(chunks),
        "deleted": 0,
    }
    stats = sync(kb_conn, "test", chunks[:-1], _fake_embed)
    assert stats["deleted"] == 1


@pytest.mark.kb
def test_keyword_search_finds_exact_terms(kb_conn):
    from sre_agent.knowledge.store import keyword_search, sync

    sync(
        kb_conn,
        "test",
        by_section(parse(RUNBOOK)) + by_section(parse(POSTMORTEM)),
        _fake_embed,
    )
    assert (
        keyword_search(kb_conn, "test", "TestAlert rollout undo")[0].doc_id == "RB-900"
    )
    assert keyword_search(kb_conn, "test", "zzzz-no-match") == []
