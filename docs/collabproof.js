/* collabproof.js — JavaScript port of collabproof/spec.py (FY 2024-25 pin).
 * All money in integer paise. This port is held to the Python spec by
 * parity_vectors.js (generated from Python); the page refuses to show a green
 * badge unless both assessment and verification vectors match. Same rules,
 * same citations, same refusal and completeness semantics.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.collabproof = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Generated from manifest + provenance + Python rule registry + spec.py.
  // Refresh with: python -m collabproof.governance sync-js-hash
  const RULE_BUNDLE_HASH = "46d371fb99ac68318f5c71cf33e8f7b718fde380825ebe01b2d18cd64a51f620";

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

  const CLAIM_FIELDS = [
    "tds_194r", "release_gate", "cash_tds", "cash_basis", "gst_reg",
    "gst_liability",
  ];

  const owns = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

  function causalRule(field, assessment, facts) {
    const c = Object.assign({}, DEFAULTS, facts);
    if (field === "tds_194r") {
      if (!assessment.benefit_qualifies)
        return c.product > 0 ? "IT-194R-RETAINED" : "IT-194R-SCOPE";
      if (!assessment.provider_obligated) return "IT-194R-CARVEOUT";
      if (assessment.aggregate <= K.S194R_FY_THRESHOLD) return "IT-194R-THRESHOLD";
      if (!c.pan) return "IT-206AA";
      if (c.bearer === "provider") return "IT-194R-GROSSUP";
      return "IT-194R-THRESHOLD";
    }
    if (field === "release_gate") return "IT-194R-RELEASEGATE";
    if (field === "gst_reg") return "GST-REG-THRESHOLD";
    if (field === "gst_liability") return "GST-RATE-18";
    return "IT-194R-SCOPE";
  }

  function claimSchemaIssues(claim, checked) {
    const issues = [];
    const money = (name, nullable) => {
      if (!checked.includes(name)) return;
      const value = claim[name];
      if (nullable && value === null) return;
      if (!Number.isSafeInteger(value) || value < 0)
        issues.push(`${name} must be a non-negative safe integer number of paise`);
    };
    const bool = (name) => {
      if (checked.includes(name) && typeof claim[name] !== "boolean")
        issues.push(`${name} must be a boolean`);
    };
    money("tds_194r", false);
    bool("release_gate");
    money("cash_tds", false);
    bool("gst_reg");
    money("gst_liability", true);
    if (checked.includes("cash_basis") && claim.cash_basis !== null &&
        claim.cash_basis !== "IT-194J-PROF" && claim.cash_basis !== "IT-194C-WORK")
      issues.push("cash_basis must be IT-194J-PROF, IT-194C-WORK, or explicit null");
    return issues;
  }

  function certificate(status, assessment, checked, missing, extra) {
    return Object.assign({
      status,
      mismatches: [],
      notes: [],
      assessment,
      required_fields: CLAIM_FIELDS.slice(),
      checked_fields: checked,
      missing_fields: missing,
      rule_bundle_hash: RULE_BUNDLE_HASH,
    }, extra || {});
  }

  // The certifier: check a complete, typed external claim against the spec.
  function verify(claim, input) {
    const a = assess(input);
    if (!claim || typeof claim !== "object" || Array.isArray(claim))
      return certificate("INVALID", a, [], CLAIM_FIELDS.slice(), {
        notes: ["claim must be an object"], note: "claim must be an object",
      });

    const checked = CLAIM_FIELDS.filter((field) => owns(claim, field));
    const missing = CLAIM_FIELDS.filter((field) => !owns(claim, field));

    if (!a.ok) {
      const notes = [`[${a.refusal}] ${a.note}`];
      if (checked.length)
        notes.push("Assertions about an out-of-scope pattern are uncertifiable; the honest output is a refusal.");
      return certificate("OUT_OF_SCOPE", a, checked, [], {
        refusal: a.refusal, note: a.note, notes, required_fields: [],
      });
    }

    const issues = claimSchemaIssues(claim, checked);
    if (issues.length)
      return certificate("INVALID", a, checked, missing, {
        notes: issues, note: issues.join(" "),
      });

    const mm = [];
    const notes = [];
    const chk = (field, expected, supportingRules) => {
      if (!owns(claim, field)) return;
      if (claim[field] !== expected) mm.push({
        field, claimed: claim[field], expected,
        rule: causalRule(field, a, input), supporting_rules: supportingRules,
      });
    };
    chk("tds_194r", a.tds_194r, a.tds_rules);
    chk("release_gate", a.gate, ["IT-194R-RELEASEGATE"]);
    chk("gst_reg", a.gst_reg, ["GST-REG-THRESHOLD"]);
    chk("gst_liability", a.gst_liability, ["GST-RATE-18", "GST-VALUE-RULE27"]);

    let ambiguous = false;
    if (owns(claim, "cash_tds")) {
      if (!owns(claim, "cash_basis")) {
        if (!Object.values(a.fork).includes(claim.cash_tds))
          mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                    expected: Object.values(a.fork), rule: "IT-FORK-JvC",
                    supporting_rules: Object.keys(a.fork) });
      } else if (claim.cash_basis === null) {
        if (!a.fork_material) {
          const exp = a.fork["IT-194J-PROF"];
          if (claim.cash_tds !== exp)
            mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                      expected: exp, rule: "IT-194J-PROF",
                      supporting_rules: ["IT-194J-PROF", "IT-194C-WORK"] });
        } else if (Object.values(a.fork).includes(claim.cash_tds)) {
          ambiguous = true;
          notes.push("Cash TDS matches a branch of a material 194J/194C fork, but the claim explicitly states no statutory basis [IT-FORK-JvC].");
        } else {
          mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                    expected: Object.values(a.fork), rule: "IT-FORK-JvC",
                    supporting_rules: Object.keys(a.fork) });
        }
      } else {
        if (claim.cash_tds !== a.fork[claim.cash_basis])
          mm.push({ field: "cash_tds", claimed: claim.cash_tds,
                    expected: a.fork[claim.cash_basis], rule: claim.cash_basis,
                    supporting_rules: [claim.cash_basis] });
        else
          notes.push(`Cash TDS certified under ${claim.cash_basis}; the 194J/194C overlap remains unresolved [IT-FORK-JvC].`);
      }
    }
    if (mm.length)
      return certificate("REJECTED", a, checked, missing, { mismatches: mm, notes });
    if (missing.length)
      return certificate("INCOMPLETE", a, checked, missing, {
        notes: ["A certificate requires every output field; omitted fields were not checked."],
        note: "A certificate requires every output field; omitted fields were not checked.",
      });
    if (ambiguous)
      return certificate("AMBIGUOUS", a, checked, [], {
        notes, note: notes.join(" "),
      });
    return certificate("CERTIFIED", a, checked, [], { notes });
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

  // Parity: replay frozen Python assessment AND verifier vectors in this engine.
  function runParity(vectors) {
    const failures = [];
    const assessments = Array.isArray(vectors) ? vectors : (vectors.assessments || []);
    const verifications = Array.isArray(vectors) ? [] : (vectors.verifications || []);
    for (let i = 0; i < assessments.length; i++) {
      const { facts, expected } = assessments[i];
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
    for (let i = 0; i < verifications.length; i++) {
      const { facts, claim, expected, id } = verifications[i];
      const got = verify(claim, facts);
      const gotMismatches = got.mismatches.map((m) => ({
        field: m.field, claimed: m.claimed, expected: m.expected, rule: m.rule,
      }));
      const expectedMismatches = (expected.mismatches || []).map((m) => ({
        field: m.field, claimed: m.claimed, expected: m.expected, rule: m.rule,
      }));
      const same = got.status === expected.status &&
        got.rule_bundle_hash === expected.rule_bundle_hash &&
        JSON.stringify(got.required_fields) === JSON.stringify(expected.required_fields || []) &&
        JSON.stringify(got.checked_fields) === JSON.stringify(expected.checked_fields || []) &&
        JSON.stringify(got.missing_fields) === JSON.stringify(expected.missing_fields || []) &&
        (got.refusal || null) === (expected.refusal || null) &&
        JSON.stringify(gotMismatches) === JSON.stringify(expectedMismatches);
      if (!same) failures.push({ i: `verify:${id || i}`, got, expected });
    }
    return { total: assessments.length + verifications.length, failures };
  }

  return { rup, pct, RULES, RULE_BUNDLE_HASH, DEFAULTS, CLAIM_FIELDS, assess, verify, naive, runParity, K };
});
