# Podcast Agent

An agentic system that turns raw podcast transcripts into show notes for an ad agency:

1. **Summary** of each episode (200–300 words): core themes, key discussions, outcomes and opinions.
2. **Key notes**: 🔹 top 5 takeaways, 💬 notable quotes with timestamps, 🧭 topic tags.
3. **Fact-check table**: every checkable claim is marked ✅ verified / ⚠ possibly outdated or inaccurate / ❓ unverifiable, with a confidence score and the cited evidence.

It runs fully locally: **Google ADK** agents on **Gemma 4 E2B** (via Ollama), **BM25** retrieval over a local knowledge base plus a mocked web-search API, and a CLI. It also includes **Evo²**, an implementation of harness-and-weights co-evolution (arXiv 2610.13025), which improves the agent's prompts/settings and fine-tunes the model.

- Sample outputs: [`out/`](out/) (`report.md`, `report.json`, `trace.jsonl` per episode)
- Evaluation: [`eval/RESULTS.md`](eval/RESULTS.md)
- Deployment strategy (AWS, diagram + narrative): [`docs/deployment.md`](docs/deployment.md), Terraform in [`infra/terraform/`](infra/terraform/)

---

## Quick start

### Option A: Docker (everything in containers)

```bash
docker compose up -d ollama                       # model server (pulls the image)
docker compose run --rm agent run data/heldout    # first run pulls gemma4:e2b (4.6 GB) automatically
```

Add the GPU override when an NVIDIA GPU is available (Docker Desktop + WSL2, or the NVIDIA Container Toolkit on Linux):

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d ollama
docker compose -f compose.yaml -f compose.gpu.yaml run --rm agent run data/heldout
```

Already running Ollama on the host? Skip the Ollama container:

```bash
docker compose run --rm --no-deps -e OLLAMA_API_BASE=http://host.docker.internal:11434 agent run data/heldout
```

Outputs appear in `./out/<episode_id>/`.

> **Memory:** the Ollama container needs about **4 GB of RAM** for Gemma 4 E2B; give Docker 6 GB or more. Docker Desktop's Hyper-V backend defaults to 2 GB: raise it under *Settings → Resources → Memory*, or use the host-Ollama shortcut above. With too little memory Ollama logs `llama-server terminated … signal: killed`. The agent then still writes a report, marked `degraded`, built from its deterministic fallbacks.
>
> **Speed:** with a GPU an episode takes 10–30 s; on CPU only, expect a few minutes per episode. The committed `out/` reports were replayed from the evaluation run's response cache, so their `stats.seconds` are near zero.

### Option B: local Python

Requires [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com) ≥ 0.40.

```bash
ollama pull gemma4:e2b
uv sync
uv run podcast-agent run data/heldout -v          # all held-out episodes, verbose
uv run podcast-agent run data/heldout/ep004_mars_noisy.txt
uv run pytest                                     # offline tests (FakeLlm, no Ollama needed)
```

### CLI

| Command | What it does |
|---|---|
| `podcast-agent run <files or dirs> [-H harness.yaml] [--only factcheck] [-e ep004] [--model ollama_chat/gemma4:e4b] [--no-cache] [-v]` | Process transcripts; write `out/<ep>/report.{json,md}` and `trace.jsonl` |
| `podcast-agent eval [--split heldout\|search\|all] [-H harness.yaml]` | Run and score against `eval/gold/`; writes `eval/RESULTS.md` |
| `podcast-agent kb-search "mars sample return" [--method tfidf] [--kind web]` | Query the knowledge base the way the research agent does |
| `podcast-agent evo run --iterations 12 --group 4` | Evo² tree search over harness edits |

Input formats: the JSON format of the samples, or text lines like `[00:02:14] Host: ...` with optional `# title: ...` header lines (see `data/heldout/ep004_mars_noisy.txt`).

---

## How it works

```
                 ┌─────────────────────────── harness/base.yaml (prompts, budgets, tools, guardrails) ──┐
transcript ──► ingest ──► PLANNER agent ──► plan validated by code (required steps, DAG, budget caps)       │
 (.json/.txt)  parse, speakers,   │                                                                        │
               fillers, glossary  ├─► SUMMARIZER agent ─┐                                                  │
                                  ├─► NOTES agent ──────┤◄── CRITIC: checks every output; re-prompts with  │
                                  ├─► CLAIMS agent ─────┤    the problems (≤2x); else deterministic       │
                                  └─► per claim:        │    fallback + `degraded` flag                    │
                                       RESEARCH agent (ReAct: search_kb / search_web tools, guarded)      │
                                       → JUDGE agent × N samples (self-consistency) → ✅/⚠/❓ + confidence │
                                                         └──► render: report.json, report.md, trace.jsonl
```

