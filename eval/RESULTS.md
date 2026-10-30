# Evaluation results

All runs: Gemma 4 E2B through Ollama, temperature 0, same code. Held-out = 5 episodes / 25 gold claims (never used for search or training); search split = 10 episodes used by Evo². Per-configuration details (every claim, per episode) are in [`results/`](results/).

## Configurations

| Configuration | Model | V̂ | verdict accuracy | evidence recall | quote validity | keypoint coverage | structure | claim recall |
|---|---|---|---|---|---|---|---|---|
| [Base harness, numeric check off](results/heldout_base_no_numeric.md) | `gemma4:e2b` | 0.869 | 0.71 | 0.92 | 1.00 | 1.00 | 1.00 | 0.80 |
| [**Base harness (default)**](results/heldout_base.md) | `gemma4:e2b` | 0.877 | 0.72 | 0.92 | 1.00 | 1.00 | 1.00 | 0.80 |
| [Evo² round-1 harness](results/heldout_evolved.md) | `gemma4:e2b` | 0.887 | 0.74 | 0.94 | 1.00 | 1.00 | 1.00 | 0.82 |
| [Base harness · q8 base control](results/heldout_base_q8-control.md) | `podcast-base:q8r` | 0.887 | 0.74 | 0.94 | 1.00 | 1.00 | 1.00 | 0.82 |
| [Base harness · q8 task-model LoRA](results/heldout_base_exec-r1.md) | `podcast-exec:r1` | 0.869 | 0.71 | 0.94 | 1.00 | 0.97 | 1.00 | 0.79 |
| [Evo² harness · q8 base control](results/heldout_evolved_q8-control.md) | `podcast-base:q8r` | 0.879 | 0.74 | 0.94 | 1.00 | 0.96 | 1.00 | 0.82 |
| [Evo² harness · q8 task-model LoRA](results/heldout_evolved_exec-r1.md) | `podcast-exec:r1` | 0.869 | 0.71 | 0.94 | 1.00 | 0.97 | 1.00 | 0.79 |
| [Search split · base, numeric check off](results/search_base_no_numeric.md) | `gemma4:e2b` | 0.883 | 0.77 | 0.90 | 1.00 | 0.95 | 1.00 | 0.89 |
| [Search split · base](results/search_base.md) | `gemma4:e2b` | 0.890 | 0.79 | 0.90 | 1.00 | 0.95 | 1.00 | 0.89 |

## Paired comparisons (95% bootstrap CI)

| Comparison | ΔV̂ (over episodes) | Δ verdict accuracy (over claims) |
|---|---|---|
| Numeric check (held-out) | +0.007 [+0.000, +0.022] | +0.040 [+0.000, +0.120] (25 claims) |
| Numeric check (search split) | +0.007 [+0.000, +0.020] | +0.019 [+0.000, +0.056] (54 claims) |
| Evo² round-1 harness vs base | +0.011 [+0.000, +0.032] | +0.040 [+0.000, +0.120] (25 claims) |
| Task-model LoRA vs q8 control, base harness | -0.018 [-0.041, +0.000] | -0.040 [-0.120, +0.000] (25 claims) |
| Task-model LoRA vs q8 control, evolved harness | -0.010 [-0.023, +0.000] | -0.040 [-0.120, +0.000] (25 claims) |

An interval that contains 0 means the difference is not distinguishable from noise at this sample size.

## Evo² evolver: well-formed edit rate on held-out parent harnesses

| Evolver model | Valid edit sets | Rate |
|---|---|---|
| `ollama_chat/gemma4:e2b` | 3/16 | 19% |
| `ollama_chat/podcast-base:q8r` | 11/64 | 17% |
| `ollama_chat/podcast-evolver:r1` | 42/64 | 66% |

Same proposer prompts (parents never used for evolver training), 8 samples each at temperature 1.0.

## Retrieval: BM25 vs TF-IDF (search split, no LLM)

| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |
|---|---|---|---|---|
| BM25 | 0.846 | 1.000 | 0.821 | 1.000 |
| TF-IDF | 0.821 | 1.000 | 0.718 | 1.000 |
