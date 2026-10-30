from podcast_agent import factcheck, ingest
from podcast_agent.retrieval import Doc, Retriever, tokenize


def docs():
    return [
        Doc("kb-1", "kb", "Mars sample return delayed", "NASA's Mars Sample Return will not return samples before the 2030s.",
            "NASA (mock DB)", "2025-01-07", "kb"),
        Doc("kb-2", "kb", "Bitcoin price history", "Bitcoin peaked near USD 126,000 in October 2025.", "Mock", "2025-11-01", "kb"),
        *[Doc(f"kb-x{i}", "kb", f"Filler {i}", f"Unrelated entry number {i} about cooking.", "Mock", "2024-01-01", "kb")
          for i in range(5)],
        Doc("web-1", "web", "Clickbait", "Mars colony by 2026, insiders say.", "rumors.test", "2020-01-01", "low"),
    ]


def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("The samples are returning") == ["sample", "returning"]


def test_bm25_and_tfidf_find_the_right_doc():
    r = Retriever(docs())
    for method in ("bm25", "tfidf"):
        assert r.kb.search("mars sample return", 1, method)[0][0].id == "kb-1"
    assert r.web.search("bitcoin", 3) == []  # no web doc mentions it


def test_claim_candidates_prefilter(noisy_txt, spec):
    ep = ingest.load(noisy_txt, spec)
    ids = [t.id for t in factcheck.claim_candidates(ep, spec)]
    assert "T02" in ids and "T03" in ids and "T04" in ids
    assert "T01" not in ids  # greeting, no entities or numbers


def test_confidence_formula(spec):
    assert factcheck.confidence("verified", 1.0, 1.0, spec) == 0.97  # clipped
    assert factcheck.confidence("verified", 1.0, 0.77, spec) == 0.92  # the PDF example
    assert factcheck.confidence("unverifiable", 1.0, 1.0, spec) == 0.5  # capped
    assert factcheck.confidence("inaccurate", 1 / 3, 0.3, spec) == round(0.2 + 0.15 + 0.105, 2)


def test_aggregate_majority_and_ties():
    samples = [{"label": "supported", "evidence_ids": ["kb-1"], "reasoning": "a"},
               {"label": "contradicted", "evidence_ids": [], "reasoning": "b"},
               {"label": "supported", "evidence_ids": ["kb-1"], "reasoning": "c"}]
    label, consistency, chosen = factcheck.aggregate(samples)
    assert label == "supported" and round(consistency, 2) == 0.67 and chosen["reasoning"] == "a"
    label, _, _ = factcheck.aggregate(samples[:2])  # 1-1 tie -> greedy sample 0 wins
    assert label == "supported"


def test_verdict_without_citation_is_not_trusted(noisy_txt, spec):
    ep = ingest.load(noisy_txt, spec)
    claim = {"claim": "x", "type": "factual"}
    samples = [{"label": "supported", "evidence_ids": [], "reasoning": "r"}]
    result = factcheck.judged_result(claim, ep.turns[1], samples, {d.id: d for d in docs()}, [], spec)
    assert result.status == "unverifiable"


def test_quantities_parse_magnitudes_and_skip_years():
    assert factcheck.quantities("about 2 billion users in 2024") == [2e9]
    assert factcheck.quantities("$1.65 billion, 40% and 100,000 dollars") == [1.65e9, 40.0, 100000.0]


def test_numeric_check_flags_stale_numbers_only():
    kb = Doc("kb-9", "kb", "Spotify users", "Spotify reported 675 million monthly active users in 2025.", "Mock",
             "2025-07-01", "kb")
    assert factcheck.numbers_disagree("Spotify has about 400 million monthly users.", [kb]) == (4e8, [6.75e8])
    assert factcheck.numbers_disagree("Spotify has about 680 million monthly users.", [kb]) is None
    assert factcheck.numbers_disagree("Spotify launched in Sweden in 2008.", [kb]) is None  # years are not compared


def test_numeric_check_downgrades_supported_verdict(noisy_txt, spec):
    ep = ingest.load(noisy_txt, spec)
    kb = Doc("kb-9", "kb", "Facebook users", "Facebook has about 3.07 billion monthly users.", "Mock", "2025-01-01", "kb")
    samples = [{"label": "supported", "evidence_ids": ["kb-9"], "reasoning": "r"}]
    claim = {"claim": "Facebook has around 2 billion monthly users.", "type": "statistic"}
    assert factcheck.judged_result(claim, ep.turns[1], samples, {"kb-9": kb}, [], spec).status == "inaccurate"
    off = spec.model_copy(update={"factcheck": spec.factcheck.model_copy(update={"numeric_check": False})})
    assert factcheck.judged_result(claim, ep.turns[1], samples, {"kb-9": kb}, [], off).status == "verified"
