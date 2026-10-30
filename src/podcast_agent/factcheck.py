"""Fact-check logic that is fixed code, not model judgement:
claim-candidate pre-filter, verdict aggregation, the ✅/⚠/❓ mapping and the confidence score.

Confidence (documented in the README):
  ✅ / ⚠ :  clip(0.20 + 0.45·consistency + 0.35·support, 0.05, 0.97)
  ❓ (judged):  min(cap, 0.20 + 0.30·consistency)            cap = 0.5 by default
  ❓ (rule: prediction / anecdote):  fixed, 0.40 by default
where consistency = share of verdict samples that agree with the majority label, and
support = reliability of the best cited source (KB 1.0, high 0.85, medium 0.6, low 0.3; none 0.3).
"""

from __future__ import annotations

import re
from collections import Counter

from .harness import HarnessSpec
from .models import SYMBOL, Episode, Evidence, FactCheckResult, Turn
from .retrieval import Doc

CLAIM_HINT = re.compile(
    r"\d|%|\$|\b(percent|million|billion|trillion|according|study|studies|survey|report|research|announced|launch\w*|"
    r"founded|approved|cleared|record|first|largest|biggest|most|average|law|regulat\w*|ban\w*|invent\w*|"
    r"discover\w*|published|handbook\w*|fda|nasa|official\w*)\b",
    re.I,
)
CAPITALIZED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z]+")

SHOW_META = re.compile(r"\b(podcast|episode|listeners?|tuning in|hosting|host of|the show|this show|joining (us|me))\b",
                       re.I)


def is_show_meta(claim: str, ep: Episode) -> bool:
    """A claim about the show itself ("Sarah Johnson is hosting the podcast"): names a speaker and talks about the
    show. Statistics about podcasts in general ("half of adults listen to podcasts") name no speaker and are kept."""
    names = {w.lower() for n in [ep.host, *ep.guests] for w in re.findall(r"[A-Za-z]{3,}", n)} - {"the"}
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", claim)}
    return bool(names & words) and bool(SHOW_META.search(claim))


STATUS_OF_LABEL = {"supported": "verified", "contradicted": "inaccurate", "outdated": "inaccurate",
                   "insufficient": "unverifiable"}
RELIABILITY_WEIGHT = {"kb": 1.0, "high": 0.85, "medium": 0.6, "low": 0.3}


def claim_candidates(ep: Episode, spec: HarnessSpec) -> list[Turn]:
    """Cheap regex pre-filter: turns with numbers, named entities or claim-ish words. The model decides the rest."""
    names = {w for n in [ep.host, *ep.guests] for w in re.findall(r"[A-Za-z]+", n)} | {"I"}
    out = []
    for t in ep.turns:
        if len(t.text.split()) < 5 or (t.text.endswith("?") and not re.search(r"\d", t.text)):
            continue
        proper = [w for w in CAPITALIZED.findall(t.text) if w not in names]
        if CLAIM_HINT.search(t.text) or proper:
            out.append(t)
    return out[: spec.claims.candidate_max]


def aggregate(samples: list[dict]) -> tuple[str, float, dict]:
    """Majority label over verdict samples (ties go to sample 0, the greedy one) and its consistency."""
    labels = [s["label"] for s in samples]
    majority = Counter(labels).most_common(1)[0][0]
    chosen = next(s for s in samples if s["label"] == majority)
    return majority, labels.count(majority) / len(labels), chosen


def support(cited: list[Doc]) -> float:
    return max((RELIABILITY_WEIGHT.get(d.reliability, 0.3) for d in cited), default=0.3)


def confidence(status: str, consistency: float, support_score: float, spec: HarnessSpec) -> float:
    if status == "unverifiable":
        return round(min(spec.factcheck.unverifiable_confidence_cap, 0.2 + 0.3 * consistency), 2)
    return round(min(0.97, max(0.05, 0.2 + 0.45 * consistency + 0.35 * support_score)), 2)


