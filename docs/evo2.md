# Evo²: co-evolving the agent's harness and weights

Evo² (arXiv [2610.13025](https://arxiv.org/abs/2610.13025)) improves an LLM agent by searching over its
**harness** (prompts, tools, settings) with an **evolver** model, and then training the evolver on its own
search trajectories. No code was released with the paper, so `evo/` is an implementation written from the
paper, scaled down to one laptop GPU and extended in one way (we also fine-tune the task model).

## What the paper does, and what we implemented

| Paper | Here | File |
|---|---|---|
| The harness is the unit of evolution | `harness/*.yaml`: every prompt, tool description, budget, retrieval and sampling knob of the agent, validated by pydantic | `src/podcast_agent/harness.py` |
| Edits to the harness | A small edit language: `set_param`, `replace_in_prompt`, `append_to_prompt`, `toggle_step`, `add_guardrail`; strict validation | `evo/genome.py` |
| One evolver model, two roles: **Proposer** (writes edits) and **Reflector** (keeps or revises them) | Both are ADK `LlmAgent`s on the same model (Gemma 4 E2B), with `output_schema` so Ollama constrains the JSON; enum fields for prompt and parameter names | `evo/search.py` |
| Tree search over harness versions; parent sampling `w(h) = ε + \|V̂(h) − ½\|` | Same, ε = 0.1 | `Evo.sample_parent` |
| Reward per edit `r = V̂(h′) − V̂(h)`; malformed edits get a penalty | Same; malformed = −0.1 (bad JSON, unknown path, out-of-range value, `old` text not found exactly once, no-op edit, junk text) | `Evo.try_edits` |
| Fitness V̂ | The eval score on 4 **search** episodes (never the held-out ones): verdict accuracy, evidence recall, quote validity, summary key-point coverage, structure | `src/podcast_agent/evaluate.py` |
| K proposals per parent form a GRPO group | K = 4 | `Evo.expand` |
| Train the evolver: SFT warm start, then GRPO over both roles ("EvoTIDE") | SFT on positive-reward edits (+ well-formed edits rejection-sampled from the base evolver, weight 0.5), then GRPO with group-normalized advantages, ratio clipping and a KL penalty to the base | `evo/train_evolver.py`, `evo/lora.py` |
| Task model is frozen | **Extension:** we also fine-tune the task model with rejection-sampling fine-tuning: outputs of the best harness on the search split that passed the critic **and** agree with gold labels | `evo/train_executor.py` |
| 27B models | Gemma 4 E2B for both roles, 4-bit LoRA on an 8 GB RTX 5060 | `evo/lora.py` |

## Differences from the paper (deliberate, for time and hardware)

- **Off-policy GRPO.** The paper runs GRPO online. We log trajectories during the search (sampled from the
  quantized Ollama model at temperature 0.9) and train afterwards; the importance ratio uses this policy's
  log-probs before the update as "old" log-probs. This is an approximation and is documented in the code.
- **One search round** with 12 expansions, plus a validity evaluation of the trained evolver; multiple rounds,
  crossover and ablations are future work.
- **Config-level edits only.** The edit language changes the YAML harness, not Python code. Code-level edits
  (letting the evolver patch `pipeline.py`) are the obvious next step and need sandboxing.
- **Small fitness set** (4 short episodes): V̂ differences of about ±0.02 are within noise. Results on the
  held-out split are the honest measure, and they are small samples too.

## Running it

```bash
# 1. search (Ollama running): writes evo/runs/round1/{trajectories.jsonl,tree.json} and harness/evolved.yaml
uv run podcast-agent evo run --iterations 12 --group 4

# 2. data for training
PODCAST_RECORD=evo/data/executor_samples.jsonl uv run podcast-agent run data/search -H harness/evolved.yaml
uv run python -m evo.sample --split train --n 8      # well-formed proposals from the base evolver
uv run python -m evo.sample --split eval --n 4       # validity baseline on held-out parents

# 3. train (unload Ollama first: `ollama stop gemma4:e2b`), training venv from evo/requirements-train.txt
.venv-train/Scripts/python -m evo.train_evolver
.venv-train/Scripts/python -m evo.train_executor
.venv-train/Scripts/python -m evo.export --adapter evo/checkpoints/evolver-r1 --tag podcast-evolver:r1
.venv-train/Scripts/python -m evo.export --adapter evo/checkpoints/executor-r1 --tag podcast-exec:r1

# 4. compare on held-out
uv run podcast-agent eval -H harness/evolved.yaml --model ollama_chat/podcast-exec:r1 --results eval/results/evolved_exec.md
uv run python -m evo.sample --split eval --n 4 --model ollama_chat/podcast-evolver:r1
```

## Results

EVO_RESULTS_PLACEHOLDER

## What we learned

LEARNED_PLACEHOLDER
