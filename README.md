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
docker compose run --rm agent run data/heldout    # first run pulls gemma4:e2b (~7 GB) automatically
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
| claims | **Claims** `LlmAgent` | A regex pre-filter shortlists turns (numbers, entities, claim words); the model writes self-contained claims and types them: factual / statistic / prediction / anecdote / opinion. Opinions are dropped. |
| fact-check | **Research** `LlmAgent` with tools + **Judge** `LlmAgent` | Predictions and anecdotes → ❓ by rule. Otherwise the research agent searches (ReAct loop; a `before_tool_callback` blocks repeated queries and enforces the search budget), a BM25 backstop searches the claim text itself, then the judge labels the evidence `supported / contradicted / outdated / insufficient`, sampled N times. |
| render | code | JSON + Markdown report; `trace.jsonl` with every plan, thought, action, observation and result |

### Design decisions

- **Small model, made reliable by structure.** Gemma 4 E2B is a 2B-effective model that runs on a laptop GPU. Every LLM output goes through an ADK `output_schema`, which Ollama enforces with **constrained decoding**; ids are `Literal[...]` enums built per episode/claim, so the model can only cite turns and evidence that exist. `reasoning` is the first field of every schema (think before answering). Temperature 0 and a fixed seed make runs repeatable, and an on-disk response cache makes re-runs instant.
- **Classic IR where it beats the LLM.** Evidence retrieval uses BM25 (`rank_bm25`), measured against TF-IDF in `eval/RESULTS.md`. TF-IDF shortlists quote candidates and tag hints. Mapping a judgement to ✅/⚠/❓ and computing confidence is fixed code, not the model.
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

RESULTS_PLACEHOLDER

---

## Evo²: evolving the harness and the weights

EVO_PLACEHOLDER

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
