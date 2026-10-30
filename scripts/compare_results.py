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
]
LABELS = {
    "heldout_base_no_numeric": "Base harness, numeric check off",
    "heldout_base": "**Base harness (default)**",
    "heldout_evolved": "Evo² round-1 harness",
    "heldout_base_q8-control": "Base harness · q8 base control",
    "heldout_base_exec-r1": "Base harness · q8 task-model LoRA",
    "heldout_evolved_q8-control": "Evo² harness · q8 base control",
    "heldout_evolved_exec-r1": "Evo² harness · q8 task-model LoRA",
    "search_base_no_numeric": "Search split · base, numeric check off",
    "search_base": "Search split · base",
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


def main() -> None:
    data = {name: d for name in LABELS if (d := load(name))}
    lines = ["# Evaluation results", "",
             "All runs: Gemma 4 E2B through Ollama, temperature 0, same code. Held-out = 5 episodes / 25 gold claims "
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
              "Same proposer prompts (parents never used for evolver training), 8 samples each at temperature 1.0.", ""]
    retrieval = data.get("search_base", {}).get("retrieval")
    if retrieval:
        lines += ["## Retrieval: BM25 vs TF-IDF (search split, no LLM)", "",
                  "| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |", "|---|---|---|---|---|"]
        for m, label in (("bm25", "BM25"), ("tfidf", "TF-IDF")):
            lines.append(f"| {label} | " + " | ".join(f"{retrieval.get(f'{m}_{q}_recall@{k}', 0):.3f}"
                                                      for q in ("claim", "turn") for k in (1, 3)) + " |")
    Path("eval/RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote eval/RESULTS.md")


if __name__ == "__main__":
    main()
