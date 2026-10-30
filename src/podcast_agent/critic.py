"""The critic: checks each sub-agent's output against the spec, and deterministic fallbacks when re-prompting fails.

Checks return a list of problems written as instructions the model can act on;
an empty list means the output passes.
"""

from __future__ import annotations

import re

from .harness import HarnessSpec
from .models import Episode, Report, Turn


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def check_summary(summary: str, spec: HarnessSpec) -> list[str]:
    n, lo, hi = word_count(summary), spec.summary.min_words, spec.summary.max_words
    if n < lo:
        return [f"The summary has {n} words; it must have {lo}-{hi}. Add more detail on the discussions and opinions."]
    if n > hi:
        return [f"The summary has {n} words; it must have {lo}-{hi}. Shorten it."]
    return []


def check_notes(takeaways: list[str], quote_ids: list[str], tags: list[str], spec: HarnessSpec) -> list[str]:
    n, problems = spec.notes, []
    real = [t for t in takeaways if word_count(t) >= 4]
    if len(real) != n.takeaways:
        problems.append(f"Give exactly {n.takeaways} takeaways (you gave {len(real)}), each a full sentence.")
    if not n.quotes_min <= len(set(quote_ids)) <= n.quotes_max:
        problems.append(f"Pick {n.quotes_min}-{n.quotes_max} different quote_ids (you gave {len(set(quote_ids))}).")
    if not n.tags_min <= len(set(tags)) <= n.tags_max:
        problems.append(f"Give {n.tags_min}-{n.tags_max} different tags (you gave {len(set(tags))}).")
    return problems


def normalize_tag(tag: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 &+-]", " ", tag.lower().lstrip("#")).split())


# ---------------------------------------------------------------- deterministic fallbacks


def fallback_summary(summary: str, ep: Episode, ranked_turns: list[Turn], spec: HarnessSpec) -> str:
    """Trim an over-long summary at a sentence boundary, or extend a short one with extractive sentences."""
    lo, hi = spec.summary.min_words, spec.summary.max_words
    sentences = re.split(r"(?<=[.!?])\s+", summary.strip()) if summary.strip() else []
    while sentences and word_count(" ".join(sentences)) > hi:
        sentences.pop()
    for t in ranked_turns:
        if word_count(" ".join(sentences)) >= lo:
            break
        addition = f"{t.speaker} noted: “{t.text}”"
        if word_count(" ".join([*sentences, addition])) <= hi:
            sentences.append(addition)
    if not sentences:
        sentences = [f"{ep.title}, hosted by {ep.host}."]
    return " ".join(sentences)


def fallback_notes(takeaways: list[str], quote_ids: list[str], tags: list[str], ranked_turns: list[Turn],
                   tag_hints: list[str], spec: HarnessSpec) -> tuple[list[str], list[str], list[str]]:
    n = spec.notes
    takeaways = [t for t in takeaways if word_count(t) >= 4][: n.takeaways]
    for t in ranked_turns:
        if len(takeaways) >= n.takeaways:
            break
        first = re.split(r"(?<=[.!?])\s+", t.text)[0]
        if first not in takeaways:
            takeaways.append(first)
    quote_ids = list(dict.fromkeys(quote_ids))[: n.quotes_max]
    for t in ranked_turns:
        if len(quote_ids) >= n.quotes_min:
            break
        if t.id not in quote_ids:
            quote_ids.append(t.id)
    tags = list(dict.fromkeys(tags))[: n.tags_max]
    for hint in tag_hints:
        if len(tags) >= n.tags_min:
            break
        if hint not in tags:
            tags.append(hint)
    return takeaways, quote_ids, tags


# ---------------------------------------------------------------- final structural check (also used by eval)


def structural_problems(report: Report, ep: Episode, spec: HarnessSpec) -> list[str]:
    problems = check_summary(report.summary, spec)
    problems += check_notes(report.takeaways, [q.turn_id for q in report.quotes], report.tags, spec)
    turns = {t.id: t for t in ep.turns}
    for q in report.quotes:
        t = turns.get(q.turn_id)
        if t is None or q.text != t.text or q.ts != t.ts:
            problems.append(f"quote {q.turn_id} does not match the transcript")
    for fc in report.fact_checks:
        if fc.turn_id not in turns:
            problems.append(f"fact-check cites unknown turn {fc.turn_id}")
    return problems
