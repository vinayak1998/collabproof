"""Versioned source governance, bundle hashing, validation, and impact analysis."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "sources" / "manifest.yaml"
PROVENANCE_PATH = ROOT / "sources" / "provenance.yaml"
SPEC_PATH = ROOT / "collabproof" / "spec.py"
JS_PATH = ROOT / "docs" / "collabproof.js"
OFFICIAL_HOST_SUFFIXES = ("incometaxindia.gov.in", "cbic-gst.gov.in")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a mapping")
    return data


def manifest() -> dict:
    return load_yaml(MANIFEST_PATH)


def provenance() -> dict:
    return load_yaml(PROVENANCE_PATH)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True,
                      default=lambda item: item.isoformat()).encode("utf-8")


def rule_bundle_hash(runtime_rules: Mapping[str, object]) -> str:
    """Hash governed inputs and executable specification deterministically."""
    registry = {
        rule_id: {
            "citation": getattr(rule, "citation"),
            "text": getattr(rule, "text"),
        }
        for rule_id, rule in sorted(runtime_rules.items())
    }
    payload = {
        "algorithm": "collabproof-rule-bundle-v1",
        "manifest": manifest(),
        "provenance": provenance(),
        "runtime_rule_registry": registry,
        "spec_sha256": hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest(),
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _test_nodes() -> set[str]:
    nodes: set[str] = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                nodes.add(f"tests/{path.name}::{node.name}")
    return nodes


def _official_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in OFFICIAL_HOST_SUFFIXES
    )


def validate_governance(runtime_rules: Mapping[str, object], *, check_cache: bool = True) -> list[str]:
    """Return all governance errors; an empty list is a valid bundle."""
    errors: list[str] = []
    try:
        man = manifest()
        prov = provenance()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]

    source_list = man.get("sources")
    if not isinstance(source_list, list):
        return ["manifest sources must be a list"]
    sources: dict[str, dict] = {}
    for i, source in enumerate(source_list):
        label = f"sources[{i}]"
        if not isinstance(source, dict):
            errors.append(f"{label} must be a mapping")
            continue
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"{label} has no id")
            continue
        if source_id in sources:
            errors.append(f"duplicate source id: {source_id}")
        sources[source_id] = source
        for field in ("authority", "official_url", "jurisdiction", "effective_from", "retrieved_on"):
            if not source.get(field):
                errors.append(f"{source_id}: missing {field}")
        if not _official_url(source.get("official_url")):
            errors.append(f"{source_id}: official_url is not an allowlisted official HTTPS URL")
        source_review = source.get("review") or {}
        source_review_status = source_review.get("status")
        if source_review_status not in {"needs_independent_tax_review", "independently_reviewed"}:
            errors.append(f"{source_id}: invalid source review status")
        if source_review_status == "independently_reviewed" and (
            not source_review.get("reviewer") or not source_review.get("reviewed_on")
        ):
            errors.append(f"{source_id}: independent source review requires reviewer and date")

        digest = source.get("sha256") or {}
        state = digest.get("state")
        value = digest.get("value")
        if state == "verified":
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                errors.append(f"{source_id}: verified sha256 must be a lowercase 64-character digest")
        elif state in {"pending", "unavailable"}:
            if value is not None or not digest.get("reason"):
                errors.append(f"{source_id}: {state} sha256 requires null value and a reason")
        else:
            errors.append(f"{source_id}: invalid sha256 state {state!r}")

        redistribution = source.get("redistribution") or {}
        redist_status = redistribution.get("status")
        allowed = redistribution.get("snapshot_allowed") is True
        snapshot = source.get("snapshot")
        if redist_status not in {"permitted", "prohibited", "not_confirmed"}:
            errors.append(f"{source_id}: invalid redistribution status")
        if allowed and redist_status != "permitted":
            errors.append(f"{source_id}: snapshot_allowed requires redistribution permitted")
        if snapshot:
            snapshot_path = ROOT / snapshot
            if not allowed:
                errors.append(f"{source_id}: impermissible checked-in snapshot {snapshot}")
            elif not snapshot_path.is_file():
                errors.append(f"{source_id}: declared snapshot is missing: {snapshot}")
            elif state != "verified":
                errors.append(f"{source_id}: checked-in snapshot requires verified sha256")
            elif hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != value:
                errors.append(f"{source_id}: checked-in snapshot hash drift")

        cache_path = source.get("cache_path")
        if not isinstance(cache_path, str) or not cache_path.startswith("sources/cache/"):
            errors.append(f"{source_id}: cache_path must be under sources/cache/")
        elif check_cache and state == "verified" and (ROOT / cache_path).is_file():
            actual = hashlib.sha256((ROOT / cache_path).read_bytes()).hexdigest()
            if actual != value:
                errors.append(f"{source_id}: local cache hash drift")

    declared_snapshots = {
        str((ROOT / source["snapshot"]).resolve())
        for source in sources.values() if source.get("snapshot")
    }
    tracked = subprocess.run(
        ["git", "ls-files", "--", "sources/cache", "sources/snapshots", "*.pdf"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    if tracked.returncode == 0:
        allowed_cache_metadata = {
            "sources/cache/.gitignore", "sources/cache/README.md",
        }
        for relative in filter(None, tracked.stdout.splitlines()):
            resolved = str((ROOT / relative).resolve())
            if relative in allowed_cache_metadata:
                continue
            if resolved not in declared_snapshots:
                errors.append(f"impermissible checked-in source material: {relative}")
    snapshot_root = ROOT / "sources" / "snapshots"
    if snapshot_root.exists():
        for path in snapshot_root.rglob("*"):
            if path.is_file() and str(path.resolve()) not in declared_snapshots:
                errors.append(f"undeclared checked-in snapshot: {path.relative_to(ROOT)}")

    rule_records = prov.get("rules")
    if not isinstance(rule_records, dict):
        return errors + ["provenance rules must be a mapping"]
    runtime_ids = set(runtime_rules)
    governed_ids = set(rule_records)
    for rule_id in sorted(runtime_ids - governed_ids):
        errors.append(f"orphaned runtime rule ID: {rule_id}")
    for rule_id in sorted(governed_ids - runtime_ids):
        errors.append(f"provenance record has no runtime rule ID: {rule_id}")

    known_tests = _test_nodes()
    for rule_id, record in sorted(rule_records.items()):
        if not isinstance(record, dict):
            errors.append(f"{rule_id}: provenance record must be a mapping")
            continue
        refs = record.get("sources")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{rule_id}: no source references")
            refs = []
        referenced_sources: list[dict] = []
        for ref in refs:
            if not isinstance(ref, dict) or not ref.get("source_id") or not ref.get("location"):
                errors.append(f"{rule_id}: source reference requires source_id and exact location")
                continue
            source_id = ref["source_id"]
            if source_id not in sources:
                errors.append(f"{rule_id}: undeclared source reference {source_id}")
            else:
                referenced_sources.append(sources[source_id])
        tests = record.get("boundary_tests")
        if not isinstance(tests, list) or not tests:
            errors.append(f"{rule_id}: absent declared boundary tests")
        else:
            for test in tests:
                if test not in known_tests:
                    errors.append(f"{rule_id}: declared boundary test does not exist: {test}")
        if not isinstance(record.get("assumptions"), list) or not record["assumptions"]:
            errors.append(f"{rule_id}: assumptions must be a non-empty list")
        if not isinstance(record.get("interpretation"), str) or not record["interpretation"].strip():
            errors.append(f"{rule_id}: interpretation must be non-empty")
        if not isinstance(record.get("formal_theorems"), list):
            errors.append(f"{rule_id}: formal_theorems must be a list (empty is allowed)")
        review = record.get("review") or {}
        review_status = review.get("status")
        if review_status not in {"needs_independent_tax_review", "independently_reviewed"}:
            errors.append(f"{rule_id}: invalid rule review status")
        if review_status == "independently_reviewed" and (
            not review.get("reviewer") or not review.get("reviewed_on")
        ):
            errors.append(f"{rule_id}: independent rule review requires reviewer and date")
        if review.get("priority") not in {"standard", "early"}:
            errors.append(f"{rule_id}: review priority must be standard or early")
        if record.get("status") not in {"experimental", "production"}:
            errors.append(f"{rule_id}: status must be experimental or production")
        if record.get("status") == "production":
            if review.get("status") != "independently_reviewed" or not review.get("reviewer") or not review.get("reviewed_on"):
                errors.append(f"{rule_id}: unreviewed rule marked production")
            for source in referenced_sources:
                source_review = source.get("review") or {}
                if (source_review.get("status") != "independently_reviewed"
                        or not source_review.get("reviewer")
                        or not source_review.get("reviewed_on")):
                    errors.append(f"{rule_id}: production rule references unreviewed source {source['id']}")

    grossup = rule_records.get("IT-194R-GROSSUP", {})
    if (grossup.get("review") or {}).get("priority") != "early":
        errors.append("IT-194R-GROSSUP: provider-borne threshold path must remain an early review target")
    return errors


def governed_rule_ids(assessment: object) -> tuple[str, ...]:
    ids: set[str] = set()
    refusal = getattr(assessment, "refusal_rule_id", None)
    if refusal:
        ids.add(refusal)
    for determination in getattr(assessment, "determinations", {}).values():
        ids.update(determination.rule_ids)
    for branch in getattr(assessment, "cash_tds_fork", ()):
        ids.add(branch.basis_rule_id)
    if getattr(assessment, "fork_material", False):
        ids.add("IT-FORK-JvC")
    return tuple(sorted(ids))


@dataclass(frozen=True)
class CertificateFreshness:
    state: str
    affected_rule_ids: tuple[str, ...]
    reason: str


def certificate_freshness(certificate_hash: str, certificate_rule_ids: Iterable[str],
                          current_hash: str, affected_rule_ids: Iterable[str] = ()) -> CertificateFreshness:
    affected = tuple(sorted(set(certificate_rule_ids) & set(affected_rule_ids)))
    if certificate_hash == current_hash:
        return CertificateFreshness("CURRENT", (), "Certificate uses the current rule bundle.")
    if affected:
        return CertificateFreshness("STALE", affected, "A changed source affects rules used by the certificate.")
    return CertificateFreshness(
        "REVERIFY_ELIGIBLE", (),
        "The bundle changed outside the certificate's known rule trail; re-run verification before relying on it.",
    )


def source_impact(previous_manifest: dict, current_manifest: dict, prov: dict | None = None) -> dict:
    """Compare source-governance fields and return affected source/rule IDs."""
    prov = prov or provenance()
    previous = {s["id"]: s for s in previous_manifest.get("sources", [])}
    current = {s["id"]: s for s in current_manifest.get("sources", [])}
    changed: set[str] = set(previous) ^ set(current)
    material_fields = ("official_url", "effective_from", "effective_to", "sha256")
    for source_id in set(previous) & set(current):
        if any(previous[source_id].get(f) != current[source_id].get(f) for f in material_fields):
            changed.add(source_id)
    affected = {
        rule_id for rule_id, record in prov.get("rules", {}).items()
        if any(ref.get("source_id") in changed for ref in record.get("sources", []))
    }
    return {"changed_source_ids": sorted(changed), "affected_rule_ids": sorted(affected)}


def fetch_sources(selected: set[str] | None = None) -> None:
    for source in manifest()["sources"]:
        if selected and source["id"] not in selected:
            continue
        destination = ROOT / source["cache_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(source["official_url"], headers={"User-Agent": "collabproof-source-governance/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            final_url = response.geturl()
            if not _official_url(final_url):
                raise RuntimeError(f"{source['id']}: redirect left official domains: {final_url}")
            payload = response.read()
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
        print(f"{source['id']} {hashlib.sha256(payload).hexdigest()} {destination.relative_to(ROOT)}")


def sync_js_hash(runtime_rules: Mapping[str, object]) -> str:
    digest = rule_bundle_hash(runtime_rules)
    text = JS_PATH.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'const RULE_BUNDLE_HASH = "[0-9a-f]{64}";',
        f'const RULE_BUNDLE_HASH = "{digest}";', text,
    )
    if count != 1:
        raise RuntimeError("docs/collabproof.js must contain exactly one RULE_BUNDLE_HASH constant")
    JS_PATH.write_text(updated, encoding="utf-8")
    return digest


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("hash")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("source_ids", nargs="*")
    sub.add_parser("sync-js-hash")
    impact = sub.add_parser("impact")
    impact.add_argument("previous_manifest", type=Path)
    impact.add_argument("--certificates", type=Path)
    args = parser.parse_args()

    from .spec import RULES

    if args.command == "validate":
        errors = validate_governance(RULES)
        if errors:
            print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
            return 1
        print(f"source governance valid; bundle {rule_bundle_hash(RULES)}")
        return 0
    if args.command == "hash":
        print(rule_bundle_hash(RULES))
        return 0
    if args.command == "fetch":
        fetch_sources(set(args.source_ids) or None)
        return 0
    if args.command == "sync-js-hash":
        print(sync_js_hash(RULES))
        return 0
    report = source_impact(load_yaml(args.previous_manifest), manifest())
    report["current_rule_bundle_hash"] = rule_bundle_hash(RULES)
    if args.certificates:
        records = json.loads(args.certificates.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            records = [records]
        for record in records:
            fresh = certificate_freshness(
                record.get("rule_bundle_hash", ""), record.get("governed_rule_ids", []),
                report["current_rule_bundle_hash"], report["affected_rule_ids"],
            )
            record["freshness"] = fresh.state
            record["affected_rule_ids"] = list(fresh.affected_rule_ids)
        report["certificates"] = records
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
