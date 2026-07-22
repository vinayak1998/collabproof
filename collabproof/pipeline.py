"""Two-phase controlled-English to Lean proof pipeline for Section 194R.

``formalize`` persists a reviewable draft; ``prove`` accepts only that strict
draft schema plus its exact digest and an explicit acceptance flag.  The
natural-language renderer receives only the persisted Lean certificate.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Optional

from .intake import (
    MAX_QUERY_BYTES,
    IntakeDraft,
    IntakeStatus,
    confirm_194r,
    formalize_194r,
)
from .render import RenderedAnswer, render_194r
from .runtime_proof import LeanCertificationError, certify_194r_facts


RENDERED_ANSWER_SCHEMA_VERSION = "collabproof-s194r-rendered-answer-v1"
RUN_MANIFEST_SCHEMA_VERSION = "collabproof-s194r-run-manifest-v1"
MAX_DRAFT_BYTES = 1_000_000


class PipelineError(RuntimeError):
    """The bounded pipeline refused to continue or failed closed."""


@dataclass(frozen=True)
class PipelineResult:
    """Paths emitted after confirmation, proof checking, and safe rendering."""

    run_directory: Path
    confirmed_case_path: Path
    certificate_path: Path
    proof_artifact_path: Path
    rendered_answer_path: Path
    rendered_text_path: Path
    manifest_path: Path
    answer: RenderedAnswer


def _load_json_object(path: str | Path, *, description: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("path is not a regular file")
        if resolved.stat().st_size > MAX_DRAFT_BYTES:
            raise ValueError(f"file exceeds the {MAX_DRAFT_BYTES}-byte limit")
        raw = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(f"cannot load {description}: {exc}") from exc
    if type(raw) is not dict:
        raise PipelineError(f"{description} must contain a JSON object")
    return raw


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: object) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode(
            "utf-8"
        ),
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formalize_file(
    query_path: str | Path,
    output_path: str | Path,
    *,
    case_id: Optional[str] = None,
) -> IntakeDraft:
    """Parse one UTF-8 controlled-English file and persist its review draft."""

    try:
        source_path = Path(query_path).resolve(strict=True)
        if not source_path.is_file():
            raise ValueError("query path is not a regular file")
        if source_path.stat().st_size > MAX_QUERY_BYTES:
            raise ValueError(f"query exceeds the {MAX_QUERY_BYTES}-byte limit")
        query = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise PipelineError(f"cannot load controlled-English query: {exc}") from exc

    destination = Path(output_path).resolve()
    if destination == source_path:
        raise PipelineError("draft output must not overwrite the source query")
    draft = formalize_194r(query, case_id=case_id)
    try:
        _write_json(destination, draft.as_dict())
    except OSError as exc:
        raise PipelineError(f"cannot persist intake draft: {exc}") from exc
    return draft


def _rendered_record(
    answer: RenderedAnswer, *, confirmation_sha256: str
) -> dict[str, object]:
    return {
        "schema_version": RENDERED_ANSWER_SCHEMA_VERSION,
        "certificate_sha256": answer.certificate_sha256,
        "confirmation_sha256": confirmation_sha256,
        "text": answer.text,
        "rule_ids": list(answer.rule_ids),
        "claims": [
            {
                "pointer": claim.pointer,
                "value": claim.value,
                "rule_ids": list(claim.rule_ids),
                "template_id": claim.template_id,
            }
            for claim in answer.claims
        ],
    }


def prove_draft_file(
    draft_path: str | Path,
    *,
    presented_draft_sha256: str,
    accepted: bool,
    output_dir: str | Path,
) -> PipelineResult:
    """Confirm a persisted draft, kernel-check it, and render its certificate.

    No proof process is invoked until the draft has passed strict round-trip
    parsing, the presented digest matches, and ``accepted`` is exactly true.
    """

    raw = _load_json_object(draft_path, description="intake draft")
    try:
        draft = IntakeDraft.from_dict(raw)
        confirmed = confirm_194r(
            draft,
            presented_draft_sha256=presented_draft_sha256,
            accepted=accepted,
        )
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"confirmation failed closed: {exc}") from exc
    if confirmed is None:
        raise PipelineError("confirmation was declined; no proof was attempted")

    base = Path(output_dir).resolve()
    run_directory = base / (
        f"s194r-{confirmed.case_id}-{confirmed.confirmation_sha256}"
    )
    try:
        base.mkdir(parents=True, exist_ok=True)
        run_directory.mkdir(mode=0o700, exist_ok=False)
        confirmed_case_path = run_directory / "confirmed-case.json"
        _write_json(confirmed_case_path, confirmed.as_dict())
        certificate = certify_194r_facts(
            confirmed.facts,
            run_directory,
            confirmed_case_path=confirmed_case_path,
        )
        certificate_path = Path(certificate["certificate_path"]).resolve(strict=True)
        proof_artifact_path = Path(
            certificate["proof_artifact"]["path"]
        ).resolve(strict=True)
        answer = render_194r(certificate_path)
    except (KeyError, LeanCertificationError, OSError, TypeError, ValueError) as exc:
        raise PipelineError(f"proof or rendering failed closed: {exc}") from exc

    rendered_answer_path = run_directory / "answer.json"
    rendered_text_path = run_directory / "answer.txt"
    try:
        _write_json(
            rendered_answer_path,
            _rendered_record(
                answer, confirmation_sha256=confirmed.confirmation_sha256
            ),
        )
        rendered_text = (
            f"Certificate SHA-256: {answer.certificate_sha256}\n"
            f"Confirmation SHA-256: {confirmed.confirmation_sha256}\n\n"
            f"{answer.text}\n"
        )
        _atomic_write_bytes(rendered_text_path, rendered_text.encode("utf-8"))
        manifest_path = run_directory / "manifest.json"
        artifacts = {
            "confirmed_case": confirmed_case_path,
            "proof_artifact": proof_artifact_path,
            "certificate": certificate_path,
            "rendered_answer": rendered_answer_path,
            "rendered_text": rendered_text_path,
        }
        artifact_hashes = {
            name: _file_sha256(path) for name, path in artifacts.items()
        }
        if artifact_hashes["certificate"] != answer.certificate_sha256:
            raise PipelineError(
                "certificate changed after verified rendering; manifest refused"
            )
        if artifact_hashes["confirmed_case"] != certificate["intake"][
            "confirmed_case_artifact"
        ]["sha256"]:
            raise PipelineError(
                "confirmed case changed after certification; manifest refused"
            )
        if artifact_hashes["proof_artifact"] != certificate["proof_artifact"][
            "sha256"
        ]:
            raise PipelineError(
                "proof artifact changed after certification; manifest refused"
            )
        _write_json(
            manifest_path,
            {
                "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
                "status": "VERIFIED_AND_RENDERED",
                "case_id": confirmed.case_id,
                "confirmation_sha256": confirmed.confirmation_sha256,
                "normalized_fact_sha256": confirmed.normalized_fact_sha256,
                "certificate_sha256": answer.certificate_sha256,
                "artifacts": {
                    name: {"path": str(path), "sha256": artifact_hashes[name]}
                    for name, path in artifacts.items()
                },
            },
        )
    except OSError as exc:
        raise PipelineError(f"cannot persist rendered answer: {exc}") from exc
    return PipelineResult(
        run_directory=run_directory,
        confirmed_case_path=confirmed_case_path,
        certificate_path=certificate_path,
        proof_artifact_path=proof_artifact_path,
        rendered_answer_path=rendered_answer_path,
        rendered_text_path=rendered_text_path,
        manifest_path=manifest_path,
        answer=answer,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    formalize = subparsers.add_parser(
        "formalize", help="create a reviewable draft from controlled English"
    )
    formalize.add_argument("query_file", type=Path)
    formalize.add_argument("--output", type=Path, required=True)
    formalize.add_argument("--case-id")

    prove = subparsers.add_parser(
        "prove", help="confirm a draft, run Lean, and render the certificate"
    )
    prove.add_argument("draft_file", type=Path)
    prove.add_argument("--confirm-sha256", required=True)
    prove.add_argument(
        "--accept",
        action="store_true",
        required=True,
        help="explicitly accept the exact displayed draft digest",
    )
    prove.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "formalize":
            draft = formalize_file(
                args.query_file,
                args.output,
                case_id=args.case_id,
            )
            draft_record = draft.as_dict()
            print(json.dumps({
                "draft_path": str(args.output.resolve()),
                "status": draft.status.value,
                "draft_sha256": draft.draft_sha256,
                "facts": draft_record["facts"],
                "missing_fields": list(draft.missing_fields),
                "clarification_questions": list(draft.clarification_questions),
                "issues": list(draft.issues),
                "specification_version": draft.specification_version,
                "specification_bundle_sha256": (
                    draft.specification_bundle_sha256
                ),
                "rule_bundle_hash": draft.rule_bundle_hash,
            }, sort_keys=True))
            return 0 if draft.status is IntakeStatus.AWAITING_CONFIRMATION else 2

        result = prove_draft_file(
            args.draft_file,
            presented_draft_sha256=args.confirm_sha256,
            accepted=args.accept,
            output_dir=args.output_dir,
        )
        print(json.dumps({
            "run_directory": str(result.run_directory),
            "confirmed_case_path": str(result.confirmed_case_path),
            "certificate_path": str(result.certificate_path),
            "proof_artifact_path": str(result.proof_artifact_path),
            "rendered_answer_path": str(result.rendered_answer_path),
            "rendered_text_path": str(result.rendered_text_path),
            "manifest_path": str(result.manifest_path),
        }, sort_keys=True))
        return 0
    except PipelineError as exc:
        parser.exit(1, f"pipeline failed closed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
