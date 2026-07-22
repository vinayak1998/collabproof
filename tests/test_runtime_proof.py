import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from collabproof import (Brand, Collab, Creator, EntityType,
                         LeanCertificationError, TaxBearer,
                         certify_unconfirmed_194r, rup)
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
    certificate = certify_unconfirmed_194r(c, tmp_path)

    assert certificate["certification_status"] == "LEAN_KERNEL_CHECKED"
    assert certificate["schema_version"] == "collabproof-runtime-certificate-v2"
    assert certificate["decision_status"] == "ANSWERED"
    assert certificate["trusted_scope"] == "section_194r_only"
    assert certificate["intake"] is None
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
    assert certificate["governance"]["rule_bundle_hash"] == (
        certificate["specification"]["rule_bundle_hash"]
    )
    assert set(certificate["applied_rule_ids"]) <= set(
        certificate["covered_rule_ids"]
    )
    assert "cash_fee_paise" not in certificate["normalized_facts"]["transaction"]
    assert certificate["complete_collab_input_facts"]["transaction"]["cash_fee_paise"] == rup(50_000)

    artifact = Path(certificate["proof_artifact"]["path"])
    assert artifact.exists()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == certificate["proof_artifact"]["sha256"]
    persisted = json.loads(Path(certificate["certificate_path"]).read_text())
    assert "certificate_path" not in persisted
    assert persisted["normalized_facts"] == certificate["normalized_facts"]


def test_cash_output_requires_and_records_an_explicit_interpretation(tmp_path):
    c = collab(cash_fee_paise=rup(50_000))
    with pytest.raises(ValueError, match="explicitly selected"):
        certify_unconfirmed_194r(c, tmp_path / "bad", cash_interpretation="unstated-choice")

    certificate = certify_unconfirmed_194r(
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
    provider = certify_unconfirmed_194r(
        collab(product_fmv_paise=rup(27_000), tax_borne_by=TaxBearer.PROVIDER),
        tmp_path / "provider",
    )
    assert provider["covered_outputs"]["aggregate_benefit_paise"] == rup(30_000)
    assert provider["covered_outputs"]["tds_due_now_paise"] == rup(3_000)

    refused = certify_unconfirmed_194r(
        collab(creator=Creator(is_resident=False)), tmp_path / "nonresident"
    )
    assert refused["covered_outputs"]["scope"] == "unsupported_non_resident"
    assert refused["covered_outputs"]["tds_due_now_paise"] == 0
    assert refused["decision_status"] == "REFUSED"
    assert refused["applied_rule_ids"] == ["SCOPE-RESIDENT"]
    assert refused["output_rule_ids"] == {"scope": ["SCOPE-RESIDENT"]}
    assert "SCOPE-RESIDENT" in refused["covered_rule_ids"]


def test_lean_fact_identity_excludes_unproved_cash_and_gst_inputs(tmp_path):
    first = certify_unconfirmed_194r(
        collab(cash_fee_paise=rup(1), deliverable_linked=True),
        tmp_path / "first",
    )
    second = certify_unconfirmed_194r(
        collab(cash_fee_paise=rup(9_99_999), deliverable_linked=False),
        tmp_path / "second",
    )
    assert first["normalized_facts"] == second["normalized_facts"]
    assert first["normalized_fact_sha256"] == second["normalized_fact_sha256"]
    assert first["proof_artifact"]["name"] == second["proof_artifact"]["name"]


def test_intake_record_must_bind_facts_spec_and_governance(tmp_path):
    from collabproof.intake import confirm_194r, formalize_194r

    source = (Path(__file__).parents[1] / "proofs/example_s194r_query.txt").read_text(
        encoding="utf-8"
    )
    draft = formalize_194r(source, case_id="runtime-confirmed")
    confirmed = confirm_194r(draft, draft.draft_sha256, True)
    destination = tmp_path / "good"
    destination.mkdir()
    confirmed_path = destination / "confirmed-case.json"
    confirmed_path.write_text(
        json.dumps(confirmed.as_dict(), sort_keys=True) + "\n", encoding="utf-8"
    )

    certificate = runtime_proof.certify_194r(confirmed_path, destination)
    artifact_record = certificate["intake"]["confirmed_case_artifact"]
    assert artifact_record["path"] == str(confirmed_path.resolve())
    assert artifact_record["sha256"] == hashlib.sha256(
        confirmed_path.read_bytes()
    ).hexdigest()
    assert {
        key: value
        for key, value in certificate["intake"].items()
        if key != "confirmed_case_artifact"
    } == confirmed.certificate_record()

    tampered_destination = tmp_path / "tampered"
    tampered_destination.mkdir()
    tampered_path = tampered_destination / "confirmed-case.json"
    tampered = confirmed.as_dict()
    tampered["confirmation_payload"]["draft_sha256"] = "e" * 64
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="confirmation payload"):
        runtime_proof.certify_194r(tampered_path, tampered_destination)
    assert list(tampered_destination.iterdir()) == [tampered_path]


def test_requested_certification_fails_closed_without_toolchain(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_proof.shutil, "which", lambda _: None)
    with pytest.raises(LeanCertificationError, match="lake is unavailable"):
        certify_unconfirmed_194r(collab(), tmp_path)
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
        certify_unconfirmed_194r(collab(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_certification_rejects_specification_change_during_lean(monkeypatch, tmp_path):
    real_identity = runtime_proof._specification_identity
    calls = 0

    def drifting_identity(root):
        nonlocal calls
        calls += 1
        identity = real_identity(root)
        if calls > 1:
            identity["deterministic_source_bundle_sha256"] = "0" * 64
        return identity

    monkeypatch.setattr(runtime_proof, "_specification_identity", drifting_identity)
    with pytest.raises(LeanCertificationError, match="changed during"):
        certify_unconfirmed_194r(collab(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_subprocess_timeout_is_a_fail_result(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], runtime_proof.COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(runtime_proof.subprocess, "run", timeout)
    result = runtime_proof._run(["lake", "build"], tmp_path)
    assert result["result"] == "FAIL"
    assert result["exit_code"] is None
    assert "second limit" in result["stderr"]


def test_unconfirmed_fact_envelope_rejects_money_above_runtime_bound():
    with pytest.raises(ValueError, match="integer from 0 through"):
        runtime_proof.normalized_194r_facts(
            runtime_proof.S194RFacts.from_collab(
                collab(product_fmv_paise=runtime_proof.MAX_MONEY_PAISE + 1)
            )
        )
