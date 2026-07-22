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

from .s194r import MAX_MONEY_PAISE, S194RFacts
from .spec import Collab, EntityType, Q, RULES, TaxBearer, assess


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
    "collabproof/s194r.py",
    "collabproof/intake.py",
    "collabproof/runtime_proof.py",
    "collabproof/render.py",
    "collabproof/pipeline.py",
)
ALLOWED_CASH_INTERPRETATIONS = ("IT-194J-PROF", "IT-194C-WORK")
COMMAND_TIMEOUT_SECONDS = 120
MAX_CONFIRMED_CASE_BYTES = 1_000_000
BASE_ASSUMPTIONS = (
    "The normalized runtime facts are accurate, complete, and refer to one recipient/provider FY aggregation.",
    "The selected specification version is the intended legal interpretation for the case.",
    "Money is exact integer paise; statutory rounding under s.288B is outside this slice.",
    "Prior-benefit and prior-deduction aggregates supplied in the facts are correct.",
)


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
        if type(value) is not int or value < 0 or value > MAX_MONEY_PAISE:
            raise ValueError(
                f"{path} must be an integer from 0 through "
                f"{MAX_MONEY_PAISE} paise"
            )
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


def normalized_194r_facts(c: Collab | S194RFacts) -> dict[str, Any]:
    """Return exactly the eleven facts consumed by the checked Lean model."""
    facts = c if isinstance(c, S194RFacts) else S194RFacts.from_collab(c)
    encoded = facts.as_dict()
    _validate_normalized_facts(encoded)
    return encoded


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
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {
            "command": command,
            "exit_code": None,
            "stdout": stdout,
            "stderr": (
                f"command exceeded the {COMMAND_TIMEOUT_SECONDS}-second limit"
            ),
            "result": "FAIL",
        }
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


def specification_bundle_sha256(root: Optional[Path] = None) -> str:
    """Hash every source that defines the confirmed Section 194R proof path."""

    root = root or _repo_root()
    source_hashes = {
        name: _sha256((root / name).read_bytes()) for name in SPEC_SOURCE_FILES
    }
    return _sha256(_canonical_json(source_hashes))


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


def theorem_source(
    case: Collab | S194RFacts, fact_hash: str
) -> tuple[str, dict[str, Any]]:
    facts = case if isinstance(case, S194RFacts) else S194RFacts.from_collab(case)
    c = facts.to_collab()
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


FORMAL_MODEL_RULE_IDS = (
    "IT-194R-SCOPE",
    "IT-194R-RETAINED",
    "IT-194R-CARVEOUT",
    "IT-194R-THRESHOLD",
    "IT-194R-GROSSUP",
    "IT-194R-RELEASEGATE",
    "IT-206AA",
    "SCOPE-RESIDENT",
    "SCOPE-BUSINESS-NEXUS",
)


def _applied_rule_ids(c: Collab) -> tuple[str, ...]:
    assessment = assess(c)
    if not assessment.ok:
        return (assessment.refusal_rule_id,) if assessment.refusal_rule_id else ()
    keys = (
        Q.BENEFIT_QUALIFIES,
        Q.PROVIDER_OBLIGATED,
        Q.AGGREGATE_BENEFIT,
        Q.TDS_194R,
        Q.RELEASE_GATE,
    )
    return tuple(sorted({
        rule_id
        for key in keys
        for rule_id in assessment.determinations[key].rule_ids
    }))


def _output_rule_ids(c: Collab) -> dict[str, list[str]]:
    assessment = assess(c)
    if not assessment.ok:
        refusal = [assessment.refusal_rule_id] if assessment.refusal_rule_id else []
        return {"scope": refusal}
    return {
        "scope": ["IT-194R-SCOPE"],
        "benefit_qualifies": list(
            assessment.determinations[Q.BENEFIT_QUALIFIES].rule_ids
        ),
        "provider_obligated": list(
            assessment.determinations[Q.PROVIDER_OBLIGATED].rule_ids
        ),
        "aggregate_benefit_paise": list(
            assessment.determinations[Q.AGGREGATE_BENEFIT].rule_ids
        ),
        "tds_due_now_paise": list(
            assessment.determinations[Q.TDS_194R].rule_ids
        ),
        "release_gate_required": list(
            assessment.determinations[Q.RELEASE_GATE].rule_ids
        ),
    }


