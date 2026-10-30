"""Runtime settings from environment variables (things that differ per machine, not per harness)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    ollama_api_base: str = field(default_factory=lambda: _env("OLLAMA_API_BASE", "http://localhost:11434"))
    model_override: str = field(default_factory=lambda: _env("PODCAST_MODEL"))  # beats harness.model if set
    fake_llm: bool = field(default_factory=lambda: _env("PODCAST_FAKE_LLM") == "1")  # offline tests
    use_cache: bool = field(default_factory=lambda: _env("PODCAST_NO_CACHE") != "1")
    cache_dir: Path = field(default_factory=lambda: Path(_env("PODCAST_CACHE_DIR", ".cache/llm")))
    kb_dir: Path = field(default_factory=lambda: Path(_env("PODCAST_KB_DIR", "kb")))
    out_dir: Path = field(default_factory=lambda: Path(_env("PODCAST_OUT_DIR", "out")))
    today: date = field(default_factory=lambda: date.fromisoformat(_env("PODCAST_TODAY") or date.today().isoformat()))
