import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from pydantic import ValidationError

from podcast_agent import harness
from podcast_agent.llm import request_key


def test_base_harness_loads_and_fills_placeholders(spec):
    text = spec.instruction("summarizer", min_words=200, max_words=300, target_words=250)
    assert "200-300 words" in text and "Rules:" in text
    assert "{today}" in spec.instruction("judge")  # unknown placeholders are left alone


def test_harness_rejects_unknown_fields(spec):
    data = spec.model_dump()
    data["limits"]["bogus"] = 1
    with pytest.raises(ValidationError):
        harness.HarnessSpec.model_validate(data)


def test_cache_key_ignores_function_call_ids():
    def req(call_id):
        call = types.FunctionCall(id=call_id, name="search_kb", args={"query": "mars"})
        return LlmRequest(contents=[types.Content(role="model", parts=[types.Part(function_call=call)])])

    assert request_key("m", req("adk-1")) == request_key("m", req("adk-2"))
    other = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hi")])])
    assert request_key("m", other) != request_key("m", req("adk-1"))


def test_worker_parses_s3_events_and_job_ids_are_stable():
    from podcast_agent.worker import job_id, parse_s3_event

    eventbridge = {"detail": {"bucket": {"name": "uploads"}, "object": {"key": "client-a/ep%20004.txt"}}}
    s3_notification = {"Records": [{"s3": {"bucket": {"name": "uploads"}, "object": {"key": "x/ep.json"}}}]}
    assert parse_s3_event(eventbridge) == ("uploads", "client-a/ep 004.txt")
    assert parse_s3_event(s3_notification) == ("uploads", "x/ep.json")
    assert job_id(b"abc", "base", "m") == job_id(b"abc", "base", "m") != job_id(b"abc", "evolved-r1", "m")
