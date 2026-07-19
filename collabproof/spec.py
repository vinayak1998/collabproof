"""
collabproof.spec — executable formalization of the Indian tax rules governing
brand <-> creator barter collaborations.

VERSION PIN (spec drift is real; this encoding is only meaningful versioned):
  * Income-tax Act, 1961 as amended through Finance (No. 2) Act, 2024
    — i.e., FY 2024-25 / AY 2025-26 figures.
  * CBDT Circular 12/2022 (16-Jun-2022) and 18/2022 (13-Sep-2022) for s.194R.
  * CGST Act, 2017 + CGST Rules, 2017 (Rule 27).
  NOT modeled: Finance Act 2025 threshold revisions, the Income-tax Act 2025
  renumbering, s.288B rounding, ss.206AB (omitted w.e.f. 2025), 195 (non-resident
  payees), GST time-of-supply/ITC/RCM/TCS-through-ECO. See README "Limitations".

DESIGN CHOICES (deliberate contrasts with prior art):
  * All money is integer PAISE (exact arithmetic; no Float in the rule layer).
  * Statutory ambiguity is a first-class output: where two sections plausibly
    govern the same cash payment (194J vs 194C), the spec returns BOTH branches
    with their statutory basis, plus a computed `fork_material` flag.
  * Procedural obligations (the s.194R in-kind release gate) are outputs,
    not just numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ----------------------------------------------------------------------------
# Money helpers — integer paise everywhere.
# ----------------------------------------------------------------------------
PAISE_PER_RUPEE = 100


def rup(rupees: int) -> int:
    """Whole rupees -> paise."""
    return rupees * PAISE_PER_RUPEE


def pct(amount_paise: int, numerator: int, denominator: int = 100) -> int:
    """Exact percentage of a paise amount; round half up to the paisa."""
    q, r = divmod(amount_paise * numerator, denominator)
    return q + (1 if 2 * r >= denominator else 0)


# ----------------------------------------------------------------------------
# Statutory constants (FY 2024-25).
# ----------------------------------------------------------------------------
S194R_RATE_NUM = 10                 # s.194R(1): 10%
S206AA_RATE_NUM = 20                # s.206AA: 20% where PAN not furnished
S194R_FY_THRESHOLD = rup(20_000)    # s.194R(1) proviso 1: aggregate <= 20,000 -> no TDS
S194R_CARVEOUT_BUSINESS = rup(1_00_00_000)   # proviso 2: individual/HUF, preceding-FY
S194R_CARVEOUT_PROFESSION = rup(50_00_000)   # turnover <= 1cr (business) / 50L (profession)

S194J_RATE_NUM = 10                 # s.194J(1): 10% (professional services)
S194J_FY_THRESHOLD = rup(30_000)    # s.194J(1) proviso: aggregate <= 30,000 -> no TDS

S194C_RATE_INDIVIDUAL_NUM = 1       # s.194C(1)(i): 1% payee individual/HUF
S194C_RATE_OTHER_NUM = 2            # s.194C(1)(ii): 2% otherwise
S194C_SINGLE_THRESHOLD = rup(30_000)     # s.194C(5): single credit > 30,000
S194C_FY_THRESHOLD = rup(1_00_000)       # s.194C(5) proviso: FY aggregate > 1,00,000

GST_REG_THRESHOLD_NORMAL = rup(20_00_000)   # s.22(1) CGST: services, normal states
GST_REG_THRESHOLD_SPECIAL = rup(10_00_000)  # s.22(1) proviso: special category states
GST_RATE_NUM = 18                           # advertising/marketing services (SAC 9983): 18%


# ----------------------------------------------------------------------------
# Rule registry — every determination cites one of these.
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    rule_id: str
    citation: str
    text: str


RULES: dict[str, Rule] = {r.rule_id: r for r in [
    Rule("IT-194R-SCOPE",
         "s.194R(1), Income-tax Act 1961",
         "Person providing any benefit/perquisite arising from business or the "
         "exercise of a profession to a RESIDENT shall deduct 10% of its value."),
    Rule("IT-194R-RETAINED",
         "CBDT Circular 12/2022, Q&A on influencer products",
         "Product given to a social-media influencer for promotion: a benefit/"
         "perquisite IF RETAINED by the influencer; NOT a benefit if returned "
         "after rendering the service."),
    Rule("IT-194R-THRESHOLD",
         "s.194R(1), first proviso",
         "No deduction where the value/aggregate value of benefits provided to "
         "the resident during the FY does not exceed Rs 20,000. Once exceeded, "
         "tax applies to the aggregate value, not the excess."),
    Rule("IT-194R-CARVEOUT",
         "s.194R, second proviso",
         "Section does not apply to an individual/HUF provider whose preceding-FY "
         "turnover <= Rs 1 crore (business) or gross receipts <= Rs 50 lakh "
         "(profession)."),
    Rule("IT-194R-GROSSUP",
         "CBDT Circular 12/2022, Q9 (pyramiding)",
         "Where the provider bears the tax on an in-kind benefit, the tax so paid "
         "is itself a benefit: t = v*r/(1-r)."),
    Rule("IT-194R-RELEASEGATE",
         "CBDT Circular 12/2022 (in-kind benefits)",
         "For benefits wholly/partly in kind, the provider must ensure tax has "
         "been paid (recipient advance-tax evidence, or provider deposit) BEFORE "
         "releasing the benefit."),
    Rule("IT-206AA",
         "s.206AA, Income-tax Act 1961",
         "Payee has not furnished PAN: deduct at the higher of the applicable "
         "rate or 20%."),
    Rule("IT-194J-PROF",
         "s.194J(1) r/w Explanation (a)",
         "Fees for professional services @10%; 'professional services' expressly "
         "includes the profession of ADVERTISING. No TDS if FY aggregate <= "
         "Rs 30,000."),
    Rule("IT-194C-WORK",
         "s.194C r/w Explanation (iv)(a); CBDT Circular 715/1995",
         "'Work' expressly includes ADVERTISING; 1% (payee individual/HUF) / 2% "
         "(others). Triggered by single credit > Rs 30,000 or FY aggregate > "
         "Rs 1,00,000."),
    Rule("IT-FORK-JvC",
         "s.194J Expl.(a) vs s.194C Expl.(iv)(a) — unresolved overlap",
         "Influencer promotional content is 'advertising' under BOTH sections. "
         "The statute does not resolve which governs a direct brand->creator "
         "payment. This spec refuses to pick: it returns both branches."),
    Rule("GST-SUPPLY-BARTER",
         "s.7(1)(a), CGST Act 2017",
         "'Supply' includes barter and exchange made for consideration in the "
         "course of business."),
    Rule("GST-CONSIDERATION",
         "s.2(31), CGST Act 2017",
         "Consideration includes any payment in money OR OTHERWISE. A product "
         "transferred against a promotion deliverable is consideration; a pure "
         "gratuitous transfer with no reciprocal obligation is not."),
    Rule("GST-VALUE-RULE27",
         "Rule 27(a), CGST Rules 2017",
         "Where consideration is not wholly in money, the value of the supply is "
         "the open market value."),
    Rule("GST-REG-THRESHOLD",
         "s.22(1), CGST Act 2017",
         "Registration required in the State from which taxable supplies are made "
         "if aggregate turnover in a FY exceeds Rs 20 lakh (Rs 10 lakh in special "
         "category states: Manipur, Mizoram, Nagaland, Tripura). Aggregate "
         "turnover (s.2(6)) includes the value of ALL taxable supplies — "
         "including barter consideration at open market value."),
    Rule("GST-RATE-18",
         "Notification 11/2017-CT(Rate), SAC 9983 (advertising/marketing)",
         "Influencer promotional services taxed at 18%."),
    Rule("SCOPE-RESIDENT",
         "s.194R applies to residents; s.195 governs non-residents",
         "Non-resident recipient: outside this spec. REFUSE rather than guess."),
    Rule("SCOPE-BUSINESS-NEXUS",
         "s.194R requires benefit 'arising from business or profession'; "
         "cf. s.56(2)(x) for pure gifts",
         "A transfer with no business nexus is not a s.194R event. REFUSE and "
         "point to the correct regime."),
]}


# ----------------------------------------------------------------------------
# Domain model.
# ----------------------------------------------------------------------------
class EntityType(Enum):
    INDIVIDUAL = "individual"
    HUF = "huf"
    FIRM = "firm"
    COMPANY = "company"


class TaxBearer(Enum):
    RECIPIENT = "recipient"   # creator pays advance tax / cash-component withholding
    PROVIDER = "provider"     # brand grosses up (Circular 12/2022 Q9)


@dataclass(frozen=True)
class Brand:
    entity_type: EntityType
    # preceding-FY figures, for the s.194R second-proviso carve-out
    preceding_fy_business_turnover_paise: int = 0
    preceding_fy_profession_receipts_paise: int = 0
    in_business: bool = True          # False => no business nexus (pure gift)


@dataclass(frozen=True)
class Creator:
    is_resident: bool = True
    pan_furnished: bool = True
    special_category_state: bool = False   # Manipur/Mizoram/Nagaland/Tripura
    gst_registered: bool = False
    # FY running aggregates BEFORE this collab:
    fy_prior_benefits_from_brand_paise: int = 0     # s.194R aggregate
    fy_prior_194r_tds_paise: int = 0
    fy_prior_cash_fees_from_brand_paise: int = 0    # s.194J/194C aggregates
    fy_prior_cash_tds_paise: int = 0
    fy_prior_aggregate_turnover_paise: int = 0      # s.2(6) CGST, all clients


@dataclass(frozen=True)
class Collab:
    brand: Brand
    creator: Creator
    cash_fee_paise: int = 0
    product_fmv_paise: int = 0        # open market value / purchase price
    product_retained: bool = True     # False => returned after campaign
    deliverable_linked: bool = True   # product is consideration for a deliverable
    tax_borne_by: TaxBearer = TaxBearer.RECIPIENT


# ----------------------------------------------------------------------------
# Output model — a determination per question, each carrying its rule trail.
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Determination:
    question: str
    value: object                     # int paise | bool | None
    rule_ids: tuple[str, ...]
    note: str = ""

    def citations(self) -> list[str]:
        return [RULES[r].citation for r in self.rule_ids]


@dataclass(frozen=True)
class ForkBranch:
    basis_rule_id: str                # IT-194J-PROF or IT-194C-WORK
    tds_paise: int


@dataclass(frozen=True)
class Assessment:
    ok: bool
    refusal_rule_id: Optional[str] = None
    refusal_note: str = ""
    determinations: dict[str, Determination] = field(default_factory=dict)
    cash_tds_fork: tuple[ForkBranch, ...] = ()
    fork_material: bool = False

    def d(self, key: str):
        return self.determinations[key].value


class Q:
    """Determination keys (the questions the spec answers)."""
    BENEFIT_QUALIFIES = "194r_benefit_qualifies"
    PROVIDER_OBLIGATED = "194r_provider_obligated"
    AGGREGATE_BENEFIT = "194r_fy_aggregate_paise"
    TDS_194R = "194r_tds_due_now_paise"
    RELEASE_GATE = "194r_release_gate_required"
    GST_SUPPLY_VALUE = "gst_supply_value_paise"
    GST_TURNOVER_AFTER = "gst_aggregate_turnover_after_paise"
    GST_REG_REQUIRED = "gst_registration_required"
    GST_LIABILITY = "gst_liability_paise"


# ----------------------------------------------------------------------------
# The assessor — assess() is the "compiler front half": facts -> determinations.
# ----------------------------------------------------------------------------
def _refuse(rule_id: str, note: str) -> Assessment:
    return Assessment(ok=False, refusal_rule_id=rule_id, refusal_note=note)


def assess(c: Collab) -> Assessment:
    # ---- input validity ----
    if c.cash_fee_paise < 0 or c.product_fmv_paise < 0:
        return _refuse("IT-194R-SCOPE", "Negative amounts are not a valid fact pattern.")

    # ---- scope gates: refuse rather than guess ----
    if not c.creator.is_resident:
        return _refuse("SCOPE-RESIDENT",
                       "Recipient is non-resident: s.195 territory, outside this spec.")
    if not c.brand.in_business:
        return _refuse("SCOPE-BUSINESS-NEXUS",
                       "No business nexus: not a s.194R event; see s.56(2)(x).")

    det: dict[str, Determination] = {}

    # ---- s.194R chain ----
    benefit_qualifies = c.product_fmv_paise > 0 and c.product_retained
    det[Q.BENEFIT_QUALIFIES] = Determination(
        Q.BENEFIT_QUALIFIES, benefit_qualifies,
        ("IT-194R-SCOPE", "IT-194R-RETAINED"),
        "Returned product is not a benefit (Circular 12/2022)." if not benefit_qualifies
        and c.product_fmv_paise > 0 else "")

    small_provider = (
        c.brand.entity_type in (EntityType.INDIVIDUAL, EntityType.HUF)
        and c.brand.preceding_fy_business_turnover_paise <= S194R_CARVEOUT_BUSINESS
        and c.brand.preceding_fy_profession_receipts_paise <= S194R_CARVEOUT_PROFESSION
    )
    provider_obligated = not small_provider
    det[Q.PROVIDER_OBLIGATED] = Determination(
        Q.PROVIDER_OBLIGATED, provider_obligated, ("IT-194R-CARVEOUT",),
        "" if provider_obligated else
        "Small individual/HUF provider: no s.194R obligation. Creator remains "
        "taxable on the benefit under s.28(iv); only the deduction duty lapses.")

    rate_num = S206AA_RATE_NUM if not c.creator.pan_furnished else S194R_RATE_NUM
    rate_rules = ("IT-206AA",) if not c.creator.pan_furnished else ()

    benefit_value = c.product_fmv_paise if benefit_qualifies else 0
    if benefit_value and provider_obligated and c.tax_borne_by is TaxBearer.PROVIDER:
        # Circular 12/2022 Q9: tax borne by provider is itself a benefit.
        grossup_tax_total = pct(benefit_value, rate_num, 100 - rate_num)
    else:
        grossup_tax_total = 0
    aggregate = c.creator.fy_prior_benefits_from_brand_paise + benefit_value + grossup_tax_total
    det[Q.AGGREGATE_BENEFIT] = Determination(
        Q.AGGREGATE_BENEFIT, aggregate,
        ("IT-194R-THRESHOLD",) + (("IT-194R-GROSSUP",) if grossup_tax_total else ()),
        "Provider-borne tax counts toward the aggregate (modeling choice; "
        "pyramiding per Circular 12/2022 Q9)." if grossup_tax_total else "")

    if not provider_obligated or aggregate <= S194R_FY_THRESHOLD or benefit_value == 0:
        tds_194r = 0
        tds_rules = ("IT-194R-THRESHOLD", "IT-194R-CARVEOUT")
    elif c.tax_borne_by is TaxBearer.PROVIDER:
        # total tax on grossed aggregate benefit borne by provider
        prior = c.creator.fy_prior_benefits_from_brand_paise
        total = pct(prior + benefit_value, rate_num, 100 - rate_num)
        tds_194r = max(0, total - c.creator.fy_prior_194r_tds_paise)
        tds_rules = ("IT-194R-SCOPE", "IT-194R-GROSSUP") + rate_rules
    else:
        total = pct(aggregate, rate_num)
        tds_194r = max(0, total - c.creator.fy_prior_194r_tds_paise)
        tds_rules = ("IT-194R-SCOPE", "IT-194R-THRESHOLD") + rate_rules
    det[Q.TDS_194R] = Determination(Q.TDS_194R, tds_194r, tds_rules)

    release_gate = tds_194r > 0 and benefit_value > 0
    det[Q.RELEASE_GATE] = Determination(
        Q.RELEASE_GATE, release_gate, ("IT-194R-RELEASEGATE",),
        "Before handing over the product: collect advance-tax evidence from the "
        "creator, or deposit the tax (gross-up)." if release_gate else "")

    # ---- cash component: the 194J vs 194C interpretive fork ----
    fork: list[ForkBranch] = []
    fork_material = False
    if c.cash_fee_paise >= 0:
        cash_agg = c.creator.fy_prior_cash_fees_from_brand_paise + c.cash_fee_paise
        cash_rate_j = S206AA_RATE_NUM if not c.creator.pan_furnished else S194J_RATE_NUM
        cash_rate_c = (S206AA_RATE_NUM if not c.creator.pan_furnished
                       else S194C_RATE_INDIVIDUAL_NUM)  # payee (creator) is individual
        # 194J branch
        base_j = cash_agg if cash_agg > S194J_FY_THRESHOLD else 0
        tds_j = max(0, pct(base_j, cash_rate_j) - c.creator.fy_prior_cash_tds_paise)
        # 194C branch
        if cash_agg > S194C_FY_THRESHOLD:
            base_c = cash_agg
        elif c.cash_fee_paise > S194C_SINGLE_THRESHOLD:
            base_c = c.cash_fee_paise
        else:
            base_c = 0
        tds_c = max(0, pct(base_c, cash_rate_c) - c.creator.fy_prior_cash_tds_paise)
        fork = [ForkBranch("IT-194J-PROF", tds_j), ForkBranch("IT-194C-WORK", tds_c)]
        fork_material = tds_j != tds_c

    # ---- GST chain ----
    product_is_consideration = (c.product_fmv_paise > 0 and c.product_retained
                                and c.deliverable_linked)
    supply_value = c.cash_fee_paise + (c.product_fmv_paise if product_is_consideration else 0)
    det[Q.GST_SUPPLY_VALUE] = Determination(
        Q.GST_SUPPLY_VALUE, supply_value,
        ("GST-SUPPLY-BARTER", "GST-CONSIDERATION", "GST-VALUE-RULE27"),
        "Product enters supply value at open market value (Rule 27(a))."
        if product_is_consideration else
        ("Retained product NOT linked to a deliverable: 194R benefit but not GST "
         "consideration — the two statutes value the same object differently."
         if c.product_fmv_paise > 0 and c.product_retained else ""))

    turnover_after = c.creator.fy_prior_aggregate_turnover_paise + supply_value
    det[Q.GST_TURNOVER_AFTER] = Determination(
        Q.GST_TURNOVER_AFTER, turnover_after, ("GST-REG-THRESHOLD",))

    threshold = (GST_REG_THRESHOLD_SPECIAL if c.creator.special_category_state
                 else GST_REG_THRESHOLD_NORMAL)
    reg_required = turnover_after > threshold
    det[Q.GST_REG_REQUIRED] = Determination(
        Q.GST_REG_REQUIRED, reg_required, ("GST-REG-THRESHOLD",),
        ("Already registered." if c.creator.gst_registered else
         "Obligation to register arises now.") if reg_required else "")

    gst_liability = pct(supply_value, GST_RATE_NUM) if c.creator.gst_registered else None
    det[Q.GST_LIABILITY] = Determination(
        Q.GST_LIABILITY, gst_liability,
        ("GST-RATE-18", "GST-VALUE-RULE27"),
        "" if c.creator.gst_registered else
        "Not registered: no charge computed. If registration is required and not "
        "taken, exposure arises on the whole supply — flagged, not guessed.")

    return Assessment(ok=True, determinations=det,
                      cash_tds_fork=tuple(fork), fork_material=fork_material)