| Step | Who | Notes |
|---|---|---|
| ingest | code | `.json`/`.txt` → turns `T01..` with exact timestamps; full speaker names; filler words removed; glossary fixes known ASR errors (`NASSA` → `NASA`) |
| plan | **Planner** `LlmAgent` (`output_schema=Plan`) | Sees an episode profile (length, claim candidates, numbers, noise) and chooses steps, claim budget, whether to use web search, and a summary focus. Code enforces invariants and logs every correction (`planner omitted X; inserting`). |
| summarize | **Summarizer** `LlmAgent` | Critic checks 200–300 words; re-prompts with the exact word count. |
| notes | **Notes** `LlmAgent` | Quotes are picked **by turn id** from a TF-IDF shortlist; text and timestamp are copied from the transcript by code, so quotes cannot be hallucinated. TF-IDF keyword hints for tags. |
| claims | **Claims** `LlmAgent` | A regex pre-filter shortlists turns (numbers, entities, claim words); the model writes self-contained claims and types them: factual / statistic / prediction / anecdote / opinion. Opinions are dropped, and so are claims about the show itself ("Sarah is hosting the podcast"). |
| fact-check | **Research** `LlmAgent` with tools + **Judge** `LlmAgent` | Predictions and anecdotes → ❓ by rule. Otherwise: (1) the research agent searches in a ReAct loop; a `before_tool_callback` blocks repeated queries and enforces the search budget, and a BM25 backstop also searches the claim text itself. (2) The judge labels the evidence `supported / contradicted / outdated / insufficient`, sampled N times. (3) If the KB could not settle the claim and the plan had web search off, the agent **escalates** to web search and judges again. (4) A **numeric check** in code turns "supported" into ⚠ when the claim's numbers match none in the cited evidence. |
| render | code | JSON + Markdown report; `trace.jsonl` with every plan, thought, action, observation and result |

### Design decisions

- **Small model, made reliable by structure.** Gemma 4 E2B is a 2B-effective model that runs on a laptop GPU. Every LLM output goes through an ADK `output_schema`, which Ollama enforces with **constrained decoding**; ids are `Literal[...]` enums built per episode/claim, so the model can only cite turns and evidence that exist. `reasoning` is the first field of every schema (think before answering). Temperature 0 and a fixed seed make runs repeatable, and an on-disk response cache makes re-runs instant.
- **Classic IR and plain code where they beat the LLM.** Evidence retrieval uses BM25 (`rank_bm25`), measured against TF-IDF in `eval/RESULTS.md`. TF-IDF shortlists quote candidates and tag hints. Mapping a judgement to ✅/⚠/❓, computing confidence and comparing numbers are fixed code, not the model.
- **The plan is a hypothesis, not a contract.** The planner decides from a profile before any claim is extracted, so some of its choices are under-informed (for example "no web search needed"). The agent revisits them when evidence says so (web escalation), and code enforces the invariants the planner may forget.
- **Two agents for fact-checking instead of one.** ADK supports tools and `output_schema` on one agent, but it implements that by adding a `set_model_response` tool, which a 2B model fills unreliably. Splitting into a tool-using researcher and a schema-constrained judge keeps both reliable.
- **The orchestrator is plain Python** (`pipeline.py`): the step order, dependency rules and critic loop are in one readable place, and each LLM step is an ADK `LlmAgent` built from the harness. A step can be re-run alone (`--only factcheck`).
- **Everything tunable lives in `harness/base.yaml`**: prompts, tool descriptions, budgets, retrieval method and k, verdict samples, guardrails, the ASR glossary. It is validated by pydantic (`extra="forbid"`). Changing behaviour live usually means editing this file, not code.

### Fact-check verdicts and confidence

| Judge label | Shown as |
|---|---|
| supported | ✅ Verified true |
| contradicted, outdated | ⚠ Possibly outdated / inaccurate |
| insufficient (or no evidence found) | ❓ Unverifiable |
| claim type prediction / anecdote | ❓ Unverifiable (rule, no search) |

Confidence (`factcheck.py`):

- ✅ / ⚠: `clip(0.20 + 0.45·consistency + 0.35·support, 0.05, 0.97)`
- ❓ judged: `min(0.5, 0.20 + 0.30·consistency)`; ❓ by rule (prediction/anecdote): `0.40`

where **consistency** is the share of the N judge samples that agree with the majority label, and **support** is the reliability of the best cited source (curated KB 1.0, high 0.85, medium 0.6, low 0.3). A verdict that cites no evidence is downgraded to ❓.

### Knowledge sources

