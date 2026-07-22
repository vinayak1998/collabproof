# Controlled-English to verified Section 194R answer

This milestone implements one deliberately bounded vertical slice:

```text
controlled-English query
  -> reviewable fact draft with source spans
  -> explicit confirmation of that exact draft
  -> fresh Lean process checks a concrete theorem
  -> governance-bound v2 certificate
  -> certificate-led static English rendering
```

It is not a general natural-language tax assistant. It accepts one line-oriented
controlled-English format, only for the modeled Section 194R decision for
**FY 2024-25**. It does not silently interpret free-form prose or fill in facts.

## 1. Exact input contract

The first nonblank line must begin with `Question:` and request the Section 194R
decision or treatment for FY 2024-25. The second nonblank line must be exactly
`Facts:`. Each fact is a `- Label: value` bullet.

For example:

```text
Question: Determine the Section 194R treatment for FY 2024-25.
Facts:
- Brand entity type: company
- Transfer is in brand business or profession: yes
- Brand preceding-FY business turnover: INR 5,00,00,000
- Brand preceding-FY profession receipts: INR 0
- Creator is resident in India for tax purposes: yes
- Creator PAN furnished: yes
- Creator prior FY benefits from this brand: INR 0
- Creator prior Section 194R TDS: INR 0
- Product fair market value: INR 30,000
- Product retained: yes
- Tax borne by: recipient
```

All eleven facts below are required. There are no defaults in the trusted fact
envelope.

| # | Canonical fact | Controlled-English label | Accepted value |
|---:|---|---|---|
| 1 | `brand.entity_type` | `Brand entity type` | `individual`, `huf`, `firm`, or `company` |
| 2 | `brand.in_business` | `Transfer is in brand business or profession` | exactly `yes` or `no` |
| 3 | `brand.preceding_fy_business_turnover_paise` | `Brand preceding-FY business turnover` | exact non-negative INR amount |
| 4 | `brand.preceding_fy_profession_receipts_paise` | `Brand preceding-FY profession receipts` | exact non-negative INR amount |
| 5 | `creator.is_resident` | `Creator is resident in India for tax purposes` | exactly `yes` or `no` |
| 6 | `creator.pan_furnished` | `Creator PAN furnished` | exactly `yes` or `no` |
| 7 | `creator.fy_prior_benefits_from_brand_paise` | `Creator prior FY benefits from this brand` | exact non-negative INR amount |
| 8 | `creator.fy_prior_194r_tds_paise` | `Creator prior Section 194R TDS` | exact non-negative INR amount |
| 9 | `transaction.product_fmv_paise` | `Product fair market value` | exact non-negative INR amount |
| 10 | `transaction.product_retained` | `Product retained` | exactly `yes` or `no` |
| 11 | `transaction.tax_borne_by` | `Tax borne by` | `recipient` or `provider` |

Money must identify INR with `INR`, `Rs`, `Rs.`, or `₹`. The parser uses integer
paise, accepts at most two decimal places and optional exact `lakh`/`crore`
suffixes, and rejects negative, approximate, foreign-currency,
scientific-notation, fractional-paisa, or above-bound values. The maximum is
`10^20` paise. An explicit zero is a supplied fact; an omitted zero-valued fact
is still missing.

The query may contain at most 65,536 UTF-8 bytes and each line at most 4,096
Unicode characters. An explicit case ID must contain 1–128 ASCII letters,
digits, underscores, or hyphens; otherwise a random 32-hex-character ID is
created. `Question:` and `Facts:` plus bullet structure are case-sensitive.
Fact labels, yes/no values, enums, and money-unit words are case-insensitive.

Canonical fact paths may be used as labels. The documented aliases
`Brand preceding-FY professional receipts` and `Product FMV` are also accepted.
Unknown labels and non-bullet instruction text are invalid rather than being
treated as directions to the parser.

## 2. Draft statuses and fail-closed intake

Formalization returns exactly one intake status:

