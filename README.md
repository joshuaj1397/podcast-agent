# Podcast Agent

An agentic system that turns raw podcast transcripts into show notes for an ad agency:

1. **Summary** of each episode (200–300 words): core themes, key discussions, outcomes and opinions.
2. **Key notes**: 🔹 top 5 takeaways, 💬 notable quotes with timestamps, 🧭 topic tags.
3. **Fact-check table**: every checkable claim is marked ✅ verified / ⚠ possibly outdated or inaccurate / ❓ unverifiable, with a confidence score and the cited evidence.
4. **Series summary** (≤ 4,000 characters, enforced in code) of a podcast series, updated incrementally as episodes are added, with every change and its reason logged. See [Series summary](#series-summary).

It runs fully locally: **Google ADK** agents on **Gemma 4 E2B** (via Ollama), **BM25** retrieval over a local knowledge base plus a mocked web-search API, and a CLI. It also includes **Evo²**, an implementation of harness-and-weights co-evolution (arXiv 2610.13025), which improves the agent's prompts/settings and fine-tunes the model.

- Sample outputs: [`out/`](out/) (`report.md`, `report.json`, `trace.jsonl` per episode); series summary: [`out/series/work_reimagined/series.md`](out/series/work_reimagined/series.md)
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
uv run podcast-agent run data/heldout/ep004_async_writing_culture.json
uv run pytest                                     # offline tests (FakeLlm, no Ollama needed)
```

### CLI

| Command | What it does |
|---|---|
| `podcast-agent run <files or dirs> [-H harness.yaml] [--only factcheck] [-e ep004] [--model ollama_chat/gemma4:e4b] [--no-cache] [-v]` | Process transcripts; write `out/<ep>/report.{json,md}` and `trace.jsonl` |
| `podcast-agent eval [--split heldout\|search\|all] [-H harness.yaml]` | Run and score against `eval/gold/`; writes `eval/RESULTS.md` |
| `podcast-agent series sync [-m series/x.yaml] [--until ep001] [--reuse-reports]` | Create or update the series summary from the manifest (episodes not yet in the series, in order) |
| `podcast-agent series add <transcript or episode id>` | Add an episode to the series (appends to the manifest), run its pipeline, update the summary |
| `podcast-agent series show` / `series rebuild` | Print summary, character count and history / replay the series from the stored digests |
| `podcast-agent series check` | Score the series state against `eval/gold/series_<id>.yaml` (revisions, conflicts, length, names) |
| `podcast-agent kb-search "mars sample return" [--method tfidf] [--kind web]` | Query the knowledge base the way the research agent does |
| `podcast-agent evo run --iterations 12 --group 4 [--evolver-model ...] [--episodes s01,s03] [--first-round 2] [--out h.yaml]` | Evo² tree search over harness edits |

Input formats: the JSON format of the samples, or text lines like `[00:02:14] Host: ...` with optional `# title: ...` header lines (see `data/search/s03_sports_recovery.txt`).

---

## Series summary

Episodes can belong to a **series**. The agent keeps a summary of the series as a whole and updates it each time an episode is added. **Work Reimagined** is the example series: ep001, ep004, ep005, ep006 and ep007 (ep002 and ep003 are standalone episodes and are not in it). The per-episode outputs are unchanged: each series episode still gets its own `out/<ep>/report.{json,md}`.

- Output: [`out/series/work_reimagined/series.md`](out/series/work_reimagined/series.md) and `series.json`. Both contain the summary, its character count, the episodes in the series and the update history with the reasoning behind each change.
- Console logs of each run: `out/series/work_reimagined/console-<ep>.log`. Structured traces: `traces/vNN-<ep>.jsonl`.

### Generate the initial series summary

The series is defined in a manifest, [`series/work_reimagined.yaml`](series/work_reimagined.yaml): its id, its title and its transcripts in air order. Membership is configuration, so nothing has to be inferred.

```bash
# Docker
docker compose run --rm agent series sync                   # every manifest episode not yet in the series, in order
docker compose run --rm agent series sync --until ep001     # or only the first one: the initial summary

# local
uv run podcast-agent series sync --until ep001
uv run podcast-agent series show                            # summary, character count, update history
uv run podcast-agent series check                           # score it against eval/gold/series_work_reimagined.yaml
```

### Add a new episode and trigger an update

```bash
uv run podcast-agent series add data/new/ep008_some_topic.json   # appends it to the manifest, then updates
uv run podcast-agent series add ep004                            # an episode already in the manifest: process it
docker compose run --rm agent series add data/heldout/ep006_return_to_office.json
```

`series add` runs the episode through the existing pipeline (summary, notes, fact-check), then updates the series summary. Each command is a separate process: the state is read from disk at the start and written at the end. In Docker, `out/` and `series/` are bind mounts, so the state survives the container. Use `--reuse-reports` to skip the episode pipeline when `out/<ep>/report.json` already exists. `series rebuild` replays the whole series from the stored episode digests (see below).

### How an update works

```
series add ep006
  1. episode pipeline   (unchanged)                      -> out/ep006/report.{json,md}
  2. extract  (LLM)     transcript + earlier episodes    -> digest: 3-10 points (theme / position / prediction /
                                                            figure / reference), each with its transcript turn, the
                                                            earlier episodes it answers and the earlier view it changes
     code checks: every substantive section covered, speakers named, one measured result per figure
  3. merge    (LLM)     current ledger + digest + hints  -> one operation per point, each with a reason:
                                                            add | revise <L-id> | reinforce <L-id>
     code resolves revise targets from the quoted earlier view, then applies the operations:
     a revised item is marked superseded (kept for history) and replaced
  4. write    (LLM)     current ledger + episode list    -> themes / evolution / connections (every sentence
                        + previous version + changes        cites its ledger ids) / arc line per episode
  5. enforce  (code)    every revised item covered; len(summary) <= 4,000, least important sentences dropped first
  6. save     (code)    series.json, series.md, versions/vNN.json, traces/vNN-ep006.jsonl   (atomic)
```

**The model decides, code resolves and enforces.** Every series step follows the rule the episode pipeline already uses (quotes chosen by turn id, evidence cited by id): the model makes the judgement, and code does what can be done exactly. Every rule below was added after reading a failing run (see [How the update logic was tuned](#results-on-work-reimagined-gemma-4-e2b)):

*Extraction*
- **Facts code can get exactly right are computed, not generated:** recurring guests (guest lists), recurring segments (the transcripts' `section` names) and earlier guests named in a new episode (for example "Mark Rivera told us…" in ep004). The arc lines name each episode's guests in code, so every guest is named.
- **Recall checks:** every point records the transcript turn it comes from (`turn_id`, restricted to valid ids). Every section where something substantive is said must contribute a point. Every measured value stated in the episode ("40 percent", "three weeks") must reach a point, because measured results are what later episodes update. If either check fails, the extract agent is re-prompted with what is missing and where.
- **Quality checks:** points must name their speaker (no bare quotes). A figure point with values in different units ("meetings cut 40 percent, onboarding three weeks") must be split, so that a later update to one number does not replace the others. If the model keeps it compound through its repairs, code splits it at clause boundaries. A "figure" without a number becomes a position, and a "theme" stating a measured value becomes a figure. Near-duplicate points are dropped.
- **`changes_earlier`:** a point that changes an earlier view restates it ("Mark Rivera predicted in ep001 that remote-first companies would dominate talent acquisition"). A statement that only repeats the point itself is discarded.

*Merge*
- **Hints:** for each new point, code lists the ledger items it *possibly relates to*: the items matching its `changes_earlier`, the same speaker's items of the same kind from the episode it answers (someone revising their own view rarely reuses its words), then BM25 matches.
- **The model decides *that* a point revises something; code resolves *which* item.** The small model reliably recognizes a revision but sometimes writes the wrong L-number. Code redirects a revise to the item its `changes_earlier` clearly matches. A newer figure goes to the figure whose value it restates: "dropped **from three weeks** to about two" updates the item that says three weeks, and that wording also turns an add into a revise. Code prefers an item that measures nothing else.
- **Rules that hold in any domain:** a figure only replaces a figure. A prediction never replaces or is replaced by a theme. A prediction is revised only by the person who made it, since someone else's prediction is a second view. When several points revise one item, the best-supported revise wins: the others become adds, or reinforce it if they repeat it.
- **A self-check against over-revising:** before choosing `revise`, the merge agent fills `old_still_current` ("is the old item still the latest word on its subject?"). A revise of an item it judged still current becomes an add, unless independent evidence backs it: the quoted earlier view, or a shared measured value.

*Writing*
- **Every summary sentence cites its ledger items** (`[L04, L12]`). Code removes the citations from the text but keeps them as **provenance** in `series.json` (`summary_sources`) and in `series.md`: summary sentence → ledger item → transcript turn and timestamp. Code also uses the citations for three things. It checks that every revised item appears in "How the ideas developed" (an uncited sentence that carries the item's words counts; otherwise re-prompt, then append the ledger's own sentence). It drops the least important sentences first when the summary is too long. And it flags sentences that name an episode their sources do not come from (`series check`).

For the first episode, the merge step is skipped: every point is added.

### Design decisions

**State persistence.** All state lives in one JSON file, `out/series/<id>/series.json`. It is also the required output. It holds the summary, character count, episodes, update history, the **ledger** (structured items behind the summary) and one **digest** per episode. Every update works on a copy of the state and writes it only at the end (temp file, then rename), so a failed or interrupted update leaves the previous version intact. Every version is also kept in `versions/vNN.json`, for diffs and rollback. A JSON file over a database: there is one writer, it is human-readable and it can be committed. In the AWS deployment ([`docs/deployment.md`](docs/deployment.md)) the same file would live in S3 with conditional writes (`If-Match`), so two workers cannot update a series at the same time.

**Update strategy: incremental operations on a ledger, with the prose rewritten from it.** The options were to edit the previous summary in place or to regenerate it from all episodes every time:

| | Edit the summary in place | Regenerate from all episodes | **Ledger operations + rewrite (chosen)** |
|---|---|---|---|
| Cost and latency per update | constant | grow with the series (O(n) per update) | **constant**: the merge sees the ledger (≤ 40 items) and one digest |
| Superseded information | hard to find inside prose; conflicting sentences survive | handled implicitly, not visible | **explicit**: `revise` marks the old item superseded, and the writer never sees it as current |
| "What changed and why" | needs a diff afterwards | needs a diff afterwards | **comes from the operations and their reasons** |
| Drift over many updates | high: every rewrite paraphrases the last one | none | low: facts live in the ledger, only the wording is rewritten |
| Consistency between versions | high | low: everything is reworded each time | medium; measured as the similarity to the previous version in every update record |

The prose is still rewritten on every update, because a 4,000-character summary is cheap to write and rewriting keeps it coherent. The facts behind it change only through logged operations. `series rebuild` replays the series from the stored digests. It is the reset if the ledger ever drifts, and the way to handle a reordered or removed episode (incremental updates assume episodes arrive in order). With temperature 0 and the response cache, a rebuild with unchanged prompts reproduces the same result.

**The character limit is enforced in code** (`series.enforce_length`), not only requested:

1. The writer is asked for about 3,400 characters (`series.target_chars`), with a budget per section.
2. The writer's output is checked before it is accepted: if the rendered summary is over 4,000 characters, the writer is re-prompted with the exact excess per section (the critic loop used everywhere in this agent).
3. If it is still too long, code trims it deterministically and keeps every section and every episode's arc line. In the section furthest over its share, it drops the least important sentence, judged by the ledger items it cites: uncited sentences first, sentences about revised items last. Then it shortens the longest arc lines, and finally cuts at a word boundary. (The first version dropped the *last* sentence of a section. Because the writer tells the evolution chronologically, that cut the newest episodes and the key revisions in 4 of 6 runs.)
4. An assertion makes it impossible to save a summary over the limit. The character count (`len()` of the exact saved text, spaces and headings included) is stored in `series.json`, printed after every update, shown in the `series.md` header and listed for every version in the update history.

As the series grows, the per-episode arc budget shrinks (`target × 27% / n`), and the ledger is capped at 40 current items: the least important are archived (fewest supporting episodes, then kind, then oldest).

All series settings and prompts are in the `series:` section of [`harness/base.yaml`](harness/base.yaml). For example, `max_chars: 2000` changes the limit.

### Results on Work Reimagined (Gemma 4 E2B)

**Headline: in 16 independent uncached builds of the series, all 9 checks passed in 13.** Mark's revision was found in 15/16, the onboarding revision in 14/16, and no build left two conflicting statements side by side (16/16).

The committed output was built in Docker, one container per episode (`docker compose run --rm agent series add ep001`, then ep004, ep005, ep006, ep007). Each update read the previous state from the `./out` bind mount:

| Version | Episode | Ledger operations | Characters | Trimmed by code | What changed (the update log's own words, shortened) |
|---|---|---|---|---|---|
| v1 | ep001 | 8 add | 2,274 | no | Initial summary: 8 ledger items |
| v2 | ep004 | 8 add | 3,504 | no | Writing-culture practices, efficiency metrics, predictions about async work |
| v3 | ep005 | 6 add, 1 revise, 2 reinforce | 3,846 | 4,340 → 3,846 | Tom Becker's caveat that outcomes alone can mask burnout revises Mark Rivera's outcomes-over-hours advice (L05 → L17) |
| v4 | ep006 | 5 add, 1 revise, 1 reinforce | 3,921 | 4,837 → 3,921 | **Mark Rivera's ep001 prediction that remote-first companies will dominate becomes intentional hybrid (L07 → L28)** |
| v5 | ep007 | 9 add, 1 revise | 3,908 | 5,566 → 3,908 | **Onboarding drops from three weeks (ep004) to about two (L12 → L31)**; AI-agent capabilities and predictions |

`podcast-agent series check` on this state: **9/9 checks passed**. All 14 summary sentences cite ledger items, and none names an episode its sources do not come from. The ep005 revision is not in the gold set. It is defensible, since Tom Becker qualifies Mark Rivera's advice, but it could just as well have been a new item next to Mark's. Each episode's console output is in `out/series/work_reimagined/console-<episode>.log`. The reasoning of every step is in `traces/`.

**How it is evaluated.** `podcast-agent series check` scores the state against [`eval/gold/series_work_reimagined.yaml`](eval/gold/series_work_reimagined.yaml). The 9 checks are:

- every episode is included, in order
- the summary is within 4,000 characters
- the guests and the hybrid revision are named
- the recurring guests are found
- the two places where the transcripts revise earlier information are revised in the ledger: in ep006, Mark Rivera turns his ep001 prediction that remote-first companies will dominate talent acquisition into intentional hybrid; in ep007, onboarding drops from three weeks (ep004) to about two
- key facts that nothing revises are still current, which catches over-revising and facts that were never extracted
- no superseded statement is still current next to its replacement

It also reports provenance: how many summary sentences cite ledger items, and whether any names an episode its sources do not come from.

Ollama is not bit-for-bit deterministic, even at temperature 0, so one passing run proves little. [`scripts/series_robustness.py`](scripts/series_robustness.py) builds the whole series N times with the response cache off. Only the series steps run, reusing the episode reports. It reports how often each check passes:

```bash
uv run python scripts/series_robustness.py --runs 16 [--model ollama_chat/gemma4:e4b]
```

**How the update logic was tuned.** Every rule in [How an update works](#how-an-update-works) came from reading the failing runs of a batch. All batches below are rescored against the final 9 checks:

| Iteration | What changed | Runs | Mark's revision | Onboarding revision | Key facts kept | All 9 checks |
|---|---|---|---|---|---|---|
| 5 | merge hints: the same speaker's earlier items | 3 | 1/3 | 3/3 | 1/3 | 1/3 |
| 6 | extraction asked to name the view it revises; guests named in arc lines by code | 3 | 3/3 | 2/3 | 2/3 | 0/3 |
| 7 | writer cites ledger ids; importance-aware trimming; revision-coverage check | 5 | 3/5 | 3/5 | 5/5 | 2/5 |
| 8 | `changes_earlier` field in extraction | 5 | 3/5 | 3/5 | 4/5 | 1/5 |
| 9 | code resolves revise targets from `changes_earlier` | 5 | 3/5 | 4/5 | 4/5 | 2/5 |
| 10 | self-quotes discarded; points must name their speaker; stricter evidence | 5 | 5/5 | 5/5 | 4/5 | 4/5 |
| 12 | kind rules; citation clean-up | 5 | 4/5 | 3/5 | 2/5 | 1/5 |
| 13 | competing revises resolved; measured values make a figure | 8 | 7/8 | 6/8 | 7/8 | 3/8 |
| 13, Gemma 4 E4B | same code, larger model for the series steps | 5 | 4/5 | 4/5 | 3/5 | 2/5 |
| 14 | history follows merged revisions; wrong-kind targets redirected; duplicates | 8 | 7/8 | 7/8 | 7/8 | 6/8 |
| 15 | figures matched by value; predictions revised only by their owner | 8 | 5/8 | 8/8 | 4/8 | 4/8 |
| 16 | prediction ~ position allowed; turn ids and section-coverage check | 8 | 7/8 | 7/8 | 5/8 | 5/8 |
| 17 | compound-figure split check | 8 | 8/8 | 6/8 | 6/8 | 4/8 |
| 18 | "from X to Y" turns an add into a revise; shared values back figure revises | 16 | 16/16 | 14/16 | 15/16 | 13/16 |
| 19 | measured-value recall check | 8 | 8/8 | 8/8 | 7/8 | 7/8 |
| **final** | compound figures split in code as a fallback | **16** | **15/16** | **14/16** | **15/16** | **13/16** |

Iterations 1–4 were single builds, before the robustness script existed. The first build over-revised: 11 `revise` ops, and Mark's core ep001 positions were superseded by points that were only related to them. The length trimmer also cut the key revisions, and the writer left out guests. Iteration 11 was stopped and is not reported, because the code changed during the batch. Each row is 5–16 independent uncached builds. With batches this small, differences of one or two runs are noise: compare pooled rows, not neighbours.

In the final configuration, no summary sentence named an episode its sources do not come from (0 in 16 runs). One update averages 5.1 LLM calls and 29 s. Code trimmed the writer's draft in 32 of 80 updates, and the 40-item ledger cap archived 2 low-priority items in 16 runs.

**Model choice.** With the iteration-13 code, Gemma 4 E4B for the series steps passed all checks in 2/5 builds at 60 s per update, against E2B's 3/8 at 24 s. The larger model bought nothing measurable, so the series uses the same E2B model as the episode pipeline. What raised the pass rate was moving decisions that can be made exactly into code.

**Known limits.**
- The prompts and rules were tuned on these five episodes. There is no held-out series, so the gold checks measure fit to this series, not generalization. The rules are domain-neutral (kinds, quoted earlier views, measured values, speakers, citations), and the prompt examples come from other topics (EV sales, waiting lists). But a second series would be the real test.
- The remaining failures are Gemma 4 E2B being inconsistent. Sometimes extraction phrases a revision as a fresh statement ("Mark Rivera predicts intentional hybrid", with no reference to the earlier view), and without any signal that it changes something, code cannot tell it from a second opinion. Sometimes a fact is not extracted at all.
- Rewriting the prose on every update keeps it coherent, but consecutive versions share only about 40% of their text (similarity 0.10–0.79, mean 0.39). The facts are stable in the ledger and the wording is not.

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
| claims | **Claims** `LlmAgent` | A regex pre-filter shortlists turns (numbers, entities, claim words); the model writes self-contained claims and types them: factual / statistic / prediction / anecdote / opinion. Opinions are dropped, and so are claims about the show itself ("Sarah is hosting the podcast"). If several candidate turns yield no claims at all, the critic asks again (a `"` inside the reasoning can end the JSON string early and force an empty list). |
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

**Held-out results** (5 episodes, 25 gold claims, never used for search or training; Gemma 4 E2B via Ollama unless noted). Full tables, every claim, and bootstrap confidence intervals: [`eval/RESULTS.md`](eval/RESULTS.md).

| Configuration | V̂ | Verdict acc. | Evidence recall | Quote validity | Key points | Structure | Claim recall |
|---|---|---|---|---|---|---|---|
| Base harness, numeric check off | 0.869 | 0.71 | 0.92 | 1.00 | 1.00 | 1.00 | 0.80 |
| **Base harness** (default) | **0.877** | 0.72 | 0.92 | 1.00 | 1.00 | 1.00 | 0.80 |
| Evo² round-1 harness (`harness/evolved.yaml`) | **0.887** | 0.74 | 0.94 | 1.00 | 1.00 | 1.00 | 0.82 |
| Base harness · task-model LoRA (`podcast-exec:r1`) | 0.869 | 0.71 | 0.94 | 1.00 | 0.97 | 1.00 | 0.79 |
| Base harness · its exact control (`podcast-base:q8r`) | 0.887 | 0.74 | 0.94 | 1.00 | 1.00 | 1.00 | 0.82 |
| **Evo² round-2 harness** (`harness/evolved_r2.yaml`) | **0.924** | 0.82 | 0.96 | 1.00 | 1.00 | 1.00 | 0.87 |
| Base harness · Gemma 4 E4B | 0.849 | 0.69 | 0.85 | 1.00 | 0.97 | 1.00 | 0.75 |
| Evo² round-2 harness · Gemma 4 E4B | 0.802 | 0.60 | 0.76 | 1.00 | 1.00 | 1.00 | 0.65 |
| 12B harness (`base_12b.yaml`) · Gemma 4 12B | 0.768 | 0.52 | 0.83 | 1.00 | 0.93 | 1.00 | 0.58 |
| Evo² round-3 harness (`evolved_r3_12b.yaml`) · Gemma 4 12B | 0.845 | 0.65 | 0.89 | 1.00 | 1.00 | 1.00 | 0.81 |

On the 10 search episodes the default harness scores 0.890 (0.883 with the numeric check off) and the round-2 harness 0.916. Averaged over three seeds, the round-2 harness gains +0.023 on held-out (0.873 → 0.896) and +0.012 on the search split (0.900 → 0.912); both confidence intervals include 0.

**Retrieval, BM25 vs TF-IDF** (no LLM; gold evidence among 228 KB + web documents; all 57 gold claims with evidence):

| Query | BM25 R@1 | TF-IDF R@1 | BM25 R@3 | TF-IDF R@3 |
|---|---|---|---|---|
| clean gold claim text | 0.807 | 0.807 | 1.000 | 1.000 |
| raw transcript turn (fillers, ASR errors) | **0.807** | 0.754 | 1.000 | 1.000 |

The two tie on clean queries, and BM25 holds up better on noisy ones; with this small KB both saturate at k = 3. On the 18 held-out claims alone TF-IDF is slightly ahead at R@1 (0.78 vs 0.72), so this is a modest edge, not a decisive one. BM25 is the default because its term-frequency saturation and length normalisation suit short, keyword-heavy fact entries; `retrieval.method: tfidf` switches it.

How to read this:

- **Structure is solved by construction.** Quotes are always verbatim with the right timestamps, summaries are 200–300 words, there are exactly 5 takeaways, and every key point is covered. This comes from schema-constrained decoding, the critic loop and copy-by-id.
- **Fact-checking is the hard part for a 2B model** (verdict accuracy 0.71–0.82). Most remaining errors are claims the extractor merges or misses, and *outdated* statistics the judge calls "supported" because the evidence is about the same subject.
- **The bigger wins were agent design, not tuning.** Two changes lifted held-out V̂ from 0.839 to 0.877 (measured just before and after adding them): escalating to web search when the KB can't settle a claim, and dropping claims about the show itself. The **numeric check** (code that turns "supported" into ⚠ when the claim's numbers match none in the evidence) adds a little on both splits and has never flipped a correct verdict. I designed these three after reading held-out errors, so the search-split numbers are the cleaner evidence for them.
- **Bigger models scored lower on held-out.** Gemma 4 E4B is below E2B on both splits with either harness; its claim extraction is all-or-nothing (one held-out episode lost every claim). Gemma 4 12B first returned one claim per episode (search split 0.549) because it copied the claims prompt's few-shot layout; with the examples rewritten (`harness/base_12b.yaml`) and an Evo² round it is the best configuration on the search split (0.943) but still below E2B on held-out (0.845). The harness was built around E2B, partly from held-out errors, so held-out favours E2B. The harness Evo² evolved *for* E4B helps E2B instead (details in [`docs/evo2.md`](docs/evo2.md#results)).
- **Every difference between configurations is within noise.** With 5 episodes / 25 claims, one claim is ±0.04 verdict accuracy. Re-running with other seeds moves held-out V̂ by about ±0.03 (the round-2 harness scored 0.924, 0.864 and 0.901 on seeds 7–9). `eval/RESULTS.md` has paired bootstrap intervals and the seed table. Read the deltas as directional.

---

## Evo²: evolving the harness and the weights

[Evo²](https://arxiv.org/abs/2610.13025) improves an agent by searching over its harness with an *evolver* model, and then training that evolver on its own search trajectories. No code was released with the paper, so [`evo/`](evo/) implements it from the paper on one 8 GB laptop GPU, with one extension: the task model is fine-tuned too. Full write-up: [`docs/evo2.md`](docs/evo2.md).

- **Genome and edits** (`evo/genome.py`): the harness YAML, and a small validated edit language (`set_param`, `replace_in_prompt`, `append_to_prompt`, `toggle_step`, `add_guardrail`). Malformed edits get reward −0.1.
- **Tree search** (`evo/search.py`): one evolver model (Gemma 4 E2B) in two ADK roles. The **Proposer** writes 4 edit sets per parent; the **Reflector** keeps them or writes a revised set. Parents are sampled with `w(h) = ε + |V̂(h) − ½|`, and the reward is `V̂(child) − V̂(parent)`. Fitness is the eval score on **search** episodes (4 in round 1; 5 in round 2, with the other 5 for validation); held-out episodes are never used. Every node's harness is saved under `evo/runs/<round>/nodes/`.
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
| Round 2 (trained evolver, E4B task model), fitness on 5 search episodes | 0.819 | **0.955** |
| ...on 5 search episodes the search did not use (E4B) | 0.840 | 0.957 |
| ...the round-2 harness on held-out with E2B (seed 7 / mean of 3 seeds) | 0.877 / 0.873 | **0.924** / 0.896 |
| Evolver edit sets that were well-formed during the search (proposer) | 7/48 (round 1) | 15/32 (round 2), 12/24 (round 3) |
| Round 3 (trained evolver, 12B task model): fitness / unused search episodes / held-out | 0.875 / 0.796 / 0.768 | **0.968** / 0.917 / 0.845 |

What worked, and what didn't:

- **The harness search found a real but small improvement.** It raised `claims.candidate_max` from 25 to 40, which lets more claim candidates reach the extractor. It held up on unseen episodes, but within noise.
- **Training the evolver is the clearest effect.** It more than doubled the share of well-formed edits, which is what makes the next search round cheaper.
- **Round 2 found the biggest harness gain, because the evolver could see the problem.** E4B's planner set claim budgets of 3–4, so extracted claims were never checked. Once the failure feedback said "3 extracted claims were not fact-checked because the planner's claim_budget was 3" instead of only "claim not extracted", the trained evolver's first proposal added a planner instruction to budget for every checkable statement. It held up on unused search episodes and transfers to E2B, though the held-out gain averaged over seeds (+0.023) is within noise.
- **A 2B evolver writes mostly malformed edits, and padded its valid ones with junk.** It appended a stray `notes` line to a prompt and added `judge` as a guardrail; both are visible in `harness/evolved.yaml`, which I left exactly as the search produced it. That led to two fixes in the evolver's *own* interface: a discriminated-union JSON schema (Ollama's grammar then only allows the fields each op uses) and a minimum length for appended text. That's the paper's point in miniature: the evolver's harness matters as much as its weights.
- **Fine-tuning the task model on its own filtered outputs (135 examples) did not help.** A first comparison suggested +0.08, but that control had been converted from the original checkpoint, which keeps 60 unused KV-shared tensors, so it was not the same model. Against an exact control (the base re-saved through the identical path), the LoRA is slightly worse, within noise. Self-distillation on this little data mostly re-teaches what the model already does.
- At this scale, off-policy GRPO on 24 examples and SFT on 11 demonstrate the loop end to end; they are not a converged result.

---

## Repository layout

```
src/podcast_agent/   cli.py pipeline.py agent_factory.py tools.py factcheck.py critic.py
                     ingest.py retrieval.py llm.py trace.py render.py models.py harness.py evaluate.py
                     series.py (series summary: manifest, state, ledger, extract/merge/write agents, length limit)
series/              series manifests (work_reimagined.yaml)
harness/             base.yaml (the genome)  evolved.yaml, evolved_r2.yaml, evolved_r3_12b.yaml (Evo² rounds 1-3)
                     base_12b.yaml (base with few-shot examples Gemma 4 12B can follow)
data/heldout/        ep001–ep007 (ep001, ep004–ep007 = Work Reimagined series; gold only for ep001–ep003)  data/search/  s01–s10 (Evo² fitness)
kb/                  facts.jsonl  search_corpus.jsonl
eval/                gold/*.yaml  RESULTS.md
evo/                 genome.py search.py lora.py train_evolver.py train_executor.py export.py  runs/
out/                 sample outputs for every episode; out/series/<id>/: series summary state and logs
infra/terraform/     AWS modules + envs/dev       docs/deployment.md
tests/               offline tests (FakeLlm)
```

## Changing things quickly

| Want to… | Do this |
|---|---|
| Change a prompt, budget, k, samples | edit `harness/base.yaml` (or copy it and pass `-H my.yaml`) |
| Use the best harness | `-H harness/evolved_r2.yaml` (Evo² round 2: higher claim budgets) |
| Use a bigger model | `--model ollama_chat/gemma4:e4b`, or `-H harness/evolved_r3_12b.yaml` for 12B; both scored lower than E2B on held-out |
| Re-run only fact-checking | `podcast-agent run data/heldout -e ep004 --only factcheck` |
| Add a knowledge-base fact | append a line to `kb/facts.jsonl`; check with `podcast-agent kb-search "..."` |
| Add an output field | add it to `models.Report`, fill it in `pipeline.assemble`, print it in `render.py` |
| Add a pipeline step | add `step_<name>` in `pipeline.py`, add the name to `STEP_ORDER` and `harness.StepName` |
| Run without cache | `--no-cache` |
| Change the series character limit | `series: max_chars` (and `target_chars`) in `harness/base.yaml`, then `podcast-agent series rebuild` |
| Add a series | write `series/<id>.yaml` (series_id, title, episodes) and run `podcast-agent series sync -m series/<id>.yaml` |
| Change what the series summary covers | `series.prompts.writer` in the harness; the sections are `PROSE` in `series.py` |
