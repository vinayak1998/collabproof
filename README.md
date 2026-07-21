# collabproof

**An executable formalization of the Indian tax rules governing brand ↔ creator barter collaborations — with a certifier that either proves an answer against the statute or names the exact rule it breaks.**

Built as a working miniature of the [Pramaana Labs](https://pramaanalabs.ai/) architecture (LLM proposes → deterministic layer verifies → certify or refuse), on a domain their published work hasn't touched: **Section 194R TDS on influencer freebies, composed with GST barter rules** — the compliance surface of India's creator economy.

Every number in this README was produced by code in this repo. Run it yourself:

```bash
pip install z3-solver hypothesis pytest
python -m pytest tests/ -q          # 19 tests: 12 golden + 7 property classes
python proofs/prove_cliff.py        # Z3 proofs + exhaustive enumeration
python run_eval.py                  # certifier vs the naive baseline (n=50)
python run_eval.py --llm            # optional; needs ANTHROPIC_API_KEY
python gen_parity_vectors.py && node docs/parity_check_node.js   # JS↔Python parity
```

**Try it in a browser — no install:** `docs/` is a self-contained page (GitHub Pages-ready:
Settings → Pages → deploy from `/docs`). Enter a deal, get every number with its statutory rule,
certify your own (or an LLM's) answer, and see the dead-zone chart live. The page's JS engine is
mechanically held to the Python spec: **62/62 frozen vectors** replay on every page load and in CI
(`.github/workflows/ci.yml`), which also gates merges on the proofs and the zero
certified-but-wrong invariant. If the JS ever drifts from the Python, the badge goes red — the
demo polices itself the same way the domain logic does.

---

## The finding: the ₹20,000 dead zone (machine-checked)

Section 194R has a **cliff, not a marginal rule**: once the FY aggregate of benefits to a creator exceeds ₹20,000, TDS applies to the *whole aggregate*, not the excess. For an in-kind freebie where the creator funds the tax (the advance-tax route required by CBDT Circular 12/2022 before the product can even be released), Z3 proves, over the unbounded domain:

| Claim | Status |
|---|---|
| Accepting a **bigger** freebie can leave the creator with **less** (witness: ₹20,000 → ₹20,001) | EXHIBIT (SAT) |
| **Dead zone is exactly ₹20,001–₹22,222**: every value in it nets below a plain ₹20,000 freebie | **PROVED** (unsat of negation) |
| From ₹22,223 up, net strictly beats ₹20,000 | **PROVED** |
| The cliff is unique — strictly monotone on each side of the threshold | **PROVED** |
| **No PAN (s.206AA, 20%): dead zone widens to ₹20,001–₹24,999**, indifference at exactly ₹25,000 | **PROVED** |
| Brand side, gross-up mode: the marginal rupee of gift at the boundary costs the brand **₹2,223.33** (exactly 6670/3) | EXHIBIT (exact rationals) |

Exhaustive enumeration over ₹1–₹1,00,000 confirms: **2,222 dead-zone values; worst case ₹1,999.10 of immediate cash-adjusted loss at ₹20,001** — and binds the proof model to the shipped `assess()` implementation at all 100,000 points, so the proofs are about the code, not a hand-copy of it.

*Scope disclosure:* "worse off" is an immediate-cash statement — TDS is creditable against final liability, so the permanent loss depends on the creator's slab; the cash-flow cliff and the dead-zone boundaries are exact regardless. This is the structural cousin of Pramaana's marginal-relief non-monotonicity (₹50L → ₹51L drops take-home by ₹4,000), found in a different statute: **threshold-cliff provisions are a reusable bug class, and provers find them mechanically.**

## What the certifier does

`assess(collab)` compiles a fact pattern into determinations, each carrying statutory citations. `verify(claim, collab)` checks any answerer's output and returns one of:

- `CERTIFIED` — every asserted field matches the spec
- `REJECTED` — with the exact failing rule, e.g. a real trace from the eval run:
  `tds_194r_paise: claimed 10000, spec says 0 [IT-194R-THRESHOLD: s.194R(1), first proviso]`
- `AMBIGUOUS` — the answer matches one branch of a *material* statutory fork but states no basis
- `OUT_OF_SCOPE` — the spec refuses the pattern (non-resident payee → s.195; no business nexus → s.56(2)(x)) rather than guessing

## Results (all real, all reproducible)

**Spec vs statute** (oracle: human): 12/12 golden tests pass, hand-computed from the Act and Circulars — including the boundary stack (aggregate exactly ₹20,000 → no TDS; cash exactly ₹30,000 → neither 194J nor 194C bites; special-state turnover crossing ₹10,00,001 → registration required).

**Invariants** (Hypothesis, ~2,900 generated cases): amounts non-negative; threshold and returned-product rules hold everywhere; s.206AA is exactly 2× in recipient mode; gross-up is never cheaper than recipient-bears (pyramiding); GST registration monotone in turnover; `verify(assess(c)) == CERTIFIED` always (round-trip soundness).

**Certifier vs the "modal misunderstanding" baseline** (n=50 curated cases): the baseline is not a strawman — each of its 8 bugs is a common real-world reading (TDS on the *excess* over ₹20,000 is endemic; barter invisible to GST turnover; retained-vs-returned ignored).

| Verdict | Count |
|---|---|
| REJECTED (wrong, with failing rule named) | **39** |
| CERTIFIED (coincidentally correct) | 6 |
| AMBIGUOUS (fork value without basis) | 3 |
| OUT_OF_SCOPE (asserted numbers on refusal patterns) | 2 |
| **Certified-but-wrong (soundness)** | **0** |

Top catch reasons: `IT-194R-SCOPE`/excess-vs-aggregate (27), missed release gate (27), 194J threshold ignored (16), threshold boundary (7), GST registration (3+1).

**No LLM numbers are published here** because no LLM was called during construction. The adapter (`collabproof/llm_adapter.py`) is wired; run `--llm` with a key to generate them. Nothing in this repo is invented.

## The LLM experiment (`experiments/three_arms.py`) — ready to run, not yet run

The question worth answering publicly is not "does an LLM score lower?" but **"does giving the
LLM the legal documents close the gap?"** Three arms, one oracle: **A** bare LLM (facts only);
**B** the steelman — the same LLM with the governing statutory texts in context; **C** arm B
inside the verifier loop, where retries happen *in the same conversation* — the legal materials
and the model's own prior answer stay in context — and the feedback turn adds *only the failing
rule's citation* (never the corrected number). The schema lets the model abstain
(`cannot_determine`), so the headline metric — **confidently-wrong answers** — is fair. A
verified pipeline's certified-wrong count is zero by construction; an LLM's is an empirical
draw, and arm B tests whether retrieval changes that. Arm C tests the productizable claim:
verification doesn't just grade a model, it repairs one.

The experiment applies its own **completeness rule** on top of `verify()`: the certifier's
"check only what's asserted" semantics are right for a certifier API but would let empty,
partial, or abstaining answers score as wins. So every attempt — initial or retry — is judged
by `experiment_status()`: abstention counts as abstention on every attempt; in-scope answers
must assert all required fields to count `CERTIFIED_COMPLETE` (missing fields → `INCOMPLETE`,
retried with field names only); out-of-scope patterns are answered correctly only by explicit
refusal (`CORRECT_REFUSAL`), while numbers on them count `ASSERTED_ON_OUT_OF_SCOPE` — the
confident-fabrication metric. Wrong beats missing (`REJECTED` takes precedence). The
`--selftest` asserts its own expected counts, including two regression cases for exactly the
holes this rule closes (an abstaining retry and a partial first answer). Also included: a dead-zone probe
("is a ₹21,000 freebie better than ₹20,000?") whose raw answers are saved verbatim, unscored,
for quotation against the machine-checked proof. Before publishing results, replace the
placeholder corpus with official statutory text (`experiments/corpus/00_README.md`).
`--selftest` exercises the plumbing with a scripted answerer and is labeled as such.

## How this extends (not duplicates) Pramaana's published work

Their public demos ([Indian marginal relief](https://pramaanalabs.ai/blog/when-math-meets-policy-formalizing-indian-tax-logic), [1040 optimization](https://pramaanalabs.ai/blog/audit-defensible-tax-optimization)) are single-statute, single-taxpayer income tax in Lean. This project deliberately probes four things they haven't shown publicly:

1. **Cross-statute composition.** One transaction hits the Income-tax Act and the CGST Act simultaneously, and they *disagree about the same object*: a retained product that isn't deliverable-linked is a 194R benefit but not GST consideration. Real compliance lives in these intersections.
2. **Statutory ambiguity as a first-class output.** Influencer promotion is "advertising" under *both* s.194J Expl.(a) and s.194C Expl.(iv)(a); the statute doesn't resolve which governs (10% vs 1%). The spec refuses to pick: it returns both branches with bases, computes whether the fork is *material* on these facts, and certifies fork-sensitive claims only under a stated interpretation. (Provable quirk: under s.206AA the branches converge at 20% — no PAN makes the ambiguity vanish.)
3. **Procedural obligations as verified outputs.** The Circular 12/2022 release gate ("no product before tax evidence") is a compliance *action*, not a number — the baseline missed it 27 times.
4. **Exact integer arithmetic.** All money is integer paise; their published 1040 cells use Lean `Float` with `native_decide`, which enlarges the trusted base to the compiler and IEEE semantics. For audit artifacts, exactness is the conservative choice — a genuine design conversation, not a gotcha.
5. **The eval↔verification bridge.** Two oracles, kept distinct: golden tests check the *spec* against the human-computed statute; the eval checks *answerers* against the spec. Verification doesn't kill evals — it relocates them up the stack (their post "[Evaluation Is Not Verification](https://pramaanalabs.ai/blog/evaluation-is-not-verification)" argues the first half; this repo operationalizes the second).

## Mapping to the verification-layer pattern

Pramaana's publicly described pattern ([their funding post](https://pramaanalabs.ai/blog/we-raised-27m-to-build-a-compiler-for-mission-critical-ai)):
encode a domain's actual rules into a formal language once; when a question arrives, translate it
into a formal statement, run it through a proof engine, and either certify the answer or name the
exact rule that breaks — refusing when it cannot prove. collabproof is that pattern in miniature,
minus the ML: `spec.py` is the hand-encoded rule fabric (tiny); `verify()` is the runtime
certifier; `AMBIGUOUS`/`OUT_OF_SCOPE` are the refusal verdicts; the rule-ID trail is the
plain-language back-translation. What this toy deliberately does **not** have is the hard part —
automated translation of statute into formal rules. Every hour I spent hand-encoding circular
Q&As is an argument for why automating that step is the actual product.

**Why Python + Z3, not Lean 4?** A deliberate one-week trade-off, not a claim of equivalence.
Lean puts definitions and proofs in one kernel-checked system; here the executable spec (Python)
and the proofs (Z3) are separate artifacts, which is exactly why `prove_cliff.py` includes a
100,000-point binding check between the two — the transcription gap is real and had to be closed
empirically. The natural v2 is a Lean 4 port of the s.194R slice (the structures, the threshold
rule, and the dead-zone theorem), at which point the binding check becomes unnecessary by
construction. No uncompiled Lean ships in this repo on principle: nothing is claimed that wasn't
run.

One boundary this build made vivid: **proof is conditional on facts.** The spec certifies
consequences of the FMV you enter; whether ₹25,000 is the *right* FMV for a gifted lehenga is an
estimation problem, not a proof problem. Deterministic verification ends where fact uncertainty
begins — which is why a real system needs a grounded-facts layer (and, in genuinely stochastic
domains, a probabilistic layer) *underneath* the prover. A certified answer to mis-entered facts
is certified garbage.

## Three opinions this build earned

1. **Formalization cost concentrates in scoping, not encoding.** Encoding the rules took hours; deciding *which* rules, which FY, which circular Q&As govern, and where to refuse took most of the effort. Autoformalization that doesn't automate scope decisions automates the cheap part.
2. **The dead zone was invisible until the model was exact.** Rounding-free paise arithmetic is what made "exactly ₹22,222" provable rather than approximately observable. Exactness isn't pedantry; it's where findings come from.
3. **Refusal boundaries are product decisions wearing legal clothes.** OUT_OF_SCOPE and AMBIGUOUS are the two most valuable verdicts this system emits — and the two an LLM will never volunteer on its own.

## Limitations (read before citing)

Toy scope, deliberately: one recipient-per-brand aggregation model; no s.288B rounding; ss.206AB, 195, GST time-of-supply/ITC/RCM/e-commerce TCS unmodeled; the GST-exclusive valuation line of Circular 12/2022 and mixed prior bearer-modes are flagged in docstrings as verify-before-relying. **Version pin:** FY 2024-25 (Act of 1961 through Finance (No. 2) Act 2024). Finance Act 2025 threshold revisions and the Income-tax Act 2025 renumbering would both break this spec — which is the point: *rule drift makes spec versioning a first-class product problem for any verification company.* Not legal or tax advice.

---

*Author: Vinayak Rastogi · July 2026 · ~1,250 lines of Python · statutes: Income-tax Act 1961 (ss. 194R, 194J, 194C, 206AA), CBDT Circulars 12/2022 & 18/2022, CGST Act 2017 (ss. 2(31), 7, 22) & Rule 27.*
