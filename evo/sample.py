"""Sample Proposer outputs offline (no fitness evaluation): SFT warm-start data, and the edit-validity metric.

Parent harnesses are rebuilt by replaying the search tree's edits from the root, so each logged proposer
prompt can be paired with the exact spec its edits must apply to. Parents are split by id into a train
set (sampled for SFT data) and an eval set (used to compare evolver checkpoints on edit validity).

  uv run python -m evo.sample --split train --n 8                          # base evolver -> SFT data
  uv run python -m evo.sample --split eval --n 4 --model ollama_chat/podcast-evolver:r1
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path

from podcast_agent import harness
from podcast_agent.agent_factory import AgentFactory, run
from podcast_agent.config import Settings
from podcast_agent.harness import HarnessSpec

from . import genome
from .genome import parent_prompts
from .search import PROPOSER, ProposalOut

ROUND = Path("evo/runs/round1")


def node_specs(round_dir: Path, base: HarnessSpec) -> dict[str, HarnessSpec]:
    tree = json.loads((round_dir / "tree.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in tree}
    specs: dict[str, HarnessSpec] = {}

    def spec_of(node_id: str) -> HarnessSpec:
        if node_id not in specs:
            n = by_id[node_id]
            specs[node_id] = (base if n["parent"] is None
                              else genome.apply(spec_of(n["parent"]), n["edits"], node_id, min_words=0))
        return specs[node_id]

    for node_id in by_id:
        spec_of(node_id)
    return specs


async def sample(model: str | None, split: str, n: int, out: Path, seed0: int) -> dict:
    base = harness.load("harness/base.yaml")
    specs = node_specs(ROUND, base)
    settings = replace(Settings(), model_override=model or "")
    factory = AgentFactory(base, settings)
    rows, valid = [], 0
    for parent_id, prompt in parent_prompts(ROUND, split):
        for j in range(n):
            agent = factory._agent("proposer", PROPOSER, ProposalOut, temperature=1.0, seed=seed0 + 97 * j)
            error, completion = None, ""
            try:
                raw, _ = await run(agent, prompt, max_llm_calls=3)
                completion = json.dumps(raw, ensure_ascii=False)
                edits = [e.model_dump(exclude_none=True) for e in ProposalOut.model_validate(raw).edits]
                genome.apply(specs[parent_id], edits, "sample")
            except Exception as err:  # malformed JSON or malformed edit
                error = f"{type(err).__name__}: {str(err)[:200]}"
            valid += error is None
            rows.append({"parent_id": parent_id, "split": split, "model": model or base.model, "valid": error is None,
                         "error": error, "system": PROPOSER, "prompt": prompt, "completion": completion})
            print(f"{parent_id} #{j}: {'valid' if error is None else error[:100]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"model": model or base.model, "split": split, "samples": len(rows),
               "valid_rate": round(valid / max(1, len(rows)), 3)}
    print(json.dumps(summary))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--split", choices=["train", "eval"], default="train")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    tag = (args.model or "base").split("/")[-1].replace(":", "-")
    out = args.out or Path(f"evo/data/proposals_{args.split}_{tag}.jsonl")
    asyncio.run(sample(args.model, args.split, args.n, out, args.seed))


if __name__ == "__main__":
    main()
