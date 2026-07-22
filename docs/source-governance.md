# Source governance and certificate maintenance

This POC separates official source bytes, source metadata, the author's interpretation, and
independent review. Only the first two can be mechanically checked. No rule currently claims
independent tax or CA review, so every runtime rule remains `experimental`;
`IT-194R-GROSSUP` is the first review target.

## Files and gates

- `sources/manifest.yaml` inventories official public sources, applicability dates, retrieval
  dates, hashes (or honest pending reasons), redistribution decisions, caches and source review.
- `sources/provenance.yaml` maps every runtime rule to exact locations, interpretation notes,
  assumptions, boundary tests, theorem names and rule review.
- `python -m collabproof.governance validate` rejects orphaned rule IDs, undeclared sources,
  missing dates, missing tests, invalid review promotion, impermissible snapshots, and hash drift
  for a checked-in snapshot or verified local cache.
- `python -m collabproof.governance sync-js-hash` refreshes the browser verifier's embedded bundle
  hash. CI runs it and rejects a diff.

The rule bundle hash is SHA-256 over canonicalized manifest and provenance data, the Python rule
registry, and the exact `collabproof/spec.py` bytes. Every Python and browser certificate carries
that digest. A certificate proves agreement only with that exact bundle—it is not legal advice and
does not prove the source interpretation is correct.

## Updating a source

1. Fetch official material into the ignored cache with
   `python -m collabproof.governance fetch [SOURCE_ID ...]`. The command refuses redirects outside
   allowlisted government domains and prints the observed digest.
2. Verify the bytes and authority. Promote `sha256.state` to `verified` only with the observed
   digest. Keep it `pending` or `unavailable` with a reason otherwise.
3. If redistribution permission is documented, set it to `permitted`, declare the checked-in
   snapshot and its verified digest. Otherwise never commit the bytes.
4. Update exact provenance locations, interpretations and boundary tests. A production promotion
   requires named, dated independent review for the rule and every source it uses.
5. Run governance validation, the tests/proofs, regenerate parity fixtures, and sync the JS hash.

## Impact and stale certificates

Keep the prior manifest (for example from the release tag), then run:

```bash
python -m collabproof.governance impact path/to/previous-manifest.yaml
python -m collabproof.governance impact path/to/previous-manifest.yaml \
  --certificates path/to/certificate-records.json
```

The report compares official URL, effective dates and hash metadata, maps changed sources through
provenance, and annotates supplied certificate records:

- `CURRENT`: certificate already carries the current bundle hash.
- `STALE`: a changed source affects a rule in the certificate's recorded trail.
- `REVERIFY_ELIGIBLE`: the global bundle changed outside the known trail; re-run verification
  before relying on it rather than treating the old digest as current.

`Certificate.governance_record()` emits the portable JSON fields expected by this workflow.

## Grounded experiment corpus

The earlier paraphrased corpus was removed. Arm B/C loads only official cached sources declared by
the manifest and fails closed when bytes or PDF-to-text sidecars are absent. The committed offline
fixture is metadata-only and exists to test governance reproducibly without redistributing legal
documents. The confidential Pramaana PDF must never be used or cached here.
