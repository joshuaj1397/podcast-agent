"""Score agent reports against eval/gold. The weighted score V̂ ∈ [0, 1] is also Evo²'s fitness function.

V̂ = 0.40·verdict_accuracy + 0.15·evidence_recall + 0.10·quote_validity + 0.20·keypoint_coverage + 0.15·structure
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from . import critic, ingest
from .config import Settings
from .harness import HarnessSpec
from .models import Report
from .retrieval import get_retriever, tokenize

GOLD_DIR = Path("eval/gold")
SPLITS = {"heldout": ["data/heldout"], "search": ["data/search"], "all": ["data/heldout", "data/search"]}
WEIGHTS = {"verdict_accuracy": 0.40, "evidence_recall": 0.15, "quote_validity": 0.10, "keypoint_coverage": 0.20,
           "structure": 0.15}


@dataclass
class EpisodeScore:
    episode_id: str
    metrics: dict[str, float]
    details: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        return round(sum(w * self.metrics[k] for k, w in WEIGHTS.items()), 4)


def load_gold(episode_id: str) -> dict | None:
    path = GOLD_DIR / f"{episode_id}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


def split_files(split: str) -> list[Path]:
    files = []
    for d in SPLITS[split]:
        files += sorted(p for p in Path(d).iterdir() if p.suffix in {".json", ".txt"})
    return files


def _overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return len(ta & tb) / max(1, len(ta | tb))


def match_claims(gold_claims: list[dict], report: Report) -> list[tuple[dict, object | None]]:
    """Pair each gold claim with the best predicted fact-check from the same turn (by timestamp + word overlap)."""
    used: set[int] = set()
    pairs = []
    for g in gold_claims:
        best, best_score = None, 0.0
        for i, fc in enumerate(report.fact_checks):
            if i in used or fc.ts != g["ts"]:
                continue
            score = _overlap(g["text"], fc.claim) + 0.01  # same turn already counts for something
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            used.add(best)
        pairs.append((g, report.fact_checks[best] if best is not None else None))
    return pairs


def score_report(report: Report, gold: dict, spec: HarnessSpec) -> EpisodeScore:
    ep = ingest.load(report.source, spec)
    pairs = match_claims(gold.get("claims", []), report)
    correct = [fc is not None and fc.status == g["verdict"] for g, fc in pairs]
    with_evidence = [(g, fc) for g, fc in pairs if g.get("evidence_ids") and g["verdict"] != "unverifiable"]
    ev_hits = [fc is not None and bool({e.id for e in fc.evidence} & set(g["evidence_ids"])) for g, fc in with_evidence]
    summary = report.summary.lower()
    points = gold.get("summary_key_points", [])
    covered = [any(alt.lower() in summary for alt in alts) for alts in points]
    turns = {t.id: t for t in ep.turns}
    valid_quotes = [q.turn_id in turns and q.text == turns[q.turn_id].text and q.ts == turns[q.turn_id].ts
                    for q in report.quotes]
    problems = critic.structural_problems(report, ep, spec)
    expected_tags = gold.get("expected_tags", [])
    tag_hits = [any(_overlap(t, mine) > 0 for mine in report.tags) for t in expected_tags]
    metrics = {
        "verdict_accuracy": _mean(correct, 1.0),
        "evidence_recall": _mean(ev_hits, 1.0),
        "quote_validity": _mean(valid_quotes, 0.0),
        "keypoint_coverage": _mean(covered, 1.0),
        "structure": max(0.0, 1 - 0.25 * len(problems)),
        "claim_recall": _mean([fc is not None for _, fc in pairs], 1.0),
        "tag_overlap": _mean(tag_hits, 1.0),
    }
    details = {
        "claims": [{"gold": g["text"], "gold_verdict": g["verdict"], "pred": fc.claim if fc else None,
                    "pred_status": fc.status if fc else None} for g, fc in pairs],
        "problems": problems,
        "extra_predicted_claims": len(report.fact_checks) - sum(fc is not None for _, fc in pairs),
    }
    return EpisodeScore(report.episode_id, {k: round(v, 4) for k, v in metrics.items()}, details)


def _mean(values: list[bool], empty: float) -> float:
    return sum(values) / len(values) if values else empty


async def evaluate_files(spec: HarnessSpec, settings: Settings, files: list[Path], write: bool = True,
                         verbose: bool = False) -> list[EpisodeScore]:
    from .pipeline import Pipeline

    pipeline = Pipeline(spec, settings, verbose=verbose)
    scores = []
    for f in files:
        report = await pipeline.run_file(f, write=write)
        gold = load_gold(report.episode_id)
        if gold:
            scores.append(score_report(report, gold, spec))
    return scores


async def evaluate_split(spec: HarnessSpec, settings: Settings, split: str) -> list[EpisodeScore]:
    return await evaluate_files(spec, settings, split_files(split))


def mean_score(scores: list[EpisodeScore]) -> float:
    return round(statistics.mean(s.score for s in scores), 4) if scores else 0.0


# ---------------------------------------------------------------- retrieval: BM25 vs TF-IDF


def retrieval_report(settings: Settings, split: str) -> dict[str, float]:
    """Recall@1/@3 of the gold evidence per method (no LLM involved), with two kinds of query:
    the clean gold claim text, and the raw transcript turn the claim was said in (noisier, more realistic)."""
    from .harness import load as load_harness

    retriever = get_retriever(str(settings.kb_dir))
    spec = load_harness("harness/base.yaml")
    hits: dict[str, list[bool]] = {}
    claims = 0
    for f in split_files(split):
        gold = load_gold(re.split(r"[_.]", f.name)[0])
        turns = {t.ts: t.text for t in ingest.load(f, spec).turns}
        for g in (gold or {}).get("claims", []):
            if not g.get("evidence_ids"):
                continue
            claims += 1
            for query_kind, query in (("claim", g["text"]), ("turn", turns.get(g["ts"], g["text"]))):
                for method in ("bm25", "tfidf"):
                    ranked = [d.id for d, _ in retriever.all.search(query, 3, method)]
                    for k in (1, 3):
                        hits.setdefault(f"{method}_{query_kind}_recall@{k}", []).append(
                            bool(set(ranked[:k]) & set(g["evidence_ids"])))
    return {key: round(_mean(v, 0.0), 3) for key, v in hits.items()} | {"claims": claims}


def write_results(path: Path, spec: HarnessSpec, scores: list[EpisodeScore], retrieval: dict,
                  model: str | None = None) -> None:
    cols = list(WEIGHTS) + ["claim_recall", "tag_overlap"]
    lines = [
        f"# Evaluation results",
        "",
        f"Harness `{spec.version}` · model `{model or spec.model}` · {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"**Mean V̂ = {mean_score(scores):.3f}** over {len(scores)} episodes.",
        "",
        "| episode | V̂ | " + " | ".join(cols) + " |",
        "|---|---|" + "---|" * len(cols),
    ]
    for s in scores:
        lines.append(f"| {s.episode_id} | {s.score:.3f} | " + " | ".join(f"{s.metrics[c]:.2f}" for c in cols) + " |")
    if scores:
        lines.append("| **mean** | **%.3f** | " % mean_score(scores)
                     + " | ".join(f"{statistics.mean(s.metrics[c] for s in scores):.2f}" for c in cols) + " |")
    r = retrieval
    lines += ["", "## Retrieval: query → gold evidence in KB + mock web (no LLM)", "",
              "| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |", "|---|---|---|---|---|"]
    for method, label in (("bm25", "BM25"), ("tfidf", "TF-IDF")):
        lines.append(f"| {label} | " + " | ".join(f"{r.get(f'{method}_{q}_recall@{k}', 0):.3f}"
                                                  for q in ("claim", "turn") for k in (1, 3)) + " |")
    lines += ["", f"{r.get('claims', 0)} gold claims with evidence.", ""]
    lines += ["## Claim matching details", ""]
    for s in scores:
        lines.append(f"**{s.episode_id}** (extra predicted claims: {s.details['extra_predicted_claims']})")
        for c in s.details["claims"]:
            ok = "✔" if c["pred_status"] == c["gold_verdict"] else "✘"
            lines.append(f"- {ok} gold `{c['gold_verdict']}`: {c['gold']} → pred `{c['pred_status']}`: {c['pred']}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {"harness": spec.version, "model": model or spec.model, "mean_score": mean_score(scores),
               "metrics": {c: round(statistics.mean(s.metrics[c] for s in scores), 4) for c in cols} if scores else {},
               "episodes": {s.episode_id: {"score": s.score, **s.metrics} for s in scores}, "retrieval": retrieval}
    path.with_suffix(".json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("\n".join(lines[:8 + len(scores) + 2]))
