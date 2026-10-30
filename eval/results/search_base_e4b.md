# Evaluation results

Harness `base` · model `ollama_chat/gemma4:e4b` · 2026-10-30 12:46

**Mean V̂ = 0.830** over 10 episodes.

| episode | V̂ | verdict_accuracy | evidence_recall | quote_validity | keypoint_coverage | structure | claim_recall | tag_overlap |
|---|---|---|---|---|---|---|---|---|
| s01 | 0.710 | 0.40 | 0.67 | 1.00 | 1.00 | 1.00 | 0.40 | 0.50 |
| s02 | 0.762 | 0.50 | 0.75 | 1.00 | 1.00 | 1.00 | 0.50 | 0.50 |
| s03 | 0.769 | 0.60 | 0.75 | 1.00 | 0.83 | 1.00 | 0.60 | 0.75 |
| s04 | 0.802 | 0.60 | 0.75 | 1.00 | 1.00 | 1.00 | 0.60 | 0.75 |
| s05 | 0.920 | 0.80 | 1.00 | 1.00 | 1.00 | 1.00 | 0.80 | 0.50 |
| s06 | 0.829 | 0.67 | 0.75 | 1.00 | 1.00 | 1.00 | 0.83 | 0.50 |
| s07 | 0.762 | 0.50 | 0.75 | 1.00 | 1.00 | 1.00 | 0.50 | 1.00 |
| s08 | 0.887 | 0.80 | 1.00 | 1.00 | 0.83 | 1.00 | 0.80 | 0.50 |
| s09 | 0.933 | 0.83 | 1.00 | 1.00 | 1.00 | 1.00 | 0.83 | 0.50 |
| s10 | 0.920 | 0.80 | 1.00 | 1.00 | 1.00 | 1.00 | 0.80 | 1.00 |
| **mean** | **0.830** | 0.65 | 0.84 | 1.00 | 0.97 | 1.00 | 0.67 | 0.65 |

## Retrieval: query → gold evidence in KB + mock web (no LLM)

| method | claim text R@1 | claim text R@3 | raw turn R@1 | raw turn R@3 |
|---|---|---|---|---|
| BM25 | 0.846 | 1.000 | 0.821 | 1.000 |
| TF-IDF | 0.821 | 1.000 | 0.718 | 1.000 |

39 gold claims with evidence.

## Claim matching details

**s01** (extra predicted claims: 1)
- ✔ gold `verified`: Netflix launched its Basic with Ads plan in November 2022 → pred `verified`: Netflix launched its Basic with Ads plan in November 2022.
- ✔ gold `verified`: Prime Video turned on ads by default in January 2024, with a $2.99 monthly fee to remove them → pred `verified`: Prime Video turned ads on by default in January 2024.
- ✘ gold `inaccurate`: Netflix's ad tier has about 5 million monthly active users globally → pred `None`: None
- ✘ gold `unverifiable`: A CPG client's CTV-only flight produced a 30% lift in brand recall versus linear → pred `None`: None
- ✘ gold `unverifiable`: Within five years linear TV will be under 10% of US video ad spend → pred `None`: None

**s02** (extra predicted claims: 0)
- ✔ gold `inaccurate`: Electric cars are only about 5% of global car sales → pred `inaccurate`: Electric cars accounted for only about 5% of global car sales.
- ✔ gold `verified`: Global electric car sales topped 17 million in 2024, more than one in five new cars → pred `verified`: Global electric car sales topped 17 million in 2024.
- ✔ gold `verified`: Almost 89% of new cars sold in Norway in 2024 were fully electric → pred `verified`: Almost 89% of new cars sold in Norway were fully electric in 2024.
- ✘ gold `inaccurate`: An EV battery typically needs replacing after about three years → pred `None`: None
- ✘ gold `unverifiable`: Karen's EV is past 60,000 miles with almost no range loss → pred `None`: None
- ✘ gold `unverifiable`: Half of all new cars sold in the US will be electric by 2030 → pred `None`: None

