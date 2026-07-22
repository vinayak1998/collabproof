"""End-to-end and proof-gating tests for the controlled-English pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from collabproof import pipeline
from collabproof.intake import ConfirmedCase, IntakeDraft, IntakeStatus
from collabproof.render import render_194r


QUERY = """Question: Determine the Section 194R treatment for FY 2024-25.
Facts:
- Brand entity type: company
- Transfer is in brand business or profession: yes
- Brand preceding-FY business turnover: INR 5,00,00,000
- Brand preceding-FY profession receipts: INR 0
- Creator is resident in India for tax purposes: yes
- Creator PAN furnished: yes
- Creator prior FY benefits from this brand: INR 0
- Creator prior Section 194R TDS: INR 0
- Product fair market value: INR 30,000
- Product retained: yes
- Tax borne by: recipient
"""


def create_draft(tmp_path: Path, source: str = QUERY) -> tuple[Path, IntakeDraft]:
    query_path = tmp_path / "query.txt"
    draft_path = tmp_path / "draft.json"
    query_path.write_text(source, encoding="utf-8")
    draft = pipeline.formalize_file(
        query_path, draft_path, case_id="pipeline-case"
    )
    return draft_path, draft


def test_formalize_file_persists_a_strict_reviewable_draft(tmp_path):
    draft_path, draft = create_draft(tmp_path)

    assert draft.status is IntakeStatus.AWAITING_CONFIRMATION
    persisted = json.loads(draft_path.read_text(encoding="utf-8"))
    assert persisted["draft_sha256"] == draft.draft_sha256
    assert persisted["source"]["text"] == QUERY
    assert IntakeDraft.from_dict(persisted) == draft


@pytest.mark.parametrize("failure", ["incomplete", "wrong_digest", "declined"])
def test_prover_is_never_called_before_a_valid_explicit_confirmation(
    tmp_path, monkeypatch, failure
):
    source = QUERY
    accepted = True
    if failure == "incomplete":
        source = source.replace("- Creator PAN furnished: yes\n", "")
    draft_path, draft = create_draft(tmp_path, source)
    digest = draft.draft_sha256
    if failure == "wrong_digest":
        digest = "0" * 64
    if failure == "declined":
        accepted = False

    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Lean certifier must not be called")

    monkeypatch.setattr(pipeline, "certify_194r_facts", forbidden)
    with pytest.raises(pipeline.PipelineError, match="confirmation"):
        pipeline.prove_draft_file(
            draft_path,
            presented_draft_sha256=digest,
            accepted=accepted,
            output_dir=tmp_path / "proof",
        )
    assert calls == 0
    assert not (tmp_path / "proof").exists()


def test_duplicate_json_keys_fail_before_confirmation_or_proof(tmp_path, monkeypatch):
    draft_path, draft = create_draft(tmp_path)
    draft_path.write_text(
        '{"schema_version":"first","schema_version":"second"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pipeline,
        "certify_194r_facts",
        lambda *args, **kwargs: pytest.fail("Lean certifier must not be called"),
    )

    with pytest.raises(pipeline.PipelineError, match="duplicate object key"):
        pipeline.prove_draft_file(
            draft_path,
            presented_draft_sha256=draft.draft_sha256,
            accepted=True,
            output_dir=tmp_path / "proof",
        )


def test_oversized_query_is_rejected_before_read_or_draft_write(tmp_path):
    query_path = tmp_path / "oversized.txt"
    draft_path = tmp_path / "draft.json"
    query_path.write_bytes(b"x" * 65_537)

    with pytest.raises(pipeline.PipelineError, match="byte limit"):
        pipeline.formalize_file(query_path, draft_path, case_id="oversized")
    assert not draft_path.exists()


def test_complete_pipeline_kernel_checks_renders_and_never_overwrites(
    tmp_path, monkeypatch
):
    draft_path, draft = create_draft(tmp_path)
    result = pipeline.prove_draft_file(
        draft_path,
        presented_draft_sha256=draft.draft_sha256,
        accepted=True,
        output_dir=tmp_path / "proof",
    )

    assert result.confirmed_case_path.is_file()
    assert result.certificate_path.is_file()
    assert result.proof_artifact_path.is_file()
    assert result.rendered_answer_path.is_file()
    assert result.rendered_text_path.is_file()
    assert result.manifest_path.is_file()

    confirmed = ConfirmedCase.from_dict(
        json.loads(result.confirmed_case_path.read_text(encoding="utf-8"))
    )
    certificate = json.loads(result.certificate_path.read_text(encoding="utf-8"))
    rendered = json.loads(result.rendered_answer_path.read_text(encoding="utf-8"))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert {
        key: value
        for key, value in certificate["intake"].items()
        if key != "confirmed_case_artifact"
    } == confirmed.certificate_record()
    assert certificate["lean_checks"]["per_case_kernel_check"]["result"] == "PASS"
    assert certificate["decision_status"] == "ANSWERED"
    assert rendered["schema_version"] == pipeline.RENDERED_ANSWER_SCHEMA_VERSION
    assert rendered["text"] == result.answer.text
    assert rendered["confirmation_sha256"] == confirmed.confirmation_sha256
    text_artifact = result.rendered_text_path.read_text(encoding="utf-8")
    assert f"Certificate SHA-256: {result.answer.certificate_sha256}" in text_artifact
    assert f"Confirmation SHA-256: {confirmed.confirmation_sha256}" in text_artifact
    assert text_artifact.endswith(result.answer.text + "\n")
    assert manifest["schema_version"] == pipeline.RUN_MANIFEST_SCHEMA_VERSION
    assert manifest["status"] == "VERIFIED_AND_RENDERED"
    assert manifest["case_id"] == confirmed.case_id
    assert manifest["confirmation_sha256"] == confirmed.confirmation_sha256
    assert manifest["certificate_sha256"] == result.answer.certificate_sha256
    expected_artifacts = {
        "confirmed_case": result.confirmed_case_path,
        "proof_artifact": result.proof_artifact_path,
        "certificate": result.certificate_path,
        "rendered_answer": result.rendered_answer_path,
        "rendered_text": result.rendered_text_path,
    }
    assert set(manifest["artifacts"]) == set(expected_artifacts)
    for name, path in expected_artifacts.items():
        assert manifest["artifacts"][name] == {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    assert render_194r(result.certificate_path) == result.answer
    assert "Section 194R TDS due now is ₹3,000" in result.answer.text
    assert "cash TDS, GST" in result.answer.text

    certificate_bytes = result.certificate_path.read_bytes()
    monkeypatch.setattr(
        pipeline,
        "certify_194r_facts",
        lambda *args, **kwargs: pytest.fail("existing run must fail before Lean"),
    )
    with pytest.raises(pipeline.PipelineError, match="File exists"):
        pipeline.prove_draft_file(
            draft_path,
            presented_draft_sha256=draft.draft_sha256,
            accepted=True,
            output_dir=tmp_path / "proof",
        )
    assert result.certificate_path.read_bytes() == certificate_bytes


def test_manifest_refuses_certificate_changed_after_render(tmp_path, monkeypatch):
    draft_path, draft = create_draft(tmp_path)
    real_render = pipeline.render_194r

    def render_then_tamper(path):
        answer = real_render(path)
        Path(path).write_bytes(Path(path).read_bytes() + b" ")
        return answer

    monkeypatch.setattr(pipeline, "render_194r", render_then_tamper)
    with pytest.raises(pipeline.PipelineError, match="changed after verified rendering"):
        pipeline.prove_draft_file(
            draft_path,
            presented_draft_sha256=draft.draft_sha256,
            accepted=True,
            output_dir=tmp_path / "proof",
        )
    assert not list((tmp_path / "proof").rglob("manifest.json"))


def test_formalize_cli_reports_hash_and_nonready_status(tmp_path, capsys):
    query_path = tmp_path / "query.txt"
    draft_path = tmp_path / "draft.json"
    query_path.write_text(
        QUERY.replace("- Creator PAN furnished: yes\n", ""), encoding="utf-8"
    )

    exit_code = pipeline.main([
        "formalize",
        str(query_path),
        "--output",
        str(draft_path),
        "--case-id",
        "cli-case",
    ])
    summary = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert summary["status"] == "NEEDS_CLARIFICATION"
    assert summary["draft_sha256"]
    assert len(summary["facts"]) == 10
    assert summary["specification_bundle_sha256"]
    assert summary["rule_bundle_hash"]
    assert summary["clarification_questions"]
    assert draft_path.is_file()
