# Evaluation results

All runs: Ollama, temperature 0, same code; Gemma 4 E2B unless the Model column says otherwise. Held-out = 5 episodes / 25 gold claims (never used for search or training); search split = 10 episodes used by Evo². Per-configuration details (every claim, per episode) are in [`results/`](results/).

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
| [**Evo² round-2 harness** (`harness/evolved_r2.yaml`)](results/heldout_evolved-r2_e2b.md) | `gemma4:e2b` | 0.924 | 0.82 | 0.96 | 1.00 | 1.00 | 1.00 | 0.87 |
| [Base harness · Gemma 4 E4B](results/heldout_base_e4b.md) | `gemma4:e4b` | 0.849 | 0.69 | 0.85 | 1.00 | 0.97 | 1.00 | 0.75 |
| [Evo² round-2 harness · Gemma 4 E4B](results/heldout_evolved-r2_e4b.md) | `gemma4:e4b` | 0.802 | 0.60 | 0.76 | 1.00 | 1.00 | 1.00 | 0.65 |
| [12B harness (`base_12b.yaml`: fixed claims examples) · Gemma 4 12B](results/heldout_base-12b_12b.md) | `gemma4:12b` | 0.768 | 0.52 | 0.83 | 1.00 | 0.93 | 1.00 | 0.58 |
| [Evo² round-3 harness · Gemma 4 12B](results/heldout_evolved-r3_12b.md) | `gemma4:12b` | 0.845 | 0.65 | 0.89 | 1.00 | 1.00 | 1.00 | 0.81 |
| [Search split · base, numeric check off](results/search_base_no_numeric.md) | `gemma4:e2b` | 0.883 | 0.77 | 0.90 | 1.00 | 0.95 | 1.00 | 0.89 |
| [Search split · base](results/search_base.md) | `gemma4:e2b` | 0.890 | 0.79 | 0.90 | 1.00 | 0.95 | 1.00 | 0.89 |
| [Search split · base · Gemma 4 E4B](results/search_base_e4b.md) | `gemma4:e4b` | 0.830 | 0.65 | 0.84 | 1.00 | 0.97 | 1.00 | 0.67 |
| [Search split · round-2 harness · E4B (selection data)](results/search_evolved-r2_e4b.md) | `gemma4:e4b` | 0.956 | 0.92 | 0.97 | 1.00 | 0.95 | 1.00 | 0.92 |
| [Search split · round-2 harness · E2B](results/search_evolved-r2_e2b.md) | `gemma4:e2b` | 0.916 | 0.83 | 0.93 | 1.00 | 0.98 | 1.00 | 0.88 |
| [Search split · base · Gemma 4 12B (one claim per episode)](results/search_base_12b.md) | `gemma4:12b` | 0.549 | 0.19 | 0.26 | 1.00 | 0.93 | 1.00 | 0.19 |
| [Search split · 12B harness · Gemma 4 12B](results/search_base-12b_12b.md) | `gemma4:12b` | 0.836 | 0.68 | 0.85 | 1.00 | 0.93 | 1.00 | 0.68 |
| [Search split · round-3 harness · 12B (selection data)](results/search_evolved-r3_12b.md) | `gemma4:12b` | 0.943 | 0.88 | 0.95 | 1.00 | 0.98 | 1.00 | 0.88 |

## Paired comparisons (95% bootstrap CI)

| Comparison | ΔV̂ (over episodes) | Δ verdict accuracy (over claims) |
|---|---|---|
| Numeric check (held-out) | +0.007 [+0.000, +0.022] | +0.040 [+0.000, +0.120] (25 claims) |
| Numeric check (search split) | +0.007 [+0.000, +0.020] | +0.019 [+0.000, +0.056] (54 claims) |
| Evo² round-1 harness vs base | +0.011 [+0.000, +0.032] | +0.040 [+0.000, +0.120] (25 claims) |
| Task-model LoRA vs q8 control, base harness | -0.018 [-0.041, +0.000] | -0.040 [-0.120, +0.000] (25 claims) |
| Task-model LoRA vs q8 control, evolved harness | -0.010 [-0.023, +0.000] | -0.040 [-0.120, +0.000] (25 claims) |
| Gemma 4 E4B vs E2B, base harness (held-out) | -0.027 [-0.059, -0.003] | -0.040 [-0.160, +0.080] (25 claims) |
| Gemma 4 E4B vs E2B, base harness (search split) | -0.060 [-0.138, +0.014] | -0.148 [-0.296, -0.019] (54 claims) |
| Evo² round-2 harness vs base, both on E4B (held-out) | -0.047 [-0.293, +0.117] | +0.080 [-0.160, +0.320] (25 claims) |
| E4B + round-2 harness vs E2B + base (held-out) | -0.074 [-0.330, +0.107] | +0.040 [-0.200, +0.280] (25 claims) |
| **Round-2 harness vs base, both on E2B (held-out)** | +0.047 [+0.000, +0.119] | +0.200 [+0.040, +0.360] (25 claims) |
| Round-2 harness vs base, both on E2B (search split) | +0.026 [-0.011, +0.065] | +0.037 [-0.056, +0.130] (54 claims) |
| Gemma 4 12B vs E2B, base harness (search split) | -0.340 [-0.390, -0.280] | -0.611 [-0.741, -0.463] (54 claims) |
| 12B + its harness (fixed examples) vs E2B + base (held-out) | -0.109 [-0.191, -0.027] | -0.200 [-0.400, +0.000] (25 claims) |
| Evo² round-3 harness vs 12B harness, both on 12B (held-out) | +0.077 [-0.073, +0.220] | +0.200 [+0.000, +0.400] (25 claims) |
| 12B + round-3 harness vs E2B + base (held-out) | -0.032 [-0.120, +0.031] | +0.000 [-0.200, +0.200] (25 claims) |

An interval that contains 0 means the difference is not distinguishable from noise at this sample size.

## Evo² evolver: well-formed edit rate on held-out parent harnesses

| Evolver model | Valid edit sets | Rate |
|---|---|---|
| `ollama_chat/gemma4:e2b` | 3/16 | 19% |
| `ollama_chat/podcast-base:q8r` | 11/64 | 17% |
| `ollama_chat/podcast-evolver:r1` | 42/64 | 66% |

Same proposer prompts (parents never used for evolver training), 8 samples each at temperature 1.0.

## Seed robustness: base vs Evo² round-2 harness on Gemma 4 E2B

| Split | Harness | seed 7 | seed 8 | seed 9 | mean |
|---|---|---|---|---|---|
| heldout | base | 0.877 | 0.865 | 0.877 | **0.873** |
| heldout | Evo² round 2 | 0.924 | 0.864 | 0.901 | **0.896** |
| heldout | Δ (paired over 5 episodes) | | | | +0.023 [-0.027, +0.097] |
| search | base | 0.890 | 0.908 | 0.903 | **0.900** |
| search | Evo² round 2 | 0.916 | 0.915 | 0.905 | **0.912** |
| search | Δ (paired over 10 episodes) | | | | +0.012 [-0.013, +0.042] |

The seed changes only the sampled calls: the judge's extra self-consistency samples (temperature 0.7). Greedy calls (planner, summarizer, notes, claims, researcher, first judge sample) are unaffected, so this is a lower bound on run-to-run noise. Seed 8 and 9 details: [`results/seeds/`](results/seeds/).

## Retrieval: BM25 vs TF-IDF (search split, no LLM)

| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |
|---|---|---|---|---|
| BM25 | 0.846 | 1.000 | 0.821 | 1.000 |
| TF-IDF | 0.821 | 1.000 | 0.718 | 1.000 |