**s03** (extra predicted claims: 0)
- ✘ gold `inaccurate`: Muscle soreness a day or two after exercise is caused by lactic acid build-up → pred `None`: None
- ✔ gold `verified`: In a Stanford study, basketball players who slept about ten hours a night improved sprint times and free-throw shooting → pred `verified`: A Stanford study found that basketball players who slept about ten hours a night for several weeks saw improvements in their sprint times and free throw shooting.
- ✔ gold `verified`: Kipchoge ran a sub-two-hour marathon (1:59:40) in Vienna in 2019, which was not an official record → pred `verified`: Kip choge ran a marathon in under two hours in Vienna in 2019 with a time of one fifty nine forty.
- ✔ gold `inaccurate`: The official men's marathon world record is still Kipchoge's 2:01:09 from Berlin → pred `inaccurate`: The official world record for a marathon is 2:01:09 set in Berlin.
- ✘ gold `unverifiable`: Marcus's college team did ice baths after every practice and nobody pulled a hamstring → pred `None`: None

**s04** (extra predicted claims: 0)
- ✔ gold `verified`: Colonial Pipeline shut down the largest US fuel pipeline in May 2021 and paid about $4.4 million in bitcoin → pred `verified`: Colonial Pipeline shut down the biggest fuel pipeline in the US in May 2021 and paid roughly $4.4 million in bitcoin to the attackers.
- ✔ gold `verified`: The 2023 MGM Resorts attackers got in by calling the IT help desk while impersonating an employee → pred `verified`: MGM Resorts was attacked in 2023 when attackers called the IT help desk and pretended to be an employee.
- ✔ gold `inaccurate`: Ransomware payments set another all-time record in 2024, higher than 2023 → pred `inaccurate`: Ransomware payments set an all-time record in 2024, which was higher than 2023.
- ✘ gold `inaccurate`: The latest IBM average cost of a data breach is $4.88 million, the highest ever → pred `None`: None
- ✘ gold `unverifiable`: A logistics client was back online in 48 hours thanks to offline backups and paid nothing → pred `None`: None

**s05** (extra predicted claims: 0)
- ✔ gold `inaccurate`: Science says you need eight glasses of plain water a day → pred `inaccurate`: Everybody knows that one needs eight glasses of plain water a day.
- ✔ gold `verified`: In 2015 the WHO's cancer agency classified processed meat as a Group 1 carcinogen → pred `verified`: In 2015, the World Health Organization's cancer agency classified processed meat as a Group 1 carcinogen.
- ✔ gold `inaccurate`: Carrots give you night vision, which is why British pilots could shoot down bombers at night in WWII → pred `inaccurate`: British pilots could shoot down bombers at night in World War Two due to carrots.
- ✔ gold `verified`: US nutrition labels must list added sugars, with large manufacturers required to comply by January 2020 → pred `verified`: Nutrition labels in the US now have to list added sugars separately.
- ✘ gold `unverifiable`: Sofia lost twelve pounds after cutting out bread for a month → pred `None`: None

**s06** (extra predicted claims: 0)
- ✘ gold `verified`: E-commerce is about 16% of total US retail sales according to the Census Bureau → pred `inaccurate`: E-commerce accounts for only 16% of total US retail sales, according to the Census Bureau.
- ✔ gold `inaccurate`: Cyber Monday is roughly a $10 billion day for US online sales → pred `inaccurate`: Cyber Monday is roughly a $10 billion day for US online sales.
- ✘ gold `verified`: About seven in ten online shopping carts are abandoned before checkout → pred `None`: None
- ✔ gold `unverifiable`: Adding Apple Pay cut one client's cart abandonment by a third → pred `unverifiable`: Victor Mensah cut a client's cart abandonment by a third by adding Apple Pay at checkout.
- ✔ gold `inaccurate`: Temu is owned by Alibaba → pred `inaccurate`: Temu is owned by Alibaba.
- ✔ gold `unverifiable`: By 2030 most physical stores will be showrooms → pred `unverifiable`: By 2030, most physical stores will be showrooms.

