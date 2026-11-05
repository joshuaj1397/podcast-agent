"""Series summary: summarize a podcast series as a whole and update it as episodes are added.

For each new episode (SeriesUpdater.sync / add):

  per-episode pipeline (unchanged: out/<ep>/report.*)
  -> extract: the episode's digest (themes, positions, predictions, figures, references to earlier episodes)
  -> merge:   ledger operations (add / revise / reinforce), each with a reason; code applies them, so a revised
              item is marked superseded instead of sitting next to its replacement
  -> write:   the summary, from the current ledger and the episode list
  -> enforce_length: code guarantees len(summary) <= series.max_chars
  -> atomic save of out/series/<id>/series.json (+ versions/vN.json, series.md, traces/vN-<ep>.jsonl)

The state file is the only memory between runs, so every command is a separate, resumable process.
"""

from __future__ import annotations

import difflib
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, PrivateAttr
from rank_bm25 import BM25Okapi

from . import harness, ingest, llm
from .config import Settings
from .harness import HarnessSpec
from .models import Episode, Report
from .pipeline import Pipeline
from .retrieval import tokenize
from .trace import Trace, console, open_trace

BASE_HARNESS = Path("harness/base.yaml")
PROSE = [("themes", "Overarching themes"), ("evolution", "How the ideas developed"),
         ("connections", "Recurring guests, segments and cross-references")]
ARC_HEADING = "Episode arc"
SHARE = {"themes": 0.20, "evolution": 0.33, "connections": 0.20, "arc": 0.27}  # of target_chars
KIND_WEIGHT = {"theme": 3, "prediction": 2, "position": 2, "figure": 2, "reference": 1}
FRAME_SECTIONS = {"introduction", "intro", "closing", "outro"}

# ---------------------------------------------------------------- manifest


class Manifest(BaseModel):
    """series/<id>.yaml: which transcripts make up the series, in air order. Membership is configuration."""

    series_id: str
    title: str
    episodes: list[str]
    _path: Path | None = PrivateAttr(None)

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        m = cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        m._path = Path(path)
        return m

    def append(self, transcript: str) -> None:
        """Add a line under `episodes:` (the last key in the file), keeping the file's comments."""
        text = self._path.read_text(encoding="utf-8")
        self._path.write_text(text + ("" if text.endswith("\n") else "\n") + f"  - {transcript}\n", encoding="utf-8")
        self.episodes.append(transcript)


# ---------------------------------------------------------------- persisted state


class DigestPoint(BaseModel):
    id: str  # ep004.P2
    kind: str
    text: str
    refers_to: list[str] = []
    turn: str = ""  # where it is said: "T11 04:10"
    changes: str = ""  # the earlier view or figure this point changes, quoted from the transcript ("" if none)


class EpisodeDigest(BaseModel):
    """What the series needs from one episode. Stored, so a rebuild never re-runs the episode pipeline."""

    episode_id: str
    title: str
    host: str
    guests: list[str]
    source: str
    sections: list[str]
    arc_role: str
    points: list[DigestPoint]
    mentions: dict[str, list[str]] = {}  # earlier guest -> turn ids where they are named (found by code)
    fact_checks: dict[str, int] = {}
    summary: str = ""


class LedgerItem(BaseModel):
    id: str  # L01, L02, ...
    kind: str
    text: str
    episodes: list[str]
    status: Literal["current", "superseded", "archived"] = "current"
    added_in: str
    updated_in: str
    refers_to: list[str] = []  # earlier episodes this item responds to (from the extract step)
    turns: list[str] = []  # transcript sources: "ep004 T07 02:15"
    revises: str | None = None  # the item this one replaced
    superseded_by: str | None = None


class AppliedOp(BaseModel):
    op: Literal["add", "revise", "reinforce", "archive"]
    item: str
    replaced: str | None = None
    point: str | None = None
    text: str
    reason: str


class UpdateRecord(BaseModel):
    version: int
    episode_id: str
    at: str
    mode: Literal["initial", "incremental", "rebuild"]
    change: str
    chars: int
    chars_before: int
    similarity: float | None  # difflib ratio to the previous summary: 1.0 unchanged, low = heavy rewrite
    ops: list[AppliedOp]
    reasoning: dict[str, str]
    length_actions: list[str]
    degraded: list[str]
    llm_calls: int
    cache_hits: int
    seconds: float
    model: str
    harness: str


class SeriesState(BaseModel):
    series_id: str
    title: str
    version: int = 0
    summary: str = ""
    char_count: int = 0
    max_chars: int = 4000
    summary_sources: dict[str, list[list]] = {}  # section -> [sentence, [ledger ids it cites]] (provenance)
    episodes: list[str] = []
    updated_at: str = ""
    history: list[UpdateRecord] = []
    ledger: list[LedgerItem] = []
    digests: dict[str, EpisodeDigest] = {}

    def current(self) -> list[LedgerItem]:
        return [i for i in self.ledger if i.status == "current"]

    def item(self, item_id: str) -> LedgerItem | None:
        return next((i for i in self.ledger if i.id == item_id), None)


class SeriesStore:
    """out/series/<id>/: series.json (the state and the output), series.md, versions/vN.json, traces/."""

    def __init__(self, out_dir: Path, series_id: str):
        self.dir = Path(out_dir) / "series" / series_id
        self.path = self.dir / "series.json"

    def load(self, manifest: Manifest, max_chars: int) -> SeriesState:
        if self.path.exists():
            return SeriesState.model_validate_json(self.path.read_text(encoding="utf-8"))
        return SeriesState(series_id=manifest.series_id, title=manifest.title, max_chars=max_chars)

    def save(self, state: SeriesState) -> None:
        """Write a temp file, then rename: a crash mid-update leaves the previous version intact."""
        data = state.model_dump_json(indent=2)
        (self.dir / "versions").mkdir(parents=True, exist_ok=True)
        (self.dir / "versions" / f"v{state.version:02d}.json").write_text(data, encoding="utf-8")
        for name, text in (("series.json", data), ("series.md", markdown(state))):
            tmp = self.dir / f".{name}.tmp"
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.dir / name)

    def trace_path(self, version: int, episode_id: str) -> Path:
        return self.dir / "traces" / f"v{version:02d}-{episode_id}.jsonl"


# ---------------------------------------------------------------- deterministic facts (no LLM)


def recurring_guests(state: SeriesState) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for ep in state.episodes:
        for g in state.digests[ep].guests:
            seen.setdefault(g, []).append(ep)
    return {g: eps for g, eps in seen.items() if len(eps) > 1}


def recurring_segments(state: SeriesState) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for ep in state.episodes:
        for s in state.digests[ep].sections:
            if s.lower() not in FRAME_SECTIONS:
                seen.setdefault(s, []).append(ep)
    return {s: eps for s, eps in seen.items() if len(eps) > 1}


def guest_mentions(ep: Episode, state: SeriesState) -> dict[str, list[str]]:
    """Earlier guests named in this episode (full or first name), excluding their own turns."""
    found: dict[str, list[str]] = {}
    for g in dict.fromkeys(g for e in state.episodes for g in state.digests[e].guests):
        pattern = re.compile(rf"\b({re.escape(g)}|{re.escape(g.split()[0])})\b")
        ids = [t.id for t in ep.turns if t.speaker != g and pattern.search(t.text)]
        if ids:
            found[g] = ids
    return found


def overlap_scores(items: list[LedgerItem], query: str) -> list[tuple[str, float]]:
    """Share of the query's words found in each item, best first (robust for small ledgers, unlike BM25's IDF)."""
    q = set(tokenize(query))
    scores = [(i.id, len(q & set(tokenize(i.text))) / (len(q) or 1)) for i in items]
    return sorted(scores, key=lambda x: -x[1])


