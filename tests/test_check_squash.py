"""Pre-push squash check (scripts/check_squash.py). No LLM, no cluster."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).parents[1] / "scripts" / "check_squash.py"
_spec = importlib.util.spec_from_file_location("check_squash", _PATH)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
sys.modules["check_squash"] = cs  # dataclasses resolve the module
_spec.loader.exec_module(cs)

C = cs.Commit
FEATURE = C(
    "a" * 40,
    "chore(hooks): enforce commit message convention",
    files={".githooks/commit-msg", "scripts/check_commit_msg.py", "Makefile"},
)


def levels(commits: list) -> list[str]:
    return [f.level for f in cs.analyze(commits)]


def test_clean_history_passes() -> None:
    other = C("b" * 40, "fix(tools): cap pod log output", files={"src/x.py"})
    assert levels([FEATURE, other]) == []


def test_fixup_marker_is_an_error() -> None:
    fix = C("b" * 40, "fixup! chore(hooks): enforce commit message convention")
    assert levels([FEATURE, fix]) == ["error"]


def test_docs_for_an_unpushed_change_should_be_squashed() -> None:
    # The real case: README section explaining the hook added one commit earlier.
    docs = C(
        "b" * 40,
        "docs: explain git hook setup in README prerequisites",
        files={"README.md"},
        added="The hook lives in `.githooks/`, run `make hooks` once per clone.",
    )
    findings = cs.analyze([FEATURE, docs])
    assert [f.level for f in findings] == ["squash"]
    assert ".githooks" in findings[0].message


def test_unrelated_docs_are_not_squashed() -> None:
    docs = C(
        "b" * 40, "docs: fix typo in demo guide", files={"docs/demo.md"}, added="the"
    )
    assert levels([FEATURE, docs]) == []


def test_same_type_scope_and_files_should_be_squashed() -> None:
    again = C(
        "b" * 40,
        "chore(hooks): allow merge commits",
        files={"scripts/check_commit_msg.py"},
    )
    assert levels([FEATURE, again]) == ["squash"]


def test_refactor_and_feature_on_same_file_stay_separate() -> None:
    refactor = C("a" * 40, "refactor(tools): extract log trimming", files={"src/t.py"})
    feat = C("b" * 40, "feat(tools): add pod event tool", files={"src/t.py"})
    assert levels([refactor, feat]) == []


def test_revert_of_unpushed_commit_drops_both() -> None:
    rev = C(
        "b" * 40, 'Revert "chore(hooks): x"', body=f"This reverts commit {'a' * 40}."
    )
    assert levels([FEATURE, rev]) == ["squash"]


def test_docs_that_reference_ids_added_earlier_should_be_squashed() -> None:
    # The real case: decisions D41/D42, then a README roadmap pointing to them.
    d1 = C(
        "a" * 40,
        "docs: decide runbook analyst and confluence source",
        files={"docs/decisions.md"},
        added="| D41 | A read-only Runbook Analyst |\n| D42 | Confluence |",
    )
    d2 = C(
        "b" * 40,
        "docs: add runbook analyst and skills to roadmap",
        files={"README.md"},
        added="- [ ] Runbook Analyst subagent over Confluence (D41, D42)",
    )
    findings = cs.analyze([d1, d2])
    assert [f.level for f in findings] == ["squash"]
    assert "D41" in findings[0].message


def test_consecutive_docs_commits_are_only_a_hint() -> None:
    d1 = C("a" * 40, "docs: add demo guide", files={"docs/demo.md"}, added="Run it")
    d2 = C("b" * 40, "docs: fix README badge", files={"README.md"}, added="badge")
    assert levels([d1, d2]) == ["consider"]


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_reads_unpushed_commits_from_a_real_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# demo\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "docs: start the demo readme")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "budget_guard.py").write_text("LIMIT = 15\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feat: add monthly budget guard")
    (repo / "README.md").write_text("# demo\nThe budget_guard caps spend.\n")
    _git(repo, "commit", "-q", "-am", "docs: explain the budget guard")
    head = _git(repo, "rev-parse", "HEAD")

    line = f"refs/heads/main {head} refs/heads/main {base}"
    assert [f.level for f in cs.run([line], cwd=str(repo))] == ["squash"]
    # Deleting a remote branch pushes nothing to check.
    assert cs.run([f"(delete) {cs.ZERO} refs/heads/x {base}"], cwd=str(repo)) == []
