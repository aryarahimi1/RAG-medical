# RAG eval — 2026-05-10 17:22

Mode: **retrieval-only**
Pass rate: **16/18** (89%)

## Per-category

| Category | Passed | Total |
|----------|--------|-------|
| indication | 3 | 3 |
| interaction | 2 | 4 |
| multi-drug | 1 | 1 |
| pharmacokinetics | 3 | 3 |
| recall | 1 | 1 |
| refusal | 3 | 3 |
| warning | 3 | 3 |

## Latency (ms, median / p95)

| Stage    | median | p95  |
|----------|--------|------|
| redact | 5 | 12 |
| detect | 159 | 326 |
| retrieve | 62 | 81 |
| rerank | 121 | 163 |
| generate | n/a | n/a |

## Failures

| id | category | check | detail |
|----|----------|-------|--------|
| ddi-ibuprofen-lisinopril | interaction | retrieval_cover | no reranked chunks for: ['ibuprofen']; got: ['lisinopril'] |
| ddi-warfarin-aspirin | interaction | retrieval_cover | no reranked chunks for: ['aspirin']; got: ['warfarin'] |

## Per-question (full)

| id | category | drug_detect | retrieval_cover | citations | idk | phrases | passed |
|----|----------|-------------|------------------|-----------|-----|---------|--------|
| pk-ibuprofen-half-life | pharmacokinetics | pass | pass | — | — | — | yes |
| pk-warfarin-half-life | pharmacokinetics | pass | pass | — | — | — | yes |
| pk-levothyroxine-duration | pharmacokinetics | pass | pass | — | — | — | yes |
| ddi-ibuprofen-lisinopril | interaction | pass | FAIL | — | — | — | NO |
| ddi-warfarin-aspirin | interaction | pass | FAIL | — | — | — | NO |
| ddi-warfarin-amoxicillin | interaction | pass | pass | — | — | — | yes |
| ddi-metformin-ciprofloxacin | interaction | pass | pass | — | — | — | yes |
| indication-metformin | indication | pass | pass | — | — | — | yes |
| indication-atorvastatin | indication | pass | pass | — | — | — | yes |
| indication-sertraline | indication | pass | pass | — | — | — | yes |
| warning-prednisone | warning | pass | pass | — | — | — | yes |
| warning-gabapentin | warning | pass | pass | — | — | — | yes |
| warning-omeprazole | warning | pass | pass | — | — | — | yes |
| refusal-price | refusal | pass | — | — | — | — | yes |
| refusal-veterinary | refusal | pass | — | — | — | — | yes |
| refusal-manufacturer | refusal | pass | — | — | — | — | yes |
| multi-ibuprofen-naproxen | multi-drug | pass | pass | — | — | — | yes |
| recall-ranitidine | recall | pass | pass | — | — | — | yes |
