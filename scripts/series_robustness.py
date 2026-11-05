"""Build the series N times without the LLM response cache and report how often each gold check passes.

Ollama is not bit-for-bit deterministic even at temperature 0, so one passing run proves little. Only the series
steps run (the episode reports in out/<ep>/ are reused); each episode is added in its own process, as in production.

  uv run python scripts/series_robustness.py --runs 5 [--model ollama_chat/gemma4:e4b] [-H harness/base.yaml]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

from podcast_agent.series import Manifest, check, load_state


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--manifest", default="series/work_reimagined.yaml")
    ap.add_argument("--out-root", default=".cache/series_robustness")
    ap.add_argument("--model", default=None)
    ap.add_argument("-H", "--harness", default="harness/base.yaml")
    args = ap.parse_args()

    manifest = Manifest.load(args.manifest)
    gold = yaml.safe_load(Path(f"eval/gold/series_{manifest.series_id}.yaml").read_text(encoding="utf-8"))
    episodes = gold["episodes"]
    passed: Counter = Counter()
    totals, revises, sims = [], [], []
    for r in range(1, args.runs + 1):
        out = Path(args.out_root) / f"run{r}"
        shutil.rmtree(out, ignore_errors=True)
        for ep in episodes:
            shutil.copytree(Path("out") / ep, out / ep)
        for ep in episodes:
            cmd = [sys.executable, "-m", "podcast_agent.cli", "series", "add", ep, "--reuse-reports", "--out", str(out),
                   "-m", args.manifest, "-H", args.harness] + (["--model", args.model] if args.model else [])
            with open(out / f"console-{ep}.log", "w", encoding="utf-8") as log:
                subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True,
                               env={**os.environ, "PODCAST_NO_CACHE": "1", "PYTHONIOENCODING": "utf-8"})
        state = load_state(out, manifest.series_id)
        results = check(state, gold)
        failed = [name for name, ok, _ in results if not ok]
        for name, ok, _ in results:
            passed[name] += ok
        totals.append(len(results) - len(failed))
        revises.append(sum(a.op == "revise" for h in state.history for a in h.ops))
        sims += [h.similarity for h in state.history[1:]]
        print(f"run {r}: {totals[-1]}/{len(results)} checks · {revises[-1]} revise ops · {state.char_count:,} chars"
              + (f" · failed: {'; '.join(failed)}" if failed else ""), flush=True)

    n = args.runs
    print(f"\n{args.model or 'harness model'} · {n} uncached runs")
    print("| Check | Passed |\n|---|---|")
    for name, _, _ in results:
        print(f"| {name} | {passed[name]}/{n} |")
    print(f"| **all checks** | **{sum(t == len(results) for t in totals)}/{n}** |")
    print(f"\nrevise ops per run: {revises} · similarity between versions: "
          f"{min(sims):.2f}–{max(sims):.2f} (mean {sum(sims) / len(sims):.2f})")


if __name__ == "__main__":
    main()
