"""
three_arms.py — the LLM experiment: does giving an LLM the legal documents close
the gap that verification closes?

  Arm A  bare       LLM sees only the deal facts.
  Arm B  grounded   LLM sees the facts + the governing legal texts (the steelman
                    for "an LLM with access to Indian tax law").
  Arm C  verified   Arm B inside the verifier loop. Retries are TRUE MULTI-TURN:
                    the conversation retains the legal materials and the model's
                    own previous answer, and the new turn adds ONLY the failing
                    rule (id + citation) — never the corrected number. Max 2
                    retries. Measures whether verification *repairs* an LLM,
                    not just grades it.

Oracle: the executable spec (collabproof/spec.py), whose own correctness is
covered by the hand-computed golden tests. Two-oracle hygiene as in run_eval.py.

Fairness features:
  * The LLM may abstain: {"cannot_determine": true, "reason": ...} — so the
    "confidently wrong" metric is fair.
  * Temperature 0; per-case assistant answers saved verbatim for audit (prompt
    templates live in this file; corpus files are versioned — full transcripts
    are reconstructible).
  * Corpus ships as paraphrase-with-citations; REPLACE WITH OFFICIAL TEXT
    before publishing (see experiments/corpus/00_README.md).

INTEGRITY: this file publishes nothing by itself. Without an API key it exits.
`--selftest` runs a scripted answerer purely to test plumbing — including the
multi-turn retry path — and is labeled as such. No LLM numbers exist until you
run with a key.

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

from collabproof import RULES, Status, assess, assessment_as_claim, naive_answer, verify
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

# Sent as a FOLLOW-UP TURN in the same conversation: the legal materials and
# your previous answer are above in the transcript. Only the failing rule is
# named; the correct value is withheld.
FEEDBACK = """Your answer above could not be certified. A deterministic checker
encoding the legal materials above found these violations (the rule that breaks
is named; the correct value is NOT given — re-derive it from the materials):

{violations}

Revise your full answer. {schema}"""


def load_corpus() -> str:
    parts = []
    cdir = os.path.join(HERE, "corpus")
    for f in sorted(os.listdir(cdir)):
        if f.endswith(".md") and not f.startswith("00_"):
            parts.append(open(os.path.join(cdir, f)).read())
    return "\n\n".join(parts)


def call_llm(messages: list, model: str) -> str:
    """messages: full [{role, content}, ...] transcript — retries keep context."""
    key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({
        "model": model, "max_tokens": 1200, "temperature": 0,
        "messages": messages,
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
    lines = [f"- field `{m.fld}` violates [{m.rule_id}]: {RULES[m.rule_id].citation}"
             for m in cert.mismatches]
    if cert.status == Status.AMBIGUOUS:
        lines.append("- cash TDS matches one branch of the s.194J/s.194C overlap "
                     "but states no statutory basis; state the basis you rely on.")
    return "\n".join(lines) or "- (no field details available)"


INCOMPLETE_FEEDBACK = """Your answer above is incomplete: it asserts nothing
false, but leaves required fields unanswered:

{missing}

