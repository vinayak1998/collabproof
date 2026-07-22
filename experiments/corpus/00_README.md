# Corpus for Arm B (grounded LLM)

No legal text is checked in here. `experiments.three_arms.load_corpus()` builds Arm B only from
the ignored `sources/cache/` paths declared in `sources/manifest.yaml`. Run:

```bash
python -m collabproof.governance fetch
# For each cached PDF, create an adjacent UTF-8 .txt file, e.g.:
pdftotext -layout sources/cache/cbdt-circular-12-2022.pdf \
  sources/cache/cbdt-circular-12-2022.txt
```

The loader fails closed if any governed official source is missing. It never substitutes the old
author paraphrases, practitioner commentary, or any confidential material. `--selftest` does not
load a legal corpus because it tests plumbing with a scripted answerer.
