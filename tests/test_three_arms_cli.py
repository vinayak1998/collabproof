import sys

from collabproof import governance
from experiments import three_arms


def test_missing_api_key_exits_before_loading_official_corpus(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["three_arms.py", "--n", "1"])

    def unexpected_corpus_load():
        raise AssertionError("missing API key must be handled before corpus access")

    monkeypatch.setattr(three_arms, "load_corpus", unexpected_corpus_load)

    assert three_arms.main() == 1
    assert capsys.readouterr().out == (
        "ANTHROPIC_API_KEY not set. No LLM numbers are invented; "
        "run --selftest to check plumbing.\n"
    )


def test_grossup_provenance_does_not_classify_t6_as_a_theorem():
    record = governance.provenance()["rules"]["IT-194R-GROSSUP"]

    assert record["formal_theorems"] == []
    assert "standalone non-runtime-bound exhibit" in record["interpretation"]
    assert any(
        "not a theorem about assess()" in assumption
        for assumption in record["assumptions"]
    )
