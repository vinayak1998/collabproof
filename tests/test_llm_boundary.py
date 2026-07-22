"""Regression tests for the LLM prompt and strict experiment boundary."""
from dataclasses import fields
import json

import pytest

from collabproof import (
    Brand,
    Collab,
    Creator,
    EntityType,
    TaxBearer,
    assess,
    assessment_as_claim,
    rup,
)
from collabproof.llm_adapter import (
    classify_llm_answer,
    facts_of,
    parse_llm_output,
    payload_for_claim,
    validate_llm_payload,
)
from collabproof.verify import Claim
from experiments import three_arms
from run_eval import evaluate


def collab(**kwargs):
    kwargs.setdefault("brand", Brand(EntityType.COMPANY))
    kwargs.setdefault("creator", Creator())
    return Collab(**kwargs)


def all_null_payload(**updates):
    payload = {
        "tds_194r_paise": None,
        "release_gate_required": None,
        "cash_tds_paise": None,
        "cash_tds_basis": None,
        "gst_registration_required": None,
        "gst_liability_paise": None,
        "cannot_determine": False,
        "reason": None,
    }
    payload.update(updates)
    return payload


def test_facts_of_serializes_every_decision_input():
    # These guards force a prompt review whenever the domain model grows.
    assert {field.name for field in fields(Brand)} == {
        "entity_type",
        "preceding_fy_business_turnover_paise",
        "preceding_fy_profession_receipts_paise",
        "in_business",
    }
    assert {field.name for field in fields(Creator)} == {
        "is_resident",
        "pan_furnished",
        "special_category_state",
        "gst_registered",
        "fy_prior_benefits_from_brand_paise",
        "fy_prior_194r_tds_paise",
        "fy_prior_cash_fees_from_brand_paise",
        "fy_prior_cash_tds_paise",
        "fy_prior_aggregate_turnover_paise",
    }
    assert {field.name for field in fields(Collab)} == {
        "brand",
        "creator",
        "cash_fee_paise",
        "product_fmv_paise",
        "product_retained",
        "deliverable_linked",
        "tax_borne_by",
    }

    c = Collab(
        brand=Brand(
            EntityType.HUF,
            preceding_fy_business_turnover_paise=101,
            preceding_fy_profession_receipts_paise=202,
            in_business=False,
        ),
        creator=Creator(
            is_resident=False,
            pan_furnished=False,
            special_category_state=True,
            gst_registered=True,
            fy_prior_benefits_from_brand_paise=303,
            fy_prior_194r_tds_paise=404,
            fy_prior_cash_fees_from_brand_paise=505,
            fy_prior_cash_tds_paise=606,
            fy_prior_aggregate_turnover_paise=707,
        ),
        cash_fee_paise=808,
        product_fmv_paise=909,
        product_retained=False,
        deliverable_linked=False,
        tax_borne_by=TaxBearer.PROVIDER,
    )

    assert json.loads(facts_of(c)) == {
        "brand_entity": "huf",
        "brand_in_business": False,
        "brand_preceding_fy_business_turnover_paise": 101,
        "brand_preceding_fy_profession_receipts_paise": 202,
        "creator_resident": False,
        "creator_pan_furnished": False,
        "creator_special_category_state": True,
        "creator_gst_registered": True,
        "fy_prior_benefits_from_brand_paise": 303,
        "fy_prior_194r_tds_paise": 404,
        "fy_prior_cash_fees_from_brand_paise": 505,
        "fy_prior_cash_tds_paise": 606,
        "fy_prior_aggregate_turnover_paise": 707,
        "cash_fee_paise": 808,
        "product_fmv_paise": 909,
        "product_retained": False,
        "deliverable_linked": False,
        "tax_borne_by": "provider",
    }


def test_in_scope_and_no_business_nexus_prompts_are_not_identical():
    in_scope = collab(product_fmv_paise=rup(30_000))
    out_of_scope = collab(
        brand=Brand(EntityType.COMPANY, in_business=False),
        product_fmv_paise=rup(30_000),
    )

    assert facts_of(in_scope) != facts_of(out_of_scope)
    assert json.loads(facts_of(in_scope))["brand_in_business"] is True
    assert json.loads(facts_of(out_of_scope))["brand_in_business"] is False


@pytest.mark.parametrize(
    ("updates", "reason_fragment"),
    [
        ({"tds_194r_paise": True}, "integer"),
        ({"tds_194r_paise": 1.0}, "integer"),
        ({"tds_194r_paise": "100"}, "integer"),
        ({"release_gate_required": 1}, "boolean"),
        ({"gst_registration_required": "false"}, "boolean"),
        ({"cannot_determine": "false"}, "cannot_determine"),
        ({"cash_tds_basis": ["IT-194J-PROF"]}, "string or null"),
        ({"cash_tds_basis": "IT-194A"}, "allowed rule"),
        ({"cash_tds_basis": "IT-194J-PROF"}, "without cash_tds_paise"),
        ({"gst_liability_paise": -1}, "negative"),
        ({"reason": 123}, "string"),
    ],
)
def test_strict_payload_rejects_wrong_types_and_enums(updates, reason_fragment):
    parsed = validate_llm_payload(all_null_payload(**updates))
    assert not parsed.valid
    assert reason_fragment in parsed.invalid_reason