STRICT_KINDS = {"figure", "prediction"}
_NUM = r"(\d+(\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty|thirty|forty|fifty|half|a third)"
MEASURE = re.compile(rf"\b{_NUM}\s*(%|percent|weeks?|days?|months?|years?|hours?|minutes?|times|x)\b", re.I)
# "from three weeks to ...": the old value of the measure the point updates
FROM_VALUE = re.compile(rf"\bfrom\s+(?:about\s+|roughly\s+|around\s+)?{_NUM}\s*(%|percent|weeks?|days?|months?|years?|"
                        rf"hours?|minutes?)\s+to\b", re.I)


def kind_mismatch(point_kind: str, item_kind: str) -> bool:
    """A figure only replaces a figure. A prediction and a position are both views (extraction mixes them up), so
    they may revise each other; a prediction never replaces or is replaced by a theme or a reference."""
    if point_kind == item_kind:
        return False
    if "figure" in (point_kind, item_kind):
        return True
    return "prediction" in (point_kind, item_kind) and not {point_kind, item_kind} <= {"prediction", "position"}


def split_compound(text: str) -> list[str]:
    """Split a figure sentence that combines results in different units at its clause boundaries ("..., and ...").
    Each part must hold exactly one unit; a part without a subject gets the sentence's lead-in ("Hannah Okafor
    reported that"). Returns [text] when it cannot be split cleanly."""
    def units(s: str) -> set[str]:
        return {m.split()[-1] for m in measures(s)}

    if len(units(text)) < 2:
        return [text]
    parts = [p.strip(" ,;.") for p in re.split(r",?\s+(?:and|while|but|whereas)\s+|;\s*", text.strip()) if p.strip(" ,;.")]
    if len(parts) < 2 or any(len(units(p)) > 1 for p in parts):
        return [text]
    lead = re.match(r"^(.*?\b(?:said|says|noted|reported|stated|explained|added|mentioned|found) that)\s", parts[0])
    prefix = lead.group(1) if lead else ""
    out: list[str] = []
    pending = ""  # leading clauses without a value join the next figure
    for p in parts:
        if not units(p):
            if out:
                out[-1] = f"{out[-1]}, and {p}"
            else:
                pending = f"{pending}, {p}" if pending else p
            continue
        out.append(f"{pending}, {p}" if pending else p)
        pending = ""
    if len(out) < 2:
        return [text]
    fixed = []
    for o in out:
        if prefix and not o.startswith(prefix):
            o = f"{prefix} {o[0].lower()}{o[1:]}"
        fixed.append(o[0].upper() + o[1:].rstrip(".") + ".")
    return fixed


def _named(person: str, text: str) -> bool:
    """The person's full name, or their first or last name, appears in the text."""
    return any(re.search(rf"\b{re.escape(part)}\b", text) for part in [person, *person.split()] if len(part) > 2)


def clean_changes(changes: str, point_text: str) -> str:
    """Keep the quoted earlier view only if it is one: at least 4 words and not mostly a repeat of the point itself."""
    changes = changes.strip()
    words = set(tokenize(changes))
    if len(words) < 4 or changes.lower() in ("none", "n/a"):
        return ""
    if len(words & set(tokenize(point_text))) / len(words) >= 0.6:
        return ""  # the model quoted the new point, not the earlier view
    return changes


def measures(text: str) -> set[str]:
    """Measured values in a text, normalized: 'three weeks', '40 percent'."""
    return {re.sub(r"s$", "", " ".join(m.group(0).lower().split())) for m in MEASURE.finditer(text)}


def resolve_revise_targets(ops: list[dict], points: dict[str, DigestPoint], items: list[LedgerItem],
                           people: list[str] = ()) -> list[str]:
    """The model decides that a point revises something; code resolves which item, using the earlier view the
    extract step quoted (`changes`). A clear match (>= 0.5 word overlap) beats a target that clearly does not match
    (< 0.3); such a revise is also `backed`, so the old_still_current self-check cannot veto it.
    Also: a newer figure restates the old value, so a figure revise goes to the figure that shares a measured value;
    and a prediction is revised only by the person who made it. Mutates ops; returns log lines."""
    notes = []
    by_id = {i.id: i for i in items}
    for o in ops:  # figures: a newer value restates the old one ("dropped from three weeks to about two")
        point, target = points.get(o.get("point", "")), by_id.get(o.get("target"))
        if not point or point.kind != "figure" or o.get("op") not in ("add", "revise") or not measures(point.text):
            continue
        frm = FROM_VALUE.search(point.text)
        old_value = measures(frm.group(0)) if frm else set()
        if o["op"] == "add" and not old_value:
            continue  # only "from X to Y" turns an add into a revise
        wanted = old_value or measures(point.text)
        sharing = [i for i in items if i.kind == "figure" and wanted & measures(i.text)]
        if not sharing:
            continue
        # Prefer the item that shares the value and measures nothing else (not a compound of several facts).
        words = dict(overlap_scores(sharing, point.text))
        units = {m.split()[-1] for m in wanted}
        other_units = lambda i: len({m.split()[-1] for m in measures(i.text)} - units)  # noqa: E731
        best = max(sharing, key=lambda i: (len(wanted & measures(i.text)), -other_units(i), words[i.id])).id
        if o["op"] == "add":
            notes.append(f"{point.id}: changes a value already in the ledger ({', '.join(sorted(wanted))}); add -> "
                         f"revise {best}")
            o["reason"] = (o.get("reason") or "") + f" (code: the point updates {best}'s value; revised)"
            o["op"], o["target"] = "revise", best
        elif o.get("target") != best and not (target and target.kind == "figure" and wanted & measures(target.text)
                                               and other_units(target) == 0):
            notes.append(f"{point.id}: revise target {o.get('target')} -> {best} (shares "
                         f"{', '.join(sorted(wanted & measures(by_id[best].text)))})")
            o["reason"] = (o.get("reason") or "") + f" (target corrected by code from {o.get('target')} to {best})"
            o["target"] = best
        o["backed"] = True  # the shared measured value is objective evidence: the self-check cannot veto it
    for o in ops:  # a revise whose target has the wrong kind: redirect to the best matching item of the right kind
        point = points.get(o.get("point", ""))
        target = by_id.get(o.get("target"))
        if o.get("op") == "revise" and point and target and kind_mismatch(point.kind, target.kind):
            same = [i for i in items if i.kind == point.kind]
            scores = overlap_scores(same, point.changes or point.text) if same else []
            if scores and scores[0][1] >= 0.3:
                notes.append(f"{point.id}: revise target {target.id} is a {target.kind}; redirected to the "
                             f"{point.kind} {scores[0][0]}")
                o["reason"] = (o.get("reason") or "") + f" (target corrected by code from {target.id} to {scores[0][0]})"
                o["target"] = scores[0][0]
    for o in ops:
        point = points.get(o.get("point", ""))
        if o.get("op") != "revise" or not point or not point.changes:
            continue
        scores = overlap_scores(items, point.changes)
        if not scores or scores[0][1] < 0.5:
            continue  # the earlier view matches nothing in the ledger clearly: no second signal
        best, best_score = scores[0]
        target_score = dict(scores).get(o.get("target"), 0.0)
        if o.get("target") != best and target_score < 0.3:
            notes.append(f"{point.id}: revise target {o.get('target')} -> {best} (the earlier view "
                         f"'{point.changes[:60]}' matches {best}, {best_score:.0%} of its words)")
            o["reason"] = (o.get("reason") or "") + f" (target corrected by code from {o.get('target')} to {best})"
            o["target"], target_score = best, best_score
        o["backed"] = bool(o.get("backed")) or target_score >= 0.5
    for o in ops:  # a prediction belongs to the person who made it: someone else's prediction is a second view
        point, target = points.get(o.get("point", "")), by_id.get(o.get("target"))
        if o.get("op") == "revise" and point and target and target.kind == "prediction":
            speakers = [n for n in people if _named(n, point.text)]
            if speakers and not any(_named(n, target.text) for n in speakers):
                o["op"] = "add"
                o["reason"] = (o.get("reason") or "") + (f" ({target.id} is someone else's prediction; "
                                                         f"added as a second view)")
                notes.append(f"{point.id}: {', '.join(speakers)} cannot revise another person's prediction "
                             f"{target.id}; added")
    # Several points revising one item: keep the best-supported revise (backed by the quoted earlier view, same kind,
    # most shared words); the others become adds, so a weaker point cannot pre-empt the real revision.
    groups: dict[str, list[dict]] = {}
    for o in ops:
        if o.get("op") == "revise" and o.get("target") in by_id:
            groups.setdefault(o["target"], []).append(o)
    for target, group in groups.items():
        if len(group) < 2:
            continue
        item = by_id[target]

        def support(o: dict) -> tuple:
            p = points.get(o.get("point", ""))
            words = set(tokenize(item.text))
            shared = len(words & set(tokenize(p.text if p else o.get("text", "")))) / (len(words) or 1)
            return (bool(o.get("backed")), bool(p and p.kind == item.kind), shared)

        keep = max(group, key=support)
        keep_words = set(tokenize(keep.get("text", "")))
        for o in group:
            if o is not keep:
                words = set(tokenize(o.get("text", "")))
                if len(words & keep_words) / (len(words) or 1) >= 0.6:  # the same update said twice: no second copy
                    o["op"] = "reinforce"
                    o["reason"] = (o.get("reason") or "") + f" (same update as {keep.get('point')}; reinforces it)"
                    notes.append(f"{o.get('point')}: duplicates {keep.get('point')}'s revise of {target}; reinforces it")
                    continue
                o["op"] = "add"
                o["reason"] = (o.get("reason") or "") + f" ({target} is revised by {keep.get('point')}; added as new)"
                notes.append(f"{o.get('point')}: also revised {target}; kept {keep.get('point')}'s revise, this one added")
    return notes


