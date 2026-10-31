# Evaluation results

Harness `base` · model `ollama_chat/gemma4:12b` · 2026-10-30 15:15

**Mean V̂ = 0.549** over 10 episodes.

| episode | V̂ | verdict_accuracy | evidence_recall | quote_validity | keypoint_coverage | structure | claim_recall | tag_overlap |
|---|---|---|---|---|---|---|---|---|
| s01 | 0.580 | 0.20 | 0.33 | 1.00 | 1.00 | 1.00 | 0.20 | 0.75 |
| s02 | 0.554 | 0.17 | 0.25 | 1.00 | 1.00 | 1.00 | 0.17 | 0.75 |
| s03 | 0.534 | 0.20 | 0.25 | 1.00 | 0.83 | 1.00 | 0.20 | 0.75 |
| s04 | 0.568 | 0.20 | 0.25 | 1.00 | 1.00 | 1.00 | 0.20 | 1.00 |
| s05 | 0.568 | 0.20 | 0.25 | 1.00 | 1.00 | 1.00 | 0.20 | 0.50 |
| s06 | 0.514 | 0.17 | 0.25 | 1.00 | 0.80 | 1.00 | 0.17 | 0.50 |
| s07 | 0.554 | 0.17 | 0.25 | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 |
| s08 | 0.534 | 0.20 | 0.25 | 1.00 | 0.83 | 1.00 | 0.20 | 1.00 |
| s09 | 0.521 | 0.17 | 0.25 | 1.00 | 0.83 | 1.00 | 0.17 | 0.75 |
| s10 | 0.568 | 0.20 | 0.25 | 1.00 | 1.00 | 1.00 | 0.20 | 0.75 |
| **mean** | **0.549** | 0.19 | 0.26 | 1.00 | 0.93 | 1.00 | 0.19 | 0.78 |

## Retrieval: query → gold evidence in KB + mock web (no LLM)

| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |
|---|---|---|---|---|
| BM25 | 0.846 | 1.000 | 0.821 | 1.000 |
| TF-IDF | 0.821 | 1.000 | 0.718 | 1.000 |

39 gold claims with evidence.

## Claim matching details

**s01** (extra predicted claims: 0)
- ✔ gold `verified`: Netflix launched its Basic with Ads plan in November 2022 → pred `verified`: Netflix launched its Basic with Ads plan in November 2022.
- ✘ gold `verified`: Prime Video turned on ads by default in January 2024, with a $2.99 monthly fee to remove them → pred `None`: None
- ✘ gold `inaccurate`: Netflix's ad tier has about 5 million monthly active users globally → pred `None`: None
- ✘ gold `unverifiable`: A CPG client's CTV-only flight produced a 30% lift in brand recall versus linear → pred `None`: None
- ✘ gold `unverifiable`: Within five years linear TV will be under 10% of US video ad spend → pred `None`: None

**s02** (extra predicted claims: 0)
- ✔ gold `inaccurate`: Electric cars are only about 5% of global car sales → pred `inaccurate`: Electric cars account for about 5% of global car sales.
- ✘ gold `verified`: Global electric car sales topped 17 million in 2024, more than one in five new cars → pred `None`: None
- ✘ gold `verified`: Almost 89% of new cars sold in Norway in 2024 were fully electric → pred `None`: None
- ✘ gold `inaccurate`: An EV battery typically needs replacing after about three years → pred `None`: None
- ✘ gold `unverifiable`: Karen's EV is past 60,000 miles with almost no range loss → pred `None`: None
- ✘ gold `unverifiable`: Half of all new cars sold in the US will be electric by 2030 → pred `None`: None

**s03** (extra predicted claims: 0)
- ✔ gold `inaccurate`: Muscle soreness a day or two after exercise is caused by lactic acid build-up → pred `inaccurate`: Muscle soreness occurring two days after a leg workout is caused by lactic acid building up in the muscles.
- ✘ gold `verified`: In a Stanford study, basketball players who slept about ten hours a night improved sprint times and free-throw shooting → pred `None`: None
- ✘ gold `verified`: Kipchoge ran a sub-two-hour marathon (1:59:40) in Vienna in 2019, which was not an official record → pred `None`: None
- ✘ gold `inaccurate`: The official men's marathon world record is still Kipchoge's 2:01:09 from Berlin → pred `None`: None
- ✘ gold `unverifiable`: Marcus's college team did ice baths after every practice and nobody pulled a hamstring → pred `None`: None

