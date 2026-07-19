/* collabproof.js — JavaScript port of collabproof/spec.py (FY 2024-25 pin).
 * All money in integer paise. This port is held to the Python spec by
 * parity_vectors.js (generated from Python); the page refuses to show a green
 * badge unless every vector matches. Same rules, same citations, same refusals.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.collabproof = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const rup = (r) => r * 100;

  // pct: exact percentage of paise, round half up (mirrors spec.pct)
  function pct(amount, num, den = 100) {
    const total = amount * num;
    const q = Math.floor(total / den);
    const r = total - q * den;
    return q + (2 * r >= den ? 1 : 0);
  }

  const K = {
    S194R_RATE: 10, S206AA_RATE: 20,
    S194R_FY_THRESHOLD: rup(20000),
    S194R_CARVEOUT_BUSINESS: rup(10000000),
    S194R_CARVEOUT_PROFESSION: rup(5000000),
    S194J_RATE: 10, S194J_FY_THRESHOLD: rup(30000),
    S194C_RATE_INDIVIDUAL: 1, S194C_SINGLE: rup(30000), S194C_FY: rup(100000),
    GST_NORMAL: rup(2000000), GST_SPECIAL: rup(1000000), GST_RATE: 18,
  };

  const RULES = {
    "IT-194R-SCOPE": "s.194R(1), Income-tax Act 1961 — 10% TDS on benefits/perquisites to residents arising from business/profession",
    "IT-194R-RETAINED": "CBDT Circular 12/2022 — influencer product retained = benefit; returned = not a benefit",
    "IT-194R-THRESHOLD": "s.194R(1) first proviso — FY aggregate ≤ ₹20,000: no TDS; once exceeded, TDS on the whole aggregate",
    "IT-194R-CARVEOUT": "s.194R second proviso — small individual/HUF provider (≤ ₹1cr business / ₹50L profession, preceding FY) exempt from deducting",
    "IT-194R-GROSSUP": "CBDT Circular 12/2022 Q9 — provider-borne tax is itself a benefit: t = v·r/(1−r)",
    "IT-194R-RELEASEGATE": "CBDT Circular 12/2022 — in-kind benefit: ensure tax is paid BEFORE releasing the product",
    "IT-206AA": "s.206AA — no PAN: deduct at 20%",
    "IT-194J-PROF": "s.194J(1) r/w Expl.(a) — professional services incl. ADVERTISING @10%; FY aggregate ≤ ₹30,000 exempt",
    "IT-194C-WORK": "s.194C r/w Expl.(iv)(a) — 'work' incl. ADVERTISING @1% (individual payee); single > ₹30,000 or FY > ₹1,00,000",
    "IT-FORK-JvC": "s.194J vs s.194C — both expressly cover advertising; the statute does not resolve which governs. Spec returns both branches.",
    "GST-SUPPLY-BARTER": "s.7(1)(a) CGST — supply includes barter/exchange",
    "GST-CONSIDERATION": "s.2(31) CGST — consideration includes payment 'or otherwise'",
    "GST-VALUE-RULE27": "Rule 27(a) CGST Rules — consideration not wholly in money: open market value",
    "GST-REG-THRESHOLD": "s.22(1) CGST — register if aggregate turnover > ₹20L (₹10L special-category states); turnover includes barter OMV",
    "GST-RATE-18": "SAC 9983 advertising/marketing — 18%",
    "SCOPE-RESIDENT": "Non-resident recipient → s.195 territory. REFUSED rather than guessed.",
    "SCOPE-BUSINESS-NEXUS": "No business nexus → not a s.194R event (see s.56(2)(x)). REFUSED rather than guessed.",
  };

  const DEFAULTS = {
    brand_entity: "company", brand_business_turnover: 0,
    brand_profession_receipts: 0, brand_in_business: true,
    resident: true, pan: true, special_state: false, gst_registered: false,
    prior_benefits: 0, prior_194r_tds: 0, prior_cash_fees: 0, prior_cash_tds: 0,
    prior_turnover: 0, cash: 0, product: 0, retained: true,
    deliverable_linked: true, bearer: "recipient",
  };

  function assess(input) {
    const c = Object.assign({}, DEFAULTS, input);

    if (c.cash < 0 || c.product < 0)
      return { ok: false, refusal: "IT-194R-SCOPE",
               note: "Negative amounts are not a valid fact pattern." };
    if (!c.resident)
      return { ok: false, refusal: "SCOPE-RESIDENT",
               note: "Recipient is non-resident: s.195 territory, outside this spec." };
    if (!c.brand_in_business)
      return { ok: false, refusal: "SCOPE-BUSINESS-NEXUS",
               note: "No business nexus: not a s.194R event; see s.56(2)(x)." };

    const notes = [];

    // s.194R chain
    const benefitQualifies = c.product > 0 && c.retained;
    if (c.product > 0 && !c.retained)
      notes.push(["IT-194R-RETAINED", "Returned product is not a benefit."]);

    const smallProvider =
      (c.brand_entity === "individual" || c.brand_entity === "huf") &&
      c.brand_business_turnover <= K.S194R_CARVEOUT_BUSINESS &&
      c.brand_profession_receipts <= K.S194R_CARVEOUT_PROFESSION;
    const providerObligated = !smallProvider;
    if (!providerObligated)
      notes.push(["IT-194R-CARVEOUT",
        "Small individual/HUF provider: no deduction duty (creator still taxable u/s 28(iv))."]);

    const rate = c.pan ? K.S194R_RATE : K.S206AA_RATE;
    const benefitValue = benefitQualifies ? c.product : 0;

    let grossupTotal = 0;
    if (benefitValue && providerObligated && c.bearer === "provider")
      grossupTotal = pct(benefitValue, rate, 100 - rate);
    const aggregate = c.prior_benefits + benefitValue + grossupTotal;

    let tds194r, tdsRules;
    if (!providerObligated || aggregate <= K.S194R_FY_THRESHOLD || benefitValue === 0) {
      tds194r = 0;
      tdsRules = ["IT-194R-THRESHOLD", "IT-194R-CARVEOUT"];
    } else if (c.bearer === "provider") {
      const total = pct(c.prior_benefits + benefitValue, rate, 100 - rate);
      tds194r = Math.max(0, total - c.prior_194r_tds);
      tdsRules = ["IT-194R-SCOPE", "IT-194R-GROSSUP"].concat(c.pan ? [] : ["IT-206AA"]);
    } else {
      const total = pct(aggregate, rate);
      tds194r = Math.max(0, total - c.prior_194r_tds);
      tdsRules = ["IT-194R-SCOPE", "IT-194R-THRESHOLD"].concat(c.pan ? [] : ["IT-206AA"]);
    }
    const gate = tds194r > 0 && benefitValue > 0;

    // 194J vs 194C fork on cash
    const cashAgg = c.prior_cash_fees + c.cash;
    const rateJ = c.pan ? K.S194J_RATE : K.S206AA_RATE;
    const rateC = c.pan ? K.S194C_RATE_INDIVIDUAL : K.S206AA_RATE;
    const baseJ = cashAgg > K.S194J_FY_THRESHOLD ? cashAgg : 0;
    const tdsJ = Math.max(0, pct(baseJ, rateJ) - c.prior_cash_tds);
    let baseC = 0;
    if (cashAgg > K.S194C_FY) baseC = cashAgg;
    else if (c.cash > K.S194C_SINGLE) baseC = c.cash;
    const tdsC = Math.max(0, pct(baseC, rateC) - c.prior_cash_tds);
    const fork = { "IT-194J-PROF": tdsJ, "IT-194C-WORK": tdsC };
    const forkMaterial = tdsJ !== tdsC;

    // GST chain
    const productIsConsideration = c.product > 0 && c.retained && c.deliverable_linked;
    if (c.product > 0 && c.retained && !c.deliverable_linked)
      notes.push(["GST-CONSIDERATION",
        "Retained but not deliverable-linked: 194R benefit, yet NOT GST consideration — the two statutes value the same object differently."]);
    const gstSupply = c.cash + (productIsConsideration ? c.product : 0);
    const turnoverAfter = c.prior_turnover + gstSupply;
    const threshold = c.special_state ? K.GST_SPECIAL : K.GST_NORMAL;
    const gstReg = turnoverAfter > threshold;
    const gstLiability = c.gst_registered ? pct(gstSupply, K.GST_RATE) : null;

    return {
      ok: true,
      tds_194r: tds194r, tds_rules: tdsRules, aggregate, gate,
      fork, fork_material: forkMaterial,
      gst_supply: gstSupply, gst_turnover_after: turnoverAfter,
      gst_reg: gstReg, gst_liability: gstLiability,
      benefit_qualifies: benefitQualifies, provider_obligated: providerObligated,
      notes,
    };
  }

  // The certifier: check an external claim against the spec.
  function verify(claim, input) {
    const a = assess(input);
    if (!a.ok)
      return { status: "OUT_OF_SCOPE", refusal: a.refusal, note: a.note, mismatches: [] };

    const mm = [];
    const chk = (field, claimed, expected, rule) => {
      if (claimed === undefined || claimed === null) return;
      if (claimed !== expected) mm.push({ field, claimed, expected, rule });
    };
    chk("tds_194r", claim.tds_194r, a.tds_194r, a.tds_rules[0]);
    chk("release_gate", claim.release_gate, a.gate, "IT-194R-RELEASEGATE");
    chk("gst_registration_required", claim.gst_reg, a.gst_reg, "GST-REG-THRESHOLD");
    chk("gst_liability", claim.gst_liability, a.gst_liability, "GST-RATE-18");

    let ambiguous = false;
    if (claim.cash_tds !== undefined && claim.cash_tds !== null) {
      if (claim.cash_basis) {
        if (!(claim.cash_basis in a.fork))
          mm.push({ field: "cash_basis", claimed: claim.cash_basis,
                    expected: Object.keys(a.fork), rule: "IT-FORK-JvC" });
        else if (claim.cash_tds !== a.fork[claim.cash_basis])
          mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                    expected: a.fork[claim.cash_basis], rule: claim.cash_basis });
      } else if (!a.fork_material) {
        const exp = a.fork["IT-194J-PROF"];
        if (claim.cash_tds !== exp)
          mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                    expected: exp, rule: "IT-194J-PROF" });
      } else if (Object.values(a.fork).includes(claim.cash_tds)) {
        ambiguous = true;
      } else {
        mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                  expected: Object.values(a.fork), rule: "IT-FORK-JvC" });
      }
    }
    if (mm.length) return { status: "REJECTED", mismatches: mm, assessment: a };
    if (ambiguous) return { status: "AMBIGUOUS", mismatches: [], assessment: a,
      note: "Value matches one branch of a material 194J/194C fork but no statutory basis was stated." };
    return { status: "CERTIFIED", mismatches: [], assessment: a };
  }

  // The "modal misunderstanding" calculator (mirrors baseline.py).
  function naive(input) {
    const c = Object.assign({}, DEFAULTS, input);
    return {
      tds_194r: pct(Math.max(0, c.product - rup(20000)), 10),
      release_gate: false,
      cash_tds: pct(c.cash, 10),
      cash_basis: null,
      gst_reg: c.prior_turnover + c.cash > rup(2000000),
      gst_liability: c.gst_registered ? pct(c.cash, 18) : null,
    };
  }

  // Parity: replay the frozen Python vectors through this JS engine.
  function runParity(vectors) {
    const failures = [];
    for (let i = 0; i < vectors.length; i++) {
      const { facts, expected } = vectors[i];
      const a = assess(facts);
      if (!expected.ok) {
        if (a.ok || a.refusal !== expected.refusal)
          failures.push({ i, got: a, expected });
        continue;
      }
      if (!a.ok) { failures.push({ i, got: a, expected }); continue; }
      const same =
        a.tds_194r === expected.tds_194r &&
        a.aggregate === expected.aggregate &&
        a.gate === expected.gate &&
        a.fork_material === expected.fork_material &&
        a.gst_supply === expected.gst_supply &&
        a.gst_turnover_after === expected.gst_turnover_after &&
        a.gst_reg === expected.gst_reg &&
        (a.gst_liability === expected.gst_liability ||
         (a.gst_liability === null && expected.gst_liability === null)) &&
        JSON.stringify(a.fork) === JSON.stringify(expected.fork);
      if (!same) failures.push({ i, got: a, expected });
    }
    return { total: vectors.length, failures };
  }

  return { rup, pct, RULES, DEFAULTS, assess, verify, naive, runParity, K };
});