def related_items(items: list[LedgerItem], points: list[DigestPoint], people: list[str] = (),
                  k: int = 3) -> dict[str, list[str]]:
    """Candidate items each new point may revise or reinforce (a hint for the merge agent), best first:
    1) BM25 matches for the earlier view the point says it changes (`changes`, quoted from the transcript),
    2) the same person's items of the same kind from the episodes the point refers to (a speaker revising their own
       earlier view rarely reuses its words), 3) BM25 matches for the point itself."""
    if not items:
        return {}
    bm25 = BM25Okapi([tokenize(i.text) or ["_"] for i in items])

    def top(query: str, n: int) -> list[str]:
        scores = bm25.get_scores(tokenize(query) or ["_"])
        return [items[j].id for j in sorted(range(len(items)), key=lambda j: -scores[j])[:n] if scores[j] > 0]

    out = {}
    for p in points:
        speakers = [n for n in people if n in p.text]
        same = {i.id for i in items if i.kind == p.kind and p.refers_to and set(i.episodes) & set(p.refers_to)
                and any(n in i.text for n in speakers)}
        changed = [i for i, score in overlap_scores(items, p.changes)[:2] if score >= 0.3] if p.changes else []
        out[p.id] = list(dict.fromkeys([*changed, *sorted(same), *top(p.text, k)]))
    return out


def lineage(state: SeriesState, item: LedgerItem) -> list[LedgerItem]:
    """The superseded items this one replaced (directly or through earlier revisions), oldest first. Follows both
    `revises` and the reverse `superseded_by` links, so items merged into one revision are all included."""
    found: list[LedgerItem] = []
    todo, seen = [item], {item.id}
    while todo:
        cur = todo.pop()
        olds = [i for i in state.ledger if i.superseded_by == cur.id]
        if cur.revises and (old := state.item(cur.revises)):
            olds.append(old)
        for old in olds:
            if old.id not in seen:
                seen.add(old.id)
                found.append(old)
                todo.append(old)
    order = {i.id: n for n, i in enumerate(state.ledger)}
    return sorted(found, key=lambda i: order[i.id])


def priority(item: LedgerItem, state: SeriesState) -> tuple:
    """Importance used to archive ledger items and to order the deterministic fallback: support, kind, recency."""
    recency = state.episodes.index(item.updated_in) if item.updated_in in state.episodes else len(state.episodes)
    return (len(item.episodes), KIND_WEIGHT.get(item.kind, 1), recency)


# ---------------------------------------------------------------- ledger operations


def apply_ops(state: SeriesState, episode_id: str, ops: list[dict], points: dict[str, DigestPoint],
              max_items: int) -> list[AppliedOp]:
    """Apply merge operations to the ledger. Code, not the model, decides what 'revise' means:
    the old item becomes superseded (kept for history, hidden from the writer) and the new one replaces it."""
    applied: list[AppliedOp] = []
    revised: dict[str, str] = {}  # item revised in this update -> its replacement
    for o in ops:
        point = points.get(o.get("point", ""))
        text = (o.get("text") or "").strip() or (point.text if point else "")
        reason = (o.get("reason") or "").strip()
        tid = o.get("target", "new")
        # Self-check: a revise of an item the model itself judged still current becomes an add, unless the extract
        # step independently found that this point changes an earlier view (two signals agree: trust the revise).
        if o["op"] == "revise" and o.get("old_still_current") and not o.get("backed"):
            reason += f" (the model judged {tid} still current, so both are kept)"
            o = {**o, "op": "add"}
        if tid in revised:
            if o["op"] == "revise":  # two points cannot both replace one statement: keep the second as new
                reason += f" ({tid} was already revised by {revised[tid]} in this update; added as new)"
                o = {**o, "op": "add"}
            tid = revised[tid]
        target = state.item(tid)
        target = target if target and target.status == "current" else None
        if not text and not (o["op"] == "reinforce" and target):
            continue  # nothing to add (reinforce needs only its target)
        kind = point.kind if point else (target.kind if target else "position")
        pid = point.id if point else None
        if o["op"] == "revise" and target and point and kind_mismatch(point.kind, target.kind):
            reason += f" (a {point.kind} cannot replace a {target.kind}; added as new)"
            o = {**o, "op": "add"}
        if o["op"] == "add" or target is None:
            if o["op"] != "add":
                reason += f" (target {tid} unknown; added as new)"
            item = _new_item(state, kind, text, [episode_id], episode_id, point.refers_to if point else [],
                             [f"{episode_id} {point.turn}"] if point and point.turn else [])
            applied.append(AppliedOp(op="add", item=item.id, point=pid, text=text, reason=reason))
        elif o["op"] == "revise" and (same := next((state.item(a.item) for a in applied if a.op in ("add", "revise")
                                                    and a.text.strip().lower() == text.lower()), None)):
            # Two old items revised into the same statement: both point to one new item (no duplicates).
            target.status, target.superseded_by = "superseded", same.id
            same.episodes = list(dict.fromkeys([*target.episodes, *same.episodes]))
            revised[target.id] = same.id
            applied.append(AppliedOp(op="revise", item=same.id, replaced=target.id, point=pid, text=text,
                                     reason=reason + f" (same statement as {same.id}; merged)"))
        elif o["op"] == "revise":
            item = _new_item(state, kind, text, list(dict.fromkeys([*target.episodes, episode_id])), episode_id,
                             point.refers_to if point else [],
                             [*target.turns, *([f"{episode_id} {point.turn}"] if point and point.turn else [])])
            item.revises, target.status, target.superseded_by = target.id, "superseded", item.id
            revised[target.id] = item.id
            applied.append(AppliedOp(op="revise", item=item.id, replaced=target.id, point=pid, text=text, reason=reason))
        else:  # reinforce
            if episode_id not in target.episodes:
                target.episodes.append(episode_id)
            if point and point.turn:
                target.turns.append(f"{episode_id} {point.turn}")
            target.updated_in = episode_id
            applied.append(AppliedOp(op="reinforce", item=target.id, point=pid, text=target.text, reason=reason))
    current = state.current()
    if len(current) > max_items:
        for item in sorted(current, key=lambda i: priority(i, state))[: len(current) - max_items]:
            item.status = "archived"
            applied.append(AppliedOp(op="archive", item=item.id, text=item.text,
                                     reason=f"ledger over {max_items} items; least supported and oldest"))
    return applied


