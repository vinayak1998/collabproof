import hashlib
import json
from pathlib import Path

import pytest

from collabproof import (Brand, Collab, Creator, EntityType,
                         LeanCertificationError, TaxBearer, certify_194r, rup)
from collabproof import runtime_proof


def collab(**overrides):
    base = {
        "brand": Brand(EntityType.COMPANY),
        "creator": Creator(),
        "product_fmv_paise": rup(30_000),
    }
    return Collab(**{**base, **overrides})


def test_runtime_certificate_contains_complete_hashed_facts_and_checked_artifact(tmp_path):
    c = collab(cash_fee_paise=rup(50_000), deliverable_linked=False)
    certificate = certify_194r(c, tmp_path)

    assert certificate["certification_status"] == "LEAN_KERNEL_CHECKED"
    assert certificate["trusted_scope"] == "section_194r_only"
    encoded = json.dumps(
        certificate["normalized_facts"], sort_keys=True, separators=(",", ":")
    ).encode()
    assert certificate["normalized_fact_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert certificate["covered_outputs"] == {
        "scope": "covered",
        "benefit_qualifies": True,
        "provider_obligated": True,
        "aggregate_benefit_paise": rup(30_000),
        "tds_due_now_paise": rup(3_000),
        "release_gate_required": True,
    }
    assert certificate["other_outputs"]["cash_tds_194j_194c"]["status"] == "UNSUPPORTED_UNVERIFIED"
    assert certificate["other_outputs"]["gst"]["status"] == "UNSUPPORTED_UNVERIFIED"
    assert certificate["lean_checks"]["per_case_kernel_check"]["result"] == "PASS"

    artifact = Path(certificate["proof_artifact"]["path"])
    assert artifact.exists()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == certificate["proof_artifact"]["sha256"]
    persisted = json.loads(Path(certificate["certificate_path"]).read_text())
    assert "certificate_path" not in persisted
    assert persisted["normalized_facts"] == certificate["normalized_facts"]


def test_cash_output_requires_and_records_an_explicit_interpretation(tmp_path):
    c = collab(cash_fee_paise=rup(50_000))
    with pytest.raises(ValueError, match="explicitly selected"):
        certify_194r(c, tmp_path / "bad", cash_interpretation="unstated-choice")

    certificate = certify_194r(
        c, tmp_path / "selected", cash_interpretation="IT-194C-WORK"
    )
    cash = certificate["other_outputs"]["cash_tds_194j_194c"]
    assert cash == {
        "status": "CONDITIONAL_UNVERIFIED_BY_LEAN",
        "selected_interpretation": "IT-194C-WORK",
        "cash_tds_paise": rup(500),
        "condition": "Only if IT-194C-WORK is selected; the overlap is not proved.",
    }
    assert any("IT-194C-WORK" in item for item in certificate["assumptions"])


def test_bearer_modes_and_unsupported_scope_are_kernel_checked(tmp_path):
    provider = certify_194r(
        collab(product_fmv_paise=rup(27_000), tax_borne_by=TaxBearer.PROVIDER),
        tmp_path / "provider",
    )
    assert provider["covered_outputs"]["aggregate_benefit_paise"] == rup(30_000)
    assert provider["covered_outputs"]["tds_due_now_paise"] == rup(3_000)

    refused = certify_194r(
        collab(creator=Creator(is_resident=False)), tmp_path / "nonresident"
    )
    assert refused["covered_outputs"]["scope"] == "unsupported_non_resident"
    assert refused["covered_outputs"]["tds_due_now_paise"] == 0


def test_requested_certification_fails_closed_without_toolchain(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_proof.shutil, "which", lambda _: None)
    with pytest.raises(LeanCertificationError, match="lake is unavailable"):
        certify_194r(collab(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_requested_certification_fails_closed_on_kernel_rejection(monkeypatch, tmp_path):
    real_run = runtime_proof._run

    def reject_kernel(command, cwd):
        if "lean" in command:
            return {
                "command": command,
                "exit_code": 1,
                "stdout": "",
                "stderr": "synthetic kernel rejection",
                "result": "FAIL",
            }
        return real_run(command, cwd)

    monkeypatch.setattr(runtime_proof, "_run", reject_kernel)
    with pytest.raises(LeanCertificationError, match="failed closed"):
        certify_194r(collab(), tmp_path)
    assert list(tmp_path.iterdir()) == []
