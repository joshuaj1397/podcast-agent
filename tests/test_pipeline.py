"""End-to-end with FakeLlm: exercises ADK agents, tools, callbacks, critic and rendering without Ollama."""

import asyncio
import json

from podcast_agent import critic, ingest
from podcast_agent.models import Plan
from podcast_agent.pipeline import Pipeline


def run(spec, settings, path, **kw):
    return asyncio.run(Pipeline(spec, settings).run_file(path, **kw))


def test_end_to_end_writes_outputs(spec, fake_settings, noisy_txt):
    report = run(spec, fake_settings, noisy_txt)
    out = fake_settings.out_dir / "ep900"
    assert {p.name for p in out.iterdir()} == {"report.json", "report.md", "trace.jsonl"}
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["episode_id"] == "ep900"
    assert "## Fact-check" in (out / "report.md").read_text(encoding="utf-8")
    kinds = {json.loads(line)["kind"] for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()}
    assert {"plan", "thought", "action", "observation", "result"} <= kinds
    assert 200 <= critic.word_count(report.summary) <= 300
    assert len(report.takeaways) == 5


def test_quotes_are_copied_from_transcript(spec, fake_settings):
    report = run(spec, fake_settings, "data/heldout/ep001_remote_work.json")
    ep = ingest.load("data/heldout/ep001_remote_work.json", spec)
    for q in report.quotes:
        turn = ep.turn(q.turn_id)
        assert (q.text, q.ts, q.speaker) == (turn.text, turn.ts, turn.speaker)
    assert critic.structural_problems(report, ep, spec) == []


def test_predictions_are_unverifiable_by_rule(spec, fake_settings, noisy_txt):
    report = run(spec, fake_settings, noisy_txt)
    for fc in report.fact_checks:
        if fc.type in ("prediction", "anecdote"):
            assert fc.status == "unverifiable" and fc.method == "rule" and fc.confidence == 0.4


def test_only_factcheck_pulls_in_claims_dependency(spec, fake_settings, noisy_txt):
    report = run(spec, fake_settings, noisy_txt, only={"factcheck"})
    assert report.summary == "" and report.fact_checks


def test_plan_validation_inserts_and_reorders(spec, fake_settings):
    p = Pipeline(spec, fake_settings)
    plan = Plan(reasoning="", steps=["factcheck", "summarize"], claim_budget=99, use_web_search=True, summary_focus="")
    fixed, notes = p.validate_plan(plan, {"claim_candidates": 3})
    assert fixed.steps.index("claims") < fixed.steps.index("factcheck")
    assert set(fixed.steps) == {"summarize", "notes", "claims", "factcheck"}
    assert fixed.claim_budget == spec.limits.claim_budget and notes


def test_critic_fallbacks_restore_structure(spec, fake_settings):
    ep = ingest.load("data/heldout/ep001_remote_work.json", spec)
    ranked = Pipeline(spec, fake_settings)._rank_turns(ep)
    summary = critic.fallback_summary("Too short.", ep, ranked, spec)
    assert critic.word_count(summary) >= spec.summary.min_words or len(ranked) < 5
    t, q, g = critic.fallback_notes(["Only one takeaway here today."], [], [], ranked, ["remote work", "slack", "docs"], spec)
    assert critic.check_notes(t, q, g, spec) == []