- `kb/facts.jsonl`: 68 curated, dated fact entries (`as_of`), used for "outdated" judgements.
- `kb/search_corpus.jsonl`: 160 mock web results with a `reliability` rating, including stale and low-quality items on purpose. Fault injection: `factcheck.web_fail_rate` in the harness.

Both are deliberately broad (a fact-check desk's KB), not tuned to the sample episodes. Entries marked "(mock)" are stand-ins.

---

## Evaluation

`eval/gold/*.yaml` hold gold labels for 15 episodes (79 claims): 5 **held-out** (the 3 provided samples, a noisy `.txt` episode with the assignment's NASA/Bitcoin examples, and a claims-dense episode) and 10 **search** episodes used only for Evo². The score V̂ ∈ [0, 1]:

`V̂ = 0.40·verdict accuracy + 0.15·evidence recall + 0.10·quote validity + 0.20·summary key-point coverage + 0.15·structure`

**Held-out results** (5 episodes, 25 gold claims, never used for search or training; Gemma 4 E2B via Ollama). Full tables, every claim, and bootstrap confidence intervals: [`eval/RESULTS.md`](eval/RESULTS.md).

| Configuration | V̂ | Verdict acc. | Evidence recall | Quote validity | Key points | Structure | Claim recall |
|---|---|---|---|---|---|---|---|
| Base harness, numeric check off | 0.869 | 0.71 | 0.92 | 1.00 | 1.00 | 1.00 | 0.80 |
| **Base harness** (default) | **0.877** | 0.72 | 0.92 | 1.00 | 1.00 | 1.00 | 0.80 |
| Evo² round-1 harness (`harness/evolved.yaml`) | **0.887** | 0.74 | 0.94 | 1.00 | 1.00 | 1.00 | 0.82 |
| Base harness · task-model LoRA (`podcast-exec:r1`) | 0.869 | 0.71 | 0.94 | 1.00 | 0.97 | 1.00 | 0.79 |
| Base harness · its exact control (`podcast-base:q8r`) | 0.887 | 0.74 | 0.94 | 1.00 | 1.00 | 1.00 | 0.82 |

On the 10 search episodes the default harness scores 0.890 (0.883 with the numeric check off).

**Retrieval, BM25 vs TF-IDF** (no LLM; gold evidence among 228 KB + web documents; all 57 gold claims with evidence):

| Query | BM25 R@1 | TF-IDF R@1 | BM25 R@3 | TF-IDF R@3 |
|---|---|---|---|---|
| clean gold claim text | 0.807 | 0.807 | 1.000 | 1.000 |
| raw transcript turn (fillers, ASR errors) | **0.807** | 0.754 | 1.000 | 1.000 |

The two tie on clean queries, and BM25 holds up better on noisy ones; with this small KB both saturate at k = 3. On the 18 held-out claims alone TF-IDF is slightly ahead at R@1 (0.78 vs 0.72), so this is a modest edge, not a decisive one. BM25 is the default because its term-frequency saturation and length normalisation suit short, keyword-heavy fact entries; `retrieval.method: tfidf` switches it.

How to read this:

- **Structure is solved by construction.** Quotes are always verbatim with the right timestamps, summaries are 200–300 words, there are exactly 5 takeaways, and every key point is covered. This comes from schema-constrained decoding, the critic loop and copy-by-id.
- **Fact-checking is the hard part for a 2B model** (verdict accuracy 0.71–0.74). Most remaining errors are claims the extractor merges or misses, and *outdated* statistics the judge calls "supported" because the evidence is about the same subject.
- **The bigger wins were agent design, not tuning.** Two changes lifted held-out V̂ from 0.839 to 0.877 (measured just before and after adding them): escalating to web search when the KB can't settle a claim, and dropping claims about the show itself. The **numeric check** (code that turns "supported" into ⚠ when the claim's numbers match none in the evidence) adds a little on both splits and has never flipped a correct verdict. I designed these three after reading held-out errors, so the search-split numbers are the cleaner evidence for them.
- **Every difference between configurations is within noise.** With 5 episodes / 25 claims, one claim is ±0.04 verdict accuracy, and every paired bootstrap interval in `eval/RESULTS.md` touches zero. Read the deltas as directional.

---

## Evo²: evolving the harness and the weights

[Evo²](https://arxiv.org/abs/2610.13025) improves an agent by searching over its harness with an *evolver* model, and then training that evolver on its own search trajectories. No code was released with the paper, so [`evo/`](evo/) implements it from the paper on one 8 GB laptop GPU, with one extension: the task model is fine-tuned too. Full write-up: [`docs/evo2.md`](docs/evo2.md).

- **Genome and edits** (`evo/genome.py`): the harness YAML, and a small validated edit language (`set_param`, `replace_in_prompt`, `append_to_prompt`, `toggle_step`, `add_guardrail`). Malformed edits get reward −0.1.
- **Tree search** (`evo/search.py`): one evolver model (Gemma 4 E2B) in two ADK roles. The **Proposer** writes 4 edit sets per parent; the **Reflector** keeps them or writes a revised set. Parents are sampled with `w(h) = ε + |V̂(h) − ½|`, and the reward is `V̂(child) − V̂(parent)`. Fitness is the eval score on 4 **search** episodes; held-out episodes are never used.
- **Training** (`evo/lora.py`, 4-bit LoRA on the RTX 5060):
  - Evolver: SFT on edits that improved fitness, plus well-formed edits rejection-sampled from the base evolver. Then GRPO over the logged groups (off-policy, ratio-clipped, KL to the base).
  - Task model: rejection-sampling fine-tuning on the best harness's own outputs on the search split. Only outputs that passed the critic **and** agree with gold labels are kept: 135 of 157.
- **Serving the tuned models**: merged adapters, converted to GGUF with llama.cpp, then `ollama create` (`evo/export.py`).

**Results** (details in [`docs/evo2.md`](docs/evo2.md) and [`eval/RESULTS.md`](eval/RESULTS.md)):

| | Before | After |
|---|---|---|
| Harness search, fitness on 4 search episodes (round 1, 12 expansions × 4 proposals; agent code at search time) | 0.731 | **0.797** |
| ...the same harness on held-out episodes | 0.877 | 0.887 (within noise) |
| Evolver: well-formed edit sets on 2 held-out parent harnesses (64 samples each, vs its exact q8 control) | 11/64 (17 %) | **42/64 (66 %)**, Fisher p < 10⁻⁷ |
| Task model (RFT LoRA) on held-out, vs its exact q8 control | 0.887 | 0.869 (no gain; within noise) |

What worked, and what didn't:

- **The harness search found a real but small improvement.** It raised `claims.candidate_max` from 25 to 40, which lets more claim candidates reach the extractor. It held up on unseen episodes, but within noise.
- **Training the evolver is the clearest effect.** It more than doubled the share of well-formed edits, which is what makes the next search round cheaper.
- **A 2B evolver writes mostly malformed edits, and padded its valid ones with junk.** It appended a stray `notes` line to a prompt and added `judge` as a guardrail; both are visible in `harness/evolved.yaml`, which I left exactly as the search produced it. That led to two fixes in the evolver's *own* interface: a discriminated-union JSON schema (Ollama's grammar then only allows the fields each op uses) and a minimum length for appended text. That's the paper's point in miniature: the evolver's harness matters as much as its weights.
- **Fine-tuning the task model on its own filtered outputs (135 examples) did not help.** A first comparison suggested +0.08, but that control had been converted from the original checkpoint, which keeps 60 unused KV-shared tensors, so it was not the same model. Against an exact control (the base re-saved through the identical path), the LoRA is slightly worse, within noise. Self-distillation on this little data mostly re-teaches what the model already does.
- At this scale, off-policy GRPO on 24 examples and SFT on 11 demonstrate the loop end to end; they are not a converged result.

---

## Repository layout

```
src/podcast_agent/   cli.py pipeline.py agent_factory.py tools.py factcheck.py critic.py
                     ingest.py retrieval.py llm.py trace.py render.py models.py harness.py evaluate.py
harness/             base.yaml (the genome)  evolved.yaml (Evo² output)
data/heldout/        ep001–ep005 (eval only)      data/search/  s01–s10 (Evo² fitness)
kb/                  facts.jsonl  search_corpus.jsonl
eval/                gold/*.yaml  RESULTS.md
evo/                 genome.py search.py lora.py train_evolver.py train_executor.py export.py  runs/
out/                 sample outputs for every episode
infra/terraform/     AWS modules + envs/dev       docs/deployment.md
tests/               offline tests (FakeLlm)
```

## Changing things quickly

| Want to… | Do this |
|---|---|
| Change a prompt, budget, k, samples | edit `harness/base.yaml` (or copy it and pass `-H my.yaml`) |
| Use a bigger model | `--model ollama_chat/gemma4:e4b` (or set `model:` in the harness) |
| Re-run only fact-checking | `podcast-agent run data/heldout -e ep004 --only factcheck` |
| Add a knowledge-base fact | append a line to `kb/facts.jsonl`; check with `podcast-agent kb-search "..."` |
| Add an output field | add it to `models.Report`, fill it in `pipeline.assemble`, print it in `render.py` |
| Add a pipeline step | add `step_<name>` in `pipeline.py`, add the name to `STEP_ORDER` and `harness.StepName` |
| Run without cache | `--no-cache` |