def _new_item(state: SeriesState, kind: str, text: str, episodes: list[str], episode_id: str,
              refers_to: list[str] = (), turns: list[str] = ()) -> LedgerItem:
    item = LedgerItem(id=f"L{len(state.ledger) + 1:02d}", kind=kind, text=text, episodes=episodes,
                      added_in=episode_id, updated_in=episode_id, refers_to=list(refers_to), turns=list(turns))
    state.ledger.append(item)
    return item


# ---------------------------------------------------------------- length enforcement (code, not prompt)


CITE = re.compile(r"\s*\[\s*((?:L|ep)\d+(?:\s*[,;]\s*(?:L|ep)\d+)*)\s*\]")  # [L04, L12]; [ep001] is stripped too
Cited = list[tuple[str, tuple[str, ...]]]  # sentences with the ledger ids they cite


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def split_cited(text: str) -> Cited:
    """Split prose into sentences with the ledger ids each one cites ([L04, L12]); the citations are removed."""
    out: Cited = []
    for piece in re.split(r"(?<=[.!?\]])\s+(?=[A-Z0-9\"“(])", text.strip()):
        ids = tuple(dict.fromkeys(i for m in CITE.finditer(piece) for i in re.split(r"\s*[,;]\s*", m[1])
                                  if i.startswith("L")))
        clean = re.sub(r"\s+([.,;:!?])", r"\1", CITE.sub("", piece)).strip()
        if clean:
            out.append((clean, ids))
    return out


def attach_by_overlap(cited: Cited, items: list[LedgerItem], threshold: float = 0.5) -> Cited:
    """An uncited sentence that carries most of an item's words covers that item: attach its id, so the coverage
    check does not insert a duplicate and trimming protects the sentence."""
    out = list(cited)
    have = {i for _, ids in out for i in ids}
    for item in items:
        if item.id in have or not out:
            continue
        words = set(tokenize(item.text))
        scores = [len(words & set(tokenize(t))) / (len(words) or 1) for t, _ in out]
        j = max(range(len(out)), key=lambda j: scores[j])
        if scores[j] >= threshold:
            out[j] = (out[j][0], (*out[j][1], item.id))
            have.add(item.id)
    return out


def _prose_text(value: str | Cited) -> str:
    return value.strip() if isinstance(value, str) else " ".join(s for s, _ in value)


def render_summary(prose: dict[str, str | Cited], arc: list[tuple[str, str, str]]) -> str:
    """prose: section key -> text (or cited sentences); arc: (episode_id, title, role) in series order."""
    parts = [f"## {heading}\n{_prose_text(prose.get(key, ''))}" for key, heading in PROSE]
    lines = [f"- {ep} · {title}" + (f": {role.strip()}" if role.strip() else "") for ep, title, role in arc]
    parts.append(f"## {ARC_HEADING}\n" + "\n".join(lines))
    return "\n\n".join(parts)


def enforce_length(prose: dict[str, str | Cited], arc: list[tuple[str, str, str]], limit: int,
                   weight: Any = None, sources: dict | None = None) -> tuple[str, list[str]]:
    """Guarantee len(summary) <= limit while keeping every section and every episode's arc line.
    1) in the prose section furthest over its share, drop the least important sentence: weight(cited ids) decides
       (uncited sentences first, revised items last); ties drop the later sentence,
    2) shorten the longest arc roles to their first sentence, then cut them at a word boundary,
    3) cut the longest prose section at a word boundary. A final assert makes overflow impossible.
    If `sources` is given, it receives the kept sentences of each section with the ledger ids they cite."""
    weight = weight or (lambda ids: 0)
    prose = {k: list(split_cited(v) if isinstance(v, str) else v) for k, v in prose.items()}
    arc, actions = list(arc), []
    over = lambda: len(render_summary(prose, arc)) - limit  # noqa: E731
    while over() > 0:
        multi = [k for k, _ in PROSE if len(prose.get(k, [])) > 1]
        if multi:
            k = max(multi, key=lambda k: len(_prose_text(prose[k])) / SHARE[k])
            j = min(range(len(prose[k])), key=lambda j: (weight(prose[k][j][1]), -j))
            dropped, ids = prose[k].pop(j)
            actions.append(f"dropped a sentence of {k} ({len(dropped)} chars, cites {', '.join(ids) or 'nothing'}): "
                           f"{dropped[:70]}")
            continue
        j = max(range(len(arc)), key=lambda j: len(arc[j][2]), default=None)
        if j is not None and len(arc[j][2]) > 60:
            ep, title, role = arc[j]
            first = sentences(role)[0] if sentences(role) else role
            new = first if len(first) < len(role) else _cut_words(role, max(60, len(role) - over() - 1))
            arc[j] = (ep, title, new)
            actions.append(f"shortened arc line for {ep} ({len(role)} -> {len(new)} chars)")
            continue
        k = max((k for k, _ in PROSE), key=lambda k: len(_prose_text(prose.get(k, []))))
        text = _prose_text(prose.get(k, []))
        if len(text) <= 1:
            break
        prose[k] = [(_cut_words(text, max(1, len(text) - over() - 1)), ())]
        actions.append(f"cut {k} at a word boundary")
    text = render_summary(prose, arc)
    if len(text) > limit:  # only reachable with an absurdly small limit
        text = _cut_words(text, limit)
        actions.append("hard cut of the whole summary")
    assert len(text) <= limit, (len(text), limit)
    if sources is not None:
        sources.update({k: [[t, list(ids)] for t, ids in v] for k, v in prose.items()})
    return text, actions


