"""Fail if the covered s.194R slice drifts across Python, JavaScript, and Lean."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collabproof import (Brand, Collab, Creator, EntityType, Q, TaxBearer,
                         assess, rup)
from collabproof.runtime_proof import normalized_194r_facts, theorem_source


COMPANY = Brand(EntityType.COMPANY)


def cases() -> list[Collab]:
    return [
        Collab(COMPANY, Creator(), product_fmv_paise=rup(20_000)),
        Collab(COMPANY, Creator(), product_fmv_paise=rup(20_001)),
        Collab(COMPANY, Creator(), product_fmv_paise=rup(30_000), product_retained=False),
        Collab(
            Brand(EntityType.INDIVIDUAL, preceding_fy_profession_receipts_paise=rup(40_00_000)),
            Creator(),
            product_fmv_paise=rup(30_000),
        ),
        Collab(
            Brand(EntityType.INDIVIDUAL, preceding_fy_business_turnover_paise=rup(1_00_00_001)),
            Creator(),
            product_fmv_paise=rup(30_000),
        ),
        Collab(COMPANY, Creator(pan_furnished=False), product_fmv_paise=rup(30_000)),
        Collab(
            COMPANY,
            Creator(),
            product_fmv_paise=rup(27_000),
            tax_borne_by=TaxBearer.PROVIDER,
        ),
        Collab(
            COMPANY,
            Creator(
                fy_prior_benefits_from_brand_paise=rup(10_000),
                fy_prior_194r_tds_paise=rup(500),
            ),
            product_fmv_paise=rup(15_000),
        ),
        Collab(COMPANY, Creator(), product_fmv_paise=0),
        Collab(COMPANY, Creator(is_resident=False), product_fmv_paise=rup(30_000)),
        Collab(
            Brand(EntityType.COMPANY, in_business=False),
            Creator(),
            product_fmv_paise=rup(30_000),
        ),
    ]


def js_facts(c: Collab) -> dict[str, object]:
    return {
        "brand_entity": c.brand.entity_type.value,
        "brand_business_turnover": c.brand.preceding_fy_business_turnover_paise,
        "brand_profession_receipts": c.brand.preceding_fy_profession_receipts_paise,
        "brand_in_business": c.brand.in_business,
        "resident": c.creator.is_resident,
        "pan": c.creator.pan_furnished,
        "prior_benefits": c.creator.fy_prior_benefits_from_brand_paise,
        "prior_194r_tds": c.creator.fy_prior_194r_tds_paise,
        "product": c.product_fmv_paise,
        "retained": c.product_retained,
        "bearer": c.tax_borne_by.value,
    }


def python_projection(c: Collab) -> dict[str, object]:
    a = assess(c)
    if not a.ok:
        return {"ok": False, "refusal": a.refusal_rule_id}
    return {
        "ok": True,
        "benefit_qualifies": a.d(Q.BENEFIT_QUALIFIES),
        "provider_obligated": a.d(Q.PROVIDER_OBLIGATED),
        "aggregate": a.d(Q.AGGREGATE_BENEFIT),
        "tds_194r": a.d(Q.TDS_194R),
        "gate": a.d(Q.RELEASE_GATE),
    }


def js_projection(raw: dict[str, object]) -> dict[str, object]:
    if not raw["ok"]:
        return {"ok": False, "refusal": raw["refusal"]}
    return {
        "ok": True,
        "benefit_qualifies": raw["benefit_qualifies"],
        "provider_obligated": raw["provider_obligated"],
        "aggregate": raw["aggregate"],
        "tds_194r": raw["tds_194r"],
        "gate": raw["gate"],
    }


def main() -> int:
    parity_cases = cases()
    node_program = (
        "const fs=require('fs');"
        "const cp=require('./docs/collabproof.js');"
        "const xs=JSON.parse(fs.readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(xs.map(x=>cp.assess(x))));"
    )
    node = subprocess.run(
        ["node", "-e", node_program],
        cwd=ROOT,
        input=json.dumps([js_facts(c) for c in parity_cases]),
        text=True,
        capture_output=True,
        check=False,
    )
    if node.returncode != 0:
        raise SystemExit(f"Node parity runner failed: {node.stderr}")
    js_results = json.loads(node.stdout)
    for index, (case, js_result) in enumerate(zip(parity_cases, js_results, strict=True)):
        expected = python_projection(case)
        got = js_projection(js_result)
        if got != expected:
            raise SystemExit(f"Python/JavaScript s.194R drift at case {index}: {got} != {expected}")

    theorem_blocks = ["import LeanProof.S194R\n"]
    for index, case in enumerate(parity_cases):
        fact_hash = __import__("hashlib").sha256(
            json.dumps(
                normalized_194r_facts(case), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        source, _ = theorem_source(case, fact_hash)
        body = source.split("\n", 1)[1]
        theorem_blocks.append(f"namespace ParityCase{index}\n{body}\nend ParityCase{index}\n")

    build = subprocess.run(
        ["lake", "build", "LeanProof.S194R"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if build.returncode != 0:
        raise SystemExit(f"Lean module build failed: {build.stderr}")
    with tempfile.TemporaryDirectory() as temp_dir:
        artifact = Path(temp_dir) / "S194RParity.lean"
        artifact.write_text("\n".join(theorem_blocks), encoding="utf-8")
        lean = subprocess.run(
            ["lake", "env", "lean", str(artifact)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    if lean.returncode != 0:
        raise SystemExit(f"Python/Lean s.194R drift: {lean.stderr}")
    print(f"PASS: {len(parity_cases)} s.194R cases agree across Python, JavaScript, and Lean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