| Status | Meaning | May proceed to proof? |
|---|---|---|
| `INVALID` | The structure, label, type, amount, or other syntax is invalid. | No |
| `UNSUPPORTED_QUERY` | The intent or period is outside Section 194R/FY 2024-25. | No |
| `CONFLICTING_FACTS` | The query gives incompatible values for the same fact. Both candidates and their evidence are retained. | No |
| `NEEDS_CLARIFICATION` | One or more of the eleven facts is absent. The draft lists the missing paths and exact clarification questions. | No |
| `AWAITING_CONFIRMATION` | All eleven facts are explicit, typed, supported, and conflict-free. | Only after explicit confirmation |

Equivalent repeated facts are allowed and retain all matching evidence spans.
Incompatible repeats are never resolved by precedence or guesswork. To address
a missing or conflicting fact, edit the source query and formalize it again.
Do not hand-edit the JSON draft: the prove phase reparses the embedded source
under the current grammar and requires every persisted field to match that
fresh parse.

The `formalize` command writes the draft for every intake status. It exits `0`
only for `AWAITING_CONFIRMATION` and exits `2` for every other intake status.
Loading, confirmation, proof, or rendering failures exit `1` and produce no
verified answer.

## 3. Evidence spans and integrity hashes

The evidence in this milestone is the user's exact query text. It is evidence
of what was asserted, not independent proof that an assertion is true.

The question and every extracted fact carry one or more records containing:

- `source_id: "query"`;
- the SHA-256 of the exact UTF-8 query bytes;
- `start` and `end` offsets measured in Unicode code points;
- the exact quoted substring at that span.

Before confirmation, the implementation checks that every quote equals the
corresponding source slice and that every span refers to the same source hash.
The persisted records then bind distinct layers:

| Digest | What it binds |
|---|---|
| `source.sha256` | Exact UTF-8 bytes of the controlled-English query |
| `draft_sha256` | Parsed status, intent, facts, evidence, issues, specification version, and rule-bundle identity for the draft |
| `normalized_fact_sha256` | Canonical nested representation of the eleven Lean-consumed facts |
| `source_bundle_sha256` | Canonical identity of the query source and its hash |
| `provenance_sha256` | The normalized facts together with their exact evidence records |
| `confirmation_sha256` | The canonical confirmation payload, including the preceding bindings, case, intent, period, specification version, and rule-bundle hash |
| `specification_bundle_sha256` | Exact Lean model, fact-envelope, intake, runtime bridge, renderer, pipeline, Python specification, Lake, and toolchain source bytes |

Changing the wording while keeping the same normalized facts changes the draft,
provenance, and confirmation identities. A change to the formal specification
or governed rule bundle also requires a new draft and confirmation.

These are unkeyed integrity digests. They detect substitution and bind records
together, but they are not digital signatures and do not identify who supplied
or accepted the facts.

## 4. Explicit two-phase confirmation

Confirmation is intentionally separate from formalization.

1. `formalize` parses the source and persists a reviewable draft. Review the
   question, all eleven normalized facts, exact evidence quotes, status,
   specification version, rule-bundle hash, and printed `draft_sha256`.
2. `prove` requires the same persisted draft, the exact digest through
   `--confirm-sha256`, and the explicit `--accept` flag. It reparses the source,
   rechecks the evidence and all hashes, and confirms only an
   `AWAITING_CONFIRMATION` draft under the current specification and governance
   bundle.

This is an integrity acknowledgment by the local command operator. It is not
an authenticated signature, approval workflow, timestamp, identity assertion,
or non-repudiation mechanism. The current CLI does not record a named signer or
verify authority to confirm tax facts.

Declining or omitting acceptance produces no confirmed case and no proof. A
wrong digest, changed source, missing fact, conflict, stale specification, or
stale governance bundle fails closed before Lean is invoked.

## 5. From confirmed facts to a v2 Lean certificate

The proof phase converts only the confirmed eleven-field `S194RFacts` envelope
into the checked Section 194R model. Cash and GST fields are not accepted as
hidden inputs to this proof slice.

The compatibility adapter constructs a broader `Collab` value with explicit
neutral values for fields outside this slice. Those values may appear in the
certificate's `complete_collab_input_facts`, but they are not extracted or
certificate-recorded facts, are excluded from the normalized-fact hash and Lean
theorem, and must not be interpreted as cash-TDS or GST conclusions.

