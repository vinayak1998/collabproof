"""Runtime Lean certificates for the narrow section 194R decision slice.

The certificate is emitted only after a fresh ``lean`` process has accepted a
generated per-case theorem.  The Lean process is an independent kernel check
of the artifact, not an independent legal formalization or an independent
oracle for the normalized input facts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .spec import Collab, EntityType, Q, TaxBearer, assess


SPEC_VERSION = (
    "income-tax-1961-through-finance-no-2-act-2024+"
    "s194r-circulars-12-and-18-2022"
)
LEAN_MODULE = "LeanProof/S194R.lean"
SPEC_SOURCE_FILES = (
    LEAN_MODULE,
    "lean-toolchain",
    "lakefile.toml",
    "collabproof/spec.py",
    "collabproof/runtime_proof.py",
)
ALLOWED_CASH_INTERPRETATIONS = ("IT-194J-PROF", "IT-194C-WORK")


class LeanCertificationError(RuntimeError):
    """No certificate was emitted because the trusted check failed closed."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized_facts(c: Collab) -> dict[str, Any]:
    """Return every input fact, with exact types and stable field names."""

    bool_fields = (
        ("brand.in_business", c.brand.in_business),
        ("creator.is_resident", c.creator.is_resident),
        ("creator.pan_furnished", c.creator.pan_furnished),
        ("creator.special_category_state", c.creator.special_category_state),
        ("creator.gst_registered", c.creator.gst_registered),
        ("transaction.product_retained", c.product_retained),
        ("transaction.deliverable_linked", c.deliverable_linked),
    )
    money_fields = (
        ("brand.preceding_fy_business_turnover_paise", c.brand.preceding_fy_business_turnover_paise),
        ("brand.preceding_fy_profession_receipts_paise", c.brand.preceding_fy_profession_receipts_paise),
        ("creator.fy_prior_benefits_from_brand_paise", c.creator.fy_prior_benefits_from_brand_paise),
        ("creator.fy_prior_194r_tds_paise", c.creator.fy_prior_194r_tds_paise),
        ("creator.fy_prior_cash_fees_from_brand_paise", c.creator.fy_prior_cash_fees_from_brand_paise),
        ("creator.fy_prior_cash_tds_paise", c.creator.fy_prior_cash_tds_paise),
        ("creator.fy_prior_aggregate_turnover_paise", c.creator.fy_prior_aggregate_turnover_paise),
        ("transaction.cash_fee_paise", c.cash_fee_paise),
        ("transaction.product_fmv_paise", c.product_fmv_paise),
    )
    for path, value in bool_fields:
        if type(value) is not bool:
            raise ValueError(f"{path} must be a boolean")
    for path, value in money_fields:
        if type(value) is not int or value < 0:
            raise ValueError(f"{path} must be a non-negative integer number of paise")
    if not isinstance(c.brand.entity_type, EntityType):
        raise ValueError("brand.entity_type must be an EntityType")
    if not isinstance(c.tax_borne_by, TaxBearer):
        raise ValueError("transaction.tax_borne_by must be a TaxBearer")

    facts = {
        "brand": {
            "entity_type": c.brand.entity_type.value,
            "in_business": c.brand.in_business,
            "preceding_fy_business_turnover_paise": (
                c.brand.preceding_fy_business_turnover_paise
            ),
            "preceding_fy_profession_receipts_paise": (
                c.brand.preceding_fy_profession_receipts_paise
            ),
        },
        "creator": {
            "is_resident": c.creator.is_resident,
            "pan_furnished": c.creator.pan_furnished,
            "special_category_state": c.creator.special_category_state,
            "gst_registered": c.creator.gst_registered,
            "fy_prior_benefits_from_brand_paise": (
                c.creator.fy_prior_benefits_from_brand_paise
            ),
            "fy_prior_194r_tds_paise": c.creator.fy_prior_194r_tds_paise,
            "fy_prior_cash_fees_from_brand_paise": (
                c.creator.fy_prior_cash_fees_from_brand_paise
            ),
            "fy_prior_cash_tds_paise": c.creator.fy_prior_cash_tds_paise,
            "fy_prior_aggregate_turnover_paise": (
                c.creator.fy_prior_aggregate_turnover_paise
            ),
        },
        "transaction": {
            "cash_fee_paise": c.cash_fee_paise,
            "product_fmv_paise": c.product_fmv_paise,
            "product_retained": c.product_retained,
            "deliverable_linked": c.deliverable_linked,
            "tax_borne_by": c.tax_borne_by.value,
        },
    }
    _validate_normalized_facts(facts)
    return facts


def _validate_normalized_facts(facts: dict[str, Any]) -> None:
    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}")
        elif type(value) is bool or type(value) is str:
            return
        elif type(value) is int and value >= 0:
            return
        else:
            raise ValueError(f"invalid normalized fact at {path}: {value!r}")

    walk(facts, "facts")


def _run(command: list[str], cwd: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command, cwd=cwd, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        return {
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "result": "FAIL",
        }
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "result": "PASS" if completed.returncode == 0 else "FAIL",
    }


