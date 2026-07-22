"""Integration-style regressions for the documentation freshness tooling."""

from pathlib import Path
import stat
import subprocess

import pytest

from tools import update_codebase_guide as guide


START = guide.START
END = guide.END


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def configure_tool(monkeypatch, repo: Path, purposes=None) -> None:
    monkeypatch.setattr(guide, "ROOT", repo)
    monkeypatch.setattr(guide, "GUIDE", repo / "CODEBASE_GUIDE.md")
    monkeypatch.setattr(
        guide,
        "FILE_PURPOSES",
        purposes or {
            "CODEBASE_GUIDE.md": ("Docs", "Authored", "Test guide"),
            "source.txt": ("Source", "Authored", "Test source"),
        },
    )
    monkeypatch.setattr(guide, "GENERATED_ARTIFACTS", set())


def init_repo(tmp_path: Path, monkeypatch, *, commit=True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "guide-test@example.invalid")
    run_git(repo, "config", "user.name", "Guide Test")
    write(repo / "CODEBASE_GUIDE.md", f"# Guide\n\n{START}\n{END}\n")
    write(repo / "source.txt", "version one\n")
    run_git(repo, "add", "CODEBASE_GUIDE.md", "source.txt")
    if commit:
        run_git(repo, "commit", "-qm", "initial")
    configure_tool(monkeypatch, repo)
    return repo


def test_staged_inventory_does_not_read_unstaged_recreated_deletion(
    tmp_path, monkeypatch
):
    repo = init_repo(tmp_path, monkeypatch)
    (repo / "source.txt").unlink()
    run_git(repo, "add", "-u", "source.txt")
    write(repo / "source.txt", "unstaged recreation\n")

    with pytest.raises(SystemExit) as error:
        guide.files_to_document(guide.indexed_entries(), staged=True)
    assert error.value.code == 2  # stale purpose: staged tree really deleted it


def test_staged_deletion_requires_authored_review(tmp_path, monkeypatch):
    repo = init_repo(tmp_path, monkeypatch)
    (repo / "source.txt").unlink()
    run_git(repo, "add", "-u", "source.txt")

    assert guide.require_authored_review("HEAD") == 1


def test_unborn_repository_accepts_new_guide_as_initial_review(tmp_path, monkeypatch):
    init_repo(tmp_path, monkeypatch, commit=False)
    assert guide.require_authored_review("HEAD") == 0


def test_per_commit_audit_rejects_later_source_only_commit(tmp_path, monkeypatch):
    repo = init_repo(tmp_path, monkeypatch)
    base = run_git(repo, "rev-parse", "HEAD")

    write(repo / "source.txt", "version two\n")
    write(repo / "CODEBASE_GUIDE.md", f"# Guide reviewed v2\n\n{START}\n{END}\n")
    run_git(repo, "add", "source.txt", "CODEBASE_GUIDE.md")
    run_git(repo, "commit", "-qm", "documented source change")

    write(repo / "source.txt", "version three\n")
    run_git(repo, "add", "source.txt")
    run_git(repo, "commit", "-qm", "undocumented source change")

    assert guide.require_each_commit_review(base) == 1


def test_atomic_write_preserves_ordinary_file_mode(tmp_path, monkeypatch):
    repo = init_repo(tmp_path, monkeypatch)
    path = repo / "CODEBASE_GUIDE.md"
    path.chmod(0o644)

    guide.write_atomically(path, "replacement\n")

    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_reversed_inventory_markers_fail_cleanly(tmp_path, monkeypatch):
    init_repo(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="exactly one ordered"):
        guide.expected_guide(f"{END}\n{START}\n", "generated")
