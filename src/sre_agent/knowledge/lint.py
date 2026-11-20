"""Layer 0 of document evaluation (D45): deterministic checks that a runbook or postmortem is
well formed and safe. Runs on hand-written and generated documents alike; costs 0 tokens."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sre_agent.knowledge import KB_DIR
from sre_agent.knowledge.docs import DOC_TYPES, Document, load_dir, parse

REQUIRED_META = (
    "id",
    "doc_type",
    "title",
    "services",
    "alerts",
    "scenario",
    "updated",
    "related",
    "synthetic",
)
REQUIRED_SECTIONS = {
    "runbook": ("When to use", "Triage", "Mitigation", "Escalation"),
    "postmortem": (
        "Summary",
        "Impact",
        "Detection",
        "Timeline (UTC)",
        "Root cause",
        "Action items",
    ),
}
ID_PATTERN = {"runbook": r"RB-\d{3}", "postmortem": r"PM-\d{3}"}
ACTION_TYPES = {"Prevent", "Mitigate", "Detect", "Process"}
ACTION_STATUS = {"Open", "In progress", "Done"}

# kubectl verbs that change state. `rollout history|status` only read.
_WRITE_CMD = re.compile(
    r"kubectl\b[^`\n]*?\b(delete|apply|patch|edit|scale|cordon|uncordon|drain|run|create|replace|set|label|annotate"
    r"|rollout (?:undo|restart|pause|resume))\b"
)
_APPROVAL = re.compile(r"needs approval", re.IGNORECASE)
_SECRETS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"xox[abposr]-[0-9A-Za-z-]{10,}"),  # Slack token
    re.compile(r"sk-ant-[0-9A-Za-z_-]{10,}"),  # Anthropic key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}"),
]
_TIME = re.compile(
    r"^- (\d{2}):(\d{2})(?: \(\+(\d+)d\))? ", re.MULTILINE
)  # "(+1d)" marks the next day


def lint(doc: Document, known_ids: set[str] | None = None) -> list[str]:
    """Problems found in one document, as one-line messages (empty = passes)."""
    errs: list[str] = []
    missing = [k for k in REQUIRED_META if k not in doc.meta]
    if missing:
        errs.append(f"front matter missing: {', '.join(missing)}")
    if doc.doc_type not in DOC_TYPES:
        return errs + [f"doc_type must be one of {DOC_TYPES}, got {doc.doc_type!r}"]
    if not re.fullmatch(ID_PATTERN[doc.doc_type], doc.id):
        errs.append(f"id {doc.id!r} does not match {ID_PATTERN[doc.doc_type]}")
    if doc.path and not Path(doc.path).name.startswith(doc.id):
        errs.append(f"file name does not start with id {doc.id}")
    if doc.meta.get("synthetic") is not True:
        errs.append("synthetic must be true for lab documents")
    for key in ("services", "alerts", "related"):
        if key in doc.meta and not isinstance(doc.meta[key], list):
            errs.append(f"{key} must be a list")
    related = doc.meta.get("related") or []
    if doc.id in related:
        errs.append("related lists the document itself")
    if known_ids is not None:
        unknown = [r for r in related if r not in known_ids]
        if unknown:
            errs.append(f"related ids do not exist: {', '.join(unknown)}")

    headings = [s.heading for s in doc.sections]
    absent = [h for h in REQUIRED_SECTIONS[doc.doc_type] if h not in headings]
    if absent:
        errs.append(f"missing sections: {', '.join(absent)}")
    for rx in _SECRETS:
        if rx.search(doc.body):
            errs.append(f"possible secret matches {rx.pattern[:30]!r}")

    if doc.doc_type == "runbook":
        errs += _lint_runbook(doc)
    else:
        errs += _lint_postmortem(doc)
    return errs


def _lint_runbook(doc: Document) -> list[str]:
    errs = []
    for line in doc.body.splitlines():
        m = _WRITE_CMD.search(line)
        if m and not _APPROVAL.search(line):
            errs.append(
                f"write command without 'needs approval': kubectl ... {m.group(1)}"
            )
    return errs


def _lint_postmortem(doc: Document) -> list[str]:
    errs = []
    if not re.search(r"\*\*Severity:\*\* SEV-\d", doc.preamble):
        errs.append("status line has no '**Severity:** SEV-n'")
    sections = {s.heading: s.text for s in doc.sections}
    if (
        "Detection" in sections
        and "time to detect" not in sections["Detection"].lower()
    ):
        errs.append("Detection does not state the time to detect")
    times = [
        int(d or 0) * 1440 + int(h) * 60 + int(m)
        for h, m, d in _TIME.findall(sections.get("Timeline (UTC)", ""))
    ]
    if not times:
        errs.append("timeline has no '- HH:MM' entries")
    elif times != sorted(times):
        errs.append("timeline entries are not in chronological order")
    errs += _lint_action_items(sections.get("Action items", ""))
    return errs


def _lint_action_items(text: str) -> list[str]:
    rows = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if line.startswith("|")
    ]
    if len(rows) < 3:
        return ["action items table is empty"]
    header = rows[0]
    need = ("Action", "Type", "Priority", "Owner", "Status")
    if any(h not in header for h in need):
        return [f"action items table needs columns {', '.join(need)}"]
    idx = {h: header.index(h) for h in need}
    errs = []
    for row in rows[2:]:
        if len(row) != len(header):
            errs.append(f"action item row has {len(row)} cells, expected {len(header)}")
            continue
        name = row[idx["Action"]][:40]
        if row[idx["Type"]] not in ACTION_TYPES:
            errs.append(
                f"action item {name!r}: type {row[idx['Type']]!r} not in {sorted(ACTION_TYPES)}"
            )
        if not re.fullmatch(r"P[0-4]", row[idx["Priority"]]):
            errs.append(f"action item {name!r}: priority must be P0-P4")
        if not row[idx["Owner"]]:
            errs.append(f"action item {name!r}: no owner")
        if row[idx["Status"]] not in ACTION_STATUS:
            errs.append(
                f"action item {name!r}: status {row[idx['Status']]!r} not in {sorted(ACTION_STATUS)}"
            )
    return errs


def lint_dir(root: Path) -> dict[str, list[str]]:
    docs = load_dir(root)
    ids = [d.id for d in docs]
    known = set(ids)
    report = {d.path: lint(d, known) for d in docs}
    for dup in {i for i in ids if ids.count(i) > 1}:
        report.setdefault("<corpus>", []).append(f"duplicate id {dup}")
    return report


def main() -> None:
    p = argparse.ArgumentParser(
        description="Lint runbooks and postmortems (layer 0, D45)."
    )
    p.add_argument(
        "paths", nargs="*", help="files to lint (default: the whole lab knowledge base)"
    )
    args = p.parse_args()
    if args.paths:
        report = {
            path: lint(parse(Path(path).read_text(), path)) for path in args.paths
        }
    else:
        report = lint_dir(KB_DIR)
    failed = {k: v for k, v in report.items() if v}
    for path, errs in failed.items():
        for e in errs:
            print(f"{path}: {e}")
    print(f"{len(report) - len(failed)}/{len(report)} documents pass")
    sys.exit(1 if failed else 0)
