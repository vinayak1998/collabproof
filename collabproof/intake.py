"""Fail-closed controlled-English intake for the Section 194R Lean slice.

This module does not attempt general natural-language understanding.  It
accepts one documented, line-oriented controlled-English format, preserves the
exact source span for every proposed fact, and refuses to create a confirmed
case until all eleven facts consumed by Lean are explicit and conflict-free.

Offsets are Python Unicode character offsets, not UTF-8 byte offsets.  The
SHA-256 digest is nevertheless computed over the exact UTF-8 source bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
import re
import secrets
from typing import Any, Mapping, Optional

from .s194r import FACT_PATHS, MAX_MONEY_PAISE, S194RFacts
from .spec import EntityType, RULES, TaxBearer


INTAKE_SCHEMA_VERSION = "collabproof-s194r-intake-v1"
CONFIRMATION_SCHEMA_VERSION = "collabproof-s194r-confirmation-v1"
SOURCE_ID = "query"
MAX_QUERY_BYTES = 65_536
MAX_LINE_CHARACTERS = 4_096

SUPPORTED_QUESTION = (
    "Determine the Section 194R treatment for FY 2024-25."
)
_SUPPORTED_QUESTION_RE = re.compile(
    r"(?:determine|compute|what is) (?:the )?section 194r "
    r"(?:decision|treatment) for fy 2024-25[?.]?",
    re.IGNORECASE,
)


class IntakeStatus(str, Enum):
    INVALID = "INVALID"
    UNSUPPORTED_QUERY = "UNSUPPORTED_QUERY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    CONFLICTING_FACTS = "CONFLICTING_FACTS"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"


class _FrozenDict(dict):
    """JSON-serializable immutable dictionary for a canonical preimage."""

    @staticmethod
    def _immutable(*_args, **_kwargs):
        raise TypeError("confirmation payload is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class FactValueType(str, Enum):
    ENTITY_TYPE = "entity_type"
    BOOLEAN = "boolean"
    MONEY_PAISE = "money_paise"
    TAX_BEARER = "tax_bearer"


@dataclass(frozen=True)
class EvidenceSpan:
    """An exact excerpt from one immutable UTF-8 source.

    ``start`` and ``end`` use Python string character indices.  ``quote`` must
    equal ``source_text[start:end]`` before confirmation succeeds.
    """

    source_id: str
    source_sha256: str
    start: int
    end: int
    quote: str


@dataclass(frozen=True)
class ExtractedFact:
    path: str
    value_type: FactValueType
    value: object
    evidence: tuple[EvidenceSpan, ...]


@dataclass(frozen=True)
class FactConflict:
    path: str
    candidates: tuple[ExtractedFact, ...]


@dataclass(frozen=True)
class IntakeDraft:
    """Immutable result of parsing one controlled-English case."""

    schema_version: str
    case_id: str
    status: IntakeStatus
    question: str
    question_evidence: Optional[EvidenceSpan]
    source_id: str
    source_text: str
    source_sha256: str
    facts: tuple[ExtractedFact, ...]
    missing_fields: tuple[str, ...]
    conflicts: tuple[FactConflict, ...]
    clarification_questions: tuple[str, ...]
    issues: tuple[str, ...]
    specification_version: str
    specification_bundle_sha256: str
    rule_bundle_hash: str
    draft_sha256: str

    def fact(self, path: str) -> Optional[ExtractedFact]:
        return next((fact for fact in self.facts if fact.path == path), None)

    def as_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible persistence schema."""

        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "status": self.status.value,
            "intent": "section_194r_decision",
            "requested_period": "FY 2024-25",
            "question": self.question,
            "question_evidence": (
                _evidence_record(self.question_evidence)
                if self.question_evidence is not None
                else None
            ),
            "source": {
                "source_id": self.source_id,
                "text": self.source_text,
                "sha256": self.source_sha256,
            },
            "facts": [_fact_record(fact) for fact in self.facts],
            "missing_fields": list(self.missing_fields),
            "conflicts": [_conflict_record(item) for item in self.conflicts],
            "clarification_questions": list(self.clarification_questions),
            "issues": list(self.issues),
            "specification_version": self.specification_version,
            "specification_bundle_sha256": self.specification_bundle_sha256,
            "rule_bundle_hash": self.rule_bundle_hash,
            "draft_sha256": self.draft_sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "IntakeDraft":
        """Reparse a persisted draft and reject any non-derived content.

        Persistence is deliberately stricter than ordinary dataclass
        construction: the source is parsed again under the current bounded
        grammar and governance context, and every persisted field must equal
        that newly derived record.
        """

        expected_keys = {
            "schema_version",
            "case_id",
            "status",
            "intent",
            "requested_period",
            "question",
            "question_evidence",
            "source",
            "facts",
            "missing_fields",
            "conflicts",
            "clarification_questions",
            "issues",
            "specification_version",
            "specification_bundle_sha256",
            "rule_bundle_hash",
            "draft_sha256",
        }
        if type(raw) is not dict or set(raw) != expected_keys:
            raise ValueError("persisted intake draft has an unexpected schema")
        if raw["schema_version"] != INTAKE_SCHEMA_VERSION:
            raise ValueError("unsupported persisted intake schema version")
        if raw["intent"] != "section_194r_decision":
            raise ValueError("persisted intake has an unsupported intent")
        if raw["requested_period"] != "FY 2024-25":
            raise ValueError("persisted intake has an unsupported period")
        source = raw["source"]
        if type(source) is not dict or set(source) != {"source_id", "text", "sha256"}:
            raise ValueError("persisted intake source has an unexpected schema")
        if source["source_id"] != SOURCE_ID or type(source["text"]) is not str:
            raise ValueError("persisted intake source is invalid")
        case_id = raw["case_id"]
        if type(case_id) is not str:
            raise ValueError("persisted intake case_id must be a string")
        reparsed = formalize_194r(source["text"], case_id=case_id)
        if raw != reparsed.as_dict():
            raise ValueError("persisted intake draft does not match a fresh parse")
        return reparsed


