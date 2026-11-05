"""Series summary with FakeLlm: persistence across runs, the length limit, ledger operations, deterministic facts."""

import asyncio
import json

import pytest

from podcast_agent import harness
from podcast_agent.series import (DigestPoint, LedgerItem, Manifest, SeriesState, SeriesUpdater, apply_ops,
                                  enforce_length, load_state, recurring_guests, recurring_segments, series_spec)

EP = {
    "ep001": "data/heldout/ep001_remote_work.json",
    "ep004": "data/heldout/ep004_async_writing_culture.json",
    "ep007": "data/heldout/ep007_ai_agents_remote_work.json",
}


def manifest(tmp_path, episodes):
    path = tmp_path / "series.yaml"
    path.write_text("series_id: test_series\ntitle: Test Series\nepisodes:\n"
                    + "".join(f"  - {EP[e]}\n" for e in episodes), encoding="utf-8")
    return path


def updater(tmp_path, spec, settings, episodes=("ep001", "ep004")):
    return SeriesUpdater(Manifest.load(manifest(tmp_path, episodes)), spec, settings)


def test_state_persists_across_runs_and_sync_is_idempotent(tmp_path, spec, fake_settings):
    first = asyncio.run(updater(tmp_path, spec, fake_settings).sync(until="ep001"))
    assert first.version == 1 and first.episodes == ["ep001"] and first.history[0].mode == "initial"

    # A new updater (a separate run of the app) continues from the saved state.
    second = asyncio.run(updater(tmp_path, spec, fake_settings).sync())
    assert second.version == 2 and second.episodes == ["ep001", "ep004"]
    assert [h.episode_id for h in second.history] == ["ep001", "ep004"]
    assert second.history[1].mode == "incremental"

    third = asyncio.run(updater(tmp_path, spec, fake_settings).sync())
    assert third.version == 2  # nothing new: no update

    saved = json.loads((fake_settings.out_dir / "series/test_series/series.json").read_text(encoding="utf-8"))
    assert saved["char_count"] == len(saved["summary"]) <= saved["max_chars"] == 4000
    assert {"summary", "char_count", "episodes", "history"} <= saved.keys()
    folder = fake_settings.out_dir / "series/test_series"
    assert {p.name for p in (folder / "versions").iterdir()} == {"v01.json", "v02.json"}
    assert {p.name for p in (folder / "traces").iterdir()} == {"v01-ep001.jsonl", "v02-ep004.jsonl"}
    md = (folder / "series.md").read_text(encoding="utf-8")
    assert "## Update history" in md and f"{saved['char_count']:,} / 4,000 characters" in md


def test_length_limit_is_enforced_in_code(tmp_path, spec, fake_settings):
    series = spec.series.model_copy(update={"max_chars": 300, "target_chars": 250})
    small = spec.model_copy(update={"series": series})
    state = asyncio.run(updater(tmp_path, small, fake_settings).sync())
    for h in state.history:
        assert h.chars <= 300
    assert state.char_count == len(state.summary) <= 300
    assert state.history[-1].length_actions  # the fake writer's draft was too long and code trimmed it
    assert "ep001" in state.summary and "ep004" in state.summary  # every episode keeps its arc line


def test_enforce_length_keeps_sections_and_every_episode():
    prose = {k: " ".join(f"Sentence {i} about {k} with some extra words." for i in range(30))
             for k in ("themes", "evolution", "connections")}
    arc = [(f"ep{i:03d}", f"Title {i}", "A long role sentence for this episode. " * 4) for i in range(1, 8)]
    text, actions = enforce_length(prose, arc, 1500)
    assert len(text) <= 1500 and actions
    assert all(f"ep{i:03d}" in text for i in range(1, 8))
    assert text.count("## ") == 4
    unchanged, none = enforce_length({"themes": "Short.", "evolution": "Short.", "connections": "Short."}, arc[:1], 4000)
    assert none == [] and len(unchanged) < 4000


