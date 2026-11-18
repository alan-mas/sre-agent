"""Before a push, flag unpushed commits that should be squashed (CONTRIBUTING.md).

A commit is one self-contained logical change that ships with its own tests and
docs. Rewriting is only safe before sharing, so the check runs as the `pre-push`
git hook (`make hooks`) and looks only at commits the remote does not have yet.

Levels:
  error    fixup!/squash!/amend! commits left over: run an autosquash rebase.
  squash   a later commit belongs to an earlier one (blocks; SQUASH_OK=1 to accept).
  consider worth a look, does not block.

Stdlib only, so the hook works before `uv sync`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

ZERO = "0" * 40
MARKERS = ("fixup! ", "squash! ", "amend! ")
HEADER_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()]*)\))?!?: ")
REVERTS_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
ID_RE = re.compile(r"(?<![\w-])(?:[A-Z][A-Z0-9]*-?\d+|#\d+)\b")
DOC_SUFFIXES = (".md", ".rst", ".txt")
# File and directory names too common to tie two commits together.
GENERIC = {
    "readme", "readme.md", "makefile", "__init__", "__init__.py", "pyproject",
    "pyproject.toml", "src", "tests", "docs", "scripts", "sre_agent", "infra",
    "claude.md", "contributing.md", "decisions.md", "uv.lock",
}  # fmt: skip


@dataclass
class Commit:
    sha: str
    header: str
    body: str = ""
    files: set[str] = field(default_factory=set)
    added: str = ""  # added lines of the diff

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def kind(self) -> tuple[str, str] | None:
        m = HEADER_RE.match(self.header)
        return (m["type"], m["scope"] or "") if m else None


@dataclass
class Finding:
    level: str  # "error" | "squash" | "consider"
    message: str


def _docs_or_tests_only(c: Commit) -> bool:
    return bool(c.files) and all(
        f.endswith(DOC_SUFFIXES) or f.startswith(("docs/", "tests/")) for f in c.files
    )


def _names(files: set[str]) -> set[str]:
    """Distinctive names a follow-up commit would mention: dirs, files, stems."""
    names: set[str] = set()
    for f in files:
        p = PurePosixPath(f)
        names.update({p.name, p.stem, *p.parent.parts})
    return {n for n in names if len(n) >= 4 and n.lower() not in GENERIC}


def _ids(text: str) -> set[str]:
    """Identifiers such as decision ids (D41), tickets (SRE-12) or issues (#12)."""
    return set(ID_RE.findall(text))


def _follow_up_hits(a: Commit, b: Commit) -> list[str]:
    """What ties a docs/tests-only commit `b` to an earlier commit `a`.

    `b` mentions files that `a` changed (when `a` is code), or ids that `a` added.
    """
    if not _docs_or_tests_only(b):
        return []
    names = set() if _docs_or_tests_only(a) else _names(a.files)
    hits = {n for n in names if n in b.added} | (_ids(a.added) & _ids(b.added))
    return sorted(hits)


def analyze(commits: list[Commit]) -> list[Finding]:
    """`commits` are unpushed, oldest first."""
    out: list[Finding] = []
    for i, b in enumerate(commits):
        if b.header.startswith(MARKERS):
            out.append(
                Finding(
                    "error", f"{b.short} '{b.header}' is a fixup marker: autosquash it"
                )
            )
            continue
        earlier = commits[:i]
        m = REVERTS_RE.search(b.body)
        target = next((a for a in earlier if m and a.sha.startswith(m.group(1))), None)
        if target:
            out.append(
                Finding(
                    "squash",
                    f"{b.short} reverts unpushed {target.short}: drop both commits",
                )
            )
            continue
        for a in reversed(earlier):
            if b.kind and b.kind == a.kind and b.files & a.files:
                out.append(
                    Finding(
                        "squash",
                        f"{b.short} '{b.header}' repeats {a.short}'s type/scope on the "
                        f"same files ({', '.join(sorted(b.files & a.files))}): "
                        "one logical change, one commit",
                    )
                )
                break
            if hits := _follow_up_hits(a, b):
                out.append(
                    Finding(
                        "squash",
                        f"{b.short} '{b.header}' documents or tests {a.short} "
                        f"(mentions {', '.join(hits[:3])}): ship a change with its "
                        "docs and tests",
                    )
                )
                break
        else:
            prev = earlier[-1] if earlier else None
            if (
                prev
                and b.kind
                and b.kind == prev.kind
                and b.kind[0] in {"docs", "chore"}
            ):
                out.append(
                    Finding(
                        "consider",
                        f"{prev.short} and {b.short} are consecutive "
                        f"'{b.kind[0]}' commits: same topic?",
                    )
                )
    return out


# --- git plumbing ---


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def load(sha: str, cwd: str | None = None) -> Commit:
    header, _, body = _git("log", "-1", "--format=%s%x00%b", sha, cwd=cwd).partition(
        "\0"
    )
    files = set(
        _git(
            "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha, cwd=cwd
        ).split()
    )
    diff = _git("show", "--format=", "--unified=0", sha, cwd=cwd)
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return Commit(sha, header.strip(), body, files, added)


def unpushed(local_sha: str, remote_sha: str, cwd: str | None = None) -> list[str]:
    rng = (
        [local_sha, "--not", "--remotes"]
        if remote_sha == ZERO
        else [f"{remote_sha}..{local_sha}"]
    )
    return _git("rev-list", "--reverse", "--no-merges", *rng, cwd=cwd).split()


def run(stdin_lines: list[str], cwd: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:  # deleting a remote branch
            continue
        _, local_sha, _, remote_sha = parts
        commits = [load(s, cwd) for s in unpushed(local_sha, remote_sha, cwd)]
        findings += analyze(commits)
    return findings


def main() -> int:
    findings = run(sys.stdin.read().splitlines())
    if not findings:
        return 0
    for f in findings:
        print(f"pre-push: {f.level}: {f.message}", file=sys.stderr)
    errors = [f for f in findings if f.level == "error"]
    squash = [f for f in findings if f.level == "squash"]
    if errors:
        print(
            "pre-push: run `git rebase -i --autosquash origin/main`, then push again",
            file=sys.stderr,
        )
        return 1
    if squash and os.environ.get("SQUASH_OK") != "1":
        print(
            "pre-push: these commits are not pushed yet, so rewriting them is safe:\n"
            "  git rebase -i origin/main   # mark the later commit 'fixup' (or 'drop')\n"
            "pre-push: to push as is: SQUASH_OK=1 git push",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
