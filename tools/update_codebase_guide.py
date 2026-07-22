#!/usr/bin/env python3
"""Refresh or validate the generated inventory in CODEBASE_GUIDE.md.

The explanatory chapters in the guide are written by a person. This script
owns only the block between the two GENERATED REPOSITORY INVENTORY markers.
Normal runs describe the working tree; the versioned Git hooks pass --staged
so a commit documents exactly its index rather than unrelated unstaged work.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "CODEBASE_GUIDE.md"
START = "<!-- BEGIN GENERATED REPOSITORY INVENTORY -->"
END = "<!-- END GENERATED REPOSITORY INVENTORY -->"


# Every committed file must have an authored purpose. Adding a file without
# adding a plain-language explanation here makes both the hook and CI fail.
# The longer, architectural explanations remain in CODEBASE_GUIDE.md itself.
FILE_PURPOSES: dict[str, tuple[str, str, str]] = {
    ".github/workflows/ci.yml": (
        "Automation",
        "Authored workflow",
        "Runs documentation and source-governance gates, the pinned Lean build, Python tests, Z3 proofs, generated-fixture freshness, and Python/JavaScript/Lean parity on pushes and pull requests.",
    ),
    ".githooks/pre-commit": (
        "Automation",
        "Authored hook",
        "Guards unstaged enforcement/prose edits, requires an authored review, refreshes the index-pure guide inventory, and stages the guide before a local commit.",
    ),
    ".githooks/pre-merge-commit": (
        "Automation",
        "Authored hook",
        "Reuses the pre-commit documentation refresh before Git creates a non-fast-forward merge commit.",
    ),
    ".gitignore": (
        "Repository",
        "Authored configuration",
        "Keeps editor files, Python caches, virtual environments, secrets, build output, and unpublished private writing out of Git.",
    ),
    "CODEBASE_GUIDE.md": (
        "Documentation",
        "Authored text + generated inventory",
        "Explains the entire project, its domain, trust model, data flow, files, commands, limitations, and documentation-maintenance process for a technically fluent newcomer.",
    ),
    "LICENSE": (
        "Repository",
        "Authored legal text",
        "Applies the MIT software license and disclaims warranties; it does not turn the project into tax or legal advice.",
    ),
    "LeanProof.lean": (
        "Lean proof project",
        "Authored module root",
        "Imports the checked-in Section 194R Lean module so the pinned Lake project has one stable build root.",
    ),
    "LeanProof/S194R.lean": (
        "Lean proof project",
        "Authored formal specification",
        "Defines the exact-paise Section 194R fact model, decision function, refusal paths, and compile-time examples used by runtime case proofs.",
    ),
    "README.md": (
        "Documentation",
        "Authored overview",
        "Presents the public thesis, machine-checked finding, measured results, comparison with Pramaana's public pattern, and scope limitations.",
    ),
    "REPO_STRUCTURE.md": (
        "Documentation",
        "Authored maintainer guide",
        "Gives front-end builders the shorter trust-chain, engine API, page, and hosting constraints needed to redesign the website safely.",
    ),
    "collabproof/__init__.py": (
        "Python package",
        "Authored source",
        "Defines the package's public import surface by re-exporting the assessor, verifier, domain models, helpers, rules, and deliberately naive answerer.",
    ),
    "collabproof/baseline.py": (
        "Python package",
        "Authored source",
        "Implements a plausible but intentionally wrong calculator whose eight documented mistakes give the verifier a realistic adversary.",
    ),
    "collabproof/governance.py": (
        "Python package",
        "Authored governance tooling",
        "Validates official-source metadata and rule provenance, hashes the governed bundle, fetches allowlisted sources, and reports certificate freshness impact.",
    ),
    "collabproof/llm_adapter.py": (
        "Python package",
        "Authored source",
        "Serializes facts for an LLM, enforces an exact eight-key JSON boundary, classifies abstentions/refusals, and optionally calls Anthropic when a key is supplied.",
    ),
    "collabproof/runtime_proof.py": (
        "Python package",
        "Authored proof bridge",
        "Normalizes a collaboration, generates a concrete Section 194R Lean theorem, checks it in a fresh Lean process, and emits a fail-closed hashed certificate.",
    ),
    "collabproof/spec.py": (
        "Python package",
        "Authored source of truth",
        "Encodes the FY 2024-25 tax-rule interpretation, exact-paise arithmetic, input/output data models, citations, refusal boundaries, and facts-to-assessment algorithm.",
    ),
    "collabproof/verify.py": (
        "Python package",
        "Authored source of truth",
        "Checks a complete typed six-field claim against an assessment and returns a fail-closed certificate with coverage, causal rule IDs, governed bundle identity, and freshness support.",
    ),
    "docs/collabproof.js": (
        "Browser",
        "Authored JavaScript port",
        "Ports the Python assessor, verifier, naive baseline, constants, and citations to a browser/Node-compatible engine whose behavior is checked by parity vectors.",
    ),
    "docs/index.html": (
        "Browser",
        "Authored static UI",
        "Provides the no-build interactive deal assessor, claim certifier, parity badge, rule explanations, and product-value sensitivity chart.",
    ),
    "docs/parity_check_node.js": (
        "Browser verification",
        "Authored CI runner",
        "Loads the JavaScript engine and generated vectors in Node, reports divergences, and exits non-zero so browser drift fails CI.",
    ),
    "docs/parity_vectors.js": (
        "Browser verification",
        "Generated by gen_parity_vectors.py",
        "Stores Python-produced expected results for 62 assessment rows (52 unique facts) and 15 adversarial verifier rows; it must never be edited by hand.",
    ),
    "docs/runtime-proof-artifacts.md": (
        "Documentation",
        "Authored proof guide",
        "Documents the Section 194R runtime theorem, certificate contents, trust boundary, uncovered outputs, and reproduction commands.",
    ),
    "docs/source-governance.md": (
        "Documentation",
        "Authored governance guide",
        "Explains source fetching, hashing, independent review gates, governed bundle identity, impact analysis, and stale-certificate handling.",
    ),
    "eval/cases.json": (
        "Evaluation",
        "Generated by run_eval.py",
        "Stores the 50 serialized collaboration fact patterns used to compare answerers against the executable specification.",
    ),
    "eval/results.json": (
        "Evaluation",
        "Generated by run_eval.py",
        "Stores per-case verdicts, mismatch explanations, rule-hit counts, and the limited secondary certified-but-wrong guard for the committed naive-baseline run.",
    ),
    "experiments/corpus/00_README.md": (
        "LLM experiment",
        "Authored warning",
        "Explains that grounded experiment arms load only ignored, manifest-declared official-source caches and fail closed when governed material is absent.",
    ),
    "experiments/results_selftest.json": (
        "LLM experiment",
        "Generated by three_arms.py --selftest",
        "Stores scripted plumbing-check results proving retries, incomplete answers, abstentions, invalid output, and out-of-scope assertions are counted as intended; these are not LLM results.",
    ),
    "experiments/three_arms.py": (
        "LLM experiment",
        "Authored experiment",
        "Runs bare, official-source-grounded, and verifier-feedback LLM arms, or a deterministic self-test, while preserving strict output validation and multi-turn context.",
    ),
    "gen_parity_vectors.py": (
        "Generation",
        "Authored generator",
        "Executes the Python source of truth over evaluation, golden, and adversarial verification cases and writes the frozen JavaScript expectations.",
    ),
    "lake-manifest.json": (
        "Lean proof project",
        "Generated Lake manifest",
        "Pins the dependency-free Lake workspace metadata used to reproduce the checked Lean build.",
    ),
    "lakefile.toml": (
        "Lean proof project",
        "Authored build configuration",
        "Defines the dependency-free Lean library and root module built locally and in CI.",
    ),
    "lean-toolchain": (
        "Lean proof project",
        "Authored toolchain pin",
        "Selects the exact Lean release used for local kernel checks and the CI build.",
    ),
    "proofs/check_lean_parity.py": (
        "Proof",
        "Authored parity runner",
        "Checks fixed Section 194R cases across the Python assessor, browser JavaScript engine, and compiled Lean model.",
    ),
    "proofs/example_s194r_facts.json": (
        "Proof",
        "Authored example input",
        "Provides a reproducible complete normalized fact pattern for the runtime Lean certificate CLI.",
    ),
    "proofs/prove_cliff.py": (
        "Proof",
        "Authored Z3 artifact",
        "Proves narrow recipient-mode dead-zone theorems, binds 100,000 values to assess(), and prints a separately labeled provider illustration that is not runtime-bound.",
    ),
    "pyproject.toml": (
        "Repository",
        "Authored configuration",
        "Declares package metadata, Python 3.10+ compatibility, the MIT license file, and pytest's test directory/options.",
    ),
    "requirements-dev.txt": (
        "Repository",
        "Authored lock-style list",
        "Pins the exact test and proof dependency versions used locally and in CI for reproducibility.",
    ),
    "run_eval.py": (
        "Evaluation",
        "Authored runner",
        "Builds the 50-case evaluation set, grades the naive or optional LLM answerer against the spec, prints aggregates, and regenerates eval JSON artifacts.",
    ),
    "sources/cache/.gitignore": (
        "Source governance",
        "Authored ignore policy",
        "Keeps fetched statutory bytes and derived text sidecars local while allowing the cache instructions and ignore policy to remain versioned.",
    ),
    "sources/cache/README.md": (
        "Source governance",
        "Authored cache guide",
        "Explains that official source bytes are local-only inputs governed by the manifest and must not be committed without documented redistribution permission.",
    ),
    "sources/fixtures/official-corpus-metadata.json": (
        "Source governance",
        "Authored metadata fixture",
        "Provides non-copyrighted official-source metadata for reproducible governance and grounded-corpus tests without bundling legal text.",
    ),
    "sources/manifest.yaml": (
        "Source governance",
        "Authored source manifest",
        "Inventories official authorities, URLs, applicability dates, retrieval state, digests, redistribution decisions, caches, and review status.",
    ),
    "sources/provenance.yaml": (
        "Source governance",
        "Authored rule provenance",
        "Maps every runtime rule ID to exact source locations, interpretation, assumptions, boundary tests, formal evidence, and review status.",
    ),
    "sources/schema.md": (
        "Source governance",
        "Authored schema guide",
        "Documents the manifest and provenance fields plus the mechanical requirements for promoting a rule from experimental to production.",
    ),
    "tests/test_golden.py": (
        "Tests",
        "Authored tests",
        "Checks ten hand-computed statutory scenarios and two refusal scenarios, using a human interpretation rather than the implementation as the oracle.",
    ),
    "tests/test_governance.py": (
        "Tests",
        "Authored governance tests",
        "Checks source/rule completeness, bundle-hash sensitivity, cache integrity, review promotion, impact analysis, and certificate freshness decisions.",
    ),
    "tests/test_llm_boundary.py": (
        "Tests",
        "Authored tests",
        "Regression-tests complete fact serialization, strict JSON types/keys, refusal and abstention semantics, evaluator consistency, and context-preserving retries.",
    ),
    "tests/test_codebase_guide.py": (
        "Tests",
        "Authored integration tests",
        "Exercises index purity, deletion review, unborn-repository adoption, per-commit history enforcement, atomic mode preservation, and marker validation for documentation automation.",
    ),
    "tests/test_properties.py": (
        "Tests",
        "Authored property tests",
        "Uses Hypothesis-generated collaborations to check seven invariants such as selected TDS non-negativity, threshold behavior, round-trip certification, and monotonic registration.",
    ),
    "tests/test_runtime_proof.py": (
        "Tests",
        "Authored proof-bridge tests",
        "Checks normalized fact completeness, concrete theorem generation, certificate hashes and scope labels, conditional cash output, and fail-closed Lean failures.",
    ),
    "tests/test_verify.py": (
        "Tests",
        "Authored adversarial tests",
        "Checks completeness, runtime claim types, status precedence, ambiguity, refusals, and path-specific mismatch rule attribution.",
    ),
    "tools/update_codebase_guide.py": (
        "Documentation automation",
        "Authored standard-library tool",
        "Requires per-file purposes, refreshes or validates the repository digest, and enforces authored guide movement per staged change and per CI commit.",
    ),
}

# These files are snapshots derived from other authored files. Changing only a
# generated snapshot does not require rewriting the guide's explanatory prose.
GENERATED_ARTIFACTS = {
    "docs/parity_vectors.js",
    "eval/cases.json",
    "eval/results.json",
    "experiments/results_selftest.json",
}


def git(*args: str, text: bool = True) -> str | bytes:
    """Run a read-only Git query from the repository root."""
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    return result.stdout


def indexed_entries() -> dict[str, str]:
    """Return index path -> Git mode without consulting the working tree."""
    raw = git("ls-files", "--stage", "-z", text=False)
    assert isinstance(raw, bytes)
    entries: dict[str, str] = {}
    for record in (part for part in raw.split(b"\0") if part):
        metadata, path = record.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0].decode("ascii")
        entries[path.decode("utf-8")] = mode
    return entries


def files_to_document(indexed: dict[str, str], *, staged: bool) -> list[str]:
    index_paths = set(indexed)
    if staged:
        # Index purity is essential: an unstaged recreation of a staged deletion
        # must not leak back into the proposed commit's inventory.
        paths = index_paths
    else:
        absent = sorted(
            path for path in index_paths
            if not os.path.lexists(ROOT / path)
        )
        if absent:
            print(
                "Tracked files are absent from the working tree. Stage their "
                "deletion or restore them before refreshing the guide:",
                file=sys.stderr,
            )
            for path in absent:
                print(f"  - {path}", file=sys.stderr)
            raise SystemExit(2)
        # Known files are included before their first commit, which makes the
        # tool usable while this documentation system is initially introduced.
        known_and_present = {
            path for path in FILE_PURPOSES if os.path.lexists(ROOT / path)
        }
        paths = index_paths | known_and_present

    missing = sorted(paths - FILE_PURPOSES.keys())
    stale = sorted(FILE_PURPOSES.keys() - paths)
    if missing or stale:
        if missing:
            print("CODEBASE_GUIDE inventory has no purpose for:", file=sys.stderr)
            for path in missing:
                print(f"  - {path}", file=sys.stderr)
        if stale:
            print("Purpose entries exist for files that are absent:", file=sys.stderr)
            for path in stale:
                print(f"  - {path}", file=sys.stderr)
        print(
            "Update FILE_PURPOSES in tools/update_codebase_guide.py, then rerun.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return sorted(paths)


def bytes_for(path: str, indexed: dict[str, str], *, staged: bool) -> bytes:
    # `git show :path` reads the staged blob. Hooks select this path so their
    # generated digest is about the next commit, not unrelated unstaged work.
    if staged and path in indexed:
        raw = git("show", f":{path}", text=False)
        assert isinstance(raw, bytes)
        return raw
    working_path = ROOT / path
    if working_path.is_symlink():
        return os.readlink(working_path).encode("utf-8")
    return working_path.read_bytes()


def git_mode_for(path: str, indexed: dict[str, str], *, staged: bool) -> str:
    if staged:
        return indexed[path]
    mode = os.lstat(ROOT / path).st_mode
    if stat.S_ISLNK(mode):
        return "120000"
    if stat.S_ISREG(mode):
        return "100755" if mode & 0o111 else "100644"
    # Gitlinks or other unusual entries are still distinguished in the digest.
    return f"worktree-{stat.S_IFMT(mode):o}"


def line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def generated_inventory(
    paths: list[str], indexed: dict[str, str], *, staged: bool
) -> str:
    digest = hashlib.sha256()
    rows: list[str] = []
    for path in paths:
        area, maintenance, purpose = FILE_PURPOSES[path]
        if path == "CODEBASE_GUIDE.md":
            lines: str | int = "self"
        else:
            data = bytes_for(path, indexed, staged=staged)
            lines = line_count(data)
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(git_mode_for(path, indexed, staged=staged).encode("ascii"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(data).digest())
        rows.append(
            f"| `{cell(path)}` | {cell(area)} | {cell(lines)} | "
            f"{cell(maintenance)} | {cell(purpose)} |"
        )

    return "\n".join(
        [
            "<!-- Generated by tools/update_codebase_guide.py. Do not hand-edit this block. -->",
            "",
            f"**Documented files:** {len(paths)}",
            f"**Repository-content snapshot ({'staged Git index' if staged else 'working tree'}):** "
            f"`sha256:{digest.hexdigest()}`",
            "",
            "The digest covers the path, Git-style file mode, and contents of every row except "
            "this guide itself. It changes when source, tests, data, configuration, automation, "
            "or executable bits change, even if the file's line count does not.",
            "",
            "| File | Area | Lines | Maintenance | What it does and why it exists |",
            "|---|---|---:|---|---|",
            *rows,
            "",
        ]
    )


def expected_guide(current: str, inventory: str) -> str:
    if (
        current.count(START) != 1
        or current.count(END) != 1
        or current.index(START) > current.index(END)
    ):
        raise SystemExit(
            f"{GUIDE.name} must contain exactly one ordered {START!r} and {END!r}."
        )
    before, tail = current.split(START, 1)
    _old, after = tail.split(END, 1)
    return f"{before}{START}\n{inventory}{END}{after}"


def authored_part(content: str) -> str:
    """Remove the machine-owned block so prose reviews cannot be faked by a digest."""
    if content.count(START) != 1 or content.count(END) != 1:
        return content
    before, tail = content.split(START, 1)
    _generated, after = tail.split(END, 1)
    return before + after


def show_text(specifier: str) -> str | None:
    try:
        raw = git("show", specifier, text=False)
    except subprocess.CalledProcessError:
        return None
    assert isinstance(raw, bytes)
    return raw.decode("utf-8")


def commit_exists(ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def decoded_paths(raw: bytes) -> set[str]:
    return {part.decode("utf-8") for part in raw.split(b"\0") if part}


def review_relevant_paths(paths: set[str]) -> list[str]:
    return sorted(paths - GENERATED_ARTIFACTS - {"CODEBASE_GUIDE.md"})


def require_authored_review(base_ref: str) -> int:
    """Require staged prose movement when authored repository files changed.

    This cannot judge prose quality. It does prevent the generated digest from
    being the only CODEBASE_GUIDE change in a source-changing commit or merge.
    """
    if not base_ref or set(base_ref) == {"0"}:
        print("No usable base revision; authored-guide comparison skipped.")
        return 0
    base_exists = commit_exists(base_ref)
    if not base_exists and base_ref != "HEAD":
        print(
            f"Cannot compare documentation: {base_ref!r} is not a commit.",
            file=sys.stderr,
        )
        return 2
    diff_args = ["diff", "--cached", "--name-only", "-z"]
    if base_exists:
        diff_args.append(base_ref)
    diff_args.append("--")
    changed_raw = git(*diff_args, text=False)
    assert isinstance(changed_raw, bytes)
    review_relevant = review_relevant_paths(decoded_paths(changed_raw))
    if not review_relevant:
        print("No authored repository changes require a prose review.")
        return 0

    old = show_text(f"{base_ref}:CODEBASE_GUIDE.md") if base_exists else None
    new = show_text(":CODEBASE_GUIDE.md")
    # Introducing the guide is itself the initial full review.
    if old is None and new is not None:
        print("CODEBASE_GUIDE.md is new; initial authored review is present.")
        return 0
    if old is None or new is None or authored_part(old) == authored_part(new):
        print(
            "Authored repository files changed, but only the generated guide "
            "inventory (or no guide text) changed.",
            file=sys.stderr,
        )
        print("Review and update CODEBASE_GUIDE.md prose for:", file=sys.stderr)
        for path in review_relevant:
            print(f"  - {path}", file=sys.stderr)
        print(
            "Stage the prose update before committing. A concise entry in the "
            "guide's documentation review record is sufficient when no other "
            "explanation needs to change.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Authored guide review present for {len(review_relevant)} changed file(s)."
    )
    return 0


def changed_paths_between(parent: str | None, commit: str) -> set[str]:
    if parent is None:
        raw = git(
            "diff-tree", "--root", "--no-commit-id", "--name-only", "-z", "-r",
            commit, text=False,
        )
    else:
        raw = git("diff", "--name-only", "-z", parent, commit, "--", text=False)
    assert isinstance(raw, bytes)
    return decoded_paths(raw)


def first_parent_of(commit: str) -> str | None:
    line = git("rev-list", "--parents", "-n", "1", commit)
    assert isinstance(line, str)
    parts = line.split()
    return parts[1] if len(parts) > 1 else None


def require_each_commit_review(base_ref: str) -> int:
    """Enforce the authored-guide rule separately on every commit in a range."""
    if not base_ref or set(base_ref) == {"0"}:
        print("No usable base revision; per-commit authored review skipped.")
        return 0
    if not commit_exists(base_ref):
        print(
            f"Cannot audit commit history: {base_ref!r} is not a commit.",
            file=sys.stderr,
        )
        return 2
    raw_commits = git("rev-list", "--reverse", "--topo-order", f"{base_ref}..HEAD")
    assert isinstance(raw_commits, str)
    commits = [line for line in raw_commits.splitlines() if line]
    failures: list[tuple[str, list[str]]] = []

    for commit in commits:
        parent = first_parent_of(commit)
        relevant = review_relevant_paths(changed_paths_between(parent, commit))
        if not relevant:
            continue
        old = show_text(f"{parent}:CODEBASE_GUIDE.md") if parent else None
        new = show_text(f"{commit}:CODEBASE_GUIDE.md")
        if old is None and new is not None:
            continue
        if old is None or new is None or authored_part(old) == authored_part(new):
            failures.append((commit, relevant))

    if failures:
        print(
            "These commits change authored repository files without changing "
            "the human-written guide:",
            file=sys.stderr,
        )
        for commit, relevant in failures:
            print(f"  - {commit[:12]}: {', '.join(relevant)}", file=sys.stderr)
        print(
            "Update the guide in each source-changing commit, or squash/fix up "
            "the branch so its reviewable commits remain self-documenting.",
            file=sys.stderr,
        )
        return 1
    print(f"Per-commit authored-guide review passed ({len(commits)} commit(s)).")
    return 0


def write_atomically(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(mode)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh or validate CODEBASE_GUIDE.md's generated inventory."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail when the generated inventory is stale",
    )
    parser.add_argument(
        "--require-authored-change-from",
        metavar="GIT_REF",
        help=(
            "fail if authored files changed from GIT_REF but the guide's "
            "human-written portion did not"
        ),
    )
    parser.add_argument(
        "--require-each-commit-authored-review-from",
        metavar="GIT_REF",
        help=(
            "audit every commit in GIT_REF..HEAD and fail when an authored-file "
            "change has no human-written guide change in the same commit"
        ),
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="build/check the inventory from the Git index instead of working files",
    )
    args = parser.parse_args()

    if not GUIDE.exists():
        print(f"Missing {GUIDE}", file=sys.stderr)
        return 2

    if args.require_authored_change_from:
        return require_authored_review(args.require_authored_change_from)
    if args.require_each_commit_authored_review_from:
        return require_each_commit_review(
            args.require_each_commit_authored_review_from
        )

    indexed = indexed_entries()
    paths = files_to_document(indexed, staged=args.staged)
    current = GUIDE.read_text(encoding="utf-8")
    expected = expected_guide(
        current, generated_inventory(paths, indexed, staged=args.staged)
    )

    if current == expected:
        print(f"{GUIDE.name} inventory is current ({len(paths)} files).")
        return 0
    if args.check:
        print(
            f"{GUIDE.name} inventory is stale. Run: "
            "python tools/update_codebase_guide.py",
            file=sys.stderr,
        )
        return 1

    write_atomically(GUIDE, expected)
    print(f"Updated {GUIDE.name} inventory ({len(paths)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
