"""
collabproof.baseline — the "modal misunderstanding" calculator.

This is NOT a strawman: each bug below is a real, commonly seen reading of
these rules (the excess-over-20k error is endemic in practice). The baseline
answers every case fluently and never refuses — which is exactly the failure
mode the certifier exists to catch.

Documented bugs (relative to the statute):
  B1  s.194R computed on the EXCESS over Rs 20,000, not the aggregate.
  B2  Ignores the retained-vs-returned distinction (Circular 12/2022).
  B3  Ignores the small individual/HUF provider carve-out.
  B4  Ignores s.206AA (always 10%, PAN or not) and prior-FY aggregation.
  B5  Cash TDS: always 194J @10% flat — no threshold, no 194C fork.
  B6  GST registration tested on CASH turnover only (barter invisible),
      always against Rs 20L (special-category states ignored).
  B7  GST charged on the cash component only.
  B8  Never flags the in-kind release gate.
"""
from __future__ import annotations

from .spec import Collab, rup, pct
from .verify import Claim


def naive_answer(c: Collab) -> Claim:
    # B1 + B2 + B3 + B4
    excess = max(0, c.product_fmv_paise - rup(20_000))
    tds_194r = pct(excess, 10)

    # B5
    cash_tds = pct(c.cash_fee_paise, 10)

    # B6
    cash_turnover = (c.creator.fy_prior_aggregate_turnover_paise + c.cash_fee_paise)
    reg_required = cash_turnover > rup(20_00_000)

    # B7
    gst_liability = pct(c.cash_fee_paise, 18) if c.creator.gst_registered else None

    # B8
    return Claim(
        tds_194r_paise=tds_194r,
        release_gate_required=False,
        cash_tds_paise=cash_tds,
        cash_tds_basis=None,
        gst_registration_required=reg_required,
        gst_liability_paise=gst_liability,
    )