**s04** (extra predicted claims: 1)
- ✔ gold `verified`: Colonial Pipeline shut down the largest US fuel pipeline in May 2021 and paid about $4.4 million in bitcoin → pred `verified`: Colonial Pipeline paid roughly $4.4 million in bitcoin to attackers in May 2021.
- ✘ gold `verified`: The 2023 MGM Resorts attackers got in by calling the IT help desk while impersonating an employee → pred `None`: None
- ✘ gold `inaccurate`: Ransomware payments set another all-time record in 2024, higher than 2023 → pred `None`: None
- ✘ gold `inaccurate`: The latest IBM average cost of a data breach is $4.88 million, the highest ever → pred `None`: None
- ✘ gold `unverifiable`: A logistics client was back online in 48 hours thanks to offline backups and paid nothing → pred `None`: None

**s05** (extra predicted claims: 0)
- ✔ gold `inaccurate`: Science says you need eight glasses of plain water a day → pred `inaccurate`: The official science states that everyone needs eight glasses of plain water a day.
- ✘ gold `verified`: In 2015 the WHO's cancer agency classified processed meat as a Group 1 carcinogen → pred `None`: None
- ✘ gold `inaccurate`: Carrots give you night vision, which is why British pilots could shoot down bombers at night in WWII → pred `None`: None
- ✘ gold `verified`: US nutrition labels must list added sugars, with large manufacturers required to comply by January 2020 → pred `None`: None
- ✘ gold `unverifiable`: Sofia lost twelve pounds after cutting out bread for a month → pred `None`: None

**s06** (extra predicted claims: 0)
- ✔ gold `verified`: E-commerce is about 16% of total US retail sales according to the Census Bureau → pred `verified`: E-commerce is about 16% of total US retail sales according to the Census Bureau.
- ✘ gold `inaccurate`: Cyber Monday is roughly a $10 billion day for US online sales → pred `None`: None
- ✘ gold `verified`: About seven in ten online shopping carts are abandoned before checkout → pred `None`: None
- ✘ gold `unverifiable`: Adding Apple Pay cut one client's cart abandonment by a third → pred `None`: None
- ✘ gold `inaccurate`: Temu is owned by Alibaba → pred `None`: None
- ✘ gold `unverifiable`: By 2030 most physical stores will be showrooms → pred `None`: None

**s07** (extra predicted claims: 1)
- ✔ gold `verified`: 2024 was the hottest year on record and the first calendar year more than 1.5C above pre-industrial levels → pred `verified`: 2024 was the first calendar year where the global average temperature went more than 1.5 degrees Celsius above pre-industrial levels.
- ✘ gold `inaccurate`: Renewables are under 20% of the world's electricity → pred `None`: None
- ✘ gold `verified`: China installed more than half of the world's new solar capacity in 2023 → pred `None`: None
- ✘ gold `inaccurate`: Atmospheric CO2 is around 350 parts per million → pred `None`: None
- ✘ gold `unverifiable`: Rooftop solar cut the institute's electric bill by about 40% → pred `None`: None
- ✘ gold `unverifiable`: Commercial fusion plants will supply power to the grid by 2035 → pred `None`: None

**s08** (extra predicted claims: 1)
- ✔ gold `verified`: TikTok passed one billion monthly users in September 2021 → pred `verified`: TikTok announced they passed a billion monthly users in September 2021.
- ✘ gold `inaccurate`: Instagram has about one billion monthly users → pred `None`: None
- ✘ gold `verified`: Threads got 100 million sign-ups in about five days → pred `None`: None
- ✘ gold `inaccurate`: Facebook started out as a Stanford-only network → pred `None`: None
- ✘ gold `unverifiable`: Chris's niece spends six hours a day on TikTok → pred `None`: None

**s09** (extra predicted claims: 0)
- ✔ gold `verified`: The global space economy is around $570 billion a year, mostly commercial → pred `verified`: The global space economy is around $570 billion a year.
- ✘ gold `verified`: SpaceX landed its first orbital-class Falcon 9 booster in December 2015 → pred `None`: None
- ✘ gold `inaccurate`: Only the US, Russia and China have ever landed on the Moon → pred `None`: None
- ✘ gold `verified`: The ISS has been continuously occupied since November 2000 → pred `None`: None
- ✘ gold `unverifiable`: Three investors laughed Raj's satellite startup out of the room in 2019 → pred `None`: None
- ✘ gold `unverifiable`: Starship will land astronauts on the Moon by 2026 → pred `None`: None

**s10** (extra predicted claims: 0)
- ✔ gold `verified`: TSMC makes roughly 90% of the world's most advanced chips → pred `verified`: TSMC in Taiwan makes roughly 90 percent of the world's most advanced chips.
- ✘ gold `inaccurate`: Moore's law says transistor counts on a chip double every year → pred `None`: None
- ✘ gold `verified`: About 1.24 billion smartphones shipped worldwide in 2024, the first growth after two down years → pred `None`: None
- ✘ gold `inaccurate`: Nvidia's market cap is about three trillion dollars → pred `None`: None
- ✘ gold `unverifiable`: Nate's new phone battery lasts twice as long as his old one → pred `None`: None

