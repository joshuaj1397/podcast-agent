"""Hour-0 smoke test: ADK + LiteLlm -> Ollama gemma4:e2b. Checks output_schema and tool calling."""
import asyncio, time, sys
from typing import Literal
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

MODEL = sys.argv[1] if len(sys.argv) > 1 else "ollama_chat/gemma4:e2b"
llm = LiteLlm(model=MODEL, temperature=0, extra_body={"options": {"num_ctx": 8192, "seed": 7}}, think=False)


class Verdict(BaseModel):
    reasoning: str = Field(description="one sentence")
    label: Literal["supported", "contradicted", "insufficient"]


calls = []
def search_kb(query: str) -> dict:
    """Search the knowledge base. Returns top passages for the query."""
    calls.append(query)
    return {"results": [{"id": "kb-1", "text": "NASA's Perseverance rover landed on Mars in February 2021."}]}


async def run(agent, msg):
    svc = InMemorySessionService()
    r = Runner(agent=agent, app_name="smoke", session_service=svc)
    s = await svc.create_session(app_name="smoke", user_id="u")
    t = time.time()
    async for ev in r.run_async(user_id="u", session_id=s.id, new_message=types.Content(role="user", parts=[types.Part(text=msg)])):
        for p in (ev.content.parts if ev.content else []) or []:
            if p.function_call: print("  CALL", p.function_call.name, p.function_call.args)
            if p.function_response: print("  RESP", str(p.function_response.response)[:80])
            if p.text: print("  TEXT", p.text[:300])
    s = await svc.get_session(app_name="smoke", user_id="u", session_id=s.id)
    print(f"  state={dict(s.state)}  ({time.time()-t:.1f}s)")


async def main():
    print("== output_schema")
    a = LlmAgent(name="judge", model=llm, instruction="Judge whether the evidence supports the claim. Reply as JSON.",
                 output_schema=Verdict, output_key="verdict")
    await run(a, "Claim: Perseverance landed on Mars in 2021. Evidence: NASA's Perseverance rover landed on Mars in February 2021.")
    print("== tools")
    b = LlmAgent(name="checker", model=llm, tools=[search_kb],
                 instruction="You are a fact checker. You MUST call the search_kb tool first with a short query. After you see the results, answer in one sentence whether the claim is supported.")
    await run(b, "Claim: Perseverance landed on Mars in 2021.")
    print("tool calls:", calls)

asyncio.run(main())
