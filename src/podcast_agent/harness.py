"""The harness spec: every behaviour knob of the agent, loaded from harness/*.yaml.

This file is the "genome" that Evo² edits. Anything the agent does that is not
fixed code (prompts, tool descriptions, budgets, retrieval settings, verdict
sampling, guardrails) lives here so it can be changed without touching code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

StepName = Literal["summarize", "notes", "claims", "factcheck"]
ClaimType = Literal["factual", "statistic", "prediction", "anecdote", "opinion"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Generation(_Strict):
    temperature: float = 0.0
    seed: int = 7
    num_ctx: int = 16384
    max_output_tokens: int = 1024
    timeout_s: int = 180


class Limits(_Strict):
    max_repairs: int = Field(2, ge=0, le=5, description="critic re-prompts per sub-agent")
    max_tool_calls: int = Field(3, ge=1, le=8, description="search calls per claim")
    max_llm_calls: int = Field(8, ge=2, le=30, description="ADK RunConfig cap per agent run")
    claim_budget: int = Field(8, ge=1, le=30, description="max claims fact-checked per episode")


class Retrieval(_Strict):
    method: Literal["bm25", "tfidf"] = "bm25"
    k: int = Field(3, ge=1, le=10)
    max_evidence: int = Field(6, ge=1, le=12, description="evidence passages shown to the judge")


class FactCheck(_Strict):
    verdict_samples: int = Field(3, ge=1, le=7)
    sample_temperature: float = Field(0.7, ge=0.0, le=1.5)
    unverifiable_types: list[ClaimType] = ["prediction", "anecdote"]
    drop_types: list[ClaimType] = ["opinion"]
    web_search: bool = True
    backstop_search: bool = Field(True, description="also BM25-search the claim text itself, so recall "
                                  "does not depend on the model's query wording")
    escalate_to_web: bool = Field(True, description="if KB evidence leaves a claim ❓ and the plan had web "
                                  "search off, search the web for that claim and judge again")
    numeric_check: bool = Field(True, description="downgrade 'supported' to ⚠ when the claim's numbers match none "
                                "in the cited evidence")
    web_fail_rate: float = Field(0.0, ge=0.0, le=1.0, description="mock search fault injection")
    unverifiable_confidence_cap: float = 0.5
    rule_unverifiable_confidence: float = 0.4


class Summary(_Strict):
    min_words: int = 200
    max_words: int = 300


class Notes(_Strict):
    takeaways: int = 5
    quotes_min: int = 3
    quotes_max: int = 5
    tags_min: int = 3
    tags_max: int = 8
    quote_candidates: int = 15
    quote_min_words: int = 8
    quote_max_words: int = 60


class Claims(_Strict):
    candidate_max: int = 25
    drop_show_meta: bool = Field(True, description="drop claims about the show itself that name the host/guests "
                                 "('X is hosting the podcast', 'X thanked listeners')")


class Prompts(_Strict):
    planner: str
    summarizer: str
    notes: str
    claims: str
    researcher: str
    judge: str


class ToolDescriptions(_Strict):
    search_kb: str
    search_web: str


class HarnessSpec(_Strict):
    version: str
    model: str = "ollama_chat/gemma4:e2b"
    enabled_steps: list[StepName] = ["summarize", "notes", "claims", "factcheck"]
    generation: Generation = Generation()
    limits: Limits = Limits()
    retrieval: Retrieval = Retrieval()
    factcheck: FactCheck = FactCheck()
    summary: Summary = Summary()
    notes: Notes = Notes()
    claims: Claims = Claims()
    prompts: Prompts
    tools: ToolDescriptions
    guardrails: list[str] = []
    glossary: dict[str, str] = Field(default_factory=dict, description="ASR fixes, applied case-insensitively")
    filler_words: list[str] = ["um", "uh", "erm", "hmm"]
    filler_phrases: list[str] = ["you know", "i mean", "like", "sort of", "kind of"]

    def instruction(self, agent: str, **values: object) -> str:
        """The system prompt for one sub-agent: its prompt (with {placeholders} filled) plus the guardrails."""
        # Only {word} placeholders are filled, so JSON examples in prompts are safe; unknown ones stay as-is.
        text = re.sub(r"\{(\w+)\}", lambda m: str(values.get(m[1], m[0])), getattr(self.prompts, agent).strip())
        if self.guardrails:
            text += "\n\nRules:\n" + "\n".join(f"- {g}" for g in self.guardrails)
        return text


def load(path: str | Path) -> HarnessSpec:
    return HarnessSpec.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def dump(spec: HarnessSpec, path: str | Path) -> None:
    data = spec.model_dump(mode="json")
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