def test_revise_supersedes_instead_of_keeping_both():
    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep006"])
    state.ledger = [LedgerItem(id="L01", kind="prediction", text="Remote-first companies will dominate hiring.",
                               episodes=["ep001"], added_in="ep001", updated_in="ep001"),
                    LedgerItem(id="L02", kind="theme", text="Documentation.", episodes=["ep001"], added_in="ep001",
                               updated_in="ep001")]
    points = {"ep006.P1": DigestPoint(id="ep006.P1", kind="prediction", text="Intentional hybrid wins.")}
    ops = [{"point": "ep006.P1", "op": "revise", "target": "L01", "text": "Mark revised: intentional hybrid wins.",
            "reason": "Mark revised his prediction"},
           {"point": "ep006.P1", "op": "revise", "target": "L01", "text": "Dana agrees: structured hybrid sticks.",
            "reason": "a second point cannot replace the same statement"},
           {"point": "", "op": "reinforce", "target": "L02", "text": "", "reason": "documentation again"},
           {"point": "", "op": "reinforce", "target": "L99", "text": "Unknown target text.", "reason": "bad id"}]
    applied = apply_ops(state, "ep006", ops, points, max_items=40)
    assert [a.op for a in applied] == ["revise", "add", "reinforce", "add"]
    old = state.item("L01")
    assert old.status == "superseded" and old.superseded_by == "L03" and state.item("L03").revises == "L01"
    assert "already revised" in applied[1].reason and state.item("L04").status == "current"
    current = {i.id for i in state.current()}
    assert "L01" not in current and {"L03", "L04"} <= current  # no conflicting statements side by side
    assert state.item("L02").episodes == ["ep001", "ep006"]
    assert "unknown" in applied[-1].reason


def test_ledger_is_capped_by_archiving_the_least_important():
    state = SeriesState(series_id="s", title="S", episodes=["ep001"])
    ops = [{"point": "", "op": "add", "target": "new", "text": f"Item {i}.", "reason": ""} for i in range(8)]
    applied = apply_ops(state, "ep001", ops, {}, max_items=5)
    assert len(state.current()) == 5 and sum(a.op == "archive" for a in applied) == 3


def test_recurring_guests_segments_and_mentions(tmp_path, spec, fake_settings):
    state = asyncio.run(updater(tmp_path, spec, fake_settings, ("ep001", "ep004", "ep007")).sync())
    assert recurring_guests(state) == {"Hannah Okafor": ["ep004", "ep007"]}
    segments = recurring_segments(state)
    assert "Predictions" in segments and "Introduction" not in segments
    assert "Mark Rivera" in state.digests["ep004"].mentions  # "Mark Rivera told us ..." found by code


def test_add_appends_to_manifest_and_updates(tmp_path, spec, fake_settings):
    up = updater(tmp_path, spec, fake_settings, ("ep001",))
    state = asyncio.run(up.add(EP["ep004"]))
    assert state.episodes == ["ep001", "ep004"]
    assert EP["ep004"] in (tmp_path / "series.yaml").read_text(encoding="utf-8")
    assert load_state(fake_settings.out_dir, "test_series").version == 2


def test_rebuild_replays_from_stored_digests(tmp_path, spec, fake_settings):
    asyncio.run(updater(tmp_path, spec, fake_settings).sync())
    state = asyncio.run(updater(tmp_path, spec, fake_settings).rebuild())
    assert state.episodes == ["ep001", "ep004"] and state.version == 4
    assert [h.mode for h in state.history] == ["initial", "incremental", "rebuild", "rebuild"]


@pytest.mark.parametrize("path", ["harness/evolved.yaml", "harness/evolved_r2.yaml", "harness/base_12b.yaml"])
def test_older_harness_files_get_the_base_series_section(path):
    spec = harness.load(path)
    assert series_spec(spec).series.max_chars == 4000


def test_check_finds_revisions_and_conflicts():
    from podcast_agent.series import EpisodeDigest, check

    def digest(ep, guests):
        return EpisodeDigest(episode_id=ep, title=ep, host="Sarah", guests=guests, source="", sections=[],
                             arc_role="", points=[])

    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep006"], summary="Mark Rivera now backs hybrid.",
                        char_count=29, digests={"ep001": digest("ep001", ["Mark Rivera"]),
                                                "ep006": digest("ep006", ["Mark Rivera"])})
    apply_ops(state, "ep001", [{"op": "add", "target": "new", "text": "Remote-first companies will dominate."}], {}, 40)
    gold = {"episodes": ["ep001", "ep006"], "max_chars": 4000, "summary_mentions": ["Mark Rivera"],
            "recurring_guests": ["Mark Rivera"],
            "revisions": [{"name": "hybrid", "episode": "ep006", "old": "remote.first", "new": "hybrid"}],
            "single_current": ["remote.first companies will dominate"]}
    assert not dict((n, ok) for n, ok, _ in check(state, gold))["revised: hybrid"]
    apply_ops(state, "ep006", [{"op": "revise", "target": "L01", "text": "Intentional hybrid wins.",
                                "old_still_current": False}], {}, 40)
    assert all(ok for _, ok, _ in check(state, gold))