def test_strict_payload_rejects_missing_unknown_and_non_object_json():
    missing = all_null_payload()
    del missing["tds_194r_paise"]
    assert "missing required keys" in validate_llm_payload(missing).invalid_reason

    unknown = all_null_payload(extra=1)
    assert "unknown keys" in validate_llm_payload(unknown).invalid_reason

    assert not parse_llm_output("[]").valid
    assert not parse_llm_output("```json\n{}\n```").valid
    assert not parse_llm_output("preface " + json.dumps(all_null_payload())).valid
    duplicate = json.dumps(all_null_payload())[:-1] + ', "reason": null}'
    assert "duplicate object key" in parse_llm_output(duplicate).invalid_reason


def test_refusal_cannot_launder_asserted_outcomes():
    raw = all_null_payload(
        tds_194r_paise=1,
        cannot_determine=True,
        reason="I cannot decide",
    )
    parsed = validate_llm_payload(raw)
    assert not parsed.valid
    assert "contradicts asserted fields" in parsed.invalid_reason

    status, cert, missing = three_arms.experiment_status(
        Claim(tds_194r_paise=1), raw, collab(product_fmv_paise=rup(30_000)))
    assert (status, cert, missing) == ("INVALID_OUTPUT", None, [])


def test_invalid_output_status_survives_both_evaluation_paths():
    c = collab(product_fmv_paise=rup(30_000))
    raw = all_null_payload(cannot_determine="false")
    parsed = validate_llm_payload(raw)
    assert not parsed.valid

    status, cert, missing = three_arms.experiment_status(Claim(), raw, c)
    assert (status, cert, missing) == ("INVALID_OUTPUT", None, [])

    report = evaluate("invalid-llm", lambda _: parsed, [c])
    assert report["tally"] == {"INVALID_OUTPUT": 1}
    assert report["rows"][0]["validation_error"]


def test_refusal_requires_a_real_reason():
    parsed = validate_llm_payload(all_null_payload(
        cannot_determine=True, reason="  "))
    assert not parsed.valid
    assert "non-empty reason" in parsed.invalid_reason


def test_all_null_answer_is_incomplete_in_experiment_and_legacy_eval():
    c = collab(product_fmv_paise=rup(30_000))
    parsed = validate_llm_payload(all_null_payload())
    assert parsed.valid
    assert classify_llm_answer(parsed, c).status == "INCOMPLETE"

    report = evaluate("all-null-llm", lambda _: parsed, [c])
    assert report["tally"] == {"INCOMPLETE": 1}
    assert report["rows"][0]["status"] == "INCOMPLETE"


def test_abstention_and_correct_refusal_are_distinct():
    raw = all_null_payload(cannot_determine=True, reason="outside known rules")
    parsed = validate_llm_payload(raw)
    assert parsed.valid
    assert classify_llm_answer(
        parsed, collab(product_fmv_paise=rup(30_000))).status == "ABSTAINED"
    assert classify_llm_answer(
        parsed,
        collab(
            brand=Brand(EntityType.COMPANY, in_business=False),
            product_fmv_paise=rup(30_000),
        ),
    ).status == "CORRECT_REFUSAL"


def test_silent_out_of_scope_answer_is_not_a_correct_refusal():
    parsed = validate_llm_payload(all_null_payload())
    verdict = classify_llm_answer(
        parsed,
        collab(
            brand=Brand(EntityType.COMPANY, in_business=False),
            product_fmv_paise=rup(30_000),
        ),
    )
    assert verdict.status == "INCOMPLETE"
    assert verdict.missing == ("cannot_determine", "reason")


def test_complete_spec_answer_is_certified_complete():
    c = collab(cash_fee_paise=rup(50_000), product_fmv_paise=rup(30_000))
    claim = assessment_as_claim(assess(c), basis="IT-194J-PROF")
    parsed = validate_llm_payload(payload_for_claim(claim))
    assert parsed.valid
    assert classify_llm_answer(parsed, c).status == "CERTIFIED_COMPLETE"


def test_explicit_null_is_a_complete_result_for_immaterial_basis_and_gst():
    c = collab(product_fmv_paise=rup(15_000))
    claim = assessment_as_claim(assess(c))
    raw = payload_for_claim(claim)
    assert raw["cash_tds_basis"] is None
    assert raw["gst_liability_paise"] is None

    parsed = validate_llm_payload(raw)
    assert parsed.valid
    assert classify_llm_answer(parsed, c).status == "CERTIFIED_COMPLETE"


def test_llm_retries_keep_initial_prompt_and_prior_answer(monkeypatch):
    calls = []
    response = json.dumps(all_null_payload())

    def fake_call(messages, model):
        calls.append(list(messages))
        return response

    monkeypatch.setattr(three_arms, "call_llm", fake_call)
    monkeypatch.setattr(three_arms.time, "sleep", lambda _: None)

    answerer = three_arms.LlmAnswerer(lambda _: "INITIAL WITH CORPUS", "test")
    c = collab(product_fmv_paise=rup(30_000))
    answerer.start(c)
    answerer.retry(c, "RULE FEEDBACK")

    assert calls[0] == [{"role": "user", "content": "INITIAL WITH CORPUS"}]
    assert calls[1] == [
        {"role": "user", "content": "INITIAL WITH CORPUS"},
        {"role": "assistant", "content": response},
        {"role": "user", "content": "RULE FEEDBACK"},
    ]
