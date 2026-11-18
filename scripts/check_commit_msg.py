"""Validate a commit message against the project convention (see CONTRIBUTING.md).

Format (Conventional Commits 1.0 + the 50/72 rule):

    <type>(<scope>)!: <summary>      <- header, <= 72 chars (aim for 50)

    <body: why the change is needed and what was wrong before>

    <trailers: Refs: ..., Fixes: ..., BREAKING CHANGE: ...>

Used as the `commit-msg` git hook (`make hooks`) and runnable by hand:
    python scripts/check_commit_msg.py .git/COMMIT_EDITMSG
Stdlib only, so the hook works before `uv sync`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TYPES = {
    "feat": "new capability for users or operators",
    "fix": "bug fix",
    "perf": "faster or cheaper (tokens, latency) without behavior change",
    "refactor": "code change that neither fixes a bug nor adds a feature",
    "test": "add or correct tests",
    "eval": "evaluation datasets, evaluators or experiments",
    "docs": "documentation only",
    "build": "dependencies, packaging, uv",
    "ci": "CI configuration",
    "chore": "tooling and maintenance that touches no runtime code",
    "revert": "revert a previous commit",
}
# Types whose commits change behavior: the body must explain why.
BODY_REQUIRED = {"feat", "fix", "perf", "refactor", "revert"}
KNOWN_SCOPES = {
    "agents", "alerts", "budget", "cli", "deps", "evals", "graph", "hooks",
    "infra", "k8s", "llm", "middleware", "postmortem", "prompts", "rbac",
    "scenarios", "slack", "tools",
}  # fmt: skip

HEADER_MAX = 72
HEADER_SOFT = 50
BODY_WIDTH = 72
BODY_MIN = 20

HEADER_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?: (?P<summary>.*)$"
)
SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9-]+)?$")
TRAILER_RE = re.compile(r"^(BREAKING[ -]CHANGE|[A-Za-z][A-Za-z0-9-]*)(: | #)\S")
SCISSORS = "# ------------------------ >8 ------------------------"
# Messages git generates or that will be squashed away before merging.
PASSTHROUGH = ("Merge ", 'Revert "', "fixup! ", "squash! ", "amend! ")
VAGUE = {
    "update", "updates", "fix", "fixes", "change", "changes", "wip", "misc",
    "stuff", "tmp", "temp", "test", "tests", "cleanup", "minor", "tweak",
    "tweaks", "improvements", "refactor", "work", "progress", "commit", "save",
}  # fmt: skip
# Common non-imperative first words ("added", "adds", "adding", ...).
_VERBS = [
    "add",
    "allow",
    "bump",
    "change",
    "clean",
    "correct",
    "create",
    "drop",
    "enable",
    "disable",
    "document",
    "extract",
    "fix",
    "handle",
    "implement",
    "improve",
    "introduce",
    "make",
    "merge",
    "move",
    "prevent",
    "refactor",
    "remove",
    "rename",
    "replace",
    "restore",
    "return",
    "revert",
    "run",
    "set",
    "simplify",
    "split",
    "support",
    "switch",
    "update",
    "upgrade",
    "use",
    "validate",
    "wire",
]


def _forms(verb: str) -> set[str]:
    stem = verb.removesuffix("e")
    third = verb + ("es" if verb.endswith(("x", "h", "s")) else "s")
    return {third, stem + "ed", stem + "ing", verb + "d" if verb.endswith("e") else ""}


NON_IMPERATIVE = set().union(*(_forms(v) for v in _VERBS)) - {""}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clean(raw: str) -> list[str]:
    """Drop git comments and the `--verbose` diff, as git does after the hook."""
    lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith(SCISSORS):
            break
        if not line.startswith("#"):
            lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def check(raw: str) -> Report:
    r = Report()
    lines = clean(raw)
    if not lines:
        r.errors.append("empty commit message")
        return r
    header = lines[0]
    if header.startswith(PASSTHROUGH):
        return r

    m = HEADER_RE.match(header)
    if not m:
        r.errors.append(
            "header must be '<type>(<scope>): <summary>' "
            "(e.g. 'fix(tools): cap log output at TOOL_MAX_CHARS')"
        )
        return r
    ctype, scope, summary = m["type"], m["scope"], m["summary"]
    breaking = bool(m["breaking"])

    if ctype not in TYPES:
        r.errors.append(f"unknown type '{ctype}'; use one of: {', '.join(TYPES)}")
    if scope is not None:
        if not SCOPE_RE.match(scope):
            r.errors.append(
                f"scope '{scope}' must be lowercase kebab-case, e.g. 'tools'"
            )
        elif scope.split("/")[0] not in KNOWN_SCOPES:
            r.warnings.append(
                f"new scope '{scope}'; known: {', '.join(sorted(KNOWN_SCOPES))}"
            )

    if len(header) > HEADER_MAX:
        r.errors.append(f"header is {len(header)} chars; max {HEADER_MAX}")
    elif len(header) > HEADER_SOFT:
        r.warnings.append(f"header is {len(header)} chars; aim for <= {HEADER_SOFT}")

    words = summary.split()
    if not words:
        r.errors.append("summary is empty")
    else:
        first = words[0]
        if summary != summary.strip():
            r.errors.append("summary has extra whitespace")
        if summary.endswith("."):
            r.errors.append("summary must not end with a period")
        if re.match(r"^[A-Z][a-z]+$", first):
            r.errors.append(
                f"start the summary in lowercase ('{first.lower()}'), "
                "unless it is an acronym or identifier"
            )
        if first.lower() in NON_IMPERATIVE:
            r.errors.append(
                f"use the imperative mood: '{first}' -> 'add', 'fix', 'remove'... "
                "(test: 'If applied, this commit will <summary>')"
            )
        if summary.lower() in VAGUE or len(words) < 2:
            r.errors.append(
                f"summary '{summary}' is too vague; say what changes "
                "(e.g. 'retry k8s API calls on 429')"
            )

    rest = lines[1:]
    if rest and rest[0]:
        r.errors.append("leave a blank line between the header and the body")

    trailers_start = len(rest)
    for i in range(len(rest) - 1, -1, -1):
        line = rest[i]
        if not line:
            break
        if TRAILER_RE.match(line) or line.startswith((" ", "\t")):
            trailers_start = i
        else:
            break
    body_lines = rest[:trailers_start]
    body = "\n".join(body_lines).strip()

    for n, line in enumerate(body_lines, start=2):
        if (
            len(line) > BODY_WIDTH
            and "://" not in line
            and not line.startswith(("    ", "\t"))
        ):
            r.errors.append(
                f"line {n} is {len(line)} chars; wrap the body at {BODY_WIDTH}"
            )

    needs_body = ctype in BODY_REQUIRED or breaking
    if needs_body and len(body) < BODY_MIN:
        r.errors.append(
            f"'{ctype}{'!' if breaking else ''}' commits need a body "
            f"(>= {BODY_MIN} chars) explaining why the change is needed"
        )
    if ctype == "revert" and "This reverts commit" not in body:
        r.errors.append("revert body must include 'This reverts commit <sha>' and why")

    for line in rest:
        if re.match(r"^breaking[ -]change:", line, re.IGNORECASE) and not (
            line.startswith(("BREAKING CHANGE: ", "BREAKING-CHANGE: "))
        ):
            r.errors.append(
                "write the footer as 'BREAKING CHANGE: <description>' (uppercase)"
            )
    return r


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: check_commit_msg.py <commit-msg-file>", file=sys.stderr)
        return 2
    report = check(Path(args[0]).read_text(encoding="utf-8"))
    for w in report.warnings:
        print(f"commit-msg: warning: {w}", file=sys.stderr)
    for e in report.errors:
        print(f"commit-msg: error: {e}", file=sys.stderr)
    if report.errors:
        print(
            "commit-msg: commit rejected; see CONTRIBUTING.md#commit-messages "
            "(message kept in .git/COMMIT_EDITMSG)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