def test_two_items_revised_into_one_statement_are_merged():
    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep006"])
    apply_ops(state, "ep001", [{"op": "add", "target": "new", "text": t} for t in ("Remote-first wins.", "Offices fade.")],
              {}, 40)
    same = "Mark now backs intentional hybrid."
    applied = apply_ops(state, "ep006", [{"op": "revise", "target": "L01", "text": same},
                                         {"op": "revise", "target": "L02", "text": same}], {}, 40)
    assert [i.id for i in state.current()] == ["L03"]
    assert state.item("L02").superseded_by == "L03" and "merged" in applied[1].reason


def test_related_items_include_the_same_speakers_earlier_item():
    from podcast_agent.series import related_items

    items = [LedgerItem(id="L06", kind="prediction", text="Mark Rivera predicts remote-first companies will dominate.",
                        episodes=["ep001"], added_in="ep001", updated_in="ep001"),
             LedgerItem(id="L17", kind="prediction", text="Hannah Okafor predicts async becomes standard; hybrid sync time.",
                        episodes=["ep004"], added_in="ep004", updated_in="ep004")]
    point = DigestPoint(id="ep006.P7", kind="prediction", refers_to=["ep001"],
                        text="Mark Rivera now thinks the winner is intentional hybrid.")
    assert related_items(items, [point], ["Mark Rivera", "Hannah Okafor"])["ep006.P7"][0] == "L06"


def test_split_cited_strips_citations_and_keeps_ids():
    from podcast_agent.series import split_cited

    text = "Mark first predicted remote-first dominance [L07]. He now backs hybrid [L30, L07]. Uncited closing line."
    assert split_cited(text) == [("Mark first predicted remote-first dominance.", ("L07",)),
                                 ("He now backs hybrid.", ("L30", "L07")), ("Uncited closing line.", ())]
    assert split_cited("Ends with a citation. [L02] Next sentence.") == [("Ends with a citation.", ("L02",)),
                                                                          ("Next sentence.", ())]


def test_trimming_drops_unimportant_sentences_before_revisions():
    sentences_ = [(f"Filler sentence number {i} with enough words to matter.", ()) for i in range(12)]
    revision = ("Mark Rivera revised his prediction to intentional hybrid.", ("L30",))
    prose = {"themes": [("Themes.", ())], "evolution": [*sentences_[:6], revision, *sentences_[6:]],
             "connections": [("Connections.", ())]}
    arc = [("ep001", "Title", "Role.")]
    weight = lambda ids: 100 if "L30" in ids else 0  # noqa: E731
    text, actions = enforce_length(prose, arc, 400, weight)
    assert len(text) <= 400 and actions
    assert revision[0] in text  # positional trimming would have cut it; importance-aware trimming keeps it
    plain, _ = enforce_length(prose, arc, 400)
    assert len(plain) <= 400


def test_attribution_issues_flag_wrong_episode_names():
    from podcast_agent.series import attribution_issues

    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep004"])
    apply_ops(state, "ep004", [{"op": "add", "target": "new", "text": "Owners and review dates fight doc rot."}], {}, 40)
    state.summary_sources = {"evolution": [["In ep001 Mark said documentation rot needs owners.", ["L01"]],
                                           ["In ep004 Hannah assigned owners.", ["L01"]], ["No citation in ep007.", []]]}
    issues = attribution_issues(state)
    assert len(issues) == 1 and "ep001" in issues[0]


def test_changes_earlier_ranks_the_named_view_first_and_backs_a_revise():
    from podcast_agent.series import related_items

    def item(i, text):
        return LedgerItem(id=i, kind="prediction", text=text, episodes=["ep001"], added_in="ep001", updated_in="ep001")

    items = [item("L06", "Mark Rivera predicts async culture will become standard practice."),
             item("L07", "Mark Rivera predicts remote-first companies will dominate talent acquisition.")]
    point = DigestPoint(id="ep006.P3", kind="prediction", refers_to=["ep001"],
                        text="Mark Rivera revised his prediction: the winner is intentional hybrid, async by default.",
                        changes="he predicted remote-first companies would dominate talent acquisition")
    assert related_items(items, [point], ["Mark Rivera"])["ep006.P3"][0] == "L07"

    # The model picks the wrong id (L06) and says the old item is still current: code resolves the target from the
    # quoted earlier view, and that second signal backs the revise over the self-check.
    from podcast_agent.series import resolve_revise_targets

    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep006"], ledger=items)
    op = {"point": "ep006.P3", "op": "revise", "target": "L06", "text": "Mark now backs hybrid.", "old_still_current": True}
    notes = resolve_revise_targets([op], {"ep006.P3": point}, items)
    assert op["target"] == "L07" and op["backed"] and "L06 -> L07" in notes[0]
    applied = apply_ops(state, "ep006", [op], {"ep006.P3": point}, 40)
    assert applied[0].op == "revise" and applied[0].replaced == "L07" and state.item("L06").status == "current"
    # Without a matching quoted view, the self-check still turns the revise into an add.
    plain = point.model_copy(update={"changes": "ep004"})
    op2 = {"point": "ep006.P3", "op": "revise", "target": "L06", "text": "x", "old_still_current": True}
    state2 = SeriesState(series_id="s", title="S", episodes=["ep001", "ep006"], ledger=[item("L06", "Async standard.")])
    resolve_revise_targets([op2], {"ep006.P3": plain}, state2.ledger)
    assert op2["target"] == "L06" and apply_ops(state2, "ep006", [op2], {"ep006.P3": plain}, 40)[0].op == "add"


