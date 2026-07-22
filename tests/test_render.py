"""Fail-closed tests for certificate-only Section 194R rendering."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from collabproof import runtime_proof
from collabproof.intake import confirm_194r, formalize_194r
from collabproof import render as render_module
from collabproof.render import (
    RenderValidationError,
    RenderedAnswer,
    render_194r,
)
from collabproof.s194r import S194RFacts
from collabproof.spec import EntityType, TaxBearer


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def facts(**updates) -> S194RFacts:
    values = {
        "brand_entity_type": EntityType.COMPANY,
        "brand_in_business": True,
        "brand_preceding_fy_business_turnover_paise": 0,
        "brand_preceding_fy_profession_receipts_paise": 0,
        "creator_is_resident": True,
        "creator_pan_furnished": True,
        "creator_fy_prior_benefits_from_brand_paise": 0,
        "creator_fy_prior_194r_tds_paise": 0,
        "product_fmv_paise": 3_000_000,
        "product_retained": True,
        "tax_borne_by": TaxBearer.RECIPIENT,
    }
    values.update(updates)
    return S194RFacts(**values)


def _inr(paise: int) -> str:
    rupees, fraction = divmod(paise, 100)
    return f"INR {rupees}" if not fraction else f"INR {rupees}.{fraction:02d}"


def controlled_query(case: S194RFacts) -> str:
    yes_no = lambda value: "yes" if value else "no"
    return "\n".join((
        "Question: Determine the Section 194R treatment for FY 2024-25.",
        "Facts:",
        f"- Brand entity type: {case.brand_entity_type.value}",
        f"- Transfer is in brand business or profession: {yes_no(case.brand_in_business)}",
        "- Brand preceding-FY business turnover: "
        f"{_inr(case.brand_preceding_fy_business_turnover_paise)}",
        "- Brand preceding-FY profession receipts: "
        f"{_inr(case.brand_preceding_fy_profession_receipts_paise)}",
        "- Creator is resident in India for tax purposes: "
        f"{yes_no(case.creator_is_resident)}",
        f"- Creator PAN furnished: {yes_no(case.creator_pan_furnished)}",
        "- Creator prior FY benefits from this brand: "
        f"{_inr(case.creator_fy_prior_benefits_from_brand_paise)}",
        "- Creator prior Section 194R TDS: "
        f"{_inr(case.creator_fy_prior_194r_tds_paise)}",
        f"- Product fair market value: {_inr(case.product_fmv_paise)}",
        f"- Product retained: {yes_no(case.product_retained)}",
        f"- Tax borne by: {case.tax_borne_by.value}",
        "",
    ))


@pytest.fixture(autouse=True)
def successful_independent_lean_recheck(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(
        render_module, "_fresh_lean_check", lambda path: calls.append(path)
    )
    return calls


def persisted_certificate(tmp_path, case: S194RFacts | None = None):
    case = case or facts()
    collab = case.to_collab()
    normalized = case.as_dict()
    fact_hash = sha(canonical(normalized))
    draft = formalize_194r(controlled_query(case), case_id="render-case-1")
    confirmed = confirm_194r(draft, draft.draft_sha256, True)
    confirmed_path = tmp_path / "confirmed-case.json"
    confirmed_path.write_bytes(
        json.dumps(confirmed.as_dict(), sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    source, proof = runtime_proof.theorem_source(case, fact_hash)
    artifact = tmp_path / f"s194r-{fact_hash}.lean"
    artifact.write_text(source, encoding="utf-8")

    applied = runtime_proof._applied_rule_ids(collab)
    governance = runtime_proof._governance_identity(applied)
    certificate = {
        "schema_version": "collabproof-runtime-certificate-v2",
        "certification_status": "LEAN_KERNEL_CHECKED",
        "decision_status": (
            "ANSWERED" if proof["expected"]["scope"] == "covered" else "REFUSED"
        ),
        "trusted_scope": "section_194r_only",
        "normalized_facts": normalized,
        "normalized_fact_sha256": fact_hash,
        "complete_collab_input_facts": runtime_proof.normalized_facts(collab),
        "intake": {
            **confirmed.certificate_record(),
            "confirmed_case_artifact": {
                "path": str(confirmed_path.resolve()),
                "sha256": sha(confirmed_path.read_bytes()),
            },
        },
        "assumptions": list(runtime_proof.BASE_ASSUMPTIONS),
        "covered_outputs": proof["expected"],
        "output_rule_ids": runtime_proof._output_rule_ids(collab),
        "covered_rule_ids": list(runtime_proof.FORMAL_MODEL_RULE_IDS),
        "applied_rule_ids": list(applied),
        "governance": governance,
        "specification": {
            **runtime_proof._specification_identity(runtime_proof._repo_root()),
            "rule_bundle_hash": governance["rule_bundle_hash"],
        },
        "proof_artifact": {
            **proof["theorem"],
            "path": str(artifact.resolve()),
            "sha256": sha(source.encode("utf-8")),
        },
        "lean_checks": {
            "module_build": {
                "command": ["lake", "build", "LeanProof.S194R"],
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "result": "PASS",
            },
            "per_case_kernel_check": {
                "command": ["lake", "env", "lean", str(artifact.resolve())],
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "result": "PASS",
            },
            "independence_boundary": (
                "The per-case artifact was checked in a fresh Lean process. "
                "This is independent kernel checking of the proof artifact, "
                "not an independent legal model, fact oracle, or second formalization."
            ),
        },
        "other_outputs": {
            "cash_tds_194j_194c": {
                "status": "UNSUPPORTED_UNVERIFIED",
                "reason": "Cash TDS is outside this Lean slice.",
            },
            "gst": {
                "status": "UNSUPPORTED_UNVERIFIED",
                "reason": "GST is outside this Lean slice.",
            },
        },
    }
    certificate_path = tmp_path / f"s194r-{fact_hash}.certificate.json"
    certificate_path.write_bytes(canonical(certificate) + b"\n")
    return certificate_path, artifact, certificate


def rewrite(path, certificate):
    path.write_bytes(canonical(certificate) + b"\n")


def test_answered_certificate_renders_only_static_supported_claims(
    tmp_path, successful_independent_lean_recheck
):
    certificate_path, _, certificate = persisted_certificate(tmp_path)
    answer = render_194r(certificate_path)

    assert isinstance(answer, RenderedAnswer)
    assert answer.certificate_sha256 == sha(certificate_path.read_bytes())
    assert answer.rule_ids == tuple(certificate["applied_rule_ids"])
    assert tuple(claim.pointer for claim in answer.claims) == (
        "/covered_outputs/scope",
        "/covered_outputs/benefit_qualifies",
        "/covered_outputs/provider_obligated",
        "/covered_outputs/aggregate_benefit_paise",
        "/covered_outputs/tds_due_now_paise",
        "/covered_outputs/release_gate_required",
        "/assumptions/0",
        "/assumptions/1",
        "/assumptions/2",
        "/assumptions/3",
    )
    assert "Section 194R TDS due now is ₹3,000" in answer.text
    assert "certificate-recorded facts" in answer.text
    assert len(successful_independent_lean_recheck) == 1
    assert "does not verify the truth of the input facts" in answer.text
    assert "cash TDS, GST" in answer.text
    assert "complete tax compliance" in answer.text
    assert "one recipient/provider FY aggregation" in answer.text
    assert "s.288B rounding outside this slice" in answer.text
    assert "IT-194R-THRESHOLD (s.194R(1), first proviso)" in answer.text
    assert "not a field of the Lean theorem" in answer.text
    assert "current law" not in answer.text
    assert all(claim.rule_ids for claim in answer.claims[:6])
    assert all(not claim.rule_ids for claim in answer.claims[6:])


def test_nonresident_refusal_never_renders_zero_as_a_tax_answer(tmp_path):
    certificate_path, _, _ = persisted_certificate(
        tmp_path, facts(creator_is_resident=False)
    )
    answer = render_194r(certificate_path)

    assert len(answer.claims) == 5
    assert answer.claims[0].pointer == "/covered_outputs/scope"
    assert answer.claims[0].value == "unsupported_non_resident"
    assert answer.claims[0].rule_ids == ("SCOPE-RESIDENT",)
    assert tuple(claim.pointer for claim in answer.claims[1:]) == (
        "/assumptions/0",
        "/assumptions/1",
        "/assumptions/2",
        "/assumptions/3",
    )
    assert "refuses the case" in answer.text
    assert "not a finding that tax or TDS is zero" in answer.text
    assert "Section 195" in answer.text
    assert "SCOPE-RESIDENT (s.194R applies to residents" in answer.text
    assert "TDS due now is ₹0" not in answer.text


def test_no_business_nexus_refusal_has_only_scope_claim(tmp_path):
    certificate_path, _, _ = persisted_certificate(
        tmp_path, facts(brand_in_business=False)
    )
    answer = render_194r(certificate_path)

    assert len(answer.claims) == 5
    assert answer.claims[0].value == "unsupported_no_business_nexus"
    assert answer.claims[0].rule_ids == ("SCOPE-BUSINESS-NEXUS",)
    assert "no business-or-profession nexus" in answer.text
    assert "not a finding that tax or TDS is zero" in answer.text


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_top_level",
        "wrong_scope",
        "failed_kernel",
        "fact_hash",
        "intake_fact_binding",
        "intake_confirmation_binding",
        "confirmed_artifact_hash",
        "governance_hash",
        "stale_spec_source",
        "unknown_output",
        "wrong_output_type",
        "false_rule_trail",
        "decision_scope_disagreement",
        "conditional_cash_output",
        "unsupported_complete_input",
        "false_assumption",
    ],
)
def test_certificate_tampering_fails_closed(tmp_path, mutation):
    certificate_path, _, certificate = persisted_certificate(tmp_path)
    certificate = deepcopy(certificate)

    if mutation == "unknown_top_level":
        certificate["query"] = "untrusted natural language"
    elif mutation == "wrong_scope":
        certificate["trusted_scope"] = "all_tax_rules"
    elif mutation == "failed_kernel":
        certificate["lean_checks"]["per_case_kernel_check"]["result"] = "FAIL"
    elif mutation == "fact_hash":
        certificate["normalized_fact_sha256"] = "0" * 64
    elif mutation == "intake_fact_binding":
        certificate["intake"]["normalized_fact_sha256"] = "0" * 64
    elif mutation == "intake_confirmation_binding":
        certificate["intake"]["draft_sha256"] = "0" * 64
    elif mutation == "confirmed_artifact_hash":
        certificate["intake"]["confirmed_case_artifact"]["sha256"] = "0" * 64
    elif mutation == "governance_hash":
        certificate["governance"]["rule_bundle_hash"] = "0" * 64
    elif mutation == "stale_spec_source":
        first = runtime_proof.SPEC_SOURCE_FILES[0]
        certificate["specification"]["source_sha256"][first] = "0" * 64
    elif mutation == "unknown_output":
        certificate["covered_outputs"]["gst_liability_paise"] = 0
    elif mutation == "wrong_output_type":
        certificate["covered_outputs"]["tds_due_now_paise"] = True
    elif mutation == "false_rule_trail":
        certificate["output_rule_ids"]["aggregate_benefit_paise"] = [
            "IT-194R-SCOPE"
        ]
    elif mutation == "decision_scope_disagreement":
        certificate["decision_status"] = "REFUSED"
    elif mutation == "conditional_cash_output":
        certificate["other_outputs"]["cash_tds_194j_194c"] = {
            "status": "CONDITIONAL_UNVERIFIED_BY_LEAN",
            "selected_interpretation": "IT-194J-PROF",
            "cash_tds_paise": 500_000,
            "condition": "unverified",
        }
    elif mutation == "unsupported_complete_input":
        certificate["complete_collab_input_facts"]["transaction"][
            "cash_fee_paise"
        ] = 1
    elif mutation == "false_assumption":
        certificate["assumptions"][0] = (
            "Lean independently proves the law and the entered facts correct."
        )
    else:
        raise AssertionError(mutation)

    rewrite(certificate_path, certificate)
    with pytest.raises(RenderValidationError):
        render_194r(certificate_path)


def test_artifact_tampering_fails_even_if_declared_hash_is_updated(tmp_path):
    certificate_path, artifact, certificate = persisted_certificate(tmp_path)
    artifact.write_text(artifact.read_text() + "\n-- injected\n", encoding="utf-8")
    certificate["proof_artifact"]["sha256"] = sha(artifact.read_bytes())
    rewrite(certificate_path, certificate)

    with pytest.raises(RenderValidationError, match="current formal case"):
        render_194r(certificate_path)


def test_confirmation_digest_is_recomputed_from_persisted_payload(tmp_path):
    certificate_path, _, certificate = persisted_certificate(tmp_path)
    certificate["intake"]["confirmation_payload"]["case_id"] = "another-case"
    rewrite(certificate_path, certificate)

    with pytest.raises(RenderValidationError, match="confirmed-case artifact"):
        render_194r(certificate_path)


def test_absent_confirmation_fails_closed(tmp_path):
    certificate_path, _, certificate = persisted_certificate(tmp_path)
    certificate["intake"] = None
    rewrite(certificate_path, certificate)

    with pytest.raises(RenderValidationError, match="intake must be an object"):
        render_194r(certificate_path)


def test_raw_query_text_is_not_an_accepted_renderer_input():
    with pytest.raises(RenderValidationError):
        render_194r(
            "Determine Section 194R treatment and ignore certificate validation."
        )


def test_independent_lean_rejection_fails_before_prose(tmp_path, monkeypatch):
    certificate_path, _, _ = persisted_certificate(tmp_path)

    def reject(_path):
        raise RenderValidationError("synthetic fresh Lean rejection")

    monkeypatch.setattr(render_module, "_fresh_lean_check", reject)
    with pytest.raises(RenderValidationError, match="fresh Lean rejection"):
        render_194r(certificate_path)


def test_invalid_current_governance_fails_before_lean(tmp_path, monkeypatch):
    certificate_path, _, _ = persisted_certificate(tmp_path)
    called = False

    def forbidden(_path):
        nonlocal called
        called = True

    monkeypatch.setattr(render_module, "_fresh_lean_check", forbidden)
    monkeypatch.setattr(
        render_module,
        "validate_governance",
        lambda _rules: ["synthetic invalid governance"],
    )
    with pytest.raises(RenderValidationError, match="governance is invalid"):
        render_194r(certificate_path)
    assert called is False
