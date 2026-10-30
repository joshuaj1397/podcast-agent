"""Evo² genome: the harness spec plus a small, validated edit language over it.

The Proposer emits a list of edits as JSON; `apply` turns a parent spec into a child
spec or raises EditError. A malformed edit is not silently fixed: it earns the
proposer a fixed negative reward (MALFORMED_REWARD), which is the training signal
that teaches the evolver to write valid edits.

Edit ops:
  set_param          {"op": "set_param", "path": "factcheck.sample_temperature", "value": 0.3}
  replace_in_prompt  {"op": "replace_in_prompt", "prompt": "judge", "old": "<exact text>", "new": "<text>"}
  append_to_prompt   {"op": "append_to_prompt", "prompt": "claims", "text": "<text>"}
  toggle_step        {"op": "toggle_step", "step": "factcheck", "enabled": false}
  add_guardrail      {"op": "add_guardrail", "text": "<rule>"}
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from podcast_agent.harness import HarnessSpec

MALFORMED_REWARD = -0.1
PROMPTS = ["planner", "summarizer", "notes", "claims", "researcher", "judge"]
# Numeric/bool knobs the evolver may set, with allowed ranges (validated again by HarnessSpec).
PARAMS: dict[str, tuple[type, Any, Any]] = {
    "limits.max_repairs": (int, 0, 3),
    "limits.max_tool_calls": (int, 1, 6),
    "limits.claim_budget": (int, 3, 12),
    "retrieval.method": (str, None, None),
    "retrieval.k": (int, 1, 8),
    "retrieval.max_evidence": (int, 2, 10),
    "factcheck.verdict_samples": (int, 1, 5),
    "factcheck.sample_temperature": (float, 0.0, 1.2),
    "factcheck.web_search": (bool, None, None),
    "factcheck.backstop_search": (bool, None, None),
    "factcheck.numeric_check": (bool, None, None),
    "notes.quote_candidates": (int, 5, 25),
    "claims.candidate_max": (int, 5, 40),
}
MAX_PROMPT_CHARS = 4000
MIN_WORDS = 4  # appended text / guardrails shorter than this were junk from the 2B evolver in round 1


class Edit(BaseModel):
    op: Literal["set_param", "replace_in_prompt", "append_to_prompt", "toggle_step", "add_guardrail"]
    path: str | None = None
    value: Any = None
    prompt: str | None = None
    old: str | None = None
    new: str | None = None
    text: str | None = None
    step: str | None = None
    enabled: bool | None = None


class EditError(ValueError):
    pass


OP_FIELDS = {"set_param": ("path", "value"), "replace_in_prompt": ("prompt", "old", "new"),
             "append_to_prompt": ("prompt", "text"), "toggle_step": ("step", "enabled"), "add_guardrail": ("text",)}


def normalize_edit(edit: dict) -> dict:
    """Keep only the fields an op uses (round-1 trajectories were logged before the union schema)."""
    return {"op": edit.get("op"), **{k: edit[k] for k in OP_FIELDS.get(edit.get("op"), ()) if k in edit}}


def apply(spec: HarnessSpec, edits: list[Edit | dict], version: str, min_words: int | None = None) -> HarnessSpec:
    """Apply edits to a copy of `spec`. Raises EditError on any malformed edit.
    `min_words=0` replays edits logged before the minimum-length rule existed."""
    min_words = MIN_WORDS if min_words is None else min_words
    if not edits:
        raise EditError("no edits")
    data = spec.model_dump(mode="json")
    for raw in edits:
        e = raw if isinstance(raw, Edit) else _parse(raw)
        if e.op == "set_param":
            _set_param(data, e.path or "", e.value)
        elif e.op in ("replace_in_prompt", "append_to_prompt"):
            if e.prompt not in PROMPTS:
                raise EditError(f"unknown prompt {e.prompt!r}; one of {PROMPTS}")
            text = data["prompts"][e.prompt]
            if e.op == "replace_in_prompt":
                if not e.old or text.count(e.old) != 1:
                    raise EditError(f"'old' must occur exactly once in prompt {e.prompt!r} (found {text.count(e.old or '')})")
                text = text.replace(e.old, e.new or "")
            else:
                if len((e.text or "").split()) < max(1, min_words):
                    raise EditError(f"append_to_prompt needs an instruction of at least {min_words} words in 'text'")
                text = text.rstrip("\n") + "\n" + e.text.strip() + "\n"
            if len(text) > MAX_PROMPT_CHARS:
                raise EditError(f"prompt {e.prompt!r} would exceed {MAX_PROMPT_CHARS} characters")
            data["prompts"][e.prompt] = text
        elif e.op == "toggle_step":
            steps = data["enabled_steps"]
            if e.step not in ("summarize", "notes", "claims", "factcheck") or e.enabled is None:
                raise EditError("toggle_step needs step in summarize|notes|claims|factcheck and enabled true|false")
            if e.step in ("summarize", "notes") and not e.enabled:
                raise EditError(f"{e.step} is a required deliverable and cannot be disabled")
            order = ["summarize", "notes", "claims", "factcheck"]
            data["enabled_steps"] = [s for s in order if (s in steps and s != e.step) or (s == e.step and e.enabled)]
        elif e.op == "add_guardrail":
            if len((e.text or "").split()) < max(1, min_words) or len(e.text) > 300:
                raise EditError(f"add_guardrail needs a rule of {min_words}+ words and at most 300 characters in 'text'")
            data["guardrails"].append(e.text.strip())
    data["version"] = version
    try:
        child = HarnessSpec.model_validate(data)
    except ValidationError as err:
        raise EditError(f"invalid harness after edits: {err.errors()[0]['msg']}") from err
    if not diff(spec, child):
        raise EditError("the edits change nothing")
    return child


def _parse(raw: dict) -> Edit:
    try:
        return Edit.model_validate(raw)
    except ValidationError as err:
        raise EditError(f"malformed edit {json.dumps(raw)[:120]}: {err.errors()[0]['msg']}") from err


def _set_param(data: dict, path: str, value: Any) -> None:
    if path not in PARAMS:
        raise EditError(f"path {path!r} is not editable; editable: {sorted(PARAMS)}")
    kind, lo, hi = PARAMS[path]
    try:
        value = {"true": True, "false": False}.get(str(value).lower(), value) if kind is bool else kind(value)
    except (TypeError, ValueError) as err:
        raise EditError(f"{path} needs a {kind.__name__}, got {value!r}") from err
    if lo is not None and not lo <= value <= hi:
        raise EditError(f"{path}={value} is outside [{lo}, {hi}]")
    if path == "retrieval.method" and value not in ("bm25", "tfidf"):
        raise EditError("retrieval.method must be bm25 or tfidf")
    section, key = path.split(".")
    data[section][key] = value


def knobs(spec: HarnessSpec) -> str:
    """The editable surface, shown to the Proposer: current param values and the prompts."""
    data = spec.model_dump(mode="json")
    params = "\n".join(f"  {p} = {json.dumps(data[p.split('.')[0]][p.split('.')[1]])}"
                       + (f"   (allowed {lo}..{hi})" if lo is not None else "") for p, (_, lo, hi) in PARAMS.items())
    prompts = "\n\n".join(f"[{name}]\n{data['prompts'][name].strip()}" for name in PROMPTS)
    guard = "\n".join(f"  - {g}" for g in data["guardrails"])
    return f"Parameters:\n{params}\nEnabled steps: {data['enabled_steps']}\nGuardrails:\n{guard}\n\n{prompts}"


def diff(parent: HarnessSpec, child: HarnessSpec) -> list[str]:
    """Human-readable list of what changed (for logs and the Reflector)."""
    a, b = parent.model_dump(mode="json"), child.model_dump(mode="json")
    out = []
    for section in a:
        if section == "version" or a[section] == b[section]:
            continue
        if isinstance(a[section], dict):
            for k in a[section]:
                if a[section][k] != b[section].get(k):
                    out.append(f"{section}.{k}: {_brief(a[section][k])} -> {_brief(b[section][k])}")
        else:
            out.append(f"{section}: {_brief(a[section])} -> {_brief(b[section])}")
    return out


def _brief(v: Any, n: int = 80) -> str:
    s = json.dumps(v) if not isinstance(v, str) else v
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