def test_clean_changes_drops_self_quotes_and_fragments():
    from podcast_agent.series import _named, clean_changes

    point = "Dana Whitfield predicted that five-day mandates will fade and structured hybrid will stick."
    assert clean_changes("Five-day mandates will fade. Structured hybrid will stick.", point) == ""
    assert clean_changes("ep004", point) == ""
    earlier = "Mark Rivera predicted in ep001 that remote-first companies would dominate talent acquisition."
    assert clean_changes(earlier, "Mark Rivera now backs intentional hybrid.") == earlier
    assert _named("Mark Rivera", "Rivera conceded the junior point.") and not _named("Tom Becker", "Tomorrow, maybe.")


def test_episode_brackets_are_stripped_and_uncited_revisions_attached():
    from podcast_agent.series import attach_by_overlap, split_cited

    assert split_cited("Mark returns in ep006 [ep001, ep006]. Hannah too [L03, ep004].") == [
        ("Mark returns in ep006.", ()), ("Hannah too.", ("L03",))]
    item = LedgerItem(id="L30", kind="prediction", text="Mark Rivera revised his prediction: intentional hybrid wins.",
                      episodes=["ep001", "ep006"], added_in="ep006", updated_in="ep006")
    cited = [("Themes first.", ()), ("Mark Rivera revised his prediction to intentional hybrid.", ())]
    assert attach_by_overlap(cited, [item])[1][1] == ("L30",)
    assert attach_by_overlap([("Unrelated sentence about meetings.", ())], [item])[0][1] == ()


def test_a_figure_cannot_replace_a_position():
    from podcast_agent.series import kind_mismatch

    assert kind_mismatch("figure", "position") and kind_mismatch("prediction", "theme")
    assert not kind_mismatch("position", "theme") and not kind_mismatch("figure", "figure")
    assert not kind_mismatch("position", "prediction")  # both are views; extraction mixes them up
    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep004"])
    apply_ops(state, "ep001", [{"op": "add", "target": "new", "text": "Measure outcomes, not hours."}],
              {"P": DigestPoint(id="P", kind="position", text="x")}, 40)
    state.ledger[0].kind = "position"
    fig = DigestPoint(id="ep004.P3", kind="figure", text="New hires ramp in three weeks instead of six.")
    applied = apply_ops(state, "ep004", [{"point": "ep004.P3", "op": "revise", "target": "L01", "text": fig.text}],
                        {"ep004.P3": fig}, 40)
    assert applied[0].op == "add" and state.item("L01").status == "current"


def test_competing_revises_keep_the_best_supported_and_measures_are_figures():
    from podcast_agent.series import MEASURE, resolve_revise_targets

    items = [LedgerItem(id="L07", kind="prediction", episodes=["ep001"], added_in="ep001", updated_in="ep001",
                        text="Mark Rivera predicted that remote-first companies will dominate talent acquisition.")]
    weak = DigestPoint(id="ep006.P2", kind="position", text="Dana Whitfield says juniors learn slower remotely.")
    real = DigestPoint(id="ep006.P7", kind="prediction", text="Mark Rivera now predicts intentional hybrid wins.",
                       changes="Mark Rivera predicted in ep001 that remote-first companies would dominate talent")
    ops = [{"point": "ep006.P2", "op": "revise", "target": "L07", "text": weak.text},
           {"point": "ep006.P7", "op": "revise", "target": "L07", "text": real.text}]
    resolve_revise_targets(ops, {p.id: p for p in (weak, real)}, items)
    assert [o["op"] for o in ops] == ["add", "revise"]
    assert MEASURE.search("onboarding dropped from three weeks to about two") and MEASURE.search("cut by 40 percent")
    assert not MEASURE.search("Hannah Okafor values clear writing over polish")


