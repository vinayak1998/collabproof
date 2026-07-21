"""
three_arms.py — the LLM experiment: does giving an LLM the legal documents close
the gap that verification closes?

  Arm A  bare       LLM sees only the deal facts.
  Arm B  grounded   LLM sees the facts + the governing legal texts (the steelman
                    for "an LLM with access to Indian tax law").
  Arm C  verified   Arm B inside the verifier loop: the checker certifies or
                    rejects; on rejection ONLY the failing rule (id + citation)
                    is fed back — never the corrected number — and the LLM
                    retries (max 2). Measures whether verification *repairs* an
                    LLM, not just grades it.

Oracle: the executable spec (collabproof/spec.py), whose own correctness is
covered by the hand-computed golden tests. Two-oracle hygiene as in run_eval.py.

Fairness features:
  * The LLM may abstain: {"cannot_determine": true, "reason": ...} — so the
    "confidently wrong" metric is fair.
  * Temperature 0; prompts and raw responses saved verbatim for audit.
  * Corpus ships as paraphrase-with-citations; REPLACE WITH OFFICIAL TEXT
    before publishing (see experiments/corpus/00_README.md).

INTEGRITY: this file publishes nothing by itself. Without an API key it exits.
`--selftest` runs a scripted answerer purely to test plumbing and is labeled as
such in its output file. No LLM numbers exist until you run with a key.

Usage:
  python experiments/three_arms.py --selftest
  ANTHROPIC_API_KEY=... python experiments/three_arms.py --model claude-sonnet-5 --n 50
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collabproof import Status, assess, naive_answer, verify
from collabproof.llm_adapter import facts_of
from collabproof.verify import Claim
from run_eval import build_cases

HERE = os.path.dirname(__file__)

SCHEMA = """Respond with ONLY a JSON object, amounts in integer paise:
{"tds_194r_paise": int|null, "release_gate_required": bool|null,
 "cash_tds_paise": int|null, "cash_tds_basis": "IT-194J-PROF"|"IT-194C-WORK"|null,
 "gst_registration_required": bool|null, "gst_liability_paise": int|null,
 "cannot_determine": bool, "reason": string|null}
If you are not certain enough to assert a field, set it to null. If the whole
fact pattern cannot be determined under these rules, set cannot_determine=true
and explain in reason. Stating a number is a claim of correctness."""

BARE = """You are advising on the Indian tax treatment (FY 2024-25) of a
brand-creator collaboration. Facts:

{facts}

{schema}"""

GROUNDED = """You are advising on the Indian tax treatment (FY 2024-25) of a
brand-creator collaboration. The governing legal materials are provided below.
Ground every number in them.

=== LEGAL MATERIALS ===
{corpus}
=== END MATERIALS ===

Facts of the deal:

{facts}

{schema}"""

FEEDBACK = """Your previous answer could not be certified. A deterministic
checker encoding the same legal materials found these violations (the rule that
breaks is named; the correct value is NOT given — re-derive it):

{violations}

