"""Readable agent logs: a rich console view (PLAN, then Thought -> Action -> Observation) and trace.jsonl.

The current Trace lives in a context variable so ADK callbacks can reach it without plumbing.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console
from rich.markup import escape
from rich.table import Table

console = Console(highlight=False, width=None if sys.stdout.isatty() else 140)


class Trace:
    def __init__(self, episode_id: str, path: Path | None, verbose: bool = False):
        self.episode_id = episode_id
        self.verbose = verbose
        self.t0 = time.time()
        self.events: list[dict] = []
        self._file = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w", encoding="utf-8")

    def log(self, kind: str, agent: str = "", **data: Any) -> None:
        event = {"t": round(time.time() - self.t0, 2), "episode": self.episode_id, "kind": kind, "agent": agent, **data}
        self.events.append(event)
        if self._file:
            self._file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()

    # ---- console views; each also logs a structured event

    def header(self, title: str) -> None:
        self.log("episode", title=title)
        console.rule(f"[bold]{escape(self.episode_id)} · {escape(title)}")

    def step(self, name: str, detail: str = "") -> None:
        self.log("step", name, detail=detail)
        console.print(f"\n[bold cyan]▶ {name}[/] [dim]{escape(detail)}[/]")

    def plan(self, plan: Any, profile: dict, adjustments: list[str]) -> None:
        self.log("plan", "planner", plan=plan.model_dump(), profile=profile, adjustments=adjustments)
        table = Table(title="PLAN", show_header=True, header_style="bold", title_justify="left")
        table.add_column("#")
        table.add_column("step")
        for i, step in enumerate(plan.steps, 1):
            table.add_row(str(i), step)
        console.print(table)
        console.print(f"  claim_budget={plan.claim_budget}  use_web_search={plan.use_web_search}")
        console.print(f"  summary_focus: {escape(plan.summary_focus)}")
        for a in adjustments:
            self.warn(a)

    def thought(self, agent: str, text: str) -> None:
        self.log("thought", agent, text=text)
        console.print(f"  [magenta]Thought[/] [dim]({agent})[/]: {escape(_short(text, 400))}")

    def action(self, agent: str, tool: str, args: dict) -> None:
        self.log("action", agent, tool=tool, args=args)
        arg_text = ", ".join(f"{v!r}" for v in args.values())
        console.print(f"  [yellow]Action[/]: {tool}({escape(arg_text)})")

    def observation(self, agent: str, text: str, **data: Any) -> None:
        self.log("observation", agent, text=text, **data)
        console.print(f"  [blue]Observation[/]: {escape(_short(text, 400 if self.verbose else 220))}")

    def result(self, agent: str, text: str, **data: Any) -> None:
        self.log("result", agent, text=text, **data)
        console.print(f"  [green]✓[/] {escape(text)}")

    def warn(self, text: str) -> None:
        self.log("warning", text=text)
        console.print(f"  [bold red]![/] {escape(text)}")

    def debug(self, agent: str, text: str) -> None:
        self.log("llm_text", agent, text=text)
        if self.verbose:
            console.print(f"  [dim]{agent} raw: {escape(_short(text, 800))}[/]")


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


_current: ContextVar[Trace | None] = ContextVar("trace", default=None)


def current() -> Trace:
    trace = _current.get()
    if trace is None:  # code used outside a pipeline run (tests, kb-search)
        trace = Trace("-", None)
        _current.set(trace)
    return trace


@contextmanager
def open_trace(episode_id: str, path: Path | None, verbose: bool) -> Iterator[Trace]:
    trace = Trace(episode_id, path, verbose)
    token = _current.set(trace)
    try:
        yield trace
    finally:
        trace.close()
        _current.reset(token)


# ---- ADK callback: every model response lands in the trace


def after_model(callback_context: Any, llm_response: Any) -> None:
    trace = current()
    agent = callback_context.agent_name
    if llm_response.error_message:
        trace.warn(f"{agent}: model error {llm_response.error_code}: {llm_response.error_message}")
    for part in (llm_response.content.parts if llm_response.content else None) or []:
        if part.text and not part.thought:
            trace.debug(agent, part.text)
    return None
