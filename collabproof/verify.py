"""
collabproof.verify — fail-closed claim certification.

The assessor answers the questions modeled by the executable specification.
The certifier checks a *complete, typed claim* against that assessment. A
certificate therefore means every required output field was present and
checked; omitted fields produce INCOMPLETE rather than vacuous success.

Statuses:
  CERTIFIED    every required field was present, well typed, and matched.
  INCOMPLETE   no checked field was wrong, but one or more fields were omitted.
  INVALID      the claim did not satisfy the runtime claim schema.
  AMBIGUOUS    all fields were present, but a material statutory fork was left
               unresolved by an explicit null basis.
  REJECTED     at least one asserted field contradicted the specification.
  OUT_OF_SCOPE the specification refuses the fact pattern.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Optional

from .spec import (S194R_FY_THRESHOLD, Assessment, Collab, Q, RULES,
                   TaxBearer, assess)

if TYPE_CHECKING:
    from .governance import CertificateFreshness


@lru_cache(maxsize=1)
def _rule_bundle_hash() -> str:
    from .governance import rule_bundle_hash
    return rule_bundle_hash(RULES)


class _Unset:
    """Marker for an omitted claim field; distinct from an explicit JSON null."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


class Status(Enum):
    CERTIFIED = "CERTIFIED"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    AMBIGUOUS = "AMBIGUOUS"
    REJECTED = "REJECTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


CLAIM_FIELDS = (
    "tds_194r_paise",
    "release_gate_required",
    "cash_tds_paise",
    "cash_tds_basis",
    "gst_registration_required",
    "gst_liability_paise",
)


@dataclass(frozen=True)
class Claim:
    """A structured answer to certify.

    ``UNSET`` means the field was omitted. ``None`` is an explicit null and is
    meaningful only for ``cash_tds_basis`` (no basis asserted) and
    ``gst_liability_paise`` (the specification computes no charge for an
    unregistered creator). Amounts are integer paise.
    """

    tds_194r_paise: object = UNSET
    release_gate_required: object = UNSET
    cash_tds_paise: object = UNSET
    cash_tds_basis: object = UNSET
    gst_registration_required: object = UNSET
    gst_liability_paise: object = UNSET

    def checked_fields(self) -> tuple[str, ...]:
        return tuple(name for name in CLAIM_FIELDS if getattr(self, name) is not UNSET)


@dataclass(frozen=True)
class Mismatch:
    fld: str
    claimed: object
    expected: object
    rule_id: str
    supporting_rule_ids: tuple[str, ...] = ()

    def explain(self) -> str:
        rule = RULES[self.rule_id]
        support = tuple(r for r in self.supporting_rule_ids if r != self.rule_id)
        suffix = f"; supporting rules: {', '.join(support)}" if support else ""
        return (
            f"{self.fld}: claimed {self.claimed!r}, spec says {self.expected!r} "
            f"[{self.rule_id}: {rule.citation}{suffix}]"
        )


@dataclass(frozen=True)
class Certificate:
    status: Status
    mismatches: tuple[Mismatch, ...] = ()
    notes: tuple[str, ...] = ()
    assessment: Optional[Assessment] = None
    required_fields: tuple[str, ...] = CLAIM_FIELDS
    checked_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    rule_bundle_hash: str = field(default_factory=_rule_bundle_hash)
    governed_rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.governed_rule_ids and self.assessment is not None:
            from .governance import governed_rule_ids
            object.__setattr__(self, "governed_rule_ids",
                               governed_rule_ids(self.assessment))

    def governance_record(self) -> dict:
        """Portable fields needed for later staleness/impact checks."""
        return {
            "status": self.status.value,
            "rule_bundle_hash": self.rule_bundle_hash,
            "governed_rule_ids": list(self.governed_rule_ids),
        }

    def freshness(self, *, current_hash: Optional[str] = None,
                  affected_rule_ids: tuple[str, ...] = ()) -> "CertificateFreshness":
        from .governance import certificate_freshness
        return certificate_freshness(
            self.rule_bundle_hash,
            self.governed_rule_ids,
            current_hash or _rule_bundle_hash(),
            affected_rule_ids,
        )