For a confirmed case, the pipeline first creates a private, unique directory
named with the complete confirmation hash and writes the complete confirmed
case there. An existing run directory is never overwritten. The runtime then:

1. recomputes the normalized facts and their hash;
2. freshly parses the confirmed-case source/evidence and verifies its exact
   file hash, confirmation, formal-specification bundle, and current rule bundle;
3. generates a concrete theorem of the form
   `decide currentSpec caseFacts = expectedDecision`;
4. builds the pinned `LeanProof.S194R` module;
5. invokes `lake env lean` in a fresh process on the generated theorem;
6. emits a certificate only if both the module build and per-case kernel check
   pass.

The persisted certificate uses schema
`collabproof-runtime-certificate-v2`. It binds:

- the confirmed intake record and normalized fact hash;
- the absolute path and exact byte hash of the colocated confirmed-case artifact;
- the exact covered outputs and their per-output rule trails;
- the path-specific applied rule IDs and the complete formal-model rule
  whitelist, including both explicit scope-refusal rules;
- the current governance rule-bundle hash;
- the pinned specification version and SHA-256 of every file in the runtime's
  declared specification-source bundle;
- the generated Lean artifact path and hash;
- the exact build and kernel-check commands, results, output, and independence
  boundary.

`certification_status: LEAN_KERNEL_CHECKED` means the fresh Lean process
accepted the concrete equality against this checked-in formal model. It does
not mean that Lean independently translated the law, authenticated the facts,
or proved the model legally correct.

Lean checks the decision values, not the explanatory rule-ID trace. The
renderer requires that trace to equal the current Python assessor and governed
whitelist exactly, but those causal citation IDs remain checked metadata rather
than fields proved by the Lean `Decision` theorem.

The decision is either:

- `ANSWERED`: the facts are inside the model's resident-recipient and
  business-nexus scope, so the covered Section 194R outputs are rendered; or
- `REFUSED`: Lean checked one of the model's explicit scope classifications,
  `unsupported_non_resident` or `unsupported_no_business_nexus`.

A refusal is **not** a finding that tax, TDS, or liability is zero. It says only
that this narrow Section 194R model will not answer the case. The renderer never
turns the placeholder zero fields in a refused formal decision into a tax
answer.

## 6. Certificate-led static rendering

The natural-language renderer accepts only the path to a persisted v2
certificate. It follows the certificate's hash-bound, colocated confirmed-case
artifact solely to revalidate the source-derived facts and acknowledgment. It
does not accept query prose as a rendering instruction, makes no LLM call, and
performs no new legal reasoning.

Before emitting text, it fail-closes unless all of these remain valid:

- exact certificate schema, canonical facts, and fact hash;
- full source-derived confirmed intake, sidecar hash, and confirmation digest;
- current governance bundle, specification version, and trusted source hashes;
- formal-model and applied rule trails;
- outputs recomputed from the current formal case;
- the colocated, byte-for-byte expected Lean artifact and its hash;
- successful recorded module-build and fresh-process kernel checks;
- both cash TDS and GST still labeled `UNSUPPORTED_UNVERIFIED`.

The renderer then independently reruns the pinned Lean build and the exact
case artifact, rechecks the sidecar, artifact, specification, and governance
identities for concurrent change, and fails closed on any rejection or timeout.
After validation, fixed templates select text solely from certificate fields.
The machine-readable rendered answer records the certificate hash and, for each
rendered claim, its certificate JSON pointer, value, rule IDs, and template ID.
An answered case renders six covered-output claims plus four explicit assumption
claims; a refused case renders only the supported scope output plus the same four
assumptions and the explicit warning that refusal is not a zero-tax conclusion.
The renderer requires the exact trusted assumption set, so certificate prose
cannot be replaced with claims that Lean authenticated the facts or law.
The prose also names each applied rule ID and statutory citation while stating
that this explanatory trail is Python/governance-validated metadata rather than
a field of the Lean theorem.

## 7. Reproduce the two phases

From the repository root, with the pinned Python environment and Lean toolchain
installed, formalize the example query:

```bash
python -m collabproof.pipeline formalize proofs/example_s194r_query.txt \
  --output draft.json
```

