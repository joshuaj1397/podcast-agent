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
# export: merge -> GGUF (llama.cpp) -> Ollama; see evo/export.py for why GGUF on Windows
.venv-train/Scripts/python -c "from pathlib import Path; from evo.export import merge; merge(Path('evo/checkpoints/executor-r1'), Path('evo/merged/podcast-exec-r1'))"
python llama.cpp/convert_hf_to_gguf.py evo/merged/podcast-exec-r1 --outtype q8_0 --outfile evo/gguf/podcast-exec-r1.gguf
.venv-train/Scripts/python -m evo.export --adapter evo/checkpoints/executor-r1 --tag podcast-exec:r1 --gguf evo/gguf/podcast-exec-r1.gguf --skip-merge
# (same for evolver-r1 -> podcast-evolver:r1, and the un-tuned base re-saved the same way -> podcast-base:q8r as the control)

# 4. compare on held-out
uv run podcast-agent eval -H harness/evolved.yaml --model ollama_chat/podcast-exec:r1 --results eval/results/evolved_exec.md
uv run python -m evo.sample --split eval --n 4 --model ollama_chat/podcast-evolver:r1
```

## Results

All agent numbers come from the same code and the same final evaluation run ([`eval/RESULTS.md`](../eval/RESULTS.md)).

**1. Harness search (round 1).** 12 expansions × 4 proposals, plus a Reflector call per expansion, gave 10 tree nodes.

| | |
|---|---|
| Fitness V̂ on the 4 search episodes | root 0.731 → best 0.797 (+0.066) |
| What the best node changed | `claims.candidate_max` 25 → 40 (more claim candidates reach the extractor), plus junk edits (below) |
| Same harness on the 5 held-out episodes | 0.877 → 0.887; the paired bootstrap CI touches 0 |
| Proposer edit sets that were well-formed | 7 / 48 (15 %) |
| Reflector revisions that were well-formed | 3 / 12 |

**2. Evolver training (SFT + off-policy GRPO).**

| | |
|---|---|
| SFT data | 4 positive-reward edits from the search (proposer + reflector) + 7 well-formed edits rejection-sampled from the base evolver on *train* parents |
| GRPO data | proposer groups from the search with non-zero reward variance; trajectories of the held-out *eval* parents are excluded |
| Training | 4-bit LoRA r=16, 3 SFT epochs (per-example loss ≈ 0.37 → 0.2–0.6; 8 examples, noisy) then 1 GRPO epoch over 8 examples; about 3 minutes on the RTX 5060 |
| Training data after holding out the eval parents | 30 of 60 logged calls; 8 SFT examples (1 positive-reward + 7 rejection-sampled), 8 GRPO examples in 6 groups |
| **Well-formed edit sets on the 2 held-out parent harnesses** (64 samples each, temperature 1.0) | exact q8 control 11/64 (17 %) → **evolver r1 42/64 (66 %)**, Fisher exact p = 3.7·10⁻⁸ |

This is the clearest Evo² effect here: a few minutes of training on a handful of the evolver's own good edits roughly quadrupled how often it writes an edit the harness can apply. Caveats: there are only 2 held-out parent harnesses (64 samples come from 2 prompts), and well-formed does not mean better. Whether r1's edits also raise fitness needs a second search round with the trained evolver, which is the next step.

**3. Task-model training (rejection-sampling fine-tuning).**

| | |
|---|---|
| Data | 157 sub-agent outputs recorded by running the best harness on the 10 search episodes; 135 kept (all planner/summarizer/notes/claims outputs that passed the critic, plus the 95/117 judge samples whose verdict matches gold) |
| Training | 4-bit LoRA r=16, 2 epochs |
| Held-out V̂, base harness | exact q8 control 0.887 → LoRA 0.869 |
| Held-out V̂, evolved harness | exact q8 control 0.879 → LoRA 0.869 |

The task-model LoRA did not help: it was slightly worse, within noise.

## What we learned

- **Measure against an exact control.** The first comparison of the task-model LoRA showed +0.08. That control had been converted to GGUF from the original checkpoint, which still contains the k/v projections of the 20 KV-shared layers (601 tensors). The merged LoRA models were re-saved through transformers, which drops those unused tensors (541). llama.cpp then ran two subtly different models. Re-saving the base through the identical path gave the real answer: no gain.
- **Watch for evaluation leakage in self-improvement loops.** The edit-validity eval uses parent prompts from the search. The first evolver checkpoint had been trained with GRPO on *all* search trajectories, including those prompts, so it was retrained with the eval parents held out before the reported numbers were taken.
- **A small evolver needs a well-designed interface.** With one flat edit schema, Gemma 4 E2B filled every field of every edit, copied text from the parameter listing into `old`, and padded valid edits with junk ("notes", "judge"). A discriminated-union schema, so constrained decoding only allows each op's own fields, plus a minimum-length rule, fixed most of that before any training. Training then raised validity further. The evolver's harness matters as much as its weights, which is the paper's thesis.
- **Self-distillation with little data mostly re-teaches what the model already does.** 135 filtered examples from the model's own outputs did not move held-out quality. A stronger teacher (a larger model's outputs, or human-corrected verdicts from the review queue) is the obvious next data source.
- **The fitness set is the bottleneck.** With 4 episodes (about 20 claims) one claim is ±0.02 V̂, so the search climbs partly on noise. The next steps are more search episodes, repeated evaluations of promising nodes, and an acceptance threshold above the noise floor.

## Implementation notes

- 8 GB VRAM: the 2.35B-parameter per-layer embedding table of Gemma 4 E2B stays on the CPU. accelerate's offload hook would copy the whole 4.4 GiB table to the GPU on every forward pass, which overflowed into shared memory and made one step take minutes. `evo/lora.py` removes the hook and runs the lookup on CPU, so a 4k-token step takes a few seconds with a peak of 6.3 GiB.
- Loss is computed only on completion tokens, with `logits_to_keep`, because the 262k vocabulary makes full logits huge. The grammar-forced opening `{` is excluded, and completions use the same 2-space JSON layout that Ollama's JSON mode produces.
- Ollama 0.40 on Windows imports safetensors only with its MLX runtime, which is unavailable there, and it has dropped LoRA adapters. Tuned models are therefore merged, converted with llama.cpp's `convert_hf_to_gguf.py` (q8_0) and registered with `ollama create`. Comparisons use a base converted the same way, never the stock `gemma4:e2b`.
