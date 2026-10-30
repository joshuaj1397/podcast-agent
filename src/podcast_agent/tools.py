"""Search tools for the research agent, plus a guard that enforces the search budget and blocks repeats.

The guard exists because small models (and Gemma 4 on Ollama in particular) can call the
same tool with the same query over and over. Repeats and over-budget calls get an error
back instead of running, which nudges the model to stop and answer.
"""

from __future__ import annotations

import hashlib
from typing import Any

from google.adk.tools import FunctionTool

from . import trace
from .harness import HarnessSpec
from .retrieval import Doc, Retriever


def make_tools(retriever: Retriever, spec: HarnessSpec, use_web: bool) -> list[FunctionTool]:
    k, method = spec.retrieval.k, spec.retrieval.method

    def search_kb(query: str) -> dict:
        hits = retriever.kb.search(query, k, method)
        return {"query": query, "results": [d.brief() for d, _ in hits]}

    def search_web(query: str) -> dict:
        if _injected_fault(query, spec.factcheck.web_fail_rate):
            return {"query": query, "error": "mock search API timed out; try another query or search_kb"}
        hits = retriever.web.search(query, k, method)
        return {"query": query, "results": [d.brief() for d, _ in hits]}

    # Tool descriptions come from the harness, so Evo² can rewrite them.
    search_kb.__doc__ = spec.tools.search_kb
    search_web.__doc__ = spec.tools.search_web
    return [FunctionTool(search_kb)] + ([FunctionTool(search_web)] if use_web else [])


def _injected_fault(query: str, rate: float) -> bool:
    """Deterministic fault injection: the same query always fails or always succeeds."""
    return rate > 0 and int(hashlib.md5(query.encode()).hexdigest()[:4], 16) / 0xFFFF < rate


class ToolGuard:
    """Per-claim before/after tool callbacks: budget, de-duplication, and evidence collection."""

    def __init__(self, retriever: Retriever, max_calls: int):
        self.retriever = retriever
        self.max_calls = max_calls
        self.queries: list[str] = []
        self.hits: dict[str, Doc] = {}  # evidence seen by the agent, in order

    def before(self, tool: Any, args: dict, tool_context: Any) -> dict | None:
        query = " ".join(str(args.get("query", "")).lower().split())
        key = f"{tool.name}: {query}"
        if key in self.queries:
            trace.current().warn(f"blocked repeated call {key!r}")
            return {"error": "You already ran exactly this search. Use different keywords, or stop and answer."}
        if len(self.queries) >= self.max_calls:
            trace.current().warn(f"blocked {key!r}: search budget of {self.max_calls} used")
            return {"error": "Search budget used up. Stop searching and reply with what you found."}
        self.queries.append(key)
        trace.current().action("researcher", tool.name, args)
        return None

    def after(self, tool: Any, args: dict, tool_context: Any, tool_response: Any) -> dict | None:
        if not isinstance(tool_response, dict):
            return None
        if "error" in tool_response:
            trace.current().observation("researcher", f"error: {tool_response['error']}")
            return None
        results = tool_response.get("results", [])
        for r in results:
            self.add(self.retriever.by_id[r["id"]])
        text = " | ".join(f"{r['id']} {r['title']} ({r['date']})" for r in results) or "no results"
        trace.current().observation("researcher", text, ids=[r["id"] for r in results])
        return None

    def add(self, doc: Doc) -> None:
        self.hits.setdefault(doc.id, doc)