@dataclass(frozen=True)
class ConfirmedCase:
    """A complete fact envelope bound to one accepted intake snapshot."""

    schema_version: str
    case_id: str
    facts: S194RFacts
    fact_evidence: tuple[ExtractedFact, ...]
    source_id: str
    source_text: str
    source_sha256: str
    draft_sha256: str
    normalized_fact_sha256: str
    source_bundle_sha256: str
    provenance_sha256: str
    specification_version: str
    specification_bundle_sha256: str
    rule_bundle_hash: str
    confirmation_payload: Mapping[str, object]
    confirmation_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.confirmation_payload, Mapping):
            raise ValueError("confirmation_payload must be a mapping")
        object.__setattr__(
            self,
            "confirmation_payload",
            _FrozenDict(self.confirmation_payload),
        )

    def certificate_record(self) -> dict[str, object]:
        """Return the exact intake record embedded in a runtime certificate."""

        return {
            "status": "CONFIRMED",
            "case_id": self.case_id,
            "intent": "section_194r_decision",
            "requested_period": "FY 2024-25",
            "draft_sha256": self.draft_sha256,
            "confirmation_payload": dict(self.confirmation_payload),
            "confirmation_sha256": self.confirmation_sha256,
            "source_bundle_sha256": self.source_bundle_sha256,
            "provenance_sha256": self.provenance_sha256,
            "normalized_fact_sha256": self.normalized_fact_sha256,
            "specification_version": self.specification_version,
            "specification_bundle_sha256": self.specification_bundle_sha256,
            "rule_bundle_hash": self.rule_bundle_hash,
        }

    def as_dict(self) -> dict[str, object]:
        """Return the strict JSON-compatible confirmed-case schema."""

        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "intent": "section_194r_decision",
            "requested_period": "FY 2024-25",
            "facts": self.facts.as_dict(),
            "fact_evidence": [_fact_record(fact) for fact in self.fact_evidence],
            "source": {
                "source_id": self.source_id,
                "text": self.source_text,
                "sha256": self.source_sha256,
            },
            "draft_sha256": self.draft_sha256,
            "normalized_fact_sha256": self.normalized_fact_sha256,
            "source_bundle_sha256": self.source_bundle_sha256,
            "provenance_sha256": self.provenance_sha256,
            "specification_version": self.specification_version,
            "specification_bundle_sha256": self.specification_bundle_sha256,
            "rule_bundle_hash": self.rule_bundle_hash,
            "confirmation_payload": dict(self.confirmation_payload),
            "confirmation_sha256": self.confirmation_sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "ConfirmedCase":
        """Load and independently validate a persisted confirmed case."""

        expected_keys = {
            "schema_version",
            "case_id",
            "intent",
            "requested_period",
            "facts",
            "fact_evidence",
            "source",
            "draft_sha256",
            "normalized_fact_sha256",
            "source_bundle_sha256",
            "provenance_sha256",
            "specification_version",
            "specification_bundle_sha256",
            "rule_bundle_hash",
            "confirmation_payload",
            "confirmation_sha256",
        }
        if type(raw) is not dict or set(raw) != expected_keys:
            raise ValueError("persisted confirmed case has an unexpected schema")
        if raw["schema_version"] != CONFIRMATION_SCHEMA_VERSION:
            raise ValueError("unsupported confirmed-case schema version")
        if raw["intent"] != "section_194r_decision":
            raise ValueError("persisted confirmed case has an unsupported intent")
        if raw["requested_period"] != "FY 2024-25":
            raise ValueError("persisted confirmed case has an unsupported period")

        case_id = raw["case_id"]
        if type(case_id) is not str or re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", case_id
        ) is None:
            raise ValueError("persisted confirmed case has an invalid case_id")
        source = raw["source"]
        if type(source) is not dict or set(source) != {"source_id", "text", "sha256"}:
            raise ValueError("persisted confirmed source has an unexpected schema")
        if source["source_id"] != SOURCE_ID or type(source["text"]) is not str:
            raise ValueError("persisted confirmed source is invalid")
        source_text = source["text"]
        source_sha256 = source["sha256"]
        if type(source_sha256) is not str or not hmac.compare_digest(
            source_sha256, _sha256(source_text.encode("utf-8"))
        ):
            raise ValueError("persisted confirmed source SHA-256 is invalid")

        # A confirmed JSON record is not authoritative merely because all of
        # its unkeyed hashes are internally consistent.  Re-run the bounded
        # parser over the persisted source and require the original draft and
        # provenance to be exactly what that parser derives.
        origin = formalize_194r(source_text, case_id=case_id)
        if origin.status is not IntakeStatus.AWAITING_CONFIRMATION:
            raise ValueError("persisted confirmed source is not confirmable")
        if raw["draft_sha256"] != origin.draft_sha256:
            raise ValueError("persisted confirmed draft SHA-256 is not source-derived")
        if raw["fact_evidence"] != [
            _fact_record(fact) for fact in origin.facts
        ]:
            raise ValueError("persisted confirmed evidence is not source-derived")
        if raw["facts"] != _to_s194r_facts(origin.facts).as_dict():
            raise ValueError("persisted confirmed facts are not source-derived")

        facts_raw = raw["facts"]
        if type(facts_raw) is not dict:
            raise ValueError("persisted normalized facts must be an object")
        facts = S194RFacts.from_dict(facts_raw)
        evidence_raw = raw["fact_evidence"]
        if type(evidence_raw) is not list:
            raise ValueError("persisted fact evidence must be a list")
        fact_evidence = tuple(_fact_from_record(item) for item in evidence_raw)
        if tuple(fact.path for fact in fact_evidence) != FACT_PATHS:
            raise ValueError("persisted fact evidence must cover FACT_PATHS in order")
        if _to_s194r_facts(fact_evidence) != facts:
            raise ValueError("persisted evidence values do not match normalized facts")
        _validate_fact_evidence(
            source_text=source_text,
            source_id=SOURCE_ID,
            source_sha256=source_sha256,
            facts=fact_evidence,
        )

        normalized_hash = _sha256(_canonical_json(facts.as_dict()))
        if raw["normalized_fact_sha256"] != normalized_hash:
            raise ValueError("persisted normalized-fact SHA-256 is invalid")
        source_bundle_hash = _source_bundle_sha256(SOURCE_ID, source_sha256)
        if raw["source_bundle_sha256"] != source_bundle_hash:
            raise ValueError("persisted source-bundle SHA-256 is invalid")
        provenance_hash = _provenance_sha256(fact_evidence)
        if raw["provenance_sha256"] != provenance_hash:
            raise ValueError("persisted provenance SHA-256 is invalid")

        specification_version, specification_bundle, bundle_hash = _current_context()
        if raw["specification_version"] != specification_version:
            raise ValueError("persisted confirmed case uses a stale specification")
        if raw["specification_bundle_sha256"] != specification_bundle:
            raise ValueError("persisted confirmed case uses stale specification sources")
        if raw["rule_bundle_hash"] != bundle_hash:
            raise ValueError("persisted confirmed case uses a stale rule bundle")
        draft_sha256 = raw["draft_sha256"]
        if type(draft_sha256) is not str or not _is_sha256(draft_sha256):
            raise ValueError("persisted draft SHA-256 is invalid")

        expected_payload = _confirmation_payload(
            case_id=case_id,
            draft_sha256=draft_sha256,
            normalized_fact_sha256=normalized_hash,
            source_bundle_sha256=source_bundle_hash,
            provenance_sha256=provenance_hash,
            specification_version=specification_version,
            specification_bundle_sha256=specification_bundle,
            rule_bundle_hash=bundle_hash,
        )
        if raw["confirmation_payload"] != expected_payload:
            raise ValueError("persisted confirmation payload is invalid")
        confirmation_hash = _sha256(_canonical_json(expected_payload))
        if raw["confirmation_sha256"] != confirmation_hash:
            raise ValueError("persisted confirmation SHA-256 is invalid")

        return cls(
            schema_version=CONFIRMATION_SCHEMA_VERSION,
            case_id=case_id,
            facts=facts,
            fact_evidence=fact_evidence,
            source_id=SOURCE_ID,
            source_text=source_text,
            source_sha256=source_sha256,
            draft_sha256=draft_sha256,
            normalized_fact_sha256=normalized_hash,
            source_bundle_sha256=source_bundle_hash,
            provenance_sha256=provenance_hash,
            specification_version=specification_version,
            specification_bundle_sha256=specification_bundle,
            rule_bundle_hash=bundle_hash,
            confirmation_payload=expected_payload,
            confirmation_sha256=confirmation_hash,
        )


