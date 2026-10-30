"""Evo² tree search over harness versions (implemented from the paper, arXiv 2610.13025; no code was released).

One evolver model plays two roles:
  Proposer  - given a parent harness, its fitness and its failures, proposes edits (K per parent = one GRPO group)
  Reflector - given the group's results, keeps the best edit or writes a revised edit list
The tree samples parents with w(h) = ε + |V̂(h) − ½|, rewards each edit by r = V̂(child) − V̂(parent),
and gives malformed edits r = −0.1. Every proposer/reflector call is logged to trajectories.jsonl,
which train_evolver.py turns into SFT and GRPO data. Fitness V̂ is podcast_agent.evaluate on search episodes.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field
from rich.console import Console

from podcast_agent import harness, trace
from podcast_agent.agent_factory import AgentFactory, run
from podcast_agent.config import Settings
from podcast_agent.evaluate import EpisodeScore, evaluate_files, split_files
from podcast_agent.harness import HarnessSpec

from . import genome

EPSILON = 0.1
FITNESS_EPISODES = ["s01", "s03", "s05", "s09"]
RUNS_DIR = Path("evo/runs")
log = Console(highlight=False)


# One object shape per op (a discriminated union): Ollama turns this into a grammar, so the evolver can only
# emit the fields that op uses, and enums mean it cannot misspell a prompt or parameter name.
class SetParam(BaseModel):
    op: Literal["set_param"]
    path: Literal[tuple(genome.PARAMS)]  # type: ignore[valid-type]
    value: str  # string-encoded; genome converts to the parameter's type


class ReplaceInPrompt(BaseModel):
    op: Literal["replace_in_prompt"]
    prompt: Literal[tuple(genome.PROMPTS)]  # type: ignore[valid-type]
    old: str
    new: str


class AppendToPrompt(BaseModel):
    op: Literal["append_to_prompt"]
    prompt: Literal[tuple(genome.PROMPTS)]  # type: ignore[valid-type]
    text: str


class ToggleStep(BaseModel):
    op: Literal["toggle_step"]
    step: Literal["claims", "factcheck"]
    enabled: bool


class AddGuardrail(BaseModel):
    op: Literal["add_guardrail"]
    text: str


ProposedEdit = Annotated[Union[SetParam, ReplaceInPrompt, AppendToPrompt, ToggleStep, AddGuardrail],
                         Field(discriminator="op")]
class ProposalOut(BaseModel):
    reasoning: str
    edits: list[ProposedEdit]


class ReflectionOut(BaseModel):
    reasoning: str
    decision: Literal["keep", "revise"]
    revised_edits: list[ProposedEdit]


PROPOSER = """You are the Proposer in an evolutionary search that improves an AI agent's harness: the prompts and
settings of a podcast summarizer and fact-checker that runs on a small 2B model. You see the harness, its fitness
score V (0..1) with a breakdown, and its failures on evaluation episodes. Propose 1-3 small edits likely to raise V.

Edit ops (JSON):
- {"op": "set_param", "path": "<parameter>", "value": "<new value>"}
- {"op": "replace_in_prompt", "prompt": "<planner|summarizer|notes|claims|researcher|judge>", "old": "<text copied exactly from that prompt>", "new": "<replacement>"}
- {"op": "append_to_prompt", "prompt": "<name>", "text": "<instruction to add>"}
- {"op": "toggle_step", "step": "<claims|factcheck>", "enabled": true}
- {"op": "add_guardrail", "text": "<rule for all agents>"}
Rules: 'old' must occur exactly once in the prompt. Values must be in range. Target the listed failures;
small precise changes beat rewrites. Write your reasoning first."""

REFLECTOR = """You are the Reflector in an evolutionary search over an AI agent's harness. Several edits were each
applied to the same parent harness and scored; reward = change in fitness V (positive is better, -0.1 = malformed).
Decide:
- keep: the results are good enough as they are (revised_edits = []).
- revise: write a better edit list for the parent, for example combine the helpful edits, drop harmful parts,
  or fix a malformed edit. Use the same edit JSON format.
