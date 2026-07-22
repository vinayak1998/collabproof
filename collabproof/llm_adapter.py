"""
collabproof.llm_adapter — pluggable LLM answerer slot.

INTEGRITY NOTE: this repo publishes NO LLM accuracy numbers, because no LLM
was called during its construction (no API key in the build environment).
Run `python run_eval.py --llm` with ANTHROPIC_API_KEY set to produce them.
Everything reported in README.md was actually executed.

The model boundary is intentionally strict. A syntactically valid JSON value
is not automatically a valid answer: every schema key must be present, types
must match exactly (``bool`` is not accepted as an integer), unknown keys are
rejected, and a refusal cannot also contain asserted outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Optional

from .spec import Collab, assess
from .verify import CLAIM_FIELDS, Certificate, Claim, Status, verify


MONEY_FIELDS = ("tds_194r_paise", "cash_tds_paise", "gst_liability_paise")
BOOL_FIELDS = ("release_gate_required", "gst_registration_required")
ALLOWED_CASH_TDS_BASES = {"IT-194J-PROF", "IT-194C-WORK"}
OUTPUT_FIELDS = CLAIM_FIELDS + ("cannot_determine", "reason")

STRICT_OUTPUT_SCHEMA = """Respond with ONLY a JSON object, amounts in integer paise:
{"tds_194r_paise": int|null, "release_gate_required": bool|null,
 "cash_tds_paise": int|null, "cash_tds_basis": "IT-194J-PROF"|"IT-194C-WORK"|null,
 "gst_registration_required": bool|null, "gst_liability_paise": int|null,
 "cannot_determine": bool, "reason": string|null}
Include every key shown and no others. If you are not certain enough to assert
one of the four scalar fields (tds_194r_paise, release_gate_required,
cash_tds_paise, gst_registration_required), set it to null; that means
unanswered, not certified. For cash_tds_basis and gst_liability_paise, null is
an explicit result (respectively: no basis asserted, or no charge computed),
not an omission. If the whole fact pattern cannot be determined under these
rules, set cannot_determine=true, leave every outcome field null, and give a
non-empty reason. A refusal cannot also assert an outcome. Stating a number or
boolean is a claim of correctness."""

PROMPT = """You are advising on Indian tax treatment of a brand-creator collaboration
(FY 2024-25, Income-tax Act 1961 + CGST Act 2017). Facts:

{facts}