@dataclass(frozen=True)
class _FieldDefinition:
    path: str
    label: str
    value_type: FactValueType
    aliases: tuple[str, ...] = ()


_FIELDS = (
    _FieldDefinition(
        "brand.entity_type", "Brand entity type", FactValueType.ENTITY_TYPE
    ),
    _FieldDefinition(
        "brand.in_business",
        "Transfer is in brand business or profession",
        FactValueType.BOOLEAN,
    ),
    _FieldDefinition(
        "brand.preceding_fy_business_turnover_paise",
        "Brand preceding-FY business turnover",
        FactValueType.MONEY_PAISE,
    ),
    _FieldDefinition(
        "brand.preceding_fy_profession_receipts_paise",
        "Brand preceding-FY profession receipts",
        FactValueType.MONEY_PAISE,
        ("Brand preceding-FY professional receipts",),
    ),
    _FieldDefinition(
        "creator.is_resident",
        "Creator is resident in India for tax purposes",
        FactValueType.BOOLEAN,
    ),
    _FieldDefinition(
        "creator.pan_furnished", "Creator PAN furnished", FactValueType.BOOLEAN
    ),
    _FieldDefinition(
        "creator.fy_prior_benefits_from_brand_paise",
        "Creator prior FY benefits from this brand",
        FactValueType.MONEY_PAISE,
    ),
    _FieldDefinition(
        "creator.fy_prior_194r_tds_paise",
        "Creator prior Section 194R TDS",
        FactValueType.MONEY_PAISE,
    ),
    _FieldDefinition(
        "transaction.product_fmv_paise",
        "Product fair market value",
        FactValueType.MONEY_PAISE,
        ("Product FMV",),
    ),
    _FieldDefinition(
        "transaction.product_retained",
        "Product retained",
        FactValueType.BOOLEAN,
    ),
    _FieldDefinition(
        "transaction.tax_borne_by", "Tax borne by", FactValueType.TAX_BEARER
    ),
)

