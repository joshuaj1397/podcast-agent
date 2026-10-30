from podcast_agent import ingest


def test_txt_parsing_cleaning_and_speakers(noisy_txt, spec):
    ep = ingest.load(noisy_txt, spec)
    assert ep.episode_id == "ep900" and ep.recorded == "2025-02-10"
    assert [t.ts for t in ep.turns] == ["00:00:05", "00:02:14", "00:02:18", "00:03:01"]
    assert ep.turns[0].speaker == "Jamie Ortiz"  # "Host"
    assert ep.turns[2].speaker == "Dr. Lena Park"  # "Guest" with a single guest
    assert ep.turns[3].speaker == "Dr. Lena Park"  # first name
    assert "Um" not in ep.turns[0].text and "you know" not in ep.turns[0].text
    assert "NASA" in ep.turns[1].text  # glossary fix
    assert "Bitcoin" in ep.turns[3].text and "uh" not in ep.turns[3].text.split()
    assert ep.turns[2].text.endswith("continues the previous turn.")  # continuation line joined
    assert ep.turns[1].raw.startswith("So Nassa")  # raw text kept


def test_json_sample_speakers(spec):
    ep = ingest.load("data/heldout/ep002_ai_healthcare.json", spec)
    assert ep.episode_id == "ep002"
    assert {t.speaker for t in ep.turns} == {"David Chen", "Dr. Priya Patel", "Alex Moore"}
    assert ep.turns[0].id == "T01" and ep.turns[0].ts == "00:00"


def test_clean_keeps_meaningful_like(spec):
    assert ingest.clean("I like this idea", spec) == "I like this idea"
    assert ingest.clean("It was, like, huge", spec) == "It was huge"


def test_profile_counts(noisy_txt, spec):
    ep = ingest.load(noisy_txt, spec)
    p = ingest.profile(ep, 2)
    assert p["turns"] == 4 and p["claim_candidates"] == 2 and p["filler_words_removed"] > 0
