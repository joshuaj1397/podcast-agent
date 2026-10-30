"""Train the evolver (Proposer + Reflector, one model) from Evo² search trajectories ("EvoTIDE": SFT then GRPO).

  SFT  - warm start on edits that improved fitness (reward > 0), both roles, plus valid edits rejection-sampled
         from the base evolver on TRAIN parents (evo/sample.py; weight 0.5: they are well-formed, not proven better)
  GRPO - group-relative advantages within each proposal group (K proposals for the same parent),
         so the model learns which of its own edits beat its alternatives, and that malformed edits lose

Usage (training venv, Ollama models unloaded):
  .venv-train/Scripts/python -m evo.train_evolver --runs evo/runs --out evo/checkpoints/evolver-r1
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sys

sys.path.insert(0, "src")  # podcast_agent (for evo.genome) without installing the agent in the training venv
from evo.genome import normalize_edit  # noqa: E402
from evo.lora import Example, group_advantages, grpo, load_model, save, sft  # noqa: E402


def load_rows(runs: Path) -> list[dict]:
    rows = []
    for f in sorted(runs.glob("*/trajectories.jsonl")):
        rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["completion"]]


def normalized(completion: str) -> str:
    """Rewrite logged completions into the current (union) edit schema; the meaning is unchanged."""
    data = json.loads(completion)
    for key in ("edits", "revised_edits"):
        if key in data:
            data[key] = [normalize_edit(e) for e in data[key]]
    return json.dumps(data, ensure_ascii=False, indent=2)  # the layout Ollama's JSON mode produces


def build_datasets(rows: list[dict], sampled: list[dict]) -> tuple[list[Example], list[Example]]:
    rows = [{**r, "completion": normalized(r["completion"])} for r in rows]
    sampled = [{**r, "completion": normalized(r["completion"])} for r in sampled if r["completion"]]
    sft_set = [Example(r["system"], r["prompt"], r["completion"]) for r in rows if r["valid"] and r["reward"] > 0]
    sft_set += [Example(r["system"], r["prompt"], r["completion"], weight=0.5)
                for r in sampled if r["valid"] and r["split"] == "train"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["role"] == "proposer":
            groups[r["group_id"]].append(r)
    grpo_set = []
    for gid, members in groups.items():
        if len(members) < 2:
            continue
        for r, adv in zip(members, group_advantages(members)):
            grpo_set.append(Example(r["system"], r["prompt"], r["completion"], weight=round(adv, 3), group=gid))
    return sft_set, grpo_set


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=Path("evo/runs"))
    ap.add_argument("--out", type=Path, default=Path("evo/checkpoints/evolver-r1"))
    ap.add_argument("--sft-epochs", type=int, default=3)
    ap.add_argument("--grpo-epochs", type=int, default=1)
    ap.add_argument("--max-len", type=int, default=4096)
    ap.add_argument("--samples", type=Path, default=Path("evo/data/proposals_train_base.jsonl"))
    args = ap.parse_args()

    rows = load_rows(args.runs)
    sampled = ([json.loads(line) for line in args.samples.read_text(encoding="utf-8").splitlines() if line.strip()]
               if args.samples.exists() else [])
    sft_set, grpo_set = build_datasets(rows, sampled)
    stats = {"trajectories": len(rows), "sampled_valid": sum(r["valid"] for r in sampled), "sft_examples": len(sft_set),
             "grpo_examples": sum(e.weight != 0 for e in grpo_set),
             "groups": len({e.group for e in grpo_set}),
             "valid_rate": round(sum(r["valid"] for r in rows) / max(1, len(rows)), 3)}
    print(json.dumps(stats))
    model, tok = load_model()
    history = {"stats": stats}
    if sft_set:
        history["sft"] = sft(model, tok, sft_set, epochs=args.sft_epochs, max_len=args.max_len)
    history["grpo"] = grpo(model, tok, grpo_set, epochs=args.grpo_epochs, max_len=args.max_len)
    save(model, args.out, history)


if __name__ == "__main__":
    main()
