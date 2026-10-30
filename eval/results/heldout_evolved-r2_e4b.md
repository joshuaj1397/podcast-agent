# Evaluation results

Harness `evolved-r2` · model `ollama_chat/gemma4:e4b` · 2026-10-30 14:22

**Mean V̂ = 0.802** over 5 episodes.

| episode | V̂ | verdict_accuracy | evidence_recall | quote_validity | keypoint_coverage | structure | claim_recall | tag_overlap |
|---|---|---|---|---|---|---|---|---|
| ep001 | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.80 |
| ep002 | 0.450 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.40 |
| ep003 | 0.800 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 | 0.40 |
| ep004 | 0.799 | 0.57 | 0.80 | 1.00 | 1.00 | 1.00 | 0.86 | 0.50 |
| ep005 | 0.964 | 0.91 | 1.00 | 1.00 | 1.00 | 1.00 | 0.91 | 0.80 |
| **mean** | **0.802** | 0.60 | 0.76 | 1.00 | 1.00 | 1.00 | 0.65 | 0.58 |

## Retrieval: query → gold evidence in KB + mock web (no LLM)

| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |
|---|---|---|---|---|
| BM25 | 0.722 | 1.000 | 0.778 | 1.000 |
| TF-IDF | 0.778 | 1.000 | 0.833 | 1.000 |

18 gold claims with evidence.

## Claim matching details

**ep001** (extra predicted claims: 1)
- ✔ gold `verified`: GitLab, Automattic and Doist have written handbooks documenting how they work → pred `verified`: Mark Rivera mentioned that GitLab, Automattic, and Doist have written handbooks for everything.
- ✔ gold `unverifiable`: Remote-first companies will dominate talent acquisition → pred `unverifiable`: Mark Rivera stated that remote-first companies will dominate talent acquisition.

**ep002** (extra predicted claims: 0)
- ✘ gold `verified`: Some hospitals use AI to read X-rays or flag urgent cases → pred `None`: None
- ✘ gold `verified`: The FDA reviews AI medical systems before they are used → pred `None`: None
- ✘ gold `verified`: The pandemic accelerated digital health adoption → pred `None`: None

**ep003** (extra predicted claims: 0)
- ✔ gold `unverifiable`: Samir's SaaS reached break-even in 18 months with no outside capital → pred `unverifiable`: Samir Gupta built a SaaS with no outside capital and reached break-even in 18 months.
- ✘ gold `unverifiable`: A fintech startup Emily advised raised seed funding and secured market leadership within two years → pred `None`: None

**ep004** (extra predicted claims: 0)
- ✘ gold `verified`: The Perseverance rover landed in Jezero Crater in February 2021 → pred `inaccurate`: The Perseverance rover landed in Jezebel Crater in February 2021.
- ✔ gold `inaccurate`: The Ingenuity helicopter is still flying on Mars, with about fifty flights so far → pred `inaccurate`: Ingenuity has done about fifty flights and NASA keeps extending the mission.
- ✔ gold `unverifiable`: A JPL contact says the Mars team has been working weekends since last summer → pred `unverifiable`: Dr. Lena Park's old lab mate said the Mars team has been working weekends since last summer.
- ✘ gold `verified`: NASA's new Mars mission launches in early 2026 → pred `inaccurate`: A mission is planned to launch in early 2026 to bring back samples from Mars.
- ✘ gold `inaccurate`: The new Mars mission will bring back samples → pred `None`: None
- ✔ gold `verified`: Bitcoin rose above $100,000 for the first time in early December 2024 → pred `verified`: Bitcoin went over one hundred thousand dollars for the first time in early December.
- ✔ gold `unverifiable`: Bitcoin will reach $200K by 2025 → pred `unverifiable`: Jamie Ortiz predicts that Bitcoin will reach $200K by 2025.

**ep005** (extra predicted claims: 2)
- ✔ gold `verified`: Netflix ended 2024 with just over 300 million paid memberships → pred `verified`: Netflix finished 2024 with just over 300 million paid memberships worldwide.
- ✔ gold `inaccurate`: Spotify has about 400 million monthly active users → pred `inaccurate`: Spotify has about 400 million monthly active users.
- ✔ gold `inaccurate`: Facebook has around 2 billion monthly users → pred `inaccurate`: Facebook still has around 2 billion monthly users.
- ✔ gold `verified`: The idea that humans use only 10% of their brains is false → pred `verified`: Brain imaging shows activity across virtually the whole brain over the course of a normal day.
- ✔ gold `inaccurate`: The average human attention span is eight seconds, shorter than a goldfish's, per Microsoft research → pred `inaccurate`: Microsoft research found that the average human attention span is now eight seconds.
- ✔ gold `verified`: The first iPhone went on sale in June 2007 → pred `verified`: The first iPhone went on sale in June 2007.
- ✔ gold `verified`: Google bought YouTube in 2006 for $1.65 billion → pred `verified`: Google bought YouTube in 2006 for $1.65 billion.
- ✔ gold `inaccurate`: Tim Berners-Lee invented the web at CERN in 1999 → pred `inaccurate`: Tim Berners-Lee invented the web at CERN in 1999.
- ✔ gold `verified`: Twitter was rebranded as X in July 2023 → pred `verified`: Elon Musk rebranded Twitter as X in July 2023.
- ✔ gold `unverifiable`: Omar's regional paper saw print ad revenue fall by half in a single year in 2009 → pred `unverifiable`: At the regional paper where Omar Haddad worked in 2009, the print ad revenue fell by half in a single year.
- ✘ gold `unverifiable`: By 2030 more than half of everything posted online will be AI-generated → pred `None`: None

