"""Generate Python→JavaScript assessment and certification parity fixtures.

The browser carries a hand-written JavaScript port. These frozen fixtures keep
both its rule assessment *and its fail-closed verifier* mechanically aligned
with the Python source of truth. CI regenerates this file and rejects a diff.
"""
from dataclasses import replace
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from collabproof import (Brand, Claim, Collab, Creator, EntityType, Q,
                         TaxBearer, assess, assessment_as_claim, rup, verify)
from run_eval import build_cases


COMPANY = Brand(EntityType.COMPANY)
SMALL_INDIVIDUAL = Brand(
    EntityType.INDIVIDUAL,
    preceding_fy_profession_receipts_paise=rup(40_00_000),
)

PY_TO_JS_FIELD = {
    "tds_194r_paise": "tds_194r",
    "release_gate_required": "release_gate",
    "cash_tds_paise": "cash_tds",
    "cash_tds_basis": "cash_basis",
    "gst_registration_required": "gst_reg",
    "gst_liability_paise": "gst_liability",
}


def golden_cases() -> list[Collab]:
    creator = Creator
    return [
        Collab(brand=COMPANY, creator=creator(), product_fmv_paise=rup(30_000)),
        Collab(brand=COMPANY, creator=creator(), product_fmv_paise=rup(15_000)),
        Collab(
            brand=COMPANY,
            creator=creator(fy_prior_benefits_from_brand_paise=rup(10_000)),
            product_fmv_paise=rup(15_000),
        ),
        Collab(
            brand=COMPANY,
            creator=creator(),
            product_fmv_paise=rup(30_000),
            product_retained=False,
        ),
        Collab(
            brand=SMALL_INDIVIDUAL,
            creator=creator(),
            product_fmv_paise=rup(30_000),
        ),
        Collab(
            brand=COMPANY,
            creator=creator(pan_furnished=False),
            product_fmv_paise=rup(30_000),
        ),
        Collab(
            brand=COMPANY,
            creator=creator(),
            cash_fee_paise=rup(50_000),
            product_fmv_paise=rup(25_000),
        ),
        Collab(
            brand=COMPANY,
            creator=creator(),
            product_fmv_paise=rup(27_000),
            tax_borne_by=TaxBearer.PROVIDER,
        ),
        Collab(
            brand=COMPANY,
            creator=creator(
                gst_registered=True,
                fy_prior_aggregate_turnover_paise=rup(19_50_000),
            ),
            cash_fee_paise=rup(50_000),
            product_fmv_paise=rup(60_000),
        ),
        Collab(
            brand=COMPANY,
            creator=creator(
                special_category_state=True,
                fy_prior_aggregate_turnover_paise=rup(10_00_000),
            ),
            cash_fee_paise=rup(30_000),
            product_fmv_paise=rup(20_000),
        ),
        Collab(
            brand=COMPANY,
            creator=creator(is_resident=False),
            product_fmv_paise=rup(30_000),
        ),
        Collab(
            brand=Brand(EntityType.COMPANY, in_business=False),
            creator=creator(),
            product_fmv_paise=rup(30_000),
        ),
    ]


def facts_of(c: Collab) -> dict:
    return {
        "brand_entity": c.brand.entity_type.value,
        "brand_business_turnover": c.brand.preceding_fy_business_turnover_paise,
        "brand_profession_receipts": c.brand.preceding_fy_profession_receipts_paise,
        "brand_in_business": c.brand.in_business,
        "resident": c.creator.is_resident,
        "pan": c.creator.pan_furnished,
        "special_state": c.creator.special_category_state,
        "gst_registered": c.creator.gst_registered,
        "prior_benefits": c.creator.fy_prior_benefits_from_brand_paise,
        "prior_194r_tds": c.creator.fy_prior_194r_tds_paise,
        "prior_cash_fees": c.creator.fy_prior_cash_fees_from_brand_paise,
        "prior_cash_tds": c.creator.fy_prior_cash_tds_paise,
        "prior_turnover": c.creator.fy_prior_aggregate_turnover_paise,
        "cash": c.cash_fee_paise,
        "product": c.product_fmv_paise,
        "retained": c.product_retained,
        "deliverable_linked": c.deliverable_linked,
        "bearer": c.tax_borne_by.value,
    }


def assessment_expected(c: Collab) -> dict:
    a = assess(c)
    if not a.ok:
        return {"ok": False, "refusal": a.refusal_rule_id}
    return {
        "ok": True,
        "tds_194r": a.d(Q.TDS_194R),
        "aggregate": a.d(Q.AGGREGATE_BENEFIT),
        "gate": a.d(Q.RELEASE_GATE),
        "fork": {b.basis_rule_id: b.tds_paise for b in a.cash_tds_fork},
        "fork_material": a.fork_material,
        "gst_supply": a.d(Q.GST_SUPPLY_VALUE),
        "gst_turnover_after": a.d(Q.GST_TURNOVER_AFTER),
        "gst_reg": a.d(Q.GST_REG_REQUIRED),
        "gst_liability": a.d(Q.GST_LIABILITY),
    }


def claim_to_js(claim: Claim) -> dict:
    return {
        PY_TO_JS_FIELD[field]: getattr(claim, field)
        for field in claim.checked_fields()
    }


def claim_from_js(raw: dict) -> Claim:
    return Claim(**{
        py_field: raw[js_field]
        for py_field, js_field in PY_TO_JS_FIELD.items()
        if js_field in raw
    })


