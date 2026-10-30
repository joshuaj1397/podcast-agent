"""Load .json / .txt transcripts into an Episode: parse turns, resolve speakers, clean ASR noise."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .harness import HarnessSpec
from .models import Episode, Turn

TXT_TURN = re.compile(r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<speaker>[^:\]]{1,40}):\s*(?P<text>.*)$")
TXT_META = re.compile(r"^#\s*(?P<key>\w+)\s*:\s*(?P<value>.*)$")


def load(path: str | Path, spec: HarnessSpec) -> Episode:
    path = Path(path)
    raw = _load_json(path) if path.suffix.lower() == ".json" else _load_txt(path)
    host, guests = raw.get("host") or "Host", list(raw.get("guests") or [])
    turns = []
    for i, t in enumerate(raw["transcript"], start=1):
        text = clean(t["text"], spec)
        if not text:
            continue
        turns.append(
            Turn(
                id=f"T{i:02d}",
                ts=t["timestamp"],
                seconds=to_seconds(t["timestamp"]),
                speaker=resolve_speaker(t["speaker"], host, guests),
                section=t.get("section"),
                text=text,
                raw=t["text"],
            )
        )
    return Episode(
        episode_id=raw.get("episode_id") or path.stem.split("_")[0],
        title=raw.get("title") or path.stem,
        host=host,
        guests=guests,
        recorded=raw.get("recorded"),
        source=path.as_posix(),
        turns=turns,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_txt(path: Path) -> dict:
    """`# key: value` header lines, then `[HH:MM:SS] Speaker: text`; other lines continue the previous turn."""
    meta: dict = {"transcript": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if m := TXT_META.match(line):
            key, value = m["key"].lower(), m["value"].strip()
            meta[key] = [g.strip() for g in value.split(",") if g.strip()] if key == "guests" else value
        elif m := TXT_TURN.match(line):
            meta["transcript"].append({"timestamp": m["ts"], "speaker": m["speaker"].strip(), "text": m["text"]})
        elif meta["transcript"]:
            meta["transcript"][-1]["text"] += " " + line
    return meta


def to_seconds(ts: str) -> int:
    seconds = 0
    for part in ts.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def resolve_speaker(label: str, host: str, guests: list[str]) -> str:
    """Map "Sarah" / "Dr. Priya" / "Host" / "Guest" to the full names from the episode metadata."""
    low = label.lower().strip()
    if low == "host":
        return host
    if low == "guest" and len(guests) == 1:
        return guests[0]
    words = {w for w in re.findall(r"[a-z]+", low) if w not in {"dr", "mr", "ms", "mrs", "prof"}}
    for name in [host, *guests]:
        if words and words <= set(re.findall(r"[a-z]+", name.lower())):
            return name
    return label


def clean(text: str, spec: HarnessSpec) -> str:
    """Remove filler words and apply the glossary of known transcription errors."""
    if spec.filler_words:
        fillers = "|".join(re.escape(w) for w in spec.filler_words)
        text = re.sub(rf"(?i)(^|[\s,]+)(?:{fillers})\b[,.]?(?=\s|$)", r"\1", text)
    for phrase in spec.filler_phrases:  # only when set off by commas: "it's, like, huge" -> "it's huge"
        text = re.sub(rf"(?i),\s*{re.escape(phrase)}\s*,", " ", text)
        text = re.sub(rf"(?i)^{re.escape(phrase)},\s*", "", text)
    for wrong, right in spec.glossary.items():
        text = re.sub(rf"(?i)\b{re.escape(wrong)}\b", right, text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    return text[:1].upper() + text[1:]


def profile(ep: Episode, n_claim_candidates: int) -> dict:
    """Facts about the episode that the planner uses to decide what to do."""
    words = sum(len(t.text.split()) for t in ep.turns)
    raw_words = sum(len(t.raw.split()) for t in ep.turns)
    return {
        "episode_id": ep.episode_id,
        "title": ep.title,
        "host": ep.host,
        "guests": ep.guests,
        "recorded": ep.recorded or "unknown",
        "turns": len(ep.turns),
        "words": words,
        "duration": ep.turns[-1].ts if ep.turns else "00:00",
        "filler_words_removed": raw_words - words,
        "turns_with_numbers": sum(bool(re.search(r"\d", t.text)) for t in ep.turns),
        "claim_candidates": n_claim_candidates,
    }
