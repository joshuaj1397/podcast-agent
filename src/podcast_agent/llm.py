"""Model construction: LiteLlm -> Ollama, wrapped in an on-disk response cache; plus FakeLlm for offline tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any, AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel

from .config import Settings
from .harness import HarnessSpec

STATS: Counter = Counter()  # llm_calls, cache_hits, retries: reset per episode by the pipeline

logging.getLogger("LiteLLM").setLevel(logging.WARNING)


def make_model(spec: HarnessSpec, settings: Settings) -> BaseLlm:
    """The harness model (or PODCAST_MODEL), with PODCAST_FALLBACK_MODELS tried in order if it keeps failing.
    Any LiteLLM model id works: ollama_chat/..., bedrock/..., sagemaker/..., anthropic/..."""
    if settings.fake_llm:
        return FakeLlm()
    names = [settings.model_override or spec.model, *_env_list("PODCAST_FALLBACK_MODELS")]
    return CachingLlm(model=names[0], inner=_litellm(names[0], spec, settings),
                      fallbacks=[_litellm(n, spec, settings) for n in names[1:]],
                      cache_dir=settings.cache_dir if settings.use_cache else None)


def _litellm(name: str, spec: HarnessSpec, settings: Settings) -> LiteLlm:
    gen = spec.generation
    if name.startswith("ollama"):
        return LiteLlm(
            model=name,
            api_base=settings.ollama_api_base,
            timeout=gen.timeout_s,
            think=False,  # Gemma 4 thinking mode off: faster, and the schemas have a `reasoning` field instead
            extra_body={"options": {"num_ctx": gen.num_ctx}},
        )
    return LiteLlm(model=name, timeout=gen.timeout_s)  # cloud providers: credentials come from the environment


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


class CachingLlm(BaseLlm):
    """Caches complete responses on disk, keyed by a hash of everything that affects the output.

    Re-running an episode (or an Evo² fitness evaluation of an unchanged prompt) then costs nothing.
    Also retries transient Ollama errors.
    """

    inner: BaseLlm
    fallbacks: list[BaseLlm] = []
    cache_dir: Path | None = None

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        path = self.cache_dir / f"{request_key(self.model, llm_request)}.json" if self.cache_dir else None
        if path and path.exists():
            STATS["cache_hits"] += 1
            for item in json.loads(path.read_text(encoding="utf-8")):
                yield LlmResponse.model_validate(item)
            return
        responses = await self._generate(llm_request)
        STATS["llm_calls"] += 1
        if path and responses and not any(r.error_code for r in responses):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([r.model_dump(mode="json", exclude_none=True) for r in responses]), encoding="utf-8")
        for r in responses:
            yield r


    async def _generate(self, llm_request: LlmRequest) -> list[LlmResponse]:
        """Retry each model with backoff, then fall back to the next model in the chain."""
        models = [self.inner, *self.fallbacks]
        for i, model in enumerate(models):
            for attempt in range(3):
                try:
                    return [r async for r in model.generate_content_async(llm_request, stream=False)]
                except Exception:
                    if attempt == 2 and i == len(models) - 1:
                        raise
                    STATS["retries"] += 1
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
            STATS["fallbacks"] += 1
        raise RuntimeError("unreachable")


def request_key(model: str, req: LlmRequest) -> str:
    cfg = req.config
    schema = cfg.response_schema
    payload = {
        "model": model,
        "system": _plain(cfg.system_instruction),
        "contents": _drop_ids([c.model_dump(mode="json", exclude_none=True) for c in req.contents]),
        "schema": schema.model_json_schema() if isinstance(schema, type) and issubclass(schema, BaseModel) else repr(schema),
        "tools": [t.model_dump(mode="json", exclude_none=True) for t in (cfg.tools or []) if hasattr(t, "model_dump")],
        "temperature": cfg.temperature,
        "seed": cfg.seed,
        "max_tokens": cfg.max_output_tokens,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _plain(value: Any) -> Any:
    return value.model_dump(mode="json", exclude_none=True) if hasattr(value, "model_dump") else value


def _drop_ids(obj: Any) -> Any:
    """ADK gives function calls random ids; strip them so tool-loop requests hash identically across runs."""
    if isinstance(obj, dict):
        return {k: _drop_ids(v) for k, v in obj.items() if k != "id"}
    if isinstance(obj, list):
        return [_drop_ids(v) for v in obj]
    return obj


# ---------------------------------------------------------------- offline fake for tests


class FakeLlm(BaseLlm):
    """Deterministic stand-in for the model: fills any response schema with plausible values,
    and for tool-using agents makes one search call before answering."""

    model: str = "fake"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        STATS["llm_calls"] += 1
        user_text = _last_user_text(llm_request)
        schema = llm_request.config.response_schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            data = fake_instance(schema.model_json_schema(), user_text)
            yield _text_response(json.dumps(data))
            return
        called = any(p.function_response for c in llm_request.contents for p in (c.parts or []))
        if llm_request.tools_dict and not called:
            tool = next(iter(llm_request.tools_dict))
            query = " ".join(user_text.replace("Claim:", "").split()[:6])
            call = types.FunctionCall(name=tool, args={"query": query})
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
            return
        yield _text_response("I found evidence related to the claim.")


def _text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _last_user_text(req: LlmRequest) -> str:
    for content in reversed(req.contents):
        for part in content.parts or []:
            if part.text and content.role == "user":
                return part.text
    return ""


def fake_instance(schema: dict, text: str) -> Any:
    words = [w for w in text.split() if w.isalpha()] or ["podcast"]
    defs = schema.get("$defs", {})

    def value(name: str, node: dict, i: int = 0) -> Any:
        if "$ref" in node:
            node = defs[node["$ref"].rsplit("/", 1)[-1]]
        if "anyOf" in node:
            node = node["anyOf"][0]
        if "enum" in node:
            return node["enum"][i % len(node["enum"])]
        kind = node.get("type")
        if kind == "object":
            return {k: value(k, v, i) for k, v in node["properties"].items()}
        if kind == "array":
            n = {"takeaways": 5, "tags": 4, "quote_ids": 3, "steps": 4, "claims": 3, "evidence_ids": 1}.get(name, 2)
            items = node["items"]
            enum = (defs[items["$ref"].rsplit("/", 1)[-1]] if "$ref" in items else items).get("enum")
            n = min(n, len(enum)) if enum else n
            return [value(name, items, j) for j in range(n)]
        if kind == "string":
            if name == "summary":
                return " ".join((words * 300)[:240])
            if name == "tags":
                return f"topic {chr(97 + i)}"
            return f"{name} {i + 1}: " + " ".join(words[i : i + 10])
        if kind == "integer":
            return 4
        if kind == "number":
            return 0.5
        if kind == "boolean":
            return True
        return None

    return value("root", schema)
