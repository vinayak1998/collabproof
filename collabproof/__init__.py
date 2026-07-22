from .spec import (Assessment, Brand, Collab, Creator, EntityType, Q, RULES,
                   TaxBearer, assess, pct, rup)
from .verify import (CLAIM_FIELDS, UNSET, Certificate, Claim, Status,
                     assessment_as_claim, verify)
from .baseline import naive_answer
from .s194r import FACT_PATHS, S194RFacts

__all__ = ["Assessment", "Brand", "Collab", "Creator", "EntityType", "Q",
           "RULES", "TaxBearer", "assess", "pct", "rup", "Certificate",
           "Claim", "Status", "UNSET", "CLAIM_FIELDS", "assessment_as_claim",
           "verify", "naive_answer", "FACT_PATHS", "S194RFacts"]
__all__ += [
    "LeanCertificationError",
    "certify_194r",
    "certify_194r_facts",
    "certify_unconfirmed_194r",
    "normalized_facts",
    "normalized_194r_facts",
    "ConfirmedCase",
    "IntakeDraft",
    "IntakeStatus",
    "confirm_194r",
    "formalize_194r",
    "RenderValidationError",
    "RenderedAnswer",
    "render_194r",
    "PipelineError",
    "PipelineResult",
    "formalize_file",
    "prove_draft_file",
]


def __getattr__(name):
    if name in {
        "LeanCertificationError",
        "certify_194r",
        "certify_194r_facts",
        "certify_unconfirmed_194r",
        "normalized_facts",
        "normalized_194r_facts",
    }:
        from . import runtime_proof
        return getattr(runtime_proof, name)
    if name in {
        "ConfirmedCase",
        "IntakeDraft",
        "IntakeStatus",
        "confirm_194r",
        "formalize_194r",
    }:
        from . import intake
        return getattr(intake, name)
    if name in {"RenderValidationError", "RenderedAnswer", "render_194r"}:
        from . import render
        return getattr(render, name)
    if name in {
        "PipelineError",
        "PipelineResult",
        "formalize_file",
        "prove_draft_file",
    }:
        from . import pipeline
        return getattr(pipeline, name)
    raise AttributeError(name)
