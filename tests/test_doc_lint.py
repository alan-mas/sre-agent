"""Layer 0 of document evaluation (D45): deterministic lint of runbooks and postmortems. 0 tokens."""

from pathlib import Path

import pytest

from sre_agent.knowledge import KB_DIR
from sre_agent.knowledge.docs import parse
from sre_agent.knowledge.lint import lint, lint_dir

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
RUNBOOK = (FIXTURES / "RB-900-test-runbook.md").read_text()
POSTMORTEM = (FIXTURES / "PM-900-test-postmortem.md").read_text()


def test_real_corpus_passes_lint():
    report = lint_dir(KB_DIR)
    assert len(report) >= 18
    assert {k: v for k, v in report.items() if v} == {}


def test_valid_documents_pass():
    assert lint(parse(RUNBOOK)) == []
    assert lint(parse(POSTMORTEM)) == []


@pytest.mark.parametrize(
    ("doc", "old", "new", "expected"),
    [
        (RUNBOOK, " (needs approval)", "", "write command without 'needs approval'"),
        (
            RUNBOOK,
            "`kubectl -n x get pods`",
            "`kubectl -n x delete pod z`",
            "write command",
        ),
        (RUNBOOK, "## Escalation", "## Contacts", "missing sections: Escalation"),
        (RUNBOOK, "synthetic: true", "synthetic: false", "synthetic must be true"),
        (RUNBOOK, "related: []", "related: [RB-999]", "related ids do not exist"),
        (RUNBOOK, "Something breaks.", "password: hunter22", "possible secret"),
        (RUNBOOK, "id: RB-900", "id: PM-900", "does not match"),
        (POSTMORTEM, "00:10 (+1d)", "00:10", "not in chronological order"),
        (POSTMORTEM, "Time to detect: 2 min.", "", "time to detect"),
        (POSTMORTEM, "**Severity:** SEV-3", "**Severity:** high", "Severity"),
        (
            POSTMORTEM,
            "| Prevent | P2 | API team | Open |",
            "| Fix | P2 |  | Maybe |",
            "type 'Fix'",
        ),
        (
            POSTMORTEM,
            "| Prevent | P2 | API team | Open |",
            "| Prevent | high | API team | Open |",
            "priority",
        ),
    ],
)
def test_lint_catches_mutation(doc, old, new, expected):
    assert old in doc
    errs = lint(parse(doc.replace(old, new)), known_ids={"RB-900", "PM-900"})
    assert any(expected in e for e in errs), errs


def test_postmortem_narrative_may_mention_write_commands():
    """The approval rule applies to runbook instructions, not to what happened in an incident."""
    doc = POSTMORTEM.replace("It broke.", "Someone ran `kubectl edit configmap x`.")
    assert lint(parse(doc)) == []