An unanswered field is not a certified field. Either assert every required
field or state cannot_determine=true with a reason. {schema}"""

CLAIM_FIELDS = ("tds_194r_paise", "release_gate_required", "cash_tds_paise",
                "gst_registration_required", "gst_liability_paise")


def required_fields(collab):
    """Experiment-level completeness for an in-scope case. gst_liability is
    required only when the creator is registered (when not registered the
    spec's answer IS None, so null is the correct complete answer)."""
    req = ["tds_194r_paise", "release_gate_required", "cash_tds_paise",
           "gst_registration_required"]
    if collab.creator.gst_registered:
        req.append("gst_liability_paise")
    return req


def experiment_status(claim, raw, collab):
    """The experiment's own verdict for ONE attempt (initial or retry).

    verify() certifies only what is asserted — correct for a certifier API,
    but as an experiment success metric it would let empty/partial claims and
    abstentions count as wins. This wrapper closes that gap:
      * cannot_determine counts as abstention on EVERY attempt;
      * in-scope: all required fields must be asserted (basis handled by
        verify()'s AMBIGUOUS when the fork is material);
      * out-of-scope: the complete answer is an explicit refusal with no
        asserted outcomes; assertions take precedence over a refusal flag.
    Precedence: wrong beats missing (REJECTED > INCOMPLETE)."""
    a = assess(collab)
    abstained = isinstance(raw, dict) and bool(raw.get("cannot_determine"))
    if not a.ok:
        asserted = any(getattr(claim, f) is not None for f in CLAIM_FIELDS)
        if asserted:
            return "ASSERTED_ON_OUT_OF_SCOPE", None, []
        if abstained:
            return "CORRECT_REFUSAL", None, []
        return "SILENT_ON_OUT_OF_SCOPE", None, []
    if abstained:
        return "ABSTAINED", None, []
    cert = verify(claim, collab)
    if cert.status == Status.REJECTED:
        return "REJECTED", cert, []
    if cert.status == Status.AMBIGUOUS:
        return "AMBIGUOUS", cert, []
    missing = [f for f in required_fields(collab) if getattr(claim, f) is None]
    if missing:
        return "INCOMPLETE", cert, missing
    return "CERTIFIED_COMPLETE", cert, []


class LlmAnswerer:
    """Conversation-per-case answerer. start() opens the transcript;
    retry() APPENDS to it, so corpus + prior answers stay in context."""

    def __init__(self, initial_prompt_of, model):
        self.initial_prompt_of = initial_prompt_of
        self.model = model
        self.transcript = None

    def _call(self):
        time.sleep(0.4)
        text = call_llm(self.transcript, self.model)
        self.transcript = self.transcript + [{"role": "assistant", "content": text}]
        return parse_claim(text)

    def start(self, collab):
        self.transcript = [{"role": "user",
                            "content": self.initial_prompt_of(collab)}]
        return self._call()

    def retry(self, collab, feedback):
        self.transcript = self.transcript + [{"role": "user", "content": feedback}]
        return self._call()

    def answers_so_far(self):
        return [m["content"] for m in (self.transcript or [])
                if m["role"] == "assistant"]


class ScriptedAnswerer:
    """--selftest only: exercises plumbing incl. the multi-turn retry path AND
    the abstention/incompleteness handling the metric depends on. NEVER a result.
      * default: first answer = naive baseline; retry = the spec's own answer.
      * case ABSTAIN_ON_RETRY_CASE: retry abstains — must count as abstention,
        never as fixed_after_feedback (regression test for the certified-
        abstention hole).
      * case PARTIAL_INITIAL_CASE: first answer asserts one correct field and
        nothing else — must count INCOMPLETE, not CERTIFIED."""

    ABSTAIN_ON_RETRY_CASE = 2
    PARTIAL_INITIAL_CASE = 4

    def __init__(self, index):
        self.index = index
        self.transcript = []

    def start(self, collab):
        self.transcript = [{"role": "user", "content": "[selftest initial]"},
                           {"role": "assistant", "content": "[scripted]"}]
        if self.index == self.PARTIAL_INITIAL_CASE:
            a = assess(collab)
            return (Claim(gst_registration_required=a.d("gst_registration_required")),
                    {"selftest": True})
        return naive_answer(collab), {"selftest": True}

    def retry(self, collab, feedback):
        self.transcript += [{"role": "user", "content": feedback},
                            {"role": "assistant", "content": "[scripted retry]"}]
        if self.index == self.ABSTAIN_ON_RETRY_CASE:
            return Claim(), {"selftest": True, "cannot_determine": True,
                             "reason": "scripted abstention"}
        a = assess(collab)
        return (assessment_as_claim(a) if a.ok else Claim()), {"selftest": True}

    def answers_so_far(self):
        return [m["content"] for m in self.transcript if m["role"] == "assistant"]


RETRYABLE = {"REJECTED", "AMBIGUOUS", "INCOMPLETE"}


def feedback_for(status, cert, missing):
    if status == "INCOMPLETE":
        return INCOMPLETE_FEEDBACK.format(
            missing="\n".join(f"- `{f}`" for f in missing), schema=SCHEMA)
    return FEEDBACK.format(violations=violations_text(cert), schema=SCHEMA)


def run_arm(name, cases, make_answerer, feedback_capable, model):
    """Every attempt — initial or retry — is judged by experiment_status(),
    so abstentions and partial answers can never score as certified."""
    tally, per_case = Counter(), []
    confidently_wrong = 0
    fixed_after_feedback = 0
    abstained_after_feedback = 0
    for i, c in enumerate(cases):
        ans = make_answerer(i)
        claim, raw = ans.start(c)
        if claim is None:
            tally["unparseable"] += 1
            per_case.append({"case": i, "first_status": "unparseable", "raw": raw})
            continue
        status, cert, missing = experiment_status(claim, raw, c)
        tally[status] += 1
        if status == "REJECTED":
            confidently_wrong += 1
        entry = {"case": i, "first_status": status,
                 "mismatches": ([m.explain() for m in cert.mismatches]
                                if cert else []),
                 "missing": missing}

        if feedback_capable and status in RETRYABLE:
            for attempt in range(2):
                fb = feedback_for(status, cert, missing)
                claim2, raw2 = ans.retry(c, fb)
                if claim2 is None:
                    entry.setdefault("retries", []).append(
                        {"attempt": attempt + 1, "status": "unparseable"})
                    break
                status, cert, missing = experiment_status(claim2, raw2, c)
                entry.setdefault("retries", []).append(
                    {"attempt": attempt + 1, "status": status})
                if status == "CERTIFIED_COMPLETE":
                    fixed_after_feedback += 1
                    tally["fixed_after_feedback"] += 1
                    break
                if status == "ABSTAINED":      # terminal: abstention is an
                    abstained_after_feedback += 1   # answer, never a fix
                    tally["abstained_after_feedback"] += 1
                    break
                if status not in RETRYABLE:
                    break
        entry["answers"] = ans.answers_so_far()   # assistant turns, for audit
        per_case.append(entry)
    return {"arm": name, "model": model, "n": len(cases), "tally": dict(tally),
            "confidently_wrong_first_answer": confidently_wrong,
            "abstained_first_answer": tally.get("ABSTAINED", 0)
                                      + tally.get("CORRECT_REFUSAL", 0),
            "fixed_after_feedback": fixed_after_feedback,
            "abstained_after_feedback": abstained_after_feedback,
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
        print("(exercises: multi-turn retries, abstention-on-retry, partial-")
        print(" initial-answer — the metric holes this harness must not have)")
        print("=" * 66)
        reports = [run_arm("selftest-scripted-with-retries", cases,
                           lambda i: ScriptedAnswerer(i), True, "none")]
        out = os.path.join(HERE, "results_selftest.json")
        if args.n == 50:   # self-checking plumbing: expected counts, asserted
            t = reports[0]["tally"]
            expect = {"CERTIFIED_COMPLETE": 6, "REJECTED": 38, "AMBIGUOUS": 3,
                      "INCOMPLETE": 1, "ASSERTED_ON_OUT_OF_SCOPE": 2,
                      "fixed_after_feedback": 41, "abstained_after_feedback": 1}
            for k, v in expect.items():
                assert t.get(k) == v, f"selftest expectation failed: {k}: {t.get(k)} != {v}"
            assert reports[0]["fixed_after_feedback"] == 41
            assert reports[0]["abstained_after_feedback"] == 1
            # A refusal flag must not launder asserted numbers on an
            # out-of-scope pattern into CORRECT_REFUSAL.
            mixed_status, _, _ = experiment_status(
                Claim(tds_194r_paise=1),
                {"cannot_determine": True, "reason": "scripted refusal"},
                cases[-1])
            assert mixed_status == "ASSERTED_ON_OUT_OF_SCOPE"
            print("selftest expectations: ALL PASSED (abstention never counted "
                  "as fixed; partial answer never counted as certified; "
                  "assertions cannot hide behind refusal)")
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set. No LLM numbers are invented; "
                  "run --selftest to check plumbing.")
            return 1

        bare_of = lambda c: BARE.format(facts=facts_of(c), schema=SCHEMA)
        grounded_of = lambda c: GROUNDED.format(
            corpus=corpus, facts=facts_of(c), schema=SCHEMA)

        reports = [
            run_arm("A-bare", cases,
                    lambda i: LlmAnswerer(bare_of, args.model), False, args.model),
            run_arm("B-grounded", cases,
                    lambda i: LlmAnswerer(grounded_of, args.model), False, args.model),
            run_arm("C-verified-loop", cases,
                    lambda i: LlmAnswerer(grounded_of, args.model), True, args.model),
        ]
        # Dead-zone probe: raw answers saved verbatim for quotation, unscored.
        probes = {}
        for label, block in [("bare", ""), ("grounded",
                              "\n=== LEGAL MATERIALS ===\n" + corpus)]:
            probes[label] = call_llm(
                [{"role": "user",
                  "content": DEADZONE_PROBE.format(corpus_block=block)}],
                args.model)
        out = os.path.join(HERE, "results.json")

    for r in reports:
        print(f"\n=== {r['arm']} (n={r['n']}, model={r['model']}) ===")
        for k, v in sorted(r["tally"].items()):
            print(f"  {k:<24} {v}")
        print(f"  confidently-wrong (first answer): {r['confidently_wrong_first_answer']}")
        print(f"  abstained (first): {r['abstained_first_answer']}   "
              f"fixed-after-feedback: {r['fixed_after_feedback']}   "
              f"abstained-after-feedback: {r['abstained_after_feedback']}")

    payload = {"reports": reports}
    if not args.selftest:
        payload["deadzone_probe_raw"] = probes
        payload["note"] = ("Spec-as-oracle: 'wrong' means 'disagrees with the "
                          "published executable spec'; audit the spec via its "
                          "golden tests. Retries are multi-turn (corpus + prior "
                          "answer retained). Corpus was placeholder/official per "
                          "experiments/corpus/00_README.md at time of run.")
    with open(out, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
