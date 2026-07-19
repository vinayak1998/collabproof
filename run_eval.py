"""
run_eval.py — evaluate answerers against the executable spec.

Two-layer eval hygiene:
  * tests/test_golden.py checks the SPEC against hand-computed statutory
    outcomes (the oracle is human).
  * this file checks ANSWERERS against the spec (the oracle is the spec).

Answerers:
  * naive-baseline : collabproof/baseline.py (the modal misunderstanding).
  * llm            : only runs if ANTHROPIC_API_KEY is set (--llm). This repo
                     ships no LLM numbers it did not actually produce.
"""
import json
import os
import sys
from collections import Counter
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(__file__))

from collabproof import (Brand, Collab, Creator, EntityType, Status, TaxBearer,
                         assess, assessment_as_claim, naive_answer, rup, verify)
from collabproof.llm_adapter import llm_answer


def build_cases() -> list[Collab]:
    company = Brand(EntityType.COMPANY)
    small = Brand(EntityType.INDIVIDUAL,
                  preceding_fy_profession_receipts_paise=rup(40_00_000))
    cases: list[Collab] = []

    # Core grid: product x cash, retained, defaults.
    for product in (0, 15_000, 20_000, 20_001, 21_000, 22_222, 22_223, 30_000, 60_000):
        for cash in (0, 30_000, 50_000):
            cases.append(Collab(brand=company, creator=Creator(),
                                cash_fee_paise=rup(cash),
                                product_fmv_paise=rup(product)))
    # Returned-product variants.
    for product in (15_000, 21_000, 30_000, 60_000):
        cases.append(Collab(brand=company, creator=Creator(),
                            cash_fee_paise=rup(20_000),
                            product_fmv_paise=rup(product), product_retained=False))
    # No-PAN variants.
    for product in (20_000, 21_000, 30_000):
        cases.append(Collab(brand=company, creator=Creator(pan_furnished=False),
                            product_fmv_paise=rup(product)))
    # Small-provider carve-out.
    for product in (21_000, 30_000, 60_000):
        cases.append(Collab(brand=small, creator=Creator(),
                            product_fmv_paise=rup(product)))
    # Prior-benefit aggregation.
    for prior, product in ((10_000, 15_000), (18_000, 2_500), (19_999, 1)):
        cases.append(Collab(brand=company,
                            creator=Creator(fy_prior_benefits_from_brand_paise=rup(prior)),
                            product_fmv_paise=rup(product)))
    # Gross-up (provider bears).
    for product in (18_000, 27_000, 45_000):
        cases.append(Collab(brand=company, creator=Creator(),
                            product_fmv_paise=rup(product),
                            tax_borne_by=TaxBearer.PROVIDER))
    # GST registration boundaries (normal + special states), incl. barter push.
    cases.append(Collab(brand=company,
                        creator=Creator(fy_prior_aggregate_turnover_paise=rup(19_50_000)),
                        cash_fee_paise=rup(30_000), product_fmv_paise=rup(25_000)))
    cases.append(Collab(brand=company,
                        creator=Creator(fy_prior_aggregate_turnover_paise=rup(19_50_000)),
                        cash_fee_paise=rup(30_000), product_fmv_paise=rup(25_000),
                        product_retained=False))
    cases.append(Collab(brand=company,
                        creator=Creator(special_category_state=True,
                                        fy_prior_aggregate_turnover_paise=rup(10_00_000)),
                        cash_fee_paise=rup(30_000), product_fmv_paise=rup(20_000)))
    cases.append(Collab(brand=company,
                        creator=Creator(gst_registered=True,
                                        fy_prior_aggregate_turnover_paise=rup(19_50_000)),
                        cash_fee_paise=rup(50_000), product_fmv_paise=rup(60_000)))
    # Gift with no deliverable: 194R benefit but not GST consideration.
    cases.append(Collab(brand=company, creator=Creator(gst_registered=True),
                        product_fmv_paise=rup(30_000), deliverable_linked=False))
    # Out-of-scope refusal patterns.
    cases.append(Collab(brand=company, creator=Creator(is_resident=False),
                        product_fmv_paise=rup(30_000)))
    cases.append(Collab(brand=Brand(EntityType.COMPANY, in_business=False),
                        product_fmv_paise=rup(30_000), creator=Creator()))
    return cases


FIELDS = ("tds_194r_paise", "release_gate_required", "cash_tds_paise",
          "gst_registration_required", "gst_liability_paise")


def wrong_fields(claim, truth) -> list[str]:
    out = []
    for f in FIELDS:
        cv, tv = getattr(claim, f), getattr(truth, f)
        if f == "cash_tds_paise":
            continue  # fork-sensitive; certifier handles it
        if cv is not None and cv != tv:
            out.append(f)
    return out


def evaluate(name, answer_fn, cases):
    tally = Counter()
    rule_hits = Counter()
    certified_wrong = 0
    rows = []
    for i, c in enumerate(cases):
        claim = answer_fn(c)
        if claim is None:
            return None
        cert = verify(claim, c)
        tally[cert.status.value] += 1
        a = assess(c)
        truth = assessment_as_claim(a) if a.ok else None
        if cert.status == Status.CERTIFIED and truth is not None:
            if wrong_fields(claim, truth):
                certified_wrong += 1
        for m in cert.mismatches:
            rule_hits[m.rule_id] += 1
        rows.append({
            "case": i,
            "status": cert.status.value,
            "mismatches": [m.explain() for m in cert.mismatches],
            "notes": list(cert.notes),
        })
    return {"answerer": name, "n": len(cases), "tally": dict(tally),
            "certified_but_wrong": certified_wrong,
            "rejections_by_rule": dict(rule_hits.most_common()), "rows": rows}


def main():
    cases = build_cases()
    os.makedirs(os.path.join(os.path.dirname(__file__), "eval"), exist_ok=True)

    with open(os.path.join(os.path.dirname(__file__), "eval", "cases.json"), "w") as f:
        json.dump([{**asdict(c),
                    "brand": {**asdict(c.brand), "entity_type": c.brand.entity_type.value},
                    "tax_borne_by": c.tax_borne_by.value} for c in cases], f, indent=1)

    reports = []
    r = evaluate("naive-baseline", naive_answer, cases)
    reports.append(r)

    if "--llm" in sys.argv:
        rl = evaluate("llm(claude)", llm_answer, cases)
        if rl is None:
            print("(--llm requested but ANTHROPIC_API_KEY not set: skipped, "
                  "no numbers invented)")
        else:
            reports.append(rl)

    for rep in reports:
        print(f"\n=== {rep['answerer']}  (n={rep['n']}) ===")
        for k, v in sorted(rep["tally"].items()):
            print(f"  {k:<14} {v}")
        print(f"  certified-but-wrong (must be 0): {rep['certified_but_wrong']}")
        print("  rejections by rule:")
        for rid, n in rep["rejections_by_rule"].items():
            print(f"    {rid:<22} {n}")

    with open(os.path.join(os.path.dirname(__file__), "eval", "results.json"), "w") as f:
        json.dump(reports, f, indent=1)
    print("\nwrote eval/cases.json and eval/results.json")


if __name__ == "__main__":
    main()
