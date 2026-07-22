"""
prove_cliff.py — machine-checked properties of the s.194R Rs 20,000 threshold.

Structural cousin of Pramaana's marginal-relief finding (their system proved
Indian slab tax is non-monotonic: Rs 50L -> Rs 51L income drops take-home by
Rs 4,000). Same class of question, NEW domain: the s.194R threshold is a CLIFF,
not a marginal rule — once the FY aggregate of benefits exceeds Rs 20,000, TDS
applies to the WHOLE aggregate. For a creator who accepts an in-kind freebie
and must fund the tax in cash (advance-tax route), that produces a provable
"dead zone" where a BIGGER freebie leaves the creator strictly worse off in
immediate cash-adjusted terms.

Model (recipient-bears, in-kind only, no priors, whole-rupee values, paise ints):
    tds(v) = 0                 if v <= 2,000,000 paise (Rs 20,000)
           = v * r / 100       otherwise            (r = 10 with PAN, 20 without)
    net(v) = v - tds(v)        value received minus cash the creator must find

Scope disclosure: "worse off" is an immediate-cash statement. TDS is creditable
against the creator's final liability, so the permanent loss depends on the
creator's marginal rate; the cash-flow cliff and dead zone are exact regardless.

Three layers of evidence, strongest first:
  1. Z3 proofs over the UNBOUNDED domain (negation-unsat).
  2. Exhaustive enumeration over Rs 1..1,00,000 (proof by computation, bounded).
  3. Model-vs-implementation binding evidence: the Z3 model is checked against
     spec.assess() on every enumerated whole-rupee point in the bounded range.
     This catches transcription drift in that slice; it is not a general proof
     that the Python implementation and Z3 model are equivalent.

IMPORTANT — T6 below is a standalone historical gross-up illustration, not a
binding claim about spec.assess(). Its cost() function applies the Rs 20,000
threshold to raw product value before gross-up. The shipped assessor instead
adds current gross-up to the aggregate before testing the threshold, so its
first whole-rupee provider-mode trigger is Rs 18,001. The binding loop below is
recipient mode only and does not reconcile these two provider-side models.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fractions import Fraction

import z3

from collabproof import Brand, Collab, Creator, EntityType, Q, assess, rup

THRESH = rup(20_000)          # paise


def z3_net(v, rate):
    tds = z3.If(v <= THRESH, 0, v * rate / 100)
    return v - tds


def prove(name, claim):
    """Prove `claim` by refuting its negation. Returns True iff proved."""
    s = z3.Solver()
    s.add(z3.Not(claim))
    r = s.check()
    ok = r == z3.unsat
    print(f"  [{'PROVED' if ok else 'FAILED: ' + str(r)}] {name}")
    if not ok and r == z3.sat:
        print(f"    counterexample: {s.model()}")
    return ok


def exhibit(name, constraint, terms):
    s = z3.Solver()
    s.add(constraint)
    assert s.check() == z3.sat
    m = s.model()
    vals = {str(t): m.eval(t) for t in terms}
    print(f"  [EXHIBIT] {name}: {vals}")


def main():
    results = []
    v, v1, v2 = z3.Ints("v v1 v2")
    whole_rupees = lambda x: x % 100 == 0   # inputs are whole-rupee paise

    print("== Z3, unbounded domain (PAN furnished, r = 10%) ==")
    net = lambda x: z3_net(x, 10)

    # T1 — the cliff exists: bigger freebie, lower net. (satisfiability exhibit)
    exhibit("T1 non-monotonicity witness",
            z3.And(whole_rupees(v1), whole_rupees(v2), v1 > 0, v1 < v2,
                   net(v1) > net(v2), v1 == THRESH, v2 == THRESH + 100),
            [v1, v2])

    # T2 — dead zone lower half: every v in (20,000 .. 22,222] nets strictly
    # less than 20,000 exactly.
    results.append(prove(
        "T2 dead zone: 20,000 < v <= 22,222  =>  net(v) < net(20,000)",
        z3.ForAll([v], z3.Implies(
            z3.And(whole_rupees(v), v > THRESH, v <= rup(22_222)),
            net(v) < net(z3.IntVal(THRESH))))))

    # T3 — dead zone is EXACT: from 22,223 up, net strictly beats 20,000.
    results.append(prove(
        "T3 exit point: v >= 22,223  =>  net(v) > net(20,000)",
        z3.ForAll([v], z3.Implies(
            z3.And(whole_rupees(v), v >= rup(22_223)),
            net(v) > net(z3.IntVal(THRESH))))))

    # T4 — single cliff: strictly monotone on each side of the threshold.
    results.append(prove(
        "T4 monotone above threshold: THRESH < v1 < v2 => net(v1) < net(v2)",
        z3.ForAll([v1, v2], z3.Implies(
            z3.And(whole_rupees(v1), whole_rupees(v2),
                   v1 > THRESH, v2 > v1),
            net(v1) < net(v2)))))

    print("== Z3, no PAN (s.206AA, r = 20%) ==")
    net20 = lambda x: z3_net(x, 20)
    # T5 — dead zone widens to (20,000 .. 25,000): indifference exactly at 25,000.
    results.append(prove(
        "T5a no-PAN dead zone: 20,000 < v < 25,000 => net(v) < net(20,000)",
        z3.ForAll([v], z3.Implies(
            z3.And(whole_rupees(v), v > THRESH, v < rup(25_000)),
            net20(v) < net20(z3.IntVal(THRESH))))))
    results.append(prove(
        "T5b no-PAN indifference point: net(25,000) == net(20,000)",
        net20(z3.IntVal(rup(25_000))) == net20(z3.IntVal(THRESH))))

    print("== Standalone brand gross-up illustration (NOT runtime-bound) ==")
    cost = lambda x: Fraction(x) + Fraction(x, 9) if x > 20_000 else Fraction(x)
    j = cost(20_001) - cost(20_000)
    assert j == Fraction(1) + Fraction(20_001, 9), j
    print(f"  [EXHIBIT] T6 brand cost(20,001) - cost(20,000) = Rs {float(j):,.2f} "
          f"(exactly {j} rupees) — the marginal rupee of gift at the boundary "
          f"costs the brand over Rs 2,223.")

    print("== Exhaustive enumeration Rs 1..1,00,000 + spec binding ==")
    brand = Brand(EntityType.COMPANY)
    dead, max_loss, max_loss_at = 0, 0, None
    net_at_thresh = rup(20_000)
    for rupee in range(1, 100_001):
        vp = rup(rupee)
        # bind: proof model vs shipped implementation
        a = assess(Collab(brand=brand, creator=Creator(), product_fmv_paise=vp))
        model_tds = 0 if vp <= THRESH else vp * 10 // 100
        assert a.d(Q.TDS_194R) == model_tds, (rupee, a.d(Q.TDS_194R), model_tds)
        n = vp - model_tds
        if vp > THRESH and n < net_at_thresh:
            dead += 1
            loss = net_at_thresh - n
            if loss > max_loss:
                max_loss, max_loss_at = loss, rupee
    print(f"  dead zone size: {dead} rupee-values (expected 2,222: 20,001..22,222)")
    print(f"  max cash-adjusted loss: Rs {max_loss/100:,.2f} at v = Rs {max_loss_at:,}")
    assert dead == 2_222 and max_loss_at == 20_001 and max_loss == 199_910

    ok = all(results)
    print(f"\n{'ALL PROOFS PASSED' if ok else 'SOME PROOFS FAILED'} "
          f"({sum(results)}/{len(results)} universal claims proved; "
          f"enumeration + spec binding over 100,000 points passed)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
