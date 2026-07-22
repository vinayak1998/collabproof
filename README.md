# collabproof

**An executable, version-pinned interpretation of Indian tax rules governing brand ↔ creator barter collaborations — with a fail-closed checker that either certifies a complete claim against the encoded specification or explains why it cannot.**

Built independently from public materials as a [Pramaana Labs](https://pramaanalabs.ai/)-inspired exploration of the verification-layer pattern (answer proposed → deterministic specification checks → certify, reject, or refuse). The synthetic domain is **Section 194R TDS on creator benefits, composed with GST barter rules**. This project is not affiliated with or endorsed by Pramaana Labs or any employer.

**New to the codebase?** Start with the exhaustive, plain-language
[codebase guide](CODEBASE_GUIDE.md), which explains the domain, every execution path,
every folder and file, the proof/testing boundaries, and how documentation stays current.

Every number in this README was produced by code in this repo. Run it yourself:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q                 # golden, adversarial, LLM-boundary, property tests
python -m collabproof.governance validate  # source/rule/review/hash gates
python proofs/prove_cliff.py        # Z3 proofs + exhaustive enumeration
python proofs/check_lean_parity.py  # Python ↔ JavaScript ↔ Lean s.194R parity
python run_eval.py                  # certifier vs the naive baseline (n=50)
python run_eval.py --llm            # optional; needs ANTHROPIC_API_KEY
python gen_parity_vectors.py && node docs/parity_check_node.js   # assessor + verifier parity
```

**Try it in a browser — no install:** `docs/` is a self-contained page (GitHub Pages-ready:
Settings → Pages → deploy from `/docs`). Enter a deal, get every number with its statutory rule,
certify your own (or an LLM's) complete answer, and inspect a fact-sensitive product-value chart.
The page's JS engine is mechanically held to the Python source of truth: **62 assessment and 15
verifier fixtures** replay on every page load and in CI (`.github/workflows/ci.yml`). CI also runs
the full Python suite, the Lean build and s.194R tri-party parity gate, the Z3 artifact,
stale-fixture detection, and Node parity. A drifted browser
engine makes the badge and build go red.

---

## The finding: the ₹20,000 dead zone (machine-checked)

Section 194R has a **cliff, not a marginal rule** in this encoded interpretation: once the FY aggregate of benefits to a creator exceeds ₹20,000, TDS applies to the *whole aggregate*, not the excess. For the theorem's deliberately narrow slice—whole-rupee product values, no prior benefits/TDS, company provider, retained in-kind product, PAN furnished, and creator-funded tax—Z3 proves over the unbounded whole-rupee domain:

| Claim | Status |
|---|---|
| Accepting a **bigger** freebie can leave the creator with **less** (witness: ₹20,000 → ₹20,001) | EXHIBIT (SAT) |
| **Whole-rupee dead zone is exactly ₹20,001–₹22,222**: every value in it nets below a plain ₹20,000 freebie | **PROVED** (unsat of negation) |
| From ₹22,223 up, net strictly beats ₹20,000 | **PROVED** |
| The cliff is unique — above-threshold monotonicity is Z3-proved; below threshold `net(v)=v` directly | **PROVED + DIRECT** |
| **No PAN (s.206AA, 20%): dead zone widens to ₹20,001–₹24,999**, indifference at exactly ₹25,000 | **PROVED** |
| Standalone brand gross-up illustration in `prove_cliff.py`: raw product ₹20,000 → ₹20,001 changes the illustrative cost by **₹2,223.33** | **EXHIBIT ONLY — not runtime-bound:** it applies the threshold before gross-up, unlike `assess()`; see `CODEBASE_GUIDE.md` |

Exhaustive enumeration over the 100,000 whole-rupee points ₹1–₹1,00,000 confirms **2,222 dead-zone values and a worst immediate cash-adjusted loss of ₹1,999.10 at ₹20,001**. It is a strong binding check for that slice, not a proof that the entire Python implementation is equivalent to the Z3 model. The runtime engine accepts paise; the paise-granularity boundary is outside this theorem.

*Scope disclosure:* "worse off" is an immediate-cash statement — TDS is creditable against final liability, so the permanent loss depends on the creator's slab; the cash-flow cliff and the dead-zone boundaries are exact regardless. This is the structural cousin of Pramaana's marginal-relief non-monotonicity (₹50L → ₹51L drops take-home by ₹4,000), found in a different statute: **threshold-cliff provisions are a reusable bug class, and provers find them mechanically.**

## What the certifier does

`assess(collab)` compiles a fact pattern into determinations, each carrying rule IDs and citations. `verify(claim, collab)` checks a typed six-field claim, reports its coverage, and returns one of:

- `CERTIFIED` — every required field is present, well typed, and matches the encoded spec
- `INCOMPLETE` — one or more required fields were omitted; unchecked fields can never turn green
- `INVALID` — malformed types, amounts, or bases fail the runtime claim schema
- `REJECTED` — with the path-specific decision rule and supporting trail, e.g. an excess-only calculation is attributed to `IT-194R-THRESHOLD`, not a generic scope citation
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

Top catch reasons in the regenerated run include the missed release gate (27), the s.194R threshold/aggregate rule (23), and the 194J branch (16). The full per-case explanations are committed in `eval/results.json`.

**No LLM numbers are published here** because no LLM was called during construction. The adapter (`collabproof/llm_adapter.py`) is wired; run `--llm` with a key to generate them. Nothing in this repo is invented.
LLM modes transmit serialized case facts to the configured provider and persist responses locally;
use synthetic, non-confidential cases only.

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

The core certifier itself is fail-closed: empty and partial claims return `INCOMPLETE`. The model
boundary additionally requires one strict eight-key JSON object, rejects unknown/duplicate keys,
bool-as-int, floats or strings for money, invalid bases, and contradictory refusal-plus-assertion
outputs. Every initial answer and retry passes through that same boundary. Explicit abstention
counts as abstention; out-of-scope patterns count as correct only when the model explicitly refuses
without asserting an outcome. Wrong beats missing (`REJECTED` takes precedence). The `--selftest`
asserts expected counts for partial answers, malformed output, asserted refusals, and abstaining
retries. Also included: a dead-zone probe
("is a ₹21,000 freebie better than ₹20,000?") whose raw answers are saved verbatim, unscored,
for quotation against the machine-checked proof. Grounded arms load only the manifest-driven,
ignored cache of official government material and fail closed when it is absent
(`experiments/corpus/00_README.md`).
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
decision rule and support trail when a claim breaks — refusing unsupported patterns. collabproof
explores that pattern without claiming implementation equivalence: `spec.py` is a hand-encoded
rule engine; `verify()` is a fail-closed equality checker over its outputs;
`AMBIGUOUS`/`OUT_OF_SCOPE` are refusal verdicts; and the rule-ID trail is the plain-language
back-translation. For the deliberately narrow s.194R slice, it now emits a per-case Lean theorem,
kernel-check result, complete normalized facts and hashes in a runtime certificate; GST and the
194J/194C fork remain explicitly unverified by Lean. See
[`docs/runtime-proof-artifacts.md`](docs/runtime-proof-artifacts.md). What this toy still does
**not** have is the hard part —
automated translation of statute into formal rules. Every hour I spent hand-encoding circular
Q&As is an argument for why automating that step is the actual product.

**Why retain Python and Z3 alongside Lean 4?** Python remains the broader executable product spec
and JavaScript remains the browser port. Z3 remains useful for property discovery and the
unbounded dead-zone proofs. Lean now owns one narrow trusted vertical slice: exact structures,
rules, and per-case decision equalities for s.194R. The tri-party parity gate detects transcription
drift, but it does not erase that the Python, JavaScript, and Lean implementations are separately
authored. No uncompiled Lean ships in this repo: the module, generated case theorems, and parity
cases are all checked in tests and CI.

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

Toy scope, deliberately: one recipient-per-brand aggregation model; no s.288B rounding; ss.206AB, 195, GST time-of-supply/ITC/RCM/e-commerce TCS unmodeled; the GST-exclusive valuation line of Circular 12/2022 and mixed prior bearer-modes are flagged in docstrings as verify-before-relying. The versioned [source governance pipeline](docs/source-governance.md) maps every rule to official public sources, assumptions and tests, but **no rule currently claims independent tax/CA review**; all remain experimental and the provider-borne gross-up threshold path is the early review target. **Version pin:** FY 2024-25 (Act of 1961 through Finance (No. 2) Act 2024). Finance Act 2025 threshold revisions and the Income-tax Act 2025 renumbering would both break this spec — which is the point: *rule drift makes spec versioning a first-class product problem for any verification company.* Educational, synthetic, and not legal or tax advice.

---

*Author: Vinayak Rastogi · July 2026 · statutes encoded: Income-tax Act 1961 (ss. 194R, 194J, 194C, 206AA), CBDT Circulars 12/2022 & 18/2022, CGST Act 2017 (ss. 2(31), 7, 22) & Rule 27.*