def _claim_schema_issues(claim: Claim) -> tuple[str, ...]:
    """Validate runtime types without Python's bool-as-int coercion."""

    issues: list[str] = []

    def money(name: str, *, nullable: bool = False) -> None:
        value = getattr(claim, name)
        if value is UNSET:
            return
        if nullable and value is None:
            return
        if type(value) is not int:  # deliberately excludes bool
            issues.append(f"{name} must be a non-negative integer number of paise")
        elif value < 0:
            issues.append(f"{name} must be non-negative")

    def boolean(name: str) -> None:
        value = getattr(claim, name)
        if value is not UNSET and type(value) is not bool:
            issues.append(f"{name} must be a boolean")

    money("tds_194r_paise")
    boolean("release_gate_required")
    money("cash_tds_paise")
    boolean("gst_registration_required")
    money("gst_liability_paise", nullable=True)

    basis = claim.cash_tds_basis
    allowed_bases = ("IT-194J-PROF", "IT-194C-WORK")
    if basis is not UNSET and basis is not None and basis not in allowed_bases:
        issues.append(
            "cash_tds_basis must be IT-194J-PROF, IT-194C-WORK, or explicit null"
        )
    return tuple(issues)


def _causal_rule(key: str, assessment: Assessment, collab: Collab) -> str:
    """Return the rule that decides the expected field on this execution path.

    Determination.rule_ids remains the full support trail. This function picks
    the path-specific rule a user needs to fix the claim, rather than blindly
    attaching the first citation in that trail.
    """

    if key == Q.TDS_194R:
        if not assessment.d(Q.BENEFIT_QUALIFIES):
            return "IT-194R-RETAINED" if collab.product_fmv_paise else "IT-194R-SCOPE"
        if not assessment.d(Q.PROVIDER_OBLIGATED):
            return "IT-194R-CARVEOUT"
        if assessment.d(Q.AGGREGATE_BENEFIT) <= S194R_FY_THRESHOLD:
            return "IT-194R-THRESHOLD"
        if not collab.creator.pan_furnished:
            return "IT-206AA"
        if collab.tax_borne_by is TaxBearer.PROVIDER:
            return "IT-194R-GROSSUP"
        # Once the threshold is crossed, the same rule requires deduction on
        # the aggregate. This diagnoses the common "tax only the excess" error.
        return "IT-194R-THRESHOLD"
    if key == Q.RELEASE_GATE:
        return "IT-194R-RELEASEGATE"
    if key == Q.GST_REG_REQUIRED:
        return "GST-REG-THRESHOLD"
    if key == Q.GST_LIABILITY:
        return "GST-RATE-18"
    return assessment.determinations[key].rule_ids[0]