def _governance_identity(applied_rule_ids: tuple[str, ...]) -> dict[str, Any]:
    from .governance import rule_bundle_hash, validate_governance

    errors = validate_governance(RULES)
    if errors:
        raise ValueError("governance validation failed: " + "; ".join(errors))

    return {
        "rule_bundle_hash": rule_bundle_hash(RULES),
        "applied_rule_ids": list(applied_rule_ids),
        "formal_model_rule_ids": list(FORMAL_MODEL_RULE_IDS),
    }


def _load_confirmed_case_artifact(
    path: str | Path,
    *,
    destination: Path,
) -> tuple[S194RFacts, dict[str, Any]]:
    """Fresh-parse a colocated confirmed-case artifact and derive its record."""

    from .intake import ConfirmedCase

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        confirmed_path = Path(path).resolve(strict=True)
        if not confirmed_path.is_file():
            raise ValueError("confirmed-case path is not a regular file")
        if confirmed_path.parent != destination:
            raise ValueError(
                "confirmed-case artifact must be colocated with proof outputs"
            )
        if confirmed_path.stat().st_size > MAX_CONFIRMED_CASE_BYTES:
            raise ValueError("confirmed-case artifact exceeds the size limit")
        data = confirmed_path.read_bytes()
        raw = json.loads(
            data.decode("utf-8"), object_pairs_hook=unique_object
        )
        if type(raw) is not dict:
            raise ValueError("confirmed-case artifact must contain an object")
        confirmed = ConfirmedCase.from_dict(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load confirmed-case artifact: {exc}") from exc

    intake_record = {
        **confirmed.certificate_record(),
        "confirmed_case_artifact": {
            "path": str(confirmed_path),
            "sha256": _sha256(data),
        },
    }
    return confirmed.facts, intake_record


def _validate_intake_record(
    record: Optional[dict[str, Any]],
    *,
    normalized_fact_sha256: str,
    specification_bundle_sha256: str,
    rule_bundle_sha256: str,
) -> Optional[dict[str, Any]]:
    if record is None:
        return None
    expected = {
        "status",
        "case_id",
        "intent",
        "requested_period",
        "draft_sha256",
        "confirmed_case_artifact",
        "confirmation_payload",
        "confirmation_sha256",
        "source_bundle_sha256",
        "provenance_sha256",
        "normalized_fact_sha256",
        "specification_version",
        "specification_bundle_sha256",
        "rule_bundle_hash",
    }
    if type(record) is not dict or set(record) != expected:
        raise ValueError("intake record has an unexpected schema")
    if record["status"] != "CONFIRMED":
        raise ValueError("intake record is not confirmed")
    if record["intent"] != "section_194r_decision":
        raise ValueError("intake intent is outside the Section 194R proof slice")
    if record["requested_period"] != "FY 2024-25":
        raise ValueError("intake period does not match the pinned specification")
    if record["specification_version"] != SPEC_VERSION:
        raise ValueError("intake specification version is stale")
    if record["specification_bundle_sha256"] != specification_bundle_sha256:
        raise ValueError("intake specification source bundle is stale")
    if record["normalized_fact_sha256"] != normalized_fact_sha256:
        raise ValueError("intake facts do not match the Lean case")
    if record["rule_bundle_hash"] != rule_bundle_sha256:
        raise ValueError("intake governance bundle is stale")
    artifact = record["confirmed_case_artifact"]
    if (
        type(artifact) is not dict
        or set(artifact) != {"path", "sha256"}
        or type(artifact["path"]) is not str
        or not Path(artifact["path"]).is_absolute()
    ):
        raise ValueError("confirmed-case artifact identity is invalid")
    artifact_hash = artifact["sha256"]
    if (
        type(artifact_hash) is not str
        or len(artifact_hash) != 64
        or any(character not in "0123456789abcdef" for character in artifact_hash)
    ):
        raise ValueError("confirmed-case artifact SHA-256 is invalid")
    case_id = record["case_id"]
    if (
        type(case_id) is not str
        or not case_id
        or len(case_id) > 128
        or any(not (character.isascii() and (character.isalnum() or character in "_-"))
               for character in case_id)
    ):
        raise ValueError("intake case_id is invalid")
    for name in (
        "draft_sha256",
        "confirmation_sha256",
        "source_bundle_sha256",
        "provenance_sha256",
    ):
        value = record[name]
        if (
            type(value) is not str
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"intake {name} must be a lowercase SHA-256")

    expected_payload = {
        "schema_version": "collabproof-s194r-confirmation-v1",
        "decision": "CONFIRMED",
        "case_id": case_id,
        "intent": record["intent"],
        "requested_period": record["requested_period"],
        "draft_sha256": record["draft_sha256"],
        "normalized_fact_sha256": record["normalized_fact_sha256"],
        "source_bundle_sha256": record["source_bundle_sha256"],
        "provenance_sha256": record["provenance_sha256"],
        "specification_version": record["specification_version"],
        "specification_bundle_sha256": record[
            "specification_bundle_sha256"
        ],
        "rule_bundle_hash": record["rule_bundle_hash"],
    }
    if record["confirmation_payload"] != expected_payload:
        raise ValueError("intake confirmation payload is invalid")
    if _sha256(_canonical_json(expected_payload)) != record["confirmation_sha256"]:
        raise ValueError("intake confirmation SHA-256 is invalid")
    return json.loads(_canonical_json(record))


def _certify_194r(
    c: Collab,
    output_dir: str | Path,
    *,
    cash_interpretation: Optional[str] = None,
    intake_record: Optional[dict[str, Any]] = None,
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

    complete_input_facts = normalized_facts(c)
    lean_case = S194RFacts.from_collab(c)
    facts = normalized_194r_facts(lean_case)
    fact_hash = _sha256(_canonical_json(facts))
    source, proof = theorem_source(lean_case, fact_hash)
    artifact_hash = _sha256(source.encode("utf-8"))
    applied_rule_ids = _applied_rule_ids(c)
    governance = _governance_identity(applied_rule_ids)
    specification_before = _specification_identity(root)
    confirmed_intake = _validate_intake_record(
        intake_record,
        normalized_fact_sha256=fact_hash,
        specification_bundle_sha256=specification_before[
            "deterministic_source_bundle_sha256"
        ],
        rule_bundle_sha256=governance["rule_bundle_hash"],
    )
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    artifact_path = destination / f"s194r-{fact_hash}.lean"
    certificate_path = destination / f"s194r-{fact_hash}.certificate.json"
    try:
        with artifact_path.open("x", encoding="utf-8") as handle:
            handle.write(source)
    except OSError as exc:
        raise LeanCertificationError(
            f"refusing to overwrite proof artifact: {artifact_path}: {exc}"
        ) from exc

    build_check = _run(["lake", "build", "LeanProof.S194R"], root)
    kernel_check = _run(["lake", "env", "lean", str(artifact_path)], root)
    if build_check["result"] != "PASS" or kernel_check["result"] != "PASS":
        artifact_path.unlink(missing_ok=True)
        raise LeanCertificationError(
            "Lean certification failed closed: "
            f"build={build_check['result']}, kernel={kernel_check['result']}; "
            f"kernel stderr={kernel_check['stderr'].strip()!r}"
        )

    specification_after = _specification_identity(root)
    governance_after = _governance_identity(applied_rule_ids)
    if (
        specification_before["deterministic_source_bundle_sha256"]
        != specification_after["deterministic_source_bundle_sha256"]
        or governance["rule_bundle_hash"] != governance_after["rule_bundle_hash"]
    ):
        artifact_path.unlink(missing_ok=True)
        raise LeanCertificationError(
            "specification or governance changed during Lean certification"
        )

    assumptions = list(BASE_ASSUMPTIONS)
    if cash_interpretation is not None:
        assumptions.append(
            f"Cash TDS is shown only conditionally under the explicitly selected {cash_interpretation} interpretation."
        )

    certificate = {
        "schema_version": "collabproof-runtime-certificate-v2",
        "certification_status": "LEAN_KERNEL_CHECKED",
        "decision_status": (
            "ANSWERED" if proof["expected"]["scope"] == "covered" else "REFUSED"
        ),
        "trusted_scope": "section_194r_only",
        "normalized_facts": facts,
        "normalized_fact_sha256": fact_hash,
        "complete_collab_input_facts": complete_input_facts,
        "intake": confirmed_intake,
        "assumptions": assumptions,
        "covered_outputs": proof["expected"],
        "output_rule_ids": _output_rule_ids(c),
        "covered_rule_ids": list(FORMAL_MODEL_RULE_IDS),
        "applied_rule_ids": list(applied_rule_ids),
        "governance": governance,
        "specification": {
            **specification_after,
            "rule_bundle_hash": governance["rule_bundle_hash"],
        },
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
    try:
        with certificate_path.open("xb") as handle:
            handle.write(_canonical_json(certificate) + b"\n")
    except OSError as exc:
        artifact_path.unlink(missing_ok=True)
        raise LeanCertificationError(
            f"refusing to overwrite proof certificate: {certificate_path}: {exc}"
        ) from exc
    certificate["certificate_path"] = str(certificate_path)
    return certificate


def certify_unconfirmed_194r(
    c: Collab,
    output_dir: str | Path,
    *,
    cash_interpretation: Optional[str] = None,
) -> dict[str, Any]:
    """Low-level compatibility proof with no claim of confirmed provenance."""

    if not isinstance(c, Collab):
        raise TypeError("c must be a Collab value")
    return _certify_194r(
        c,
        output_dir,
        cash_interpretation=cash_interpretation,
        intake_record=None,
    )


def certify_194r_facts(
    facts: S194RFacts,
    output_dir: str | Path,
    *,
    confirmed_case_path: str | Path,
) -> dict[str, Any]:
    """Certify exact facts only when a full confirmed artifact derives them."""

    if not isinstance(facts, S194RFacts):
        raise TypeError("facts must be an S194RFacts value")
    destination = Path(output_dir).resolve()
    confirmed_facts, intake_record = _load_confirmed_case_artifact(
        confirmed_case_path, destination=destination
    )
    if confirmed_facts != facts:
        raise ValueError("confirmed-case facts do not match the requested Lean facts")
    return _certify_194r(
        facts.to_collab(),
        destination,
        intake_record=intake_record,
    )


def certify_194r(
    confirmed_case_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Load a full confirmed case, validate its source, and kernel-check it."""

    destination = Path(output_dir).resolve()
    facts, intake_record = _load_confirmed_case_artifact(
        confirmed_case_path, destination=destination
    )
    return _certify_194r(
        facts.to_collab(), destination, intake_record=intake_record
    )


def _collab_from_json(raw: dict[str, Any]) -> Collab:
    from .spec import Brand, Creator

    if type(raw) is not dict or set(raw) != {"brand", "creator", "transaction"}:
        raise ValueError("unconfirmed Collab input requires exact top-level fields")
    brand = raw["brand"]
    creator = raw["creator"]
    transaction = raw["transaction"]
    if type(brand) is not dict or set(brand) != {
        "entity_type",
        "in_business",
        "preceding_fy_business_turnover_paise",
        "preceding_fy_profession_receipts_paise",
    }:
        raise ValueError("unconfirmed Collab input has incomplete brand fields")
    if type(creator) is not dict or set(creator) != {
        "is_resident",
        "pan_furnished",
        "special_category_state",
        "gst_registered",
        "fy_prior_benefits_from_brand_paise",
        "fy_prior_194r_tds_paise",
        "fy_prior_cash_fees_from_brand_paise",
        "fy_prior_cash_tds_paise",
        "fy_prior_aggregate_turnover_paise",
    }:
        raise ValueError("unconfirmed Collab input has incomplete creator fields")
    if type(transaction) is not dict or set(transaction) != {
        "cash_fee_paise",
        "product_fmv_paise",
        "product_retained",
        "deliverable_linked",
        "tax_borne_by",
    }:
        raise ValueError("unconfirmed Collab input has incomplete transaction fields")
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
    parser.add_argument(
        "--allow-unconfirmed-structured-facts",
        action="store_true",
        required=True,
        help="acknowledge that this low-level CLI has no confirmed intake provenance",
    )
    args = parser.parse_args()

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        if args.facts_json.stat().st_size > MAX_CONFIRMED_CASE_BYTES:
            raise ValueError("facts JSON exceeds the size limit")
        raw = json.loads(
            args.facts_json.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
        )
        certificate = certify_unconfirmed_194r(
            _collab_from_json(raw),
            args.output_dir,
            cash_interpretation=args.cash_interpretation,
        )
    except (
        LeanCertificationError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        parser.exit(1, f"certification failed closed: {exc}\n")
    print(certificate["certificate_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
