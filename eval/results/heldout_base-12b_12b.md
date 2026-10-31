# Evaluation results

Harness `base-12b` · model `ollama_chat/gemma4:12b` · 2026-10-30 18:17

**Mean V̂ = 0.768** over 5 episodes.

| episode | V̂ | verdict_accuracy | evidence_recall | quote_validity | keypoint_coverage | structure | claim_recall | tag_overlap |
|---|---|---|---|---|---|---|---|---|
| ep001 | 0.800 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 | 1.00 |
| ep002 | 0.967 | 1.00 | 1.00 | 1.00 | 0.83 | 1.00 | 1.00 | 0.80 |
| ep003 | 0.800 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 | 0.60 |
| ep004 | 0.557 | 0.14 | 0.60 | 1.00 | 0.80 | 1.00 | 0.43 | 0.75 |
| ep005 | 0.715 | 0.45 | 0.56 | 1.00 | 1.00 | 1.00 | 0.45 | 0.80 |
| **mean** | **0.768** | 0.52 | 0.83 | 1.00 | 0.93 | 1.00 | 0.58 | 0.79 |

## Retrieval: query → gold evidence in KB + mock web (no LLM)

| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |
|---|---|---|---|---|
| BM25 | 0.722 | 1.000 | 0.778 | 1.000 |
| TF-IDF | 0.778 | 1.000 | 0.833 | 1.000 |

18 gold claims with evidence.

## Claim matching details

**ep001** (extra predicted claims: 2)
- ✔ gold `verified`: GitLab, Automattic and Doist have written handbooks documenting how they work → pred `verified`: GitLab has written handbooks for its operations.
- ✘ gold `unverifiable`: Remote-first companies will dominate talent acquisition → pred `None`: None

**ep002** (extra predicted claims: 0)
- ✔ gold `verified`: Some hospitals use AI to read X-rays or flag urgent cases → pred `verified`: Some hospitals use AI for reading X-rays or flagging urgent cases.
- ✔ gold `verified`: The FDA reviews AI medical systems before they are used → pred `verified`: The FDA reviews AI systems carefully.
- ✔ gold `verified`: The pandemic accelerated digital health adoption → pred `verified`: The pandemic accelerated the adoption of digital health including AI tools.

**ep003** (extra predicted claims: 0)
- ✔ gold `unverifiable`: Samir's SaaS reached break-even in 18 months with no outside capital → pred `unverifiable`: Samir Gupta built a SaaS with no outside capital and reached break-even in 18 months.
- ✘ gold `unverifiable`: A fintech startup Emily advised raised seed funding and secured market leadership within two years → pred `None`: None

**ep004** (extra predicted claims: 2)
- ✘ gold `verified`: The Perseverance rover landed in Jezero Crater in February 2021 → pred `inaccurate`: The Perseverance rover landed in Jezebel Crater in February 2021.
- ✔ gold `inaccurate`: The Ingenuity helicopter is still flying on Mars, with about fifty flights so far → pred `inaccurate`: The Ingenuity helicopter has completed about fifty flights.
- ✘ gold `unverifiable`: A JPL contact says the Mars team has been working weekends since last summer → pred `None`: None
- ✘ gold `verified`: NASA's new Mars mission launches in early 2026 → pred `inaccurate`: The new Mars mission plans to bring back samples.
- ✘ gold `inaccurate`: The new Mars mission will bring back samples → pred `None`: None
- ✘ gold `verified`: Bitcoin rose above $100,000 for the first time in early December 2024 → pred `None`: None
- ✘ gold `unverifiable`: Bitcoin will reach $200K by 2025 → pred `None`: None

**ep005** (extra predicted claims: 3)
- ✔ gold `verified`: Netflix ended 2024 with just over 300 million paid memberships → pred `verified`: Netflix finished 2024 with just over 300 million paid memberships worldwide.
- ✔ gold `inaccurate`: Spotify has about 400 million monthly active users → pred `inaccurate`: Spotify has about 400 million monthly active users.
- ✔ gold `inaccurate`: Facebook has around 2 billion monthly users → pred `inaccurate`: Facebook has around 2 billion monthly users.
- ✔ gold `verified`: The idea that humans use only 10% of their brains is false → pred `verified`: Brain imaging shows activity across virtually the whole brain over the course of a normal day.
- ✔ gold `inaccurate`: The average human attention span is eight seconds, shorter than a goldfish's, per Microsoft research → pred `inaccurate`: Microsoft research found that the average human attention span is eight seconds.
- ✘ gold `verified`: The first iPhone went on sale in June 2007 → pred `None`: None
- ✘ gold `verified`: Google bought YouTube in 2006 for $1.65 billion → pred `None`: None
- ✘ gold `inaccurate`: Tim Berners-Lee invented the web at CERN in 1999 → pred `None`: None
- ✘ gold `verified`: Twitter was rebranded as X in July 2023 → pred `None`: None
- ✘ gold `unverifiable`: Omar's regional paper saw print ad revenue fall by half in a single year in 2009 → pred `None`: None
- ✘ gold `unverifiable`: By 2030 more than half of everything posted online will be AI-generated → pred `None`: None