def verification_text(label: str, cited: list[Doc]) -> str:
    sources = ", ".join(dict.fromkeys(f"{d.source} [{d.id}]" for d in cited)) or "no source"
    return {
        "supported": f"True (confirmed by {sources})",
        "contradicted": f"Possibly inaccurate (contradicted by {sources})",
        "outdated": f"Possibly outdated (newer data: {sources})",
        "insufficient": "Unverifiable (no evidence found that addresses the claim)",
    }[label]


def to_evidence(doc: Doc) -> Evidence:
    return Evidence(id=doc.id, title=doc.title, source=doc.source, date=doc.date, reliability=doc.reliability,
                    text=doc.text, url=doc.url)


QUANTITY = re.compile(
    r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%|percent|thousand|million|billion|trillion|[kmb]n?\b)?", re.I)
MULTIPLIER = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9, "b": 1e9, "bn": 1e9,
              "trillion": 1e12}


def quantities(text: str) -> list[float]:
    """Numbers with their magnitude ("2 billion" -> 2e9); bare years are skipped."""
    out = []
    for m in QUANTITY.finditer(text):
        value, unit = float(m[1].replace(",", "")), (m[2] or "").lower()
        if not unit and "." not in m[1] and "," not in m[1] and 1900 <= value <= 2100:
            continue
        out.append(value * MULTIPLIER.get(unit, 1))
    return out


def numbers_disagree(claim: str, cited: list[Doc], tolerance: float = 0.15) -> tuple[float, list[float]] | None:
    """If the claim states quantities and none is within `tolerance` of a quantity in the cited evidence,
    return (claim quantity, evidence quantities). Small models are unreliable at comparing numbers; code is not."""
    claimed = quantities(claim)
    found = [q for d in cited for q in quantities(f"{d.title}. {d.text}")]
    if not claimed or not found:
        return None
    if any(abs(c - e) <= tolerance * max(abs(c), abs(e)) for c in claimed for e in found):
        return None
    return claimed[0], found


def judged_result(claim: dict, turn: Turn, samples: list[dict], evidence: dict[str, Doc], queries: list[str],
                  spec: HarnessSpec) -> FactCheckResult:
    label, consistency, chosen = aggregate(samples)
    cited = [evidence[i] for i in dict.fromkeys(chosen["evidence_ids"]) if i in evidence]
    if label != "insufficient" and not cited:  # a verdict with no cited source is not trusted
        label, chosen = "insufficient", {**chosen, "reasoning": chosen["reasoning"] + " (no evidence cited)"}
    if label == "supported" and spec.factcheck.numeric_check and (gap := numbers_disagree(claim["claim"], cited)):
        label = "contradicted"
        chosen = {**chosen, "reasoning": chosen["reasoning"] + f" [numeric check: the claim says {gap[0]:,.4g} but "
                  f"the cited evidence says {', '.join(f'{x:,.4g}' for x in gap[1][:4])}; marked ⚠]"}
    status = STATUS_OF_LABEL[label]
    return FactCheckResult(
        claim=claim["claim"], turn_id=turn.id, ts=turn.ts, speaker=turn.speaker, type=claim["type"],
        status=status, symbol=SYMBOL[status], verification=verification_text(label, cited),
        confidence=confidence(status, consistency, support(cited), spec), reasoning=chosen["reasoning"],
        method="judged", labels=[s["label"] for s in samples], queries=queries,
        evidence=[to_evidence(d) for d in cited],
    )


def rule_result(claim: dict, turn: Turn, spec: HarnessSpec, why: str, method: str = "rule") -> FactCheckResult:
    """❓ without asking the model: predictions and anecdotes cannot be checked against public facts."""
    return FactCheckResult(
        claim=claim["claim"], turn_id=turn.id, ts=turn.ts, speaker=turn.speaker, type=claim["type"],
        status="unverifiable", symbol=SYMBOL["unverifiable"], verification=f"Unverifiable ({why})",
        confidence=spec.factcheck.rule_unverifiable_confidence, reasoning=why, method=method,  # type: ignore[arg-type]
    )


RULE_REASON = {"prediction": "a prediction about the future", "anecdote": "a personal anecdote with no public record",
               "opinion": "an opinion"}