{schema}"""


@dataclass(frozen=True)
class ParsedLlmAnswer:
    """Result of validating one raw model response.

    ``claim`` is populated for every schema-valid response, including a valid
    abstention or an incomplete all-null response. Invalid output never crosses
    the model boundary as a ``Claim``.
    """

    claim: Optional[Claim]
    raw: object
    invalid_reason: Optional[str] = None

    @property
    def valid(self) -> bool:
        return self.invalid_reason is None

    @property
    def cannot_determine(self) -> bool:
        return bool(self.valid and isinstance(self.raw, dict)
                    and self.raw["cannot_determine"] is True)


@dataclass(frozen=True)
class LlmVerdict:
    """Experiment-safe classification of a validated model answer."""

    status: str
    certificate: Optional[Certificate] = None
    missing: tuple[str, ...] = ()


def facts_of(c: Collab) -> str:
    """Serialize every fact consumed by ``assess``.

    Keeping this list explicit makes prompt changes reviewable. Regression
    tests compare it with the domain model so an in-scope and out-of-scope case
    cannot silently become the same model input again.
    """
    return json.dumps({
        "brand_entity": c.brand.entity_type.value,
        "brand_in_business": c.brand.in_business,
        "brand_preceding_fy_business_turnover_paise": c.brand.preceding_fy_business_turnover_paise,
        "brand_preceding_fy_profession_receipts_paise": c.brand.preceding_fy_profession_receipts_paise,
        "creator_resident": c.creator.is_resident,
        "creator_pan_furnished": c.creator.pan_furnished,
        "creator_special_category_state": c.creator.special_category_state,
        "creator_gst_registered": c.creator.gst_registered,
        "fy_prior_benefits_from_brand_paise": c.creator.fy_prior_benefits_from_brand_paise,
        "fy_prior_194r_tds_paise": c.creator.fy_prior_194r_tds_paise,
        "fy_prior_cash_fees_from_brand_paise": c.creator.fy_prior_cash_fees_from_brand_paise,
        "fy_prior_cash_tds_paise": c.creator.fy_prior_cash_tds_paise,
        "fy_prior_aggregate_turnover_paise": c.creator.fy_prior_aggregate_turnover_paise,
        "cash_fee_paise": c.cash_fee_paise,
        "product_fmv_paise": c.product_fmv_paise,
        "product_retained": c.product_retained,
        "deliverable_linked": c.deliverable_linked,
        "tax_borne_by": c.tax_borne_by.value,
    }, indent=2, sort_keys=True)


def _invalid(raw: object, reason: str) -> ParsedLlmAnswer:
    return ParsedLlmAnswer(None, raw, reason)


def validate_llm_payload(raw: object) -> ParsedLlmAnswer:
    """Validate an already-decoded payload against the strict output schema."""
    if type(raw) is not dict:
        return _invalid(raw, "top-level JSON value must be an object")
    if any(type(key) is not str for key in raw):
        return _invalid(raw, "object keys must be strings")

    keys = set(raw)
    expected = set(OUTPUT_FIELDS)
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing:
        return _invalid(raw, "missing required keys: " + ", ".join(missing))
    if unknown:
        return _invalid(raw, "unknown keys: " + ", ".join(unknown))

    for field in MONEY_FIELDS:
        value = raw[field]
        if value is not None and type(value) is not int:
            return _invalid(raw, f"{field} must be an integer or null")
        if type(value) is int and value < 0:
            return _invalid(raw, f"{field} must not be negative")

    for field in BOOL_FIELDS:
        value = raw[field]
        if value is not None and type(value) is not bool:
            return _invalid(raw, f"{field} must be a boolean or null")

    basis = raw["cash_tds_basis"]
    if basis is not None and type(basis) is not str:
        return _invalid(raw, "cash_tds_basis must be a string or null")
    if basis is not None and basis not in ALLOWED_CASH_TDS_BASES:
        return _invalid(raw, "cash_tds_basis is not an allowed rule id")
    if basis is not None and raw["cash_tds_paise"] is None:
        return _invalid(raw, "cash_tds_basis cannot be asserted without cash_tds_paise")

    if type(raw["cannot_determine"]) is not bool:
        return _invalid(raw, "cannot_determine must be a boolean")
    if raw["reason"] is not None and type(raw["reason"]) is not str:
        return _invalid(raw, "reason must be a string or null")

    asserted = [field for field in CLAIM_FIELDS if raw[field] is not None]
    if raw["cannot_determine"]:
        if asserted:
            return _invalid(
                raw,
                "cannot_determine=true contradicts asserted fields: "
                + ", ".join(asserted),
            )
        if type(raw["reason"]) is not str or not raw["reason"].strip():
            return _invalid(raw, "cannot_determine=true requires a non-empty reason")

    # For four scalar outcomes, JSON null means "not asserted", so omit that
    # constructor argument and let Claim retain its missing-value sentinel.
    # Two outcomes legitimately have null as the complete result and therefore
    # must preserve explicit None: no cash basis when the fork is immaterial,
    # and no computed GST liability for an unregistered creator.
    claim_kwargs = {
        "cash_tds_basis": basis,
        "gst_liability_paise": raw["gst_liability_paise"],
    }
    for field in (
        "tds_194r_paise",
        "release_gate_required",
        "cash_tds_paise",
        "gst_registration_required",
    ):
        if raw[field] is not None:
            claim_kwargs[field] = raw[field]
    claim = Claim(**claim_kwargs)
    return ParsedLlmAnswer(claim, raw)


def parse_llm_output(text: str) -> ParsedLlmAnswer:
    """Parse an answer that must contain *only* the JSON object."""
    def reject_duplicate_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate object key: {key}")
            obj[key] = value
        return obj

    try:
        raw = json.loads(text.strip(), object_pairs_hook=reject_duplicate_keys)
    except (TypeError, ValueError) as exc:
        return _invalid(text, f"invalid JSON: {exc}")
    return validate_llm_payload(raw)


def payload_for_claim(
    claim: Claim,
    *,
    cannot_determine: bool = False,
    reason: Optional[str] = None,
) -> dict:
    """Build a schema-complete payload for deterministic test answerers."""
    def money_or_null(value):
        return value if type(value) is int else None

    def bool_or_null(value):
        return value if type(value) is bool else None

    basis = claim.cash_tds_basis
    if basis is not None and type(basis) is not str:
        basis = None
    return {
        "tds_194r_paise": money_or_null(claim.tds_194r_paise),
        "release_gate_required": bool_or_null(claim.release_gate_required),
        "cash_tds_paise": money_or_null(claim.cash_tds_paise),
        "cash_tds_basis": basis,
        "gst_registration_required": bool_or_null(claim.gst_registration_required),
        "gst_liability_paise": money_or_null(claim.gst_liability_paise),
        "cannot_determine": cannot_determine,
        "reason": reason,
    }


def classify_llm_answer(parsed: ParsedLlmAnswer, c: Collab) -> LlmVerdict:
    """Classify one response without allowing partial/empty verifier wins.

    This is shared by the legacy evaluator and the three-arm experiment so the
    two paths cannot silently use different success definitions.
    """
    if not parsed.valid or parsed.claim is None:
        return LlmVerdict("INVALID_OUTPUT")

    claim = parsed.claim
    a = assess(c)
    if not a.ok:
        if parsed.cannot_determine:
            return LlmVerdict("CORRECT_REFUSAL")
        # In the model schema, null means "not asserted". Inspect the raw
        # payload here rather than Claim's internal missing-value sentinel.
        asserted = any(parsed.raw[field] is not None for field in CLAIM_FIELDS)
        if asserted:
            return LlmVerdict("ASSERTED_ON_OUT_OF_SCOPE")
        return LlmVerdict(
            "INCOMPLETE", missing=("cannot_determine", "reason"))

    if parsed.cannot_determine:
        return LlmVerdict("ABSTAINED")

    cert = verify(claim, c)
    if cert.status == Status.INVALID:
        return LlmVerdict("INVALID_OUTPUT", cert)
    if cert.status == Status.REJECTED:
        return LlmVerdict("REJECTED", cert)
    if cert.status == Status.AMBIGUOUS:
        return LlmVerdict("AMBIGUOUS", cert)

    if cert.status == Status.INCOMPLETE:
        return LlmVerdict(
            "INCOMPLETE", cert, tuple(getattr(cert, "missing_fields", ())))
    if cert.status == Status.CERTIFIED:
        return LlmVerdict("CERTIFIED_COMPLETE", cert)
    # Defensive fallback if the core certifier adds a status without updating
    # this experiment boundary.
    return LlmVerdict("INVALID_OUTPUT", cert)


def _raw_llm_answer(c: Collab, model: str) -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    # Imported lazily so using the deterministic evaluator has no HTTP side
    # effects and needs no third-party client package.
    import urllib.request

    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": PROMPT.format(
                facts=facts_of(c), schema=STRICT_OUTPUT_SCHEMA),
        }],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    return out["content"][0]["text"]


def llm_response(
    c: Collab,
    model: str = "claude-sonnet-5",
) -> Optional[ParsedLlmAnswer]:
    """Return a validated model response, or ``None`` when no key is set."""
    text = _raw_llm_answer(c, model)
    return None if text is None else parse_llm_output(text)


def llm_answer(c: Collab, model: str = "claude-sonnet-5") -> Optional[Claim]:
    """Compatibility wrapper returning only a schema-valid non-refusal claim.

    Evaluators should use :func:`llm_response` so invalid output and abstention
    remain observable instead of being collapsed into ``None``.
    """
    parsed = llm_response(c, model)
    if parsed is None or not parsed.valid or parsed.cannot_determine:
        return None
    return parsed.claim
