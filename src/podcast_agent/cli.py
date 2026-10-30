"""Command line: podcast-agent run | eval | kb-search | evo."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import typer

from . import harness
from .config import Settings
from .trace import console

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Podcast summarizer, show-notes and fact-check agent.")
evo_app = typer.Typer(no_args_is_help=True, help="Evo²: evolve the harness (and model weights).")
app.add_typer(evo_app, name="evo")

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles: print ✅ ⚠ ❓ instead of crashing
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("LITELLM_LOG", "ERROR")

HARNESS = typer.Option(Path("harness/base.yaml"), "--harness", "-H", help="Harness spec (YAML).")


def episode_files(paths: list[Path], episode: str | None = None) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        files += sorted(f for f in p.iterdir() if f.suffix in {".json", ".txt"}) if p.is_dir() else [p]
    if episode:
        files = [f for f in files if f.stem.split("_")[0] == episode or f.stem == episode]
    return files


@app.command("run")
def run_cmd(
    paths: list[Path] = typer.Argument(..., help="Transcript files or directories."),
    harness_path: Path = HARNESS,
    only: Optional[list[str]] = typer.Option(None, "--only", help="Run only these steps (summarize, notes, claims, factcheck)."),
    episode: Optional[str] = typer.Option(None, "--episode", "-e", help="Only this episode id, e.g. ep004."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output directory (default: out/)."),
    model: Optional[str] = typer.Option(None, "--model", help="Override the model, e.g. ollama_chat/gemma4:e4b."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the LLM response cache."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw model output too."),
) -> None:
    """Summarize, extract notes and fact-check each transcript; writes out/<episode>/report.{json,md} + trace.jsonl."""
    from .pipeline import Pipeline

    settings = Settings()
    settings = replace(settings, use_cache=settings.use_cache and not no_cache,
                       out_dir=out or settings.out_dir, model_override=model or settings.model_override)
    spec = harness.load(harness_path)
    pipeline = Pipeline(spec, settings, verbose=verbose)
    files = episode_files(paths, episode)
    if not files:
        raise typer.BadParameter("no .json/.txt transcripts found")

    async def main() -> None:
        for f in files:
            report = await pipeline.run_file(f, only=set(only) if only else None)
            counts = {s: sum(fc.status == s for fc in report.fact_checks) for s in ("verified", "inaccurate", "unverifiable")}
            console.print(f"[bold]{report.episode_id}[/] done in {report.stats.get('seconds')}s · "
                          f"✅ {counts['verified']} ⚠ {counts['inaccurate']} ❓ {counts['unverifiable']}"
                          + (f" · degraded: {len(report.degraded)}" if report.degraded else ""))

    asyncio.run(main())


@app.command("eval")
def eval_cmd(
    harness_path: Path = HARNESS,
    split: str = typer.Option("heldout", help="heldout | search | all"),
    results: Path = typer.Option(Path("eval/RESULTS.md"), help="Where to write the results table."),
    model: Optional[str] = typer.Option(None, "--model"),
    out: Optional[Path] = typer.Option(None, "--out", help="Where reports go (default: out/)."),
) -> None:
    """Run the agent on a data split and score it against eval/gold (also Evo²'s fitness function)."""
    from .evaluate import evaluate_split, retrieval_report, write_results

    settings = Settings()
    settings = replace(settings, model_override=model or settings.model_override, out_dir=out or settings.out_dir)
    spec = harness.load(harness_path)
    scores = asyncio.run(evaluate_split(spec, settings, split))
    write_results(results, spec, scores, retrieval_report(settings, split), settings.model_override or None)


@app.command("kb-search")
def kb_search_cmd(
    query: str,
    k: int = typer.Option(5, "-k"),
    method: str = typer.Option("bm25", help="bm25 | tfidf"),
    kind: str = typer.Option("all", help="kb | web | all"),
) -> None:
    """Search the knowledge base / mock web corpus the way the research agent does."""
    from .retrieval import get_retriever

    retriever = get_retriever(str(Settings().kb_dir))
    for doc, score in getattr(retriever, kind).search(query, k, method):
        console.print(f"[bold]{doc.id}[/] {score:6.2f}  {doc.title} [dim]({doc.source}, {doc.date}, {doc.reliability})[/]")
        console.print(f"        {doc.text[:200]}")


@app.command("worker")
def worker_cmd(once: bool = typer.Option(False, "--once", help="Process one poll and exit.")) -> None:
    """Cloud worker: consume S3 upload events from SQS (see docs/deployment.md). Needs `uv sync --extra aws`."""
    from .worker import Worker

    Worker().run(once=once)


@evo_app.command("run")
def evo_run_cmd(
    harness_path: Path = HARNESS,
    rounds: int = typer.Option(1, help="Search rounds."),
    iterations: int = typer.Option(6, help="Tree-search expansions per round."),
    group: int = typer.Option(4, help="Proposals per expansion (GRPO group size)."),
    evolver_model: Optional[str] = typer.Option(None, help="Evolver model (default: same as the harness model)."),
    first_round: int = typer.Option(1, help="Number of the first round (its directory under evo/runs/)."),
    episodes: str = typer.Option("s01,s03,s05,s09", help="Search episodes used as fitness, comma-separated."),
    out: Path = typer.Option(Path("harness/evolved.yaml"), "--out", help="Where the best harness is written."),
) -> None:
    """Evo² tree search over harness edits; writes evo/runs/<round>/trajectories.jsonl and the best harness."""
    sys.path.insert(0, str(Path.cwd()))
    from evo.search import run_search

    asyncio.run(run_search(harness_path, rounds=rounds, iterations=iterations, group=group, evolver_model=evolver_model,
                           first_round=first_round, episodes=episodes.split(","), out=out))


if __name__ == "__main__":
    app()