Revise your answer. {schema}"""


def load_corpus() -> str:
    parts = []
    cdir = os.path.join(HERE, "corpus")
    for f in sorted(os.listdir(cdir)):
        if f.endswith(".md") and not f.startswith("00_"):
            parts.append(open(os.path.join(cdir, f)).read())
    return "\n\n".join(parts)


def call_llm(prompt: str, model: str) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({
        "model": model, "max_tokens": 1200, "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        out = json.loads(resp.read())
    return out["content"][0]["text"]


def parse_claim(text: str):
    try:
        s = text[text.find("{"): text.rfind("}") + 1]
        d = json.loads(s)
    except Exception:
        return None, {"parse_error": True, "raw": text}
    claim = Claim(
        tds_194r_paise=d.get("tds_194r_paise"),
        release_gate_required=d.get("release_gate_required"),
        cash_tds_paise=d.get("cash_tds_paise"),
        cash_tds_basis=d.get("cash_tds_basis"),
        gst_registration_required=d.get("gst_registration_required"),
        gst_liability_paise=d.get("gst_liability_paise"),
    )
    return claim, d


def violations_text(cert) -> str:
    lines = [f"- field `{m.fld}` violates [{m.rule_id}]: "
             f"{__import__('collabproof').RULES[m.rule_id].citation}"
             for m in cert.mismatches]
    if cert.status == Status.AMBIGUOUS:
        lines.append("- cash TDS matches one branch of the s.194J/s.194C overlap "
                     "but states no statutory basis; state the basis you rely on.")
    return "\n".join(lines) or "- (no field details available)"


def scripted_answerer(collab, _prompt_kind):
    """--selftest only: exercises plumbing. NEVER a result."""
    claim = naive_answer(collab)
    return claim, {"selftest": True}


def run_arm(name, cases, answer_fn, feedback_capable, model):
    tally, per_case = Counter(), []
    confidently_wrong = 0
    abstained = 0
    fixed_after_feedback = 0
    for i, c in enumerate(cases):
        claim, raw = answer_fn(c, "initial")
        if claim is None:
            tally["unparseable"] += 1
            per_case.append({"case": i, "status": "unparseable", "raw": raw})
            continue
        if isinstance(raw, dict) and raw.get("cannot_determine"):
            abstained += 1
            a = assess(c)
            tally["abstained_in_scope" if a.ok else "abstained_out_of_scope"] += 1
            per_case.append({"case": i, "status": "abstained", "raw": raw})
            continue
        cert = verify(claim, c)
        tally[cert.status.value] += 1
        if cert.status == Status.REJECTED:
            confidently_wrong += 1
        entry = {"case": i, "status": cert.status.value,
                 "mismatches": [m.explain() for m in cert.mismatches], "raw": raw}

        if feedback_capable and cert.status in (Status.REJECTED, Status.AMBIGUOUS):
            for attempt in range(2):
                fb = FEEDBACK.format(violations=violations_text(cert), schema=SCHEMA)
                claim2, raw2 = answer_fn(c, ("retry", fb))
                if claim2 is None:
                    break
                cert = verify(claim2, c)
                entry.setdefault("retries", []).append(
                    {"attempt": attempt + 1, "status": cert.status.value})
                if cert.status == Status.CERTIFIED:
                    fixed_after_feedback += 1
                    tally["fixed_after_feedback"] += 1
                    break
        per_case.append(entry)
    return {"arm": name, "model": model, "n": len(cases), "tally": dict(tally),
            "confidently_wrong_first_answer": confidently_wrong,
            "abstained": abstained,
            "fixed_after_feedback": fixed_after_feedback,
            "per_case": per_case}


DEADZONE_PROBE = """Under Indian tax law (FY 2024-25, s.194R), a brand offers a
creator a choice between a gifted product worth exactly Rs 20,000 or one worth
Rs 21,000 (no cash fee; product retained; creator funds any tax herself as
required before release of an in-kind benefit). Which gift leaves the creator
better off in immediate cash-adjusted terms, and by how much? Show the numbers.
{corpus_block}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    cases = build_cases()[: args.n]
    corpus = load_corpus()

    if args.selftest:
        print("=" * 66)
        print("SELFTEST — scripted answerer, PLUMBING CHECK ONLY, NOT RESULTS")
        print("=" * 66)
        def fn(c, kind): return scripted_answerer(c, kind)
        reports = [run_arm("selftest-scripted", cases, fn, False, "none")]
        out = os.path.join(HERE, "results_selftest.json")
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set. No LLM numbers are invented; "
                  "run --selftest to check plumbing.")
            return 1

        def mk(prompt_of):
            def fn(c, kind):
                if isinstance(kind, tuple):      # retry with feedback
                    prompt = kind[1] + "\n\nFacts again:\n" + facts_of(c)
                else:
                    prompt = prompt_of(c)
                time.sleep(0.4)
                return parse_claim(call_llm(prompt, args.model))
            return fn

        bare_fn = mk(lambda c: BARE.format(facts=facts_of(c), schema=SCHEMA))
        grounded_fn = mk(lambda c: GROUNDED.format(
            corpus=corpus, facts=facts_of(c), schema=SCHEMA))

        reports = [
            run_arm("A-bare", cases, bare_fn, False, args.model),
            run_arm("B-grounded", cases, grounded_fn, False, args.model),
            run_arm("C-verified-loop", cases, grounded_fn, True, args.model),
        ]
        # Dead-zone probe: raw answers saved verbatim for quotation, unscored.
        probes = {}
        for label, block in [("bare", ""), ("grounded",
                              "\n=== LEGAL MATERIALS ===\n" + corpus)]:
            probes[label] = call_llm(
                DEADZONE_PROBE.format(corpus_block=block), args.model)
        out = os.path.join(HERE, "results.json")

    for r in reports:
        print(f"\n=== {r['arm']} (n={r['n']}, model={r['model']}) ===")
        for k, v in sorted(r["tally"].items()):
            print(f"  {k:<24} {v}")
        print(f"  confidently-wrong (first answer): {r['confidently_wrong_first_answer']}")
        print(f"  abstained: {r['abstained']}   fixed-after-feedback: {r['fixed_after_feedback']}")

    payload = {"reports": reports}
    if not args.selftest:
        payload["deadzone_probe_raw"] = probes
        payload["note"] = ("Spec-as-oracle: 'wrong' means 'disagrees with the "
                          "published executable spec'; audit the spec via its "
                          "golden tests. Corpus was placeholder/official per "
                          "experiments/corpus/00_README.md at time of run.")
    with open(out, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
