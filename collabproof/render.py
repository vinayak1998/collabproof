"""Certificate-only natural-language rendering for the trusted s.194R slice.

The renderer accepts a persisted runtime-certificate file, never a natural-
language query or an in-memory model answer.  It validates the complete trust
envelope before selecting a static template.  Rendering is therefore a
projection of already checked certificate fields, not another reasoning step.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .governance import rule_bundle_hash, validate_governance
from .intake import ConfirmedCase
from .runtime_proof import (
    BASE_ASSUMPTIONS,
    COMMAND_TIMEOUT_SECONDS,
    FORMAL_MODEL_RULE_IDS,
    MAX_CONFIRMED_CASE_BYTES,
    SPEC_SOURCE_FILES,
    SPEC_VERSION,
    _output_rule_ids,
    _specification_identity,
    theorem_source,
)
from .s194r import MAX_MONEY_PAISE, S194RFacts
from .spec import RULES


SCHEMA_VERSION = "collabproof-runtime-certificate-v2"
MAX_CERTIFICATE_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 1_000_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TOP_LEVEL_FIELDS = frozenset({
    "schema_version",
    "certification_status",
    "decision_status",
    "trusted_scope",
    "normalized_facts",
    "normalized_fact_sha256",
    "complete_collab_input_facts",
    "intake",
    "assumptions",
    "covered_outputs",
    "output_rule_ids",
    "covered_rule_ids",
    "applied_rule_ids",
    "governance",
    "specification",
    "proof_artifact",
    "lean_checks",
    "other_outputs",
})

OUTPUT_FIELDS = (
    "scope",
    "benefit_qualifies",
    "provider_obligated",
    "aggregate_benefit_paise",
    "tds_due_now_paise",
    "release_gate_required",
)

INTAKE_FIELDS = frozenset({
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
})

SPECIFICATION_FIELDS = frozenset({
    "version",
    "identity",
    "git_commit",
    "worktree_clean",
    "deterministic_source_bundle_sha256",
    "source_sha256",
    "rule_bundle_hash",
})

PROOF_FIELDS = frozenset({
    "name",
    "statement",
    "specification_version",
    "proof_term",
    "path",
    "sha256",
})

CHECK_FIELDS = frozenset({
    "command",
    "exit_code",
    "stdout",
    "stderr",
    "result",
})

INDEPENDENCE_BOUNDARY = (
    "The per-case artifact was checked in a fresh Lean process. "
    "This is independent kernel checking of the proof artifact, "
    "not an independent legal model, fact oracle, or second formalization."
)

CONFIRMATION_PAYLOAD_FIELDS = frozenset({
    "schema_version",
    "decision",
    "case_id",
    "intent",
    "requested_period",
    "draft_sha256",
    "normalized_fact_sha256",
    "source_bundle_sha256",
    "provenance_sha256",
    "specification_version",
    "specification_bundle_sha256",
    "rule_bundle_hash",
})


class RenderValidationError(ValueError):
    """The persisted certificate did not satisfy the rendering trust gate."""


@dataclass(frozen=True)
class RenderedClaim:
    """One rendered statement and its exact certificate support."""

    pointer: str
    value: object
    rule_ids: tuple[str, ...]
    template_id: str


@dataclass(frozen=True)
class RenderedAnswer:
    """Static prose plus machine-readable support for every rendered claim."""

    text: str
    certificate_sha256: str
    rule_ids: tuple[str, ...]
    claims: tuple[RenderedClaim, ...]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fail(message: str) -> None:
    raise RenderValidationError(message)


def _exact_object(value: object, fields: frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(f"{name} must be an object")
    if set(value) != fields:
        _fail(f"{name} has an unexpected schema")
    return value


def _sha_field(value: object, name: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        _fail(f"{name} must be a lowercase SHA-256 digest")
    return value


def _string(value: object, name: str) -> str:
    if type(value) is not str or not value:
        _fail(f"{name} must be a non-empty string")
    return value


def _string_list(value: object, name: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        _fail(f"{name} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        _fail(f"{name} must not contain duplicate values")
    return value


def _money(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_MONEY_PAISE:
        _fail(
            f"{name} must be an integer from 0 through "
            f"{MAX_MONEY_PAISE} paise"
        )
    return value


def _json_with_unique_keys(data: bytes) -> object:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    return json.loads(data.decode("utf-8"), object_pairs_hook=unique)


def _load_certificate(path: str | Path) -> tuple[Path, bytes, dict[str, Any]]:
    if not isinstance(path, (str, Path)):
        _fail("render_194r accepts only a certificate file path")
    try:
        certificate_path = Path(path).resolve(strict=True)
        if not certificate_path.is_file():
            _fail("certificate path is not a regular file")
        if certificate_path.stat().st_size > MAX_CERTIFICATE_BYTES:
            _fail("certificate exceeds the rendering size limit")
        data = certificate_path.read_bytes()
        raw = _json_with_unique_keys(data)
    except RenderValidationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        _fail(f"cannot load a valid certificate: {exc}")
    certificate = _exact_object(raw, TOP_LEVEL_FIELDS, "certificate")
    return certificate_path, data, certificate


def _validate_complete_facts(raw: object, lean_facts: dict[str, Any]) -> None:
    complete = _exact_object(
        raw, frozenset({"brand", "creator", "transaction"}),
        "complete_collab_input_facts",
    )
    brand = _exact_object(
        complete["brand"],
        frozenset({
            "entity_type",
            "in_business",
            "preceding_fy_business_turnover_paise",
            "preceding_fy_profession_receipts_paise",
        }),
        "complete_collab_input_facts.brand",
    )
    creator = _exact_object(
        complete["creator"],
        frozenset({
            "is_resident",
            "pan_furnished",
            "special_category_state",
            "gst_registered",
            "fy_prior_benefits_from_brand_paise",
            "fy_prior_194r_tds_paise",
            "fy_prior_cash_fees_from_brand_paise",
            "fy_prior_cash_tds_paise",
            "fy_prior_aggregate_turnover_paise",
        }),
        "complete_collab_input_facts.creator",
    )
    transaction = _exact_object(
        complete["transaction"],
        frozenset({
            "cash_fee_paise",
            "product_fmv_paise",
            "product_retained",
            "deliverable_linked",
            "tax_borne_by",
        }),
        "complete_collab_input_facts.transaction",
    )

    if brand["entity_type"] not in {"individual", "huf", "firm", "company"}:
        _fail("complete brand entity_type is invalid")
    if transaction["tax_borne_by"] not in {"recipient", "provider"}:
        _fail("complete transaction tax_borne_by is invalid")
    for name, value in (
        ("brand.in_business", brand["in_business"]),
        ("creator.is_resident", creator["is_resident"]),
        ("creator.pan_furnished", creator["pan_furnished"]),
        ("creator.special_category_state", creator["special_category_state"]),
        ("creator.gst_registered", creator["gst_registered"]),
        ("transaction.product_retained", transaction["product_retained"]),
        ("transaction.deliverable_linked", transaction["deliverable_linked"]),
    ):
        if type(value) is not bool:
            _fail(f"complete fact {name} must be a boolean")
    for group_name, group, fields in (
        ("brand", brand, (
            "preceding_fy_business_turnover_paise",
            "preceding_fy_profession_receipts_paise",
        )),
        ("creator", creator, (
            "fy_prior_benefits_from_brand_paise",
            "fy_prior_194r_tds_paise",
            "fy_prior_cash_fees_from_brand_paise",
            "fy_prior_cash_tds_paise",
            "fy_prior_aggregate_turnover_paise",
        )),
        ("transaction", transaction, ("cash_fee_paise", "product_fmv_paise")),
    ):
        for field in fields:
            _money(group[field], f"complete fact {group_name}.{field}")

    projection = {
        "brand": {
            "entity_type": brand["entity_type"],
            "in_business": brand["in_business"],
            "preceding_fy_business_turnover_paise": (
                brand["preceding_fy_business_turnover_paise"]
            ),
            "preceding_fy_profession_receipts_paise": (
                brand["preceding_fy_profession_receipts_paise"]
            ),
        },
        "creator": {
            "is_resident": creator["is_resident"],
            "pan_furnished": creator["pan_furnished"],
            "fy_prior_benefits_from_brand_paise": (
                creator["fy_prior_benefits_from_brand_paise"]
            ),
            "fy_prior_194r_tds_paise": creator["fy_prior_194r_tds_paise"],
        },
        "transaction": {
            "product_fmv_paise": transaction["product_fmv_paise"],
            "product_retained": transaction["product_retained"],
            "tax_borne_by": transaction["tax_borne_by"],
        },
    }
    if projection != lean_facts:
        _fail("complete facts do not project to the normalized Lean facts")
    from .runtime_proof import normalized_facts

    if complete != normalized_facts(S194RFacts.from_dict(lean_facts).to_collab()):
        _fail("confirmed pipeline certificate contains unsupported hidden inputs")


def _validate_intake(
    raw: object,
    *,
    certificate_path: Path,
    fact_hash: str,
    governance_hash: str,
    specification_bundle_hash: str,
) -> dict[str, Any]:
    intake = _exact_object(raw, INTAKE_FIELDS, "intake")
    expected_literals = {
        "status": "CONFIRMED",
        "intent": "section_194r_decision",
        "requested_period": "FY 2024-25",
        "specification_version": SPEC_VERSION,
    }
    for field, expected in expected_literals.items():
        if intake[field] != expected:
            _fail(f"intake {field} does not match the trusted slice")
    case_id = _string(intake["case_id"], "intake.case_id")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", case_id) is None:
        _fail("intake.case_id has an invalid format")
    for field in (
        "draft_sha256",
        "confirmation_sha256",
        "source_bundle_sha256",
        "provenance_sha256",
    ):
        _sha_field(intake[field], f"intake.{field}")
    if intake["normalized_fact_sha256"] != fact_hash:
        _fail("intake confirmation is bound to different normalized facts")
    _sha_field(intake["normalized_fact_sha256"], "intake.normalized_fact_sha256")
    if intake["rule_bundle_hash"] != governance_hash:
        _fail("intake confirmation is bound to a different governance bundle")
    _sha_field(intake["rule_bundle_hash"], "intake.rule_bundle_hash")
    if intake["specification_bundle_sha256"] != specification_bundle_hash:
        _fail("intake confirmation is bound to different specification sources")
    _sha_field(
        intake["specification_bundle_sha256"],
        "intake.specification_bundle_sha256",
    )

    artifact = _exact_object(
        intake["confirmed_case_artifact"],
        frozenset({"path", "sha256"}),
        "intake.confirmed_case_artifact",
    )
    recorded_artifact_hash = _sha_field(
        artifact["sha256"], "intake.confirmed_case_artifact.sha256"
    )
    try:
        confirmed_path = Path(
            _string(artifact["path"], "intake.confirmed_case_artifact.path")
        )
        if not confirmed_path.is_absolute():
            _fail("confirmed-case artifact path must be absolute")
        confirmed_path = confirmed_path.resolve(strict=True)
        if not confirmed_path.is_file():
            _fail("confirmed-case artifact is not a regular file")
        if confirmed_path.parent != certificate_path.parent:
            _fail("confirmed-case artifact is not colocated with its certificate")
        if confirmed_path.stat().st_size > MAX_CONFIRMED_CASE_BYTES:
            _fail("confirmed-case artifact exceeds the rendering size limit")
        confirmed_data = confirmed_path.read_bytes()
        confirmed_raw = _json_with_unique_keys(confirmed_data)
        if type(confirmed_raw) is not dict:
            _fail("confirmed-case artifact must contain an object")
        confirmed = ConfirmedCase.from_dict(confirmed_raw)
    except RenderValidationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        _fail(f"cannot validate confirmed-case artifact: {exc}")
    if _sha256(confirmed_data) != recorded_artifact_hash:
        _fail("confirmed-case artifact hash is invalid")
    summary = {
        key: value for key, value in intake.items()
        if key != "confirmed_case_artifact"
    }
    if summary != confirmed.certificate_record():
        _fail("certificate intake does not match the confirmed-case artifact")
    if confirmed.normalized_fact_sha256 != fact_hash:
        _fail("confirmed-case artifact is bound to different normalized facts")

    payload = _exact_object(
        intake["confirmation_payload"],
        CONFIRMATION_PAYLOAD_FIELDS,
        "intake.confirmation_payload",
    )
    expected_payload = {
        "schema_version": "collabproof-s194r-confirmation-v1",
        "decision": "CONFIRMED",
        "case_id": case_id,
        "intent": intake["intent"],
        "requested_period": intake["requested_period"],
        "draft_sha256": intake["draft_sha256"],
        "normalized_fact_sha256": intake["normalized_fact_sha256"],
        "source_bundle_sha256": intake["source_bundle_sha256"],
        "provenance_sha256": intake["provenance_sha256"],
        "specification_version": intake["specification_version"],
        "specification_bundle_sha256": intake[
            "specification_bundle_sha256"
        ],
        "rule_bundle_hash": intake["rule_bundle_hash"],
    }
    if payload != expected_payload:
        _fail("intake confirmation payload does not bind the persisted intake record")
    expected_confirmation_hash = _sha256(_canonical_json(expected_payload))
    if intake["confirmation_sha256"] != expected_confirmation_hash:
        _fail("intake confirmation digest is invalid")
    return intake


def _validate_governance(certificate: dict[str, Any]) -> str:
    governance = _exact_object(
        certificate["governance"],
        frozenset({"rule_bundle_hash", "applied_rule_ids", "formal_model_rule_ids"}),
        "governance",
    )
    try:
        errors = validate_governance(RULES)
        if errors:
            _fail("current governance is invalid: " + "; ".join(errors))
        current_hash = rule_bundle_hash(RULES)
    except Exception as exc:  # fail closed if current governance cannot be read
        _fail(f"cannot establish current governance identity: {exc}")
    _sha_field(governance["rule_bundle_hash"], "governance.rule_bundle_hash")
    if governance["rule_bundle_hash"] != current_hash:
        _fail("certificate governance bundle is not current")

    covered = _string_list(certificate["covered_rule_ids"], "covered_rule_ids")
    applied = _string_list(certificate["applied_rule_ids"], "applied_rule_ids")
    governed_applied = _string_list(
        governance["applied_rule_ids"], "governance.applied_rule_ids"
    )
    formal = _string_list(
        governance["formal_model_rule_ids"], "governance.formal_model_rule_ids"
    )
    expected_formal = list(FORMAL_MODEL_RULE_IDS)
    if covered != expected_formal or formal != expected_formal:
        _fail("certificate formal-model rule coverage is not the trusted whitelist")
    if applied != governed_applied:
        _fail("top-level and governed applied rule IDs differ")
    if any(rule_id not in RULES for rule_id in (*covered, *applied)):
        _fail("certificate contains an unknown rule ID")
    return current_hash


def _validate_specification(raw: object, governance_hash: str) -> str:
    spec = _exact_object(raw, SPECIFICATION_FIELDS, "specification")
    if spec["version"] != SPEC_VERSION:
        _fail("certificate specification version is stale")
    if spec["rule_bundle_hash"] != governance_hash:
        _fail("specification and governance bundle hashes differ")
    _sha_field(spec["rule_bundle_hash"], "specification.rule_bundle_hash")
    if type(spec["worktree_clean"]) is not bool:
        _fail("specification.worktree_clean must be a boolean")
    identity = _string(spec["identity"], "specification.identity")
    commit = spec["git_commit"]
    if commit is not None and (type(commit) is not str or not commit):
        _fail("specification.git_commit must be a non-empty string or null")

    root = Path(__file__).resolve().parent.parent
    source_hashes = _exact_object(
        spec["source_sha256"], frozenset(SPEC_SOURCE_FILES),
        "specification.source_sha256",
    )
    current_source_hashes: dict[str, str] = {}
    for relative in SPEC_SOURCE_FILES:
        recorded = _sha_field(
            source_hashes[relative], f"specification.source_sha256.{relative}"
        )
        try:
            current = _sha256((root / relative).read_bytes())
        except OSError as exc:
            _fail(f"cannot establish current specification identity: {exc}")
        if recorded != current:
            _fail(f"certificate specification source is stale: {relative}")
        current_source_hashes[relative] = current

    bundle_hash = _sha256(_canonical_json(current_source_hashes))
    if spec["deterministic_source_bundle_sha256"] != bundle_hash:
        _fail("deterministic specification bundle hash is invalid")
    _sha_field(
        spec["deterministic_source_bundle_sha256"],
        "specification.deterministic_source_bundle_sha256",
    )
    if spec["worktree_clean"]:
        if commit is None or identity != f"git:{commit}":
            _fail("clean specification identity is invalid")
    elif identity != f"dirty-sha256:{bundle_hash}":
        _fail("dirty specification identity is invalid")

    current_identity = _specification_identity(root)
    for field in ("identity", "git_commit", "worktree_clean"):
        if spec[field] != current_identity[field]:
            _fail(f"recorded specification {field} is not current")
    return bundle_hash


def _validate_outputs(
    certificate: dict[str, Any], facts: S194RFacts, fact_hash: str
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    outputs = _exact_object(
        certificate["covered_outputs"], frozenset(OUTPUT_FIELDS), "covered_outputs"
    )
    if outputs["scope"] not in {
        "covered", "unsupported_non_resident", "unsupported_no_business_nexus"
    }:
        _fail("covered_outputs.scope is invalid")
    for field in ("benefit_qualifies", "provider_obligated", "release_gate_required"):
        if type(outputs[field]) is not bool:
            _fail(f"covered_outputs.{field} must be a boolean")
    for field in ("aggregate_benefit_paise", "tds_due_now_paise"):
        _money(outputs[field], f"covered_outputs.{field}")

    decision = certificate["decision_status"]
    if decision not in {"ANSWERED", "REFUSED"}:
        _fail("decision_status must be ANSWERED or REFUSED")
    expected_decision = "ANSWERED" if outputs["scope"] == "covered" else "REFUSED"
    if decision != expected_decision:
        _fail("decision_status contradicts the checked scope")

    _, expected_proof = theorem_source(facts, fact_hash)
    if outputs != expected_proof["expected"]:
        _fail("covered outputs do not match the current formal case")

    expected_rule_fields = (
        frozenset(OUTPUT_FIELDS) if decision == "ANSWERED" else frozenset({"scope"})
    )
    output_rules = _exact_object(
        certificate["output_rule_ids"], expected_rule_fields, "output_rule_ids"
    )
    for field, ids in output_rules.items():
        _string_list(ids, f"output_rule_ids.{field}")
        if any(rule_id not in FORMAL_MODEL_RULE_IDS or rule_id not in RULES for rule_id in ids):
            _fail(f"output_rule_ids.{field} contains an uncovered rule")
    expected_output_rules = _output_rule_ids(facts.to_collab())
    if output_rules != expected_output_rules:
        _fail("output rule trail does not match the current formal case")
    used_rules = {rule_id for ids in output_rules.values() for rule_id in ids}
    if used_rules != set(certificate["applied_rule_ids"]):
        _fail("output rule trail does not equal the applied rule trail")
    return outputs, output_rules


def _validate_artifact_and_checks(
    certificate: dict[str, Any],
    certificate_path: Path,
    facts: S194RFacts,
    fact_hash: str,
) -> Path:
    proof = _exact_object(certificate["proof_artifact"], PROOF_FIELDS, "proof_artifact")
    expected_source, expected_proof = theorem_source(facts, fact_hash)
    theorem = expected_proof["theorem"]
    for field in ("name", "statement", "specification_version", "proof_term"):
        if proof[field] != theorem[field]:
            _fail(f"proof_artifact.{field} does not match the formal case")
    _sha_field(proof["sha256"], "proof_artifact.sha256")
    try:
        artifact_path = Path(_string(proof["path"], "proof_artifact.path"))
        if not artifact_path.is_absolute():
            _fail("proof artifact path must be absolute")
        artifact_path = artifact_path.resolve(strict=True)
        if not artifact_path.is_file():
            _fail("proof artifact is not a regular file")
        if artifact_path.parent != certificate_path.parent:
            _fail("proof artifact is not colocated with its certificate")
        if artifact_path.name != f"s194r-{fact_hash}.lean":
            _fail("proof artifact filename does not match normalized facts")
        if artifact_path.stat().st_size > MAX_ARTIFACT_BYTES:
            _fail("proof artifact exceeds the rendering size limit")
        artifact = artifact_path.read_bytes()
    except RenderValidationError:
        raise
    except OSError as exc:
        _fail(f"cannot load proof artifact: {exc}")
    expected_bytes = expected_source.encode("utf-8")
    if artifact != expected_bytes:
        _fail("proof artifact does not encode the current formal case")
    if proof["sha256"] != _sha256(artifact):
        _fail("proof artifact hash is invalid")

    checks = _exact_object(
        certificate["lean_checks"],
        frozenset({"module_build", "per_case_kernel_check", "independence_boundary"}),
        "lean_checks",
    )
    if checks["independence_boundary"] != INDEPENDENCE_BOUNDARY:
        _fail("Lean independence boundary text is invalid")
    module = _validate_check(checks["module_build"], "lean_checks.module_build")
    kernel = _validate_check(
        checks["per_case_kernel_check"], "lean_checks.per_case_kernel_check"
    )
    if module["command"] != ["lake", "build", "LeanProof.S194R"]:
        _fail("recorded Lean module-build command is invalid")
    if kernel["command"] != ["lake", "env", "lean", str(artifact_path)]:
        _fail("recorded per-case Lean command targets a different artifact")
    return artifact_path


def _fresh_lean_check(artifact_path: Path) -> None:
    """Independently rerun Lean before any certificate prose is returned."""

    lake = shutil.which("lake")
    if lake is None:
        _fail("cannot independently verify certificate: lake is unavailable")
    root = Path(__file__).resolve().parent.parent
    commands = (
        [lake, "build", "LeanProof.S194R"],
        [lake, "env", "lean", str(artifact_path)],
    )
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            _fail("independent Lean verification timed out")
        except OSError as exc:
            _fail(f"independent Lean verification could not start: {exc}")
        if completed.returncode != 0:
            _fail("independent Lean verification rejected the certificate artifact")


def _validate_check(raw: object, name: str) -> dict[str, Any]:
    check = _exact_object(raw, CHECK_FIELDS, name)
    if (
        type(check["command"]) is not list
        or any(type(part) is not str or not part for part in check["command"])
    ):
        _fail(f"{name}.command must be a list of non-empty strings")
    if type(check["exit_code"]) is not int or check["exit_code"] != 0:
        _fail(f"{name} did not record a successful exit")
    if check["result"] != "PASS":
        _fail(f"{name} did not pass")
    if type(check["stdout"]) is not str or type(check["stderr"]) is not str:
        _fail(f"{name} output fields must be strings")
    return check


def _validate_other_outputs(raw: object) -> None:
    other = _exact_object(
        raw, frozenset({"cash_tds_194j_194c", "gst"}), "other_outputs"
    )
    expected_fields = frozenset({"status", "reason"})
    for name in ("cash_tds_194j_194c", "gst"):
        item = _exact_object(other[name], expected_fields, f"other_outputs.{name}")
        if item["status"] != "UNSUPPORTED_UNVERIFIED":
            _fail(f"other_outputs.{name} must remain unsupported and unverified")
        _string(item["reason"], f"other_outputs.{name}.reason")


def _validate_assumptions(value: object) -> tuple[str, ...]:
    if type(value) is not list or tuple(value) != BASE_ASSUMPTIONS:
        _fail("assumptions do not match the trusted Section 194R assumption set")
    return tuple(value)


def _indian_integer(value: int) -> str:
    digits = str(value)
    if len(digits) <= 3:
        return digits
    tail = digits[-3:]
    head = digits[:-3]
    pairs: list[str] = []
    while head:
        pairs.append(head[-2:])
        head = head[:-2]
    return ",".join(reversed(pairs)) + "," + tail


def _rupees(paise: int) -> str:
    whole, fraction = divmod(paise, 100)
    rendered = f"₹{_indian_integer(whole)}"
    return rendered if fraction == 0 else f"{rendered}.{fraction:02d}"


def _claim(
    output_rules: dict[str, list[str]],
    field: str,
    value: object,
    template_id: str,
) -> RenderedClaim:
    return RenderedClaim(
        pointer=f"/covered_outputs/{field}",
        value=value,
        rule_ids=tuple(output_rules[field]),
        template_id=template_id,
    )


_ASSUMPTION_TEMPLATE_IDS = (
    "assumption-fact-accuracy-and-aggregation-v1",
    "assumption-legal-model-selection-v1",
    "assumption-money-and-rounding-v1",
    "assumption-prior-aggregates-v1",
)


def _assumption_claims(
    assumptions: tuple[str, ...],
) -> tuple[RenderedClaim, ...]:
    return tuple(
        RenderedClaim(
            pointer=f"/assumptions/{index}",
            value=value,
            rule_ids=(),
            template_id=_ASSUMPTION_TEMPLATE_IDS[index],
        )
        for index, value in enumerate(assumptions)
    )


def _assumption_text() -> str:
    return (
        "This result assumes the entered facts are accurate and complete for "
        "one recipient/provider FY aggregation, the pinned specification is the "
        "intended legal interpretation, amounts are exact integer paise with "
        "s.288B rounding outside this slice, and the entered prior-benefit and "
        "prior-deduction aggregates are correct."
    )


def _rule_trail_text(output_rules: dict[str, list[str]]) -> str:
    ordered: list[str] = []
    for field in OUTPUT_FIELDS:
        rule_ids = output_rules.get(field, [])
        for rule_id in rule_ids:
            if rule_id not in ordered:
                ordered.append(rule_id)
    citations = "; ".join(
        f"{rule_id} ({RULES[rule_id].citation})" for rule_id in ordered
    )
    return (
        "Explanatory rule trail, validated by the Python/governance layer but "
        f"not a field of the Lean theorem: {citations}."
    )


def _render_answered(
    outputs: dict[str, Any],
    output_rules: dict[str, list[str]],
    assumptions: tuple[str, ...],
) -> tuple[str, tuple[RenderedClaim, ...]]:
    output_claims = (
        _claim(output_rules, "scope", outputs["scope"], "scope-covered-v1"),
        _claim(
            output_rules, "benefit_qualifies", outputs["benefit_qualifies"],
            "benefit-qualifies-yes-v1" if outputs["benefit_qualifies"]
            else "benefit-qualifies-no-v1",
        ),
        _claim(
            output_rules, "provider_obligated", outputs["provider_obligated"],
            "provider-obligated-yes-v1" if outputs["provider_obligated"]
            else "provider-obligated-no-v1",
        ),
        _claim(
            output_rules, "aggregate_benefit_paise",
            outputs["aggregate_benefit_paise"], "aggregate-benefit-v1",
        ),
        _claim(
            output_rules, "tds_due_now_paise", outputs["tds_due_now_paise"],
            "tds-due-positive-v1" if outputs["tds_due_now_paise"] > 0
            else "tds-due-zero-v1",
        ),
        _claim(
            output_rules, "release_gate_required",
            outputs["release_gate_required"],
            "release-gate-required-v1" if outputs["release_gate_required"]
            else "release-gate-not-required-v1",
        ),
    )
    benefit = (
        "The retained product qualifies as a Section 194R benefit in this model."
        if outputs["benefit_qualifies"] else
        "The product does not qualify as a Section 194R benefit in this model."
    )
    provider = (
        "The provider is within the modeled Section 194R deduction obligation."
        if outputs["provider_obligated"] else
        "The provider is outside the modeled Section 194R deduction obligation."
    )
    tds = (
        f"Section 194R TDS due now is {_rupees(outputs['tds_due_now_paise'])}."
        if outputs["tds_due_now_paise"] else
        "The model computes no Section 194R TDS due now for this case."
    )
    gate = (
        "The Section 194R in-kind release gate is required before release."
        if outputs["release_gate_required"] else
        "The model does not require the Section 194R in-kind release gate on these facts."
    )
    text = " ".join((
        "Using the certificate-recorded facts and the pinned FY 2024-25 formal model, "
        "an independent fresh Lean check produced this limited Section 194R result.",
        benefit,
        provider,
        f"The modeled FY aggregate benefit is "
        f"{_rupees(outputs['aggregate_benefit_paise'])}.",
        tds,
        gate,
        _rule_trail_text(output_rules),
        _assumption_text(),
        "The kernel check verifies only the computation against this formal model. "
        "It does not verify the truth of the input facts, the legal correctness or "
        "currentness of the model, cash TDS, GST, or complete tax compliance.",
    ))
    return text, output_claims + _assumption_claims(assumptions)


def _render_refused(
    outputs: dict[str, Any],
    output_rules: dict[str, list[str]],
    assumptions: tuple[str, ...],
) -> tuple[str, tuple[RenderedClaim, ...]]:
    scope = outputs["scope"]
    if scope == "unsupported_non_resident":
        reason = "the recipient is outside its resident-recipient scope"
        consequence = "It does not decide Section 195 or any other non-resident rule."
        template = "scope-refusal-nonresident-v1"
    elif scope == "unsupported_no_business_nexus":
        reason = "the entered transfer has no business-or-profession nexus"
        consequence = "It does not decide any other regime that may apply to the transfer."
        template = "scope-refusal-no-business-nexus-v1"
    else:  # guarded by _validate_outputs
        _fail("unsupported refusal scope")
    output_claims = (_claim(output_rules, "scope", scope, template),)
    text = " ".join((
        "Using the certificate-recorded facts and the pinned FY 2024-25 formal model, "
        f"an independent fresh Lean check confirmed only that this Section 194R slice refuses "
        f"the case because {reason}.",
        "This verified scope classification is not a finding that tax or TDS is zero.",
        consequence,
        _rule_trail_text(output_rules),
        _assumption_text(),
        "The kernel check does not verify the truth of the input facts, the legal "
        "correctness or currentness of the model, cash TDS, GST, or complete tax compliance.",
    ))
    return text, output_claims + _assumption_claims(assumptions)


def render_194r(path: str | Path) -> RenderedAnswer:
    """Validate and render a persisted v2 Section 194R certificate.

    Any absent, stale, malformed, tampered, unconfirmed, or non-kernel-checked
    trust binding raises :class:`RenderValidationError`; no prose is returned.
    """

    certificate_path, data, certificate = _load_certificate(path)
    if certificate["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported runtime certificate schema")
    if certificate["certification_status"] != "LEAN_KERNEL_CHECKED":
        _fail("certificate was not Lean-kernel checked")
    if certificate["trusted_scope"] != "section_194r_only":
        _fail("certificate trusted scope is not Section 194R only")

    try:
        facts = S194RFacts.from_dict(certificate["normalized_facts"])
    except (TypeError, ValueError) as exc:
        _fail(f"invalid normalized Section 194R facts: {exc}")
    normalized = facts.as_dict()
    if normalized != certificate["normalized_facts"]:
        _fail("normalized facts are not canonical")
    fact_hash = _sha256(_canonical_json(normalized))
    if certificate["normalized_fact_sha256"] != fact_hash:
        _fail("normalized fact hash is invalid")
    _sha_field(certificate["normalized_fact_sha256"], "normalized_fact_sha256")
    _validate_complete_facts(certificate["complete_collab_input_facts"], normalized)

    governance_hash = _validate_governance(certificate)
    specification_bundle_hash = _validate_specification(
        certificate["specification"], governance_hash
    )
    _validate_intake(
        certificate["intake"],
        certificate_path=certificate_path,
        fact_hash=fact_hash,
        governance_hash=governance_hash,
        specification_bundle_hash=specification_bundle_hash,
    )
    outputs, output_rules = _validate_outputs(certificate, facts, fact_hash)
    artifact_path = _validate_artifact_and_checks(
        certificate, certificate_path, facts, fact_hash
    )
    _validate_other_outputs(certificate["other_outputs"])
    assumptions = _validate_assumptions(certificate["assumptions"])

    _fresh_lean_check(artifact_path)

    # Detect any source, sidecar, governance, or artifact mutation that raced
    # the fresh subprocess check before prose is released.
    governance_hash = _validate_governance(certificate)
    specification_bundle_hash = _validate_specification(
        certificate["specification"], governance_hash
    )
    _validate_intake(
        certificate["intake"],
        certificate_path=certificate_path,
        fact_hash=fact_hash,
        governance_hash=governance_hash,
        specification_bundle_hash=specification_bundle_hash,
    )
    _validate_artifact_and_checks(
        certificate, certificate_path, facts, fact_hash
    )

    if certificate["decision_status"] == "ANSWERED":
        text, claims = _render_answered(outputs, output_rules, assumptions)
    else:
        text, claims = _render_refused(outputs, output_rules, assumptions)
    return RenderedAnswer(
        text=text,
        certificate_sha256=_sha256(data),
        rule_ids=tuple(certificate["applied_rule_ids"]),
        claims=claims,
    )
