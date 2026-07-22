from .spec import (Assessment, Brand, Collab, Creator, EntityType, Q, RULES,
                   TaxBearer, assess, pct, rup)
from .verify import (CLAIM_FIELDS, UNSET, Certificate, Claim, Status,
                     assessment_as_claim, verify)
from .baseline import naive_answer

__all__ = ["Assessment", "Brand", "Collab", "Creator", "EntityType", "Q",
           "RULES", "TaxBearer", "assess", "pct", "rup", "Certificate",
           "Claim", "Status", "UNSET", "CLAIM_FIELDS", "assessment_as_claim",
           "verify", "naive_answer"]
__all__ += ["LeanCertificationError", "certify_194r", "normalized_facts"]


def __getattr__(name):
    if name in {"LeanCertificationError", "certify_194r", "normalized_facts"}:
        from . import runtime_proof
        return getattr(runtime_proof, name)
    raise AttributeError(name)