if tuple(field.path for field in _FIELDS) != FACT_PATHS:
    raise RuntimeError("intake field registry must exactly match S194R FACT_PATHS")


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


_FIELD_BY_LABEL: dict[str, _FieldDefinition] = {}
for _field in _FIELDS:
    for _label in (_field.label, _field.path, *_field.aliases):
        _normalized = _normalized_label(_label)
        if _normalized in _FIELD_BY_LABEL:
            raise RuntimeError(f"duplicate controlled-English label: {_label}")
        _FIELD_BY_LABEL[_normalized] = _field


_CLARIFICATIONS = {
    "brand.entity_type": "What is the brand entity type: individual, HUF, firm, or company?",
    "brand.in_business": (
        "Is this transfer in the course of the brand's business or profession: yes or no?"
    ),
    "brand.preceding_fy_business_turnover_paise": (
        "What was the brand's preceding-FY business turnover in INR?"
    ),
    "brand.preceding_fy_profession_receipts_paise": (
        "What were the brand's preceding-FY profession receipts in INR?"
    ),
    "creator.is_resident": (
        "Is the creator resident in India for tax purposes: yes or no?"
    ),
    "creator.pan_furnished": "Has the creator furnished PAN: yes or no?",
    "creator.fy_prior_benefits_from_brand_paise": (
        "What prior benefits came from this brand in the same FY, in INR?"
    ),
    "creator.fy_prior_194r_tds_paise": (
        "How much prior Section 194R TDS was deposited in the same FY, in INR?"
    ),
    "transaction.product_fmv_paise": "What is the product fair market value in INR?",
    "transaction.product_retained": "Will the creator retain the product: yes or no?",
    "transaction.tax_borne_by": "Who bears the tax: recipient or provider?",
}


# Ungrouped digits or Indian grouping (for example 30,000 or 5,00,00,000),
# followed by at most two decimal places.  The only accepted scale words are
# the unambiguous Indian units ``lakh`` and ``crore``; a scaled amount cannot
# also contain grouping separators.
_INR_RE = re.compile(
    r"(?:INR[ \t]+|Rs\.?[ \t]+|₹[ \t]*)"
    r"(0|[1-9]\d*|[1-9]\d?(?:,\d{2})*,\d{3})"
    r"(?:\.(\d{1,2}))?"
    r"(?:[ \t]+(lakh|crore))?",
    re.IGNORECASE,
)


def parse_inr_to_paise(text: str) -> int:
    """Parse an exact non-negative INR amount without floating point."""

    if type(text) is not str:
        raise ValueError("money must be text")
    match = _INR_RE.fullmatch(text)
    if match is None:
        raise ValueError(
            "money must use INR, Rs, or ₹, exact digits, valid Indian grouping, "
            "an optional lakh/crore unit, and at most two decimal places"
        )
    whole = match.group(1)
    fraction = match.group(2) or ""
    unit = match.group(3).casefold() if match.group(3) else None
    if unit is not None and "," in whole:
        raise ValueError("lakh/crore amounts must not also use digit grouping")
    scale_rupees = {None: 1, "lakh": 1_00_000, "crore": 1_00_00_000}[unit]
    denominator = 10 ** len(fraction)
    numerator = int(whole.replace(",", "")) * denominator + int(fraction or "0")
    paise_numerator = numerator * scale_rupees * 100
    paise, remainder = divmod(paise_numerator, denominator)
    if remainder:
        raise ValueError("money contains a fractional paisa")
    if paise > MAX_MONEY_PAISE:
        raise ValueError(
            f"money exceeds the supported {MAX_MONEY_PAISE}-paise bound"
        )
    return paise


