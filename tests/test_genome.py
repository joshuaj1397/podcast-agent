import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evo import genome  # noqa: E402


def test_valid_edits_apply(spec):
    child = genome.apply(spec, [
        {"op": "set_param", "path": "factcheck.sample_temperature", "value": "0.2"},
        {"op": "replace_in_prompt", "prompt": "judge", "old": "You are a careful fact-checker.",
         "new": "You are a meticulous fact-checker."},
        {"op": "add_guardrail", "text": "Quote numbers exactly as the evidence states them."},
    ], "child")
    assert child.version == "child" and child.factcheck.sample_temperature == 0.2
    assert "meticulous" in child.prompts.judge
    assert len(genome.diff(spec, child)) == 3


@pytest.mark.parametrize("edit, message", [
    ({"op": "set_param", "path": "claims.candidate_max", "value": "50"}, "outside"),
    ({"op": "set_param", "path": "model", "value": "x"}, "not editable"),
    ({"op": "replace_in_prompt", "prompt": "judge", "old": "not in the prompt", "new": "x"}, "exactly once"),
    ({"op": "append_to_prompt", "prompt": "notes", "text": "notes"}, "at least"),
    ({"op": "toggle_step", "step": "summarize", "enabled": False}, "required"),
    ({"op": "toggle_step", "step": "claims", "enabled": True}, "change nothing"),
    ({"op": "explode"}, "malformed"),
])
def test_malformed_edits_raise(spec, edit, message):
    with pytest.raises(genome.EditError, match=message):
        genome.apply(spec, [edit], "child")