Write your reasoning first."""


@dataclass
class Node:
    id: str
    parent: str | None
    spec: HarnessSpec
    score: float
    metrics: dict[str, float]
    failures: list[str]
    edits: list[dict] = field(default_factory=list)
    reward: float = 0.0
    depth: int = 0


class Evo:
    def __init__(self, base: HarnessSpec, settings: Settings, round_dir: Path, group: int, evolver_model: str | None):
        self.base = base
        self.settings = settings
        self.round_dir = round_dir
        self.group = group
        evolver_settings = replace(settings, model_override=evolver_model or settings.model_override)
        self.factory = AgentFactory(base, evolver_settings)
        self.files = [f for f in split_files("search") if f.stem.split("_")[0] in FITNESS_EPISODES]
        self.nodes: dict[str, Node] = {}
        self.traj = (round_dir / "trajectories.jsonl").open("a", encoding="utf-8")
        self.calls = 0

    # ---------------------------------------------------------------- fitness

    async def fitness(self, spec: HarnessSpec) -> tuple[float, dict[str, float], list[str]]:
        trace.console.quiet = True  # the pipeline's own console output would drown the search log
        try:
            scores = await evaluate_files(spec, self.settings, self.files, write=False)
        finally:
            trace.console.quiet = False
        mean = round(statistics.mean(s.score for s in scores), 4)
        metrics = {k: round(statistics.mean(s.metrics[k] for s in scores), 3) for k in scores[0].metrics}
        return mean, metrics, failures(scores)

    async def add_node(self, spec: HarnessSpec, parent: Node | None, edits: list[dict]) -> Node:
        t0 = time.time()
        score, metrics, fails = await self.fitness(spec)
        node = Node(spec.version, parent.id if parent else None, spec, score, metrics, fails, edits,
                    round(score - parent.score, 4) if parent else 0.0, parent.depth + 1 if parent else 0)
        self.nodes[node.id] = node
        log.print(f"  node {node.id}: V̂={score:.3f}" + (f" (r={node.reward:+.3f})" if parent else "")
                  + f" [{time.time() - t0:.0f}s]  {metrics}")
        return node

    # ---------------------------------------------------------------- search loop

    def sample_parent(self, rng: random.Random) -> Node:
        nodes = list(self.nodes.values())
        weights = [EPSILON + abs(n.score - 0.5) for n in nodes]
        return rng.choices(nodes, weights=weights, k=1)[0]

    async def expand(self, parent: Node, it: int) -> None:
        group_id = f"{self.round_dir.name}-g{it}"
        user = self.proposer_message(parent)
        children: list[tuple[dict, float, Node | None]] = []
        for j in range(self.group):
            out, completion = await self.call("proposer", PROPOSER, user, ProposalOut, seed=1000 * it + j)
            edits = [e.model_dump(exclude_none=True) for e in out.edits] if out else []
            child, reward, error = await self.try_edits(parent, edits, f"{self.round_dir.name}-n{it}{'abcdefgh'[j]}")
            self.log_traj(group_id, "proposer", parent, child, PROPOSER, user, completion, reward, error)
            children.append(({"edits": edits, "error": error, "reasoning": out.reasoning if out else ""}, reward, child))

        # Reflector: keep, or revise into one more child.
        summary = "\n\n".join(
            f"Edit set {i + 1}: reward {r:+.3f}" + (f" (malformed: {c['error']})" if c["error"] else "")
            + f"\n{json.dumps(c['edits'], ensure_ascii=False)}" for i, (c, r, _) in enumerate(children))
        r_user = f"Parent fitness V = {parent.score:.3f}\nParent failures:\n" + "\n".join(parent.failures[:8]) \
                 + f"\n\nResults:\n{summary}\n\n{genome.knobs(parent.spec)}"
        out, completion = await self.call("reflector", REFLECTOR, r_user, ReflectionOut, seed=7000 + it)
        if out and out.decision == "revise" and out.revised_edits:
            edits = [e.model_dump(exclude_none=True) for e in out.revised_edits]
            child, reward, error = await self.try_edits(parent, edits, f"{self.round_dir.name}-n{it}r")
            best_sibling = max(r for _, r, _ in children)
            # Reflector reward: did revising beat the best original proposal?
            self.log_traj(group_id, "reflector", parent, child, REFLECTOR, r_user, completion,
                          reward - best_sibling if error is None else genome.MALFORMED_REWARD, error)
        else:
            self.log_traj(group_id, "reflector", parent, None, REFLECTOR, r_user, completion, 0.0, None)

    async def try_edits(self, parent: Node, edits: list[dict], version: str) -> tuple[Node | None, float, str | None]:
        try:
            spec = genome.apply(parent.spec, edits, version)
        except genome.EditError as err:
            log.print(f"  [red]malformed[/] {version}: {err}")
            return None, genome.MALFORMED_REWARD, str(err)
        log.print(f"  try {version}: {genome.diff(parent.spec, spec)}")
        child = await self.add_node(spec, parent, edits)
        return child, child.reward, None

    async def call(self, role: str, system: str, user: str, schema: type[BaseModel], seed: int):
        agent = self.factory._agent(role, system, schema, temperature=0.9, seed=seed)
        self.calls += 1
        try:
            raw, _ = await run(agent, user, max_llm_calls=3)
            out = schema.model_validate(raw)
            return out, json.dumps(raw, ensure_ascii=False)
        except Exception as err:
            log.print(f"  [red]{role} failed[/]: {type(err).__name__}: {str(err)[:150]}")
            return None, ""

    def proposer_message(self, parent: Node) -> str:
        lineage, node = [], parent
        while node.parent:
            lineage.append(f"- {json.dumps(node.edits, ensure_ascii=False)[:300]} -> reward {node.reward:+.3f}")
            node = self.nodes[node.parent]
        history = "\n".join(lineage[:5]) or "- (this is the base harness)"
        return (f"Fitness V = {parent.score:.3f}\nBreakdown: {json.dumps(parent.metrics)}\n\n"
                f"Failures on evaluation episodes:\n" + "\n".join(parent.failures[:12])
                + f"\n\nEdits that led to this harness:\n{history}\n\nCurrent harness:\n{genome.knobs(parent.spec)}")

    def log_traj(self, group_id: str, role: str, parent: Node, child: Node | None, system: str, user: str,
                 completion: str, reward: float, error: str | None) -> None:
        self.traj.write(json.dumps({
            "round": self.round_dir.name, "group_id": group_id, "role": role, "parent_id": parent.id,
            "child_id": child.id if child else None, "parent_score": parent.score,
            "child_score": child.score if child else None, "reward": round(reward, 4), "valid": error is None,
            "error": error, "system": system, "prompt": user, "completion": completion,
        }, ensure_ascii=False) + "\n")
        self.traj.flush()


def failures(scores: list[EpisodeScore]) -> list[str]:
    """Turn eval details into short, actionable failure lines for the Proposer."""
    out = []
    for s in scores:
        for c in s.details["claims"]:
            if c["pred_status"] is None:
                out.append(f"{s.episode_id}: claim not extracted: \"{c['gold']}\" (gold {c['gold_verdict']})")
            elif c["pred_status"] != c["gold_verdict"]:
                out.append(f"{s.episode_id}: wrong verdict {c['pred_status']} (gold {c['gold_verdict']}): \"{c['pred']}\"")
        if s.metrics["keypoint_coverage"] < 1:
            out.append(f"{s.episode_id}: summary missed {1 - s.metrics['keypoint_coverage']:.0%} of key points")
        if s.metrics["evidence_recall"] < 1:
            out.append(f"{s.episode_id}: cited evidence missed the gold source for some claims")
        out += [f"{s.episode_id}: structure: {p}" for p in s.details["problems"]]
        if s.details["extra_predicted_claims"]:
            out.append(f"{s.episode_id}: {s.details['extra_predicted_claims']} extra claims not in gold")
    return out


async def run_search(harness_path: Path, rounds: int = 1, iterations: int = 6, group: int = 4,
                     evolver_model: str | None = None, seed: int = 0) -> Node:
    base = harness.load(harness_path)
    settings = Settings()
    rng = random.Random(seed)
    best: Node | None = None
    for r in range(1, rounds + 1):
        round_dir = RUNS_DIR / f"round{r}"
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "trajectories.jsonl").unlink(missing_ok=True)
        evo = Evo(base, settings, round_dir, group, evolver_model)
        log.rule(f"Evo² round {r}: {iterations} expansions × {group} proposals on {FITNESS_EPISODES}")
        root = await evo.add_node(base.model_copy(update={"version": f"round{r}-root"}), None, [])
        for it in range(1, iterations + 1):
            parent = evo.sample_parent(rng)
            log.print(f"\n[bold]expansion {it}/{iterations}[/] parent {parent.id} (V̂={parent.score:.3f})")
            await evo.expand(parent, it)
        evo.traj.close()
        best = max(evo.nodes.values(), key=lambda n: n.score)
        tree = [{"id": n.id, "parent": n.parent, "score": n.score, "reward": n.reward, "depth": n.depth,
                 "metrics": n.metrics, "edits": n.edits} for n in evo.nodes.values()]
        (round_dir / "tree.json").write_text(json.dumps(tree, indent=1, ensure_ascii=False), encoding="utf-8")
        log.rule(f"round {r}: root V̂={root.score:.3f} → best {best.id} V̂={best.score:.3f} "
                 f"({len(evo.nodes)} nodes, {evo.calls} evolver calls)")
        if best.score > root.score:
            harness.dump(best.spec.model_copy(update={"version": f"evolved-r{r}"}), "harness/evolved.yaml")
            log.print(f"wrote harness/evolved.yaml from {best.id}: {genome.diff(base, best.spec)}")
        base = best.spec
    return best