**s07** (extra predicted claims: 1)
- ✔ gold `verified`: 2024 was the hottest year on record and the first calendar year more than 1.5C above pre-industrial levels → pred `verified`: 2024 was the hottest year on record.
- ✔ gold `inaccurate`: Renewables are under 20% of the world's electricity → pred `inaccurate`: Renewables are under 20% of the world's electricity.
- ✔ gold `verified`: China installed more than half of the world's new solar capacity in 2023 → pred `verified`: China installed more than half of all the new solar capacity in the world in 2023.
- ✘ gold `inaccurate`: Atmospheric CO2 is around 350 parts per million → pred `None`: None
- ✘ gold `unverifiable`: Rooftop solar cut the institute's electric bill by about 40% → pred `None`: None
- ✘ gold `unverifiable`: Commercial fusion plants will supply power to the grid by 2035 → pred `None`: None

**s08** (extra predicted claims: 0)
- ✔ gold `verified`: TikTok passed one billion monthly users in September 2021 → pred `verified`: TikTok passed a billion monthly users in 2021, and TikTok announced this in September 2021.
- ✔ gold `inaccurate`: Instagram has about one billion monthly users → pred `inaccurate`: Instagram has about a billion monthly users.
- ✔ gold `verified`: Threads got 100 million sign-ups in about five days → pred `verified`: Threads got a hundred million sign-ups in about five days, which was the fastest app ever to do so at the time.
- ✔ gold `inaccurate`: Facebook started out as a Stanford-only network → pred `inaccurate`: Facebook started out as a Stanford-only thing.
- ✘ gold `unverifiable`: Chris's niece spends six hours a day on TikTok → pred `None`: None

**s09** (extra predicted claims: 0)
- ✔ gold `verified`: The global space economy is around $570 billion a year, mostly commercial → pred `verified`: The global space economy is around $570 billion a year, and most of this money comes from commercial sources rather than government sources.
- ✔ gold `verified`: SpaceX landed its first orbital-class Falcon 9 booster in December 2015 → pred `verified`: SpaceX landed its first orbital-class Falcon 9 booster in December 2015.
- ✔ gold `inaccurate`: Only the US, Russia and China have ever landed on the Moon → pred `inaccurate`: Only the US, Russia, and China have ever landed on the Moon.
- ✔ gold `verified`: The ISS has been continuously occupied since November 2000 → pred `verified`: The International Space Station has had humans on board continuously since November 2000.
- ✔ gold `unverifiable`: Three investors laughed Raj's satellite startup out of the room in 2019 → pred `unverifiable`: Raj Mehta's first satellite startup pitched in 2019 was laughed out of the room by three investors, and those same firms later contacted them.
- ✘ gold `unverifiable`: Starship will land astronauts on the Moon by 2026 → pred `None`: None

**s10** (extra predicted claims: 0)
- ✔ gold `verified`: TSMC makes roughly 90% of the world's most advanced chips → pred `verified`: TSMC in Taiwan makes roughly 90 percent of the world's most advanced chips.
- ✔ gold `inaccurate`: Moore's law says transistor counts on a chip double every year → pred `inaccurate`: Moore's law says the number of transistors on a chip doubles every single year.
- ✔ gold `verified`: About 1.24 billion smartphones shipped worldwide in 2024, the first growth after two down years → pred `verified`: About 1.24 billion smartphones were shipped worldwide in 2024.
- ✔ gold `inaccurate`: Nvidia's market cap is about three trillion dollars → pred `inaccurate`: Nvidia is sitting at about a three trillion dollar market cap right now.
- ✘ gold `unverifiable`: Nate's new phone battery lasts twice as long as his old one → pred `None`: None