def test_lineage_includes_merged_revisions_and_wrong_kind_targets_are_redirected():
    from podcast_agent.series import lineage, resolve_revise_targets

    state = SeriesState(series_id="s", title="S", episodes=["ep001", "ep006"])
    apply_ops(state, "ep001", [{"op": "add", "target": "new", "text": t} for t in ("A wins.", "B wins.")], {}, 40)
    same = "Hybrid wins."
    apply_ops(state, "ep006", [{"op": "revise", "target": "L01", "text": same},
                               {"op": "revise", "target": "L02", "text": same}], {}, 40)
    assert {i.id for i in lineage(state, state.item("L03"))} == {"L01", "L02"}

    items = [LedgerItem(id="L09", kind="theme", text="Remote work culture.", episodes=["ep001"], added_in="ep001",
                        updated_in="ep001"),
             LedgerItem(id="L07", kind="prediction", episodes=["ep001"], added_in="ep001", updated_in="ep001",
                        text="Mark Rivera predicted remote-first companies will dominate talent acquisition.")]
    point = DigestPoint(id="ep006.P7", kind="prediction", text="Mark Rivera revised his prediction to hybrid.",
                        changes="Mark Rivera predicted remote-first companies would dominate talent acquisition")
    op = {"point": "ep006.P7", "op": "revise", "target": "L09", "text": point.text}
    resolve_revise_targets([op], {point.id: point}, items)
    assert op["target"] == "L07"


def test_figure_revise_follows_the_restated_value_and_predictions_stay_with_their_owner():
    from podcast_agent.series import measures, resolve_revise_targets

    def item(i, kind, text):
        return LedgerItem(id=i, kind=kind, text=text, episodes=["ep004"], added_in="ep004", updated_in="ep004")

    items = [item("L11", "figure", "Hannah Okafor said decisions took about a day longer at first."),
             item("L12", "figure", "Hannah Okafor said new hires ramped in three weeks instead of six weeks."),
             item("L27", "prediction", "Mark Rivera predicts intentional hybrid will win.")]
    assert measures("from three weeks to about two weeks") == {"three week", "two week"}
    fig = DigestPoint(id="ep007.P2", kind="figure", text="Hannah Okafor: onboarding fell from three weeks to about two.")
    op = {"point": fig.id, "op": "revise", "target": "L11", "text": fig.text}
    resolve_revise_targets([op], {fig.id: fig}, items, ["Hannah Okafor", "Mark Rivera", "Lucas Ferreira"])
    assert op["target"] == "L12"
    pred = DigestPoint(id="ep007.P8", kind="prediction", text="Lucas Ferreira predicts documented teams pull ahead.")
    op2 = {"point": pred.id, "op": "revise", "target": "L27", "text": pred.text}
    resolve_revise_targets([op2], {pred.id: pred}, items, ["Hannah Okafor", "Mark Rivera", "Lucas Ferreira"])
    assert op2["op"] == "add"


def test_from_x_to_y_promotes_an_add_to_a_revise_of_the_pure_figure():
    from podcast_agent.series import resolve_revise_targets

    def fig(i, text):
        return LedgerItem(id=i, kind="figure", text=text, episodes=["ep004"], added_in="ep004", updated_in="ep004")

    items = [fig("L11", "Meetings fell 40 percent and new hires ramped in three weeks."),
             fig("L12", "New hires ramped in three weeks instead of six weeks.")]
    point = DigestPoint(id="ep007.P3", kind="figure", text="Hannah Okafor: onboarding dropped from three weeks to two.")
    op = {"point": point.id, "op": "add", "target": "new", "text": point.text, "old_still_current": True}
    resolve_revise_targets([op], {point.id: point}, items)
    assert op["op"] == "revise" and op["target"] == "L12" and op["backed"]  # L12: no other unit (L11 is compound)
    state = SeriesState(series_id="s", title="S", episodes=["ep004", "ep007"], ledger=items)
    assert apply_ops(state, "ep007", [op], {point.id: point}, 40)[0].op == "revise"
    assert state.item("L11").status == "current"


def test_split_compound_figures():
    from podcast_agent.series import split_compound

    text = ("Hannah Okafor reported that by the second quarter, recurring meetings were cut by roughly 40 percent, "
            "and new hires were ramping in three weeks instead of six.")
    assert split_compound(text) == [
        "Hannah Okafor reported that by the second quarter, recurring meetings were cut by roughly 40 percent.",
        "Hannah Okafor reported that new hires were ramping in three weeks instead of six."]
    single = "New hires were ramping in three weeks instead of six weeks."
    assert split_compound(single) == [single]
