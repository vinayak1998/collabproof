"""
collabproof.verify — the certifier.

verify(claim, collab) plays the role of Pramaana's "compiler back half":
an external answer (from an LLM, a junior accountant, a blog calculator) is
checked field-by-field against the executable spec. It either CERTIFIES the
answer, or returns the exact rule that breaks, with citation.

Statuses:
  CERTIFIED    every asserted field matches the spec (fork fields matched
               under a stated statutory basis, or the fork is immaterial).
  AMBIGUOUS    a fork-sensitive field was asserted without a statutory basis
               while the branches disagree. Not wrong — unprovable as stated.
  REJECTED     at least one asserted field contradicts the spec.
  OUT_OF_SCOPE the spec refuses this fact pattern; any numeric assertion
               about it is uncertifiable by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .spec import Assessment, Collab, Q, RULES, assess


class Status(Enum):
    CERTIFIED = "CERTIFIED"
    AMBIGUOUS = "AMBIGUOUS"
    REJECTED = "REJECTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class Claim:
    """What an answerer asserts about a collab. All fields optional:
    only asserted fields are checked. Amounts in paise."""
    tds_194r_paise: Optional[int] = None
    release_gate_required: Optional[bool] = None
    cash_tds_paise: Optional[int] = None
    cash_tds_basis: Optional[str] = None      # "IT-194J-PROF" | "IT-194C-WORK"
    gst_registration_required: Optional[bool] = None
    gst_liability_paise: Optional[int] = None


@dataclass(frozen=True)
class Mismatch:
    fld: str
    claimed: object
    expected: object
    rule_id: str

    def explain(self) -> str:
        r = RULES[self.rule_id]
        return (f"{self.fld}: claimed {self.claimed!r}, spec says {self.expected!r} "
                f"[{self.rule_id}: {r.citation}]")


@dataclass(frozen=True)
class Certificate:
    status: Status
    mismatches: tuple[Mismatch, ...] = ()
    notes: tuple[str, ...] = ()
    assessment: Optional[Assessment] = None


def verify(claim: Claim, collab: Collab) -> Certificate:
    a = assess(collab)

    if not a.ok:
        asserted = any(v is not None for v in (
            claim.tds_194r_paise, claim.cash_tds_paise,
            claim.gst_liability_paise, claim.gst_registration_required,
            claim.release_gate_required))
        note = (f"Spec refuses this fact pattern [{a.refusal_rule_id}: "
                f"{RULES[a.refusal_rule_id].citation}] — {a.refusal_note}")
        if asserted:
            return Certificate(Status.OUT_OF_SCOPE, notes=(
                note, "Numeric assertions about an out-of-scope pattern are "
                      "uncertifiable; the honest output is a refusal."), assessment=a)
        return Certificate(Status.OUT_OF_SCOPE, notes=(note,), assessment=a)

    mismatches: list[Mismatch] = []
    notes: list[str] = []

    def check(fld: str, claimed, key: str):
        if claimed is None:
            return
        d = a.determinations[key]
        if claimed != d.value:
            mismatches.append(Mismatch(fld, claimed, d.value, d.rule_ids[0]))

    check("tds_194r_paise", claim.tds_194r_paise, Q.TDS_194R)
    check("release_gate_required", claim.release_gate_required, Q.RELEASE_GATE)
    check("gst_registration_required", claim.gst_registration_required, Q.GST_REG_REQUIRED)
    check("gst_liability_paise", claim.gst_liability_paise, Q.GST_LIABILITY)

    ambiguous = False
    if claim.cash_tds_paise is not None:
        branches = {b.basis_rule_id: b.tds_paise for b in a.cash_tds_fork}
        if claim.cash_tds_basis is not None:
            if claim.cash_tds_basis not in branches:
                mismatches.append(Mismatch("cash_tds_basis", claim.cash_tds_basis,
                                           tuple(branches), "IT-FORK-JvC"))
            elif claim.cash_tds_paise != branches[claim.cash_tds_basis]:
                mismatches.append(Mismatch("cash_tds_paise", claim.cash_tds_paise,
                                           branches[claim.cash_tds_basis],
                                           claim.cash_tds_basis))
            else:
                notes.append(f"cash TDS certified UNDER {claim.cash_tds_basis} "
                             f"[{RULES[claim.cash_tds_basis].citation}]; the "
                             f"194J/194C overlap itself is unresolved [IT-FORK-JvC].")
        else:
            if not a.fork_material:
                # branches agree — basis is immaterial, value checkable outright
                expected = a.cash_tds_fork[0].tds_paise if a.cash_tds_fork else 0
                if claim.cash_tds_paise != expected:
                    mismatches.append(Mismatch("cash_tds_paise", claim.cash_tds_paise,
                                               expected, "IT-194J-PROF"))
            elif claim.cash_tds_paise in branches.values():
                ambiguous = True
                notes.append("cash TDS matches one branch of a MATERIAL 194J/194C "
                             "fork but no statutory basis was stated. Value "
                             f"alternatives: {branches} [IT-FORK-JvC].")
            else:
                mismatches.append(Mismatch("cash_tds_paise", claim.cash_tds_paise,
                                           tuple(branches.values()), "IT-FORK-JvC"))

    if mismatches:
        return Certificate(Status.REJECTED, tuple(mismatches), tuple(notes), a)
    if ambiguous:
        return Certificate(Status.AMBIGUOUS, (), tuple(notes), a)
    return Certificate(Status.CERTIFIED, (), tuple(notes), a)


def assessment_as_claim(a: Assessment, basis: Optional[str] = None) -> Claim:
    """Round-trip helper: the spec's own answer, expressed as a Claim."""
    if not a.ok:
        return Claim()
    branches = {b.basis_rule_id: b.tds_paise for b in a.cash_tds_fork}
    if basis is None and not a.fork_material and a.cash_tds_fork:
        basis_val = a.cash_tds_fork[0].tds_paise
        basis_id = None
    else:
        basis_id = basis or "IT-194J-PROF"
        basis_val = branches[basis_id]
    return Claim(
        tds_194r_paise=a.d(Q.TDS_194R),
        release_gate_required=a.d(Q.RELEASE_GATE),
        cash_tds_paise=basis_val,
        cash_tds_basis=basis_id,
        gst_registration_required=a.d(Q.GST_REG_REQUIRED),
        gst_liability_paise=a.d(Q.GST_LIABILITY),
    )