def _specification_identity(root: Path) -> dict[str, Any]:
    source_hashes = {
        name: _sha256((root / name).read_bytes()) for name in SPEC_SOURCE_FILES
    }
    bundle_hash = _sha256(_canonical_json(source_hashes))
    commit = _run(["git", "rev-parse", "HEAD"], root)
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    clean = commit["result"] == "PASS" and status["result"] == "PASS" and not status["stdout"]
    commit_id = commit["stdout"].strip() if commit["result"] == "PASS" else None
    return {
        "version": SPEC_VERSION,
        "identity": f"git:{commit_id}" if clean else f"dirty-sha256:{bundle_hash}",
        "git_commit": commit_id,
        "worktree_clean": clean,
        "deterministic_source_bundle_sha256": bundle_hash,
        "source_sha256": source_hashes,
    }


def _lean_bool(value: bool) -> str:
    return "true" if value else "false"


def _lean_entity(value: EntityType) -> str:
    return f".{value.value}"


def _lean_bearer(value: TaxBearer) -> str:
    return f".{value.value}"


def _lean_scope(refusal_rule_id: Optional[str]) -> str:
    if refusal_rule_id == "SCOPE-RESIDENT":
        return ".unsupportedNonResident"
    if refusal_rule_id == "SCOPE-BUSINESS-NEXUS":
        return ".unsupportedNoBusinessNexus"
    return ".covered"


def theorem_source(c: Collab, fact_hash: str) -> tuple[str, dict[str, Any]]:
    a = assess(c)
    if a.ok:
        expected = {
            "scope": "covered",
            "benefit_qualifies": a.d(Q.BENEFIT_QUALIFIES),
            "provider_obligated": a.d(Q.PROVIDER_OBLIGATED),
            "aggregate_benefit_paise": a.d(Q.AGGREGATE_BENEFIT),
            "tds_due_now_paise": a.d(Q.TDS_194R),
            "release_gate_required": a.d(Q.RELEASE_GATE),
        }
    else:
        expected = {
            "scope": (
                "unsupported_non_resident"
                if a.refusal_rule_id == "SCOPE-RESIDENT"
                else "unsupported_no_business_nexus"
            ),
            "benefit_qualifies": False,
            "provider_obligated": False,
            "aggregate_benefit_paise": 0,
            "tds_due_now_paise": 0,
            "release_gate_required": False,
        }

    theorem_name = f"case_{fact_hash[:16]}"
    source = f"""import LeanProof.S194R

open CollabProof.S194R

-- Complete normalized-fact SHA-256: {fact_hash}
def caseFacts : Facts :=
  {{ isResident := {_lean_bool(c.creator.is_resident)}
    brandInBusiness := {_lean_bool(c.brand.in_business)}
    brandEntity := {_lean_entity(c.brand.entity_type)}
    precedingBusinessTurnoverPaise := {c.brand.preceding_fy_business_turnover_paise}
    precedingProfessionReceiptsPaise := {c.brand.preceding_fy_profession_receipts_paise}
    panFurnished := {_lean_bool(c.creator.pan_furnished)}
    priorBenefitsPaise := {c.creator.fy_prior_benefits_from_brand_paise}
    priorTdsPaise := {c.creator.fy_prior_194r_tds_paise}
    productFmvPaise := {c.product_fmv_paise}
    productRetained := {_lean_bool(c.product_retained)}
    taxBearer := {_lean_bearer(c.tax_borne_by)} }}

def expectedDecision : Decision :=
  {{ scope := {_lean_scope(a.refusal_rule_id)}
    benefitQualifies := {_lean_bool(expected['benefit_qualifies'])}
    providerObligated := {_lean_bool(expected['provider_obligated'])}
    aggregateBenefitPaise := {expected['aggregate_benefit_paise']}
    tdsDueNowPaise := {expected['tds_due_now_paise']}
    releaseGateRequired := {_lean_bool(expected['release_gate_required'])} }}

theorem {theorem_name} :
    decide currentSpec caseFacts = expectedDecision := by
  decide
"""
    theorem = {
        "name": theorem_name,
        "statement": "decide currentSpec caseFacts = expectedDecision",
        "specification_version": SPEC_VERSION,
        "proof_term": "by decide",
    }
    return source, {"expected": expected, "theorem": theorem}


def _conditional_cash_output(c: Collab, interpretation: str) -> dict[str, Any]:
    a = assess(c)
    if not a.ok:
        return {
            "status": "UNSUPPORTED",
            "reason": "The Python specification refused the fact pattern.",
        }
    branches = {branch.basis_rule_id: branch.tds_paise for branch in a.cash_tds_fork}
    return {
        "status": "CONDITIONAL_UNVERIFIED_BY_LEAN",
        "selected_interpretation": interpretation,
        "cash_tds_paise": branches[interpretation],
        "condition": f"Only if {interpretation} is selected; the overlap is not proved.",
    }


