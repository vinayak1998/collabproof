# Repo structure & website build guide

For rebuilding the front-end (in Cursor or anywhere) without breaking the one thing that makes
this project credible: **the UI may change freely; the engine and its parity chain may not.**

## The invariant (read this before touching anything)

```
Python assess + verify ──gen_parity_vectors.py──▶ parity_vectors.js ──runParity()──▶ JS engine
(source of truth)                           (assessment + verifier fixtures)          (site runtime)
```

The Python package is the source of truth for the rules. The JS engine is a hand-port, and the
only reason it can be trusted is that every build replays 62 assessment fixtures and 15
adversarial verifier fixtures computed by Python (in CI *and* in the browser). Whatever website
you build:

1. **Never edit `parity_vectors.js` by hand.** Regenerate: `python gen_parity_vectors.py`.
2. **Never fork the rule logic into UI code.** Components call `assess()` / `verify()` — no
   tax arithmetic in components, ever. If the UI needs a derived number, add it to the engine
   and to the Python spec, regenerate vectors, and let CI prove the port.
3. **Ship the parity badge.** If `runParity()` fails, the site should say so loudly. A
   verification demo that doesn't verify itself is marketing.
4. Rule changes always land in `collabproof/spec.py` first (with a golden test), then in
   `docs/collabproof.js`, then `gen_parity_vectors.py` → CI green.

## Repo tree (what ships, what it's for)

```
collabproof/
├── README.md                    # results, claims, limitations — the public story
├── REPO_STRUCTURE.md            # this file
├── LICENSE                      # MIT (code only; not advice)
├── pyproject.toml               # project and pytest configuration
├── requirements-dev.txt         # pinned test/proof dependencies used by CI
├── sources/                     # official-source manifest, provenance, ignored cache
├── docs/source-governance.md    # review, hashing, maintenance and staleness workflow
├── .github/workflows/ci.yml     # tests + Z3 + fixture freshness + JS↔Python parity
│
├── collabproof/                 # ← SOURCE OF TRUTH (Python package)
│   ├── spec.py                  #   the rules: s.194R, 194J/194C fork, GST; citations inline
│   ├── verify.py                #   complete typed claims + six fail-closed verdicts
│   ├── baseline.py              #   the "modal misunderstanding" calculator (demo adversary)
│   └── llm_adapter.py           #   optional LLM answerer (runs only with an API key)
│
├── tests/
│   ├── test_golden.py           # 12 hand-computed statutory outcomes (oracle: human)
│   ├── test_verify.py           # adversarial completeness/type/causal-rule checks
│   ├── test_llm_boundary.py     # prompt/schema/refusal/retry regressions
│   └── test_properties.py       # 7 Hypothesis invariant classes (~2,900 cases)
│
├── proofs/
│   └── prove_cliff.py           # Z3 dead-zone proofs + 100k enumeration + spec binding
│
├── run_eval.py                  # answerers vs spec (oracle: spec); writes eval/results.json
├── gen_parity_vectors.py        # Python → docs/parity_vectors.js (the frozen answers)
│
└── docs/                        # ← WEB LAYER (currently: reference prototype, Pages-ready)
    ├── collabproof.js           #   THE ENGINE (UMD: browser + Node). Portable. Sacred.
    ├── parity_vectors.js        #   generated — do not hand-edit
    ├── parity_check_node.js     #   CI-side parity twin
    └── index.html               #   reference UI — treat as a functional wireframe to replace
```

`eval/` output (cases.json, results.json) is generated; commit it if you want the numbers
browsable on GitHub, or gitignore it and let CI regenerate.

## Building the real website

**Recommended shape:** keep this repo as the truth + engine, and build the site as a `web/`
app in the same repo (Vite + React or Next.js — anything). The app imports two files from
`docs/` (or move them to `engine/` and update the two require paths in
`parity_check_node.js` and `ci.yml`):

- `collabproof.js` — the engine (works as an ES-consumable UMD module)
- `parity_vectors.js` — the frozen truth

