"""Adversarial tests for the public certification contract."""
from dataclasses import replace

from collabproof import (UNSET, Brand, Claim, Collab, Creator, EntityType, Status,
                         TaxBearer, assess, assessment_as_claim, rup, verify)


COMPANY = Brand(EntityType.COMPANY)


def collab(**kwargs):
    kwargs.setdefault("brand", COMPANY)
    kwargs.setdefault("creator", Creator())
    return Collab(**kwargs)


def test_empty_claim_is_incomplete_not_certified():
    cert = verify(Claim(), collab(product_fmv_paise=rup(30_000)))
    assert cert.status is Status.INCOMPLETE
    assert cert.checked_fields == ()
    assert cert.missing_fields == cert.required_fields


def test_correct_partial_claim_is_incomplete_with_coverage():
    c = collab(product_fmv_paise=rup(30_000))
    cert = verify(Claim(tds_194r_paise=rup(3_000)), c)
    assert cert.status is Status.INCOMPLETE
    assert cert.checked_fields == ("tds_194r_paise",)
    assert "release_gate_required" in cert.missing_fields


def test_complete_claim_with_explicit_null_gst_liability_certifies():
    c = collab(product_fmv_paise=rup(30_000))
    truth = assessment_as_claim(assess(c))
    assert truth.gst_liability_paise is None
    cert = verify(truth, c)
    assert cert.status is Status.CERTIFIED
    assert cert.checked_fields == cert.required_fields
    assert cert.missing_fields == ()


def test_wrong_claim_is_rejected_even_when_other_fields_are_missing():
    c = collab(product_fmv_paise=rup(30_000))
    cert = verify(Claim(tds_194r_paise=0), c)
    assert cert.status is Status.REJECTED
    assert cert.missing_fields


def test_threshold_error_names_threshold_rule_not_scope_rule():
    c = collab(product_fmv_paise=rup(21_000))
    truth = assessment_as_claim(assess(c))
    cert = verify(replace(truth, tds_194r_paise=rup(100)), c)
    assert cert.status is Status.REJECTED
    mismatch = next(m for m in cert.mismatches if m.fld == "tds_194r_paise")
    assert mismatch.rule_id == "IT-194R-THRESHOLD"


def test_no_pan_rate_error_names_206aa():
    c = collab(product_fmv_paise=rup(30_000), creator=Creator(pan_furnished=False))
    truth = assessment_as_claim(assess(c))
    cert = verify(replace(truth, tds_194r_paise=rup(3_000)), c)
    assert cert.status is Status.REJECTED
    assert cert.mismatches[0].rule_id == "IT-206AA"


def test_returned_product_error_names_retained_rule():
    c = collab(product_fmv_paise=rup(30_000), product_retained=False)
    truth = assessment_as_claim(assess(c))
    cert = verify(replace(truth, tds_194r_paise=rup(3_000)), c)
    assert cert.status is Status.REJECTED
    assert cert.mismatches[0].rule_id == "IT-194R-RETAINED"


def test_small_provider_error_names_carveout_rule():
    small_provider = Brand(
        EntityType.INDIVIDUAL,
        preceding_fy_profession_receipts_paise=rup(40_00_000),
    )
    c = collab(brand=small_provider, product_fmv_paise=rup(30_000))
    truth = assessment_as_claim(assess(c))
    cert = verify(replace(truth, tds_194r_paise=rup(3_000)), c)
    assert cert.status is Status.REJECTED
    assert cert.mismatches[0].rule_id == "IT-194R-CARVEOUT"


def test_provider_borne_error_names_grossup_rule():
    c = collab(
        product_fmv_paise=rup(27_000),
        tax_borne_by=TaxBearer.PROVIDER,
    )
    truth = assessment_as_claim(assess(c))
    cert = verify(replace(truth, tds_194r_paise=rup(2_700)), c)
    assert cert.status is Status.REJECTED
    assert cert.mismatches[0].rule_id == "IT-194R-GROSSUP"


def test_release_gate_error_names_release_gate_rule():
    c = collab(product_fmv_paise=rup(30_000))
    truth = assessment_as_claim(assess(c))
    cert = verify(replace(truth, release_gate_required=False), c)
    assert cert.status is Status.REJECTED
    gate = next(m for m in cert.mismatches if m.fld == "release_gate_required")
    assert gate.rule_id == "IT-194R-RELEASEGATE"


def test_gst_liability_error_names_rate_rule():
    c = collab(
        cash_fee_paise=rup(50_000),
        product_fmv_paise=rup(60_000),
        creator=Creator(gst_registered=True),
    )
    truth = assessment_as_claim(assess(c), basis="IT-194J-PROF")
    cert = verify(replace(truth, gst_liability_paise=0), c)
    assert cert.status is Status.REJECTED
    gst = next(m for m in cert.mismatches if m.fld == "gst_liability_paise")
    assert gst.rule_id == "GST-RATE-18"


def test_malformed_runtime_types_are_invalid_not_coerced():
    c = collab(product_fmv_paise=rup(30_000))
    truth = assessment_as_claim(assess(c))
    for malformed in (
        replace(truth, tds_194r_paise=True),
        replace(truth, release_gate_required=1),
        replace(truth, gst_registration_required="false"),
        replace(truth, gst_liability_paise=0.0),
        replace(truth, cash_tds_basis="unknown"),
    ):
        assert verify(malformed, c).status is Status.INVALID


def test_explicit_null_basis_is_ambiguous_only_for_material_fork():
    c = collab(cash_fee_paise=rup(50_000), product_fmv_paise=rup(25_000))
    truth = assessment_as_claim(assess(c), basis="IT-194J-PROF")
    cert = verify(replace(truth, cash_tds_basis=None), c)
    assert cert.status is Status.AMBIGUOUS
    assert cert.missing_fields == ()


def test_omitted_basis_is_incomplete_even_when_other_fields_are_complete():
    c = collab(cash_fee_paise=rup(50_000), product_fmv_paise=rup(25_000))
    truth = assessment_as_claim(assess(c), basis="IT-194J-PROF")
    cert = verify(replace(truth, cash_tds_basis=UNSET), c)
    assert cert.status is Status.INCOMPLETE
    assert cert.missing_fields == ("cash_tds_basis",)


def test_refused_fact_pattern_stays_out_of_scope():
    c = collab(
        product_fmv_paise=rup(30_000),
        brand=Brand(EntityType.COMPANY, in_business=False),
    )
    cert = verify(Claim(), c)
    assert cert.status is Status.OUT_OF_SCOPE
    assert cert.required_fields == ()
