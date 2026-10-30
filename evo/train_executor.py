"""Train the task model ("executor") by rejection-sampling fine-tuning on the best harness's own outputs.

Data comes from running the agent on the SEARCH split with PODCAST_RECORD=<file>: every sub-agent output
that passed the critic is logged. We then keep only samples that are also right according to gold labels:
  judge      - the verdict maps to the gold ✅/⚠/❓ of the matching claim
  claims     - at least 60% of the episode's gold claim turns were extracted
  summarizer - covers at least 75% of the gold summary key points
  planner / notes - kept (they passed the structural critic)
Held-out episodes are never used for training.

Usage:
  PODCAST_RECORD=evo/data/executor_samples.jsonl uv run podcast-agent run data/search -H harness/evolved.yaml
  .venv-train/Scripts/python -m evo.train_executor --samples evo/data/executor_samples.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

STATUS_OF_LABEL = {"supported": "verified", "contradicted": "inaccurate", "outdated": "inaccurate",
                   "insufficient": "unverifiable"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def gold_for(episode_id: str) -> dict:
    path = Path("eval/gold") / f"{episode_id}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def keep(row: dict, gold: dict, turn_ts: dict[str, str]) -> bool:
    out = row["output"]
    if row["agent"] == "judge":
        same_turn = [g for g in gold.get("claims", []) if g["ts"] == row.get("ts")]
        if not same_turn:
            return False
        g = max(same_turn, key=lambda g: len(_words(g["text"]) & _words(row.get("claim", ""))))
        return STATUS_OF_LABEL[out["label"]] == g["verdict"]
    if row["agent"] == "claims":
        gold_ts = {g["ts"] for g in gold.get("claims", [])}
        got = {turn_ts.get(c["turn_id"]) for c in out["claims"]}
        return not gold_ts or len(gold_ts & got) / len(gold_ts) >= 0.6
    if row["agent"] == "summarizer":
        points = gold.get("summary_key_points", [])
        text = out["summary"].lower()
        return not points or sum(any(a in text for a in alts) for alts in points) / len(points) >= 0.75
    return True


def turn_timestamps(episode_id: str) -> dict[str, str]:
    """Turn id -> timestamp, via the agent's own ingest (so ids match what the model saw)."""
    sys.path.insert(0, "src")
    from podcast_agent import harness, ingest

    spec = harness.load("harness/base.yaml")
    for f in Path("data/search").iterdir():
        if f.stem.split("_")[0] == episode_id:
            return {t.id: t.ts for t in ingest.load(f, spec).turns}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=Path, default=Path("evo/data/executor_samples.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("evo/checkpoints/executor-r1"))
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=4096)
    args = ap.parse_args()

    rows = [json.loads(line) for line in args.samples.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if not r["episode_id"].startswith("ep")]  # never train on held-out episodes
    cache: dict[str, tuple[dict, dict]] = {}
    kept = []
    for r in rows:
        if r["episode_id"] not in cache:
            cache[r["episode_id"]] = (gold_for(r["episode_id"]), turn_timestamps(r["episode_id"]))
        if keep(r, *cache[r["episode_id"]]):
            kept.append(r)
    stats = {"recorded": len(rows), "kept": len(kept), "by_agent": dict(Counter(r["agent"] for r in kept)),
             "rejected_by_agent": dict(Counter(r["agent"] for r in rows) - Counter(r["agent"] for r in kept))}
    print(json.dumps(stats))
    from evo.lora import Example, load_model, save, sft  # torch lives only in the training venv

    examples = [Example(r["system"], r["prompt"], json.dumps(r["output"], ensure_ascii=False, indent=2)) for r in kept]
    model, tok = load_model()
    history = {"stats": stats, "sft": sft(model, tok, examples, epochs=args.epochs, max_len=args.max_len)}
    save(model, args.out, history)


if __name__ == "__main__":
    main()
