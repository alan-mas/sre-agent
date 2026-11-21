"""MCP server for the knowledge base (D50): same answers as the LangChain tools, read-only surface.

No tokens and no database: the store is replaced by a fake, as in test_tools_knowledge.py."""

import asyncio
import json

import pytest

from sre_agent import mcp_kb
from sre_agent.knowledge.store import Hit
from sre_agent.tools import knowledge


def _hit(doc_id, doc_type):
    return Hit(
        f"{doc_id}#0", doc_id, doc_type, f"title {doc_id}", "", "text " * 300, 0.1
    )


@pytest.fixture
def fake_kb(monkeypatch):
    monkeypatch.setattr(knowledge, "_conn", lambda: None)
    monkeypatch.setattr(
        knowledge.store,
        "search",
        lambda conn, q, variant, k: [
            _hit("RB-008", "runbook"),
            _hit("PM-007", "postmortem"),
        ],
    )


def _call(name: str, args: dict) -> str:
    result = asyncio.run(mcp_kb.server.call_tool(name, args))
    return result.content[0].text


def test_server_exposes_exactly_two_read_only_tools():
    tools = asyncio.run(mcp_kb.server.list_tools())
    assert sorted(t.name for t in tools) == ["get_document", "search_runbooks"]
    for t in tools:
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
        assert t.annotations.open_world_hint is False


def test_descriptions_match_the_langchain_tools():
    tools = {t.name: t for t in asyncio.run(mcp_kb.server.list_tools())}
    for lc_tool in knowledge.KNOWLEDGE_TOOLS:
        mcp_tool = tools[lc_tool.name]
        assert mcp_tool.description == lc_tool.description
        props = mcp_tool.input_schema["properties"]
        for arg, schema in lc_tool.args.items():
            assert props[arg]["description"] == schema["description"]


def test_invalid_input_is_refused_by_the_server(fake_kb):
    out = json.loads(_call("get_document", {"doc_id": "RB-008; DROP TABLE kb_chunks"}))
    assert "error" in out


class FakeConn:
    """Stands in for psycopg in get_document: returns rows for RB-008 only, so both the found
    and the not-found paths run. It gives data, not logic: filtering stays in our code."""

    def execute(self, sql, params):
        found = [("DNS failures", "Diagnosis", "Check CoreDNS pods first.")]
        rows = found if params == ("RB-008",) else []
        return type("Cursor", (), {"fetchall": lambda self: rows})()


# One case per code path the core controls; every result must be identical through both doors.
EQUIVALENCE_CASES = [
    ("search_runbooks", {"query": "DNS resolution failures"}),  # plain search
    ("search_runbooks", {"query": "DNS outage", "doc_type": "postmortem"}),  # filter
    ("search_runbooks", {"query": ""}),  # validation error
    ("get_document", {"doc_id": "RB-008"}),  # found
    ("get_document", {"doc_id": "RB-999"}),  # well-formed, does not exist
    ("get_document", {"doc_id": "RB-008; DROP TABLE kb_chunks"}),  # malformed, refused
]


@pytest.mark.parametrize(("tool", "args"), EQUIVALENCE_CASES)
def test_mcp_and_langchain_return_the_same_json(fake_kb, monkeypatch, tool, args):
    monkeypatch.setattr(knowledge, "_conn", lambda: FakeConn())
    lc_tool = {t.name: t for t in knowledge.KNOWLEDGE_TOOLS}[tool]
    assert _call(tool, args) == lc_tool.invoke(args)


def test_equivalence_cases_reach_the_paths_they_name(fake_kb, monkeypatch):
    """Guards the guard: if a case silently hit the wrong path, equivalence would still hold."""
    monkeypatch.setattr(knowledge, "_conn", lambda: FakeConn())
    out = {
        json.dumps(args): json.loads(_call(tool, args))
        for tool, args in EQUIVALENCE_CASES
    }
    plain, filtered, empty, found, missing, malformed = out.values()
    assert [r["id"] for r in plain] == ["RB-008", "PM-007"]
    assert [r["type"] for r in filtered] == ["postmortem"]
    assert "query must be" in empty["error"]
    assert found["id"] == "RB-008" and "Check CoreDNS" in found["text"]
    assert missing == {"error": "no document RB-999"}
    assert "doc_id must look like" in malformed["error"]
