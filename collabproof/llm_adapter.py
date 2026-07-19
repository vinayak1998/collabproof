"""
collabproof.llm_adapter — pluggable LLM answerer slot.

INTEGRITY NOTE: this repo publishes NO LLM accuracy numbers, because no LLM
was called during its construction (no API key in the build environment).
Run `python run_eval.py --llm` with ANTHROPIC_API_KEY set to produce them.
Everything reported in README.md was actually executed.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .spec import Collab, Q, TaxBearer
from .verify import Claim

PROMPT = """You are advising on Indian tax treatment of a brand-creator collaboration
(FY 2024-25, Income-tax Act 1961 + CGST Act 2017). Facts:

{facts}

Answer with ONLY a JSON object, amounts in integer paise:
{{"tds_194r_paise": int, "release_gate_required": bool,
  "cash_tds_paise": int, "cash_tds_basis": "IT-194J-PROF"|"IT-194C-WORK"|null,
  "gst_registration_required": bool, "gst_liability_paise": int|null}}"""


def facts_of(c: Collab) -> str:
    return json.dumps({
        "brand_entity": c.brand.entity_type.value,
        "brand_preceding_fy_business_turnover_paise": c.brand.preceding_fy_business_turnover_paise,
        "brand_preceding_fy_profession_receipts_paise": c.brand.preceding_fy_profession_receipts_paise,
        "creator_resident": c.creator.is_resident,
        "creator_pan_furnished": c.creator.pan_furnished,
        "creator_special_category_state": c.creator.special_category_state,
        "creator_gst_registered": c.creator.gst_registered,
        "fy_prior_benefits_from_brand_paise": c.creator.fy_prior_benefits_from_brand_paise,
        "fy_prior_194r_tds_paise": c.creator.fy_prior_194r_tds_paise,
        "fy_prior_cash_fees_from_brand_paise": c.creator.fy_prior_cash_fees_from_brand_paise,
        "fy_prior_aggregate_turnover_paise": c.creator.fy_prior_aggregate_turnover_paise,
        "cash_fee_paise": c.cash_fee_paise,
        "product_fmv_paise": c.product_fmv_paise,
        "product_retained": c.product_retained,
        "deliverable_linked": c.deliverable_linked,
        "tax_borne_by": c.tax_borne_by.value,
    }, indent=2)


def llm_answer(c: Collab, model: str = "claude-sonnet-5") -> Optional[Claim]:
    """Returns None if no API key is configured (the eval then skips the LLM row)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import urllib.request
    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": PROMPT.format(facts=facts_of(c))}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    text = out["content"][0]["text"]
    text = text[text.find("{"): text.rfind("}") + 1]
    d = json.loads(text)
    return Claim(
        tds_194r_paise=d.get("tds_194r_paise"),
        release_gate_required=d.get("release_gate_required"),
        cash_tds_paise=d.get("cash_tds_paise"),
        cash_tds_basis=d.get("cash_tds_basis"),
        gst_registration_required=d.get("gst_registration_required"),
        gst_liability_paise=d.get("gst_liability_paise"),
    )
