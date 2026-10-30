"""Build the ADK LlmAgents from the harness spec, and run one agent to completion.

Every agent is built fresh per call because its output schema can depend on the
episode (valid turn ids) or the claim (valid evidence ids).
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from . import llm, trace
from .config import Settings
from .harness import HarnessSpec
from .models import Plan, SummaryOut, claims_schema, notes_schema, verdict_schema
from .tools import ToolGuard

APP = "podcast_agent"
logging.getLogger("google_adk").setLevel(logging.ERROR)


class AgentFactory:
    def __init__(self, spec: HarnessSpec, settings: Settings):
        self.spec = spec
        self.settings = settings

    def _agent(
        self,
        name: str,
        instruction: str,
        output_schema: type[BaseModel] | None = None,
        tools: list | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        **callbacks: Any,
    ) -> LlmAgent:
        gen = self.spec.generation
        return LlmAgent(
            name=name,
            model=llm.make_model(self.spec, self.settings),
            # A callable instruction is used verbatim (no ADK {state} templating), so prompts may contain braces.
            instruction=lambda _ctx: instruction,
            output_schema=output_schema,
            output_key="output" if output_schema else None,
            tools=tools or [],
            generate_content_config=types.GenerateContentConfig(
                temperature=gen.temperature if temperature is None else temperature,
                seed=gen.seed if seed is None else seed,
                max_output_tokens=gen.max_output_tokens,
            ),
            after_model_callback=trace.after_model,
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            **callbacks,
        )

    def planner(self) -> LlmAgent:
        return self._agent("planner", self.spec.instruction("planner"), Plan)

    def summarizer(self) -> LlmAgent:
        s = self.spec.summary
        text = self.spec.instruction(
            "summarizer", min_words=s.min_words, max_words=s.max_words, target_words=(s.min_words + s.max_words) // 2
        )
        return self._agent("summarizer", text, SummaryOut)

    def notes(self, quote_ids: list[str]) -> LlmAgent:
        n = self.spec.notes
        text = self.spec.instruction(
            "notes", takeaways=n.takeaways, quotes_min=n.quotes_min, quotes_max=n.quotes_max,
            tags_min=n.tags_min, tags_max=n.tags_max,
        )
        return self._agent("notes", text, notes_schema(quote_ids))

    def claims(self, turn_ids: list[str]) -> LlmAgent:
        return self._agent("claims", self.spec.instruction("claims"), claims_schema(turn_ids))

    def researcher(self, tools: list, guard: ToolGuard) -> LlmAgent:
        text = self.spec.instruction("researcher", max_tool_calls=self.spec.limits.max_tool_calls)
        return self._agent(
            "researcher", text, tools=tools, before_tool_callback=guard.before, after_tool_callback=guard.after
        )

    def judge(self, evidence_ids: list[str], today: str, recorded: str, sample: int) -> LlmAgent:
        """Sample 0 is greedy; later samples use a higher temperature and their own seed (self-consistency)."""
        text = self.spec.instruction("judge", today=today, recorded=recorded)
        temperature = None if sample == 0 else self.spec.factcheck.sample_temperature
        return self._agent(
            "judge", text, verdict_schema(evidence_ids), temperature=temperature, seed=self.spec.generation.seed + sample
        )


async def run(agent: LlmAgent, message: str, max_llm_calls: int) -> tuple[Any, str]:
    """Run one agent on one user message in a fresh session. Returns (structured output or None, final text)."""
    sessions = InMemorySessionService()
    runner = Runner(agent=agent, app_name=APP, session_service=sessions)
    session = await sessions.create_session(app_name=APP, user_id="pipeline")
    texts: list[str] = []
    events = runner.run_async(
        user_id="pipeline",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        run_config=RunConfig(max_llm_calls=max_llm_calls),
    )
    async for event in events:
        for part in (event.content.parts if event.content else None) or []:
            if part.text and not part.thought:
                texts.append(part.text)
    session = await sessions.get_session(app_name=APP, user_id="pipeline", session_id=session.id)
    return session.state.get("output"), "\n".join(texts[-1:])
