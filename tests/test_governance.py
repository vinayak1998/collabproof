"""Source-to-rule governance, bundle identity, and update impact gates."""
from copy import deepcopy
import json
import re

from collabproof import (Brand, Collab, Creator, EntityType, RULES, assess,
                         assessment_as_claim, rup, verify)
from collabproof import governance
from experiments import three_arms


def test_checked_in_governance_is_complete_and_valid():
    assert governance.validate_governance(RULES) == []


def test_every_certificate_has_current_bundle_hash_and_rule_trail():
    collab = Collab(
        brand=Brand(EntityType.COMPANY), creator=Creator(),
        product_fmv_paise=rup(30_000),
    )
    certificate = verify(assessment_as_claim(assess(collab)), collab)
    assert certificate.rule_bundle_hash == governance.rule_bundle_hash(RULES)
    assert "IT-194R-THRESHOLD" in certificate.governed_rule_ids
    assert certificate.freshness().state == "CURRENT"
    assert certificate.governance_record()["rule_bundle_hash"] == certificate.rule_bundle_hash


def test_browser_verifier_embeds_current_bundle_hash():
    text = governance.JS_PATH.read_text(encoding="utf-8")
    match = re.search(r'const RULE_BUNDLE_HASH = "([0-9a-f]{64})";', text)
    assert match and match.group(1) == governance.rule_bundle_hash(RULES)


def test_validator_rejects_orphans_unreviewed_production_and_snapshots(monkeypatch):
    with_orphan = dict(RULES)
    with_orphan["ORPHAN"] = next(iter(RULES.values()))
    assert "orphaned runtime rule ID: ORPHAN" in governance.validate_governance(with_orphan)

    prov = deepcopy(governance.provenance())
    prov["rules"]["IT-194R-GROSSUP"]["status"] = "production"
    monkeypatch.setattr(governance, "provenance", lambda: prov)
    errors = governance.validate_governance(RULES)
    assert "IT-194R-GROSSUP: unreviewed rule marked production" in errors

    man = deepcopy(governance.manifest())
    man["sources"][0]["snapshot"] = "sources/snapshots/act.pdf"
    monkeypatch.setattr(governance, "manifest", lambda: man)
    errors = governance.validate_governance(RULES)
    assert any("impermissible checked-in snapshot" in error for error in errors)


def test_source_update_maps_affected_rules_and_certificate_states():
    old = deepcopy(governance.manifest())
    new = deepcopy(old)
    source = next(item for item in new["sources"] if item["id"] == "cbdt-circular-12-2022")
    source["sha256"] = {"state": "verified", "value": "a" * 64, "reason": None}
    impact = governance.source_impact(old, new)
    assert impact["changed_source_ids"] == ["cbdt-circular-12-2022"]
    assert "IT-194R-GROSSUP" in impact["affected_rule_ids"]

    stale = governance.certificate_freshness(
        "old", ["IT-194R-GROSSUP"], "new", impact["affected_rule_ids"])
    eligible = governance.certificate_freshness(
        "old", ["GST-RATE-18"], "new", impact["affected_rule_ids"])
    assert stale.state == "STALE"
    assert stale.affected_rule_ids == ("IT-194R-GROSSUP",)
    assert eligible.state == "REVERIFY_ELIGIBLE"


def test_offline_corpus_fixture_is_metadata_only_and_paraphrases_are_gone():
    fixture = json.loads(
        (governance.ROOT / "sources/fixtures/official-corpus-metadata.json")
        .read_text(encoding="utf-8")
    )
    assert fixture["purpose"].startswith("Offline governance test fixture")
    assert not list((governance.ROOT / "experiments/corpus").glob("[1-9]*.md"))


def test_grounded_corpus_reads_only_manifest_cache(tmp_path, monkeypatch):
    cache = tmp_path / "sources/cache/official.html"
    cache.parent.mkdir(parents=True)
    cache.write_text("<html><body><h1>Official fixture</h1><script>ignore()</script></body></html>",
                     encoding="utf-8")
    man = {
        "sources": [{
            "id": "official", "title": "Official fixture",
            "official_url": "https://www.incometaxindia.gov.in/example",
            "cache_path": "sources/cache/official.html",
        }]
    }
    prov = {"rules": {"R": {"sources": [{"source_id": "official"}]}}}
    monkeypatch.setattr(three_arms, "ROOT", tmp_path)
    monkeypatch.setattr(three_arms, "manifest", lambda: man)
    monkeypatch.setattr(three_arms, "provenance", lambda: prov)
    corpus = three_arms.load_corpus()
    assert "Official fixture" in corpus
    assert "ignore()" not in corpus
