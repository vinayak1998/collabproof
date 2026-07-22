# Source governance schema

`manifest.yaml` is the versioned inventory of official public authorities. `provenance.yaml`
maps every runtime rule ID in `collabproof.spec.RULES` to exact locations and review evidence.
Both files are part of the deterministic rule bundle.

## Manifest fields

- `schema_version`, `bundle_version`, `jurisdiction`, `as_of`: bundle identity and applicability.
- `sources[].id`: stable local identifier.
- `title`, `authority`, `official_url`, `jurisdiction`: source identity. URLs must be HTTPS and
  official; the validator currently allowlists `incometaxindia.gov.in` and `cbic-gst.gov.in`.
- `effective_from`, `effective_to`, `retrieved_on`: ISO dates. `effective_to` may be null only for
  an open-ended instrument.
- `sha256.state`: `verified`, `pending`, or `unavailable`. `verified` requires a 64-character
  lowercase digest. A pending/unavailable entry requires an honest `reason`.
- `redistribution.status`: `permitted`, `prohibited`, or `not_confirmed`.
  `snapshot_allowed` is true only with documented permission.
- `snapshot`: repository-relative checked-in path, or null. A snapshot is rejected unless
  redistribution is permitted and its digest matches.
- `cache_path`: ignored repository-relative local path populated from the official URL.
- `review`: `status` (`needs_independent_tax_review` or `independently_reviewed`), reviewer and date.

## Provenance fields

Each `rules.<RULE_ID>` record declares `status` (`experimental` or `production`), one or more
`sources` with `source_id` and an exact `location`, `interpretation`, non-empty `assumptions`,
`boundary_tests`, `formal_theorems` (an empty list is honest), and `review`. Production is rejected
unless both the rule review and all referenced source reviews are `independently_reviewed`.

No current rule claims independent tax/CA review. The provider-borne gross-up path is explicitly
priority `early` because its threshold-crossing treatment deserves review before other paths.