def _cut_words(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    cut = text[: max(0, n - 1)].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


# ---------------------------------------------------------------- the agent


def series_spec(spec: HarnessSpec) -> HarnessSpec:
    """Harness files written before the series feature have no `series` section; borrow base.yaml's."""
    if spec.series is not None:
        return spec
    return spec.model_copy(update={"series": harness.load(BASE_HARNESS).series})


class SeriesUpdater:
    def __init__(self, manifest: Manifest, spec: HarnessSpec, settings: Settings, verbose: bool = False):
        self.manifest = manifest
        self.spec = series_spec(spec)
        self.cfg = self.spec.series
        self.settings = settings
        self.pipeline = Pipeline(self.spec, settings, verbose=verbose)
        self.verbose = verbose
        self.store = SeriesStore(settings.out_dir, manifest.series_id)

    @property
    def trace(self) -> Trace:
        return self.pipeline.trace

    # ------------------------------------------------------------ commands

    def episode_ids(self) -> list[tuple[str, Path]]:
        return [(ingest.load(p, self.spec).episode_id, Path(p)) for p in self.manifest.episodes]

    async def sync(self, until: str | None = None, reuse_reports: bool = False) -> SeriesState:
        """Bring the series up to date with the manifest: every episode not in the state yet, in order."""
        state = self.store.load(self.manifest, self.cfg.max_chars)
        ids = self.episode_ids()
        if until and until not in [e for e, _ in ids]:
            raise ValueError(f"{until} is not in the manifest {self.manifest._path}")
        extra = [e for e in state.episodes if e not in [i for i, _ in ids]]
        if extra:
            console.print(f"[yellow]! the state has episodes no longer in the manifest: {extra} "
                          "(run `series rebuild` to drop them)[/]")
        pending = []
        for ep_id, path in ids:
            if ep_id not in state.episodes:
                pending.append(path)
            if ep_id == until:
                break
        if not pending:
            console.print(f"[green]✓[/] {state.title} is up to date: v{state.version}, {len(state.episodes)} episodes, "
                          f"{state.char_count:,}/{self.cfg.max_chars:,} characters")
        for path in pending:
            state = await self.add_episode(state, path, reuse_reports=reuse_reports)
        return state

    async def add(self, transcript: str, reuse_reports: bool = False) -> SeriesState:
        """Add a transcript to the manifest (if it is not listed yet), then sync up to and including it."""
        known = dict(self.episode_ids())
        ep_id = transcript if transcript in known else ingest.load(transcript, self.spec).episode_id
        if ep_id not in known:
            self.manifest.append(Path(transcript).as_posix())
            console.print(f"[green]✓[/] added {transcript} to {self.manifest._path}")
        return await self.sync(until=ep_id, reuse_reports=reuse_reports)

    async def rebuild(self) -> SeriesState:
        """Replay the whole series from the stored episode digests (no episode pipeline, no extraction):
        resets drift, and handles reordered or removed episodes in the manifest."""
        old = self.store.load(self.manifest, self.cfg.max_chars)
        state = SeriesState(series_id=old.series_id, title=self.manifest.title, max_chars=self.cfg.max_chars,
                            version=old.version, history=old.history)
        for ep_id, path in self.episode_ids():
            digest = old.digests.get(ep_id)
            state = await self.add_episode(state, path, digest=digest, mode="rebuild" if digest else None)
        return state

    # ------------------------------------------------------------ one update

    async def add_episode(self, state: SeriesState, path: Path, reuse_reports: bool = False,
                          digest: EpisodeDigest | None = None, mode: str | None = None) -> SeriesState:
        state = state.model_copy(deep=True)  # nothing is persisted unless the whole update succeeds
        ep = ingest.load(path, self.spec)
        report = None
        if digest is None:
            report = self._load_report(ep.episode_id) if reuse_reports else None
            if report is None:
                report = await self.pipeline.run_file(path)  # 1. the existing per-episode pipeline
        mode = mode or ("initial" if not state.episodes else "incremental")
        version, before = state.version + 1, state.summary
        llm.STATS.clear()
        t0 = time.time()
        with open_trace(f"{state.series_id}", self.store.trace_path(version, ep.episode_id), self.verbose) as tr:
            self.pipeline.trace = tr
            tr.header(f"series v{version} · {mode}: + {ep.episode_id} {ep.title}")
            degraded: list[str] = []
            reasoning: dict[str, str] = {}
            if digest is None:
                digest = await self.extract(state, ep, report, reasoning, degraded)  # 2. what matters for the series
            ops, change = await self.merge(state, digest, reasoning, degraded)  # 3. how the ledger changes
            state.episodes.append(ep.episode_id)
            state.digests[ep.episode_id] = digest
            applied = apply_ops(state, ep.episode_id, ops, {p.id: p for p in digest.points}, self.cfg.max_ledger_items)
            self._log_ops(applied)
            sources: dict = {}
            summary, actions = await self.write(state, applied, reasoning, degraded, sources)  # 4. within the limit
            state.version, state.summary, state.char_count = version, summary, len(summary)
            state.summary_sources = sources
            state.max_chars = self.cfg.max_chars
            state.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
            similarity = round(difflib.SequenceMatcher(None, before, summary).ratio(), 2) if before else None
            record = UpdateRecord(
                version=version, episode_id=ep.episode_id, at=state.updated_at, mode=mode,
                change=change or _describe(applied), chars=len(summary), chars_before=len(before),
                similarity=similarity, ops=applied, reasoning=reasoning, length_actions=actions, degraded=degraded,
                llm_calls=llm.STATS["llm_calls"], cache_hits=llm.STATS["cache_hits"], seconds=round(time.time() - t0, 1),
                model="fake" if self.settings.fake_llm else (self.settings.model_override or self.spec.model),
                harness=self.spec.version)
            state.history.append(record)
            self.store.save(state)
            tr.log("series_update", record=record.model_dump())
            tr.result("save", f"v{version} saved to {self.store.path} · {len(summary):,}/{self.cfg.max_chars:,} "
                              f"characters" + (f" · similarity to v{version - 1}: {similarity}" if similarity else ""))
        console.print(f"\n[bold]{state.title} · series summary v{version}[/] "
                      f"[dim]({len(state.episodes)} episodes)[/]\n{summary}\n")
        console.print(f"[bold]Characters: {len(summary):,} / {self.cfg.max_chars:,}[/] · change: {record.change}")
        return state

    def _load_report(self, episode_id: str) -> Report | None:
        path = self.settings.out_dir / episode_id / "report.json"
        if path.exists():
            console.print(f"[dim]reusing {path}[/]")
            return Report.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    # ------------------------------------------------------------ step 2: extract

    async def extract(self, state: SeriesState, ep: Episode, report: Report, reasoning: dict,
                      degraded: list[str]) -> EpisodeDigest:
        tr, cfg = self.trace, self.cfg
        tr.step("extract", "series extract agent: what this episode adds to the series")
        mentions = guest_mentions(ep, state)
        people = list(dict.fromkeys([ep.host, *ep.guests, *(g for e in state.episodes for g in state.digests[e].guests)]))
        if mentions:
            tr.observation("code", "earlier guests named: " + "; ".join(f"{g} ({', '.join(ids)})" for g, ids in mentions.items()))
        earlier = [f"- {e} \"{state.digests[e].title}\" (guests: {', '.join(state.digests[e].guests) or 'none'}): "
                   f"{state.digests[e].arc_role}" for e in state.episodes]
        lines = [f"[{t.id} {t.ts} {t.section or ''}] {t.speaker}: {t.text}" for t in ep.turns]
        message = (f"Series: {state.title}. This is episode {len(state.episodes) + 1}: {ep.episode_id} \"{ep.title}\".\n"
                   f"Host: {ep.host}. Guests: {', '.join(ep.guests) or 'none'}.\n\n"
                   f"Earlier episodes:\n{chr(10).join(earlier) or '(none: this is the first episode)'}\n\n"
                   + (f"Earlier guests named in this episode (found by code): "
                      + "; ".join(f"{g} in {', '.join(ids)}" for g, ids in mentions.items()) + "\n\n" if mentions else "")
                   + "Transcript (turn id, time, section):\n" + "\n".join(lines))
        turn_ids = [t.id for t in ep.turns]
        turns = {t.id: t for t in ep.turns}
        # Recall check: every section where a guest (or the host, answering) says something substantive must
        # contribute a point. Frame sections and sections with only the host's questions are exempt.
        sections: dict[str, list[str]] = {}
        for t in ep.turns:
            if t.section and t.section.lower() not in FRAME_SECTIONS and not t.text.rstrip().endswith("?"):
                sections.setdefault(t.section, []).append(t.id)
        # Measured results are what later episodes update, so each one stated (not asked) must reach a point.
        spoken_values = {t.id: measures(t.text) for t in ep.turns
                         if measures(t.text) and not t.text.rstrip().endswith("?")
                         and (t.section or "").lower() not in FRAME_SECTIONS}

        def check(o: Any) -> list[str]:
            n = len([p for p in o.points if p.text.strip()])
            if not cfg.points_min <= n <= cfg.points_max:
                return [f"Give {cfg.points_min}-{cfg.points_max} points (you gave {n})."]
            compound = [f"P{j} ({', '.join(sorted(measures(p.text)))})" for j, p in enumerate(o.points, 1)
                        if len({m.split()[-1] for m in measures(p.text)}) > 1]
            if compound:
                return [f"Points {'; '.join(compound)} combine several measured results. Split each into one figure "
                        "point per result, so a later update to one number does not replace the others."]
            stated = set().union(*(measures(p.text) for p in o.points if p.text.strip()))
            unmeasured = [f"{m} ({tid})" for tid, ms in spoken_values.items() for m in sorted(ms) if m not in stated]
            if unmeasured:
                return [f"These measured results have no point: {', '.join(unmeasured)}. Give each one its own figure "
                        "point (replace a less important point if you are at the maximum)."]
            covered = {turns[p.turn_id].section for p in o.points if p.text.strip() and p.turn_id in turns}
            missing = {s: ids for s, ids in sections.items() if s not in covered}
            if missing:
                return ["No point comes from these sections: " + "; ".join(f"{s} ({', '.join(ids)})"
                                                                           for s, ids in missing.items())
                        + ". Add a point for each (replace a less important point if you are at the maximum)."]
            unnamed = [f"P{j}" for j, p in enumerate(o.points, 1)
                       if p.kind != "theme" and p.text.strip() and not any(_named(x, p.text) for x in people)]
            if unnamed:
                return [f"Points {', '.join(unnamed)} do not say who said it. Rewrite each as a self-contained "
                        "sentence that names the speaker, not as a quote."]
            return [] if o.arc_role.strip() else ["arc_role is empty: write one sentence."]

        out, problems = await self.pipeline.run_structured(
            "series_extract", lambda: self.pipeline.agents.series_extract(list(state.episodes), turn_ids), message, None,
            check=check)
        points: list[DigestPoint] = []
        if out is not None:
            reasoning["extract"] = out.reasoning
            tr.thought("series_extract", out.reasoning)
            for p in out.points[: cfg.points_max]:
                words = set(tokenize(p.text))
                dup = next((q.id for q in points if len(words & set(tokenize(q.text))) / (len(words) or 1) >= 0.8), None)
                if dup:
                    tr.observation("code", f"dropped a point that repeats {dup}: {p.text[:80]}")
                    continue
                if p.text.strip():
                    refs = [r for r in dict.fromkeys(p.refers_to) if r in state.episodes]
                    kind = p.kind
                    if kind == "theme" and MEASURE.search(p.text):  # a measured result is a figure, whatever the label
                        kind = "figure"
                        tr.observation("code", f"P{len(points) + 1} states a measured number: kind theme -> figure")
                    elif kind == "figure" and not MEASURE.search(p.text) and not re.search(r"\d", p.text):
                        kind = "position"  # a "figure" without any number is a view
                        tr.observation("code", f"P{len(points) + 1} has no number: kind figure -> position")
                    t = turns.get(p.turn_id)
                    texts = split_compound(p.text.strip()) if kind == "figure" else [p.text.strip()]
                    if len(texts) > 1:  # the model kept a compound figure through its repairs: split it in code
                        tr.observation("code", f"split a compound figure into {len(texts)} points: {p.text[:80]}")
                    for text in texts:
                        points.append(DigestPoint(id=f"{ep.episode_id}.P{len(points) + 1}", kind=kind, text=text,
                                                  refers_to=refs, turn=f"{t.id} {t.ts}" if t else "",
                                                  changes=clean_changes(p.changes_earlier, text)))
        if len(points) < cfg.points_min:  # deterministic fallback: the episode report's takeaways
            for t in report.takeaways:
                if len(points) >= cfg.points_min:
                    break
                points.append(DigestPoint(id=f"{ep.episode_id}.P{len(points) + 1}", kind="position", text=t))
            degraded.append(f"extract fallback ({problems[0] if problems else 'too few points'})")
            tr.warn("extract: topped up points from the episode's takeaways")
        arc_role = (out.arc_role.strip() if out is not None and out.arc_role.strip()
                    else f"{ep.title}, with {', '.join(ep.guests) or ep.host}.")
        for p in points:
            refs = (f" -> refers to {', '.join(p.refers_to)}" if p.refers_to else "") + (
                f" -> changes: {p.changes}" if p.changes else "")
            tr.observation("series_extract", f"{p.id} [{p.kind}] {p.text}{refs}")
        tr.result("series_extract", f"{len(points)} points · arc role: {arc_role}")
        counts = {s: sum(fc.status == s for fc in report.fact_checks) for s in ("verified", "inaccurate", "unverifiable")}
        return EpisodeDigest(
            episode_id=ep.episode_id, title=ep.title, host=ep.host, guests=ep.guests, source=ep.source,
            sections=list(dict.fromkeys(t.section for t in ep.turns if t.section)), arc_role=arc_role, points=points,
            mentions=mentions, fact_checks=counts, summary=report.summary)

    # ------------------------------------------------------------ step 3: merge

    async def merge(self, state: SeriesState, digest: EpisodeDigest, reasoning: dict,
                    degraded: list[str]) -> tuple[list[dict], str]:
        tr = self.trace
        current = state.current()
        if not current:
            tr.step("merge", "empty ledger: every point of the first episode is added (no LLM call)")
            ops = [{"point": p.id, "op": "add", "target": "new", "text": p.text,
                    "reason": "first episode of the series"} for p in digest.points]
            return ops, f"Initial summary from {digest.episode_id}: {len(ops)} ledger items."
        tr.step("merge", f"series merge agent: {len(digest.points)} new points against {len(current)} ledger items")
        people = list(dict.fromkeys([digest.host, *digest.guests,
                                     *(g for e in state.episodes for g in state.digests[e].guests)]))
        related = related_items(current, digest.points, people)
        local = {p.id: p.id.split(".")[1] for p in digest.points}  # ep004.P2 -> P2
        ledger_text = "\n".join(f"[{i.id} {i.kind} | {', '.join(i.episodes)}] {i.text}" for i in current)
        point_text = "\n".join(
            f"{local[p.id]} [{p.kind}" + (f" | refers to {', '.join(p.refers_to)}" if p.refers_to else "") + f"] {p.text}"
            + (f"\n   changes the earlier view: {p.changes}" if p.changes else "")
            + (f"\n   possibly related: {', '.join(related[p.id])}" if related.get(p.id) else "")
            for p in digest.points)
        message = (f"Current ledger ({len(current)} items):\n{ledger_text}\n\n"
                   f"New episode {digest.episode_id} \"{digest.title}\" (guests: {', '.join(digest.guests) or 'none'}). "
                   f"Its points:\n{point_text}\n\nGive one operation for each point.")
        point_ids, ledger_ids = list(local.values()), [i.id for i in current]

        def check(o: Any) -> list[str]:
            problems = []
            missing = [p for p in point_ids if p not in {op.point for op in o.ops}]
            if missing:
                problems.append(f"Every point needs one operation; missing: {', '.join(missing)}.")
            bad = [op.point for op in o.ops if op.op != "add" and op.target == "new"]
            if bad:
                problems.append(f"{', '.join(bad)}: revise and reinforce need the id of an existing ledger item as "
                                "target; use add for new information.")
            kinds = {local[p.id]: p.kind for p in digest.points}
            items = {i.id: i for i in current}
            mismatched = [f"{op.point} is a {kinds[op.point]} but {op.target} is a {items[op.target].kind}"
                          for op in o.ops if op.op == "revise" and op.target in items
                          and kind_mismatch(kinds.get(op.point, ""), items[op.target].kind)]
            if mismatched:
                problems.append("; ".join(mismatched) + ". A figure can only revise a figure, and a prediction only a "
                                "prediction: use add, or revise the item of the same kind it updates.")
            return problems

        out, problems = await self.pipeline.run_structured(
            "series_merge", lambda: self.pipeline.agents.series_merge(point_ids, ledger_ids), message, None, check=check)
        if out is None:
            degraded.append(f"merge failed ({problems[0] if problems else 'no output'}); points added")
            tr.warn("merge: no usable output; adding every point")
            return [{"point": f"{digest.episode_id}.{p}", "op": "add", "target": "new", "text": "",
                     "reason": "merge agent failed"}
                    for p in point_ids], ""
        reasoning["merge"] = out.reasoning
        tr.thought("series_merge", out.reasoning)
        ops = [{"point": f"{digest.episode_id}.{op.point}", "op": op.op, "target": op.target, "text": op.text,
                "reason": op.reason, "old_still_current": op.old_still_current} for op in out.ops]
        for note in resolve_revise_targets(ops, {p.id: p for p in digest.points}, current, people):
            tr.warn(f"merge: {note}")
        covered = {op.point for op in out.ops}
        for p in point_ids:  # problems left after the repairs: never lose a point silently
            if p not in covered:
                ops.append({"point": f"{digest.episode_id}.{p}", "op": "add", "target": "new", "text": "",
                            "reason": "no operation from the merge agent; added by code"})
                tr.warn(f"merge: {p} had no operation; added")
        tr.result("series_merge", out.change_summary)
        return ops, out.change_summary.strip()

    def _log_ops(self, applied: list[AppliedOp]) -> None:
        symbol = {"add": "+", "revise": "~", "reinforce": "=", "archive": "-"}
        for a in applied:
            target = f"{a.replaced} -> {a.item}" if a.replaced else a.item
            self.trace.log("ledger_op", "series_merge", **a.model_dump())
            console.print(f"  [yellow]{symbol[a.op]} {a.op}[/] {target}: {a.text[:150]}"
                          + (f"\n      [dim]why: {a.reason}[/]" if a.reason else ""))
        counts = {k: sum(a.op == k for a in applied) for k in symbol}
        self.trace.result("ledger", ", ".join(f"{v} {k}" for k, v in counts.items() if v) or "no change")

    # ------------------------------------------------------------ step 4: write + enforce

    async def write(self, state: SeriesState, applied: list[AppliedOp], reasoning: dict,
                    degraded: list[str], sources: dict) -> tuple[str, list[str]]:
        tr, cfg = self.trace, self.cfg
        n = len(state.episodes)
        budgets = {f"{k}_chars": int(cfg.target_chars * SHARE[k]) for k in ("themes", "evolution", "connections")}
        budgets["arc_chars"] = max(60, int(cfg.target_chars * SHARE["arc"] / n))
        tr.step("write", f"series writer agent: {n} episodes, target {cfg.target_chars:,} chars, "
                         f"hard limit {cfg.max_chars:,} (enforced in code)")
        message = self._writer_message(state, applied)
        titles = {e: arc_title(state.digests[e]) for e in state.episodes}
        revised = [i for i in state.current() if i.revises]  # must appear in "How the ideas developed"

        def assemble(o: Any) -> tuple[dict[str, Cited], list[tuple[str, str, str]]]:
            roles = {}
            for a in o.arc:
                roles.setdefault(a.episode_id, CITE.sub("", a.role).strip())
            prose = {k: split_cited(getattr(o, k)) for k, _ in PROSE}
            prose["evolution"] = attach_by_overlap(prose["evolution"], revised)
            return prose, [(e, titles[e], roles.get(e, "")) for e in state.episodes]

        def uncovered(evolution: Cited) -> list[LedgerItem]:
            cited = {i for _, ids in evolution for i in ids}
            return [i for i in revised if i.id not in cited and not any(o.id in cited for o in lineage(state, i))]

        def check(o: Any) -> list[str]:
            prose, arc = assemble(o)
            problems = []
            missing = [e for e, _, role in arc if not role]
            if missing:
                problems.append(f"arc needs one entry for every episode; missing: {', '.join(missing)}.")
            short = [k for k, text in prose.items() if len(_prose_text(text)) < cfg.min_section_chars]
            if short:
                problems.append(f"These fields are empty or too short: {', '.join(short)}.")
            gaps = uncovered(prose["evolution"])
            if gaps:
                problems.append("evolution must describe every revised item and cite its id; missing: "
                                + "; ".join(f"[{i.id}] {i.text}" for i in gaps))
            total = len(render_summary(prose, arc))
            if total > cfg.max_chars:
                sizes = ", ".join(f"{k} {len(_prose_text(t)):,}" for k, t in prose.items())
                problems.append(f"The summary is {total:,} characters; the limit is {cfg.max_chars:,}. Cut about "
                                f"{total - cfg.target_chars:,} characters, keeping the revised items (now: {sizes}).")
            return problems

        def weight(ids: tuple[str, ...]) -> int:
            """Trimming order: uncited sentences go first, revised items (and their history) last."""
            best = 0
            for item in filter(None, map(state.item, ids)):
                if item.status == "current" and item.revises:
                    score = 100
                elif item.status == "superseded":
                    score = 50  # the earlier view in a revision sentence
                else:
                    score = 10 + 5 * len(item.episodes) + KIND_WEIGHT.get(item.kind, 1)
                best = max(best, score)
            return best

        out, problems = await self.pipeline.run_structured(
            "series_writer", lambda: self.pipeline.agents.series_writer(list(state.episodes), budgets), message, None,
            check=check)
        if out is not None:
            reasoning["writer"] = out.reasoning
            tr.thought("series_writer", out.reasoning)
            prose, arc = assemble(out)
        else:
            prose, arc = self._fallback_sections(state)
            degraded.append(f"writer failed ({problems[0] if problems else 'no output'}); summary built from the ledger")
            tr.warn("writer: no usable output; building the summary from the ledger")
        filled = [e for e, _, role in arc if not role]
        if filled:
            arc = [(e, t, role or state.digests[e].arc_role) for e, t, role in arc]
            tr.warn(f"writer: arc lines for {', '.join(filled)} taken from the episode digests")
        gaps = uncovered(prose["evolution"])
        if gaps:  # never lose a revision: put the ledger's own sentence first in the section
            prose["evolution"] = prose["evolution"] + [(_sentence(i.text), (i.id,)) for i in gaps]
            tr.warn(f"writer: revised items {', '.join(i.id for i in gaps)} added to evolution from the ledger")
        cites = {k: sorted({i for _, ids in v for i in ids}) for k, v in prose.items()}
        tr.observation("series_writer", "cited: " + "; ".join(f"{k} {', '.join(v) or '-'}" for k, v in cites.items()))
        draft = render_summary(prose, arc)
        text, actions = enforce_length(prose, arc, cfg.max_chars, weight, sources)
        if actions:
            degraded.append(f"length enforced in code: {len(draft):,} -> {len(text):,} chars")
            for a in actions:
                tr.warn(f"length: {a}")
        tr.result("series_writer", f"summary: {len(text):,} characters (limit {cfg.max_chars:,})"
                  + (f", trimmed from {len(draft):,}" if actions else ""))
        return text, actions

    def _writer_message(self, state: SeriesState, applied: list[AppliedOp]) -> str:
        eps = "\n".join(f"{i}. {e} \"{d.title}\" (guests: {', '.join(d.guests) or 'none'}). Role: {d.arc_role}"
                        for i, (e, d) in enumerate(((e, state.digests[e]) for e in state.episodes), 1))
        guests = "; ".join(f"{g} ({', '.join(eps_)})" for g, eps_ in recurring_guests(state).items()) or "none yet"
        segments = "; ".join(f"{s} ({', '.join(eps_)})" for s, eps_ in recurring_segments(state).items()) or "none yet"
        groups = {"Themes": ["theme"], "Positions, predictions and figures": ["position", "prediction", "figure"],
                  "References between episodes": ["reference"]}
        blocks = []
        for name, kinds in groups.items():
            rows = []
            for i in state.current():
                if i.kind in kinds:
                    responds = f"; responds to {', '.join(i.refers_to)}" if i.refers_to else ""
                    row = f"- [{i.id}] {i.text} ({', '.join(i.episodes)}{responds})"
                    chain = lineage(state, i)
                    if chain:
                        row += "\n    revision history (superseded, oldest first): " + " | ".join(
                            f"{old.added_in}: {old.text}" for old in chain) + f" | {i.added_in}: current"
                    rows.append(row)
            if rows:
                blocks.append(f"{name}:\n" + "\n".join(rows))
        previous = (f"\n\nPrevious version of the summary (v{state.version}, before {state.episodes[-1]} was added):\n"
                    f"{state.summary}") if state.summary else ""
        changes = "\n".join(f"- {a.op} {a.replaced + ' -> ' if a.replaced else ''}{a.item}: {a.text}"
                            for a in applied if a.op != "archive")
        if state.summary and changes:
            previous += f"\n\nChanges in this update ({state.episodes[-1]}), to work into the summary:\n{changes}"
        return (f"Series: {state.title}, hosted by {state.digests[state.episodes[0]].host}. "
                f"{len(state.episodes)} episodes, in order:\n{eps}\n\n"
                f"Recurring guests (found by code): {guests}.\nRecurring segments (found by code): {segments}.\n\n"
                f"Ledger (current items):\n" + "\n\n".join(blocks) + previous)

    def _fallback_sections(self, state: SeriesState) -> tuple[dict[str, Cited], list[tuple[str, str, str]]]:
        """Deterministic summary from the ledger: one cited sentence per item, most important first."""
        items = sorted(state.current(), key=lambda i: priority(i, state), reverse=True)
        cited = lambda kinds: [(_sentence(i.text), (i.id,)) for i in items if i.kind in kinds]  # noqa: E731
        guests = "; ".join(f"{g} ({', '.join(e)})" for g, e in recurring_guests(state).items())
        segments = "; ".join(f"{s} ({', '.join(e)})" for s, e in recurring_segments(state).items())
        conn = [(x, ()) for x in [f"Recurring guests: {guests}." if guests else "",
                                  f"Recurring segments: {segments}." if segments else ""] if x] + cited({"reference"})
        prose = {"themes": cited({"theme"}), "evolution": cited({"position", "prediction", "figure"}),
                 "connections": conn or [("No recurring guests or segments yet.", ())]}
        return prose, [(e, arc_title(state.digests[e]), state.digests[e].arc_role) for e in state.episodes]


def arc_title(d: EpisodeDigest) -> str:
    """Arc lines name each episode's guests in code, so every guest is named whatever the writer does."""
    return f"{d.title} ({', '.join(d.guests)})" if d.guests else d.title


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _describe(applied: list[AppliedOp]) -> str:
    counts = {k: sum(a.op == k for a in applied) for k in ("add", "revise", "reinforce", "archive")}
    return ", ".join(f"{v} {k}" for k, v in counts.items() if v) or "no change"


# ---------------------------------------------------------------- series.md


def markdown(s: SeriesState) -> str:
    def cell(text: str) -> str:
        return " ".join(text.replace("|", "/").split())

    lines = [f"# {s.title} · series summary", "",
             f"*Version {s.version} · {len(s.episodes)} episodes · **{s.char_count:,} / {s.max_chars:,} characters** "
             f"(spaces included) · updated {s.updated_at}*", "", s.summary, "", "---", "",
             "## Episodes in the series", "", "| # | Episode | Title | Guests | Fact-check | Report |",
             "|---|---|---|---|---|---|"]
    for i, e in enumerate(s.episodes, 1):
        d = s.digests[e]
        fc = d.fact_checks
        lines.append(f"| {i} | {e} | {cell(d.title)} | {cell(', '.join(d.guests))} | "
                     f"✅ {fc.get('verified', 0)} ⚠ {fc.get('inaccurate', 0)} ❓ {fc.get('unverifiable', 0)} | "
                     f"[report](../../{e}/report.md) |")
    lines += ["", "## Update history", "",
              "| Version | Episode added | Change to series summary | Ledger ops | Characters | Similarity to previous |",
              "|---|---|---|---|---|---|"]
    for h in s.history:
        lines.append(f"| v{h.version} ({h.mode}) | {h.episode_id} | {cell(h.change)} | {_describe(h.ops)} | "
                     f"{h.chars:,} | {h.similarity if h.similarity is not None else '–'} |")
    lines += ["", "### What changed and why", ""]
    for h in s.history:
        lines.append(f"**v{h.version} · {h.episode_id}** ({h.llm_calls} LLM calls, {h.seconds}s, model `{h.model}`)")
        lines.append("")
        for a in h.ops:
            target = f"{a.replaced} → {a.item}" if a.replaced else a.item
            lines.append(f"- **{a.op}** {target}: {cell(a.text)}" + (f" — *{cell(a.reason)}*" if a.reason else ""))
        for a in h.length_actions:
            lines.append(f"- length: {cell(a)}")
        for d in h.degraded:
            lines.append(f"- ⚠ {cell(d)}")
        lines.append("")
    if s.summary_sources:
        lines += ["<details><summary>Sources: the ledger items behind each sentence of the summary</summary>", ""]
        for key, heading in PROSE:
            lines.append(f"**{heading}**")
            lines.append("")
            for text, ids in s.summary_sources.get(key, []):
                refs = ", ".join(f"{i} ({'; '.join(s.item(i).turns) or ', '.join(s.item(i).episodes)})"
                                 for i in ids if s.item(i)) or "no citation"
                lines.append(f"- {cell(text)} ← {refs}")
            lines.append("")
        lines += ["</details>", ""]
    lines += ["<details><summary>Ledger (current and superseded items)</summary>", ""]
    for i in s.ledger:
        text = f"[{i.id} {i.kind} · {'; '.join(i.turns) or ', '.join(i.episodes)}] {cell(i.text)}"
        if i.status == "current":
            lines.append(f"- {text}" + (f" *(revises {i.revises})*" if i.revises else ""))
        else:
            lines.append(f"- ~~{text}~~ *({i.status}" + (f" by {i.superseded_by}" if i.superseded_by else "") + ")*")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def attribution_issues(state: SeriesState) -> list[str]:
    """Summary sentences that name an episode none of their cited ledger items (or their history) comes from."""
    issues = []
    for section, rows in state.summary_sources.items():
        for text, ids in rows:
            items = [i for i in map(state.item, ids) if i]
            if not items:
                continue
            allowed = {e for i in items for x in [i, *lineage(state, i)] for e in [*x.episodes, *x.refers_to]}
            wrong = sorted(set(re.findall(r"ep\d{3}", text)) - allowed)
            if wrong:
                issues.append(f"{section}: names {', '.join(wrong)} but cites {', '.join(ids)}: {text[:90]}")
    return issues


def check(state: SeriesState, gold: dict) -> list[tuple[str, bool, str]]:
    """Score a series state against eval/gold/series_<id>.yaml: (check, passed, detail)."""
    rx = lambda p: re.compile(p, re.I)  # noqa: E731
    results = [("all episodes, in order", state.episodes == gold["episodes"], ", ".join(state.episodes)),
               (f"summary <= {gold['max_chars']:,} chars", len(state.summary) <= gold["max_chars"],
                f"{len(state.summary):,} (stored count {state.char_count:,})")]
    missing = [m for m in gold.get("summary_mentions", []) if not rx(re.escape(m)).search(state.summary)]
    results.append(("summary names", not missing, f"missing: {', '.join(missing)}" if missing else "all named"))
    found = sorted(recurring_guests(state))
    results.append(("recurring guests (code)", found == sorted(gold.get("recurring_guests", [])), ", ".join(found)))
    for r in gold.get("revisions", []):
        hits = [i for i in state.current() if i.added_in == r["episode"] and rx(r["new"]).search(i.text)
                and any(rx(r["old"]).search(old.text) for old in lineage(state, i))]
        results.append((f"revised: {r['name']}", bool(hits),
                        f"{hits[0].id} revises {hits[0].revises}" if hits else "no matching revise in the ledger"))
    lost = [name for name, p in gold.get("must_stay_current", {}).items()
            if not any(rx(p).search(i.text) for i in state.current())]
    if gold.get("must_stay_current"):
        results.append(("key unrevised facts present", not lost, f"lost: {'; '.join(lost)}" if lost else "all kept"))
    for p in gold.get("single_current", []):
        hits = [i.id for i in state.current() if rx(p).search(i.text)]
        results.append((f"single current item /{p}/", len(hits) <= 1, ", ".join(hits) or "none"))
    return results


def load_state(out_dir: Path, series_id: str) -> SeriesState | None:
    path = SeriesStore(out_dir, series_id).path
    return SeriesState.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

