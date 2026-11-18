"""Commit-message convention checker (scripts/check_commit_msg.py). No LLM, no cluster."""

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "scripts" / "check_commit_msg.py"
_spec = importlib.util.spec_from_file_location("check_commit_msg", _PATH)
assert _spec and _spec.loader
ccm = importlib.util.module_from_spec(_spec)
sys.modules["check_commit_msg"] = ccm  # dataclasses resolve the module
_spec.loader.exec_module(ccm)

BODY = (
    "\n\nThe tool returned full pod logs and blew the context window on\nnoisy pods.\n"
)


@pytest.mark.parametrize(
    "msg",
    [
        "fix(tools): cap pod log output at TOOL_MAX_CHARS" + BODY,
        "docs: explain the HITL approval flow",
        "chore(hooks): add commit-msg convention check",
        "feat(slack)!: require two approvers for writes"
        + BODY
        + "\nBREAKING CHANGE: single-approver configs are rejected\n",
        "fix: handle empty alert payloads"
        + BODY
        + "\nRefs: #12\nCo-authored-by: A <a@b.c>\n",
        (
            "revert: feat(llm): add model fallback\n\nThis reverts commit abc1234 because "
            "the fallback doubled cost."
        ),
        "Merge branch 'main' into feature",
        'Revert "feat: something"\n\nThis reverts commit abc.',
        "fixup! fix(tools): cap pod log output",
        (
            "docs: update README for kind\n# Please enter the commit message\n"
            "# ------------------------ >8 ------------------------\ndiff --git a b"
        ),
        "test(evals): cover the out-of-scope refusal case",
    ],
)
def test_valid(msg: str) -> None:
    assert ccm.check(msg).errors == []


@pytest.mark.parametrize(
    ("msg", "needle"),
    [
        ("", "empty"),
        ("# only comments", "empty"),
        ("added retries", "header must be"),
        ("Fix: handle empty payloads", "header must be"),
        ("feature: add retries" + BODY, "unknown type"),
        ("fix(Tools): handle empty payloads" + BODY, "kebab-case"),
        ("docs: Update the README", "lowercase"),
        ("docs: updated the README", "imperative"),
        ("docs: adds the decision log", "imperative"),
        ("docs: explain the approval flow.", "period"),
        ("chore: wip", "vague"),
        ("docs: update", "vague"),
        ("fix: handle empty payloads", "need a body"),
        ("feat(tools)!: drop the scale tool", "need a body"),
        ("fix: handle empty payloads\nThe body starts right away.", "blank line"),
        ("docs: " + "x " * 40, "max 72"),
        ("fix: handle empty payloads\n\n" + "word " * 20, "wrap the body"),
        (
            "revert: feat: add fallback\n\nIt doubled the monthly cost.",
            "This reverts commit",
        ),
        (
            "feat: add fallback" + BODY + "\nbreaking change: config renamed",
            "uppercase",
        ),
    ],
)
def test_invalid(msg: str, needle: str) -> None:
    errors = ccm.check(msg).errors
    assert any(needle in e for e in errors), errors


def test_warnings_do_not_block() -> None:
    r = ccm.check("docs(newarea): explain how the new area handles retries ok")
    assert r.errors == []
    assert any("new scope" in w for w in r.warnings)
    assert any("aim for" in w for w in r.warnings)


def test_long_url_line_is_allowed() -> None:
    url = "https://example.com/" + "a" * 80
    assert (
        ccm.check(f"fix: handle empty payloads\n\nSee {url} for the report.").errors
        == []
    )


def test_main_exit_codes(tmp_path: Path) -> None:
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.write_text("docs: explain the approval flow\n")
    bad.write_text("updated stuff\n")
    assert ccm.main([str(good)]) == 0
    assert ccm.main([str(bad)]) == 1
    assert ccm.main([]) == 2