def certify_194r(
    c: Collab,
    output_dir: str | Path,
    *,
    cash_interpretation: Optional[str] = None,
) -> dict[str, Any]:
    """Emit a theorem artifact and certificate, or raise without certifying."""

    if cash_interpretation not in (None, *ALLOWED_CASH_INTERPRETATIONS):
        raise ValueError(
            "cash_interpretation must be explicitly selected as "
            "IT-194J-PROF or IT-194C-WORK"
        )

    root = _repo_root()
    if shutil.which("lake") is None:
        raise LeanCertificationError("Lean certification requested but lake is unavailable")

    facts = normalized_facts(c)
    fact_hash = _sha256(_canonical_json(facts))
    source, proof = theorem_source(c, fact_hash)
    artifact_hash = _sha256(source.encode("utf-8"))
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    artifact_path = destination / f"s194r-{fact_hash[:16]}.lean"
    certificate_path = destination / f"s194r-{fact_hash[:16]}.certificate.json"
    artifact_path.write_text(source, encoding="utf-8")

    build_check = _run(["lake", "build", "LeanProof.S194R"], root)
    kernel_check = _run(["lake", "env", "lean", str(artifact_path)], root)
    if build_check["result"] != "PASS" or kernel_check["result"] != "PASS":
        artifact_path.unlink(missing_ok=True)
        raise LeanCertificationError(
            "Lean certification failed closed: "
            f"build={build_check['result']}, kernel={kernel_check['result']}; "
            f"kernel stderr={kernel_check['stderr'].strip()!r}"
        )

    assumptions = [
        "The normalized runtime facts are accurate, complete, and refer to one recipient/provider FY aggregation.",
        "The selected specification version is the intended legal interpretation for the case.",
        "Money is exact integer paise; statutory rounding under s.288B is outside this slice.",
        "Prior-benefit and prior-deduction aggregates supplied in the facts are correct.",
    ]
    if cash_interpretation is not None:
        assumptions.append(
            f"Cash TDS is shown only conditionally under the explicitly selected {cash_interpretation} interpretation."
        )

    certificate = {
        "schema_version": "collabproof-runtime-certificate-v1",
        "certification_status": "LEAN_KERNEL_CHECKED",
        "trusted_scope": "section_194r_only",
        "normalized_facts": facts,
        "normalized_fact_sha256": fact_hash,
        "assumptions": assumptions,
        "covered_outputs": proof["expected"],
        "covered_rule_ids": [
            "IT-194R-SCOPE",
            "IT-194R-RETAINED",
            "IT-194R-CARVEOUT",
            "IT-194R-THRESHOLD",
            "IT-194R-GROSSUP",
            "IT-194R-RELEASEGATE",
            "IT-206AA",
        ],
        "specification": _specification_identity(root),
        "proof_artifact": {
            **proof["theorem"],
            "path": str(artifact_path),
            "sha256": artifact_hash,
        },
        "lean_checks": {
            "module_build": build_check,
            "per_case_kernel_check": kernel_check,
            "independence_boundary": (
                "The per-case artifact was checked in a fresh Lean process. "
                "This is independent kernel checking of the proof artifact, "
                "not an independent legal model, fact oracle, or second formalization."
            ),
        },
        "other_outputs": {
            "cash_tds_194j_194c": (
                _conditional_cash_output(c, cash_interpretation)
                if cash_interpretation is not None
                else {
                    "status": "UNSUPPORTED_UNVERIFIED",
                    "reason": (
                        "No interpretation selected; no 194J/194C value is exposed "
                        "or implied by this certificate."
                    ),
                }
            ),
            "gst": {
                "status": "UNSUPPORTED_UNVERIFIED",
                "reason": "GST is outside the initial Lean trusted slice.",
            },
        },
    }
    certificate_path.write_bytes(_canonical_json(certificate) + b"\n")
    certificate["certificate_path"] = str(certificate_path)
    return certificate


def _collab_from_json(raw: dict[str, Any]) -> Collab:
    from .spec import Brand, Creator

    brand = raw["brand"]
    creator = raw["creator"]
    transaction = raw["transaction"]
    return Collab(
        brand=Brand(
            entity_type=EntityType(brand["entity_type"]),
            in_business=brand["in_business"],
            preceding_fy_business_turnover_paise=brand[
                "preceding_fy_business_turnover_paise"
            ],
            preceding_fy_profession_receipts_paise=brand[
                "preceding_fy_profession_receipts_paise"
            ],
        ),
        creator=Creator(**creator),
        cash_fee_paise=transaction["cash_fee_paise"],
        product_fmv_paise=transaction["product_fmv_paise"],
        product_retained=transaction["product_retained"],
        deliverable_linked=transaction["deliverable_linked"],
        tax_borne_by=TaxBearer(transaction["tax_borne_by"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("facts_json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cash-interpretation", choices=ALLOWED_CASH_INTERPRETATIONS)
    args = parser.parse_args()
    raw = json.loads(args.facts_json.read_text(encoding="utf-8"))
    try:
        certificate = certify_194r(
            _collab_from_json(raw),
            args.output_dir,
            cash_interpretation=args.cash_interpretation,
        )
    except (LeanCertificationError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"certification failed closed: {exc}\n")
    print(certificate["certificate_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