def verify(claim: Claim, collab: Collab) -> Certificate:
    assessment = assess(collab)
    checked = claim.checked_fields()

    if not assessment.ok:
        note = (
            f"Spec refuses this fact pattern [{assessment.refusal_rule_id}: "
            f"{RULES[assessment.refusal_rule_id].citation}] — {assessment.refusal_note}"
        )
        notes = [note]
        if checked:
            notes.append(
                "Assertions about an out-of-scope pattern are uncertifiable; "
                "the honest output is a refusal."
            )
        return Certificate(
            Status.OUT_OF_SCOPE,
            notes=tuple(notes),
            assessment=assessment,
            required_fields=(),
            checked_fields=checked,
            missing_fields=(),
        )

    missing = tuple(name for name in CLAIM_FIELDS if name not in checked)
    schema_issues = _claim_schema_issues(claim)
    if schema_issues:
        return Certificate(
            Status.INVALID,
            notes=schema_issues,
            assessment=assessment,
            checked_fields=checked,
            missing_fields=missing,
        )

    mismatches: list[Mismatch] = []
    notes: list[str] = []

    def check(field_name: str, key: str) -> None:
        claimed = getattr(claim, field_name)
        if claimed is UNSET:
            return
        determination = assessment.determinations[key]
        if claimed != determination.value:
            primary = _causal_rule(key, assessment, collab)
            mismatches.append(
                Mismatch(
                    field_name,
                    claimed,
                    determination.value,
                    primary,
                    determination.rule_ids,
                )
            )

    check("tds_194r_paise", Q.TDS_194R)
    check("release_gate_required", Q.RELEASE_GATE)
    check("gst_registration_required", Q.GST_REG_REQUIRED)
    check("gst_liability_paise", Q.GST_LIABILITY)

    ambiguous = False
    cash_tds = claim.cash_tds_paise
    cash_basis = claim.cash_tds_basis
    if cash_tds is not UNSET:
        branches = {b.basis_rule_id: b.tds_paise for b in assessment.cash_tds_fork}
        if cash_basis is UNSET:
            # Coverage reporting will return INCOMPLETE below. We can still
            # reject a value that matches no branch.
            if cash_tds not in branches.values():
                mismatches.append(
                    Mismatch(
                        "cash_tds_paise",
                        cash_tds,
                        tuple(branches.values()),
                        "IT-FORK-JvC",
                        tuple(branches),
                    )
                )
        elif cash_basis is None:
            if not assessment.fork_material:
                expected = next(iter(branches.values()), 0)
                if cash_tds != expected:
                    mismatches.append(
                        Mismatch(
                            "cash_tds_paise",
                            cash_tds,
                            expected,
                            "IT-194J-PROF",
                            ("IT-194J-PROF", "IT-194C-WORK"),
                        )
                    )
            elif cash_tds in branches.values():
                ambiguous = True
                notes.append(
                    "Cash TDS matches a branch of a material 194J/194C fork, "
                    "but the claim explicitly states no statutory basis "
                    "[IT-FORK-JvC]."
                )
            else:
                mismatches.append(
                    Mismatch(
                        "cash_tds_paise",
                        cash_tds,
                        tuple(branches.values()),
                        "IT-FORK-JvC",
                        tuple(branches),
                    )
                )
        elif cash_tds != branches[cash_basis]:
            mismatches.append(
                Mismatch(
                    "cash_tds_paise",
                    cash_tds,
                    branches[cash_basis],
                    cash_basis,
                    (cash_basis,),
                )
            )
        else:
            notes.append(
                f"Cash TDS certified under {cash_basis} "
                f"[{RULES[cash_basis].citation}]; the 194J/194C overlap itself "
                "remains unresolved [IT-FORK-JvC]."
            )

    if mismatches:
        return Certificate(
            Status.REJECTED,
            tuple(mismatches),
            tuple(notes),
            assessment,
            checked_fields=checked,
            missing_fields=missing,
        )
    if missing:
        return Certificate(
            Status.INCOMPLETE,
            notes=(
                "A certificate requires every output field; omitted fields were not checked.",
            ),
            assessment=assessment,
            checked_fields=checked,
            missing_fields=missing,
        )
    if ambiguous:
        return Certificate(
            Status.AMBIGUOUS,
            notes=tuple(notes),
            assessment=assessment,
            checked_fields=checked,
        )
    return Certificate(
        Status.CERTIFIED,
        notes=tuple(notes),
        assessment=assessment,
        checked_fields=checked,
    )


def assessment_as_claim(assessment: Assessment, basis: Optional[str] = None) -> Claim:
    """Express every output of an in-scope assessment as a complete claim."""

    if not assessment.ok:
        return Claim()
    branches = {b.basis_rule_id: b.tds_paise for b in assessment.cash_tds_fork}
    if basis is None and not assessment.fork_material and assessment.cash_tds_fork:
        basis_value = assessment.cash_tds_fork[0].tds_paise
        basis_id = None
    else:
        basis_id = basis or "IT-194J-PROF"
        basis_value = branches[basis_id]
    return Claim(
        tds_194r_paise=assessment.d(Q.TDS_194R),
        release_gate_required=assessment.d(Q.RELEASE_GATE),
        cash_tds_paise=basis_value,
        cash_tds_basis=basis_id,
        gst_registration_required=assessment.d(Q.GST_REG_REQUIRED),
        gst_liability_paise=assessment.d(Q.GST_LIABILITY),
    )
