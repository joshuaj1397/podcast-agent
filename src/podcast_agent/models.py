"""Data models: the parsed episode, the LLM output schemas, and the final report.

LLM schemas put `reasoning` first so the small model "thinks" before it commits to
an answer. Fields that reference the transcript (turn ids, evidence ids) are built
per episode with `Literal[...]` of the valid ids, so Ollama's constrained decoding
can only emit ids that exist. Code then copies exact text and timestamps.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

from .harness import ClaimType, StepName

# ---------------------------------------------------------------- episode


class Turn(BaseModel):
    id: str  # T01, T02, ...
    ts: str  # timestamp exactly as written in the source file
    seconds: int
    speaker: str  # resolved full name where possible
    section: str | None = None
    text: str  # cleaned (fillers removed, glossary applied)
    raw: str  # original text


class Episode(BaseModel):
    episode_id: str
    title: str
    host: str
    guests: list[str]
    recorded: str | None = None
    source: str
    turns: list[Turn]

    def turn(self, turn_id: str) -> Turn:
        return next(t for t in self.turns if t.id == turn_id)

    def transcript(self, turn_ids: set[str] | None = None) -> str:
        """Transcript as the LLM sees it: one line per turn with its id."""
        return "\n".join(
            f"[{t.id} {t.ts}] {t.speaker}: {t.text}" for t in self.turns if turn_ids is None or t.id in turn_ids
        )


# ---------------------------------------------------------------- LLM output schemas


class Plan(BaseModel):
    reasoning: str
    steps: list[StepName]
    claim_budget: int
    use_web_search: bool
    summary_focus: str


class SummaryOut(BaseModel):
    reasoning: str = Field(description="one or two sentences on the main themes")
    summary: str


def notes_schema(quote_ids: list[str]) -> type[BaseModel]:
    return create_model(
        "NotesOut",
        reasoning=(str, ...),
        takeaways=(list[str], ...),
        quote_ids=(list[Literal[tuple(quote_ids)]], ...),  # type: ignore[valid-type]
        tags=(list[str], ...),
    )


def claims_schema(turn_ids: list[str]) -> type[BaseModel]:
    item = create_model(
        "ClaimItem",
        turn_id=(Literal[tuple(turn_ids)], ...),  # type: ignore[valid-type]
        claim=(str, ...),
        type=(ClaimType, ...),
    )
    return create_model("ClaimsOut", reasoning=(str, ...), claims=(list[item], ...))  # type: ignore[valid-type]


VerdictLabel = Literal["supported", "contradicted", "outdated", "insufficient"]


def verdict_schema(evidence_ids: list[str]) -> type[BaseModel]:
    return create_model(
        "VerdictOut",
        reasoning=(str, ...),
        label=(VerdictLabel, ...),
        evidence_ids=(list[Literal[tuple(evidence_ids)]], ...),  # type: ignore[valid-type]
    )


# ---------------------------------------------------------------- report

Status = Literal["verified", "inaccurate", "unverifiable"]
SYMBOL: dict[str, str] = {"verified": "✅", "inaccurate": "⚠", "unverifiable": "❓"}


class Quote(BaseModel):
    turn_id: str
    ts: str
    speaker: str
    text: str


class Evidence(BaseModel):
    id: str
    title: str
    source: str
    date: str
    reliability: str
    text: str
    url: str | None = None


class FactCheckResult(BaseModel):
    claim: str
    turn_id: str
    ts: str
    speaker: str
    type: ClaimType
    status: Status
    symbol: str
    verification: str  # human-readable, e.g. "True (confirmed by NASA (mock DB))"
    confidence: float
    reasoning: str
    method: Literal["judged", "rule", "fallback"]
    labels: list[str] = []  # one per verdict sample
    queries: list[str] = []
    evidence: list[Evidence] = []


class Report(BaseModel):
    episode_id: str
    title: str
    host: str
    guests: list[str]
    recorded: str | None
    source: str
    summary: str
    takeaways: list[str]
    quotes: list[Quote]
    tags: list[str]
    fact_checks: list[FactCheckResult]
    plan: Plan | None = None
    degraded: list[str] = []  # critic failures that fell back to deterministic output
    harness_version: str
    model: str
    stats: dict[str, float] = {}


# ---------------------------------------------------------------- series (LLM output schemas; state lives in series.py)

PointKind = Literal["theme", "position", "prediction", "figure", "reference"]
LedgerOpName = Literal["add", "revise", "reinforce"]


def series_extract_schema(earlier_ids: list[str], turn_ids: list[str]) -> type[BaseModel]:
    refs = tuple(earlier_ids) or ("none",)
    point = create_model(
        "SeriesPoint",
        turn_id=(Literal[tuple(turn_ids)], ...),  # type: ignore[valid-type]  # where the point is said
        kind=(PointKind, ...),
        text=(str, ...),
        refers_to=(list[Literal[refs]], ...),  # type: ignore[valid-type]
        changes_earlier=(str, ...),  # the earlier view or figure this point changes, as the transcript states it
    )
    return create_model("ExtractOut", reasoning=(str, ...), points=(list[point], ...),  # type: ignore[valid-type]
                        arc_role=(str, ...))


def series_merge_schema(point_ids: list[str], ledger_ids: list[str]) -> type[BaseModel]:
    op = create_model(
        "LedgerOp",
        point=(Literal[tuple(point_ids)], ...),  # type: ignore[valid-type]
        target=(Literal[(*ledger_ids, "new")], ...),  # type: ignore[valid-type]
        # Self-check before the operation is chosen: code turns a revise of a still-current item into an add.
        old_still_current=(bool, ...),
        op=(LedgerOpName, ...),
        text=(str, ...),
        reason=(str, ...),
    )
    return create_model("MergeOut", reasoning=(str, ...), ops=(list[op], ...),  # type: ignore[valid-type]
                        change_summary=(str, ...))


def series_writer_schema(episode_ids: list[str]) -> type[BaseModel]:
    arc = create_model("ArcEntry", episode_id=(Literal[tuple(episode_ids)], ...), role=(str, ...))  # type: ignore[valid-type]
    return create_model("SeriesSummaryOut", reasoning=(str, ...), themes=(str, ...), evolution=(str, ...),
                        connections=(str, ...), arc=(list[arc], ...))  # type: ignore[valid-type]