def _parse_value(field: _FieldDefinition, raw: str) -> object:
    if field.value_type is FactValueType.MONEY_PAISE:
        return parse_inr_to_paise(raw)
    lowered = raw.casefold()
    if field.value_type is FactValueType.BOOLEAN:
        if lowered == "yes":
            return True
        if lowered == "no":
            return False
        raise ValueError("boolean facts must be exactly yes or no")
    if field.value_type is FactValueType.ENTITY_TYPE:
        try:
            return EntityType(lowered)
        except ValueError as exc:
            raise ValueError(
                "brand entity type must be individual, huf, firm, or company"
            ) from exc
    if field.value_type is FactValueType.TAX_BEARER:
        try:
            return TaxBearer(lowered)
        except ValueError as exc:
            raise ValueError("tax bearer must be recipient or provider") from exc
    raise AssertionError(f"unhandled fact type: {field.value_type}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, (EntityType, TaxBearer, Enum)):
        return value.value
    return value


def _evidence_record(evidence: EvidenceSpan) -> dict[str, object]:
    return {
        "source_id": evidence.source_id,
        "source_sha256": evidence.source_sha256,
        "start": evidence.start,
        "end": evidence.end,
        "quote": evidence.quote,
        "offset_unit": "unicode_code_point",
    }


def _fact_record(fact: ExtractedFact) -> dict[str, object]:
    return {
        "path": fact.path,
        "value_type": fact.value_type.value,
        "value": _json_value(fact.value),
        "evidence": [_evidence_record(item) for item in fact.evidence],
    }


def _is_sha256(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _evidence_from_record(raw: object) -> EvidenceSpan:
    expected = {
        "source_id",
        "source_sha256",
        "start",
        "end",
        "quote",
        "offset_unit",
    }
    if type(raw) is not dict or set(raw) != expected:
        raise ValueError("persisted evidence has an unexpected schema")
    if raw["offset_unit"] != "unicode_code_point":
        raise ValueError("persisted evidence uses an unsupported offset unit")
    if type(raw["source_id"]) is not str or not _is_sha256(raw["source_sha256"]):
        raise ValueError("persisted evidence source identity is invalid")
    if type(raw["start"]) is not int or type(raw["end"]) is not int:
        raise ValueError("persisted evidence offsets must be integers")
    if type(raw["quote"]) is not str:
        raise ValueError("persisted evidence quote must be text")
    return EvidenceSpan(
        source_id=raw["source_id"],
        source_sha256=raw["source_sha256"],
        start=raw["start"],
        end=raw["end"],
        quote=raw["quote"],
    )


def _fact_from_record(raw: object) -> ExtractedFact:
    expected = {"path", "value_type", "value", "evidence"}
    if type(raw) is not dict or set(raw) != expected:
        raise ValueError("persisted fact has an unexpected schema")
    path = raw["path"]
    if type(path) is not str or path not in FACT_PATHS:
        raise ValueError("persisted fact path is invalid")
    try:
        value_type = FactValueType(raw["value_type"])
    except (TypeError, ValueError) as exc:
        raise ValueError("persisted fact value type is invalid") from exc
    expected_type = next(field.value_type for field in _FIELDS if field.path == path)
    if value_type is not expected_type:
        raise ValueError("persisted fact value type does not match its path")

    value = raw["value"]
    if value_type is FactValueType.BOOLEAN:
        if type(value) is not bool:
            raise ValueError("persisted boolean fact is invalid")
    elif value_type is FactValueType.MONEY_PAISE:
        if type(value) is not int or value < 0:
            raise ValueError("persisted money fact is invalid")
    elif value_type is FactValueType.ENTITY_TYPE:
        try:
            value = EntityType(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("persisted entity type is invalid") from exc
    elif value_type is FactValueType.TAX_BEARER:
        try:
            value = TaxBearer(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("persisted tax bearer is invalid") from exc

    evidence_raw = raw["evidence"]
    if type(evidence_raw) is not list or not evidence_raw:
        raise ValueError("persisted fact must have evidence")
    return ExtractedFact(
        path=path,
        value_type=value_type,
        value=value,
        evidence=tuple(_evidence_from_record(item) for item in evidence_raw),
    )


def _conflict_record(conflict: FactConflict) -> dict[str, object]:
    return {
        "path": conflict.path,
        "candidates": [_fact_record(item) for item in conflict.candidates],
    }


def _source_bundle_sha256(source_id: str, source_sha256: str) -> str:
    payload = {
        "schema_version": "collabproof-source-bundle-v1",
        "sources": [{"source_id": source_id, "sha256": source_sha256}],
    }
    return _sha256(_canonical_json(payload))


def _provenance_sha256(facts: tuple[ExtractedFact, ...]) -> str:
    payload = {
        "schema_version": "collabproof-s194r-provenance-v1",
        "facts": [_fact_record(fact) for fact in facts],
    }
    return _sha256(_canonical_json(payload))


def _confirmation_payload(
    *,
    case_id: str,
    draft_sha256: str,
    normalized_fact_sha256: str,
    source_bundle_sha256: str,
    provenance_sha256: str,
    specification_version: str,
    specification_bundle_sha256: str,
    rule_bundle_hash: str,
) -> dict[str, object]:
    """Return the complete canonical preimage for confirmation SHA-256."""

    return {
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "decision": "CONFIRMED",
        "case_id": case_id,
        "intent": "section_194r_decision",
        "requested_period": "FY 2024-25",
        "draft_sha256": draft_sha256,
        "normalized_fact_sha256": normalized_fact_sha256,
        "source_bundle_sha256": source_bundle_sha256,
        "provenance_sha256": provenance_sha256,
        "specification_version": specification_version,
        "specification_bundle_sha256": specification_bundle_sha256,
        "rule_bundle_hash": rule_bundle_hash,
    }


def _current_context() -> tuple[str, str, str]:
    # Imported lazily so runtime_proof may consume ConfirmedCase without an
    # import cycle.  Recomputed at confirmation time to detect governance drift.
    from .governance import rule_bundle_hash, validate_governance
    from .runtime_proof import SPEC_VERSION, specification_bundle_sha256

    errors = validate_governance(RULES)
    if errors:
        raise ValueError("governance validation failed: " + "; ".join(errors))
    return SPEC_VERSION, specification_bundle_sha256(), rule_bundle_hash(RULES)


def _draft_payload_values(
    *,
    case_id: str,
    status: IntakeStatus,
    question: str,
    question_evidence: Optional[EvidenceSpan],
    source_id: str,
    source_sha256: str,
    facts: tuple[ExtractedFact, ...],
    missing_fields: tuple[str, ...],
    conflicts: tuple[FactConflict, ...],
    clarification_questions: tuple[str, ...],
    issues: tuple[str, ...],
    specification_version: str,
    specification_bundle_sha256: str,
    rule_bundle_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "case_id": case_id,
        "status": status.value,
        "intent": "section_194r_decision",
        "requested_period": "FY 2024-25",
        "question": question,
        "question_evidence": (
            _evidence_record(question_evidence)
            if question_evidence is not None
            else None
        ),
        "source": {"source_id": source_id, "sha256": source_sha256},
        "facts": [_fact_record(fact) for fact in facts],
        "missing_fields": list(missing_fields),
        "conflicts": [_conflict_record(item) for item in conflicts],
        "clarification_questions": list(clarification_questions),
        "issues": list(issues),
        "specification_version": specification_version,
        "specification_bundle_sha256": specification_bundle_sha256,
        "rule_bundle_hash": rule_bundle_hash,
    }


def _draft_payload(draft: IntakeDraft) -> dict[str, object]:
    return _draft_payload_values(
        case_id=draft.case_id,
        status=draft.status,
        question=draft.question,
        question_evidence=draft.question_evidence,
        source_id=draft.source_id,
        source_sha256=draft.source_sha256,
        facts=draft.facts,
        missing_fields=draft.missing_fields,
        conflicts=draft.conflicts,
        clarification_questions=draft.clarification_questions,
        issues=draft.issues,
        specification_version=draft.specification_version,
        specification_bundle_sha256=draft.specification_bundle_sha256,
        rule_bundle_hash=draft.rule_bundle_hash,
    )


def _line_records(source: str) -> list[tuple[str, int, int]]:
    records: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_line in source.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        records.append((content, cursor, cursor + len(content)))
        cursor += len(raw_line)
    if source and not records:
        records.append((source, 0, len(source)))
    return records


def formalize_194r(query: str, *, case_id: Optional[str] = None) -> IntakeDraft:
    """Translate the bounded controlled-English format into an intake draft.

    The accepted format is exactly one ``Question:`` line, a ``Facts:``
    header, and bullet lines using the eleven labels in ``_FIELDS``.  Labels
    may also be written as their canonical ``FACT_PATHS``.  Repeated equivalent
    facts merge their evidence; incompatible repeats become conflicts.
    """

    if case_id is None:
        case_id = secrets.token_hex(16)
    if type(case_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", case_id) is None:
        raise ValueError("case_id must contain 1-128 letters, digits, underscores, or hyphens")

    non_string = type(query) is not str
    source = query if type(query) is str else ""
    source_sha256 = _sha256(source.encode("utf-8"))
    specification_version, specification_bundle, bundle_hash = _current_context()
    issues: list[str] = []
    if non_string:
        issues.append("query must be a string")
    if len(source.encode("utf-8")) > MAX_QUERY_BYTES:
        issues.append(f"query exceeds the {MAX_QUERY_BYTES}-byte limit")

    records = _line_records(source)
    if any(len(line) > MAX_LINE_CHARACTERS for line, _, _ in records):
        issues.append(f"a line exceeds the {MAX_LINE_CHARACTERS}-character limit")
    nonblank = [(line, start, end) for line, start, end in records if line.strip()]

    question = ""
    question_evidence: Optional[EvidenceSpan] = None
    supported_question = False
    fact_lines: list[tuple[str, int, int]] = []
    if len(nonblank) < 2:
        issues.append("query requires a Question: line followed by a Facts: header")
    else:
        question_line, question_start, question_end = nonblank[0]
        if not question_line.startswith("Question:"):
            issues.append("first nonblank line must start with Question:")
        else:
            question = question_line[len("Question:"):].strip()
            if not question:
                issues.append("Question: must contain a question")
            question_evidence = EvidenceSpan(
                SOURCE_ID,
                source_sha256,
                question_start,
                question_end,
                source[question_start:question_end],
            )
            supported_question = _SUPPORTED_QUESTION_RE.fullmatch(question) is not None

        facts_header, _, _ = nonblank[1]
        if facts_header != "Facts:":
            issues.append("second nonblank line must be exactly Facts:")
        else:
            fact_lines = nonblank[2:]

    candidates: dict[str, list[ExtractedFact]] = {path: [] for path in FACT_PATHS}
    for line, start, end in fact_lines:
        if not line.startswith("- "):
            issues.append(f"fact line must start with '- ': {line!r}")
            continue
        body = line[2:]
        if ":" not in body:
            issues.append(f"fact bullet has no label/value separator: {line!r}")
            continue
        raw_label, raw_value = body.split(":", 1)
        field = _FIELD_BY_LABEL.get(_normalized_label(raw_label))
        if field is None:
            issues.append(f"unknown fact label: {raw_label.strip()!r}")
            continue
        value_text = raw_value.strip()
        if not value_text:
            issues.append(f"{field.path} has no value")
            continue
        try:
            value = _parse_value(field, value_text)
        except ValueError as exc:
            issues.append(f"{field.path}: {exc}")
            continue
        evidence = EvidenceSpan(
            SOURCE_ID, source_sha256, start, end, source[start:end]
        )
        candidates[field.path].append(
            ExtractedFact(field.path, field.value_type, value, (evidence,))
        )

    facts: list[ExtractedFact] = []
    conflicts: list[FactConflict] = []
    missing: list[str] = []
    for path in FACT_PATHS:
        proposed = candidates[path]
        if not proposed:
            missing.append(path)
            continue
        first = proposed[0]
        if any(item.value != first.value for item in proposed[1:]):
            conflicts.append(FactConflict(path, tuple(proposed)))
            continue
        evidence = tuple(
            span for proposal in proposed for span in proposal.evidence
        )
        facts.append(
            ExtractedFact(path, first.value_type, first.value, evidence)
        )

    missing_fields = tuple(missing)
    conflict_records = tuple(conflicts)
    clarification_questions = tuple(_CLARIFICATIONS[path] for path in missing_fields)
    issue_records = tuple(issues)
    if issue_records:
        status = IntakeStatus.INVALID
    elif not supported_question:
        status = IntakeStatus.UNSUPPORTED_QUERY
    elif conflict_records:
        status = IntakeStatus.CONFLICTING_FACTS
    elif missing_fields:
        status = IntakeStatus.NEEDS_CLARIFICATION
    else:
        status = IntakeStatus.AWAITING_CONFIRMATION

    fact_records = tuple(facts)
    payload = _draft_payload_values(
        case_id=case_id,
        status=status,
        question=question,
        question_evidence=question_evidence,
        source_id=SOURCE_ID,
        source_sha256=source_sha256,
        facts=fact_records,
        missing_fields=missing_fields,
        conflicts=conflict_records,
        clarification_questions=clarification_questions,
        issues=issue_records,
        specification_version=specification_version,
        specification_bundle_sha256=specification_bundle,
        rule_bundle_hash=bundle_hash,
    )
    draft_hash = _sha256(_canonical_json(payload))
    return IntakeDraft(
        schema_version=INTAKE_SCHEMA_VERSION,
        case_id=case_id,
        status=status,
        question=question,
        question_evidence=question_evidence,
        source_id=SOURCE_ID,
        source_text=source,
        source_sha256=source_sha256,
        facts=fact_records,
        missing_fields=missing_fields,
        conflicts=conflict_records,
        clarification_questions=clarification_questions,
        issues=issue_records,
        specification_version=specification_version,
        specification_bundle_sha256=specification_bundle,
        rule_bundle_hash=bundle_hash,
        draft_sha256=draft_hash,
    )


def _validate_evidence(draft: IntakeDraft) -> None:
    actual_source_hash = _sha256(draft.source_text.encode("utf-8"))
    if not hmac.compare_digest(actual_source_hash, draft.source_sha256):
        raise ValueError("draft source text does not match its SHA-256")

    evidence_items: list[EvidenceSpan] = []
    if draft.question_evidence is not None:
        evidence_items.append(draft.question_evidence)
    for fact in draft.facts:
        if not fact.evidence:
            raise ValueError(f"{fact.path} has no evidence")
        evidence_items.extend(fact.evidence)
    for conflict in draft.conflicts:
        for candidate in conflict.candidates:
            if not candidate.evidence:
                raise ValueError(f"conflicting {candidate.path} candidate has no evidence")
            evidence_items.extend(candidate.evidence)

    _validate_spans(
        source_text=draft.source_text,
        source_id=draft.source_id,
        source_sha256=draft.source_sha256,
        evidence=evidence_items,
    )


def _validate_spans(
    *,
    source_text: str,
    source_id: str,
    source_sha256: str,
    evidence: list[EvidenceSpan],
) -> None:
    for item in evidence:
        if item.source_id != source_id:
            raise ValueError("evidence refers to an unknown source")
        if not hmac.compare_digest(item.source_sha256, source_sha256):
            raise ValueError("evidence source SHA-256 does not match the draft")
        if (
            type(item.start) is not int
            or type(item.end) is not int
            or item.start < 0
            or item.start >= item.end
            or item.end > len(source_text)
        ):
            raise ValueError("evidence span is outside the source")
        if source_text[item.start:item.end] != item.quote:
            raise ValueError("evidence quote does not match its exact source span")


def _validate_fact_evidence(
    *,
    source_text: str,
    source_id: str,
    source_sha256: str,
    facts: tuple[ExtractedFact, ...],
) -> None:
    evidence: list[EvidenceSpan] = []
    for fact in facts:
        if not fact.evidence:
            raise ValueError(f"{fact.path} has no evidence")
        evidence.extend(fact.evidence)
    _validate_spans(
        source_text=source_text,
        source_id=source_id,
        source_sha256=source_sha256,
        evidence=evidence,
    )


def _to_s194r_facts(extracted: tuple[ExtractedFact, ...]) -> S194RFacts:
    values = {fact.path: fact.value for fact in extracted}
    if tuple(path for path in FACT_PATHS if path not in values):
        raise ValueError("cannot construct Section 194R facts from an incomplete draft")
    if len(values) != len(FACT_PATHS):
        raise ValueError("draft must contain each Section 194R fact exactly once")
    return S194RFacts(
        brand_entity_type=values["brand.entity_type"],
        brand_in_business=values["brand.in_business"],
        brand_preceding_fy_business_turnover_paise=values[
            "brand.preceding_fy_business_turnover_paise"
        ],
        brand_preceding_fy_profession_receipts_paise=values[
            "brand.preceding_fy_profession_receipts_paise"
        ],
        creator_is_resident=values["creator.is_resident"],
        creator_pan_furnished=values["creator.pan_furnished"],
        creator_fy_prior_benefits_from_brand_paise=values[
            "creator.fy_prior_benefits_from_brand_paise"
        ],
        creator_fy_prior_194r_tds_paise=values[
            "creator.fy_prior_194r_tds_paise"
        ],
        product_fmv_paise=values["transaction.product_fmv_paise"],
        product_retained=values["transaction.product_retained"],
        tax_borne_by=values["transaction.tax_borne_by"],
    )


def confirm_194r(
    draft: IntakeDraft,
    presented_draft_sha256: str,
    accepted: bool,
) -> Optional[ConfirmedCase]:
    """Confirm one untampered, complete draft, or return ``None`` if declined."""

    if not isinstance(draft, IntakeDraft):
        raise TypeError("draft must be an IntakeDraft")
    if type(accepted) is not bool:
        raise ValueError("accepted must be a boolean")
    if not accepted:
        return None
    if type(presented_draft_sha256) is not str or not hmac.compare_digest(
        presented_draft_sha256, draft.draft_sha256
    ):
        raise ValueError("presented draft SHA-256 does not match")

    # Confirmation never trusts an arbitrary dataclass instance.  Serialize it
    # through the strict persistence schema, reparse its source, and use only
    # that independently derived object below.
    draft = IntakeDraft.from_dict(draft.as_dict())
    recomputed = _sha256(_canonical_json(_draft_payload(draft)))
    if not hmac.compare_digest(recomputed, draft.draft_sha256):
        raise ValueError("draft content does not match its SHA-256")
    _validate_evidence(draft)

    if draft.status is not IntakeStatus.AWAITING_CONFIRMATION:
        raise ValueError(
            f"only AWAITING_CONFIRMATION drafts may be confirmed, got {draft.status.value}"
        )
    if draft.missing_fields or draft.conflicts or draft.issues:
        raise ValueError("draft is not complete and conflict-free")

    current_specification, current_specification_bundle, current_bundle = (
        _current_context()
    )
    if draft.specification_version != current_specification:
        raise ValueError("specification changed; formalize and confirm the case again")
    if not hmac.compare_digest(
        draft.specification_bundle_sha256, current_specification_bundle
    ):
        raise ValueError(
            "specification sources changed; formalize and confirm the case again"
        )
    if not hmac.compare_digest(draft.rule_bundle_hash, current_bundle):
        raise ValueError("rule bundle changed; formalize and confirm the case again")

    facts = _to_s194r_facts(draft.facts)
    normalized_fact_sha256 = _sha256(_canonical_json(facts.as_dict()))
    source_bundle_sha256 = _source_bundle_sha256(
        draft.source_id, draft.source_sha256
    )
    provenance_sha256 = _provenance_sha256(draft.facts)
    confirmation_payload = _confirmation_payload(
        case_id=draft.case_id,
        draft_sha256=draft.draft_sha256,
        normalized_fact_sha256=normalized_fact_sha256,
        source_bundle_sha256=source_bundle_sha256,
        provenance_sha256=provenance_sha256,
        specification_version=draft.specification_version,
        specification_bundle_sha256=draft.specification_bundle_sha256,
        rule_bundle_hash=draft.rule_bundle_hash,
    )
    confirmation_sha256 = _sha256(_canonical_json(confirmation_payload))
    return ConfirmedCase(
        schema_version=CONFIRMATION_SCHEMA_VERSION,
        case_id=draft.case_id,
        facts=facts,
        fact_evidence=draft.facts,
        source_id=draft.source_id,
        source_text=draft.source_text,
        source_sha256=draft.source_sha256,
        draft_sha256=draft.draft_sha256,
        normalized_fact_sha256=normalized_fact_sha256,
        source_bundle_sha256=source_bundle_sha256,
        provenance_sha256=provenance_sha256,
        specification_version=draft.specification_version,
        specification_bundle_sha256=draft.specification_bundle_sha256,
        rule_bundle_hash=draft.rule_bundle_hash,
        confirmation_payload=confirmation_payload,
        confirmation_sha256=confirmation_sha256,
    )