def verification_expected(c: Collab, raw_claim: dict) -> dict:
    cert = verify(claim_from_js(raw_claim), c)
    return {
        "status": cert.status.value,
        "rule_bundle_hash": cert.rule_bundle_hash,
        "mismatches": [
            {
                "field": PY_TO_JS_FIELD[m.fld],
                "claimed": m.claimed,
                "expected": m.expected,
                "rule": m.rule_id,
            }
            for m in cert.mismatches
        ],
        "required_fields": [PY_TO_JS_FIELD[f] for f in cert.required_fields],
        "checked_fields": [PY_TO_JS_FIELD[f] for f in cert.checked_fields],
        "missing_fields": [PY_TO_JS_FIELD[f] for f in cert.missing_fields],
        "refusal": (
            cert.assessment.refusal_rule_id
            if cert.assessment is not None and not cert.assessment.ok
            else None
        ),
    }


def verification_cases() -> list[tuple[str, Collab, dict]]:
    basic = Collab(brand=COMPANY, creator=Creator(), product_fmv_paise=rup(30_000))
    threshold = Collab(brand=COMPANY, creator=Creator(), product_fmv_paise=rup(21_000))
    material = Collab(
        brand=COMPANY,
        creator=Creator(),
        cash_fee_paise=rup(50_000),
        product_fmv_paise=rup(25_000),
    )
    returned = Collab(
        brand=COMPANY,
        creator=Creator(),
        product_fmv_paise=rup(30_000),
        product_retained=False,
    )
    no_pan = Collab(
        brand=COMPANY,
        creator=Creator(pan_furnished=False),
        product_fmv_paise=rup(30_000),
    )
    registered = Collab(
        brand=COMPANY,
        creator=Creator(gst_registered=True),
        cash_fee_paise=rup(50_000),
        product_fmv_paise=rup(60_000),
    )
    refused = Collab(
        brand=Brand(EntityType.COMPANY, in_business=False),
        creator=Creator(),
        product_fmv_paise=rup(30_000),
    )

    basic_truth = assessment_as_claim(assess(basic))
    threshold_truth = assessment_as_claim(assess(threshold))
    material_truth = assessment_as_claim(assess(material), basis="IT-194J-PROF")
    returned_truth = assessment_as_claim(assess(returned))
    no_pan_truth = assessment_as_claim(assess(no_pan))
    registered_truth = assessment_as_claim(assess(registered), basis="IT-194J-PROF")

    return [
        ("complete-certified-explicit-nulls", basic, claim_to_js(basic_truth)),
        ("empty-is-incomplete", basic, {}),
        ("correct-partial-is-incomplete", basic, {"tds_194r": rup(3_000)}),
        (
            "threshold-error-names-threshold",
            threshold,
            claim_to_js(replace(threshold_truth, tds_194r_paise=rup(100))),
        ),
        (
            "material-null-basis-is-ambiguous",
            material,
            claim_to_js(replace(material_truth, cash_tds_basis=None)),
        ),
        (
            "omitted-material-basis-is-incomplete",
            material,
            {k: v for k, v in claim_to_js(material_truth).items() if k != "cash_basis"},
        ),
        (
            "wrong-cash-branch-names-basis",
            material,
            claim_to_js(replace(material_truth, cash_tds_paise=rup(500))),
        ),
        (
            "returned-product-names-retained-rule",
            returned,
            claim_to_js(replace(returned_truth, tds_194r_paise=rup(3_000))),
        ),
        (
            "no-pan-rate-names-206aa",
            no_pan,
            claim_to_js(replace(no_pan_truth, tds_194r_paise=rup(3_000))),
        ),
        ("registered-complete-certificate", registered, claim_to_js(registered_truth)),
        (
            "gst-rate-mismatch",
            registered,
            claim_to_js(replace(registered_truth, gst_liability_paise=0)),
        ),
        (
            "bool-is-not-money",
            basic,
            claim_to_js(replace(basic_truth, tds_194r_paise=True)),
        ),
        (
            "unknown-basis-is-invalid",
            basic,
            claim_to_js(replace(basic_truth, cash_tds_basis="IT-194A")),
        ),
        ("refused-empty-claim", refused, {}),
        ("refused-asserted-claim", refused, claim_to_js(basic_truth)),
    ]


def main() -> None:
    assessment_cases = build_cases() + golden_cases()
    assessments = [
        {
            "id": f"assessment-{i:02d}",
            "facts": facts_of(c),
            "expected": assessment_expected(c),
        }
        for i, c in enumerate(assessment_cases)
    ]
    verifications = [
        {
            "id": case_id,
            "facts": facts_of(c),
            "claim": raw_claim,
            "expected": verification_expected(c, raw_claim),
        }
        for case_id, c, raw_claim in verification_cases()
    ]
    vectors = {
        "schema_version": 2,
        "assessments": assessments,
        "verifications": verifications,
    }

    out = os.path.join(os.path.dirname(__file__), "docs", "parity_vectors.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("// GENERATED by gen_parity_vectors.py — do not edit by hand.\n")
        f.write("// Python assessment + fail-closed verifier fixtures.\n")
        f.write("window.PARITY_VECTORS = ")
        json.dump(vectors, f, indent=1)
        f.write(";\n")
    print(
        f"wrote {len(assessments)} assessment + {len(verifications)} verifier "
        f"vectors -> docs/parity_vectors.js"
    )


if __name__ == "__main__":
    main()
