"""
Property-based tests (Hypothesis): invariants that must hold across the whole
input space the strategy can reach — the statistical cousin of the Z3 proofs
in proofs/prove_cliff.py.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from collabproof import (Brand, Collab, Creator, EntityType, Q, Status,
                         TaxBearer, assess, assessment_as_claim, naive_answer,
                         rup, verify)

rupees = st.integers(min_value=0, max_value=5_00_000).map(rup)
turnovers = st.integers(min_value=0, max_value=3_00_00_000).map(rup)

creators = st.builds(
    Creator,
    is_resident=st.just(True),
    pan_furnished=st.booleans(),
    special_category_state=st.booleans(),
    gst_registered=st.booleans(),
    fy_prior_benefits_from_brand_paise=st.integers(0, 1_00_000).map(rup),
    fy_prior_194r_tds_paise=st.just(0),
    fy_prior_cash_fees_from_brand_paise=st.integers(0, 2_00_000).map(rup),
    fy_prior_cash_tds_paise=st.just(0),
    fy_prior_aggregate_turnover_paise=turnovers,
)

brands = st.builds(
    Brand,
    entity_type=st.sampled_from(list(EntityType)),
    preceding_fy_business_turnover_paise=st.integers(0, 5_00_00_000).map(rup),
    preceding_fy_profession_receipts_paise=st.integers(0, 2_00_00_000).map(rup),
    in_business=st.just(True),
)

collabs = st.builds(
    Collab, brand=brands, creator=creators,
    cash_fee_paise=rupees, product_fmv_paise=rupees,
    product_retained=st.booleans(), deliverable_linked=st.booleans(),
    tax_borne_by=st.sampled_from(list(TaxBearer)),
)


@settings(max_examples=500, deadline=None)
@given(collabs)
def test_p1_amounts_nonnegative(c):
    a = assess(c)
    assert a.ok
    assert a.d(Q.TDS_194R) >= 0
    for b in a.cash_tds_fork:
        assert b.tds_paise >= 0


@settings(max_examples=500, deadline=None)
@given(collabs)
def test_p2_threshold_and_returned(c):
    a = assess(c)
    if a.d(Q.AGGREGATE_BENEFIT) <= rup(20_000):
        assert a.d(Q.TDS_194R) == 0          # s.194R first proviso
    if not c.product_retained:
        assert a.d(Q.BENEFIT_QUALIFIES) is False   # Circular 12/2022


@settings(max_examples=300, deadline=None)
@given(collabs)
def test_p3_206aa_doubles_recipient_mode(c):
    """No-PAN rate (20%) is exactly 2x the PAN rate (10%) in recipient-bears
    mode with no prior deductions."""
    if c.tax_borne_by is not TaxBearer.RECIPIENT:
        return
    cr_pan = Creator(**{**c.creator.__dict__, "pan_furnished": True})
    cr_nopan = Creator(**{**c.creator.__dict__, "pan_furnished": False})
    t_pan = assess(Collab(**{**c.__dict__, "creator": cr_pan})).d(Q.TDS_194R)
    t_nopan = assess(Collab(**{**c.__dict__, "creator": cr_nopan})).d(Q.TDS_194R)
    assert t_nopan == 2 * t_pan


@settings(max_examples=500, deadline=None)
@given(collabs)
def test_p4_roundtrip_soundness(c):
    """The certifier must always certify the spec's own assessment
    (completeness of verify w.r.t. assess)."""
    a = assess(c)
    cert = verify(assessment_as_claim(a), c)
    assert cert.status == Status.CERTIFIED


@settings(max_examples=300, deadline=None)
@given(collabs)
def test_p5_grossup_never_cheaper(c):
    """Pyramiding: provider-borne tax >= recipient-borne tax on the same facts."""
    rec = assess(Collab(**{**c.__dict__, "tax_borne_by": TaxBearer.RECIPIENT}))
    prov = assess(Collab(**{**c.__dict__, "tax_borne_by": TaxBearer.PROVIDER}))
    assert prov.d(Q.TDS_194R) >= rec.d(Q.TDS_194R)


@settings(max_examples=300, deadline=None)
@given(collabs, st.integers(1, 50_000).map(rup))
def test_p6_gst_registration_monotone(c, bump):
    """More prior turnover can never un-require registration."""
    a1 = assess(c)
    cr2 = Creator(**{**c.creator.__dict__,
                     "fy_prior_aggregate_turnover_paise":
                         c.creator.fy_prior_aggregate_turnover_paise + bump})
    a2 = assess(Collab(**{**c.__dict__, "creator": cr2}))
    assert a2.d(Q.GST_REG_REQUIRED) >= a1.d(Q.GST_REG_REQUIRED)


@settings(max_examples=500, deadline=None)
@given(collabs)
def test_p7_certifier_soundness_vs_baseline(c):
    """THE core guarantee: whenever the naive baseline disagrees with the spec
    on any asserted field, the certifier must NOT certify it. Zero
    confidently-wrong certified answers, by construction."""
    a = assess(c)
    cert = verify(naive_answer(c), c)
    if cert.status == Status.CERTIFIED:
        truth = assessment_as_claim(a)
        nb = naive_answer(c)
        assert nb.tds_194r_paise == truth.tds_194r_paise
        assert nb.gst_registration_required == truth.gst_registration_required
        assert nb.release_gate_required == truth.release_gate_required
