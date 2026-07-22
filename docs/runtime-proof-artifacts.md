# Runtime proof artifacts

The trusted runtime slice is intentionally narrow: the covered section 194R
facts and decision only. `LeanProof/S194R.lean` defines exact-paise facts,
the retained-product rule, the individual/HUF provider carve-out, the
₹20,000 aggregate threshold, PAN/no-PAN rates, recipient/provider bearer
modes, the in-kind release gate, scope refusals, and the resulting decision.
Cash TDS under 194J/194C and every GST output remain outside this Lean model.

## Certificate flow and trust boundary

`certify_194r()` canonicalizes the complete `Collab` input, hashes that JSON,
gets the expected covered projection from the existing Python assessor, and
writes a Lean theorem with those concrete facts and outputs. The theorem has
the form:

```lean
theorem case_<fact-hash> :
    decide currentSpec caseFacts = expectedDecision := by
  decide
```

The generated file is checked by `lake env lean <artifact>`. The certificate
records the exact command, exit code, stdout, stderr, artifact hash, normalized
fact hash, assumptions, specification identity, and checked outputs. A missing
toolchain, failed module build, or rejected theorem raises
`LeanCertificationError`, removes the incomplete artifact, emits no
certificate, and returns a non-zero CLI status.

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
Python specification, and certificate bridge sources. Individual source
hashes are always included.

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
194J, 194C, ss.195/206AB, fact provenance, statute ingestion, or legal-version
migration.

## Reproduce

Install the pinned Python dependencies and the Lean toolchain named in
`lean-toolchain`, then run:

```bash
lake build
python proofs/check_lean_parity.py
python -m collabproof.runtime_proof proofs/example_s194r_facts.json \
  --output-dir /tmp/collabproof-certificate
```

Inspect the emitted `.lean` and `.certificate.json` files, then independently
repeat the recorded command:

```bash
lake env lean /tmp/collabproof-certificate/s194r-<fact-hash-prefix>.lean
```

To expose a conditional Python cash-TDS output, make the interpretation
explicit:

```bash
python -m collabproof.runtime_proof proofs/example_s194r_facts.json \
  --output-dir /tmp/collabproof-certificate \
  --cash-interpretation IT-194J-PROF
```

The CI gate builds Lean, runs the Python tests (including runtime certificate
failure tests), checks fixed Python/JavaScript/Lean parity cases, retains the
Z3 property checks, and replays the broader generated JavaScript fixtures.
