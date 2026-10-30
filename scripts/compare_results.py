"""Build eval/RESULTS.md from eval/results/*.{json,md}: every configuration, plus paired comparisons with
bootstrap 95% confidence intervals (episodes for V̂, individual gold claims for verdict accuracy).

  uv run python scripts/compare_results.py
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

RESULTS = Path("eval/results")
COLS = ["verdict_accuracy", "evidence_recall", "quote_validity", "keypoint_coverage", "structure", "claim_recall"]
COMPARISONS = [  # (label, candidate, reference)
    ("Numeric check (held-out)", "heldout_base", "heldout_base_no_numeric"),
    ("Numeric check (search split)", "search_base", "search_base_no_numeric"),
    ("Evo² round-1 harness vs base", "heldout_evolved", "heldout_base"),
    ("Task-model LoRA vs q8 control, base harness", "heldout_base_exec-r1", "heldout_base_q8-control"),
    ("Task-model LoRA vs q8 control, evolved harness", "heldout_evolved_exec-r1", "heldout_evolved_q8-control"),
    ("Gemma 4 E4B vs E2B, base harness (held-out)", "heldout_base_e4b", "heldout_base"),
    ("Gemma 4 E4B vs E2B, base harness (search split)", "search_base_e4b", "search_base"),
    ("Evo² round-2 harness vs base, both on E4B (held-out)", "heldout_evolved-r2_e4b", "heldout_base_e4b"),
    ("E4B + round-2 harness vs E2B + base (held-out)", "heldout_evolved-r2_e4b", "heldout_base"),
    ("**Round-2 harness vs base, both on E2B (held-out)**", "heldout_evolved-r2_e2b", "heldout_base"),
    ("Round-2 harness vs base, both on E2B (search split)", "search_evolved-r2_e2b", "search_base"),
]
LABELS = {
    "heldout_base_no_numeric": "Base harness, numeric check off",
    "heldout_base": "**Base harness (default)**",
    "heldout_evolved": "Evo² round-1 harness",
    "heldout_base_q8-control": "Base harness · q8 base control",
    "heldout_base_exec-r1": "Base harness · q8 task-model LoRA",
    "heldout_evolved_q8-control": "Evo² harness · q8 base control",
    "heldout_evolved_exec-r1": "Evo² harness · q8 task-model LoRA",
    "heldout_evolved-r2_e2b": "**Evo² round-2 harness** (`harness/evolved_r2.yaml`)",
    "heldout_base_e4b": "Base harness · Gemma 4 E4B",
    "heldout_evolved-r2_e4b": "Evo² round-2 harness · Gemma 4 E4B",
    "search_base_no_numeric": "Search split · base, numeric check off",
    "search_base": "Search split · base",
    "search_base_e4b": "Search split · base · Gemma 4 E4B",
    "search_evolved-r2_e4b": "Search split · round-2 harness · E4B (selection data)",
    "search_evolved-r2_e2b": "Search split · round-2 harness · E2B",
}


def load(name: str) -> dict | None:
    path = RESULTS / f"{name}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    md = (RESULTS / f"{name}.md").read_text(encoding="utf-8")
    data["claims"] = [line.startswith("- ✔") for line in md.splitlines() if re.match(r"- [✔✘] gold", line)]
    return data


def paired_ci(a: list[float], b: list[float], n: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    rng = random.Random(seed)
    diffs = [x - y for x, y in zip(a, b)]
    boots = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return sum(diffs) / len(diffs), boots[int(0.025 * n)], boots[int(0.975 * n)]


def validity() -> list[str]:
    rows = []
    for f in sorted(Path("evo/data").glob("proposals_eval_*.jsonl")):
        if "oldschema" in f.name:
            continue
        samples = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        model = samples[0]["model"] if samples else f.stem
        valid = sum(s["valid"] for s in samples)
        rows.append(f"| `{model}` | {valid}/{len(samples)} | {valid / max(1, len(samples)):.0%} |")
    return rows


SEEDS = {"base": "{split}_base", "evolved_r2": "{split}_evolved-r2_e2b"}  # seed 7 = the main runs


def seed_runs(split: str, harness: str) -> dict[int, dict]:
    runs = {}
    main = RESULTS / f"{SEEDS[harness].format(split=split)}.json"
    if main.exists():
        runs[7] = json.loads(main.read_text(encoding="utf-8"))
    for f in sorted((RESULTS / "seeds").glob(f"{split}_{harness}_s*.json")):
        runs[int(f.stem.rsplit("_s", 1)[1])] = json.loads(f.read_text(encoding="utf-8"))
    return runs


def seeds() -> list[str]:
    """Same configuration, different sampling seeds (generation.seed; the judge's samples use seed + k).
    Paired CI over episodes, each episode's score averaged over the seeds."""
    lines = []
    for split in ("heldout", "search"):
        runs = {h: seed_runs(split, h) for h in SEEDS}
        common = sorted(set(runs["base"]) & set(runs["evolved_r2"]))
        if len(common) < 2:
            continue
        for h, label in (("base", "base"), ("evolved_r2", "Evo² round 2")):
            vals = [runs[h][k]["mean_score"] for k in common]
            lines.append(f"| {split} | {label} | " + " | ".join(f"{v:.3f}" for v in vals)
                         + f" | **{sum(vals) / len(vals):.3f}** |")
        eps = sorted(runs["base"][common[0]]["episodes"])
        avg = {h: [sum(runs[h][k]["episodes"][e]["score"] for k in common) / len(common) for e in eps] for h in SEEDS}
        d, lo, hi = paired_ci(avg["evolved_r2"], avg["base"])
        lines.append(f"| {split} | Δ (paired over {len(eps)} episodes) | | | | {d:+.3f} [{lo:+.3f}, {hi:+.3f}] |")
    if not lines:
        return []
    return ["## Seed robustness: base vs Evo² round-2 harness on Gemma 4 E2B", "",
            "| Split | Harness | seed 7 | seed 8 | seed 9 | mean |", "|---|---|---|---|---|---|", *lines, "",
            "The seed changes only the sampled calls: the judge's extra self-consistency samples (temperature 0.7). "
            "Greedy calls (planner, summarizer, notes, claims, researcher, first judge sample) are unaffected, so this "
            "is a lower bound on run-to-run noise. Seed 8 and 9 details: [`results/seeds/`](results/seeds/).", ""]


def main() -> None:
    data = {name: d for name in LABELS if (d := load(name))}
    lines = ["# Evaluation results", "",
             "All runs: Ollama, temperature 0, same code; Gemma 4 E2B unless the Model column says otherwise. Held-out = 5 episodes / 25 gold claims "
             "(never used for search or training); search split = 10 episodes used by Evo². Per-configuration "
             "details (every claim, per episode) are in [`results/`](results/).", "",
             "## Configurations", "",
             "| Configuration | Model | V̂ | " + " | ".join(c.replace("_", " ") for c in COLS) + " |",
             "|---|---|---|" + "---|" * len(COLS)]
    for name, label in LABELS.items():
        if name in data:
            d = data[name]
            model = d["model"].replace("ollama_chat/", "")
            lines.append(f"| [{label}](results/{name}.md) | `{model}` | {d['mean_score']:.3f} | "
                         + " | ".join(f"{d['metrics'][c]:.2f}" for c in COLS) + " |")
    lines += ["", "## Paired comparisons (95% bootstrap CI)", "",
              "| Comparison | ΔV̂ (over episodes) | Δ verdict accuracy (over claims) |", "|---|---|---|"]
    for label, cand, ref in COMPARISONS:
        if cand not in data or ref not in data:
            continue
        eps = sorted(set(data[cand]["episodes"]) & set(data[ref]["episodes"]))
        v = paired_ci([data[cand]["episodes"][e]["score"] for e in eps], [data[ref]["episodes"][e]["score"] for e in eps])
        a, b = data[cand]["claims"], data[ref]["claims"]
        c = paired_ci([float(x) for x in a], [float(x) for x in b]) if len(a) == len(b) and a else (0, 0, 0)
        lines.append(f"| {label} | {v[0]:+.3f} [{v[1]:+.3f}, {v[2]:+.3f}] | "
                     f"{c[0]:+.3f} [{c[1]:+.3f}, {c[2]:+.3f}] ({len(a)} claims) |")
    lines += ["", "An interval that contains 0 means the difference is not distinguishable from noise at this "
              "sample size.", "", "## Evo² evolver: well-formed edit rate on held-out parent harnesses", "",
              "| Evolver model | Valid edit sets | Rate |", "|---|---|---|", *validity(), "",
              "Same proposer prompts (parents never used for evolver training), 8 samples each at temperature 1.0.", "",
              *seeds()]
    retrieval = data.get("search_base", {}).get("retrieval")
    if retrieval:
        lines += ["## Retrieval: BM25 vs TF-IDF (search split, no LLM)", "",
                  "| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |", "|---|---|---|---|---|"]
        for m, label in (("bm25", "BM25"), ("tfidf", "TF-IDF")):
            lines.append(f"| {label} | " + " | ".join(f"{retrieval.get(f'{m}_{q}_recall@{k}', 0):.3f}"
                                                      for q in ("claim", "turn") for k in (1, 3)) + " |")
    Path("eval/RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("wrote eval/RESULTS.md")


if __name__ == "__main__":
    main()
