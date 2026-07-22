# Runtime proof artifacts

The trusted runtime slice is intentionally narrow: the covered section 194R
facts and decision only. `LeanProof/S194R.lean` defines exact-paise facts,
the retained-product rule, the individual/HUF provider carve-out, the
₹20,000 aggregate threshold, PAN/no-PAN rates, recipient/provider bearer
modes, the in-kind release gate, scope refusals, and the resulting decision.
Cash TDS under 194J/194C and every GST output remain outside this Lean model.

## Certificate flow and trust boundary

`certify_unconfirmed_194r()` is the explicitly low-level compatibility path: it
projects a complete `Collab` onto the exact eleven fields
consumed by Lean, canonicalizes and hashes that JSON, gets the expected covered
projection from the existing Python assessor, and writes a Lean theorem with
those concrete facts and outputs. The broader input is retained separately for
audit, but cash/GST-only fields cannot change the Lean fact hash or theorem.
It emits `intake: null`, so its certificate cannot enter the natural-language
renderer. The product path, `certify_194r()`, instead loads and freshly parses a
full confirmed-case artifact; `certify_194r_facts()` additionally checks that a
supplied no-default envelope equals that artifact. The theorem has the form:

```lean
theorem case_FACT_HASH_PREFIX :
    decide currentSpec caseFacts = expectedDecision := by
  decide
```

The generated file is checked by `lake env lean <artifact>`. The v2 certificate
records the exact command, exit code, stdout, stderr, artifact hash, normalized
fact hash, assumptions, specification identity, current governed rule-bundle
hash, exact output-to-rule trail, and checked outputs. When invoked through the
controlled-English pipeline, it also embeds the confirmed draft, source,
provenance, fact, specification, and governance digests. The runtime bridge
recomputes the confirmation preimage before starting Lean. A stale or tampered
confirmation raises `ValueError` before any artifact is written. A missing
toolchain, failed module build, or rejected theorem raises
`LeanCertificationError`; a failed theorem is removed and no certificate is
emitted. Both CLI paths return a non-zero status.

This is a genuine Lean kernel check of the concrete equality. It establishes
that the checked-in Lean function produces those outputs for those facts. It
does not establish that the input facts are true, that the legal
formalization is correct, or that Python generated the right theorem. The
fresh Lean process is independent checking of the artifact, but it is not a
second legal model or an independent fact oracle; the certificate says so
explicitly. Z3 remains a property-discovery and theorem-exploration tool and
is not in the runtime certificate trust path.

The specification identity is `git:<commit>` for a clean worktree. During
development, when the tree is dirty or Git metadata is unavailable, it is a
deterministic SHA-256 over the listed Lean toolchain, Lean specification,
exact Section 194R fact model, Python specification, and certificate bridge
sources. Individual source hashes are always included. The independent
governance identity covers the official-source manifest, rule provenance,
Python rule registry, and specification source; both identities must be
current before certificate-only rendering succeeds.

## Covered and uncovered behavior

Covered outputs are the scope classification, retained-product qualification,
provider obligation, FY aggregate benefit, TDS due now, and release gate.
Non-resident and no-business-nexus inputs produce theorem-checked unsupported
scope classifications rather than substantive 194R amounts.

The certificate never presents GST as Lean-verified. It also does not expose a
194J/194C cash value unless the caller passes either `IT-194J-PROF` or
`IT-194C-WORK`. When selected, that value is clearly labeled
`CONDITIONAL_UNVERIFIED_BY_LEAN` and the selection is repeated as an explicit
assumption. The unresolved overlap itself is never claimed as proved.

Known limitations from the Python specification still apply, including the
single recipient/provider FY aggregation model, reliance on entered FMV and
prior aggregates, and no s.288B rounding. The Lean slice does not cover GST,
194J, 194C, ss.195/206AB, statute ingestion, or legal-version migration. The
controlled pipeline records fact provenance, but hashes and source spans do
not establish that a user's factual statements are true.

## Controlled question and rendered answer

The higher-level workflow uses `collabproof.pipeline`. Its first phase accepts
only the documented Section 194R/FY 2024-25 controlled-English grammar and
persists a review draft. All eleven Lean facts must be explicit, conflict-free,
and evidence-linked. Its second phase requires the exact draft SHA-256 plus
`--accept`, then invokes Lean and passes the persisted certificate path to
`render_194r()`. The renderer follows only the certificate's hash-bound,
colocated proof and confirmed-case artifacts.

The renderer revalidates the strict certificate schema, full confirmed-case
sidecar and source-derived evidence, confirmation digest, normalized facts,
current specification sources, governance bundle, dynamic rule trail, exact
generated theorem, artifact hash, and recorded successful Lean commands. It
then independently reruns the pinned Lean build and exact per-case artifact,
rechecks the trust identities, and only then chooses static templates. Each
rendered output or assumption claim has a machine-readable certificate pointer.
The prose exposes the canonical limitations and governed rule citations while
marking that citation trail as Python-checked metadata rather than a Lean
theorem field. The renderer never uses raw query prose to generate the answer.
Refusal templates state that an unsupported scope is not a finding of zero tax.

## Reproduce

Install the pinned Python dependencies and the Lean toolchain named in
`lean-toolchain`, then run:

```bash
lake build
python proofs/check_lean_parity.py
python -m collabproof.runtime_proof proofs/example_s194r_facts.json \
  --output-dir /tmp/collabproof-certificate \
  --allow-unconfirmed-structured-facts

python -m collabproof.pipeline formalize proofs/example_s194r_query.txt \
  --output /tmp/collabproof-draft.json --case-id demo-194r
# After reviewing every proposed fact, copy the printed draft_sha256:
python -m collabproof.pipeline prove /tmp/collabproof-draft.json \
  --confirm-sha256 REVIEWED_DRAFT_SHA256 --accept \
  --output-dir /tmp/collabproof-proof
```

Inspect the emitted `.lean` and `.certificate.json` files, then independently
repeat the recorded command:

```bash
lake env lean /tmp/collabproof-certificate/s194r-FACT_SHA256.lean
```

To expose a conditional Python cash-TDS output, make the interpretation
explicit:

```bash
python -m collabproof.runtime_proof proofs/example_s194r_facts.json \
  --output-dir /tmp/collabproof-certificate \
  --cash-interpretation IT-194J-PROF \
  --allow-unconfirmed-structured-facts
```

The CI gate builds Lean, runs the Python tests (including runtime certificate
failure tests), checks fixed Python/JavaScript/Lean parity cases, retains the
Z3 property checks, and replays the broader generated JavaScript fixtures.
