"""
Golden tests: 10 fact patterns computed BY HAND from the statute/circulars,
independently of the spec code. These test the spec itself (the eval set in
eval/cases.json, by contrast, uses the spec as oracle to test answerers —
keeping those two layers distinct is basic eval hygiene).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collabproof import (Brand, Collab, Creator, EntityType, Q, TaxBearer,
                         assess, rup)

COMPANY = Brand(EntityType.COMPANY)
SMALL_INDIVIDUAL = Brand(EntityType.INDIVIDUAL,
                         preceding_fy_profession_receipts_paise=rup(40_00_000))


def C(**kw):  # creator with defaults
    return Creator(**kw)


def collab(**kw):
    kw.setdefault("brand", COMPANY)
    kw.setdefault("creator", C())
    return Collab(**kw)


# G1: pure barter, Rs 30,000 retained, company brand -> TDS 3,000; gate on.
def test_g1_basic_in_kind():
    a = assess(collab(product_fmv_paise=rup(30_000)))
    assert a.ok
    assert a.d(Q.TDS_194R) == rup(3_000)
    assert a.d(Q.RELEASE_GATE) is True


# G2: Rs 15,000 retained, no priors -> under threshold, zero, no gate.
def test_g2_under_threshold():
    a = assess(collab(product_fmv_paise=rup(15_000)))
    assert a.d(Q.TDS_194R) == 0
    assert a.d(Q.RELEASE_GATE) is False


# G3: Rs 15,000 now + Rs 10,000 prior benefits -> aggregate 25,000 crosses;
# TDS due on the AGGREGATE (2,500), not the excess (500).
def test_g3_aggregate_crossing():
    a = assess(collab(product_fmv_paise=rup(15_000),
                      creator=C(fy_prior_benefits_from_brand_paise=rup(10_000))))
    assert a.d(Q.AGGREGATE_BENEFIT) == rup(25_000)
    assert a.d(Q.TDS_194R) == rup(2_500)


# G4: Rs 30,000 product RETURNED -> not a benefit (Circular 12/2022); zero.
def test_g4_returned_product():
    a = assess(collab(product_fmv_paise=rup(30_000), product_retained=False))
    assert a.d(Q.BENEFIT_QUALIFIES) is False
    assert a.d(Q.TDS_194R) == 0
    assert a.d(Q.GST_SUPPLY_VALUE) == 0   # returned product isn't consideration


# G5: small individual provider (profession receipts 40L <= 50L) -> carve-out.
def test_g5_small_provider_carveout():
    a = assess(collab(brand=SMALL_INDIVIDUAL, product_fmv_paise=rup(30_000)))
    assert a.d(Q.PROVIDER_OBLIGATED) is False
    assert a.d(Q.TDS_194R) == 0


# G6: no PAN -> s.206AA 20% -> 6,000 on Rs 30,000.
def test_g6_no_pan_206aa():
    a = assess(collab(product_fmv_paise=rup(30_000), creator=C(pan_furnished=False)))
    assert a.d(Q.TDS_194R) == rup(6_000)


# G7: cash 50,000 + product 25,000. 194R = 2,500. Cash fork is MATERIAL:
# 194J @10% = 5,000 vs 194C @1% (individual payee) = 500.
def test_g7_interpretive_fork():
    a = assess(collab(cash_fee_paise=rup(50_000), product_fmv_paise=rup(25_000)))
    assert a.d(Q.TDS_194R) == rup(2_500)
    branches = {b.basis_rule_id: b.tds_paise for b in a.cash_tds_fork}
    assert branches == {"IT-194J-PROF": rup(5_000), "IT-194C-WORK": rup(500)}
    assert a.fork_material is True


# G8: provider bears the tax on Rs 27,000 in kind -> pyramiding (Circ. Q9):
# t = 27,000 * 0.1/0.9 = 3,000 exactly; grossed benefit 30,000 crosses threshold.
def test_g8_grossup_pyramiding():
    a = assess(collab(product_fmv_paise=rup(27_000), tax_borne_by=TaxBearer.PROVIDER))
    assert a.d(Q.AGGREGATE_BENEFIT) == rup(30_000)
    assert a.d(Q.TDS_194R) == rup(3_000)


# G9: composition. cash 50,000 + product 60,000; creator GST-registered with
# prior turnover 19,50,000. 194R = 6,000; GST value = 1,10,000 -> 18% = 19,800;
# turnover after = 20,60,000.
def test_g9_cross_statute_composition():
    a = assess(collab(cash_fee_paise=rup(50_000), product_fmv_paise=rup(60_000),
                      creator=C(gst_registered=True,
                                fy_prior_aggregate_turnover_paise=rup(19_50_000))))
    assert a.d(Q.TDS_194R) == rup(6_000)
    assert a.d(Q.GST_SUPPLY_VALUE) == rup(1_10_000)
    assert a.d(Q.GST_LIABILITY) == rup(19_800)
    assert a.d(Q.GST_TURNOVER_AFTER) == rup(20_60_000)
    assert a.d(Q.GST_REG_REQUIRED) is True   # already registered; threshold exceeded


# G10: boundaries, everywhere at once. Special-category state, prior turnover
# exactly 10,00,000; cash exactly 30,000; product exactly 20,000.
#  - 194R: aggregate exactly 20,000 does NOT exceed threshold -> 0.
#  - 194J: aggregate exactly 30,000 does NOT exceed 30,000 -> 0.
#  - 194C: single exactly 30,000 does NOT exceed 30,000; agg <= 1L -> 0.
#    (fork branches agree -> immaterial)
#  - GST: turnover after = 10,50,000 EXCEEDS 10,00,000 -> registration required.
def test_g10_boundary_stack():
    a = assess(collab(cash_fee_paise=rup(30_000), product_fmv_paise=rup(20_000),
                      creator=C(special_category_state=True,
                                fy_prior_aggregate_turnover_paise=rup(10_00_000))))
    assert a.d(Q.TDS_194R) == 0
    assert {b.tds_paise for b in a.cash_tds_fork} == {0}
    assert a.fork_material is False
    assert a.d(Q.GST_REG_REQUIRED) is True


# Refusals: the spec refuses rather than guesses.
def test_refusal_non_resident():
    a = assess(collab(product_fmv_paise=rup(30_000), creator=C(is_resident=False)))
    assert not a.ok and a.refusal_rule_id == "SCOPE-RESIDENT"


def test_refusal_no_business_nexus():
    a = assess(collab(product_fmv_paise=rup(30_000),
                      brand=Brand(EntityType.COMPANY, in_business=False)))
    assert not a.ok and a.refusal_rule_id == "SCOPE-BUSINESS-NEXUS"