**Engine API contract (everything the UI may call):**

```js
const cp = require("./collabproof.js");   // or window.collabproof in a <script> build

cp.assess(facts)   // → { ok, tds_194r, tds_rules[], aggregate, gate, fork{}, fork_material,
                   //     gst_supply, gst_turnover_after, gst_reg, gst_liability, notes[] }
                   //   or { ok:false, refusal:"SCOPE-RESIDENT"|..., note }
cp.verify(claim, facts)  // → { status: "CERTIFIED"|"INCOMPLETE"|"INVALID"|"REJECTED"|
                         //             "AMBIGUOUS"|"OUT_OF_SCOPE",
                         //     required_fields[], checked_fields[], missing_fields[],
                         //     mismatches:[{field, claimed, expected, rule}], ... }
cp.naive(facts)          // → a plausible-but-wrong Claim (for the comparison demo)
cp.runParity(window.PARITY_VECTORS)  // → { total, failures[] }  — show the badge
cp.RULES                 // rule_id → citation text (render these; never hardcode citations)
cp.DEFAULTS              // the facts object shape with defaults
```

**Units:** every amount in/out of the engine is **integer paise**. Convert at the UI boundary
only (`₹ = paise/100`); never do rule math in rupees or floats.

**Pages worth building** (the prototype crams all of this into one screen — unpack it):

1. `/` — landing: what this is, the dead-zone headline finding, parity badge, CTA to the checker.
2. `/check` — the deal checker: facts form → determinations, each amount with its rule chip
   (click chip → citation popover from `cp.RULES`). Refusals get a distinct full-panel state.
3. `/certify` — paste-your-answer mode (yours / your CA's / an LLM's) → verdict + exact failing
   rule; "load the naive calculator's answer" as the one-click demo.
4. `/cliff` — the dead-zone explainer: interactive chart (PAN toggle moves the zone
   20,001–22,222 ↔ 20,001–24,999), the Z3 claims listed with PROVED tags, link to
   `proofs/prove_cliff.py`.
5. `/methodology` — scope, FY 2024-25 pin, two-oracle eval design, limitations, "not advice."

**UX states the design must treat as first-class** (this is the product's whole point):
CERTIFIED (green), INCOMPLETE (missing coverage), INVALID (bad schema), REJECTED (red, with the
decision rule), **AMBIGUOUS** (amber — matched a branch of the 194J/194C fork without stating a
basis), and **REFUSED/OUT_OF_SCOPE** (violet — non-resident, no business nexus). Don't bury the
non-green states; they're the thesis.

## Hosting

- **Static is enough** — the engine is client-side; there is no backend.
  - GitHub Pages: free, current `docs/` deploys as-is (Settings → Pages → `/docs`).
  - **Vercel or Netlify (recommended for the real site):** import the repo, build the `web/`
    app, custom domain (e.g. `collabproof.in` / a subdomain of your site) with automatic HTTPS.
- Keep CI as the merge gate; add the site build to it. Optional: a deploy check that runs
  `node docs/parity_check_node.js` post-build so a bad engine bundle can't ship.
- Meta/SEO: title "collabproof — certify the tax treatment of a creator barter deal", OG image
  of the dead-zone chart, footer disclaimers on every page (FY pin · educational · not advice).

## Prompt seed for Cursor

> Build a small marketing+tool website (Vite+React+Tailwind) inside `web/`, using
> `docs/collabproof.js` and `docs/parity_vectors.js` as an untouchable engine layer. Pages:
> landing, /check, /certify, /cliff, /methodology as specified in REPO_STRUCTURE.md. All money
> handled as integer paise at the engine boundary. Render rule citations only via `cp.RULES`.
> Six verdict states (certified/incomplete/invalid/rejected/ambiguous/refused) each get distinct, prominent visual
> treatment. Run `cp.runParity` on app mount and render a pass/fail badge in the header. No tax
> logic anywhere in components. Keep the existing `docs/index.html` untouched as a reference
> implementation.
```