The command prints a JSON summary containing `status` and `draft_sha256`.
Inspect `draft.json`; proceed only when the status is
`AWAITING_CONFIRMATION` and every fact and evidence span matches what you meant.

Then replace `DRAFT_SHA256_FROM_FORMALIZE` below with the exact 64-character
digest printed by the first command:

```bash
python -m collabproof.pipeline prove draft.json \
  --confirm-sha256 DRAFT_SHA256_FROM_FORMALIZE \
  --accept \
  --output-dir build/s194r-case
```

On success, the command prints paths to:

- the persisted confirmed case;
- the generated `.lean` theorem and v2 `.certificate.json` beside it;
- a machine-readable `.answer.json` containing claim support; and
- a certificate-derived `.answer.txt` containing the static English result
  plus its certificate and confirmation SHA-256 identifiers; and
- a last-written `manifest.json` cross-checking and hashing the confirmed case,
  proof artifact, certificate, and both answer projections.

All of these live under the newly created per-confirmation run directory. A
second attempt with the same case and confirmation refuses to overwrite the
first audit record. The answer files are convenience projections: re-run
`render_194r()` against the certificate before relying on a copied or later
modified answer file.

If formalization reports `NEEDS_CLARIFICATION`, answer every listed question in
the source file and rerun the first command. If it reports
`CONFLICTING_FACTS`, correct the conflicting source lines rather than choosing a
candidate in the draft. `INVALID` and `UNSUPPORTED_QUERY` likewise require a
new source query; none of these states can be forced through with `--accept`.

## 8. Scope and non-claims

This vertical slice covers only the encoded Section 194R decision for FY
2024-25. The following remain outside its trusted result:

- cash TDS under Sections 194J or 194C and their unresolved classification;
- GST supply, registration, valuation, rate, liability, and compliance;
- other tax provisions, including non-resident treatment under Section 195;
- law after the FY 2024-25 model pin, including any current-law conclusion;
- whether the human legal formalization is correct or complete;
- whether a source summary, interpretation, or entered fact is legally
  sufficient;
- whether residency, PAN status, turnover, prior benefits/TDS, FMV, retention,
  business nexus, or tax-bearer facts are true.

The output is educational software evidence about one encoded model, not legal
or tax advice. Current source records and rule interpretations still require
independent professional review; a kernel-checked computation does not replace
that review.

## 9. Local privacy and artifact handling

This pipeline does not call an external model or network service. It reads local
files and invokes the locally installed Lean toolchain. Local-only is not the
same as confidential or access-controlled:

- `draft.json` contains the complete query text, extracted facts, and evidence
  quotes in plaintext;
- the confirmed-case file retains that source text and all evidence;
- the certificate and Lean artifact contain normalized fact values and local
  paths, and the certificate records process commands and output;
- rendered answer files contain the resulting tax treatment;
- SHA-256 values are integrity identifiers, not encryption or anonymization.

Use synthetic data for demonstrations. If real facts are ever processed, place
the query and output directory under appropriate filesystem access controls,
define retention and deletion rules, and avoid committing the artifacts. This
milestone provides no encryption at rest, secret-management integration,
multi-user authorization, or secure deletion guarantee.

## 10. Remaining production gaps

The bounded slice proves the architecture, not production readiness. Major work
still includes:

1. general natural-language understanding and evaluated autoformalization,
   without weakening the explicit-fact and confirmation boundary;
2. document ingestion, external evidence provenance, contradiction handling
   across sources, and fact authentication;
3. authenticated, signed, timestamped confirmation and certificate records;
4. independently reviewed, byte-pinned legal sources and a controlled
   statute-to-formal-model update process;
5. formal coverage for cash TDS, GST, other provisions, and legal-version
   migration beyond FY 2024-25;
6. principled handling of uncertain or probabilistic facts rather than forcing
   them into booleans and exact amounts;
7. adversarial and semantic-fidelity evaluation over paraphrases, omissions,
   conflicts, document injection, and translation round trips;
8. production privacy, encryption, identity, access control, audit retention,
   observability, resource limits, deployment hardening, and human escalation;
9. independent legal, security, and operational review of the entire trust
   chain, including the static renderer and certificate-verification policy.
