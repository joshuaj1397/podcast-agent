"""The orchestrator: ingest -> plan -> run the planned steps -> critic -> render.

Each LLM step is an ADK LlmAgent (built by agent_factory from the harness). This file
is plain Python on purpose: the order of steps, the dependency rules and the
critic loop are all visible in one place and easy to change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from . import critic, factcheck, ingest, llm, render
from .agent_factory import AgentFactory, run
from .config import Settings
from .harness import HarnessSpec, StepName
from .models import Episode, FactCheckResult, Plan, Quote, Report, SummaryOut, Turn
from .retrieval import Retriever, get_retriever, rank_quotable, top_terms
from .tools import ToolGuard, make_tools
from .trace import Trace, open_trace

STEP_ORDER: list[StepName] = ["summarize", "notes", "claims", "factcheck"]
DEPENDS_ON: dict[str, list[str]] = {"factcheck": ["claims"]}


@dataclass
class EpisodeState:
    """Everything produced for one episode so far (the pipeline's working memory)."""

    ep: Episode
    candidates: list[Turn]
    ranked_turns: list[Turn]  # most distinctive turns first (TF-IDF): fallback material
    plan: Plan | None = None
    summary: str = ""
    takeaways: list[str] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    fact_checks: list[FactCheckResult] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, spec: HarnessSpec, settings: Settings, verbose: bool = False):
        self.spec = spec
        self.settings = settings
        self.verbose = verbose
        self.agents = AgentFactory(spec, settings)
        self.retriever: Retriever = get_retriever(str(settings.kb_dir))
        self.trace: Trace

    # ------------------------------------------------------------ entry point

    async def run_file(self, path: str | Path, only: set[str] | None = None, write: bool = True) -> Report:
        ep = ingest.load(path, self.spec)
        out_dir = self.settings.out_dir / ep.episode_id
        llm.STATS.clear()
        t0 = time.time()
        with open_trace(ep.episode_id, out_dir / "trace.jsonl" if write else None, self.verbose) as tr:
            self.trace = tr
            tr.header(ep.title)
            candidates = factcheck.claim_candidates(ep, self.spec)
            state = EpisodeState(ep, candidates, self._rank_turns(ep))
            state.plan = await self.plan(state)
            for step in self._steps_to_run(state.plan, only):
                await getattr(self, f"step_{step}")(state)
            report = self.assemble(state)
            report.stats = {"seconds": round(time.time() - t0, 1), **{k: float(v) for k, v in llm.STATS.items()}}
            for problem in critic.structural_problems(report, ep, self.spec):
                tr.warn(f"final check: {problem}")
            if write:
                render.write(report, out_dir)
                tr.result("render", f"wrote {out_dir}/report.json, report.md, trace.jsonl")
            tr.log("report", stats=report.stats, degraded=report.degraded)
        return report

    # ------------------------------------------------------------ planning

    async def plan(self, state: EpisodeState) -> Plan:
        tr, ep = self.trace, state.ep
        tr.step("plan", "planner agent decides steps and budgets")
        profile = ingest.profile(ep, len(state.candidates))
        message = "Episode profile:\n" + json.dumps(profile, indent=1)
        plan, problems = await self.run_structured("planner", self.agents.planner, message, Plan)
        if plan is None:
            plan = Plan(reasoning="default plan (planner failed)", steps=list(STEP_ORDER),
                        claim_budget=self.spec.limits.claim_budget, use_web_search=True, summary_focus="core themes")
            state.degraded.append(f"plan: {problems}")
        tr.thought("planner", plan.reasoning)
        plan, adjustments = self.validate_plan(plan, profile)
        tr.plan(plan, profile, adjustments)
        return plan

    def validate_plan(self, plan: Plan, profile: dict) -> tuple[Plan, list[str]]:
        """The planner decides; code enforces the invariants (required steps, dependencies, budget limits)."""
        notes: list[str] = []
        enabled = [s for s in STEP_ORDER if s in self.spec.enabled_steps]
        required = {"summarize", "notes"} | ({"claims", "factcheck"} if profile["claim_candidates"] else set())
        steps = [s for s in dict.fromkeys(plan.steps) if s in enabled]
        for s in enabled:
            if s in required and s not in steps:
                notes.append(f"planner omitted {s}; inserting")
                steps.insert(min(STEP_ORDER.index(s), len(steps)), s)
        if "factcheck" in steps and "claims" in steps and steps.index("factcheck") < steps.index("claims"):
            notes.append("planner put factcheck before claims; reordering")
            steps.remove("claims")
            steps.insert(steps.index("factcheck"), "claims")
        cap = self.spec.limits.claim_budget
        budget = min(max(plan.claim_budget, 1), cap)
        if budget != plan.claim_budget:
            notes.append(f"claim_budget {plan.claim_budget} clamped to {budget} (limit {cap})")
        web = plan.use_web_search and self.spec.factcheck.web_search
        return plan.model_copy(update={"steps": steps, "claim_budget": budget, "use_web_search": web}), notes

    def _steps_to_run(self, plan: Plan, only: set[str] | None) -> list[str]:
        if not only:
            return list(plan.steps)
        wanted = set(only) | {d for s in only for d in DEPENDS_ON.get(s, [])}
        return [s for s in plan.steps if s in wanted]

    # ------------------------------------------------------------ steps

    async def step_summarize(self, s: EpisodeState) -> None:
        self.trace.step("summarize", "summarizer agent")
        ep = s.ep
        message = (f"Title: {ep.title}\nHost: {ep.host}\nGuests: {', '.join(ep.guests)}\n"
                   f"Focus: {s.plan.summary_focus}\n\nTranscript:\n{ep.transcript()}")
        out, problems = await self.run_structured(
            "summarizer", self.agents.summarizer, message, SummaryOut,
            check=lambda o: critic.check_summary(o.summary, self.spec),
        )
        s.summary = out.summary.strip() if out else ""
        if out:
            self.trace.thought("summarizer", out.reasoning)
        if problems:
            s.summary = critic.fallback_summary(s.summary, ep, s.ranked_turns, self.spec)
            s.degraded.append(f"summary fallback ({problems[0]})")
            self.trace.warn("summary: using deterministic fallback")
        self.trace.result("summarizer", f"summary: {critic.word_count(s.summary)} words")

    async def step_notes(self, s: EpisodeState) -> None:
        self.trace.step("notes", "notes agent: takeaways, quotes, tags")
        ep, n = s.ep, self.spec.notes
        quotable = [t for t in s.ranked_turns
                    if n.quote_min_words <= critic.word_count(t.text) <= n.quote_max_words and not t.text.endswith("?")]
        quote_ids = sorted(t.id for t in quotable[: n.quote_candidates]) or [ep.turns[0].id]
        background = [d.text for d in self.retriever.kb.docs]
        tag_hints = [critic.normalize_tag(t) for t in top_terms([t.text for t in ep.turns], background)]
        message = (f"Title: {ep.title}\n\nTranscript:\n{ep.transcript()}\n\n"
                   f"Quote candidates (choose quote_ids only from these):\n{ep.transcript(set(quote_ids))}\n\n"
                   f"Keyword hints for tags: {', '.join(tag_hints)}")

        def check(o: Any) -> list[str]:
            return critic.check_notes(o.takeaways, o.quote_ids, [critic.normalize_tag(t) for t in o.tags], self.spec)

        out, problems = await self.run_structured("notes", lambda: self.agents.notes(quote_ids), message,
                                                  None, check=check)
        takeaways = [t.strip() for t in out.takeaways] if out else []
        chosen = list(dict.fromkeys(out.quote_ids)) if out else []
        tags = list(dict.fromkeys(critic.normalize_tag(t) for t in out.tags if critic.normalize_tag(t))) if out else []
        if out:
            self.trace.thought("notes", out.reasoning)
        if problems:
            takeaways, chosen, tags = critic.fallback_notes(takeaways, chosen, tags, quotable or s.ranked_turns,
                                                            tag_hints, self.spec)
            s.degraded.append(f"notes fallback ({problems[0]})")
            self.trace.warn("notes: topped up with deterministic fallback")
        s.takeaways, s.tags = takeaways, tags
        # Quotes are chosen by id; the text and timestamp are copied from the transcript, never generated.
        s.quotes = [Quote(turn_id=t.id, ts=t.ts, speaker=t.speaker, text=t.text) for t in map(ep.turn, chosen)]
        self.trace.result("notes", f"{len(takeaways)} takeaways, {len(s.quotes)} quotes, tags: {', '.join(tags)}")

    async def step_claims(self, s: EpisodeState) -> None:
        self.trace.step("claims", f"claims agent over {len(s.candidates)} candidate turns (regex pre-filter)")
        if not s.candidates:
            self.trace.result("claims", "no candidate turns; nothing to check")
            return
        ids = [t.id for t in s.candidates]
        message = (f"Transcript for context:\n{s.ep.transcript()}\n\n"
                   f"Candidate turns (use only these turn ids):\n{s.ep.transcript(set(ids))}")
        out, problems = await self.run_structured("claims", lambda: self.agents.claims(ids), message, None,
                                                  check=lambda o: [])
        if out is None:
            s.degraded.append(f"claims failed ({problems[0] if problems else 'no output'})")
            return
        self.trace.thought("claims", out.reasoning)
        claims, seen = [], set()
        for c in out.claims:
            key = (c.turn_id, c.claim.strip().lower())
            if c.claim.strip() and key not in seen:
                seen.add(key)
                claims.append(c.model_dump())
        dropped = [c for c in claims if c["type"] in self.spec.factcheck.drop_types]
        claims = [c for c in claims if c["type"] not in self.spec.factcheck.drop_types]
        # Checkable facts first, then predictions/anecdotes; keep transcript order within each group.
        claims.sort(key=lambda c: c["type"] in self.spec.factcheck.unverifiable_types)
        over = claims[s.plan.claim_budget:]
        s.claims = claims[: s.plan.claim_budget]
        for c in dropped:
            self.trace.log("claim_dropped", "claims", claim=c, reason="opinion")
        self.trace.result("claims", f"{len(s.claims)} claims to check ({len(dropped)} opinions dropped, "
                                    f"{len(over)} over budget)")
        for c in s.claims:
            self.trace.log("claim", "claims", **c)

    async def step_factcheck(self, s: EpisodeState) -> None:
        self.trace.step("factcheck", f"{len(s.claims)} claims: research agent (tools) -> judge agent (samples)")
        for i, claim in enumerate(s.claims, 1):
            turn = s.ep.turn(claim["turn_id"])
            self.trace.step(f"claim {i}/{len(s.claims)}", f"[{claim['type']}] {claim['claim']} ({turn.speaker} {turn.ts})")
            result = await self.check_claim(claim, turn, s)
            s.fact_checks.append(result)
            self.trace.result("factcheck", f"{result.symbol} {result.status} · {result.confidence:.2f} · "
                                           f"{result.verification}", result=result.model_dump())

    async def check_claim(self, claim: dict, turn: Turn, s: EpisodeState) -> FactCheckResult:
        fc = self.spec.factcheck
        if claim["type"] in fc.unverifiable_types:
            why = factcheck.RULE_REASON[claim["type"]]
            self.trace.thought("factcheck", f"rule: {why} -> ❓ without searching")
            return factcheck.rule_result(claim, turn, self.spec, why)

        # 1. Research: a ReAct loop where the model chooses queries and the guard enforces the budget.
        guard = ToolGuard(self.retriever, self.spec.limits.max_tool_calls)
        tools = make_tools(self.retriever, self.spec, use_web=s.plan.use_web_search)
        message = (f"Claim: {claim['claim']}\nSaid by {turn.speaker} at {turn.ts} in the podcast \"{s.ep.title}\" "
                   f"(recorded {s.ep.recorded or 'unknown'}).\nOriginal words: \"{turn.text}\"")
        try:
            _, text = await run(self.agents.researcher(tools, guard), message, self.spec.limits.max_llm_calls)
            if text:
                self.trace.thought("researcher", text)
        except Exception as e:  # LLM-call limit or model error: keep whatever evidence was found
            self.trace.warn(f"researcher stopped: {type(e).__name__}: {e}")
        if fc.backstop_search:
            for index in [self.retriever.kb] + ([self.retriever.web] if s.plan.use_web_search else []):
                for doc, _ in index.search(claim["claim"], self.spec.retrieval.k, self.spec.retrieval.method):
                    if doc.id not in guard.hits:
                        self.trace.log("backstop_hit", "factcheck", id=doc.id)
                    guard.add(doc)
        evidence = dict(list(guard.hits.items())[: self.spec.retrieval.max_evidence])
        if not evidence:
            return factcheck.rule_result(claim, turn, self.spec, "no evidence found", method="fallback")

        # 2. Judge: structured verdict over the evidence, sampled N times for self-consistency.
        ev_text = "\n".join(f"[{d.id}] {d.source}, {'as of' if d.kind == 'kb' else 'published'} {d.date}, "
                            f"reliability {d.reliability}: {d.title}. {d.text}" for d in evidence.values())
        judge_msg = (f"Claim: {claim['claim']}\nSaid by {turn.speaker}: \"{turn.text}\"\n"
                     f"Episode recorded: {s.ep.recorded or 'unknown'}\n\nEvidence:\n{ev_text}")
        samples = []
        for k in range(fc.verdict_samples):
            build = lambda k=k: self.agents.judge(list(evidence), self.settings.today.isoformat(),
                                                  s.ep.recorded or "unknown", k)
            out, _ = await self.run_structured("judge", build, judge_msg, None, check=lambda o: [], repairs=0,
                                               meta={"ts": turn.ts, "claim": claim["claim"], "sample": k})
            if out:
                samples.append(out.model_dump())
                if k == 0:
                    self.trace.thought("judge", f"{out.reasoning} -> {out.label} {out.evidence_ids}")
        if not samples:
            return factcheck.rule_result(claim, turn, self.spec, "judge failed", method="fallback")
        if len(samples) > 1:
            self.trace.observation("judge", f"samples: {[x['label'] for x in samples]}")
        return factcheck.judged_result(claim, turn, samples, evidence, guard.queries, self.spec)

    # ------------------------------------------------------------ helpers

    async def run_structured(self, name: str, build: Callable[[], Any], message: str,
                             schema: type[BaseModel] | None, check: Callable[[Any], list[str]] = lambda o: [],
                             repairs: int | None = None, meta: dict | None = None) -> tuple[Any, list[str]]:
        """Run an output_schema agent; on schema or critic problems, re-prompt with the problems (up to max_repairs).
        Returns (validated output or the last invalid one or None, remaining problems)."""
        repairs = self.spec.limits.max_repairs if repairs is None else repairs
        out, problems, prompt = None, ["no output"], message
        for attempt in range(repairs + 1):
            try:
                agent = build()
                model = schema or agent.output_schema
                raw, _ = await run(agent, prompt, self.spec.limits.max_llm_calls)
                candidate = model.model_validate(raw)
                problems = check(candidate)
                out = candidate
            except (ValidationError, TypeError, ValueError) as e:
                problems = [f"Your answer did not match the required JSON format: {str(e).splitlines()[0]}"]
            except Exception as e:
                problems = [f"{type(e).__name__}: {e}"]
            if not problems:
                self._record(name, agent, message, raw, meta)
                return out, []
            self.trace.warn(f"critic: {name} attempt {attempt + 1}: {'; '.join(problems)}")
            prompt = (f"{message}\n\nYour previous answer had these problems:\n"
                      + "\n".join(f"- {p}" for p in problems) + "\nFix them and answer again.")
        return out, problems

    def _record(self, name: str, agent: Any, message: str, raw: Any, meta: dict | None) -> None:
        """With PODCAST_RECORD=<file>, log outputs that passed the critic: fine-tuning data for the task model
        (rejection-sampling fine-tuning; evo/train_executor.py filters judge samples against gold labels)."""
        path = os.environ.get("PODCAST_RECORD")
        if not path:
            return
        row = {"agent": name, "episode_id": self.trace.episode_id, "harness": self.spec.version,
               "system": agent.instruction(None), "prompt": message, "output": raw, **(meta or {})}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _rank_turns(self, ep: Episode) -> list[Turn]:
        order = rank_quotable([t.text for t in ep.turns])
        return [ep.turns[i] for i in order]

    def assemble(self, s: EpisodeState) -> Report:
        ep = s.ep
        return Report(
            episode_id=ep.episode_id, title=ep.title, host=ep.host, guests=ep.guests, recorded=ep.recorded,
            source=ep.source, summary=s.summary, takeaways=s.takeaways, quotes=s.quotes, tags=s.tags,
            fact_checks=s.fact_checks, plan=s.plan, degraded=s.degraded, harness_version=self.spec.version,
            model="fake" if self.settings.fake_llm else (self.settings.model_override or self.spec.model),
        )
